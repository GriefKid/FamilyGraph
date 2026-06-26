"""
Smart Features: Alerts, Psychology Analysis, Daily Tips
"""
import json
import os
from datetime import date, timedelta
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
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

def _compute_alerts(user=None):
    """Compute all active alerts — no AI, fast."""
    today = date.today()
    alerts = []
    user_filter = {'owner': user} if user and user.is_authenticated else {}

    # ── 1. Birthdays today ──────────────────────────────────────
    for node in Node.objects.filter(birth_day__month=today.month, birth_day__day=today.day, **user_filter):
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
        for node in Node.objects.filter(birth_day__month=d.month, birth_day__day=d.day, **user_filter):
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
    for ev in Event.objects.filter(date__gte=today, date__lte=today + timedelta(days=7), **user_filter).prefetch_related('participants'):
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
    for entry in JournalEntry.objects.filter(created_at__date__gte=cutoff7, ai_analyzed=True, **user_filter).prefetch_related('mentioned_nodes')[:30]:
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
        first_entry = JournalEntry.objects.filter(**user_filter).order_by('entry_date').first()
        app_age_days = (today - first_entry.entry_date).days if (first_entry and first_entry.entry_date) else 0

        if app_age_days >= 30:
            root = user.root_node if (user and user.is_authenticated) else None
            if root:
                cutoff30 = today - timedelta(days=30)
                recent_ids = set(
                    JournalEntry.objects.filter(entry_date__gte=cutoff30, **user_filter)
                    .values_list('mentioned_nodes__id', flat=True)
                )
                recent_ids.discard(None)

                connected_ids = set(
                    Relationship.objects.filter(source=root, **user_filter).values_list('target_id', flat=True)
                ) | set(
                    Relationship.objects.filter(target=root, **user_filter).values_list('source_id', flat=True)
                )

                dormant_ids = connected_ids - recent_ids - {root.id}
                for node in Node.objects.filter(id__in=dormant_ids, **user_filter)[:4]:
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

    # ── 6. Relationship decay (90 days no journal mention) ─────
    try:
        if user and user.is_authenticated and user.root_node:
            root = user.root_node
            cutoff90 = today - timedelta(days=90)
            seen_decay = set()
            active_rels = Relationship.objects.filter(
                status='active', **user_filter,
            ).select_related('source', 'target')

            for rel in active_rels:
                other = rel.target if rel.source_id == root.id else rel.source
                if other.id == root.id or other.id in seen_decay:
                    continue
                # ذکر در خاطرات ۹۰ روز اخیر
                mentioned = other.journal_entries.filter(
                    created_at__date__gte=cutoff90, **user_filter
                ).exists()
                if not mentioned:
                    seen_decay.add(other.id)
                    alerts.append({
                        'id':       f'decay_{rel.id}',
                        'type':     'decay',
                        'priority': 'medium',
                        'node_id':       other.id,
                        'node_username': other.username,
                        'node_name':     other.display_name(),
                        'title': f'📉 رابطه با {other.display_name()} داره ضعیف می‌شه',
                        'body':  f'مدت ۳ ماهه از {other.display_name()} توی خاطراتت ذکری نشده. این رابطه رو فراموش کردی؟',
                        'days_until': None,
                    })
    except Exception:
        pass

    # ── فیلتر کردن هشدارهایی که کاربر قبلاً اقدام کرده ────────────────────
    excluded_ids = set(
        AlertAction.objects.filter(
            action__in=['completed', 'dismissed'], **user_filter,
        ).values_list('alert_id', flat=True)
    )
    # dismissed ها بعد از ۷ روز دوباره نشون داده می‌شن
    dismissed_old = set(
        AlertAction.objects.filter(
            action='dismissed',
            created_at__date__lt=today - timedelta(days=7),
            **user_filter,
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
    user = request.user if request.user.is_authenticated else None
    return JsonResponse({'alerts': _compute_alerts(user)})


def alerts_count_api(request):
    """JSON: quick badge count."""
    user = request.user if request.user.is_authenticated else None
    alerts = _compute_alerts(user)
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


@login_required
def alerts_view(request):
    """Full /alerts/ page."""
    alerts = _compute_alerts(request.user)
    return render(request, 'alerts/alerts.html', {'alerts': alerts})


@login_required
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
            node = Node.objects.get(pk=node_id, owner=request.user)
        except Node.DoesNotExist:
            pass

    AlertAction.objects.create(
        alert_id=body.get('alert_id', ''),
        alert_type=body.get('alert_type', ''),
        node=node,
        title=body.get('title', ''),
        action=body.get('action', 'dismissed'),
        outcome=body.get('outcome', ''),
        owner=request.user,
    )
    # کش هشدارها رو پاک کن تا دفعه بعد تازه لود بشه
    cache.delete('alerts_list')
    return JsonResponse({'ok': True})


@login_required
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
        grp = GroupModel.objects.get(name=old_name, owner=request.user)
        grp.name = new_name
        grp.save()
        cache.delete('graph_all_data')
        return JsonResponse({'ok': True})
    except GroupModel.DoesNotExist:
        return JsonResponse({'error': f'گروه «{old_name}» پیدا نشد'}, status=404)


@login_required
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

    deleted, _ = GroupModel.objects.filter(name=name, owner=request.user).delete()
    cache.delete('graph_all_data')
    return JsonResponse({'ok': True, 'deleted': deleted})


# ═══════════════════════════════════════════════════════════════
#  PSYCHOLOGY / SOCIOLOGY ANALYSIS
# ═══════════════════════════════════════════════════════════════

def _build_nx(user=None):
    import networkx as nx
    G = nx.Graph()
    if user and user.is_authenticated:
        all_nodes = list(Node.objects.filter(owner=user))
        all_rels  = list(Relationship.objects.filter(owner=user).select_related('source', 'target'))
    else:
        all_nodes = list(Node.objects.all())
        all_rels  = list(Relationship.objects.select_related('source', 'target'))
    for n in all_nodes:
        G.add_node(n.id, label=n.display_name())
    for r in all_rels:
        G.add_edge(r.source_id, r.target_id, weight=r.strength, status=r.status, rel=r.rel or '')
    return G, all_nodes, all_rels


@login_required
def psychology_view(request):
    """
    Comprehensive network psychology & sociology analysis page.

    Theories implemented:
      - Dunbar's Number (1992) — cognitive limit on stable social relationships
      - Granovetter's Strength of Weak Ties (1973) — weak ties bridge structural gaps
      - Burt's Structural Holes (1992) — constraint score, brokerage positions
      - Watts & Strogatz Small World (1998) — high clustering + short path length
      - Barabási & Albert Scale-Free (1999) — power law degree distribution
      - Putnam Social Capital (2000) — bonding vs. bridging capital
      - Bowlby & Ainsworth Attachment Theory — applied to network patterns
      - Gould-Fernandez Brokerage Types (1989) — coordinator/gatekeeper/liaison
      - McPherson Homophily (2001) — birds of a feather flock together
      - Social Exchange Theory (Blau 1964) — reciprocity in relationships
      - Simmel Triadic Closure (1908) — friend-of-friend suggestions
      - Network Resilience — articulation points, node connectivity
      - Community Detection — Louvain modularity
    """
    import networkx as nx
    import math

    user = request.user if request.user.is_authenticated else None
    G, all_nodes, all_rels = _build_nx(user)
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()

    if n_nodes == 0:
        return render(request, 'psychology/psychology.html', {'empty': True})

    # ── Owner filter shortcut ────────────────────────────────────
    ufilter = {'owner': user} if user else {}

    # ── Root node ────────────────────────────────────────────────
    root = user.root_node if user else None

    # ═══════════════════════════════════════════════════════════
    # 1. DUNBAR'S NUMBER (Robin Dunbar, 1992)
    # Cognitive limit: 5 support clique / 15 sympathy group /
    #                  50 affinity group / 150 Dunbar number / 500+
    # ═══════════════════════════════════════════════════════════
    dunbar = {'intimate': 0, 'close': 0, 'friends': 0, 'acquaintances': 0, 'weak': 0}
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
            dunbar_notes.append(f"⚠️ لایه صمیمی ({dunbar['intimate']} نفر) از حد شناختی ۵ نفر بیشتر — کیفیت ممکن است افت کند")
        elif dunbar['intimate'] < 2:
            dunbar_notes.append(f"💡 لایه صمیمی ({dunbar['intimate']} نفر) بسیار کم — روابط عمیق را تقویت کن")
        if dunbar['close'] > 15:
            dunbar_notes.append(f"⚠️ لایه نزدیک ({dunbar['close']} نفر) از حد ۱۵ نفر بیشتر — انرژی شناختی تقسیم می‌شود")
        if total_direct > 150:
            dunbar_notes.append(f"⚠️ {total_direct} ارتباط مستقیم — بالاتر از عدد داونبار (۱۵۰) — مدیریت سخت‌تر می‌شود")
        elif total_direct < 15:
            dunbar_notes.append(f"💡 فقط {total_direct} ارتباط — شبکه کوچک است؛ گسترش توصیه می‌شود")
    else:
        dunbar_notes.append('نود اصلی (من) را تعریف کن تا تحلیل داونبار انجام شود')

    # ═══════════════════════════════════════════════════════════
    # 2. GRANOVETTER WEAK TIE THEORY (1973)
    # Weak ties = bridges to new info; too many → shallow network
    # Optimal: 35-65% weak ties
    # ═══════════════════════════════════════════════════════════
    strong = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) >= 4)
    weak   = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) <= 2)
    medium = n_edges - strong - weak
    weak_ratio = weak / max(n_edges, 1)
    if 0.35 <= weak_ratio <= 0.65:
        grano_status = 'optimal'; grano_label = 'بهینه'
        grano_note = f'نسبت پیوندهای ضعیف ({weak_ratio:.0%}) در محدوده مناسب — تنوع اطلاعاتی خوب است'
    elif weak_ratio < 0.35:
        grano_status = 'too_strong'; grano_label = 'بیش از حد صمیمی'
        grano_note = f'پیوندهای ضعیف کم ({weak_ratio:.0%}) — خطر اتاق پژواک (Echo Chamber)'
    else:
        grano_status = 'too_weak'; grano_label = 'روابط کم‌عمق'
        grano_note = f'پیوندهای ضعیف زیاد ({weak_ratio:.0%}) — روابط عمیق کافی نیست'

    # ═══════════════════════════════════════════════════════════
    # 3. CENTRALITY MEASURES
    # Degree: direct connections
    # Betweenness: how often on shortest path (broker power)
    # Closeness: how quickly can reach everyone
    # Eigenvector: connected to well-connected people (PageRank-like)
    # ═══════════════════════════════════════════════════════════
    deg_cent = nx.degree_centrality(G)
    btw_cent = nx.betweenness_centrality(G, normalized=True) if n_nodes > 2 else {}
    cls_cent = nx.closeness_centrality(G) if n_nodes > 1 else {}
    try:
        eig_cent = nx.eigenvector_centrality(G, max_iter=1000) if n_nodes > 1 else {}
    except Exception:
        eig_cent = {}

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
                'eigenvector': round(eig_cent.get(nid, 0) * 100),
            })
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # 4. CLUSTERING COEFFICIENT + ECHO CHAMBER RISK
    # High clustering = closed triangles → echo chamber risk
    # Low clustering = open network → diverse info flow
    # ═══════════════════════════════════════════════════════════
    avg_clust = nx.average_clustering(G) if n_nodes > 2 else 0
    if avg_clust > 0.65:
        echo_risk = 'high'; echo_label = 'زیاد'
    elif avg_clust > 0.35:
        echo_risk = 'medium'; echo_label = 'متوسط'
    else:
        echo_risk = 'low'; echo_label = 'کم'

    # ═══════════════════════════════════════════════════════════
    # 5. NETWORK RESILIENCE — articulation points + connectivity
    # Articulation points: remove one → graph disconnects
    # Node connectivity: min nodes to disconnect graph
    # ═══════════════════════════════════════════════════════════
    resilience_score = 0
    node_connectivity = 0
    art_point_ids = []
    critical_nodes = []
    is_connected = nx.is_connected(G)
    if is_connected and n_nodes >= 3:
        try:
            node_connectivity = nx.node_connectivity(G)
            resilience_score = min(100, node_connectivity * 25)
            art_point_ids = list(nx.articulation_points(G))
            for nid in art_point_ids[:5]:
                try:
                    nd = Node.objects.get(pk=nid)
                    critical_nodes.append({
                        'name': nd.display_name(),
                        'degree': G.degree(nid),
                    })
                except Exception:
                    pass
        except Exception:
            resilience_score = 20

    # ═══════════════════════════════════════════════════════════
    # 6. COMMUNITY DETECTION + STRUCTURAL HOLES (Burt 1992)
    # Communities via Louvain algorithm (Blondel et al., 2008)
    # Structural Holes: nodes bridging communities have strategic
    # advantage — they control info flow (low constraint = good)
    # ═══════════════════════════════════════════════════════════
    n_communities = 0
    modularity_val = 0.0
    bridges = []
    bridge_ids = set()
    brokers = []
    comm_map = {}   # initialised here so Burt block can safely reference it
    try:
        from networkx.algorithms.community import louvain_communities
        comms = list(louvain_communities(G, seed=42))
        n_communities = len(comms)
        modularity_val = nx.community.modularity(G, comms)
        for i, c in enumerate(comms):
            for nid in c:
                comm_map[nid] = i
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

    # Burt Constraint: lower = more structural holes = more social capital
    try:
        constraint_map = nx.constraint(G)
        sorted_constraint = sorted(constraint_map.items(), key=lambda x: x[1])
        for nid, c in sorted_constraint[:5]:
            try:
                nd = Node.objects.get(pk=nid)
                # Gould-Fernandez brokerage role classification
                neighbors = list(G.neighbors(nid))
                neighbor_comms = [comm_map.get(nb, -1) for nb in neighbors] if comm_map else []
                own_comm = comm_map.get(nid, -1)
                cross_comm = sum(1 for nc in neighbor_comms if nc != own_comm)
                if c < 0.2:
                    brokerage_type = 'واسطه راهبردی (Strategic Broker)'
                elif c < 0.4:
                    brokerage_type = 'رابط گروه‌ها (Bridge Connector)'
                elif c < 0.6:
                    brokerage_type = 'عضو جزئی (Partial Member)'
                else:
                    brokerage_type = 'عضو منسجم (Embedded Member)'
                brokers.append({
                    'name': nd.display_name(),
                    'constraint': round(c, 3),
                    'type': brokerage_type,
                })
            except Exception:
                pass
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # 7. TRIADIC CLOSURE — Friend Suggestions (Simmel 1908)
    # If A knows B and B knows C, A and C likely should connect.
    # People with most mutual friends = strongest suggestions.
    # ═══════════════════════════════════════════════════════════
    friend_suggestions = []
    if root and root.id in G:
        root_neighbors = set(G.neighbors(root.id))
        potential = {}
        for nb in root_neighbors:
            for nn in G.neighbors(nb):
                if nn != root.id and nn not in root_neighbors:
                    potential[nn] = potential.get(nn, 0) + 1
        sorted_potential = sorted(potential.items(), key=lambda x: x[1], reverse=True)[:5]
        for nid, common_count in sorted_potential:
            try:
                nd = Node.objects.get(pk=nid)
                friend_suggestions.append({
                    'name': nd.display_name(),
                    'common': common_count,
                })
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════
    # 8. SMALL WORLD ANALYSIS (Watts & Strogatz, 1998)
    # Small World: high clustering + short average path length
    # avg_path_length ≈ ln(N) means it's a small world
    # Six Degrees of Separation theory applies when APL < ln(N)*2
    # ═══════════════════════════════════════════════════════════
    avg_path_length = None
    is_small_world = False
    small_world_score = 0
    six_degrees = None
    if is_connected and 2 < n_nodes <= 300:
        try:
            avg_path_length = round(nx.average_shortest_path_length(G), 2)
            expected_random = math.log(max(n_nodes, 2))
            is_small_world = (avg_path_length <= expected_random * 1.5) and avg_clust > 0.2
            small_world_score = round(min(100, (expected_random / max(avg_path_length, 0.01)) * avg_clust * 120))
            six_degrees = avg_path_length <= 6
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # 9. SOCIAL CAPITAL (Putnam, 2000)
    # Bonding Capital: dense connections within groups (clustering)
    # Bridging Capital: connections across groups (bridge nodes)
    # Optimal: balanced mix of both
    # ═══════════════════════════════════════════════════════════
    bonding_score = round(avg_clust * 100)
    bridging_score = round(min(100, len(bridge_ids) / max(n_nodes, 1) * 200))
    if bonding_score > 70 and bridging_score < 30:
        social_capital_note = '💡 سرمایه انسجامی قوی ولی سرمایه پل‌سازی ضعیف — با گروه‌های جدید ارتباط برقرار کن'
        social_capital_status = 'bonding_heavy'
    elif bridging_score > 70 and bonding_score < 30:
        social_capital_note = '💡 پل‌سازی زیاد ولی روابط عمیق کم — روابط موجود را تعمیق بده'
        social_capital_status = 'bridging_heavy'
    elif bonding_score >= 30 and bridging_score >= 30:
        social_capital_note = '✅ ترکیب سالمی از سرمایه انسجامی و پل‌سازی داری'
        social_capital_status = 'balanced'
    else:
        social_capital_note = '⚠️ هر دو نوع سرمایه اجتماعی نیاز به تقویت دارند'
        social_capital_status = 'weak'

    # ═══════════════════════════════════════════════════════════
    # 10. SCALE-FREE NETWORK (Barabási & Albert, 1999)
    # In scale-free networks, a few nodes have very high degree
    # (hubs) — preferential attachment drives growth
    # Coefficient of Variation > 1 suggests scale-free-like behavior
    # ═══════════════════════════════════════════════════════════
    degrees = [d for _, d in G.degree()]
    avg_degree = round(sum(degrees) / max(len(degrees), 1), 1)
    max_degree = max(degrees) if degrees else 0
    if len(degrees) > 1:
        degree_variance = sum((d - avg_degree) ** 2 for d in degrees) / len(degrees)
        degree_std = degree_variance ** 0.5
        cv = degree_std / max(avg_degree, 0.01)  # Coefficient of Variation
        is_scale_free_like = cv > 1.0
    else:
        is_scale_free_like = False
        cv = 0

    # Network density (0=sparse, 1=complete)
    density = nx.density(G)
    density_pct = round(density * 100)

    # ═══════════════════════════════════════════════════════════
    # 11. MY NETWORK ROLE — position classification
    # Based on centrality profile:
    # Hub: high degree + betweenness
    # Gatekeeper/Broker: high betweenness, bridges groups
    # Clique Member: high clustering, embedded in tight group
    # Networker: moderate degree, spread across groups
    # Peripheral: low degree, on edges of network
    # ═══════════════════════════════════════════════════════════
    my_role = None
    my_role_desc = None
    my_role_color = '#6366f1'
    my_deg_val = 0
    my_btw_val = 0
    my_cls_val = 0
    my_eig_val = 0
    my_clust_val = 0
    if root and root.id in G:
        my_deg_val = round(deg_cent.get(root.id, 0) * 100)
        my_btw_val = round(btw_cent.get(root.id, 0) * 100)
        my_cls_val = round(cls_cent.get(root.id, 0) * 100)
        my_eig_val = round(eig_cent.get(root.id, 0) * 100)
        my_clust_val = round(nx.clustering(G, root.id) * 100) if n_nodes > 2 else 0
        d_pct = deg_cent.get(root.id, 0)
        b_pct = btw_cent.get(root.id, 0)
        c_coeff = nx.clustering(G, root.id) if n_nodes > 2 else 0
        if d_pct >= 0.4 and b_pct >= 0.2:
            my_role = 'هاب مرکزی'
            my_role_desc = 'تو یکی از مرکزی‌ترین گره‌های شبکه‌ای. اطلاعات و تأثیر از طریق تو جریان پیدا می‌کنه. ازین جایگاه برای ارزش‌آفرینی استفاده کن.'
            my_role_color = '#f43f5e'
        elif b_pct >= 0.25 or root.id in bridge_ids:
            my_role = 'دروازه‌بان / واسطه'
            my_role_desc = 'تو پل بین گروه‌های مختلف هستی. اطلاعات منحصربه‌فردی داری که بقیه ندارن — این قدرت استراتژیک (Structural Hole) است.'
            my_role_color = '#f59e0b'
        elif c_coeff >= 0.7:
            my_role = 'عضو کلیک'
            my_role_desc = 'دوستانت همه با هم آشنا هستن — گروه صمیمی و منسجم. ولی شاید از اطلاعات خارج از گروه محروم باشی.'
            my_role_color = '#10b981'
        elif d_pct >= 0.2:
            my_role = 'شبکه‌ساز'
            my_role_desc = 'ارتباطات متنوعی داری. نه در مرکز نه در حاشیه — موقعیت مناسب برای رشد و تأثیرگذاری.'
            my_role_color = '#8b5cf6'
        elif G.degree(root.id) <= 2:
            my_role = 'پیرامونی'
            my_role_desc = 'در حاشیه شبکه هستی. فرصت‌های اتصال زیاد وجود داره — با افزودن ارتباطات جدید می‌تونی جایگاهت رو تغییر بدی.'
            my_role_color = '#6b7280'
        else:
            my_role = 'عضو پیوندی'
            my_role_desc = 'در شبکه حضور داری و نقش اتصال‌دهنده ایفا می‌کنی — موقعیت متعادل بین صمیمیت و گستردگی.'
            my_role_color = '#06b6d4'

    # ═══════════════════════════════════════════════════════════
    # 12. ATTACHMENT STYLE HINTS (Bowlby & Ainsworth)
    # Applied heuristically to network patterns:
    # Secure: balanced intimate + active relationships
    # Avoidant: large network, few deep ties
    # Anxious/Preoccupied: very few intense ties, low total
    # ═══════════════════════════════════════════════════════════
    attachment_style = None
    attachment_desc = None
    attachment_color = '#6366f1'
    if root and root.id in G and total_direct > 0:
        intimate_cnt = dunbar.get('intimate', 0)
        close_cnt    = dunbar.get('close', 0)
        active_cnt   = Relationship.objects.filter(status='active', **ufilter).count()
        total_r      = len(all_rels)
        active_ratio = active_cnt / max(total_r, 1)
        if intimate_cnt >= 2 and close_cnt >= 3 and active_ratio >= 0.5:
            attachment_style = 'احتمالاً ایمن (Secure)'
            attachment_desc  = 'شواهد شبکه: روابط صمیمی متعادل با نرخ فعالیت بالا — ویژگی‌های دلبستگی ایمن طبق Bowlby & Ainsworth'
            attachment_color = '#10b981'
        elif intimate_cnt < 2 and total_direct > 15 and avg_clust < 0.3:
            attachment_style = 'احتمالاً اجتنابی (Dismissing-Avoidant)'
            attachment_desc  = 'شبکه بزرگ اما روابط عمیق کم — الگوی مرتبط با دلبستگی اجتنابی. توصیه: عمق دادن به روابط انتخابی'
            attachment_color = '#f59e0b'
        elif intimate_cnt >= 3 and total_direct <= 8:
            attachment_style = 'احتمالاً دوسوگرا (Anxious-Preoccupied)'
            attachment_desc  = 'تمرکز شدید روی تعداد کمی — الگوی مرتبط با دلبستگی اضطرابی. توصیه: گسترش شبکه با حفظ عمق'
            attachment_color = '#f43f5e'
        else:
            attachment_style = 'نامشخص / متنوع'
            attachment_desc  = 'داده کافی برای تشخیص قطعی الگوی دلبستگی وجود ندارد. با اضافه کردن اطلاعات بیشتر دقت افزایش می‌یابد.'
            attachment_color = '#6366f1'

    # ═══════════════════════════════════════════════════════════
    # 13. HOMOPHILY (McPherson et al., 2001)
    # Do people with same career/group tend to connect?
    # Measured by career similarity among connected pairs
    # ═══════════════════════════════════════════════════════════
    homophily_score = 0
    homophily_note = ''
    try:
        career_map = {n.id: (n.career or '').strip().lower() for n in all_nodes}
        same_career_edges = 0
        career_comparable = 0
        for u, v in G.edges():
            cu, cv = career_map.get(u, ''), career_map.get(v, '')
            if cu and cv:
                career_comparable += 1
                if cu == cv:
                    same_career_edges += 1
        if career_comparable > 0:
            homophily_score = round(same_career_edges / career_comparable * 100)
            if homophily_score > 60:
                homophily_note = f'🔴 همگنی بالا ({homophily_score}%) — اکثر روابطت با افراد هم‌شغل است. تنوع بیشتر توصیه می‌شود'
            elif homophily_score > 35:
                homophily_note = f'🟡 همگنی متوسط ({homophily_score}%) — ترکیبی از روابط هم‌شغل و متنوع'
            else:
                homophily_note = f'🟢 تنوع خوب ({homophily_score}% هم‌شغل) — شبکه‌ات از گروه‌های مختلف تشکیل شده'
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # 14. SOCIAL EXCHANGE THEORY (Blau, 1964) — Reciprocity
    # Active relationships as proxy for reciprocal exchange
    # ═══════════════════════════════════════════════════════════
    active_rels  = Relationship.objects.filter(status='active',   **ufilter).count()
    distant_rels = Relationship.objects.filter(status='distant',  **ufilter).count()
    inactive_rels= Relationship.objects.filter(status='inactive', **ufilter).count()
    total_r_count= len(all_rels)
    reciprocity_rate = f'{round(active_rels / max(total_r_count, 1) * 100)}%'

    # ═══════════════════════════════════════════════════════════
    # 15. RELATIONSHIP TYPE DISTRIBUTION
    # ═══════════════════════════════════════════════════════════
    rel_type_counts = {}
    for r in all_rels:
        k = r.rel or '(بدون نوع)'
        rel_type_counts[k] = rel_type_counts.get(k, 0) + 1
    rel_types_sorted = sorted(rel_type_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    max_rel_type_count = rel_types_sorted[0][1] if rel_types_sorted else 1

    # ═══════════════════════════════════════════════════════════
    # 16. JOURNAL INSIGHTS
    # ═══════════════════════════════════════════════════════════
    total_entries    = JournalEntry.objects.filter(**ufilter).count()
    analyzed_entries = JournalEntry.objects.filter(ai_analyzed=True, **ufilter).count()
    recent_moods     = list(
        JournalEntry.objects.filter(**ufilter).exclude(mood='').order_by('-created_at')
        .values_list('mood', flat=True)[:20]
    )

    context = {
        # Basic counts
        'n_nodes': n_nodes,
        'n_edges': n_edges,
        'density': round(density, 3),
        'density_pct': density_pct,
        'avg_degree': avg_degree,
        'max_degree': max_degree,

        # Dunbar
        'dunbar': dunbar,
        'dunbar_notes': dunbar_notes,
        'total_direct': total_direct,

        # Granovetter
        'strong': strong, 'weak': weak, 'medium': medium,
        'weak_ratio_pct': round(weak_ratio * 100),
        'grano_status': grano_status,
        'grano_label': grano_label,
        'grano_note': grano_note,

        # Centrality
        'top_connectors': top_connectors,

        # Clustering / Echo Chamber
        'avg_clust': round(avg_clust, 3),
        'avg_clust_pct': round(avg_clust * 100),
        'echo_risk': echo_risk,
        'echo_label': echo_label,

        # Resilience
        'resilience_score': resilience_score,
        'node_connectivity': node_connectivity,
        'critical_nodes': critical_nodes,
        'art_point_count': len(art_point_ids),

        # Community + Structural Holes
        'n_communities': n_communities,
        'modularity_val': round(modularity_val, 3),
        'bridges': bridges,
        'brokers': brokers,

        # Triadic Closure
        'friend_suggestions': friend_suggestions,

        # Small World
        'avg_path_length': avg_path_length,
        'is_small_world': is_small_world,
        'small_world_score': small_world_score,
        'six_degrees': six_degrees,

        # Social Capital
        'bonding_score': bonding_score,
        'bridging_score': bridging_score,
        'social_capital_note': social_capital_note,
        'social_capital_status': social_capital_status,

        # Scale-free
        'is_scale_free_like': is_scale_free_like,
        'degree_cv': round(cv, 2),

        # My Role
        'my_role': my_role,
        'my_role_desc': my_role_desc,
        'my_role_color': my_role_color,
        'my_deg_val': my_deg_val,
        'my_btw_val': my_btw_val,
        'my_cls_val': my_cls_val,
        'my_eig_val': my_eig_val,
        'my_clust_val': my_clust_val,

        # Attachment Style
        'attachment_style': attachment_style,
        'attachment_desc': attachment_desc,
        'attachment_color': attachment_color,

        # Homophily
        'homophily_score': homophily_score,
        'homophily_note': homophily_note,

        # Reciprocity / Status
        'active_rels': active_rels,
        'distant_rels': distant_rels,
        'inactive_rels': inactive_rels,
        'reciprocity_rate': reciprocity_rate,

        # Relationship types
        'rel_types': rel_types_sorted,
        'max_rel_type_count': max_rel_type_count,

        # Journal
        'total_entries': total_entries,
        'analyzed_entries': analyzed_entries,
        'recent_moods_json': json.dumps(recent_moods, ensure_ascii=False),
    }
    return render(request, 'psychology/psychology.html', context)


@login_required
@csrf_exempt
def psychology_ai_api(request):
    """POST → comprehensive AI psychology+sociology narrative. Cached 6h per user."""
    cache_key = f'psych_ai_{request.user.id}_{date.today().strftime("%Y%m%d")}'
    body = {}
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        pass
    cached = cache.get(cache_key)
    if cached and not (request.GET.get('refresh') or body.get('refresh')):
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    user = request.user if request.user.is_authenticated else None
    G, all_nodes, all_rels = _build_nx(user)
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()

    if n_nodes == 0:
        return JsonResponse({'error': 'شبکه خالی است'}, status=400)

    import networkx as nx, math
    avg_clust = nx.average_clustering(G) if n_nodes > 2 else 0
    density   = nx.density(G)
    is_connected = nx.is_connected(G)

    deg_cent = nx.degree_centrality(G)
    btw_cent = nx.betweenness_centrality(G, normalized=True) if n_nodes > 2 else {}

    degrees  = [d for _, d in G.degree()]
    avg_deg  = round(sum(degrees) / max(len(degrees), 1), 1)
    max_deg  = max(degrees) if degrees else 0

    strong = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) >= 4)
    weak   = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) <= 2)
    weak_ratio = weak / max(n_edges, 1)

    avg_path = None
    if is_connected and 2 < n_nodes <= 200:
        try:
            avg_path = round(nx.average_shortest_path_length(G), 2)
        except Exception:
            pass

    ufilter = {'owner': user} if user else {}
    root = user.root_node if user else None

    # Dunbar layers for root
    dunbar = {'intimate': 0, 'close': 0, 'friends': 0, 'acquaintances': 0, 'weak': 0}
    if root and root.id in G:
        for _, _, d in G.edges(root.id, data=True):
            s = d.get('weight', 3)
            if s == 5:   dunbar['intimate'] += 1
            elif s == 4: dunbar['close'] += 1
            elif s == 3: dunbar['friends'] += 1
            elif s == 2: dunbar['acquaintances'] += 1
            else:        dunbar['weak'] += 1

    # Communities
    n_communities = 0
    try:
        from networkx.algorithms.community import louvain_communities
        comms = list(louvain_communities(G, seed=42))
        n_communities = len(comms)
    except Exception:
        pass

    # Relationship types
    rel_types = {}
    for r in all_rels:
        k = r.rel or 'نامشخص'
        rel_types[k] = rel_types.get(k, 0) + 1

    # Top betweenness nodes (potential brokers)
    top_brokers = []
    for nid, b in sorted(btw_cent.items(), key=lambda x: x[1], reverse=True)[:3]:
        try:
            top_brokers.append(Node.objects.get(pk=nid).display_name())
        except Exception:
            pass

    # Recent moods
    recent_moods = list(
        JournalEntry.objects.filter(**ufilter).exclude(mood='').order_by('-created_at')
        .values_list('mood', flat=True)[:10]
    )

    active_cnt = Relationship.objects.filter(status='active', **ufilter).count()

    network_summary = {
        'افراد_شبکه': n_nodes,
        'روابط': n_edges,
        'چگالی_شبکه': round(density, 3),
        'میانگین_درجه': avg_deg,
        'حداکثر_درجه': max_deg,
        'پیوندهای_قوی': strong,
        'پیوندهای_ضعیف': weak,
        'نسبت_پیوند_ضعیف': f'{round(weak_ratio*100)}%',
        'ضریب_خوشه‌بندی': round(avg_clust, 3),
        'تعداد_گروه_اجتماعی': n_communities,
        'میانگین_مسیر_کوتاه': avg_path,
        'روابط_فعال': active_cnt,
        'لایه_داونبار': dunbar,
        'انواع_رابطه': dict(sorted(rel_types.items(), key=lambda x: x[1], reverse=True)[:6]),
        'واسطه‌های_اصلی': top_brokers,
        'حال_و_هوای_اخیر': recent_moods,
    }

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    prompt = f"""تو یه روانشناس و جامعه‌شناس متخصص شبکه‌های اجتماعی هستی. داده‌های شبکه اجتماعی شخصی زیر رو با عمق کامل تحلیل کن:

{json.dumps(network_summary, ensure_ascii=False, indent=2)}

این تحلیل باید شامل همه این تئوری‌ها باشه:
• نظریه داونبار (Dunbar): تحلیل لایه‌های شناختی و ظرفیت مدیریت روابط
• نظریه گرانووتر (Granovetter): ارزیابی پیوندهای ضعیف و قوی و نقش اطلاع‌رسانی
• حفره‌های ساختاری بورت (Burt): موقعیت واسطه‌ای و سرمایه اجتماعی
• دنیای کوچک واتس-استروگاتز (Watts-Strogatz): تحلیل ساختار «شش درجه جدایی»
• سرمایه اجتماعی پاتنام (Putnam): تعادل سرمایه انسجامی vs پل‌سازی
• نظریه دلبستگی بولبی-اینسورث (Bowlby-Ainsworth): الگوی دلبستگی شبکه
• نظریه مبادله اجتماعی بلاو (Blau): تعادل و عمل‌متقابل در روابط
• همگنی مک‌فرسون (McPherson): تنوع یا همگنی در شبکه
• شبکه‌های مقیاس‌آزاد باراباسی (Barabási): توزیع قدرت در شبکه

خروجی JSON (به فارسی کامل):
{{
  "health": {{"score": 0-100, "label": "وضعیت کلی", "summary": "خلاصه ارزیابی (۳-۴ جمله)"}},
  "patterns": ["الگوی ۱ با پشتوانه تئوری", "الگوی ۲", "الگوی ۳"],
  "risks": ["ریسک ۱ با توضیح", "ریسک ۲", "ریسک ۳"],
  "opportunities": ["فرصت ۱", "فرصت ۲", "فرصت ۳"],
  "recommendations": [
    {{"action": "اقدام مشخص عملی", "theory": "پشتوانه نظری", "impact": "بالا/متوسط/کم"}},
    {{"action": "...", "theory": "...", "impact": "..."}}
  ],
  "psychological_profile": "پروفایل روانشناختی کامل (۵-۷ جمله عمیق)",
  "sociological_summary": "خلاصه جامعه‌شناختی کامل (۵-۷ جمله)"
}}

فقط JSON. عمیق و تخصصی به فارسی."""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'متخصص روانشناسی و جامعه‌شناسی شبکه‌های اجتماعی. فقط JSON خروجی بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=2500,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=6 * 3600)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


# ═══════════════════════════════════════════════════════════════
#  DAILY TIPS
# ═══════════════════════════════════════════════════════════════

@login_required
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
        'alerts_count':  len([a for a in _compute_alerts(request.user if request.user.is_authenticated else None) if a.get('priority') == 'high']),
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

    req_user = request.user if request.user.is_authenticated else None
    ufilter  = {'owner': req_user} if req_user else {}

    n_nodes = Node.objects.filter(**ufilter).count()
    n_edges = Relationship.objects.filter(**ufilter).count()

    alerts = _compute_alerts(req_user)
    urgent = [a['title'] for a in alerts if a.get('priority') == 'high'][:3]

    recent_moods = list(
        JournalEntry.objects.filter(**ufilter).order_by('-created_at').exclude(mood='').values_list('mood', flat=True)[:5]
    )

    weak_rels  = list(Relationship.objects.filter(strength__lte=2, **ufilter).select_related('target')[:5])
    weak_names = [r.target.display_name() for r in weak_rels]

    root = req_user.root_node if req_user else None
    cutoff14 = today - timedelta(days=14)
    mentioned_ids = set(
        JournalEntry.objects.filter(entry_date__gte=cutoff14, **ufilter).values_list('mentioned_nodes__id', flat=True)
    )
    mentioned_ids.discard(None)
    if root:
        connected_ids = set(
            Relationship.objects.filter(source=root, **ufilter).values_list('target_id', flat=True)
        ) | set(
            Relationship.objects.filter(target=root, **ufilter).values_list('source_id', flat=True)
        )
    else:
        connected_ids = set()

    overlooked = list(Node.objects.filter(id__in=connected_ids - mentioned_ids - {root.id if root else 0}, **ufilter)[:4])
    overlooked_names = [n.display_name() for n in overlooked]

    # تعطیلات نزدیک (۱۴ روز آینده)
    near_holidays = upcoming_holidays(14)
    near_hol_str  = ', '.join(f'{h["jalali"]} ({h["holiday"]})' for h in near_holidays) if near_holidays else 'ندارد'

    # ── کش — per user so each user gets their own tips ──────────────────────
    uid = request.user.id if request.user.is_authenticated else 0
    cache_key = f'daily_tips_{uid}_{today.strftime("%Y%m%d")}'
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
