"""Small, owner-scoped feedback summaries for relationship suggestions.

This is intentionally derived from existing recommendation outcomes instead of
introducing another persistence layer. It gives the assistant a conservative
signal only after enough feedback exists, and never exposes the feedback text.
"""

from .models import RelationshipRecommendation


def recommendation_learning(user, node):
    """Return an evidence summary for one owner's relationship with ``node``."""
    rows = RelationshipRecommendation.objects.filter(
        owner=user,
        node=node,
        status='completed',
        outcome__in=('better', 'same', 'worse'),
    )
    total = rows.count()
    better = rows.filter(outcome='better').count()
    same = rows.filter(outcome='same').count()
    worse = rows.filter(outcome='worse').count()
    helpful = rows.filter(helpful=True).count()
    answered = rows.filter(helpful__isnull=False).count()
    return {
        'total': total,
        'better': better,
        'same': same,
        'worse': worse,
        'helpful': helpful,
        'helpful_answered': answered,
        'helpful_rate': round(100 * helpful / answered) if answered else None,
    }


def learning_guidance(summary):
    """Translate the summary into a cautious, Persian UI/assistant hint."""
    total = summary.get('total', 0)
    if total < 2:
        return 'هنوز بازخورد کافی از اقدام‌های قبلی نداریم؛ این پیشنهاد را قطعی فرض نکن.'
    if summary.get('better', 0) > summary.get('same', 0) + summary.get('worse', 0):
        return f"از {total} بازخورد ثبت‌شده، {summary['better']} مورد بهترشدن گزارش شده؛ قدم‌های کوچک و مستقیم را در اولویت می‌گذارم."
    if summary.get('worse', 0) >= summary.get('better', 0) and summary.get('worse', 0) > 0:
        return 'در بخشی از اقدام‌های قبلی نتیجه بدتر گزارش شده؛ پیشنهادها را محافظه‌کارانه‌تر و قابل‌لغو نگه می‌دارم.'
    return 'بازخوردهای قبلی نتیجه‌ی یکدستی نشان نمی‌دهند؛ قبل از اقدام، زمینه و ترجیح خودت را دوباره بررسی کن.'
