"""Explicit glossary validation/persistence; never derives roles from AI text."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from core.streaming.glossary import build_glossary, validate_glossary, glossary_snapshot, set_shared_glossary
from core.streaming import session_store as store
from core.streaming.pipeline import active_streaming_sessions
from test_session_store import persisted


def term(src="小林", tgt="Tiểu Lâm", note="Tên riêng"):
    return {"src": src, "tgt": tgt, "note": note,
            "provenance": {"kind": "user_supplied", "reference": "user-edit-1"}}


def test_version_stable_order_independent_and_sensitive_to_every_supplied_field():
    a, b = term(), term("회사", "công ty", "Thuật ngữ tiếng Hàn")
    original = build_glossary([a, b])
    assert original == build_glossary([b, a])
    for field, value in [("src", "林"), ("tgt", "Lâm"), ("note", "Tên đã cung cấp")]:
        changed = deepcopy(a)
        changed[field] = value
        assert build_glossary([changed, b])["version"] != original["version"]
    changed = deepcopy(a)
    changed["provenance"]["reference"] = "user-edit-2"
    assert build_glossary([changed, b])["version"] != original["version"]


def test_snapshot_is_detached_and_empty_default_does_not_infer_relationships():
    session = SimpleNamespace(rolling_context=[{"zh": "我", "vi": "chị"}], translator=SimpleNamespace())
    assert glossary_snapshot(session)["terms"] == []
    result = set_shared_glossary(session, [term()])
    result["terms"][0]["tgt"] = "caller mutation"
    assert glossary_snapshot(session)["terms"][0]["tgt"] == "Tiểu Lâm"
    assert session.translator.glossary == glossary_snapshot(session)
    session.translator.glossary["terms"][0]["tgt"] = "adapter mutation"
    assert glossary_snapshot(session)["terms"][0]["tgt"] == "Tiểu Lâm"
    snapshot = glossary_snapshot(session)
    set_shared_glossary(session, [term(tgt="Lâm")], expected_version=snapshot["version"])
    assert session.translator.glossary == glossary_snapshot(session)
    with pytest.raises(ValueError, match="đã thay đổi"):
        set_shared_glossary(session, [], expected_version=snapshot["version"])


def test_pipeline_translation_review_and_pacing_share_persisted_version(persisted):
    from core.translation_context import format_dialogue_context
    from core.video_intelligence import VideoIntelligence
    expected = set_shared_glossary(persisted, [term()])
    persisted.persist()
    active_streaming_sessions.pop(persisted.task_id, None)
    restored = store.restore_saved_session(persisted.task_id)
    assert restored.translator.glossary == expected
    row = next(iter(restored.segments.values()))
    context = restored._dialogue_context_before(row)
    formatted = json.loads(format_dialogue_context(context))
    assert formatted and all(r['terminology_context'] == expected for r in formatted)
    versioned = VideoIntelligence._versioned_terminology_context(formatted, expected)
    assert versioned['shared_version'] == expected['version']
    changed = deepcopy(expected['terms'])
    changed[0]['provenance']['reference'] = 'new-user-confirmation'
    newer = build_glossary(changed)
    assert VideoIntelligence._versioned_terminology_context(formatted, newer)['version'] != versioned['version']


def test_full_visual_checkpoint_invalidates_when_glossary_changes(persisted):
    from core.video_intelligence import VideoIntelligence
    processor = VideoIntelligence()
    rows = list(persisted.segments.values())
    processor.glossary = build_glossary([term()])
    first = processor._checkpoint_identity(persisted.video_path, rows, persisted.total_duration)
    processor.glossary = build_glossary([term(tgt='Lâm')])
    second = processor._checkpoint_identity(persisted.video_path, rows, persisted.total_duration)
    assert first and second and first['key'] != second['key']


@pytest.mark.parametrize("field,value", [("src", ""), ("src", None), ("tgt", " "),
    ("note", "x" * 1001), ("src", "bad\x00"), ("src", "x" * 201),
    ("provenance", {"kind": "ai_draft", "reference": "unverified"}),
    ("provenance", {"kind": "user_supplied", "reference": ""}),
    ("provenance", {"kind": "user_supplied", "reference": "x", "verified": True}),
    ("speaker_relation", "elder_sister")])
def test_invalid_or_inferred_metadata_rejected(field, value):
    value_term = term()
    value_term[field] = value
    with pytest.raises(ValueError):
        build_glossary([value_term])


def test_duplicate_normalized_source_and_tampered_version_rejected():
    with pytest.raises(ValueError):
        build_glossary([term("café"), term("cafe\u0301")])
    value = build_glossary([term()])
    value["terms"][0]["tgt"] = "tampered"
    with pytest.raises(ValueError, match="không khớp"):
        validate_glossary(value)


@pytest.mark.parametrize("compressed", [False, True])
def test_plain_compressed_checkpoint_preserves_exact_glossary(persisted, monkeypatch, compressed):
    monkeypatch.setattr(store, "COMPRESS_AFTER_BYTES", 1 if compressed else 100_000_000)
    expected = set_shared_glossary(persisted, [term()])
    persisted.persist()
    record = json.loads(store._project_path(persisted.task_id).read_text(encoding="utf-8"))
    assert (record.get("storage") == "zlib-project-1") is compressed
    for _ in range(2):
        restored = store.restore_saved_session(persisted.task_id)
        assert glossary_snapshot(restored) == expected
        restored.persist()
        active_streaming_sessions.pop(persisted.task_id)


def test_legacy_project_restores_empty_table_without_editing_manifest(persisted):
    record = store._read(persisted.task_id)
    record["session"].pop("shared_glossary")
    path = store._project_path(persisted.task_id)
    path.write_text(store._encode_project(record), encoding="utf-8")
    before = path.read_bytes()
    restored = store.restore_saved_session(persisted.task_id)
    assert glossary_snapshot(restored) == build_glossary()
    assert path.read_bytes() == before


def test_invalid_table_cannot_replace_saved_project(persisted):
    path = store._project_path(persisted.task_id)
    before = path.read_bytes()
    persisted.shared_glossary = build_glossary([term()])
    persisted.shared_glossary["terms"][0]["provenance"]["kind"] = "ai_draft"
    with pytest.raises(ValueError):
        store.save_session(persisted)
    assert path.read_bytes() == before
