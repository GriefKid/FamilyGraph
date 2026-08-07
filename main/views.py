import json
import logging
import os
from datetime import timedelta
from django.db.models import Q, ProtectedError
from django.views.decorators.http import require_http_methods, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone

from .forms import NodeForm, RelationshipForm, EventForm
from .models import Relationship, AppSettings, JournalEntry, JournalImage, AlertAction
from django.core.cache import cache
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from .models import Node, Information, Event
from django.views.generic import ListView
from django.views.generic import TemplateView

def _ai_error_msg(e: Exception) -> str:
    s = str(e)
    if '429' in s or 'rate limit' in s.lower() or 'Rate limit' in s:
        return ('حد روزانه تموم شده 😔 — فردا دوباره امتحان کن '
                'یا GROQ_API_KEY رو در .env تنظیم کن (۱۴,۴۰۰ درخواست/روز رایگان).')
    return f'خطای AI: {s[:200]}'

def _get_ai_client_and_model():
    """Return the project's configured OpenAI-compatible client and model."""
    from .views_smart_features import _ai_client, _model

    client, configured, _provider = _ai_client()
    if not configured:
        raise RuntimeError(
            'AI is not configured. Set OPENROUTER_API_KEY or run Ollama locally.'
        )
    return client, _model()

COMMUNITY_PALETTE = [
    "#6366f1","#ec4899","#f59e0b","#10b981","#3b82f6",
    "#ef4444","#8b5cf6","#06b6d4","#f97316","#14b8a6",
]

def _build_graph(user):
    """Build a networkx Graph from DB filtered by user. Returns (G, nodes_list, rels_list)."""
    import networkx as nx
    all_nodes = list(Node.objects.filter(owner=user))
    all_rels  = list(Relationship.objects.filter(owner=user).select_related('source', 'target'))
    G = nx.Graph()
    for n in all_nodes:
        G.add_node(n.id)
    for r in all_rels:
        G.add_edge(r.source_id, r.target_id, strength=r.strength, status=r.status)
    return G, all_nodes, all_rels

def _community_map(G):
    """Return {node_id: community_index}."""
    try:
        from networkx.algorithms.community import louvain_communities
        raw = louvain_communities(G, seed=42)
    except Exception:
        import networkx as nx
        raw = list(nx.connected_components(G))
    raw = sorted(raw, key=lambda s: -len(s))
    result = {}
    for i, group in enumerate(raw):
        for nid in group:
            result[nid] = i
    return result


class GraphView(LoginRequiredMixin, TemplateView):
    template_name = "nodes/graph.html"


class HomeBriefingView(LoginRequiredMixin, TemplateView):
    """The action-oriented home view: what matters in the user's relationships today."""

    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()
        root_id = user.root_node_id

        nodes = Node.objects.filter(owner=user).exclude(pk=root_id)
        relationships = Relationship.objects.filter(owner=user)
        node_map = {node.id: node for node in nodes}

        attention = []
        try:
            from .health import compute_health
            health = compute_health(user)
            rank = {'red': 0, 'yellow': 1, 'unknown': 2, 'green': 3}
            for node_id, item in health.items():
                node = node_map.get(node_id)
                if not node or item.get('status') not in ('red', 'yellow'):
                    continue
                attention.append({
                    'node': node,
                    'status': item.get('status'),
                    'score': item.get('score'),
                    'days_since': item.get('days_since'),
                    'expected': item.get('expected'),
                })
            attention.sort(
                key=lambda item: (
                    rank[item['status']],
                    item['score'] if item['score'] is not None else 101,
                )
            )
        except Exception:
            pass

        due_followups = []
        try:
            from .models import FollowUp
            due_followups = list(
                FollowUp.objects.filter(owner=user, done=False)
                .select_related('node')
                .order_by('due_date', '-created_at')[:4]
            )
        except Exception:
            pass

        upcoming_events = list(
            Event.objects.filter(
                owner=user,
                date__gte=today,
                date__lte=today + timedelta(days=7),
            )
            .prefetch_related('participants')
            .order_by('date', 'event_time')[:4]
        )

        recent_memories = list(
            JournalEntry.objects.filter(owner=user)
            .order_by('-created_at')[:3]
        )

        checkin_done = any(
            'checkin' in (entry.tags or [])
            for entry in JournalEntry.objects.filter(owner=user, entry_date=today)
            .only('tags')
        )

        from .models import Debt, ExtractionSuggestion
        open_debts = list(
            Debt.objects.filter(owner=user, settled=False)
            .select_related('node').order_by('date')[:4]
        )
        pending_suggestions = ExtractionSuggestion.objects.filter(
            owner=user, status='pending'
        ).count()
        today_actions = []
        if not checkin_done:
            today_actions.append({'icon': '⚡', 'title': 'چک‌این امروز',
                                  'note': 'حال و انرژی امروزت را ثبت کن.', 'url': '/checkin/'})
        for item in attention[:2]:
            today_actions.append({'icon': '💬', 'title': f'یک قدم برای {item["node"].display_name()}',
                                  'note': 'مدتی از آخرین تعامل گذشته است.',
                                  'url': f'/nodes/{item["node"].id}/'})
        if pending_suggestions:
            today_actions.append({'icon': '✨', 'title': f'{pending_suggestions} پیشنهاد منتظر تصمیم',
                                  'note': 'حافظهٔ AI را مرور و اصلاح کن.', 'url': '/extractions/'})

        context.update({
            'today': today,
            'attention': attention[:3],
            'due_followups': due_followups,
            'upcoming_events': upcoming_events,
            'recent_memories': recent_memories,
            'checkin_done': checkin_done,
            'open_debts': open_debts,
            'pending_suggestions': pending_suggestions,
            # A daily briefing should create focus, not another task list.
            'today_actions': today_actions[:3],
            'people_count': nodes.count(),
            'is_new_workspace': not nodes.exists(),
            'relationship_count': relationships.count(),
            'onboarding_ready': bool(
                root_id and Information.objects.filter(node_id=root_id).exists()
            ),
        })
        return context


class NodeListView(LoginRequiredMixin, ListView):
    model = Node
    template_name = 'nodes/node_list.html'
    context_object_name = 'nodes'
    paginate_by = 24

    def get_queryset(self):
        return Node.objects.filter(owner=self.request.user).select_related('owner')


@login_required
def home(request):
    nodes = Node.objects.filter(owner=request.user)
    relationships = Relationship.objects.filter(owner=request.user)
    context = {
        'nodes': nodes,
        'relationships': relationships,
        'node_count': nodes.count(),
        'relationship_count': relationships.count(),
    }
    return render(request, 'home.html', context)

class UpdateNodeView(LoginRequiredMixin, UpdateView):
    model = Node
    form_class = NodeForm
    template_name = 'nodes/node_form.html'
    success_url = reverse_lazy('node_list')

    def dispatch(self, request, *args, **kwargs):
        node = self.get_object()
        # اگه self-node کاربر بود → ریدایرکت به پروفایل
        if (request.user.is_authenticated and
                node.owner == request.user and
                node.username == request.user.username):
            return redirect('profile')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'ویرایش {self.object.username}'
        return context

    def get_queryset(self):
        return Node.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        if form.has_changed():
            existing = Node.objects.filter(
                username=form.cleaned_data['username'],
                owner=self.request.user,
            ).exclude(pk=self.object.pk).first()
            if existing:
                form.add_error('username', 'این نام قبلاً استفاده شده')
                return self.form_invalid(form)
        response = super().form_valid(form)
        # BUGFIX مفهومی: فیلد متنی «گروه» → گروه واقعی M2M
        if self.object.group and self.object.group.strip():
            from .models import Group as _G
            _g, _ = _G.objects.get_or_create(
                name=self.object.group.strip(), owner=self.request.user)
            self.object.groups.add(_g)
        return response



@login_required
def node_delete(request, pk):
    node = get_object_or_404(Node, pk=pk, owner=request.user)

    if request.method == 'POST':
        try:
            # اول relationships مرتبط رو حذف کن (PROTECT جلوگیری می‌کنه)
            Relationship.objects.filter(
                Q(source=node) | Q(target=node),
                owner=request.user
            ).delete()
            # بعد informations مرتبط رو حذف کن (PROTECT دیگه)
            node.informations.all().delete()
            node.delete()
            messages.success(request, "Node و روابط مرتبطش حذف شدند")
        except Exception as e:
            messages.error(request, f"خطا در حذف: {e}")
        return redirect('node_list')

    rel_count  = Relationship.objects.filter(
        Q(source=node) | Q(target=node), owner=request.user
    ).count()
    return render(request, 'nodes/node_confirm_delete.html', {
        'node': node,
        'rel_count': rel_count,
    })


@login_required
@require_http_methods(["GET"])
def node_detail(request, pk):
    node = get_object_or_404(Node, pk=pk, owner=request.user)
    linked_user = None
    if node.imported_from_id:
        linked_user = node.imported_from
    elif node.username:
        linked_user = get_user_model().objects.filter(username=node.username).first()
    if linked_user:
        return redirect('public_profile', username=linked_user.username)

    relationships = Relationship.objects.filter(
        Q(source=node) | Q(target=node), owner=request.user
    ).select_related('source', 'target')

    informations = Information.objects.filter(node=node)
    from django.db import ProgrammingError as _PE
    try:
        events = list(node.events.order_by('-date')[:10])
    except _PE:
        events = list(node.events.only('id','title','date','description').order_by('-date')[:10])
        for _ev in events:
            _ev.__dict__.update({'event_time': None, 'reminder_sent_7d': False,
                                 'reminder_sent_1d': False, 'reminder_sent_3h': False,
                                 'post_event_prompted': False})

    # ── community + centrality for this node ──
    community_idx = None
    community_color = None
    degree_centrality = None
    betweenness_centrality = None
    try:
        import networkx as nx
        G, all_nodes, all_rels = _build_graph(request.user)
        if G.number_of_nodes() > 1:
            deg_c  = nx.degree_centrality(G)
            bet_c  = nx.betweenness_centrality(G)
            com_map = _community_map(G)
            community_idx   = com_map.get(node.id, 0)
            community_color = COMMUNITY_PALETTE[community_idx % len(COMMUNITY_PALETTE)]
            degree_centrality     = round(deg_c.get(node.id, 0), 3)
            betweenness_centrality = round(bet_c.get(node.id, 0), 3)
    except Exception:
        pass

    # Journal entries that mention this node
    journal_entries = node.journal_entries.prefetch_related('images').order_by('-created_at')[:10]

    # ── V4: تعامل‌ها + سلامت رابطه ──
    from django.db.utils import OperationalError as _OE
    interactions = []
    try:
        from .models import Interaction
        interactions = list(Interaction.objects.filter(node=node, owner=request.user)[:15])
    except (_OE, _PE):
        interactions = []

    node_health = None
    try:
        from .health import compute_health
        node_health = compute_health(request.user).get(node.id)
    except Exception:
        node_health = None

    node_closeness = ''
    try:
        from .models import NodeCloseness
        nc = NodeCloseness.objects.filter(node=node, owner=request.user).first()
        node_closeness = nc.tier if nc else ''
    except Exception:
        node_closeness = ''

    # V4: موضوعات باز
    followups_open, followups_done = [], []
    try:
        from .models import FollowUp
        followups_open = list(FollowUp.objects.filter(
            node=node, owner=request.user, done=False)[:50])
        followups_done = list(FollowUp.objects.filter(
            node=node, owner=request.user, done=True).order_by('-done_at')[:5])
    except Exception:
        pass

    # V6: قرض و طلب با این شخص
    node_debts, node_debt_balance = [], 0
    try:
        from .views_ledger import serialize_debt, node_balance
        from .models import Debt
        node_debts = [serialize_debt(d) for d in
                      Debt.objects.filter(node=node, owner=request.user, settled=False)[:20]]
        node_debt_balance = node_balance(request.user, node.id)
    except Exception:
        pass

    # V9: شناخت‌نامه — تحلیل ذخیره‌شده
    node_insight = None
    try:
        _info0 = informations.first() if hasattr(informations, 'first') else (informations[0] if informations else None)
        if _info0 and isinstance(_info0.data, dict) and (
                _info0.data.get('friendship_score') is not None or _info0.data.get('personality')):
            node_insight = _info0.data
    except Exception:
        pass

    # V10: روند ۱۲ ماهه‌ی تعامل + حس (برای sparkline)
    trend = []
    try:
        from .models import Interaction as _Ix
        _today = timezone.localdate()
        from datetime import timedelta as _td
        rows = list(_Ix.objects.filter(
            node=node, owner=request.user,
            date__gte=_today - _td(days=370)).values_list('date', 'feeling'))
        buckets = {}
        for d_, f_ in rows:
            k = (d_.year, d_.month)
            b = buckets.setdefault(k, {'n': 0, 'fs': 0, 'fn': 0})
            b['n'] += 1
            if f_:
                b['fs'] += f_
                b['fn'] += 1
        seq = []
        yy, mm = _today.year, _today.month
        for i in range(11, -1, -1):
            m2, y2 = mm - i, yy
            while m2 <= 0:
                m2 += 12
                y2 -= 1
            b = buckets.get((y2, m2), {'n': 0, 'fs': 0, 'fn': 0})
            avgf = (b['fs'] / b['fn']) if b['fn'] else 0
            seq.append({'label': f'{y2}/{m2:02d}', 'n': b['n'], 'feel': avgf})
        mx = max((s['n'] for s in seq), default=0) or 1
        for s in seq:
            s['h'] = max(6, round(s['n'] / mx * 100)) if s['n'] else 4
            s['color'] = ('#34d399' if s['feel'] > 0.25 else
                          ('#f87171' if s['feel'] < -0.25 else '#818cf8'))
        if any(s['n'] for s in seq):
            trend = seq
    except Exception:
        trend = []

    # V10: رویدادهای زندگی + هدف فعال
    life_events = []
    try:
        from .models import LifeEvent
        life_events = list(LifeEvent.objects.filter(
            node=node, owner=request.user, archived=False)[:10])
    except Exception:
        pass

    active_goal, goal_progress = None, None
    try:
        from .models import RelationshipGoal
        active_goal = RelationshipGoal.objects.filter(
            node=node, owner=request.user, status='active').first()
        if active_goal and node_health and node_health.get('score') is not None \
                and active_goal.baseline_score is not None:
            goal_progress = node_health['score'] - active_goal.baseline_score
    except Exception:
        pass

    # V11: اگه این نود یه کاربر واقعی اپه → لینک به پروفایل اجتماعیش
    social_username = None
    try:
        from django.contrib.auth import get_user_model as _gum
        if _gum().objects.filter(username=node.username).exists():
            social_username = node.username
    except Exception:
        pass

    relationship_timeline = []
    for item in interactions:
        relationship_timeline.append({'date': item.date, 'icon': '⚡',
                                      'title': item.get_kind_display(), 'detail': item.note})
    for item in journal_entries:
        relationship_timeline.append({'date': item.entry_date or item.created_at.date(), 'icon': '📓',
                                      'title': 'خاطرهٔ مرتبط', 'detail': item.text[:180]})
    for item in events:
        relationship_timeline.append({'date': item.date, 'icon': '📅',
                                      'title': item.title, 'detail': item.description[:180]})
    for item in node_debts:
        relationship_timeline.append({'date': item.get('date'), 'icon': '💰',
                                      'title': 'ثبت مالی', 'detail': item.get('note', '')})
    relationship_timeline.sort(key=lambda row: str(row['date'] or ''), reverse=True)

    insight_sources = []
    try:
        from .models import ExtractionSuggestion
        node_names = {value for value in (node.username, node.name, node.first_name, node.nickname) if value}
        for suggestion in ExtractionSuggestion.objects.filter(owner=request.user, status='approved')[:100]:
            if (str(suggestion.payload.get('node_id') or '') == str(node.id)
                    or suggestion.payload.get('person_raw') in node_names):
                insight_sources.append(suggestion)
    except Exception:
        pass

    memory_facts = []
    try:
        from .models import MemoryFact
        memory_facts = list(MemoryFact.objects.filter(owner=request.user, node=node, active=True)
                            .select_related('suggestion')[:80])
    except Exception:
        pass

    commitments = gifts = meeting_reflections = []
    try:
        from .models import Commitment, GiftIdea, MeetingReflection
        commitments = list(Commitment.objects.filter(owner=request.user, node=node)[:30])
        gifts = list(GiftIdea.objects.filter(owner=request.user, node=node)[:30])
        meeting_reflections = list(MeetingReflection.objects.filter(owner=request.user, node=node)[:20])
        for item in meeting_reflections:
            relationship_timeline.append({'date': item.happened_at.date(), 'icon': '🤝',
                                          'title': 'بازتاب ملاقات', 'detail': item.summary[:180]})
        for item in commitments:
            relationship_timeline.append({'date': item.created_at.date(), 'icon': '📌',
                                          'title': 'قول و تعهد', 'detail': item.text})
        relationship_timeline.sort(key=lambda row: str(row['date'] or ''), reverse=True)
    except Exception:
        pass

    from .models import CLOSENESS_CHOICES, LifeEvent as _LE
    life_event_kinds = _LE.KIND_CHOICES
    is_root_node = bool(request.user.root_node_id and node.id == request.user.root_node_id)

    context = {
        'node': node,
        'relationships': relationships,
        'informations': informations,
        'events': events,
        'community_idx':   community_idx,
        'community_color': community_color,
        'degree_centrality': degree_centrality,
        'betweenness_centrality': betweenness_centrality,
        'journal_entries': journal_entries,
        # V4
        'interactions':      interactions,
        'node_health':       node_health,
        'node_closeness':    node_closeness,
        'closeness_choices': CLOSENESS_CHOICES,
        'is_root_node':      is_root_node,
        'followups_open':    followups_open,
        'followups_done':    followups_done,
        'today':             timezone.localdate(),
        'node_debts':        node_debts,
        'node_debt_balance': node_debt_balance,
        'node_debt_balance_fmt': f'{abs(node_debt_balance):,}',
        'node_insight':      node_insight,
        # V10
        'trend':             trend,
        'life_events':       life_events,
        'life_event_kinds':  life_event_kinds,
        'active_goal':       active_goal,
        'goal_progress':     goal_progress,
        'social_username':   social_username,
        'relationship_timeline': relationship_timeline[:30],
        'insight_sources': insight_sources[:10],
        'memory_facts': memory_facts,
        'commitments': commitments,
        'gift_ideas': gifts,
        'meeting_reflections': meeting_reflections,
    }
    return render(request, 'nodes/node_detail.html', context)


@login_required
def create_node(request):
    if request.method == 'POST':
        form = NodeForm(request.POST, request.FILES)
        if form.is_valid():
            node = form.save(commit=False)
            node.owner = request.user
            if Node.objects.filter(owner=request.user, username=node.username).exists():
                base = node.username[:90] or 'person'
                suffix = 2
                while Node.objects.filter(owner=request.user, username=f'{base}-{suffix}').exists():
                    suffix += 1
                node.username = f'{base}-{suffix}'
            node.save()
            form.save_m2m()
            # BUGFIX مفهومی: فیلد متنی «گروه» توی فرم هیچ اثری نداشت —
            # حالا به گروه واقعی (M2M) تبدیل می‌شه تا توی گراف رنگ بگیره
            if node.group and node.group.strip():
                from .models import Group as _G
                _g, _ = _G.objects.get_or_create(name=node.group.strip(), owner=request.user)
                node.groups.add(_g)
            messages.success(request, f'نود "{node.username}" ایجاد شد')
            return redirect('node_list')
    else:
        form = NodeForm()

    return render(request, 'nodes/node_form.html', {'form': form})


class RelationshipListView(LoginRequiredMixin, ListView):
    model = Relationship
    template_name = 'relationships/relationship_list.html'
    context_object_name = 'relationships'
    # BUGFIX: pagination باعث می‌شد فقط ۲۰ رابطه‌ی اول دیده بشه و
    # بقیه (مثل رابطه با همسر) «صفحه نداشته باشن» — همه رو نشون بده
    paginate_by = None

    def get_queryset(self):
        return Relationship.objects.filter(owner=self.request.user) \
                                   .select_related('source', 'target') \
                                   .order_by('-strength', 'source__username')

class RelationshipDetailView(LoginRequiredMixin, DetailView):
    model = Relationship
    template_name = 'relationships/relationship_detail.html'

    def get_queryset(self):
        return Relationship.objects.filter(owner=self.request.user)

class RelationshipCreateView(LoginRequiredMixin, CreateView):
    model = Relationship
    form_class = RelationshipForm
    template_name = 'relationships/relationship_form.html'
    success_url = reverse_lazy('relationship_list')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        qs = Node.objects.filter(owner=self.request.user)
        form.fields['source'].queryset = qs
        form.fields['target'].queryset = qs
        return form

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

class RelationshipUpdateView(LoginRequiredMixin, UpdateView):
    model = Relationship
    form_class = RelationshipForm
    template_name = 'relationships/relationship_form.html'
    success_url = reverse_lazy('relationship_list')

    def get_queryset(self):
        return Relationship.objects.filter(owner=self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        qs = Node.objects.filter(owner=self.request.user)
        form.fields['source'].queryset = qs
        form.fields['target'].queryset = qs
        return form

class RelationshipDeleteView(LoginRequiredMixin, DeleteView):
    model = Relationship
    template_name = 'relationships/relationship_confirm_delete.html'
    success_url = reverse_lazy('relationship_list')

    def get_queryset(self):
        return Relationship.objects.filter(owner=self.request.user)





@login_required
def information_detail(request, info_id):
    info = get_object_or_404(Information, id=info_id, node__owner=request.user)

    context = {
        'info': info,
        'node': info.node,
        'node_data': info.data,
        'informations': [info],
        'relationships': {
            'outgoing': info.node.relationships_outgoing() if hasattr(info.node, 'relationships_outgoing') else [],
            'incoming': info.node.relationships_incoming() if hasattr(info.node, 'relationships_incoming') else []
        },
    }

    return render(request, 'information_detail.html', context)


logger = logging.getLogger(__name__)

@login_required
def graph_level_data(request, level=0):
    """Legacy V1 endpoint — replaced by /api/graph/all/ in V3."""
    return JsonResponse({'nodes': [], 'relationships': [], 'level': level, 'deprecated': True})

class InformationListView(LoginRequiredMixin, ListView):
    model = Information
    template_name = 'informations/informations_list.html'
    context_object_name = 'informations'
    paginate_by = 20

    def get_queryset(self):
        return Information.objects.filter(node__owner=self.request.user).select_related('node')


class InformationDetailView(LoginRequiredMixin, DetailView):
    model = Information
    template_name = 'informations/information_detail.html'

    def get_queryset(self):
        return Information.objects.filter(node__owner=self.request.user)


class InformationCreateView(LoginRequiredMixin, CreateView):
    model = Information
    fields = ['node', 'visibility', 'data']
    template_name = 'informations/information_form.html'
    success_url = reverse_lazy('information_list')

    def form_valid(self, form):
        if not form.instance.visibility:
            form.instance.visibility = 'private'
        return super().form_valid(form)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['node'].queryset = Node.objects.filter(owner=self.request.user)
        return form


class InformationUpdateView(LoginRequiredMixin, UpdateView):
    model = Information
    fields = ['node', 'visibility', 'data']
    template_name = 'informations/information_form.html'
    success_url = reverse_lazy('information_list')

    def get_queryset(self):
        return Information.objects.filter(node__owner=self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['node'].queryset = Node.objects.filter(owner=self.request.user)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'ویرایش اطلاعات #{self.object.id}'
        return context


class InformationDeleteView(LoginRequiredMixin, DeleteView):
    model = Information
    template_name = 'informations/information_confirm_delete.html'
    success_url = reverse_lazy('information_list')

    def get_queryset(self):
        return Information.objects.filter(node__owner=self.request.user)


@login_required
def home_graph_api(request):
    nodes = Node.objects.filter(owner=request.user).only("id", "username")
    relationships = (
        Relationship.objects
        .filter(owner=request.user)
        .select_related('source', 'target')
    )

    elements = {
        "nodes": [],
        "edges": []
    }

    for node in nodes:
        elements["nodes"].append({
            "data": {
                "id": str(node.id),
                "label": node.username or f"Node-{node.id}",
            }
        })

    for rel in relationships:
        elements["edges"].append({
            "data": {
                "id": f"rel-{rel.id}",
                "source": str(rel.source.id),
                "target": str(rel.target.id),
                "label": rel.rel or "",
            }
        })

    return JsonResponse(elements)


@login_required
def events_list(request):
    from django.db import ProgrammingError
    today = timezone.localdate()
    try:
        all_events = list(Event.objects.filter(owner=request.user).prefetch_related('participants').order_by('date'))
        upcoming_raw = [e for e in all_events if e.date >= today]
        past_events  = sorted([e for e in all_events if e.date < today], key=lambda e: e.date, reverse=True)
        upcoming_events = [{'event': ev, 'days_left': (ev.date - today).days} for ev in upcoming_raw]
    except ProgrammingError:
        # migration هنوز اجرا نشده — فیلد event_time وجود نداره
        # از .only() استفاده می‌کنیم تا event_time در SELECT نباشه
        # بعد مقدار None رو مستقیم در __dict__ می‌ذاریم تا template lazy load نزنه
        all_events = list(
            Event.objects.filter(owner=request.user)
            .only('id', 'title', 'date', 'description', 'owner_id')
            .prefetch_related('participants')
            .order_by('date')   # override Meta.ordering که event_time داره
        )
        _safe_defaults = {
            'event_time': None,
            'reminder_sent_7d': False,
            'reminder_sent_1d': False,
            'reminder_sent_3h': False,
            'post_event_prompted': False,
        }
        for ev in all_events:
            ev.__dict__.update(_safe_defaults)
        upcoming_raw = [e for e in all_events if e.date >= today]
        past_events  = sorted([e for e in all_events if e.date < today], key=lambda e: e.date, reverse=True)
        upcoming_events = [{'event': ev, 'days_left': (ev.date - today).days} for ev in upcoming_raw]

    return render(request, 'events/events_list.html', {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'events': all_events,
        'today': today,
    })

@login_required
def event_create(request):
    from django.db import ProgrammingError
    if request.method == 'POST':
        form = EventForm(request.POST)
        form.fields['participants'].queryset = Node.objects.filter(owner=request.user)
        if form.is_valid():
            ev = form.save(commit=False)
            ev.owner = request.user
            try:
                ev.save()
                form.save_m2m()
            except ProgrammingError:
                # migration هنوز نخورده — بدون event_time ذخیره می‌کنیم
                ev.event_time = None
                # raw INSERT بدون ستون‌های جدید
                from django.db import connection
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO main_event (title, date, description, owner_id) VALUES (%s,%s,%s,%s)",
                        [ev.title, ev.date, ev.description or '', request.user.id]
                    )
                    ev.id = cur.lastrowid
                # participants M2M
                for p in form.cleaned_data.get('participants', []):
                    with connection.cursor() as cur:
                        cur.execute(
                            "INSERT OR IGNORE INTO main_event_participants (event_id, node_id) VALUES (%s,%s)",
                            [ev.id, p.id]
                        )
            return redirect('events_list')
    else:
        form = EventForm()
        form.fields['participants'].queryset = Node.objects.filter(owner=request.user)
    return render(request, 'events/event_form.html', {'form': form})

@login_required
@csrf_exempt
def event_complete_api(request, pk):
    """V11: «✓ برگزار شد» — برای همه‌ی شرکت‌کننده‌ها تعامل حضوری ثبت می‌کنه.
    این همون چیزیه که صفحه رویدادها رو به موتور سلامت رابطه وصل می‌کنه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    event = get_object_or_404(Event, pk=pk, owner=request.user)
    logged = 0
    try:
        from .models import Interaction
        for p in event.participants.all():
            if request.user.root_node_id and p.id == request.user.root_node_id:
                continue
            _, was_new = Interaction.objects.get_or_create(
                node=p, owner=request.user, kind='meet', date=event.date,
                defaults={'feeling': 0, 'note': f'رویداد: {event.title[:80]}'},
            )
            if was_new:
                logged += 1
    except Exception:
        return JsonResponse({'error': 'جدول تعامل‌ها آماده نیست'}, status=503)
    try:
        event.post_event_prompted = True
        event.save(update_fields=['post_event_prompted'])
    except Exception:
        pass
    return JsonResponse({'ok': True, 'logged': logged})


@login_required
def event_delete(request, pk):
    event = get_object_or_404(Event, pk=pk, owner=request.user)
    if request.method == 'POST':
        event.delete()
        return redirect('events_list')
    return render(request, 'events/event_confirm_delete.html', {'event': event})


@login_required
def communities_view(request):
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        return render(request, 'communities/communities.html', {'error': 'networkx نصب نیست. دستور: py -m pip install networkx'})

    all_nodes = list(Node.objects.filter(owner=request.user))
    all_rels  = list(Relationship.objects.filter(owner=request.user).select_related('source', 'target'))
    node_map  = {n.id: n for n in all_nodes}

    G = nx.Graph()
    for n in all_nodes:
        G.add_node(n.id)
    for r in all_rels:
        G.add_edge(r.source_id, r.target_id)

    if G.number_of_nodes() == 0:
        return render(request, 'communities/communities.html', {'communities': [], 'node_community': {}})

    try:
        raw = louvain_communities(G, seed=42)
    except Exception:
        # fallback: هر connected component یه community
        raw = list(nx.connected_components(G))

    # sort communities by size desc
    raw = sorted(raw, key=lambda s: -len(s))

    communities = []
    node_community = {}  # node_id -> community_index
    for i, group in enumerate(raw):
        members = [node_map[nid] for nid in group if nid in node_map]
        color   = COMMUNITY_PALETTE[i % len(COMMUNITY_PALETTE)]
        communities.append({'index': i+1, 'color': color, 'members': members, 'size': len(members)})
        for nid in group:
            node_community[nid] = i

    return render(request, 'communities/communities.html', {
        'communities': communities,
        'total': len(communities),
        'node_count': len(all_nodes),
        'edge_count': len(all_rels),
    })


@login_required
def groups_view(request):
    """صفحه مدیریت گروه‌ها — rename و assign نودها (M2M)."""
    from .models import Group as GroupModel
    all_groups = list(GroupModel.objects.filter(owner=request.user).prefetch_related('nodes').order_by('name'))
    all_nodes  = list(Node.objects.filter(owner=request.user).prefetch_related('groups').order_by('username'))

    # نودهایی که در هیچ گروهی نیستند
    ungrouped = [n for n in all_nodes if not n.groups.exists()]

    return render(request, 'groups/groups.html', {
        'groups':    all_groups,   # Group objects (هر کدوم .nodes.all() داره)
        'ungrouped': ungrouped,
        'all_nodes': all_nodes,
    })


@login_required
@csrf_exempt
def assign_group_api(request):
    """
    POST {node_ids, group_name, action}
    action: 'add' (default) | 'remove'
    group_name: اسم گروه — اگه وجود نداشت ساخته می‌شه
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    from .models import Group as GroupModel
    node_ids   = body.get('node_ids', [])
    group_name = (body.get('group_name') or '').strip()
    action     = body.get('action', 'add')

    if not node_ids:
        return JsonResponse({'error': 'node_ids لازم است'}, status=400)

    nodes = list(Node.objects.filter(pk__in=node_ids, owner=request.user))

    if action == 'remove':
        if not group_name:
            return JsonResponse({'error': 'group_name لازم است'}, status=400)
        try:
            grp = GroupModel.objects.get(name=group_name, owner=request.user)
            for n in nodes:
                n.groups.remove(grp)
        except GroupModel.DoesNotExist:
            pass
    else:
        if not group_name:
            return JsonResponse({'error': 'group_name لازم است'}, status=400)
        grp, _ = GroupModel.objects.get_or_create(name=group_name, owner=request.user)
        for n in nodes:
            n.groups.add(grp)

    return JsonResponse({'ok': True, 'count': len(nodes)})


# «بینش‌ها» حذف شد — تحلیل‌هاش در «روانشناسی» و «بریفینگ روزانه» پوشش داده می‌شن.
@login_required
def insights_view(request):
    return redirect('daily')


@login_required
def node_ai_summary(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    node = get_object_or_404(Node, pk=pk, owner=request.user)
    rels = Relationship.objects.filter(
        Q(source=node) | Q(target=node), owner=request.user
    ).select_related('source', 'target')
    infos = Information.objects.filter(node=node)

    rels_text = "\n".join(
        f"- {'خروجی به' if r.source == node else 'ورودی از'} {r.target.username if r.source == node else r.source.username}"
        + (f" [{r.rel}]" if r.rel else "")
        for r in rels
    ) or "هیچ رابطه‌ای ندارد"

    info_text = "\n".join(f"- {i.data}" for i in infos) or "اطلاعات اضافه‌ای ثبت نشده"

    prompt = (
        f"یک خلاصه تحلیلی از این شخص بنویس:\n\n"
        f"نام کاربری: {node.username}\n"
        f"نام: {node.name or '—'}\n"
        f"شغل: {node.career or '—'}\n"
        f"تولد: {node.birth_day or '—'}\n\n"
        f"روابط:\n{rels_text}\n\n"
        f"اطلاعات:\n{info_text}\n\n"
        "در ۲-۳ پاراگراف کوتاه فارسی بنویس: این شخص کیه، چه نقشی در شبکه داره، و چه نکته مهمی درباره‌اش هست."
    )

    # ── کش: خلاصه node تا زمانی که اطلاعاتش تغییر کنه معتبره ──────────────
    cache_key = f'node_summary_{pk}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'summary': cached, 'from_cache': True})

    try:
        client, ai_model = _get_ai_client_and_model()
        response = client.chat.completions.create(
            model=ai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
        )
        summary_text = response.choices[0].message.content
        cache.set(cache_key, summary_text, timeout=12 * 3600)  # کش ۱۲ ساعته
        return JsonResponse({'summary': summary_text})
    except Exception as e:
        return JsonResponse({'error': _ai_error_msg(e)}, status=500)


@login_required
def chat_view(request):
    # V8: گفتگوی قبلی رو هم بیار — همدم حافظه داره
    past = []
    try:
        from .models import ChatMessage
        past = list(ChatMessage.objects.filter(owner=request.user)
                    .order_by('-created_at')[:30])[::-1]
    except Exception:
        pass
    return render(request, 'chat/chat.html', {'past_messages': past})


@login_required
@csrf_exempt
def chat_clear_api(request):
    """POST → پاک کردن حافظه‌ی همدم (شروع گفتگوی نو)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        from .models import ChatMessage
        ChatMessage.objects.filter(owner=request.user).delete()
        return JsonResponse({'ok': True})
    except Exception:
        return JsonResponse({'ok': True})


@login_required
@csrf_exempt
def chat_to_journal_api(request):
    """POST → حرف‌های امروزِ کاربر در چت → یادداشت ژورنال.
    این‌طوری درد دل‌ها وارد موتور mood-alert و روانشناسی می‌شن."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    today = timezone.localdate()
    texts = []
    try:
        from .models import ChatMessage
        msgs = ChatMessage.objects.filter(
            owner=request.user, role='user', created_at__date=today)
        texts = [m.content for m in msgs]
    except Exception:
        pass
    if not texts:
        return JsonResponse({'error': 'امروز هنوز چیزی توی چت نگفتی'}, status=400)

    entry = JournalEntry.objects.create(
        text='(از گفتگو با همدم)\n' + '\n'.join(texts)[:4000],
        entry_date=today,
        tags=['chat'],
        owner=request.user,
    )
    try:
        from .extraction import extract_text
        extract_text(request.user, entry.text, 'chat', entry.id)
    except Exception:
        pass
    return JsonResponse({'ok': True, 'entry_id': entry.id,
                         'msg': 'ذخیره شد — از صفحه ژورنال می‌تونی تحلیل AI هم بزنی'})


@login_required
def chat_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        raw_history  = data.get('history') or []
        chat_style = data.get('style', 'friendly')
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    if not user_message:
        return JsonResponse({'error': 'message is empty'}, status=400)

    from .persian_chat import (
        PERSIAN_FEW_SHOTS,
        STYLE_LABELS,
        language_policy,
        normalize_persian_reply,
        persian_quality_issues,
    )
    if chat_style not in STYLE_LABELS:
        chat_style = 'friendly'

    # ── V5: تاریخچه گفتگو — چت دوطرفه و پیوسته ──
    history = []
    for m in raw_history[-12:]:
        role = m.get('role')
        content = (m.get('content') or '').strip()
        if role in ('user', 'assistant') and content:
            history.append({'role': role, 'content': content[:2000]})

    # ── V8: حافظه‌ی بین‌جلسه‌ای — اگه صفحه تازه باز شده، از DB ادامه بده ──
    if not history:
        try:
            from .models import ChatMessage
            recent = list(ChatMessage.objects.filter(owner=request.user)
                          .order_by('-created_at')[:12])[::-1]
            history = [{'role': m.role, 'content': m.content[:2000]} for m in recent]
        except Exception:
            pass

    # ─── root node (کاربر اصلی که داره چت می‌کنه) ───────────────────────────
    root_node = request.user.root_node

    # ─── serialize graph (فقط داده‌های این کاربر) ────────────────────────────
    all_nodes = Node.objects.filter(owner=request.user)
    all_rels  = Relationship.objects.filter(owner=request.user).select_related('source', 'target')
    all_info  = Information.objects.filter(node__owner=request.user).select_related('node')

    # اطلاعات خود کاربر اصلی
    root_info = ""
    if root_node:
        root_info = (
            f"نام: {root_node.name or root_node.username}\n"
            f"شغل: {root_node.career or '—'}\n"
            f"تولد: {root_node.birth_day or '—'}"
        )
        root_extra = root_node.informations.first()
        if root_extra and root_extra.data:
            d = root_extra.data
            if isinstance(d, dict):
                if d.get('personality'): root_info += f"\nشخصیت: {d['personality']}"
                if d.get('interests'):   root_info += f"\nعلایق: {', '.join(d['interests']) if isinstance(d['interests'], list) else d['interests']}"
                if d.get('values'): root_info += f"\nارزش‌ها: {', '.join(d['values']) if isinstance(d['values'], list) else d['values']}"
                if d.get('communication_style'): root_info += f"\nسبک ارتباطی: {d['communication_style']}"
                if d.get('relationship_goals'): root_info += f"\nهدف‌های رابطه‌ای: {d['relationship_goals']}"
                if d.get('boundaries'): root_info += f"\nمرزها و حساسیت‌ها: {d['boundaries']}"
                if d.get('social_energy'): root_info += f"\nانرژی اجتماعی: {d['social_energy']}"

    # روابط از دید من (root)
    if root_node:
        my_rels = Relationship.objects.filter(
            Q(source=root_node) | Q(target=root_node), owner=request.user
        ).select_related('source', 'target')
        rels_text = "\n".join(
            f"- من ↔ {(r.target if r.source == root_node else r.source).display_name()}"
            + (f" [{r.rel}]" if r.rel else "")
            + (f" (قدرت: {r.strength}/5)" if r.strength else "")
            for r in my_rels
        ) or "هنوز رابطه‌ای ثبت نشده"
    else:
        rels_text = "\n".join(
            f"- {r.source.username} → {r.target.username}" + (f" [{r.rel}]" if r.rel else "")
            for r in all_rels
        )

    # سایر افراد شبکه
    others = [n for n in all_nodes if not root_node or n.id != root_node.id]
    nodes_text = "\n".join(
        f"- {n.display_name()}"
        + (f" (شغل: {n.career})" if n.career else "")
        + (f" (تولد: {n.birth_day})" if n.birth_day else "")
        for n in others
    ) or "موردی ثبت نشده"

    # V9: شناخت‌نامه — تحلیل‌های ذخیره‌شده هر شخص، خوانا برای AI
    info_lines = []
    for i in all_info:
        if root_node and i.node_id == root_node.id:
            continue
        d = i.data if isinstance(i.data, dict) else {}
        nm = i.node.display_name()
        bits = []
        if d.get('friendship_score') is not None:
            bits.append(f"نمره دوستی: {d['friendship_score']}/100")
        if d.get('personality'):
            bits.append(f"شخصیت: {str(d['personality'])[:140]}")
        if d.get('values'):
            bits.append("ارزش‌ها: " + '، '.join(str(v) for v in list(d['values'])[:4]))
        if d.get('interests'):
            bits.append("علایق: " + '، '.join(str(v) for v in list(d['interests'])[:4]))
        if d.get('red_flags'):
            bits.append("⚠️ هشدارها: " + '، '.join(str(v) for v in list(d['red_flags'])[:3]))
        if d.get('relationship_quality'):
            bits.append(f"کیفیت رابطه: {str(d['relationship_quality'])[:100]}")
        if not bits and d:
            bits.append(str(d)[:150])
        if bits:
            info_lines.append(f"- {nm}: " + " | ".join(bits))
    info_text = "\n".join(info_lines) or "موردی ثبت نشده"

    # یادداشت‌های اخیر
    recent_journals = JournalEntry.objects.filter(owner=request.user).order_by('-entry_date')[:5]
    journal_text = "\n".join(
        f"- {j.entry_date}: {j.text[:120]}{'...' if len(j.text) > 120 else ''}"
        + (f" [حال: {j.mood}]" if j.mood else "")
        for j in recent_journals
    ) or "یادداشتی ثبت نشده"

    # اقدامات اخیر روی هشدارها (برای AI تا الگوهای رابطه رو بهتر بفهمه)
    recent_actions = AlertAction.objects.filter(owner=request.user).order_by('-created_at')[:8]
    actions_text = "\n".join(
        f"- {a.created_at.strftime('%Y-%m-%d')}: [{a.get_action_display()}] {a.title}"
        + (f" → نتیجه: {a.outcome}" if a.outcome else "")
        for a in recent_actions
    ) or "هنوز اقدامی ثبت نشده"

    who_am_i = (
        f"نام من (کاربر اصلی): {root_node.display_name()}\n{root_info}"
        if root_node else
        "کاربر اصلی (root node) هنوز در تنظیمات مشخص نشده"
    )

    # V6: خلاصه قرض و طلب — چت از حساب‌ها خبر داره
    ledger_text = "حسابی ثبت نشده"
    try:
        from .models import Debt
        _open = list(Debt.objects.filter(owner=request.user, settled=False)
                     .select_related('node')[:20])
        if _open:
            _lines = []
            for _d in _open:
                _who = _d.node.display_name()
                if _d.direction == 'i_owe':
                    _lines.append(f"- من به {_who} بدهکارم: {_d.remaining:,} {_d.currency}"
                                  + (f" (سررسید: {_d.due_date})" if _d.due_date else ""))
                else:
                    _lines.append(f"- {_who} به من بدهکاره: {_d.remaining:,} {_d.currency}"
                                  + (f" (سررسید: {_d.due_date})" if _d.due_date else ""))
            ledger_text = "\n".join(_lines)
    except Exception:
        pass

    persian_policy = language_policy(chat_style)
    system_prompt = (
        persian_policy + "\n\n"
        "تو «همدم» هستی — همراهِ شخصی صاحب این شبکه روابط. دو نقش داری و بسته به حرف کاربر "
        "روان بین‌شون جابه‌جا می‌شی:\n\n"
        "۱) **همدمِ درد دل** — وقتی کاربر از احساساتش می‌گه (دلخوری، تنهایی، استرس، دعوا، دلتنگی، شادی): "
        "اول فقط بشنو. احساسش رو نام‌گذاری و تأیید کن («سخته که...»، «حق داری ناراحت باشی»). "
        "سریع نصیحت نکن — فقط اگه خودش راه‌حل خواست پیشنهاد بده. با سوال‌های کوتاه و ملایم کمکش کن "
        "بیشتر باز بشه («بعدش چی شد؟»، «الان چه حسی داری؟»). لحن گرم، خودمونی، بدون قضاوت. "
        "اگه کسی از شبکه‌ش رو اسم برد، از شناختت درباره اون رابطه با ظرافت استفاده کن.\n"
        "۲) **تحلیلگر شبکه** — وقتی سوال داده‌ای یا تحلیلی می‌پرسه، دقیق و کاربردی از داده‌ها جواب بده.\n\n"
        f"## کاربر کیست:\n{who_am_i}\n\n"
        f"## روابطش:\n{rels_text}\n\n"
        f"## افراد شبکه‌اش:\n{nodes_text}\n\n"
        f"## شناخت‌نامه افراد (تحلیل‌های ذخیره‌شده — نمره دوستی، شخصیت، هشدارها):\n{info_text}\n\n"
        f"## یادداشت‌های اخیرش:\n{journal_text}\n\n"
        f"## اقدامات اخیرش:\n{actions_text}\n\n"
        f"## قرض و طلب‌های باز:\n{ledger_text}\n\n"
        "قواعد: وقتی می‌گه «من» منظورش شخص اصلی بالاست. داده‌های شبکه رو فقط وقتی وسط بکش که "
        "به حرفش مربوطه — وسط درد دل آمار نریز. "
        "وقتی درباره‌ی یه شخص خاص سوال می‌کنه یا تحلیل رابطه می‌خواد، حتماً از شناخت‌نامه‌ش "
        "(نمره دوستی، شخصیت، ارزش‌ها، هشدارها) استفاده کن و تحلیلت رو مستند بده. "
        "پاسخ‌ها کوتاه (۲ تا ۵ جمله) مگه تحلیل مفصل بخواد. "
        "به فارسی محاوره‌ای و صمیمی. اگه نشانه‌ی ناراحتی عمیق یا مداوم دیدی، با مهربونی پیشنهاد کن "
        "با یه آدم مورد اعتماد یا مشاور هم حرف بزنه — بدون بزرگ‌نمایی.\n\n"
        + persian_policy
    )

    try:
        client, ai_model = _get_ai_client_and_model()
        response = client.chat.completions.create(
            model=ai_model,
            messages=(
                [{"role": "system", "content": system_prompt}]
                + PERSIAN_FEW_SHOTS
                + history
                + [{"role": "user", "content": user_message}]
            ),
            max_tokens=1024,
            temperature=0.6,
        )
        reply = normalize_persian_reply(response.choices[0].message.content)

        # یک فرصت بازنویسی سبک و محدود برای خروجی انگلیسی، رباتیک یا بیش‌ازحد بلند.
        # این مرحله داده‌های خصوصی گراف را دوباره ارسال نمی‌کند.
        quality_issues = persian_quality_issues(reply)
        if quality_issues:
            try:
                rewrite = client.chat.completions.create(
                    model=ai_model,
                    messages=[
                        {"role": "system", "content": persian_policy},
                        {
                            "role": "user",
                            "content": (
                                "پاسخ زیر را با حفظ معنی، به فارسی طبیعی ایران بازنویسی کن. "
                                "مقدمه اضافه نکن و فقط متن نهایی را بده:\n\n" + reply[:3000]
                            ),
                        },
                    ],
                    max_tokens=700,
                    temperature=0.35,
                )
                rewritten = normalize_persian_reply(rewrite.choices[0].message.content)
                if rewritten and not persian_quality_issues(rewritten):
                    reply = rewritten
                    quality_issues = []
            except Exception:
                pass

        if not reply:
            reply = 'ببخش، نتونستم جواب خوبی بسازم. یک بار دیگه برام می‌نویسی؟'

        # ── V8: ذخیره‌ی دوطرفه — درد دل‌ها دیگه گم نمی‌شن ──
        # BUGFIX: صفحه insights هم از همین API استفاده می‌کنه؛ با فلگ ephemeral
        # سوال‌های اون صفحه دیگه حافظه‌ی همدم رو آلوده نمی‌کنن.
        if not data.get('ephemeral'):
            try:
                from .models import ChatMessage
                ChatMessage.objects.create(role='user', content=user_message[:4000],
                                           owner=request.user)
                ChatMessage.objects.create(role='assistant', content=(reply or '')[:4000],
                                           owner=request.user)
            except Exception:
                pass   # جدول migrate نشده — چت بدون حافظه هم کار کنه

        return JsonResponse({'reply': reply, 'style': chat_style})
    except Exception as e:
        return JsonResponse({'error': _ai_error_msg(e)}, status=500)


@login_required
def graph_all_api(request):
    """Return all nodes+edges with community and centrality data for D3 graph."""
    try:
        import networkx as nx
        G, all_nodes, all_rels = _build_graph(request.user)

        if G.number_of_nodes() > 0:
            deg_c   = nx.degree_centrality(G)
            com_map = _community_map(G)
        else:
            deg_c   = {}
            com_map = {}
    except Exception:
        all_nodes = list(Node.objects.filter(owner=request.user))
        all_rels  = list(Relationship.objects.filter(owner=request.user).select_related('source', 'target'))
        deg_c   = {}
        com_map = {}

    # Root node — از user.root_node
    root_id = None
    if request.user.is_authenticated and request.user.root_node_id:
        root_id = str(request.user.root_node_id)

    # گروه‌های دستی (M2M) — prefetch برای performance
    all_nodes_qs = Node.objects.filter(owner=request.user).prefetch_related('groups')
    all_nodes = list(all_nodes_qs)

    # ساخت نگاشت node_id → [group_names]
    node_groups_map = {}
    all_group_names = set()
    for n in all_nodes:
        gnames = [g.name for g in n.groups.all()]
        node_groups_map[n.id] = gnames
        all_group_names.update(gnames)

    # گروه‌های موجود (با رنگ از مدل اگه داره، وگرنه از palette)
    from .models import Group as GroupModel
    db_groups = {g.name: g.color for g in GroupModel.objects.filter(owner=request.user)}
    all_groups_sorted = sorted(all_group_names)
    group_color_map = {}
    for i, g in enumerate(all_groups_sorted):
        group_color_map[g] = db_groups.get(g) or COMMUNITY_PALETTE[i % len(COMMUNITY_PALETTE)]

    # V9: نمره دوستی از شناخت‌نامه
    fscore_map = {}
    try:
        for nid_, d_ in Information.objects.filter(
                node__owner=request.user).values_list('node_id', 'data'):
            if isinstance(d_, dict) and d_.get('friendship_score') is not None:
                fscore_map[nid_] = d_['friendship_score']
    except Exception:
        pass

    node_data = []
    for n in all_nodes:
        c_idx   = com_map.get(n.id, 0)
        gnames  = node_groups_map.get(n.id, [])
        # رنگ: اول گروه اول، وگرنه رنگ community
        color   = group_color_map.get(gnames[0], COMMUNITY_PALETTE[c_idx % len(COMMUNITY_PALETTE)]) if gnames else COMMUNITY_PALETTE[c_idx % len(COMMUNITY_PALETTE)]
        node_data.append({
            "id":         str(n.id),
            "username":   n.username,
            "label":      n.display_name() if hasattr(n, 'display_name') else n.username,
            "image":      n.picture.url if n.picture else None,
            "centrality": round(deg_c.get(n.id, 0), 4),
            "community":  c_idx,
            "groups":     gnames,           # لیست گروه‌ها (M2M)
            "group":      gnames[0] if gnames else '',   # backward compat
            "color":      color,
            "fscore":     fscore_map.get(n.id),
        })

    # ── V4: سلامت رابطه — رنگ یال‌های متصل به root ──
    health_map = {}
    health_counts = {}
    try:
        from .health import compute_health, health_summary
        health_map = compute_health(request.user)
        health_counts = health_summary(health_map)
    except Exception:
        pass

    root_id_int = request.user.root_node_id if request.user.is_authenticated else None

    edge_data = []
    for r in all_rels:
        e = {
            "source":   str(r.source_id),
            "target":   str(r.target_id),
            "label":    r.rel or "",
            "strength": r.strength,
        }
        # فقط یال‌هایی که یه سرشون root است رنگ سلامت می‌گیرن
        if root_id_int and root_id_int in (r.source_id, r.target_id):
            other = r.target_id if r.source_id == root_id_int else r.source_id
            h = health_map.get(other)
            if h:
                e["health_status"] = h["status"]
                e["health_color"]  = h["color"]          # None برای unknown
                e["health_score"]  = h["score"]
                e["days_since"]    = h["days_since"]
        edge_data.append(e)

    return JsonResponse({
        "nodes":         node_data,
        "edges":         edge_data,
        "root_id":       root_id,
        "all_groups":    all_groups_sorted,
        "group_colors":  group_color_map,
        "health_counts": health_counts,
    })


# ════════════════════════════════════════════════════════════════
# Settings (root node)
# ════════════════════════════════════════════════════════════════

@login_required
def settings_view(request):
    # BUGFIX: دکمه «تنظیم به عنوان root» توی گراف به اینجا POST می‌زنه؛
    # قبلاً POST نادیده گرفته می‌شد و root واقعاً ذخیره نمی‌شد!
    if request.method == 'POST' and request.POST.get('root_node'):
        try:
            request.user.root_node = Node.objects.get(
                pk=request.POST['root_node'], owner=request.user)
            request.user.save(update_fields=['root_node'])
            return JsonResponse({'ok': True})
        except (Node.DoesNotExist, ValueError):
            return JsonResponse({'error': 'نود پیدا نشد'}, status=404)
    # صفحه تنظیمات به پروفایل ادغام شده
    return redirect('profile')


# ════════════════════════════════════════════════════════════════
# Journal
# ════════════════════════════════════════════════════════════════

@login_required
def journal_view(request):
    user = request.user
    entries = JournalEntry.objects.filter(owner=user).prefetch_related('images', 'mentioned_nodes')[:20]
    nodes_for_mention = list(Node.objects.filter(owner=user).values('username', 'name', 'first_name', 'last_name', 'nickname'))

    # Collect all unique tags from user's entries
    all_tags = []
    for e in JournalEntry.objects.filter(owner=user).values_list('tags', flat=True):
        if e:
            all_tags.extend(e)
    all_tags = sorted(set(all_tags))

    # All node usernames for people filter
    all_node_usernames = list(Node.objects.filter(owner=user).values_list('username', flat=True))

    # All distinct moods
    all_moods = list(
        JournalEntry.objects.filter(owner=user).exclude(mood='').values_list('mood', flat=True).distinct()[:20]
    )

    return render(request, 'journal/journal.html', {
        'entries': entries,
        'nodes_json': json.dumps(nodes_for_mention, ensure_ascii=False),
        'all_tags_json': json.dumps(all_tags, ensure_ascii=False),
        'all_nodes_json': json.dumps(all_node_usernames, ensure_ascii=False),
        'all_moods_json': json.dumps(all_moods, ensure_ascii=False),
    })


@login_required
@csrf_exempt
def journal_image_upload_api(request):
    """Upload an image for a journal entry (before or after entry creation)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    image_file = request.FILES.get('image')
    if not image_file:
        return JsonResponse({'error': 'فایلی ارسال نشد'}, status=400)
    img = JournalImage.objects.create(image=image_file)
    return JsonResponse({'id': img.id, 'url': img.image.url})


@login_required
@csrf_exempt
def journal_analyze_api(request):
    """Diary text → rich structured extraction with root-node awareness."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        body = json.loads(request.body)
        text = body.get('text', '').strip()
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    if not text:
        return JsonResponse({'error': 'متن خالی است'}, status=400)

    # If existing entry_id passed, analyze an already-saved entry
    existing_entry_id = body.get('entry_id')

    # ── Who is "me"? ─────────────────────────────────────────
    root_username = None
    root_display  = None
    if request.user.is_authenticated and request.user.root_node:
        root_username = request.user.root_node.username
        root_display  = request.user.root_node.display_name()

    me_info = (
        f'نویسنده این خاطره "{root_display}" با username "{root_username}" است. '
        f'هر جا "من" یا اول شخص مفرد آمد یعنی همین شخص. '
        f'برای نویسنده نود جدید نساز — روابط را به username "{root_username}" وصل کن.'
        if root_username else
        'نویسنده مشخص نیست — اگر "من" آمد، username آن را "me" بگذار.'
    )

    owner_filter = {'owner': request.user} if request.user.is_authenticated else {}
    existing_nodes = ', '.join(Node.objects.filter(**owner_filter).values_list('username', flat=True)[:80]) or 'هیچ'

    prompt = f"""تو یک تحلیلگر هوشمند خاطرات شخصی هستی.

{me_info}
نودهای موجود در شبکه: {existing_nodes}

متن خاطره:
\"\"\"
{text}
\"\"\"

دقیقاً یک JSON برگردان. هیچ متنی خارج از JSON ننویس:

{{
  "nodes": [
    {{"username": "نام_انگلیسی_بدون_فاصله", "name": "نام کامل فارسی"}}
  ],
  "relationships": [
    {{
      "from": "username_الف",
      "to": "username_ب",
      "type": "نوع رابطه (دوست، همکار، تیم‌لید، برادر، ...)",
      "strength": 3
    }}
  ],
  "events": [
    {{"title": "عنوان", "date": "YYYY-MM-DD یا null", "description": "توضیح"}}
  ],
  "attributes": [
    {{
      "username": "نام_کاربری",
      "personality":          ["ویژگی شخصیتی"],
      "mood":                 "خلق‌وخو در این خاطره",
      "interests":            ["علایق"],
      "preferences":          ["سلایق و ترجیحات"],
      "strengths":            ["نقاط قوت"],
      "weaknesses":           ["نقاط ضعف"],
      "goals":                ["اهداف"],
      "values":               ["ارزش‌ها و باورها"],
      "communication_style":  "شیوه ارتباطی",
      "relationship_quality": "کیفیت رابطه با نویسنده",
      "notable_facts":        ["نکات مهم دیگر"]
    }}
  ],
  "my_mood": "خلق‌وخوی نویسنده در این خاطره",
  "my_insights": ["چیزی که نویسنده فهمیده یا احساس کرده"],
  "summary": "یک جمله خلاصه"
}}

قوانین:
1. فقط افراد واقعی ذکر شده در متن — نه حدس
2. نویسنده (من) نود جدید نمی‌شود — روابط از "{root_username or 'me'}" شروع می‌شود
3. attributes فقط برای افراد دیگر (نه نویسنده) — مگر نویسنده چیزی درباره خودش گفته
4. strength: عدد ۱ تا ۵ — ۵ خیلی قوی
5. هر آرایه بدون اطلاعات را [] بگذار"""

    try:
        import re
        client, ai_model = _get_ai_client_and_model()
        response = client.chat.completions.create(
            model=ai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        content = response.choices[0].message.content
        match = re.search(r'\{[\s\S]*\}', content)
        result = json.loads(match.group() if match else content)
        result['_root_username'] = root_username

        # Parse tags from body
        raw_tags = body.get('tags', [])
        if isinstance(raw_tags, str):
            raw_tags = [t.strip() for t in raw_tags.split(',') if t.strip()]

        # Save/update entry
        entry_date_str = body.get('entry_date', '').strip()
        entry_date = None
        if entry_date_str:
            try:
                from datetime import date
                entry_date = date.fromisoformat(entry_date_str)
            except Exception:
                pass

        entry_kind = body.get('entry_kind', 'moment')
        if entry_kind not in dict(JournalEntry.ENTRY_KIND_CHOICES):
            entry_kind = 'moment'
        occurred_at = timezone.now()
        raw_occurred_at = body.get('occurred_at', '')
        if raw_occurred_at:
            try:
                from datetime import datetime
                occurred_at = datetime.fromisoformat(raw_occurred_at.replace('Z', '+00:00'))
                if timezone.is_naive(occurred_at):
                    occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())
            except (TypeError, ValueError):
                pass
        if entry_date is None:
            entry_date = timezone.localdate(occurred_at)

        entry_owner = request.user if request.user.is_authenticated else None
        if existing_entry_id:
            try:
                entry = JournalEntry.objects.get(id=existing_entry_id, owner=entry_owner)
                entry.ai_analyzed = True
                entry.mood = result.get('my_mood', '')
                entry.entry_kind = entry_kind
                entry.occurred_at = occurred_at
                if raw_tags:
                    entry.tags = raw_tags
                entry.save(update_fields=['ai_analyzed', 'mood', 'tags', 'entry_kind', 'occurred_at'])
            except JournalEntry.DoesNotExist:
                entry = JournalEntry.objects.create(
                    text=text, entry_date=entry_date, occurred_at=occurred_at, entry_kind=entry_kind, tags=raw_tags,
                    mood=result.get('my_mood', ''), ai_analyzed=True, owner=entry_owner,
                )
        else:
            entry = JournalEntry.objects.create(
                text=text, entry_date=entry_date, occurred_at=occurred_at, entry_kind=entry_kind, tags=raw_tags,
                mood=result.get('my_mood', ''), ai_analyzed=True, owner=entry_owner,
            )
        result['_entry_id'] = entry.id
        try:
            from .extraction import extract_text
            result['_suggestions_created'] = len(extract_text(request.user, entry.text, 'journal', entry.id))
        except Exception:
            result['_suggestions_created'] = 0

        # Link any pre-uploaded images to this entry
        image_ids = body.get('image_ids', [])
        if image_ids:
            JournalImage.objects.filter(id__in=image_ids, entry__isnull=True).update(entry=entry)

        try:
            from .views_journal_extra import _extract_profile_media_from_journal
            _extract_profile_media_from_journal(entry)
        except Exception:
            pass

        return JsonResponse({'result': result})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'AI پاسخ معتبر JSON نداد', 'raw': content[:600]}, status=500)
    except Exception as e:
        return JsonResponse({'error': _ai_error_msg(e)}, status=500)


@login_required
@csrf_exempt
def journal_apply_api(request):
    """Apply extracted entities + rich attributes to DB."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    from datetime import date as today_date

    created = {'nodes': [], 'relationships': [], 'events': [], 'attributes': []}

    # ── Privacy maps از frontend ───────────────────────────
    node_privacy = data.get('node_privacy', {})   # {username: true/false}
    rel_privacy  = data.get('rel_privacy', {})    # {"from||to||type": true/false}
    # ── Public node links: {username: {id, display_name, owner_username, ...}} ──
    node_links   = data.get('node_links', {})     # نودهایی که از شبکه عمومی انتخاب شدن

    # ── Resolve root node ──────────────────────────────────
    root_node     = None
    root_username = (data.get('_root_username') or '').strip()
    req_user = request.user if request.user.is_authenticated else None
    if req_user and req_user.root_node:
        root_node = req_user.root_node
        if not root_username:
            root_username = root_node.username

    # aliasهایی که همیشه به نویسنده (root) اشاره دارن
    _me_aliases = {'me', 'من', 'خودم', 'نویسنده', 'i', 'myself'}
    if root_username:
        _me_aliases.add(root_username.lower())

    def resolve_node(uname):
        uname = (uname or '').strip()
        if uname.lower() in _me_aliases and root_node:
            return root_node
        if req_user:
            try:
                return Node.objects.get(username=uname, owner=req_user)
            except Node.DoesNotExist:
                return None
        return None

    # ── Create nodes (never create root) ──────────────────

    for nd in data.get('nodes', []):
        username = (nd.get('username') or '').strip()
        if not username or username.lower() in _me_aliases:
            continue
        is_pub   = bool(node_privacy.get(username, False))
        link     = node_links.get(username)          # اگه از شبکه عمومی لینک شده
        defaults = {'name': nd.get('name', ''), 'is_public': is_pub}

        if link:
            # داده‌های عمومی منبع رو کپی کن + قفل username
            try:
                src_node = Node.objects.get(id=link['id'])
                defaults.update({
                    'first_name':     src_node.first_name,
                    'last_name':      src_node.last_name,
                    'nickname':       src_node.nickname,
                    'career':         src_node.career,
                    'username_locked': True,
                    'imported_from':  src_node.owner,
                })
                if src_node.picture:
                    defaults['picture'] = src_node.picture
            except Node.DoesNotExist:
                pass

        if req_user:
            defaults['owner'] = req_user
        node, is_new = Node.objects.get_or_create(
            username=username,
            owner=req_user,
            defaults=defaults,
        )
        created['nodes'].append({
            'id':           node.id,
            'username':     node.username,
            'display_name': node.display_name(),
            'is_new':       is_new,
        })
        if not is_new:
            changed = False
            if node_privacy.get(username) is not None and node.is_public != is_pub:
                node.is_public = is_pub
                changed = True
            if link and not node.imported_from:
                try:
                    src = Node.objects.get(id=link['id'])
                    node.imported_from   = src.owner
                    node.username_locked = True
                    changed = True
                except Node.DoesNotExist:
                    pass
            if changed:
                node.save()

    # ── Relationships — یک یال per pair (merge در هر جهت) ──
    for rd in data.get('relationships', []):
        frm      = (rd.get('from') or '').strip()
        to       = (rd.get('to')   or '').strip()
        rel_type = (rd.get('type') or '').strip()
        strength = min(5, max(1, int(rd.get('strength') or 3)))
        src = resolve_node(frm)
        tgt = resolve_node(to)
        if not src or not tgt or src == tgt:
            continue
        rel_key = f"{frm}||{to}||{rel_type}"
        is_pub  = bool(rel_privacy.get(rel_key, False))

        # بررسی یال موجود در هر دو جهت
        existing = Relationship.objects.filter(
            Q(source=src, target=tgt) | Q(source=tgt, target=src),
            owner=req_user
        ).first()

        if existing:
            # اضافه کردن نوع رابطه به یال موجود — اگه تکراری نبود
            cur_types = [t.strip() for t in (existing.rel or '').split('،') if t.strip()]
            changed = False
            if rel_type and rel_type not in cur_types:
                cur_types.append(rel_type)
                existing.rel = '، '.join(cur_types)
                changed = True
            if existing.strength < strength:
                existing.strength = strength
                changed = True
            if rel_privacy.get(rel_key) is not None and existing.is_public != is_pub:
                existing.is_public = is_pub
                changed = True
            if changed:
                existing.save()
        else:
            Relationship.objects.create(
                source=src, target=tgt, rel=rel_type,
                strength=strength, is_public=is_pub, owner=req_user
            )
            created['relationships'].append(f"{src.username}→{tgt.username}")

    # ── Events ────────────────────────────────────────────
    from django.db import ProgrammingError as _PErrJ
    for ed in data.get('events', []):
        title = (ed.get('title') or '').strip()
        if not title:
            continue
        date_str = ed.get('date')
        try:
            from datetime import date
            event_date = date.fromisoformat(date_str) if date_str else today_date.today()
        except Exception:
            event_date = today_date.today()
        try:
            ev = Event.objects.create(
                title=title, date=event_date,
                description=ed.get('description', ''),
                owner=req_user,
            )
            if root_node:
                ev.participants.add(root_node)
            created['events'].append(title)
        except _PErrJ:
            # migration هنوز نخورده — raw INSERT
            from django.db import connection as _conn
            with _conn.cursor() as _cur:
                _cur.execute(
                    "INSERT INTO main_event (title, date, description, owner_id) VALUES (%s,%s,%s,%s)",
                    [title, event_date, ed.get('description', ''), req_user.id]
                )
                ev_id = _cur.lastrowid
            if root_node:
                with _conn.cursor() as _cur:
                    _cur.execute(
                        "INSERT OR IGNORE INTO main_event_participants (event_id, node_id) VALUES (%s,%s)",
                        [ev_id, root_node.id]
                    )
            created['events'].append(title)

    # ── Rich attributes → Information model ───────────────
    LIST_KEYS = ['personality', 'interests', 'preferences', 'strengths',
                 'weaknesses', 'goals', 'values', 'notable_facts']
    STR_KEYS  = ['mood', 'communication_style', 'relationship_quality']

    for attr in data.get('attributes', []):
        uname = (attr.get('username') or '').strip()
        node  = resolve_node(uname)
        if not node:
            continue

        info_qs = Information.objects.filter(node=node, data__has_key='_journal_attributes')
        if info_qs.exists():
            info   = info_qs.first()
            stored = dict(info.data or {})
        else:
            stored = {'_journal_attributes': True}

        for key in LIST_KEYS:
            new_vals = attr.get(key) or []
            if new_vals:
                existing = stored.get(key) or []
                stored[key] = list(dict.fromkeys(existing + new_vals))

        for key in STR_KEYS:
            if attr.get(key):
                stored[key] = attr[key]

        if attr.get('notes'):
            prev = stored.get('notes', '')
            stored['notes'] = (prev + '\n' + attr['notes']).strip() if prev else attr['notes']

        if info_qs.exists():
            info.data = stored
            info.save()
        else:
            Information.objects.create(node=node, visibility='private', data=stored)

        created['attributes'].append(node.username)

    # ── Link mentioned nodes to JournalEntry ──────────────
    entry_id = data.get('_entry_id')
    if entry_id:
        try:
            entry = JournalEntry.objects.get(id=entry_id, owner=req_user)
            # Collect all node usernames that appeared in relationships
            mentioned = set()
            for rd in data.get('relationships', []):
                for side in ('from', 'to'):
                    u = (rd.get(side) or '').strip()
                    if u and u not in ('me', 'من', root_username):
                        mentioned.add(u)
            for nd in data.get('nodes', []):
                u = (nd.get('username') or '').strip()
                if u:
                    mentioned.add(u)

            mentioned_nodes_resolved = []
            for uname in mentioned:
                n = resolve_node(uname)
                if n:
                    entry.mentioned_nodes.add(n)
                    mentioned_nodes_resolved.append(n)
            if root_node:
                entry.mentioned_nodes.add(root_node)

            # ── V4: ذکر در ژورنال = تعامل خودکار ──────────────
            # برای هر نود ذکرشده (غیر از root) یه Interaction با
            # kind='journal' ثبت می‌شه — روزی یکی، تکراری نمی‌سازه.
            try:
                from .models import Interaction
                ix_date = entry.entry_date or timezone.localdate()
                auto_logged = 0
                for n in mentioned_nodes_resolved:
                    if root_node and n.id == root_node.id:
                        continue
                    _, was_new = Interaction.objects.get_or_create(
                        node=n, owner=req_user, kind='journal', date=ix_date,
                        defaults={'feeling': 0,
                                  'note': (entry.text or '')[:100]},
                    )
                    if was_new:
                        auto_logged += 1
                created['auto_interactions'] = auto_logged
            except Exception:
                pass   # جدول هنوز migrate نشده — مشکلی نیست
        except JournalEntry.DoesNotExist:
            pass

    return JsonResponse({'created': created})


# ─────────────────────────────────────────────────────────────────
# Public Node Search API
# ─────────────────────────────────────────────────────────────────

@login_required
def export_graph(request):
    """⬇ Export کامل گراف کاربر به JSON — قابل دانلود."""
    from datetime import date as date_type
    user = request.user

    nodes = list(Node.objects.filter(owner=user).values(
        'id', 'username', 'first_name', 'last_name', 'nickname',
        'career', 'birth_day', 'phone_number', 'name', 'is_public',
        'username_locked', 'group',
    ))
    rels = list(Relationship.objects.filter(owner=user).values(
        'id', 'rel', 'source__username', 'target__username',
        'strength', 'status', 'met_at', 'is_public',
    ))
    entries = list(JournalEntry.objects.filter(owner=user).values(
        'id', 'text', 'entry_date', 'tags', 'mood', 'ai_analyzed', 'created_at',
    ))

    # dates → strings for JSON
    for n in nodes:
        if n['birth_day']:
            n['birth_day'] = str(n['birth_day'])
    for r in rels:
        if r['met_at']:
            r['met_at'] = str(r['met_at'])
    for e in entries:
        e['created_at'] = str(e['created_at'])
        if e['entry_date']:
            e['entry_date'] = str(e['entry_date'])

    data = {
        'version':     '3',
        'exported_at': str(timezone.now()),
        'username':    user.username,
        'nodes':       nodes,
        'relationships': rels,
        'journal_entries': entries,
    }
    response = HttpResponse(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8',
    )
    response['Content-Disposition'] = f'attachment; filename="familygraph_{user.username}.json"'
    return response


@login_required
@require_GET
def public_node_search(request):
    """جستجوی نودهای عمومی از حساب‌های عمومی — برای ساجست هنگام ادد کردن نود."""
    from django.db.models import Q
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    nodes = Node.objects.filter(
        is_public=True,
        owner__is_public=True,
    ).exclude(
        owner=request.user
    ).filter(
        Q(username__icontains=q)   |
        Q(first_name__icontains=q) |
        Q(last_name__icontains=q)  |
        Q(nickname__icontains=q)   |
        Q(name__icontains=q)
    ).select_related('owner')[:10]

    results = []
    for n in nodes:
        results.append({
            'id':            n.id,
            'username':      n.username,
            'display_name':  n.display_name(),
            'first_name':    n.first_name,
            'last_name':     n.last_name,
            'nickname':      n.nickname,
            'career':        n.career,
            'owner_username': n.owner.username if n.owner else '',
            'picture_url':   n.picture.url if n.picture else '',
        })
    return JsonResponse({'results': results})


# ─────────────────────────────────────────────────────────────────
# Quick Node Update (for inline profile completion in journal)
# ─────────────────────────────────────────────────────────────────

@login_required
@csrf_exempt
def node_quick_update(request, pk):
    """آپدیت سریع فیلدهای پایه یک نود — برای فرم inline در journal."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    node = get_object_or_404(Node, pk=pk, owner=request.user)

    FIELDS = ['first_name', 'last_name', 'nickname', 'career', 'phone_number']
    for field in FIELDS:
        if field in data:
            setattr(node, field, (data[field] or '').strip())

    if 'birth_day' in data:
        val = (data['birth_day'] or '').strip()
        if val:
            try:
                from datetime import date as _date
                node.birth_day = _date.fromisoformat(val)
            except Exception:
                pass
        else:
            node.birth_day = None

    node.save()
    return JsonResponse({'ok': True, 'display_name': node.display_name()})


@login_required
@csrf_exempt
def node_create_from_image(request):
    """ایجاد نود از عکس drag-drop شده روی گراف — wizard step per image."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    import re, uuid as _uuid

    req_user     = request.user
    target_id    = (request.POST.get('target_id') or '').strip()
    first_name   = (request.POST.get('first_name')   or '').strip()
    last_name    = (request.POST.get('last_name')    or '').strip()
    career       = (request.POST.get('career')       or '').strip()
    phone        = (request.POST.get('phone_number') or '').strip()
    birth_day    = (request.POST.get('birth_day')    or '').strip()
    is_public    = request.POST.get('is_public') == 'true'
    rel_type     = (request.POST.get('rel_type')     or '').strip()
    try:
        rel_strength = min(5, max(1, int(request.POST.get('rel_strength') or 3)))
    except (ValueError, TypeError):
        rel_strength = 3

    image = request.FILES.get('image')

    if not target_id:
        return JsonResponse({'error': 'target_id required'}, status=400)
    target = get_object_or_404(Node, pk=target_id, owner=req_user)

    # ── Generate unique username از نام ──────────────────
    raw  = f"{first_name} {last_name}".strip()
    base = re.sub(r'[^\w]', '_', raw).lower().strip('_') if raw else 'person'
    base = base or 'person'
    username = base
    counter  = 1
    while Node.objects.filter(username=username, owner=req_user).exists():
        username = f"{base}_{counter}"
        counter += 1

    # ── Create node ───────────────────────────────────────
    new_node = Node(
        username=username,
        owner=req_user,
        first_name=first_name,
        last_name=last_name,
        career=career,
        phone_number=phone,
        is_public=is_public,
    )
    if birth_day:
        try:
            from datetime import date as _date
            new_node.birth_day = _date.fromisoformat(birth_day)
        except Exception:
            pass
    if image:
        new_node.picture = image
    new_node.save()

    # ── Create relationship (merge اگه موجود بود) ────────
    existing = Relationship.objects.filter(
        Q(source=target, target=new_node) | Q(source=new_node, target=target),
        owner=req_user
    ).first()
    if not existing:
        Relationship.objects.create(
            source=target, target=new_node,
            rel=rel_type, strength=rel_strength,
            is_public=is_public, owner=req_user,
        )

    return JsonResponse({
        'ok': True,
        'node': {
            'id':           new_node.id,
            'username':     new_node.username,
            'display_name': new_node.display_name(),
            'picture_url':  new_node.picture.url if new_node.picture else None,
        }
    })


@login_required
@csrf_exempt
def relationship_quick_create(request):
    """ایجاد یا merge یال از طریق drag روی گراف."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    req_user  = request.user
    source_id = data.get('source_id')
    target_id = data.get('target_id')
    rel_type  = (data.get('rel_type') or '').strip()
    is_public = bool(data.get('is_public', False))
    try:
        strength = min(5, max(1, int(data.get('strength') or 3)))
    except (ValueError, TypeError):
        strength = 3

    source = get_object_or_404(Node, pk=source_id, owner=req_user)
    target = get_object_or_404(Node, pk=target_id, owner=req_user)

    if source == target:
        return JsonResponse({'error': 'نود نمیتونه با خودش رابطه داشته باشه'}, status=400)

    # ── merge یا ایجاد — مثل journal_apply_api ────────────
    existing = Relationship.objects.filter(
        Q(source=source, target=target) | Q(source=target, target=source),
        owner=req_user
    ).first()

    if existing:
        cur_types = [t.strip() for t in (existing.rel or '').split('،') if t.strip()]
        changed = False
        if rel_type and rel_type not in cur_types:
            cur_types.append(rel_type)
            existing.rel = '، '.join(cur_types)
            changed = True
        if existing.strength < strength:
            existing.strength = strength
            changed = True
        if changed:
            existing.save()
        return JsonResponse({'ok': True, 'merged': True})

    Relationship.objects.create(
        source=source, target=target,
        rel=rel_type, strength=strength,
        is_public=is_public, owner=req_user,
    )
    return JsonResponse({'ok': True, 'merged': False})
