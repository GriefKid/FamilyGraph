import csv
import io
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.conf import settings
from pathlib import Path

from .models import (Commitment, Debt, Event, GiftIdea, Information, Interaction,
                     JournalEntry, MeetingReflection, MemoryFact, Node, NodeSafetySetting,
                     Relationship, ShareLink)


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return None


def service_worker(request):
    content = (Path(settings.BASE_DIR) / 'static' / 'service-worker.js').read_text(encoding='utf-8')
    return HttpResponse(content, content_type='application/javascript', headers={'Service-Worker-Allowed': '/'})


def _briefing(user, node):
    facts = MemoryFact.objects.filter(owner=user, node=node, active=True).order_by('-observed_at')[:20]
    return {
        'facts': facts,
        'commitments': Commitment.objects.filter(owner=user, node=node, status='open')[:10],
        'debts': Debt.objects.filter(owner=user, node=node, settled=False)[:10],
        'events': Event.objects.filter(
            owner=user, participants=node, participants__owner=user,
            date__gte=timezone.localdate(),
        ).distinct()[:8],
        'last_interaction': Interaction.objects.filter(owner=user, node=node).first(),
        'safety': NodeSafetySetting.objects.filter(owner=user, node=node).first(),
    }


@login_required
def relationship_life_hub(request):
    user = request.user
    nodes = Node.objects.filter(owner=user, merged_into__isnull=True).exclude(pk=user.root_node_id)
    open_commitments = Commitment.objects.filter(owner=user, status='open').select_related('node')[:40]
    gifts = GiftIdea.objects.filter(owner=user).select_related('node')[:40]
    completed = Commitment.objects.filter(owner=user, status='done').count()
    total = Commitment.objects.filter(owner=user).count()
    progress = round(completed / total * 100) if total else 0
    return render(request, 'relationship_life/hub.html', {
        'nodes': nodes, 'commitments': open_commitments, 'gifts': gifts,
        'reflections': MeetingReflection.objects.filter(owner=user).select_related('node')[:20],
        'progress': progress,
    })


@login_required
def trust_center_view(request):
    """Explain the user's private-data and AI boundaries in plain language."""
    user = request.user
    safety = list(NodeSafetySetting.objects.filter(owner=user, pause_contact_suggestions=True)
                  .select_related('node').order_by('node__username'))
    no_ai_facts = MemoryFact.objects.filter(owner=user, confidentiality='no_ai').count()
    private_nodes = Node.objects.filter(owner=user, is_public=False).count()
    return render(request, 'relationship_life/trust_center.html', {
        'private_nodes': private_nodes,
        'no_ai_facts': no_ai_facts,
        'paused_people': safety,
        'ai_enabled': user.ai_chat_enabled or user.ai_extraction_enabled or user.ai_journal_enabled,
        'share_links': ShareLink.objects.filter(owner=user, revoked=False,
                                                expires_at__gt=timezone.now()).select_related('node')[:20],
    })


@login_required
def person_card_view(request, pk):
    """A concise, private, print-friendly brief for one person."""
    node = get_object_or_404(Node, owner=request.user, pk=pk, merged_into__isnull=True)
    from .models import FollowUp
    return render(request, 'relationship_life/person_card.html', {
        'node': node,
        'facts': MemoryFact.objects.filter(owner=request.user, node=node, active=True,
                                            confidentiality__in=('normal', 'personal'))[:8],
        'followups': FollowUp.objects.filter(owner=request.user, node=node, done=False)[:5],
        'events': Event.objects.filter(owner=request.user, participants=node,
                                       date__gte=timezone.localdate()).order_by('date')[:4],
    })


@login_required
@require_POST
def share_link_create_api(request, pk):
    node = get_object_or_404(Node, owner=request.user, pk=pk, merged_into__isnull=True)
    data = _body(request) or {}
    try:
        days = max(1, min(int(data.get('days', 7)), 30))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'مدت اعتبار نامعتبر است.'}, status=400)
    link = ShareLink.objects.create(owner=request.user, node=node,
                                    expires_at=timezone.now() + timedelta(days=days))
    return JsonResponse({'ok': True, 'token': str(link.token), 'expires_at': link.expires_at.isoformat()})


@login_required
@require_POST
def share_link_revoke_api(request, token):
    link = get_object_or_404(ShareLink, owner=request.user, token=token, revoked=False)
    link.revoked = True
    link.save(update_fields=['revoked'])
    return JsonResponse({'ok': True})


def shared_person_card_view(request, token):
    link = get_object_or_404(ShareLink, token=token, revoked=False,
                             expires_at__gt=timezone.now())
    facts = MemoryFact.objects.filter(owner=link.owner, node=link.node, active=True,
                                      confidentiality='normal')[:6]
    return render(request, 'relationship_life/shared_person_card.html', {'node': link.node, 'facts': facts})


@login_required
def meeting_briefing_api(request, pk):
    node = get_object_or_404(Node, owner=request.user, pk=pk, merged_into__isnull=True)
    data = _briefing(request.user, node)
    existing_gifts = set(GiftIdea.objects.filter(owner=request.user, node=node).values_list('title', flat=True))
    gift_suggestions = [f.value for f in data['facts'] if f.category in ('interest', 'preference')
                        and f.value not in existing_gifts][:3]
    return JsonResponse({
        'person': node.display_name(),
        'last_interaction': str(data['last_interaction'].date) if data['last_interaction'] else None,
        'facts': [{'category': fact.get_category_display(), 'value': fact.value,
                   'confidence': fact.effective_confidence, 'source': f'{fact.source} #{fact.source_id or "—"}'}
                  for fact in data['facts'] if fact.ai_usable and fact.confidentiality != 'no_ai'],
        'commitments': [{'id': item.id, 'text': item.text, 'responsible': item.responsible,
                         'due_date': str(item.due_date or '')} for item in data['commitments']],
        'debts': [{'amount': item.remaining, 'direction': item.direction} for item in data['debts']],
        'events': [{'title': item.title, 'date': str(item.date)} for item in data['events']],
        'boundaries': data['safety'].boundaries if data['safety'] else '',
        'paused': bool(data['safety'] and data['safety'].pause_contact_suggestions),
        'gift_suggestions': gift_suggestions,
    })


@login_required
def introduction_brief_api(request):
    left = get_object_or_404(Node, owner=request.user, pk=request.GET.get('left'))
    right = get_object_or_404(Node, owner=request.user, pk=request.GET.get('right'))
    if left == right:
        return JsonResponse({'error': 'دو شخص متفاوت انتخاب کن.'}, status=400)
    left_values = set(MemoryFact.objects.filter(owner=request.user, node=left, active=True,
        confidentiality__in=('normal', 'personal'), category__in=('interest','value')).values_list('value', flat=True))
    right_values = set(MemoryFact.objects.filter(owner=request.user, node=right, active=True,
        confidentiality__in=('normal', 'personal'), category__in=('interest','value')).values_list('value', flat=True))
    caution = NodeSafetySetting.objects.filter(owner=request.user, node__in=(left, right),
                                               pause_contact_suggestions=True).exists()
    return JsonResponse({'left': left.display_name(), 'right': right.display_name(),
                         'shared_topics': sorted(left_values & right_values),
                         'safe_to_suggest': not caution,
                         'note': 'هیچ دادهٔ حساس، مالی یا سلامت در این پیشنهاد به اشتراک گذاشته نمی‌شود.'})


@login_required
@require_POST
def quick_capture_api(request):
    data = _body(request)
    if data is None:
        return JsonResponse({'error': 'JSON نامعتبر است.'}, status=400)
    kind = data.get('kind')
    node = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
    text = str(data.get('text', '')).strip()[:1200]
    if kind != 'moment' and not node:
        return JsonResponse({'error': 'شخص معتبر لازم است.'}, status=400)
    if not text:
        return JsonResponse({'error': 'متن خالی است.'}, status=400)
    if kind == 'moment':
        entry = JournalEntry.objects.create(owner=request.user, text=text, entry_date=timezone.localdate(),
                                            occurred_at=timezone.now(), entry_kind='moment')
        if node: entry.mentioned_nodes.add(node)
        from .memory_pipeline import capture_text
        suggestions = capture_text(request.user, text, 'journal', entry.id, node=node)
        return JsonResponse({'ok': True, 'id': entry.id, 'suggestions': len(suggestions)})
    if kind == 'interaction':
        obj = Interaction.objects.create(owner=request.user, node=node, kind=data.get('interaction_kind', 'other'),
                                         date=timezone.localdate(), feeling=int(data.get('feeling', 0)), note=text[:300])
    elif kind == 'commitment':
        obj = Commitment.objects.create(owner=request.user, node=node,
            responsible=data.get('responsible') if data.get('responsible') in ('me', 'them') else 'me',
            text=text[:300], due_date=data.get('due_date') or None)
    elif kind == 'gift':
        obj = GiftIdea.objects.create(owner=request.user, node=node, title=text[:200],
                                      occasion=str(data.get('occasion', ''))[:100], budget=data.get('budget') or None)
    else:
        return JsonResponse({'error': 'نوع ثبت نامعتبر است.'}, status=400)
    from .memory_pipeline import capture_node_note
    suggestions = capture_node_note(request.user, node, text, kind, obj.id)
    return JsonResponse({'ok': True, 'id': obj.id, 'suggestions': len(suggestions)})


@login_required
@require_POST
def meeting_reflection_api(request):
    data = _body(request) or {}
    node = Node.objects.filter(owner=request.user, pk=data.get('node_id')).first()
    summary = str(data.get('summary', '')).strip()
    if not node or not summary:
        return JsonResponse({'error': 'شخص و خلاصه لازم است.'}, status=400)
    reflection = MeetingReflection.objects.create(
        owner=request.user, node=node, summary=summary[:3000], feeling=int(data.get('feeling', 0)),
        relationship_change=data.get('relationship_change') if data.get('relationship_change') in ('better','same','worse') else 'same')
    Interaction.objects.create(owner=request.user, node=node, kind='meet', date=timezone.localdate(),
                               feeling=reflection.feeling, note=summary[:300])
    entry = JournalEntry.objects.create(owner=request.user, text=summary, entry_date=timezone.localdate(),
                                        occurred_at=timezone.now(), entry_kind='moment', tags=['ملاقات'])
    entry.mentioned_nodes.add(node)
    from .memory_pipeline import capture_text
    suggestions = capture_text(request.user, summary, 'journal', entry.id, node=node)
    return JsonResponse({'ok': True, 'id': reflection.id, 'suggestions': len(suggestions)})


@login_required
@require_POST
def commitment_action_api(request, pk):
    item = get_object_or_404(Commitment, owner=request.user, pk=pk)
    data = _body(request) or {}
    if data.get('action') == 'done':
        item.status, item.completed_at = 'done', timezone.now()
    elif data.get('action') == 'dismiss':
        item.status = 'dismissed'
    else:
        return JsonResponse({'error': 'عمل نامعتبر است.'}, status=400)
    item.save(update_fields=['status', 'completed_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def safety_setting_api(request, pk):
    node = get_object_or_404(Node, owner=request.user, pk=pk)
    data = _body(request) or {}
    setting, _ = NodeSafetySetting.objects.update_or_create(owner=request.user, node=node, defaults={
        'pause_contact_suggestions': bool(data.get('pause_contact_suggestions')),
        'hide_emotional_reminders': bool(data.get('hide_emotional_reminders')),
        'no_contact_until': data.get('no_contact_until') or None,
        'boundaries': str(data.get('boundaries', ''))[:2000],
    })
    return JsonResponse({'ok': True, 'id': setting.id})


@login_required
@require_POST
def nvc_draft_api(request, pk):
    """Compose an NVC draft for a hard conversation with this person.
    Optionally saves it as a follow-up when ``save`` is truthy."""
    node = get_object_or_404(Node, owner=request.user, pk=pk)
    data = _body(request) or {}
    from .grounded_insights import nvc_compose
    result = nvc_compose(
        data.get('observation', ''), data.get('feeling', ''),
        data.get('need', ''), data.get('request', ''),
    )
    if data.get('save') and result['draft'] and not result['draft'].startswith('برای ساختن'):
        from .models import FollowUp
        FollowUp.objects.create(
            owner=request.user, node=node,
            text=f'[گفت‌وگو] {result["draft"][:280]}',
        )
        result['saved'] = True
    return JsonResponse({'ok': True, **result})


@login_required
def person_export(request, pk):
    node = get_object_or_404(Node, owner=request.user, pk=pk)
    payload = {'person': {'username': node.username, 'name': node.display_name()},
               'memory': list(MemoryFact.objects.filter(owner=request.user, node=node).values()),
               'commitments': list(Commitment.objects.filter(owner=request.user, node=node).values()),
               'gifts': list(GiftIdea.objects.filter(owner=request.user, node=node).values()),
               'interactions': list(Interaction.objects.filter(owner=request.user, node=node).values()),
               'debts': list(Debt.objects.filter(owner=request.user, node=node).values()),
               'events': list(Event.objects.filter(owner=request.user, participants=node).values()),
               'journal': list(JournalEntry.objects.filter(owner=request.user, mentioned_nodes=node).values()),
               'relationships': list(Relationship.objects.filter(owner=request.user).filter(
                   Q(source=node) | Q(target=node)).values()),
               'safety': list(NodeSafetySetting.objects.filter(owner=request.user, node=node).values())}
    response = HttpResponse(json.dumps(payload, ensure_ascii=False, default=str, indent=2), content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="person-{node.id}.json"'
    return response


@login_required
@require_POST
@transaction.atomic
def person_delete_complete(request, pk):
    node = get_object_or_404(Node, owner=request.user, pk=pk)
    data = _body(request) or {}
    if node.pk == request.user.root_node_id:
        return JsonResponse({'error': 'نود اصلی حساب قابل حذف نیست.'}, status=400)
    if data.get('confirm') != node.username:
        return JsonResponse({'error': 'برای حذف، username شخص را دقیق وارد کن.'}, status=400)
    Relationship.objects.filter(owner=request.user).filter(Q(source=node) | Q(target=node)).delete()
    Information.objects.filter(node=node).delete()
    node.delete()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def csv_import_preview(request):
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'فایل CSV لازم است.'}, status=400)
    try:
        text = uploaded.read().decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(text)))[:500]
    except (UnicodeDecodeError, csv.Error):
        return JsonResponse({'error': 'CSV معتبر UTF-8 نیست.'}, status=400)
    clean = [{'username': str(r.get('username') or r.get('name') or '').strip()[:100],
              'name': str(r.get('name') or '').strip()[:200],
              'phone': str(r.get('phone') or '').strip()[:20]} for r in rows]
    return JsonResponse({'rows': [row for row in clean if row['username']]})


def _parse_vcards(text):
    """Minimal vCard 2.1/3.0/4.0 reader → [{name, username, phone, email}]."""
    import re as _re
    # unfold folded lines (continuation lines start with space or tab)
    unfolded = _re.sub(r'\r?\n[ \t]', '', text)
    cards = []
    cur = None
    for line in unfolded.splitlines():
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        if up == 'BEGIN:VCARD':
            cur = {'name': '', 'phone': '', 'email': ''}
        elif up == 'END:VCARD':
            if cur:
                cards.append(cur)
            cur = None
        elif cur is None:
            continue
        else:
            key, _, value = s.partition(':')
            key = key.split(';', 1)[0].upper()
            value = value.strip()
            if key == 'FN' and value:
                cur['name'] = value[:200]
            elif key == 'N' and not cur['name']:
                parts = [p.strip() for p in value.split(';') if p.strip()]
                cur['name'] = ' '.join(reversed(parts[:2]))[:200] if parts else ''
            elif key == 'TEL' and not cur['phone']:
                cur['phone'] = _re.sub(r'[^\d+]', '', value)[:20]
            elif key == 'EMAIL' and not cur['email']:
                cur['email'] = value[:120]
            elif key == 'ORG' and not cur['name']:
                cur['name'] = value.replace(';', ' ').strip()[:200]
    return cards


@login_required
@require_POST
def vcard_import_preview(request):
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'فایل vCard (.vcf) لازم است.'}, status=400)
    try:
        text = uploaded.read()[:2 * 1024 * 1024].decode('utf-8-sig', errors='ignore')
    except Exception:
        return JsonResponse({'error': 'فایل قابل خواندن نبود.'}, status=400)
    cards = _parse_vcards(text)[:1000]
    rows = []
    seen = set()
    for c in cards:
        name = c['name'].strip()
        if not name:
            continue
        base = ''.join(ch if ch.isalnum() else '_' for ch in name.lower()).strip('_') or 'contact'
        username = base
        i = 1
        while username in seen:
            i += 1
            username = f'{base}_{i}'
        seen.add(username)
        rows.append({'username': username[:100], 'name': name, 'phone': c['phone']})
    return JsonResponse({'rows': rows, 'count': len(rows)})


@login_required
@require_POST
def csv_import_apply(request):
    data = _body(request) or {}
    rows = data.get('rows')
    if not isinstance(rows, list):
        return JsonResponse({'error': 'ردیف‌ها معتبر نیستند.'}, status=400)
    created = 0
    for row in rows[:500]:
        username = str(row.get('username', '')).strip()[:100]
        if username:
            _, was_created = Node.objects.get_or_create(owner=request.user, username=username,
                defaults={'name': str(row.get('name', ''))[:200], 'phone_number': str(row.get('phone', ''))[:20]})
            created += int(was_created)
    return JsonResponse({'ok': True, 'created': created})
