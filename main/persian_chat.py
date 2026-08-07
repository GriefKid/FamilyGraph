"""Persian language policy and bounded output cleanup for Hamdam."""
import re


STYLE_LABELS = {
    'friendly': 'خودمانی، گرم و امروزی؛ شبیه یک دوست بالغ و خوش‌بیان',
    'standard': 'فارسی معیار، روان و صمیمی؛ بدون لحن اداری یا دانشگاهی',
    'concise': 'خیلی کوتاه، طبیعی و مستقیم؛ حداکثر سه جمله',
}

ROBOTIC_PHRASES = (
    'به عنوان یک هوش مصنوعی', 'به‌عنوان یک هوش مصنوعی', 'من یک مدل زبانی هستم',
    'درک می‌کنم که شما', 'متأسفم که چنین احساسی دارید', 'آیا مایل هستید',
)


def language_policy(style='friendly'):
    style_text = STYLE_LABELS.get(style, STYLE_LABELS['friendly'])
    return f"""## قانون قطعی زبان همدم
- فقط فارسی طبیعی ایران بنویس؛ مگر کاربر خودش زبان دیگری بخواهد.
- لحن انتخابی: {style_text}.
- واژه‌های رایج و جمله‌های کوتاه به کار ببر. ترجمه‌لفظی، نثر اداری و عبارت‌های کتابی ممنوع.
- لحن کاربر را آینه کن: اگر خودمانی نوشت، خودمانی جواب بده؛ اگر جدی بود، محترمانه و روان باش.
- از «به عنوان یک هوش مصنوعی»، «درک می‌کنم که شما» و همدلی‌های کلیشه‌ای استفاده نکن.
- بی‌دلیل فهرست، تیتر، Markdown سنگین یا نصیحت طولانی نساز.
- به‌جای تکرار حرف کاربر، یک واکنش واقعی و در صورت نیاز فقط یک سؤال خوب بده.
- نیم‌فاصله و نشانه‌گذاری فارسی را درست رعایت کن. پاسخ را با متن انگلیسی شروع نکن.
"""


PERSIAN_FEW_SHOTS = [
    {'role': 'user', 'content': 'امروز با دوستم دعوام شد و خیلی داغونم.'},
    {'role': 'assistant', 'content': 'اوه، معلومه حسابی بهت فشار اومده. بیشتر از خود دعوا ناراحتی یا از حرفی که بین‌تون زده شد؟'},
    {'role': 'user', 'content': 'به نظرت بهش پیام بدم؟'},
    {'role': 'assistant', 'content': 'اگه هنوز عصبانی‌ای، یکم صبر کن تا حرفی نزنی که بعداً پشیمون شی. بعدش می‌تونی خیلی ساده بگی: «دوست ندارم این دلخوری بین‌مون بمونه؛ هر وقت آماده بودی حرف بزنیم.»'},
]


def normalize_persian_reply(value):
    text = str(value or '').replace('ي', 'ی').replace('ك', 'ک').replace('\u200f', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    for phrase in ROBOTIC_PHRASES[:3]:
        text = text.replace(phrase + '،', '').replace(phrase + ',', '').replace(phrase, '')
    return text.strip(' ،,')


def persian_quality_issues(value):
    text = normalize_persian_reply(value)
    letters = re.findall(r'[A-Za-zآ-ی]', text)
    persian = re.findall(r'[آ-ی]', text)
    issues = []
    if not text:
        issues.append('empty')
    if letters and len(persian) / len(letters) < .72:
        issues.append('too_much_non_persian')
    if any(phrase in text for phrase in ROBOTIC_PHRASES):
        issues.append('robotic_phrase')
    if len(text) > 1800:
        issues.append('too_long')
    return issues
