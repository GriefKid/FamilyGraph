"""
views_persona.py — موتور «شناخت» (V11)

هر گوشه‌ی اپ که چیزی از یک آدم یا یک رابطه می‌فهمه، اینجا جمع می‌شه:
آثار فرهنگی و نمره‌هاش، شناخت‌نامه، ژورنال، چت‌ها، تعامل‌ها و حس‌ها،
قرض‌ها، قول‌ها، رویدادهای زندگی، سلامت رابطه، اهداف…

خروجی سنتز: جملات کلی و انسانی («قرمز دوست داره»، «شب‌ها سرحال‌تره»)
بدون ذکر اینکه از کجا فهمیدیم.
"""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Node, Relationship

User = get_user_model()


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════
#  گردآوری سیگنال‌های یک شخص — از همه‌جای اپ
# ═══════════════════════════════════════════════════════════════

def gather_person_signals(user, node):
    S = []

    # ── پایه ──
    base = f'نام: {node.display_name()}'
    if node.career:
        base += f' | شغل: {node.career}'
    if node.birth_day:
        base += f' | تولد: {node.birth_day}'
    S.append(base)

    # ── رابطه با من ──
    root = user.root_node
    if root and root.id != node.id:
        for r in Relationship.objects.filter(
                Q(source=root, target=node) | Q(source=node, target=root), owner=user):
            S.append(f'رابطه با من: {r.rel or "نامشخص"} | قدرت {r.strength}/5 | وضعیت {r.status}'
                     + (f' | آشنایی از {r.met_at}' if r.met_at else ''))

    # ── سلامت + دایره نزدیکی ──
    def _health():
        from .health import compute_health
        h = compute_health(user).get(node.id)
        if h and h.get('status') != 'unknown':
            return (f'وضعیت تماس: {h["label"]} | آخرین تعامل {h["days_since"]} روز پیش '
                    f'| الگوی مورد انتظار: هر {h["expected"]} روز')
    v = _safe(_health, None)
    if v:
        S.append(v)
    def _tier():
        from .models import NodeCloseness
        nc = NodeCloseness.objects.filter(node=node, owner=user).first()
        return f'دایره نزدیکی: {nc.get_tier_display()}' if nc else None
    v = _safe(_tier, None)
    if v:
        S.append(v)

    # ── شناخت‌نامه (Information) ──
    def _insight():
        info = node.informations.first()
        d = info.data if (info and isinstance(info.data, dict)) else {}
        out = []
        for k, label in (('personality', 'شخصیت'), ('communication_style', 'سبک ارتباط'),
                         ('relationship_quality', 'کیفیت رابطه'), ('tip', 'توصیه قبلی'),
                         ('about_me', 'درباره من'), ('relationship_goals', 'هدف‌های رابطه‌ای'),
                         ('boundaries', 'مرزها و حساسیت‌ها'), ('social_energy', 'انرژی اجتماعی')):
            if d.get(k):
                out.append(f'{label}: {str(d[k])[:200]}')
        for k, label in (('values', 'ارزش‌ها'), ('interests', 'علایق'),
                         ('strengths', 'قوت‌ها'), ('red_flags', 'هشدارها')):
            if d.get(k):
                out.append(f'{label}: ' + '، '.join(str(x) for x in list(d[k])[:5]))
        if d.get('friendship_score') is not None:
            out.append(f'نمره دوستی: {d["friendship_score"]}/100')
        return out
    S += _safe(_insight, [])

    # ── تعامل‌ها و حس‌ها ──
    def _inter():
        from .models import Interaction
        rows = list(Interaction.objects.filter(owner=user, node=node)
                    .order_by('-date')[:150])
        if not rows:
            return []
        kinds = {}
        feels = [i.feeling for i in rows if i.feeling]
        for i in rows:
            kinds[i.get_kind_display()] = kinds.get(i.get_kind_display(), 0) + 1
        out = [f'{len(rows)} تعامل ثبت‌شده: ' + '، '.join(f'{k}×{v}' for k, v in kinds.items())]
        if feels:
            avg = sum(feels) / len(feels)
            out.append(f'میانگین حس بعد از تعامل: {avg:+.2f} '
                       f'({"انرژی‌بخش" if avg > 0.3 else ("انرژی‌گیر" if avg < -0.3 else "خنثی")})')
        notes = [i.note for i in rows[:20] if i.note and i.note not in
                 ('ایمپورت تلگرام', 'چک-این روزانه', 'از هشدار ثبت شد')][:5]
        if notes:
            out.append('یادداشت تعامل‌ها: ' + ' | '.join(n[:80] for n in notes))
        return out
    S += _safe(_inter, [])

    # ── ژورنال ──
    def _journal():
        entries = list(node.journal_entries.filter(owner=user).order_by('-created_at')[:6])
        return [f'از خاطرات ({e.entry_date or e.created_at.date()}): {e.text[:180]}'
                + (f' [حس: {e.mood}]' if e.mood else '') for e in entries]
    S += _safe(_journal, [])

    # ── رویدادهای زندگی ──
    def _life():
        from .models import LifeEvent
        evs = list(LifeEvent.objects.filter(node=node, owner=user)[:6])
        return [f'رویداد زندگی: {e.get_kind_display()}'
                + (f' ({e.title})' if e.title else '') + f' — {e.date}' for e in evs]
    S += _safe(_life, [])

    # ── مالی + قول‌ها ──
    def _money():
        from .models import Debt
        rows = list(Debt.objects.filter(owner=user, node=node))
        if not rows:
            return None
        settled = sum(1 for d in rows if d.settled)
        return f'سابقه مالی: {len(rows)} قلم قرض/طلب، {settled} تسویه‌شده'
    v = _safe(_money, None)
    if v:
        S.append(v)
    def _fu():
        from .models import FollowUp
        done = FollowUp.objects.filter(owner=user, node=node, done=True).count()
        openc = FollowUp.objects.filter(owner=user, node=node, done=False).count()
        if done or openc:
            return f'موضوعات باز/قول‌ها: {done} انجام‌شده، {openc} باز'
    v = _safe(_fu, None)
    if v:
        S.append(v)

    # ── هدف فعال ──
    def _goal():
        from .models import RelationshipGoal
        g = RelationshipGoal.objects.filter(node=node, owner=user, status='active').first()
        return f'هدف من روی این رابطه: «{g.text}»' if g else None
    v = _safe(_goal, None)
    if v:
        S.append(v)

    # ── رویدادهای مشترک با من ──
    def _shared_events():
        root_ = user.root_node
        if not root_:
            return []
        evs = list(node.events.filter(owner=user, participants=root_).order_by('-date')[:5])
        return [f'رویداد مشترک: {e.title} ({e.date})'
                + (f' — {e.description[:100]}' if e.description else '') for e in evs]
    S += _safe(_shared_events, [])

    # ── گروه‌ها + جایگاه در شبکه ──
    def _groups():
        g = [x.name for x in node.groups.all()[:6]]
        return f'عضو گروه‌های: {"، ".join(g)}' if g else None
    v = _safe(_groups, None)
    if v:
        S.append(v)
    def _network():
        deg = Relationship.objects.filter(
            Q(source=node) | Q(target=node), owner=user).count()
        out = f'جایگاه در شبکه: {deg} ارتباط مستقیم'
        root_ = user.root_node
        if root_ and root_.id != node.id:
            my_n = set(Relationship.objects.filter(owner=user).filter(
                Q(source=root_) | Q(target=root_)).values_list('source_id', 'target_id'))
            my_ids = {x for p in my_n for x in p} - {root_.id}
            their_n = set(Relationship.objects.filter(owner=user).filter(
                Q(source=node) | Q(target=node)).values_list('source_id', 'target_id'))
            their_ids = {x for p in their_n for x in p} - {node.id}
            mutual = (my_ids & their_ids) - {root_.id, node.id}
            if mutual:
                out += f' | {len(mutual)} آشنای مشترک با من'
        return out
    v = _safe(_network, None)
    if v:
        S.append(v)

    # ── روند قدرت یال من↔او ──
    def _strength_trend():
        root_ = user.root_node
        if not root_:
            return None
        rel = Relationship.objects.filter(
            Q(source=root_, target=node) | Q(source=node, target=root_),
            owner=user).first()
        if not rel:
            return None
        rows = list(rel.strength_history.all()[:10])
        if len(rows) >= 2:
            return (f'روند قدرت رابطه: از {rows[-1].strength} به {rows[0].strength} '
                    f'در {len(rows)} تغییر')
    v = _safe(_strength_trend, None)
    if v:
        S.append(v)

    # ── متن قول‌ها و موضوعات باز (محتوا خودش شناخته) ──
    def _fu_texts():
        from .models import FollowUp
        rows = list(FollowUp.objects.filter(owner=user, node=node).order_by('-created_at')[:6])
        return [f'{"[انجام‌شده]" if f.done else "[باز]"} قول/موضوع: {f.text[:120]}' for f in rows]
    S += _safe(_fu_texts, [])

    # ── جزئیات مالی باز ──
    def _money_detail():
        from .models import Debt
        rows = list(Debt.objects.filter(owner=user, node=node, settled=False)[:4])
        return [f'حساب باز: {"من بهش" if d.direction == "i_owe" else "اون به من"} '
                f'{d.remaining:,} {d.currency} بدهکار'
                + (f' (بابت {d.note[:60]})' if d.note else '') for d in rows]
    S += _safe(_money_detail, [])

    # ── درد دل‌های من با «همدم» که اسمش توش اومده ──
    def _confessions():
        from .models import ChatMessage
        names = {node.display_name(), node.username, node.nickname}
        names = [n for n in names if n and len(n) >= 2]
        out = []
        if not names:
            return out
        q = Q()
        for n in names:
            q |= Q(content__icontains=n)
        rows = list(ChatMessage.objects.filter(owner=user, role='user').filter(q)
                    .order_by('-created_at')[:5])
        for m in rows:
            out.append(f'از درد دل‌های من: {m.content[:180]}')
        return out
    S += _safe(_confessions, [])

    # ── خاطراتی که با اسمش نوشتم ولی لینک نشدن ──
    def _journal_by_name():
        from .models import JournalEntry
        nm = node.display_name()
        if not nm or len(nm) < 2:
            return []
        linked_ids = set(node.journal_entries.values_list('id', flat=True))
        rows = list(JournalEntry.objects.filter(owner=user, text__icontains=nm)
                    .exclude(id__in=linked_ids).order_by('-created_at')[:4])
        return [f'از خاطرات ({e.entry_date or e.created_at.date()}): {e.text[:160]}' for e in rows]
    S += _safe(_journal_by_name, [])

    # ── رفتار من با هشدارهاش (اهمیتی که بهش می‌دم) ──
    def _alert_behavior():
        from .models import AlertAction
        done = AlertAction.objects.filter(owner=user, node=node, action='completed').count()
        dism = AlertAction.objects.filter(owner=user, node=node, action='dismissed').count()
        if done or dism:
            return f'رسیدگی من به یادآورهای مربوط بهش: {done} انجام، {dism} رد'
    v = _safe(_alert_behavior, None)
    if v:
        S.append(v)

    # ── شناخت قبلی (انباشت — چیزی که قبلاً فهمیدیم گم نشه) ──
    def _prior():
        from .models import PersonaProfile
        p = PersonaProfile.objects.filter(node=node, owner=user).first()
        if p and p.statements:
            return ['شناخت قبلی (اگه هنوز درسته نگهش دار): '
                    + ' | '.join(str(s.get('text', s))[:100] for s in p.statements[:10])]
        return []
    S += _safe(_prior, [])

    # ── اگر این نود، یک کاربر واقعی اپه: آثار فرهنگی + تحلیل چت داخلی ──
    def _as_user():
        out = []
        u = None
        if node.imported_from_id:
            imported_user = node.imported_from
            if imported_user == user or imported_user.is_public:
                u = imported_user
        elif node.username == user.username:
            u = user
        if not u:
            return out
        from .models import ProfileMediaItem, ChatAnalysis, SocialCircleMessage, SocialPost
        media_qs = ProfileMediaItem.objects.filter(user=u)
        if u != user:
            media_qs = media_qs.filter(is_public=True)
        items = list(media_qs[:40])
        if items:
            kind_fa = {'book': 'کتاب', 'movie': 'فیلم', 'series': 'سریال', 'music': 'موسیقی'}
            best = sorted([i for i in items if i.rating], key=lambda i: -i.rating)[:6]
            latest = items[:6]
            seen = set()
            for i in best + latest:
                if i.id in seen:
                    continue
                seen.add(i.id)
                line = f'{kind_fa.get(i.kind, i.kind)} «{i.title}»'
                if i.rating:
                    line += f' — نمره {i.rating:g}/5'
                if i.notes:
                    line += f' — نظرش: {i.notes[:150]}'
                out.append('اثر فرهنگی: ' + line)
        ca = ChatAnalysis.objects.filter(user=user, friend=u).first()
        if ca:
            if ca.summary:
                out.append(f'از گفتگوهای داخلی: {ca.summary[:250]}')
            if ca.mood:
                out.append(f'حال‌وهوای گفتگوها: {ca.mood}')
            for s in (ca.signals or [])[:4]:
                out.append(f'سیگنال گفتگو: {str(s)[:150]}')
        # نمونه‌ی خودِ پیام‌های اخیر — لحن و دنیای واقعیش
        from .models import DirectMessage
        msgs = list(DirectMessage.objects.filter(
            Q(sender=user, receiver=u) | Q(sender=u, receiver=user)
        ).order_by('-created_at')[:14])[::-1]
        if msgs:
            sample = ' / '.join(
                f'{"من" if m.sender_id == user.id else "او"}: {m.content[:70]}'
                for m in msgs)
            out.append(f'نمونه گفتگوی اخیر: {sample[:600]}')
        # بیو و مشخصات پروفایلش
        if u.bio:
            out.append(f'بیوی خودش: {u.bio[:200]}')
        if u.city:
            out.append(f'شهر: {u.city}')
        posts = list(SocialPost.objects.filter(author=u, is_public=True)
                     .order_by('-created_at')[:4])
        for post in posts:
            out.append(f'پست عمومی: {post.body[:180]}')
        shared_circles = user.social_circles.filter(members=u)[:3]
        for circle in shared_circles:
            messages = list(
                SocialCircleMessage.objects.filter(circle=circle)
                .select_related('author').order_by('-created_at')[:4]
            )[::-1]
            if messages:
                sample = ' / '.join(
                    f'{message.author.username}: {message.body[:80]}'
                    for message in messages
                )
                out.append(f'از حلقه مشترک «{circle.name}»: {sample[:400]}')
        return out
    S += _safe(_as_user, [])

    return S


# ═══════════════════════════════════════════════════════════════
#  گردآوری سیگنال‌های یک رابطه (یال)
# ═══════════════════════════════════════════════════════════════

def gather_rel_signals(user, rel):
    S = []
    a, b = rel.source, rel.target
    S.append(f'رابطه بین «{a.display_name()}» و «{b.display_name()}»: '
             f'{rel.rel or "بدون برچسب"} | قدرت {rel.strength}/5 | وضعیت {rel.status}'
             + (f' | آشنایی از {rel.met_at}' if rel.met_at else ''))

    # تاریخچه قدرت
    def _hist():
        rows = list(rel.strength_history.all()[:12])
        if len(rows) >= 2:
            newest, oldest = rows[0], rows[-1]
            return (f'روند قدرت: از {oldest.strength} به {newest.strength} '
                    f'({len(rows)} تغییر ثبت‌شده)')
    v = _safe(_hist, None)
    if v:
        S.append(v)

    # رویدادهای مشترک
    def _events():
        evs = list(a.events.filter(owner=user, participants=b).order_by('-date')[:5])
        return [f'رویداد مشترک: {e.title} ({e.date})' for e in evs]
    S += _safe(_events, [])

    # اگه یال به «من» وصله → کل سیگنال‌های شخصِ مقابل هم مربوطه
    root = user.root_node
    other = None
    if root:
        if rel.source_id == root.id:
            other = b
        elif rel.target_id == root.id:
            other = a
    # گروه‌های مشترک دو سر یال
    def _shared_groups():
        ga = set(a.groups.values_list('name', flat=True))
        gb = set(b.groups.values_list('name', flat=True))
        m = ga & gb
        return f'بافت مشترک (گروه‌ها): {"، ".join(sorted(m))}' if m else None
    v = _safe(_shared_groups, None)
    if v:
        S.append(v)

    if other is not None:
        S.append('— این رابطه‌ی خودِ منه؛ سیگنال‌های شخص مقابل: —')
        S += gather_person_signals(user, other)[:40]
    else:
        # رابطه‌ی بین دو نفر دیگه — شناخت هر دو طرف اگه هست
        def _p(n):
            from .models import PersonaProfile
            p = PersonaProfile.objects.filter(node=n, owner=user).first()
            if p and p.summary:
                return f'شناخت کلی از {n.display_name()}: {p.summary[:250]}'
        for n in (a, b):
            v = _safe(lambda n=n: _p(n), None)
            if v:
                S.append(v)
        # آشناهای مشترک
        def _mutual():
            a_n = set(Relationship.objects.filter(owner=user).filter(
                Q(source=a) | Q(target=a)).values_list('source_id', 'target_id'))
            ids_a = {x for pair in a_n for x in pair} - {a.id}
            b_n = set(Relationship.objects.filter(owner=user).filter(
                Q(source=b) | Q(target=b)).values_list('source_id', 'target_id'))
            ids_b = {x for pair in b_n for x in pair} - {b.id}
            m = ids_a & ids_b
            return f'{len(m)} آشنای مشترک دارند' if m else None
        v = _safe(_mutual, None)
        if v:
            S.append(v)

    return S


# ═══════════════════════════════════════════════════════════════
#  سنتز AI
# ═══════════════════════════════════════════════════════════════

def _synthesize(kind_label, signals):
    """signals → jملات شناخت کلی. خروجی: (statements, summary) یا raise."""
    from .views_smart_features import _ai_client, _model, _extract_json
    client, api_key, _prov = _ai_client()
    if not api_key:
        raise RuntimeError('کلید AI تنظیم نشده')

    data = '\n'.join(f'- {s}' for s in signals[:90])
    prompt = f"""تو حافظه‌ی جمعی اپ FamilyGraph هستی. این‌ها همه‌ی چیزهاییه که اپ درباره‌ی {kind_label} می‌دونه:

{data}

حالا «شناخت کلی» بده — مثل حرف زدن یه دوست قدیمی، نه گزارش داده:
- جملات ساده و کلی («قرمز دوست داره»، «شب‌ها سرحال‌تره»، «به قولش عمل می‌کنه»)
- هرگز نگو از کجا فهمیدی؛ منبع و عدد و آمار نیار مگه ضروری باشه
- استنتاج سطح‌بالا مجازه اما جایی که مطمئن نیستی، با «احتمالاً/به نظر می‌رسه» بگو
- حداکثر ۱۸ جمله، مرتب از مطمئن‌ترین به حدسی‌ترین
- اگه «شناخت قبلی» توی داده‌ها هست، جمله‌های هنوز-درستش رو نگه دار و با یافته‌های جدید ترکیب کن
- ⚠️ قانون تناقض: اگه دو نشانه با هم نمی‌خونن (مثلاً «از کارش ناراضیه» و «استعفا داده»)،
  نشانه‌ی جدیدتر (تاریخ جلوتر) واقعیتِ الانه — جمله‌ی قدیمیِ باطل‌شده رو کلاً حذف کن،
  یا اگه ارزش داره به شکل روایت بگو («از کارش ناراضی بود و آخرش استعفا داد»)
- خروجی نباید هیچ دو جمله‌ی متناقضی داشته باشه — قبل از جواب، خودت چک کن

JSON خالص:
{{"statements": [{{"text": "...", "kind": "شخصیت/سلیقه/عادت/ارزش/رابطه"}}],
  "summary": "جمع‌بندی ۲-۳ جمله‌ای"}}"""

    resp = client.chat.completions.create(
        model=_model(),
        messages=[
            {'role': 'system', 'content': 'حافظه‌ی جمعی و شناخت‌ساز. فقط JSON خالص.'},
            {'role': 'user', 'content': prompt},
        ],
        max_tokens=1100,
    )
    result = _extract_json(resp.choices[0].message.content)
    stmts = result.get('statements') or []
    clean = []
    for s in stmts[:18]:
        if isinstance(s, dict) and s.get('text'):
            clean.append({'text': str(s['text'])[:300], 'kind': str(s.get('kind', ''))[:30]})
        elif isinstance(s, str) and s.strip():
            clean.append({'text': s.strip()[:300], 'kind': ''})
    return clean, str(result.get('summary', ''))[:600]


def _statement_text(s):
    if isinstance(s, dict):
        return str(s.get('text', '')).strip()
    return str(s or '').strip()


def _payload(p):
    prev_texts = {_statement_text(s) for s in (getattr(p, 'previous_statements', None) or [])}
    statements = p.statements or []
    fresh = [_statement_text(s) for s in statements if _statement_text(s) not in prev_texts]
    return {
        'statements': statements,
        'summary': p.summary or '',
        'new_statements': fresh if prev_texts else [],
        'had_previous': bool(prev_texts),
        'previous_synth_at': (
            p.previous_synth_at.strftime('%Y-%m-%d %H:%M')
            if getattr(p, 'previous_synth_at', None) else None
        ),
        'updated_at': p.updated_at.strftime('%Y-%m-%d %H:%M') if p.updated_at else None,
    }


# ═══════════════════════════════════════════════════════════════
#  API — شخص
# ═══════════════════════════════════════════════════════════════

@login_required
def persona_get_api(request, pk):
    node = get_object_or_404(Node, pk=pk, owner=request.user)
    try:
        from .models import PersonaProfile
        p = PersonaProfile.objects.filter(node=node, owner=request.user).first()
        return JsonResponse({'ok': True, 'persona': _payload(p) if p else None})
    except Exception:
        return JsonResponse({'ok': True, 'persona': None})


@login_required
def persona_synthesize_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    node = get_object_or_404(Node, pk=pk, owner=request.user)

    signals = gather_person_signals(request.user, node)
    if len(signals) < 2:
        return JsonResponse({'error': 'هنوز داده‌ی کافی درباره‌ش ثبت نشده — '
                                      'تعامل، ژورنال یا تحلیل اضافه کن'}, status=400)
    try:
        statements, summary = _synthesize(f'شخصِ «{node.display_name()}»', signals)
    except Exception as e:
        from .views_smart_features import _rate_limit_msg
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)

    try:
        from .models import PersonaProfile
        existing = PersonaProfile.objects.filter(node=node, owner=request.user).first()
        prev_statements = list(existing.statements or []) if existing else []
        prev_at = existing.updated_at if existing else None
        p, _ = PersonaProfile.objects.update_or_create(
            node=node, defaults={'statements': statements, 'summary': summary,
                                 'owner': request.user,
                                 'previous_statements': prev_statements,
                                 'previous_synth_at': prev_at})
        return JsonResponse({'ok': True, 'persona': _payload(p)})
    except Exception:
        return JsonResponse({'ok': True, 'persona': {
            'statements': statements, 'summary': summary, 'updated_at': None},
            'warning': 'جدول شناخت هنوز migrate نشده — ذخیره نشد'})


# ═══════════════════════════════════════════════════════════════
#  API — رابطه
# ═══════════════════════════════════════════════════════════════

@login_required
def rel_persona_get_api(request, pk):
    rel = get_object_or_404(
        Relationship,
        pk=pk,
        owner=request.user,
        source__owner=request.user,
        target__owner=request.user,
    )
    try:
        from .models import RelationshipProfile
        p = RelationshipProfile.objects.filter(relationship=rel, owner=request.user).first()
        return JsonResponse({'ok': True, 'persona': _payload(p) if p else None})
    except Exception:
        return JsonResponse({'ok': True, 'persona': None})


@login_required
def rel_persona_synthesize_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    rel = get_object_or_404(
        Relationship,
        pk=pk,
        owner=request.user,
        source__owner=request.user,
        target__owner=request.user,
    )

    signals = gather_rel_signals(request.user, rel)
    if len(signals) < 2:
        return JsonResponse({'error': 'داده‌ی کافی درباره این رابطه نیست'}, status=400)
    try:
        statements, summary = _synthesize(
            f'رابطه‌ی «{rel.source.display_name()} و {rel.target.display_name()}»', signals)
    except Exception as e:
        from .views_smart_features import _rate_limit_msg
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)

    try:
        from .models import RelationshipProfile
        existing = RelationshipProfile.objects.filter(relationship=rel, owner=request.user).first()
        prev_statements = list(existing.statements or []) if existing else []
        prev_at = existing.updated_at if existing else None
        p, _ = RelationshipProfile.objects.update_or_create(
            relationship=rel, defaults={'statements': statements, 'summary': summary,
                                        'owner': request.user,
                                        'previous_statements': prev_statements,
                                        'previous_synth_at': prev_at})
        return JsonResponse({'ok': True, 'persona': _payload(p)})
    except Exception:
        return JsonResponse({'ok': True, 'persona': {
            'statements': statements, 'summary': summary, 'updated_at': None},
            'warning': 'جدول شناخت هنوز migrate نشده — ذخیره نشد'})
