"""
views_checkin.py — چک-این روزانه (V5)

جایگزین سبک ژورنال برای روزهایی که حوصله‌ی نوشتن نیست:
۳ قدم، همه با ضربه — «با کیا در تماس بودی؟ حالت چطوره؟ چیز مهمی شد؟»
خروجی همون JournalEntry + Interaction است، پس همه‌ی موتورهای
سلامت/هشدار/روانشناسی بدون تغییر تغذیه می‌شن.
"""
import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Node, JournalEntry
from .utils_jalali import jalali_full_str, jalali_day_name
from .health import compute_health

MOOD_LABELS = {
    2:  'عالی 😄',
    1:  'خوب 🙂',
    0:  'معمولی 😐',
    -1: 'ناراحت 😕',
    -2: 'خیلی ناراحت و غمگین 😞',
}
VALID_KINDS = {'call', 'meet', 'message', 'online', 'checkin'}


def journal_streak(user):
    """چند روز پشت سر هم (امروز یا از دیروز) یادداشت/چک-این ثبت شده."""
    try:
        dates = set()
        rows = JournalEntry.objects.filter(owner=user) \
                                   .values_list('entry_date', 'created_at')[:400]
        for ed, ca in rows:
            d = ed or (ca.date() if ca else None)
            if d:
                dates.add(d)
        today = timezone.localdate()
        start = today if today in dates else today - timedelta(days=1)
        streak, d = 0, start
        while d in dates:
            streak += 1
            d -= timedelta(days=1)
        return streak
    except Exception:
        return 0


def _todays_checkin(user):
    """entry چک-این امروز اگه وجود داره."""
    today = timezone.localdate()
    try:
        for e in JournalEntry.objects.filter(owner=user, entry_date=today):
            if 'checkin' in (e.tags or []):
                return e
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
#  GET /checkin/
# ═══════════════════════════════════════════════════════════════

@login_required
def checkin_view(request):
    """صفحه چک-این — آدم‌ها از گراف خود کاربر، اول اون‌هایی که نیاز به توجه دارن."""
    user = request.user
    today = timezone.localdate()

    hmap = {}
    try:
        hmap = compute_health(user)
    except Exception:
        pass

    # آدم‌ها: متصل به root اگه root هست، وگرنه همه — مرتب بر اساس نیاز به توجه
    status_rank = {'red': 0, 'yellow': 1, 'unknown': 2, 'green': 3}
    if hmap:
        node_ids = list(hmap.keys())
        nodes = {n.id: n for n in Node.objects.filter(owner=user, id__in=node_ids)}
        ordered = sorted(
            node_ids,
            key=lambda nid: (status_rank.get(hmap[nid]['status'], 2),
                             -(hmap[nid]['score'] is None and 1 or 0))
        )
        people = []
        for nid in ordered:
            n = nodes.get(nid)
            if not n:
                continue
            h = hmap[nid]
            people.append({
                'id':     n.id,
                'label':  n.display_name(),
                'image':  n.picture.url if n.picture else None,
                'status': h['status'],
            })
    else:
        root_id = user.root_node_id
        people = [{
            'id':     n.id,
            'label':  n.display_name(),
            'image':  n.picture.url if n.picture else None,
            'status': 'unknown',
        } for n in Node.objects.filter(owner=user).exclude(id=root_id or -1)[:40]]

    existing = _todays_checkin(user)

    return render(request, 'checkin/checkin.html', {
        'people_json':   json.dumps(people, ensure_ascii=False),
        'people_count':  len(people),
        'streak':        journal_streak(user),
        'already':       existing is not None,
        'jalali_full':   jalali_full_str(today),
        'day_name':      jalali_day_name(today),
    })


# ═══════════════════════════════════════════════════════════════
#  POST /api/checkin/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def checkin_submit_api(request):
    """POST {contacts:[{node_id,kind}], mood, highlight?, followup?{node_id,text}}"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    user = request.user
    today = timezone.localdate()

    # ── نودهای معتبر ──
    contacts = body.get('contacts') or []
    node_ids = [c.get('node_id') for c in contacts if c.get('node_id')]
    valid_nodes = {n.id: n for n in Node.objects.filter(owner=user, id__in=node_ids)}

    # ── تعامل‌ها (dedupe: هر نفر/نوع/روز یه بار) ──
    logged = 0
    try:
        from .models import Interaction
        for c in contacts:
            n = valid_nodes.get(c.get('node_id'))
            if not n:
                continue
            kind = c.get('kind') if c.get('kind') in VALID_KINDS else 'checkin'
            _, was_new = Interaction.objects.get_or_create(
                node=n, owner=user, kind=kind, date=today,
                defaults={'feeling': 0, 'note': 'چک-این روزانه'},
            )
            if was_new:
                logged += 1
    except Exception:
        pass   # جدول migrate نشده — چک-این بدون تعامل هم می‌ارزه

    # ── mood + highlight → JournalEntry (روزی یکی، آپدیت می‌شه) ──
    mood_val = body.get('mood')
    try:
        mood_val = int(mood_val)
        mood_val = max(-2, min(2, mood_val))
    except (TypeError, ValueError):
        mood_val = None
    mood_label = MOOD_LABELS.get(mood_val, '') if mood_val is not None else ''

    highlight = (body.get('highlight') or '').strip()[:500]
    names = [n.display_name() for n in valid_nodes.values()]
    auto_text = 'چک-این روزانه'
    if names:
        auto_text += ' — در تماس با: ' + '، '.join(names[:8])

    entry = _todays_checkin(user)
    if entry:
        if highlight:
            entry.text = (entry.text + '\n' + highlight).strip() if entry.text else highlight
        if mood_label:
            entry.mood = mood_label
        entry.save()
    else:
        entry = JournalEntry.objects.create(
            text=highlight or auto_text,
            entry_date=today,
            tags=['checkin'],
            mood=mood_label,
            ai_analyzed=True,   # تا موتور mood-alert بتونه ازش استفاده کنه
            owner=user,
        )
    for n in valid_nodes.values():
        entry.mentioned_nodes.add(n)
    try:
        from .extraction import extract_text
        extract_text(user, entry.text, 'checkin', entry.id)
    except Exception:
        pass

    # ── فالوآپ اختیاری ──
    fu_created = False
    fu = body.get('followup') or {}
    fu_text = (fu.get('text') or '').strip()[:300]
    fu_node = valid_nodes.get(fu.get('node_id')) if fu.get('node_id') else None
    if not fu_node and fu.get('node_id'):
        try:
            fu_node = Node.objects.get(pk=fu.get('node_id'), owner=user)
        except Node.DoesNotExist:
            fu_node = None
    if fu_text and fu_node:
        try:
            from .models import FollowUp
            FollowUp.objects.create(node=fu_node, text=fu_text, owner=user)
            fu_created = True
        except Exception:
            pass

    return JsonResponse({
        'ok': True,
        'interactions_logged': logged,
        'followup_created': fu_created,
        'streak': journal_streak(user),
    })
