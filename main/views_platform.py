import base64
import hashlib
import json
import os
from datetime import date as date_type, datetime as datetime_type
from collections import Counter

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import connection
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (AIExtractionTrace, AIQualityEvaluation, AIRequestMetric, Commitment, ExtractionSuggestion, FeatureFlag,
                     Interaction, JournalEntry, KnowledgeTriple, MemoryFact, Node,
                     ObservabilityEvent, Relationship, RelationshipRecommendation)
from .uploads import UploadValidationError, read_limited_upload


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except (TypeError, ValueError):
        return None


@login_required
def platform_tools_view(request):
    return render(request, 'platform/tools.html')


@login_required
def command_palette_api(request):
    q = request.GET.get('q', '').strip()[:80]
    commands = [
        {'title': 'ثبت یک لحظه', 'subtitle': 'خاطره سریع', 'url': '/journal/', 'icon': '✎'},
        {'title': 'ثبت قرض یا طلب', 'subtitle': 'دفتر مالی', 'url': '/ledger/', 'icon': '💰'},
        {'title': 'ساخت رویداد', 'subtitle': 'تقویم', 'url': '/events/create/', 'icon': '📅'},
        {'title': 'جست‌وجوی حافظه', 'subtitle': 'پاسخ منبع‌دار', 'url': '/memory/', 'icon': '🔎'},
        {'title': 'خط زمان خاطره‌ها', 'subtitle': 'عکس‌ها و لحظه‌ها به ترتیب زمان', 'url': '/memory/timeline/', 'icon': '🕰️'},
        {'title': 'افزودن شخص', 'subtitle': 'گراف رابطه', 'url': '/nodes/create/', 'icon': '👤'},
    ]
    if q:
        commands = [item for item in commands if q.casefold() in (item['title'] + item['subtitle']).casefold()]
    people = Node.objects.filter(owner=request.user, merged_into__isnull=True)
    if q:
        normalized = q.replace('ي', 'ی').replace('ك', 'ک')
        variants = {q, normalized, normalized.replace('ی', 'ي'), normalized.replace('ک', 'ك'), normalized.replace('ی', 'ي').replace('ک', 'ك')}
        query_filter = Q()
        for term in variants:
            query_filter |= Q(username__icontains=term) | Q(name__icontains=term) | Q(nickname__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term)
        people = people.filter(query_filter)
    results = commands + [{'title': node.display_name(), 'subtitle': f'@{node.username}',
                           'url': f'/nodes/{node.id}/', 'icon': '◉'} for node in people[:8]]
    return JsonResponse({'results': results[:12]})


@login_required
def onboarding_api(request):
    user = request.user
    goal = (user.feature_overrides or {}).get('onboarding_goal', '')
    steps = [
        {'id': 'person', 'title': 'افزودن اولین شخص مهم', 'description': 'یک نفر مهم را به گراف اضافه کن.', 'done': user.nodes.exclude(pk=user.root_node_id).exists(), 'url': '/nodes/create/'},
        {'id': 'relationship', 'title': 'مشخص‌کردن یک رابطه', 'description': 'بگو این شخص چه نسبتی با تو دارد.', 'done': user.relationships.exists(), 'url': '/relationships/create/'},
        {'id': 'journal', 'title': 'ثبت اولین لحظه', 'description': 'یک خاطرهٔ کوتاه ثبت کن تا شناخت رابطه شروع شود.', 'done': user.journal_entries.exists(), 'url': '/journal/'},
    ]
    goal_choices = [
        {'id': 'family', 'label': 'خانواده‌ام', 'description': 'آدم‌ها و خاطره‌های خانوادگی را مرتب کنم.'},
        {'id': 'friends', 'label': 'دوستانم', 'description': 'رابطه‌های مهمم را در جریان نگه دارم.'},
        {'id': 'memories', 'label': 'خاطره‌ها', 'description': 'لحظه‌ها و اتفاق‌های مهم را ثبت کنم.'},
    ]
    if goal == 'memories':
        steps.sort(key=lambda row: {'journal': 0, 'person': 1, 'relationship': 2}[row['id']])
    elif goal in ('family', 'friends'):
        steps.sort(key=lambda row: {'person': 0, 'relationship': 1, 'journal': 2}[row['id']])
    return JsonResponse({'completed': all(row['done'] for row in steps), 'goal': goal,
                         'goal_choices': goal_choices, 'steps': steps})


@login_required
@require_POST
def onboarding_goal_api(request):
    data = _body(request) or {}
    goal = data.get('goal', '')
    if goal not in {'family', 'friends', 'memories'}:
        return JsonResponse({'error': 'هدف شروع نامعتبر است.'}, status=400)
    overrides = dict(request.user.feature_overrides or {})
    overrides['onboarding_goal'] = goal
    request.user.feature_overrides = overrides
    request.user.save(update_fields=['feature_overrides'])
    return JsonResponse({'ok': True, 'goal': goal})


@login_required
@require_POST
def onboarding_complete_api(request):
    request.user.onboarding_completed = True
    request.user.save(update_fields=['onboarding_completed'])
    return JsonResponse({'ok': True})


@user_passes_test(lambda user: user.is_superuser)
def ai_quality_dashboard(request):
    from .ai_quality import percentile

    suggestions = ExtractionSuggestion.objects.values('kind').annotate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='pending')),
        approved=Count('id', filter=Q(status='approved')),
        dismissed=Count('id', filter=Q(status='dismissed')),
    ).order_by('kind')
    suggestion_rows = []
    for row in suggestions:
        decided = row['approved'] + row['dismissed']
        suggestion_rows.append({
            **row,
            'decided': decided,
            'approval_rate': round(100 * row['approved'] / decided, 1) if decided else None,
        })

    chat_metrics = AIRequestMetric.objects.filter(feature='chat')
    if chat_metrics.exists():
        trace_total = chat_metrics.count()
        durations = list(chat_metrics.order_by('-created_at').values_list('duration_ms', flat=True)[:1000])
        trace_statuses = chat_metrics.values('status').annotate(total=Count('id')).order_by('status')
        provider_rows = list(chat_metrics.values('provider', 'actual_model').annotate(
            total=Count('id'),
            failed=Count('id', filter=Q(status__in=(
                'error', 'timeout', 'degraded_timeout', 'degraded_quality',
            ))),
            avg_ms=Avg('duration_ms'),
        ).order_by('provider', 'actual_model'))
        metric_source = 'چت همدم'
    else:
        trace_total = AIExtractionTrace.objects.count()
        durations = list(AIExtractionTrace.objects.order_by('-created_at').values_list('duration_ms', flat=True)[:1000])
        trace_statuses = AIExtractionTrace.objects.values('status').annotate(total=Count('id')).order_by('status')
        provider_rows = list(AIExtractionTrace.objects.values('provider').annotate(
            total=Count('id'), failed=Count('id', filter=Q(status='ai_failed')),
            avg_ms=Avg('duration_ms'),
        ).order_by('provider'))
        metric_source = 'استخراج متن'
    for row in provider_rows:
        row['label'] = row['provider'] or 'استخراج محلی'
        if row.get('actual_model'):
            row['label'] += f" · {row['actual_model']}"
        row['avg_ms'] = round(row['avg_ms'] or 0)
        row['error_rate'] = round(100 * row['failed'] / row['total'], 1) if row['total'] else 0

    recommendations = RelationshipRecommendation.objects.all()
    recommendation_feedback = recommendations.filter(Q(helpful__isnull=False) | ~Q(outcome=''))
    feedback_total = recommendation_feedback.count()
    helpful_total = recommendation_feedback.filter(helpful=True).count()
    outcome_counts = {
        key: recommendation_feedback.filter(outcome=key).count()
        for key in ('better', 'same', 'worse')
    }
    return render(request, 'platform/ai_quality.html', {
        'suggestion_rows': suggestion_rows,
        'trace_statuses': trace_statuses,
        'trace_count': trace_total,
        'duration_sample_count': len(durations),
        'metric_source': metric_source,
        'avg_ms': round(sum(durations) / len(durations)) if durations else 0,
        'p50_ms': percentile(durations, 50),
        'p95_ms': percentile(durations, 95),
        'under_10s_rate': round(100 * sum(value <= 10000 for value in durations) / len(durations), 1) if durations else 0,
        'provider_rows': provider_rows,
        'feedback_total': feedback_total,
        'helpful_rate': round(100 * helpful_total / feedback_total, 1) if feedback_total else None,
        'outcome_counts': outcome_counts,
        'latest_evaluation': AIQualityEvaluation.objects.first(),
        'errors': ObservabilityEvent.objects.filter(level='error')[:30],
    })


@user_passes_test(lambda user: user.is_superuser)
@require_POST
def ai_quality_run_api(request):
    from .ai_quality import run_persian_extraction_eval

    report = run_persian_extraction_eval()
    evaluation = AIQualityEvaluation.objects.create(
        suite_version=report['suite_version'], engine_version=report['engine_version'],
        total_cases=report['total_cases'], passed_cases=report['passed_cases'],
        pass_rate=report['pass_rate'], precision=report['precision'], recall=report['recall'],
        duration_ms=report['duration_ms'], report=report, run_by=request.user,
    )
    if request.POST.get('redirect') == '1':
        return redirect(f'/platform/ai-quality/?ran={evaluation.id}')
    return JsonResponse({'ok': True, 'evaluation_id': evaluation.id, **report})


@user_passes_test(lambda user: user.is_superuser)
def ai_debug_private(request):
    # Raw text is deliberately limited to the current superuser's own records.
    traces = AIExtractionTrace.objects.filter(owner=request.user)[:50]
    return render(request, 'platform/ai_debug.html', {'traces': traces})


@user_passes_test(lambda user: user.is_superuser)
@require_POST
def ai_trace_rerun(request, pk):
    trace = get_object_or_404(AIExtractionTrace, pk=pk, owner=request.user)
    from .extraction import extract_text
    rows = extract_text(request.user, trace.input_text, 'debug', trace.id)
    return JsonResponse({'ok': True, 'suggestions_created': len(rows)})


@user_passes_test(lambda user: user.is_superuser)
def feature_flags_view(request):
    if request.method == 'POST':
        flag = get_object_or_404(FeatureFlag, pk=request.POST.get('flag_id'))
        flag.enabled = request.POST.get('enabled') == 'on'
        flag.staff_only = request.POST.get('staff_only') == 'on'
        flag.rollout_percent = min(100, max(0, int(request.POST.get('rollout_percent', 100))))
        flag.save()
    return render(request, 'platform/feature_flags.html', {'flags': FeatureFlag.objects.all()})


@login_required
@require_POST
def frontend_error_api(request):
    data = _body(request) or {}
    ObservabilityEvent.objects.create(
        request_id=getattr(request, 'request_id', ''), owner=request.user, area='frontend',
        path=str(data.get('path', request.path))[:240], code=str(data.get('code', 'js_error'))[:100],
        message=str(data.get('message', 'Frontend error'))[:500],
        metadata={'browser': request.headers.get('User-Agent', '')[:180]})
    return JsonResponse({'ok': True})


def system_health_api(request):
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1'); cursor.fetchone()
    except Exception:
        db_ok = False
    cache_ok = True
    try:
        from django.core.cache import cache
        cache.set('health:probe', 'ok', timeout=10)
        cache_ok = cache.get('health:probe') == 'ok'
    except Exception:
        cache_ok = False
    ai_provider = next((name for name in ('OPENROUTER_API_KEY','GEMINI_API_KEY','MISTRAL_API_KEY','GROQ_API_KEY')
                        if os.environ.get(name)), '') or ('OLLAMA' if os.environ.get('OLLAMA_ENABLED', '1') == '1' else '')
    data = {'ok': db_ok and cache_ok, 'database': 'ok' if db_ok else 'error',
            'cache': 'ok' if cache_ok else 'error',
            'ai_configured': bool(ai_provider), 'ai_provider': ai_provider,
            'time': timezone.now().isoformat()}
    return JsonResponse(data, status=200 if data['ok'] else 503)


def _backup_payload(user):
    from .models import Debt, Event, FollowUp, NodeContactDetails
    events = []
    for event in Event.objects.filter(owner=user).prefetch_related('participants'):
        events.append({'title': event.title, 'date': event.date, 'event_time': event.event_time,
                       'description': event.description,
                       'participants': [node.username for node in event.participants.filter(owner=user)]})
    return {
        'version': 2,
        'created_at': timezone.now().isoformat(),
        'nodes': list(Node.objects.filter(owner=user).values(
            'username', 'name', 'first_name', 'last_name', 'nickname', 'career',
            'birth_day', 'phone_number', 'is_public', 'group',
        )),
        'contact_details': list(NodeContactDetails.objects.filter(owner=user).values(
            'node__username', 'email', 'alternate_phone', 'bank_name', 'card_number',
            'account_number', 'iban', 'telegram_username', 'whatsapp_number',
            'instagram_username', 'x_username', 'linkedin_url', 'address', 'notes',
        )),
        'relationships': list(Relationship.objects.filter(owner=user).values(
            'source__username', 'target__username', 'rel', 'strength', 'status',
        )),
        'events': events,
        'facts': list(MemoryFact.objects.filter(owner=user).values(
            'node__username', 'category', 'value', 'confidence', 'source', 'active',
            'ai_usable', 'confidentiality',
        )),
        'journal': list(JournalEntry.objects.filter(owner=user).values(
            'text', 'entry_date', 'occurred_at', 'entry_kind', 'tags', 'mood',
        )),
        'interactions': list(Interaction.objects.filter(owner=user).values(
            'node__username', 'kind', 'date', 'feeling', 'support_kind', 'note',
        )),
        'followups': list(FollowUp.objects.filter(owner=user).values(
            'node__username', 'text', 'due_date', 'done',
        )),
        'commitments': list(Commitment.objects.filter(owner=user).values(
            'node__username', 'responsible', 'text', 'due_date', 'status',
        )),
        'debts': list(Debt.objects.filter(owner=user).values(
            'node__username', 'direction', 'amount', 'paid', 'currency', 'date',
            'due_date', 'note', 'settled',
        )),
    }


def _fernet(password, salt):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError('بسته cryptography نصب نشده است.') from exc
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 390000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def _read_backup(uploaded, password):
    raw = read_limited_upload(uploaded, max_bytes=50 * 1024 * 1024, label='فایل بکاپ')
    if not raw.startswith(b'FGB1'):
        raise ValueError('فرمت بکاپ معتبر نیست.')
    try:
        return json.loads(_fernet(password, raw[4:20]).decrypt(raw[20:]).decode())
    except RuntimeError:
        raise
    except Exception as exc:
        raise ValueError('رمز اشتباه یا فایل خراب است.') from exc


@login_required
@require_POST
def encrypted_backup_download(request):
    password = request.POST.get('password', '')
    if len(password) < 8:
        return JsonResponse({'error': 'رمز بکاپ باید حداقل ۸ کاراکتر باشد.'}, status=400)
    salt = os.urandom(16)
    try:
        token = _fernet(password, salt).encrypt(json.dumps(_backup_payload(request.user), ensure_ascii=False, default=str).encode())
    except RuntimeError as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    response = HttpResponse(b'FGB1' + salt + token, content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="familygraph-backup.fgb"'
    return response


@login_required
@require_POST
def encrypted_backup_preview(request):
    uploaded, password = request.FILES.get('file'), request.POST.get('password', '')
    if not uploaded:
        return JsonResponse({'error': 'فایل بکاپ لازم است.'}, status=400)
    try:
        payload = _read_backup(uploaded, password)
    except (UploadValidationError, ValueError, RuntimeError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'valid': True, 'version': payload.get('version'),
                         'counts': {key: len(value) for key, value in payload.items() if isinstance(value, list)}})


@login_required
@require_POST
@transaction.atomic
def encrypted_backup_restore(request):
    uploaded, password = request.FILES.get('file'), request.POST.get('password', '')
    if not uploaded:
        return JsonResponse({'error': 'فایل بکاپ لازم است.'}, status=400)
    try:
        payload = _read_backup(uploaded, password)
    except (UploadValidationError, ValueError, RuntimeError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    if payload.get('version') not in (1, 2):
        return JsonResponse({'error': 'نسخه بکاپ پشتیبانی نمی‌شود.'}, status=400)
    def backup_date(value):
        if not value:
            return None
        if isinstance(value, date_type):
            return value
        try:
            return date_type.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None

    def backup_datetime(value):
        if not value:
            return None
        if isinstance(value, datetime_type):
            return value
        try:
            return datetime_type.fromisoformat(str(value).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return None

    nodes = {}
    for row in payload.get('nodes', [])[:5000]:
        username = str(row.get('username', ''))[:100]
        if not username: continue
        node, _ = Node.objects.update_or_create(owner=request.user, username=username,
            defaults={key: row.get(key) or '' for key in ('name','first_name','last_name','nickname','career','phone_number','group')}
                     | {'birth_day': backup_date(row.get('birth_day')), 'is_public': bool(row.get('is_public'))})
        nodes[username] = node
    from .models import NodeContactDetails
    for row in payload.get('contact_details', [])[:5000]:
        node = nodes.get(row.get('node__username'))
        if node:
            NodeContactDetails.objects.update_or_create(
                owner=request.user, node=node,
                defaults={key: row.get(key) or '' for key in (
                    'email', 'alternate_phone', 'bank_name', 'card_number', 'account_number',
                    'iban', 'telegram_username', 'whatsapp_number', 'instagram_username',
                    'x_username', 'linkedin_url', 'address', 'notes',
                )},
            )
    for row in payload.get('relationships', [])[:10000]:
        source, target = nodes.get(row.get('source__username')), nodes.get(row.get('target__username'))
        if source and target and source != target:
            Relationship.objects.update_or_create(owner=request.user, source=source, target=target,
                rel=row.get('rel'), defaults={'strength': row.get('strength', 3), 'status': row.get('status', 'active')})
    for row in payload.get('facts', [])[:20000]:
        node = nodes.get(row.get('node__username'))
        if node and row.get('category') in dict(MemoryFact.CATEGORY_CHOICES) and row.get('value'):
            MemoryFact.objects.update_or_create(owner=request.user, node=node, category=row['category'], value=row['value'],
                defaults={'confidence': row.get('confidence', 70), 'source': 'restore',
                           'active': row.get('active', True), 'ai_usable': row.get('ai_usable', True),
                           'confidentiality': row.get('confidentiality', 'personal')})
    from .models import Debt, Event, FollowUp
    for row in payload.get('events', [])[:10000]:
        event_date = backup_date(row.get('date'))
        title = str(row.get('title') or '').strip()[:200]
        if not event_date or not title:
            continue
        event, _ = Event.objects.update_or_create(
            owner=request.user, title=title, date=event_date,
            defaults={'event_time': row.get('event_time') or None,
                      'description': str(row.get('description') or '')[:2000]},
        )
        event.participants.set([nodes[name] for name in row.get('participants', []) if name in nodes])
    for row in payload.get('journal', [])[:20000]:
        text = str(row.get('text') or '').strip()
        if not text:
            continue
        JournalEntry.objects.get_or_create(
            owner=request.user, text=text[:10000], entry_date=backup_date(row.get('entry_date')),
            defaults={'occurred_at': backup_datetime(row.get('occurred_at')),
                      'entry_kind': row.get('entry_kind') or 'reflection',
                      'tags': row.get('tags') or [], 'mood': row.get('mood') or ''},
        )
    for row in payload.get('interactions', [])[:20000]:
        node = nodes.get(row.get('node__username'))
        interaction_date = backup_date(row.get('date'))
        if node and interaction_date:
            Interaction.objects.get_or_create(
                owner=request.user, node=node, kind=row.get('kind') or 'other',
                date=interaction_date, note=str(row.get('note') or '')[:300],
                defaults={'feeling': row.get('feeling', 0), 'support_kind': row.get('support_kind') or ''},
            )
    for row in payload.get('followups', [])[:20000]:
        node = nodes.get(row.get('node__username'))
        text = str(row.get('text') or '').strip()
        if node and text:
            FollowUp.objects.get_or_create(
                owner=request.user, node=node, text=text[:300],
                defaults={'due_date': backup_date(row.get('due_date')), 'done': bool(row.get('done'))},
            )
    for row in payload.get('commitments', [])[:20000]:
        node = nodes.get(row.get('node__username'))
        text = str(row.get('text') or '').strip()
        if node and text:
            Commitment.objects.get_or_create(
                owner=request.user, node=node, text=text[:300],
                defaults={'responsible': row.get('responsible') if row.get('responsible') in ('me', 'them') else 'me',
                          'due_date': backup_date(row.get('due_date')), 'status': row.get('status') or 'open'},
            )
    for row in payload.get('debts', [])[:20000]:
        node = nodes.get(row.get('node__username'))
        debt_date = backup_date(row.get('date'))
        if node and debt_date and row.get('amount') is not None:
            Debt.objects.get_or_create(
                owner=request.user, node=node, amount=row.get('amount'), date=debt_date,
                defaults={'direction': row.get('direction') if row.get('direction') in ('i_owe', 'they_owe') else 'i_owe',
                          'paid': row.get('paid', 0), 'currency': row.get('currency') or 'طھظˆظ…ط§ظ†',
                          'due_date': backup_date(row.get('due_date')), 'note': row.get('note') or '',
                          'settled': bool(row.get('settled'))},
            )
    return JsonResponse({'ok': True, 'nodes': len(nodes),
                         'preview_before_apply': True, 'message': 'بازیابی با merge امن انجام شد.'})


@login_required
@require_POST
def demo_mode_api(request):
    data = _body(request) or {}
    if data.get('action') == 'reset':
        Node.objects.filter(owner=request.user, is_demo=True).delete()
        request.user.demo_mode = False
    else:
        samples = [('demo-sara', 'سارا'), ('demo-ali', 'علی'), ('demo-maryam', 'مریم')]
        for username, name in samples:
            Node.objects.get_or_create(owner=request.user, username=username,
                                       defaults={'name': name, 'is_demo': True})
        request.user.demo_mode = True
    request.user.save(update_fields=['demo_mode'])
    return JsonResponse({'ok': True, 'demo_mode': request.user.demo_mode})


@login_required
def jobs_list_api(request):
    """Recent background jobs for the current user."""
    from .models import BackgroundJob
    jobs = BackgroundJob.objects.filter(owner=request.user)[:20]
    return JsonResponse({'ok': True, 'jobs': [j.as_dict() for j in jobs]})


@login_required
def job_detail_api(request, pk):
    from .models import BackgroundJob
    job = get_object_or_404(BackgroundJob, pk=pk, owner=request.user)
    return JsonResponse({'ok': True, 'job': job.as_dict()})
