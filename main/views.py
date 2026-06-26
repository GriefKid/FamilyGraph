import json
import logging
import os
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
    """Returns (client, model_name). Priority: Gemini → Mistral → Groq → OpenRouter."""
    from openai import OpenAI
    gemini_key = os.environ.get('GEMINI_API_KEY', '')
    if gemini_key:
        return (
            OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key=gemini_key),
            "gemini-1.5-flash"
        )
    mistral_key = os.environ.get('MISTRAL_API_KEY', '')
    if mistral_key:
        return OpenAI(base_url="https://api.mistral.ai/v1", api_key=mistral_key), "mistral-small-latest"
    groq_key = os.environ.get('GROQ_API_KEY', '')
    if groq_key:
        return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key), "llama-3.3-70b-versatile"
    openrouter_key = os.environ.get('OPENROUTER_API_KEY', '')
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key), "google/gemma-4-31b-it:free"

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
        return super().form_valid(form)



@login_required
def node_delete(request, pk):
    node = get_object_or_404(Node, pk=pk, owner=request.user)

    if request.method == 'POST':
        try:
            node.delete()
            messages.success(request, "Node حذف شد")
        except ProtectedError:
            messages.error(request, "این Node در Relationship استفاده شده")
        return redirect('node_list')

    return render(request, 'nodes/node_confirm_delete.html', {'node': node})


@login_required
@require_http_methods(["GET"])
def node_detail(request, pk):
    node = get_object_or_404(Node, pk=pk, owner=request.user)

    relationships = Relationship.objects.filter(
        Q(source=node) | Q(target=node), owner=request.user
    ).select_related('source', 'target')

    informations = Information.objects.filter(node=node)
    events = node.events.order_by('-date')[:10]

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
    }
    return render(request, 'nodes/node_detail.html', context)


@login_required
def create_node(request):
    if request.method == 'POST':
        form = NodeForm(request.POST, request.FILES)
        if form.is_valid():
            node = form.save(commit=False)
            node.owner = request.user
            node.save()
            form.save_m2m()
            messages.success(request, f'نود "{node.username}" ایجاد شد')
            return redirect('node_list')
    else:
        form = NodeForm()

    return render(request, 'nodes/node_form.html', {'form': form})


class RelationshipListView(LoginRequiredMixin, ListView):
    model = Relationship
    template_name = 'relationships/relationship_list.html'
    context_object_name = 'relationships'
    paginate_by = 20

    def get_queryset(self):
        return Relationship.objects.filter(owner=self.request.user).select_related('source', 'target')

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
    events = Event.objects.filter(owner=request.user).prefetch_related('participants')
    return render(request, 'events/events_list.html', {'events': events})

@login_required
def event_create(request):
    if request.method == 'POST':
        form = EventForm(request.POST)
        form.fields['participants'].queryset = Node.objects.filter(owner=request.user)
        if form.is_valid():
            ev = form.save(commit=False)
            ev.owner = request.user
            ev.save()
            form.save_m2m()
            return redirect('events_list')
    else:
        form = EventForm()
        form.fields['participants'].queryset = Node.objects.filter(owner=request.user)
    return render(request, 'events/event_form.html', {'form': form})

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


@login_required
def insights_view(request):
    all_nodes = list(Node.objects.filter(owner=request.user))
    all_rels  = list(Relationship.objects.filter(owner=request.user).select_related('source', 'target'))

    node_count = len(all_nodes)
    edge_count = len(all_rels)
    node_map   = {n.id: n for n in all_nodes}

    # degree per node
    degree = {n.id: 0 for n in all_nodes}
    for r in all_rels:
        degree[r.source_id] = degree.get(r.source_id, 0) + 1
        degree[r.target_id] = degree.get(r.target_id, 0) + 1

    top_nodes = sorted(degree.items(), key=lambda x: -x[1])[:8]
    top_nodes = [(node_map[nid], deg) for nid, deg in top_nodes if nid in node_map]

    most_connected        = top_nodes[0][0] if top_nodes else None
    most_connected_degree = top_nodes[0][1] if top_nodes else 0
    isolated              = [n for n in all_nodes if degree.get(n.id, 0) == 0]

    rel_types = {}
    for r in all_rels:
        label = r.rel or "نامشخص"
        rel_types[label] = rel_types.get(label, 0) + 1
    rel_types = sorted(rel_types.items(), key=lambda x: -x[1])

    max_edges  = node_count * (node_count - 1)
    density    = round(edge_count / max_edges, 3) if max_edges > 0 else 0
    avg_degree = round(2 * edge_count / node_count, 2) if node_count > 0 else 0

    # ── Centrality ──────────────────────────────────────────────────────
    centrality_rows = []
    clustering_coef = 0.0
    structural_holes_count = 0  # nodes bridging communities
    strong_tie_pct  = 0.0
    weak_tie_pct    = 0.0

    try:
        import networkx as nx
        G, _, _ = _build_graph(request.user)

        if G.number_of_nodes() > 1:
            deg_c  = nx.degree_centrality(G)
            bet_c  = nx.betweenness_centrality(G)
            clo_c  = nx.closeness_centrality(G)

            for n in all_nodes:
                centrality_rows.append({
                    'node':        n,
                    'degree_c':    round(deg_c.get(n.id, 0), 3),
                    'between_c':   round(bet_c.get(n.id, 0), 3),
                    'closeness_c': round(clo_c.get(n.id, 0), 3),
                })
            centrality_rows.sort(key=lambda x: -x['degree_c'])

            # Clustering coefficient (0-1): how tightly knit neighbourhoods are
            clustering_coef = round(nx.average_clustering(G), 3)

            # Structural holes: nodes with high betweenness relative to degree
            structural_holes_count = sum(
                1 for n in all_nodes
                if bet_c.get(n.id, 0) > 0.15
            )

    except ImportError:
        pass

    # Strong / weak tie balance
    if all_rels:
        strong = sum(1 for r in all_rels if r.strength >= 4)
        weak   = sum(1 for r in all_rels if r.strength <= 2)
        strong_tie_pct = round(strong / len(all_rels) * 100, 1)
        weak_tie_pct   = round(weak   / len(all_rels) * 100, 1)

    # ── Enhanced Health Score (100 points) ──────────────────────────────
    isolated_ratio = len(isolated) / node_count if node_count else 1
    avg_strength   = (sum(r.strength for r in all_rels) / len(all_rels)) if all_rels else 0
    active_ratio   = (sum(1 for r in all_rels if r.status == 'active') / len(all_rels)) if all_rels else 0

    # 1. Density (max 20): well-connected but not clique
    density_score     = min(density * 300, 20)
    # 2. Isolation (max 20): fewer isolated nodes is better
    isolation_score   = max(0, 20 - isolated_ratio * 20)
    # 3. Clustering (max 20): tight social circles
    clustering_score  = round(clustering_coef * 20, 1)
    # 4. Strength (max 20): quality of relationships
    strength_score    = round((avg_strength / 5) * 20, 1)
    # 5. Stability (max 20): active vs inactive
    stability_score   = round(active_ratio * 20, 1)

    health_score = round(density_score + isolation_score + clustering_score + strength_score + stability_score)
    health_color = "#10b981" if health_score >= 70 else "#f59e0b" if health_score >= 40 else "#ef4444"
    health_label = "سالم" if health_score >= 70 else "متوسط" if health_score >= 40 else "نیاز به توجه"

    health_components = [
        {'name': 'تراکم شبکه',    'score': round(density_score, 1),   'max': 20, 'desc': 'چقدر شبکه‌ات به‌هم وصله'},
        {'name': 'کاهش انزوا',    'score': round(isolation_score, 1), 'max': 20, 'desc': 'چقدر کم نودهای بدون ارتباط داری'},
        {'name': 'خوشه‌بندی',     'score': clustering_score,          'max': 20, 'desc': 'چقدر دوستانت با هم آشنا هستند'},
        {'name': 'کیفیت روابط',   'score': strength_score,            'max': 20, 'desc': 'میانگین قدرت روابطت'},
        {'name': 'پایداری شبکه',  'score': stability_score,           'max': 20, 'desc': '٪ روابط فعال'},
    ]

    return render(request, 'insights/insights.html', {
        'node_count':              node_count,
        'edge_count':              edge_count,
        'density':                 density,
        'avg_degree':              avg_degree,
        'most_connected':          most_connected,
        'most_connected_degree':   most_connected_degree,
        'isolated':                isolated,
        'top_nodes':               top_nodes,
        'rel_types':               rel_types,
        'centrality_rows':         centrality_rows,
        'health_score':            health_score,
        'health_color':            health_color,
        'health_label':            health_label,
        'health_components':       health_components,
        'clustering_coef':         clustering_coef,
        'structural_holes_count':  structural_holes_count,
        'strong_tie_pct':          strong_tie_pct,
        'weak_tie_pct':            weak_tie_pct,
    })


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
    return render(request, 'chat/chat.html')


@login_required
def chat_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    if not user_message:
        return JsonResponse({'error': 'message is empty'}, status=400)

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

    info_text = "\n".join(
        f"- {i.node.display_name()}: {i.data}"
        for i in all_info
        if not root_node or i.node_id != root_node.id
    ) or "موردی ثبت نشده"

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

    system_prompt = (
        "تو یک دستیار هوشمند شخصی هستی که به صاحب این شبکه روابط کمک می‌کنی.\n\n"
        f"## من کیستم (صاحب شبکه که داره باهات صحبت می‌کنه):\n{who_am_i}\n\n"
        f"## روابط من با دیگران:\n{rels_text}\n\n"
        f"## افراد دیگر در شبکه:\n{nodes_text}\n\n"
        f"## اطلاعات بیشتر درباره افراد:\n{info_text}\n\n"
        f"## یادداشت‌های اخیر من:\n{journal_text}\n\n"
        f"## اقدامات اخیر من روی هشدارها و نکات روزانه:\n{actions_text}\n\n"
        "وقتی کاربر می‌گه «من»، «خودم»، «شبکه‌ام» منظورش همون شخص اصلی بالاست. "
        "از اقدامات ثبت‌شده برای درک الگوهای رابطه‌ای استفاده کن. "
        "به فارسی، مختصر و کاربردی پاسخ بده."
    )

    try:
        client, ai_model = _get_ai_client_and_model()
        response = client.chat.completions.create(
            model=ai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            max_tokens=1024,
        )
        return JsonResponse({'reply': response.choices[0].message.content})
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
        })

    edge_data = [
        {
            "source":   str(r.source_id),
            "target":   str(r.target_id),
            "label":    r.rel or "",
            "strength": r.strength,
        }
        for r in all_rels
    ]

    return JsonResponse({
        "nodes":        node_data,
        "edges":        edge_data,
        "root_id":      root_id,
        "all_groups":   all_groups_sorted,
        "group_colors": group_color_map,
    })


# ════════════════════════════════════════════════════════════════
# Settings (root node)
# ════════════════════════════════════════════════════════════════

@login_required
def settings_view(request):
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

        entry_owner = request.user if request.user.is_authenticated else None
        if existing_entry_id:
            try:
                entry = JournalEntry.objects.get(id=existing_entry_id, owner=entry_owner)
                entry.ai_analyzed = True
                entry.mood = result.get('my_mood', '')
                if raw_tags:
                    entry.tags = raw_tags
                entry.save(update_fields=['ai_analyzed', 'mood', 'tags'])
            except JournalEntry.DoesNotExist:
                entry = JournalEntry.objects.create(
                    text=text, entry_date=entry_date, tags=raw_tags,
                    mood=result.get('my_mood', ''), ai_analyzed=True, owner=entry_owner,
                )
        else:
            entry = JournalEntry.objects.create(
                text=text, entry_date=entry_date, tags=raw_tags,
                mood=result.get('my_mood', ''), ai_analyzed=True, owner=entry_owner,
            )
        result['_entry_id'] = entry.id

        # Link any pre-uploaded images to this entry
        image_ids = body.get('image_ids', [])
        if image_ids:
            JournalImage.objects.filter(id__in=image_ids, entry__isnull=True).update(entry=entry)

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

    def resolve_node(uname):
        uname = (uname or '').strip()
        if uname in ('me', 'من', root_username) and root_node:
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
        if not username or username == root_username:
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
        ev = Event.objects.create(
            title=title, date=event_date,
            description=ed.get('description', ''),
            owner=req_user,
        )
        if root_node:
            ev.participants.add(root_node)
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
            for uname in mentioned:
                n = resolve_node(uname)
                if n:
                    entry.mentioned_nodes.add(n)
            if root_node:
                entry.mentioned_nodes.add(root_node)
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