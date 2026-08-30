"""Web Push helpers for the weekly relationship pulse.

The actual encrypted send goes through `pywebpush` (add it to
requirements and `pip install`). Everything else — subscriptions, the
digest, the API and the service worker — works without it; when the
library or the VAPID keys are missing, sending is a logged no-op.
"""
import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def vapid_public_key():
    return getattr(settings, 'VAPID_PUBLIC_KEY', '') or ''


def push_configured():
    return bool(getattr(settings, 'VAPID_PRIVATE_KEY', '') and vapid_public_key())


def push_available():
    """True when we can actually send (keys + library present)."""
    if not push_configured():
        return False
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:
        return False


def send_web_push(subscription_info, payload):
    """Send one notification.

    Returns (ok, gone). `gone` means the subscription is dead (404/410) and
    the caller should delete it.
    """
    if not push_available():
        logger.info('push skipped: pywebpush or VAPID keys not configured')
        return False, False
    from pywebpush import webpush, WebPushException
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={'sub': f"mailto:{getattr(settings, 'VAPID_ADMIN_EMAIL', 'admin@example.com')}"},
            timeout=10,
        )
        return True, False
    except WebPushException as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status in (404, 410):
            return False, True
        logger.warning('web push failed (%s): %s', status, exc)
        return False, False
    except Exception as exc:  # network, encoding, ...
        logger.warning('web push error: %s', exc)
        return False, False


def build_pulse(user):
    """A short, action-first digest for one user, or None if nothing is due."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import FollowUp
    try:
        from .health import compute_health, attention_priority
    except Exception:
        return None

    today = timezone.localdate()
    lines = []

    try:
        priority = attention_priority(user, compute_health(user))
        ranked = sorted(priority.items(), key=lambda kv: -kv[1]['score'])
        top = [nid for nid, row in ranked[:2] if row['score'] >= 25]
        if top:
            from .models import Node
            names = dict(
                Node.objects.filter(owner=user, id__in=top).values_list('id', 'username')
            )
            for nid in top:
                who = names.get(nid, 'یک نفر')
                reason = (priority[nid]['factors'] or ['یک قدم توجه می‌خواهد'])[0]
                lines.append(f'{who}: {reason}')
    except Exception:
        pass

    try:
        overdue = FollowUp.objects.filter(
            owner=user, node__owner=user, done=False, due_date__lt=today,
        ).count()
        if overdue:
            lines.append(f'{overdue} پیگیری عقب‌افتاده')
    except Exception:
        pass

    if not lines:
        return None
    return {
        'title': 'نبض هفتگی روابط',
        'body': ' · '.join(lines[:3]),
        'url': '/weekly/',
        'tag': 'relationship-pulse',
    }
