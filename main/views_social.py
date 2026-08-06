import json

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    ArtisticWork,
    ChatAnalysis,
    DirectMessage,
    Follow,
    FriendRequest,
    Friendship,
    Information,
    Node,
    ProfileMediaItem,
    Relationship,
    SocialPost,
)

User = get_user_model()


def _rate_limited(user, action, limit, window_seconds):
    """Small cache-backed throttle for social actions; returns a retry-after value."""
    key = f'anti-spam:{action}:{user.pk}:{int(timezone.now().timestamp() // window_seconds)}'
    if cache.add(key, 1, timeout=window_seconds):
        return None
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
        count = 1
    return window_seconds if count > limit else None


def _spam_error(retry_after):
    return JsonResponse(
        {'error': 'تعداد درخواست‌ها زیاد است؛ کمی بعد دوباره تلاش کن.', 'retry_after': retry_after},
        status=429,
    )


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except Exception:
        return {}


def _friend_ids(user):
    return set(Friendship.objects.filter(user=user).values_list('friend_id', flat=True))


def _user_card(user, viewer=None):
    card = {
        'id': user.id,
        'username': user.username,
        'name': (f'{user.first_name} {user.last_name}'.strip() or user.username),
        'bio': user.bio,
        'career': user.career,
        'city': user.city,
        'is_public': user.is_public,
        'followers_count': Follow.objects.filter(target=user).count(),
        'following_count': Follow.objects.filter(follower=user).count(),
        'connections_count': Friendship.objects.filter(user=user).count(),
    }
    if viewer and viewer.is_authenticated and viewer != user:
        card['is_friend'] = Friendship.objects.filter(user=viewer, friend=user).exists()
        card['is_connection'] = card['is_friend']
        card['is_following'] = Follow.objects.filter(follower=viewer, target=user).exists()
        card['request_sent'] = FriendRequest.objects.filter(
            sender=viewer, receiver=user, request_type='connection', status='pending'
        ).exists()
        card['follow_request_sent'] = FriendRequest.objects.filter(
            sender=viewer, receiver=user, request_type='follow', status='pending'
        ).exists()
        card['connection_request_sent'] = card['request_sent']
        card['request_received'] = FriendRequest.objects.filter(
            sender=user, receiver=viewer, request_type='connection', status='pending'
        ).exists()
    return card


def _ensure_friend_graph_link(user, friend):
    user_node, _ = Node.objects.get_or_create(
        owner=user,
        username=friend.username,
        defaults={
            'first_name': friend.first_name,
            'last_name': friend.last_name,
            'career': friend.career,
            'username_locked': True,
            'imported_from': friend if friend.is_public else None,
        },
    )
    if friend.root_node_id:
        root = user.root_node
        if root and root.id != user_node.id:
            rel, _ = Relationship.objects.get_or_create(
                owner=user,
                source=root,
                target=user_node,
                rel='دوست',
                defaults={'strength': 4, 'status': 'active'},
            )
            return user_node, rel
    return user_node, None


def _accept_request(friend_request):
    with transaction.atomic():
        friend_request.status = 'accepted'
        friend_request.responded_at = timezone.now()
        friend_request.save(update_fields=['status', 'responded_at'])

        a_node, a_rel = _ensure_friend_graph_link(friend_request.sender, friend_request.receiver)
        b_node, b_rel = _ensure_friend_graph_link(friend_request.receiver, friend_request.sender)

        Friendship.objects.update_or_create(
            user=friend_request.sender,
            friend=friend_request.receiver,
            defaults={'relationship': a_rel},
        )
        Friendship.objects.update_or_create(
            user=friend_request.receiver,
            friend=friend_request.sender,
            defaults={'relationship': b_rel},
        )
        Follow.objects.get_or_create(follower=friend_request.sender, target=friend_request.receiver)
        Follow.objects.get_or_create(follower=friend_request.receiver, target=friend_request.sender)
        return a_node, b_node


@login_required
def social_view(request):
    # V12: پرایوت‌ها بخش اجتماعی ندارن
    if not request.user.is_public:
        return render(request, 'social/locked.html')
    friends = Friendship.objects.filter(user=request.user).select_related('friend', 'relationship')
    friend_ids = list(friends.values_list('friend_id', flat=True))
    following = Follow.objects.filter(follower=request.user).select_related('target')
    audience = User.objects.filter(id__in=_audience_ids(request.user)).exclude(id=request.user.id).order_by('username')
    incoming = FriendRequest.objects.filter(receiver=request.user, status='pending').select_related('sender')
    outgoing = FriendRequest.objects.filter(sender=request.user, status='pending').select_related('receiver')
    infos = Information.objects.filter(node__owner=request.user).select_related('node').prefetch_related('shared_with')[:80]
    incoming_shared = Information.objects.filter(
        Q(visibility='selected', shared_with=request.user)
    ).exclude(node__owner=request.user).select_related('node', 'node__owner').distinct()[:40]
    following_ids = set(following.values_list('target_id', flat=True))
    feed_author_ids = following_ids | {request.user.id}
    posts = SocialPost.objects.filter(
        author_id__in=feed_author_ids,
        is_public=True,
        author__is_public=True,
    ).select_related('author')[:30]
    return render(request, 'social/social.html', {
        'friends': friends,
        'following': following,
        'audience': audience,
        'incoming': incoming,
        'outgoing': outgoing,
        'infos': infos,
        'incoming_shared': incoming_shared,
        'posts': posts,
    })


@login_required
def discover_api(request):
    q = (request.GET.get('q') or '').strip()
    # V12: فقط کاربرهای پابلیک که «قابل کشف» بودن رو روشن گذاشتن
    qs = User.objects.exclude(id=request.user.id).filter(
        is_public=True, discoverable=True)
    if q:
        qs = qs.filter(
            Q(username__icontains=q) |
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(city__icontains=q) |
            Q(career__icontains=q)
        )

    visible_ids = {u.id for u in qs[:40]}
    visible_ids |= _friend_ids(request.user)
    visible_ids |= set(FriendRequest.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user),
        status='pending',
    ).values_list('sender_id', flat=True))
    visible_ids |= set(FriendRequest.objects.filter(
        Q(sender=request.user) | Q(receiver=request.user),
        status='pending',
    ).values_list('receiver_id', flat=True))

    users = User.objects.filter(id__in=visible_ids).exclude(id=request.user.id)[:40]
    return JsonResponse({'ok': True, 'users': [_user_card(u, request.user) for u in users]})


@login_required
@csrf_exempt
def follow_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    retry_after = _rate_limited(request.user, 'follow', 10, 60 * 60)
    if retry_after:
        return _spam_error(retry_after)
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        return JsonResponse({'error': 'نمی‌توانی خودت را فالو کنی'}, status=400)
    if not target.is_public and not Friendship.objects.filter(user=request.user, friend=target).exists():
        FriendRequest.objects.get_or_create(
            sender=request.user,
            receiver=target,
            status='pending',
            defaults={'message': 'درخواست دنبال کردن / دوستی'},
        )
        return JsonResponse({'ok': True, 'request_sent': True, 'private': True})
    Follow.objects.get_or_create(follower=request.user, target=target)
    return JsonResponse({'ok': True, 'following': True})


@login_required
@csrf_exempt
def unfollow_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    Follow.objects.filter(follower=request.user, target_id=user_id).delete()
    return JsonResponse({'ok': True, 'following': False})


@login_required
@csrf_exempt
def friend_request_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    retry_after = _rate_limited(request.user, 'connection', 6, 60 * 60)
    if retry_after:
        return _spam_error(retry_after)
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        return JsonResponse({'error': 'نمی‌توانی به خودت درخواست بدهی'}, status=400)
    if Friendship.objects.filter(user=request.user, friend=target).exists():
        return JsonResponse({'ok': True, 'already_friend': True})

    reverse_req = FriendRequest.objects.filter(
        sender=target, receiver=request.user, status='pending'
    ).first()
    if reverse_req:
        _accept_request(reverse_req)
        return JsonResponse({'ok': True, 'accepted': True})

    body = _body(request)
    FriendRequest.objects.get_or_create(
        sender=request.user,
        receiver=target,
        status='pending',
        defaults={'message': (body.get('message') or '')[:240]},
    )
    return JsonResponse({'ok': True})


@login_required
@csrf_exempt
def friend_respond_api(request, request_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    fr = get_object_or_404(FriendRequest, pk=request_id, receiver=request.user, status='pending')
    action = _body(request).get('action')
    if action == 'accept':
        _accept_request(fr)
        return JsonResponse({'ok': True, 'accepted': True})
    fr.status = 'rejected'
    fr.responded_at = timezone.now()
    fr.save(update_fields=['status', 'responded_at'])
    return JsonResponse({'ok': True, 'rejected': True})


@login_required
def friends_api(request):
    friends = Friendship.objects.filter(user=request.user).select_related('friend')
    return JsonResponse({'ok': True, 'friends': [_user_card(f.friend, request.user) for f in friends]})


@login_required
def messages_api(request, user_id):
    friend = get_object_or_404(User, pk=user_id)
    if not Friendship.objects.filter(user=request.user, friend=friend).exists():
        return JsonResponse({'error': 'فقط با دوست‌هایت می‌توانی چت کنی'}, status=403)
    qs = DirectMessage.objects.filter(
        Q(sender=request.user, receiver=friend) |
        Q(sender=friend, receiver=request.user)
    ).select_related('sender').order_by('created_at')[:200]
    analysis = ChatAnalysis.objects.filter(user=request.user, friend=friend).first()
    return JsonResponse({
        'ok': True,
        'messages': [{
            'id': m.id,
            'mine': m.sender_id == request.user.id,
            'sender': m.sender.username,
            'content': m.content,
            'created_at': m.created_at.strftime('%Y-%m-%d %H:%M'),
        } for m in qs],
        'analysis': {
            'summary': analysis.summary,
            'mood': analysis.mood,
            'topics': analysis.topics,
            'signals': analysis.signals,
            'suggestions': analysis.suggestions,
        } if analysis else None,
    })


def _analysis_payload(analysis):
    if not analysis:
        return None
    return {
        'summary': analysis.summary,
        'mood': analysis.mood,
        'topics': analysis.topics,
        'signals': analysis.signals,
        'suggestions': analysis.suggestions,
    }


def _chat_analysis_for(user, friend):
    msgs = list(DirectMessage.objects.filter(
        Q(sender=user, receiver=friend) |
        Q(sender=friend, receiver=user)
    ).order_by('-created_at')[:80])
    msgs.reverse()
    if len(msgs) < 2:
        return None

    try:
        from .views_smart_features import _ai_client, _extract_json, _model
        client, api_key, _provider = _ai_client()
        if not api_key:
            raise RuntimeError('AI key is not configured')
        transcript = '\n'.join(
            f"{'من' if m.sender_id == user.id else friend.username}: {m.content[:500]}"
            for m in msgs
        )
        prompt = f"""این چت بین من و {friend.username} را برای FamilyGraph تحلیل کن.
هدف: شناخت عمیق «هر دو نفر» و «خودِ رابطه» از دل گفتگو —
شخصیت و حال‌وهوای هر طرف، دینامیک بینشون (کی پیش‌قدمه، لحن، تعادل)، موضوعات تکراری،
قول‌وقرارها، نیاز به پیگیری و تغییر حال‌وهوا.
فقط JSON معتبر بده:
{{"summary":"خلاصه کوتاه","mood":"حال‌وهوای رابطه",
  "person_read":"شناختی که از {friend.username} از این چت به دست میاد (۱-۲ جمله)",
  "my_read":"شناختی که از خودِ من از این چت به دست میاد (۱ جمله)",
  "relationship_read":"شناخت یال/دینامیک رابطه (۱-۲ جمله)",
  "topics":[],"signals":[],"suggestions":[],"followups":[]}}

چت:
{transcript}"""
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'تحلیل‌گر روابط هستی. فقط JSON معتبر بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=900,
        )
        data = _extract_json(resp.choices[0].message.content)
    except Exception:
        joined = ' '.join(m.content for m in msgs[-20:])
        data = {
            'summary': joined[:240] + ('...' if len(joined) > 240 else ''),
            'mood': 'تحلیل آفلاین',
            'topics': [],
            'signals': ['AI در دسترس نبود؛ خلاصه آفلاین ساخته شد.'],
            'suggestions': ['بعد از تنظیم کلید AI دوباره تحلیل کن.'],
            'followups': [],
        }

    # V12: خوانش شخص و رابطه هم وارد سیگنال‌ها بشه تا موتور شناخت بخوره
    extra_signals = []
    for k in ('person_read', 'relationship_read', 'my_read'):
        if data.get(k):
            extra_signals.append(str(data[k])[:250])
    analysis, _ = ChatAnalysis.objects.update_or_create(
        user=user,
        friend=friend,
        defaults={
            'summary': data.get('summary', ''),
            'mood': data.get('mood', ''),
            'topics': data.get('topics') or [],
            'signals': (data.get('signals') or []) + extra_signals,
            'suggestions': data.get('suggestions') or [],
            'raw': data,
        },
    )

    try:
        node = Node.objects.filter(owner=user, username=friend.username).first()
        followups = data.get('followups') or []
        if node and isinstance(followups, list):
            from .models import FollowUp
            for text in followups[:3]:
                text = str(text).strip()[:300]
                if text and not FollowUp.objects.filter(owner=user, node=node, text=text, done=False).exists():
                    FollowUp.objects.create(owner=user, node=node, text=text)
    except Exception:
        pass

    DirectMessage.objects.filter(id__in=[m.id for m in msgs], sender=user).update(analyzed=True)
    return analysis


@login_required
@csrf_exempt
def message_send_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    friend = get_object_or_404(User, pk=user_id)
    if not Friendship.objects.filter(user=request.user, friend=friend).exists():
        return JsonResponse({'error': 'فقط با دوست‌هایت می‌توانی چت کنی'}, status=403)
    content = (_body(request).get('content') or '').strip()
    if not content:
        return JsonResponse({'error': 'پیام خالی است'}, status=400)
    msg = DirectMessage.objects.create(sender=request.user, receiver=friend, content=content[:3000])
    analysis = _chat_analysis_for(request.user, friend)
    return JsonResponse({'ok': True, 'message': {
        'id': msg.id,
        'mine': True,
        'sender': request.user.username,
        'content': msg.content,
        'created_at': msg.created_at.strftime('%Y-%m-%d %H:%M'),
    }, 'analysis': _analysis_payload(analysis)})


@login_required
@csrf_exempt
def chat_analyze_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    friend = get_object_or_404(User, pk=user_id)
    if not Friendship.objects.filter(user=request.user, friend=friend).exists():
        return JsonResponse({'error': '??? ?? ??????? ???? ????? ???'}, status=403)
    analysis = _chat_analysis_for(request.user, friend)
    if not analysis:
        return JsonResponse({'error': '???? ?????? ????? ??? ???? ???? ???'}, status=400)
    return JsonResponse({'ok': True, 'analysis': _analysis_payload(analysis)})


@login_required
@csrf_exempt
def information_share_api(request, info_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    info = get_object_or_404(Information, pk=info_id, node__owner=request.user)
    body = _body(request)
    visibility = body.get('visibility')
    if visibility not in {'private', 'public', 'friends', 'selected'}:
        return JsonResponse({'error': 'سطح اشتراک‌گذاری نامعتبر است'}, status=400)
    info.visibility = visibility
    info.save(update_fields=['visibility'])
    info.shared_with.clear()
    if visibility == 'selected':
        allowed = _friend_ids(request.user)
        selected = [int(x) for x in body.get('friend_ids') or [] if str(x).isdigit()]
        info.shared_with.add(*User.objects.filter(id__in=(allowed & set(selected))))
    return JsonResponse({'ok': True})


# Final v2 API overrides. These intentionally appear after legacy definitions.
@login_required
@csrf_exempt
def follow_api(request, user_id):
    """V12: فالو نیازمند تایید طرفه، مگه «تایید خودکار فالو»ش روشن باشه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        return JsonResponse({'error': 'نمی‌توانی خودت را فالو کنی'}, status=400)
    already_connected = Friendship.objects.filter(user=request.user, friend=target).exists()
    if getattr(target, 'auto_accept_follow', False) or already_connected:
        Follow.objects.get_or_create(follower=request.user, target=target)
        _notify_social(target, f'{request.user.username} فالوت کرد.', '/social/')
        return JsonResponse({'ok': True, 'following': True})
    FriendRequest.objects.get_or_create(
        sender=request.user, receiver=target, request_type='follow', status='pending',
        defaults={'message': 'درخواست فالو'},
    )
    _notify_social(target, f'{request.user.username} درخواست فالو داده.', '/social/requests/')
    return JsonResponse({'ok': True, 'request_sent': True})


@login_required
@csrf_exempt
def friend_request_api(request, user_id):
    """V12: کانکشن نیازمند تاییده، مگه «تایید خودکار کانکشن» طرف روشن باشه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        return JsonResponse({'error': 'نمی‌توانی به خودت connection بدهی'}, status=400)
    if Friendship.objects.filter(user=request.user, friend=target).exists():
        return JsonResponse({'ok': True, 'already_connected': True})
    reverse_req = FriendRequest.objects.filter(sender=target, receiver=request.user, request_type='connection', status='pending').first()
    if reverse_req:
        _accept_connection_request(reverse_req)
        return JsonResponse({'ok': True, 'accepted': True})
    req, _created = FriendRequest.objects.get_or_create(
        sender=request.user, receiver=target, request_type='connection', status='pending',
        defaults={'message': 'درخواست connection'},
    )
    if getattr(target, 'auto_accept_connection', False):
        _accept_connection_request(req)
        return JsonResponse({'ok': True, 'accepted': True, 'auto': True})
    _notify_social(target, f'{request.user.username} درخواست connection داده.', '/social/requests/')
    return JsonResponse({'ok': True, 'request_sent': True})


@login_required
@csrf_exempt
def friend_respond_api(request, request_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    req = get_object_or_404(FriendRequest, pk=request_id, receiver=request.user, status='pending')
    action = _body(request).get('action')
    if action == 'accept':
        if req.request_type == 'follow':
            _accept_follow_request(req)
        else:
            _accept_connection_request(req)
        return JsonResponse({'ok': True, 'accepted': True, 'type': req.request_type})
    req.status = 'rejected'
    req.responded_at = timezone.now()
    req.save(update_fields=['status', 'responded_at'])
    _notify_social(req.sender, f'{request.user.username} درخواست {req.request_type} را رد کرد.', '/social/')
    return JsonResponse({'ok': True, 'rejected': True})


@login_required
@csrf_exempt
def information_share_api(request, info_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    info = get_object_or_404(Information, pk=info_id, node__owner=request.user)
    body = _body(request)
    selected = {int(x) for x in body.get('friend_ids') or [] if str(x).isdigit()}
    allowed = _audience_ids(request.user)
    info.visibility = 'selected' if selected else 'private'
    info.save(update_fields=['visibility'])
    info.shared_with.clear()
    info.shared_with.add(*User.objects.filter(id__in=(allowed & selected)))
    return JsonResponse({'ok': True, 'visibility': info.visibility})


@login_required
def messages_api(request, user_id):
    friend = get_object_or_404(User, pk=user_id)
    if not Friendship.objects.filter(user=request.user, friend=friend).exists():
        return JsonResponse({'error': 'فقط با connectionها می‌توانی چت کنی'}, status=403)

    qs = DirectMessage.objects.filter(
        Q(sender=request.user, receiver=friend) |
        Q(sender=friend, receiver=request.user)
    ).select_related('sender', 'reply_to').order_by('created_at')[:240]

    now = timezone.now()
    DirectMessage.objects.filter(
        sender=friend,
        receiver=request.user,
        delivered_at__isnull=True,
    ).update(delivered_at=now)

    if request.GET.get('mark_read') == '1':
        DirectMessage.objects.filter(
            sender=friend,
            receiver=request.user,
            read_at__isnull=True,
        ).update(read_at=now, delivered_at=now)

    analysis = ChatAnalysis.objects.filter(user=request.user, friend=friend).first()
    return JsonResponse({
        'ok': True,
        'messages': [{
            'id': m.id,
            'mine': m.sender_id == request.user.id,
            'sender': m.sender.username,
            'content': m.content,
            'created_at': m.created_at.strftime('%Y-%m-%d %H:%M'),
            'delivered': bool(m.delivered_at),
            'read': bool(m.read_at),
            'edited': bool(m.edited_at),
            'reply_to': {
                'id': m.reply_to_id,
                'content': m.reply_to.content[:120],
                'sender': m.reply_to.sender.username,
            } if m.reply_to_id else None,
        } for m in qs],
        'analysis': _analysis_payload(analysis),
    })


@login_required
@csrf_exempt
def message_send_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    friend = get_object_or_404(User, pk=user_id)
    if not Friendship.objects.filter(user=request.user, friend=friend).exists():
        return JsonResponse({'error': 'فقط با connectionها می‌توانی چت کنی'}, status=403)
    # V12: سیاست چت طرف مقابل
    if getattr(friend, 'chat_policy', 'connections') == 'nobody':
        return JsonResponse({'error': 'این کاربر دریافت پیام رو بسته'}, status=403)

    body = _body(request)
    content = (body.get('content') or '').strip()
    if not content:
        return JsonResponse({'error': 'پیام خالی است'}, status=400)

    reply_to = None
    reply_id = body.get('reply_to')
    if str(reply_id).isdigit():
        reply_to = DirectMessage.objects.filter(
            id=int(reply_id),
        ).filter(
            Q(sender=request.user, receiver=friend) |
            Q(sender=friend, receiver=request.user)
        ).first()

    msg = DirectMessage.objects.create(
        sender=request.user,
        receiver=friend,
        content=content[:3000],
        reply_to=reply_to,
    )

    try:
        _chat_analysis_for(request.user, friend)
        _chat_analysis_for(friend, request.user)
    except Exception:
        pass

    _notify_social(friend, f'{request.user.username} پیام جدید فرستاد.', '/social/chat/')
    return JsonResponse({'ok': True, 'message': {
        'id': msg.id,
        'mine': True,
        'sender': request.user.username,
        'content': msg.content,
        'created_at': msg.created_at.strftime('%Y-%m-%d %H:%M'),
        'delivered': False,
        'read': False,
        'edited': False,
        'reply_to': {
            'id': reply_to.id,
            'content': reply_to.content[:120],
            'sender': reply_to.sender.username,
        } if reply_to else None,
    }})


@login_required
@csrf_exempt
def typing_api(request, user_id):
    friend = get_object_or_404(User, pk=user_id)
    if not Friendship.objects.filter(user=request.user, friend=friend).exists():
        return JsonResponse({'error': 'forbidden'}, status=403)
    from django.core.cache import cache
    if request.method == 'POST':
        cache.set(f'typing:{request.user.id}:{friend.id}', True, 8)
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': True, 'typing': bool(cache.get(f'typing:{friend.id}:{request.user.id}'))})


@login_required
def chat_unread_api(request):
    rows = DirectMessage.objects.filter(receiver=request.user, read_at__isnull=True).values_list('sender_id', flat=True)
    counts = {}
    for sender_id in rows:
        counts[str(sender_id)] = counts.get(str(sender_id), 0) + 1
    return JsonResponse({'ok': True, 'counts': counts, 'total': sum(counts.values())})


# --- Social v2 overrides: Follow != Connection ---
def _notify_social(user, message, link='/social/'):
    try:
        from .models import Notification
        Notification.objects.create(user=user, notif_type='sync', message=message, link=link)
    except Exception:
        pass


def _audience_ids(user):
    return set(Follow.objects.filter(follower=user).values_list('target_id', flat=True)) | _friend_ids(user)


def _merge_graphs_for_connection(a, b):
    def copy(src_user, dst_user):
        node_map = {}
        for src in Node.objects.filter(owner=src_user):
            dst, _ = Node.objects.get_or_create(
                owner=dst_user,
                username=src.username,
                defaults={
                    'first_name': src.first_name,
                    'last_name': src.last_name,
                    'nickname': src.nickname,
                    'name': src.name,
                    'career': src.career,
                    'phone_number': src.phone_number,
                    'group': src.group,
                    'is_public': src.is_public,
                    'username_locked': True,
                    'imported_from': src_user if src_user.is_public else None,
                },
            )
            node_map[src.id] = dst
        for rel in Relationship.objects.filter(owner=src_user).select_related('source', 'target'):
            source = node_map.get(rel.source_id)
            target = node_map.get(rel.target_id)
            if source and target and source.id != target.id:
                Relationship.objects.get_or_create(
                    owner=dst_user, source=source, target=target, rel=rel.rel,
                    defaults={'strength': rel.strength, 'status': rel.status, 'met_at': rel.met_at, 'is_public': rel.is_public},
                )
    copy(a, b)
    copy(b, a)


def _ensure_connection_edge(owner, other_user):
    """V12: راس طرف مقابل + یال با نام اولیه‌ی «سایت» (بعداً خودش تغییرش می‌ده)."""
    other_node, _ = Node.objects.get_or_create(
        owner=owner,
        username=other_user.username,
        defaults={
            'first_name': other_user.first_name,
            'last_name': other_user.last_name,
            'career': other_user.career,
            'username_locked': True,
            'imported_from': other_user if other_user.is_public else None,
        },
    )
    if owner.root_node and owner.root_node_id != other_node.id:
        rel, _ = Relationship.objects.get_or_create(
            owner=owner, source=owner.root_node, target=other_node, rel='سایت',
            defaults={'strength': 3, 'status': 'active'},
        )
        return rel
    return None


def _accept_follow_request(req):
    with transaction.atomic():
        req.status = 'accepted'
        req.responded_at = timezone.now()
        req.save(update_fields=['status', 'responded_at'])
        Follow.objects.get_or_create(follower=req.sender, target=req.receiver)
        _notify_social(req.sender, f'{req.receiver.username} درخواست follow را قبول کرد.', '/social/')


def _accept_connection_request(req):
    # V12: دیگه merge کامل گراف انجام نمی‌شه (حریم خصوصی!) —
    # فقط راس همدیگه + یال «سایت» + فالو و دوستی متقابل.
    with transaction.atomic():
        req.status = 'accepted'
        req.responded_at = timezone.now()
        req.save(update_fields=['status', 'responded_at'])
        rel_a = _ensure_connection_edge(req.sender, req.receiver)
        rel_b = _ensure_connection_edge(req.receiver, req.sender)
        Friendship.objects.update_or_create(user=req.sender, friend=req.receiver, defaults={'relationship': rel_a})
        Friendship.objects.update_or_create(user=req.receiver, friend=req.sender, defaults={'relationship': rel_b})
        Follow.objects.get_or_create(follower=req.sender, target=req.receiver)
        Follow.objects.get_or_create(follower=req.receiver, target=req.sender)
        _notify_social(req.sender, f'{req.receiver.username} درخواست connection را قبول کرد.', '/social/chat/')


def _user_card(user, viewer=None):
    card = {
        'id': user.id,
        'username': user.username,
        'name': (f'{user.first_name} {user.last_name}'.strip() or user.username),
        'bio': user.bio,
        'career': user.career,
        'city': user.city,
        'is_public': user.is_public,
        'followers_count': Follow.objects.filter(target=user).count(),
        'following_count': Follow.objects.filter(follower=user).count(),
        'connections_count': Friendship.objects.filter(user=user).count(),
    }
    if viewer and viewer.is_authenticated and viewer != user:
        connected = Friendship.objects.filter(user=viewer, friend=user).exists()
        card.update({
            'is_friend': connected,
            'is_connection': connected,
            'is_following': Follow.objects.filter(follower=viewer, target=user).exists(),
            'connection_request_sent': FriendRequest.objects.filter(sender=viewer, receiver=user, request_type='connection', status='pending').exists(),
            'connection_request_received': FriendRequest.objects.filter(sender=user, receiver=viewer, request_type='connection', status='pending').exists(),
            'follow_request_sent': FriendRequest.objects.filter(sender=viewer, receiver=user, request_type='follow', status='pending').exists(),
        })
        card['request_sent'] = card['connection_request_sent']
        card['request_received'] = card['connection_request_received']
    return card


def _work_analysis(kind, title, creator=''):
    label = {'book': 'کتاب', 'movie': 'فیلم', 'series': 'سریال', 'music': 'موسیقی'}.get(kind, 'اثر')
    base = {
        'summary': f'{label} «{title}» به عنوان یک سیگنال فرهنگی برای شناخت سلیقه، ارزش‌ها، تخیل و حساسیت‌های فرد استفاده می‌شود.',
        'personality_signals': [
            'اگر امتیاز بالا باشد، این اثر احتمالا با جهان‌بینی یا نیازهای عاطفی فرد هم‌راستاست.',
            'اگر امتیاز پایین باشد، تضاد فرد با مضمون یا سبک اثر می‌تواند برای شناخت مرزهای سلیقه‌ای او مهم باشد.',
        ],
        'relationship_signals': [
            'شباهت یا تفاوت در واکنش به این اثر می‌تواند موضوع گفتگو، نزدیکی یا فاصله فرهنگی بین دو نفر باشد.',
        ],
    }
    try:
        from .views_smart_features import _ai_client, _extract_json, _model
        client, api_key, _provider = _ai_client()
        if not api_key:
            return base
        prompt = f"""برای FamilyGraph این اثر را تحلیل کن: نوع={label}، عنوان={title}، سازنده={creator}.
هدف اپ شناخت شخصیت فرد و واقعیت رابطه‌هاست. فقط JSON بده:
{{"summary":"","personality_signals":[],"relationship_signals":[],"themes":[]}}"""
        resp = client.chat.completions.create(
            model=_model(),
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=700,
        )
        data = _extract_json(resp.choices[0].message.content)
        return data if isinstance(data, dict) else base
    except Exception:
        return base


def _fetch_cover_url(kind, title, creator=''):
    """جلد واقعی اثر — زنجیره‌ی چند منبع بدون کلید:
    کتاب: Google Books → OpenLibrary → ویکی‌پدیا fa → en
    سریال: TVMaze → iTunes → ویکی‌پدیا
    فیلم/موسیقی: iTunes → ویکی‌پدیا
    هر کدوم که از ایران در دسترس بود، جواب می‌ده؛ نشد → None بی‌صدا."""
    import urllib.request
    import urllib.parse
    import json as _json

    def _get(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'FamilyGraph/1.0'})
        with urllib.request.urlopen(req, timeout=7) as r:
            return _json.loads(r.read().decode('utf-8'))

    quote = urllib.parse.quote
    q = title if not creator else f'{title} {creator}'
    has_fa = any('؀' <= ch <= 'ۿ' for ch in title)

    # جستجوی ویکی‌پدیا (fuzzy + عکس در یک درخواست) — مطمئن‌ترین راه برای آثار فارسی
    wikis = ['wsearch_fa', 'wsearch_en'] if has_fa else ['wsearch_en', 'wsearch_fa']
    chain = {
        'book':   wikis + ['openlib', 'gbooks'],
        'series': wikis + ['tvmaze', 'itunes'],
        'movie':  wikis + ['itunes'],
        'music':  ['itunes'] + wikis,
    }.get(kind, wikis)

    errors = []
    for src in chain:
        try:
            if src in ('wsearch_fa', 'wsearch_en'):
                lang = 'fa' if src == 'wsearch_fa' else 'en'
                d = _get(f'https://{lang}.wikipedia.org/w/api.php?action=query'
                         f'&generator=search&gsrsearch={quote(title.strip())}&gsrlimit=4'
                         f'&prop=pageimages&piprop=thumbnail&pithumbsize=400&format=json')
                pages = ((d.get('query') or {}).get('pages') or {})
                # صفحه‌ها به ترتیب رتبه‌ی جستجو (index)
                for _, pg in sorted(pages.items(), key=lambda kv: kv[1].get('index', 99)):
                    img = (pg.get('thumbnail') or {}).get('source')
                    if img:
                        return img, errors
            elif src == 'gbooks':
                d = _get('https://www.googleapis.com/books/v1/volumes?maxResults=3&q=' + quote(q))
                for item in d.get('items') or []:
                    links = (item.get('volumeInfo') or {}).get('imageLinks') or {}
                    img = links.get('thumbnail') or links.get('smallThumbnail')
                    if img:
                        return (img.replace('http://', 'https://')
                                   .replace('&edge=curl', '')), errors
            elif src == 'openlib':
                d = _get('https://openlibrary.org/search.json?limit=3&q=' + quote(q))
                for doc in d.get('docs') or []:
                    if doc.get('cover_i'):
                        return f'https://covers.openlibrary.org/b/id/{doc["cover_i"]}-M.jpg', errors
            elif src == 'tvmaze':
                d = _get('https://api.tvmaze.com/singlesearch/shows?q=' + quote(title))
                img = (d.get('image') or {}).get('medium')
                if img:
                    return img.replace('http://', 'https://'), errors
            elif src == 'itunes':
                media = {'movie': 'movie', 'series': 'tvShow', 'music': 'music'}.get(kind, 'all')
                d = _get('https://itunes.apple.com/search?limit=3&media=' + media
                         + '&term=' + quote(q))
                for item in d.get('results') or []:
                    img = item.get('artworkUrl100')
                    if img:
                        return img.replace('100x100', '400x400'), errors
            errors.append(f'{src}: نتیجه نداشت')
        except Exception as e:
            errors.append(f'{src}: {type(e).__name__}')
            continue
    return None, errors


def _get_or_create_work(kind, title, creator=''):
    work, created = ArtisticWork.objects.get_or_create(
        kind=kind,
        title=title,
        defaults={'creator': creator, 'analysis': _work_analysis(kind, title, creator)},
    )
    changed = False
    if creator and not work.creator:
        work.creator = creator
        changed = True
    if not work.analysis:
        work.analysis = _work_analysis(kind, work.title, work.creator)
        changed = True
    # V11: جلد واقعی — فقط برای اثر تازه (که صفحه کند نشه؛ قدیمی‌ها lazy از UI پر می‌شن)
    if created and not work.cover_url:
        cu, _errs = _fetch_cover_url(kind, title, creator)
        if cu:
            work.cover_url = cu[:200]
            changed = True
    if changed:
        work.save()
    return work


@login_required
@csrf_exempt
def work_cover_api(request, work_id):
    """POST → اگه اثر جلد نداره، از اینترنت پیدا کن و ذخیره کن. lazy از UI صدا زده می‌شه.
    اگه پیدا نشد، دلیل هر منبع رو هم برمی‌گردونه که دیگه معما نمونه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    work = get_object_or_404(ArtisticWork, pk=work_id)
    errors = []
    if not work.cover_url:
        cu, errors = _fetch_cover_url(work.kind, work.title, work.creator)
        if cu:
            work.cover_url = cu[:200]
            work.save(update_fields=['cover_url'])
    return JsonResponse({'ok': True, 'cover_url': work.cover_url or None,
                         'tried': errors})


@login_required
def chat_view(request):
    # V12: پرایوت‌ها بخش اجتماعی ندارن
    if not request.user.is_public:
        return render(request, 'social/locked.html')
    connections = Friendship.objects.filter(user=request.user).select_related('friend').order_by('friend__username')
    selected_id = request.GET.get('u') or ''
    return render(request, 'social/chat.html', {'connections': connections, 'selected_id': selected_id})


@login_required
def public_profile_view(request, username):
    profile = get_object_or_404(User, username=username)
    private_locked = bool(profile != request.user and not profile.is_public and not Friendship.objects.filter(user=request.user, friend=profile).exists())
    cover_presets = [
        {'id': 'aurora', 'name': 'Aurora', 'css': 'linear-gradient(135deg,#0f172a 0%,#4f46e5 42%,#06b6d4 100%)'},
        {'id': 'forest', 'name': 'Forest', 'css': 'linear-gradient(135deg,#052e2b 0%,#16a34a 48%,#facc15 100%)'},
        {'id': 'rose', 'name': 'Rose', 'css': 'linear-gradient(135deg,#3b0764 0%,#e11d48 54%,#fb7185 100%)'},
        {'id': 'graphite', 'name': 'Graphite', 'css': 'linear-gradient(135deg,#111827 0%,#334155 52%,#94a3b8 100%)'},
        {'id': 'sunset', 'name': 'Sunset', 'css': 'linear-gradient(135deg,#312e81 0%,#f97316 50%,#fde68a 100%)'},
    ]
    preset_map = {item['id']: item['css'] for item in cover_presets}
    cover_css = preset_map.get(profile.cover_preset or 'aurora', cover_presets[0]['css'])
    # V11: برای هر نوع اثر — ۳ تا از بهترین‌ها + ۳ تا از آخرین‌ها (با نمره و توصیف)
    media_sections = []
    kind_meta = [('book', 'کتاب‌ها', '📚'), ('movie', 'فیلم‌ها', '🎬'),
                 ('series', 'سریال‌ها', '📺'), ('music', 'موسیقی', '🎵')]
    for kind, label, icon in kind_meta:
        qs = ProfileMediaItem.objects.filter(user=profile, kind=kind)
        if profile != request.user:
            qs = qs.filter(is_public=True)
        best = list(qs.filter(rating__gt=0).order_by('-rating', '-completed_on', '-created_at')[:3])
        latest = list(qs[:3])
        if best or latest:
            media_sections.append({'kind': kind, 'label': label, 'icon': icon,
                                   'best': best, 'latest': latest})

    # V11: نودِ معادل این کاربر توی گراف بیننده — برای بخش «شناخت»
    persona_node_id = None
    try:
        if profile == request.user and request.user.root_node_id:
            persona_node_id = request.user.root_node_id
        else:
            n = Node.objects.filter(owner=request.user, username=profile.username).first()
            if n:
                persona_node_id = n.id
    except Exception:
        pass

    return render(request, 'social/profile.html', {
        'profile_user': profile,
        'card': _user_card(profile, request.user),
        'private_locked': private_locked,
        'cover_presets': cover_presets,
        'cover_css': cover_css,
        'media_sections': media_sections,
        'persona_node_id': persona_node_id,
        # backward-compat (اگه جایی از تمپلیت قدیمی مونده باشه)
        'latest_books': ProfileMediaItem.objects.filter(user=profile, kind='book')[:3],
        'latest_movies': ProfileMediaItem.objects.filter(user=profile, kind='movie')[:3],
        'latest_series': ProfileMediaItem.objects.filter(user=profile, kind='series')[:3],
        'posts': SocialPost.objects.filter(
            author=profile,
            is_public=True,
        )[:20] if profile != request.user else SocialPost.objects.filter(author=profile)[:20],
    })


@login_required
def profile_edit_view(request):
    from django.contrib import messages
    from datetime import date
    user = request.user
    if request.method == 'POST':
        action = request.POST.get('action', 'profile')
        if action == 'profile':
            user.first_name = request.POST.get('first_name', '').strip()
            user.last_name = request.POST.get('last_name', '').strip()
            user.bio = request.POST.get('bio', '').strip()
            user.career = request.POST.get('career', '').strip()
            user.city = request.POST.get('city', '').strip()
            user.country = request.POST.get('country', '').strip()
            user.public_interests = _split_public_items(request.POST.get('public_interests', ''))
            user.public_values = _split_public_items(request.POST.get('public_values', ''))
            user.public_communication_style = request.POST.get(
                'public_communication_style', ''
            ).strip()[:280]
            user.is_public = request.POST.get('is_public', '') == 'on'
            bd_raw = request.POST.get('birth_date', '').strip()
            if bd_raw:
                try:
                    user.birth_date = date.fromisoformat(bd_raw)
                except ValueError:
                    messages.error(request, 'فرمت تاریخ تولد درست نیست.')
                    return redirect('profile_edit')
            if request.FILES.get('avatar'):
                user.avatar = request.FILES['avatar']
            user.save()
            self_node = Node.objects.filter(owner=user, username=user.username).first()
            if self_node:
                self_node.first_name = user.first_name
                self_node.last_name = user.last_name
                self_node.career = user.career
                self_node.nickname = request.POST.get('nickname', '').strip()
                self_node.phone_number = request.POST.get('phone_number', '').strip()
                if user.birth_date:
                    self_node.birth_day = user.birth_date
                self_node.save()
            messages.success(request, 'اطلاعات پروفایل ذخیره شد.')
        elif action == 'root_node':
            root_id = request.POST.get('root_node_id', '').strip()
            user.root_node = Node.objects.filter(pk=root_id, owner=user).first() if root_id else None
            user.save(update_fields=['root_node'])
            messages.success(request, 'نود اصلی ذخیره شد.')
        elif action == 'password':
            old_pw = request.POST.get('old_password', '')
            new_pw = request.POST.get('new_password', '')
            new_pw2 = request.POST.get('new_password2', '')
            if not user.check_password(old_pw):
                messages.error(request, 'رمز فعلی اشتباه است.')
            elif new_pw != new_pw2:
                messages.error(request, 'تکرار رمز جدید یکی نیست.')
            elif len(new_pw) < 8:
                messages.error(request, 'رمز جدید باید حداقل ۸ کاراکتر باشد.')
            else:
                from django.contrib.auth import login
                user.set_password(new_pw)
                user.save()
                login(request, user)
                messages.success(request, 'رمز عبور تغییر کرد.')
        elif action == 'privacy':
            # V12: تنظیمات شبکه اجتماعی
            user.discoverable           = request.POST.get('discoverable', '') == 'on'
            user.auto_accept_follow     = request.POST.get('auto_accept_follow', '') == 'on'
            user.auto_accept_connection = request.POST.get('auto_accept_connection', '') == 'on'
            cp = request.POST.get('chat_policy', 'connections')
            user.chat_policy = cp if cp in ('connections', 'nobody') else 'connections'
            user.save(update_fields=['discoverable', 'auto_accept_follow',
                                     'auto_accept_connection', 'chat_policy'])
            messages.success(request, 'تنظیمات شبکه ذخیره شد.')
        elif action == 'ai_privacy':
            user.ai_extraction_enabled = request.POST.get('ai_extraction_enabled') == 'on'
            user.ai_journal_enabled = request.POST.get('ai_journal_enabled') == 'on'
            user.ai_checkin_enabled = request.POST.get('ai_checkin_enabled') == 'on'
            user.ai_chat_enabled = request.POST.get('ai_chat_enabled') == 'on'
            user.save(update_fields=['ai_extraction_enabled', 'ai_journal_enabled',
                                     'ai_checkin_enabled', 'ai_chat_enabled'])
            messages.success(request, 'کنترل‌های حافظه و استخراج AI ذخیره شد.')
        elif action == 'media':
            title = request.POST.get('title', '').strip()
            kind = request.POST.get('kind', 'book')
            if title and kind in {'book', 'movie', 'series', 'music'}:
                completed_on = request.POST.get('completed_on') or None
                creator = request.POST.get('creator', '').strip()
                status = request.POST.get('status', 'completed')
                if status not in {'completed', 'current', 'planned'}:
                    status = 'completed'
                work = _get_or_create_work(kind, title, creator)
                ProfileMediaItem.objects.update_or_create(
                    user=user, kind=kind, title=title,
                    defaults={
                        'work': work,
                        'creator': creator or work.creator,
                        'rating': request.POST.get('rating') or 0,
                        'completed_on': completed_on,
                        'status': status,
                        'is_public': request.POST.get('is_public', '') == 'on',
                        'source': 'manual',
                        'notes': request.POST.get('notes', '').strip(),
                    },
                )
                messages.success(request, 'اثر به پروفایل اضافه شد.')
        elif action == 'post':
            body = request.POST.get('body', '').strip()[:1200]
            if not user.is_public:
                messages.error(request, 'برای انتشار پست، ابتدا پروفایلت را پابلیک کن.')
            elif body:
                SocialPost.objects.create(
                    author=user,
                    body=body,
                    image=request.FILES.get('post_image'),
                    is_public=True,
                )
                messages.success(request, 'پست عمومی منتشر شد.')
        elif action == 'delete_media':
            ProfileMediaItem.objects.filter(user=user, id=request.POST.get('media_id')).delete()
            messages.success(request, 'اثر حذف شد.')
        return redirect('profile_edit')

    cover_presets = [
        {'id': 'aurora', 'name': 'Aurora', 'css': 'linear-gradient(135deg,#0f172a 0%,#4f46e5 42%,#06b6d4 100%)'},
        {'id': 'forest', 'name': 'Forest', 'css': 'linear-gradient(135deg,#052e2b 0%,#16a34a 48%,#facc15 100%)'},
        {'id': 'rose', 'name': 'Rose', 'css': 'linear-gradient(135deg,#3b0764 0%,#e11d48 54%,#fb7185 100%)'},
        {'id': 'graphite', 'name': 'Graphite', 'css': 'linear-gradient(135deg,#111827 0%,#334155 52%,#94a3b8 100%)'},
        {'id': 'sunset', 'name': 'Sunset', 'css': 'linear-gradient(135deg,#312e81 0%,#f97316 50%,#fde68a 100%)'},
    ]
    return render(request, 'social/profile_edit.html', {
        'profile_user': user,
        'self_node': Node.objects.filter(owner=user, username=user.username).first(),
        'all_nodes': Node.objects.filter(owner=user).order_by('username'),
        'cover_presets': cover_presets,
        'media_items': ProfileMediaItem.objects.filter(user=user)[:80],
        'posts': SocialPost.objects.filter(author=user)[:30],
    })


def _split_public_items(raw):
    return list(dict.fromkeys(
        item.strip()[:80] for item in raw.replace('،', ',').replace('\n', ',').split(',')
        if item.strip()
    ))[:12]


@login_required
def post_create_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    retry_after = _rate_limited(request.user, 'post', 8, 60 * 60)
    if retry_after:
        return _spam_error(retry_after)
    if not request.user.is_public:
        return JsonResponse({'error': 'برای انتشار پست، پروفایل باید پابلیک باشد.'}, status=403)
    body = (_body(request).get('body') or '').strip()[:1200]
    if not body:
        return JsonResponse({'error': 'متن پست خالی است.'}, status=400)
    normalized = ' '.join(body.lower().split())
    recent_same = SocialPost.objects.filter(
        author=request.user, created_at__gte=timezone.now() - timedelta(hours=24)
    ).only('body')
    if any(' '.join(post.body.lower().split()) == normalized for post in recent_same):
        return JsonResponse({'error': 'این پست را در ۲۴ ساعت اخیر منتشر کرده‌ای.'}, status=400)
    post = SocialPost.objects.create(author=request.user, body=body, is_public=True)
    return JsonResponse({
        'ok': True,
        'post': {
            'id': post.id,
            'body': post.body,
            'created_at': post.created_at.strftime('%Y-%m-%d %H:%M'),
        },
    })


@login_required
def work_suggest_api(request):
    q = (request.GET.get('q') or '').strip()
    kind = request.GET.get('kind') or ''
    qs = ArtisticWork.objects.all()
    if kind in {'book', 'movie', 'series', 'music'}:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(creator__icontains=q))
    return JsonResponse({'ok': True, 'works': [{
        'id': w.id,
        'kind': w.kind,
        'title': w.title,
        'creator': w.creator,
        'year': w.year,
    } for w in qs[:12]]})


@login_required
def profile_network_view(request, username, kind):
    profile = get_object_or_404(User, username=username)
    if kind == 'followers':
        title = 'فالوئرها'
        people = [x.follower for x in Follow.objects.filter(target=profile).select_related('follower')]
    elif kind == 'following':
        title = 'فالوینگ'
        people = [x.target for x in Follow.objects.filter(follower=profile).select_related('target')]
    else:
        title = 'کانکشن‌ها'
        people = [x.friend for x in Friendship.objects.filter(user=profile).select_related('friend')]
    return render(request, 'social/profile_network.html', {'profile_user': profile, 'kind': kind, 'title': title, 'people': people})


@login_required
@csrf_exempt
def profile_cover_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    preset = request.POST.get('preset') or _body(request).get('preset')
    allowed = {'aurora', 'forest', 'rose', 'graphite', 'sunset'}
    changed = []
    if preset in allowed:
        request.user.cover_preset = preset
        request.user.cover_image = None
        changed += ['cover_preset', 'cover_image']
    if request.FILES.get('cover_image'):
        request.user.cover_image = request.FILES['cover_image']
        changed.append('cover_image')
    if changed:
        request.user.save(update_fields=list(dict.fromkeys(changed)))
    return JsonResponse({'ok': True})


@login_required
@csrf_exempt
def follow_api(request, user_id):
    """V12: فالو نیازمند تایید طرفه، مگه «تایید خودکار فالو»ش روشن باشه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        return JsonResponse({'error': 'نمی‌توانی خودت را فالو کنی'}, status=400)
    already_connected = Friendship.objects.filter(user=request.user, friend=target).exists()
    if getattr(target, 'auto_accept_follow', False) or already_connected:
        Follow.objects.get_or_create(follower=request.user, target=target)
        _notify_social(target, f'{request.user.username} فالوت کرد.', '/social/')
        return JsonResponse({'ok': True, 'following': True})
    FriendRequest.objects.get_or_create(
        sender=request.user, receiver=target, request_type='follow', status='pending',
        defaults={'message': 'درخواست فالو'},
    )
    _notify_social(target, f'{request.user.username} درخواست فالو داده.', '/social/requests/')
    return JsonResponse({'ok': True, 'request_sent': True})


@login_required
@csrf_exempt
def friend_request_api(request, user_id):
    """V12: کانکشن نیازمند تاییده، مگه «تایید خودکار کانکشن» طرف روشن باشه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    target = get_object_or_404(User, pk=user_id)
    if target == request.user:
        return JsonResponse({'error': 'نمی‌توانی به خودت connection بدهی'}, status=400)
    if Friendship.objects.filter(user=request.user, friend=target).exists():
        return JsonResponse({'ok': True, 'already_connected': True})
    reverse_req = FriendRequest.objects.filter(sender=target, receiver=request.user, request_type='connection', status='pending').first()
    if reverse_req:
        _accept_connection_request(reverse_req)
        return JsonResponse({'ok': True, 'accepted': True})
    req, _created = FriendRequest.objects.get_or_create(
        sender=request.user, receiver=target, request_type='connection', status='pending',
        defaults={'message': 'درخواست connection'},
    )
    if getattr(target, 'auto_accept_connection', False):
        _accept_connection_request(req)
        return JsonResponse({'ok': True, 'accepted': True, 'auto': True})
    _notify_social(target, f'{request.user.username} درخواست connection داده.', '/social/requests/')
    return JsonResponse({'ok': True, 'request_sent': True})


@login_required
@csrf_exempt
def friend_respond_api(request, request_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    req = get_object_or_404(FriendRequest, pk=request_id, receiver=request.user, status='pending')
    action = _body(request).get('action')
    if action == 'accept':
        if req.request_type == 'follow':
            _accept_follow_request(req)
        else:
            _accept_connection_request(req)
        return JsonResponse({'ok': True, 'accepted': True, 'type': req.request_type})
    req.status = 'rejected'
    req.responded_at = timezone.now()
    req.save(update_fields=['status', 'responded_at'])
    _notify_social(req.sender, f'{request.user.username} درخواست {req.request_type} را رد کرد.', '/social/')
    return JsonResponse({'ok': True, 'rejected': True})


@login_required
@csrf_exempt
def information_share_api(request, info_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    info = get_object_or_404(Information, pk=info_id, node__owner=request.user)
    body = _body(request)
    selected = {int(x) for x in body.get('friend_ids') or [] if str(x).isdigit()}
    allowed = _audience_ids(request.user)
    info.visibility = 'selected' if selected else 'private'
    info.save(update_fields=['visibility'])
    info.shared_with.clear()
    info.shared_with.add(*User.objects.filter(id__in=(allowed & selected)))
    return JsonResponse({'ok': True, 'visibility': info.visibility})
