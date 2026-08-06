"""Small deterministic parser for common Persian/Jalali date expressions."""
import re
from datetime import datetime, time, timedelta

from django.utils import timezone


DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
MONTHS = {'فروردین': 1, 'اردیبهشت': 2, 'خرداد': 3, 'تیر': 4, 'مرداد': 5,
          'شهریور': 6, 'مهر': 7, 'آبان': 8, 'آذر': 9, 'دی': 10, 'بهمن': 11, 'اسفند': 12}
WEEKDAYS = {'دوشنبه': 0, 'سه شنبه': 1, 'سه‌شنبه': 1, 'چهارشنبه': 2,
            'پنجشنبه': 3, 'پنج‌شنبه': 3, 'جمعه': 4, 'شنبه': 5, 'یکشنبه': 6, 'یک‌شنبه': 6}
WORD_NUMBERS = {'یک': 1, 'دو': 2, 'سه': 3, 'چهار': 4, 'پنج': 5, 'شش': 6,
                'هفت': 7, 'هشت': 8, 'نه': 9, 'ده': 10}


def parse_persian_datetime(text, base_date=None):
    text = ' '.join((text or '').translate(DIGITS).split())
    base = base_date or timezone.localdate()
    result, matched = None, ''
    if 'پس فردا' in text:
        result, matched = base + timedelta(days=2), 'پس فردا'
    elif 'فردا' in text:
        result, matched = base + timedelta(days=1), 'فردا'
    elif 'امروز' in text:
        result, matched = base, 'امروز'
    elif 'دیروز' in text:
        result, matched = base - timedelta(days=1), 'دیروز'
    else:
        number = r'(\d+|' + '|'.join(WORD_NUMBERS) + r')'
        weeks = re.search(number + r'\s*هفته\s*(?:دیگه|دیگر|بعد)', text)
        days = re.search(number + r'\s*روز\s*(?:دیگه|دیگر|بعد)', text)
        if weeks:
            value = int(weeks.group(1)) if weeks.group(1).isdigit() else WORD_NUMBERS[weeks.group(1)]
            result, matched = base + timedelta(weeks=value), weeks.group(0)
        elif days:
            value = int(days.group(1)) if days.group(1).isdigit() else WORD_NUMBERS[days.group(1)]
            result, matched = base + timedelta(days=value), days.group(0)
        else:
            for label, weekday in WEEKDAYS.items():
                if label in text:
                    delta = (weekday - base.weekday()) % 7 or 7
                    result, matched = base + timedelta(days=delta), label
                    break
    jalali = re.search(r'(?:(\d{4})[/-])?(\d{1,2})[/-](\d{1,2})', text)
    named = re.search(r'(\d{1,2})\s*(' + '|'.join(MONTHS) + r')(?:\s*(\d{4}))?', text)
    try:
        import jdatetime
        if named:
            today_j = jdatetime.date.fromgregorian(date=base)
            jy = int(named.group(3) or today_j.year)
            result = jdatetime.date(jy, MONTHS[named.group(2)], int(named.group(1))).togregorian()
            matched = named.group(0)
        elif jalali and (jalali.group(1) or int(jalali.group(2)) > 12):
            today_j = jdatetime.date.fromgregorian(date=base)
            jy = int(jalali.group(1) or today_j.year)
            result = jdatetime.date(jy, int(jalali.group(2)), int(jalali.group(3))).togregorian()
            matched = jalali.group(0)
    except (ImportError, ValueError):
        pass
    clock = re.search(r'ساعت\s*(\d{1,2})(?::(\d{2}))?', text)
    parsed_time = None
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            parsed_time = time(hour, minute)
    return {'date': result, 'time': parsed_time, 'matched': matched}
