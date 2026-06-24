import json
import logging
import os
from django.db.models import Q, ProtectedError
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse

from .forms import NodeForm, RelationshipForm, EventForm
from .models import Relationship, AppSettings, JournalEntry, JournalImage
from django.core.cache import cache
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from .models import Node, Information, Event
from django.views.generic import ListView
from django.views.generic import TemplateView

COMMUNITY_PALETTE = [
    "#6366f1","#ec4899","#f59e0b","#10b981","#3b82f6",
    "#ef4444","#8b5cf6","#06b6d4","#f97316","#14b8a6",
]

def _build_graph():
    """Build a networkx Graph from DB. Returns (G, nodes_list, rels_list)."""
    import networkx as nx
    all_nodes = list(Node.objects.all())
    all_rels  = list(Relationship.objects.select_related('source', 'target'))
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


class GraphView(TemplateView):
    template_name = "nodes/graph.html"


class NodeListView(ListView):
    model = Node
    template_name = 'nodes/node_list.html'
    context_object_name = 'nodes'
    paginate_by = 24

    def get_queryset(self):
        return Node.objects.all().only(
            "id",
            "username",
            "picture",
            "name",
            "career"
        )


def home(request):
    nodes = Node.objects.all()
    relationships = Relationship.objects.all()
    context = {
        'nodes': nodes,
        'relationships': relationships,
        'node_count': nodes.count(),
        'relationship_count': relationships.count(),
    }
    return render(request, 'home.html', context)

class UpdateNodeView(UpdateView):
    model = Node
    form_class = NodeForm
    template_name = 'nodes/node_form.html'
    success_url = reverse_lazy('node_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'ویرایش {self.object.username}'
        return context

    def form_valid(self, form):
        # قبل save، چک کن unique نباشه
        if form.has_changed():
            existing = Node.objects.filter(username=form.cleaned_data['username']).exclude(pk=self.object.pk).first()
            if existing:
                form.add_error('username', 'این نام قبلاً استفاده شده')
                return self.form_invalid(form)
        return super().form_valid(form)



def node_delete(request, pk):
    node = get_object_or_404(Node, pk=pk)

    if request.method == 'POST':
        try:
            node.delete()
            messages.success(request, "Node حذف شد")
        except ProtectedError:
            messages.error(request, "این Node در Relationship استفاده شده")
        return redirect('node_list')

    return render(request, 'nodes/node_confirm_delete.html', {'node': node})


@require_http_methods(["GET"])
def node_detail(request, pk):
    node = get_object_or_404(Node, pk=pk)

    relationships = Relationship.objects.filter(
        Q(source=node) | Q(target=node)
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
        G, all_nodes, all_rels = _build_graph()
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


def create_node(request):
    if request.method == 'POST':
        form = NodeForm(request.POST)
        if form.is_valid():
            node = form.save()
            messages.success(request, f'نود "{node.username}" ایجاد شد')
            return redirect('node_list')
    else:
        form = NodeForm()

    return render(request, 'nodes/node_form.html', {'form': form})


class RelationshipListView(ListView):
    model = Relationship
    template_name = 'relationships/relationship_list.html'
    context_object_name = 'relationships'
    paginate_by = 20

class RelationshipDetailView(DetailView):
    model = Relationship
    template_name = 'relationships/relationship_detail.html'

class RelationshipCreateView(CreateView):
    model = Relationship
    form_class = RelationshipForm
    template_name = 'relationships/relationship_form.html'
    success_url = reverse_lazy('relationship_list')

class RelationshipUpdateView(UpdateView):
    model = Relationship
    form_class = RelationshipForm
    template_name = 'relationships/relationship_form.html'
    success_url = reverse_lazy('relationship_list')

class RelationshipDeleteView(DeleteView):
    model = Relationship
    template_name = 'relationships/relationship_confirm_delete.html'
    success_url = reverse_lazy('relationship_list')


class InformationCreateView(CreateView):
    model = Information
    fields = ['node', 'visibility', 'data']
    template_name = 'informations/information_form.html'
    success_url = reverse_lazy('information_list')

    def form_valid(self, form):
        if not form.instance.visibility:
            form.instance.visibility = 'private'
        messages.success(
            self.request,
            f'اطلاعات برای "{form.instance.node}" اضافه شد'
        )
        return super().form_valid(form)


def information_detail(request, info_id):
    info = get_object_or_404(Information, id=info_id)
    
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

def graph_level_data(request, level=0):

    try:
        level = int(level)
        if level > 20:
            return JsonResponse({'error': 'حداکثر 20 level', 'nodes': [], 'relationships': []})

        cache_key = f'graph_level_{level}'
        cached = cache.get(cache_key)
        if cached:
            return JsonResponse(cached)

        root_node = Node.objects.filter(username="root").first()

        if not root_node:
            return JsonResponse({
                'nodes': [],
                'relationships': [],
                'error': 'root node not found'
            })

        if not root_node:
            return JsonResponse({
                'nodes': [],
                'relationships': [],
                'level': 0,
                'error': 'no nodes found'
            })

        nodes = [root_node]
        relationships = []
        nodes_per_level = min(50, 10 ** level)
        
        if level == 0:
            pass
            
        else:
            current_parents = [root_node]
            for depth in range(1, level + 1):
                next_parents = []
                current_rels = Relationship.objects.filter(
                    source__in=current_parents
                ).select_related('target')[:nodes_per_level]

                for rel in current_rels:
                    if rel.target not in nodes:
                        nodes.append(rel.target)
                        next_parents.append(rel.target)
                    relationships.append({
                        'id': f"e{rel.id}",
                        'source': rel.source.id,
                        'target': rel.target.id,
                        'label': rel.rel or f"L{depth}"
                    })
                
                current_parents = next_parents[:nodes_per_level]
                if not current_parents:
                    break
        
        node_data = []
        seen_ids = set()
        for node in nodes:
            if node.id not in seen_ids:
                seen_ids.add(node.id)
                node_data.append({
                    'id': str(node.id),
                    'label': node.username or f"Node-{node.id}",
                    'username': node.username or "",
                    'level': min(level, len(node_data))
                })
        
        result = {
            'nodes': node_data,
            'relationships': relationships,
            'level': level,
            'total_levels': 100,
            'nodes_loaded': len(node_data),
            'memory_usage': f"{len(node_data) * 100:.0f}KB"
        }
        
        cache.set(cache_key, result, 300)
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"Graph level {level} error: {str(e)}")
        return JsonResponse({
            'error': str(e),
            'nodes': [],
            'relationships': [],
            'level': level
        }, status=500)

class InformationListView(ListView):
    model = Information
    template_name = 'informations/informations_list.html'
    context_object_name = 'informations'
    paginate_by = 20

    def get_queryset(self):
        return Information.objects.select_related('node').all()


class InformationDetailView(DetailView):
    model = Information
    template_name = 'informations/information_detail.html'

class InformationUpdateView(UpdateView):
    model = Information
    fields = ['node', 'visibility', 'data']
    template_name = 'informations/information_form.html'
    success_url = reverse_lazy('information_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'ویرایش اطلاعات #{self.object.id}'
        return context


class InformationDeleteView(DeleteView):
    model = Information
    template_name = 'informations/information_confirm_delete.html'
    success_url = reverse_lazy('information_list')


def home_graph_api(request):
    nodes = Node.objects.only("id", "username")
    relationships = (
        Relationship.objects
        .select_related('father', 'child')
        .all()
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


def events_list(request):
    events = Event.objects.prefetch_related('participants').all()
    return render(request, 'events/events_list.html', {'events': events})

def event_create(request):
    if request.method == 'POST':
        form = EventForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('events_list')
    else:
        form = EventForm()
    return render(request, 'events/event_form.html', {'form': form})

def event_delete(request, pk):
    event = get_object_or_404(Event, pk=pk)
    if request.method == 'POST':
        event.delete()
        return redirect('events_list')
    return render(request, 'events/event_confirm_delete.html', {'event': event})


def communities_view(request):
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities
    except ImportError:
        return render(request, 'communities/communities.html', {'error': 'networkx نصب نیست. دستور: py -m pip install networkx'})

    all_nodes = list(Node.objects.all())
    all_rels  = list(Relationship.objects.select_related('source', 'target'))
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


def insights_view(request):
    all_nodes = list(Node.objects.all())
    all_rels  = list(Relationship.objects.select_related('source', 'target'))

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
        G, _, _ = _build_graph()

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


def node_ai_summary(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    node = get_object_or_404(Node, pk=pk)
    rels = Relationship.objects.filter(
        Q(source=node) | Q(target=node)
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

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'OPENROUTER_API_KEY تنظیم نشده'}, status=500)

    try:
        from openai import OpenAI
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        response = client.chat.completions.create(
            model="google/gemma-4-31b-it:free",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
        )
        return JsonResponse({'summary': response.choices[0].message.content})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def chat_view(request):
    return render(request, 'chat/chat.html')


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

    # ─── serialize graph ───
    all_nodes = Node.objects.all()
    all_rels  = Relationship.objects.select_related('source', 'target')
    all_info  = Information.objects.select_related('node')

    nodes_text = "\n".join(
        f"- {n.username}"
        + (f" (نام: {n.name})" if n.name else "")
        + (f" (شغل: {n.career})" if n.career else "")
        + (f" (تولد: {n.birth_day})" if n.birth_day else "")
        for n in all_nodes
    )

    rels_text = "\n".join(
        f"- {r.source.username} → {r.target.username}"
        + (f" [{r.rel}]" if r.rel else "")
        for r in all_rels
    )

    info_text = "\n".join(
        f"- {i.node.username}: {i.data}"
        for i in all_info
    ) or "موردی ثبت نشده"

    system_prompt = (
        "تو یک دستیار هوشمند هستی که به تحلیل شبکه روابط شخصی کمک می‌کنی.\n\n"
        f"افراد در شبکه:\n{nodes_text}\n\n"
        f"روابط:\n{rels_text}\n\n"
        f"اطلاعات ثبت‌شده:\n{info_text}\n\n"
        "بر اساس این داده‌ها به فارسی و مختصر پاسخ بده."
    )

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'OPENROUTER_API_KEY تنظیم نشده'}, status=500)

    try:
        from openai import OpenAI
    except ImportError:
        return JsonResponse({'error': 'پکیج openai نصب نیست. دستور: py -m pip install openai'}, status=500)

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        response = client.chat.completions.create(
            model="google/gemma-4-31b-it:free",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            max_tokens=1024,
        )
        return JsonResponse({'reply': response.choices[0].message.content})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def graph_all_api(request):
    """Return all nodes+edges with community and centrality data for D3 graph."""
    try:
        import networkx as nx
        G, all_nodes, all_rels = _build_graph()

        if G.number_of_nodes() > 0:
            deg_c   = nx.degree_centrality(G)
            com_map = _community_map(G)
        else:
            deg_c   = {}
            com_map = {}
    except Exception:
        all_nodes = list(Node.objects.all())
        all_rels  = list(Relationship.objects.select_related('source', 'target'))
        deg_c   = {}
        com_map = {}

    # Root node — graceful fallback if AppSettings table doesn't exist yet
    root_id = None
    try:
        settings = AppSettings.get()
        root_id = str(settings.root_node_id) if settings.root_node_id else None
    except Exception:
        pass

    node_data = []
    for n in all_nodes:
        c_idx = com_map.get(n.id, 0)
        node_data.append({
            "id":         str(n.id),
            "username":   n.username,
            "label":      n.display_name() if hasattr(n, 'display_name') else n.username,
            "image":      n.picture.url if n.picture else None,
            "centrality": round(deg_c.get(n.id, 0), 4),
            "community":  c_idx,
            "color":      COMMUNITY_PALETTE[c_idx % len(COMMUNITY_PALETTE)],
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
        "nodes":   node_data,
        "edges":   edge_data,
        "root_id": root_id,
    })


# ════════════════════════════════════════════════════════════════
# Settings (root node)
# ════════════════════════════════════════════════════════════════

def settings_view(request):
    app_settings = AppSettings.get()
    nodes = Node.objects.order_by('username')
    if request.method == 'POST':
        root_id = request.POST.get('root_node')
        if root_id:
            app_settings.root_node = get_object_or_404(Node, pk=root_id)
        else:
            app_settings.root_node = None
        app_settings.save()
        messages.success(request, 'تنظیمات ذخیره شد.')
        return redirect('settings')
    return render(request, 'settings/settings.html', {
        'settings': app_settings,
        'nodes':    nodes,
    })


# ════════════════════════════════════════════════════════════════
# Journal
# ════════════════════════════════════════════════════════════════

def journal_view(request):
    entries = JournalEntry.objects.prefetch_related('images', 'mentioned_nodes').all()[:20]
    nodes_for_mention = list(Node.objects.values('username', 'name', 'first_name', 'last_name', 'nickname'))

    # Collect all unique tags from all entries
    all_tags = []
    for e in JournalEntry.objects.values_list('tags', flat=True):
        if e:
            all_tags.extend(e)
    all_tags = sorted(set(all_tags))

    # All node usernames for people filter
    all_node_usernames = list(Node.objects.values_list('username', flat=True))

    # All distinct moods
    all_moods = list(
        JournalEntry.objects.exclude(mood='').values_list('mood', flat=True).distinct()[:20]
    )

    return render(request, 'journal/journal.html', {
        'entries': entries,
        'nodes_json': json.dumps(nodes_for_mention, ensure_ascii=False),
        'all_tags_json': json.dumps(all_tags, ensure_ascii=False),
        'all_nodes_json': json.dumps(all_node_usernames, ensure_ascii=False),
        'all_moods_json': json.dumps(all_moods, ensure_ascii=False),
    })


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

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'OPENROUTER_API_KEY تنظیم نشده'}, status=500)

    # ── Who is "me"? ─────────────────────────────────────────
    root_username = None
    root_display  = None
    try:
        app_settings = AppSettings.get()
        if app_settings.root_node:
            root_username = app_settings.root_node.username
            root_display  = app_settings.root_node.display_name()
    except Exception:
        pass

    me_info = (
        f'نویسنده این خاطره "{root_display}" با username "{root_username}" است. '
        f'هر جا "من" یا اول شخص مفرد آمد یعنی همین شخص. '
        f'برای نویسنده نود جدید نساز — روابط را به username "{root_username}" وصل کن.'
        if root_username else
        'نویسنده مشخص نیست — اگر "من" آمد، username آن را "me" بگذار.'
    )

    existing_nodes = ', '.join(Node.objects.values_list('username', flat=True)[:80]) or 'هیچ'

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
        from openai import OpenAI
        import re
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        response = client.chat.completions.create(
            model="google/gemma-4-31b-it:free",
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

        if existing_entry_id:
            try:
                entry = JournalEntry.objects.get(id=existing_entry_id)
                entry.ai_analyzed = True
                entry.mood = result.get('my_mood', '')
                if raw_tags:
                    entry.tags = raw_tags
                entry.save(update_fields=['ai_analyzed', 'mood', 'tags'])
            except JournalEntry.DoesNotExist:
                entry = JournalEntry.objects.create(
                    text=text, entry_date=entry_date, tags=raw_tags,
                    mood=result.get('my_mood', ''), ai_analyzed=True
                )
        else:
            entry = JournalEntry.objects.create(
                text=text, entry_date=entry_date, tags=raw_tags,
                mood=result.get('my_mood', ''), ai_analyzed=True
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
        return JsonResponse({'error': str(e)}, status=500)


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

    # ── Resolve root node ──────────────────────────────────
    root_node     = None
    root_username = (data.get('_root_username') or '').strip()
    try:
        app_settings = AppSettings.get()
        root_node = app_settings.root_node
        if root_node and not root_username:
            root_username = root_node.username
    except Exception:
        pass

    def resolve_node(uname):
        uname = (uname or '').strip()
        if uname in ('me', 'من', root_username) and root_node:
            return root_node
        try:
            return Node.objects.get(username=uname)
        except Node.DoesNotExist:
            return None

    # ── Create nodes (never create root) ──────────────────
    for nd in data.get('nodes', []):
        username = (nd.get('username') or '').strip()
        if not username or username == root_username:
            continue
        node, is_new = Node.objects.get_or_create(
            username=username,
            defaults={'name': nd.get('name', '')}
        )
        if is_new:
            created['nodes'].append(username)

    # ── Relationships ──────────────────────────────────────
    for rd in data.get('relationships', []):
        frm      = (rd.get('from') or '').strip()
        to       = (rd.get('to')   or '').strip()
        rel_type = rd.get('type', '')
        strength = int(rd.get('strength') or 3)
        src = resolve_node(frm)
        tgt = resolve_node(to)
        if not src or not tgt or src == tgt:
            continue
        _, is_new = Relationship.objects.get_or_create(
            source=src, target=tgt, rel=rel_type,
            defaults={'strength': min(5, max(1, strength))}
        )
        if is_new:
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
            description=ed.get('description', '')
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
            entry = JournalEntry.objects.get(id=entry_id)
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