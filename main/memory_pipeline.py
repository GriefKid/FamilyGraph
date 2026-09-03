"""Central, consent-aware entry point for feeding text into relationship memory.

Every text source goes through the same extraction code so suggestions keep a
source, source id, confidence and an auditable trace.  This module deliberately
does not auto-approve facts or expose data across owners.
"""

from .extraction import extract_text


MAX_OBSERVATION_CHARS = 4000


def capture_text(owner, text, source, source_id=None, *, node=None):
    """Extract suggestions from an owner-scoped text observation.

    ``node`` is optional context for short notes that do not name their
    subject explicitly.  The original text remains in the trace while the
    contextual prefix helps the conservative extractor link the observation.
    """
    if not owner or not getattr(owner, 'ai_extraction_enabled', True):
        return []
    text = str(text or '').strip()[:MAX_OBSERVATION_CHARS]
    if not text:
        return []
    source = str(source or 'unknown').strip()[:20] or 'unknown'
    if source_id is not None:
        try:
            source_id = int(source_id)
        except (TypeError, ValueError):
            source_id = None
        if source_id is not None and source_id < 1:
            source_id = None
    if node is not None:
        if node.owner_id not in (None, owner.id):
            return []
        text = f'{node.display_name()}: {text}'[:MAX_OBSERVATION_CHARS]
    return extract_text(owner, text, source, source_id)


def capture_node_note(owner, node, text, source='interaction', source_id=None):
    """Capture a short note whose subject is already known by the app."""
    return capture_text(owner, text, source, source_id, node=node)
