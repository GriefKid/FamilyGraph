"""One-click undo for the few destructive/bulk actions.

record_undoable(user, kind, label, **payload) after doing the action; the
frontend shows an 'برگردان' toast that POSTs /api/undo/<id>/.
Supported kinds: 'merge', 'import', 'bulk_delete'.
"""
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

UNDO_WINDOW_SECONDS = 900   # actions older than this can't be undone


def record_undoable(user, kind, label, **payload):
    from .models import UndoableAction
    return UndoableAction.objects.create(
        owner=user, kind=kind, label=str(label)[:200], payload=payload or {},
    )


def _undo_merge(user, payload):
    from .models import NodeMergeOperation, Interaction, FollowUp, Debt, MemoryFact, NodeAlias, \
        Relationship, JournalEntry, Event
    op = NodeMergeOperation.objects.filter(
        pk=payload.get('operation_id'), owner=user, status='applied',
    ).first()
    if not op:
        return False, 'این ادغام دیگر قابل‌برگشت نیست.'
    model_map = {'interactions': Interaction, 'followups': FollowUp, 'debts': Debt,
                 'facts': MemoryFact, 'aliases': NodeAlias}
    snap = op.snapshot or {}
    for label, ids in (snap.get('models') or {}).items():
        model_map[label].objects.filter(
            owner=user, id__in=ids, node=op.primary_node,
        ).update(node=op.duplicate_node)
    for item in snap.get('relationships', []):
        rel = Relationship.objects.filter(owner=user, id=item['id']).first()
        if rel:
            if item.get('source_was_duplicate'):
                rel.source = op.duplicate_node
            else:
                rel.target = op.duplicate_node
            rel.save()
    for eid in snap.get('journal_primary_added', []):
        entry = JournalEntry.objects.filter(owner=user, id=eid).first()
        if entry:
            entry.mentioned_nodes.remove(op.primary_node)
    for eid in snap.get('journals', []):
        entry = JournalEntry.objects.filter(owner=user, id=eid).first()
        if entry:
            entry.mentioned_nodes.add(op.duplicate_node)
    for eid in snap.get('event_primary_added', []):
        ev = Event.objects.filter(owner=user, id=eid).first()
        if ev:
            ev.participants.remove(op.primary_node)
    for eid in snap.get('events', []):
        ev = Event.objects.filter(owner=user, id=eid).first()
        if ev:
            ev.participants.add(op.duplicate_node)
    if snap.get('generated_alias_id'):
        NodeAlias.objects.filter(owner=user, id=snap['generated_alias_id']).delete()
    op.duplicate_node.merged_into = None
    op.duplicate_node.save(update_fields=['merged_into'])
    op.status = 'undone'
    op.save(update_fields=['status'])
    return True, 'ادغام برگردانده شد.'


def _undo_import(user, payload):
    from .models import Node
    ids = [i for i in (payload.get('node_ids') or []) if isinstance(i, int)]
    if not ids:
        return False, 'چیزی برای برگرداندن نیست.'
    deleted, _ = Node.objects.filter(owner=user, id__in=ids).delete()
    return True, f'{deleted} مورد از ورودِ اخیر حذف شد.'


_HANDLERS = {'merge': _undo_merge, 'import': _undo_import, 'bulk_delete': _undo_import}


@login_required
@require_POST
def undo_action_api(request, pk):
    from .models import UndoableAction
    action = get_object_or_404(UndoableAction, pk=pk, owner=request.user, undone=False)
    age = (timezone.now() - action.created_at).total_seconds()
    if age > UNDO_WINDOW_SECONDS:
        return JsonResponse({'error': 'مهلت برگرداندن این عملیات گذشته است.'}, status=400)
    handler = _HANDLERS.get(action.kind)
    if not handler:
        return JsonResponse({'error': 'این عملیات قابل‌برگشت نیست.'}, status=400)
    ok, message = handler(request.user, action.payload or {})
    if ok:
        action.undone = True
        action.save(update_fields=['undone'])
        return JsonResponse({'ok': True, 'message': message})
    return JsonResponse({'error': message}, status=400)
