from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import render
from django.utils import timezone

from datetime import date, timedelta

from .models import Commitment, Debt, Event, FollowUp, Node


@login_required
def people_hub(request):
    return render(request, 'hubs/people.html')


@login_required
def insight_hub(request):
    return render(request, 'hubs/insight.html')


@login_required
def relationship_work_hub(request):
    user = request.user
    today = timezone.localdate()
    review_start = today - timedelta(days=7)
    week_end = today + timedelta(days=7)
    month_end = today + timedelta(days=30)
    scope = request.GET.get('scope', 'focus')
    if scope not in {'focus', 'overdue', 'week', 'all'}:
        scope = 'focus'

    followups_qs = FollowUp.objects.filter(
        owner=user, node__owner=user, done=False,
    ).select_related('node').order_by('due_date', '-created_at')
    commitments_qs = Commitment.objects.filter(
        owner=user, node__owner=user, status='open',
    ).select_related('node').order_by('due_date', '-created_at')
    debts_qs = Debt.objects.filter(
        owner=user, node__owner=user, settled=False,
    ).select_related('node').order_by('due_date', '-created_at')
    events_qs = Event.objects.filter(
        owner=user, post_event_prompted=False,
        date__gte=review_start, date__lte=month_end,
    ).prefetch_related(
        Prefetch('participants', queryset=Node.objects.filter(owner=user))
    ).order_by('date', 'event_time')

    counts = {
        'followups': followups_qs.count(),
        'commitments': commitments_qs.count(),
        'debts': debts_qs.count(),
        'events': events_qs.count(),
    }
    overdue_count = (
        followups_qs.filter(due_date__lt=today).count() +
        commitments_qs.filter(due_date__lt=today).count() +
        debts_qs.filter(due_date__lt=today).count() +
        events_qs.filter(date__lt=today).count()
    )

    def due_state(due_date):
        if due_date is None:
            return 'unscheduled'
        if due_date < today:
            return 'overdue'
        if due_date == today:
            return 'today'
        if due_date <= week_end:
            return 'week'
        return 'later'

    queue = []
    for followup in followups_qs[:80]:
        queue.append({
            'kind': 'followup', 'kind_label': 'پیگیری', 'icon': '📌',
            'title': followup.text, 'node': followup.node,
            'due_date': followup.due_date, 'due_state': due_state(followup.due_date),
            'url': '/followups/', 'can_complete': True, 'object_id': followup.id,
        })
    for commitment in commitments_qs[:80]:
        queue.append({
            'kind': 'commitment', 'kind_label': 'تعهد', 'icon': '🤝',
            'title': commitment.text, 'node': commitment.node,
            'due_date': commitment.due_date, 'due_state': due_state(commitment.due_date),
            'url': '/relationship-life/', 'can_complete': True, 'object_id': commitment.id,
            'responsible': commitment.get_responsible_display(),
        })
    for debt in debts_qs[:80]:
        direction = 'طلب تو' if debt.direction == 'they_owe' else 'بدهی تو'
        queue.append({
            'kind': 'debt', 'kind_label': 'حساب مالی', 'icon': '💰',
            'title': f'{direction}: {debt.remaining:,} {debt.currency}',
            'node': debt.node, 'due_date': debt.due_date,
            'due_state': due_state(debt.due_date), 'url': '/ledger/',
            'can_complete': False, 'object_id': debt.id,
            'note': debt.note,
        })
    for event in events_qs[:80]:
        participants = list(event.participants.all())
        queue.append({
            'kind': 'event', 'kind_label': 'رویداد', 'icon': '📅',
            'title': event.title, 'node': participants[0] if participants else None,
            'people': '، '.join(person.display_name() for person in participants[:3]),
            'due_date': event.date, 'due_state': due_state(event.date),
            'url': '/events/', 'can_complete': event.date <= today,
            'object_id': event.id,
        })

    def visible(item):
        if scope == 'overdue':
            return item['due_state'] == 'overdue'
        if scope == 'week':
            return item['due_state'] in {'today', 'week'}
        if scope == 'focus':
            return item['due_state'] in {'overdue', 'today', 'week', 'unscheduled'}
        return True

    rank = {'overdue': 0, 'today': 1, 'week': 2, 'later': 3, 'unscheduled': 4}
    queue = [item for item in queue if visible(item)]
    queue.sort(key=lambda item: (
        rank[item['due_state']],
        item['due_date'] or date.max,
        item['kind'],
        item['object_id'],
    ))
    queue = queue[:40]

    return render(request, 'hubs/relationship_work.html', {
        'scope': scope,
        'queue': queue,
        'queue_count': len(queue),
        'counts': counts,
        'total_open': sum(counts.values()),
        'overdue_count': overdue_count,
        'today': today,
        'week_end': week_end,
    })


@login_required
def import_hub(request):
    return render(request, 'hubs/import.html')
