from copy import deepcopy

from test_session_store import persisted
from core.streaming.session_store import restore_saved_session, _clean_segment_field


def evidence():
    return {"status": "unresolved", "source_supported": False,
        "automatic_language": "ko", "automatic_probability": .72,
        "selected_language": "ko", "conditional_selected": False,
        "independent": [{"language": "zh", "text": "你好", "tokens": [{"text": "你", "at": .2}],
                         "crop_start": 0., "crop_end": 2.}],
        "alternatives": [{"language": "ko", "kind": "automatic", "rows": [
            {"text": "안녕", "start": .1, "end": 1., "words": [{"word": "안녕", "start": .1, "end": 1.}]}]}],
        "reason": "independent_language_disagreement", "crop_start": 0., "crop_end": 2.,
        "interval_start": 0., "timing_origin": "source_interval"}


def test_disagreement_evidence_survives_actual_save_and_restore(persisted):
    proof = evidence()
    persisted.segments[0].source_language_review = deepcopy(proof)
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].source_language_review == proof
    assert restored.segments[0].source_language_review["source_supported"] is False


def test_language_evidence_has_own_bounded_fields_without_widening_generic_metadata():
    proof = evidence()
    proof["unexpected"] = "not a source field"
    cleaned = _clean_segment_field("source_language_review", proof)
    assert cleaned == evidence()
