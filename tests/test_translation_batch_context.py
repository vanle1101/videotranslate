"""Target ownership and evidence-only terminology identities."""
from copy import deepcopy

import pytest

from core.video_intelligence import VideoIntelligence
from core.translation_review import AutomaticTranslationReviewer, _LocalAudioEvidence


@pytest.mark.parametrize("speech_count,screen_count,limit", [(0, 24, 8), (17, 25, 8), (9, 19, 4), (0, 0, 4)])
def test_eight_total_translation_targets_not_eight_plus_twenty_four(speech_count, screen_count, limit):
    speech = [{"id": i} for i in range(speech_count)]
    screens = [{"id": f"ocr{i}"} for i in range(screen_count)]
    batches = VideoIntelligence._bounded_translation_targets(speech, screens, limit)
    assert all(len(s) + len(o) <= 8 for s, o in batches)
    assert [row for s, _ in batches for row in s] == speech
    assert [row for _, o in batches for row in o] == screens


def test_review_and_local_audio_batches_share_eight_target_ceiling():
    assert AutomaticTranslationReviewer.BATCH_SIZE == 8
    assert _LocalAudioEvidence.BATCH_SIZE == 8
    assert AutomaticTranslationReviewer.ADDRESS_BATCH_SIZE <= 8


def test_terminology_version_is_deterministic_and_changes_with_source_or_mapping():
    source = [{"id": 1, "start": 1., "end": 2., "text_zh": "名字", "revision": 0},
              {"id": 2, "start": 2., "end": 3., "text_zh": "你好"}]
    glossary = {"terms": [{"src": "名字", "tgt": "Tên", "note": "supplied"},
                           {"src": "你好", "tgt": "Chào", "note": ""}]}
    original = deepcopy((source, glossary))
    first = VideoIntelligence._versioned_terminology_context(source, glossary)
    second = VideoIntelligence._versioned_terminology_context(list(reversed(source)),
        {"terms": list(reversed(glossary["terms"]))})
    assert first == second
    assert (source, glossary) == original
    glossary["terms"][0]["tgt"] = "Tên khác"
    assert VideoIntelligence._versioned_terminology_context(source, glossary)["version"] != first["version"]
    source[0]["revision"] = 1
    assert VideoIntelligence._versioned_terminology_context(source, original[1])["version"] != first["version"]


def test_no_glossary_does_not_invent_names_or_relationships_from_draft():
    rows = [{"id": 1, "start": 0, "end": 1, "text_zh": "你", "final_vi": "Mẹ ơi!",
             "verification": {"status": "unresolved"}}]
    context = VideoIntelligence._versioned_terminology_context(rows)
    assert context["terms"] == []
    assert context["provenance"] == "no-supplied-table"
    assert "Mẹ" not in str(context)
