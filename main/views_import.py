"""
views_import.py — ایمپورت تعاملی تلگرام (V8.1)

جریان سه‌مرحله‌ای:
  1. scan   → فایل پارس می‌شه، هیچی نوشته نمی‌شه؛ لیست مخاطب‌ها + پیشنهاد match برمی‌گرده
  2. کاربر برای هر مخاطب تصمیم می‌گیره: نود موجود / نود جدید / رد شو
  3. apply  → فقط طبق تصمیم‌های کاربر می‌نویسه
  + undo    → پاک‌سازی کامل هر چیزی که ایمپورت ساخته
  + analyze → AI متن گفتگو رو می‌خونه، آدم‌های ذکرشده رو درمیاره و از کاربر می‌پرسه کی‌ان
"""
import io
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Node, Relationship

MAX_SIZE = 400 * 1024 * 1024
SCAN_TTL = 2 * 3600           # کش اسکن: ۲ ساعت
SAMPLE_CHARS = 7000           # حجم نمونه‌ی متن هر مخاطب برای AI
IMPORT_NOTE = 'ایمپورت تلگرام'
IMPORT_REL = 'تلگرام'


def _norm(s):
    return (s or '').strip().lower().replace('‌', ' ').replace('  ', ' ')


def _flatten_text(t):
    """text تلگرام یا str است یا لیستی از str/dict."""
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        out = []
        for part in t:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get('text'):
                out.append(str(part['text']))
        return ''.join(out)
    return ''


def _body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return None


@login_required
def telegram_import_view(request):
    nodes = Node.objects.filter(owner=request.user).order_by('username')
    node_opts = [{'id': n.id, 'label': n.display_name()} for n in nodes]
    root = request.user.root_node
    return render(request, 'import/telegram.html', {
        'node_opts_json': json.dumps(node_opts, ensure_ascii=False),
        'root_id': root.id if root else '',
        'root_name': root.display_name() if root else '',
    })


# ═══════════════════════════════════════════════════════════════
#  مرحله ۱ — POST /api/import/telegram/scan/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def telegram_scan_api(request):
    """پارس فایل بدون نوشتن — لیست مخاطب‌ها + پیشنهاد match."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'فایل result.json رو انتخاب کن'}, status=400)
    if f.size > MAX_SIZE:
        return JsonResponse({'error': 'فایل بزرگ‌تر از ۴۰۰ مگابایته'}, status=400)

    try:
        days_limit = max(7, min(int(request.POST.get('days', 365)), 3650))
    except (TypeError, ValueError):
        days_limit = 365

    try:
        data = json.load(io.TextIOWrapper(f.file, encoding='utf-8'))
    except Exception:
        return JsonResponse({'error': 'JSON نامعتبر — همون result.json تلگرام رو بده'}, status=400)

    if isinstance(data, dict) and 'chats' in data:
        chats = (data.get('chats') or {}).get('list') or []
    elif isinstance(data, dict) and data.get('messages') is not None:
        chats = [data]
    else:
        return JsonResponse({'error': 'ساختار فایل شناخته نشد'}, status=400)

    user = request.user
    today = timezone.localdate()
    cutoff = today - timedelta(days=days_limit)

    # نگاشت نام → نود برای پیشنهاد
    lookup = {}
    for n in Node.objects.filter(owner=user):
        for key in (n.username, n.nickname, n.name,
                    f"{n.first_name} {n.last_name}".strip()):
            k = _norm(key)
            if k:
                lookup.setdefault(k, n)

    scan = {}       # name → {'msgs', 'days': [iso], 'sample'}
    contacts = []   # خروجی UI

    for chat in chats:
        if not isinstance(chat, dict) or chat.get('type') != 'personal_chat':
            continue
        name = (chat.get('name') or '').strip()
        msgs = chat.get('messages') or []
        if not name or name.lower() == 'deleted account' or not msgs:
            continue

        dates, texts = set(), []
        for m in msgs:
            ds = m.get('date')
            if not ds:
                continue
            try:
                d = datetime.strptime(ds[:10], '%Y-%m-%d').date()
            except ValueError:
                continue
            if cutoff <= d <= today:
                dates.add(d)
                txt = _flatten_text(m.get('text')).strip()
                if txt:
                    who = 'او' if _norm(m.get('from')) == _norm(name) else 'من'
                    texts.append(f'{who}: {txt[:220]}')
        if not dates:
            continue

        # نمونه‌ی متن برای تحلیل AI — آخرین پیام‌ها
        sample = '\n'.join(texts[-260:])[-SAMPLE_CHARS:]

        # پیشنهاد match: تطبیق دقیق → شباهت شمول
        nname = _norm(name)
        suggested = None
        hit = lookup.get(nname)
        if hit is None and len(nname) >= 3:
            for k, nd in lookup.items():
                if len(k) >= 3 and (nname in k or k in nname):
                    hit = nd
                    break
        if hit:
            suggested = {'id': hit.id, 'label': hit.display_name()}

        scan[name] = {'msgs': len(msgs), 'days': sorted(str(d) for d in dates),
                      'sample': sample}
        contacts.append({
            'name': name,
            'msgs': len(msgs),
            'active_days': len(dates),
            'suggested': suggested,
        })

    if not contacts:
        return JsonResponse({'error': 'هیچ چت شخصی‌ای توی بازه‌ی انتخابی پیدا نشد'}, status=400)

    contacts.sort(key=lambda c: -c['msgs'])
    cache.set(f'tg_scan_{user.id}', scan, SCAN_TTL)

    return JsonResponse({'ok': True, 'contacts': contacts, 'total': len(contacts)})


# ═══════════════════════════════════════════════════════════════
#  مرحله ۲ — POST /api/import/telegram/apply/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def telegram_apply_api(request):
    """{mapping: [{name, action: 'skip'|'new'|'node:ID'}], make_edges} → نوشتن طبق تصمیم کاربر."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    user = request.user
    scan = cache.get(f'tg_scan_{user.id}')
    if not scan:
        return JsonResponse({'error': 'اسکن منقضی شده — فایل رو دوباره اسکن کن'}, status=410)

    make_edges = bool(body.get('make_edges', True))
    root = user.root_node

    root_neighbors = set()
    if root:
        root_neighbors = set(Relationship.objects.filter(
            owner=user, source=root).values_list('target_id', flat=True)) | set(
            Relationship.objects.filter(owner=user, target=root).values_list('source_id', flat=True))

    stats = {'contacts': 0, 'nodes_created': 0, 'interactions': 0, 'edges': 0, 'skipped': 0}
    report = []
    name_to_node = {}   # برای مرحله‌ی تحلیل

    for item in body.get('mapping') or []:
        name = (item.get('name') or '').strip()
        action = item.get('action') or 'skip'
        info = scan.get(name)
        if not info:
            continue
        if action == 'skip':
            stats['skipped'] += 1
            continue

        # ── نود ──
        node, is_new = None, False
        if action == 'new':
            node, is_new = Node.objects.get_or_create(
                username=name[:100], owner=user, defaults={'name': name[:200]})
            if is_new:
                stats['nodes_created'] += 1
        elif action.startswith('node:'):
            try:
                node = Node.objects.get(pk=int(action.split(':')[1]), owner=user)
            except Exception:
                continue
        if node is None or (root and node.id == root.id):
            continue

        stats['contacts'] += 1
        name_to_node[name] = node.id

        # ── تعامل‌های روزانه (dedupe) ──
        made = 0
        try:
            from .models import Interaction
            dates = {datetime.strptime(d, '%Y-%m-%d').date() for d in info['days']}
            existing = set(Interaction.objects.filter(
                owner=user, node=node, kind='message',
                date__gte=min(dates)).values_list('date', flat=True))
            rows = [Interaction(node=node, owner=user, kind='message', date=d,
                                feeling=0, note=IMPORT_NOTE)
                    for d in sorted(dates - existing)]
            if rows:
                Interaction.objects.bulk_create(rows)
                made = len(rows)
                stats['interactions'] += made
        except Exception:
            pass

        # ── یال به root ──
        edge_made = False
        if make_edges and root and node.id not in root_neighbors:
            try:
                strength = 4 if info['msgs'] >= 1000 else (3 if info['msgs'] >= 200 else 2)
                Relationship.objects.create(
                    source=root, target=node, rel=IMPORT_REL,
                    strength=strength, status='active', owner=user)
                root_neighbors.add(node.id)
                stats['edges'] += 1
                edge_made = True
            except Exception:
                pass

        report.append({'name': name, 'node_id': node.id, 'is_new': is_new,
                       'messages': info['msgs'], 'active_days': len(info['days']),
                       'interactions_added': made, 'edge_created': edge_made,
                       'has_sample': bool(info.get('sample'))})

    cache.set(f'tg_map_{user.id}', name_to_node, SCAN_TTL)
    report.sort(key=lambda r: -r['messages'])
    return JsonResponse({'ok': True, 'stats': stats, 'report': report})


# ═══════════════════════════════════════════════════════════════
#  Undo — POST /api/import/telegram/undo/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def telegram_undo_api(request):
    """پاک‌سازی همه‌ی چیزهایی که ایمپورت تلگرام ساخته."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    user = request.user
    deleted = {'interactions': 0, 'relationships': 0, 'nodes': 0}

    # ۱) تعامل‌های ایمپورتی
    try:
        from .models import Interaction
        deleted['interactions'] = Interaction.objects.filter(
            owner=user, note=IMPORT_NOTE).delete()[0]
    except Exception:
        pass

    # ۲) یال‌های «تلگرام»
    try:
        deleted['relationships'] = Relationship.objects.filter(
            owner=user, rel=IMPORT_REL).delete()[0]
    except Exception:
        pass

    # ۳) نودهای ساخته‌ی ایمپورت: امضاشون username==name و کاملاً خالی +
    #    الان هم هیچ اتصالی/داده‌ای ندارن
    try:
        candidates = Node.objects.filter(owner=user, username_locked=False) \
            .exclude(pk=user.root_node_id or -1) \
            .filter(first_name='', last_name='', nickname='', career='',
                    phone_number='', picture='', birth_day__isnull=True)
        removed = 0
        for n in candidates:
            if _norm(n.username) != _norm(n.name):
                continue
            has_rel = Relationship.objects.filter(Q(source=n) | Q(target=n)).exists()
            if has_rel or n.informations.exists() or n.events.exists() \
               or n.journal_entries.exists():
                continue
            try:
                from .models import Interaction
                if Interaction.objects.filter(node=n).exists():
                    continue
            except Exception:
                pass
            try:
                from .models import FollowUp, Debt
                if FollowUp.objects.filter(node=n).exists() or Debt.objects.filter(node=n).exists():
                    continue
            except Exception:
                pass
            n.delete()
            removed += 1
        deleted['nodes'] = removed
    except Exception:
        pass

    return JsonResponse({'ok': True, 'deleted': deleted})


# ═══════════════════════════════════════════════════════════════
#  تحلیل AI — POST /api/import/telegram/analyze/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def telegram_analyze_api(request):
    """{name} → AI متن گفتگو رو می‌خونه و آدم‌های ذکرشده رو درمیاره."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request) or {}
    name = (body.get('name') or '').strip()

    scan = cache.get(f'tg_scan_{request.user.id}') or {}
    info = scan.get(name)
    if not info or not info.get('sample'):
        return JsonResponse({'error': 'نمونه‌ی متن در دسترس نیست — دوباره اسکن کن'}, status=410)

    from .views_smart_features import _ai_client, _model, _extract_json, _rate_limit_msg
    client, api_key, _prov = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'کلید AI تنظیم نشده'}, status=500)

    prompt = f"""این نمونه‌ی گفتگوی من با «{name}» در تلگرامه («من» = خودم، «او» = {name}):

{info['sample']}

آدم‌های سومی که توی گفتگو ذکر شدن رو دربیار (اسم کوچیک، لقب، هرچی صداشون کردن).
برای هرکدوم حدس بزن نسبتش با {name} یا با من چیه (از بافت جمله‌ها).
اسم‌های عمومی/سلبریتی/برند رو نیار — فقط آدم‌های واقعیِ زندگی ما.
حداکثر ۶ نفر، مرتب بر اساس تکرار.

JSON خالص:
{{"people": [
  {{"name": "...", "relation": "مثلاً: دوستِ {name} / همکارِ من / خواهرش",
    "evidence": "نقل‌قول کوتاهی که ازش فهمیدی"}}
]}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'تحلیلگر شبکه‌ی اجتماعی. فقط JSON خروجی بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=800,
        )
        result = _extract_json(resp.choices[0].message.content)
        people = result.get('people') or []
        return JsonResponse({'ok': True, 'people': people[:6]})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


# ═══════════════════════════════════════════════════════════════
#  تحلیل رابطه — POST /api/import/telegram/relation/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def telegram_relation_api(request):
    """{name} → AI از روی گفتگو: شخصیت، اخلاقیات، نمره‌ی دوستی، پرچم قرمز و…"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request) or {}
    name = (body.get('name') or '').strip()

    scan = cache.get(f'tg_scan_{request.user.id}') or {}
    info = scan.get(name)
    if not info or not info.get('sample'):
        return JsonResponse({'error': 'نمونه‌ی متن در دسترس نیست — دوباره اسکن کن'}, status=410)

    from .views_smart_features import _ai_client, _model, _extract_json, _rate_limit_msg
    client, api_key, _prov = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'کلید AI تنظیم نشده'}, status=500)

    prompt = f"""نمونه‌ی گفتگوی من با «{name}» («من» = خودم، «او» = {name}):

{info['sample']}

به‌عنوان روانشناس روابط، این رابطه و شخص «{name}» رو از روی همین گفتگو تحلیل کن.
منصف باش — از شواهد متن نتیجه بگیر، نه حدس کلی. فارسی خودمونی.

JSON خالص:
{{
  "personality": "۲-۳ جمله درباره شخصیت و اخلاقیات {name}",
  "communication_style": "سبک ارتباطیش در یک جمله",
  "values": ["ارزش‌هایی که براش مهمه، حداکثر ۴"],
  "interests": ["علایقش که از چت معلومه، حداکثر ۴"],
  "strengths": ["نقاط قوت این رابطه، حداکثر ۳"],
  "red_flags": ["نکات منفی/هشدار اگه هست، حداکثر ۳ — نبود، خالی"],
  "relationship_quality": "کیفیت کلی رابطه در یک جمله",
  "friendship_score": 0-100,
  "score_reasons": ["چرا این نمره، ۲-۳ دلیل کوتاه"],
  "suggested_rel_type": "دوست صمیمی / دوست / همکار / آشنا / خانواده",
  "suggested_strength": 1-5,
  "tip": "یه توصیه عملی برای بهتر شدن این رابطه"
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'روانشناس روابط — دقیق، مستند به متن، بدون کلی‌گویی. فقط JSON.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=1000,
        )
        result = _extract_json(resp.choices[0].message.content)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


# ═══════════════════════════════════════════════════════════════
#  ذخیره تحلیل در پروفایل — POST /api/import/telegram/save-relation/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def telegram_save_relation_api(request):
    """{node_id, data{...}, set_strength, strength} → merge در Information + قدرت یال."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    user = request.user
    try:
        node = Node.objects.get(pk=body.get('node_id'), owner=user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود پیدا نشد'}, status=404)

    d = body.get('data') or {}
    from .models import Information
    info = Information.objects.filter(node=node).first()
    stored = info.data if (info and isinstance(info.data, dict)) else {}

    LIST_KEYS = ('values', 'interests', 'strengths', 'red_flags', 'score_reasons')
    STR_KEYS = ('personality', 'communication_style', 'relationship_quality',
                'suggested_rel_type', 'tip')
    for k in LIST_KEYS:
        vals = d.get(k) or []
        if vals:
            old = stored.get(k) or []
            stored[k] = list(dict.fromkeys(list(old) + [str(v) for v in vals]))[:12]
    for k in STR_KEYS:
        if d.get(k):
            stored[k] = str(d[k])[:400]
    if d.get('friendship_score') is not None:
        try:
            stored['friendship_score'] = max(0, min(100, int(d['friendship_score'])))
        except (TypeError, ValueError):
            pass
    stored['analyzed_from'] = 'telegram_chat'

    if info:
        info.data = stored
        info.save()
    else:
        Information.objects.create(node=node, visibility='private', data=stored)

    # قدرت یال root↔node (اختیاری)
    strength_updated = False
    if body.get('set_strength'):
        try:
            s = max(1, min(5, int(body.get('strength') or 3)))
            root = user.root_node
            if root:
                rel = Relationship.objects.filter(
                    Q(source=root, target=node) | Q(source=node, target=root),
                    owner=user).first()
                if rel and rel.strength != s:
                    rel.strength = s
                    rel.save()
                    strength_updated = True
        except Exception:
            pass

    return JsonResponse({'ok': True, 'strength_updated': strength_updated})


# ═══════════════════════════════════════════════════════════════
#  اعمال آدم‌های کشف‌شده — POST /api/import/telegram/apply-mentions/
# ═══════════════════════════════════════════════════════════════

@login_required
@csrf_exempt
def telegram_apply_mentions_api(request):
    """{contact_node_id, items: [{name, action, relation}]} → ساخت نود/یال طبق تصمیم کاربر."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    user = request.user
    try:
        contact = Node.objects.get(pk=body.get('contact_node_id'), owner=user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'نود مخاطب پیدا نشد'}, status=404)

    created_nodes = created_edges = 0
    for item in body.get('items') or []:
        action = item.get('action') or 'skip'
        if action == 'skip':
            continue
        name = (item.get('name') or '').strip()
        relation = (item.get('relation') or '').strip()[:100]

        node = None
        if action == 'new' and name:
            node, is_new = Node.objects.get_or_create(
                username=name[:100], owner=user, defaults={'name': name[:200]})
            if is_new:
                created_nodes += 1
        elif action.startswith('node:'):
            try:
                node = Node.objects.get(pk=int(action.split(':')[1]), owner=user)
            except Exception:
                continue
        if node is None or node.id == contact.id:
            continue

        exists = Relationship.objects.filter(
            Q(source=contact, target=node) | Q(source=node, target=contact),
            owner=user).exists()
        if not exists:
            try:
                Relationship.objects.create(
                    source=contact, target=node,
                    rel=relation or 'آشنا (از چت تلگرام)',
                    strength=2, status='active', owner=user)
                created_edges += 1
            except Exception:
                pass

    return JsonResponse({'ok': True, 'nodes_created': created_nodes,
                         'edges_created': created_edges})
