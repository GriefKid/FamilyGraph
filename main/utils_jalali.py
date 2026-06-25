"""
ابزارهای تقویم جلالی (شمسی) برای FamilyGraph.
نیاز دارد: pip install jdatetime
"""
from __future__ import annotations
from datetime import date, timedelta


# ── نام‌های ماه و روز شمسی ───────────────────────────────────────────────────

MONTHS_FA = [
    'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
    'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند',
]

# jdatetime weekday: 0=شنبه, 1=یکشنبه, ..., 5=پنجشنبه, 6=جمعه
DAYS_FA_JD = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه']

# Python weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
DAYS_FA_PY = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']


# ── تعطیلات ثابت شمسی (ماه, روز) ────────────────────────────────────────────

FIXED_HOLIDAYS: dict[tuple[int, int], str] = {
    (1,  1):  'عید نوروز',
    (1,  2):  'عید نوروز',
    (1,  3):  'عید نوروز',
    (1,  4):  'عید نوروز',
    (1,  12): 'روز جمهوری اسلامی',
    (1,  13): 'سیزده به در',
    (3,  14): 'رحلت امام خمینی',
    (3,  15): 'قیام ۱۵ خرداد',
    (11, 22): 'پیروزی انقلاب اسلامی',
    (12, 29): 'ملی شدن صنعت نفت',
}

# تعطیلات مذهبی قمری — هر سال تغییر می‌کنند.
# این لیست برای سال ۱۴۰۳/۱۴۰۴/۱۴۰۵ شمسی (تقریبی) است.
# کلید: (سال_شمسی, ماه_شمسی, روز_شمسی)
LUNAR_HOLIDAYS_1403: list[tuple[int, int, str]] = [
    # ماه/روز شمسی, نام
    (1,  6,  'عید نوروز تعطیل اضافه'),
    (2,  16, 'رحلت پیامبر اکرم'),
    (2,  18, 'شهادت امام حسن'),
    (3,  28, 'شهادت امام علی'),
    (4,  1,  'عید فطر'),
    (4,  2,  'عید فطر (تعطیل دوم)'),
    (6,  8,  'عید قربان'),
    (6,  18, 'عید غدیر خم'),
    (7,  1,  'تاسوعا'),
    (7,  2,  'عاشورا'),
    (9,  20, 'اربعین'),
    (9,  28, 'رحلت پیامبر اکرم / شهادت امام حسن'),
    (9,  30, 'شهادت امام رضا'),
    (10, 8,  'شهادت امام حسن عسکری'),
    (12, 17, 'میلاد پیامبر اکرم / امام صادق'),
]

LUNAR_HOLIDAYS_1404: list[tuple[int, int, str]] = [
    (2,  5,  'رحلت پیامبر اکرم'),
    (2,  7,  'شهادت امام حسن'),
    (3,  18, 'شهادت امام علی'),
    (3,  21, 'عید فطر'),
    (3,  22, 'عید فطر (تعطیل دوم)'),
    (5,  27, 'عید قربان'),
    (6,  7,  'عید غدیر خم'),
    (6,  21, 'تاسوعا'),
    (6,  22, 'عاشورا'),
    (9,  9,  'اربعین'),
    (9,  17, 'رحلت پیامبر اکرم'),
    (9,  19, 'شهادت امام رضا'),
    (9,  27, 'شهادت امام حسن عسکری'),
    (12, 6,  'میلاد پیامبر اکرم / امام صادق'),
]

LUNAR_HOLIDAYS_1405: list[tuple[int, int, str]] = [
    (1,  25, 'رحلت پیامبر اکرم'),
    (1,  27, 'شهادت امام حسن'),
    (3,  8,  'شهادت امام علی'),
    (3,  9,  'عید فطر'),
    (3,  10, 'عید فطر (تعطیل دوم)'),
    (5,  16, 'عید قربان'),
    (5,  26, 'عید غدیر خم'),
    (6,  10, 'تاسوعا'),
    (6,  11, 'عاشورا'),
    (8,  29, 'اربعین'),
    (9,  6,  'رحلت پیامبر اکرم'),
    (9,  8,  'شهادت امام رضا'),
    (9,  16, 'شهادت امام حسن عسکری'),
    (11, 25, 'میلاد پیامبر اکرم / امام صادق'),
]

_LUNAR_BY_YEAR = {
    1403: {(m, d): name for m, d, name in LUNAR_HOLIDAYS_1403},
    1404: {(m, d): name for m, d, name in LUNAR_HOLIDAYS_1404},
    1405: {(m, d): name for m, d, name in LUNAR_HOLIDAYS_1405},
}


# ── توابع اصلی ────────────────────────────────────────────────────────────────

def to_jalali(d: date):
    """تبدیل تاریخ میلادی به شمسی. اگه jdatetime نصب نشده None برمی‌گردونه."""
    try:
        import jdatetime
        return jdatetime.date.fromgregorian(date=d)
    except ImportError:
        return None


def jalali_str(d: date) -> str:
    """مثال: ۵ تیر ۱۴۰۴"""
    jd = to_jalali(d)
    if jd is None:
        return d.strftime('%Y/%m/%d')
    return f'{jd.day} {MONTHS_FA[jd.month - 1]} {jd.year}'


def jalali_full_str(d: date) -> str:
    """مثال: پنجشنبه، ۵ تیر ۱۴۰۴"""
    jd = to_jalali(d)
    if jd is None:
        day_name = DAYS_FA_PY[d.weekday()]
        return f'{day_name}، {d.strftime("%Y/%m/%d")}'
    day_name = DAYS_FA_JD[jd.weekday()]
    return f'{day_name}، {jd.day} {MONTHS_FA[jd.month - 1]} {jd.year}'


def jalali_day_name(d: date) -> str:
    """نام روز هفته به فارسی."""
    jd = to_jalali(d)
    if jd is None:
        return DAYS_FA_PY[d.weekday()]
    return DAYS_FA_JD[jd.weekday()]


def jalali_month_name(d: date) -> str:
    """نام ماه شمسی."""
    jd = to_jalali(d)
    if jd is None:
        return ''
    return MONTHS_FA[jd.month - 1]


def is_holiday(d: date) -> tuple[bool, str]:
    """
    برمی‌گردونه (True/False, 'اسم تعطیل').
    جمعه همیشه تعطیله.
    تعطیلات رسمی ایران (شمسی ثابت + قمری) هم تعطیله.
    """
    # جمعه
    if d.weekday() == 4:   # Python: 4=Friday
        return True, 'جمعه'

    jd = to_jalali(d)
    if jd is None:
        return False, ''

    # تعطیلات ثابت شمسی
    name = FIXED_HOLIDAYS.get((jd.month, jd.day), '')
    if name:
        return True, name

    # تعطیلات قمری (بر اساس سال)
    lunar = _LUNAR_BY_YEAR.get(jd.year, {})
    name = lunar.get((jd.month, jd.day), '')
    if name:
        return True, name

    return False, ''


def upcoming_holidays(n_days: int = 30) -> list[dict]:
    """لیست تعطیلات n روز آینده."""
    today = date.today()
    result = []
    for i in range(1, n_days + 1):
        d = today + timedelta(days=i)
        flag, name = is_holiday(d)
        if flag and name != 'جمعه':
            result.append({
                'date':      d,
                'jalali':    jalali_str(d),
                'day_name':  jalali_day_name(d),
                'holiday':   name,
                'days_away': i,
            })
    return result


def season_fa(d: date) -> str:
    """فصل فارسی."""
    jd = to_jalali(d)
    if jd is None:
        return ''
    m = jd.month
    if m <= 3:   return 'بهار'
    if m <= 6:   return 'تابستان'
    if m <= 9:   return 'پاییز'
    return 'زمستان'
