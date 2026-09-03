"""
theories.py — نظریه‌های تکمیلی روانشناسی/جامعه‌شناسی (V5)

برخلاف تحلیل‌های ساختاری psychology_view (که از گراف محاسبه می‌شن)،
این‌ها از داده‌های رفتاری V4 تغذیه می‌شن: تعامل‌ها، حس بعد از تعامل،
موضوعات باز، ذکر در ژورنال، شهر و نوع رابطه.

خروجی: لیست کارت — {icon, name, theory, value, value_color, label, note, tip}
همه‌چیز fail-safe: نبود جدول/داده → کارت با پیام «داده کافی نیست».
"""
from datetime import timedelta

from django.utils import timezone

from .models import Node, Relationship, JournalEntry


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def extra_theories(user):
    today = timezone.localdate()
    cards = []

    # داده‌های پایه
    def _interactions():
        from .models import Interaction
        return list(Interaction.objects.filter(owner=user)
                    .values_list('node_id', 'date', 'feeling', 'kind'))
    inters = _safe(_interactions, [])

    node_names = _safe(
        lambda: {n.id: n.display_name() for n in Node.objects.filter(owner=user)}, {})

    # ═══ 1. اثر مواجهه صرف — Zajonc (1968) ═══
    cutoff30 = today - timedelta(days=30)
    freq = {}
    for nid, d, _f, _k in inters:
        if d >= cutoff30:
            freq[nid] = freq.get(nid, 0) + 1
    top_freq = sorted(freq.items(), key=lambda x: -x[1])[:3]
    if top_freq:
        value = '، '.join(f"{node_names.get(nid, '؟')} ({c})" for nid, c in top_freq)
        note = 'بیشترین تعامل ثبت‌شده در ۳۰ روز اخیر؛ تکرار تماس به‌تنهایی کیفیت رابطه را ثابت نمی‌کند'
    else:
        value = '—'
        note = 'هنوز تعاملی ثبت نشده تا الگوی مواجهه دیده بشه'
    cards.append({
        'icon': '🔁', 'name': 'اثر مواجهه صرف',
        'theory': 'Mere Exposure Effect — Zajonc (1968)',
        'value': value, 'value_color': '#a5b4fc', 'label': 'پرتکرارترین‌ها', 'note': note,
        'tip': 'پژوهش اثر مواجهه نشان می‌دهد آشناییِ تکرارشونده در بعضی موقعیت‌ها می‌تواند حس آشنایی را بیشتر کند. '
               'اما کیفیت، رضایت و مرزهای رابطه از تعداد تماس مهم‌ترند؛ این کارت فقط فراوانی ثبت‌شده را نشان می‌دهد.',
    })

    # ═══ 2. انرژی احساسی — Collins (2004) ═══
    feels = {}
    for nid, _d, f, _k in inters:
        if f:
            feels.setdefault(nid, []).append(f)
    energizers, drainers = [], []
    for nid, fl in feels.items():
        if len(fl) >= 2:
            avg = sum(fl) / len(fl)
            if avg >= 0.4:
                energizers.append((node_names.get(nid, '؟'), avg))
            elif avg <= -0.4:
                drainers.append((node_names.get(nid, '؟'), avg))
    energizers.sort(key=lambda x: -x[1]); drainers.sort(key=lambda x: x[1])
    if energizers or drainers:
        parts = []
        if energizers:
            parts.append('⚡ حس مثبت پس از تعامل: ' + '، '.join(n for n, _ in energizers[:3]))
        if drainers:
            parts.append('🪫 حس ناخوشایند پس از تعامل: ' + '، '.join(n for n, _ in drainers[:2]))
        value = ' | '.join(parts)
        note = 'بر اساس حسی که بعد از تعامل‌ها ثبت کردی (حداقل ۲ ثبت برای هر نفر)'
        color = '#34d399' if energizers else '#f87171'
    else:
        value = '—'
        note = 'موقع ثبت تعامل، «حس بعدش» (😊😐😕) رو هم بزن تا این تحلیل فعال بشه'
        color = '#64748b'
    cards.append({
        'icon': '⚡', 'name': 'انرژی احساسی تعامل‌ها',
        'theory': 'Interaction Ritual Chains — Collins (2004)',
        'value': value, 'value_color': color, 'label': 'الگوی حس بعد از تعامل', 'note': note,
        'tip': 'حسی که بعد از تعامل ثبت می‌کنی می‌تواند در طول زمان یک الگوی شخصی نشان دهد. '
               'این داده درباره تجربهٔ تو از موقعیت است و نباید به برچسب ثابت برای شخصیت طرف مقابل تبدیل شود.',
    })

    # ═══ 3. نفوذ اجتماعی — Altman & Taylor (1973) ═══
    def _mentions():
        rows = JournalEntry.objects.filter(owner=user).values_list('mentioned_nodes__id', flat=True)
        m = {}
        for nid in rows:
            if nid is not None:
                m[nid] = m.get(nid, 0) + 1
        return m
    mentions = _safe(_mentions, {})
    root_id = getattr(user, 'root_node_id', None)
    mentions.pop(root_id, None)
    top_m = sorted(mentions.items(), key=lambda x: -x[1])[:3]
    if top_m:
        value = '، '.join(f"{node_names.get(nid, '؟')} ({c})" for nid, c in top_m)
        note = 'کسانی که بیشتر در یادداشت‌ها ذکر شده‌اند؛ تعداد ذکر به‌تنهایی عمق رابطه را نشان نمی‌دهد'
    else:
        value = '—'
        note = 'با نوشتن ژورنال یا چک-این، عمق روابط قابل اندازه‌گیری می‌شود'
    cards.append({
        'icon': '🧅', 'name': 'عمق خودافشایی',
        'theory': 'Social Penetration Theory — Altman & Taylor (1973)',
        'value': value, 'value_color': '#c4b5fd', 'label': 'پرحضورترین‌ها در ذهن تو', 'note': note,
        'tip': 'نظریه نفوذ اجتماعی به تدریجی و متقابل‌بودن خودافشایی توجه می‌کند. '
               'این کارت فقط تعداد ذکر نام‌ها را می‌شمارد و محتوای خصوصی یا عمق خودافشایی را درجه‌بندی نمی‌کند.',
    })

    # ═══ 4. هنجار عمل متقابل — Gouldner (1960) ═══
    def _fu_stats():
        from .models import FollowUp
        done = FollowUp.objects.filter(owner=user, done=True).count()
        total = FollowUp.objects.filter(owner=user).count()
        return done, total
    done, total = _safe(_fu_stats, (0, 0))
    if total:
        pct = round(done / total * 100)
        value = f'{pct}%'
        color = '#34d399' if pct >= 70 else ('#fbbf24' if pct >= 40 else '#f87171')
        note = f'{done} از {total} موضوع باز (قول/قرار) انجام شده'
    else:
        value = '—'; color = '#64748b'
        note = 'قول‌ها و قرارهات رو در «موضوعات باز» ثبت کن تا نرخ وفای به عهدت سنجیده بشه'
    cards.append({
        'icon': '🤝', 'name': 'وفای به عهد',
        'theory': 'Norm of Reciprocity — Gouldner (1960)',
        'value': value, 'value_color': color, 'label': 'نرخ انجام قول‌ها', 'note': note,
        'tip': 'این کارت فقط نسبت موضوعات بازِ انجام‌شده را نشان می‌دهد. '
               'اعتماد یا عمل متقابل را نمی‌توان از این عدد به‌تنهایی سنجید، اما می‌تواند برای مرور قول‌های ثبت‌شده مفید باشد.',
    })

    # ═══ 5. پیوندهای خفته — Levin et al. (2011) ═══
    def _dormant():
        from .health import compute_health
        hmap = compute_health(user)
        out = []
        for nid, h in hmap.items():
            if h['status'] in ('red', 'unknown'):
                # فقط اون‌هایی که یال قوی داشتن
                strong_rel = Relationship.objects.filter(
                    owner=user, strength__gte=4).filter(
                    source_id__in=[root_id, nid], target_id__in=[root_id, nid]).exists()
                if strong_rel:
                    out.append(h['name'])
        return out
    dormant = _safe(_dormant, []) if root_id else []
    if dormant:
        value = '، '.join(dormant[:3]) + (f' +{len(dormant)-3}' if len(dormant) > 3 else '')
        color = '#fbbf24'
        note = f'{len(dormant)} رابطه‌ی قویِ خفته — احیاشون سریع‌ترین برد شبکه‌ست'
    else:
        value = 'هیچ 🎉'; color = '#34d399'
        note = 'رابطه‌ی قویِ رهاشده‌ای نداری'
    cards.append({
        'icon': '🌱', 'name': 'پیوندهای خفته',
        'theory': 'Dormant Ties — Levin, Walter & Murnighan (2011)',
        'value': value, 'value_color': color, 'label': 'روابط قوی ولی سردشده', 'note': note,
        'tip': 'یک رابطهٔ قدیمی ممکن است هنوز زمینهٔ مشترک داشته باشد، اما امنیت و تمایل دو طرف مهم است. '
               'اگر مناسب می‌دانی، یک پیام کوتاه و بدون گلایه یا انتظار پاسخ می‌تواند شروع کم‌فشاری باشد.',
    })

    # ═══ 6. نیاز به تعلق — Baumeister & Leary (1995) ═══
    week1 = sum(1 for _n, d, _f, _k in inters if d >= today - timedelta(days=7))
    week2 = sum(1 for _n, d, _f, _k in inters
                if today - timedelta(days=14) <= d < today - timedelta(days=7))
    if week1 or week2:
        trend = '📈 رو به رشد' if week1 > week2 else ('📉 رو به افت' if week1 < week2 else '➡️ ثابت')
        value = f'{week1} تعامل این هفته ({trend})'
        color = '#34d399' if week1 >= week2 and week1 > 0 else '#fbbf24'
        note = f'هفته قبل: {week2} تعامل'
    else:
        value = '—'; color = '#64748b'
        note = 'دو هفته تعامل ثبت کن تا روند مشخص بشه'
    cards.append({
        'icon': '🧲', 'name': 'نیاز به تعلق',
        'theory': 'Need to Belong — Baumeister & Leary (1995)',
        'value': value, 'value_color': color, 'label': 'روند تماس هفتگی', 'note': note,
        'tip': 'احساس تعلق برای بسیاری از آدم‌ها مهم است، اما تعداد مطلوب تعامل برای هر فرد فرق دارد. '
               'این روند فقط تماس‌های ثبت‌شده را مقایسه می‌کند و تنهایی یا سلامت روان را تشخیص نمی‌دهد.',
    })

    # ═══ 7. اثر مجاورت — Festinger (1950) ═══
    # شهر روی مدل Node وجود نداره — به جاش بزرگ‌ترین «بافت مشترک» (گروه) رو می‌سنجیم
    def _same_group():
        from .models import Group
        groups = Group.objects.filter(owner=user).prefetch_related('nodes')
        best = None
        for g in groups:
            c = g.nodes.count()
            if c and (best is None or c > best[1]):
                best = (g.name, c)
        return best
    best_group = _safe(_same_group, None)
    if best_group:
        value = f'{best_group[0]} ({best_group[1]} نفر)'
        note = 'بزرگ‌ترین خوشه‌ی هم‌بافت تو — مجاورت، موتور پیش‌فرض دوستی است'
    else:
        value = '—'
        note = 'نودهات رو گروه‌بندی کن (دانشگاه، محله، کار…) تا اثر مجاورت دیده بشه'
    cards.append({
        'icon': '📍', 'name': 'اثر مجاورت',
        'theory': 'Propinquity Effect — Festinger, Schachter & Back (1950)',
        'value': value, 'value_color': '#a5b4fc', 'label': 'بزرگ‌ترین بافت مشترک', 'note': note,
        'tip': 'فستینگر در خوابگاه MIT کشف کرد قوی‌ترین پیش‌بینی‌کننده‌ی دوستی، نه شخصیت است نه علاقه — '
               '«فاصله‌ی فیزیکی» است. کسانی که مسیرشان بیشتر به تو می‌خورد، دوستت می‌شوند. '
               'نتیجه‌ی عملی: اگر دوستی جدید می‌خواهی، خودت را در مسیرهای تکرارشونده قرار بده (کلاس، باشگاه، جمع ثابت).',
    })

    # ═══ 8. پیوند خویشاوندی — Hamilton (1964) ═══
    def _kin():
        kin_words = ['خانواده', 'خواهر', 'برادر', 'پدر', 'مادر', 'همسر', 'فرزند',
                     'عمو', 'دایی', 'خاله', 'عمه', 'پسرخاله', 'دخترخاله', 'فامیل']
        rels = list(Relationship.objects.filter(owner=user).values_list('rel', flat=True))
        if not rels:
            return None
        kin = sum(1 for r in rels if r and any(w in r for w in kin_words))
        return kin, len(rels)
    kin_stat = _safe(_kin, None)
    if kin_stat:
        kin, tot = kin_stat
        pct = round(kin / tot * 100)
        value = f'{pct}%'
        color = '#fbbf24' if pct > 70 else '#a5b4fc'
        note = f'{kin} از {tot} رابطه خانوادگی است' + (
            ' — شبکه‌ی خیلی خانواده-محور؛ روابط انتخابی (دوستی/کاری) جای رشد دارند' if pct > 70 else '')
    else:
        value = '—'; color = '#64748b'
        note = 'نوع روابط (خانواده/دوست/همکار) رو مشخص کن'
    cards.append({
        'icon': '🧬', 'name': 'وزن خویشاوندی',
        'theory': 'Kin Selection — Hamilton (1964)',
        'value': value, 'value_color': color, 'label': 'سهم خانواده از شبکه', 'note': note,
        'tip': 'این کارت فقط سهم برچسب‌های خانوادگی را در داده‌های گراف نشان می‌دهد. '
               'از این نسبت نمی‌توان حمایت واقعی، رضایت یا کیفیت زندگی را نتیجه گرفت.',
    })

    # ═══ 9. سلامت رابطه‌ها — از تحلیل‌های شواهدمحور ذخیره‌شده ═══
    def _fscores():
        from .models import Information
        from .relationship_intelligence import is_grounded_profile
        out = []
        for nid, d in Information.objects.filter(node__owner=user).values_list('node_id', 'data'):
            if is_grounded_profile(d) and d.get('friendship_score') is not None:
                out.append((node_names.get(nid, '؟'), int(d['friendship_score'])))
        return out
    fs = _safe(_fscores, [])
    if fs:
        fs.sort(key=lambda x: -x[1])
        top = '، '.join(f'{n} ({s})' for n, s in fs[:3])
        low = f" | کم‌ترین: {fs[-1][0]} ({fs[-1][1]})" if len(fs) > 3 else ''
        value = top + low
        color = '#34d399' if fs[0][1] >= 70 else '#fbbf24'
        note = f'{len(fs)} رابطه نمره‌گذاری شده — از «تحلیل رابطه» در پروفایل هر شخص'
    else:
        value = '—'; color = '#64748b'
        note = 'هنوز رابطه‌ای تحلیل نشده — توی پروفایل هر شخص «💠 تحلیل رابطه» رو بزن'
    cards.append({
        'icon': '💠', 'name': 'سلامت رابطه‌ها',
        'theory': 'Evidence-based Relationship Health — FamilyGraph',
        'value': value, 'value_color': color, 'label': 'کیفیت‌سنجی روابط', 'note': note,
        'tip': 'این نمره از داده‌های ثبت‌شده مثل تعامل، حس، نبض رابطه و قدرت فعلی ساخته می‌شود و اطمینان جداگانه دارد. '
               'پژوهش‌های کیفیت رابطه (مثل مقیاس Rubin) نشون می‌دن خودآگاهی نسبت به کیفیت روابط، '
               'اولین قدم بهبودشونه — چیزی که اندازه گرفته بشه، مدیریت می‌شه.',
    })

    return cards
