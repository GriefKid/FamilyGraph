"""
Extra journal views — imported in urls.py alongside main views.
"""
import json
from datetime import datetime, timedelta
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.utils import timezone
from .models import ArtisticWork, JournalEntry, JournalImage, ProfileMediaItem


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

    bucket = int(timezone.now().timestamp() // 3600)
    rate_key = f'anti-spam:journal-moment:{request.user.pk}:{bucket}'
    moment_count = 1 if cache.add(rate_key, 1, timeout=3600) else cache.incr(rate_key)
    if moment_count > 30:
        return JsonResponse({'error': 'تعداد ثبت لحظه در این ساعت زیاد است؛ کمی بعد ادامه بده.', 'retry_after': 3600}, status=429)
    normalized = ' '.join(text.lower().split())
    recent_entries = JournalEntry.objects.filter(
        owner=request.user, created_at__gte=timezone.now() - timedelta(minutes=5)
    ).only('text')
    if any(' '.join(entry.text.lower().split()) == normalized for entry in recent_entries):
        return JsonResponse({'error': 'همین لحظه را همین چند دقیقه پیش ثبت کرده‌ای.'}, status=400)

    from datetime import date as _date
    entry_date_str = body.get('entry_date')
    entry_date_str = entry_date_str.strip() if isinstance(entry_date_str, str) else ''
    entry_date = None
    if entry_date_str:
        try:
            entry_date = _date.fromisoformat(entry_date_str)
        except Exception:
            pass

    raw_tags = body.get('tags', [])
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(',') if t.strip()]
    elif isinstance(raw_tags, list):
        raw_tags = [str(tag).strip()[:80] for tag in raw_tags if str(tag).strip()][:30]
    else:
        raw_tags = []

    occurred_at = timezone.now()
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

    entry = JournalEntry.objects.create(
        text=text, entry_date=entry_date, occurred_at=occurred_at, entry_kind=entry_kind, tags=raw_tags,
        ai_analyzed=False, owner=request.user,
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
    from .extraction import extract_text
    suggestions = extract_text(request.user, entry.text, 'journal', entry.id)

    return JsonResponse({'id': entry.id, 'message': 'ذخیره شد', 'suggestions_created': len(suggestions), 'occurred_at': entry.occurred_at.isoformat()})


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
        qs = qs.filter(text__icontains=q)
    if tag:
        qs = qs.filter(tags__contains=[tag])
    if person:
        qs = qs.filter(mentioned_nodes__username=person)
    if mood:
        qs = qs.filter(mood__icontains=mood)
    if d_from:
        qs = qs.filter(entry_date__gte=d_from)
    if d_to:
        qs = qs.filter(entry_date__lte=d_to)
    if has_img == '1':
        qs = qs.filter(images__isnull=False).distinct()

    entries = []
    for e in qs[:60]:
        first_img = e.images.first()
        entries.append({
            'id':         e.id,
            'text':       e.text,
            'preview':    e.text[:100],
            'entry_date': str(e.entry_date) if e.entry_date else None,
            'tags':       e.tags or [],
            'mood':       e.mood,
            'kind':       e.entry_kind,
            'analyzed':   e.ai_analyzed,
            'created_at': e.created_at.strftime('%Y/%m/%d'),
            'occurred_at': timezone.localtime(e.occurred_at).strftime('%H:%M') if e.occurred_at else '',
            'image':      first_img.image.url if first_img else None,
            'mentioned':  [n.username for n in e.mentioned_nodes.all()[:5]],
        })
    return JsonResponse({'entries': entries})
