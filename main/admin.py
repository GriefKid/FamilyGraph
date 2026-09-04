from django.contrib import admin
from django.contrib.admin import display
from .models import (
    ArtisticWork,
    AIExtractionTrace,
    AIQualityEvaluation,
    AIRequestMetric,
    ChatAnalysis,
    Commitment,
    Debt,
    DirectMessage,
    Follow,
    FollowUp,
    GiftIdea,
    FeatureFlag,
    FriendRequest,
    Friendship,
    Information,
    Interaction,
    KnowledgeTriple,
    MemoryFact,
    MeetingReflection,
    Node,
    NodeAlias,
    NodeMergeOperation,
    NodeSafetySetting,
    ObservabilityEvent,
    NodeCloseness,
    ProfileMediaItem,
    Relationship,
    RelationshipRecommendation,
)

admin.site.register(Commitment)
admin.site.register(GiftIdea)
admin.site.register(MeetingReflection)
admin.site.register(NodeSafetySetting)
admin.site.register(FeatureFlag)
admin.site.register(AIExtractionTrace)
admin.site.register(AIQualityEvaluation)
admin.site.register(AIRequestMetric)
admin.site.register(KnowledgeTriple)
admin.site.register(ObservabilityEvent)


@admin.register(MemoryFact)
class MemoryFactAdmin(admin.ModelAdmin):
    list_display = ['node', 'category', 'value', 'confidence', 'active', 'ai_usable']
    list_filter = ['category', 'active', 'ai_usable', 'source']
    search_fields = ['node__username', 'value']
    raw_id_fields = ['owner', 'node', 'suggestion', 'superseded_by']


@admin.register(NodeAlias)
class NodeAliasAdmin(admin.ModelAdmin):
    list_display = ['alias', 'node', 'owner']
    search_fields = ['alias', 'node__username']
    raw_id_fields = ['owner', 'node']


@admin.register(NodeMergeOperation)
class NodeMergeOperationAdmin(admin.ModelAdmin):
    list_display = ['primary_node', 'duplicate_node', 'status', 'created_at']
    list_filter = ['status']
    raw_id_fields = ['owner', 'primary_node', 'duplicate_node']


@admin.register(RelationshipRecommendation)
class RelationshipRecommendationAdmin(admin.ModelAdmin):
    list_display = ['node', 'title', 'status', 'outcome', 'helpful', 'created_at']
    list_filter = ['status', 'outcome', 'helpful']
    raw_id_fields = ['owner', 'node']


@admin.register(ArtisticWork)
class ArtisticWorkAdmin(admin.ModelAdmin):
    list_display = ['title', 'kind', 'creator', 'year']
    list_filter = ['kind']
    search_fields = ['title', 'creator']


@admin.register(Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = ['node', 'direction', 'amount', 'paid', 'due_date', 'settled']
    list_filter = ['direction', 'settled']
    raw_id_fields = ['node']


@admin.register(FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ['text', 'node', 'due_date', 'done']
    list_filter = ['done']
    raw_id_fields = ['node']


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ['node', 'kind', 'date', 'feeling', 'note']
    list_filter = ['kind', 'feeling']
    raw_id_fields = ['node']


@admin.register(NodeCloseness)
class NodeClosenessAdmin(admin.ModelAdmin):
    list_display = ['node', 'tier']
    list_filter = ['tier']
    raw_id_fields = ['node']

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

@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ['user', 'friend', 'relationship', 'created_at']
    raw_id_fields = ['user', 'friend', 'relationship']


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ['follower', 'target', 'created_at']
    raw_id_fields = ['follower', 'target']


@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ['sender', 'receiver', 'status', 'created_at', 'responded_at']
    list_filter = ['status']
    raw_id_fields = ['sender', 'receiver']


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    list_display = ['sender', 'receiver', 'created_at', 'analyzed']
    list_filter = ['analyzed']
    raw_id_fields = ['sender', 'receiver']


@admin.register(ChatAnalysis)
class ChatAnalysisAdmin(admin.ModelAdmin):
    list_display = ['user', 'friend', 'mood', 'updated_at']
    raw_id_fields = ['user', 'friend']


@admin.register(ProfileMediaItem)
class ProfileMediaItemAdmin(admin.ModelAdmin):
    list_display = ['user', 'kind', 'title', 'rating', 'completed_on', 'source']
    list_filter = ['kind', 'source', 'rating']
    search_fields = ['title', 'creator', 'user__username']
    raw_id_fields = ['user', 'source_journal']
