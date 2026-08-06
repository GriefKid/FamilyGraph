from datetime import date, datetime

from django import template
from django.utils import timezone

from main.utils_jalali import jalali_full_str, jalali_str, to_jalali


register = template.Library()


_PERSIAN_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def _fa_number(value):
    return str(value).translate(_PERSIAN_DIGITS)


@register.filter
def jalali_date(value, style='long'):
    """Render dates in Jalali; datetimes are first converted to Tehran time."""
    if not value:
        return ''
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        value_date = value.date()
        time_value = value.strftime('%H:%M')
    elif isinstance(value, date):
        value_date = value
        time_value = ''
    else:
        return value

    if style == 'compact':
        jd = to_jalali(value_date)
        rendered = f'{jd.year}/{jd.month:02d}/{jd.day:02d}' if jd else value_date.strftime('%Y/%m/%d')
    elif style == 'full':
        rendered = jalali_full_str(value_date)
    else:
        rendered = jalali_str(value_date)

    if style in ('datetime', 'full_datetime') and time_value:
        rendered = f'{rendered} · {time_value}'
    return _fa_number(rendered)
