import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Debt, Event, ExtractionSuggestion, KnowledgeTriple, MemoryFact, Node, NodeAlias,
                     Relationship, RelationshipPulse)
from .utils_jalali import parse_date_input


@login_required
def extraction_inbox(request):
    counts = {
        status: ExtractionSuggestion.objects.filter(owner=request.user, status=status).count()
        for status in ('pending', 'approved', 'dismissed')
    }
    return render(request, 'extractions/inbox.html', {'counts': counts})


@login_required
@require_POST
def relationship_pulse_create_api(request):
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'دادهٔ نامعتبر است.'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'آبجکت JSON لازم است.'}, status=400)

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
    suggestions = []
    if node and pulse.note:
        try:
            from .memory_pipeline import capture_node_note
            suggestions = capture_node_note(request.user, node, pulse.note, 'pulse', pulse.id)
        except Exception:
            pass
    return JsonResponse({'ok': True, 'id': pulse.id, 'suggestions': len(suggestions)})


@login_required
def extraction_suggestions_api(request):
    status = request.GET.get('status', 'pending')
    if status not in ('pending', 'approved', 'dismissed', 'all'):
        return JsonResponse({'error': 'وضعیت نامعتبر است.'}, status=400)
    rows = ExtractionSuggestion.objects.filter(owner=request.user)
    if status != 'all':
        rows = rows.filter(status=status)
    rows = rows[:100]
    return JsonResponse({'nodes': [
        {'id': node.id, 'name': node.display_name(), 'username': node.username}
        for node in Node.objects.filter(owner=request.user).order_by('username')[:120]
    ], 'suggestions': [
        {'id': row.id, 'kind': row.kind, 'payload': row.payload, 'source': row.source,
         'source_id': row.source_id, 'status': row.status,
         'created_at': row.created_at.isoformat(), 'can_undo': bool(row.applied_model and row.applied_object_id)}
        for row in rows
    ]})


@login_required
@require_POST
def extraction_suggestion_decide_api(request, pk):
    suggestion = ExtractionSuggestion.objects.filter(pk=pk, owner=request.user).first()
    if not suggestion:
        return JsonResponse({'error': 'پیشنهاد پیدا نشد.'}, status=404)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'دادهٔ نامعتبر است.'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'آبجکت JSON لازم است.'}, status=400)
    action = data.get('action')
    if action == 'undo':
        if suggestion.status != 'approved' or not suggestion.applied_model or not suggestion.applied_object_id:
            return JsonResponse({'error': 'این پیشنهاد قابل بازگردانی نیست.'}, status=400)
        if suggestion.applied_model == 'relationship':
            relationship = Relationship.objects.filter(pk=suggestion.applied_object_id, owner=request.user).first()
            previous = suggestion.payload.get('_previous_relationship')
            if relationship and previous:
                relationship.rel = previous.get('rel')
                relationship.status = previous.get('status', 'active')
                relationship.strength = previous.get('strength', 3)
                relationship.save()
            elif relationship:
                relationship.delete()
            suggestion.status, suggestion.applied_model, suggestion.applied_object_id = 'pending', '', None
            suggestion.save(update_fields=['status', 'applied_model', 'applied_object_id', 'updated_at'])
            return JsonResponse({'ok': True})
        model_map = {'event': Event, 'debt': Debt, 'node': Node, 'alias': NodeAlias,
                     'memory': MemoryFact}
        model = model_map.get(suggestion.applied_model)
        obj = model.objects.filter(pk=suggestion.applied_object_id, owner=request.user).first() if model else None
        if obj:
            obj.delete()
        suggestion.status = 'pending'
        suggestion.applied_model = ''
        suggestion.applied_object_id = None
        suggestion.save(update_fields=['status', 'applied_model', 'applied_object_id', 'updated_at'])
        return JsonResponse({'ok': True})
    if suggestion.status != 'pending':
        return JsonResponse({'error': 'این پیشنهاد قبلاً بررسی شده است.'}, status=409)
    if action == 'edit':
        payload = data.get('payload')
        if not isinstance(payload, dict):
            return JsonResponse({'error': 'محتوای ویرایش معتبر نیست.'}, status=400)
        if suggestion.kind == 'person' and payload.get('name'):
            payload['name_raw'] = payload.pop('name')
        if suggestion.kind == 'debt' and payload.get('amount_value'):
            try:
                payload['amount_value'] = int(str(payload['amount_value']).translate(
                    str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')).replace(',', '').replace('٬', ''))
            except ValueError:
                return JsonResponse({'error': 'مبلغ معتبر نیست.'}, status=400)
        suggestion.payload = {**suggestion.payload, **payload}
        suggestion.save(update_fields=['payload', 'updated_at'])
        return JsonResponse({'ok': True, 'payload': suggestion.payload})
    if action == 'dismiss':
        suggestion.status = 'dismissed'; suggestion.save(update_fields=['status'])
        return JsonResponse({'ok': True})
    if action != 'approve':
        return JsonResponse({'error': 'عمل نامعتبر است.'}, status=400)
    if suggestion.kind == 'event':
        event_date = timezone.localdate()
        chosen_date = data.get('date') or suggestion.payload.get('date')
        if chosen_date:
            try:
                from datetime import date
                event_date = parse_date_input(chosen_date)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'تاریخ رویداد معتبر نیست.'}, status=400)
        event = Event.objects.create(owner=request.user, title=(data.get('title') or suggestion.payload.get('title') or suggestion.payload.get('snippet') or 'رویداد')[:200], date=event_date, event_time=data.get('time') or suggestion.payload.get('time') or None)
        participant = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
        if participant:
            event.participants.add(participant)
        suggestion.applied_model, suggestion.applied_object_id = 'event', event.id
    elif suggestion.kind == 'debt':
        node = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
        supplied_amount = data.get('amount_value')
        raw_amount = supplied_amount if supplied_amount not in (None, '') else suggestion.payload.get('amount_raw', '')
        digits = str(raw_amount).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')).replace(',', '').replace('٬', '')
        amount = int(digits) if digits.isdigit() else suggestion.payload.get('amount_value')
        if not node or not amount:
            return JsonResponse({'error': 'برای ثبت مالی، شخص و مبلغ معتبر لازم است.'}, status=400)
        debt = Debt.objects.create(owner=request.user, node=node, direction=data.get('direction') if data.get('direction') in ('i_owe','they_owe') else suggestion.payload.get('direction', 'i_owe'), amount=amount, date=timezone.localdate(), note=suggestion.payload.get('snippet','')[:300])
        suggestion.applied_model, suggestion.applied_object_id = 'debt', debt.id
    elif suggestion.kind == 'person':
        name = (data.get('name') or suggestion.payload.get('name_raw') or '').strip()[:100]
        if not name:
            return JsonResponse({'error': 'نام شخص لازم است.'}, status=400)
        selected = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
        if selected:
            normalized = ' '.join(name.replace('ي', 'ی').replace('ك', 'ک').lower().split())
            alias, _ = NodeAlias.objects.update_or_create(
                owner=request.user, normalized_alias=normalized,
                defaults={'node': selected, 'alias': name})
            suggestion.applied_model, suggestion.applied_object_id = 'alias', alias.id
        else:
            username = (data.get('username') or name).strip()[:100]
            node, created = Node.objects.get_or_create(owner=request.user, username=username, defaults={'name': name})
            if created:
                suggestion.applied_model, suggestion.applied_object_id = 'node', node.id
    elif suggestion.kind == 'memory':
        node = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
        category = data.get('category') or suggestion.payload.get('category')
        value = (data.get('value') or suggestion.payload.get('value') or '').strip()[:300]
        if not node or category not in dict(MemoryFact.CATEGORY_CHOICES) or not value:
            return JsonResponse({'error': 'شخص، دسته و مقدار شناختی معتبر لازم است.'}, status=400)
        fact, created = MemoryFact.objects.get_or_create(
            owner=request.user, node=node, category=category, value=value,
            defaults={'confidence': min(100, max(0, int(data.get('confidence') or suggestion.payload.get('confidence') or 70))),
                      'source': suggestion.source, 'source_id': suggestion.source_id,
                      'suggestion': suggestion, 'active': True},
        )
        if created:
            suggestion.applied_model, suggestion.applied_object_id = 'memory', fact.id
        KnowledgeTriple.objects.get_or_create(
            owner=request.user, subject=node, predicate=category, object_text=value,
            object_node=None, defaults={'confidence': fact.confidence, 'source': suggestion.source,
                                        'source_id': suggestion.source_id})
    elif suggestion.kind == 'relationship':
        node = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
        root = request.user.root_node
        if not node or not root or node.id == root.id:
            return JsonResponse({'error': 'ابتدا شخص و نود اصلی خودت را مشخص کن.'}, status=400)
        relationship = Relationship.objects.filter(owner=request.user).filter(
            source=root, target=node).first() or Relationship.objects.filter(
            owner=request.user, source=node, target=root).first()
        if relationship:
            suggestion.payload['_previous_relationship'] = {
                'rel': relationship.rel, 'status': relationship.status, 'strength': relationship.strength}
        else:
            relationship = Relationship(owner=request.user, source=root, target=node)
        relationship.rel = (data.get('relationship_type') or suggestion.payload.get('relationship_type') or relationship.rel or 'آشنا')[:100]
        new_status = data.get('status') or suggestion.payload.get('status') or 'active'
        relationship.status = new_status if new_status in dict(Relationship.STATUS_CHOICES) else 'active'
        relationship.strength = min(5, max(1, int(data.get('strength') or suggestion.payload.get('strength') or 3)))
        relationship.save()
        KnowledgeTriple.objects.get_or_create(
            owner=request.user, subject=root, predicate='relationship', object_text=relationship.rel or '',
            object_node=node, defaults={'confidence': relationship.strength * 20,
                                        'source': suggestion.source, 'source_id': suggestion.source_id})
        suggestion.applied_model, suggestion.applied_object_id = 'relationship', relationship.id
    suggestion.status = 'approved'; suggestion.save(update_fields=['status', 'payload', 'applied_model', 'applied_object_id', 'updated_at'])
    return JsonResponse({'ok': True})
