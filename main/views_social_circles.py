import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import Friendship, SocialCircle, SocialCircleMessage


def _body(request):
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _connections(user):
    return Friendship.objects.filter(user=user).select_related('friend')


@login_required
def circles_view(request):
    if not request.user.is_public:
        return render(request, 'social/locked.html')
    circles = SocialCircle.objects.filter(members=request.user).prefetch_related('members')
    return render(request, 'social/circles.html', {
        'circles': circles,
        'connections': _connections(request.user),
    })


@login_required
def circle_create_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if not request.user.is_public:
        return JsonResponse({'error': 'بخش اجتماعی فقط برای پروفایل پابلیک است.'}, status=403)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'JSON object required'}, status=400)
    name = (body.get('name') or '').strip()[:100]
    description = (body.get('description') or '').strip()[:280]
    if not name:
        return JsonResponse({'error': 'اسم حلقه را بنویس.'}, status=400)

    allowed_ids = set(_connections(request.user).values_list('friend_id', flat=True))
    requested_ids = {
        int(value) for value in (body.get('member_ids') or []) if str(value).isdigit()
    }
    circle = SocialCircle.objects.create(
        name=name, description=description, created_by=request.user
    )
    circle.members.add(request.user, *(allowed_ids & requested_ids))
    return JsonResponse({'ok': True, 'id': circle.id, 'name': circle.name})


def _circle_for_member(request, circle_id):
    return get_object_or_404(
        SocialCircle.objects.prefetch_related('members'),
        pk=circle_id,
        members=request.user,
    )


@login_required
def circle_messages_api(request, circle_id):
    circle = _circle_for_member(request, circle_id)
    messages = SocialCircleMessage.objects.filter(circle=circle).select_related('author')[:150]
    return JsonResponse({
        'ok': True,
        'circle': {
            'id': circle.id,
            'name': circle.name,
            'members': [
                {'id': member.id, 'name': member.get_full_name() or member.username}
                for member in circle.members.all()
            ],
        },
        'messages': [{
            'id': message.id,
            'mine': message.author_id == request.user.id,
            'author': message.author.get_full_name() or message.author.username,
            'body': message.body,
            'created_at': message.created_at.strftime('%Y-%m-%d %H:%M'),
        } for message in messages],
    })


@login_required
def circle_send_api(request, circle_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    circle = _circle_for_member(request, circle_id)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'JSON object required'}, status=400)
    body = (body.get('body') or '').strip()[:2000]
    if not body:
        return JsonResponse({'error': 'پیام خالی است.'}, status=400)
    message = SocialCircleMessage.objects.create(
        circle=circle, author=request.user, body=body
    )
    return JsonResponse({
        'ok': True,
        'message': {
            'id': message.id,
            'body': message.body,
            'created_at': message.created_at.strftime('%Y-%m-%d %H:%M'),
        },
    })
