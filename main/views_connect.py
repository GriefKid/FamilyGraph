"""
views_connect.py — مسیر آشنایی (V5)

«می‌خوام با X دوست/همکار بشم — چطوری؟»
  1. مسیر گراف: کوتاه‌ترین زنجیره‌ی معرفی از من تا هدف
  2. آشناهای مشترک + گروه‌ها و رویدادهای مشترک
  3. پلن AI: قدم‌به‌قدم، بر اساس شناختی که از آدم‌های وسط راه داریم
"""
import json
from collections import deque
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Node, Relationship


def _adjacency(user):
    """گراف بدون جهت: node_id → {neighbor_id: (rel_label, strength)}"""
    adj = {}
    for r in Relationship.objects.filter(owner=user).only(
            'source_id', 'target_id', 'rel', 'strength'):
        adj.setdefault(r.source_id, {})[r.target_id] = (r.rel or '', r.strength or 3)
        adj.setdefault(r.target_id, {})[r.source_id] = (r.rel or '', r.strength or 3)
    return adj


def _shortest_path(adj, src, dst):
    """BFS — لیست node_id از src تا dst یا None."""
    if src == dst or src not in adj:
        return None
    q, seen, parent = deque([src]), {src}, {}
    while q:
        cur = q.popleft()
        for nb in adj.get(cur, {}):
            if nb in seen:
                continue
            seen.add(nb)
            parent[nb] = cur
            if nb == dst:
                path = [dst]
                while path[-1] != src:
                    path.append(parent[path[-1]])
                return list(reversed(path))
            q.append(nb)
    return None


def _connect_data(user, target):
    """داده‌های خام مسیر آشنایی — dict یا error."""
    root = user.root_node
    if not root:
        return {'error': 'اول نود اصلی (من) رو در پروفایل تنظیم کن'}
    if root.id == target.id:
        return {'error': 'این خودتی 😄'}

    adj = _adjacency(user)
    names = {n.id: n.display_name() for n in Node.objects.filter(owner=user)}

    is_direct = target.id in adj.get(root.id, {})

    # مسیر
    path_ids = _shortest_path(adj, root.id, target.id)
    path = []
    if path_ids:
        for i, nid in enumerate(path_ids):
            step = {'id': nid, 'name': names.get(nid, '؟'), 'me': nid == root.id}
            if i > 0:
                rel, s = adj[path_ids[i - 1]][nid]
                step['via_rel'] = rel
                step['via_strength'] = s
            path.append(step)

    # آشناهای مشترک — مرتب بر اساس قدرتِ یال با من
    my_nbrs = adj.get(root.id, {})
    tg_nbrs = adj.get(target.id, {})
    mutual_ids = set(my_nbrs) & set(tg_nbrs) - {root.id, target.id}
    mutuals = sorted(
        [{'id': m, 'name': names.get(m, '؟'),
          'my_strength': my_nbrs[m][1], 'their_strength': tg_nbrs[m][1]}
         for m in mutual_ids],
        key=lambda x: -(x['my_strength'] + x['their_strength'])
    )

    # گروه‌های مشترک
    shared_groups = []
    try:
        root_groups = set(root.groups.values_list('name', flat=True))
        tg_groups = set(target.groups.values_list('name', flat=True))
        shared_groups = sorted(root_groups & tg_groups)
    except Exception:
        pass

    # رویدادهای مشترک گذشته
    shared_events = []
    try:
        today = timezone.localdate()
        evs = target.events.filter(owner=user, date__lte=today,
                                   participants=root).order_by('-date')[:5]
        shared_events = [{'title': e.title, 'date': str(e.date)} for e in evs]
    except Exception:
        pass

    return {
        'is_direct': is_direct,
        'path': path,
        'hops': (len(path) - 1) if path else None,
        'mutuals': mutuals[:8],
        'shared_groups': shared_groups,
        'shared_events': shared_events,
        'target_name': target.display_name(),
        'target_career': target.career or '',
    }


# ═══════════════════════════════════════════════════════════════
#  GET /api/connect/<pk>/
# ═══════════════════════════════════════════════════════════════

@login_required
def connect_info_api(request, pk):
    try:
        target = Node.objects.get(pk=pk, owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)
    data = _connect_data(request.user, target)
    if 'error' in data:
        return JsonResponse(data, status=400)
    return JsonResponse({'ok': True, **data})


# ═══════════════════════════════════════════════════════════════
#  POST /api/connect/<pk>/plan/   {goal: "friendship"|"work"}
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def connect_plan_api(request, pk):
    """پلن قدم‌به‌قدم AI برای ساختن رابطه با هدف."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        target = Node.objects.get(pk=pk, owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)

    try:
        body = json.loads(request.body or '{}')
    except Exception:
        body = {}
    goal = 'work' if body.get('goal') == 'work' else 'friendship'
    goal_fa = 'رابطه کاری/حرفه‌ای' if goal == 'work' else 'دوستی'

    cache_key = f'connect_plan_{request.user.id}_{pk}_{goal}_{date.today():%Y%m%d}'
    cached = cache.get(cache_key)
    if cached and not body.get('refresh'):
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    data = _connect_data(request.user, target)
    if 'error' in data:
        return JsonResponse(data, status=400)

    # اطلاعات شخصیتی هدف (اگه ثبت شده)
    person = {'name': data['target_name'], 'career': data['target_career']}
    try:
        info = target.informations.first()
        if info and isinstance(info.data, dict):
            for k in ('personality', 'interests', 'values', 'preferences'):
                if info.data.get(k):
                    person[k] = info.data[k]
    except Exception:
        pass

    path_str = ' ← '.join(s['name'] for s in data['path']) if data['path'] else 'مسیر مستقیمی در گراف نیست'
    mutuals_str = '، '.join(
        f"{m['name']} (با من: {m['my_strength']}/5، با هدف: {m['their_strength']}/5)"
        for m in data['mutuals'][:5]) or 'آشنای مشترکی ثبت نشده'

    from .views_smart_features import _ai_client, _model, _extract_json, _rate_limit_msg
    client, api_key, _prov = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key تنظیم نشده'}, status=500)

    prompt = f"""هدف کاربر: ساختن {goal_fa} با «{person['name']}».

## داده‌های گراف اجتماعی:
- وضعیت فعلی: {'رابطه مستقیم دارند (تقویتش کن)' if data['is_direct'] else 'رابطه مستقیم ندارند (از صفر)'}
- مسیر معرفی در گراف: {path_str}
- آشناهای مشترک: {mutuals_str}
- گروه‌های مشترک: {'، '.join(data['shared_groups']) or '—'}
- رویدادهای مشترک گذشته: {'، '.join(e['title'] for e in data['shared_events']) or '—'}
- اطلاعات هدف: {json.dumps(person, ensure_ascii=False)}

یک پلن عملی ۴ تا ۶ قدمی بده. قدم اول باید همین امروز قابل انجام باشه.
اگه آشنای مشترک قوی هست، از مسیر معرفی استفاده کن. اگه نیست، از بافت مشترک (گروه/رویداد/علاقه).
اصول روانشناسی که بلدی رو به کار بگیر (مواجهه مکرر، شباهت، عمل متقابل، خودافشایی تدریجی) ولی اسم‌فروشی نکن — عملی بگو.

JSON خالص:
{{
  "steps": [
    {{"n": 1, "title": "...", "how": "دقیقاً چیکار کنه", "why": "چرا جواب می‌ده (کوتاه)", "when": "امروز/این هفته/..."}}
  ],
  "opener": "یه پیام/جمله‌ی شروع پیشنهادی به فارسی خودمونی",
  "warning": "یه اشتباه رایج که نباید بکنه"
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'مشاور روابط اجتماعی — عملی، مشخص، بدون کلی‌گویی. فقط JSON خروجی بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=1100,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=24 * 3600)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)
