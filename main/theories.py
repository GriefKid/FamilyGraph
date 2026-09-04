"""
theories.py — نظریه‌های تکمیلی روانشناسی/جامعه‌شناسی (V5)

برخلاف تحلیل‌های ساختاری psychology_view (که از گراف محاسبه می‌شن)،
این‌ها از داده‌های رفتاری V4 تغذیه می‌شن: تعامل‌ها، حس بعد از تعامل،
موضوعات باز، ذکر در ژورنال، شهر و نوع رابطه.

خروجی: لیست کارت — {icon, name, theory, value, value_color, label, note, tip}
همه‌چیز fail-safe: نبود جدول/داده → کارت با پیام «داده کافی نیست».
"""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from .models import Interaction, Node, Relationship, JournalEntry


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

    # ═══ 10. رابطه‌های دوطرفه — Reciprocity / directed network ═══
    def _relationship_rows():
        return list(Relationship.objects.filter(owner=user).values_list(
            'source_id', 'target_id', 'strength', 'status', 'rel'))

    rel_rows = _safe(_relationship_rows, [])
    directed = {
        (source_id, target_id)
        for source_id, target_id, _strength, status, _rel in rel_rows
        if source_id != target_id and status != 'inactive'
    }
    pairs = {tuple(sorted(pair)) for pair in directed}
    reciprocal_pairs = sum(
        1 for source_id, target_id in pairs
        if (source_id, target_id) in directed and (target_id, source_id) in directed
    )
    if pairs:
        reciprocal_pct = round(reciprocal_pairs / len(pairs) * 100)
        value = f'{reciprocal_pct}% ({reciprocal_pairs} از {len(pairs)} جفت)'
        color = '#34d399' if reciprocal_pct >= 60 else ('#fbbf24' if reciprocal_pct >= 30 else '#f87171')
        note = 'فقط دوطرفه‌بودن یال‌های ثبت‌شده را نشان می‌دهد؛ به‌معنای برابر بودن احساس یا تلاش نیست'
    else:
        value = '—'; color = '#64748b'
        note = 'برای دیدن این کارت، چند رابطه را از هر دو جهت در گراف ثبت کن'
    cards.append({
        'icon': '↔️', 'name': 'دوطرفه‌بودن رابطه‌ها',
        'theory': 'Reciprocity in Social Networks — Gouldner (1960)',
        'value': value, 'value_color': color, 'label': 'ثبت دوطرفهٔ یال‌ها', 'note': note,
        'tip': 'در شبکه، رابطهٔ دوطرفه می‌تواند نشانه‌ای از شناخت متقابل باشد. این کارت ساختار ثبت‌شده را می‌سنجد، نه کیفیت، رضایت یا تعادل عاطفی رابطه.',
    })

    # ═══ 11. رابطه‌های چندلایه — Multiplexity ═══
    def _group_map():
        nodes = Node.objects.filter(owner=user).prefetch_related('groups')
        return {node.id: {group.id for group in node.groups.all()} for node in nodes}

    group_map = _safe(_group_map, {})
    interaction_nodes = {node_id for node_id, _date, _feeling, _kind in inters if node_id}
    def _followup_rows():
        from .models import FollowUp
        return list(FollowUp.objects.filter(owner=user).values_list('node_id', 'done'))
    followup_rows = _safe(_followup_rows, [])
    followup_nodes = {node_id for node_id, _done in followup_rows if node_id}
    root_layers = []
    if root_id:
        neighbors = set()
        for source_id, target_id, _strength, status, _rel in rel_rows:
            if status != 'inactive' and root_id in (source_id, target_id):
                neighbors.add(target_id if source_id == root_id else source_id)
        for node_id in neighbors:
            layers = {'رابطه'}
            if group_map.get(root_id, set()) & group_map.get(node_id, set()):
                layers.add('گروه مشترک')
            if node_id in interaction_nodes:
                layers.add('تعامل')
            if node_id in followup_nodes:
                layers.add('موضوع باز')
            if len(layers) >= 2:
                root_layers.append((node_names.get(node_id, '؟'), len(layers)))
    root_layers.sort(key=lambda item: (-item[1], item[0]))
    if root_layers:
        value = '، '.join(f'{name} ({count} لایه)' for name, count in root_layers[:3])
        note = 'رابطه‌هایی که در بیش از یک زمینهٔ ثبت‌شدهٔ تو حضور دارند؛ چندلایه بودن به‌معنای سالم یا امن بودن نیست'
        color = '#a5b4fc'
    else:
        value = '—'; color = '#64748b'
        note = 'گروه‌بندی، تعامل یا موضوع باز را برای یک رابطه ثبت کن تا لایه‌های مشترک دیده شود'
    cards.append({
        'icon': '🧩', 'name': 'رابطه‌های چندلایه',
        'theory': 'Multiplexity — چندنقشی‌بودن پیوندهای اجتماعی',
        'value': value, 'value_color': color, 'label': 'چند زمینهٔ مشترک با «من»', 'note': note,
        'tip': 'یک رابطه ممکن است هم‌زمان خانوادگی، دوستانه، کاری و بخشی از یک گروه باشد. این کارت فقط هم‌پوشانی داده‌های ثبت‌شده را نشان می‌دهد و دربارهٔ مرزهای رابطه قضاوت نمی‌کند.',
    })

    # ═══ 12. تمرکز شبکه — Freeman centralization ═══
    adjacency = defaultdict(set)
    for source_id, target_id, _strength, status, _rel in rel_rows:
        if status != 'inactive' and source_id != target_id:
            adjacency[source_id].add(target_id)
            adjacency[target_id].add(source_id)
    degrees = sorted(((node_id, len(neighbors)) for node_id, neighbors in adjacency.items()),
                     key=lambda item: (-item[1], item[0]))
    degree_sum = sum(degree for _node_id, degree in degrees)
    if len(degrees) >= 2 and degree_sum:
        top_degree = sum(degree for _node_id, degree in degrees[:3])
        concentration = round(top_degree / degree_sum * 100)
        top_names = '، '.join(node_names.get(node_id, '؟') for node_id, _degree in degrees[:3])
        value = f'{concentration}% · {top_names}'
        color = '#f87171' if concentration >= 75 else ('#fbbf24' if concentration >= 50 else '#34d399')
        note = 'سهم سه گرهٔ پراتصال از همهٔ اتصال‌های ثبت‌شده؛ تمرکز بالا یعنی شبکه مسیرهای جایگزین کمتری دارد'
    else:
        value = '—'; color = '#64748b'
        note = 'برای سنجش تمرکز، حداقل دو نفر با اتصال ثبت‌شده لازم است'
    cards.append({
        'icon': '🎯', 'name': 'تمرکز شبکه',
        'theory': 'Network Centralization — Freeman (1979)',
        'value': value, 'value_color': color, 'label': 'وابستگی اتصال‌ها به چند نفر', 'note': note,
        'tip': 'تمرکز زیاد الزاماً بد نیست؛ گاهی یک نفر عمداً هماهنگ‌کنندهٔ شبکه است. این کارت فقط به تو یادآوری می‌کند که آیا مسیرهای مستقل دیگری هم ثبت شده‌اند یا نه.',
    })

    # ═══ 13. هستهٔ شبکه — k-core ═══
    core_numbers = {node_id: 0 for node_id in adjacency}
    remaining = set(adjacency)
    k = 1
    while remaining:
        removed = {node_id for node_id in remaining
                   if sum(neighbor in remaining for neighbor in adjacency[node_id]) < k}
        if removed:
            for node_id in removed:
                core_numbers[node_id] = k - 1
                remaining.remove(node_id)
        else:
            k += 1
    if core_numbers:
        max_core = max(core_numbers.values())
        core_names = [node_names.get(node_id, '؟') for node_id, score in core_numbers.items()
                      if score == max_core]
        value = f'k={max_core}: ' + '، '.join(core_names[:5])
        color = '#34d399' if max_core >= 2 else '#a5b4fc'
        note = 'افراد این هسته چند اتصال به افراد متصل دیگر دارند؛ هسته‌ای بودن به‌معنای مهم‌تر یا بهتر بودن فرد نیست'
    else:
        value = '—'; color = '#64748b'
        note = 'با ساختن چند اتصال متقاطع، هستهٔ شبکه قابل مشاهده می‌شود'
    cards.append({
        'icon': '🫀', 'name': 'هستهٔ شبکه',
        'theory': 'k-Core Decomposition — Seidman (1983)',
        'value': value, 'value_color': color, 'label': 'مرکزِ چنداتصالهٔ شبکه', 'note': note,
        'tip': 'k-core به‌جای شمردن محبوبیت، دنبال بخشی از شبکه می‌گردد که هر عضو آن چند اتصال درون شبکه‌ای دارد. این یک ویژگی ساختاری است، نه رتبه‌بندی آدم‌ها.',
    })

    # ═══ 14. روند گرم و سرد شدن — temporal tie change ═══
    recent_cutoff = today - timedelta(days=30)
    previous_cutoff = today - timedelta(days=60)
    recent_counts = defaultdict(int)
    previous_counts = defaultdict(int)
    for node_id, interaction_date, _feeling, _kind in inters:
        if interaction_date >= recent_cutoff:
            recent_counts[node_id] += 1
        elif interaction_date >= previous_cutoff:
            previous_counts[node_id] += 1
    trajectory = []
    for node_id in set(recent_counts) | set(previous_counts):
        delta = recent_counts[node_id] - previous_counts[node_id]
        total_interactions = recent_counts[node_id] + previous_counts[node_id]
        if total_interactions >= 2 and delta:
            trajectory.append((delta, node_names.get(node_id, '؟'), recent_counts[node_id], previous_counts[node_id]))
    warming = sorted((row for row in trajectory if row[0] > 0), reverse=True)
    cooling = sorted((row for row in trajectory if row[0] < 0))
    trajectory_parts = []
    if warming:
        trajectory_parts.append('گرم‌تر: ' + '، '.join(row[1] for row in warming[:2]))
    if cooling:
        trajectory_parts.append('سردتر: ' + '، '.join(row[1] for row in cooling[:2]))
    if trajectory_parts:
        value = ' | '.join(trajectory_parts); color = '#fbbf24'
        note = 'مقایسهٔ تعداد تعامل‌های ثبت‌شده در ۳۰ روز اخیر با ۳۰ روز قبل؛ تغییر تعداد تماس علت رابطه را توضیح نمی‌دهد'
    else:
        value = '—'; color = '#64748b'
        note = 'برای دیدن روند، با چند نفر در دو بازهٔ زمانی تعامل ثبت کن'
    cards.append({
        'icon': '🌡️', 'name': 'روند گرم و سرد شدن',
        'theory': 'Temporal Tie Dynamics — پویایی زمانی پیوندها',
        'value': value, 'value_color': color, 'label': 'تغییر ریتم تعامل', 'note': note,
        'tip': 'رابطه‌ها در طول زمان تغییر می‌کنند. این کارت تغییر فراوانی ثبت تماس را به‌عنوان دعوتی برای مرور نشان می‌دهد، نه اینکه علت فاصله یا صمیمیت را حدس بزند.',
    })

    # ═══ 15. تنوع حمایت اجتماعی — Cohen & Wills (1985) ═══
    def _support_rows():
        return list(Interaction.objects.filter(owner=user).values_list('support_kind', flat=True))
    support_rows = _safe(_support_rows, [])
    support_labels = {
        'heard': 'شنیده‌شدن', 'info': 'اطلاعات', 'practical': 'کمک عملی', 'presence': 'حضور',
    }
    support_kinds = {kind for kind in support_rows if kind}
    if support_kinds:
        names = '، '.join(support_labels.get(kind, kind) for kind in sorted(support_kinds))
        value = f'{len(support_kinds)} نوع: {names}'
        color = '#34d399' if len(support_kinds) >= 3 else '#fbbf24'
        note = f'{len(support_rows)} تعامل با نوع حمایت ثبت شده؛ نوع حمایت با کیفیت یا کافی بودن آن یکی نیست'
    else:
        value = '—'; color = '#64748b'
        note = 'در ثبت تعامل، نوع حمایت را هم مشخص کن تا این کارت فعال شود'
    cards.append({
        'icon': '🫂', 'name': 'تنوع حمایت اجتماعی',
        'theory': 'Stress-Buffering / Social Support — Cohen & Wills (1985)',
        'value': value, 'value_color': color, 'label': 'انواع حمایت تجربه‌شده', 'note': note,
        'tip': 'حمایت می‌تواند عاطفی، اطلاعاتی، عملی یا صرفاً حضور باشد. این کارت تنوع چیزی را که خودت بعد از تعامل ثبت کرده‌ای نشان می‌دهد و جای پرسیدن نیاز واقعی را نمی‌گیرد.',
    })

    return cards
