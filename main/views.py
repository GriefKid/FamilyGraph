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
from .models import Relationship
from django.core.cache import cache
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from .models import Node, Information, Event
from django.views.generic import ListView
from .models import Node
from django.views.generic import TemplateView


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

    context = {
        'node': node,
        'relationships': relationships,
        'informations': informations,
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


COMMUNITY_PALETTE = [
    "#6366f1","#ec4899","#f59e0b","#10b981","#3b82f6",
    "#ef4444","#8b5cf6","#06b6d4","#f97316","#14b8a6",
]

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

    # top nodes sorted by degree
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

    # ── Centrality (Task 8) ──
    centrality_rows = []
    try:
        import networkx as nx
        G = nx.Graph()
        for n in all_nodes: G.add_node(n.id)
        for r in all_rels:  G.add_edge(r.source_id, r.target_id)

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
    except ImportError:
        centrality_rows = []

    # ── Network Health Score (Task 9) ──
    isolated_ratio  = len(isolated) / node_count if node_count else 1
    avg_strength    = 0
    if all_rels:
        avg_strength = sum(r.strength for r in all_rels) / len(all_rels)
    active_ratio    = sum(1 for r in all_rels if r.status == 'active') / len(all_rels) if all_rels else 0

    density_score   = min(density * 200, 40)          # max 40
    isolation_score = max(0, 20 - isolated_ratio * 20) # max 20
    strength_score  = (avg_strength / 5) * 20          # max 20
    active_score    = active_ratio * 20                 # max 20

    health_score = round(density_score + isolation_score + strength_score + active_score)
    health_color = "#10b981" if health_score >= 70 else "#f59e0b" if health_score >= 40 else "#ef4444"
    health_label = "سالم" if health_score >= 70 else "متوسط" if health_score >= 40 else "نیاز به توجه"

    return render(request, 'insights/insights.html', {
        'node_count':             node_count,
        'edge_count':             edge_count,
        'density':                density,
        'avg_degree':             avg_degree,
        'most_connected':         most_connected,
        'most_connected_degree':  most_connected_degree,
        'isolated':               isolated,
        'top_nodes':              top_nodes,
        'rel_types':              rel_types,
        'centrality_rows':        centrality_rows,
        'health_score':           health_score,
        'health_color':           health_color,
        'health_label':           health_label,
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

    nodes = Node.objects.only("id","username","picture")
    relationships = Relationship.objects.select_related(
        "source","target"
    )

    node_data=[]
    edge_data=[]

    for n in nodes:

        node_data.append({
            "id":str(n.id),
            "username":n.username,
            "image":n.picture.url if n.picture else None
        })

    for r in relationships:

        edge_data.append({
            "source":str(r.source_id),
            "target":str(r.target_id),
            "label":r.rel or ""
        })

    return JsonResponse({
        "nodes":node_data,
        "edges":edge_data
    })