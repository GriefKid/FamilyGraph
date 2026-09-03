"""
health.py — موتور «سلامت رابطه» (V4)

برای هر آدمِ متصل به root، از سه منبع آخرین تعامل رو پیدا می‌کنه:
  1. Interaction  (ثبت سریع تعامل — دقیق‌ترین)
  2. Event        (رویدادهای گذشته که شخص شرکت داشته)
  3. JournalEntry (ذکر شدن در یادداشت روزانه)

بعد بر اساس «انتظار تماس» (دایره نزدیکی یا fallback از قدرت رابطه):
  ratio = روزهای گذشته از آخرین تعامل / فاصله‌ی مورد انتظار
  ratio ≤ 1        → سبز   (100..70)
  1 < ratio ≤ 3    → زرد   (70..30)
  ratio > 3        → قرمز  (30..5)
  هیچ تعاملی ثبت نشده → unknown (خاکستری)

همه‌چیز fail-safe است: اگه جدول Interaction هنوز migrate نشده،
فقط از Event و Journal استفاده می‌شه.
"""
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from .models import (
    Node, Relationship, Event, JournalEntry,
    CLOSENESS_EXPECTED_DAYS,
)

# fallback وقتی closeness تعیین نشده — از قدرت رابطه (۱..۵) حدس می‌زنیم
STRENGTH_EXPECTED_DAYS = {5: 14, 4: 30, 3: 60, 2: 120, 1: 180}
DEFAULT_EXPECTED_DAYS = 60

HEALTH_COLORS = {
    'green':   '#34d399',
    'yellow':  '#fbbf24',
    'red':     '#f87171',
    'unknown': None,   # رنگ پیش‌فرض گراف
}

STATUS_LABELS = {
    'green':   '🟢 سالم',
    'yellow':  '🟡 نیاز به توجه',
    'red':     '🔴 سرد شده',
    'unknown': '⚪ بدون داده',
}


def _safe(fn, default):
    """اجرای امن query — جدول/ستونِ migrate‌نشده کل صفحه رو نکُشه."""
    try:
        return fn()
    except (OperationalError, ProgrammingError):
        return default
    except Exception:
        return default


def root_connected_ids(user):
    """id همه‌ی نودهایی که با root یال مستقیم دارن."""
    root = getattr(user, 'root_node', None)
    if not root:
        return set(), None
    ids = set(
        Relationship.objects.filter(owner=user, source=root).values_list('target_id', flat=True)
    ) | set(
        Relationship.objects.filter(owner=user, target=root).values_list('source_id', flat=True)
    )
    ids.discard(root.id)
    return ids, root


def last_contact_map(user):
    """node_id → (آخرین تاریخ تعامل, منبع). از سه منبع، جدیدترین برنده‌ست."""
    today = timezone.localdate()
    result = {}

    def bump(nid, d, src):
        if nid is None or d is None:
            return
        if d > today:            # تعامل آینده (مثلاً event فردا) حساب نمی‌شه
            return
        cur = result.get(nid)
        if cur is None or d > cur[0]:
            result[nid] = (d, src)

    # 1) Interaction — ممکنه جدولش هنوز نباشه
    def _from_interactions():
        from .models import Interaction
        rows = Interaction.objects.filter(owner=user).values_list('node_id', 'date')
        return list(rows)
    for nid, d in _safe(_from_interactions, []):
        bump(nid, d, 'interaction')

    # 2) رویدادهای گذشته
    def _from_events():
        rows = Event.objects.filter(owner=user, date__lte=today) \
                            .values_list('participants__id', 'date')
        return list(rows)
    for nid, d in _safe(_from_events, []):
        bump(nid, d, 'event')

    # 3) ذکر در یادداشت‌ها — entry_date اگه بود، وگرنه created_at
    def _from_journal():
        rows = JournalEntry.objects.filter(owner=user) \
                                   .values_list('mentioned_nodes__id', 'entry_date', 'created_at')
        return list(rows)
    if getattr(user, 'ai_journal_enabled', True):
        for nid, ed, ca in _safe(_from_journal, []):
            d = ed or (ca.date() if ca else None)
            bump(nid, d, 'journal')

    return result


def closeness_map(user):
    """node_id → tier. جدول جداست تا نبودنش چیزی رو نشکنه."""
    def _q():
        from .models import NodeCloseness
        return dict(NodeCloseness.objects.filter(owner=user)
                    .values_list('node_id', 'tier'))
    return _safe(_q, {})


def expected_days_for(tier, rel_strength=None):
    """فاصله‌ی مورد انتظار بین دو تعامل (روز) — None یعنی بدون انتظار."""
    if tier:
        return CLOSENESS_EXPECTED_DAYS.get(tier, DEFAULT_EXPECTED_DAYS)
    if rel_strength:
        return STRENGTH_EXPECTED_DAYS.get(rel_strength, DEFAULT_EXPECTED_DAYS)
    return DEFAULT_EXPECTED_DAYS


LEARNED_CADENCE_MIN_INTERACTIONS = 4   # ≥3 gaps
LEARNED_CADENCE_BOUNDS = (7, 365)


def learned_cadence_map(user):
    """node_id → فاصلهٔ معمولِ واقعیِ تعامل با هر نفر (median فاصله‌ها).

    فقط برای کسانی که تعامل کافی ثبت شده. این باعث می‌شود «سلامت رابطه»
    الگوی خودِ شما را یاد بگیرد؛ رفیقی که ماهی یک‌بار می‌بینید نباید بعد از
    ۲۰ روز «سرد شده» علامت بخورد.
    """
    def _q():
        from .models import Interaction
        rows = list(
            Interaction.objects.filter(owner=user)
            .exclude(node__isnull=True)
            .order_by('node_id', 'date')
            .values_list('node_id', 'date')
        )
        by_node = {}
        for nid, d in rows:
            if d is not None:
                by_node.setdefault(nid, []).append(d)
        low, high = LEARNED_CADENCE_BOUNDS
        out = {}
        for nid, dates in by_node.items():
            if len(dates) < LEARNED_CADENCE_MIN_INTERACTIONS:
                continue
            gaps = sorted(
                (dates[i + 1] - dates[i]).days
                for i in range(len(dates) - 1)
                if (dates[i + 1] - dates[i]).days > 0
            )
            if len(gaps) < 3:
                continue
            mid = len(gaps) // 2
            median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
            out[nid] = int(max(low, min(high, round(median))))
        return out
    return _safe(_q, {})


def _score(days_since, expected):
    """امتیاز 0..100 + وضعیت."""
    ratio = days_since / expected if expected else 0
    if ratio <= 1:
        score = 100 - 30 * ratio            # 100..70
        status = 'green'
    elif ratio <= 3:
        score = 70 - 20 * (ratio - 1)       # 70..30
        status = 'yellow'
    else:
        score = max(5, 30 - 5 * (ratio - 3))  # 30..5
        status = 'red'
    return round(score), status


def compute_health(user):
    """
    خروجی: dict[node_id] = {
        score, status, color, label,
        days_since, expected, last_date, last_source, closeness
    }
    فقط برای نودهای متصل به root. کاملاً fail-safe.
    """
    try:
        connected, root = root_connected_ids(user)
    except Exception:
        return {}
    if not connected:
        return {}

    today = timezone.localdate()
    contacts = last_contact_map(user)

    # قوی‌ترین یال root↔node برای fallback قدرت
    strength_map = {}
    try:
        for rel in Relationship.objects.filter(owner=user).only(
                'source_id', 'target_id', 'strength'):
            other = None
            if rel.source_id == root.id:
                other = rel.target_id
            elif rel.target_id == root.id:
                other = rel.source_id
            if other is not None:
                strength_map[other] = max(strength_map.get(other, 0), rel.strength or 3)
    except Exception:
        pass

    def _nodes():
        return list(Node.objects.filter(owner=user, id__in=connected)
                    .only('id', 'username', 'first_name', 'last_name', 'nickname', 'name'))
    try:
        nodes = _nodes()
    except (OperationalError, ProgrammingError):
        return {}

    tiers = closeness_map(user)
    learned = learned_cadence_map(user)

    result = {}
    for n in nodes:
        closeness = tiers.get(n.id, '')
        # An explicit closeness tier is the user's own choice and wins. Otherwise
        # prefer the cadence learned from real interactions, then rel-strength.
        if closeness:
            expected = expected_days_for(closeness)
            expected_source = 'closeness'
        elif n.id in learned:
            expected = learned[n.id]
            expected_source = 'learned'
        else:
            expected = expected_days_for(None, strength_map.get(n.id))
            expected_source = 'strength' if strength_map.get(n.id) else 'default'
        entry = {
            'node_id':    n.id,
            'name':       n.display_name(),
            'closeness':  closeness,
            'expected':   expected,
            'expected_source': expected_source,
            'last_date':  None,
            'last_source': None,
            'days_since': None,
            'score':      None,
            'status':     'unknown',
            'color':      HEALTH_COLORS['unknown'],
            'label':      STATUS_LABELS['unknown'],
        }
        contact = contacts.get(n.id)
        if expected is None:
            # tier «دور» — انتظاری نیست، همیشه خنثی
            if contact:
                entry['last_date'] = contact[0]
                entry['last_source'] = contact[1]
                entry['days_since'] = (today - contact[0]).days
            result[n.id] = entry
            continue
        if contact:
            d, src = contact
            days_since = (today - d).days
            score, status = _score(days_since, expected)
            entry.update({
                'last_date':   d,
                'last_source': src,
                'days_since':  days_since,
                'score':       score,
                'status':      status,
                'color':       HEALTH_COLORS[status],
                'label':       STATUS_LABELS[status],
            })
        result[n.id] = entry
    return result


def health_summary(health_map):
    """شمارش وضعیت‌ها — برای HUD گراف و صفحه هشدارها."""
    counts = {'green': 0, 'yellow': 0, 'red': 0, 'unknown': 0}
    for h in health_map.values():
        counts[h['status']] = counts.get(h['status'], 0) + 1
    return counts


_STATUS_WEIGHT = {'red': 45, 'yellow': 22, 'unknown': 8, 'green': 0}
_NEG_MOOD_WORDS = (
    'ناراحت', 'غمگین', 'استرس', 'اضطراب', 'عصبانی', 'نگران', 'تنها', 'افسرده',
    'sad', 'stress', 'anxious', 'worried', 'upset', 'depressed',
)


def attention_priority(user, health_map=None):
    """node_id → {'score': float, 'factors': [str]}

    ترکیب سلامت رابطه با نشانه‌های فوریت (پیگیری عقب‌افتاده، حال بد اخیر،
    رویداد نزدیک، بدهی باز) تا رتبه‌بندی «نیازمند توجه» واقعی‌تر شود و برای
    هر نفر یک «چرا» به دست بیاید.
    """
    from datetime import timedelta

    if health_map is None:
        health_map = compute_health(user)
    today = timezone.localdate()
    out = {}
    for nid, h in health_map.items():
        factors = []
        score = float(_STATUS_WEIGHT.get(h.get('status'), 0))
        days = h.get('days_since')
        expected = h.get('expected')
        if days and expected:
            over = days - expected
            if over > 0:
                score += min(25, over / max(1, expected) * 15)
        if h.get('status') == 'red':
            factors.append('مدت‌هاست تعاملی نبوده')
        elif h.get('status') == 'yellow':
            factors.append('دارد از رابطه فاصله می‌افتد')
        out[nid] = {'score': score, 'factors': factors}

    def _bump(nid, amount, reason):
        row = out.setdefault(nid, {'score': 0.0, 'factors': []})
        row['score'] += amount
        if reason not in row['factors']:
            row['factors'].append(reason)

    # پیگیری‌های عقب‌افتاده
    try:
        from .models import FollowUp
        for nid in FollowUp.objects.filter(
            owner=user, node__owner=user, done=False, due_date__lt=today,
        ).values_list('node_id', flat=True):
            _bump(nid, 30, 'پیگیری عقب‌افتاده دارد')
    except Exception:
        pass

    # حال بدِ اخیر در یادداشت‌ها (۱۴ روز)
    try:
        cutoff = today - timedelta(days=14)
        rows = JournalEntry.objects.filter(
            owner=user, entry_date__gte=cutoff,
        ).exclude(mood='').values_list('mentioned_nodes__id', 'mood')
        for nid, mood in rows:
            if nid and mood and any(w in mood.lower() for w in _NEG_MOOD_WORDS):
                _bump(nid, 20, 'حال‌وهوایش اخیراً خوب نبوده')
    except Exception:
        pass

    # رویداد نزدیک (۳ روز آینده) — آمادگی لازم است
    try:
        soon = today + timedelta(days=3)
        for nid in Event.objects.filter(
            owner=user, date__gte=today, date__lte=soon,
        ).values_list('participants__id', flat=True):
            if nid:
                _bump(nid, 15, 'رویداد نزدیک با او داری')
    except Exception:
        pass

    # بدهی باز
    try:
        from .models import Debt
        for nid in Debt.objects.filter(
            owner=user, node__owner=user, settled=False,
        ).values_list('node_id', flat=True):
            _bump(nid, 8, 'حساب مالی باز دارید')
    except Exception:
        pass

    return out
