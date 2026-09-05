import json
import logging
import os
import re
import time
from datetime import date, timedelta
from django.db.models import Q, ProtectedError, Prefetch
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone

from .forms import NodeForm, NodeContactDetailsForm, RelationshipForm, EventForm
from .models import Relationship, AppSettings, JournalEntry, JournalImage, AlertAction, Group
from django.core.cache import cache
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from .models import Group, Node, NodeContactDetails, Information, Event
from .uploads import UploadValidationError, normalize_image_upload
from .text_utils import finglish_slug
from .utils_jalali import jalali_str, parse_date_input
from django.views.generic import ListView
from django.views.generic import TemplateView

def _ai_error_msg(e: Exception) -> str:
    s = str(e)
    if '429' in s or 'rate limit' in s.lower() or 'Rate limit' in s:
        return ('سهمیهٔ سرویس آنلاین فعلاً تمام شده 😔 — پاسخ‌گویی با مدل محلی ادامه پیدا می‌کند؛ '
                'برای استفادهٔ آنلاین باید سهمیه بعداً آزاد شود یا key سرویس دیگری تنظیم کنی.')
    return f'خطای AI: {s[:200]}'

_NONANSWER_RE = re.compile(
    r'^\s*("?)(user|response|assistant|prompt)?\s*safety\s*:',
    re.IGNORECASE,
)


def _is_model_nonanswer(text: str) -> bool:
    """True when the model returned classifier/guard scaffolding instead of a
    real answer (OpenRouter's free auto-router sometimes lands on a safety
    model). Such output must never reach the user as the reply."""
    t = (text or '').strip().strip('"\'` ').lower()
    if not t:
        return False
    if _NONANSWER_RE.match(text or ''):
        return True
    if t in {'safe', 'unsafe', 'safe.', 'unsafe.'}:
        return True
    if re.fullmatch(r's\d{1,2}(\s*,\s*s\d{1,2})*', t):
        return True
    if 'response safety:' in t or 'user safety:' in t:
        return True
    return False


def _get_ai_client_and_model():
    """Return the project's configured OpenAI-compatible client and model."""
    from .views_smart_features import _ai_client, _model

    client, configured, _provider = _ai_client()
    if not configured:
        raise RuntimeError(
            'AI is not configured. Set OPENROUTER_API_KEY or run Ollama locally.'
        )
    return client, _model()


class ChatResponseDeadline(TimeoutError):
    """Raised when chat must stop provider work and return a local response."""


def _chat_response_deadline_seconds():
    try:
        configured = float(os.environ.get('CHAT_RESPONSE_DEADLINE', '8'))
    except (TypeError, ValueError):
        configured = 8.0
    # The browser aborts after nine seconds; reserve time for JSON + network.
    return min(8.0, max(2.0, configured))


def _chat_provider_name(client):
    from .views_smart_features import _AIClientFailover, _OllamaClient

    if isinstance(client, _AIClientFailover):
        return 'openrouter+ollama'
    if isinstance(client, _OllamaClient):
        return 'ollama'
    base_url = str(getattr(client, 'base_url', '')).lower()
    for provider in ('openrouter', 'groq', 'mistral', 'gemini'):
        if provider in base_url or (provider == 'gemini' and 'googleapis' in base_url):
            return provider
    return 'custom' if base_url else ''


def _record_chat_metric(user, started, *, provider, requested_model='', actual_model='',
                        status='success', attempts=0, fallback_used=False):
    """Store operational numbers only; never prompt, reply, or person data."""
    try:
        from .models import AIRequestMetric
        AIRequestMetric.objects.create(
            owner=user, feature='chat', provider=provider,
            requested_model=str(requested_model or '')[:120],
            actual_model=str(actual_model or '')[:120],
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            deadline_ms=round(_chat_response_deadline_seconds() * 1000),
            status=status, attempts=max(0, attempts), fallback_used=fallback_used,
        )
    except Exception:
        pass


def _fast_degraded_chat_reply(message):
    """Useful sub-millisecond fallback when free/local generation is unavailable."""
    normalized = _fa_norm(message)
    if any(word in normalized for word in ('ناراحتم', 'غمگین', 'دلم گرفته', 'تنها', 'گریه')):
        return 'می‌فهمم که الان سنگینه. من اینجام؛ دوست داری از چیزی که بیشتر آزارت داده بگی؟'
    if any(word in normalized for word in ('عصبانی', 'دعوا', 'قهر', 'دلخور')):
        return 'حق داری درگیرش باشی. اگر بخوای، بگو دقیقاً چه اتفاقی افتاد تا بدون قضاوت باهم بازش کنیم.'
    if any(word in normalized for word in ('استرس', 'اضطراب', 'نگران')):
        return 'می‌فهمم که نگرانی خسته‌کننده است. الان مهم‌ترین چیزی که ذهنت را درگیر کرده چیست؟'
    return (
        'بخش گفت‌وگوی آزاد الان به سقف زمان پاسخ رسید. '
        'اگر اسم یکی از آدم‌های گرافت را بگویی و تحلیل رابطه بخواهی، از داده‌های ثبت‌شده فوری جواب می‌دهم.'
    )

COMMUNITY_PALETTE = [
    "#6366f1","#ec4899","#f59e0b","#10b981","#3b82f6",
    "#ef4444","#8b5cf6","#06b6d4","#f97316","#14b8a6",
]

def _build_graph(user):
    """Build a networkx Graph from DB filtered by user. Returns (G, nodes_list, rels_list)."""
    import networkx as nx
    all_nodes = list(Node.objects.filter(owner=user, merged_into__isnull=True))
    all_rels  = list(Relationship.objects.filter(
        owner=user,
        source__owner=user,
        target__owner=user,
        source__merged_into__isnull=True,
        target__merged_into__isnull=True,
    ).select_related('source', 'target'))
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
        snoozes = (user.feature_overrides or {}).get('daily_snoozed_until', {})

        def is_snoozed(key):
            try:
                return date.fromisoformat(snoozes.get(key, '')) >= today
            except (TypeError, ValueError):
                return False

        def is_muted(key):
            try:
                return date.fromisoformat((user.feature_overrides or {}).get('daily_muted_until', {}).get(key, '')) >= today
            except (TypeError, ValueError):
                return False
        root_id = user.root_node_id

        nodes = Node.objects.filter(
            owner=user, merged_into__isnull=True,
        ).exclude(pk=root_id)
        pinned_people = list(
            Node.objects.filter(owner=user, is_pinned=True, merged_into__isnull=True)
            .exclude(pk=root_id).order_by('username')[:6]
        )
        relationships = Relationship.objects.filter(owner=user)
        node_map = {node.id: node for node in nodes}

        attention = []
        try:
            from .health import compute_health, attention_priority
            health = compute_health(user)
            priority = attention_priority(user, health)
            for node_id, item in health.items():
                node = node_map.get(node_id)
                if not node:
                    continue
                prio = priority.get(node_id, {'score': 0.0, 'factors': []})
                # Surface anyone the ranking flags, not only red/yellow health —
                # an overdue follow-up or a bad mood can matter on its own.
                if item.get('status') not in ('red', 'yellow') and prio['score'] < 25:
                    continue
                attention.append({
                    'node': node,
                    'status': item.get('status'),
                    'score': item.get('score'),
                    'days_since': item.get('days_since'),
                    'expected': item.get('expected'),
                    'priority': round(prio['score']),
                    'reasons': prio['factors'],
                })
            attention.sort(key=lambda item: -item['priority'])
        except Exception:
            pass

        due_followups = []
        overdue_followups = []
        try:
            from .models import FollowUp
            open_followups = FollowUp.objects.filter(owner=user, node__owner=user, done=False)
            due_followups = list(
                open_followups
                .select_related('node')
                .order_by('due_date', '-created_at')[:4]
            )
            overdue_followups = list(
                open_followups.filter(due_date__lt=today)
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
            .prefetch_related(Prefetch(
                'participants', queryset=Node.objects.filter(owner=user),
            ))
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
        if not checkin_done and not is_snoozed('checkin') and not is_muted('checkin'):
            today_actions.append({'icon': '⚡', 'title': 'چک‌این امروز',
                                  'note': 'حال و انرژی امروزت را ثبت کن.', 'url': '/checkin/'})
        for item in attention[:2]:
            if is_snoozed(f'node-{item["node"].id}') or is_muted(f'node-{item["node"].id}'):
                continue
            today_actions.append({'icon': '💬', 'title': f'یک قدم برای {item["node"].display_name()}',
                                  'note': 'مدتی از آخرین تعامل گذشته است.',
                                  'url': f'/nodes/{item["node"].id}/'})
        if pending_suggestions and not is_snoozed('suggestions') and not is_muted('suggestions'):
            today_actions.append({'icon': '✨', 'title': f'{pending_suggestions} پیشنهاد منتظر تصمیم',
                                  'note': 'حافظهٔ AI را مرور و اصلاح کن.', 'url': '/extractions/'})

        context.update({
            'today': today,
            'attention': attention[:3],
            'due_followups': due_followups,
            'overdue_followups': overdue_followups,
            'upcoming_events': upcoming_events,
            'recent_memories': recent_memories,
            'checkin_done': checkin_done,
            'open_debts': open_debts,
            'pending_suggestions': pending_suggestions,
            # A daily briefing should create focus, not another task list.
            'today_actions': today_actions[:3],
            'people_count': nodes.count(),
            'pinned_people': pinned_people,
            'is_new_workspace': not nodes.exists(),
            'relationship_count': relationships.count(),
            'onboarding_ready': bool(
                root_id and Information.objects.filter(node_id=root_id).exists()
            ),
        })
        return context


@login_required
@require_http_methods(['POST'])
def daily_action_snooze_api(request):
    try:
        body = json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return JsonResponse({'error': 'JSON نامعتبر است.'}, status=400)
    key = str(body.get('key', ''))
    if key not in {'checkin', 'suggestions'} and (not key.startswith('node-') or not key[5:].isdigit() or not Node.objects.filter(owner=request.user, pk=key[5:]).exists()):
        return JsonResponse({'error': 'پیشنهاد نامعتبر است.'}, status=400)
    overrides = dict(request.user.feature_overrides or {})
    snoozes = dict(overrides.get('daily_snoozed_until') or {})
    snoozes[key] = (timezone.localdate() + timedelta(days=1)).isoformat()
    overrides['daily_snoozed_until'] = snoozes
    request.user.feature_overrides = overrides
    request.user.save(update_fields=['feature_overrides'])
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['POST'])
def daily_action_feedback_api(request):
    try:
        body = json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return JsonResponse({'error': 'JSON نامعتبر است.'}, status=400)
    key = str(body.get('key', ''))
    if key not in {'checkin', 'suggestions'} and (not key.startswith('node-') or not key[5:].isdigit() or not Node.objects.filter(owner=request.user, pk=key[5:]).exists()):
        return JsonResponse({'error': 'پیشنهاد نامعتبر است.'}, status=400)
    overrides = dict(request.user.feature_overrides or {})
    muted = dict(overrides.get('daily_muted_until') or {})
    muted[key] = (timezone.localdate() + timedelta(days=30)).isoformat()
    overrides['daily_muted_until'] = muted
    request.user.feature_overrides = overrides
    request.user.save(update_fields=['feature_overrides'])
    return JsonResponse({'ok': True})


class NodeListView(LoginRequiredMixin, ListView):
    model = Node
    template_name = 'nodes/node_list.html'
    context_object_name = 'nodes'
    paginate_by = 24

    def get_queryset(self):
        queryset = Node.objects.filter(owner=self.request.user, merged_into__isnull=True).select_related('owner')
        query = self.request.GET.get('q', '').strip()[:80]
        if query:
            normalized = query.replace('ي', 'ی').replace('ك', 'ک')
            variants = {
                query, normalized, normalized.replace('ی', 'ي'), normalized.replace('ک', 'ك'),
                normalized.replace('ی', 'ي').replace('ک', 'ك'),
            }
            search_filter = Q()
            for term in variants:
                search_filter |= (
                    Q(username__icontains=term) | Q(name__icontains=term) |
                    Q(first_name__icontains=term) | Q(last_name__icontains=term) |
                    Q(nickname__icontains=term) | Q(career__icontains=term)
                )
            queryset = queryset.filter(search_filter)
        group_id = self.request.GET.get('group', '').strip()
        if group_id.isdigit():
            queryset = queryset.filter(groups__id=group_id, groups__owner=self.request.user).distinct()
        focus = self.request.GET.get('focus')
        if focus == 'pinned':
            queryset = queryset.filter(is_pinned=True)
        elif focus == 'attention':
            try:
                from .health import compute_health
                attention_ids = [node_id for node_id, item in compute_health(self.request.user).items()
                                 if item.get('status') in {'yellow', 'red'}]
                queryset = queryset.filter(pk__in=attention_ids)
            except Exception:
                queryset = queryset.none()
        elif focus in ('app', 'offline'):
            User = get_user_model()
            app_q = Q(imported_from__isnull=False) | Q(
                username__in=User.objects.values_list('username', flat=True)
            )
            queryset = queryset.filter(app_q) if focus == 'app' else queryset.exclude(app_q)
        return queryset.order_by('-is_pinned', 'username')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('q', '').strip()[:80]
        context['groups'] = Group.objects.filter(owner=self.request.user)
        context['selected_group'] = self.request.GET.get('group', '').strip()
        context['selected_focus'] = self.request.GET.get('focus', '').strip()

        # Mark which people in the directory actually use the app, and which of
        # those I'm connected to (so I can chat with them).
        page_nodes = list(context.get('nodes') or [])
        usernames = {n.username for n in page_nodes if n.username}
        User = get_user_model()
        accounts = {u.username: u for u in User.objects.filter(username__in=usernames)}
        from .models import Friendship
        friend_ids = set(
            Friendship.objects.filter(user=self.request.user).values_list('friend_id', flat=True)
        )
        can_use_chat = bool(getattr(self.request.user, 'is_public', False))
        app_user_count = 0
        for n in page_nodes:
            acc = accounts.get(n.username)
            n.is_app_user = bool(acc) or n.imported_from_id is not None
            n.app_account = acc
            n.chat_user_id = acc.id if acc else None
            n.is_connected = bool(acc and acc.id in friend_ids)
            n.can_chat = bool(n.is_connected and can_use_chat and n.chat_user_id != self.request.user.id)
            n.account_last_login = acc.last_login if acc else None
            if n.is_app_user:
                app_user_count += 1
        context['app_user_count'] = app_user_count
        context['directory_can_chat'] = can_use_chat
        return context


@login_required
@require_POST
def toggle_node_pin_api(request, pk):
    node = get_object_or_404(Node, pk=pk, owner=request.user)
    node.is_pinned = not node.is_pinned
    node.save(update_fields=['is_pinned'])
    return JsonResponse({'ok': True, 'is_pinned': node.is_pinned})

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
        context['contact_form'] = getattr(self, 'contact_form', None) or self.get_contact_form()
        return context

    def get_contact_form(self):
        details = NodeContactDetails.objects.filter(
            owner=self.request.user, node=self.object,
        ).first()
        return NodeContactDetailsForm(
            self.request.POST or None,
            instance=details,
        )

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
        self.contact_form = self.get_contact_form()
        if not self.contact_form.is_valid():
            return self.form_invalid(form)
        response = super().form_valid(form)
        # BUGFIX مفهومی: فیلد متنی «گروه» → گروه واقعی M2M
        if self.object.group and self.object.group.strip():
            from .models import Group as _G
            _g, _ = _G.objects.get_or_create(
                name=self.object.group.strip(), owner=self.request.user)
            self.object.groups.add(_g)
        if self.contact_form.has_changed():
            details = self.contact_form.save(commit=False)
            details.node = self.object
            details.owner = self.request.user
            details.save()
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
        candidate = get_user_model().objects.filter(username=node.username).first()
        # A local contact may share a username with a platform account; only
        # the user's own root/contact identity should redirect implicitly.
        if candidate and candidate.pk == request.user.pk:
            linked_user = candidate
    if linked_user:
        return redirect('public_profile', username=linked_user.username)

    relationships = Relationship.objects.filter(
        Q(source=node) | Q(target=node),
        owner=request.user,
        source__owner=request.user,
        target__owner=request.user,
    ).select_related('source', 'target')

    informations = Information.objects.filter(node=node)
    from django.db import ProgrammingError as _PE
    try:
        events = list(Event.objects.filter(
            owner=request.user, participants=node,
        ).prefetch_related(Prefetch(
            'participants', queryset=Node.objects.filter(owner=request.user),
        )).order_by('-date')[:10])
    except _PE:
        events = list(Event.objects.filter(
            owner=request.user, participants=node,
        ).only('id', 'title', 'date', 'description', 'owner_id').prefetch_related(Prefetch(
            'participants', queryset=Node.objects.filter(owner=request.user),
        )).order_by('-date')[:10])
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
    journal_entries = JournalEntry.objects.filter(
        owner=request.user, mentioned_nodes=node,
    ).prefetch_related('images').order_by('-created_at')[:10]

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

    relationship_focus = None
    if followups_open:
        relationship_focus = {
            'title': 'یک پیگیری باز داری',
            'note': followups_open[0].text[:120],
            'target': 'followup-section',
        }
    elif not interactions:
        relationship_focus = {
            'title': 'اولین تعامل را ثبت کن',
            'note': 'یک تماس، پیام یا دیدار کوتاه کافی است.',
            'target': 'interaction-section',
        }
    elif node_health and node_health.get('status') in ('yellow', 'red'):
        relationship_focus = {
            'title': 'یک قدم کوچک برای این رابطه',
            'note': 'مدتی از آخرین تعامل گذشته است؛ یک پیام کوتاه هم ارزش دارد.',
            'target': 'interaction-section',
        }

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
        from .relationship_intelligence import grounded_information
        _info0 = grounded_information(node)
        if _info0 and (
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
        'relationship_focus': relationship_focus,
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
        'is_new_person': not any((relationships, interactions, journal_entries, events, informations)),
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
            return redirect('node_detail', pk=node.pk)
    else:
        form = NodeForm()

    return render(request, 'nodes/node_form.html', {'form': form})


class RelationshipListView(LoginRequiredMixin, ListView):
    model = Relationship
    template_name = 'relationships/relationship_list.html'
    context_object_name = 'relationships'
    # BUGFIX: pagination باعث می‌شد فقط ۲۰ رابطه‌ی اول دیده بشه و
    # بقیه (مثل رابطه با همسر) «صفحه نداشته باشن» — همه رو نشون بده
    paginate_by = 24

    def get_queryset(self):
        queryset = Relationship.objects.filter(owner=self.request.user) \
            .filter(source__owner=self.request.user, target__owner=self.request.user) \
            .select_related('source', 'target')
        query = self.request.GET.get('q', '').strip()[:80]
        if query:
            normalized = query.replace('ي', 'ی').replace('ك', 'ک')
            variants = {
                query,
                normalized,
                normalized.replace('ی', 'ي'),
                normalized.replace('ک', 'ك'),
                normalized.replace('ی', 'ي').replace('ک', 'ك'),
            }
            search_filter = Q()
            for term in variants:
                search_filter |= (
                    Q(source__username__icontains=term)
                    | Q(source__name__icontains=term)
                    | Q(source__first_name__icontains=term)
                    | Q(source__last_name__icontains=term)
                    | Q(target__username__icontains=term)
                    | Q(target__name__icontains=term)
                    | Q(target__first_name__icontains=term)
                    | Q(target__last_name__icontains=term)
                    | Q(rel__icontains=term)
                    | Q(status__icontains=term)
                )
            queryset = queryset.filter(search_filter)
        status = self.request.GET.get('status', '').strip()
        if status in {'active', 'distant', 'inactive'}:
            queryset = queryset.filter(status=status)
        return queryset.order_by('-strength', 'source__username', 'target__username', 'id')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_query'] = self.request.GET.get('q', '').strip()[:80]
        status = self.request.GET.get('status', '').strip()
        context['current_status'] = status if status in {'active', 'distant', 'inactive'} else ''
        return context

class RelationshipDetailView(LoginRequiredMixin, DetailView):
    model = Relationship
    template_name = 'relationships/relationship_detail.html'

    def get_queryset(self):
        return Relationship.objects.filter(
            owner=self.request.user,
            source__owner=self.request.user,
            target__owner=self.request.user,
        )

class RelationshipCreateView(LoginRequiredMixin, CreateView):
    model = Relationship
    form_class = RelationshipForm
    template_name = 'relationships/relationship_form.html'
    success_url = reverse_lazy('relationship_list')

    def get_initial(self):
        initial = super().get_initial()
        target_id = self.request.GET.get('target')
        if target_id and Node.objects.filter(owner=self.request.user, pk=target_id).exists():
            initial['target'] = target_id
        if self.request.user.root_node_id:
            initial['source'] = self.request.user.root_node_id
        return initial

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        qs = Node.objects.filter(owner=self.request.user)
        form.fields['source'].queryset = qs
        form.fields['target'].queryset = qs
        if not form.instance.pk:
            form.instance.source_id = form.initial.get('source') or None
            form.instance.target_id = form.initial.get('target') or None
        return form

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        target_id = self.request.GET.get('target')
        if target_id and Node.objects.filter(owner=self.request.user, pk=target_id).exists():
            return reverse_lazy('node_detail', kwargs={'pk': target_id})
        return super().get_success_url()

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
        'node_data_json': json.dumps(info.data or {}, ensure_ascii=False, indent=2),
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['object_data_json'] = json.dumps(
            self.object.data or {}, ensure_ascii=False, indent=2
        )
        return context


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
    nodes = Node.objects.filter(
        owner=request.user, merged_into__isnull=True,
    ).only("id", "username")
    relationships = (
        Relationship.objects
        .filter(
            owner=request.user,
            source__merged_into__isnull=True,
            target__merged_into__isnull=True,
        )
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
    from django.core.paginator import Paginator
    from django.db import ProgrammingError
    from django.db.models import Prefetch
    today = timezone.localdate()
    query = request.GET.get('q', '').strip()[:80]
    scope = request.GET.get('scope', '').strip()
    if scope not in {'upcoming', 'past'}:
        scope = ''
    event_queryset = Event.objects.filter(owner=request.user)
    if query:
        normalized = query.replace('ي', 'ی').replace('ك', 'ک')
        variants = {
            query,
            normalized,
            normalized.replace('ی', 'ي'),
            normalized.replace('ک', 'ك'),
            normalized.replace('ی', 'ي').replace('ک', 'ك'),
        }
        search_filter = Q()
        for term in variants:
            search_filter |= (
                Q(title__icontains=term)
                | Q(description__icontains=term)
                | Q(participants__username__icontains=term, participants__owner=request.user)
                | Q(participants__name__icontains=term, participants__owner=request.user)
                | Q(participants__first_name__icontains=term, participants__owner=request.user)
                | Q(participants__last_name__icontains=term, participants__owner=request.user)
            )
        event_queryset = event_queryset.filter(search_filter).distinct()
    if scope == 'upcoming':
        event_queryset = event_queryset.filter(date__gte=today)
    elif scope == 'past':
        event_queryset = event_queryset.filter(date__lt=today)
    event_queryset = event_queryset.prefetch_related(
        Prefetch('participants', queryset=Node.objects.filter(owner=request.user))
    )
    try:
        page_obj = Paginator(event_queryset.order_by('date', 'id'), 20).get_page(
            request.GET.get('page', 1)
        )
        all_events = list(page_obj.object_list)
        upcoming_raw = [e for e in all_events if e.date >= today]
        past_events  = sorted([e for e in all_events if e.date < today], key=lambda e: e.date, reverse=True)
        upcoming_events = [{'event': ev, 'days_left': (ev.date - today).days} for ev in upcoming_raw]
    except ProgrammingError:
        # migration هنوز اجرا نشده — فیلد event_time وجود نداره
        # از .only() استفاده می‌کنیم تا event_time در SELECT نباشه
        # بعد مقدار None رو مستقیم در __dict__ می‌ذاریم تا template lazy load نزنه
        all_events = list(
            event_queryset
            .only('id', 'title', 'date', 'description', 'owner_id')
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
        page_obj = None
        upcoming_raw = [e for e in all_events if e.date >= today]
        past_events  = sorted([e for e in all_events if e.date < today], key=lambda e: e.date, reverse=True)
        upcoming_events = [{'event': ev, 'days_left': (ev.date - today).days} for ev in upcoming_raw]

    page_query = request.GET.copy()
    page_query.pop('page', None)
    return render(request, 'events/events_list.html', {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'events': all_events,
        'today': today,
        'current_query': query,
        'current_scope': scope,
        'page_obj': page_obj,
        'page_query': page_query.urlencode(),
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
@require_POST
def event_complete_api(request, pk):
    """V11: «✓ برگزار شد» — برای همه‌ی شرکت‌کننده‌ها تعامل حضوری ثبت می‌کنه.
    این همون چیزیه که صفحه رویدادها رو به موتور سلامت رابطه وصل می‌کنه."""
    event = get_object_or_404(Event, pk=pk, owner=request.user)
    logged = 0
    try:
        from .models import Interaction
        for p in event.participants.filter(owner=request.user):
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
    all_rels  = list(Relationship.objects.filter(
        owner=request.user,
        source__owner=request.user,
        target__owner=request.user,
    ).select_related('source', 'target'))
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
def suggested_circles_api(request):
    """Detected communities (Louvain) among not-yet-grouped people.

    Combines the graph clustering already used for colouring with the Group
    model: each cluster of >=3 ungrouped people becomes a one-click 'make a
    group' suggestion posted back to assign_group_api.
    """
    root_id = request.user.root_node_id
    grouped_ids = set(
        Node.objects.filter(owner=request.user, groups__isnull=False)
        .values_list('id', flat=True)
    )
    try:
        G, all_nodes, _ = _build_graph(request.user)
        com_map = _community_map(G)
    except Exception:
        return JsonResponse({'ok': True, 'circles': []})

    names = {n.id: n.display_name() for n in all_nodes}
    buckets = {}
    for nid, cidx in com_map.items():
        if nid == root_id or nid in grouped_ids or nid not in names:
            continue
        buckets.setdefault(cidx, []).append(nid)

    circles = []
    for cidx, ids in buckets.items():
        if len(ids) < 3:
            continue
        ids.sort(key=lambda i: names.get(i, ''))
        circles.append({
            'key': f'circle-{cidx}',
            'size': len(ids),
            'node_ids': ids,
            'members': [names[i] for i in ids],
        })
    circles.sort(key=lambda c: -c['size'])
    return JsonResponse({'ok': True, 'circles': circles[:6]})


@login_required
@require_POST
def assign_group_api(request):
    """
    POST {node_ids, group_name, action}
    action: 'add' (default) | 'remove'
    group_name: اسم گروه — اگه وجود نداشت ساخته می‌شه
    """
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    from .models import Group as GroupModel
    node_ids   = body.get('node_ids', [])
    group_name = (body.get('group_name') or '').strip()
    action     = body.get('action', 'add')

    if not node_ids:
        return JsonResponse({'error': 'node_ids لازم است'}, status=400)

    if not isinstance(node_ids, list) or len(node_ids) > 100:
        return JsonResponse({'error': 'invalid node_ids'}, status=400)
    try:
        node_ids = [int(node_id) for node_id in node_ids]
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid node_ids'}, status=400)
    nodes = list(Node.objects.filter(pk__in=node_ids, owner=request.user))
    if len(nodes) != len(set(node_ids)):
        return JsonResponse({'error': 'one or more people were not found'}, status=404)

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
    from .grounded_insights import person_summary
    from .relationship_intelligence import analyze_person_relationship

    analysis = analyze_person_relationship(request.user, node)
    return JsonResponse({
        'summary': person_summary(node, analysis),
        'analysis': analysis,
        'generated_by': 'grounded_insights_v1',
    })


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
@require_POST
def chat_clear_api(request):
    """POST → پاک کردن حافظه‌ی همدم (شروع گفتگوی نو)."""
    try:
        from .models import ChatMessage
        ChatMessage.objects.filter(owner=request.user).delete()
        return JsonResponse({'ok': True})
    except Exception:
        return JsonResponse({'ok': True})


@login_required
@require_POST
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
        from .memory_pipeline import capture_text
        capture_text(request.user, entry.text, 'chat', entry.id)
    except Exception:
        pass
    return JsonResponse({'ok': True, 'entry_id': entry.id,
                         'msg': 'ذخیره شد — از صفحه ژورنال می‌تونی تحلیل AI هم بزنی'})


def _fa_norm(text):
    return (str(text or '')
            .replace('ي', 'ی').replace('ك', 'ک').replace('‌', ' ')
            .lower())


def _named_nodes_for_query(user, query, limit=3):
    """Resolve explicitly mentioned people without fuzzy cross-tenant search."""
    qn = _fa_norm(query)
    if not qn:
        return []
    matches = []
    nodes = Node.objects.filter(owner=user).only(
        'id', 'username', 'name', 'first_name', 'last_name', 'nickname', 'owner_id'
    )
    aliases_by_node = {}
    try:
        from .models import NodeAlias
        for node_id, alias in NodeAlias.objects.filter(owner=user).values_list('node_id', 'alias'):
            aliases_by_node.setdefault(node_id, []).append(alias)
    except Exception:
        pass
    for node in nodes:
        full_name = f'{node.first_name} {node.last_name}'.strip()
        candidates = {
            _fa_norm(value).strip()
            for value in (
                node.display_name(), node.username, node.name, node.nickname,
                node.first_name, full_name, *aliases_by_node.get(node.id, []),
            )
            if value
        }
        candidates = {value for value in candidates if len(value) >= 2 and value not in _FA_STOPWORDS}
        matched = [value for value in candidates if value in qn]
        if matched:
            matches.append((max(len(value) for value in matched), node))
    matches.sort(key=lambda item: (-item[0], item[1].id))
    return [node for _, node in matches[:limit]]


def _persist_chat_exchange(user, user_message, reply, *, ephemeral=False):
    if ephemeral:
        return
    try:
        from .models import ChatMessage
        user_chat = ChatMessage.objects.create(
            role='user', content=user_message[:4000], owner=user,
        )
        ChatMessage.objects.create(role='assistant', content=(reply or '')[:4000], owner=user)
        from .memory_pipeline import capture_text
        capture_text(user, user_message, 'chat', user_chat.id)
    except Exception:
        pass


_FA_STOPWORDS = {
    'من', 'تو', 'او', 'ما', 'شما', 'با', 'به', 'از', 'که', 'را', 'رو', 'در',
    'این', 'اون', 'آن', 'یه', 'یک', 'و', 'یا', 'کی', 'چی', 'چه', 'کجا', 'کِی',
    'آخرین', 'بار', 'کِیه', 'بود', 'شد', 'کرد', 'های', 'هست', 'برای', 'چطور',
}


def _retrieve_context(user, query, limit=8):
    """Local semantic-ish retrieval over the owner's own history for chat.

    No remote embeddings are needed: Persian normalization, related terms and
    fuzzy token scoring rank evidence older than recent chat windows.
    """
    from .models import (Commitment, Event, GiftIdea, Interaction, KnowledgeTriple,
                         LifeEvent, MeetingReflection, MemoryFact, PersonaProfile,
                         RelationshipGoal, RelationshipProfile)
    from .local_memory import query_terms, score_text

    terms = query_terms(query)
    if not terms:
        return ''

    # People named in the question
    named_nodes = _named_nodes_for_query(user, query)
    named_ids = [node.id for node in named_nodes]

    def _score(text):
        return score_text(text, terms)

    lines = []

    if getattr(user, 'ai_journal_enabled', True):
        jq = JournalEntry.objects.filter(owner=user)
        if named_ids:
            jq = jq.filter(Q(mentioned_nodes__id__in=named_ids) | Q(text__icontains=query[:60]))
        scored = sorted(
            ((_score(j.text), j) for j in jq.order_by('-entry_date')[:400]),
            key=lambda p: -p[0],
        )
        for s, j in scored[:limit]:
            if s <= 0:
                break
            lines.append(f"- یادداشت {jalali_str(j.entry_date or j.created_at.date())}: {j.text[:280]}"
                         + (f" [حال: {j.mood}]" if j.mood else ""))

    try:
        iq = Interaction.objects.filter(owner=user, node__owner=user).select_related('node')
        if named_ids:
            iq = iq.filter(node_id__in=named_ids)
        for it in iq.order_by('-date')[:limit]:
            who = it.node.display_name() if it.node_id else '—'
            note = f" — {it.note}" if getattr(it, 'note', '') else ''
            lines.append(f"- تعامل {jalali_str(it.date)} با {who} ({it.get_kind_display()}){note}")
            if len(lines) >= limit * 2:
                break
    except Exception:
        pass

    try:
        mq = MemoryFact.objects.filter(owner=user, node__owner=user, active=True).select_related('node')
        if named_ids:
            mq = mq.filter(node_id__in=named_ids)
        scored_facts = sorted(
            ((_score(mf.value) + (1 if named_ids else 0), mf) for mf in mq[:120]),
            key=lambda pair: (-pair[0], -pair[1].confidence, -pair[1].id),
        )
        for score, mf in scored_facts[:limit]:
            if score > 0:
                lines.append(f"- دربارهٔ {mf.node.display_name()}: {mf.value}")
    except Exception:
        pass

    # Search structured relationship records too, not only free text.
    try:
        event_q = Q()
        for term in terms:
            event_q |= Q(title__icontains=term) | Q(description__icontains=term)
        if named_ids:
            event_q |= Q(participants__id__in=named_ids)
        for event in Event.objects.filter(owner=user).filter(event_q).distinct().prefetch_related('participants')[:limit]:
            people = ', '.join(p.display_name() for p in event.participants.all()[:4])
            lines.append(f"- رویداد {jalali_str(event.date)}: {event.title}"
                         + (f" (شرکت‌کننده: {people})" if people else '')
                         + (f" — {event.description[:140]}" if event.description else ''))
    except Exception:
        pass

    try:
        def _field_query(field):
            query = Q()
            for term in terms:
                query |= Q(**{f'{field}__icontains': term})
            return query

        node_filter = {'node_id__in': named_ids} if named_ids else {}
        for item in Commitment.objects.filter(owner=user).filter(_field_query('text'), **node_filter)[:limit]:
            lines.append(f"- تعهد با {item.node.display_name()}: {item.text} ({item.status})")
        gift_query = _field_query('title') | _field_query('notes')
        for item in GiftIdea.objects.filter(owner=user).filter(gift_query, **node_filter)[:limit]:
            lines.append(f"- ایدهٔ هدیه برای {item.node.display_name()}: {item.title}")
        for item in MeetingReflection.objects.filter(owner=user).filter(_field_query('summary'), **node_filter)[:limit]:
            lines.append(f"- بازتاب ملاقات با {item.node.display_name()}: {item.summary[:220]}")
        for item in LifeEvent.objects.filter(owner=user).filter(_field_query('title'), **node_filter)[:limit]:
            lines.append(f"- رویداد زندگی {item.node.display_name()}: {item.title or item.get_kind_display()}")
        for item in RelationshipGoal.objects.filter(owner=user, status='active').filter(_field_query('text'), **node_filter)[:limit]:
            lines.append(f"- هدف رابطه با {item.node.display_name()}: {item.text}")
    except Exception:
        pass

    try:
        triple_q = Q()
        for term in terms:
            triple_q |= Q(predicate__icontains=term) | Q(object_text__icontains=term)
        if named_ids:
            triple_q |= Q(subject_id__in=named_ids) | Q(object_node_id__in=named_ids)
        for triple in KnowledgeTriple.objects.filter(owner=user, active=True).filter(
                triple_q).select_related('subject', 'object_node')[:limit]:
            subject = triple.subject.display_name()
            obj = triple.object_node.display_name() if triple.object_node_id else triple.object_text
            lines.append(f"- دانش رابطه‌ای: {subject} — {triple.predicate} — {obj}")
    except Exception:
        pass

    try:
        for profile in PersonaProfile.objects.filter(owner=user).select_related('node')[:80]:
            if named_ids and profile.node_id not in named_ids:
                continue
            if not named_ids and _score(profile.summary) <= 0:
                continue
            statements = profile.statements if isinstance(profile.statements, list) else []
            statements = [item for item in statements if isinstance(item, dict)
                          and item.get('evidence_ids')]
            if not statements:
                continue
            details = ' | '.join(
                str(item.get('text', item))[:140] if isinstance(item, dict) else str(item)[:140]
                for item in statements[:4]
            )
            value = profile.summary[:240] if profile.summary else details
            if value:
                lines.append(f"- پروفایل شناختی {profile.node.display_name()}: {value}")
        for profile in RelationshipProfile.objects.filter(owner=user).select_related(
                'relationship__source', 'relationship__target')[:80]:
            rel = profile.relationship
            participant_ids = {rel.source_id, rel.target_id}
            if named_ids and not participant_ids.intersection(named_ids):
                continue
            if not named_ids and _score(profile.summary) <= 0:
                continue
            statements = profile.statements if isinstance(profile.statements, list) else []
            if not any(isinstance(item, dict) and item.get('evidence_ids') for item in statements):
                continue
            value = profile.summary[:260] if profile.summary else ''
            if value:
                lines.append(f"- پروفایل رابطه {rel.source.display_name()} / {rel.target.display_name()}: {value}")
    except Exception:
        pass

    if not lines:
        return ''
    header = "## مرتبط با سؤالت (از کل تاریخچه، نه فقط اخیر):\n"
    return header + "\n".join(lines[:limit * 2]) + "\n\n"


@login_required
def chat_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)
    user_message = data.get('message', '')
    if not isinstance(user_message, str):
        return JsonResponse({'error': 'message must be a string'}, status=400)
    user_message = user_message.strip()
    raw_history = data.get('history')
    raw_history = raw_history if isinstance(raw_history, list) else []
    chat_style = data.get('style', 'friendly')
    chat_style = chat_style if isinstance(chat_style, str) else 'friendly'

    if not user_message:
        return JsonResponse({'error': 'message is empty'}, status=400)

    request_started = time.monotonic()
    chat_deadline_at = request_started + _chat_response_deadline_seconds()

    from .persian_chat import (
        PERSIAN_FEW_SHOTS,
        STYLE_LABELS,
        language_policy,
        normalize_persian_reply,
        persian_quality_issues,
    )
    if chat_style not in STYLE_LABELS:
        chat_style = 'friendly'

    # Avoid model startup/prompt work for deterministic small talk.
    quick_text = user_message.casefold()
    if len(user_message) <= 80:
        if any(marker in quick_text for marker in ('چند شنبه', 'چه روزی', 'امروز چند')):
            today = timezone.localdate()
            from .utils_jalali import jalali_day_name
            _record_chat_metric(
                request.user, request_started, provider='local-rules',
                actual_model='authoritative-date', status='success',
            )
            return JsonResponse({
                'reply': f'امروز {jalali_day_name(today)} است.',
                'style': chat_style, 'grounded': True,
            })
        if any(marker in quick_text for marker in ('سلام', 'درود', 'خوبی', 'صبح بخیر', 'شب بخیر')):
            _record_chat_metric(
                request.user, request_started, provider='local-rules',
                actual_model='small-talk', status='success',
            )
            return JsonResponse({
                'reply': 'سلام! من اینجام. چطور می‌تونم کمکت کنم؟',
                'style': chat_style, 'grounded': True,
            })

    # Answer common questions about the user's own network straight from the
    # data — instant, offline, no model. Free-form talk still goes to the LLM.
    try:
        from .grounded_insights import grounded_chat_reply
        grounded = grounded_chat_reply(request.user, user_message)
    except Exception:
        grounded = None
    if grounded:
        try:
            from .models import ChatMessage
            ChatMessage.objects.create(owner=request.user, role='user', content=user_message[:2000])
            ChatMessage.objects.create(owner=request.user, role='assistant', content=grounded[:2000])
        except Exception:
            pass
        _record_chat_metric(
            request.user, request_started, provider='local-data',
            actual_model='grounded-insights', status='success',
        )
        return JsonResponse({'reply': grounded, 'style': chat_style, 'grounded': True})

    # ── V5: تاریخچه گفتگو — چت دوطرفه و پیوسته ──
    history = []
    for m in raw_history[-8:]:
        if not isinstance(m, dict):
            continue
        role = m.get('role')
        content = m.get('content')
        content = content.strip() if isinstance(content, str) else ''
        if role in ('user', 'assistant') and content:
            history.append({'role': role, 'content': content[:800]})

    # ── V8: حافظه‌ی بین‌جلسه‌ای — اگه صفحه تازه باز شده، از DB ادامه بده ──
    if not history:
        try:
            from .models import ChatMessage
            recent = list(ChatMessage.objects.filter(owner=request.user)
                          .order_by('-created_at')[:12])[::-1]
            history = [{'role': m.role, 'content': m.content[:800]} for m in recent[-8:]]
        except Exception:
            pass

    # ─── root node (کاربر اصلی که داره چت می‌کنه) ───────────────────────────
    root_node = request.user.root_node

    # ─── serialize graph (فقط داده‌های این کاربر) ────────────────────────────
    all_nodes = Node.objects.filter(owner=request.user).only(
        'id', 'username', 'name', 'first_name', 'last_name', 'nickname',
        'career', 'birth_day', 'owner_id',
    )[:300]
    all_rels = Relationship.objects.filter(owner=request.user).select_related(
        'source', 'target',
    )[:400]
    all_info = Information.objects.filter(node__owner=request.user).select_related('node')[:250]

    # اطلاعات خود کاربر اصلی
    root_info = ""
    if root_node:
        root_info = (
            f"نام: {root_node.name or root_node.username}\n"
            f"شغل: {root_node.career or '—'}\n"
            f"تولد: {jalali_str(root_node.birth_day) if root_node.birth_day else '—'}"
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
        ).select_related('source', 'target')[:250]
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
        + (f" (تولد: {jalali_str(n.birth_day)})" if n.birth_day else "")
        for n in others
    ) or "موردی ثبت نشده"

    # V9: شناخت‌نامه — تحلیل‌های ذخیره‌شده هر شخص، خوانا برای AI
    info_lines = []
    from .relationship_intelligence import is_grounded_profile
    for i in all_info:
        if root_node and i.node_id == root_node.id:
            continue
        d = i.data if isinstance(i.data, dict) else {}
        if not is_grounded_profile(d):
            continue
        nm = i.node.display_name()
        bits = []
        if d.get('friendship_score') is not None:
            bits.append(f"سلامت رابطه: {d['friendship_score']}/100")
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
    recent_journals = (
        JournalEntry.objects.filter(owner=request.user).order_by('-entry_date')[:5]
        if getattr(request.user, 'ai_journal_enabled', True) else []
    )
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

    # Keep greetings and date questions fast: they do not need the whole graph.
    light_markers = ('سلام', 'درود', 'خوبی', 'ممنون', 'مرسی', 'امروز', 'چند شنبه', 'ساعت')
    is_light_chat = len(user_message) <= 80 and any(
        marker in user_message.casefold() for marker in light_markers
    )
    if is_light_chat:
        history = history[-4:]
        who_am_i = 'کاربر اصلی شبکه'
        rels_text = nodes_text = info_text = journal_text = actions_text = ledger_text = ''
        retrieved_context = ''
        relationship_context = ''
    else:
        retrieved_context = _retrieve_context(request.user, user_message)
        named_nodes = _named_nodes_for_query(request.user, user_message)
        relationship_context = ''
        if named_nodes:
            try:
                from .relationship_intelligence import chat_relationship_context
                relationship_context = '\n\n'.join(
                    chat_relationship_context(request.user, node) for node in named_nodes[:2]
                )[:3600]
            except Exception:
                relationship_context = ''
        # Bound dynamic context so local models spend time answering, not reading.
        retrieved_context = retrieved_context[:3200]
        rels_text = rels_text[:2000]
        nodes_text = nodes_text[:1600]
        info_text = info_text[:2800]
        journal_text = journal_text[:1000]
        actions_text = actions_text[:900]
        ledger_text = ledger_text[:900]
        if not named_nodes:
            # Generic conversation does not need a directory dump. Relevant
            # journals/facts can still arrive through keyword retrieval.
            rels_text = nodes_text = info_text = ''

        # Direct analysis questions do not need a generative model.  Returning
        # the evidence engine's result keeps latency predictable and prevents
        # confident hallucinations when a free provider is unavailable.
        analysis_markers = (
            'تحلیل', 'رابطه ام', 'رابطه‌ام', 'رابطه من', 'چی میدونی', 'چی می‌دونی',
            'چقدر صمیم', 'سلامت رابطه', 'نمره رابطه', 'شناختت از',
        )
        if len(named_nodes) == 1 and any(marker in _fa_norm(user_message)
                                         for marker in analysis_markers):
            try:
                from .relationship_intelligence import analyze_person_relationship
                result = analyze_person_relationship(request.user, named_nodes[0])
                parts = [result['relationship_quality'], result['personality']]
                if result.get('red_flags'):
                    parts.append('نکات نیازمند توجه: ' + '؛ '.join(result['red_flags'][:2]) + '.')
                parts.append(result['tip'])
                reply = ' '.join(part for part in parts if part)
                _persist_chat_exchange(
                    request.user, user_message, reply, ephemeral=bool(data.get('ephemeral')),
                )
                _record_chat_metric(
                    request.user, request_started, provider='local-data',
                    actual_model='relationship-intelligence', status='success',
                )
                return JsonResponse({
                    'reply': reply,
                    'style': chat_style,
                    'analysis': {
                        'confidence': result['confidence'],
                        'confidence_label': result['confidence_label'],
                        'evidence_count': result['data_coverage']['evidence_count'],
                    },
                })
            except Exception:
                pass

    persian_policy = language_policy(chat_style)
    # Date questions must use the application clock, not the model's memory.
    # The product timezone is Asia/Tehran, so inject an authoritative date and
    # weekday into every chat request.
    today = timezone.localdate()
    from .utils_jalali import jalali_full_str, jalali_day_name
    date_context = (
        f"AUTHORITATIVE CURRENT DATE (Asia/Tehran): Gregorian {today.isoformat()}; "
        f"Persian calendar {jalali_full_str(today)}; weekday {jalali_day_name(today)}. "
        "For questions about امروز, امروز چند شنبه است, or the current date, "
        "use this exact context and do not guess."
    )
    system_prompt = (
        date_context + "\n\n" + persian_policy + "\n\n"
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
        f"## شناخت‌نامه افراد (فقط تحلیل شواهدمحور، همراه اطمینان):\n{info_text}\n\n"
        f"{retrieved_context}"
        f"## یادداشت‌های اخیرش:\n{journal_text}\n\n"
        f"## اقدامات اخیرش:\n{actions_text}\n\n"
        f"## قرض و طلب‌های باز:\n{ledger_text}\n\n"
        "قواعد: وقتی می‌گه «من» منظورش شخص اصلی بالاست. داده‌های شبکه رو فقط وقتی وسط بکش که "
        "به حرفش مربوطه — وسط درد دل آمار نریز. "
        "وقتی درباره‌ی یه شخص خاص سوال می‌کنه یا تحلیل رابطه می‌خواد، فقط از شواهد و شناخت‌نامه "
        "معتبر استفاده کن؛ اطمینان پایین و کمبود داده را صریح بگو و شخصیت یا هشدار نساز. "
        "پاسخ‌ها کوتاه (۲ تا ۵ جمله) مگه تحلیل مفصل بخواد. "
        "به فارسی محاوره‌ای و صمیمی. اگه نشانه‌ی ناراحتی عمیق یا مداوم دیدی، با مهربونی پیشنهاد کن "
        "با یه آدم مورد اعتماد یا مشاور هم حرف بزنه — بدون بزرگ‌نمایی.\n\n"
        + persian_policy
    )

    # Local models spend a noticeable amount of time reading instructions.
    # Keep the same evidence, but use a compact policy/context envelope.
    cloud_keys = ('OPENROUTER_API_KEY', 'GEMINI_API_KEY', 'MISTRAL_API_KEY', 'GROQ_API_KEY')
    local_mode = (
        os.environ.get('OLLAMA_ENABLED', '1').strip().lower() in {'1', 'true', 'yes', 'on'}
        and os.environ.get('AI_PROVIDER', '').strip().lower() in {'', 'ollama'}
        and not any(os.environ.get(key, '').strip() for key in cloud_keys)
    )
    if local_mode:
        history = [
            {'role': item['role'], 'content': item['content'][:400]}
            for item in history[-2:]
        ]
        local_policy = (
            "به فارسی محاوره‌ای و کوتاه جواب بده. بر اساس شواهد پاسخ بده و حدس را حقیقت نگو. "
            "اگر دادهٔ کافی نداری، صادقانه بگو.\n"
            + persian_policy[:700] + "\n"
            + f"نمونهٔ لحن: {PERSIAN_FEW_SHOTS[0]['content']}\n"
            + f"پاسخ نمونه: {PERSIAN_FEW_SHOTS[1]['content']}"
        )
        system_prompt = (
            date_context + "\n" + local_policy + "\n\n"
            f"کاربر و شناخت خودش:\n{who_am_i[:400]}\n\n"
            f"روابط:\n{rels_text[:650]}\n\n"
            f"افراد:\n{nodes_text[:550]}\n\n"
            f"پروفایل‌ها و حافظه:\n{info_text[:950]}\n\n"
            f"دادهٔ مرتبط با سؤال:\n{retrieved_context[:1450]}\n\n"
            f"خاطرات اخیر:\n{journal_text[:350]}\n"
            f"تعهدها و اقدام‌ها:\n{actions_text[:350]}\n"
            f"حساب‌ها:\n{ledger_text[:350]}"
        )[:3800]

    provider_name = ''
    ai_model = ''
    actual_model = ''
    attempts = 0
    fallback_used = False
    degraded_reason = ''
    try:
        if chat_deadline_at - time.monotonic() <= 0.35:
            raise ChatResponseDeadline('context preparation used the chat budget')
        client, ai_model = _get_ai_client_and_model()
        provider_name = _chat_provider_name(client)
        generation_started = time.monotonic()
        from .views_smart_features import _AIClientFailover, _OllamaClient
        is_local_ollama = isinstance(client, _OllamaClient)
        forced_provider = os.environ.get('AI_PROVIDER', '').strip().lower()
        is_openrouter = bool(os.environ.get('OPENROUTER_API_KEY', '').strip()) and (
            forced_provider in {'', 'openrouter'}
        )
        local_timeout = False
        completion_options = {
            'model': ai_model,
            'messages': (
                [{"role": "system", "content": system_prompt}]
                + ([] if local_mode else PERSIAN_FEW_SHOTS)
                + history
                + ([{
                    'role': 'system',
                    'content': '## شواهد مرتبط و قابل اتکا\n' + relationship_context,
                }] if relationship_context else [])
                + [{"role": "user", "content": user_message}]
            ),
            'max_tokens': 48 if local_mode else 160,
            'temperature': 0.6,
        }

        def _call_completion(options):
            nonlocal attempts
            remaining = chat_deadline_at - time.monotonic()
            if remaining <= 0.35:
                raise ChatResponseDeadline('chat provider deadline reached')
            bounded = dict(options)
            bounded['timeout'] = max(0.25, remaining)
            attempts += 1
            return client.chat.completions.create(**bounded)

        if is_openrouter:
            # The free router can select a reasoning model. Reserve the small
            # output budget for the actual answer so content is not empty.
            completion_options['reasoning_effort'] = 'none'
        try:
            response = _call_completion(completion_options)
        except Exception as exc:
            if not local_mode:
                raise
            from .views_smart_features import OllamaRequestTimeout
            if isinstance(exc, OllamaRequestTimeout):
                # Do not spend another timeout window on a fallback request.
                local_timeout = True
                response = None
            else:
                # A large personal graph can exceed a small local runner's context.
                # Retry with the authoritative date and the user question only.
                fallback_prompt = (
                    date_context + "\n" + local_policy + "\n"
                    "کوتاه، دقیق جواب بده."
                )
                response = _call_completion({
                    'model': ai_model,
                    'messages': [
                        {"role": "system", "content": fallback_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    'max_tokens': 48,
                    'temperature': 0.6,
                })
        if local_timeout:
            reply = _fast_degraded_chat_reply(user_message)
        else:
            from .views_smart_features import _strip_reasoning
            actual_model = str(getattr(response, 'model', '') or ai_model)
            if isinstance(client, _AIClientFailover):
                fallback_used = client.chat.completions.last_backend == 'ollama'
            raw_content = getattr(response.choices[0].message, 'content', None) or ''
            reply = normalize_persian_reply(_strip_reasoning(raw_content))
            if _is_model_nonanswer(raw_content) or _is_model_nonanswer(reply):
                # The free router landed on a guard/classifier model. Ask once
                # more (it usually routes elsewhere); if it still isn't an
                # answer, fall through to the deterministic degraded reply.
                retry = _call_completion(completion_options)
                response = retry
                actual_model = str(getattr(retry, 'model', '') or ai_model)
                raw_content = getattr(retry.choices[0].message, 'content', None) or ''
                reply = normalize_persian_reply(_strip_reasoning(raw_content))
                if _is_model_nonanswer(raw_content) or _is_model_nonanswer(reply):
                    raise RuntimeError('model returned safety-classifier scaffold')

        # یک فرصت بازنویسی سبک و محدود برای خروجی انگلیسی، رباتیک یا بیش‌ازحد بلند.
        # این مرحله داده‌های خصوصی گراف را دوباره ارسال نمی‌کند.
        quality_issues = persian_quality_issues(reply)
        finish_reason = str(getattr(response.choices[0], 'finish_reason', '') or '') if response else ''
        if finish_reason in {'length', 'deadline'}:
            quality_issues.append('incomplete_answer')
        if (quality_issues and not is_local_ollama and not local_timeout
                and time.monotonic() - generation_started < 2.5
                and chat_deadline_at - time.monotonic() > 0.75):
            try:
                rewrite = _call_completion({
                    'model': ai_model,
                    'messages': [
                        {"role": "system", "content": persian_policy},
                        {
                            "role": "user",
                            "content": (
                                "پاسخ زیر را با حفظ معنی، به فارسی طبیعی ایران بازنویسی کن. "
                                "مقدمه اضافه نکن و فقط متن نهایی را بده:\n\n" + reply[:3000]
                            ),
                        },
                    ],
                    'max_tokens': 120,
                    'temperature': 0.35,
                })
                rewritten = normalize_persian_reply(rewrite.choices[0].message.content)
                rewritten_issues = persian_quality_issues(rewritten)
                rewrite_finish = str(getattr(rewrite.choices[0], 'finish_reason', '') or '')
                if rewrite_finish in {'length', 'deadline'}:
                    rewritten_issues.append('incomplete_answer')
                if rewritten and not rewritten_issues:
                    reply = rewritten
                    quality_issues = []
            except Exception:
                pass

        # OpenRouter's free router can return an English reasoning trace or a
        # classifier instead of Persian chat. Give the local model the
        # remaining shared budget; never display a known-bad answer.
        if (quality_issues and isinstance(client, _AIClientFailover)
                and client.chat.completions.last_backend == 'openrouter'
                and chat_deadline_at - time.monotonic() > 0.75):
            try:
                cache.set('ai:provider-cooldown:openrouter', True, timeout=60)
                local_retry = _call_completion({
                    'model': ai_model,
                    'messages': [
                        {'role': 'system', 'content': (
                            'فقط به فارسی طبیعی ایران و حداکثر سه جمله جواب بده. '
                            'مقدمه و فرایند فکر ننویس.'
                        )},
                        {'role': 'user', 'content': user_message[:1200]},
                    ],
                    'max_tokens': 48,
                    'temperature': 0.45,
                })
                local_content = _strip_reasoning(
                    getattr(local_retry.choices[0].message, 'content', None) or ''
                )
                local_reply = normalize_persian_reply(local_content)
                local_issues = persian_quality_issues(local_reply)
                local_finish = str(getattr(local_retry.choices[0], 'finish_reason', '') or '')
                if local_finish in {'length', 'deadline'}:
                    local_issues.append('incomplete_local_answer')
                if local_reply and not local_issues and not _is_model_nonanswer(local_reply):
                    reply = local_reply
                    quality_issues = []
                    actual_model = str(getattr(local_retry, 'model', '') or actual_model)
                    fallback_used = True
            except Exception:
                pass

        if quality_issues:
            reply = _fast_degraded_chat_reply(user_message)
            degraded_reason = 'generation_quality'

        if not reply:
            reply = _fast_degraded_chat_reply(user_message)
            degraded_reason = 'generation_empty'

        # ── V8: ذخیره‌ی دوطرفه — درد دل‌ها دیگه گم نمی‌شن ──
        # BUGFIX: صفحه insights هم از همین API استفاده می‌کنه؛ با فلگ ephemeral
        # سوال‌های اون صفحه دیگه حافظه‌ی همدم رو آلوده نمی‌کنن.
        _persist_chat_exchange(
            request.user, user_message, reply, ephemeral=bool(data.get('ephemeral')),
        )

        _record_chat_metric(
            request.user, request_started, provider=provider_name,
            requested_model=ai_model, actual_model=actual_model,
            status=('degraded_timeout' if local_timeout else
                    ('degraded_quality' if degraded_reason else 'success')),
            attempts=attempts, fallback_used=fallback_used,
        )

        return JsonResponse({
            'reply': reply, 'style': chat_style,
            **({'degraded': True,
                'reason': degraded_reason or 'generation_deadline'}
               if local_timeout or degraded_reason else {}),
        })
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'All chat providers failed for user_id=%s: %s',
            request.user.id, type(exc).__name__,
        )
        reply = _fast_degraded_chat_reply(user_message)
        _persist_chat_exchange(
            request.user, user_message, reply,
            ephemeral=bool(data.get('ephemeral')),
        )
        error_name = type(exc).__name__.lower()
        timed_out = isinstance(exc, TimeoutError) or 'timeout' in error_name or 'deadline' in error_name
        _record_chat_metric(
            request.user, request_started, provider=provider_name or 'unavailable',
            requested_model=ai_model, actual_model=actual_model,
            status='timeout' if timed_out else 'error', attempts=attempts,
            fallback_used=fallback_used,
        )
        return JsonResponse({
            'reply': reply, 'style': chat_style, 'degraded': True,
            'reason': 'generation_deadline' if timed_out else 'generation_provider_unavailable',
        })


@login_required
def graph_all_api(request):
    """Return all nodes+edges with community and centrality data for D3 graph."""
    graph_cache_key = f'graph_all_data:{request.user.id}:{request.user.date_joined.isoformat()}'
    if request.GET.get('refresh') != '1':
        cached = cache.get(graph_cache_key)
        if cached:
            return JsonResponse(cached)
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
        all_nodes = list(Node.objects.filter(owner=request.user, merged_into__isnull=True))
        all_rels  = list(Relationship.objects.filter(
            owner=request.user,
            source__owner=request.user,
            target__owner=request.user,
            source__merged_into__isnull=True,
            target__merged_into__isnull=True,
        ).select_related('source', 'target'))
        deg_c   = {}
        com_map = {}

    # Root node — از user.root_node
    root_id = None
    if request.user.is_authenticated and request.user.root_node_id:
        root_id = str(request.user.root_node_id)

    # گروه‌های دستی (M2M) — prefetch برای performance
    all_nodes_qs = Node.objects.filter(
        owner=request.user, merged_into__isnull=True,
    ).prefetch_related('groups')
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

    # سلامت رابطه از شناخت‌نامه شواهدمحور
    fscore_map = {}
    try:
        from .relationship_intelligence import is_grounded_profile
        for nid_, d_ in Information.objects.filter(
                node__owner=request.user).values_list('node_id', 'data'):
            if is_grounded_profile(d_) and d_.get('friendship_score') is not None:
                fscore_map[nid_] = d_['friendship_score']
    except Exception:
        pass

    # ── V4: سلامت رابطه — برای رنگ حلقهٔ نودها و یال‌های متصل به root ──
    health_map = {}
    health_counts = {}
    try:
        from .health import compute_health, health_summary
        health_map = compute_health(request.user)
        health_counts = health_summary(health_map)
    except Exception:
        pass

    # ── «از کِی در شبکه‌ای؟» — قدیمی‌ترین نشانه برای هر نفر (time-lapse) ──
    since_map = {}

    def _mark(nid, d):
        if nid is None or d is None:
            return
        cur = since_map.get(nid)
        if cur is None or d < cur:
            since_map[nid] = d

    try:
        from .models import Interaction, Debt
        for nid, d in Interaction.objects.filter(owner=request.user).values_list('node_id', 'date'):
            _mark(nid, d)
        for nid, d in Event.objects.filter(owner=request.user).values_list('participants__id', 'date'):
            _mark(nid, d)
        for nid, d in JournalEntry.objects.filter(owner=request.user).values_list('mentioned_nodes__id', 'entry_date'):
            _mark(nid, d)
        for nid, d in Debt.objects.filter(owner=request.user).values_list('node_id', 'date'):
            _mark(nid, d)
    except Exception:
        pass
    for n in all_nodes:
        if getattr(n, 'created_at', None):
            _mark(n.id, n.created_at.date())

    node_data = []
    for n in all_nodes:
        c_idx   = com_map.get(n.id, 0)
        gnames  = node_groups_map.get(n.id, [])
        # رنگ: اول گروه اول، وگرنه رنگ community
        color   = group_color_map.get(gnames[0], COMMUNITY_PALETTE[c_idx % len(COMMUNITY_PALETTE)]) if gnames else COMMUNITY_PALETTE[c_idx % len(COMMUNITY_PALETTE)]
        h = health_map.get(n.id) or {}
        since = since_map.get(n.id)
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
            "health_status": h.get("status"),           # green|yellow|red|unknown|None
            "health_score":  h.get("score"),
            "days_since":     h.get("days_since"),
            "since":         since.isoformat() if since else None,
        })

    root_id_int = request.user.root_node_id if request.user.is_authenticated else None

    edge_data = []
    for r in all_rels:
        edge_since = r.met_at
        if not edge_since and getattr(r, 'created_at', None):
            edge_since = r.created_at.date()
        if not edge_since:
            # fall back to when both people entered the network
            a, b = since_map.get(r.source_id), since_map.get(r.target_id)
            edge_since = max(a, b) if a and b else (a or b)
        e = {
            "source":   str(r.source_id),
            "target":   str(r.target_id),
            "label":    r.rel or "",
            "strength": r.strength,
            "since":    edge_since.isoformat() if edge_since else None,
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

    payload = {
        "nodes":         node_data,
        "edges":         edge_data,
        "root_id":       root_id,
        "all_groups":    all_groups_sorted,
        "group_colors":  group_color_map,
        "health_counts": health_counts,
    }
    cache.set(graph_cache_key, payload, timeout=15)
    return JsonResponse(payload)


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
    # صفحه تنظیمات با پروفایل ادغام شده؛ مسیر قدیمی را بدون redirect زنجیره‌ای
    # به صفحهٔ canonical بفرست تا لینک‌ها و بوکمارک‌های قدیمی هم روان بمانند.
    return redirect('profile_edit')


# ════════════════════════════════════════════════════════════════
# Journal
# ════════════════════════════════════════════════════════════════

@login_required
def journal_view(request):
    user = request.user
    entries = list(JournalEntry.objects.filter(owner=user).prefetch_related('images', 'mentioned_nodes')[:20])
    for entry in entries:
        entry.tags_json = json.dumps(entry.tags or [], ensure_ascii=False)
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
        'nodes_json': nodes_for_mention,
        'all_tags_json': all_tags,
        'all_nodes_json': all_node_usernames,
        'all_moods_json': all_moods,
    })


@login_required
@require_POST
def journal_image_upload_api(request):
    """Upload an image for a journal entry (before or after entry creation)."""
    image_file = request.FILES.get('image')
    if not image_file:
        return JsonResponse({'error': 'فایلی ارسال نشد'}, status=400)
    try:
        image_file = normalize_image_upload(
            image_file, max_bytes=10 * 1024 * 1024, max_dimension=4096,
            label='تصویر خاطره',
        )
    except UploadValidationError as exc:
        return JsonResponse({'error': str(exc), 'code': 'invalid_image'}, status=400)
    img = JournalImage.objects.create(image=image_file, owner=request.user)
    return JsonResponse({'id': img.id, 'url': img.image.url})


@login_required
@require_POST
def journal_image_ocr_api(request):
    """POST {image_id} → recognised Persian/English text from a journal image.

    Uses the project's OpenAI-compatible vision model (works with the free
    OpenRouter / Gemini / Groq vision models and local Ollama llava).
    """
    import base64

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)
    image_id = body.get('image_id')
    if not isinstance(image_id, int) or isinstance(image_id, bool):
        return JsonResponse({'error': 'image_id لازم است'}, status=400)

    img = get_object_or_404(JournalImage, pk=image_id, owner=request.user)
    try:
        with img.image.open('rb') as fh:
            raw = fh.read(6 * 1024 * 1024)
    except Exception:
        return JsonResponse({'error': 'تصویر قابل خواندن نبود'}, status=400)

    name = (img.image.name or '').lower()
    mime = 'image/png' if name.endswith('.png') else (
        'image/webp' if name.endswith('.webp') else 'image/jpeg')
    data_uri = f'data:{mime};base64,' + base64.b64encode(raw).decode('ascii')

    try:
        client, model = _get_ai_client_and_model()
    except RuntimeError:
        return JsonResponse({
            'error': 'خواندن متن تصویر فعلاً در دسترس نیست؛ متن را دستی وارد کن یا بعداً دوباره امتحان کن.',
            'code': 'vision_provider_unavailable',
        }, status=503)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': (
                        'متنِ داخل این تصویر را دقیقاً و کامل بنویس (فارسی یا انگلیسی). '
                        'ترتیب و خط‌شکنی‌ها را تا حد ممکن حفظ کن. اگر متنی وجود ندارد، '
                        'فقط بنویس: (متنی یافت نشد). هیچ توضیح اضافه‌ای نده.'
                    )},
                    {'type': 'image_url', 'image_url': {'url': data_uri}},
                ],
            }],
            max_tokens=700,
        )
        text = (resp.choices[0].message.content or '').strip()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'Journal OCR provider failed for user_id=%s: %s',
            request.user.id, type(exc).__name__,
        )
        return JsonResponse({
            'error': 'خواندن متن تصویر کامل نشد؛ متن را دستی وارد کن یا بعداً دوباره امتحان کن.',
            'code': 'vision_provider_failed',
        }, status=503)

    if not text or text.startswith('(متنی'):
        return JsonResponse({'ok': True, 'text': '', 'empty': True})
    return JsonResponse({'ok': True, 'text': text[:4000]})


@login_required
@require_POST
def journal_analyze_api(request):
    """Diary text → rich structured extraction with root-node awareness."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)
    text = body.get('text', '')
    if not isinstance(text, str):
        return JsonResponse({'error': 'text must be a string'}, status=400)
    text = text.strip()

    if not text:
        return JsonResponse({'error': 'متن خالی است'}, status=400)

    # If existing entry_id passed, analyze an already-saved entry
    existing_entry_id = body.get('entry_id')
    if not isinstance(existing_entry_id, int) or isinstance(existing_entry_id, bool):
        existing_entry_id = None

    # The journal analysis path is local and approval-based.  The model must
    # never block saving a memory or turn inferred traits into profile facts.
    raw_tags = body.get('tags', [])
    if isinstance(raw_tags, str):
        raw_tags = [tag.strip() for tag in raw_tags.split(',') if tag.strip()]
    elif isinstance(raw_tags, list):
        raw_tags = [str(tag).strip()[:80] for tag in raw_tags if str(tag).strip()][:30]
    else:
        raw_tags = []

    entry_date = None
    entry_date_str = body.get('entry_date', '')
    entry_date_str = entry_date_str.strip() if isinstance(entry_date_str, str) else ''
    if entry_date_str:
        try:
            entry_date = parse_date_input(entry_date_str)
        except ValueError:
            pass

    occurred_at = timezone.now()
    raw_occurred_at = body.get('occurred_at', '')
    if isinstance(raw_occurred_at, str) and raw_occurred_at:
        try:
            from datetime import datetime
            occurred_at = datetime.fromisoformat(raw_occurred_at.replace('Z', '+00:00'))
            if timezone.is_naive(occurred_at):
                occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())
        except ValueError:
            return JsonResponse({'error': 'زمان خاطره معتبر نیست'}, status=400)
    if entry_date is None:
        entry_date = timezone.localdate(occurred_at)

    entry_kind = body.get('entry_kind', 'moment')
    if entry_kind not in dict(JournalEntry.ENTRY_KIND_CHOICES):
        return JsonResponse({'error': 'نوع خاطره معتبر نیست'}, status=400)

    supplied_mood = body.get('mood') if isinstance(body.get('mood'), str) else None

    if existing_entry_id:
        entry = JournalEntry.objects.filter(
            id=existing_entry_id, owner=request.user,
        ).first()
        if not entry:
            return JsonResponse({'error': 'خاطره پیدا نشد'}, status=404)
        entry.text = text
        entry.entry_date = entry_date
        entry.occurred_at = occurred_at
        entry.entry_kind = entry_kind
        entry.tags = raw_tags
        entry.mood = supplied_mood[:100] if supplied_mood is not None else entry.mood
        entry.ai_analyzed = True
        entry.save(update_fields=[
            'text', 'entry_date', 'occurred_at', 'entry_kind', 'tags',
            'mood', 'ai_analyzed',
        ])
    else:
        entry = JournalEntry.objects.create(
            text=text, entry_date=entry_date, occurred_at=occurred_at,
            entry_kind=entry_kind, tags=raw_tags, mood=(supplied_mood or '')[:100], ai_analyzed=True,
            owner=request.user,
        )

    image_ids = body.get('image_ids')
    image_ids = [
        image_id for image_id in image_ids
        if isinstance(image_id, int) and not isinstance(image_id, bool)
    ] if isinstance(image_ids, list) else []
    if image_ids:
        JournalImage.objects.filter(
            id__in=image_ids, entry__isnull=True, owner=request.user,
        ).update(entry=entry)

    try:
        from .views_journal_extra import _extract_profile_media_from_journal
        _extract_profile_media_from_journal(entry)
    except Exception:
        pass

    from .memory_pipeline import capture_text
    capture_text(request.user, entry.text, 'journal', entry.id)
    from .models import ExtractionSuggestion
    suggestions = list(ExtractionSuggestion.objects.filter(
        owner=request.user, source='journal', source_id=entry.id, status='pending',
    ))
    from .grounded_insights import journal_result
    root_username = request.user.root_node.username if request.user.root_node else 'me'
    result = journal_result(suggestions, text, root_username)
    result['_root_username'] = root_username
    result['_entry_id'] = entry.id
    result['_suggestions_created'] = len(suggestions)
    return JsonResponse({'result': result})


@login_required
@require_POST
def journal_apply_api(request):
    """Apply extracted entities + rich attributes to DB."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    from datetime import date as today_date

    created = {'nodes': [], 'relationships': [], 'events': [], 'attributes': []}

    # ── Privacy maps از frontend ───────────────────────────
    def as_mapping(value):
        return value if isinstance(value, dict) else {}

    def as_list(value):
        return value if isinstance(value, list) else []

    node_privacy = as_mapping(data.get('node_privacy'))   # {username: true/false}
    rel_privacy  = as_mapping(data.get('rel_privacy'))    # {"from||to||type": true/false}
    # ── Public node links: {username: {id, display_name, owner_username, ...}} ──
    node_links   = as_mapping(data.get('node_links'))     # نودهایی که از شبکه عمومی انتخاب شدن
    node_rows = as_list(data.get('nodes'))
    relationship_rows = as_list(data.get('relationships'))
    event_rows = as_list(data.get('events'))
    supplied_attribute_rows = as_list(data.get('attributes'))

    # Accept only candidates emitted for this exact owner-scoped journal.
    # This prevents old model output or handcrafted JSON from being promoted
    # to graph facts through the legacy apply endpoint.
    entry_id = data.get('_entry_id')
    if not isinstance(entry_id, int) or isinstance(entry_id, bool):
        return JsonResponse({'error': 'منبع معتبر برای این تحلیل پیدا نشد'}, status=400)
    source_entry = JournalEntry.objects.filter(id=entry_id, owner=request.user).first()
    if not source_entry:
        return JsonResponse({'error': 'خاطره پیدا نشد'}, status=404)
    from .models import ExtractionSuggestion
    allowed = {
        suggestion.id: suggestion.kind
        for suggestion in ExtractionSuggestion.objects.filter(
            owner=request.user, source='journal', source_id=source_entry.id,
            status='pending',
        )
    }

    def suggestion_id(row):
        if not isinstance(row, dict):
            return None
        value = row.get('_suggestion_id')
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    node_rows = [
        row for row in node_rows
        if allowed.get(suggestion_id(row)) in {'person', 'relationship'}
    ]
    relationship_rows = [
        row for row in relationship_rows
        if allowed.get(suggestion_id(row)) == 'relationship'
    ]
    event_rows = [
        row for row in event_rows
        if allowed.get(suggestion_id(row)) == 'event'
    ]
    applied_suggestion_ids = {
        suggestion_id(row)
        for row in [*node_rows, *relationship_rows, *event_rows]
        if suggestion_id(row) is not None
    }
    attribute_rows = []
    if supplied_attribute_rows:
        created['ignored_unverified_attributes'] = len(supplied_attribute_rows)

    def resolve_public_link(link):
        if not isinstance(link, dict):
            return None
        try:
            node_id = int(link.get('id'))
        except (TypeError, ValueError):
            return None
        return Node.objects.filter(
            pk=node_id, is_public=True, owner__is_public=True
        ).select_related('owner').first()

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

    for nd in node_rows:
        if not isinstance(nd, dict):
            continue
        username = (nd.get('username') or '').strip()
        if not username or username.lower() in _me_aliases:
            continue
        is_pub   = bool(node_privacy.get(username, False))
        link     = node_links.get(username)          # اگه از شبکه عمومی لینک شده
        defaults = {'name': nd.get('name', ''), 'is_public': is_pub}

        if link:
            # داده‌های عمومی منبع رو کپی کن + قفل username
            src_node = resolve_public_link(link)
            if src_node:
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
                src = resolve_public_link(link)
                if src:
                    node.imported_from   = src.owner
                    node.username_locked = True
                    changed = True
            if changed:
                node.save()

    # ── Relationships — یک یال per pair (merge در هر جهت) ──
    for rd in relationship_rows:
        if not isinstance(rd, dict):
            continue
        frm      = (rd.get('from') or '').strip()
        to       = (rd.get('to')   or '').strip()
        rel_type = (rd.get('type') or '').strip()
        try:
            strength = min(5, max(1, int(rd.get('strength') or 3)))
        except (TypeError, ValueError):
            strength = 3
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
    for ed in event_rows:
        if not isinstance(ed, dict):
            continue
        title = (ed.get('title') or '').strip()
        if not title:
            continue
        date_str = ed.get('date')
        try:
            event_date = parse_date_input(date_str) if date_str else today_date.today()
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

    for attr in attribute_rows:
        if not isinstance(attr, dict):
            continue
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
    if entry_id:
        try:
            entry = JournalEntry.objects.get(id=entry_id, owner=req_user)
            # Collect all node usernames that appeared in relationships
            mentioned = set()
            for rd in relationship_rows:
                if not isinstance(rd, dict):
                    continue
                for side in ('from', 'to'):
                    u = (rd.get(side) or '').strip()
                    if u and u not in ('me', 'من', root_username):
                        mentioned.add(u)
            for nd in node_rows:
                if not isinstance(nd, dict):
                    continue
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

    if applied_suggestion_ids:
        ExtractionSuggestion.objects.filter(
            owner=request.user,
            id__in=applied_suggestion_ids,
            status='pending',
        ).update(status='approved')

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
    rels = list(Relationship.objects.filter(
        owner=user, source__owner=user, target__owner=user,
    ).values(
        'id', 'rel', 'source__username', 'target__username',
        'strength', 'status', 'met_at', 'is_public',
    ))
    entries = list(JournalEntry.objects.filter(owner=user).values(
        'id', 'text', 'entry_date', 'tags', 'mood', 'ai_analyzed', 'created_at',
    ))

    from .models import (Commitment, Debt, FollowUp, GiftIdea, Interaction, LifeEvent,
                         MemoryFact, MeetingReflection, NodeContactDetails,
                         RelationshipGoal, RelationshipPulse)
    events = []
    for event in Event.objects.filter(owner=user).prefetch_related('participants'):
        events.append({
            'title': event.title, 'date': event.date, 'event_time': event.event_time,
            'description': event.description,
            'participants': [node.username for node in event.participants.filter(owner=user)],
        })
    informations = list(Information.objects.filter(node__owner=user).values(
        'node__username', 'visibility', 'data',
    ))
    try:
        from .models import ChatMessage
        chat_messages = list(ChatMessage.objects.filter(owner=user).values(
            'role', 'content', 'created_at',
        ))
    except Exception:
        chat_messages = []
    contacts = list(NodeContactDetails.objects.filter(owner=user).values(
        'node__username', 'email', 'alternate_phone', 'bank_name', 'card_number',
        'account_number', 'iban', 'telegram_username', 'whatsapp_number',
        'instagram_username', 'x_username', 'linkedin_url', 'address', 'notes',
    ))
    memories = list(MemoryFact.objects.filter(owner=user).values(
        'node__username', 'category', 'value', 'confidence', 'source', 'source_id',
        'active', 'ai_usable', 'confidentiality',
    ))
    interactions = list(Interaction.objects.filter(owner=user).values(
        'node__username', 'kind', 'date', 'feeling', 'support_kind', 'note',
    ))
    followups = list(FollowUp.objects.filter(owner=user).values(
        'node__username', 'text', 'due_date', 'done', 'done_at', 'created_at',
    ))
    commitments = list(Commitment.objects.filter(owner=user).values(
        'node__username', 'responsible', 'text', 'due_date', 'status', 'completed_at',
    ))
    debts = list(Debt.objects.filter(owner=user).values(
        'node__username', 'direction', 'amount', 'paid', 'currency', 'date', 'due_date',
        'note', 'settled', 'settled_at',
    ))
    life_events = list(LifeEvent.objects.filter(owner=user).values(
        'node__username', 'kind', 'title', 'date', 'archived', 'created_at',
    ))
    goals = list(RelationshipGoal.objects.filter(owner=user).values(
        'node__username', 'text', 'status', 'baseline_score', 'created_at', 'closed_at',
    ))
    gifts = list(GiftIdea.objects.filter(owner=user).values(
        'node__username', 'title', 'occasion', 'budget', 'notes', 'created_at',
    ))
    reflections = list(MeetingReflection.objects.filter(owner=user).values(
        'node__username', 'summary', 'feeling', 'relationship_change', 'created_at',
    ))
    pulses = list(RelationshipPulse.objects.filter(owner=user).values(
        'node__username', 'support', 'autonomy', 'belonging', 'trust', 'voice',
        'note', 'created_at',
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
        'contact_details': contacts,
        'events': events,
        'informations': informations,
        'chat_messages': chat_messages,
        'memories': memories,
        'interactions': interactions,
        'followups': followups,
        'commitments': commitments,
        'debts': debts,
        'life_events': life_events,
        'relationship_goals': goals,
        'gift_ideas': gifts,
        'meeting_reflections': reflections,
        'relationship_pulses': pulses,
    }
    response = HttpResponse(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
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
@require_POST
def node_quick_update(request, pk):
    """آپدیت سریع فیلدهای پایه یک نود — برای فرم inline در journal."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

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
                node.birth_day = parse_date_input(val)
            except Exception:
                pass
        else:
            node.birth_day = None

    node.save()
    return JsonResponse({'ok': True, 'display_name': node.display_name()})


@login_required
@require_POST
def node_create_from_image(request):
    """ایجاد نود از عکس drag-drop شده روی گراف — wizard step per image."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

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
    if image:
        try:
            image = normalize_image_upload(
                image, max_bytes=8 * 1024 * 1024, max_dimension=2400,
                label='تصویر شخص',
            )
        except UploadValidationError as exc:
            return JsonResponse({'error': str(exc), 'code': 'invalid_image'}, status=400)

    # ── Generate unique username از نام ──────────────────
    raw = f"{first_name} {last_name}".strip()
    base = finglish_slug(raw) or 'person'
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
            new_node.birth_day = parse_date_input(birth_day)
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
@require_POST
def relationship_quick_create(request):
    """ایجاد یا merge یال از طریق drag روی گراف."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

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
