import base64
import hashlib
import json
import os
from collections import Counter

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import connection
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (AIExtractionTrace, Commitment, ExtractionSuggestion, FeatureFlag,
                     Interaction, JournalEntry, KnowledgeTriple, MemoryFact, Node,
                     ObservabilityEvent, Relationship)


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
        {'title': 'افزودن شخص', 'subtitle': 'گراف رابطه', 'url': '/nodes/create/', 'icon': '👤'},
    ]
    if q:
        commands = [item for item in commands if q.casefold() in (item['title'] + item['subtitle']).casefold()]
    people = Node.objects.filter(owner=request.user, merged_into__isnull=True)
    if q:
        people = people.filter(username__icontains=q) | people.filter(name__icontains=q) | people.filter(nickname__icontains=q)
    results = commands + [{'title': node.display_name(), 'subtitle': f'@{node.username}',
                           'url': f'/nodes/{node.id}/', 'icon': '◉'} for node in people[:8]]
    return JsonResponse({'results': results[:12]})


@login_required
def onboarding_api(request):
    user = request.user
    goal = (user.feature_overrides or {}).get('onboarding_goal', '')
    steps = [
        {'id': 'profile', 'title': 'معرفی خودت', 'done': bool(user.first_name and user.root_node_id), 'url': '/profile/edit/'},
        {'id': 'person', 'title': 'افزودن اولین شخص', 'done': user.nodes.exclude(pk=user.root_node_id).exists(), 'url': '/nodes/create/'},
        {'id': 'relationship', 'title': 'ساخت اولین رابطه', 'done': user.relationships.exists(), 'url': '/relationships/create/'},
        {'id': 'journal', 'title': 'ثبت اولین خاطره', 'done': user.journal_entries.exists(), 'url': '/journal/'},
        {'id': 'approval', 'title': 'بررسی اولین پیشنهاد AI', 'done': user.extraction_suggestions.filter(status='approved').exists(), 'url': '/extractions/'},
    ]
    goal_choices = [
        {'id': 'family', 'label': 'خانواده‌ام', 'description': 'آدم‌ها و خاطره‌های خانوادگی را مرتب کنم.'},
        {'id': 'friends', 'label': 'دوستانم', 'description': 'رابطه‌های مهمم را در جریان نگه دارم.'},
        {'id': 'memories', 'label': 'خاطره‌ها', 'description': 'لحظه‌ها و اتفاق‌های مهم را ثبت کنم.'},
    ]
    if goal == 'memories':
        steps.sort(key=lambda row: {'journal': 0, 'person': 1, 'relationship': 2, 'profile': 3, 'approval': 4}[row['id']])
    elif goal in ('family', 'friends'):
        steps.sort(key=lambda row: {'person': 0, 'relationship': 1, 'journal': 2, 'profile': 3, 'approval': 4}[row['id']])
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
    suggestions = ExtractionSuggestion.objects.values('kind', 'status').annotate(total=Count('id'))
    matrix = {}
    for row in suggestions:
        matrix.setdefault(row['kind'], {})[row['status']] = row['total']
    traces = AIExtractionTrace.objects.values('status').annotate(total=Count('id'))
    return render(request, 'platform/ai_quality.html', {
        'matrix': matrix, 'trace_statuses': traces,
        'trace_count': AIExtractionTrace.objects.count(),
        'avg_ms': round(sum(AIExtractionTrace.objects.values_list('duration_ms', flat=True)[:1000]) /
                        max(1, min(1000, AIExtractionTrace.objects.count()))),
        'errors': ObservabilityEvent.objects.filter(level='error')[:30],
    })


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
    return {'version': 1, 'created_at': timezone.now().isoformat(),
            'nodes': list(Node.objects.filter(owner=user).values('username','name','first_name','last_name','nickname','career','phone_number','is_public')),
            'relationships': list(Relationship.objects.filter(owner=user).values('source__username','target__username','rel','strength','status')),
            'facts': list(MemoryFact.objects.filter(owner=user).values('node__username','category','value','confidence','source','active','ai_usable','confidentiality')),
            'journal': list(JournalEntry.objects.filter(owner=user).values('text','entry_date','entry_kind','tags','mood')),
            'interactions': list(Interaction.objects.filter(owner=user).values('node__username','kind','date','feeling','note')),
            'commitments': list(Commitment.objects.filter(owner=user).values('node__username','responsible','text','due_date','status'))}


def _fernet(password, salt):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError('بسته cryptography نصب نشده است.') from exc
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 390000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def _read_backup(uploaded, password):
    raw = uploaded.read()
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
    except (ValueError, RuntimeError) as exc:
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
    except (ValueError, RuntimeError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    if payload.get('version') != 1:
        return JsonResponse({'error': 'نسخه بکاپ پشتیبانی نمی‌شود.'}, status=400)
    nodes = {}
    for row in payload.get('nodes', [])[:5000]:
        username = str(row.get('username', ''))[:100]
        if not username: continue
        node, _ = Node.objects.update_or_create(owner=request.user, username=username,
            defaults={key: row.get(key) or '' for key in ('name','first_name','last_name','nickname','career','phone_number')})
        nodes[username] = node
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
