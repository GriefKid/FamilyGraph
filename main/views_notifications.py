"""
views_notifications.py — صفحه اطلاعیه‌ها + پاسخ به SyncNotification
"""
import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Notification, SyncNotification, Node


@login_required
def notifications_view(request):
    user = request.user
    sync_notifs = SyncNotification.objects.filter(
        recipient=user,
        status='pending',
    ).select_related('from_user').order_by('-created_at')

    general_notifs = Notification.objects.filter(
        user=user,
    ).order_by('-created_at')[:30]

    # علامت‌گذاری همه general به عنوان خوانده‌شده
    Notification.objects.filter(user=user, is_read=False).update(is_read=True)

    return render(request, 'notifications/notifications.html', {
        'sync_notifs':    sync_notifs,
        'general_notifs': general_notifs,
        'notification_mode': (user.feature_overrides or {}).get('notification_mode', 'important'),
    })


@login_required
@require_POST
def notification_preferences_api(request):
    """Save the user's preferred cadence for non-essential notifications.

    This is deliberately separate from sync requests: those are direct data
    changes and must remain visible until the owner responds to them.
    """
    try:
        data = json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return JsonResponse({'error': 'JSON نامعتبر است.'}, status=400)

    mode = data.get('mode')
    if mode not in {'important', 'daily', 'weekly'}:
        return JsonResponse({'error': 'تنظیم اعلان نامعتبر است.'}, status=400)

    overrides = dict(request.user.feature_overrides or {})
    overrides['notification_mode'] = mode
    request.user.feature_overrides = overrides
    request.user.save(update_fields=['feature_overrides'])
    return JsonResponse({'ok': True, 'mode': mode})


@login_required
@require_POST
def sync_respond_api(request, notif_id):
    """پاسخ به SyncNotification: accepted / ignored / flagged"""
    try:
        data   = json.loads(request.body)
        action = data.get('action')   # 'accepted' | 'ignored' | 'flagged'
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    if action not in ('accepted', 'ignored', 'flagged'):
        return JsonResponse({'error': 'action نامعتبر'}, status=400)

    notif = get_object_or_404(SyncNotification, id=notif_id, recipient=request.user)
    notif.status = action
    notif.save()

    if action == 'accepted':
        _apply_sync(notif, request.user)

    return JsonResponse({'ok': True, 'action': action})


def _apply_sync(notif: SyncNotification, recipient):
    """اعمال تغییرات روی نود وقتی کاربر «بله» زد."""
    payload = notif.payload or {}
    node_id = payload.get('node_id')

    # پیدا کردن نود
    node = None
    if node_id:
        try:
            node = Node.objects.get(id=node_id, owner=recipient)
        except Node.DoesNotExist:
            pass
    if node is None:
        try:
            node = Node.objects.get(username=notif.node_username, owner=recipient)
        except Node.DoesNotExist:
            return

    # لینک به کاربر public
    node.imported_from   = notif.from_user
    node.username_locked = True

    # آپدیت داده‌های عمومی
    if payload.get('first_name'):
        node.first_name = payload['first_name']
    if payload.get('last_name'):
        node.last_name = payload['last_name']
    if payload.get('career'):
        node.career = payload['career']
    # username رو تغییر نده — فقط قفل کن
    node.save()
