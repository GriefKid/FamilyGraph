"""
Smart Features: Alerts, Psychology Analysis, Daily Tips
"""
import json
import os
from datetime import date, timedelta
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from openai import OpenAI

from .models import Node, Relationship, Event, Information, JournalEntry, AppSettings, AlertAction
from .utils_jalali import (
    jalali_str, jalali_full_str, jalali_day_name, jalali_month_name,
    is_holiday, upcoming_holidays, season_fa,
)

# ── AI Provider Config ────────────────────────────────────────────────────
# اولویت: Gemini → Mistral → Groq → OpenRouter
#
# Mistral  (رایگان، بدون بلاک ایران): console.mistral.ai → MISTRAL_API_KEY
# Groq     (14,400 req/day رایگان): console.groq.com → GROQ_API_KEY
# OpenRouter (50 req/day رایگان)  : openrouter.ai → OPENROUTER_API_KEY
# Gemini   (1,500 req/day - بلاک در ایران بدون VPN)
# ─────────────────────────────────────────────────────────────────────────

def _ai_client():
    """Return (OpenAI client, api_key, provider).
    Priority: Gemini → Mistral → Groq → OpenRouter
    """
    gemini_key = os.environ.get('GEMINI_API_KEY', '')
    if gemini_key:
        return (
            OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key=gemini_key),
            gemini_key, 'gemini'
        )
    mistral_key = os.environ.get('MISTRAL_API_KEY', '')
    if mistral_key:
        return OpenAI(base_url="https://api.mistral.ai/v1", api_key=mistral_key), mistral_key, 'mistral'
    groq_key = os.environ.get('GROQ_API_KEY', '')
    if groq_key:
        return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key), groq_key, 'groq'
    openrouter_key = os.environ.get('OPENROUTER_API_KEY', '')
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key), openrouter_key, 'openrouter'


def _model():
    """Pick correct model name for active provider."""
    if os.environ.get('GEMINI_API_KEY'):
        return "gemini-1.5-flash"
    if os.environ.get('MISTRAL_API_KEY'):
        return "mistral-small-latest"       # رایگان، بدون بلاک ایران
    if os.environ.get('GROQ_API_KEY'):
        return "llama-3.3-70b-versatile"   # 14,400 req/day free
    return "google/gemma-4-31b-it:free"    # 50 req/day free


def _rate_limit_msg(e: Exception) -> str:
    s = str(e)
    if '429' in s or 'rate limit' in s.lower() or 'Rate limit' in s:
        return ('حد روزانه تموم شده 😔 — فردا دوباره امتحان کن '
                'یا MISTRAL_API_KEY رو در .env تنظیم کن (console.mistral.ai — رایگان)')
    return f'خطای AI: {s[:200]}'


def _extract_json(raw: str) -> dict:
    """Pull JSON from AI output (may be wrapped in ```json)."""
    raw = raw.strip()
    if '```json' in raw:
        raw = raw.split('```json')[1].split('```')[0]
    elif '```' in raw:
        raw = raw.split('```')[1].split('```')[0]
    return json.loads(raw.strip())


# ═══════════════════════════════════════════════════════════════
#  ALERTS
# ═══════════════════════════════════════════════════════════════

def _compute_alerts():
    """Compute all active alerts — no AI, fast."""
    today = date.today()
    alerts = []

    # ── 1. Birthdays today ──────────────────────────────────────
    for node in Node.objects.filter(birth_day__month=today.month, birth_day__day=today.day):
        age = today.year - node.birth_day.year
        alerts.append({
            'id': f'bday_{node.id}',
            'type': 'birthday',
            'priority': 'high',
            'node_id': node.id,
            'node_username': node.username,
            'node_name': node.display_name(),
            'title': f'🎂 تولد {node.display_name()} امروزه!',
            'body': f'امروز {age}مین سالگرد تولد {node.display_name()} است. ({jalali_str(today)})',
            'days_until': 0,
        })

    # ── 2. Birthdays upcoming (1-7 days) ───────────────────────
    for delta in range(1, 8):
        d = today + timedelta(days=delta)
        for node in Node.objects.filter(birth_day__month=d.month, birth_day__day=d.day):
            age = today.year - node.birth_day.year
            # اگه روز تولد با تعطیل رسمی ایرانی مصادف بود، اشاره کن
            hol_flag, hol_nm = is_holiday(d)
            hol_note = f' (مصادف با {hol_nm})' if hol_flag and hol_nm != 'جمعه' else (
                       ' (جمعه)' if hol_flag else '')
            alerts.append({
                'id': f'bday_{node.id}_{delta}',
                'type': 'birthday_upcoming',
                'priority': 'medium',
                'node_id': node.id,
                'node_username': node.username,
                'node_name': node.display_name(),
                'title': f'🎂 تولد {node.display_name()} — {delta} روز دیگر',
                'body': f'{node.display_name()} در {jalali_str(d)}{hol_note} {age} ساله می‌شود.',
                'days_until': delta,
            })

    # ── 3. Upcoming events (next 7 days) ───────────────────────
    for ev in Event.objects.filter(date__gte=today, date__lte=today + timedelta(days=7)).prefetch_related('participants'):
        days = (ev.date - today).days
        parts = [p.display_name() for p in ev.participants.all()[:4]]
        ev_hol, ev_hol_name = is_holiday(ev.date)
        hol_note = f' — {ev_hol_name}' if ev_hol and ev_hol_name != 'جمعه' else ''
        alerts.append({
            'id': f'event_{ev.id}',
            'type': 'event',
            'priority': 'high' if days == 0 else 'medium',
            'event_id': ev.id,
            'node_id': None,
            'node_name': None,
            'title': f'📅 {ev.title}' + (' — امروز!' if days == 0 else f' — {days} روز دیگر'),
            'body': f'{jalali_str(ev.date)}{hol_note}' + (f' | {ev.description}' if ev.description else '') + (f' | شرکت‌کنندگان: {", ".join(parts)}' if parts else ''),
            'days_until': days,
        })

    # ── 4. Mood-based alerts from recent journal (7 days) ──────
    negative_words = ['ناراحت', 'غمگین', 'استرس', 'اضطراب', 'عصبانی', 'نگران',
                      'sad', 'stress', 'anxious', 'worried', 'upset', 'depressed', 'تنها', 'افسرده']
    cutoff7 = today - timedelta(days=7)
    seen_mood_nodes = set()
    for entry in JournalEntry.objects.filter(created_at__date__gte=cutoff7, ai_analyzed=True).prefetch_related('mentioned_nodes')[:30]:
        if entry.mood and any(neg in entry.mood.lower() for neg in negative_words):
            for node in entry.mentioned_nodes.all()[:3]:
                if node.id not in seen_mood_nodes:
                    seen_mood_nodes.add(node.id)
                    alerts.append({
                        'id': f'mood_{node.id}_{entry.id}',
                        'type': 'mood_alert',
                        'priority': 'medium',
                        'node_id': node.id,
                        'node_username': node.username,
                        'node_name': node.display_name(),
                        'title': f'💛 {node.display_name()} ممکن است به حمایت نیاز داشته باشد',
                        'body': f'بر اساس یادداشت اخیر، حال {node.display_name()} چندان خوب نبود ({entry.mood}).',
                        'days_until': None,
                    })

    # ── 5. Dormant connections (no journal mention in 30+ days) ─
    # فقط اگه اپ حداقل ۳۰ روزه استفاده شده نشون بده
    try:
        first_entry = JournalEntry.objects.order_by('entry_date').first()
        app_age_days = (today - first_entry.entry_date).days if (first_entry and first_entry.entry_date) else 0

        if app_age_days >= 30:
            settings = AppSettings.get()
            root = settings.root_node
            if root:
                cutoff30 = today - timedelta(days=30)
                recent_ids = set(
                    JournalEntry.objects.filter(entry_date__gte=cutoff30)
                    .values_list('mentioned_nodes__id', flat=True)
                )
                recent_ids.discard(None)

                connected_ids = set(
                    Relationship.objects.filter(source=root).values_list('target_id', flat=True)
                ) | set(
                    Relationship.objects.filter(target=root).values_list('source_id', flat=True)
                )

                dormant_ids = connected_ids - recent_ids - {root.id}
                for node in Node.objects.filter(id__in=dormant_ids)[:4]:
                    alerts.append({
                        'id': f'dormant_{node.id}',
                        'type': 'dormant',
                        'priority': 'low',
                        'node_id': node.id,
                        'node_username': node.username,
                        'node_name': node.display_name(),
                        'title': f'💤 مدتی از {node.display_name()} بی‌خبری',
                        'body': f'بیش از ۳۰ روز است که در یادداشت‌هایت از {node.display_name()} یادی نشده.',
                        'days_until': None,
                    })
    except Exception:
        pass

    # ── فیلتر کردن هشدارهایی که کاربر قبلاً اقدام کرده ────────────────────
    excluded_ids = set(
        AlertAction.objects.filter(
            action__in=['completed', 'dismissed']
        ).values_list('alert_id', flat=True)
    )
    # dismissed ها بعد از ۷ روز دوباره نشون داده می‌شن
    dismissed_old = set(
        AlertAction.objects.filter(
            action='dismissed',
            created_at__date__lt=today - timedelta(days=7)
        ).values_list('alert_id', flat=True)
    )
    excluded_ids -= dismissed_old

    alerts = [a for a in alerts if a['id'] not in excluded_ids]

    # Sort: high > medium > low, then by days_until
    priority_order = {'high': 0, 'medium': 1, 'low': 2}
    alerts.sort(key=lambda a: (
        priority_order.get(a.get('priority', 'low'), 3),
        a.get('days_until', 999) if a.get('days_until') is not None else 999
    ))
    return alerts


def alerts_api(request):
    """JSON: all current alerts."""
    return JsonResponse({'alerts': _compute_alerts()})


def alerts_count_api(request):
    """JSON: quick badge count."""
    alerts = _compute_alerts()
    high_count = sum(1 for a in alerts if a.get('priority') == 'high')
    return JsonResponse({'total': len(alerts), 'high': high_count})


@csrf_exempt
def alert_recommendation_api(request):
    """POST {node_id, alert_type, title} → AI gift/action suggestions."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    node_id = body.get('node_id')
    alert_type = body.get('alert_type', '')
    alert_title = body.get('title', '')

    # Gather person data
    person_data = {}
    if node_id:
        try:
            node = Node.objects.get(pk=node_id)
            person_data['name'] = node.display_name()
            person_data['career'] = node.career or ''
            info_obj = node.informations.first()
            if info_obj and info_obj.data:
                d = info_obj.data
                person_data['personality'] = d.get('personality', '')
                person_data['interests'] = d.get('interests', [])
                person_data['preferences'] = d.get('preferences', [])
                person_data['values'] = d.get('values', [])
                person_data['relationship_quality'] = d.get('relationship_quality', '')
                person_data['strengths'] = d.get('strengths', [])
                person_data['mood_history'] = d.get('mood', '')
        except Node.DoesNotExist:
            pass

    # ── کش: پیشنهادات برای همین نود+نوع هشدار قبلاً ساخته شده؟ ─────────────
    cache_key = f'alert_rec_{node_id}_{alert_type}_{date.today().strftime("%Y%m%d")}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    prompt = f"""هشدار: {alert_title}
نوع: {alert_type}
اطلاعات شخص: {json.dumps(person_data, ensure_ascii=False)}

۵ پیشنهاد شخصی‌سازی‌شده بده:
- تولد/رویداد: ایده هدیه یا کار بر اساس علایق
- mood_alert: چطور حمایت کنیم
- dormant: چطور رابطه رو احیا کنیم

JSON:
{{
  "suggestions": [
    {{"rank": 1, "action": "...", "reason": "...", "difficulty": "آسان/متوسط/سخت"}}
  ],
  "personal_note": "یه نکته شخصی"
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'مشاور روابط اجتماعی. فقط JSON خروجی بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=900,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=24 * 3600)  # کش ۲۴ ساعته
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


def alerts_view(request):
    """Full /alerts/ page."""
    alerts = _compute_alerts()
    return render(request, 'alerts/alerts.html', {'alerts': alerts})


@csrf_exempt
def alert_action_api(request):
    """POST {alert_id, alert_type, node_id, title, action, outcome} → ذخیره اقدام کاربر."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    node = None
    node_id = body.get('node_id')
    if node_id:
        try:
            node = Node.objects.get(pk=node_id)
        except Node.DoesNotExist:
            pass

    AlertAction.objects.create(
        alert_id=body.get('alert_id', ''),
        alert_type=body.get('alert_type', ''),
        node=node,
        title=body.get('title', ''),
        action=body.get('action', 'dismissed'),
        outcome=body.get('outcome', ''),
    )
    # کش هشدارها رو پاک کن تا دفعه بعد تازه لود بشه
    cache.delete('alerts_list')
    return JsonResponse({'ok': True})


@csrf_exempt
def rename_group_api(request):
    """POST {old_name, new_name} → تغییر نام گروه (Group model)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    from .models import Group as GroupModel
    old_name = (body.get('old_name') or '').strip()
    new_name = (body.get('new_name') or '').strip()

    if not old_name or not new_name:
        return JsonResponse({'error': 'old_name و new_name لازم‌اند'}, status=400)
    if old_name == new_name:
        return JsonResponse({'ok': True})

    try:
        grp = GroupModel.objects.get(name=old_name)
        grp.name = new_name
        grp.save()
        cache.delete('graph_all_data')
        return JsonResponse({'ok': True})
    except GroupModel.DoesNotExist:
        return JsonResponse({'error': f'گروه «{old_name}» پیدا نشد'}, status=404)


@csrf_exempt
def delete_group_api(request):
    """POST {name} → حذف گروه و خروج نودها از اون گروه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    from .models import Group as GroupModel
    name = (body.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'name لازم است'}, status=400)

    deleted, _ = GroupModel.objects.filter(name=name).delete()
    cache.delete('graph_all_data')
    return JsonResponse({'ok': True, 'deleted': deleted})


# ═══════════════════════════════════════════════════════════════
#  PSYCHOLOGY / SOCIOLOGY ANALYSIS
# ═══════════════════════════════════════════════════════════════

def _build_nx():
    import networkx as nx
    G = nx.Graph()
    all_nodes = list(Node.objects.all())
    all_rels = list(Relationship.objects.select_related('source', 'target'))
    for n in all_nodes:
        G.add_node(n.id, label=n.display_name())
    for r in all_rels:
        G.add_edge(r.source_id, r.target_id, weight=r.strength, status=r.status, rel=r.rel or '')
    return G, all_nodes, all_rels


def psychology_view(request):
    """Comprehensive network psychology page."""
    import networkx as nx

    G, all_nodes, all_rels = _build_nx()
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()

    if n_nodes == 0:
        return render(request, 'psychology/psychology.html', {'empty': True})

    # ── Root node ────────────────────────────────────────────────
    root = None
    try:
        root = AppSettings.get().root_node
    except Exception:
        pass

    # ── Dunbar's Number ──────────────────────────────────────────
    dunbar = {'intimate': 0, 'close': 0, 'friends': 0, 'acquaintances': 0, 'weak': 0}
    dunbar_ideal = {'intimate': '1–5', 'close': '6–15', 'friends': '16–50', 'acquaintances': '51–150', 'weak': '150+'}
    dunbar_notes = []
    total_direct = 0
    if root and root.id in G:
        for _, _, d in G.edges(root.id, data=True):
            s = d.get('weight', 3)
            if s == 5:   dunbar['intimate'] += 1
            elif s == 4: dunbar['close'] += 1
            elif s == 3: dunbar['friends'] += 1
            elif s == 2: dunbar['acquaintances'] += 1
            else:        dunbar['weak'] += 1
        total_direct = sum(dunbar.values())
        if dunbar['intimate'] > 5:
            dunbar_notes.append(f"⚠️ لایه صمیمی ({dunbar['intimate']} نفر) از حد شناختی ۵ نفر بیشتر است — کیفیت ممکن است افت کند")
        elif dunbar['intimate'] < 2:
            dunbar_notes.append(f"💡 لایه صمیمی ({dunbar['intimate']} نفر) بسیار کم است — روابط عمیق را تقویت کن")
        if total_direct > 150:
            dunbar_notes.append(f"⚠️ {total_direct} ارتباط مستقیم — بالاتر از عدد داونبار (۱۵۰) — مدیریت سخت می‌شود")
        elif total_direct < 20:
            dunbar_notes.append(f"💡 فقط {total_direct} ارتباط — شبکه کوچک است؛ گسترش شبکه توصیه می‌شود")
    else:
        dunbar_notes.append('نود اصلی (من) را در تنظیمات تعریف کن تا تحلیل داونبار انجام شود')

    # ── Granovetter Weak Tie Theory ──────────────────────────────
    strong = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) >= 4)
    weak = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) <= 2)
    medium = n_edges - strong - weak
    weak_ratio = weak / max(n_edges, 1)
    if 0.35 <= weak_ratio <= 0.65:
        grano_status = 'optimal'
        grano_label = 'بهینه'
        grano_note = f'نسبت پیوندهای ضعیف ({weak_ratio:.0%}) در محدوده مناسب — تنوع اطلاعاتی خوب است'
    elif weak_ratio < 0.35:
        grano_status = 'too_strong'
        grano_label = 'بیش از حد صمیمی'
        grano_note = f'پیوندهای ضعیف کم ({weak_ratio:.0%}) — خطر اتاق پژواک (echo chamber)'
    else:
        grano_status = 'too_weak'
        grano_label = 'روابط کم‌عمق'
        grano_note = f'پیوندهای ضعیف زیاد ({weak_ratio:.0%}) — روابط عمیق کافی نیست'

    # ── Centrality ───────────────────────────────────────────────
    deg_cent = nx.degree_centrality(G)
    btw_cent = nx.betweenness_centrality(G, normalized=True) if n_nodes > 2 else {}
    cls_cent = nx.closeness_centrality(G) if n_nodes > 1 else {}

    # Top connectors by degree
    top_by_deg = sorted(deg_cent.items(), key=lambda x: x[1], reverse=True)[:6]
    top_connectors = []
    for nid, deg in top_by_deg:
        try:
            nd = Node.objects.get(pk=nid)
            top_connectors.append({
                'name': nd.display_name(),
                'degree': round(deg * 100),
                'betweenness': round(btw_cent.get(nid, 0) * 100),
                'closeness': round(cls_cent.get(nid, 0) * 100),
            })
        except Exception:
            pass

    # ── Clustering & Echo Chamber ────────────────────────────────
    avg_clust = nx.average_clustering(G) if n_nodes > 2 else 0
    if avg_clust > 0.65:
        echo_risk = 'high'; echo_label = 'زیاد'
    elif avg_clust > 0.35:
        echo_risk = 'medium'; echo_label = 'متوسط'
    else:
        echo_risk = 'low'; echo_label = 'کم'

    # ── Network Resilience ───────────────────────────────────────
    resilience_score = 0
    node_connectivity = 0
    if nx.is_connected(G) and n_nodes >= 3:
        try:
            node_connectivity = nx.node_connectivity(G)
            resilience_score = min(100, node_connectivity * 25)
        except Exception:
            resilience_score = 20

    # ── Communities ──────────────────────────────────────────────
    n_communities = 0
    modularity_val = 0.0
    bridges = []
    try:
        from networkx.algorithms.community import louvain_communities
        comms = list(louvain_communities(G, seed=42))
        n_communities = len(comms)
        modularity_val = nx.community.modularity(G, comms)
        # Nodes bridging communities
        comm_map = {}
        for i, c in enumerate(comms):
            for nid in c:
                comm_map[nid] = i
        bridge_ids = set()
        for u, v in G.edges():
            if comm_map.get(u) != comm_map.get(v):
                bridge_ids.add(u); bridge_ids.add(v)
        for nid in list(bridge_ids)[:6]:
            try:
                bridges.append(Node.objects.get(pk=nid).display_name())
            except Exception:
                pass
    except Exception:
        pass

    # ── Active vs Dormant ────────────────────────────────────────
    active_rels = Relationship.objects.filter(status='active').count()
    distant_rels = Relationship.objects.filter(status='distant').count()
    inactive_rels = Relationship.objects.filter(status='inactive').count()

    # ── Relationship types distribution ─────────────────────────
    rel_type_counts = {}
    for r in all_rels:
        k = r.rel or '(بدون نام)'
        rel_type_counts[k] = rel_type_counts.get(k, 0) + 1
    rel_types_sorted = sorted(rel_type_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # ── Journal insights ─────────────────────────────────────────
    total_entries = JournalEntry.objects.count()
    analyzed_entries = JournalEntry.objects.filter(ai_analyzed=True).count()
    recent_moods = list(JournalEntry.objects.exclude(mood='').order_by('-created_at').values_list('mood', flat=True)[:20])

    context = {
        'n_nodes': n_nodes,
        'n_edges': n_edges,
        'dunbar': dunbar,
        'dunbar_ideal': dunbar_ideal,
        'dunbar_notes': dunbar_notes,
        'total_direct': total_direct,
        'strong': strong, 'weak': weak, 'medium': medium,
        'weak_ratio_pct': round(weak_ratio * 100),
        'grano_status': grano_status,
        'grano_label': grano_label,
        'grano_note': grano_note,
        'top_connectors': top_connectors,
        'avg_clust': round(avg_clust, 3),
        'avg_clust_pct': round(avg_clust * 100),
        'echo_risk': echo_risk,
        'echo_label': echo_label,
        'resilience_score': resilience_score,
        'node_connectivity': node_connectivity,
        'n_communities': n_communities,
        'modularity_val': round(modularity_val, 3),
        'bridges': bridges,
        'active_rels': active_rels,
        'distant_rels': distant_rels,
        'inactive_rels': inactive_rels,
        'rel_types': rel_types_sorted,
        'total_entries': total_entries,
        'analyzed_entries': analyzed_entries,
        'recent_moods_json': json.dumps(recent_moods, ensure_ascii=False),
    }
    return render(request, 'psychology/psychology.html', context)


@csrf_exempt
def psychology_ai_api(request):
    """POST → comprehensive AI psychology+sociology narrative. Cached 6h."""
    # ── کش: اگه تحلیل امروز قبلاً انجام شده، از کش برگردون ──────────────
    cache_key = f'psych_ai_{date.today().strftime("%Y%m%d")}'
    cached = cache.get(cache_key)
    if cached and not (request.GET.get('refresh') or (request.body and json.loads(request.body or '{}').get('refresh'))):
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    G, all_nodes, all_rels = _build_nx()
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()

    if n_nodes == 0:
        return JsonResponse({'error': 'شبکه خالی است'}, status=400)

    import networkx as nx
    avg_clust = nx.average_clustering(G) if n_nodes > 2 else 0

    # Relationship type summary
    rel_types = {}
    for r in all_rels:
        k = r.rel or 'نامشخص'
        rel_types[k] = rel_types.get(k, 0) + 1

    # Sample of person data
    samples = []
    for info in Information.objects.select_related('node').all()[:8]:
        if info.data:
            samples.append({'person': info.node.display_name(), 'data': info.data})

    recent_moods = list(
        JournalEntry.objects.exclude(mood='').order_by('-created_at').values_list('mood', flat=True)[:15]
    )

    strong = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) >= 4)
    weak = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) <= 2)

    network_summary = {
        'n_nodes': n_nodes, 'n_edges': n_edges,
        'strong_ties': strong, 'weak_ties': weak,
        'avg_clustering': round(avg_clust, 3),
        'relationship_types': dict(sorted(rel_types.items(), key=lambda x: x[1], reverse=True)[:8]),
        'person_data_samples': samples[:4],
        'recent_moods': recent_moods,
    }

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    prompt = f"""تو یه روانشناس و جامعه‌شناس متخصص هستی. شبکه اجتماعی زیر رو با تمام دانش روانشناسی و جامعه‌شناسی‌ات تحلیل کن:

{json.dumps(network_summary, ensure_ascii=False, indent=2)}

تحلیل جامع بنویس که شامل اینا باشه:
۱. ارزیابی کلی سلامت شبکه (داونبار، گرانووتر، بورت)
۲. الگوهای رفتاری (attachment style، social capital، social exchange theory)
۳. ریسک‌ها و آسیب‌پذیری‌ها
۴. فرصت‌های رشد
۵. ۵ توصیه عملی با پشتوانه تئوری
۶. خلاصه جامعه‌شناختی کلی

به فارسی، عمیق و تخصصی.

JSON:
{{
  "health": {{"score": 0-100, "label": "...", "summary": "..."}},
  "patterns": ["..."],
  "risks": ["..."],
  "opportunities": ["..."],
  "recommendations": [{{"action": "...", "theory": "...", "impact": "بالا/متوسط/کم"}}],
  "sociological_summary": "...",
  "psychological_profile": "..."
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'روانشناس و جامعه‌شناس متخصص. فقط JSON خروجی بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=2000,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=6 * 3600)   # کش ۶ ساعته
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


# ═══════════════════════════════════════════════════════════════
#  DAILY TIPS
# ═══════════════════════════════════════════════════════════════

def daily_tips_view(request):
    """Daily briefing page /daily/."""
    today = date.today()
    is_hol, hol_name = is_holiday(today)
    upcoming = upcoming_holidays(30)

    context = {
        'today':         today,
        'jalali_date':   jalali_str(today),
        'jalali_full':   jalali_full_str(today),
        'day_name':      jalali_day_name(today),
        'month_name':    jalali_month_name(today),
        'season':        season_fa(today),
        'is_holiday':    is_hol,
        'holiday_name':  hol_name,
        'upcoming_holidays': upcoming[:5],
        'alerts_count':  len([a for a in _compute_alerts() if a.get('priority') == 'high']),
    }
    return render(request, 'daily/daily.html', context)


@csrf_exempt
def daily_tips_api(request):
    """POST → AI daily network tips — با تقویم شمسی و تعطیلات ایرانی."""
    today       = date.today()
    is_hol, hol_name = is_holiday(today)
    day_name    = jalali_day_name(today)
    jalali_date = jalali_str(today)
    season      = season_fa(today)

    n_nodes = Node.objects.count()
    n_edges = Relationship.objects.count()

    alerts = _compute_alerts()
    urgent = [a['title'] for a in alerts if a.get('priority') == 'high'][:3]

    recent_moods = list(
        JournalEntry.objects.order_by('-created_at').exclude(mood='').values_list('mood', flat=True)[:5]
    )

    weak_rels  = list(Relationship.objects.filter(strength__lte=2).select_related('target')[:5])
    weak_names = [r.target.display_name() for r in weak_rels]

    cutoff14 = today - timedelta(days=14)
    mentioned_ids = set(
        JournalEntry.objects.filter(entry_date__gte=cutoff14).values_list('mentioned_nodes__id', flat=True)
    )
    mentioned_ids.discard(None)
    try:
        root = AppSettings.get().root_node
        connected_ids = set(
            Relationship.objects.filter(source=root).values_list('target_id', flat=True)
        ) | set(
            Relationship.objects.filter(target=root).values_list('source_id', flat=True)
        ) if root else set()
    except Exception:
        connected_ids = set()

    overlooked = list(Node.objects.filter(id__in=connected_ids - mentioned_ids - {root.id if root else 0})[:4])
    overlooked_names = [n.display_name() for n in overlooked]

    # تعطیلات نزدیک (۱۴ روز آینده)
    near_holidays = upcoming_holidays(14)
    near_hol_str  = ', '.join(f'{h["jalali"]} ({h["holiday"]})' for h in near_holidays) if near_holidays else 'ندارد'

    # ── کش ──────────────────────────────────────────────────────────────────
    cache_key = f'daily_tips_{today.strftime("%Y%m%d")}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    # ── نوع روز ─────────────────────────────────────────────────────────────
    if is_hol and hol_name != 'جمعه':
        day_type_desc = f'تعطیل رسمی ({hol_name}) — روز استراحت و جشن'
        day_context   = f'امروز تعطیل رسمی ({hol_name}) است. نکاتی مناسب این مناسبت بده — تبریک، دید و بازدید، فعالیت‌های جمعی و لذت‌بخش.'
    elif is_hol:
        day_type_desc = 'جمعه — روز تعطیل'
        day_context   = 'امروز جمعه و تعطیله. نکات برای وقت آزاد: خانواده، دوستان صمیمی، استراحت، شارژ انرژی.'
    else:
        day_type_desc = 'روز کاری'
        day_context   = 'امروز روز کاریه. نکات برای ارتباطات هدفمند، پیگیری کارها، تقویت شبکه حرفه‌ای و شخصی.'

    prompt = f"""تو یه مشاور روابط اجتماعی هستی.

📅 امروز: {day_name}، {jalali_date} | فصل: {season} | وضعیت: {day_type_desc}
🔜 تعطیلات نزدیک: {near_hol_str}

وضعیت شبکه روابط:
- {n_nodes} نفر، {n_edges} رابطه
- حال و هوای اخیر: {', '.join(recent_moods) if recent_moods else 'ثبت نشده'}
- هشدارهای فوری: {', '.join(urgent) if urgent else 'ندارد'}
- روابط ضعیف (نیاز به توجه): {', '.join(weak_names) if weak_names else 'ندارد'}
- مدتی از اینها بی‌خبری: {', '.join(overlooked_names) if overlooked_names else 'ندارد'}

{day_context}

اگه تعطیل رسمیه یا نزدیکه به تعطیل رسمی، نکاتت رو با اون مناسبت align کن.
تقویم ایرانی و فرهنگ ایرانی رو در نظر بگیر.

۴-۵ نکته عملی، کوتاه و قابل اجرا برای همین امروز بده.

JSON:
{{
  "day_message": "پیام کوتاه متناسب با روز (مناسبت رو ذکر کن اگه داره)",
  "tips": [
    {{
      "emoji": "...",
      "title": "...",
      "action": "اقدام مشخص و قابل اجرا",
      "reason": "چرا این کار امروز مهمه؟",
      "time_needed": "۵ دقیقه"
    }}
  ],
  "focus_person": {{"name": "...", "suggestion": "..."}}
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'مشاور روابط اجتماعی ایرانی. فقط JSON خروجی بده. بدون markdown.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=1400,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=24 * 3600)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)
