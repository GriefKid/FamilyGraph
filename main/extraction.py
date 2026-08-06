"""Conservative shared extraction; suggestions are never applied automatically."""
import re

from .models import ExtractionSuggestion


def extract_text(owner, text, source, source_id=None):
    text = (text or '').strip()
    suggestions = []
    for match in re.finditer(r'(?:قرض|بدهکار|طلبکار).{0,50}?([۰-۹0-9][۰-۹0-9,٬]*)', text):
        suggestions.append(('debt', {'snippet': match.group(0), 'amount_raw': match.group(1)}))
    for match in re.finditer(r'(?:قرار|جلسه|تولد|عروسی|امتحان|بیماری|سفر).{0,80}', text):
        suggestions.append(('event', {'snippet': match.group(0)}))
    for match in re.finditer(r'@([\w.-]{2,30})', text):
        suggestions.append(('person', {'username': match.group(1)}))
    for word in ('تنها', 'استرس', 'خوشحال', 'دلخور', 'اعتماد', 'حمایت'):
        if word in text:
            suggestions.append(('signal', {'signal': word}))
    return [ExtractionSuggestion.objects.create(owner=owner, source=source, source_id=source_id, kind=kind, payload=payload) for kind, payload in suggestions]
