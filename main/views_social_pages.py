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

from .models import (Follow, FriendRequest, Friendship, GiftBox, Information,
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
    my_media = set(ProfileMediaItem.objects.filter(user=me, is_public=True).values_list('title', flat=True))
    my_career = set((me.career or '').lower().split())
    my_city = (me.city or '').strip().lower()
    my_interests = {str(item).strip().lower() for item in (me.public_interests or []) if str(item).strip()}
    my_values = {str(item).strip().lower() for item in (me.public_values or []) if str(item).strip()}
    my_style_words = set((me.public_communication_style or '').lower().split())

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
                ProfileMediaItem.objects.filter(user=u, is_public=True).values_list('title', flat=True))
            if common:
                score += min(len(common), 4) * 2
                reasons.append(f'سلیقه مشترک: {"، ".join(list(common)[:2])}')
        public_interests = {
            str(item).strip().lower() for item in (u.public_interests or []) if str(item).strip()
        }
        common_interests = my_interests & public_interests
        if common_interests:
            score += min(len(common_interests), 3) * 3
            reasons.append(f'علاقه مشترک: {"، ".join(list(common_interests)[:2])}')
        public_values = {
            str(item).strip().lower() for item in (u.public_values or []) if str(item).strip()
        }
        common_values = my_values & public_values
        if common_values:
            score += min(len(common_values), 2) * 3
            reasons.append(f'ارزش مشترک: {"، ".join(list(common_values)[:2])}')
        their_style_words = set((u.public_communication_style or '').lower().split())
        if len(my_style_words & their_style_words) >= 2:
            score += 2
            reasons.append('سبک ارتباطی نزدیک')
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
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

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
            r = Relationship.objects.select_related('source', 'target').get(
                pk=item_id,
                owner=me,
                source__owner=me,
                target__owner=me,
            )
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


# ═══════════════════════════════════════════════════════════════
#  GiftBox — اشتراک‌گذاری گراف‌محور با مکعب سه‌بعدی
# ═══════════════════════════════════════════════════════════════

SHARE_TYPE_LABELS = {'node': '👤 راس', 'edge': '🔗 یال', 'data': '📊 دیتا'}

REACTION_DELTA = {
    'true':   +2.5,
    'accept':  0,
    'reject':  0,
    'false':  -2.5,
}

DEFAULT_FACES = [
    {'emo': '📦', 'lbl': 'فرستنده', 'ci': 0},
    {'emo': '👤', 'lbl': 'گیرنده',  'ci': 1},
    {'emo': '📊', 'lbl': 'داده',    'ci': 2},
    {'emo': '🔗', 'lbl': 'یال',     'ci': 3},
    {'emo': '⭐', 'lbl': 'اعتماد',  'ci': 4},
    {'emo': '📅', 'lbl': 'زمان',    'ci': 5},
]


def _node_snapshot(node):
    return {
        'username':     node.username,
        'first_name':   node.first_name,
        'last_name':    node.last_name,
        'nickname':     node.nickname,
        'career':       node.career,
        'display_name': node.display_name(),
    }


def _find_or_create_node(owner, snap):
    uname = (snap.get('username') or snap.get('display_name') or 'unknown').strip()
    node, created = Node.objects.get_or_create(
        owner=owner,
        username=uname,
        defaults={
            'first_name': snap.get('first_name', ''),
            'last_name':  snap.get('last_name', ''),
            'nickname':   snap.get('nickname', ''),
            'career':     snap.get('career', ''),
        }
    )
    return node, created


def _apply_graph_content(box, recipient):
    p     = box.payload or {}
    stype = box.share_type
    added = []

    if stype == 'node':
        node, created = _find_or_create_node(recipient, p)
        if created:
            added.append(f'راس: {node.display_name()}')

    elif stype == 'edge':
        src, sc = _find_or_create_node(recipient, p.get('source', {}))
        tgt, tc = _find_or_create_node(recipient, p.get('target', {}))
        if sc:
            added.append(f'راس: {src.display_name()}')
        if tc:
            added.append(f'راس: {tgt.display_name()}')
        exists = Relationship.objects.filter(owner=recipient, source=src, target=tgt).exists()
        if not exists:
            Relationship.objects.create(
                owner=recipient,
                source=src, target=tgt,
                rel=p.get('rel', ''),
                strength=p.get('strength', 3),
            )
            added.append(f'یال: {src.display_name()} ↔ {tgt.display_name()}')

    elif stype == 'data':
        node, created = _find_or_create_node(recipient, p.get('about', {}))
        if created:
            added.append(f'راس: {node.display_name()}')
        info_data = p.get('info_data') or {}
        if info_data:
            existing = Information.objects.filter(node=node).first()
            if existing and isinstance(existing.data, dict):
                merged = dict(existing.data)
                for k, v in info_data.items():
                    if k not in merged:
                        merged[k] = v
                existing.data = merged
                existing.save()
            else:
                Information.objects.create(node=node, visibility='private', data=info_data)
            added.append(f'دیتا درباره {node.display_name()}')

    return added


def _time_ago(dt):
    from django.utils import timezone
    diff = timezone.now() - dt
    s = int(diff.total_seconds())
    if s < 60:    return 'همین الان'
    if s < 3600:  return f'{s//60} دقیقه پیش'
    if s < 86400: return f'{s//3600} ساعت پیش'
    return f'{s//86400} روز پیش'


def _box_to_dict(box, me_id):
    rd    = box.reactions_dict()
    p     = box.payload or {}
    stype = box.share_type

    if stype == 'node':
        title    = p.get('display_name') or p.get('username', '?')
        subtitle = p.get('career', '')
    elif stype == 'edge':
        src   = (p.get('source') or {}).get('display_name', '?')
        tgt   = (p.get('target') or {}).get('display_name', '?')
        title    = f'{src} ↔ {tgt}'
        subtitle = p.get('rel', '')
    elif stype == 'data':
        about    = (p.get('about') or {}).get('display_name', '?')
        title    = f'دیتا درباره {about}'
        keys     = [k for k in (p.get('info_data') or {}) if not k.startswith('_')]
        subtitle = ' · '.join(keys[:4])
    else:
        title = subtitle = '?'

    return {
        'id':            box.id,
        'share_type':    stype,
        'type_label':    SHARE_TYPE_LABELS.get(stype, stype),
        'title':         title,
        'subtitle':      subtitle,
        'payload':       p,
        'cube_faces':    box.cube_faces or DEFAULT_FACES,
        'reactions':     rd,
        'my_reaction':   box.my_reaction,
        'content_added': box.content_added,
        'opened':        box.opened,
        'time_ago':      _time_ago(box.created_at),
        'sender_id':     box.sender_id,
        'sender_name':   box.sender.get_full_name() or box.sender.username,
        'sender_avatar': (box.sender.username or '?')[0].upper(),
        'sender_trust':  getattr(box.sender, 'trust_score', 80),
        'recipient_id':  box.recipient_id,
        'recipient_name': box.recipient.get_full_name() or box.recipient.username,
    }


@login_required
def gifbox_view(request):
    me = request.user

    inbox = GiftBox.objects.filter(recipient=me).select_related('sender', 'recipient')
    sent  = GiftBox.objects.filter(sender=me).select_related('sender', 'recipient')

    inbox_json = [_box_to_dict(b, me.id) for b in inbox]
    sent_json  = [_box_to_dict(b, me.id) for b in sent]

    follower_ids = set(Follow.objects.filter(target=me).values_list('follower_id', flat=True))
    friend_ids   = set(Friendship.objects.filter(user=me).values_list('friend_id', flat=True))
    recip_ids    = follower_ids | friend_ids
    recip_users  = User.objects.filter(id__in=recip_ids).exclude(id=me.id)
    recipients_json = [
        {'id': u.id, 'name': u.get_full_name() or u.username,
         'avatar': (u.username or '?')[0].upper(),
         'trust': getattr(u, 'trust_score', 80)}
        for u in recip_users
    ]

    my_nodes = Node.objects.filter(owner=me).order_by('username')
    nodes_json = [
        {'id': n.id, 'display_name': n.display_name(),
         'career': n.career or '', 'username': n.username}
        for n in my_nodes
    ]

    my_rels = Relationship.objects.filter(
        owner=me,
        source__owner=me,
        target__owner=me,
    ).select_related('source', 'target')
    edges_json = [
        {'id': r.id,
         'label': f'{r.source.display_name()} {r.emoji()} {r.target.display_name()}',
         'rel': r.rel or '', 'strength': r.strength,
         'source_name': r.source.display_name(),
         'target_name': r.target.display_name()}
        for r in my_rels
    ]

    my_infos = Information.objects.filter(node__owner=me).select_related('node')
    infos_json = [
        {'id': i.id,
         'about': i.node.display_name(),
         'about_username': i.node.username,
         'keys': [k for k in (i.data or {}) if not k.startswith('_')][:5]}
        for i in my_infos if i.data
    ]

    unread_count = inbox.filter(opened=False).count()
    all_user_ids = recip_ids | {me.id}
    trust_users  = list(User.objects.filter(id__in=all_user_ids).order_by('-trust_score'))

    from .templatetags.gifbox_tags import quota as trust_quota_fn
    trust_quota = trust_quota_fn(me.trust_score)

    return render(request, 'social/gifbox.html', {
        'inbox_json':      inbox_json,
        'sent_json':       sent_json,
        'recipients_json': recipients_json,
        'nodes_json':      nodes_json,
        'edges_json':      edges_json,
        'infos_json':      infos_json,
        'unread_count':    unread_count,
        'me':              me,
        'trust_quota':     trust_quota,
        'trust_users':     trust_users,
    })


@login_required
def gifbox_send_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    me           = request.user
    recipient_id = data.get('recipient_id')
    share_type   = data.get('share_type')
    source_id    = data.get('source_id')
    cube_faces   = data.get('cube_faces') or DEFAULT_FACES

    if share_type not in ('node', 'edge', 'data'):
        return JsonResponse({'error': 'نوع شیر نامعتبره'}, status=400)
    if (
        not isinstance(recipient_id, int) or isinstance(recipient_id, bool) or recipient_id < 1 or
        not isinstance(source_id, int) or isinstance(source_id, bool) or source_id < 1
    ):
        return JsonResponse({'error': 'شناسه نامعتبره'}, status=400)

    if (
        not isinstance(cube_faces, list) or len(cube_faces) != 6 or
        any(
            not isinstance(face, dict) or
            not isinstance(face.get('ci'), int) or isinstance(face.get('ci'), bool) or
            not 0 <= face['ci'] < 6 or
            not isinstance(face.get('emo'), str) or len(face['emo']) > 12 or
            not isinstance(face.get('lbl'), str) or len(face['lbl']) > 40
            for face in cube_faces
        )
    ):
        return JsonResponse({'error': 'پیکربندی مکعب نامعتبره'}, status=400)

    ts = getattr(me, 'trust_score', 80)
    if ts < 30:
        return JsonResponse({'error': 'امتیاز اعتماد شما مسدود شده'}, status=403)

    import datetime
    today_count = GiftBox.objects.filter(
        sender=me, created_at__date=datetime.date.today()
    ).count()
    limits = {30: 1, 45: 2, 60: 3, 75: 5}
    limit  = None if ts >= 90 else next(
        (v for thr, v in sorted(limits.items(), reverse=True) if ts >= thr), 1
    )
    if limit and today_count >= limit:
        return JsonResponse({'error': f'سهمیه امروز تموم شد ({limit}/روز)'}, status=403)

    try:
        if share_type == 'node':
            node    = Node.objects.get(pk=source_id, owner=me)
            payload = _node_snapshot(node)
        elif share_type == 'edge':
            rel     = Relationship.objects.select_related('source', 'target').get(
                pk=source_id,
                owner=me,
                source__owner=me,
                target__owner=me,
            )
            payload = {
                'source':   _node_snapshot(rel.source),
                'target':   _node_snapshot(rel.target),
                'rel':      rel.rel or '',
                'strength': rel.strength,
            }
        else:
            info    = Information.objects.select_related('node').get(pk=source_id, node__owner=me)
            payload = {
                'about':     _node_snapshot(info.node),
                'info_data': info.data if isinstance(info.data, dict) else {},
            }
    except (Node.DoesNotExist, Relationship.DoesNotExist, Information.DoesNotExist):
        return JsonResponse({'error': 'آیتم پیدا نشد'}, status=404)

    recipient = User.objects.filter(pk=recipient_id).first()
    if recipient is None:
        return JsonResponse({'error': 'گیرنده پیدا نشد'}, status=404)
    is_allowed_recipient = (
        recipient.id != me.id and (
            Follow.objects.filter(target=me, follower=recipient).exists() or
            Friendship.objects.filter(user=me, friend=recipient).exists()
        )
    )
    if not is_allowed_recipient:
        return JsonResponse({'error': 'گیرنده در ارتباطات مجاز شما نیست'}, status=403)

    box = GiftBox.objects.create(
        sender=me,
        recipient=recipient,
        share_type=share_type,
        payload=payload,
        cube_faces=cube_faces,
    )
    return JsonResponse({'ok': True, 'id': box.id})


@login_required
def gifbox_react_api(request, box_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    reaction = data.get('reaction')
    if reaction not in ('true', 'false', 'accept', 'reject'):
        return JsonResponse({'error': 'واکنش نامعتبر'}, status=400)

    try:
        box = GiftBox.objects.select_related('sender', 'recipient').get(
            pk=box_id, recipient=request.user
        )
    except GiftBox.DoesNotExist:
        return JsonResponse({'error': 'پیدا نشد'}, status=404)

    if box.my_reaction:
        return JsonResponse({'error': 'قبلاً واکنش دادی'}, status=400)

    box.my_reaction = reaction
    rd = box.reactions_dict()
    rd[reaction] = rd.get(reaction, 0) + 1
    box.reactions = rd

    added_items = []
    if reaction in ('true', 'accept') and not box.content_added:
        try:
            added_items = _apply_graph_content(box, request.user)
            box.content_added = True
        except Exception:
            pass

    box.save(update_fields=['my_reaction', 'reactions', 'content_added'])

    sender = box.sender
    delta  = REACTION_DELTA.get(reaction, 0)
    new_ts = max(0, min(100, int(round(getattr(sender, 'trust_score', 80) + delta))))
    sender.trust_score = new_ts
    sender.save(update_fields=['trust_score'])

    return JsonResponse({
        'ok':            True,
        'reactions':     rd,
        'new_trust':     new_ts,
        'added':         added_items,
        'content_added': box.content_added,
    })


@login_required
def gifbox_open_api(request, box_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        box = GiftBox.objects.get(pk=box_id, recipient=request.user)
        if not box.opened:
            box.opened = True
            box.save(update_fields=['opened'])
    except GiftBox.DoesNotExist:
        pass
    return JsonResponse({'ok': True})
