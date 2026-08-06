"""Conservative shared extraction; suggestions are never applied automatically."""
import re

from .models import ExtractionSuggestion


def extract_text(owner, text, source, source_id=None):
    text = (text or '').strip()
    suggestions = []
    money = r'[۰-۹0-9][۰-۹0-9,٬]*'
    word_amounts = {'یک': 1, 'دو': 2, 'سه': 3, 'چهار': 4, 'پنج': 5, 'شش': 6, 'هفت': 7, 'هشت': 8, 'نه': 9, 'ده': 10, 'بیست': 20, 'پنجاه': 50, 'صد': 100, 'دویست': 200, 'سیصد': 300, 'چهارصد': 400, 'پانصد': 500}
    # Supports both «قرض ۳۰۰هزار» and «علی ۳۰۰هزار قرض گرفت».
    debt_patterns = (
        rf'(?P<person>[\wآ-ی]+).{{0,20}}?(?P<amount>{money}).{{0,30}}?(?P<verb>قرض گرفت|گرفت قرض|بدهکار شد|پس می‌دهد)',
        rf'(?P<verb>قرض دادم|قرض گرفت|بدهکار|طلبکار).{{0,50}}?(?P<amount>{money})',
    )
    for pattern in debt_patterns:
        for match in re.finditer(pattern, text):
            snippet = match.group(0)
            verb = match.groupdict().get('verb', '')
            direction = 'they_owe' if ('از من' in snippet or 'قرض گرفت' in verb or 'بدهکار' in verb) else 'i_owe'
            suggestions.append(('debt', {
                'snippet': snippet, 'amount_raw': match.group('amount'),
                'person_raw': match.groupdict().get('person', ''), 'direction': direction,
            }))
            person = match.groupdict().get('person', '')
            if person:
                suggestions.append(('person', {'name_raw': person, 'reason': 'در یک پیشنهاد مالی ذکر شده'}))
    for word, value in word_amounts.items():
        if re.search(rf'([\wآ-ی]+).{{0,20}}?{word}\s+هزار.{{0,30}}?قرض گرفت', text):
            match = re.search(rf'([\wآ-ی]+).{{0,20}}?{word}\s+هزار.{{0,30}}?قرض گرفت', text)
            person = match.group(1)
            suggestions.append(('debt', {'snippet': match.group(0), 'amount_raw': f'{word} هزار', 'amount_value': value * 1000, 'person_raw': person, 'direction': 'they_owe'}))
            suggestions.append(('person', {'name_raw': person, 'reason': 'در یک پیشنهاد مالی ذکر شده'}))
    for match in re.finditer(r'(?:قرار|جلسه|تولد|عروسی|امتحان|بیماری|سفر).{0,80}', text):
        suggestions.append(('event', {'snippet': match.group(0)}))
    for match in re.finditer(r'@([\w.-]{2,30})', text):
        suggestions.append(('person', {'username': match.group(1)}))
    for word in ('تنها', 'استرس', 'خوشحال', 'دلخور', 'اعتماد', 'حمایت'):
        if word in text:
            suggestions.append(('signal', {'signal': word}))
    return [ExtractionSuggestion.objects.create(owner=owner, source=source, source_id=source_id, kind=kind, payload=payload) for kind, payload in suggestions]
