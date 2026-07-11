"""
views_social_pages.py — صفحه‌های مجزای بخش اجتماعی (V12)

/social/            هاب
/social/discover/   کشف آدم‌ها (پیشنهاد هوشمند + جستجو)
/social/requests/   درخواست‌های فالو و کانکشن
/social/share/      اشتراک‌گذاری راس/یال/دیتا با فالوئرها
+ گیت پابلیک: پرایوت‌ها کل این بخش رو ندارن.
"""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .models import (Follow, FriendRequest, Friendship, Information,
                     Node, ProfileMediaItem, Relationship)

User = get_user_model()


def _gate(request):
    """پرایوت‌ها بخش اجتماعی ندارن — تجربه‌ی «آفلاین»."""
    if not request.user.is_public:
        return render(request, 'social/locked.html')
    return None


def _my_followers(user):
    """لیست شیر من = کسایی که فالوم کردن."""
    return [f.follower for f in
            Follow.objects.filter(target=user).select_related('follower')]


# ═══════════════════════════════════════════════════════════════
#  کشف — پیشنهاد هوشمند
# ═══════════════════════════════════════════════════════════════

@login_required
def discover_view(request):
    g = _gate(request)
    if g:
        return g
    return render(request, 'social/discover.html')


@login_required
def suggest_users_api(request):
    """آدم‌های شبیه من که به دردم می‌خورن — رتبه‌بندی شباهت."""
    me = request.user
    my_friends = set(Friendship.objects.filter(user=me).values_list('friend_id', flat=True))
    my_media = set(ProfileMediaItem.objects.filter(user=me).values_list('title', flat=True))
    my_career = set((me.career or '').lower().split())
    my_city = (me.city or '').strip().lower()

    # دوستِ دوست‌ها (قوی‌ترین سیگنال)
    fof = {}
    if my_friends:
        for row in Friendship.objects.filter(user_id__in=my_friends) \
                                     .values_list('friend_id', flat=True):
            if row != me.id and row not in my_friends:
                fof[row] = fof.get(row, 0) + 1

    candidates = User.objects.filter(is_public=True, discoverable=True) \
                             .exclude(id=me.id).exclude(id__in=my_friends)[:200]
    scored = []
    for u in candidates:
        score, reasons = 0, []
        if fof.get(u.id):
            score += fof[u.id] * 4
            reasons.append(f'{fof[u.id]} کانکشن مشترک')
        if my_city and (u.city or '').strip().lower() == my_city:
            score += 3
            reasons.append(f'هم‌شهری ({u.city})')
        u_career = set((u.career or '').lower().split())
        if my_career & u_career:
            score += 2
            reasons.append('حوزه کاری مشابه')
        if my_media:
            common = my_media & set(
                ProfileMediaItem.objects.filter(user=u).values_list('title', flat=True))
            if common:
                score += min(len(common), 4) * 2
                reasons.append(f'سلیقه مشترک: {"، ".join(list(common)[:2])}')
        if u.bio:
            score += 1
        if score > 0:
            scored.append((score, u, reasons))
    scored.sort(key=lambda x: -x[0])

    # اگه سیگنالی نبود، تازه‌ترین پابلیک‌ها
    if not scored:
        scored = [(0, u, ['کاربر فعال شبکه']) for u in candidates[:12]]

    from .views_social import _user_card
    out = []
    for score, u, reasons in scored[:12]:
        card = _user_card(u, me)
        card['reasons'] = reasons
        out.append(card)
    return JsonResponse({'ok': True, 'users': out})


# ═══════════════════════════════════════════════════════════════
#  درخواست‌ها
# ═══════════════════════════════════════════════════════════════

@login_required
def requests_view(request):
    g = _gate(request)
    if g:
        return g
    incoming = FriendRequest.objects.filter(
        receiver=request.user, status='pending').select_related('sender')
    outgoing = FriendRequest.objects.filter(
        sender=request.user, status='pending').select_related('receiver')
    return render(request, 'social/requests.html', {
        'incoming': incoming, 'outgoing': outgoing,
    })


# ═══════════════════════════════════════════════════════════════
#  اشتراک‌گذاری — راس / یال / دیتا با فالوئرها
# ═══════════════════════════════════════════════════════════════

@login_required
def share_view(request):
    g = _gate(request)
    if g:
        return g
    me = request.user
    followers = _my_followers(me)
    nodes = Node.objects.filter(owner=me).exclude(id=me.root_node_id or -1) \
                        .order_by('username')[:300]
    rels = Relationship.objects.filter(owner=me) \
                               .select_related('source', 'target')[:300]
    infos = Information.objects.filter(node__owner=me).select_related('node')[:200]
    incoming = []
    try:
        from .models import SharedItem
        incoming = list(SharedItem.objects.filter(recipient=me)
                        .select_related('sender')[:30])
    except Exception:
        pass
    return render(request, 'social/share.html', {
        'followers': followers,
        'nodes': nodes,
        'rels': rels,
        'infos': infos,
        'incoming': incoming,
    })


def _apply_node_share(recipient, payload):
    node, created = Node.objects.get_or_create(
        owner=recipient, username=payload.get('username', '')[:100],
        defaults={
            'first_name': payload.get('first_name', '')[:100],
            'last_name': payload.get('last_name', '')[:100],
            'nickname': payload.get('nickname', '')[:100],
            'career': payload.get('career', '')[:200],
            'name': payload.get('name', '')[:200],
        })
    return node, created


def _apply_edge_share(recipient, payload):
    a, _ = _apply_node_share(recipient, payload.get('source') or {})
    b, _ = _apply_node_share(recipient, payload.get('target') or {})
    if a.id == b.id:
        return None, False
    exists = Relationship.objects.filter(
        Q(source=a, target=b) | Q(source=b, target=a), owner=recipient).exists()
    if exists:
        return None, False
    rel = Relationship.objects.create(
        owner=recipient, source=a, target=b,
        rel=(payload.get('rel') or '')[:100],
        strength=max(1, min(5, int(payload.get('strength') or 3))),
        status='active')
    return rel, True


def _apply_info_share(recipient, payload):
    node, _ = _apply_node_share(recipient, payload.get('node') or {})
    data = payload.get('data') or {}
    if not isinstance(data, dict):
        data = {'shared_note': str(data)[:500]}
    info = Information.objects.filter(node=node).first()
    if info and isinstance(info.data, dict):
        merged = dict(info.data)
        for k, v in data.items():
            if k not in merged:
                merged[k] = v
        info.data = merged
        info.save()
    else:
        Information.objects.create(node=node, visibility='private', data=data)
    return node, True


@login_required
@csrf_exempt
def share_send_api(request):
    """POST {item_type: node|edge|info, item_id, recipient_ids[]}
    فقط به فالوئرها. اگه گیرنده داشته باشه هیچی، نداشته باشه اضافه می‌شه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if not request.user.is_public:
        return JsonResponse({'error': 'بخش اجتماعی مخصوص پروفایل‌های پابلیکه'}, status=403)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    me = request.user
    item_type = body.get('item_type')
    if item_type not in ('node', 'edge', 'info'):
        return JsonResponse({'error': 'نوع آیتم نامعتبر'}, status=400)

    follower_ids = set(Follow.objects.filter(target=me).values_list('follower_id', flat=True))
    recipient_ids = {int(x) for x in body.get('recipient_ids') or [] if str(x).isdigit()}
    recipients = list(User.objects.filter(id__in=(follower_ids & recipient_ids)))
    if not recipients:
        return JsonResponse({'error': 'گیرنده‌ای انتخاب نشده — فقط به فالوئرهات می‌تونی شیر کنی'}, status=400)

    # ── ساخت payload از آیتم خودم ──
    def node_payload(n):
        return {'username': n.username, 'first_name': n.first_name,
                'last_name': n.last_name, 'nickname': n.nickname,
                'career': n.career, 'name': n.name}

    item_id = body.get('item_id')
    title = ''
    if item_type == 'node':
        try:
            n = Node.objects.get(pk=item_id, owner=me)
        except Node.DoesNotExist:
            return JsonResponse({'error': 'راس پیدا نشد'}, status=404)
        payload = node_payload(n)
        title = n.display_name()
    elif item_type == 'edge':
        try:
            r = Relationship.objects.select_related('source', 'target').get(pk=item_id, owner=me)
        except Relationship.DoesNotExist:
            return JsonResponse({'error': 'یال پیدا نشد'}, status=404)
        payload = {'source': node_payload(r.source), 'target': node_payload(r.target),
                   'rel': r.rel or '', 'strength': r.strength}
        title = f'{r.source.display_name()} ⟷ {r.target.display_name()}'
    else:
        try:
            info = Information.objects.select_related('node').get(pk=item_id, node__owner=me)
        except Information.DoesNotExist:
            return JsonResponse({'error': 'اطلاعات پیدا نشد'}, status=404)
        payload = {'node': node_payload(info.node),
                   'data': info.data if isinstance(info.data, dict) else {}}
        title = f'اطلاعات {info.node.display_name()}'

    # ── اعمال برای هر گیرنده + رکورد ──
    applied_count = 0
    from .views_social import _notify_social
    for rcp in recipients:
        applied = False
        try:
            if item_type == 'node':
                _, applied = _apply_node_share(rcp, payload)
            elif item_type == 'edge':
                _, applied = _apply_edge_share(rcp, payload)
            else:
                _, applied = _apply_info_share(rcp, payload)
        except Exception:
            applied = False
        try:
            from .models import SharedItem
            SharedItem.objects.create(sender=me, recipient=rcp, item_type=item_type,
                                      title=title[:240], payload=payload, applied=applied)
        except Exception:
            pass
        if applied:
            applied_count += 1
        _notify_social(rcp, f'{me.username} یه {"راس" if item_type == "node" else ("یال" if item_type == "edge" else "اطلاعات")} باهات شیر کرد: {title[:60]}', '/social/share/')

    return JsonResponse({'ok': True, 'recipients': len(recipients),
                         'applied': applied_count,
                         'skipped': len(recipients) - applied_count})
