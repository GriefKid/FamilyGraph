from django.contrib import admin
from django.contrib.admin import display
from .models import Node, Relationship, Information

@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ['username']
    search_fields = ['username']

@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ['rel', 'get_source', 'get_target']
    raw_id_fields = ['source', 'target']

    @display(description='مبدا')
    def get_source(self, obj):
        return obj.source.username or 'N/A'

    @display(description='مقصد')
    def get_target(self, obj):
        return obj.target.username or 'N/A'

@admin.register(Information)
class InformationAdmin(admin.ModelAdmin):
    list_display = ['node', 'visibility', 'data_preview']
    list_filter = ['visibility']
    fields = ['node', 'visibility', 'data']

    @display(description='نمونه داده')
    def data_preview(self, obj):
        return str(obj.data)[:50] + '...' if obj.data else 'خالی'
