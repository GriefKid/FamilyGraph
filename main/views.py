import logging
from django.db.models import Q, ProtectedError
from django.views.decorators.http import require_http_methods
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse

from .forms import NodeForm, RelationshipForm
from .models import Relationship
from django.core.cache import cache
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from .models import Node, Information
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


def graph_all_api(request):

    nodes = Node.objects.only("id","username","picture")
    relationships = Relationship.objects.select_related(
        "father","child"
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