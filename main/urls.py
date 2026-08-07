from django.urls import path
from . import views
from .views_auth import (
    login_view, logout_view, register_view, profile_view, captcha_refresh,
)
from .views_notifications import notifications_view, sync_respond_api
from .views import (
    groups_view,
    assign_group_api,
    InformationListView,
    InformationDetailView,
    InformationCreateView,
    InformationUpdateView,
    InformationDeleteView,
)
from .views_journal_extra import (
    journal_save_api,
    journal_calendar_api,
    journal_entries_api,
)
from .views_interactions import (
    interaction_log_api,
    interactions_recent_api,
    set_closeness_api,
    health_api,
    node_relation_analyze_api,
)
from .views_followups import (
    followup_create_api,
    followup_toggle_api,
    followup_delete_api, followup_snooze_api,
    followups_list_api,
)
from .views_checkin import checkin_view, checkin_submit_api
from .views_connect import connect_info_api, connect_plan_api
from .views_stt import stt_api
from .views_social_pages import (
    discover_view,
    requests_view,
    share_view,
    suggest_users_api,
    share_send_api,
    gifbox_view,
    gifbox_send_api,
    gifbox_react_api,
    gifbox_open_api,
)
from .views_persona import (
    persona_get_api,
    persona_synthesize_api,
    rel_persona_get_api,
    rel_persona_synthesize_api,
)
from .views_psychology import relationship_pulse_create_api, extraction_inbox, extraction_suggestions_api, extraction_suggestion_decide_api
from .views_memory import (memory_hub, memory_timeline_view, knowledge_graph_view, memory_fact_api, memory_search_api,
    relationship_assistant_api, recommendation_feedback_api, node_merge_preview_api,
    node_merge_apply_api, node_merge_undo_api, clear_psychology_inferences_api)
from .views_relationship_life import (relationship_life_hub, meeting_briefing_api,
    quick_capture_api, meeting_reflection_api, commitment_action_api,
    safety_setting_api, person_export, csv_import_preview, csv_import_apply, service_worker,
    introduction_brief_api, person_delete_complete, trust_center_view, person_card_view,
    share_link_create_api, share_link_revoke_api, shared_person_card_view)
from .views_platform import (platform_tools_view, command_palette_api, onboarding_api, onboarding_complete_api,
    onboarding_goal_api,
    ai_quality_dashboard, ai_debug_private, ai_trace_rerun, feature_flags_view, frontend_error_api,
    system_health_api, encrypted_backup_download, encrypted_backup_preview,
    encrypted_backup_restore, demo_mode_api)
from .views_life import (
    life_event_create_api,
    life_event_delete_api,
    goal_create_api,
    goal_close_api,
    weekly_view,
    monthly_recap_view,
)
from .views_import import (
    telegram_import_view,
    telegram_scan_api,
    telegram_apply_api,
    telegram_undo_api,
    telegram_analyze_api,
    telegram_apply_mentions_api,
    telegram_relation_api,
    telegram_save_relation_api,
)
from .views_hubs import people_hub, insight_hub, relationship_work_hub, import_hub
from .views_whatsapp_import import whatsapp_import_view, whatsapp_scan_api, whatsapp_apply_api
from .views_ledger import (
    ledger_view,
    debt_create_api,
    debt_pay_api,
    debt_delete_api,
    borrow_suggest_api,
)
from .views_smart_features import (
    alerts_api,
    alerts_count_api,
    alert_recommendation_api,
    alert_action_api,
    rename_group_api,
    delete_group_api,
    alerts_view,
    psychology_view,
    psychology_ai_api,
    daily_tips_view,
    daily_tips_api,
)
from .views_social import (
    chat_view,
    chat_unread_api,
    social_view,
    discover_api,
    follow_api,
    friend_request_api,
    friend_respond_api,
    friends_api,
    unfollow_api,
    messages_api,
    message_send_api,
    profile_edit_view,
    profile_network_view,
    public_profile_view,
    work_suggest_api,
    work_cover_api,
    profile_cover_api,
    chat_analyze_api,
    information_share_api,
    post_create_api,
    typing_api,
)
from .views_social_circles import (
    circle_create_api,
    circle_messages_api,
    circle_send_api,
    circles_view,
)

urlpatterns = [
    path('service-worker.js', service_worker, name='service_worker'),
    path('api/system/health/', system_health_api, name='system_health'),
    path('platform/tools/', platform_tools_view, name='platform_tools'),
    path('api/platform/command-palette/', command_palette_api, name='command_palette'),
    path('api/platform/onboarding/', onboarding_api, name='onboarding_status'),
    path('api/platform/onboarding/goal/', onboarding_goal_api, name='onboarding_goal'),
    path('api/platform/onboarding/complete/', onboarding_complete_api, name='onboarding_complete'),
    path('api/platform/frontend-error/', frontend_error_api, name='frontend_error'),
    path('api/platform/backup/download/', encrypted_backup_download, name='encrypted_backup_download'),
    path('api/platform/backup/preview/', encrypted_backup_preview, name='encrypted_backup_preview'),
    path('api/platform/backup/restore/', encrypted_backup_restore, name='encrypted_backup_restore'),
    path('api/platform/demo/', demo_mode_api, name='demo_mode'),
    path('platform/ai-quality/', ai_quality_dashboard, name='ai_quality_dashboard'),
    path('platform/ai-debug/', ai_debug_private, name='ai_debug_private'),
    path('api/platform/ai-debug/<int:pk>/rerun/', ai_trace_rerun, name='ai_trace_rerun'),
    path('platform/feature-flags/', feature_flags_view, name='feature_flags'),

    # ======================
    # AUTH
    # ======================
    path('login/',       login_view,       name='login'),
    path('logout/',      logout_view,      name='logout'),
    path('register/',    register_view,    name='register'),
    path('profile/',     profile_view,     name='profile'),
    path('people/', people_hub, name='people_hub'),
    path('insight-center/', insight_hub, name='insight_hub'),
    path('relationship-work/', relationship_work_hub, name='relationship_work_hub'),
    path('import/', import_hub, name='import_hub'),
    path('api/captcha/', captcha_refresh,  name='captcha_refresh'),
    path('api/nodes/public-search/', views.public_node_search, name='public_node_search'),
    path('api/nodes/<int:pk>/quick-update/', views.node_quick_update, name='node_quick_update'),
    path('api/nodes/create-from-image/',    views.node_create_from_image, name='node_create_from_image'),
    path('api/relationships/quick-create/', views.relationship_quick_create, name='relationship_quick_create'),
    path('notifications/',           notifications_view,       name='notifications'),
    path('api/sync/<int:notif_id>/respond/', sync_respond_api, name='sync_respond'),
    path('export/',                  views.export_graph,       name='export_graph'),

    # ======================
    # HOME
    # ======================
    path('', views.HomeBriefingView.as_view(), name='home'),
    path('api/daily/snooze/', views.daily_action_snooze_api, name='daily_action_snooze'),
    path('api/daily/feedback/', views.daily_action_feedback_api, name='daily_action_feedback'),

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
    path('events/',            views.events_list,   name='events_list'),
    path('events/create/',     views.event_create,  name='event_create'),
    path('events/<int:pk>/delete/', views.event_delete, name='event_delete'),
    path('api/events/<int:pk>/complete/', views.event_complete_api, name='event_complete'),
    path('communities/', views.communities_view, name='communities'),
    path('insights/', views.insights_view, name='insights'),   # → redirect به /daily/ (حذف شده)
    path('api/node/<int:pk>/summary/', views.node_ai_summary, name='node_ai_summary'),
    path('chat/', views.chat_view, name='chat'),
    path('api/chat/', views.chat_api, name='chat_api'),
    path('api/chat/clear/', views.chat_clear_api, name='chat_clear'),
    path('api/chat/save-journal/', views.chat_to_journal_api, name='chat_save_journal'),

    # ======================
    # SETTINGS
    # ======================
    path('settings/', views.settings_view, name='settings'),

    # ======================
    # JOURNAL
    # ======================
    path('journal/', views.journal_view, name='journal'),
    path('api/journal/analyze/',      views.journal_analyze_api,      name='journal_analyze'),
    path('api/journal/apply/',        views.journal_apply_api,        name='journal_apply'),
    path('api/journal/upload-image/', views.journal_image_upload_api, name='journal_image_upload'),
    path('api/journal/save/',         journal_save_api,               name='journal_save'),
    path('api/journal/calendar/',     journal_calendar_api,           name='journal_calendar'),
    path('api/journal/entries/',      journal_entries_api,            name='journal_entries'),

    # ======================
    # INTERACTIONS + HEALTH (V4)
    # ======================
    path('api/interactions/log/',        interaction_log_api,     name='interaction_log'),
    path('api/interactions/recent/',     interactions_recent_api, name='interactions_recent'),
    path('api/nodes/<int:pk>/closeness/', set_closeness_api,      name='set_closeness'),
    path('api/nodes/<int:pk>/relation-analyze/', node_relation_analyze_api, name='node_relation_analyze'),
    path('api/health/',                  health_api,              name='health_api'),
    path('api/followups/create/',        followup_create_api,     name='followup_create'),
    path('api/followups/<int:pk>/toggle/', followup_toggle_api,   name='followup_toggle'),
    path('api/followups/<int:pk>/snooze/', followup_snooze_api,   name='followup_snooze'),
    path('api/followups/<int:pk>/delete/', followup_delete_api,   name='followup_delete'),
    path('api/followups/',               followups_list_api,      name='followups_list'),
    path('checkin/',                     checkin_view,            name='checkin'),
    path('api/checkin/',                 checkin_submit_api,      name='checkin_submit'),
    path('api/connect/<int:pk>/',        connect_info_api,        name='connect_info'),
    path('api/connect/<int:pk>/plan/',   connect_plan_api,        name='connect_plan'),
    path('ledger/',                      ledger_view,             name='ledger'),
    path('api/debts/create/',            debt_create_api,         name='debt_create'),
    path('api/debts/<int:pk>/pay/',      debt_pay_api,            name='debt_pay'),
    path('api/debts/<int:pk>/delete/',   debt_delete_api,         name='debt_delete'),
    path('api/borrow/suggest/',          borrow_suggest_api,      name='borrow_suggest'),
    path('api/stt/',                     stt_api,                 name='stt'),
    path('api/persona/node/<int:pk>/',            persona_get_api,            name='persona_get'),
    path('api/persona/node/<int:pk>/synthesize/', persona_synthesize_api,     name='persona_synthesize'),
    path('api/persona/rel/<int:pk>/',             rel_persona_get_api,        name='rel_persona_get'),
    path('api/persona/rel/<int:pk>/synthesize/',  rel_persona_synthesize_api, name='rel_persona_synthesize'),
    path('weekly/',                      weekly_view,             name='weekly'),
    path('monthly/',                     monthly_recap_view,      name='monthly_recap'),
    path('api/life-events/create/',      life_event_create_api,   name='life_event_create'),
    path('api/life-events/<int:pk>/delete/', life_event_delete_api, name='life_event_delete'),
    path('api/goals/create/',            goal_create_api,         name='goal_create'),
    path('api/goals/<int:pk>/close/',    goal_close_api,          name='goal_close'),
    path('import/telegram/',             telegram_import_view,    name='telegram_import'),
    path('import/whatsapp/',             whatsapp_import_view,    name='whatsapp_import'),
    path('api/import/whatsapp/scan/',    whatsapp_scan_api,       name='whatsapp_scan'),
    path('api/import/whatsapp/apply/',   whatsapp_apply_api,      name='whatsapp_apply'),
    path('api/import/telegram/scan/',    telegram_scan_api,       name='telegram_scan'),
    path('api/import/telegram/apply/',   telegram_apply_api,      name='telegram_apply'),
    path('api/import/telegram/undo/',    telegram_undo_api,       name='telegram_undo'),
    path('api/import/telegram/analyze/', telegram_analyze_api,    name='telegram_analyze'),
    path('api/import/telegram/apply-mentions/', telegram_apply_mentions_api, name='telegram_apply_mentions'),
    path('api/import/telegram/relation/',        telegram_relation_api,      name='telegram_relation'),
    path('api/import/telegram/save-relation/',   telegram_save_relation_api, name='telegram_save_relation'),

    # ======================
    # SMART FEATURES
    # ======================
    path('alerts/',                      alerts_view,                name='alerts'),
    path('psychology/',                  psychology_view,            name='psychology'),
    path('daily/',                       daily_tips_view,            name='daily'),
    path('api/alerts/',                  alerts_api,                 name='alerts_api'),
    path('api/alerts/count/',            alerts_count_api,           name='alerts_count_api'),
    path('api/alerts/recommendation/',   alert_recommendation_api,   name='alert_recommendation_api'),
    path('api/alerts/action/',           alert_action_api,           name='alert_action_api'),
    path('api/groups/rename/',           rename_group_api,           name='rename_group_api'),
    path('api/groups/assign/',           assign_group_api,           name='assign_group_api'),
    path('api/groups/delete/',           delete_group_api,           name='delete_group_api'),
    path('groups/',                      groups_view,                name='groups'),
    path('api/psychology/analyze/',      psychology_ai_api,          name='psychology_ai_api'),
    path('api/psychology/pulse/',        relationship_pulse_create_api, name='relationship_pulse_create'),
    path('extractions/',                 extraction_inbox, name='extraction_inbox'),
    path('memory/',                      memory_hub, name='memory_hub'),
    path('memory/timeline/',             memory_timeline_view, name='memory_timeline'),
    path('memory/knowledge/',            knowledge_graph_view, name='knowledge_graph'),
    path('api/memory/facts/',            memory_fact_api, name='memory_fact_create'),
    path('api/memory/facts/<int:pk>/',   memory_fact_api, name='memory_fact_update'),
    path('api/memory/search/',           memory_search_api, name='memory_search'),
    path('api/memory/assistant/<int:pk>/', relationship_assistant_api, name='relationship_assistant'),
    path('api/memory/recommendations/<int:pk>/', recommendation_feedback_api, name='recommendation_feedback'),
    path('api/memory/merge/preview/',    node_merge_preview_api, name='node_merge_preview'),
    path('api/memory/merge/',            node_merge_apply_api, name='node_merge_apply'),
    path('api/memory/merge/<int:pk>/undo/', node_merge_undo_api, name='node_merge_undo'),
    path('api/memory/psychology/clear/', clear_psychology_inferences_api, name='clear_psychology_inferences'),
    path('relationship-life/',           relationship_life_hub, name='relationship_life_hub'),
    path('trust/',                       trust_center_view, name='trust_center'),
    path('people/<int:pk>/card/',        person_card_view, name='person_card'),
    path('api/people/<int:pk>/share-link/', share_link_create_api, name='share_link_create'),
    path('api/share-links/<uuid:token>/revoke/', share_link_revoke_api, name='share_link_revoke'),
    path('shared/person/<uuid:token>/', shared_person_card_view, name='shared_person_card'),
    path('api/relationship-life/briefing/<int:pk>/', meeting_briefing_api, name='meeting_briefing'),
    path('api/relationship-life/capture/', quick_capture_api, name='quick_capture'),
    path('api/relationship-life/reflection/', meeting_reflection_api, name='meeting_reflection'),
    path('api/relationship-life/commitments/<int:pk>/', commitment_action_api, name='commitment_action'),
    path('api/relationship-life/safety/<int:pk>/', safety_setting_api, name='safety_setting'),
    path('api/relationship-life/person/<int:pk>/export/', person_export, name='person_export'),
    path('api/relationship-life/person/<int:pk>/delete/', person_delete_complete, name='person_delete_complete'),
    path('api/relationship-life/introduction/', introduction_brief_api, name='introduction_brief'),
    path('api/relationship-life/import/csv/preview/', csv_import_preview, name='csv_import_preview'),
    path('api/relationship-life/import/csv/apply/', csv_import_apply, name='csv_import_apply'),
    path('api/extractions/',             extraction_suggestions_api, name='extraction_suggestions'),
    path('api/extractions/<int:pk>/',    extraction_suggestion_decide_api, name='extraction_suggestion_decide'),
    path('api/daily/tips/',              daily_tips_api,             name='daily_tips_api'),

    # ======================
    # SOCIAL GRAPH
    # ======================
    path('social/gifbox/',               gifbox_view,                name='social_gifbox'),
    path('api/social/gifbox/send/',      gifbox_send_api,            name='gifbox_send'),
    path('api/social/gifbox/<int:box_id>/react/', gifbox_react_api,  name='gifbox_react'),
    path('api/social/gifbox/<int:box_id>/open/',  gifbox_open_api,   name='gifbox_open'),
    path('social/',                      social_view,                name='social'),
    path('social/discover/',             discover_view,              name='social_discover_page'),
    path('social/requests/',             requests_view,              name='social_requests_page'),
    path('social/share/',                share_view,                 name='social_share_page'),
    path('api/social/suggest/',          suggest_users_api,          name='social_suggest'),
    path('api/social/share/send/',       share_send_api,             name='social_share_send'),
    path('social/chat/',                 chat_view,                  name='social_chat'),
    path('social/circles/',              circles_view,               name='social_circles'),
    path('profile/edit/',                profile_edit_view,           name='profile_edit'),
    path('u/<str:username>/',            public_profile_view,         name='public_profile'),
    path('u/<str:username>/<str:kind>/',  profile_network_view,        name='profile_network'),
    path('api/social/discover/',         discover_api,               name='social_discover'),
    path('api/social/works/suggest/',    work_suggest_api,            name='social_work_suggest'),
    path('api/social/works/<int:work_id>/cover/', work_cover_api,     name='social_work_cover'),
    path('api/social/friends/',          friends_api,                name='social_friends'),
    path('api/social/follow/<int:user_id>/', follow_api,             name='social_follow'),
    path('api/social/unfollow/<int:user_id>/', unfollow_api,         name='social_unfollow'),
    path('api/social/request/<int:user_id>/', friend_request_api,    name='social_friend_request'),
    path('api/social/request/respond/<int:request_id>/', friend_respond_api, name='social_friend_respond'),
    path('api/social/messages/<int:user_id>/', messages_api,         name='social_messages'),
    path('api/social/messages/<int:user_id>/send/', message_send_api, name='social_message_send'),
    path('api/social/messages/<int:user_id>/analyze/', chat_analyze_api, name='social_chat_analyze'),
    path('api/social/messages/<int:user_id>/typing/', typing_api,     name='social_typing'),
    path('api/social/messages/unread/',   chat_unread_api,            name='social_unread'),
    path('api/social/profile/cover/',      profile_cover_api,          name='social_profile_cover'),
    path('api/social/share-info/<int:info_id>/', information_share_api, name='social_share_info'),
    path('api/social/posts/',                   post_create_api,       name='social_post_create'),
    path('api/social/circles/',                 circle_create_api,     name='social_circle_create'),
    path('api/social/circles/<int:circle_id>/messages/', circle_messages_api, name='social_circle_messages'),
    path('api/social/circles/<int:circle_id>/messages/send/', circle_send_api, name='social_circle_send'),

    # ======================
    # LEGACY / API
    # ======================
    path('api/home/graph/', views.home_graph_api, name='home_graph_api'),
    path('api/graph/level/<int:level>/', views.graph_level_data, name='graph_level_data'),
    path("api/graph/all/", views.graph_all_api),
    path('legacy/info/<int:info_id>/', views.information_detail, name='legacy_information_detail'),

]
