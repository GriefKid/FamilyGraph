"""Conservative, explainable extraction. Nothing is applied without approval."""
import hashlib
import json
import re

from django.db import models

from .models import ExtractionSuggestion, Node


PERSIAN_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
NUMBER_WORDS = {
    'یک': 1, 'دو': 2, 'سه': 3, 'چهار': 4, 'پنج': 5, 'شش': 6, 'هفت': 7,
    'هشت': 8, 'نه': 9, 'ده': 10, 'بیست': 20, 'سی': 30, 'چهل': 40,
    'پنجاه': 50, 'صد': 100, 'دویست': 200, 'سیصد': 300,
    'چهارصد': 400, 'پانصد': 500, 'یک میلیون': 1_000_000,
}


def _normalise(text):
    return re.sub(r'\s+', ' ', (text or '').translate(PERSIAN_DIGITS).replace('ي', 'ی').replace('ك', 'ک')).strip()


def _amount(raw):
    clean = raw.replace(',', '').replace('٬', '').replace('تومان', '').replace('تومن', '').strip()
    if clean.isdigit():
        return int(clean)
    multiplier = 1_000_000 if 'میلیون' in clean else (1_000 if 'هزار' in clean else 1)
    words = clean.replace('میلیون', '').replace('هزار', '').split()
    if words and words[0].isdigit():
        return int(words[0]) * multiplier
    return sum(NUMBER_WORDS.get(word, 0) for word in words if word != 'و') * multiplier or None


def _fingerprint(kind, payload):
    stable = json.dumps({'kind': kind, 'payload': payload}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()


def _candidate_person(owner, raw):
    raw = raw.strip(' ،,.!؟')
    stop = {'من', 'امروز', 'دیروز', 'فردا', 'اون', 'او', 'ایشون'}
    if not raw or raw in stop:
        return None
    existing = Node.objects.filter(owner=owner).filter(
        models.Q(username__iexact=raw) | models.Q(name__iexact=raw) |
        models.Q(first_name__iexact=raw) | models.Q(nickname__iexact=raw)
    ).first()
    return {'name_raw': raw, 'existing_node_id': existing.id if existing else None}


def _collect(owner, text):
    normalized = _normalise(text)
    found = []
    names = {}
    amount_token = r'(?:[0-9][0-9,٬]*\s*(?:هزار|میلیون)?|(?:یک|دو|سه|چهار|پنج|شش|هفت|هشت|نه|ده|بیست|سی|چهل|پنجاه|صد|دویست|سیصد|چهارصد|پانصد)(?:\s+و\s+(?:ده|بیست|سی|چهل|پنجاه))?\s*(?:هزار|میلیون)?)'
    patterns = [
        (rf'(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{{1,30}})\s+ازم\s+(?P<amount>{amount_token})\s*(?:تومان|تومن)?\s*(?:قرض\s+گرفت|گرفت)', 'they_owe'),
        (rf'به\s+(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{{1,30}})\s+(?P<amount>{amount_token})\s*(?:تومان|تومن)?\s*(?:قرض\s+دادم|دادم)', 'they_owe'),
        (rf'از\s+(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{{1,30}})\s+(?P<amount>{amount_token})\s*(?:تومان|تومن)?\s*(?:قرض\s+گرفتم|گرفتم)', 'i_owe'),
        (rf'(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{{1,30}})\s+(?P<amount>{amount_token})\s*(?:تومان|تومن)?\s+ازم\s+طلب(?:کار)?(?:ه|\s+داره)', 'i_owe'),
    ]
    for pattern, direction in patterns:
        for match in re.finditer(pattern, normalized, re.I):
            person = _candidate_person(owner, match.group('person'))
            value = _amount(match.group('amount'))
            if not person or not value:
                continue
            names[person['name_raw']] = person
            found.append(('debt', {
                'snippet': match.group(0), 'person_raw': person['name_raw'],
                'node_id': person['existing_node_id'], 'amount_raw': match.group('amount'),
                'amount_value': value, 'direction': direction,
                'explanation': 'عبارت مالی، نام شخص و جهت قرض در متن دیده شد.',
            }))

    for match in re.finditer(r'(?:قرار|جلسه|تولد|عروسی|امتحان|سفر|مهمانی)\s+[^.!؟\n]{0,90}', normalized):
        found.append(('event', {'snippet': match.group(0), 'title': match.group(0)[:100],
                                'explanation': 'یک عبارت زمان‌مند یا رویداد در متن دیده شد.'}))
    for match in re.finditer(r'@([\w.-]{2,30})', normalized):
        names.setdefault(match.group(1), {'name_raw': match.group(1), 'username': match.group(1), 'existing_node_id': None})
    signals = {
        'تنها': 'تنهایی', 'استرس': 'استرس', 'خوشحال': 'خلق مثبت', 'دلخور': 'دلخوری',
        'اعتماد': 'اعتماد', 'حمایت': 'حمایت', 'دعوا': 'تعارض', 'آرامش': 'آرامش',
    }
    for word, label in signals.items():
        if word in normalized:
            found.append(('signal', {'signal': label, 'snippet': word,
                                     'explanation': f'واژهٔ «{word}» در متن ثبت‌شده وجود داشت.'}))
    for person in names.values():
        if not person.get('existing_node_id'):
            found.append(('person', {**person, 'explanation': 'این نام در متن آمده اما هنوز در گراف پیدا نشد.'}))
    return found


def extract_text(owner, text, source, source_id=None):
    if not owner or not owner.ai_extraction_enabled:
        return []
    source_flags = {'journal': owner.ai_journal_enabled, 'checkin': owner.ai_checkin_enabled,
                    'chat': owner.ai_chat_enabled}
    if source in source_flags and not source_flags[source]:
        return []
    created = []
    for kind, payload in _collect(owner, text):
        fingerprint = _fingerprint(kind, payload)
        row, was_created = ExtractionSuggestion.objects.get_or_create(
            owner=owner, source=source, source_id=source_id, fingerprint=fingerprint,
            defaults={'kind': kind, 'payload': payload},
        )
        if was_created:
            created.append(row)
    return created
