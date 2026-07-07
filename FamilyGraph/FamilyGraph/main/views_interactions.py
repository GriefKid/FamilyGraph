"""
views_interactions.py — API های ثبت سریع تعامل و سلامت رابطه (V4)
"""
import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Node, CLOSENESS_CHOICES
from .health import compute_health, health_summary
from .utils_jalali import jalali_str

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
@csrf_exempt
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
            date_val = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'error': 'فرمت تاریخ: YYYY-MM-DD'}, status=400)
    else:
        date_val = timezone.localdate()
    if date_val > timezone.localdate():
        return JsonResponse({'error': 'تاریخ تعامل نمی‌تونه آینده باشه'}, status=400)

    note = (body.get('note') or '').strip()[:300]

    try:
        from .models import Interaction
        inter = Interaction.objects.create(
            node=node, kind=kind, date=date_val,
            feeling=feeling, note=note, owner=request.user,
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
        qs = Interaction.objects.filter(owner=request.user).select_related('node')
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
@csrf_exempt
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
@csrf_exempt
def node_relation_analyze_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    user = request.user
    try:
        node = Node.objects.get(pk=pk, owner=user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)

    from django.db.models import Q as _Q
    from .models import Relationship, JournalEntry
    nm = node.display_name()
    facts = []

    # رابطه با root
    root = user.root_node
    if root:
        rels = Relationship.objects.filter(
            _Q(source=root, target=node) | _Q(source=node, target=root), owner=user)
        for r in rels:
            facts.append(f"نوع رابطه: {r.rel or 'نامشخص'} | قدرت: {r.strength}/5 | وضعیت: {r.status}"
                         + (f" | آشنایی از: {r.met_at}" if r.met_at else ""))

    # سلامت
    h = _node_health(user, node.id)
    if h and h.get('status') != 'unknown':
        facts.append(f"سلامت رابطه: {h['label']} | آخرین تعامل: {h['days_since']} روز پیش "
                     f"| انتظار تماس: هر {h['expected']} روز")

    # تعامل‌ها + حس‌ها
    try:
        from .models import Interaction
        inters = list(Interaction.objects.filter(owner=user, node=node)
                      .order_by('-date')[:120])
        if inters:
            kinds = {}
            feels = [i.feeling for i in inters if i.feeling]
            for i in inters:
                kinds[i.get_kind_display()] = kinds.get(i.get_kind_display(), 0) + 1
            facts.append(f"{len(inters)} تعامل ثبت‌شده: " +
                         '، '.join(f"{k}×{v}" for k, v in kinds.items()))
            if feels:
                avg = sum(feels) / len(feels)
                facts.append(f"میانگین حس بعد از تعامل: {avg:+.2f} "
                             f"({'انرژی‌بخش' if avg > 0.3 else ('انرژی‌گیر' if avg < -0.3 else 'خنثی')})")
    except Exception:
        pass

    # ژورنال — آخرین ذکرها
    try:
        entries = list(node.journal_entries.filter(owner=user).order_by('-created_at')[:5])
        for e in entries:
            facts.append(f"یادداشت ({e.entry_date or e.created_at.date()}): {e.text[:180]}")
    except Exception:
        pass

    # قرض و فالوآپ
    try:
        from .models import Debt
        open_d = Debt.objects.filter(owner=user, node=node, settled=False).count()
        done_d = Debt.objects.filter(owner=user, node=node, settled=True).count()
        if open_d or done_d:
            facts.append(f"حساب مالی: {open_d} قلم باز، {done_d} تسویه‌شده")
    except Exception:
        pass
    try:
        from .models import FollowUp
        fu_o = FollowUp.objects.filter(owner=user, node=node, done=False).count()
        fu_d = FollowUp.objects.filter(owner=user, node=node, done=True).count()
        if fu_o or fu_d:
            facts.append(f"موضوعات باز: {fu_o} باز، {fu_d} انجام‌شده")
    except Exception:
        pass

    # شناخت قبلی
    try:
        info = node.informations.first()
        if info and isinstance(info.data, dict) and info.data.get('personality'):
            facts.append(f"شناخت قبلی: {str(info.data['personality'])[:200]}")
    except Exception:
        pass

    if len(facts) < 2:
        return JsonResponse({'error': 'داده‌ی کافی درباره این رابطه ثبت نشده — '
                                      'چند تعامل/یادداشت ثبت کن یا از ایمپورت تلگرام بیا'}, status=400)

    from .views_smart_features import _ai_client, _model, _extract_json, _rate_limit_msg
    client, api_key, _prov = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'کلید AI تنظیم نشده'}, status=500)

    prompt = f"""داده‌های ثبت‌شده درباره رابطه‌ی من و «{nm}» در اپ روابطم:

""" + '\n'.join(f"- {f}" for f in facts) + f"""

به‌عنوان روانشناس روابط، این رابطه رو تحلیل کن. منصف و مستند به همین داده‌ها. فارسی خودمونی.

JSON خالص:
{{
  "personality": "برداشتت از {nm} بر اساس داده‌ها، ۲-۳ جمله",
  "communication_style": "الگوی ارتباطی این رابطه در یک جمله",
  "values": [], "interests": [],
  "strengths": ["نقاط قوت این رابطه، حداکثر ۳"],
  "red_flags": ["نکات هشدار اگه هست، حداکثر ۳"],
  "relationship_quality": "کیفیت کلی در یک جمله",
  "friendship_score": 0-100,
  "score_reasons": ["۲-۳ دلیل کوتاه"],
  "suggested_rel_type": "...", "suggested_strength": 1-5,
  "tip": "یه توصیه عملی"
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'روانشناس روابط — دقیق و مستند به داده. فقط JSON.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=1000,
        )
        result = _extract_json(resp.choices[0].message.content)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


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
