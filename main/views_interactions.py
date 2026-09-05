"""
views_interactions.py — API های ثبت سریع تعامل و سلامت رابطه (V4)
"""
import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.utils import timezone

from .models import Node, CLOSENESS_CHOICES
from .health import compute_health, health_summary
from .utils_jalali import jalali_str, parse_date_input

MIGRATION_MSG = ('جدول تعامل‌ها هنوز ساخته نشده — '
                 'فایل migrate_and_run.bat رو یه بار اجرا کن.')

KIND_LABELS = {
    'call': '📞 تلفنی', 'meet': '🤝 حضوری', 'message': '💬 پیام',
    'online': '🌐 آنلاین', 'journal': '📓 از ژورنال',
    'checkin': '⚡ چک-این', 'other': '✦ سایر',
}
FEELING_EMOJI = {1: '😊', 0: '😐', -1: '😕'}


def _body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return None


def _serialize(i):
    return {
        'id':         i.id,
        'node_id':    i.node_id,
        'kind':       i.kind,
        'kind_label': KIND_LABELS.get(i.kind, i.kind),
        'date':       str(i.date),
        'date_fa':    jalali_str(i.date),
        'feeling':    i.feeling,
        'feeling_emoji': FEELING_EMOJI.get(i.feeling, '😐'),
        'note':       i.note,
    }


def _node_health(user, node_id):
    """سلامت یک نود بعد از تغییر — برای آپدیت زنده‌ی UI."""
    h = compute_health(user).get(node_id)
    if not h:
        return None
    out = dict(h)
    if out.get('last_date'):
        out['last_date_fa'] = jalali_str(out['last_date'])
        out['last_date'] = str(out['last_date'])
    return out


# ═══════════════════════════════════════════════════════════════
#  POST /api/interactions/log/
# ═══════════════════════════════════════════════════════════════

@login_required
def interaction_log_api(request):
    """POST {node_id, kind, feeling?, note?, date?} → ثبت تعامل + سلامت جدید."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    try:
        node = Node.objects.get(pk=body.get('node_id'), owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)

    kind = body.get('kind', 'call')
    if kind not in KIND_LABELS:
        kind = 'other'

    try:
        feeling = int(body.get('feeling', 0))
    except (TypeError, ValueError):
        feeling = 0
    feeling = max(-1, min(1, feeling))

    date_str = (body.get('date') or '').strip()
    if date_str:
        try:
            date_val = parse_date_input(date_str)
        except ValueError:
            return JsonResponse({'error': 'فرمت تاریخ: ۱۴۰۴/۰۱/۰۱'}, status=400)
    else:
        date_val = timezone.localdate()
    if date_val > timezone.localdate():
        return JsonResponse({'error': 'تاریخ تعامل نمی‌تونه آینده باشه'}, status=400)

    note = (body.get('note') or '').strip()[:300]
    support_kind = (body.get('support_kind') or '').strip()
    from .models import Interaction as _I
    if support_kind not in dict(_I.SUPPORT_CHOICES):
        support_kind = ''

    try:
        from .models import Interaction
        inter = Interaction.objects.create(
            node=node, kind=kind, date=date_val,
            feeling=feeling, support_kind=support_kind, note=note, owner=request.user,
        )
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)

    # یادآوری موضوعات باز — «راستی این‌ها رو باهاش داری»
    from .views_followups import open_followups_for
    return JsonResponse({
        'ok': True,
        'interaction': _serialize(inter),
        'health': _node_health(request.user, node.id),
        'open_followups': open_followups_for(request.user, node.id),
    })


# ═══════════════════════════════════════════════════════════════
#  GET /api/interactions/recent/?node_id=
# ═══════════════════════════════════════════════════════════════

@login_required
def interactions_recent_api(request):
    """۲۰ تعامل آخر یک نود (یا کل شبکه اگه node_id ندی)."""
    try:
        from .models import Interaction
        qs = Interaction.objects.filter(
            owner=request.user, node__owner=request.user,
        ).select_related('node')
        node_id = request.GET.get('node_id')
        if node_id:
            qs = qs.filter(node_id=node_id)
        items = []
        for i in qs[:20]:
            d = _serialize(i)
            d['node_name'] = i.node.display_name()
            items.append(d)
        return JsonResponse({'ok': True, 'interactions': items})
    except (OperationalError, ProgrammingError):
        return JsonResponse({'ok': True, 'interactions': [], 'warning': MIGRATION_MSG})


# ═══════════════════════════════════════════════════════════════
#  POST /api/nodes/<pk>/closeness/
# ═══════════════════════════════════════════════════════════════

@login_required
def set_closeness_api(request, pk):
    """POST {closeness} → تعیین دایره نزدیکی نود."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    tier = (body.get('closeness') or '').strip()
    valid = {c[0] for c in CLOSENESS_CHOICES} | {''}
    if tier not in valid:
        return JsonResponse({'error': 'tier نامعتبر'}, status=400)

    try:
        node = Node.objects.get(pk=pk, owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)

    try:
        from .models import NodeCloseness
        if tier:
            NodeCloseness.objects.update_or_create(
                node=node, defaults={'tier': tier, 'owner': request.user},
            )
        else:
            # «خودکار» = حذف تنظیم → fallback از قدرت رابطه
            NodeCloseness.objects.filter(node=node, owner=request.user).delete()
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': 'جدول دایره نزدیکی هنوز ساخته نشده — '
                                      'migrate_and_run.bat رو اجرا کن.'}, status=503)

    return JsonResponse({
        'ok': True,
        'closeness': tier,
        'health': _node_health(request.user, node.id),
    })


# ═══════════════════════════════════════════════════════════════
#  POST /api/nodes/<pk>/relation-analyze/  (V9)
#  تحلیل رابطه از داده‌های خود اپ — تعامل‌ها، حس‌ها، ژورنال، قرض، فالوآپ
# ═══════════════════════════════════════════════════════════════

@login_required
def node_relation_analyze_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    user = request.user
    try:
        node = Node.objects.get(pk=pk, owner=user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)

    try:
        from .relationship_intelligence import analyze_person_relationship
        result = analyze_person_relationship(user, node)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': f'تحلیل داده‌ها انجام نشد: {str(e)[:160]}'}, status=500)


# ═══════════════════════════════════════════════════════════════
#  GET /api/health/
# ═══════════════════════════════════════════════════════════════

@login_required
def health_api(request):
    """سلامت همه‌ی روابط root — برای گراف و داشبورد."""
    hmap = compute_health(request.user)
    out = {}
    for nid, h in hmap.items():
        e = dict(h)
        if e.get('last_date'):
            e['last_date_fa'] = jalali_str(e['last_date'])
            e['last_date'] = str(e['last_date'])
        out[str(nid)] = e
    return JsonResponse({'ok': True, 'health': out, 'summary': health_summary(hmap)})
