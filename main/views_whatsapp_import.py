import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Node, Relationship


MAX_SIZE = 100 * 1024 * 1024
SCAN_TTL = 2 * 3600
IMPORT_NOTE = 'ایمپورت واتساپ'

LINE_PATTERNS = (
    re.compile(r'^\[(?P<date>\d{1,4}[/-]\d{1,2}[/-]\d{1,4}),\s*[^\]]+\]\s*(?P<name>[^:]{1,120}):\s*(?P<text>.*)$'),
    re.compile(r'^(?P<date>\d{1,4}[/-]\d{1,2}[/-]\d{1,4}),\s*.+?\s+-\s+(?P<name>[^:]{1,120}):\s*(?P<text>.*)$'),
)


def _normalize(value):
    return re.sub(r'\s+', ' ', str(value or '').strip().lower().replace('\u200c', ' '))


def _read_export(upload):
    raw = upload.read()
    if upload.name.lower().endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            candidates = [name for name in archive.namelist() if name.lower().endswith('.txt')]
            if not candidates:
                raise ValueError('داخل فایل ZIP هیچ گفتگوی متنی پیدا نشد')
            if archive.getinfo(candidates[0]).file_size > MAX_SIZE:
                raise ValueError('حجم متن داخل ZIP بیشتر از ۱۰۰ مگابایت است')
            raw = archive.read(candidates[0])
    for encoding in ('utf-8-sig', 'utf-8', 'utf-16'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError('رمزگذاری فایل قابل خواندن نیست')


def _parse_date(value):
    part = value.replace('-', '/').strip()
    pieces = part.split('/')
    if len(pieces) != 3:
        return None
    candidates = ('%Y/%m/%d', '%d/%m/%Y', '%m/%d/%Y', '%d/%m/%y', '%m/%d/%y')
    for pattern in candidates:
        try:
            result = datetime.strptime(part, pattern).date()
            if result <= timezone.localdate():
                return result
        except ValueError:
            continue
    return None


def parse_whatsapp_export(text):
    contacts = defaultdict(lambda: {'messages': 0, 'dates': set()})
    for line in text.splitlines():
        match = next((match for pattern in LINE_PATTERNS
                      if (match := pattern.match(line.strip()))), None)
        if not match:
            continue
        name = match.group('name').strip()
        day = _parse_date(match.group('date'))
        if not name or not day:
            continue
        row = contacts[name]
        row['messages'] += 1
        row['dates'].add(day.isoformat())
    return contacts


@login_required
def whatsapp_import_view(request):
    nodes = Node.objects.filter(owner=request.user).order_by('username')
    return render(request, 'import/whatsapp.html', {'nodes': nodes})


@login_required
@require_POST
def whatsapp_scan_api(request):
    upload = request.FILES.get('file')
    if not upload:
        return JsonResponse({'error': 'فایل TXT یا ZIP خروجی واتساپ را انتخاب کن'}, status=400)
    if upload.size > MAX_SIZE:
        return JsonResponse({'error': 'حجم فایل نباید بیشتر از ۱۰۰ مگابایت باشد'}, status=400)
    try:
        parsed = parse_whatsapp_export(_read_export(upload))
    except (ValueError, zipfile.BadZipFile) as exc:
        return JsonResponse({'error': str(exc) or 'فایل واتساپ معتبر نیست'}, status=400)
    if not parsed:
        return JsonResponse({'error': 'هیچ پیام قابل تشخیصی در فایل پیدا نشد'}, status=400)

    lookup = {}
    for node in Node.objects.filter(owner=request.user):
        for label in (node.username, node.name, node.nickname,
                      f'{node.first_name} {node.last_name}'.strip()):
            if _normalize(label):
                lookup.setdefault(_normalize(label), node)

    cache_payload, contacts = {}, []
    for name, info in parsed.items():
        suggested = lookup.get(_normalize(name))
        cache_payload[name] = {
            'messages': info['messages'], 'dates': sorted(info['dates']),
        }
        contacts.append({
            'name': name, 'messages': info['messages'], 'active_days': len(info['dates']),
            'suggested': ({'id': suggested.id, 'label': suggested.display_name()}
                          if suggested else None),
        })
    contacts.sort(key=lambda item: -item['messages'])
    cache.set(f'wa_scan_{request.user.id}', cache_payload, SCAN_TTL)
    return JsonResponse({'ok': True, 'contacts': contacts, 'total': len(contacts)})


@login_required
@require_POST
def whatsapp_apply_api(request):
    try:
        body = json.loads(request.body)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'درخواست نامعتبر است'}, status=400)
    scanned = cache.get(f'wa_scan_{request.user.id}')
    if not scanned:
        return JsonResponse({'error': 'پیش‌نمایش منقضی شده؛ فایل را دوباره اسکن کن'}, status=410)

    root = request.user.root_node
    stats = {'contacts': 0, 'nodes_created': 0, 'interactions': 0, 'skipped': 0}
    with transaction.atomic():
        for choice in body.get('mapping') or []:
            name = str(choice.get('name') or '').strip()
            action = str(choice.get('action') or 'skip')
            info = scanned.get(name)
            if not info or action == 'skip':
                stats['skipped'] += 1
                continue
            node = None
            if action == 'new':
                node, created = Node.objects.get_or_create(
                    owner=request.user, username=name[:100], defaults={'name': name[:200]})
                stats['nodes_created'] += int(created)
            elif action.startswith('node:'):
                try:
                    node = Node.objects.get(owner=request.user, pk=int(action.split(':', 1)[1]))
                except (Node.DoesNotExist, TypeError, ValueError):
                    continue
            if not node or (root and node.pk == root.pk):
                continue
            stats['contacts'] += 1
            try:
                from .models import Interaction
                existing = set(Interaction.objects.filter(
                    owner=request.user, node=node, kind='message', note=IMPORT_NOTE,
                ).values_list('date', flat=True))
                rows = [Interaction(owner=request.user, node=node, kind='message',
                                    date=datetime.strptime(day, '%Y-%m-%d').date(),
                                    feeling=0, note=IMPORT_NOTE)
                        for day in info['dates']
                        if datetime.strptime(day, '%Y-%m-%d').date() not in existing]
                Interaction.objects.bulk_create(rows)
                stats['interactions'] += len(rows)
            except (ImportError, ValueError):
                pass
            if body.get('make_edges', True) and root:
                already = Relationship.objects.filter(owner=request.user).filter(
                    Q(source=root, target=node) | Q(source=node, target=root)
                ).exists()
                if not already:
                    Relationship.objects.create(owner=request.user, source=root, target=node,
                                                rel='واتساپ', strength=2)
    cache.delete(f'wa_scan_{request.user.id}')
    return JsonResponse({'ok': True, 'stats': stats})
