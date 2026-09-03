"""Evidence-first intelligence for people and relationships.

Language models are useful for wording, but they must not be the source of
truth for relationship scores, personality claims, or safety warnings.  This
module builds a tenant-scoped evidence set and derives conservative results
locally, so analysis remains fast and useful when a cloud provider is down or
out of quota.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from .models import (
    Commitment,
    FollowUp,
    Interaction,
    JournalEntry,
    KnowledgeTriple,
    LifeEvent,
    MeetingReflection,
    MemoryFact,
    Node,
    NodeCloseness,
    NodeSafetySetting,
    Relationship,
    RelationshipGoal,
    RelationshipPulse,
    RelationshipStrengthHistory,
)


CATEGORY_LABELS = {
    'interest': 'علاقه',
    'value': 'ارزش',
    'communication': 'سبک ارتباطی',
    'boundary': 'مرز',
    'sensitivity': 'حساسیت',
    'preference': 'ترجیح',
    'life_topic': 'موضوع زندگی',
    'emotion': 'محرک احساسی',
    'other': 'واقعیت ثبت‌شده',
}

SOURCE_LABELS = {
    'profile': 'پروفایل',
    'relationship': 'رابطه ثبت‌شده',
    'strength_history': 'تاریخچه قدرت رابطه',
    'interaction': 'تعامل‌ها',
    'memory': 'حافظه تأییدشده',
    'knowledge': 'دانش رابطه‌ای',
    'pulse': 'نبض رابطه',
    'commitment': 'تعهدها',
    'followup': 'موضوعات باز',
    'reflection': 'بازتاب ملاقات',
    'journal': 'ژورنال',
    'safety': 'مرزهای ایمنی',
    'life_event': 'رویداد زندگی',
    'goal': 'هدف رابطه',
    'closeness': 'دایره نزدیکی',
    'event': 'رویداد مشترک',
}


def is_grounded_profile(data: Any) -> bool:
    """Return true only for stored analyses produced by this evidence engine."""
    return isinstance(data, dict) and data.get('generated_by') == 'evidence_engine_v1'


def grounded_information(node: Node):
    """Find the evidence-engine Information row without assuming row order."""
    return next(
        (item for item in node.informations.all() if is_grounded_profile(item.data)),
        None,
    )


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(round(max(low, min(high, value))))


def _iso(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value) if value else None


def confidence_label(value: int) -> str:
    if value >= 70:
        return 'زیاد'
    if value >= 40:
        return 'متوسط'
    return 'کم'


class _EvidenceCollector:
    def __init__(self):
        self.items: list[dict[str, Any]] = []

    def add(
        self,
        kind: str,
        text: str,
        source: str,
        *,
        source_id: int | str | None = None,
        observed_at: Any = None,
        confidence: int = 100,
        basis: str = 'observed',
    ) -> str | None:
        clean = ' '.join(str(text or '').split())
        if not clean:
            return None
        evidence_id = f'E{len(self.items) + 1}'
        self.items.append({
            'id': evidence_id,
            'kind': kind,
            'text': clean[:500],
            'source': source,
            'source_label': SOURCE_LABELS.get(source, source),
            'source_id': source_id,
            'observed_at': _iso(observed_at),
            'confidence': _clamp(confidence),
            'basis': basis,
        })
        return evidence_id


def _assert_owned(user, node: Node) -> None:
    if not getattr(user, 'is_authenticated', False) or node.owner_id != user.id:
        raise ValueError('این شخص متعلق به کاربر فعلی نیست.')


def _direct_relationships(user, node: Node) -> list[Relationship]:
    root_id = getattr(user, 'root_node_id', None)
    if not root_id or root_id == node.id:
        return []
    return list(Relationship.objects.filter(
        owner=user,
    ).filter(
        Q(source_id=root_id, target=node) | Q(source=node, target_id=root_id)
    ).select_related('source', 'target').order_by('-created_at', '-id'))


def _collect_person_data(user, node: Node) -> dict[str, Any]:
    """Fetch only records owned by ``user`` and safe to use for AI analysis."""
    _assert_owned(user, node)
    today = timezone.localdate()

    relationships = _direct_relationships(user, node)
    interactions = list(Interaction.objects.filter(
        owner=user, node=node,
    ).order_by('-date', '-id')[:240])
    memory_facts = list(MemoryFact.objects.filter(
        owner=user,
        node=node,
        active=True,
        ai_usable=True,
    ).exclude(confidentiality='no_ai').order_by('-confidence', '-observed_at')[:60])
    pulses = list(RelationshipPulse.objects.filter(
        owner=user, node=node,
    ).order_by('-created_at')[:12])
    commitments = list(Commitment.objects.filter(
        owner=user, node=node,
    ).order_by('-created_at')[:80])
    followups = list(FollowUp.objects.filter(
        owner=user, node=node,
    ).order_by('-created_at')[:80])
    reflections = list(MeetingReflection.objects.filter(
        owner=user, node=node,
    ).order_by('-happened_at')[:20])
    journals = []
    if getattr(user, 'ai_journal_enabled', True):
        journals = list(JournalEntry.objects.filter(
            owner=user, mentioned_nodes=node,
        ).distinct().order_by('-created_at')[:20])
    triples = list(KnowledgeTriple.objects.filter(
        owner=user,
        active=True,
    ).filter(
        Q(subject=node) | Q(object_node=node)
    ).select_related('subject', 'object_node').order_by('-created_at')[:40])
    life_events = list(LifeEvent.objects.filter(
        owner=user, node=node,
    ).order_by('-date')[:20])
    goals = list(RelationshipGoal.objects.filter(
        owner=user, node=node,
    ).order_by('status', '-created_at')[:20])
    safety = NodeSafetySetting.objects.filter(owner=user, node=node).first()
    closeness = NodeCloseness.objects.filter(owner=user, node=node).first()

    return {
        'today': today,
        'relationships': relationships,
        'interactions': interactions,
        'memory_facts': memory_facts,
        'pulses': pulses,
        'commitments': commitments,
        'followups': followups,
        'reflections': reflections,
        'journals': journals,
        'triples': triples,
        'life_events': life_events,
        'goals': goals,
        'safety': safety,
        'closeness': closeness,
    }


def _person_evidence(user, node: Node, data: dict[str, Any]) -> list[dict[str, Any]]:
    c = _EvidenceCollector()
    today = data['today']

    profile_parts = [f'نام ثبت‌شده: {node.display_name()}']
    if node.career:
        profile_parts.append(f'شغل: {node.career}')
    if node.birth_day:
        profile_parts.append(f'تولد: {node.birth_day}')
    c.add('identity', ' | '.join(profile_parts), 'profile', source_id=node.id,
          observed_at=node.created_at, confidence=100)

    for rel in data['relationships']:
        text = (
            f'نوع رابطه: {rel.rel or "ثبت نشده"}؛ قدرت ثبت‌شده {rel.strength}/۵؛ '
            f'وضعیت {rel.get_status_display()}'
        )
        if rel.met_at:
            text += f'؛ آشنایی از {rel.met_at}'
        c.add('relationship', text, 'relationship', source_id=rel.id,
              observed_at=rel.created_at, confidence=100)

    closeness = data['closeness']
    if closeness:
        c.add('relationship', f'دایره نزدیکی ثبت‌شده: {closeness.get_tier_display()}',
              'closeness', source_id=closeness.id, confidence=100)

    memory_ids: dict[int, str] = {}
    for fact in data['memory_facts']:
        eid = c.add(
            fact.category,
            f'{CATEGORY_LABELS.get(fact.category, "واقعیت")}: {fact.value}',
            'memory',
            source_id=fact.id,
            observed_at=fact.observed_at,
            confidence=fact.effective_confidence,
        )
        if eid:
            memory_ids[fact.id] = eid

    for triple in data['triples']:
        subject = triple.subject.display_name()
        obj = triple.object_node.display_name() if triple.object_node_id else triple.object_text
        c.add('knowledge', f'{subject} — {triple.predicate} — {obj}', 'knowledge',
              source_id=triple.id, observed_at=triple.created_at,
              confidence=triple.confidence)

    interactions = data['interactions']
    if interactions:
        kind_counts = Counter(item.get_kind_display() for item in interactions)
        feelings = [item.feeling for item in interactions]
        recent_90 = sum(1 for item in interactions if item.date >= today - timedelta(days=90))
        avg = sum(feelings) / len(feelings)
        mood = 'مثبت' if avg > 0.25 else ('منفی' if avg < -0.25 else 'خنثی/ترکیبی')
        kinds = '، '.join(f'{kind}: {count}' for kind, count in kind_counts.most_common(4))
        c.add(
            'interaction_summary',
            f'{len(interactions)} تعامل ثبت شده؛ {recent_90} مورد در ۹۰ روز اخیر؛ '
            f'میانگین حس {mood} ({avg:+.2f})؛ الگو: {kinds}',
            'interaction',
            source_id=node.id,
            observed_at=interactions[0].date,
            confidence=min(98, 58 + len(interactions) * 4),
            basis='derived',
        )
        for item in [item for item in interactions[:16] if item.note][:8]:
            c.add(
                'interaction_note',
                f'{item.get_kind_display()} در {item.date}؛ حس {item.get_feeling_display()}؛ {item.note}',
                'interaction',
                source_id=item.id,
                observed_at=item.date,
                confidence=100,
            )

    if data['pulses']:
        pulse = data['pulses'][0]
        average = (pulse.support + pulse.autonomy + pulse.belonging + pulse.trust + pulse.voice) / 5
        text = (
            f'خودارزیابی اخیر رابطه: حمایت {pulse.support}/۵، آزادی خودبودن {pulse.autonomy}/۵، '
            f'تعلق {pulse.belonging}/۵، اعتماد {pulse.trust}/۵، امکان گفت‌وگوی مخالف {pulse.voice}/۵؛ '
            f'میانگین {average:.1f}/۵'
        )
        if pulse.note:
            text += f'؛ یادداشت: {pulse.note}'
        c.add('relationship_pulse', text, 'pulse', source_id=pulse.id,
              observed_at=pulse.created_at, confidence=100)

    commitments = data['commitments']
    if commitments:
        completed = sum(1 for item in commitments if item.status in {'done', 'completed'})
        overdue = sum(1 for item in commitments if item.status not in {'done', 'completed'}
                      and item.due_date and item.due_date < today)
        c.add('commitment',
              f'{len(commitments)} تعهد ثبت شده؛ {completed} انجام‌شده؛ {overdue} عقب‌افتاده',
              'commitment', source_id=node.id, confidence=100, basis='derived')

    followups = data['followups']
    if followups:
        open_items = [item for item in followups if not item.done]
        overdue = sum(1 for item in open_items if item.due_date and item.due_date < today)
        c.add('followup',
              f'{len(open_items)} موضوع باز و {len(followups) - len(open_items)} موضوع انجام‌شده؛ '
              f'{overdue} مورد عقب‌افتاده',
              'followup', source_id=node.id, confidence=100, basis='derived')
        for item in open_items[:4]:
            c.add('followup', f'موضوع باز: {item.text}' +
                  (f'؛ سررسید {item.due_date}' if item.due_date else ''),
                  'followup', source_id=item.id, observed_at=item.created_at, confidence=100)

    for reflection in data['reflections'][:6]:
        c.add('reflection',
              f'بازتاب ملاقات: {reflection.summary[:300]}؛ حس {reflection.feeling:+d}؛ '
              f'تغییر رابطه {reflection.relationship_change}',
              'reflection', source_id=reflection.id, observed_at=reflection.happened_at,
              confidence=100)

    for entry in data['journals'][:6]:
        c.add('journal', f'یادداشت مرتبط: {entry.text[:320]}', 'journal',
              source_id=entry.id, observed_at=entry.occurred_at or entry.entry_date or entry.created_at,
              confidence=95)

    safety = data['safety']
    if safety:
        if safety.boundaries:
            c.add('boundary', f'مرز ثبت‌شده: {safety.boundaries}', 'safety',
                  source_id=safety.id, observed_at=safety.updated_at, confidence=100)
        if safety.pause_contact_suggestions or (
                safety.no_contact_until and safety.no_contact_until >= today):
            text = 'پیشنهاد تماس برای این رابطه متوقف شده'
            if safety.no_contact_until:
                text += f' تا {safety.no_contact_until}'
            c.add('safety', text, 'safety', source_id=safety.id,
                  observed_at=safety.updated_at, confidence=100)

    for item in data['life_events'][:5]:
        c.add('life_event', f'{item.get_kind_display()}: {item.title or "بدون توضیح"}',
              'life_event', source_id=item.id, observed_at=item.date, confidence=100)

    for goal in data['goals'][:5]:
        c.add('goal', f'هدف رابطه ({goal.get_status_display()}): {goal.text}', 'goal',
              source_id=goal.id, observed_at=goal.created_at, confidence=100)

    return c.items


def build_person_evidence(user, node: Node) -> list[dict[str, Any]]:
    data = _collect_person_data(user, node)
    return _person_evidence(user, node, data)


def _analysis_confidence(data: dict[str, Any], evidence: list[dict[str, Any]]) -> int:
    points = 0
    if data['relationships']:
        points += 14
    interaction_count = len(data['interactions'])
    if interaction_count:
        points += min(30, 8 + interaction_count * 3)
        if (data['interactions'][0].date - data['interactions'][-1].date).days >= 30:
            points += 5
    points += min(22, len(data['memory_facts']) * 5)
    if data['pulses']:
        points += 12
    if data['reflections']:
        points += min(8, len(data['reflections']) * 2)
    if data['journals']:
        points += min(7, len(data['journals']))
    if data['triples']:
        points += min(7, len(data['triples']) * 2)
    source_count = len({item['source'] for item in evidence if item['source'] != 'profile'})
    # Independent source types matter: the same claim supported by a recorded
    # relationship, interactions, approved facts and a pulse is meaningfully
    # stronger than many rows from a single source.
    points += min(10, max(0, source_count - 1) * 3)
    return min(96, points)


def _relationship_score(data: dict[str, Any]) -> tuple[int | None, list[str]]:
    """Weighted score over observed components; missing data is never negative."""
    components: list[tuple[float, float, str]] = []
    today = data['today']

    if data['relationships']:
        rel = data['relationships'][0]
        components.append((rel.strength * 20, 35, f'قدرت ثبت‌شده رابطه {rel.strength}/۵ است'))
        status_values = {'active': 85, 'distant': 45, 'inactive': 25}
        components.append((status_values.get(rel.status, 50), 10,
                           f'وضعیت رابطه «{rel.get_status_display()}» ثبت شده'))

    interactions = data['interactions']
    if interactions:
        days_since = max(0, (today - interactions[0].date).days)
        if days_since <= 7:
            recency = 100
        elif days_since <= 30:
            recency = 82
        elif days_since <= 90:
            recency = 60
        elif days_since <= 180:
            recency = 38
        else:
            recency = 20
        recent_90 = sum(1 for item in interactions if item.date >= today - timedelta(days=90))
        activity = min(100, 30 + recent_90 * 12)
        components.append((recency, 18, f'آخرین تعامل {days_since} روز پیش بوده'))
        components.append((activity, 12, f'{recent_90} تعامل در ۹۰ روز اخیر ثبت شده'))
        avg = sum(item.feeling for item in interactions) / len(interactions)
        components.append(((avg + 1) * 50, 15,
                           f'میانگین حس {len(interactions)} تعامل {avg:+.2f} است'))

    if data['pulses']:
        p = data['pulses'][0]
        average = (p.support + p.autonomy + p.belonging + p.trust + p.voice) / 5
        components.append((average * 20, 25,
                           f'میانگین خودارزیابی اخیر رابطه {average:.1f}/۵ است'))

    if not components:
        return None, ['برای ساخت نمره هنوز رابطه، تعامل یا نبض رابطه ثبت نشده است']
    total_weight = sum(weight for _, weight, _ in components)
    score = _clamp(sum(value * weight for value, weight, _ in components) / total_weight)
    reasons = [reason for _, _, reason in sorted(components, key=lambda item: -item[1])[:4]]
    return score, reasons


def _coverage(data: dict[str, Any], evidence: list[dict[str, Any]], confidence: int) -> dict[str, Any]:
    sources = sorted({item['source'] for item in evidence if item['source'] != 'profile'})
    missing = []
    if not data['relationships']:
        missing.append('نوع و قدرت رابطه')
    if len(data['interactions']) < 3:
        missing.append('حداقل ۳ تعامل')
    if not data['memory_facts']:
        missing.append('واقعیت‌های تأییدشده درباره فرد')
    if not data['pulses']:
        missing.append('خودارزیابی نبض رابطه')
    return {
        'confidence': confidence,
        'confidence_label': confidence_label(confidence),
        'evidence_count': len(evidence),
        'source_count': len(sources),
        'source_types': sources,
        'source_labels': [SOURCE_LABELS.get(source, source) for source in sources],
        'missing': missing,
    }


def analyze_person_relationship(user, node: Node) -> dict[str, Any]:
    """Return a fast, deterministic and explainable person/relationship analysis."""
    data = _collect_person_data(user, node)
    evidence = _person_evidence(user, node, data)
    confidence = _analysis_confidence(data, evidence)
    coverage = _coverage(data, evidence, confidence)
    score, score_reasons = _relationship_score(data)
    name = node.display_name()

    by_category: dict[str, list[MemoryFact]] = {}
    for fact in data['memory_facts']:
        by_category.setdefault(fact.category, []).append(fact)

    personality_facts = [
        fact for category in ('value', 'preference', 'communication', 'interest', 'other')
        for fact in by_category.get(category, [])
    ]
    if len(personality_facts) >= 2:
        values = '؛ '.join(fact.value for fact in personality_facts[:4])
        personality = f'چیزهایی که فعلاً با شواهد درباره {name} می‌دانیم: {values}.'
    elif personality_facts:
        personality = (
            f'فعلاً فقط یک نشانه قابل استفاده درباره {name} ثبت شده: '
            f'{personality_facts[0].value}. برای نتیجه‌گیری شخصیتی کافی نیست.'
        )
    else:
        personality = (
            f'هنوز داده کافی برای برداشت قابل‌اعتماد درباره شخصیت {name} ثبت نشده؛ '
            'رفتار رابطه را می‌شود سنجید، اما شخصیت را نباید حدس زد.'
        )

    communication = by_category.get('communication', [])
    if communication:
        communication_style = '؛ '.join(fact.value for fact in communication[:3])
    elif data['interactions']:
        kinds = Counter(item.get_kind_display() for item in data['interactions'])
        top_kind, top_count = kinds.most_common(1)[0]
        communication_style = (
            f'در داده‌های ثبت‌شده، رایج‌ترین شکل ارتباط {top_kind} '
            f'({top_count} از {len(data["interactions"])} تعامل) بوده؛ این الگوی تماس است، نه حکم شخصیتی.'
        )
    else:
        communication_style = 'برای تشخیص الگوی ارتباطی هنوز تعامل یا ترجیح ارتباطی کافی ثبت نشده است.'

    strengths: list[str] = []
    warnings: list[str] = []
    interactions = data['interactions']
    if len(interactions) >= 3:
        positive = sum(1 for item in interactions if item.feeling > 0)
        negative = sum(1 for item in interactions if item.feeling < 0)
        if positive / len(interactions) >= 0.6:
            strengths.append(f'{positive} مورد از {len(interactions)} تعامل با حس مثبت ثبت شده')
        if negative >= 3 and negative / len(interactions) >= 0.5:
            warnings.append(f'{negative} مورد از {len(interactions)} تعامل با حس منفی ثبت شده')
    if data['pulses']:
        p = data['pulses'][0]
        pulse_values = {
            'اعتماد': p.trust,
            'حمایت': p.support,
            'تعلق': p.belonging,
            'آزادی خودبودن': p.autonomy,
            'امکان مخالفت محترمانه': p.voice,
        }
        strengths.extend(f'{label} در خودارزیابی اخیر {value}/۵ بوده'
                         for label, value in pulse_values.items() if value >= 4)
        warnings.extend(f'{label} در خودارزیابی اخیر {value}/۵ بوده'
                        for label, value in pulse_values.items() if value <= 2)

    safety = data['safety']
    if safety and safety.boundaries:
        warnings.append(f'مرز ثبت‌شده: {safety.boundaries[:180]}')
    if safety and (safety.pause_contact_suggestions or
                   (safety.no_contact_until and safety.no_contact_until >= data['today'])):
        warnings.append('پیشنهاد تماس برای این رابطه متوقف شده و باید رعایت شود')

    overdue_followups = [item for item in data['followups'] if not item.done and item.due_date
                         and item.due_date < data['today']]
    if overdue_followups:
        warnings.append(f'{len(overdue_followups)} موضوع باز از سررسید گذشته است')

    if score is None:
        relationship_quality = 'برای برآورد سلامت رابطه هنوز داده کافی ثبت نشده است.'
    else:
        band = 'پایدار/مثبت' if score >= 70 else ('ترکیبی' if score >= 40 else 'نیازمند توجه')
        relationship_quality = (
            f'برآورد سلامت رابطه از داده‌های ثبت‌شده {score}/۱۰۰ ({band}) است؛ '
            f'اطمینان تحلیل {confidence}/۱۰۰ ({confidence_label(confidence)}) است.'
        )

    rel = data['relationships'][0] if data['relationships'] else None
    if safety and (safety.pause_contact_suggestions or
                   (safety.no_contact_until and safety.no_contact_until >= data['today'])):
        tip = 'فعلاً مرز ثبت‌شده و توقف تماس را رعایت کن؛ هیچ یادآوری تماسی پیشنهاد نمی‌شود.'
    elif overdue_followups:
        tip = f'اول یکی از موضوعات عقب‌افتاده را روشن کن: «{overdue_followups[0].text[:120]}».'
    elif len(interactions) < 3:
        tip = 'برای تحلیل دقیق‌تر، بعد از چند تعامل نوع تماس، تاریخ و حس خودت را ثبت کن.'
    elif warnings:
        tip = 'به موردهای نیازمند توجه نگاه کن و قبل از نتیجه‌گیری، یک نمونه مشخص دیگر ثبت کن.'
    else:
        tip = 'همین روند ثبت تعامل و حس بعد از آن را ادامه بده تا تغییرات رابطه قابل سنجش بماند.'

    return {
        'personality': personality,
        'communication_style': communication_style,
        'values': [fact.value for fact in by_category.get('value', [])[:8]],
        'interests': [fact.value for fact in by_category.get('interest', [])[:8]],
        'strengths': strengths[:4],
        'red_flags': warnings[:4],
        'relationship_quality': relationship_quality,
        'friendship_score': score,
        'score_label': 'برآورد سلامت رابطه از داده‌های ثبت‌شده',
        'score_reasons': score_reasons,
        'suggested_rel_type': rel.rel if rel else '',
        'suggested_strength': rel.strength if rel else None,
        'tip': tip,
        'confidence': confidence,
        'confidence_label': confidence_label(confidence),
        'data_coverage': coverage,
        'evidence': evidence[:24],
        'generated_by': 'evidence_engine_v1',
    }


def evidence_statements(evidence: list[dict[str, Any]], *, limit: int = 12) -> tuple[list[dict[str, Any]], str]:
    """Convert evidence to conservative persona statements without an LLM."""
    useful = [item for item in evidence if item.get('source') != 'profile']
    priorities = {
        'memory': 0,
        'relationship': 1,
        'pulse': 2,
        'interaction': 3,
        'safety': 4,
        'knowledge': 5,
        'reflection': 6,
        'goal': 7,
        'followup': 8,
        'journal': 9,
        'life_event': 10,
    }
    chosen = sorted(
        useful,
        key=lambda item: (priorities.get(item.get('source'), 20), -int(item.get('confidence', 0))),
    )[:limit]
    statements = [{
        'text': item['text'],
        'kind': CATEGORY_LABELS.get(item.get('kind'), item.get('source_label', 'شاهد')),
        'confidence': item.get('confidence', 0),
        'evidence_ids': [item['id']],
        'basis': item.get('basis', 'observed'),
        'source_label': item.get('source_label', ''),
    } for item in chosen]
    if not statements:
        return [], 'هنوز داده‌ای فراتر از مشخصات پایه برای شناخت قابل‌اعتماد ثبت نشده است.'
    avg = _clamp(sum(item['confidence'] for item in statements) / len(statements))
    sources = len({item.get('source') for item in chosen})
    summary = (
        f'این جمع‌بندی بر پایه {len(chosen)} شاهد از {sources} نوع منبع ساخته شده است. '
        f'اطمینان متوسط شواهد {avg}/۱۰۰ ({confidence_label(avg)}) است؛ '
        'برداشت‌های بدون شاهد عمداً حذف شده‌اند.'
    )
    return statements, summary


def build_relationship_evidence(user, rel: Relationship) -> list[dict[str, Any]]:
    if rel.owner_id != user.id:
        raise ValueError('این رابطه متعلق به کاربر فعلی نیست.')
    c = _EvidenceCollector()
    c.add(
        'relationship',
        f'رابطه {rel.source.display_name()} و {rel.target.display_name()}: '
        f'{rel.rel or "بدون برچسب"}؛ قدرت {rel.strength}/۵؛ وضعیت {rel.get_status_display()}',
        'relationship', source_id=rel.id, observed_at=rel.created_at, confidence=100,
    )
    history = list(RelationshipStrengthHistory.objects.filter(
        owner=user, relationship=rel,
    ).order_by('-changed_at')[:20])
    if history:
        oldest, newest = history[-1], history[0]
        c.add('relationship_trend',
              f'قدرت رابطه در {len(history)} ثبت از {oldest.strength}/۵ به {newest.strength}/۵ رسیده',
              'strength_history', source_id=rel.id, observed_at=newest.changed_at,
              confidence=100, basis='derived')

    root_id = getattr(user, 'root_node_id', None)
    other = None
    if root_id == rel.source_id:
        other = rel.target
    elif root_id == rel.target_id:
        other = rel.source
    if other is not None:
        person_items = build_person_evidence(user, other)
        for item in person_items:
            if item['source'] == 'profile':
                continue
            c.add(item['kind'], item['text'], item['source'], source_id=item['source_id'],
                  observed_at=item['observed_at'], confidence=item['confidence'],
                  basis=item['basis'])
    return c.items


def chat_relationship_context(user, node: Node) -> str:
    """Compact, model-safe context for chat questions about a named person."""
    result = analyze_person_relationship(user, node)
    evidence = result['evidence'][:10]
    lines = [
        f'تحلیل محلی رابطه با {node.display_name()}:',
        result['relationship_quality'],
        f'اطمینان: {result["confidence"]}/۱۰۰ ({result["confidence_label"]})',
        'قانون: فقط از شواهد زیر نتیجه بگیر؛ نبود داده را صریح بگو.',
    ]
    lines.extend(f'[{item["id"]}] {item["text"]}' for item in evidence)
    return '\n'.join(lines)
