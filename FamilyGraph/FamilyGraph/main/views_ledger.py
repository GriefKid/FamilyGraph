"""
views_ledger.py — دفتر قرض و طلب (V6)

- ثبت قرض/طلب با سررسید و پرداخت جزئی
- دفتر کل: تراز خالص با هر نفر
- «از کی قرض بگیرم؟» — رتبه‌بندی بر اساس قدرت رابطه، سلامت رابطه،
  سابقه‌ی قرضِ تسویه‌شده و بدهی فعلی
"""
import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Node, Relationship
from .utils_jalali import jalali_str

MIGRATION_MSG = ('جدول قرض و طلب هنوز ساخته نشده — '
                 'فایل migrate_and_run.bat رو یه بار اجرا کن.')


def _body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return None


def _parse_amount(v):
    """رقم فارسی/جداکننده‌دار → int مثبت یا None."""
    if v is None:
        return None
    s = str(v)
    fa = '۰۱۲۳۴۵۶۷۸۹'
    for i, ch in enumerate(fa):
        s = s.replace(ch, str(i))
    s = ''.join(c for c in s if c.isdigit())
    if not s:
        return None
    try:
        n = int(s)
        return n if 0 < n < 10**15 else None
    except ValueError:
        return None


def serialize_debt(d, today=None):
    today = today or timezone.localdate()
    days_left = (d.due_date - today).days if d.due_date else None
    return {
        'id':        d.id,
        'node_id':   d.node_id,
        'direction': d.direction,
        'amount':    d.amount,
        'paid':      d.paid,
        'remaining': d.remaining,
        'amount_fmt':    f'{d.amount:,}',
        'paid_fmt':      f'{d.paid or 0:,}',
        'remaining_fmt': f'{d.remaining:,}',
        'currency':  d.currency,
        'date_fa':   jalali_str(d.date),
        'due_date':  str(d.due_date) if d.due_date else None,
        'due_fa':    jalali_str(d.due_date) if d.due_date else None,
        'days_left': days_left,
        'overdue_days': abs(days_left) if (days_left is not None and days_left < 0) else 0,
        'overdue':   bool(d.due_date and days_left is not None and days_left < 0 and not d.settled),
        'note':      d.note,
        'settled':   d.settled,
    }


def node_balance(user, node_id):
    """تراز خالص با یک نفر: مثبت = اون به من بدهکاره."""
    try:
        from .models import Debt
        net = 0
        for d in Debt.objects.filter(owner=user, node_id=node_id, settled=False):
            net += d.remaining if d.direction == 'they_owe' else -d.remaining
        return net
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════
#  POST /api/debts/create/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def debt_create_api(request):
    """POST {node_id, direction, amount, currency?, date?, due_date?, note?}"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    try:
        node = Node.objects.get(pk=body.get('node_id'), owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'شخص پیدا نشد'}, status=404)

    direction = body.get('direction')
    if direction not in ('i_owe', 'they_owe'):
        return JsonResponse({'error': 'جهت نامعتبر'}, status=400)

    amount = _parse_amount(body.get('amount'))
    if not amount:
        return JsonResponse({'error': 'مبلغ نامعتبر'}, status=400)

    currency = body.get('currency') if body.get('currency') in ('تومان', 'دلار', 'یورو') else 'تومان'

    def _d(key):
        s = (body.get(key) or '').strip()
        if not s:
            return None
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except ValueError:
            return 'ERR'
    date_val = _d('date') or timezone.localdate()
    due_val = _d('due_date')
    if date_val == 'ERR' or due_val == 'ERR':
        return JsonResponse({'error': 'فرمت تاریخ: YYYY-MM-DD'}, status=400)

    note = (body.get('note') or '').strip()[:300]

    try:
        from .models import Debt
        debt = Debt.objects.create(
            node=node, direction=direction, amount=amount, currency=currency,
            date=date_val, due_date=due_val, note=note, owner=request.user,
        )
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)

    return JsonResponse({'ok': True, 'debt': serialize_debt(debt),
                         'balance': node_balance(request.user, node.id)})


# ═══════════════════════════════════════════════════════════════
#  POST /api/debts/<pk>/pay/   {amount?}  — بدون amount = تسویه کامل
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def debt_pay_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request) or {}
    try:
        from .models import Debt
        debt = Debt.objects.get(pk=pk, owner=request.user)
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)
    except Exception:
        return JsonResponse({'error': 'پیدا نشد'}, status=404)

    pay = _parse_amount(body.get('amount'))
    if pay is None:
        pay = debt.remaining          # تسویه کامل
    pay = min(pay, debt.remaining)

    debt.paid = (debt.paid or 0) + pay
    if debt.paid >= debt.amount:
        debt.settled = True
        debt.settled_at = timezone.now()
    debt.save()
    return JsonResponse({'ok': True, 'debt': serialize_debt(debt),
                         'balance': node_balance(request.user, debt.node_id)})


# ═══════════════════════════════════════════════════════════════
#  POST /api/debts/<pk>/delete/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def debt_delete_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        from .models import Debt
        Debt.objects.filter(pk=pk, owner=request.user).delete()
        return JsonResponse({'ok': True})
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)


# ═══════════════════════════════════════════════════════════════
#  GET /api/borrow/suggest/?amount=
# ═══════════════════════════════════════════════════════════════

@login_required
def borrow_suggest_api(request):
    """«از کی قرض بگیرم؟» — رتبه‌بندی هوشمند بدون AI (سریع و آفلاین)."""
    user = request.user
    root_id = user.root_node_id
    if not root_id:
        return JsonResponse({'error': 'اول نود اصلی (من) رو تنظیم کن'}, status=400)

    # سلامت رابطه‌ها
    health = {}
    try:
        from .health import compute_health
        health = compute_health(user)
    except Exception:
        pass

    # قوی‌ترین یال با هر نفر
    strength = {}
    for r in Relationship.objects.filter(owner=user).only('source_id', 'target_id', 'strength'):
        other = None
        if r.source_id == root_id:
            other = r.target_id
        elif r.target_id == root_id:
            other = r.source_id
        if other:
            strength[other] = max(strength.get(other, 0), r.strength or 3)

    # سابقه قرض
    lent_before, i_owe_now = {}, {}
    try:
        from .models import Debt
        for d in Debt.objects.filter(owner=user):
            if d.direction == 'i_owe':
                if d.settled:
                    lent_before[d.node_id] = lent_before.get(d.node_id, 0) + 1   # قبلاً بهم قرض داده و صاف کردم
                else:
                    i_owe_now[d.node_id] = i_owe_now.get(d.node_id, 0) + d.remaining
    except Exception:
        pass

    names = {n.id: n.display_name() for n in Node.objects.filter(owner=user)}

    # V9: نمره دوستی از شناخت‌نامه
    fscores = {}
    try:
        from .models import Information
        for nid_, d_ in Information.objects.filter(node__owner=user).values_list('node_id', 'data'):
            if isinstance(d_, dict) and d_.get('friendship_score') is not None:
                fscores[nid_] = int(d_['friendship_score'])
    except Exception:
        pass

    candidates = []
    for nid, s in strength.items():
        if nid == root_id:
            continue
        score = s * 2
        reasons = [f'قدرت رابطه {s}/5']
        h = health.get(nid, {})
        if h.get('status') == 'green':
            score += 3
            reasons.append('رابطه گرم و فعاله')
        elif h.get('status') == 'red':
            score -= 2
            reasons.append('رابطه سرد شده — اول احیاش کن')
        fs = fscores.get(nid)
        if fs is not None:
            if fs >= 70:
                score += 3
                reasons.append(f'💠 نمره دوستی {fs}/100')
            elif fs < 40:
                score -= 3
                reasons.append(f'💠 نمره دوستی پایین ({fs})')
            else:
                reasons.append(f'💠 نمره دوستی {fs}')
        if lent_before.get(nid):
            score += 3
            reasons.append(f'{lent_before[nid]} بار قبلاً قرض داده و خوش‌حساب بودی')
        if i_owe_now.get(nid):
            score -= 5
            reasons.append(f'الان {i_owe_now[nid]:,} بهش بدهکاری')
        candidates.append({'node_id': nid, 'name': names.get(nid, '؟'),
                           'score': score, 'reasons': reasons})

    candidates.sort(key=lambda c: -c['score'])
    return JsonResponse({'ok': True, 'suggestions': candidates[:5]})


# ═══════════════════════════════════════════════════════════════
#  GET /ledger/
# ═══════════════════════════════════════════════════════════════

@login_required
def ledger_view(request):
    user = request.user
    today = timezone.localdate()

    debts, table_missing = [], False
    try:
        from .models import Debt
        debts = list(Debt.objects.filter(owner=user).select_related('node'))
    except (OperationalError, ProgrammingError):
        table_missing = True

    total_i_owe = total_they_owe = 0
    per_person = {}
    open_items, settled_items = [], []
    for d in debts:
        sd = serialize_debt(d, today)
        sd['node_name'] = d.node.display_name()
        if d.settled:
            settled_items.append(sd)
            continue
        open_items.append(sd)
        rem = d.remaining
        if d.direction == 'i_owe':
            total_i_owe += rem
            delta = -rem
        else:
            total_they_owe += rem
            delta = rem
        p = per_person.setdefault(d.node_id, {'node_id': d.node_id,
                                              'name': d.node.display_name(),
                                              'net': 0, 'count': 0, 'next_due': None})
        p['net'] += delta
        p['count'] += 1
        if d.due_date and (p['next_due'] is None or d.due_date < p['next_due']):
            p['next_due'] = d.due_date

    persons = sorted(per_person.values(), key=lambda p: p['net'])
    # V9: نمره دوستی کنار حساب هر نفر
    fscores = {}
    try:
        from .models import Information
        for nid_, d_ in Information.objects.filter(node__owner=user).values_list('node_id', 'data'):
            if isinstance(d_, dict) and d_.get('friendship_score') is not None:
                fscores[nid_] = int(d_['friendship_score'])
    except Exception:
        pass
    for p in persons:
        p['net_fmt'] = f'{abs(p["net"]):,}'
        p['next_due_fa'] = jalali_str(p['next_due']) if p['next_due'] else None
        p['next_due_overdue'] = bool(p['next_due'] and p['next_due'] < today)
        p['fscore'] = fscores.get(p['node_id'])

    # سررسید نزدیک (۷ روز)
    open_items.sort(key=lambda x: (x['due_date'] is None, x['due_date'] or '9999'))
    settled_items.sort(key=lambda x: -x['id'])

    nodes = Node.objects.filter(owner=user).exclude(id=user.root_node_id or -1) \
                        .order_by('username')

    return render(request, 'ledger/ledger.html', {
        'table_missing':   table_missing,
        'total_i_owe':     f'{total_i_owe:,}',
        'total_they_owe':  f'{total_they_owe:,}',
        'net':             total_they_owe - total_i_owe,
        'net_fmt':         f'{abs(total_they_owe - total_i_owe):,}',
        'persons':         persons,
        'open_items':      open_items,
        'settled_items':   settled_items[:10],
        'open_count':      len(open_items),
        'nodes':           nodes,
        'today_iso':       str(today),
    })
