"""
Extra journal views — imported in urls.py alongside main views.
"""
import json
from datetime import datetime, timedelta
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import ArtisticWork, JournalEntry, JournalImage, ProfileMediaItem
from .utils_jalali import jalali_input_value, parse_date_input


def _extract_profile_media_from_journal(entry):
    import re
    text = entry.text or ''
    patterns = [
        ('book', r'(?:کتاب|رمان)\s+[«"“]?([^»"”\n،,.]{2,80})[»"”]?\s*(?:رو|را)?\s*(?:تموم|تمام|خواندم|خوندم)'),
        ('movie', r'(?:فیلم)\s+[«"“]?([^»"”\n،,.]{2,80})[»"”]?\s*(?:رو|را)?\s*(?:دیدم|تماشا کردم)'),
        ('series', r'(?:سریال)\s+[«"“]?([^»"”\n،,.]{2,80})[»"”]?\s*(?:رو|را)?\s*(?:دیدم|تموم|تمام|تماشا کردم)'),
    ]
    for kind, pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            title = match.group(1).strip(' .،:؛"«»')
            if title:
                work, _ = ArtisticWork.objects.get_or_create(
                    kind=kind,
                    title=title[:240],
                    defaults={
                        'analysis': {'summary': 'این اثر از خاطره کاربر کشف شده و برای شناخت شخصیت او استفاده می‌شود.'},
                    },
                )
                ProfileMediaItem.objects.get_or_create(
                    user=entry.owner,
                    kind=kind,
                    title=title[:240],
                    defaults={
                        'work': work,
                        'rating': 0,
                        'completed_on': entry.entry_date,
                        'source': 'journal',
                        'source_journal': entry,
                        'notes': text[:400],
                        'analysis': {'signal': 'این اثر از متن خاطره تشخیص داده شده و هنوز امتیاز دستی ندارد.'},
                    },
                )


@login_required
def journal_save_api(request):
    """Save a journal entry without AI analysis."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({'error': 'JSON object required'}, status=400)

    text = body.get('text')
    if not isinstance(text, str):
        return JsonResponse({'error': 'text must be a string'}, status=400)
    text = text.strip()
    if not text:
        return JsonResponse({'error': 'متن خالی است'}, status=400)

    raw_entry_id = body.get('entry_id')
    entry_id = raw_entry_id if isinstance(raw_entry_id, int) and not isinstance(raw_entry_id, bool) else None
    entry = None
    if entry_id:
        entry = JournalEntry.objects.filter(id=entry_id, owner=request.user).first()
        if not entry:
            return JsonResponse({'error': 'journal entry not found'}, status=404)

    bucket = int(timezone.now().timestamp() // 3600)
    rate_key = f'anti-spam:journal-moment:{request.user.pk}:{bucket}'
    moment_count = 1 if cache.add(rate_key, 1, timeout=3600) else cache.incr(rate_key)
    if moment_count > 30:
        return JsonResponse({'error': 'تعداد ثبت لحظه در این ساعت زیاد است؛ کمی بعد ادامه بده.', 'retry_after': 3600}, status=429)
    normalized = ' '.join(text.lower().split())
    recent_entries = JournalEntry.objects.filter(
        owner=request.user, created_at__gte=timezone.now() - timedelta(minutes=5)
    ).exclude(id=entry_id).only('text')
    if entry is None and any(' '.join(item.text.lower().split()) == normalized for item in recent_entries):
        return JsonResponse({'error': 'همین لحظه را همین چند دقیقه پیش ثبت کرده‌ای.'}, status=400)

    entry_date_str = body.get('entry_date')
    entry_date_str = entry_date_str.strip() if isinstance(entry_date_str, str) else ''
    entry_date = None
    if entry_date_str:
        try:
            entry_date = parse_date_input(entry_date_str)
        except Exception:
            pass

    raw_tags = body.get('tags', [])
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(',') if t.strip()]
    elif isinstance(raw_tags, list):
        raw_tags = [str(tag).strip()[:80] for tag in raw_tags if str(tag).strip()][:30]
    else:
        raw_tags = []

    occurred_at = entry.occurred_at if entry else timezone.now()
    raw_occurred_at = body.get('occurred_at', '')
    if raw_occurred_at:
        try:
            occurred_at = datetime.fromisoformat(raw_occurred_at.replace('Z', '+00:00'))
            if timezone.is_naive(occurred_at):
                occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())
        except (TypeError, ValueError):
            return JsonResponse({'error': 'invalid occurred_at'}, status=400)
    if entry_date is None:
        entry_date = timezone.localdate(occurred_at)

    entry_kind = body.get('entry_kind', 'moment')
    if entry_kind not in dict(JournalEntry.ENTRY_KIND_CHOICES):
        return JsonResponse({'error': 'invalid entry_kind'}, status=400)

    mood = body.get('mood') if isinstance(body.get('mood'), str) else (entry.mood if entry else '')
    if entry:
        entry.text = text
        entry.entry_date = entry_date
        entry.occurred_at = occurred_at
        entry.entry_kind = entry_kind
        entry.tags = raw_tags
        entry.mood = mood[:100]
        entry.ai_analyzed = False
        entry.save(update_fields=['text', 'entry_date', 'occurred_at', 'entry_kind', 'tags', 'mood', 'ai_analyzed'])
    else:
        entry = JournalEntry.objects.create(
            text=text, entry_date=entry_date, occurred_at=occurred_at, entry_kind=entry_kind, tags=raw_tags,
            mood=mood[:100], ai_analyzed=False, owner=request.user,
        )

    image_ids = body.get('image_ids')
    image_ids = [
        image_id for image_id in image_ids
        if isinstance(image_id, int) and not isinstance(image_id, bool)
    ] if isinstance(image_ids, list) else []
    if image_ids:
        JournalImage.objects.filter(
            id__in=image_ids, owner=request.user, entry__isnull=True,
        ).update(entry=entry)

    _extract_profile_media_from_journal(entry)
    from .memory_pipeline import capture_text
    suggestions = capture_text(request.user, entry.text, 'journal', entry.id)

    followups_created = 0
    try:
        from .grounded_insights import future_intents_to_followups
        followups_created = future_intents_to_followups(request.user, entry)
    except Exception:
        pass

    return JsonResponse({'id': entry.id, 'message': 'ذخیره شد',
                         'updated': bool(entry_id),
                         'entry_date_fa': jalali_input_value(entry.entry_date),
                         'suggestions_created': len(suggestions),
                         'followups_created': followups_created,
                         'occurred_at': entry.occurred_at.isoformat()})


@login_required
@require_http_methods(['DELETE'])
def journal_entry_delete_api(request, pk):
    entry = JournalEntry.objects.filter(pk=pk, owner=request.user).first()
    if not entry:
        return JsonResponse({'error': 'journal entry not found'}, status=404)
    entry.delete()
    return JsonResponse({'ok': True, 'id': pk})


@login_required
def journal_calendar_api(request):
    """Return entries grouped by date for a given year/month."""
    from datetime import date as _date
    try:
        year  = int(request.GET.get('year',  _date.today().year))
        month = int(request.GET.get('month', _date.today().month))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid params'}, status=400)

    qs = JournalEntry.objects.filter(
        owner=request.user,
        entry_date__year=year, entry_date__month=month,
    ).prefetch_related('images').order_by('entry_date', 'occurred_at', 'created_at')

    cal = {}
    for e in qs:
        key = str(e.entry_date)
        if key not in cal:
            cal[key] = []
        first_img = e.images.first()
        cal[key].append({
            'id':       e.id,
            'entry_date_fa': jalali_input_value(e.entry_date),
            'preview':  e.text[:80],
            'tags':     e.tags or [],
            'mood':     e.mood,
            'kind':     e.entry_kind,
            'occurred_at': e.occurred_at.isoformat() if e.occurred_at else None,
            'analyzed': e.ai_analyzed,
            'image':    first_img.image.url if first_img else None,
        })

    return JsonResponse({'year': year, 'month': month, 'entries': cal})


@login_required
def journal_entries_api(request):
    """Return filtered journal entries (up to 60)."""
    qs = JournalEntry.objects.filter(
        owner=request.user,
    ).prefetch_related(
        'images', 'mentioned_nodes'
    ).order_by('-entry_date', '-occurred_at', '-created_at')

    q       = request.GET.get('q', '').strip()
    tag     = request.GET.get('tag', '').strip()
    person  = request.GET.get('person', '').strip()
    mood    = request.GET.get('mood', '').strip()
    d_from  = request.GET.get('from', '').strip()
    d_to    = request.GET.get('to', '').strip()
    has_img = request.GET.get('has_image', '')

    if q:
        normalized = q.replace('ي', 'ی').replace('ك', 'ک')
        variants = {q, normalized, normalized.replace('ی', 'ي'), normalized.replace('ک', 'ك'),
                    normalized.replace('ی', 'ي').replace('ک', 'ك')}
        text_filter = Q()
        for term in variants:
            text_filter |= Q(text__icontains=term)
        qs = qs.filter(text_filter)
    if tag:
        qs = qs.filter(tags__contains=[tag])
    if person:
        qs = qs.filter(mentioned_nodes__username=person)
    if mood:
        qs = qs.filter(mood__icontains=mood)
    if d_from:
        try:
            d_from_value = parse_date_input(d_from)
        except (TypeError, ValueError, OverflowError):
            return JsonResponse({'error': 'فرمت تاریخ شروع: ۱۴۰۴/۰۱/۰۱'}, status=400)
        qs = qs.filter(entry_date__gte=d_from_value)
    if d_to:
        try:
            d_to_value = parse_date_input(d_to)
        except (TypeError, ValueError, OverflowError):
            return JsonResponse({'error': 'فرمت تاریخ پایان: ۱۴۰۴/۱۲/۲۹'}, status=400)
        qs = qs.filter(entry_date__lte=d_to_value)
    if has_img == '1':
        qs = qs.filter(images__isnull=False).distinct()

    total = qs.count()
    entries = []
    for e in qs[:60]:
        first_img = e.images.first()
        entries.append({
            'id':         e.id,
            'text':       e.text,
            'preview':    e.text[:100],
            'entry_date': str(e.entry_date) if e.entry_date else None,
            'entry_date_fa': jalali_input_value(e.entry_date) if e.entry_date else None,
            'tags':       e.tags or [],
            'mood':       e.mood,
            'kind':       e.entry_kind,
            'analyzed':   e.ai_analyzed,
            'created_at': e.created_at.strftime('%Y/%m/%d'),
            'created_at_fa': jalali_input_value(timezone.localtime(e.created_at).date()),
            'occurred_at': timezone.localtime(e.occurred_at).strftime('%H:%M') if e.occurred_at else '',
            'occurred_at_iso': timezone.localtime(e.occurred_at).isoformat() if e.occurred_at else '',
            'image':      first_img.image.url if first_img else None,
            'mentioned':  [n.username for n in e.mentioned_nodes.all()[:5]],
        })
    return JsonResponse({'entries': entries, 'total': total})
