"""Fast, deterministic insights built only from owner-scoped observations.

These helpers intentionally separate measurements from interpretation.  They
never diagnose personality, attachment style, intent, or relationship quality
from sparse text.  Generative providers may later rephrase these results, but
the default product path stays local, explainable, and available offline.
"""

from __future__ import annotations

import re
from collections import Counter

from django.db.models import Q
from django.utils import timezone

from .models import Interaction, Node, Relationship


ENGINE = 'grounded_insights_v1'


def _items(value, limit=5):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        text = str(item or '').strip()
        if text and text not in result:
            result.append(text[:160])
        if len(result) >= limit:
            break
    return result


def person_summary(node, analysis):
    """Render the existing relationship engine result without an LLM call."""
    lines = [f'«{node.display_name()}» در گراف شخصی تو ثبت شده است.']
    basics = []
    if node.career:
        basics.append(f'شغل ثبت‌شده: {node.career}')
    if analysis.get('relationship_quality'):
        basics.append(analysis['relationship_quality'])
    if basics:
        lines.append('؛ '.join(basics) + '.')

    interests = _items(analysis.get('interests'), 4)
    if interests:
        lines.append('علایق ثبت‌شده: ' + '، '.join(interests) + '.')
    personality = str(analysis.get('personality') or '').strip()
    if personality:
        lines.append(personality)

    confidence = int(analysis.get('confidence') or 0)
    evidence_count = int((analysis.get('data_coverage') or {}).get('evidence_count') or 0)
    lines.append(
        f'این جمع‌بندی از {evidence_count} شاهد ثبت‌شده ساخته شده و اطمینان تحلیل {confidence}٪ است.'
    )
    if analysis.get('tip'):
        lines.append(str(analysis['tip']))
    return '\n\n'.join(lines)


def network_analysis(user, summary):
    """Explain network structure and contact rhythm without psychological claims."""
    from .health import compute_health

    health_map = compute_health(user)
    known = [row for row in health_map.values() if row.get('score') is not None]
    unknown = [row for row in health_map.values() if row.get('score') is None]
    red = [row for row in known if row.get('status') == 'red']
    yellow = [row for row in known if row.get('status') == 'yellow']
    green = [row for row in known if row.get('status') == 'green']
    score = round(sum(row['score'] for row in known) / len(known)) if known else None

    interaction_count = Interaction.objects.filter(owner=user, node__owner=user).count()
    evidence_count = int(summary.get('روابط') or 0) + interaction_count
    coverage = round(len(known) / max(len(health_map), 1) * 100) if health_map else 0
    confidence = min(100, round(coverage * .65 + min(35, interaction_count * 3)))

    if score is None:
        label = 'دادهٔ تماس کافی نیست'
        health_summary = (
            'برای سنجش ریتم ارتباط‌ها هنوز تعامل تاریخ‌دار کافی ثبت نشده است؛ '
            'نبود داده به معنی رابطهٔ ضعیف نیست.'
        )
    else:
        label = 'ریتم ارتباط‌ها پایدار' if score >= 70 else (
            'بخشی از روابط نیازمند توجه' if score >= 40 else 'فاصلهٔ ارتباطی ثبت شده'
        )
        health_summary = (
            f'امتیاز {score} فقط ریتم تماس‌های ثبت‌شده را نسبت به فاصلهٔ مورد انتظار می‌سنجد. '
            f'برای {len(known)} رابطه داده داریم و {len(unknown)} رابطه هنوز بدون دادهٔ کافی است.'
        )

    n_edges = int(summary.get('روابط') or 0)
    strong = int(summary.get('پیوندهای_قوی') or 0)
    weak = int(summary.get('پیوندهای_ضعیف') or 0)
    communities = int(summary.get('تعداد_گروه_اجتماعی') or 0)
    active = int(summary.get('روابط_فعال') or 0)
    brokers = _items(summary.get('واسطه‌های_اصلی'), 3)

    patterns = []
    if n_edges:
        patterns.append(f'از {n_edges} رابطه، {strong} رابطه با قدرت ۴ یا ۵ و {weak} رابطه با قدرت ۱ یا ۲ ثبت شده است.')
        patterns.append(f'{active} رابطه با وضعیت «فعال» ثبت شده است؛ این عدد وضعیت ثبت‌شده است، نه قضاوت درباره کیفیت رابطه.')
    if communities:
        patterns.append(f'گراف به {communities} خوشهٔ ساختاری تقسیم می‌شود؛ این خوشه‌ها فقط نزدیکی شبکه‌ای را نشان می‌دهند.')
    if brokers:
        patterns.append('افراد با نقش اتصال ساختاری بیشتر: ' + '، '.join(brokers) + '.')
    if not patterns:
        patterns.append('برای مشاهدهٔ الگوهای ساختاری، چند رابطه و تعامل تاریخ‌دار ثبت کن.')

    risks = []
    if red:
        risks.append('فاصلهٔ تماس از انتظار ثبت‌شده بیشتر شده: ' + '، '.join(row['name'] for row in red[:4]) + '.')
    if unknown:
        risks.append(f'{len(unknown)} رابطه دادهٔ تماس کافی ندارد؛ دربارهٔ وضعیت آن‌ها نمی‌شود نتیجه گرفت.')
    if n_edges and weak / n_edges >= .6:
        risks.append('بیش از ۶۰٪ یال‌ها با قدرت ۱ یا ۲ ثبت شده‌اند؛ ممکن است درجهٔ نزدیکی بعضی رابطه‌ها نیاز به بازبینی داشته باشد.')
    if not risks:
        risks.append('از داده‌های فعلی هشدار ساختاری مشخصی دیده نشد؛ این به معنی نبود مشکل در دنیای واقعی نیست.')

    opportunities = []
    if yellow:
        opportunities.append('یک تماس کوتاه و بدون فشار با این افراد می‌تواند ریتم ارتباط را برگرداند: ' + '، '.join(row['name'] for row in yellow[:4]) + '.')
    if unknown:
        opportunities.append('با ثبت یک تعامل برای روابط بدون داده، پوشش تحلیل را بالا ببر.')
    if weak and strong:
        opportunities.append('پیوندهای قوی برای حمایت و پیوندهای ضعیف برای دسترسی به جمع‌های متفاوت کاربردهای جدا دارند؛ لازم نیست همهٔ رابطه‌ها قوی شوند.')
    if not opportunities:
        opportunities.append('ثبت حس بعد از تعامل و فاصلهٔ تماس دلخواه، تحلیل بعدی را دقیق‌تر می‌کند.')

    recommendations = []
    if red:
        recommendations.append({
            'action': f'امروز با {red[0]["name"]} یک پیام کوتاه و بدون انتظار پاسخ فوری بفرست.',
            'theory': 'فاصلهٔ تماس ثبت‌شده', 'impact': 'بالا',
        })
    if unknown:
        recommendations.append({
            'action': 'بعد از تعامل بعدی، نوع تماس و حس بعدش را ثبت کن.',
            'theory': 'افزایش پوشش داده', 'impact': 'متوسط',
        })
    if brokers:
        recommendations.append({
            'action': f'نقش ارتباطی {brokers[0]} را در گراف مرور کن؛ فقط در صورت مناسب‌بودن از او برای معرفی کمک بگیر.',
            'theory': 'مرکزیت بینابینی', 'impact': 'متوسط',
        })
    if not recommendations:
        recommendations.append({
            'action': 'این هفته یک تعامل معنادار را ثبت کن تا روند زمانی شکل بگیرد.',
            'theory': 'دادهٔ طولی', 'impact': 'متوسط',
        })

    return {
        'health': {'score': score, 'label': label, 'summary': health_summary},
        'patterns': patterns,
        'risks': risks,
        'opportunities': opportunities,
        'recommendations': recommendations,
        'psychological_profile': (
            'از ساختار گراف، تعداد پیام یا فاصلهٔ تماس نمی‌توان شخصیت، سبک دلبستگی یا سلامت روان را تشخیص داد. '
            'این بخش فقط الگوهای رفتاریِ ثبت‌شده را گزارش می‌کند و برای شناخت عمیق‌تر به خوداظهاری و شواهد مستقیم نیاز دارد.'
        ),
        'sociological_summary': (
            f'شبکهٔ ثبت‌شده شامل {summary.get("افراد_شبکه", 0)} نفر و {n_edges} رابطه است. '
            f'{communities} خوشهٔ ساختاری و {strong} پیوند قوی ثبت شده است. '
            'این اعداد شکل داده‌های واردشده را توصیف می‌کنند و الزاماً کل روابط واقعی کاربر را نمایندگی نمی‌کنند.'
        ),
        'confidence': confidence,
        'coverage': {
            'contact_coverage_percent': coverage,
            'relationships_with_contact_data': len(known),
            'relationships_without_contact_data': len(unknown),
            'evidence_count': evidence_count,
        },
        'generated_by': ENGINE,
    }


def alert_recommendations(person_data, alert_type, alert_title):
    name = str(person_data.get('name') or 'این شخص')
    interests = _items(person_data.get('interests'), 3)
    preferences = _items(person_data.get('preferences'), 2)
    suggestions = []

    def add(action, reason, difficulty='آسان'):
        suggestions.append({
            'rank': len(suggestions) + 1,
            'action': action,
            'reason': reason,
            'difficulty': difficulty,
        })

    kind = str(alert_type or '').lower()
    title = str(alert_title or '').strip()
    if kind == 'mood_alert':
        add(f'به {name} یک پیام بدون قضاوت بده: «اگر دوست داشتی حرف بزنی، من هستم.»',
            'پیشنهاد حمایت می‌دهد بدون اینکه درباره حال او تشخیص بدهد.')
        add('به‌جای راه‌حل فوری، اول بپرس الان گوش‌دادن می‌خواهد یا کمک عملی.',
            'نوع حمایت را خودِ فرد مشخص می‌کند.')
        add('اگر پاسخ داد، یک کمک کوچک و مشخص پیشنهاد کن؛ مثل تماس کوتاه یا همراهی در یک کار.',
            'پیشنهاد مشخص، پذیرفتن یا ردکردن کمک را ساده می‌کند.')
    elif kind in {'dormant', 'cooling', 'decay'}:
        add(f'یک احوال‌پرسی کوتاه و بدون گلایه برای {name} بفرست.',
            'شروع کم‌فشار برای رابطه‌ای است که مدتی تعامل ثبت‌شده نداشته.')
        add('به یک خاطره یا موضوع مشترک واقعی اشاره کن، فقط اگر واقعاً یادت هست.',
            'جزئیات واقعی پیام را شخصی می‌کند؛ چیزی ساخته نمی‌شود.')
        add('اگر پاسخ گرم بود، یک تماس یا دیدار کوتاه پیشنهاد بده.',
            'حرکت تدریجی از فشار ناگهانی جلوگیری می‌کند.', 'متوسط')
    elif kind in {'birthday', 'lifeevent', 'event'} or 'تولد' in title:
        add(f'یک پیام کوتاه و مشخص برای {name} بفرست و به خودِ مناسبت اشاره کن.',
            'یادآوری به‌موقع از هدیهٔ گران مهم‌تر است.')
        if interests:
            add(f'اگر هدیه می‌خواهی، یک گزینهٔ کوچک مرتبط با علاقهٔ ثبت‌شدهٔ «{interests[0]}» بررسی کن.',
                'این پیشنهاد فقط به علاقه‌ای تکیه دارد که قبلاً ثبت شده.', 'متوسط')
        else:
            add('اگر از سلیقه‌اش مطمئن نیستی، قبل از خرید از خودش یا یک نزدیک مطمئن سؤال کن.',
                'نبود داده با حدس‌زدن جبران نمی‌شود.', 'متوسط')
        add('اگر رابطه‌تان نزدیک است، یک تماس کوتاه یا برنامهٔ مشترک پیشنهاد بده.',
            'زمان مشترک معمولاً از پیام عمومی شخصی‌تر است.', 'متوسط')
    else:
        add(f'جزئیات هشدار «{title or "موضوع امروز"}» را مرور کن و یک اقدام کوچک برای {name} انتخاب کن.',
            'پیشنهاد مستقیماً از هشدار ثبت‌شده می‌آید.')
        add('اگر مطمئن نیستی چه کمکی مناسب است، مستقیم و محترمانه بپرس.',
            'سؤال روشن بهتر از حدس درباره نیاز طرف مقابل است.')

    if preferences:
        add(f'ترجیح ثبت‌شدهٔ «{preferences[0]}» را فقط در صورت مرتبط‌بودن با این موقعیت در نظر بگیر.',
            'شخصی‌سازی بر اساس دادهٔ تأییدشده انجام می‌شود.')

    return {
        'suggestions': suggestions[:5],
        'personal_note': (
            f'این پیشنهادها از {len(interests) + len(preferences)} ترجیح یا علاقهٔ ثبت‌شده و خودِ هشدار ساخته شده‌اند.'
            if interests or preferences else
            'شناخت ثبت‌شده کم است؛ پیشنهادها عمداً عمومی و بدون حدس شخصیتی‌اند.'
        ),
        'generated_by': ENGINE,
    }


def greeting(name, alert_type, alert_title):
    name = str(name or 'دوستم').strip()
    kind = str(alert_type or '').lower()
    title = str(alert_title or '').strip()
    if kind == 'birthday' or 'تولد' in title:
        return f'{name} جان، تولدت مبارک! امیدوارم سال تازهٔ زندگیت پر از اتفاق‌های خوب و حالِ دلِ خوش باشه 🎂'
    if 'نوروز' in title or 'سال نو' in title:
        return f'{name} جان، سال نو مبارک! امیدوارم سال پیش رو برات پر از آرامش، سلامتی و خبرهای خوب باشه 🌱'
    if 'یلدا' in title:
        return f'{name} جان، یلدات مبارک! امیدوارم این شب بلند کنار عزیزانت گرم و خوش بگذره.'
    if title:
        return f'{name} جان، به مناسبت {title} به یادت بودم. امیدوارم روز خیلی خوبی داشته باشی.'
    return f'{name} جان، امروز به یادت بودم؛ امیدوارم حالت خوب باشه و روز قشنگی داشته باشی.'


def daily_tips(*, day_name, jalali_date, is_holiday, holiday_name,
               urgent, weak_names, overlooked_names):
    if is_holiday and holiday_name and holiday_name != 'جمعه':
        day_message = f'امروز {day_name}، {jalali_date} و {holiday_name} است؛ یک ارتباط گرم و کم‌فشار کافی است.'
    elif is_holiday:
        day_message = f'امروز {day_name}، {jalali_date} است؛ روز مناسبی برای یک تماس کوتاه یا استراحت آگاهانه.'
    else:
        day_message = f'امروز {day_name}، {jalali_date} است؛ یک اقدام کوچک و مشخص برای شبکه‌ات انتخاب کن.'

    tips = []

    def add(emoji, title, action, reason, time_needed='۵ دقیقه'):
        tips.append({
            'emoji': emoji, 'title': title, 'action': action,
            'reason': reason, 'time_needed': time_needed,
            # Compatibility with both daily page renderers.
            'tip': f'{title}: {action}', 'why': reason,
        })

    if urgent:
        add('📌', 'اولویت ثبت‌شده', f'هشدار «{urgent[0]}» را مرور و یک قدم کوچک انجام بده.',
            'این مورد در داده‌های امروز با اولویت بالا ثبت شده است.')
    if overlooked_names:
        add('👋', 'احوال‌پرسی کوتاه', f'به {overlooked_names[0]} یک پیام کوتاه و بدون انتظار پاسخ فوری بده.',
            'در ۱۴ روز اخیر ذکری از این رابطه ثبت نشده؛ این فقط یادآوری است، نه قضاوت.')
    if weak_names:
        add('🧭', 'بازبینی نزدیکی', f'قدرت رابطهٔ ثبت‌شده با {weak_names[0]} را مرور کن؛ اگر هنوز درست است، لازم نیست تغییرش بدهی.',
            'هر رابطه‌ای قرار نیست صمیمی باشد؛ مهم درست‌بودن داده است.')
    if is_holiday:
        add('☕', 'وقت باکیفیت', 'با یک نفر که خودت انتخاب می‌کنی تماس کوتاه یا برنامهٔ ساده‌ای هماهنگ کن.',
            'روز تعطیل فضای بیشتری برای ارتباط بدون عجله دارد.', '۱۰ دقیقه')
    else:
        add('✅', 'یک موضوع باز', 'یکی از قول‌ها یا پیگیری‌های باز را ببند.',
            'اقدام مشخص از فهرست توصیه‌های عمومی مفیدتر است.', '۱۰ دقیقه')
    if len(tips) < 3:
        add('📝', 'دادهٔ بهتر', 'بعد از تعامل بعدی، نوع تماس و حس بعدش را ثبت کن.',
            'دادهٔ تاریخ‌دار دقت تحلیل‌های بعدی را بالا می‌برد.', '۱ دقیقه')

    focus = overlooked_names[0] if overlooked_names else (weak_names[0] if weak_names else '')
    return {
        'day_message': day_message,
        'tips': tips[:5],
        'focus_person': {
            'name': focus,
            'suggestion': 'یک پیام کوتاه و بدون فشار بفرست.' if focus else '',
        },
        'generated_by': ENGINE,
    }


def connect_plan(data, goal, person):
    name = str(person.get('name') or data.get('target_name') or 'این شخص')
    work = goal == 'work'
    relation_name = 'ارتباط حرفه‌ای' if work else 'دوستی'
    mutuals = data.get('mutuals') or []
    groups = data.get('shared_groups') or []
    events = data.get('shared_events') or []
    interests = _items(person.get('interests'), 3)
    steps = []
    evidence = []

    def add(title, how, why, when):
        steps.append({'n': len(steps) + 1, 'title': title, 'how': how, 'why': why, 'when': when})

    if data.get('is_direct'):
        add('از رابطهٔ موجود شروع کن', f'یک پیام کوتاه و متناسب با سابقهٔ واقعی‌ات برای {name} بفرست.',
            'رابطه از قبل در گراف مستقیم ثبت شده است.', 'امروز')
        evidence.append('رابطهٔ مستقیم ثبت‌شده')
    elif mutuals:
        bridge = mutuals[0]['name']
        add('مناسب‌بودن معرفی را بررسی کن', f'از {bridge} بپرس آیا یک معرفی کوتاه برای هر دو طرف مناسب است.',
            'او قوی‌ترین آشنای مشترک ثبت‌شده در گراف است.', 'امروز')
        evidence.append(f'آشنای مشترک: {bridge}')
    elif groups:
        add('از بافت مشترک شروع کن', f'در زمینهٔ واقعی گروه «{groups[0]}» یک گفت‌وگوی مرتبط با {name} شروع کن.',
            'گروه مشترک در داده‌ها ثبت شده است.', 'این هفته')
        evidence.append(f'گروه مشترک: {groups[0]}')
    elif events:
        add('به تجربهٔ مشترک اشاره کن', f'دربارهٔ رویداد «{events[0]["title"]}» یک سؤال واقعی از {name} بپرس.',
            'رویداد مشترک در گراف ثبت شده است.', 'این هفته')
        evidence.append(f'رویداد مشترک: {events[0]["title"]}')
    else:
        add('شفاف و کوتاه شروع کن', f'خودت را کوتاه معرفی کن و دلیل واقعی علاقه‌ات به {relation_name} با {name} را بگو.',
            'در گراف مسیر یا بافت مشترکی ثبت نشده؛ معرفی شفاف از ساختن بهانه بهتر است.', 'امروز')

    if interests:
        add('یک نقطهٔ مشترک واقعی پیدا کن', f'اگر «{interests[0]}» واقعاً به هدفت مربوط است، یک سؤال باز و مشخص درباره‌اش بپرس.',
            'این علاقه قبلاً برای این فرد ثبت و تأیید شده است.', 'گفت‌وگوی اول')
        evidence.append(f'علاقهٔ ثبت‌شده: {interests[0]}')
    else:
        add('به‌جای حدس سؤال بپرس', 'یک سؤال باز دربارهٔ کارها یا علایق فعلی‌اش بپرس و به جوابش گوش بده.',
            'اطلاعات کافی برای شخصی‌سازی بیشتر وجود ندارد.', 'گفت‌وگوی اول')

    add('ارزش کوچکی اضافه کن',
        'اگر پاسخ داد، یک منبع، معرفی یا کمک کوچک و مرتبط پیشنهاد بده؛ بدون ایجاد بدهی یا انتظار متقابل.',
        'کمک باید متناسب با نیاز گفته‌شده باشد، نه ابزار فشار.', 'این هفته')
    add('ریتم طرف مقابل را رعایت کن',
        'اگر پاسخ کوتاه یا دیر بود، پیگیری پشت‌سرهم نکن؛ چند روز بعد فقط در صورت داشتن دلیل واقعی ادامه بده.',
        'مرز و رضایت طرف مقابل از هر تکنیک ارتباطی مهم‌تر است.', 'در ادامه')

    opener = (
        f'سلام {name}، من دربارهٔ کار/تجربه‌ات کنجکاوم. اگر فرصت داشتی دوست دارم کمی بیشتر درباره‌اش بدونم.'
        if work else
        f'سلام {name}، گفتم یه احوال‌پرسی کنم. اگر دوست داشتی خوشحال می‌شم بیشتر آشنا بشیم.'
    )
    return {
        'steps': steps[:6],
        'opener': opener,
        'warning': 'از اطلاعات شخصی ثبت‌شده برای تحت فشار گذاشتن یا ایجاد صمیمیت مصنوعی استفاده نکن.',
        'confidence': min(90, 35 + len(evidence) * 20),
        'evidence': evidence,
        'generated_by': ENGINE,
    }


_TOPICS = {
    'کار و درس': ('کار', 'پروژه', 'جلسه', 'دانشگاه', 'درس', 'امتحان'),
    'برنامه و قرار': ('قرار', 'بریم', 'بیای', 'ساعت', 'فردا', 'امروز'),
    'حال و احوال': ('خوبی', 'حالت', 'خسته', 'خوشحال', 'ناراحت'),
    'خانواده': ('خانواده', 'مامان', 'مادر', 'بابا', 'پدر', 'خواهر', 'برادر'),
}


def direct_chat_analysis(user, friend, messages):
    """Measure a direct chat; do not infer either participant's personality."""
    total = len(messages)
    mine = sum(1 for message in messages if message.sender_id == user.id)
    theirs = total - mine
    mine_questions = sum(message.content.count('؟') + message.content.count('?')
                         for message in messages if message.sender_id == user.id)
    their_questions = sum(message.content.count('؟') + message.content.count('?')
                          for message in messages if message.sender_id != user.id)
    joined = ' '.join(message.content for message in messages).lower()
    topic_counts = {
        label: sum(joined.count(term) for term in terms)
        for label, terms in _TOPICS.items()
    }
    topics = [label for label, count in sorted(topic_counts.items(), key=lambda row: -row[1]) if count][:4]
    start = messages[0].created_at if messages else None
    end = messages[-1].created_at if messages else None
    span_days = max(1, (end.date() - start.date()).days + 1) if start and end else 0

    signals = [
        f'از {total} پیام بررسی‌شده، {mine} پیام از تو و {theirs} پیام از {friend.username} بوده است.',
        f'در متن {mine_questions} سؤال از طرف تو و {their_questions} سؤال از طرف او دیده شد.',
    ]
    if span_days:
        signals.append(f'این نمونه بازه‌ای حدود {span_days} روز را پوشش می‌دهد.')
    suggestions = [
        'برای شناخت دقیق‌تر، به گفته‌های صریح و تغییرات در چند بازهٔ زمانی تکیه کن؛ تعداد پیام به‌تنهایی کیفیت رابطه را نشان نمی‌دهد.'
    ]
    return {
        'summary': f'این گزارش از {total} پیام واقعی بین تو و {friend.username} ساخته شده است؛ فقط الگوی قابل‌اندازه‌گیری را نشان می‌دهد.',
        'mood': 'از متن چت به‌تنهایی قابل تعیین نیست',
        'topics': topics,
        'signals': signals,
        'suggestions': suggestions,
        'followups': [],
        'confidence': 100 if total >= 10 else 70,
        'generated_by': ENGINE,
        'grounded': True,
        'metrics': {
            'messages': total,
            'mine': mine,
            'theirs': theirs,
            'my_questions': mine_questions,
            'their_questions': their_questions,
            'span_days': span_days,
        },
        'evidence_message_ids': [message.id for message in messages],
    }


def cultural_work_analysis(kind, title, creator=''):
    label = {'book': 'کتاب', 'movie': 'فیلم', 'series': 'سریال', 'music': 'موسیقی'}.get(kind, 'اثر')
    creator_note = f' از {creator}' if creator else ''
    return {
        'summary': (
            f'{label} «{title}»{creator_note} به‌عنوان یک انتخاب فرهنگی ثبت شده است. '
            'خودِ این انتخاب به‌تنهایی ویژگی شخصیتی، ارزش یا وضعیت رابطه را ثابت نمی‌کند.'
        ),
        # Keep legacy keys for template/model compatibility, but make their
        # contents explicit questions instead of personality claims.
        'personality_signals': [
            'برای شناخت بهتر، دلیل انتخاب و بخش محبوب یا نامطلوب این اثر را از خودِ فرد بپرس.',
            'امتیاز و یادداشت شخص از عنوان اثر اطلاعات معتبرتری می‌دهد.',
        ],
        'relationship_signals': [
            'این اثر می‌تواند موضوع گفت‌وگو باشد؛ شباهت سلیقه به‌تنهایی نشانهٔ کیفیت رابطه نیست.',
        ],
        'themes': [],
        'generated_by': ENGINE,
        'grounded': True,
    }


_NAME_STOP = {
    'من', 'او', 'اون', 'ایشون', 'امروز', 'فردا', 'دیروز', 'بعد', 'قبل',
    'سلام', 'باشه', 'آره', 'نه', 'خوب', 'خب', 'ولی', 'اگه', 'اگر', 'وقتی',
    'کسی', 'یکی', 'همه', 'هیچکس', 'تلگرام', 'اینستاگرام',
}


def telegram_people(user, target_name, sample):
    """Find explicit third-person mentions and return their exact source line."""
    lines = [line.strip() for line in str(sample or '').splitlines() if line.strip()]
    candidates = {}

    def add(name, evidence, relation='نسبت در متن صریح مشخص نشده', confidence=65):
        clean = str(name or '').strip(' ،,:؛.!؟«»"\'')[:50]
        key = clean.replace('ي', 'ی').replace('ك', 'ک').lower()
        if (len(clean) < 2 or clean in _NAME_STOP or key == str(target_name or '').lower()
                or key in {'من', 'او'}):
            return
        row = candidates.setdefault(key, {
            'name': clean, 'relation': relation, 'evidence': evidence[:220],
            'confidence': confidence, 'source': 'telegram_export', 'count': 0,
        })
        row['count'] += 1
        row['confidence'] = max(row['confidence'], confidence)

    # Existing owner-scoped people are the safest matches.
    for node in Node.objects.filter(owner=user).only(
            'username', 'name', 'first_name', 'last_name', 'nickname'):
        labels = _items([
            node.nickname, node.name,
            f'{node.first_name} {node.last_name}'.strip(), node.first_name, node.username,
        ], 5)
        for line in lines:
            body = line.split(':', 1)[-1]
            for label in labels:
                if len(label) >= 2 and re.search(rf'(?<![\wآ-ی]){re.escape(label)}(?![\wآ-ی])', body, re.I):
                    add(node.display_name(), line, 'شخص موجود در گراف که نامش صریحاً آمده', 90)
                    break

    relation_re = re.compile(
        r'(?P<rel>دوست|همکار|خواهر|برادر|مادر|مامان|پدر|بابا|همسر|نامزد|رئیس|استاد|دکتر)'
        r'(?:م|ت|ش|مون|تون|شون)?\s+(?P<name>[آ-یA-Za-z][آ-یA-Za-z\u200c_-]{1,30})'
    )
    action_re = re.compile(
        r'(?P<name>[آ-یA-Za-z][آ-یA-Za-z\u200c_-]{1,30})\s+'
        r'(?:گفت|اومد|آمد|رفت|زنگ زد|پیام داد|پرسید)'
    )
    handle_re = re.compile(r'@(?P<name>[\w.-]{2,30})')
    for line in lines:
        body = line.split(':', 1)[-1].strip()
        for match in relation_re.finditer(body):
            add(match.group('name'), line, f'{match.group("rel")}؛ همان‌طور که در متن آمده', 85)
        for match in action_re.finditer(body):
            add(match.group('name'), line, confidence=72)
        for match in handle_re.finditer(body):
            add(match.group('name'), line, 'نام کاربری صریح در متن', 80)

    rows = sorted(candidates.values(), key=lambda row: (-row['count'], -row['confidence'], row['name']))[:6]
    for row in rows:
        row.pop('count', None)
    return {'people': rows, 'generated_by': ENGINE, 'grounded': True}


def journal_result(suggestions, text, root_username):
    """Convert reviewed extraction candidates to the legacy journal preview."""
    nodes, relationships, events = [], [], []
    seen_nodes = set()

    def person_identity(suggestion, payload):
        existing_id = payload.get('existing_node_id')
        existing = None
        if isinstance(existing_id, int) and not isinstance(existing_id, bool):
            existing = Node.objects.filter(
                owner=suggestion.owner, id=existing_id,
            ).only('username', 'name', 'first_name', 'last_name', 'nickname').first()
        if existing:
            return existing.username, existing.display_name()
        name = str(payload.get('name_raw') or payload.get('name') or '').strip()
        username = str(payload.get('username') or re.sub(r'\s+', '_', name)).strip()[:80]
        return username, name

    for suggestion in suggestions:
        payload = suggestion.payload if isinstance(suggestion.payload, dict) else {}
        sid = suggestion.id
        if suggestion.kind == 'person':
            username, name = person_identity(suggestion, payload)
            if not name:
                continue
            if username not in seen_nodes:
                nodes.append({'username': username, 'name': name, '_suggestion_id': sid})
                seen_nodes.add(username)
        elif suggestion.kind == 'relationship':
            username, name = person_identity(suggestion, payload)
            if not name:
                continue
            if username not in seen_nodes:
                nodes.append({'username': username, 'name': name, '_suggestion_id': sid})
                seen_nodes.add(username)
            relationships.append({
                'from': root_username or 'me', 'to': username,
                'type': str(payload.get('relationship_type') or '')[:80],
                'strength': min(5, max(1, int(payload.get('strength') or 3))),
                '_suggestion_id': sid,
            })
        elif suggestion.kind == 'event':
            title = str(payload.get('title') or payload.get('snippet') or '').strip()
            if title:
                events.append({
                    'title': title[:100], 'date': payload.get('date') or None,
                    'description': str(payload.get('snippet') or '')[:300],
                    '_suggestion_id': sid,
                })

    count = len(suggestions)
    return {
        'nodes': nodes, 'relationships': relationships, 'events': events,
        'attributes': [], 'my_mood': '', 'my_insights': [],
        'summary': (
            f'خاطره ذخیره شد و {count} پیشنهاد از عبارت‌های صریح متن پیدا شد. '
            'قبل از افزودن به گراف، موارد را مرور کن.' if count else
            'خاطره ذخیره شد. گزارهٔ صریح و قابل‌اعتمادی برای افزودن خودکار به گراف پیدا نشد.'
        ),
        'generated_by': ENGINE,
        'grounded': True,
        'source_preview': str(text or '')[:180],
    }
