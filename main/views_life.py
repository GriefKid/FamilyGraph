"""
views_life.py — رویدادهای زندگی + هدف رابطه + گزارش هفتگی (V10)
"""
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Node, JournalEntry
from .utils_jalali import jalali_str, parse_date_input

MIGRATION_MSG = 'جدول‌های جدید هنوز ساخته نشدن — migrate_and_run.bat رو یه بار اجرا کن.'


def _body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  رویدادهای زندگی
# ═══════════════════════════════════════════════════════════════

@login_required
def life_event_create_api(request):
    """POST {node_id, kind, date?, title?}"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    try:
        node = Node.objects.get(pk=body.get('node_id'), owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'شخص پیدا نشد'}, status=404)

    from .models import LIFE_EVENT_RITUALS
    kind = body.get('kind')
    if kind not in LIFE_EVENT_RITUALS:
        return JsonResponse({'error': 'نوع نامعتبر'}, status=400)

    date_str = (body.get('date') or '').strip()
    if date_str:
        try:
            date_val = parse_date_input(date_str)
        except ValueError:
            return JsonResponse({'error': 'فرمت تاریخ: ۱۴۰۴/۰۱/۰۱'}, status=400)
    else:
        date_val = timezone.localdate()

    title = (body.get('title') or '').strip()[:200]

    try:
        from .models import LifeEvent
        ev = LifeEvent.objects.create(node=node, kind=kind, date=date_val,
                                      title=title, owner=request.user)
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)

    suggestions = []
    try:
        from .memory_pipeline import capture_node_note
        suggestions = capture_node_note(request.user, node, title or ev.get_kind_display(), 'life_event', ev.id)
    except Exception:
        pass
    return JsonResponse({'ok': True, 'id': ev.id, 'suggestions': len(suggestions),
                         'kind_label': ev.get_kind_display(),
                         'date_fa': jalali_str(ev.date)})


@login_required
def life_event_delete_api(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        from .models import LifeEvent
        LifeEvent.objects.filter(pk=pk, owner=request.user).delete()
        return JsonResponse({'ok': True})
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)


# ═══════════════════════════════════════════════════════════════
#  هدف رابطه
# ═══════════════════════════════════════════════════════════════

@login_required
def goal_create_api(request):
    """POST {node_id, text} — baseline از سلامت فعلی گرفته می‌شه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    text = (body.get('text') or '').strip()[:300]
    if not text:
        return JsonResponse({'error': 'هدف خالیه'}, status=400)
    try:
        node = Node.objects.get(pk=body.get('node_id'), owner=request.user)
    except Node.DoesNotExist:
        return JsonResponse({'error': 'شخص پیدا نشد'}, status=404)

    baseline = None
    try:
        from .health import compute_health
        h = compute_health(request.user).get(node.id)
        if h and h.get('score') is not None:
            baseline = h['score']
    except Exception:
        pass

    try:
        from .models import RelationshipGoal
        g = RelationshipGoal.objects.create(node=node, text=text,
                                            baseline_score=baseline,
                                            owner=request.user)
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)

    suggestions = []
    try:
        from .memory_pipeline import capture_node_note
        suggestions = capture_node_note(request.user, node, text, 'relationship_goal', g.id)
    except Exception:
        pass
    return JsonResponse({'ok': True, 'id': g.id, 'baseline': baseline,
                         'suggestions': len(suggestions)})


@login_required
def goal_close_api(request, pk):
    """POST {status: achieved|abandoned}"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    body = _body(request) or {}
    status = body.get('status')
    if status not in ('achieved', 'abandoned'):
        return JsonResponse({'error': 'status نامعتبر'}, status=400)
    try:
        from .models import RelationshipGoal
        g = RelationshipGoal.objects.get(pk=pk, owner=request.user)
        g.status = status
        g.closed_at = timezone.now()
        g.save()
        return JsonResponse({'ok': True})
    except (OperationalError, ProgrammingError):
        return JsonResponse({'error': MIGRATION_MSG}, status=503)
    except Exception:
        return JsonResponse({'error': 'پیدا نشد'}, status=404)


# ═══════════════════════════════════════════════════════════════
#  گزارش هفتگی — /weekly/
# ═══════════════════════════════════════════════════════════════

@login_required
def weekly_view(request):
    user = request.user
    today = timezone.localdate()
    week_ago = today - timedelta(days=7)
    two_weeks = today - timedelta(days=14)

    # ── تعامل‌ها: این هفته vs هفته قبل ──
    this_week = last_week = 0
    per_person_delta = []
    try:
        from .models import Interaction
        rows = list(Interaction.objects.filter(owner=user, date__gte=two_weeks)
                    .values_list('node_id', 'date'))
        counts_this, counts_last = {}, {}
        for nid, d in rows:
            if d > week_ago:
                this_week += 1
                counts_this[nid] = counts_this.get(nid, 0) + 1
            else:
                last_week += 1
                counts_last[nid] = counts_last.get(nid, 0) + 1
        names = {n.id: n.display_name() for n in Node.objects.filter(owner=user)}
        all_ids = set(counts_this) | set(counts_last)
        for nid in all_ids:
            delta = counts_this.get(nid, 0) - counts_last.get(nid, 0)
            if delta:
                per_person_delta.append({'name': names.get(nid, '؟'),
                                         'node_id': nid, 'delta': delta,
                                         'this': counts_this.get(nid, 0)})
        per_person_delta.sort(key=lambda x: -x['delta'])
    except Exception:
        pass
    warmer = [p for p in per_person_delta if p['delta'] > 0][:5]
    colder = [p for p in per_person_delta if p['delta'] < 0][-5:][::-1]

    # ── دستاوردهای هفته ──
    from .models import AlertAction
    done_actions = AlertAction.objects.filter(
        owner=user, action='completed',
        created_at__date__gt=week_ago).count()
    fu_done = debts_settled = 0
    try:
        from .models import FollowUp
        fu_done = FollowUp.objects.filter(owner=user, done=True,
                                          done_at__date__gt=week_ago).count()
    except Exception:
        pass
    try:
        from .models import Debt
        debts_settled = Debt.objects.filter(owner=user, settled=True,
                                            settled_at__date__gt=week_ago).count()
    except Exception:
        pass

    # ── بهترین لحظه‌ی هفته (ژورنال با حس خوب) ──
    best_moment = None
    try:
        pos = ['عالی', 'خوب', 'شاد', 'خوشحال', 'happy', 'great']
        for e in JournalEntry.objects.filter(owner=user, created_at__date__gt=week_ago).exclude(mood=''):
            if any(p in e.mood for p in pos):
                best_moment = {'text': e.text[:220], 'mood': e.mood,
                               'date_fa': jalali_str(e.entry_date or e.created_at.date())}
                break
    except Exception:
        pass

    # ── استریک + چک-این‌های هفته ──
    streak = checkins = 0
    try:
        from .views_checkin import journal_streak
        streak = journal_streak(user)
        checkins = JournalEntry.objects.filter(
            owner=user, created_at__date__gt=week_ago).count()
    except Exception:
        pass

    # ── رویدادها و حافظه‌های تازه: مواد خامی که گزارش را قابل‌اقدام می‌کنند ──
    event_count = 0
    event_highlights = []
    memory_count = 0
    memory_highlights = []
    try:
        from .models import Event
        event_qs = Event.objects.filter(owner=user, date__gt=week_ago, date__lte=today)
        event_count = event_qs.count()
        event_highlights = list(event_qs.prefetch_related('participants').order_by('date', 'event_time')[:4])
    except Exception:
        pass
    try:
        from .models import MemoryFact
        memory_qs = MemoryFact.objects.filter(
            owner=user, created_at__date__gt=week_ago, created_at__date__lte=today,
            active=True,
        ).select_related('node').order_by('-confidence', '-created_at')
        memory_count = memory_qs.count()
        memory_highlights = list(memory_qs[:5])
    except Exception:
        pass

    # ── برنامه هفته بعد: ۳ نفر که بیشترین نیاز رو دارن ──
    plan = []
    try:
        from .health import compute_health
        hmap = compute_health(user)
        needy = [h for h in hmap.values()
                 if h['status'] in ('yellow', 'red') and h['days_since'] is not None]
        needy.sort(key=lambda h: h['score'] or 0)
        plan = [{'name': h['name'], 'node_id': h['node_id'],
                 'days': h['days_since'], 'status': h['status']} for h in needy[:3]]
    except Exception:
        pass

    # ── اهداف فعال ──
    goals = []
    try:
        from .models import RelationshipGoal
        from .health import compute_health
        hmap = compute_health(user)
        for g in RelationshipGoal.objects.filter(owner=user, status='active').select_related('node')[:5]:
            cur = hmap.get(g.node_id, {}).get('score')
            prog = None
            if cur is not None and g.baseline_score is not None:
                prog = cur - g.baseline_score
            goals.append({'text': g.text, 'name': g.node.display_name(),
                          'node_id': g.node_id, 'progress': prog,
                          'current': cur, 'baseline': g.baseline_score})
    except Exception:
        pass

    narrative = []
    if this_week > last_week:
        narrative.append(f'این هفته تعامل‌هایت از {last_week} به {this_week} رسید و شبکه‌ات فعال‌تر بود.')
    elif this_week < last_week:
        narrative.append(f'این هفته {this_week} تعامل ثبت شد؛ کمی آرام‌تر از هفتهٔ قبل با {last_week} تعامل.')
    else:
        narrative.append(f'ریتم ارتباط‌ها با {this_week} تعامل نسبت به هفتهٔ قبل ثابت ماند.')
    if warmer:
        narrative.append(f'رابطه با {warmer[0]["name"]} بیشترین رشد ثبت‌شده را داشت.')
    if colder:
        narrative.append(f'{colder[0]["name"]} نسبت به هفتهٔ قبل کمتر در جریان روزهایت بود.')
    if fu_done or debts_settled:
        narrative.append(f'{fu_done} پیگیری و {debts_settled} حساب مالی بسته شد.')
    next_steps = [f'یک ارتباط کوتاه با {item["name"]}' for item in plan[:3]]

    return render(request, 'daily/weekly.html', {
        'jalali_today':   jalali_str(today),
        'week_start_fa':  jalali_str(week_ago + timedelta(days=1)),
        'this_week':      this_week,
        'last_week':      last_week,
        'trend_up':       this_week >= last_week,
        'warmer':         warmer,
        'colder':         colder,
        'done_actions':   done_actions,
        'fu_done':        fu_done,
        'debts_settled':  debts_settled,
        'best_moment':    best_moment,
        'streak':         streak,
        'checkins':       checkins,
        'event_count':    event_count,
        'event_highlights': event_highlights,
        'memory_count':   memory_count,
        'memory_highlights': memory_highlights,
        'plan':           plan,
        'goals':          goals,
        'narrative':      narrative,
        'next_steps':     next_steps,
    })


@login_required
def monthly_recap_view(request):
    """Create a private monthly recap from existing activity; no setup required."""
    user = request.user
    today = timezone.localdate()
    month_start = today.replace(day=1)
    previous_month_end = month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

    from .models import Commitment, Event, FollowUp, Interaction
    from .models import MemoryFact

    interactions = Interaction.objects.filter(owner=user, date__range=(month_start, today))
    previous_interactions = Interaction.objects.filter(
        owner=user, date__range=(previous_month_start, previous_month_end)
    )
    most_present = list(
        interactions.values('node_id').annotate(total=Count('id')).order_by('-total', 'node_id')[:5]
    )
    names = {
        node.id: node.display_name()
        for node in Node.objects.filter(owner=user, id__in=[row['node_id'] for row in most_present])
    }
    for row in most_present:
        row['name'] = names.get(row['node_id'], 'بدون نام')

    month_memories = MemoryFact.objects.filter(
        owner=user, active=True, created_at__date__range=(month_start, today),
    ).select_related('node').order_by('-confidence', '-created_at')

    next_steps = []
    try:
        from .health import compute_health
        needs_attention = [row for row in compute_health(user).values()
                           if row.get('status') in ('red', 'yellow') and row.get('days_since') is not None]
        needs_attention.sort(key=lambda row: row.get('score') if row.get('score') is not None else 101)
        next_steps = needs_attention[:3]
    except Exception:
        pass

    return render(request, 'daily/monthly_recap.html', {
        'month_start': month_start,
        'today': today,
        'interaction_count': interactions.count(),
        'previous_interaction_count': previous_interactions.count(),
        'interaction_delta': interactions.count() - previous_interactions.count(),
        'people_count': interactions.values('node_id').distinct().count(),
        'most_present': most_present,
        'completed_commitments': Commitment.objects.filter(
            owner=user, status='done', completed_at__date__range=(month_start, today)
        ).count(),
        'completed_followups': FollowUp.objects.filter(
            owner=user, done=True, done_at__date__range=(month_start, today)
        ).count(),
        'journal_count': JournalEntry.objects.filter(owner=user, entry_date__range=(month_start, today)).count(),
        'event_count': Event.objects.filter(owner=user, date__range=(month_start, today)).count(),
        'memory_count': month_memories.count(),
        'memory_highlights': list(month_memories[:5]),
        'next_steps': next_steps,
    })


@login_required
def yearbook_view(request):
    """A printable 'year in relationships' book, built entirely from stored
    data — no AI. ?year=<Jalali year> (default current); add ?month=1..12
    for a single Jalali month."""
    import jdatetime
    from django.db.models import Q, Max, Min
    from .models import Interaction, Event, FollowUp, Relationship, RelationshipStrengthHistory
    user = request.user

    today = timezone.localdate()
    j_today = jdatetime.date.fromgregorian(date=today)
    try:
        jy = int(request.GET.get('year') or j_today.year)
    except (TypeError, ValueError):
        jy = j_today.year
    try:
        jm = int(request.GET.get('month') or 0)
    except (TypeError, ValueError):
        jm = 0
    if jm and 1 <= jm <= 12 and (request.GET.get('year') is None):
        # a bare ?month= means "this Jalali year"
        jy = j_today.year

    if jm and 1 <= jm <= 12:
        scope = 'month'
        start = jdatetime.date(jy, jm, 1).togregorian()
        last_day = 31 if jm <= 6 else 30
        while last_day > 28:
            try:
                end = jdatetime.date(jy, jm, last_day).togregorian()
                break
            except ValueError:
                last_day -= 1
        period_label = f'{jdatetime.date.j_months_fa[jm - 1]} {jy}'
        prev_jm, prev_jy = (12, jy - 1) if jm == 1 else (jm - 1, jy)
        next_jm, next_jy = (1, jy + 1) if jm == 12 else (jm + 1, jy)
        nav_prev = f'?year={prev_jy}&month={prev_jm}'
        nav_next = f'?year={next_jy}&month={next_jm}'
    else:
        scope = 'year'
        start = jdatetime.date(jy, 1, 1).togregorian()
        try:
            end = jdatetime.date(jy, 12, 30).togregorian()
        except ValueError:
            end = jdatetime.date(jy, 12, 29).togregorian()
        period_label = str(jy)
        nav_prev = f'?year={jy - 1}'
        nav_next = f'?year={jy + 1}'
    end = min(end, today)
    rng = (start, end)

    inter = Interaction.objects.filter(owner=user, date__range=rng)
    journals = JournalEntry.objects.filter(owner=user, entry_date__range=rng)
    events = Event.objects.filter(owner=user, date__range=rng)

    # آدم‌های پررنگ سال
    top_people = []
    for row in (inter.exclude(node__isnull=True)
                .values('node_id')
                .annotate(n=Count('id'), first=Min('date'), last=Max('date'))
                .order_by('-n')[:8]):
        node = Node.objects.filter(pk=row['node_id']).first()
        if node:
            top_people.append({'name': node.display_name(), 'id': node.id, 'n': row['n'],
                               'first_fa': jalali_str(row['first']), 'last_fa': jalali_str(row['last'])})

    # خاطره‌های ماندگار — بلندترین‌ها + آنهایی که حس دارند
    highlights = list(
        journals.exclude(text='').order_by('-occurred_at')[:200]
    )
    highlights.sort(key=lambda e: (bool(e.mood), len(e.text or '')), reverse=True)
    memory_highlights = [{
        'date_fa': jalali_str(e.entry_date or (e.created_at.date() if e.created_at else today)),
        'text': (e.text or '')[:400], 'mood': e.mood,
    } for e in highlights[:6]]

    # رابطه‌هایی که امسال قوی‌تر شدند
    grew = []
    for rel in (Relationship.objects.filter(owner=user)
                .filter(Q(source=user.root_node) | Q(target=user.root_node))
                .select_related('source', 'target')):
        series = list(RelationshipStrengthHistory.objects
                      .filter(owner=user, relationship=rel, changed_at__date__range=rng)
                      .order_by('changed_at').values_list('strength', flat=True))
        if len(series) >= 2 and series[-1] > series[0]:
            other = rel.target if rel.source_id == user.root_node_id else rel.source
            grew.append({'name': other.display_name(), 'from': series[0], 'to': series[-1]})

    # قدم‌های آرام سال بعد
    next_steps = []
    try:
        from .health import attention_priority, compute_health
        ranked = sorted(attention_priority(user, compute_health(user)).items(),
                        key=lambda kv: -kv[1]['score'])
        names = dict(Node.objects.filter(owner=user, id__in=[n for n, _ in ranked[:3]])
                     .values_list('id', 'username'))
        for nid, row in ranked[:3]:
            if row['score'] >= 20:
                next_steps.append({'name': names.get(nid, 'یک نفر'),
                                   'why': (row['factors'] or ['یک قدم توجه'])[0]})
    except Exception:
        pass

    stats = {
        'people_total': Node.objects.filter(owner=user, merged_into__isnull=True).exclude(pk=user.root_node_id).count(),
        'people_new': Node.objects.filter(owner=user, created_at__date__range=rng).exclude(pk=user.root_node_id).count(),
        'interactions': inter.count(),
        'journals': journals.count(),
        'events': events.count(),
        'followups_done': FollowUp.objects.filter(owner=user, done=True, done_at__date__range=rng).count(),
    }

    return render(request, 'daily/yearbook.html', {
        'scope': scope,
        'period_label': period_label,
        'period_word': 'ماه' if scope == 'month' else 'سال',
        'nav_prev': nav_prev,
        'nav_next': nav_next,
        'range_fa': f'{jalali_str(start)} تا {jalali_str(end)}',
        'stats': stats,
        'top_people': top_people,
        'memory_highlights': memory_highlights,
        'grew': grew,
        'next_steps': next_steps,
    })


@login_required
def monthbook_view(request):
    """Convenience entry point: the relationship book for the current Jalali
    month. Anything explicit in the querystring is passed straight through."""
    import jdatetime
    from django.shortcuts import redirect
    if request.GET.get('month'):
        return redirect(f"/yearbook/?{request.GET.urlencode()}")
    jt = jdatetime.date.fromgregorian(date=timezone.localdate())
    return redirect(f'/yearbook/?year={jt.year}&month={jt.month}')
