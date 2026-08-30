"""Presentation helpers for people/nodes: initials and a stable avatar colour."""
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _display_name(node):
    if node is None:
        return ''
    getter = getattr(node, 'display_name', None)
    if callable(getter):
        try:
            return getter() or ''
        except Exception:
            return ''
    return str(node)


@register.filter
def person_initials(node):
    """Up to two initials from a person's display name (works for Persian too)."""
    name = _display_name(node).strip()
    if not name:
        return '؟'
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[-1][:1]).upper()
    return parts[0][:2].upper() if parts else '؟'


@register.filter
def avatar_style(value):
    """Deterministic, readable HSL background for an initials avatar.

    Keyed off any stable string (username or id) so the same person always
    gets the same colour and the directory becomes visually scannable.
    """
    key = str(value or '')
    h = 0
    for ch in key:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hue = h % 360
    return mark_safe(
        f'background:hsl({hue} 62% 46%);color:#fff;'
    )
