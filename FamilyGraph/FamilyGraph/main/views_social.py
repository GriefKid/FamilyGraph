import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    ChatAnalysis,
    DirectMessage,
    FriendRequest,
    Friendship,
    Information,
    Node,
    Relationship,
)

User = get_user_model()


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
    }
    if viewer and viewer.is_authenticated and viewer != user:
        card['is_friend'] = Friendship.objects.filter(user=viewer, friend=user).exists()
        card['request_sent'] = FriendRequest.objects.filter(
            sender=viewer, receiver=user, status='pending'
        ).exists()
        card['request_received'] = FriendRequest.objects.filter(
            sender=user, receiver=viewer, status='pending'
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
        return a_node, b_node


@login_required
def social_view(request):
    friends = Friendship.objects.filter(user=request.user).select_related('friend', 'relationship')
    friend_ids = list(friends.values_list('friend_id', flat=True))
    incoming = FriendRequest.objects.filter(receiver=request.user, status='pending').select_related('sender')
    outgoing = FriendRequest.objects.filter(sender=request.user, status='pending').select_related('receiver')
    infos = Information.objects.filter(node__owner=request.user).select_related('node').prefetch_related('shared_with')[:80]
    incoming_shared = Information.objects.filter(
        Q(visibility='friends', node__owner_id__in=friend_ids) |
        Q(visibility='selected', shared_with=request.user)
    ).exclude(node__owner=request.user).select_related('node', 'node__owner').distinct()[:40]
    return render(request, 'social/social.html', {
        'friends': friends,
        'incoming': incoming,
        'outgoing': outgoing,
        'infos': infos,
        'incoming_shared': incoming_shared,
    })


@login_required
def discover_api(request):
    q = (request.GET.get('q') or '').strip()
    qs = User.objects.exclude(id=request.user.id)
    if q:
        qs = qs.filter(
            Q(username__icontains=q) |
            Q(first_name__icontains=q) |
            Q(last_name__icontains=q) |
            Q(city__icontains=q) |
            Q(career__icontains=q)
        )
    else:
        qs = qs.filter(is_public=True)

    visible_ids = {u.id for u in (qs[:40] if q else qs.filter(is_public=True)[:40])}
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
def friend_request_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
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
    return JsonResponse({'ok': True, 'message': {
        'id': msg.id,
        'mine': True,
        'sender': request.user.username,
        'content': msg.content,
        'created_at': msg.created_at.strftime('%Y-%m-%d %H:%M'),
    }})


@login_required
@csrf_exempt
def chat_analyze_api(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    friend = get_object_or_404(User, pk=user_id)
    if not Friendship.objects.filter(user=request.user, friend=friend).exists():
        return JsonResponse({'error': 'فقط چت دوست‌ها قابل تحلیل است'}, status=403)
    msgs = list(DirectMessage.objects.filter(
        Q(sender=request.user, receiver=friend) |
        Q(sender=friend, receiver=request.user)
    ).order_by('-created_at')[:80])
    msgs.reverse()
    if len(msgs) < 2:
        return JsonResponse({'error': 'برای تحلیل، حداقل چند پیام لازم است'}, status=400)

    try:
        from .views_smart_features import _ai_client, _extract_json, _model
        client, api_key, _provider = _ai_client()
        if not api_key:
            raise RuntimeError('AI key is not configured')
        transcript = '\n'.join(
            f"{'من' if m.sender_id == request.user.id else friend.username}: {m.content[:500]}"
            for m in msgs
        )
        prompt = f"""این چت بین من و {friend.username} را برای FamilyGraph تحلیل کن.
فقط JSON بده:
{{"summary":"خلاصه کوتاه","mood":"حال‌وهوای رابطه","topics":[],"signals":[],"suggestions":[]}}

چت:
{transcript}"""
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'تحلیل‌گر روابط هستی. فقط JSON معتبر بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=700,
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
        }

    analysis, _ = ChatAnalysis.objects.update_or_create(
        user=request.user,
        friend=friend,
        defaults={
            'summary': data.get('summary', ''),
            'mood': data.get('mood', ''),
            'topics': data.get('topics') or [],
            'signals': data.get('signals') or [],
            'suggestions': data.get('suggestions') or [],
            'raw': data,
        },
    )
    DirectMessage.objects.filter(id__in=[m.id for m in msgs], sender=request.user).update(analyzed=True)
    return JsonResponse({'ok': True, 'analysis': {
        'summary': analysis.summary,
        'mood': analysis.mood,
        'topics': analysis.topics,
        'signals': analysis.signals,
        'suggestions': analysis.suggestions,
    }})


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
