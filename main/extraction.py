"""Conservative, explainable extraction. Nothing is applied without approval."""
import hashlib
import json
import re
import os
import time

from django.db import models

from .models import AIExtractionTrace, ExtractionSuggestion, FeatureFlag, Node, NodeAlias
from .persian_datetime import parse_persian_datetime


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
    exact = Node.objects.filter(owner=owner).filter(
        models.Q(username__iexact=raw) | models.Q(name__iexact=raw) |
        models.Q(first_name__iexact=raw) | models.Q(nickname__iexact=raw)
    ).first()
    alias_node = NodeAlias.objects.filter(owner=owner, normalized_alias=_normalise(raw).lower()).select_related('node').first()
    exact = exact or (alias_node.node if alias_node else None)
    candidates = list(Node.objects.filter(owner=owner).filter(
        models.Q(username__icontains=raw) | models.Q(name__icontains=raw) |
        models.Q(first_name__icontains=raw) | models.Q(nickname__icontains=raw)
    ).values('id', 'username', 'name', 'first_name', 'nickname')[:5])
    return {'name_raw': raw, 'existing_node_id': exact.id if exact else None,
            'candidate_node_ids': [row['id'] for row in candidates]}


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

    for match in re.finditer(r'[^.!؟\n]{0,35}(?:قرار|جلسه|تولد|عروسی|امتحان|سفر|مهمانی)[^.!؟\n]{0,90}', normalized):
        parsed = parse_persian_datetime(match.group(0))
        found.append(('event', {'snippet': match.group(0), 'title': match.group(0)[:100],
                                'date': parsed['date'].isoformat() if parsed['date'] else '',
                                'time': parsed['time'].isoformat(timespec='minutes') if parsed['time'] else '',
                                'date_expression': parsed['matched'],
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

    # Identity and relationship changes. The user chooses the target person before apply.
    relation_patterns = [
        (r'(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+(?:همکار جدیدم(?:ه| است)|همکارمه)', 'همکار', 'active', 3),
        (r'(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+(?:دوست جدیدم(?:ه| است)|دوستمه)', 'دوست', 'active', 3),
        (r'با\s+(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+قهر', '', 'distant', 1),
        (r'(?:دیگه|دیگر)\s+با\s+(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+(?:در ارتباط نیستم|حرف نمی.?زنم)', '', 'inactive', 1),
        (r'(?:رابطه(?:مون|‌مون)\s+با\s+)?(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+بهتر شده', '', 'active', 4),
    ]
    for pattern, rel_type, status, strength in relation_patterns:
        for match in re.finditer(pattern, normalized):
            person = _candidate_person(owner, match.group('person'))
            if not person:
                continue
            names[person['name_raw']] = person
            found.append(('relationship', {**person, 'relationship_type': rel_type,
                          'status': status, 'strength': strength, 'snippet': match.group(0),
                          'explanation': 'عبارت متن، نوع یا وضعیت رابطه را توصیف می‌کند.'}))

    # Useful personal facts with explicit subjects; conservative by design.
    fact_patterns = [
        (r'(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+(?:عاشق|خیلی دوست داره)\s+(?P<value>[^.!؟]{2,60})', 'interest'),
        (r'(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+از\s+(?P<value>[^.!؟]{2,60})\s+(?:بدش میاد|متنفره)', 'sensitivity'),
        (r'برای\s+(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+(?P<value>صداقت|احترام|خانواده|آزادی|رشد)\s+مهمه', 'value'),
        (r'(?P<person>[آ-یA-Za-z][آ-یA-Za-z‌_-]{1,30})\s+ترجیح میده\s+(?P<value>[^.!؟]{2,80})', 'preference'),
    ]
    for pattern, category in fact_patterns:
        for match in re.finditer(pattern, normalized):
            person = _candidate_person(owner, match.group('person'))
            if not person:
                continue
            names[person['name_raw']] = person
            found.append(('memory', {**person, 'category': category,
                          'value': match.group('value').strip(), 'confidence': 80,
                          'snippet': match.group(0),
                          'explanation': 'یک گزارهٔ مستقیم دربارهٔ این فرد در متن دیده شد.'}))
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
    started = time.monotonic()
    regex_items = _collect(owner, text)
    ai_items, provider, model_name, trace_status, trace_error = [], '', '', 'regex_only', ''
    hybrid_flag = FeatureFlag.objects.filter(name='hybrid-ai').first()
    hybrid_enabled = os.environ.get('AI_HYBRID_ENABLED', '0') == '1' or bool(hybrid_flag and hybrid_flag.is_enabled_for(owner))
    if hybrid_enabled and text.strip():
        try:
            from .views_smart_features import _ai_client, _extract_json, _model
            client, configured, provider = _ai_client()
            model_name = _model()
            if client and configured:
                prompt = ('فقط JSON بده. از متن فارسی، موارد قطعی را استخراج کن. '
                          'خروجی: {"suggestions":[{"kind":"event|debt|person|relationship|memory|commitment",'
                          '"payload":{...},"confidence":0-100}]}. تشخیص پزشکی و حدس هویتی ممنوع.\nمتن: ' + text[:4000])
                response = client.chat.completions.create(
                    model=model_name, messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.1, max_tokens=900)
                parsed = _extract_json(response.choices[0].message.content)
                for row in parsed.get('suggestions', [])[:20]:
                    if row.get('kind') in {'event','debt','person','relationship','memory','commitment'} and isinstance(row.get('payload'), dict):
                        payload = {**row['payload'], 'ai_confidence': min(100, max(0, int(row.get('confidence', 60)))),
                                   'explanation': row['payload'].get('explanation', 'این پیشنهاد توسط موتور AI استخراج شد.')}
                        ai_items.append((row['kind'], payload))
                trace_status = 'hybrid'
        except Exception as exc:
            trace_status, trace_error = 'ai_failed', type(exc).__name__
    combined, seen = [], set()
    for kind, payload in [*regex_items, *ai_items]:
        marker = _fingerprint(kind, payload)
        if marker not in seen:
            seen.add(marker); combined.append((kind, payload))
    created = []
    for kind, payload in combined:
        fingerprint = _fingerprint(kind, payload)
        row, was_created = ExtractionSuggestion.objects.get_or_create(
            owner=owner, source=source, source_id=source_id, fingerprint=fingerprint,
            defaults={'kind': kind, 'payload': payload},
        )
        if was_created:
            created.append(row)
    try:
        AIExtractionTrace.objects.create(
            owner=owner, source=source, source_id=source_id, input_text=text[:4000],
            regex_output=[{'kind': k, 'payload': p} for k, p in regex_items],
            ai_output=[{'kind': k, 'payload': p} for k, p in ai_items],
            merged_output=[{'kind': k, 'payload': p} for k, p in combined],
            provider=provider, model_name=model_name,
            duration_ms=round((time.monotonic() - started) * 1000), status=trace_status,
            error_code=trace_error)
    except Exception:
        pass
    return created
