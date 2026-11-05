import math
import pytest

from scripts.publish_multilingual_recovery import validate_input, evidence_for, srt_text


def row(**changes):
    return {"id": 0, "start": 1., "end": 3., "text_zh": "우리 친구잖아?",
            "text_vi": "Chúng ta là bạn mà?", "source_language": "ko", **changes}


def test_coverage_is_not_inferred_from_final_speech():
    result = validate_input({"segments": [row()]}, original_duration=20.)
    assert result["source_prepared_seconds"] == 0
    assert result["duration"] == 20


@pytest.mark.parametrize("changes", [{"end": math.nan}, {"start": -1}, {"end": 21},
                                    {"source_language": ""}, {"source_supported": "true"},
                                    {"text_vi": "우리 친구잖아?"}])
def test_invalid_evidence_or_translation_is_rejected(changes):
    with pytest.raises(ValueError):
        validate_input({"segments": [row(**changes)]}, original_duration=20.)


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        validate_input({"segments": [row(), row(start=4., end=5.)]}, original_duration=20.)


def test_semantics_cannot_certify_source():
    proof = evidence_for(row(multilingual_review={"semantic_verified": True}))
    assert proof["semantic_verified"]
    assert not proof["source_supported"]
    assert proof["status"] == "unresolved"


def test_both_proofs_and_real_translation_required():
    accepted = row(source_supported=True, multilingual_review={"semantic_verified": True})
    assert evidence_for(accepted)["status"] == "verified"
    assert evidence_for({**accepted, "text_vi": ""})["status"] == "unresolved"


def test_partial_srt_retains_measured_timing_and_omits_missing_translation():
    text = srt_text([row(), row(id=1, start=5., end=6., text_vi="")])
    assert text == "1\n00:00:01,000 --> 00:00:03,000\nChúng ta là bạn mà?\n"


@pytest.mark.parametrize("source_method", [None, "audio", "visible-chinese-subtitle-transcription"])
def test_publish_saves_new_manifest_without_touching_original(tmp_path, monkeypatch, source_method):
    import argparse
    import asyncio
    import json
    from config import settings
    from core.streaming import session_store
    from scripts import publish_multilingual_recovery as publisher

    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(publisher, "ROOT", tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not decoded during publication")
    original_path = tmp_path / "original.json"
    original_path.write_text("original manual edits", encoding="utf8")
    original = {"session": {"total_duration": 20., "video_path": str(source),
        "voice": "vi-VN-HoaiMyNeural", "tts_engine_name": "edge-tts", "asr_engine_name": "faster-whisper"}}
    monkeypatch.setattr(session_store, "_read", lambda task_id: original)
    input_path = tmp_path / "input.json"
    source_row = row(source_method=source_method,
                     issues=["subtitle_timing_not_independent_audio_timing"])
    input_path.write_text(json.dumps({"segments": [source_row], "source_prepared_seconds": 4.}), encoding="utf8")
    asyncio.run(publisher.publish(argparse.Namespace(input=input_path, task_id="new-recovery",
        original_task="original", dry_run=False, synthesize=False)))
    saved = json.loads((tmp_path / "workspace/projects/new-recovery.json").read_text(encoding="utf8"))
    assert saved["state"] == "FAILED"
    assert saved["session"]["_source_prepared_seconds"] == 4.
    assert not saved["segments"][0]["verification"]["source_supported"]
    assert saved["segments"][0]["final_vi"] == row()["text_vi"]
    assert saved["segments"][0]["source_method"] == (source_method or "unknown")
    assert saved["segments"][0]["source_language"] == "ko"
    assert source_row["issues"][0] in saved["segments"][0]["verification"]["reason"]
    assert original_path.read_text(encoding="utf8") == "original manual edits"
