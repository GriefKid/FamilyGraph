import json
from datetime import timedelta
from difflib import SequenceMatcher

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Debt, Event, ExtractionSuggestion, FollowUp, Interaction, JournalEntry,
                     KnowledgeTriple, MemoryFact, Node, NodeAlias, NodeMergeOperation, Relationship,
                     RelationshipRecommendation, NodeSafetySetting)


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return None


def _duplicates(user):
    nodes = list(Node.objects.filter(owner=user, merged_into__isnull=True).exclude(pk=user.root_node_id))
    pairs = []
    for i, left in enumerate(nodes):
        a = left.display_name().replace(' ', '').lower()
        for right in nodes[i + 1:]:
            b = right.display_name().replace(' ', '').lower()
            score = SequenceMatcher(None, a, b).ratio()
            alias_match = NodeAlias.objects.filter(owner=user, node=left,
                                                   normalized_alias__in=right.aliases.values('normalized_alias')).exists()
            if score >= .72 or alias_match:
                pairs.append({'left': left, 'right': right, 'score': round(score * 100)})
    return sorted(pairs, key=lambda row: row['score'], reverse=True)[:30]


@login_required
def memory_hub(request):
    user = request.user
    facts = MemoryFact.objects.filter(owner=user).select_related('node', 'suggestion')[:200]
    conflicts = []
    grouped = {}
    for fact in facts:
        if fact.active:
            grouped.setdefault((fact.node_id, fact.category), []).append(fact)
    for rows in grouped.values():
        values = {row.value.casefold() for row in rows}
        if len(values) > 1:
            conflicts.append(rows)
    low_confidence = [fact for fact in facts if fact.active and fact.confidence < 60]
    stale = ExtractionSuggestion.objects.filter(owner=user, status='pending',
                                                 created_at__lt=timezone.now() - timedelta(days=14)).count()
    orphan_nodes = Node.objects.filter(owner=user, merged_into__isnull=True).exclude(pk=user.root_node_id).exclude(
        Q(as_source__owner=user) | Q(as_target__owner=user)).distinct()[:30]
    return render(request, 'memory/hub.html', {
        'nodes': Node.objects.filter(owner=user, merged_into__isnull=True).order_by('username'),
        'facts': facts, 'conflicts': conflicts, 'duplicates': _duplicates(user),
        'low_confidence': low_confidence, 'stale_suggestions': stale,
        'orphan_nodes': orphan_nodes,
        'events_without_people': Event.objects.filter(owner=user, participants__isnull=True).count(),
        'debts_without_due': Debt.objects.filter(owner=user, settled=False, due_date__isnull=True).count(),
        'recent_merges': NodeMergeOperation.objects.filter(owner=user)[:20],
    })


@login_required
def knowledge_graph_view(request):
    triples = KnowledgeTriple.objects.filter(owner=request.user, active=True).select_related(
        'subject', 'object_node')[:500]
    return render(request, 'memory/knowledge.html', {'triples': triples})


@login_required
@require_POST
def memory_fact_api(request, pk=None):
    data = _body(request)
    if data is None:
        return JsonResponse({'error': 'JSON نامعتبر است.'}, status=400)
    action = data.get('action', 'create')
    fact = get_object_or_404(MemoryFact, pk=pk, owner=request.user) if pk else None
    if action == 'create':
        node = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
        category, value = data.get('category'), str(data.get('value', '')).strip()[:300]
        if not node or category not in dict(MemoryFact.CATEGORY_CHOICES) or not value:
            return JsonResponse({'error': 'شخص، دسته و مقدار معتبر لازم است.'}, status=400)
        fact, _ = MemoryFact.objects.update_or_create(
            owner=request.user, node=node, category=category, value=value,
            defaults={'confidence': min(100, max(0, int(data.get('confidence', 100)))),
                      'source': 'manual', 'ai_usable': bool(data.get('ai_usable', True)),
                      'confidentiality': data.get('confidentiality') if data.get('confidentiality') in dict(MemoryFact._meta.get_field('confidentiality').choices) else 'personal',
                      'active': True})
        KnowledgeTriple.objects.get_or_create(
            owner=request.user, subject=node, predicate=category, object_text=value,
            object_node=None, defaults={'confidence': fact.confidence, 'source': 'manual'})
    elif action == 'update' and fact:
        if data.get('category') in dict(MemoryFact.CATEGORY_CHOICES):
            fact.category = data['category']
        if str(data.get('value', '')).strip():
            fact.value = str(data['value']).strip()[:300]
        fact.confidence = min(100, max(0, int(data.get('confidence', fact.confidence))))
        fact.ai_usable = bool(data.get('ai_usable', fact.ai_usable))
        if data.get('confidentiality') in dict(MemoryFact._meta.get_field('confidentiality').choices):
            fact.confidentiality = data['confidentiality']
        fact.save()
    elif action == 'toggle' and fact:
        fact.active = not fact.active
        fact.save(update_fields=['active'])
    elif action == 'delete' and fact:
        fact.delete()
        return JsonResponse({'ok': True})
    elif action == 'supersede' and fact:
        winner = MemoryFact.objects.filter(owner=request.user, node=fact.node,
                                           pk=data.get('winner_id')).first()
        if not winner:
            return JsonResponse({'error': 'واقعیت جایگزین پیدا نشد.'}, status=404)
        fact.active, fact.superseded_by = False, winner
        fact.save(update_fields=['active', 'superseded_by'])
    else:
        return JsonResponse({'error': 'عمل نامعتبر است.'}, status=400)
    return JsonResponse({'ok': True, 'id': fact.id})


@login_required
def memory_search_api(request):
    q = request.GET.get('q', '').strip()[:120]
    if len(q) < 2:
        return JsonResponse({'results': []})
    user = request.user
    results = []
    stop = {'کی', 'چه', 'کسی', 'کسانی', 'از', 'با', 'به', 'رو', 'را', 'درباره', 'چی',
            'گفت', 'میاد', 'داره', 'دارم', 'هستن', 'است', 'موضوع', 'آخرین', 'بار'}
    tokens = [token.strip('؟?!،,.') for token in q.split()
              if len(token.strip('؟?!،,.')) > 1 and token.strip('؟?!،,.') not in stop]
    fact_query = Q()
    for token in tokens or [q]:
        fact_query |= Q(value__icontains=token) | Q(node__username__icontains=token) | Q(node__name__icontains=token)
    for fact in MemoryFact.objects.filter(owner=user, active=True).filter(fact_query).select_related('node')[:30]:
        results.append({'kind': 'memory', 'title': fact.node.display_name(), 'text': fact.value,
                        'source': f'{fact.source} #{fact.source_id or "—"}', 'url': f'/nodes/{fact.node_id}/'})
    journal_query = Q()
    for token in tokens or [q]:
        journal_query |= Q(text__icontains=token)
    for entry in JournalEntry.objects.filter(owner=user).filter(journal_query).prefetch_related('mentioned_nodes')[:20]:
        results.append({'kind': 'journal', 'title': 'خاطره', 'text': entry.text[:220],
                        'source': f'journal #{entry.id}', 'url': '/journal/'})
    debt_query = Q(settled=False) if any(word in q for word in ('مالی', 'قرض', 'طلب', 'بدهی')) else Q()
    for token in tokens:
        debt_query |= Q(note__icontains=token) | Q(node__name__icontains=token)
    for debt in Debt.objects.filter(owner=user).filter(debt_query).select_related('node')[:15]:
        results.append({'kind': 'debt', 'title': debt.node.display_name(),
                        'text': f'{debt.remaining:,} {debt.currency}', 'source': f'debt #{debt.id}', 'url': '/ledger/'})
    if any(word in q for word in ('تولد', 'ماه بعد', 'رویداد')):
        today = timezone.localdate()
        for event in Event.objects.filter(owner=user, date__gte=today, date__lte=today + timedelta(days=45)).prefetch_related('participants')[:20]:
            if 'تولد' not in q or 'تولد' in event.title:
                results.append({'kind': 'event', 'title': event.title, 'text': str(event.date),
                                'source': f'event #{event.id}', 'url': '/events/'})
    return JsonResponse({'query': q, 'interpreted_tokens': tokens, 'results': results[:50]})


def _assistant_payload(user, node):
    facts = list(MemoryFact.objects.filter(owner=user, node=node, active=True, ai_usable=True)[:40])
    followups = list(FollowUp.objects.filter(owner=user, node=node, done=False)[:5])
    sensitivities = [f.value for f in facts if f.category in ('boundary', 'sensitivity')]
    interests = [f.value for f in facts if f.category in ('interest', 'preference', 'life_topic')]
    topic = interests[0] if interests else (followups[0].text if followups else 'حال این روزهایش')
    reason = f'بر اساس {len(facts)} واقعیت تأییدشده و {len(followups)} موضوع باز.'
    return {'topic': topic, 'avoid': sensitivities[:3], 'open_topics': [f.text for f in followups],
            'draft': f'سلام {node.display_name()}، یاد {topic} افتادم. این روزها چطوری؟',
            'reason': reason, 'safety': 'این‌ها پیشنهاد و فرضیه‌اند، نه تشخیص روان‌شناختی.'}


@login_required
def relationship_assistant_api(request, pk):
    node = get_object_or_404(Node, pk=pk, owner=request.user, merged_into__isnull=True)
    safety = NodeSafetySetting.objects.filter(owner=request.user, node=node).first()
    if safety and (safety.pause_contact_suggestions or
                   (safety.no_contact_until and safety.no_contact_until >= timezone.localdate())):
        rec, _ = RelationshipRecommendation.objects.get_or_create(
            owner=request.user, node=node, status='active', kind='safety',
            defaults={'title': 'حالت محافظتی', 'suggestion': '',
                      'reason': 'پیشنهاد تماس طبق تنظیم کاربر متوقف است.'})
        return JsonResponse({'topic': 'حالت محافظتی فعال است', 'draft': '', 'open_topics': [],
                             'avoid': [safety.boundaries] if safety.boundaries else [],
                             'reason': 'طبق تنظیم خودت، پیشنهاد تماس برای این رابطه متوقف شده است.',
                             'safety': 'هیچ اقدامی پیشنهاد نمی‌شود.', 'recommendation_id': rec.id})
    payload = _assistant_payload(request.user, node)
    latest = RelationshipRecommendation.objects.filter(owner=request.user, node=node).first()
    if latest and latest.status == 'snoozed' and latest.snoozed_until and latest.snoozed_until >= timezone.localdate():
        return JsonResponse({'topic': 'فعلاً استراحت', 'draft': '', 'open_topics': [], 'avoid': [],
                             'reason': f'این پیشنهاد تا {latest.snoozed_until} عقب افتاده است.',
                             'safety': 'پس از پایان زمان تعویق دوباره بررسی می‌شود.',
                             'recommendation_id': latest.id})
    if latest and latest.status == 'dismissed' and latest.created_at >= timezone.now() - timedelta(days=30):
        return JsonResponse({'topic': 'فعلاً پیشنهادی نداریم', 'draft': '', 'open_topics': [], 'avoid': [],
                             'reason': 'برای این شخص تا ۳۰ روز پیشنهاد تازه نمی‌دهیم.',
                             'safety': 'می‌توانی بعداً دوباره بررسی کنی.',
                             'recommendation_id': latest.id})
    rec = latest if latest and latest.status == 'active' else RelationshipRecommendation.objects.create(
        owner=request.user, node=node, status='active', title='یک ارتباط کوچک',
        suggestion=payload['draft'], reason=payload['reason'])
    return JsonResponse({**payload, 'recommendation_id': rec.id})


@login_required
@require_POST
def clear_psychology_inferences_api(request):
    count, _ = MemoryFact.objects.filter(
        owner=request.user, source__in=('journal', 'checkin', 'chat'),
        category__in=('emotion', 'communication', 'sensitivity', 'boundary'),
    ).delete()
    ExtractionSuggestion.objects.filter(owner=request.user, kind='signal').update(status='dismissed')
    return JsonResponse({'ok': True, 'deleted': count})


@login_required
@require_POST
def recommendation_feedback_api(request, pk):
    rec = get_object_or_404(RelationshipRecommendation, pk=pk, owner=request.user)
    data = _body(request) or {}
    action = data.get('action')
    if action == 'snooze':
        rec.snoozed_until, rec.status = timezone.localdate() + timedelta(days=min(90, max(1, int(data.get('days', 7))))), 'snoozed'
    elif action == 'dismiss':
        rec.status = 'dismissed'
    elif action == 'outcome':
        rec.status, rec.acted_at = 'completed', timezone.now()
        rec.outcome = data.get('outcome') if data.get('outcome') in ('better', 'same', 'worse') else 'same'
        rec.outcome_note = str(data.get('note', ''))[:300]
        rec.helpful = data.get('helpful') if isinstance(data.get('helpful'), bool) else None
    else:
        return JsonResponse({'error': 'عمل نامعتبر است.'}, status=400)
    rec.save()
    return JsonResponse({'ok': True})


def _merge_preview(user, primary, duplicate):
    return {'interactions': Interaction.objects.filter(owner=user, node=duplicate).count(),
            'followups': FollowUp.objects.filter(owner=user, node=duplicate).count(),
            'debts': Debt.objects.filter(owner=user, node=duplicate).count(),
            'facts': MemoryFact.objects.filter(owner=user, node=duplicate).count(),
            'aliases': NodeAlias.objects.filter(owner=user, node=duplicate).count(),
            'journals': JournalEntry.objects.filter(owner=user, mentioned_nodes=duplicate).count(),
            'events': Event.objects.filter(owner=user, participants=duplicate).count(),
            'relationships': Relationship.objects.filter(owner=user).filter(
                Q(source=duplicate) | Q(target=duplicate)).count()}


@login_required
def node_merge_preview_api(request):
    primary = get_object_or_404(Node, owner=request.user, pk=request.GET.get('primary'))
    duplicate = get_object_or_404(Node, owner=request.user, pk=request.GET.get('duplicate'))
    if primary == duplicate:
        return JsonResponse({'error': 'دو شخص باید متفاوت باشند.'}, status=400)
    return JsonResponse({'primary': primary.display_name(), 'duplicate': duplicate.display_name(),
                         'moves': _merge_preview(request.user, primary, duplicate)})


@login_required
@require_POST
@transaction.atomic
def node_merge_apply_api(request):
    data = _body(request) or {}
    primary = get_object_or_404(Node, owner=request.user, pk=data.get('primary_id'), merged_into__isnull=True)
    duplicate = get_object_or_404(Node, owner=request.user, pk=data.get('duplicate_id'), merged_into__isnull=True)
    if primary == duplicate or duplicate.pk == request.user.root_node_id:
        return JsonResponse({'error': 'ادغام انتخاب‌شده مجاز نیست.'}, status=400)
    snapshot = {'models': {}, 'relationships': [], 'journals': [], 'journal_primary_added': [],
                'events': [], 'event_primary_added': [], 'generated_alias_id': None}
    for label, model in [('interactions', Interaction), ('followups', FollowUp), ('debts', Debt)]:
        ids = list(model.objects.filter(owner=request.user, node=duplicate).values_list('id', flat=True))
        snapshot['models'][label] = ids
        model.objects.filter(id__in=ids).update(node=primary)
    moved_facts = []
    for fact in MemoryFact.objects.filter(owner=request.user, node=duplicate):
        if not MemoryFact.objects.filter(owner=request.user, node=primary, category=fact.category, value=fact.value).exists():
            fact.node = primary; fact.save(update_fields=['node']); moved_facts.append(fact.id)
    snapshot['models']['facts'] = moved_facts
    moved_aliases = []
    for alias in NodeAlias.objects.filter(owner=request.user, node=duplicate):
        if not NodeAlias.objects.filter(owner=request.user, normalized_alias=alias.normalized_alias).exclude(pk=alias.pk).exists():
            alias.node = primary; alias.save(update_fields=['node']); moved_aliases.append(alias.id)
    snapshot['models']['aliases'] = moved_aliases
    for rel in Relationship.objects.filter(owner=request.user).filter(Q(source=duplicate) | Q(target=duplicate)):
        other_id = rel.target_id if rel.source_id == duplicate.id else rel.source_id
        if other_id == primary.id:
            continue
        new_source = primary.id if rel.source_id == duplicate.id else rel.source_id
        new_target = primary.id if rel.target_id == duplicate.id else rel.target_id
        conflict = Relationship.objects.filter(owner=request.user, source_id=new_source,
                                               target_id=new_target, rel=rel.rel).exclude(pk=rel.pk).exists()
        if not conflict:
            snapshot['relationships'].append({'id': rel.id, 'source_was_duplicate': rel.source_id == duplicate.id})
            rel.source_id, rel.target_id = new_source, new_target
            rel.save()
    for entry in JournalEntry.objects.filter(owner=request.user, mentioned_nodes=duplicate):
        snapshot['journals'].append(entry.id)
        if not entry.mentioned_nodes.filter(pk=primary.pk).exists():
            entry.mentioned_nodes.add(primary); snapshot['journal_primary_added'].append(entry.id)
        entry.mentioned_nodes.remove(duplicate)
    for event in Event.objects.filter(owner=request.user, participants=duplicate):
        snapshot['events'].append(event.id)
        if not event.participants.filter(pk=primary.pk).exists():
            event.participants.add(primary); snapshot['event_primary_added'].append(event.id)
        event.participants.remove(duplicate)
    alias_text = duplicate.display_name()
    normalized = ' '.join(alias_text.replace('ي', 'ی').replace('ك', 'ک').lower().split())
    generated_alias, created = NodeAlias.objects.update_or_create(
        owner=request.user, normalized_alias=normalized, defaults={'node': primary, 'alias': alias_text})
    if created:
        snapshot['generated_alias_id'] = generated_alias.id
    duplicate.merged_into = primary; duplicate.save(update_fields=['merged_into'])
    operation = NodeMergeOperation.objects.create(owner=request.user, primary_node=primary,
                                                   duplicate_node=duplicate, snapshot=snapshot)
    return JsonResponse({'ok': True, 'operation_id': operation.id})


@login_required
@require_POST
@transaction.atomic
def node_merge_undo_api(request, pk):
    op = get_object_or_404(NodeMergeOperation, pk=pk, owner=request.user, status='applied')
    model_map = {'interactions': Interaction, 'followups': FollowUp, 'debts': Debt,
                 'facts': MemoryFact, 'aliases': NodeAlias}
    for label, ids in op.snapshot.get('models', {}).items():
        model_map[label].objects.filter(owner=request.user, id__in=ids, node=op.primary_node).update(node=op.duplicate_node)
    for item in op.snapshot.get('relationships', []):
        relationship = Relationship.objects.filter(owner=request.user, id=item['id']).first()
        if relationship:
            if item['source_was_duplicate']:
                relationship.source = op.duplicate_node
            else:
                relationship.target = op.duplicate_node
            relationship.save()
    for entry in JournalEntry.objects.filter(owner=request.user, id__in=op.snapshot.get('journals', [])):
        entry.mentioned_nodes.add(op.duplicate_node)
        if entry.id in op.snapshot.get('journal_primary_added', []):
            entry.mentioned_nodes.remove(op.primary_node)
    for event in Event.objects.filter(owner=request.user, id__in=op.snapshot.get('events', [])):
        event.participants.add(op.duplicate_node)
        if event.id in op.snapshot.get('event_primary_added', []):
            event.participants.remove(op.primary_node)
    if op.snapshot.get('generated_alias_id'):
        NodeAlias.objects.filter(owner=request.user, id=op.snapshot['generated_alias_id']).delete()
    op.duplicate_node.merged_into = None; op.duplicate_node.save(update_fields=['merged_into'])
    op.status, op.undone_at = 'undone', timezone.now(); op.save(update_fields=['status', 'undone_at'])
    return JsonResponse({'ok': True})
