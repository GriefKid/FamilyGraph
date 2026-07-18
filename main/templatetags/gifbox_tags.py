from django import template

register = template.Library()


@register.filter
def trust_color(score):
    """رنگ امتیاز اعتماد بر اساس مقدار"""
    try:
        score = int(score)
    except (TypeError, ValueError):
        return '#6366f1'
    if score >= 80:
        return '#43e97b'
    if score >= 60:
        return '#feca57'
    if score >= 40:
        return '#fd9644'
    return '#ff4757'


@register.filter
def quota(score):
    """سهمیه شیر روزانه بر اساس امتیاز اعتماد"""
    try:
        score = int(score)
    except (TypeError, ValueError):
        return '—'
    if score >= 90:
        return 'نامحدود'
    if score >= 75:
        return '۵/روز'
    if score >= 60:
        return '۳/روز'
    if score >= 45:
        return '۲/روز'
    if score >= 30:
        return '۱/روز'
    return 'مسدود 🚫'
