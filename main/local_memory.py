"""Dependency-free Persian lexical retrieval for private relationship memory.

This ranks already stored, owner-scoped evidence before the chat model sees it.
It is intentionally local, fast, and not a source of psychological truth.
"""

from difflib import SequenceMatcher


STOPWORDS = {
    'من', 'تو', 'او', 'ما', 'شما', 'با', 'به', 'از', 'که', 'را', 'رو', 'در',
    'این', 'اون', 'آن', 'یه', 'یک', 'و', 'یا', 'کی', 'چی', 'چه', 'کجا',
    'آخرین', 'بار', 'بود', 'شد', 'کرد', 'های', 'هست', 'برای', 'چطور',
}

TERM_ALIASES = {
    'علایق': 'علاقه', 'علاقهها': 'علاقه', 'دوستداشتن': 'علاقه',
    'احوال': 'حال', 'روحیه': 'حال', 'احساس': 'حال', 'حس': 'حال',
    'تماس': 'ارتباط', 'گفتگو': 'صحبت', 'ملاقات': 'دیدار', 'قرار': 'دیدار',
}


def normalize(text):
    return (str(text or '').replace('ي', 'ی').replace('ك', 'ک')
            .replace('‌', ' ').replace('ۀ', 'ه').lower())


def _stem(token):
    token = token.strip('؟?!،؛:,.()[]{}"\'')
    for suffix in ('ترین', 'های', 'ها', 'تر', 'ام', 'ات', 'اش', 'ی'):
        if len(token) >= len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def query_terms(query):
    terms = set()
    for raw in normalize(query).split():
        token = raw.strip('؟?!،؛:,.()[]{}"\'')
        if len(token) < 2 or token in STOPWORDS:
            continue
        token = TERM_ALIASES.get(token, token)
        terms.add(token)
        stem = _stem(token)
        if len(stem) >= 2:
            terms.add(stem)
    return terms


def score_text(text, terms):
    """Score exact, stemmed and close Persian token matches locally."""
    tokens = [_stem(token) for token in normalize(text).split()]
    if not terms or not tokens:
        return 0
    score = 0
    for term in terms:
        stem = _stem(term)
        if term in tokens or stem in tokens:
            score += 3
            continue
        best = max((SequenceMatcher(None, stem, token).ratio() for token in tokens), default=0)
        if len(stem) >= 3 and best >= 0.78:
            score += 1
    return score
