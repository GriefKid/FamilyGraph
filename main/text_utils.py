"""Small dependency-free text helpers used for human-readable identifiers."""

import re


_COMMON_FINGLISH = {
    'آراد': 'arad', 'آرمان': 'arman', 'آرین': 'arian', 'احمد': 'ahmad',
    'اکبر': 'akbar', 'الهام': 'elham', 'امیر': 'amir', 'امیرحسین': 'amirhossein',
    'اصغر': 'asghar', 'پارسا': 'parsa', 'بردیا': 'bardia', 'پوریا': 'pouria',
    'حامد': 'hamed', 'حسن': 'hasan', 'حسین': 'hossein', 'حمید': 'hamid',
    'رضا': 'reza', 'رضوان': 'rezvan', 'رضایی': 'rezaei', 'رحیمی': 'rahimi',
    'سارا': 'sara', 'سجاد': 'sajjad', 'سمیه': 'somayeh', 'سپهر': 'sepehr',
    'شایان': 'shayan', 'شیرین': 'shirin', 'صادقی': 'sadeghi', 'فاطمه': 'fatemeh',
    'فرهاد': 'farhad', 'کامران': 'kamran', 'کیانا': 'kiana', 'کیان': 'kian',
    'کیوان': 'keyvan', 'کریمی': 'karimi', 'کاظمی': 'kazemi', 'مریم': 'maryam',
    'مجید': 'majid', 'محمد': 'mohammad', 'محمدرضا': 'mohammadreza',
    'محمدی': 'mohammadi', 'مهدی': 'mehdi', 'مرادی': 'moradi', 'مینا': 'mina',
    'موسی': 'mousa', 'موسوی': 'mousavi', 'نگار': 'negar', 'نرگس': 'narges',
    'نسرین': 'nasrin', 'نوید': 'navid', 'نوشین': 'noushin', 'نوری': 'nouri',
    'نیما': 'nima', 'هادی': 'hadi', 'حیدری': 'heidari', 'جعفری': 'jafari',
    'زهرا': 'zahra', 'علی': 'ali', 'علیرضا': 'alireza',
    'علوی': 'alavi', 'لیلا': 'leyla', 'مهدی': 'mehdi', 'یزدانی': 'yazdani',
}

_PERSIAN_TO_LATIN = str.maketrans({
    'ا': 'a', 'آ': 'a', 'ب': 'b', 'پ': 'p', 'ت': 't', 'ث': 's', 'ج': 'j',
    'چ': 'ch', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'z', 'ر': 'r', 'ز': 'z',
    'ژ': 'zh', 'س': 's', 'ش': 'sh', 'ص': 's', 'ض': 'z', 'ط': 't', 'ظ': 'z',
    'ع': 'a', 'غ': 'gh', 'ف': 'f', 'ق': 'gh', 'ک': 'k', 'گ': 'g', 'ل': 'l',
    'م': 'm', 'ن': 'n', 'و': 'v', 'ه': 'h', 'ی': 'y', 'ء': '', 'ئ': 'y',
    'ؤ': 'v', 'ۀ': 'e', 'ة': 'h', 'ك': 'k', 'ي': 'y', 'ى': 'a',
})


def finglish_slug(value):
    """Turn a Persian/Arabic or Latin name into a readable ASCII slug.

    Common Persian names use their familiar spellings; unknown words still
    get a deterministic consonant-preserving transliteration rather than a
    random or Unicode identifier.
    """
    value = str(value or '').replace('\u200c', ' ').replace('ـ', '')
    value = value.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    tokens = re.findall(r"[A-Za-z0-9]+|[آ-یءئؤۀةككيى]+", value)
    result = []
    for token in tokens:
        normalized = token.translate(str.maketrans('ككيى', 'ککیی'))
        result.append(_COMMON_FINGLISH.get(normalized, normalized.translate(_PERSIAN_TO_LATIN)))
    return re.sub(r'[^a-z0-9]+', '_', '_'.join(result).lower()).strip('_')[:92]
