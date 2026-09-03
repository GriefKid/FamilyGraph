"""
Smart Features: Alerts, Psychology Analysis, Daily Tips
"""
import json
import os
import time
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request, urlopen
from datetime import date, timedelta
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Prefetch
from django.utils import timezone
from django.views.decorators.http import require_POST
from openai import OpenAI

from .models import Node, Relationship, Event, Information, JournalEntry, AppSettings, AlertAction
from .utils_jalali import (
    jalali_str, jalali_full_str, jalali_day_name, jalali_month_name,
    is_holiday, upcoming_holidays, season_fa,
)


class _ChatCompletionFailover:
    """Retry retryable cloud chat failures against a local Ollama model."""

    def __init__(self, primary, fallback, fallback_model):
        self.primary = primary
        self.fallback = fallback
        self.fallback_model = fallback_model

    def create(self, *args, **kwargs):
        try:
            return self.primary.chat.completions.create(*args, **kwargs)
        except Exception as primary_error:
            message = str(primary_error).lower()
            retryable = (
                '429', 'rate limit', 'timeout', 'timed out', 'connection',
                '500', '502', '503', '504', 'service unavailable',
            )
            if not any(marker in message for marker in retryable):
                raise
            fallback_kwargs = dict(kwargs)
            fallback_kwargs['model'] = self.fallback_model
            try:
                return self.fallback.chat.completions.create(*args, **fallback_kwargs)
            except Exception as fallback_error:
                raise RuntimeError(
                    f'Cloud AI failed ({primary_error}); local Ollama fallback '
                    f'with model {self.fallback_model!r} also failed ({fallback_error})'
                ) from fallback_error


class _AIClientFailover:
    """Expose the subset of the OpenAI client used by this application."""

    def __init__(self, primary, fallback, fallback_model):
        self.chat = type('Chat', (), {})()
        self.chat.completions = _ChatCompletionFailover(
            primary, fallback, fallback_model
        )


_OLLAMA_DEFAULT_MODEL = 'hamdam-fa:latest'
_OLLAMA_MODEL_PREFERENCES = (
    'hamdam-fa:latest',
    'hamdam-fa',
    'qwen3:8b',
    'qwen3:14b',
    'gemma3:12b',
    'deepseek-r1:14b',
)
_OLLAMA_DISCOVERY_CACHE = {'base_url': '', 'expires_at': 0.0, 'models': ()}


def _ollama_enabled():
    return os.environ.get('OLLAMA_ENABLED', '1').strip().lower() in {
        '1', 'true', 'yes', 'on',
    }


def _ollama_base_url():
    return os.environ.get(
        'OLLAMA_BASE_URL', 'http://127.0.0.1:11434'
    ).rstrip('/')


def _ollama_model_names():
    """Return models that the local Ollama server can actually run.

    Discovery is intentionally short-lived: it avoids probing Ollama twice in a
    single request while still noticing a model installed after Django started.
    """
    base_url = _ollama_base_url()
    now = time.monotonic()
    cached = _OLLAMA_DISCOVERY_CACHE
    if cached['base_url'] == base_url and cached['expires_at'] > now:
        return cached['models']

    models = ()
    try:
        timeout = max(0.1, float(os.environ.get('OLLAMA_DISCOVERY_TIMEOUT', '0.8')))
        with urlopen(f'{base_url}/api/tags', timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
        models = tuple(
            item.get('name') or item.get('model')
            for item in payload.get('models', [])
            if isinstance(item, dict) and (item.get('name') or item.get('model'))
        )
    except (OSError, ValueError, TypeError, URLError):
        models = ()

    cached.update({
        'base_url': base_url,
        'expires_at': now + 10,
        'models': models,
    })
    return models


def _ollama_model(models=None, configured_model=None):
    """Resolve a configured model to an installed model, with safe fallback."""
    models = tuple(_ollama_model_names() if models is None else models)
    configured = (
        configured_model
        if configured_model is not None
        else os.environ.get('OLLAMA_MODEL', _OLLAMA_DEFAULT_MODEL)
    ).strip() or _OLLAMA_DEFAULT_MODEL
    if not models or configured in models:
        return configured

    # Ollama commonly reports an explicit ``:latest`` tag for a bare model.
    aliases = {name.removesuffix(':latest'): name for name in models}
    if configured in aliases:
        return aliases[configured]
    if configured.removesuffix(':latest') in aliases:
        return aliases[configured.removesuffix(':latest')]

    for preferred in _OLLAMA_MODEL_PREFERENCES:
        if preferred in models:
            return preferred
        if preferred in aliases:
            return aliases[preferred]
    for family in ('qwen3.8', 'qwen3', 'gemma3', 'deepseek-r1', 'llama'):
        match = next((name for name in models if name.startswith(family)), None)
        if match:
            return match
    return models[0]


class _OllamaChatCompletions:
    """Small OpenAI-shaped adapter backed by Ollama's native chat API."""

    def __init__(self, base_url):
        self.base_url = base_url

    def create(self, *args, **kwargs):
        if args:
            raise TypeError('Ollama chat completion only accepts keyword arguments')
        model = kwargs.pop('model')
        messages = kwargs.pop('messages')
        options = {}
        if kwargs.get('max_tokens') is not None:
            options['num_predict'] = kwargs['max_tokens']
        for name in ('temperature', 'top_p'):
            if kwargs.get(name) is not None:
                options[name] = kwargs[name]

        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'think': False,
        }
        if options:
            payload['options'] = options
        response_format = kwargs.get('response_format')
        if isinstance(response_format, dict) and response_format.get('type') == 'json_object':
            payload['format'] = 'json'

        request = Request(
            f'{self.base_url}/api/chat',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        timeout = max(1.0, float(os.environ.get('OLLAMA_REQUEST_TIMEOUT', '240')))
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode('utf-8'))
        content = result.get('message', {}).get('content', '')
        return SimpleNamespace(
            model=result.get('model', model),
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=content),
            )],
        )


class _OllamaClient:
    def __init__(self, base_url):
        self.chat = SimpleNamespace(
            completions=_OllamaChatCompletions(base_url)
        )


def _ollama_client(models=None):
    """Return the local Ollama client and a model that is actually installed."""
    models = _ollama_model_names() if models is None else tuple(models)
    model = _ollama_model(models=models)
    return _OllamaClient(_ollama_base_url()), model


def _available_ollama_client():
    if not _ollama_enabled():
        return None
    models = _ollama_model_names()
    return _ollama_client(models=models) if models else None


# ── AI Provider Config ────────────────────────────────────────────────────
# اولویت: OpenRouter → Gemini → Mistral → Groq → Ollama محلی
#
# Mistral  (رایگان، بدون بلاک ایران): console.mistral.ai → MISTRAL_API_KEY
# Groq     (14,400 req/day رایگان): console.groq.com → GROQ_API_KEY
# OpenRouter (مدل‌های رایگان): openrouter.ai → OPENROUTER_API_KEY
# Ollama    (کاملاً محلی و رایگان): ollama.com → OLLAMA_MODEL
# Gemini   (1,500 req/day - بلاک در ایران بدون VPN)
# ─────────────────────────────────────────────────────────────────────────

_PROVIDER_BASE_URLS = {
    'openrouter': 'https://openrouter.ai/api/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/openai/',
    'mistral': 'https://api.mistral.ai/v1',
    'groq': 'https://api.groq.com/openai/v1',
}
_PROVIDER_KEY_ENV = {
    'openrouter': 'OPENROUTER_API_KEY',
    'gemini': 'GEMINI_API_KEY',
    'mistral': 'MISTRAL_API_KEY',
    'groq': 'GROQ_API_KEY',
}


def _forced_provider():
    """AI_PROVIDER in the environment pins the provider and skips auto-priority."""
    name = os.environ.get('AI_PROVIDER', '').strip().lower()
    if not name:
        return None
    if name == 'ollama':
        available = _available_ollama_client()
        if not available:
            return None
        client, _m = available
        return client, 'ollama', 'ollama'
    # Any OpenAI-compatible endpoint (e.g. an in-country proxy that is not
    # geo-blocked): AI_PROVIDER=custom + AI_BASE_URL + AI_API_KEY (+ AI_MODEL).
    if name == 'custom' or os.environ.get('AI_BASE_URL', '').strip():
        base = os.environ.get('AI_BASE_URL', '').strip()
        if not base:
            return None
        key = os.environ.get('AI_API_KEY', '').strip() or 'not-needed'
        return OpenAI(base_url=base, api_key=key), key, 'custom'
    if name in _PROVIDER_BASE_URLS:
        key = os.environ.get(_PROVIDER_KEY_ENV[name], '')
        if not key:
            return None  # fall through to auto so the app still works
        primary = OpenAI(base_url=_PROVIDER_BASE_URLS[name], api_key=key)
        fallback = _available_ollama_client() if name == 'openrouter' else None
        if fallback:
            fb, fbm = fallback
            primary = _AIClientFailover(primary, fb, fbm)
        return primary, key, name
    return None


def _ai_client():
    """Return (OpenAI client, api_key, provider).

    If AI_PROVIDER is set it wins. Otherwise:
    Priority: OpenRouter → Gemini → Mistral → Groq → local Ollama
    """
    forced = _forced_provider()
    if forced:
        return forced

    # Prefer the project's free cloud provider when a key is configured.
    # OpenRouter accepts the OpenAI client already used by this project.
    openrouter_key = os.environ.get('OPENROUTER_API_KEY', '')
    if openrouter_key:
        primary = OpenAI(
            base_url='https://openrouter.ai/api/v1',
            api_key=openrouter_key,
        )
        fallback_config = _available_ollama_client()
        if fallback_config:
            fallback, fallback_model = fallback_config
            primary = _AIClientFailover(primary, fallback, fallback_model)
        provider = 'openrouter+ollama' if fallback_config else 'openrouter'
        return primary, openrouter_key, provider

    gemini_key = os.environ.get('GEMINI_API_KEY', '')
    if gemini_key:
        return (
            OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", api_key=gemini_key),
            gemini_key, 'gemini'
        )
    mistral_key = os.environ.get('MISTRAL_API_KEY', '')
    if mistral_key:
        return OpenAI(base_url="https://api.mistral.ai/v1", api_key=mistral_key), mistral_key, 'mistral'
    groq_key = os.environ.get('GROQ_API_KEY', '')
    if groq_key:
        return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key), groq_key, 'groq'
    # Ollama exposes an OpenAI-compatible local API and needs no cloud key.
    available = _available_ollama_client()
    if available:
        client, _model_name = available
        return client, 'ollama', 'ollama'

    return None, '', ''


_PROVIDER_DEFAULT_MODEL = {
    'openrouter': 'openrouter/free',
    'gemini': 'gemini-2.5-flash',
    'mistral': 'mistral-small-latest',      # رایگان، بدون بلاک ایران
    'groq': 'llama-3.3-70b-versatile',      # 14,400 req/day رایگان، سریع
}


def _model():
    """Pick the model name for the active provider."""
    configured_model = os.environ.get('AI_MODEL', '').strip()
    forced = os.environ.get('AI_PROVIDER', '').strip().lower()
    cloud_configured = any(
        os.environ.get(key_env) for key_env in _PROVIDER_KEY_ENV.values()
    )
    ollama_active = forced == 'ollama' or (not forced and not cloud_configured)
    if configured_model:
        if ollama_active:
            return _ollama_model(configured_model=configured_model)
        return configured_model
    if forced == 'ollama':
        return _ollama_model()
    if forced == 'custom' or os.environ.get('AI_BASE_URL', '').strip():
        # most in-country proxies front OpenAI; override with AI_MODEL.
        return 'gpt-4o-mini'
    if (
        forced in _PROVIDER_DEFAULT_MODEL
        and os.environ.get(_PROVIDER_KEY_ENV[forced])
    ):
        return _PROVIDER_DEFAULT_MODEL[forced]
    if os.environ.get('OPENROUTER_API_KEY'):
        return 'openrouter/free'
    if os.environ.get('GEMINI_API_KEY'):
        return "gemini-2.5-flash"
    if os.environ.get('MISTRAL_API_KEY'):
        return "mistral-small-latest"
    if os.environ.get('GROQ_API_KEY'):
        return "llama-3.3-70b-versatile"
    return _ollama_model()


def _rate_limit_msg(e: Exception) -> str:
    s = str(e)
    if '429' in s or 'rate limit' in s.lower() or 'Rate limit' in s:
        return ('حد روزانه تموم شده 😔 — فردا دوباره امتحان کن '
                'یا OPENROUTER_API_KEY را در .env تنظیم کن، یا Ollama محلی را اجرا کن.')
    return f'خطای AI: {s[:200]}'


def _strip_reasoning(raw: str) -> str:
    """Drop <think>...</think> blocks that reasoning models (deepseek-r1,
    qwen-r1, …) prepend — they break JSON parsing and leak into replies."""
    import re
    raw = re.sub(r'<think>.*?</think>', '', raw or '', flags=re.S | re.I).strip()
    raw = re.sub(r'^<think>.*$', '', raw, flags=re.S | re.I).strip()  # unclosed
    return raw


def _extract_json(raw: str) -> dict:
    """Pull JSON from AI output (may be wrapped in ```json or a <think> block)."""
    raw = _strip_reasoning(raw).strip()
    if '```json' in raw:
        raw = raw.split('```json')[1].split('```')[0]
    elif '```' in raw:
        raw = raw.split('```')[1].split('```')[0]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # last resort: the outermost {...}
        start, end = raw.find('{'), raw.rfind('}')
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
        raise


# ═══════════════════════════════════════════════════════════════
#  ALERTS
# ═══════════════════════════════════════════════════════════════

def _compute_alerts(user=None):
    """Compute all active alerts — no AI, fast."""
    today = timezone.localdate()
    alerts = []
    user_filter = {'owner': user} if user and user.is_authenticated else {}
    participant_queryset = Node.objects.filter(owner=user) if user and user.is_authenticated else Node.objects.all()

    # ── 1. Birthdays today ──────────────────────────────────────
    for node in Node.objects.filter(birth_day__month=today.month, birth_day__day=today.day, **user_filter):
        age = today.year - node.birth_day.year
        alerts.append({
            'id': f'bday_{node.id}',
            'type': 'birthday',
            'priority': 'high',
            'node_id': node.id,
            'node_username': node.username,
            'node_name': node.display_name(),
            'title': f'🎂 تولد {node.display_name()} امروزه!',
            'body': f'امروز {age}مین سالگرد تولد {node.display_name()} است. ({jalali_str(today)})',
            'days_until': 0,
        })

    # ── 2. Birthdays upcoming (1-7 days) ───────────────────────
    for delta in range(1, 8):
        d = today + timedelta(days=delta)
        for node in Node.objects.filter(birth_day__month=d.month, birth_day__day=d.day, **user_filter):
            # BUGFIX: سن باید نسبت به سالِ روز تولد حساب بشه (مرز سال نو)
            age = d.year - node.birth_day.year
            # اگه روز تولد با تعطیل رسمی ایرانی مصادف بود، اشاره کن
            hol_flag, hol_nm = is_holiday(d)
            hol_note = f' (مصادف با {hol_nm})' if hol_flag and hol_nm != 'جمعه' else (
                       ' (جمعه)' if hol_flag else '')
            alerts.append({
                'id': f'bday_{node.id}_{delta}',
                'type': 'birthday_upcoming',
                'priority': 'medium',
                'node_id': node.id,
                'node_username': node.username,
                'node_name': node.display_name(),
                'title': f'🎂 تولد {node.display_name()} — {delta} روز دیگر',
                'body': f'{node.display_name()} در {jalali_str(d)}{hol_note} {age} ساله می‌شود.',
                'days_until': delta,
            })

    # ── 3. Upcoming events — reminders at 7d / 1d / today + post-event ──────
    from django.db import ProgrammingError as _PErr
    try:
        _upcoming_evs = list(Event.objects.filter(
            date__gte=today, date__lte=today + timedelta(days=7), **user_filter
        ).prefetch_related(Prefetch('participants', queryset=participant_queryset)))
        _post_evs = list(Event.objects.filter(
            date__gte=today - timedelta(days=3),
            date__lt=today,
            post_event_prompted=False,
            **user_filter
        ).prefetch_related(Prefetch('participants', queryset=participant_queryset))[:3])
    except _PErr:
        # migration هنوز نخورده — فقط ستون‌های قدیمی، order_by صریح تا Meta.ordering override بشه
        _upcoming_evs = list(Event.objects.filter(
            date__gte=today, date__lte=today + timedelta(days=7), **user_filter
        ).only('id','title','date','description','owner_id')
         .order_by('date')   # override Meta.ordering که event_time داره
         .prefetch_related(Prefetch('participants', queryset=participant_queryset)))
        _safe = {'event_time': None, 'reminder_sent_7d': False, 'reminder_sent_1d': False,
                 'reminder_sent_3h': False, 'post_event_prompted': False}
        for _ev in _upcoming_evs:
            _ev.__dict__.update(_safe)
        _post_evs = []

    # رویدادهای آینده (۷ روز آینده)
    for ev in _upcoming_evs:
        days = (ev.date - today).days
        parts = [p.display_name() for p in ev.participants.all()[:4]]
        ev_hol, ev_hol_name = is_holiday(ev.date)
        hol_note = f' — {ev_hol_name}' if ev_hol and ev_hol_name != 'جمعه' else ''
        ev_time = getattr(ev, 'event_time', None)
        time_note = f' ساعت {ev_time.strftime("%H:%M")}' if ev_time else ''

        if days == 0:
            pri = 'high'; title = f'📅 {ev.title} — امروز!{time_note}'
        elif days == 1:
            pri = 'high'; title = f'📅 {ev.title} — فردا!{time_note}'
        elif days <= 7:
            pri = 'medium'; title = f'📅 {ev.title} — {days} روز دیگر{time_note}'
        else:
            pri = 'low'; title = f'📅 {ev.title} — {days} روز دیگر{time_note}'

        alerts.append({
            'id': f'event_{ev.id}',
            'type': 'event',
            'priority': pri,
            'event_id': ev.id,
            'node_id': None,
            'node_name': None,
            'title': title,
            'body': f'{jalali_str(ev.date)}{hol_note}{time_note}'
                    + (f' | {ev.description}' if ev.description else '')
                    + (f' | شرکت‌کنندگان: {", ".join(parts)}' if parts else ''),
            'days_until': days,
            'event_time': str(ev_time) if ev_time else None,
        })

    # رویدادهای گذشته ۳ روز — prompt «چطور بود؟»
    for ev in _post_evs:
        parts = [p.display_name() for p in ev.participants.all()[:4]]
        alerts.append({
            'id': f'post_event_{ev.id}',
            'type': 'post_event',
            'priority': 'medium',
            'event_id': ev.id,
            'node_id': None,
            'node_name': None,
            'title': f'📝 {ev.title} — چطور بود؟',
            'body': f'رویداد «{ev.title}» ({jalali_str(ev.date)}) تموم شد — یادداشت کوتاهی بنویس تا خاطراتت ثبت بشه.'
                    + (f' | شرکت‌کنندگان: {", ".join(parts)}' if parts else ''),
            'days_until': None,
            'journal_prefill': ev.title,
        })

    # ── 4. Mood-based alerts from recent journal (7 days) ──────
    negative_words = ['ناراحت', 'غمگین', 'استرس', 'اضطراب', 'عصبانی', 'نگران',
                      'sad', 'stress', 'anxious', 'worried', 'upset', 'depressed', 'تنها', 'افسرده']
    cutoff7 = today - timedelta(days=7)
    seen_mood_nodes = set()
    for entry in JournalEntry.objects.filter(
        created_at__date__gte=cutoff7, ai_analyzed=True, **user_filter
    ).prefetch_related(Prefetch('mentioned_nodes', queryset=participant_queryset))[:30]:
        if entry.mood and any(neg in entry.mood.lower() for neg in negative_words):
            for node in entry.mentioned_nodes.all()[:3]:
                if node.id not in seen_mood_nodes:
                    seen_mood_nodes.add(node.id)
                    alerts.append({
                        'id': f'mood_{node.id}_{entry.id}',
                        'type': 'mood_alert',
                        'priority': 'medium',
                        'node_id': node.id,
                        'node_username': node.username,
                        'node_name': node.display_name(),
                        'title': f'💛 {node.display_name()} ممکن است به حمایت نیاز داشته باشد',
                        'body': f'بر اساس یادداشت اخیر، حال {node.display_name()} چندان خوب نبود ({entry.mood}).',
                        'days_until': None,
                    })

    # ── 5. Dormant connections (no journal mention in 30+ days) ─
    # فقط اگه اپ حداقل ۳۰ روزه استفاده شده نشون بده
    try:
        first_entry = JournalEntry.objects.filter(**user_filter).order_by('entry_date').first()
        app_age_days = (today - first_entry.entry_date).days if (first_entry and first_entry.entry_date) else 0

        if app_age_days >= 30:
            root = user.root_node if (user and user.is_authenticated) else None
            if root:
                cutoff30 = today - timedelta(days=30)
                recent_ids = set(
                    JournalEntry.objects.filter(entry_date__gte=cutoff30, **user_filter)
                    .values_list('mentioned_nodes__id', flat=True)
                )
                recent_ids.discard(None)

                connected_ids = set(
                    Relationship.objects.filter(source=root, **user_filter).values_list('target_id', flat=True)
                ) | set(
                    Relationship.objects.filter(target=root, **user_filter).values_list('source_id', flat=True)
                )

                dormant_ids = connected_ids - recent_ids - {root.id}
                for node in Node.objects.filter(id__in=dormant_ids, **user_filter)[:4]:
                    alerts.append({
                        'id': f'dormant_{node.id}',
                        'type': 'dormant',
                        'priority': 'low',
                        'node_id': node.id,
                        'node_username': node.username,
                        'node_name': node.display_name(),
                        'title': f'💤 مدتی از {node.display_name()} بی‌خبری',
                        'body': f'بیش از ۳۰ روز است که در یادداشت‌هایت از {node.display_name()} یادی نشده.',
                        'days_until': None,
                    })
    except Exception:
        pass

    # ── 6. Relationship decay (90 days no journal mention) ─────
    try:
        if user and user.is_authenticated and user.root_node:
            root = user.root_node
            cutoff90 = today - timedelta(days=90)

            # فقط اگه کاربر حداقل ۹۰ روزه از journal استفاده می‌کنه decay معنا داره
            # اگه قدیمی‌ترین entry بعد از cutoff باشه = کاربر تازه‌واردِ — skip
            earliest_entry = JournalEntry.objects.filter(
                **user_filter
            ).order_by('created_at').values('created_at').first()

            journal_old_enough = (
                earliest_entry is not None and
                earliest_entry['created_at'].date() <= cutoff90
            )

            if journal_old_enough:
                seen_decay = set()
                active_rels = Relationship.objects.filter(
                    status='active', source__owner=user, target__owner=user, **user_filter,
                ).select_related('source', 'target')

                # پیش‌واکشی یک‌باره نودهایی که در ۹۰ روز اخیر ذکر شدن — جلوگیری از N+1
                recently_mentioned_ids = set(
                    JournalEntry.objects.filter(
                        created_at__date__gte=cutoff90, **user_filter
                    ).values_list('mentioned_nodes__id', flat=True)
                )
                recently_mentioned_ids.discard(None)

                for rel in active_rels:
                    other = rel.target if rel.source_id == root.id else rel.source
                    if other.id == root.id or other.id in seen_decay:
                        continue
                    if other.id not in recently_mentioned_ids:
                        seen_decay.add(other.id)
                        alerts.append({
                            'id':       f'decay_{rel.id}',
                            'type':     'decay',
                            'priority': 'medium',
                            'node_id':       other.id,
                            'node_username': other.username,
                            'node_name':     other.display_name(),
                            'title': f'📉 رابطه با {other.display_name()} داره ضعیف می‌شه',
                            'body':  f'مدت ۳ ماهه از {other.display_name()} توی خاطراتت ذکری نشده. این رابطه رو فراموش کردی؟',
                            'days_until': None,
                        })
    except Exception:
        pass

    # ── 7. Relationship cooling (V4 — بر اساس موتور سلامت رابطه) ──────────
    # دقیق‌تر از dormant/decay چون از تعامل‌های ثبت‌شده + رویدادها + ژورنال
    # با هم استفاده می‌کنه و «انتظار تماس» هر نفر رو جدا حساب می‌کنه.
    try:
        from .health import compute_health
        if user and user.is_authenticated:
            hmap = compute_health(user)
            week_bucket = today.isocalendar()[1]   # id هفتگی — بعد از dismiss، هفته بعد برمی‌گرده
            cooling = [h for h in hmap.values()
                       if h['status'] in ('yellow', 'red') and h['days_since'] is not None]
            cooling.sort(key=lambda h: h['score'] or 0)
            tier_names = {'inner': 'حلقه نزدیک', 'close': 'نزدیک', 'friend': 'دوست',
                          'acquaintance': 'آشنا'}
            for h in cooling[:6]:
                tier_note = f" ({tier_names[h['closeness']]})" if h.get('closeness') in tier_names else ''
                alerts.append({
                    'id':       f"cooling_{h['node_id']}_w{week_bucket}",
                    'type':     'cooling',
                    'priority': 'high' if h['status'] == 'red' else 'medium',
                    'node_id':   h['node_id'],
                    'node_name': h['name'],
                    'title': (f"🔴 رابطه با {h['name']} سرد شده" if h['status'] == 'red'
                              else f"🟡 {h['name']} منتظر یه خبره"),
                    'body': (f"{h['days_since']} روزه تعاملی با {h['name']} ثبت نشده — "
                             f"انتظار{tier_note}: هر {h['expected']} روز. "
                             f"یه تماس کوتاه هم کافیه."),
                    'days_until': None,
                    'health_score': h['score'],
                })
    except Exception:
        pass

    # ── 8. FollowUps due (V4 — موضوعات باز سررسید‌دار) ─────────────────────
    try:
        from .models import FollowUp
        fu_qs = FollowUp.objects.filter(
            done=False, due_date__isnull=False,
            due_date__lte=today + timedelta(days=2), **user_filter,
        ).select_related('node')[:10]
        for fu in fu_qs:
            dleft = (fu.due_date - today).days
            nm = fu.node.display_name()
            if dleft < 0:
                pri   = 'high'
                title = f'⏰ {-dleft} روز از سررسیدش گذشته: {fu.text[:50]}'
            elif dleft == 0:
                pri   = 'high'
                title = f'⏰ امروز سررسیدشه: {fu.text[:50]}'
            else:
                pri   = 'medium'
                title = f'📌 {dleft} روز تا سررسید: {fu.text[:50]}'
            alerts.append({
                'id':        f'followup_{fu.id}_{fu.due_date}',
                'type':      'followup',
                'priority':  pri,
                'node_id':   fu.node_id,
                'node_name': nm,
                'title':     title,
                'body':      f'موضوع باز با {nm}: «{fu.text}» — سررسید: {jalali_str(fu.due_date)}. '
                             f'«انجام دادم» بزنی خودش تیک می‌خوره.',
                'days_until': max(dleft, 0),
            })
    except Exception:
        pass

    # ── 9. یادآوری چک-این (V5 — اگه امروز هیچی ثبت نشده) ──────────────────
    try:
        if user and user.is_authenticated:
            has_today = JournalEntry.objects.filter(
                entry_date=today, **user_filter).exists() or JournalEntry.objects.filter(
                created_at__date=today, **user_filter).exists()
            if not has_today:
                alerts.append({
                    'id':       f'checkin_{today}',
                    'type':     'checkin',
                    'priority': 'low',
                    'node_id':  None,
                    'node_name': None,
                    'title':    '⚡ چک-این امروز یادت نره',
                    'body':     'حوصله‌ی ژورنال نوشتن نداری؟ چک-این ۳۰ ثانیه‌ست: /checkin/ '
                                '— فقط بگو با کیا در تماس بودی و حالت چطوره.',
                    'days_until': None,
                })
    except Exception:
        pass

    # ── 12. آیین‌های رویدادهای زندگی (V10 — سوگ/جراحی/کنکور/عروسی…) ──────
    try:
        from .models import LifeEvent, LIFE_EVENT_RITUALS
        for le in LifeEvent.objects.filter(archived=False, **user_filter).select_related('node'):
            rituals = LIFE_EVENT_RITUALS.get(le.kind, [])
            nm = le.node.display_name()
            for offset, action_text in rituals:
                due = le.date + timedelta(days=offset)
                # پنجره‌ی نمایش: از روزش تا ۲ روز بعد (که از دست نره)
                if due <= today <= due + timedelta(days=2):
                    late = (today - due).days
                    pri = 'high' if le.kind in ('mourning', 'illness') or late == 0 else 'medium'
                    alerts.append({
                        'id':       f'lifeevent_{le.id}_{offset}',
                        'type':     'lifeevent',
                        'priority': pri,
                        'node_id':   le.node_id,
                        'node_name': nm,
                        'title':    f'{le.get_kind_display().split()[0]} {nm} — {action_text}',
                        'body':     (f'{le.get_kind_display()}'
                                     + (f' ({le.title})' if le.title else '')
                                     + f' — {jalali_str(le.date)}. '
                                     + ('⏰ یه کم دیر شده، ولی هنوز ارزشش رو داره.' if late > 0 else
                                        'این لحظه‌ها رابطه رو می‌سازن.')),
                        'days_until': 0,
                    })
    except Exception:
        pass

    # ── 13. سنجش هفتگی اهداف رابطه (V10) ─────────────────────────────────
    try:
        from .models import RelationshipGoal
        from .health import compute_health as _ch
        if today.weekday() == 5:   # شنبه — شروع هفته ایرانی
            hmap_g = _ch(user) if (user and user.is_authenticated) else {}
            for g in RelationshipGoal.objects.filter(status='active', **user_filter).select_related('node')[:3]:
                cur = hmap_g.get(g.node_id, {}).get('score')
                prog = ''
                if cur is not None and g.baseline_score is not None:
                    diff = cur - g.baseline_score
                    prog = f' پیشرفت تا الان: {"+" if diff >= 0 else ""}{diff} امتیاز.'
                alerts.append({
                    'id':       f'goal_{g.id}_w{today.isocalendar()[1]}',
                    'type':     'goal',
                    'priority': 'low',
                    'node_id':   g.node_id,
                    'node_name': g.node.display_name(),
                    'title':    f'🎯 هدف هفته: {g.text[:60]}',
                    'body':     f'هدفت روی {g.node.display_name()}: «{g.text}».{prog} '
                                f'این هفته یه قدم براش بردار.',
                    'days_until': None,
                })
    except Exception:
        pass

    # ── 10. پیشنهاد آشنایی (V5 — Triadic Closure روی گراف خود کاربر) ──────
    # دوستِ دوست با ≥۲ آشنای مشترک = بیشترین شانس و سود اتصال
    try:
        if user and user.is_authenticated and user.root_node_id:
            root_id_ = user.root_node_id
            adj_ = {}
            for r_ in Relationship.objects.filter(
                owner=user, source__owner=user, target__owner=user,
            ).only('source_id', 'target_id'):
                adj_.setdefault(r_.source_id, set()).add(r_.target_id)
                adj_.setdefault(r_.target_id, set()).add(r_.source_id)
            my_nbrs_ = adj_.get(root_id_, set())
            cand_ = {}
            for nb_ in my_nbrs_:
                for nn_ in adj_.get(nb_, set()):
                    if nn_ != root_id_ and nn_ not in my_nbrs_:
                        cand_[nn_] = cand_.get(nn_, 0) + 1
            best_ = sorted(((n_, c_) for n_, c_ in cand_.items() if c_ >= 2),
                           key=lambda x: -x[1])[:2]
            month_bucket = today.strftime('%Y%m')
            for nid_, cnt_ in best_:
                try:
                    nd_ = Node.objects.get(pk=nid_, owner=user)
                except Node.DoesNotExist:
                    continue
                alerts.append({
                    'id':       f'connect_{nid_}_{month_bucket}',
                    'type':     'connect',
                    'priority': 'low',
                    'node_id':   nd_.id,
                    'node_username': nd_.username,
                    'node_name': nd_.display_name(),
                    'title':    f'🌉 با {nd_.display_name()} آشنا شو — به نفعته',
                    'body':     f'{cnt_} آشنای مشترک دارید — طبق نظریه بستار سه‌گانه (Simmel)، '
                                f'این اتصال هم راحته هم شبکه‌ات رو منسجم‌تر می‌کنه. '
                                f'توی پروفایلش «مسیر آشنایی» رو بزن تا پلن قدم‌به‌قدم بگیری.',
                    'days_until': None,
                })
    except Exception:
        pass

    # ── 11. سررسید قرض و طلب (V6) ─────────────────────────────────────────
    try:
        from .models import Debt
        due_debts = Debt.objects.filter(
            settled=False, due_date__isnull=False,
            due_date__lte=today + timedelta(days=3), **user_filter,
        ).select_related('node')[:10]
        for db_ in due_debts:
            dleft = (db_.due_date - today).days
            nm = db_.node.display_name()
            rem = f'{db_.remaining:,} {db_.currency}'
            if db_.direction == 'i_owe':
                if dleft < 0:
                    pri, title = 'high', f'💸 قرضت به {nm} {-dleft} روز گذشته!'
                    body_ = (f'{rem} به {nm} بدهکاری و سررسیدش ({jalali_str(db_.due_date)}) رد شده — '
                             f'دیر شدنِ پول، رابطه رو بی‌صدا خراب می‌کنه. امروز حلش کن.')
                elif dleft == 0:
                    pri, title = 'high', f'💸 امروز سررسید قرضت به {nm}'
                    body_ = f'{rem} — قبل از اینکه خودش مجبور بشه بگه، تو پیش‌قدم شو.'
                else:
                    pri, title = 'medium', f'💸 {dleft} روز تا سررسید قرضت به {nm}'
                    body_ = f'{rem} — از الان آماده‌ش کن. «انجام دادم» = تسویه کامل.'
            else:
                if dleft <= 0:
                    pri, title = 'medium', f'💰 طلبت از {nm} سررسید شده'
                    body_ = (f'{rem} — یه یادآوری دوستانه و محترمانه بکن؛ '
                             f'شاید یادش رفته. «انجام دادم» = گرفتمش.')
                else:
                    pri, title = 'low', f'💰 {dleft} روز تا سررسید طلبت از {nm}'
                    body_ = f'{rem} ({jalali_str(db_.due_date)})'
            alerts.append({
                'id':       f'debt_{db_.id}_{db_.due_date}',
                'type':     'debt',
                'priority': pri,
                'node_id':   db_.node_id,
                'node_name': nm,
                'title':     title,
                'body':      body_,
                'days_until': max(dleft, 0),
            })
    except Exception:
        pass

    # cooling دقیق‌تر از dormant/decay است — برای یک نفر دوتا هشدار مشابه نشون نده
    _cooling_nodes = {a['node_id'] for a in alerts if a['type'] == 'cooling'}
    alerts = [a for a in alerts
              if not (a['type'] in ('dormant', 'decay') and a.get('node_id') in _cooling_nodes)]

    # ── فیلتر کردن هشدارهایی که کاربر قبلاً اقدام کرده ────────────────────
    excluded_ids = set(
        AlertAction.objects.filter(
            action__in=['completed', 'dismissed'], **user_filter,
        ).values_list('alert_id', flat=True)
    )
    # dismissed ها بعد از ۷ روز دوباره نشون داده می‌شن
    dismissed_old = set(
        AlertAction.objects.filter(
            action='dismissed',
            created_at__date__lt=today - timedelta(days=7),
            **user_filter,
        ).values_list('alert_id', flat=True)
    )
    excluded_ids -= dismissed_old

    alerts = [a for a in alerts if a['id'] not in excluded_ids]

    if user and user.is_authenticated:
        try:
            from .models import FriendRequest
            pending_social = FriendRequest.objects.filter(
                receiver=user,
                status='pending',
            ).select_related('sender').order_by('-created_at')[:12]
            for req in pending_social:
                kind = 'connection' if getattr(req, 'request_type', '') == 'connection' else 'follow'
                title = 'درخواست کانکشن جدید' if kind == 'connection' else 'درخواست فالو جدید'
                alerts.append({
                    'id': f'social_request_{req.id}',
                    'type': 'social_request',
                    'priority': 'high' if kind == 'connection' else 'medium',
                    'title': title,
                    'subtitle': f'{req.sender.username} برای {kind} درخواست داده است.',
                    'message': req.message or '',
                    'days_until': 0,
                    'url': '/social/',
                    'created_at': req.created_at.isoformat() if req.created_at else '',
                })
        except Exception:
            pass

    # Sort: high > medium > low, then by days_until
    priority_order = {'high': 0, 'medium': 1, 'low': 2}
    alerts.sort(key=lambda a: (
        priority_order.get(a.get('priority', 'low'), 3),
        a.get('days_until', 999) if a.get('days_until') is not None else 999
    ))
    return alerts


@login_required
def alerts_api(request):
    """JSON: all current alerts."""
    return JsonResponse({'alerts': _compute_alerts(request.user)})


@login_required
def alerts_count_api(request):
    """JSON: quick badge count."""
    alerts = _compute_alerts(request.user)
    high_count = sum(1 for a in alerts if a.get('priority') == 'high')
    return JsonResponse({'total': len(alerts), 'high': high_count})


@login_required
def alert_recommendation_api(request):
    """POST {node_id, alert_type, title} → AI gift/action suggestions."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    node_id = body.get('node_id')
    alert_type = body.get('alert_type', '')
    alert_title = body.get('title', '')

    # Gather person data (owner-scoped)
    person_data = {}
    if node_id:
        try:
            node = Node.objects.get(pk=node_id, owner=request.user)
            person_data['name'] = node.display_name()
            person_data['career'] = node.career or ''
            info_obj = node.informations.first()
            if info_obj and info_obj.data:
                d = info_obj.data
                person_data['personality'] = d.get('personality', '')
                person_data['interests'] = d.get('interests', [])
                person_data['preferences'] = d.get('preferences', [])
                person_data['values'] = d.get('values', [])
                person_data['relationship_quality'] = d.get('relationship_quality', '')
                person_data['strengths'] = d.get('strengths', [])
                person_data['mood_history'] = d.get('mood', '')
        except Node.DoesNotExist:
            pass

    # ── کش: per user + node + alert type ──────────────────────────────────────
    cache_key = f'alert_rec_{request.user.id}_{node_id}_{alert_type}_{date.today().strftime("%Y%m%d")}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    prompt = f"""هشدار: {alert_title}
نوع: {alert_type}
اطلاعات شخص: {json.dumps(person_data, ensure_ascii=False)}

۵ پیشنهاد شخصی‌سازی‌شده بده:
- تولد/رویداد: ایده هدیه یا کار بر اساس علایق
- mood_alert: چطور حمایت کنیم
- dormant: چطور رابطه رو احیا کنیم

JSON:
{{
  "suggestions": [
    {{"rank": 1, "action": "...", "reason": "...", "difficulty": "آسان/متوسط/سخت"}}
  ],
  "personal_note": "یه نکته شخصی"
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'مشاور روابط اجتماعی. فقط JSON خروجی بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=900,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=24 * 3600)  # کش ۲۴ ساعته
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


@login_required
@require_POST
def alert_greeting_api(request):
    """POST {node_id, alert_type, title} → a short, ready-to-send Persian greeting.

    For birthdays / anniversaries / Iranian occasions. Personalised from the
    owner-scoped person data, cached per user+node+type per day.
    """
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    node_id = body.get('node_id')
    alert_type = str(body.get('alert_type', ''))[:40]
    alert_title = str(body.get('title', ''))[:160]

    name = 'دوستم'
    person_bits = []
    if node_id:
        try:
            node = Node.objects.get(pk=node_id, owner=request.user)
        except (Node.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'شخص پیدا نشد'}, status=404)
        name = node.display_name()
        if node.career:
            person_bits.append(f'شغل: {node.career}')
        info_obj = node.informations.first()
        if info_obj and isinstance(info_obj.data, dict):
            d = info_obj.data
            if d.get('interests'):
                person_bits.append('علایق: ' + '، '.join(map(str, d['interests'][:4])))
            if d.get('relationship_quality'):
                person_bits.append(f"کیفیت رابطه: {d['relationship_quality']}")

    cache_key = f'alert_greet_{request.user.id}_{node_id}_{alert_type}_{date.today():%Y%m%d}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'ok': True, 'greeting': cached, 'from_cache': True})

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    prompt = f"""یک پیام تبریک کوتاه فارسی برای «{name}» بنویس.
مناسبت: {alert_title} (نوع: {alert_type})
{chr(10).join(person_bits) if person_bits else ''}

قواعد:
- گرم و صمیمی، نه رسمی و کلیشه‌ای
- ۱ تا ۳ جمله، آمادهٔ ارسال
- اگر مناسبت ایرانی است (نوروز، یلدا، عید و…) فضای همان مناسبت را داشته باشد
- بدون هشتگ و بدون ایموجی زیاد (حداکثر یکی)
فقط خودِ پیام را بده، بدون توضیح."""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'نویسندهٔ پیام‌های تبریک فارسیِ گرم و کوتاه.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=220,
        )
        greeting = (resp.choices[0].message.content or '').strip().strip('"').strip()
        greeting = greeting[:600]
        if not greeting:
            return JsonResponse({'error': 'پیام خالی برگشت'}, status=502)
        cache.set(cache_key, greeting, timeout=12 * 3600)
        return JsonResponse({'ok': True, 'greeting': greeting})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


@login_required
def alerts_view(request):
    """Full /alerts/ page — includes daily context."""
    today       = timezone.localdate()
    is_hol, hol_name = is_holiday(today)
    upcoming    = upcoming_holidays(30)
    alerts      = _compute_alerts(request.user)

    # ── V4: نوار خلاصه — سلامت روابط + موضوعات باز + رویدادهای امروز ──
    health_counts = {}
    try:
        from .health import compute_health, health_summary
        health_counts = health_summary(compute_health(request.user))
    except Exception:
        pass

    open_followups_count = 0
    try:
        from .models import FollowUp
        open_followups_count = FollowUp.objects.filter(
            owner=request.user, done=False).count()
    except Exception:
        pass

    today_events_count = 0
    try:
        today_events_count = Event.objects.filter(
            owner=request.user, date=today).count()
    except Exception:
        pass

    return render(request, 'alerts/alerts.html', {
        'alerts': alerts,
        'jalali_date': jalali_str(today),
        'jalali_full': jalali_full_str(today),
        'day_name': jalali_day_name(today),
        'month_name': jalali_month_name(today),
        'season': season_fa(today),
        'is_holiday': is_hol,
        'holiday_name': hol_name,
        'upcoming_holidays': upcoming[:5],
        # V4
        'health_counts': health_counts,
        'open_followups_count': open_followups_count,
        'today_events_count': today_events_count,
    })


@login_required
def alert_action_api(request):
    """POST {alert_id, alert_type, node_id, title, action, outcome} → ذخیره اقدام کاربر."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    node = None
    node_id = body.get('node_id')
    if node_id:
        try:
            node = Node.objects.get(pk=node_id, owner=request.user)
        except Node.DoesNotExist:
            pass

    action_val = body.get('action', 'dismissed')
    alert_type = body.get('alert_type', '')

    AlertAction.objects.create(
        alert_id=body.get('alert_id', ''),
        alert_type=alert_type,
        node=node,
        title=body.get('title', ''),
        action=action_val,
        outcome=body.get('outcome', ''),
        owner=request.user,
    )

    # V6: «انجام دادم» روی هشدار قرض → تسویه کامل
    if action_val == 'completed' and alert_type == 'debt':
        try:
            from .models import Debt
            from django.utils import timezone as _tz3
            did = int((body.get('alert_id', '') or '').split('_')[1])
            db_ = Debt.objects.get(pk=did, owner=request.user)
            db_.paid = db_.amount
            db_.settled = True
            db_.settled_at = _tz3.now()
            db_.save()
        except Exception:
            pass

    # V4: «انجام دادم» روی هشدار موضوع باز → خود followup هم تیک بخوره
    if action_val == 'completed' and alert_type == 'followup':
        try:
            from .models import FollowUp
            from django.utils import timezone as _tz2
            fid = int((body.get('alert_id', '') or '').split('_')[1])
            FollowUp.objects.filter(pk=fid, owner=request.user).update(
                done=True, done_at=_tz2.now())
        except Exception:
            pass

    # V4/V10: «انجام دادم» روی هشدار سرد شدن یا آیین رویداد زندگی = تماس گرفتی
    # → تعامل خودکار ثبت بشه تا سلامت همون لحظه سبز بشه.
    if action_val == 'completed' and node and alert_type in ('cooling', 'dormant', 'decay', 'lifeevent'):
        try:
            from .models import Interaction
            from django.utils import timezone as _tz
            Interaction.objects.create(
                node=node, kind='other', date=_tz.localdate(),
                feeling=0, owner=request.user,
                note=(body.get('outcome', '') or 'ثبت‌شده از هشدار')[:300],
            )
        except Exception:
            pass   # جدول هنوز migrate نشده — مشکلی نیست

    # کش هشدارها رو پاک کن تا دفعه بعد تازه لود بشه
    cache.delete('alerts_list')
    return JsonResponse({'ok': True})


@login_required
def rename_group_api(request):
    """POST {old_name, new_name} → تغییر نام گروه (Group model)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    from .models import Group as GroupModel
    old_name = (body.get('old_name') or '').strip()
    new_name = (body.get('new_name') or '').strip()

    if not old_name or not new_name:
        return JsonResponse({'error': 'old_name و new_name لازم‌اند'}, status=400)
    if old_name == new_name:
        return JsonResponse({'ok': True})

    try:
        grp = GroupModel.objects.get(name=old_name, owner=request.user)
        grp.name = new_name
        grp.save()
        cache.delete('graph_all_data')
        return JsonResponse({'ok': True})
    except GroupModel.DoesNotExist:
        return JsonResponse({'error': f'گروه «{old_name}» پیدا نشد'}, status=404)


@login_required
def delete_group_api(request):
    """POST {name} → حذف گروه و خروج نودها از اون گروه."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    from .models import Group as GroupModel
    name = (body.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'name لازم است'}, status=400)

    deleted, _ = GroupModel.objects.filter(name=name, owner=request.user).delete()
    cache.delete('graph_all_data')
    return JsonResponse({'ok': True, 'deleted': deleted})


# ═══════════════════════════════════════════════════════════════
#  PSYCHOLOGY / SOCIOLOGY ANALYSIS
# ═══════════════════════════════════════════════════════════════

def _build_nx(user):
    import networkx as nx
    G = nx.Graph()
    all_nodes = list(Node.objects.filter(owner=user))
    all_rels  = list(Relationship.objects.filter(
        owner=user,
        source__owner=user,
        target__owner=user,
    ).select_related('source', 'target'))
    for n in all_nodes:
        G.add_node(n.id, label=n.display_name())
    for r in all_rels:
        G.add_edge(r.source_id, r.target_id, weight=r.strength, status=r.status, rel=r.rel or '')
    return G, all_nodes, all_rels


@login_required
def psychology_view(request):
    """
    Comprehensive network psychology & sociology analysis page.

    Theories implemented:
      - Dunbar's Number (1992) — cognitive limit on stable social relationships
      - Granovetter's Strength of Weak Ties (1973) — weak ties bridge structural gaps
      - Burt's Structural Holes (1992) — constraint score, brokerage positions
      - Watts & Strogatz Small World (1998) — high clustering + short path length
      - Barabási & Albert Scale-Free (1999) — power law degree distribution
      - Putnam Social Capital (2000) — bonding vs. bridging capital
      - Bowlby & Ainsworth Attachment Theory — applied to network patterns
      - Gould-Fernandez Brokerage Types (1989) — coordinator/gatekeeper/liaison
      - McPherson Homophily (2001) — birds of a feather flock together
      - Social Exchange Theory (Blau 1964) — reciprocity in relationships
      - Simmel Triadic Closure (1908) — friend-of-friend suggestions
      - Network Resilience — articulation points, node connectivity
      - Community Detection — Louvain modularity
    """
    import networkx as nx
    import math

    user = request.user
    G, all_nodes, all_rels = _build_nx(user)
    node_by_id = {node.id: node for node in all_nodes}
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()

    if n_nodes == 0:
        return render(request, 'psychology/psychology.html', {'empty': True})

    # ── Owner filter shortcut ────────────────────────────────────
    ufilter = {'owner': user}

    # ── Root node ────────────────────────────────────────────────
    root = user.root_node

    # ═══════════════════════════════════════════════════════════
    # 1. DUNBAR'S NUMBER (Robin Dunbar, 1992)
    # Cognitive limit: 5 support clique / 15 sympathy group /
    #                  50 affinity group / 150 Dunbar number / 500+
    # ═══════════════════════════════════════════════════════════
    dunbar = {'intimate': 0, 'close': 0, 'friends': 0, 'acquaintances': 0, 'weak': 0}
    dunbar_notes = []
    total_direct = 0
    if root and root.id in G:
        for _, _, d in G.edges(root.id, data=True):
            s = d.get('weight', 3)
            if s == 5:   dunbar['intimate'] += 1
            elif s == 4: dunbar['close'] += 1
            elif s == 3: dunbar['friends'] += 1
            elif s == 2: dunbar['acquaintances'] += 1
            else:        dunbar['weak'] += 1
        total_direct = sum(dunbar.values())
        if dunbar['intimate'] > 5:
            dunbar_notes.append(f"⚠️ لایه صمیمی ({dunbar['intimate']} نفر) از حد شناختی ۵ نفر بیشتر — کیفیت ممکن است افت کند")
        elif dunbar['intimate'] < 2:
            dunbar_notes.append(f"💡 لایه صمیمی ({dunbar['intimate']} نفر) بسیار کم — روابط عمیق را تقویت کن")
        if dunbar['close'] > 15:
            dunbar_notes.append(f"⚠️ لایه نزدیک ({dunbar['close']} نفر) از حد ۱۵ نفر بیشتر — انرژی شناختی تقسیم می‌شود")
        if total_direct > 150:
            dunbar_notes.append(f"⚠️ {total_direct} ارتباط مستقیم — بالاتر از عدد داونبار (۱۵۰) — مدیریت سخت‌تر می‌شود")
        elif total_direct < 15:
            dunbar_notes.append(f"💡 فقط {total_direct} ارتباط — شبکه کوچک است؛ گسترش توصیه می‌شود")
    else:
        dunbar_notes.append('نود اصلی (من) را تعریف کن تا تحلیل داونبار انجام شود')

    # ═══════════════════════════════════════════════════════════
    # 2. GRANOVETTER WEAK TIE THEORY (1973)
    # Weak ties = bridges to new info; too many → shallow network
    # Optimal: 35-65% weak ties
    # ═══════════════════════════════════════════════════════════
    strong = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) >= 4)
    weak   = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) <= 2)
    medium = n_edges - strong - weak
    weak_ratio = weak / max(n_edges, 1)
    if 0.35 <= weak_ratio <= 0.65:
        grano_status = 'optimal'; grano_label = 'بهینه'
        grano_note = f'نسبت پیوندهای ضعیف ({weak_ratio:.0%}) در محدوده مناسب — تنوع اطلاعاتی خوب است'
    elif weak_ratio < 0.35:
        grano_status = 'too_strong'; grano_label = 'بیش از حد صمیمی'
        grano_note = f'پیوندهای ضعیف کم ({weak_ratio:.0%}) — خطر اتاق پژواک (Echo Chamber)'
    else:
        grano_status = 'too_weak'; grano_label = 'روابط کم‌عمق'
        grano_note = f'پیوندهای ضعیف زیاد ({weak_ratio:.0%}) — روابط عمیق کافی نیست'

    # ═══════════════════════════════════════════════════════════
    # 3. CENTRALITY MEASURES
    # Degree: direct connections
    # Betweenness: how often on shortest path (broker power)
    # Closeness: how quickly can reach everyone
    # Eigenvector: connected to well-connected people (PageRank-like)
    # ═══════════════════════════════════════════════════════════
    deg_cent = nx.degree_centrality(G)
    btw_cent = nx.betweenness_centrality(G, normalized=True) if n_nodes > 2 else {}
    cls_cent = nx.closeness_centrality(G) if n_nodes > 1 else {}
    try:
        eig_cent = nx.eigenvector_centrality(G, max_iter=1000) if n_nodes > 1 else {}
    except Exception:
        eig_cent = {}

    top_by_deg = sorted(deg_cent.items(), key=lambda x: x[1], reverse=True)[:6]
    top_connectors = []
    for nid, deg in top_by_deg:
        try:
            nd = node_by_id.get(nid)
            if nd is None:
                continue
            top_connectors.append({
                'name': nd.display_name(),
                'degree': round(deg * 100),
                'betweenness': round(btw_cent.get(nid, 0) * 100),
                'closeness': round(cls_cent.get(nid, 0) * 100),
                'eigenvector': round(eig_cent.get(nid, 0) * 100),
            })
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # 4. CLUSTERING COEFFICIENT + ECHO CHAMBER RISK
    # High clustering = closed triangles → echo chamber risk
    # Low clustering = open network → diverse info flow
    # ═══════════════════════════════════════════════════════════
    avg_clust = nx.average_clustering(G) if n_nodes > 2 else 0
    if avg_clust > 0.65:
        echo_risk = 'high'; echo_label = 'زیاد'
    elif avg_clust > 0.35:
        echo_risk = 'medium'; echo_label = 'متوسط'
    else:
        echo_risk = 'low'; echo_label = 'کم'

    # ═══════════════════════════════════════════════════════════
    # 5. NETWORK RESILIENCE — articulation points + connectivity
    # Articulation points: remove one → graph disconnects
    # Node connectivity: min nodes to disconnect graph
    # ═══════════════════════════════════════════════════════════
    resilience_score = 0
    node_connectivity = 0
    art_point_ids = []
    critical_nodes = []
    is_connected = nx.is_connected(G)
    if is_connected and n_nodes >= 3:
        try:
            node_connectivity = nx.node_connectivity(G)
            resilience_score = min(100, node_connectivity * 25)
            art_point_ids = list(nx.articulation_points(G))
            for nid in art_point_ids[:5]:
                try:
                    nd = node_by_id.get(nid)
                    if nd is None:
                        continue
                    critical_nodes.append({
                        'name': nd.display_name(),
                        'degree': G.degree(nid),
                    })
                except Exception:
                    pass
        except Exception:
            resilience_score = 20

    # ═══════════════════════════════════════════════════════════
    # 6. COMMUNITY DETECTION + STRUCTURAL HOLES (Burt 1992)
    # Communities via Louvain algorithm (Blondel et al., 2008)
    # Structural Holes: nodes bridging communities have strategic
    # advantage — they control info flow (low constraint = good)
    # ═══════════════════════════════════════════════════════════
    n_communities = 0
    modularity_val = 0.0
    bridges = []
    bridge_ids = set()
    brokers = []
    comm_map = {}   # initialised here so Burt block can safely reference it
    try:
        from networkx.algorithms.community import louvain_communities
        comms = list(louvain_communities(G, seed=42))
        n_communities = len(comms)
        modularity_val = nx.community.modularity(G, comms)
        for i, c in enumerate(comms):
            for nid in c:
                comm_map[nid] = i
        for u, v in G.edges():
            if comm_map.get(u) != comm_map.get(v):
                bridge_ids.add(u); bridge_ids.add(v)
        for nid in list(bridge_ids)[:6]:
            try:
                nd = node_by_id.get(nid)
                if nd is not None:
                    bridges.append(nd.display_name())
            except Exception:
                pass
    except Exception:
        pass

    # Burt Constraint: lower = more structural holes = more social capital
    try:
        constraint_map = nx.constraint(G)
        sorted_constraint = sorted(constraint_map.items(), key=lambda x: x[1])
        for nid, c in sorted_constraint[:5]:
            try:
                nd = node_by_id.get(nid)
                if nd is None:
                    continue
                # Gould-Fernandez brokerage role classification
                neighbors = list(G.neighbors(nid))
                neighbor_comms = [comm_map.get(nb, -1) for nb in neighbors] if comm_map else []
                own_comm = comm_map.get(nid, -1)
                cross_comm = sum(1 for nc in neighbor_comms if nc != own_comm)
                if c < 0.2:
                    brokerage_type = 'واسطه راهبردی (Strategic Broker)'
                elif c < 0.4:
                    brokerage_type = 'رابط گروه‌ها (Bridge Connector)'
                elif c < 0.6:
                    brokerage_type = 'عضو جزئی (Partial Member)'
                else:
                    brokerage_type = 'عضو منسجم (Embedded Member)'
                brokers.append({
                    'name': nd.display_name(),
                    'constraint': round(c, 3),
                    'type': brokerage_type,
                })
            except Exception:
                pass
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # 7. TRIADIC CLOSURE — Friend Suggestions (Simmel 1908)
    # If A knows B and B knows C, A and C likely should connect.
    # People with most mutual friends = strongest suggestions.
    # ═══════════════════════════════════════════════════════════
    friend_suggestions = []
    if root and root.id in G:
        root_neighbors = set(G.neighbors(root.id))
        potential = {}
        for nb in root_neighbors:
            for nn in G.neighbors(nb):
                if nn != root.id and nn not in root_neighbors:
                    potential[nn] = potential.get(nn, 0) + 1
        sorted_potential = sorted(potential.items(), key=lambda x: x[1], reverse=True)[:5]
        for nid, common_count in sorted_potential:
            try:
                nd = node_by_id.get(nid)
                if nd is None:
                    continue
                friend_suggestions.append({
                    'name': nd.display_name(),
                    'common': common_count,
                })
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════
    # 8. SMALL WORLD ANALYSIS (Watts & Strogatz, 1998)
    # Small World: high clustering + short average path length
    # avg_path_length ≈ ln(N) means it's a small world
    # Six Degrees of Separation theory applies when APL < ln(N)*2
    # ═══════════════════════════════════════════════════════════
    avg_path_length = None
    is_small_world = False
    small_world_score = 0
    six_degrees = None
    if is_connected and 2 < n_nodes <= 300:
        try:
            avg_path_length = round(nx.average_shortest_path_length(G), 2)
            expected_random = math.log(max(n_nodes, 2))
            is_small_world = (avg_path_length <= expected_random * 1.5) and avg_clust > 0.2
            small_world_score = round(min(100, (expected_random / max(avg_path_length, 0.01)) * avg_clust * 120))
            six_degrees = avg_path_length <= 6
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # 9. SOCIAL CAPITAL (Putnam, 2000)
    # Bonding Capital: dense connections within groups (clustering)
    # Bridging Capital: connections across groups (bridge nodes)
    # Optimal: balanced mix of both
    # ═══════════════════════════════════════════════════════════
    bonding_score = round(avg_clust * 100)
    bridging_score = round(min(100, len(bridge_ids) / max(n_nodes, 1) * 200))
    if bonding_score > 70 and bridging_score < 30:
        social_capital_note = '💡 سرمایه انسجامی قوی ولی سرمایه پل‌سازی ضعیف — با گروه‌های جدید ارتباط برقرار کن'
        social_capital_status = 'bonding_heavy'
    elif bridging_score > 70 and bonding_score < 30:
        social_capital_note = '💡 پل‌سازی زیاد ولی روابط عمیق کم — روابط موجود را تعمیق بده'
        social_capital_status = 'bridging_heavy'
    elif bonding_score >= 30 and bridging_score >= 30:
        social_capital_note = '✅ ترکیب سالمی از سرمایه انسجامی و پل‌سازی داری'
        social_capital_status = 'balanced'
    else:
        social_capital_note = '⚠️ هر دو نوع سرمایه اجتماعی نیاز به تقویت دارند'
        social_capital_status = 'weak'

    # ═══════════════════════════════════════════════════════════
    # 10. SCALE-FREE NETWORK (Barabási & Albert, 1999)
    # In scale-free networks, a few nodes have very high degree
    # (hubs) — preferential attachment drives growth
    # Coefficient of Variation > 1 suggests scale-free-like behavior
    # ═══════════════════════════════════════════════════════════
    degrees = [d for _, d in G.degree()]
    avg_degree = round(sum(degrees) / max(len(degrees), 1), 1)
    max_degree = max(degrees) if degrees else 0
    if len(degrees) > 1:
        degree_variance = sum((d - avg_degree) ** 2 for d in degrees) / len(degrees)
        degree_std = degree_variance ** 0.5
        cv = degree_std / max(avg_degree, 0.01)  # Coefficient of Variation
        is_scale_free_like = cv > 1.0
    else:
        is_scale_free_like = False
        cv = 0

    # Network density (0=sparse, 1=complete)
    density = nx.density(G)
    density_pct = round(density * 100)

    # ═══════════════════════════════════════════════════════════
    # 11. MY NETWORK ROLE — position classification
    # Based on centrality profile:
    # Hub: high degree + betweenness
    # Gatekeeper/Broker: high betweenness, bridges groups
    # Clique Member: high clustering, embedded in tight group
    # Networker: moderate degree, spread across groups
    # Peripheral: low degree, on edges of network
    # ═══════════════════════════════════════════════════════════
    my_role = None
    my_role_desc = None
    my_role_color = '#6366f1'
    my_deg_val = 0
    my_btw_val = 0
    my_cls_val = 0
    my_eig_val = 0
    my_clust_val = 0
    if root and root.id in G:
        my_deg_val = round(deg_cent.get(root.id, 0) * 100)
        my_btw_val = round(btw_cent.get(root.id, 0) * 100)
        my_cls_val = round(cls_cent.get(root.id, 0) * 100)
        my_eig_val = round(eig_cent.get(root.id, 0) * 100)
        my_clust_val = round(nx.clustering(G, root.id) * 100) if n_nodes > 2 else 0
        d_pct = deg_cent.get(root.id, 0)
        b_pct = btw_cent.get(root.id, 0)
        c_coeff = nx.clustering(G, root.id) if n_nodes > 2 else 0
        if d_pct >= 0.4 and b_pct >= 0.2:
            my_role = 'هاب مرکزی'
            my_role_desc = 'تو یکی از مرکزی‌ترین گره‌های شبکه‌ای. اطلاعات و تأثیر از طریق تو جریان پیدا می‌کنه. ازین جایگاه برای ارزش‌آفرینی استفاده کن.'
            my_role_color = '#f43f5e'
        elif b_pct >= 0.25 or root.id in bridge_ids:
            my_role = 'دروازه‌بان / واسطه'
            my_role_desc = 'تو پل بین گروه‌های مختلف هستی. اطلاعات منحصربه‌فردی داری که بقیه ندارن — این قدرت استراتژیک (Structural Hole) است.'
            my_role_color = '#f59e0b'
        elif c_coeff >= 0.7:
            my_role = 'عضو کلیک'
            my_role_desc = 'دوستانت همه با هم آشنا هستن — گروه صمیمی و منسجم. ولی شاید از اطلاعات خارج از گروه محروم باشی.'
            my_role_color = '#10b981'
        elif d_pct >= 0.2:
            my_role = 'شبکه‌ساز'
            my_role_desc = 'ارتباطات متنوعی داری. نه در مرکز نه در حاشیه — موقعیت مناسب برای رشد و تأثیرگذاری.'
            my_role_color = '#8b5cf6'
        elif G.degree(root.id) <= 2:
            my_role = 'پیرامونی'
            my_role_desc = 'در حاشیه شبکه هستی. فرصت‌های اتصال زیاد وجود داره — با افزودن ارتباطات جدید می‌تونی جایگاهت رو تغییر بدی.'
            my_role_color = '#6b7280'
        else:
            my_role = 'عضو پیوندی'
            my_role_desc = 'در شبکه حضور داری و نقش اتصال‌دهنده ایفا می‌کنی — موقعیت متعادل بین صمیمیت و گستردگی.'
            my_role_color = '#06b6d4'

    # ═══════════════════════════════════════════════════════════
    # 12. ATTACHMENT STYLE HINTS (Bowlby & Ainsworth)
    # Applied heuristically to network patterns:
    # Secure: balanced intimate + active relationships
    # Avoidant: large network, few deep ties
    # Anxious/Preoccupied: very few intense ties, low total
    # ═══════════════════════════════════════════════════════════
    attachment_style = None
    attachment_desc = None
    attachment_color = '#6366f1'
    if root and root.id in G and total_direct > 0:
        intimate_cnt = dunbar.get('intimate', 0)
        close_cnt    = dunbar.get('close', 0)
        active_cnt   = Relationship.objects.filter(status='active', **ufilter).count()
        total_r      = len(all_rels)
        active_ratio = active_cnt / max(total_r, 1)
        if intimate_cnt >= 2 and close_cnt >= 3 and active_ratio >= 0.5:
            attachment_style = 'احتمالاً ایمن (Secure)'
            attachment_desc  = 'شواهد شبکه: روابط صمیمی متعادل با نرخ فعالیت بالا — ویژگی‌های دلبستگی ایمن طبق Bowlby & Ainsworth'
            attachment_color = '#10b981'
        elif intimate_cnt < 2 and total_direct > 15 and avg_clust < 0.3:
            attachment_style = 'احتمالاً اجتنابی (Dismissing-Avoidant)'
            attachment_desc  = 'شبکه بزرگ اما روابط عمیق کم — الگوی مرتبط با دلبستگی اجتنابی. توصیه: عمق دادن به روابط انتخابی'
            attachment_color = '#f59e0b'
        elif intimate_cnt >= 3 and total_direct <= 8:
            attachment_style = 'احتمالاً دوسوگرا (Anxious-Preoccupied)'
            attachment_desc  = 'تمرکز شدید روی تعداد کمی — الگوی مرتبط با دلبستگی اضطرابی. توصیه: گسترش شبکه با حفظ عمق'
            attachment_color = '#f43f5e'
        else:
            attachment_style = 'نامشخص / متنوع'
            attachment_desc  = 'داده کافی برای تشخیص قطعی الگوی دلبستگی وجود ندارد. با اضافه کردن اطلاعات بیشتر دقت افزایش می‌یابد.'
            attachment_color = '#6366f1'

    # ═══════════════════════════════════════════════════════════
    # 13. HOMOPHILY (McPherson et al., 2001)
    # Do people with same career/group tend to connect?
    # Measured by career similarity among connected pairs
    # ═══════════════════════════════════════════════════════════
    homophily_score = 0
    homophily_note = ''
    try:
        career_map = {n.id: (n.career or '').strip().lower() for n in all_nodes}
        same_career_edges = 0
        career_comparable = 0
        for u, v in G.edges():
            c_u, c_v = career_map.get(u, ''), career_map.get(v, '')
            if c_u and c_v:
                career_comparable += 1
                if c_u == c_v:
                    same_career_edges += 1
        if career_comparable > 0:
            homophily_score = round(same_career_edges / career_comparable * 100)
            if homophily_score > 60:
                homophily_note = f'🔴 همگنی بالا ({homophily_score}%) — اکثر روابطت با افراد هم‌شغل است. تنوع بیشتر توصیه می‌شود'
            elif homophily_score > 35:
                homophily_note = f'🟡 همگنی متوسط ({homophily_score}%) — ترکیبی از روابط هم‌شغل و متنوع'
            else:
                homophily_note = f'🟢 تنوع خوب ({homophily_score}% هم‌شغل) — شبکه‌ات از گروه‌های مختلف تشکیل شده'
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # 14. SOCIAL EXCHANGE THEORY (Blau, 1964) — Reciprocity
    # Active relationships as proxy for reciprocal exchange
    # ═══════════════════════════════════════════════════════════
    active_rels  = Relationship.objects.filter(status='active',   **ufilter).count()
    distant_rels = Relationship.objects.filter(status='distant',  **ufilter).count()
    inactive_rels= Relationship.objects.filter(status='inactive', **ufilter).count()
    total_r_count= len(all_rels)
    reciprocity_rate = f'{round(active_rels / max(total_r_count, 1) * 100)}%'

    # ═══════════════════════════════════════════════════════════
    # 15. RELATIONSHIP TYPE DISTRIBUTION
    # ═══════════════════════════════════════════════════════════
    rel_type_counts = {}
    for r in all_rels:
        k = r.rel or '(بدون نوع)'
        rel_type_counts[k] = rel_type_counts.get(k, 0) + 1
    rel_types_sorted = sorted(rel_type_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    max_rel_type_count = rel_types_sorted[0][1] if rel_types_sorted else 1

    # ═══════════════════════════════════════════════════════════
    # 16. JOURNAL INSIGHTS
    # ═══════════════════════════════════════════════════════════
    total_entries    = JournalEntry.objects.filter(**ufilter).count()
    analyzed_entries = JournalEntry.objects.filter(ai_analyzed=True, **ufilter).count()
    recent_moods     = list(
        JournalEntry.objects.filter(**ufilter).exclude(mood='').order_by('-created_at')
        .values_list('mood', flat=True)[:20]
    )

    # ═══════════════════════════════════════════════════════════
    # 17. LONELINESS RISK (Cacioppo & Patrick, 2008)
    # عوامل ریسک تنهایی اجتماعی بر اساس معیارهای شبکه
    # "Loneliness: Human Nature and the Need for Social Connection"
    # ═══════════════════════════════════════════════════════════
    loneliness_risk = 0
    loneliness_factors = []
    if root and root.id in G:
        if dunbar['intimate'] < 2:
            loneliness_risk += 30
            loneliness_factors.append('روابط بسیار صمیمی کم است (کمتر از ۲ نفر)')
        if dunbar['intimate'] + dunbar['close'] < 5:
            loneliness_risk += 20
            loneliness_factors.append('لایه‌های نزدیک شبکه ضعیف است')
        if total_direct < 10:
            loneliness_risk += 20
            loneliness_factors.append('شبکه کلی محدود است (کمتر از ۱۰ ارتباط)')
        if my_clust_val < 20:
            loneliness_risk += 15
            loneliness_factors.append('اعضای شبکه یکدیگر را نمی‌شناسند')
        if total_r_count > 0 and active_rels < total_r_count * 0.3:
            loneliness_risk += 15
            loneliness_factors.append('نسبت روابط فعال پایین است')
    loneliness_risk = min(100, loneliness_risk)
    if loneliness_risk >= 60:
        loneliness_label = 'ریسک بالا'; loneliness_color = '#ef4444'
    elif loneliness_risk >= 35:
        loneliness_label = 'ریسک متوسط'; loneliness_color = '#f59e0b'
    else:
        loneliness_label = 'ریسک پایین'; loneliness_color = '#10b981'

    # ═══════════════════════════════════════════════════════════
    # 18. SHANNON DIVERSITY INDEX (Shannon, 1948)
    # H = -Σ p_i × log₂(p_i) — تنوع انواع رابطه و حرفه
    # شبکه متنوع = دسترسی به اطلاعات متنوع‌تر (Burt 1992)
    # ═══════════════════════════════════════════════════════════
    diversity_h = 0.0
    diversity_normalized = 0
    career_diversity_norm = 0
    try:
        if rel_type_counts:
            total_c = sum(rel_type_counts.values())
            probs = [c / total_c for c in rel_type_counts.values()]
            diversity_h = -sum(p * math.log2(p) for p in probs if p > 0)
            max_h = math.log2(len(rel_type_counts)) if len(rel_type_counts) > 1 else 1
            diversity_normalized = round(diversity_h / max(max_h, 0.001) * 100)
        careers = [n.career for n in all_nodes if n.career and n.career.strip()]
        if careers:
            from collections import Counter as _Counter
            career_cnt = _Counter(careers)
            total_ca = len(careers)
            probs_ca = [c / total_ca for c in career_cnt.values()]
            career_h = -sum(p * math.log2(p) for p in probs_ca if p > 0)
            max_ca = math.log2(len(career_cnt)) if len(career_cnt) > 1 else 1
            career_diversity_norm = round(career_h / max(max_ca, 0.001) * 100)
    except Exception:
        pass

    if diversity_normalized >= 75:
        diversity_label = 'عالی'; diversity_color = '#10b981'
    elif diversity_normalized >= 50:
        diversity_label = 'خوب'; diversity_color = '#6366f1'
    elif diversity_normalized >= 25:
        diversity_label = 'متوسط'; diversity_color = '#f59e0b'
    else:
        diversity_label = 'کم'; diversity_color = '#ef4444'

    # ═══════════════════════════════════════════════════════════
    # 19. SOCIAL SUPPORT TYPOLOGY (Cobb, 1976)
    # چهار نوع حمایت اجتماعی: عاطفی، اطلاعاتی، ابزاری، ارزیابانه
    # "Social Support as a Moderator of Life Stress" — Psychosomatic Med
    # ═══════════════════════════════════════════════════════════
    support_emotional     = 0
    support_informational = 0
    support_instrumental  = 0
    support_appraisal     = 0
    for r in all_rels:
        rel_type = (r.rel or '').lower()
        strength = r.strength or 3
        is_family = any(t in rel_type for t in ['خانواده', 'خواهر', 'برادر', 'پدر', 'مادر', 'همسر', 'فرزند'])
        is_friend = any(t in rel_type for t in ['دوست', 'رفیق', 'صمیمی'])
        is_work   = any(t in rel_type for t in ['همکار', 'کار', 'شغل', 'استاد', 'مدیر', 'مشاور'])
        if is_family or (is_friend and strength >= 4):
            support_emotional += 1
        if is_work:
            support_instrumental += 1
        if not is_family and not is_friend and not is_work:
            support_informational += 1
        if strength >= 3 and not is_family:
            support_appraisal += 1

    _st = max(total_r_count, 1)
    support_emotional_pct     = round(support_emotional     / _st * 100)
    support_informational_pct = round(support_informational / _st * 100)
    support_instrumental_pct  = round(support_instrumental  / _st * 100)
    support_appraisal_pct     = round(support_appraisal     / _st * 100)
    support_notes = []
    if support_emotional < 3:
        support_notes.append('⚠️ پشتیبانی عاطفی ضعیف — روابط صمیمی بیشتری نیاز است')
    if support_instrumental < 2:
        support_notes.append('💡 شبکه حرفه‌ای محدود — ارتباطات شغلی را گسترش بده')
    if support_informational < 5:
        support_notes.append('💡 منابع اطلاعاتی کم — با افراد متنوع‌تر آشنا شو')

    # ═══════════════════════════════════════════════════════════
    # 20. EGO NETWORK EMBEDDEDNESS (Granovetter, 1985)
    # هر رابطه چقدر با دوستان مشترک تقویت شده؟
    # "Economic Action and Social Structure: The Problem of Embeddedness"
    # ═══════════════════════════════════════════════════════════
    embeddedness_list = []
    avg_embeddedness  = 0
    if root and root.id in G and total_direct > 0:
        ego_nbrs = set(G.neighbors(root.id))
        for nb in ego_nbrs:
            nb_nbrs = set(G.neighbors(nb))
            mutual  = len(ego_nbrs & nb_nbrs)
            nd = node_by_id.get(nb)
            if nd is None:
                continue
            embeddedness_list.append({
                'name': nd.display_name(),
                'mutual': mutual,
                'strength': G.get_edge_data(root.id, nb, {}).get('weight', 3),
            })
        embeddedness_list.sort(key=lambda x: (-x['mutual'], -x['strength']))
        avg_embeddedness = round(
            sum(e['mutual'] for e in embeddedness_list) / max(len(embeddedness_list), 1), 1
        )

    # ═══════════════════════════════════════════════════════════
    # 21. STRUCTURAL TRANSITIVITY & BALANCE (Heider, 1946)
    # "The Psychology of Interpersonal Relations"
    # Transitivity = 3 × مثلث‌ها / مسیرهای دو گام
    # هر چه بالاتر → شبکه متعادل‌تر و پایدارتر
    # ═══════════════════════════════════════════════════════════
    transitivity_pct = 0
    n_triangles      = 0
    balance_label    = 'نامشخص'
    balance_note     = ''
    try:
        transitivity     = nx.transitivity(G)
        n_triangles      = sum(nx.triangles(G).values()) // 3
        transitivity_pct = round(transitivity * 100)
        if transitivity >= 0.5:
            balance_label = 'بالا — شبکه منسجم'
            balance_note  = 'بیش از نیمی از سه‌گانه‌ها بسته‌اند — نشانه روابط پایدار، اعتماد متقابل و تعادل ساختاری'
        elif transitivity >= 0.25:
            balance_label = 'متوسط'
            balance_note  = 'تعادل نسبی در شبکه — ترکیبی از روابط باز و بسته'
        else:
            balance_label = 'پایین'
            balance_note  = 'بیشتر روابط مستقل‌اند — فرصت زیادی برای Triadic Closure وجود دارد'
    except Exception:
        transitivity = 0

    # ═══════════════════════════════════════════════════════════
    # 22. EMOTIONAL CONTAGION RISK (Hatfield et al., 1993)
    # "Emotional Contagion" — احساسات از طریق شبکه نزدیک منتقل می‌شن
    # اگه دوستان صمیمی اخیراً حال بدی داشتن، خطر بالاتره
    # ═══════════════════════════════════════════════════════════
    negative_moods_nearby = 0
    contagion_risk_pct    = 0
    try:
        neg_words = ['ناراحت', 'غمگین', 'استرس', 'اضطراب', 'عصبانی', 'نگران',
                     'sad', 'stress', 'anxious', 'depressed', 'تنها', 'افسرده']
        cutoff14 = date.today() - timedelta(days=14)
        for entry in JournalEntry.objects.filter(
            created_at__date__gte=cutoff14, ai_analyzed=True, **ufilter
        ).prefetch_related('mentioned_nodes')[:20]:
            if entry.mood and any(neg in entry.mood.lower() for neg in neg_words):
                for mnode in entry.mentioned_nodes.all():
                    edata = G.get_edge_data(root.id, mnode.id) if root and mnode.id in G else None
                    if edata and edata.get('weight', 3) >= 3:
                        negative_moods_nearby += 1
                        break
        contagion_risk_pct = min(100, negative_moods_nearby * 25)
    except Exception:
        pass

    if contagion_risk_pct >= 60:
        contagion_label = 'بالا'; contagion_color = '#ef4444'
    elif contagion_risk_pct >= 30:
        contagion_label = 'متوسط'; contagion_color = '#f59e0b'
    else:
        contagion_label = 'پایین'; contagion_color = '#10b981'

    # ═══════════════════════════════════════════════════════════
    # 23. CORE-PERIPHERY STRUCTURE (Borgatti & Everett, 2000)
    # هسته: نودهایی با degree بالاتر از میانگین → core
    # حاشیه: نودهایی با degree پایین‌تر → periphery
    # "Models of Core/Periphery Structures" — Social Networks
    # ═══════════════════════════════════════════════════════════
    core_nodes     = []
    periphery_nodes = []
    core_pct       = 0
    try:
        avg_deg_val = avg_degree
        for n in all_nodes:
            deg = G.degree(n.id) if n.id in G else 0
            if deg > avg_deg_val:
                core_nodes.append({'name': n.display_name(), 'degree': deg})
            else:
                periphery_nodes.append({'name': n.display_name(), 'degree': deg})
        core_nodes.sort(key=lambda x: -x['degree'])
        core_pct = round(len(core_nodes) / max(n_nodes, 1) * 100)
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # 24. RELATIONSHIP STRENGTH DISTRIBUTION
    # توزیع قدرت روابط — آمار توصیفی
    # ═══════════════════════════════════════════════════════════
    strengths_all = [r.strength or 3 for r in all_rels]
    if strengths_all:
        avg_strength = round(sum(strengths_all) / len(strengths_all), 2)
        str_dist = {}
        for s in strengths_all:
            k = max(1, min(5, s))
            str_dist[k] = str_dist.get(k, 0) + 1
        str_dist = {i: str_dist.get(i, 0) for i in range(1, 6)}
    else:
        avg_strength = 0
        str_dist = {i: 0 for i in range(1, 6)}

    # ═══════════════════════════════════════════════════════════
    # 25. NETWORK HEALTH SCORE — COMPOSITE (FamilyGraph)
    # امتیاز سلامت کلی شبکه از ۶ بُعد مستقل
    # ═══════════════════════════════════════════════════════════
    weak_ratio_pct_val = round(weak_ratio * 100)
    health_components = {
        'تنوع رابطه':     min(100, diversity_normalized),
        'سرمایه پل‌سازی': min(100, bridging_score),
        'روابط صمیمی':    min(100, dunbar.get('intimate', 0) * 25),
        'تعادل پیوند':    max(0, 100 - abs(weak_ratio_pct_val - 50) * 2) if n_edges else 0,
        'نرخ فعالیت':     round(active_rels / max(total_r_count, 1) * 100),
        'تاب‌آوری':       min(100, resilience_score),
    }
    health_score = round(
        health_components['تنوع رابطه']     * 0.15
        + health_components['سرمایه پل‌سازی'] * 0.15
        + health_components['روابط صمیمی']   * 0.20
        + health_components['تعادل پیوند']   * 0.15
        + health_components['نرخ فعالیت']    * 0.20
        + health_components['تاب‌آوری']      * 0.15
    )
    if health_score >= 75:
        health_label = 'عالی'; health_color = '#10b981'
    elif health_score >= 55:
        health_label = 'خوب'; health_color = '#6366f1'
    elif health_score >= 35:
        health_label = 'متوسط'; health_color = '#f59e0b'
    else:
        health_label = 'نیاز به توجه'; health_color = '#ef4444'

    context = {
        # Basic counts
        'n_nodes': n_nodes,
        'n_edges': n_edges,
        'density': round(density, 3),
        'density_pct': density_pct,
        'avg_degree': avg_degree,
        'max_degree': max_degree,

        # Dunbar
        'dunbar': dunbar,
        'dunbar_notes': dunbar_notes,
        'total_direct': total_direct,

        # Granovetter
        'strong': strong, 'weak': weak, 'medium': medium,
        'weak_ratio_pct': round(weak_ratio * 100),
        'grano_status': grano_status,
        'grano_label': grano_label,
        'grano_note': grano_note,

        # Centrality
        'top_connectors': top_connectors,

        # Clustering / Echo Chamber
        'avg_clust': round(avg_clust, 3),
        'avg_clust_pct': round(avg_clust * 100),
        'echo_risk': echo_risk,
        'echo_label': echo_label,

        # Resilience
        'resilience_score': resilience_score,
        'node_connectivity': node_connectivity,
        'critical_nodes': critical_nodes,
        'art_point_count': len(art_point_ids),

        # Community + Structural Holes
        'n_communities': n_communities,
        'modularity_val': round(modularity_val, 3),
        'bridges': bridges,
        'brokers': brokers,

        # Triadic Closure
        'friend_suggestions': friend_suggestions,

        # Small World
        'avg_path_length': avg_path_length,
        'is_small_world': is_small_world,
        'small_world_score': small_world_score,
        'six_degrees': six_degrees,

        # Social Capital
        'bonding_score': bonding_score,
        'bridging_score': bridging_score,
        'social_capital_note': social_capital_note,
        'social_capital_status': social_capital_status,

        # Scale-free
        'is_scale_free_like': is_scale_free_like,
        'degree_cv': round(cv, 2),

        # My Role
        'my_role': my_role,
        'my_role_desc': my_role_desc,
        'my_role_color': my_role_color,
        'my_deg_val': my_deg_val,
        'my_btw_val': my_btw_val,
        'my_cls_val': my_cls_val,
        'my_eig_val': my_eig_val,
        'my_clust_val': my_clust_val,

        # Attachment Style
        'attachment_style': attachment_style,
        'attachment_desc': attachment_desc,
        'attachment_color': attachment_color,

        # Homophily
        'homophily_score': homophily_score,
        'homophily_note': homophily_note,

        # Reciprocity / Status
        'active_rels': active_rels,
        'distant_rels': distant_rels,
        'inactive_rels': inactive_rels,
        'reciprocity_rate': reciprocity_rate,

        # Relationship types
        'rel_types': rel_types_sorted,
        'max_rel_type_count': max_rel_type_count,

        # Journal
        'total_entries': total_entries,
        'analyzed_entries': analyzed_entries,
        'recent_moods_json': recent_moods,

        # ── NEW: Loneliness Risk (17) ──────────────────────────
        'loneliness_risk': loneliness_risk,
        'loneliness_label': loneliness_label,
        'loneliness_color': loneliness_color,
        'loneliness_factors': loneliness_factors,

        # ── NEW: Shannon Diversity (18) ────────────────────────
        'diversity_normalized': diversity_normalized,
        'diversity_label': diversity_label,
        'diversity_color': diversity_color,
        'career_diversity_norm': career_diversity_norm,

        # ── NEW: Social Support (19) ───────────────────────────
        'support_emotional': support_emotional,
        'support_informational': support_informational,
        'support_instrumental': support_instrumental,
        'support_appraisal': support_appraisal,
        'support_emotional_pct': support_emotional_pct,
        'support_informational_pct': support_informational_pct,
        'support_instrumental_pct': support_instrumental_pct,
        'support_appraisal_pct': support_appraisal_pct,
        'support_notes': support_notes,

        # ── NEW: Embeddedness (20) ─────────────────────────────
        'embeddedness_list': embeddedness_list[:8],
        'avg_embeddedness': avg_embeddedness,

        # ── NEW: Balance/Transitivity (21) ────────────────────
        'transitivity_pct': transitivity_pct,
        'n_triangles': n_triangles,
        'balance_label': balance_label,
        'balance_note': balance_note,

        # ── NEW: Emotional Contagion (22) ─────────────────────
        'contagion_risk_pct': contagion_risk_pct,
        'contagion_label': contagion_label,
        'contagion_color': contagion_color,

        # ── NEW: Core-Periphery (23) ───────────────────────────
        'core_nodes': core_nodes[:8],
        'core_pct': core_pct,
        'periphery_count': len(periphery_nodes),

        # ── NEW: Strength Distribution (24) ───────────────────
        'avg_strength': avg_strength,
        'str_dist': str_dist,

        # ── NEW: Health Score (25) ────────────────────────────
        'health_score': health_score,
        'health_label': health_label,
        'health_color': health_color,
        'health_components': health_components,
    }

    # ── V5: نظریه‌های رفتاری (از داده‌های تعامل/فالوآپ/ژورنال) ──
    try:
        from .theories import extra_theories
        context['extra_theories'] = extra_theories(user)
    except Exception:
        context['extra_theories'] = []
    from .theory_catalog import THEORY_CATALOG
    context['theory_catalog'] = THEORY_CATALOG

    return render(request, 'psychology/psychology.html', context)


@login_required
def psychology_ai_api(request):
    """POST → comprehensive AI psychology+sociology narrative. Cached 6h per user."""
    cache_key = f'psych_ai_{request.user.id}_{date.today().strftime("%Y%m%d")}'
    body = {}
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        pass
    if not isinstance(body, dict):
        body = {}
    cached = cache.get(cache_key)
    if cached and not (request.GET.get('refresh') or body.get('refresh')):
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    user = request.user
    G, all_nodes, all_rels = _build_nx(user)
    node_by_id = {node.id: node for node in all_nodes}
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()

    if n_nodes == 0:
        return JsonResponse({'error': 'شبکه خالی است'}, status=400)

    import networkx as nx, math
    avg_clust = nx.average_clustering(G) if n_nodes > 2 else 0
    density   = nx.density(G)
    is_connected = nx.is_connected(G)

    deg_cent = nx.degree_centrality(G)
    btw_cent = nx.betweenness_centrality(G, normalized=True) if n_nodes > 2 else {}

    degrees  = [d for _, d in G.degree()]
    avg_deg  = round(sum(degrees) / max(len(degrees), 1), 1)
    max_deg  = max(degrees) if degrees else 0

    strong = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) >= 4)
    weak   = sum(1 for _, _, d in G.edges(data=True) if d.get('weight', 3) <= 2)
    weak_ratio = weak / max(n_edges, 1)

    avg_path = None
    if is_connected and 2 < n_nodes <= 200:
        try:
            avg_path = round(nx.average_shortest_path_length(G), 2)
        except Exception:
            pass

    ufilter = {'owner': user}
    root = user.root_node

    # Dunbar layers for root
    dunbar = {'intimate': 0, 'close': 0, 'friends': 0, 'acquaintances': 0, 'weak': 0}
    if root and root.id in G:
        for _, _, d in G.edges(root.id, data=True):
            s = d.get('weight', 3)
            if s == 5:   dunbar['intimate'] += 1
            elif s == 4: dunbar['close'] += 1
            elif s == 3: dunbar['friends'] += 1
            elif s == 2: dunbar['acquaintances'] += 1
            else:        dunbar['weak'] += 1

    # Communities
    n_communities = 0
    try:
        from networkx.algorithms.community import louvain_communities
        comms = list(louvain_communities(G, seed=42))
        n_communities = len(comms)
    except Exception:
        pass

    # Relationship types
    rel_types = {}
    for r in all_rels:
        k = r.rel or 'نامشخص'
        rel_types[k] = rel_types.get(k, 0) + 1

    # Top betweenness nodes (potential brokers)
    top_brokers = []
    for nid, b in sorted(btw_cent.items(), key=lambda x: x[1], reverse=True)[:3]:
        node = node_by_id.get(nid)
        if node is not None:
            top_brokers.append(node.display_name())

    # Recent moods
    recent_moods = list(
        JournalEntry.objects.filter(**ufilter).exclude(mood='').order_by('-created_at')
        .values_list('mood', flat=True)[:10]
    )

    active_cnt = Relationship.objects.filter(status='active', **ufilter).count()

    network_summary = {
        'افراد_شبکه': n_nodes,
        'روابط': n_edges,
        'چگالی_شبکه': round(density, 3),
        'میانگین_درجه': avg_deg,
        'حداکثر_درجه': max_deg,
        'پیوندهای_قوی': strong,
        'پیوندهای_ضعیف': weak,
        'نسبت_پیوند_ضعیف': f'{round(weak_ratio*100)}%',
        'ضریب_خوشه‌بندی': round(avg_clust, 3),
        'تعداد_گروه_اجتماعی': n_communities,
        'میانگین_مسیر_کوتاه': avg_path,
        'روابط_فعال': active_cnt,
        'لایه_داونبار': dunbar,
        'انواع_رابطه': dict(sorted(rel_types.items(), key=lambda x: x[1], reverse=True)[:6]),
        'واسطه‌های_اصلی': top_brokers,
        'حال_و_هوای_اخیر': recent_moods,
    }

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    prompt = f"""تو یه روانشناس و جامعه‌شناس متخصص شبکه‌های اجتماعی هستی. داده‌های شبکه اجتماعی شخصی زیر رو با عمق کامل تحلیل کن:

{json.dumps(network_summary, ensure_ascii=False, indent=2)}

این تحلیل باید شامل همه این تئوری‌ها باشه:
• نظریه داونبار (Dunbar): تحلیل لایه‌های شناختی و ظرفیت مدیریت روابط
• نظریه گرانووتر (Granovetter): ارزیابی پیوندهای ضعیف و قوی و نقش اطلاع‌رسانی
• حفره‌های ساختاری بورت (Burt): موقعیت واسطه‌ای و سرمایه اجتماعی
• دنیای کوچک واتس-استروگاتز (Watts-Strogatz): تحلیل ساختار «شش درجه جدایی»
• سرمایه اجتماعی پاتنام (Putnam): تعادل سرمایه انسجامی vs پل‌سازی
• نظریه دلبستگی بولبی-اینسورث (Bowlby-Ainsworth): الگوی دلبستگی شبکه
• نظریه مبادله اجتماعی بلاو (Blau): تعادل و عمل‌متقابل در روابط
• همگنی مک‌فرسون (McPherson): تنوع یا همگنی در شبکه
• شبکه‌های مقیاس‌آزاد باراباسی (Barabási): توزیع قدرت در شبکه

خروجی JSON (به فارسی کامل):
{{
  "health": {{"score": 0-100, "label": "وضعیت کلی", "summary": "خلاصه ارزیابی (۳-۴ جمله)"}},
  "patterns": ["الگوی ۱ با پشتوانه تئوری", "الگوی ۲", "الگوی ۳"],
  "risks": ["ریسک ۱ با توضیح", "ریسک ۲", "ریسک ۳"],
  "opportunities": ["فرصت ۱", "فرصت ۲", "فرصت ۳"],
  "recommendations": [
    {{"action": "اقدام مشخص عملی", "theory": "پشتوانه نظری", "impact": "بالا/متوسط/کم"}},
    {{"action": "...", "theory": "...", "impact": "..."}}
  ],
  "psychological_profile": "پروفایل روانشناختی کامل (۵-۷ جمله عمیق)",
  "sociological_summary": "خلاصه جامعه‌شناختی کامل (۵-۷ جمله)"
}}

فقط JSON. عمیق و تخصصی به فارسی."""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'متخصص روانشناسی و جامعه‌شناسی شبکه‌های اجتماعی. فقط JSON خروجی بده.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=2500,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=6 * 3600)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)


# ═══════════════════════════════════════════════════════════════
#  DAILY TIPS
# ═══════════════════════════════════════════════════════════════

@login_required
def daily_tips_view(request):
    """V10: بریفینگ روزانه — ماموریت‌ها + نبض شبکه + فلش‌بک + نکته عمیق AI."""
    user = request.user
    today = timezone.localdate()
    is_hol, hol_name = is_holiday(today)
    upcoming = upcoming_holidays(30)

    alerts = _compute_alerts(user)

    # ── 🎯 ماموریت‌های امروز: مهم‌ترین هشدارهای قابلِ اقدام ──
    type_rank = {'lifeevent': 0, 'birthday': 1, 'followup': 2, 'debt': 3,
                 'event': 4, 'cooling': 5, 'mood_alert': 6, 'connect': 7,
                 'dormant': 8, 'decay': 9, 'goal': 10}
    pri_rank = {'high': 0, 'medium': 1, 'low': 2}
    mission_icons = {'lifeevent': '🎗', 'birthday': '🎂', 'followup': '📌',
                     'debt': '💰', 'event': '📅', 'cooling': '🔥',
                     'mood_alert': '💛', 'connect': '🌉', 'dormant': '🌱',
                     'decay': '📉', 'goal': '🎯'}
    actionable = [a for a in alerts if a.get('type') in type_rank]
    actionable.sort(key=lambda a: (pri_rank.get(a.get('priority'), 3),
                                   type_rank[a['type']]))
    missions = actionable[:4]
    for m in missions:
        m['icon'] = mission_icons.get(m['type'], '🎯')

    # ── 💓 نبض شبکه ──
    health_counts = {}
    try:
        from .health import compute_health, health_summary
        health_counts = health_summary(compute_health(user))
    except Exception:
        pass
    open_fu = 0
    try:
        from .models import FollowUp
        open_fu = FollowUp.objects.filter(owner=user, done=False).count()
    except Exception:
        pass
    net_balance = 0
    try:
        from .models import Debt
        for d_ in Debt.objects.filter(owner=user, settled=False):
            net_balance += d_.remaining if d_.direction == 'they_owe' else -d_.remaining
    except Exception:
        pass
    streak, checked_today = 0, False
    try:
        from .views_checkin import journal_streak, _todays_checkin
        streak = journal_streak(user)
        checked_today = _todays_checkin(user) is not None
    except Exception:
        pass

    # ── 🕰 فلش‌بک: امروز در سال‌های گذشته ──
    flashback = []
    try:
        for e in JournalEntry.objects.filter(
                owner=user, entry_date__month=today.month,
                entry_date__day=today.day, entry_date__lt=today
        ).order_by('-entry_date')[:3]:
            yrs = today.year - e.entry_date.year
            flashback.append({
                'icon': '📓', 'years': yrs,
                'title': f'{yrs} سال پیش، همین روز نوشتی:',
                'text': e.text[:220], 'mood': e.mood,
            })
    except Exception:
        pass
    try:
        for ev in Event.objects.filter(
                owner=user, date__month=today.month,
                date__day=today.day, date__lt=today
        ).prefetch_related(
            Prefetch('participants', queryset=Node.objects.filter(owner=user))
        ).order_by('-date')[:2]:
            yrs = today.year - ev.date.year
            parts = [p.display_name() for p in ev.participants.all()[:3]]
            flashback.append({
                'icon': '📅', 'years': yrs,
                'title': f'{yrs} سال پیش، همین روز: {ev.title}',
                'text': ('با ' + '، '.join(parts)) if parts else (ev.description or '')[:150],
                'mood': '',
            })
    except Exception:
        pass
    flashback.sort(key=lambda f: f['years'])

    # ── سلام بر اساس ساعت ──
    hour = timezone.localtime().hour
    if hour < 12:
        greeting = 'صبح بخیر ☀️'
    elif hour < 17:
        greeting = 'ظهر بخیر 🌤'
    elif hour < 20:
        greeting = 'عصر بخیر 🌇'
    else:
        greeting = 'شب بخیر 🌙'

    context = {
        'today':         today,
        'jalali_date':   jalali_str(today),
        'jalali_full':   jalali_full_str(today),
        'day_name':      jalali_day_name(today),
        'month_name':    jalali_month_name(today),
        'season':        season_fa(today),
        'is_holiday':    is_hol,
        'holiday_name':  hol_name,
        'upcoming_holidays': upcoming[:4],
        # V10
        'greeting':        greeting,
        'first_name':      user.first_name or user.username,
        'missions':        missions,
        'health_counts':   health_counts,
        'open_fu':         open_fu,
        'net_balance':     net_balance,
        'net_balance_fmt': f'{abs(net_balance):,}',
        'streak':          streak,
        'checked_today':   checked_today,
        'flashback':       flashback[:4],
    }
    return render(request, 'daily/daily.html', context)


@login_required
def daily_tips_api(request):
    """POST → AI daily network tips — با تقویم شمسی و تعطیلات ایرانی."""
    today       = timezone.localdate()
    is_hol, hol_name = is_holiday(today)
    day_name    = jalali_day_name(today)
    jalali_date = jalali_str(today)
    season      = season_fa(today)

    req_user = request.user
    ufilter  = {'owner': req_user}

    n_nodes = Node.objects.filter(**ufilter).count()
    n_edges = Relationship.objects.filter(**ufilter).count()

    alerts = _compute_alerts(req_user)
    urgent = [a['title'] for a in alerts if a.get('priority') == 'high'][:3]

    recent_moods = list(
        JournalEntry.objects.filter(**ufilter).order_by('-created_at').exclude(mood='').values_list('mood', flat=True)[:5]
    )

    weak_rels  = list(Relationship.objects.filter(
        strength__lte=2, target__owner=req_user, **ufilter
    ).select_related('target')[:5])
    weak_names = [r.target.display_name() for r in weak_rels]

    root = req_user.root_node
    cutoff14 = today - timedelta(days=14)
    mentioned_ids = set(
        JournalEntry.objects.filter(entry_date__gte=cutoff14, **ufilter).values_list('mentioned_nodes__id', flat=True)
    )
    mentioned_ids.discard(None)
    if root:
        connected_ids = set(
            Relationship.objects.filter(source=root, **ufilter).values_list('target_id', flat=True)
        ) | set(
            Relationship.objects.filter(target=root, **ufilter).values_list('source_id', flat=True)
        )
    else:
        connected_ids = set()

    overlooked = list(Node.objects.filter(id__in=connected_ids - mentioned_ids - {root.id if root else 0}, **ufilter)[:4])
    overlooked_names = [n.display_name() for n in overlooked]

    # تعطیلات نزدیک (۱۴ روز آینده)
    near_holidays = upcoming_holidays(14)
    near_hol_str  = ', '.join(f'{h["jalali"]} ({h["holiday"]})' for h in near_holidays) if near_holidays else 'ندارد'

    # ── کش — per user so each user gets their own tips ──────────────────────
    uid = request.user.id if request.user.is_authenticated else 0
    cache_key = f'daily_tips_{uid}_{today.strftime("%Y%m%d")}'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse({'ok': True, 'result': cached, 'from_cache': True})

    client, api_key, _provider = _ai_client()
    if not api_key:
        return JsonResponse({'error': 'API key نیست'}, status=500)

    # ── نوع روز ─────────────────────────────────────────────────────────────
    if is_hol and hol_name != 'جمعه':
        day_type_desc = f'تعطیل رسمی ({hol_name}) — روز استراحت و جشن'
        day_context   = f'امروز تعطیل رسمی ({hol_name}) است. نکاتی مناسب این مناسبت بده — تبریک، دید و بازدید، فعالیت‌های جمعی و لذت‌بخش.'
    elif is_hol:
        day_type_desc = 'جمعه — روز تعطیل'
        day_context   = 'امروز جمعه و تعطیله. نکات برای وقت آزاد: خانواده، دوستان صمیمی، استراحت، شارژ انرژی.'
    else:
        day_type_desc = 'روز کاری'
        day_context   = 'امروز روز کاریه. نکات برای ارتباطات هدفمند، پیگیری کارها، تقویت شبکه حرفه‌ای و شخصی.'

    prompt = f"""تو یه مشاور روابط اجتماعی هستی.

📅 امروز: {day_name}، {jalali_date} | فصل: {season} | وضعیت: {day_type_desc}
🔜 تعطیلات نزدیک: {near_hol_str}

وضعیت شبکه روابط:
- {n_nodes} نفر، {n_edges} رابطه
- حال و هوای اخیر: {', '.join(recent_moods) if recent_moods else 'ثبت نشده'}
- هشدارهای فوری: {', '.join(urgent) if urgent else 'ندارد'}
- روابط ضعیف (نیاز به توجه): {', '.join(weak_names) if weak_names else 'ندارد'}
- مدتی از اینها بی‌خبری: {', '.join(overlooked_names) if overlooked_names else 'ندارد'}

{day_context}

اگه تعطیل رسمیه یا نزدیکه به تعطیل رسمی، نکاتت رو با اون مناسبت align کن.
تقویم ایرانی و فرهنگ ایرانی رو در نظر بگیر.

۴-۵ نکته عملی، کوتاه و قابل اجرا برای همین امروز بده.

JSON:
{{
  "day_message": "پیام کوتاه متناسب با روز (مناسبت رو ذکر کن اگه داره)",
  "tips": [
    {{
      "emoji": "...",
      "title": "...",
      "action": "اقدام مشخص و قابل اجرا",
      "reason": "چرا این کار امروز مهمه؟",
      "time_needed": "۵ دقیقه"
    }}
  ],
  "focus_person": {{"name": "...", "suggestion": "..."}}
}}"""

    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[
                {'role': 'system', 'content': 'مشاور روابط اجتماعی ایرانی. فقط JSON خروجی بده. بدون markdown.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=1400,
        )
        result = _extract_json(resp.choices[0].message.content)
        cache.set(cache_key, result, timeout=24 * 3600)
        return JsonResponse({'ok': True, 'result': result})
    except Exception as e:
        return JsonResponse({'error': _rate_limit_msg(e)}, status=500)
