"""Deterministic, privacy-safe quality evaluation for relationship extraction."""
import json
import math
import time
from pathlib import Path

from .extraction import _collect, _normalise


SUITE_PATH = Path(__file__).with_name('evals') / 'persian_extraction_v1.json'
ENGINE_VERSION = 'deterministic-extractor-v2'


def load_persian_extraction_suite(path=None):
    with Path(path or SUITE_PATH).open(encoding='utf-8') as stream:
        return json.load(stream)


def _synthetic_person_resolver(known_people):
    known = {_normalise(name).casefold(): index + 1000 for index, name in enumerate(known_people or [])}
    blocked = {'من', 'امروز', 'دیروز', 'فردا', 'اون', 'او', 'ایشون'}

    def resolve(raw):
        cleaned = raw.strip(' ،,.!؟')
        if not cleaned or cleaned in blocked:
            return None
        return {
            'name_raw': cleaned,
            'existing_node_id': known.get(_normalise(cleaned).casefold()),
            'candidate_node_ids': [],
        }

    return resolve


def _matches(expected, actual):
    if expected.get('kind') != actual.get('kind'):
        return False
    payload = actual.get('payload') or {}
    return all(payload.get(key) == value for key, value in (expected.get('fields') or {}).items())


def run_persian_extraction_eval(path=None):
    """Run the checked-in Persian suite without database or network access.

    The returned report contains synthetic case identifiers and aggregate counts,
    never production input text.
    """
    suite = load_persian_extraction_suite(path)
    started = time.monotonic()
    totals = {'tp': 0, 'fp': 0, 'fn': 0}
    by_kind = {}
    failures = []
    passed = 0

    for case in suite.get('cases', []):
        resolver = _synthetic_person_resolver(case.get('known_people'))
        actual = [{'kind': kind, 'payload': payload}
                  for kind, payload in _collect(None, case.get('text', ''), resolver)]
        expected = list(case.get('expected') or [])
        unmatched = set(range(len(actual)))
        missing = []

        for wanted in expected:
            found_index = next((index for index in unmatched if _matches(wanted, actual[index])), None)
            kind = wanted.get('kind', 'unknown')
            kind_totals = by_kind.setdefault(kind, {'tp': 0, 'fp': 0, 'fn': 0})
            if found_index is None:
                totals['fn'] += 1
                kind_totals['fn'] += 1
                missing.append(kind)
            else:
                unmatched.remove(found_index)
                totals['tp'] += 1
                kind_totals['tp'] += 1

        allowed_extra = set(case.get('allowed_extra_kinds') or [])
        unexpected = []
        for index in unmatched:
            kind = actual[index].get('kind', 'unknown')
            if kind in allowed_extra:
                continue
            totals['fp'] += 1
            by_kind.setdefault(kind, {'tp': 0, 'fp': 0, 'fn': 0})['fp'] += 1
            unexpected.append(kind)

        forbidden = set(case.get('forbidden_kinds') or [])
        forbidden_found = sorted({row['kind'] for row in actual if row['kind'] in forbidden})
        if missing or unexpected or forbidden_found:
            failures.append({
                'case_id': case.get('id', 'unnamed'),
                'missing_kinds': sorted(missing),
                'unexpected_kinds': sorted(unexpected),
                'forbidden_kinds': forbidden_found,
            })
        else:
            passed += 1

    precision_denominator = totals['tp'] + totals['fp']
    recall_denominator = totals['tp'] + totals['fn']
    precision = 100 * totals['tp'] / precision_denominator if precision_denominator else 100.0
    recall = 100 * totals['tp'] / recall_denominator if recall_denominator else 100.0
    total_cases = len(suite.get('cases', []))
    per_kind = {}
    for kind, values in sorted(by_kind.items()):
        p_denominator = values['tp'] + values['fp']
        r_denominator = values['tp'] + values['fn']
        per_kind[kind] = {
            **values,
            'precision': round(100 * values['tp'] / p_denominator, 2) if p_denominator else 100.0,
            'recall': round(100 * values['tp'] / r_denominator, 2) if r_denominator else 100.0,
        }
    return {
        'suite_version': suite.get('suite_version', 'unknown'),
        'engine_version': ENGINE_VERSION,
        'total_cases': total_cases,
        'passed_cases': passed,
        'pass_rate': round(100 * passed / total_cases, 2) if total_cases else 100.0,
        'precision': round(precision, 2),
        'recall': round(recall, 2),
        'duration_ms': round((time.monotonic() - started) * 1000),
        'totals': totals,
        'per_kind': per_kind,
        'failures': failures,
    }


def percentile(values, percent):
    """Return a nearest-rank percentile for a small operational sample."""
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percent / 100 * len(ordered)) - 1))
    return ordered[index]
