from django.contrib import admin
from django.contrib.admin import display
from .models import Node, Relationship, Information

@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ['username']
    search_fields = ['username']

@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ['rel', 'get_father', 'get_child']
    raw_id_fields = ['father', 'child']
    
    @display(description='پدر')
    def get_father(self, obj):
        return obj.father.username or 'N/A'
    
    @display(description='فرزند')    
    def get_child(self, obj):
        return obj.child.username or 'N/A'

@admin.register(Information)
class InformationAdmin(admin.ModelAdmin):
    list_display = ['node', 'visibility', 'data_preview']
    list_filter = ['visibility']
    fields = ['node', 'visibility', 'data']

    @display(description='نمونه داده')
    def data_preview(self, obj):
        return str(obj.data)[:50] + '...' if obj.data else 'خالی'
