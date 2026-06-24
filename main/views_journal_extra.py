"""
Extra journal views — imported in urls.py alongside main views.
"""
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import JournalEntry, JournalImage


@csrf_exempt
def journal_save_api(request):
    """Save a journal entry without AI analysis."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    text = body.get('text', '').strip()
    if not text:
        return JsonResponse({'error': 'متن خالی است'}, status=400)

    from datetime import date as _date
    entry_date_str = body.get('entry_date', '').strip()
    entry_date = None
    if entry_date_str:
        try:
            entry_date = _date.fromisoformat(entry_date_str)
        except Exception:
            pass

    raw_tags = body.get('tags', [])
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(',') if t.strip()]

    entry = JournalEntry.objects.create(
        text=text, entry_date=entry_date, tags=raw_tags, ai_analyzed=False
    )

    image_ids = body.get('image_ids', [])
    if image_ids:
        JournalImage.objects.filter(id__in=image_ids, entry__isnull=True).update(entry=entry)

    return JsonResponse({'id': entry.id, 'message': 'ذخیره شد'})


def journal_calendar_api(request):
    """Return entries grouped by date for a given year/month."""
    from datetime import date as _date
    try:
        year  = int(request.GET.get('year',  _date.today().year))
        month = int(request.GET.get('month', _date.today().month))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid params'}, status=400)

    qs = JournalEntry.objects.filter(
        entry_date__year=year, entry_date__month=month
    ).prefetch_related('images').order_by('entry_date', 'created_at')

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
            'analyzed': e.ai_analyzed,
            'image':    first_img.image.url if first_img else None,
        })

    return JsonResponse({'year': year, 'month': month, 'entries': cal})


def journal_entries_api(request):
    """Return filtered journal entries (up to 60)."""
    qs = JournalEntry.objects.prefetch_related(
        'images', 'mentioned_nodes'
    ).order_by('-entry_date', '-created_at')

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
            'analyzed':   e.ai_analyzed,
            'created_at': e.created_at.strftime('%Y/%m/%d'),
            'image':      first_img.image.url if first_img else None,
            'mentioned':  [n.username for n in e.mentioned_nodes.all()[:5]],
        })
    return JsonResponse({'entries': entries})
