from django.urls import path
from . import views
from .views import (
    InformationListView,
    InformationDetailView,
    InformationCreateView,
    InformationUpdateView,
    InformationDeleteView,
)

urlpatterns = [

    # ======================
    # HOME
    # ======================
    path('', views.GraphView.as_view(), name='home'),

    # ======================
    # NODE CRUD
    # ======================
    path('nodes/', views.NodeListView.as_view(), name='node_list'),
    path('nodes/create/', views.create_node, name='node_create'),
    path('nodes/<int:pk>/', views.node_detail, name='node_detail'),
    path('nodes/<int:pk>/edit/', views.UpdateNodeView.as_view(), name='node_update'),
    path('nodes/<int:pk>/delete/', views.node_delete, name='node_delete'),

    # ======================
    # RELATIONSHIP CRUD
    # ======================
    path('relationships/', views.RelationshipListView.as_view(), name='relationship_list'),
    path('relationships/create/', views.RelationshipCreateView.as_view(), name='relationship_create'),
    path('relationships/<int:pk>/', views.RelationshipDetailView.as_view(), name='relationship_detail'),
    path('relationships/<int:pk>/update/', views.RelationshipUpdateView.as_view(), name='relationship_update'),
    path('relationships/<int:pk>/delete/', views.RelationshipDeleteView.as_view(), name='relationship_delete'),

    # ======================
    # INFORMATION CRUD
    # ======================
    path('informations/', InformationListView.as_view(), name='information_list'),
    path('informations/create/', InformationCreateView.as_view(), name='information_create'),
    path('informations/<int:pk>/', InformationDetailView.as_view(), name='information_detail'),
    path('informations/<int:pk>/update/', InformationUpdateView.as_view(), name='information_update'),
    path('informations/<int:pk>/delete/', InformationDeleteView.as_view(), name='information_delete'),

    # ======================
    # GRAPH
    # ======================
    path('graph/', views.GraphView.as_view(), name='graph'),


    # ======================
    # CHAT AI
    # ======================
    path('insights/', views.insights_view, name='insights'),
    path('api/node/<int:pk>/summary/', views.node_ai_summary, name='node_ai_summary'),
    path('chat/', views.chat_view, name='chat'),
    path('api/chat/', views.chat_api, name='chat_api'),

    # ======================
    # LEGACY / API
    # ======================
    path('api/home/graph/', views.home_graph_api, name='home_graph_api'),
    path('api/graph/level/<int:level>/', views.graph_level_data, name='graph_level_data'),
    path("api/graph/all/", views.graph_all_api),
    path('legacy/info/<int:info_id>/', views.information_detail, name='legacy_information_detail'),

]
