"""
views_followups.py — API های «موضوعات باز» (V4)
قول‌ها، سوال‌ها و کارهای نیمه‌کاره با هر شخص.
"""
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Node
from .utils_jalali import jalali_str

MIGRATION_MSG = ('جدول موضوعات باز هنوز ساخته نشده — '
                 'فایل migrate_and_run.bat رو یه بار اجرا کن.')


def _body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return None


def serialize_followup(f, today=None):
    today = today or timezone.localdate()
    days_left = (f.due_date - today).days if f.due_date else None
    return {
        'id':         f.id,
        'node_id':    f.node_id,
        'text':       f.text,
        'due_date':   str(f.due_date) if f.due_date else None,
        'due_fa':     jalali_str(f.due_date) if f.due_date else None,
        'days_left':  days_left,
        'overdue':    bool(f.due_date and days_left is not None and days_left < 0 and not f.done),
        'done':       f.done,
    }


def open_followups_for(user, node_id, limit=5):
    """موضوعات باز یک نود — برای یادآوری موقع ثبت تعامل. fail-safe."""
    try:
        from .models import FollowUp
        qs = FollowUp.objects.filter(
            owner=user, node_id=node_id, node__owner=user, done=False,
        )[:limit]
        return [serialize_followup(f) for f in qs]
    except (OperationalError, ProgrammingError):
        return []
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
#  POST /api/followups/create/
# ═══════════════════════════════════════════════════════════════

@login_required
def followup_create_api(request):
    """POST {node_id, text, due_date?} → ساخت موضوع باز."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    text = (body.get('text') or '').strip()[:300]
    if not text:
        return JsonResponse({'error': 'متن موضوع خالیه'}, status=400)

    try:
        node = Node.objects.get(pk=body.get('node_id'), owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)

    due = None
    due_str = (body.get('due_date') or '').strip()
    if due_str:
        try:
            due = datetime.strptime(due_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'error': 'فرمت تاریخ: YYYY-MM-DD'}, status=400)

    try:
        from .models import FollowUp
        f = FollowUp.objects.create(node=node, text=text, due_date=due, owner=request.user)
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)

    return JsonResponse({'ok': True, 'followup': serialize_followup(f)})


# ═══════════════════════════════════════════════════════════════
#  POST /api/followups/<pk>/toggle/
# ═══════════════════════════════════════════════════════════════

@login_required
def followup_toggle_api(request, pk):
    """POST → تیک زدن / برداشتن تیک."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        from .models import FollowUp
        f = FollowUp.objects.get(pk=pk, owner=request.user)
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)
    except Exception:
        return JsonResponse({'error': 'پیدا نشد'}, status=404)

    f.done = not f.done
    f.done_at = timezone.now() if f.done else None
    f.save(update_fields=['done', 'done_at'])
    return JsonResponse({'ok': True, 'followup': serialize_followup(f)})


@login_required
@csrf_exempt
def followup_snooze_api(request, pk):
    """POST {days?} → defer an open follow-up without discarding it."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request) or {}
    try:
        days = max(1, min(int(body.get('days', 7)), 90))
        from .models import FollowUp
        followup = FollowUp.objects.get(pk=pk, owner=request.user, done=False)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'days نامعتبر است'}, status=400)
    except FollowUp.DoesNotExist:
        return JsonResponse({'error': 'پیدا نشد'}, status=404)
    followup.due_date = timezone.localdate() + timedelta(days=days)
    followup.save(update_fields=['due_date'])
    return JsonResponse({'ok': True, 'followup': serialize_followup(followup)})


# ═══════════════════════════════════════════════════════════════
#  POST /api/followups/<pk>/delete/
# ═══════════════════════════════════════════════════════════════

@login_required
def followup_delete_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        from .models import FollowUp
        FollowUp.objects.filter(pk=pk, owner=request.user).delete()
        return JsonResponse({'ok': True})
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)


# ═══════════════════════════════════════════════════════════════
#  GET /api/followups/?node_id=
# ═══════════════════════════════════════════════════════════════

@login_required
def followups_list_api(request):
    """لیست موضوعات — باز‌ها + ۱۰ تای آخر انجام‌شده."""
    try:
        from .models import FollowUp
        qs = FollowUp.objects.filter(
            owner=request.user, node__owner=request.user,
        ).select_related('node')
        node_id = request.GET.get('node_id')
        if node_id:
            qs = qs.filter(node_id=node_id)
        open_items = [serialize_followup(f) for f in qs.filter(done=False)[:50]]
        done_items = [serialize_followup(f) for f in qs.filter(done=True)[:10]]
        return JsonResponse({'ok': True, 'open': open_items, 'done': done_items})
    except (OperationalError, ProgrammingError):
        return JsonResponse({'ok': True, 'open': [], 'done': [], 'warning': MIGRATION_MSG})


@login_required
def followups_view(request):
    """A single owner-scoped inbox for open and completed relationship follow-ups."""
    from .models import FollowUp
    query = (request.GET.get('q') or '').strip()[:80]
    show = request.GET.get('show', 'open')
    if show not in {'open', 'done', 'all', 'overdue'}:
        show = 'open'
    qs = FollowUp.objects.filter(owner=request.user).select_related('node')
    if show == 'open':
        qs = qs.filter(done=False)
    elif show == 'done':
        qs = qs.filter(done=True)
    elif show == 'overdue':
        qs = qs.filter(done=False, due_date__lt=timezone.localdate())
    if query:
        qs = qs.filter(text__icontains=query)
    qs = qs.order_by('done', 'due_date', '-created_at')[:100]
    return render(request, 'followups/followups.html', {
        'followups': qs,
        'show': show,
        'query': query,
        'open_count': FollowUp.objects.filter(owner=request.user, done=False).count(),
        'done_count': FollowUp.objects.filter(owner=request.user, done=True).count(),
        'nodes': Node.objects.filter(owner=request.user, merged_into__isnull=True).order_by('username'),
        'today': timezone.localdate(),
    })
