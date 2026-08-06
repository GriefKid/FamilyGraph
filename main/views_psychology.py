import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Debt, Event, ExtractionSuggestion, Node, RelationshipPulse


@login_required
@require_POST
def relationship_pulse_create_api(request):
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'دادهٔ نامعتبر است.'}, status=400)

    try:
        ratings = {name: int(data.get(name)) for name in ('support', 'autonomy', 'belonging', 'trust', 'voice')}
    except (TypeError, ValueError):
        return JsonResponse({'error': 'همهٔ امتیازها را انتخاب کن.'}, status=400)
    if any(value < 1 or value > 5 for value in ratings.values()):
        return JsonResponse({'error': 'امتیازها باید بین ۱ تا ۵ باشند.'}, status=400)

    node = None
    if data.get('node_id'):
        node = Node.objects.filter(pk=data['node_id'], owner=request.user).first()
        if not node:
            return JsonResponse({'error': 'شخص انتخاب‌شده پیدا نشد.'}, status=404)
    pulse = RelationshipPulse.objects.create(owner=request.user, node=node, note=str(data.get('note', ''))[:280], **ratings)
    return JsonResponse({'ok': True, 'id': pulse.id})


@login_required
def extraction_suggestions_api(request):
    rows = ExtractionSuggestion.objects.filter(owner=request.user, status='pending')[:40]
    return JsonResponse({'nodes': [
        {'id': node.id, 'name': node.display_name()} for node in Node.objects.filter(owner=request.user).order_by('username')[:120]
    ], 'suggestions': [
        {'id': row.id, 'kind': row.kind, 'payload': row.payload, 'source': row.source}
        for row in rows
    ]})


@login_required
@require_POST
def extraction_suggestion_decide_api(request, pk):
    suggestion = ExtractionSuggestion.objects.filter(pk=pk, owner=request.user, status='pending').first()
    if not suggestion:
        return JsonResponse({'error': 'پیشنهاد پیدا نشد.'}, status=404)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'دادهٔ نامعتبر است.'}, status=400)
    action = data.get('action')
    if action == 'dismiss':
        suggestion.status = 'dismissed'; suggestion.save(update_fields=['status'])
        return JsonResponse({'ok': True})
    if action != 'approve':
        return JsonResponse({'error': 'عمل نامعتبر است.'}, status=400)
    if suggestion.kind == 'event':
        Event.objects.create(owner=request.user, title=(data.get('title') or suggestion.payload.get('snippet') or 'رویداد')[:200], date=timezone.localdate())
    elif suggestion.kind == 'debt':
        node = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
        digits = str(suggestion.payload.get('amount_raw', '')).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')).replace(',', '').replace('٬', '')
        amount = suggestion.payload.get('amount_value') or (int(digits) if digits.isdigit() else None)
        if not node or not amount:
            return JsonResponse({'error': 'برای ثبت مالی، شخص و مبلغ معتبر لازم است.'}, status=400)
        Debt.objects.create(owner=request.user, node=node, direction=data.get('direction') if data.get('direction') in ('i_owe','they_owe') else suggestion.payload.get('direction', 'i_owe'), amount=amount, date=timezone.localdate(), note=suggestion.payload.get('snippet','')[:300])
    elif suggestion.kind == 'person':
        name = (data.get('name') or suggestion.payload.get('name_raw') or '').strip()[:100]
        if not name:
            return JsonResponse({'error': 'نام شخص لازم است.'}, status=400)
        username = (data.get('username') or name).strip()[:100]
        node, _ = Node.objects.get_or_create(owner=request.user, username=username, defaults={'name': name})
    suggestion.status = 'approved'; suggestion.save(update_fields=['status'])
    return JsonResponse({'ok': True})
