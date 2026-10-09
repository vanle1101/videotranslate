import copy

import pytest

from core.dialogue_segments import split_dialogue_segments
from core.semantic_segments import (SEMANTIC_TRANSLATION_POLICY, build_semantic_units,
                                    semantic_context)


def source(identity, text, start, end, **metadata):
    return {"id": identity, "text_zh": text, "start": start, "end": end, **metadata}


def audio_proof(speaker="voice-a", scope="video-17", confidence=.95):
    return {"speaker_id": speaker, "speaker_evidence": {"speaker_id": speaker,
        "verified": True, "method": "audio_diarization", "confidence": confidence,
        "model": "actual-diarization-model", "scope_id": scope}}


def confirmation(speaker="voice-a", scope="video-17"):
    return {"speaker_id": speaker, "speaker_evidence": {"speaker_id": speaker,
        "verified": True, "method": "user_confirmation", "scope_id": scope,
        "confirmation_id": "recorded-user-action"}}


def fragments(**metadata):
    return [source(112, "我真的不是回来", 10, 11.25, **metadata),
            source(113, "跟你开玩笑的", 11.3, 12.8, **metadata)]


def test_actual_incomplete_purpose_clause_gets_joint_context_without_claiming_same_voice():
    rows = fragments()
    before = copy.deepcopy(rows)
    unit, = build_semantic_units(rows)
    assert unit["source_ids"] == [112, 113]
    assert [part["text_zh"] for part in unit["source_parts"]] == ["我真的不是回来", "跟你开玩笑的"]
    assert unit["kind"] == "contextual_exchange"
    assert "text_zh" not in unit  # No merged sentence asserted as source evidence.
    assert not unit["same_speaker_confirmed"] and not unit["can_combine_dubbing"]
    assert rows == before
    assert [(part["start"], part["end"]) for part in unit["source_parts"]] == [(10, 11.25), (11.3, 12.8)]


def test_audio_grounded_same_voice_can_form_complete_translation_unit():
    unit, = build_semantic_units(fragments(**audio_proof()))
    assert unit["kind"] == "utterance" and unit["can_combine_dubbing"]
    assert unit["text_zh"] == "我真的不是回来跟你开玩笑的"
    assert unit["boundaries"][0]["evidence"]["scope_id"] == "video-17"


def test_one_recorded_user_confirmation_can_link_same_speaker_rows():
    unit, = build_semantic_units(fragments(**confirmation()))
    assert unit["can_combine_dubbing"]


@pytest.mark.parametrize("metadata", [
    {"speaker_id": "A"},
    {"speaker_id": "A", "speaker_verified": True},
    {"source_asr_row_id": 44},
    {"speaker_id": "A", "speaker_evidence": {"speaker_id": "A", "method": "ocr", "verified": True}},
    {"speaker_id": "A", "speaker_evidence": {"speaker_id": "A", "method": "text_inference", "verified": True}},
    {"speaker_id": "A", "speaker_evidence": {"speaker_id": "A", "method": "audio_diarization", "verified": True,
                                                  "confidence": .98}},
    {"speaker_id": "A", "speaker_evidence": {"speaker_id": "A", "method": "user_confirmation", "verified": True,
                                                  "scope_id": "video"}},
    audio_proof(confidence=.6),
])
def test_label_asr_parent_ocr_or_unmeasured_confidence_never_proves_continuity(metadata):
    unit, = build_semantic_units(fragments(**metadata))
    assert unit["kind"] == "contextual_exchange" and not unit["can_combine_dubbing"]


def test_chunk_local_diarizer_labels_do_not_alias_across_identity_scopes():
    rows = [source(1, "第一部分", 0, 1, **audio_proof(scope="chunk-1")),
            source(2, "第二部分", 1.05, 2, **audio_proof(scope="chunk-2"))]
    unit, = build_semantic_units(rows)
    assert not unit["same_speaker_confirmed"]


def test_known_different_speakers_never_merge_even_without_punctuation_or_pause():
    rows = [source(1, "我十九", 0, 1, **audio_proof("A")),
            source(2, "我十八", 1.05, 2, **audio_proof("B"))]
    units = build_semantic_units(rows)
    assert [unit["source_ids"] for unit in units] == [[1], [2]]
    assert all(not unit["can_combine_dubbing"] for unit in units)


def test_conflicting_speaker_aliases_cannot_supply_merge_permission():
    rows = fragments(**audio_proof())
    rows[0]["speaker"] = "another-voice"
    unit, = build_semantic_units(rows)
    assert not unit["same_speaker_confirmed"]


def test_utterance_proof_cannot_override_conflicting_speaker_metadata():
    rows = fragments(**audio_proof())
    rows[0]["speaker"] = "another-voice"
    for row in rows:
        row.update(utterance_id="utterance-4", utterance_evidence={
            "utterance_id": "utterance-4", "verified": True, "method": "user_confirmation",
            "scope_id": "video-17", "confirmation_id": "recorded-user-action"})
    unit, = build_semantic_units(rows)
    assert not unit["can_combine_dubbing"]


def test_nonfinite_evidence_is_not_serialized_as_valid_provider_json():
    with pytest.raises(ValueError):
        build_semantic_units(fragments(**audio_proof(confidence=float("nan"))))


def test_overlapping_speech_rows_never_form_combined_audio_unit():
    rows = [source(1, "先说", 0, 1.2, **audio_proof()),
            source(2, "后说", 1, 2, **audio_proof())]
    assert len(build_semantic_units(rows)) == 2


@pytest.mark.parametrize("metadata", [
    {"manual_edit": True}, {"user_edited": True},
    {"verification": {"status": "manual"}, "final_vi": "Lời tự sửa", "revision": 3},
])
def test_manual_edit_is_preserved_and_blocks_automatic_audio_combination(metadata):
    rows = fragments(**audio_proof())
    rows[0].update(metadata)
    before = copy.deepcopy(rows)
    unit, = build_semantic_units(rows)
    assert unit["same_speaker_confirmed"] and unit["has_manual_edit"]
    assert not unit["can_combine_dubbing"]
    assert rows == before


def test_timestamp_order_is_used_without_renumbering_or_mutating_input():
    rows = fragments()
    unit, = build_semantic_units(reversed(rows))
    assert unit["source_ids"] == [112, 113]
    assert [row["id"] for row in rows] == [112, 113]


def test_complete_sentence_short_reply_and_long_pause_keep_own_units():
    rows = [source(1, "你回来吗？", 0, 1), source(2, "嗯", 1.1, 1.3),
            source(3, "我知道了。", 2.3, 3.5)]
    assert [unit["source_ids"] for unit in build_semantic_units(rows)] == [[1], [2], [3]]


def test_quoted_sentence_ending_stops_grouping_but_ellipsis_keeps_context():
    assert len(build_semantic_units([source(1, "“你好。”", 0, 1), source(2, "然后", 1.1, 2)])) == 2
    unit, = build_semantic_units([source(1, "并不是…", 0, 1), source(2, "这个意思", 1.1, 2)])
    assert unit["source_ids"] == [1, 2]


def test_grouping_is_bounded_and_loses_no_row_at_limits():
    rows = [source(index, "片段", index, index + .9) for index in range(20)]
    units = build_semantic_units(rows, max_rows=4, max_duration=3)
    assert [identity for unit in units for identity in unit["source_ids"]] == list(range(20))
    assert all(len(unit["source_ids"]) <= 4 and unit["end"] - unit["start"] <= 3 for unit in units)


def test_stable_unit_id_and_source_sensitive_cache_hash():
    rows = fragments()
    unit, = build_semantic_units(rows)
    rows[0]["text_zh"] = "我真的不是回来这里"
    edited, = build_semantic_units(rows)
    assert edited["unit_id"] == unit["unit_id"]
    assert edited["input_hash"] != unit["input_hash"]
    configured, = build_semantic_units(rows, max_gap=.6)
    assert configured["input_hash"] != edited["input_hash"]


def test_manual_revision_invalidates_hash_but_not_unit_identity():
    rows = fragments(**confirmation())
    rows[0].update(final_vi="Lời đầu", revision=1, verification={"status": "manual"})
    first, = build_semantic_units(rows)
    rows[0].update(final_vi="Lời sửa", revision=2)
    second, = build_semantic_units(rows)
    assert first["unit_id"] == second["unit_id"] and first["input_hash"] != second["input_hash"]
    assert "Lời sửa" not in str(second)  # Draft does not become Chinese source evidence.


def test_focus_batch_can_see_both_sides_of_fragment_without_emitting_extra_ids():
    rows = fragments()
    context = semantic_context(rows, rows[:1])
    assert not context["missing_focus_ids"]
    assert context["units"][0]["source_ids"] == [112, 113]
    assert "Chỉ xuất đúng ID được yêu cầu" in SEMANTIC_TRANSLATION_POLICY


def test_context_budget_reports_missing_focus_and_never_silently_truncates_source():
    rows = fragments()
    context = semantic_context(rows, rows[:1], max_chars=1)
    assert context["units"] == []
    assert context["truncated"] and context["missing_focus_ids"] == [112]


def test_context_selects_nearest_units_at_large_video_boundary():
    rows = [source(index, "完整句子。", index, index + .9) for index in range(200)]
    context = semantic_context(rows, [rows[150]], max_units=3)
    assert [unit["source_ids"] for unit in context["units"]] == [[149], [150], [151]]
    assert context["truncated"] and not context["missing_focus_ids"]


@pytest.mark.parametrize("row", [
    source(1, "", 0, 1), source(1, "词", float("nan"), 1), source(1, "词", 1, 1),
    source(True, "词", 0, 1), {"text_zh": "词", "start": 0, "end": 1},
])
def test_invalid_source_never_produces_apparently_valid_unit(row):
    with pytest.raises(ValueError):
        build_semantic_units([row])


def test_duplicate_ids_cannot_duplicate_meaning_or_downstream_audio():
    with pytest.raises(ValueError, match="Duplicate"):
        build_semantic_units([source(1, "第一句", 0, 1), source(1, "第二句", 1.1, 2)])


def test_splitter_carries_measured_parent_without_claiming_single_speaker():
    row = source(71, "甲乙", 0, 2, words=[
        {"word": "甲", "start": .1, "end": .5},
        {"word": "乙", "start": 1, "end": 1.5}])
    rows = split_dialogue_segments([row])
    assert [part["source_asr_row_id"] for part in rows] == [71, 71]
    assert [part["source_piece_index"] for part in rows] == [0, 1]
    assert [part["source_piece_count"] for part in rows] == [2, 2]
    assert [(part["source_asr_start"], part["source_asr_end"]) for part in rows] == [(0, 2), (0, 2)]
    unit, = build_semantic_units(rows)
    assert not unit["same_speaker_confirmed"]


def test_splitter_does_not_mislabel_word_speaker_with_old_row_label_and_proof():
    row = source(71, "甲乙", 0, 2, **audio_proof("A"), speaker="A", words=[
        {"word": "甲", "start": .1, "end": .5, "speaker": "A"},
        {"word": "乙", "start": .6, "end": 1.5, "speaker": "B"}])
    rows = split_dialogue_segments([row])
    assert [(part["speaker"], part["speaker_id"]) for part in rows] == [("A", "A"), ("B", "B")]
    assert rows[0]["speaker_evidence"]["speaker_id"] == "A"
    assert "speaker_evidence" not in rows[1]
    assert len(build_semantic_units(rows)) == 2
