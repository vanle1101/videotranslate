"""Persistence boundaries: corruption and missing media never become success."""
import json
import asyncio
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest

from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, active_streaming_sessions
from core.streaming.session_store import list_saved_sessions, restore_saved_session, _project_path


def timing_issue():
    return {"code": "TIMING_CONFLICT", "required_seconds": 1.59, "available_seconds": 1.25,
        "max_speed": 1.15, "remedy": "Chọn cách diễn đạt/giọng phù hợp; không cắt từ hoặc chồng tiếng."}


def test_measured_timing_conflict_and_real_failure_reason_survive_repeated_restore(persisted):
    segment = persisted.segments[0]
    segment.status, segment.failed_stage = "FAILED", "ALIGNING"
    segment.error = "Giọng còn quá dài sau khi tăng tốc tự nhiên; cần giữ nguyên nghĩa."
    segment.timing_issue = timing_issue()
    persisted.persist()
    for _ in range(2):
        restored = restore_saved_session(persisted.task_id)
        actual = restored.segments[0]
        assert actual.timing_issue == segment.timing_issue
        assert actual.failed_stage == "ALIGNING" and actual.error == segment.error
        assert actual.status == "FAILED"  # A readable WAV cannot approve a failed timing gate.
        restored.persist()
        active_streaming_sessions.pop(persisted.task_id)


@pytest.mark.parametrize("field,value", [
    ("code", "DONE"), ("required_seconds", "1.59"), ("required_seconds", True),
    ("required_seconds", 0), ("required_seconds", float("inf")),
    ("available_seconds", -1), ("available_seconds", True),
    ("max_speed", 0), ("max_speed", 100), ("max_speed", float("nan")),
    ("remedy", ""), ("remedy", {"text": "do something"}),
    ("unknown_authority", True),
])
def test_malformed_timing_conflict_never_enters_durable_project(persisted, field, value):
    from core.streaming import session_store as store
    old = _project_path(persisted.task_id).read_bytes()
    issue = timing_issue()
    issue[field] = value
    persisted.segments[0].timing_issue = issue
    with pytest.raises(ValueError):
        store.save_session(persisted)
    assert _project_path(persisted.task_id).read_bytes() == old


def source_proof(kind, identity, *, method="audio_diarization", verified=True):
    return {kind + "_id": identity, "verified": verified, "method": method,
        "confidence": .94, "model": "upstream-audio-model", "scope_id": "video-scope-1"}


def test_source_identity_evidence_and_asr_provenance_roundtrip_without_inventing_certainty(persisted):
    segment = persisted.segments[0]
    segment.speaker_id, segment.utterance_id = "voice-a", 17
    segment.speaker_evidence = source_proof("speaker", "voice-a")
    segment.utterance_evidence = {"utterance_id": 17, "method": "user_confirmation", "verified": True,
        "scope_id": "video-scope-1", "confirmation_id": "actual-recorded-action"}
    segment.source_asr_row_id = 18
    segment.source_asr_start, segment.source_asr_end = 0., 2.2
    segment.source_piece_index, segment.source_piece_count = 0, 2
    persisted.persist()
    for _ in range(2):
        restored = restore_saved_session(persisted.task_id)
        actual = restored.segments[0]
        for key in ("speaker_id", "speaker_evidence", "utterance_id", "utterance_evidence",
            "source_asr_row_id", "source_asr_start", "source_asr_end", "source_piece_index", "source_piece_count"):
            assert getattr(actual, key) == getattr(segment, key)
        assert actual.status == "READY"
        restored.persist()
        active_streaming_sessions.pop(persisted.task_id)


@pytest.mark.parametrize("kind", ["speaker", "utterance"])
def test_partial_unverified_identity_evidence_is_retained_without_adding_missing_proof(persisted, kind):
    segment = persisted.segments[0]
    setattr(segment, kind + "_id", "unproven-label")
    evidence = {kind + "_id": "unproven-label", "verified": False, "method": "ocr",
        "reason": "Chưa có bằng chứng từ âm thanh hoặc người dùng.", "api_key": "must-not-persist"}
    setattr(segment, kind + "_evidence", evidence)
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    actual = getattr(restored.segments[0], kind + "_evidence")
    assert actual == {key: value for key, value in evidence.items() if key != "api_key"}
    assert "scope_id" not in actual and actual["verified"] is False


@pytest.mark.parametrize("kind", ["speaker", "utterance"])
@pytest.mark.parametrize("field,value", [
    ("verified", "true"), ("method", "ocr"), ("scope_id", None),
    ("confidence", True), ("confidence", 1.1), ("model", ""),
    ("identity", "another-label"),
])
def test_invalid_or_invented_identity_authority_cannot_be_saved(persisted, kind, field, value):
    from core.streaming import session_store as store
    segment = persisted.segments[0]
    setattr(segment, kind + "_id", "voice-a")
    proof = source_proof(kind, "voice-a")
    proof[kind + "_id" if field == "identity" else field] = value
    setattr(segment, kind + "_evidence", proof)
    old = _project_path(persisted.task_id).read_bytes()
    with pytest.raises(ValueError):
        store.save_session(persisted)
    assert _project_path(persisted.task_id).read_bytes() == old


@pytest.mark.parametrize("field,value", [
    ("speaker_id", True), ("utterance_id", -1), ("speaker_id", ""),
    ("source_asr_row_id", {}), ("source_asr_start", "0"),
    ("source_asr_end", float("nan")), ("source_piece_index", True),
    ("source_piece_count", 0), ("source_piece_index", -1),
])
def test_source_metadata_types_are_not_coerced_into_valid_identity_or_provenance(persisted, field, value):
    from core.streaming import session_store as store
    setattr(persisted.segments[0], field, value)
    with pytest.raises(ValueError):
        store.save_session(persisted)


@pytest.mark.parametrize("start,end,index,count", [(1, 3, 0, 2), (0, 1, 0, 2), (0, 3, 2, 2)])
def test_asr_container_and_piece_indices_must_cover_saved_fragment(persisted, start, end, index, count):
    from core.streaming import session_store as store
    segment = persisted.segments[0]
    segment.source_asr_start, segment.source_asr_end = start, end
    segment.source_piece_index, segment.source_piece_count = index, count
    with pytest.raises(ValueError):
        store.save_session(persisted)


def test_stop_recovery_is_distinct_from_corrupt_or_over_budget_audio(persisted):
    segment = persisted.segments[0]
    segment.status, segment.audio_path = "TTS", None
    persisted.is_running = True
    persisted.stop()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].status == "FAILED" and restored.segments[0].failed_stage == "TTS"
    assert "khi tác vụ dừng" in restored.segments[0].error
    assert restored.get_progress()["status"] == "STOPPED"


def test_over_budget_wav_restore_keeps_file_and_reports_alignment_not_provider_failure(persisted):
    segment = persisted.segments[0]
    path = Path(segment.audio_path)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * (3 * 24000))
    restored = restore_saved_session(persisted.task_id)
    actual = restored.segments[0]
    assert actual.status == "FAILED" and actual.failed_stage == "ALIGNING"
    assert "3.000" in actual.error and "2.000" in actual.error
    assert actual.audio_path is None and path.is_file()


def test_malformed_and_missing_wav_restore_keep_distinct_actionable_reasons(persisted):
    segment = persisted.segments[0]
    path = Path(segment.audio_path)
    path.write_bytes(b"invalid wav")
    invalid = restore_saved_session(persisted.task_id).segments[0]
    assert invalid.status == "FAILED" and "bị hỏng" in invalid.error
    active_streaming_sessions.pop(persisted.task_id)
    path.unlink()  # This fixture's own WAV, never user media.
    missing = restore_saved_session(persisted.task_id).segments[0]
    assert missing.status == "FAILED" and "Thiếu tệp giọng" in missing.error


def test_readable_wav_from_interrupted_tts_is_not_promoted_without_a_successful_commit(persisted):
    persisted.segments[0].status = "TTS"
    persisted.is_running = True
    persisted.stop()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].status == "FAILED"
    assert restored.get_progress()["status"] == "STOPPED"


def test_large_project_compression_roundtrip_and_tamper_detection():
    from core.streaming.session_store import _encode_project, _decode_project
    payload = {"segments": [{"id": sid, "source": "字幕" * 200, "audit": "Bằng chứng nguồn. " * 100}
                            for sid in range(1500)]}
    encoded = _encode_project(payload)
    assert json.loads(encoded)["storage"] == "zlib-project-1"
    assert len(encoded.encode()) < len(json.dumps(payload, ensure_ascii=False).encode()) / 5
    assert _decode_project(encoded) == payload
    envelope = json.loads(encoded)
    envelope["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="thiếu hoặc hỏng"):
        _decode_project(json.dumps(envelope))


def test_compressed_project_expansion_is_bounded_and_legacy_remains_plain():
    from core.streaming.session_store import _encode_project, _decode_project, MAX_EXPANDED_BYTES
    small = {"version": 1, "segments": []}
    assert json.loads(_encode_project(small)) == small
    assert _decode_project(_encode_project(small)) == small
    invalid = {"storage": "zlib-project-1", "raw_bytes": MAX_EXPANDED_BYTES + 1, "payload": ""}
    with pytest.raises(ValueError, match="Dung lượng"):
        _decode_project(json.dumps(invalid))


def test_large_editable_project_reopens_with_source_identity_and_summary(persisted, monkeypatch):
    import core.streaming.session_store as store
    monkeypatch.setattr(store, "COMPRESS_AFTER_BYTES", 1)
    persisted._chunked_source_identity = "a" * 64
    persisted._visual_context_summary = "Bản tóm tắt nháp, không phải bằng chứng."
    before = persisted.segments[0].final_vi
    path = persisted.persist()
    assert json.loads(path.read_text(encoding="utf-8"))["storage"] == "zlib-project-1"
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].final_vi == before
    assert restored._chunked_source_identity == "a" * 64
    assert restored._visual_context_summary == persisted._visual_context_summary


def test_capacity_failure_stops_processing_instead_of_silently_ignoring_saves(persisted, monkeypatch):
    import core.streaming.session_store as store
    path = _project_path(persisted.task_id)
    durable = path.read_bytes()
    monkeypatch.setattr(store, "MAX_EXPANDED_BYTES", 64)
    with pytest.raises(store.ProjectCapacityError, match="giới hạn"):
        persisted._persist_if_enabled()
    assert persisted._persistence_capacity_failed and not persisted.can_retry
    assert path.read_bytes() == durable
    # The terminal error/finished event cannot recurse into failed saving.
    persisted._persist_if_enabled()
    assert any("giới hạn" in message for message in persisted.warnings)


def test_active_chunked_save_failure_blocks_runtime_and_keeps_durable_checkpoint(persisted, monkeypatch):
    path = _project_path(persisted.task_id)
    durable = path.read_bytes()
    persisted._chunked_source_started = True
    persisted.is_running = True
    failing_save = Mock(side_effect=OSError("disk full: private detail"))
    monkeypatch.setattr(persisted, "persist", failing_save)

    with pytest.raises(RuntimeError, match="Không lưu được dự án"):
        persisted._persist_if_enabled()

    assert not persisted.is_running
    assert persisted._persistence_failure_blocked is True
    # Existing callback guards use this terminal flag to prevent more work or
    # recursive saves after the durable checkpoint becomes unavailable.
    assert persisted._persistence_capacity_failed is True
    assert path.read_bytes() == durable
    persisted._persist_if_enabled()
    assert failing_save.call_count == 1


def test_persistent_edit_survives_restart_with_its_own_wav_and_retires_only_old_audio(persisted, monkeypatch):
    import core.streaming.session_store as store
    before = Path(persisted.segments[0].audio_path)
    old_content = before.read_bytes()

    def synthesis(**kwargs):
        with wave.open(str(kwargs["output_path"]), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x27" * 24000)
        return {"text": kwargs["text"], "tts_duration": 1, "speed_ratio": 1, "boundaries": []}

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesis)
    edited = asyncio.run(persisted.edit_segment(0, "Lời do người dùng sửa."))
    record = store._read(persisted.task_id)
    assert record["segments"][0]["final_vi"] == edited["final_vi"]
    assert record["segments"][0]["revision"] == edited["revision"] == 1
    saved_audio = Path(record["segments"][0]["audio_path"])
    assert saved_audio != before and saved_audio.read_bytes() != old_content
    assert not before.exists()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].final_vi == edited["final_vi"]
    assert Path(restored.segments[0].audio_path) == saved_audio
    assert restored.segments[0].status == "READY"


def test_real_manifest_replace_failure_rolls_back_edit_and_retry_can_commit(persisted, monkeypatch):
    import core.streaming.session_store as store
    from core.streaming.pipeline import ProjectEditSaveError
    from copy import deepcopy
    manifest = store._project_path(persisted.task_id)
    durable = manifest.read_bytes()
    prior = deepcopy(persisted.segments[0].to_dict())
    before = Path(persisted.segments[0].audio_path)
    original_replace = store._replace_project_with_retry

    def synthesis(**kwargs):
        with wave.open(str(kwargs["output_path"]), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x27" * 24000)
        return {"text": kwargs["text"], "tts_duration": 1, "speed_ratio": 1, "boundaries": []}

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesis)
    monkeypatch.setattr(store, "_replace_project_with_retry", Mock(side_effect=PermissionError("locked manifest")))
    with pytest.raises(ProjectEditSaveError, match="Nội dung và giọng cũ"):
        asyncio.run(persisted.edit_segment(0, "Lời vừa sửa."))
    assert manifest.read_bytes() == durable and persisted.segments[0].to_dict() == prior
    assert before.exists() and list(persisted.segments_dir.iterdir()) == [before]
    assert not list(manifest.parent.glob("*.tmp"))

    monkeypatch.setattr(store, "_replace_project_with_retry", original_replace)
    asyncio.run(persisted.edit_segment(0, "Lời vừa sửa."))
    record = store._read(persisted.task_id)
    assert record["segments"][0]["final_vi"] == "Lời vừa sửa."
    assert Path(record["segments"][0]["audio_path"]).is_file()
    assert not before.exists()


@pytest.fixture
def persisted(tmp_path, monkeypatch):
    for name, value in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace" / "inputs", "OUTPUT_DIR": tmp_path / "workspace" / "outputs",
        "TEMP_DIR": tmp_path / "workspace" / "temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "clip.mp4"
    source.write_bytes(b"video")
    session = StreamingPipelineSession("persist-test", source)
    session.initialized = True
    session.total_duration = 2
    session.video_size = (1920, 1080)
    segment = SegmentItem(0, 0, 2, 2)
    segment.status = "READY"
    segment.text_zh, segment.final_vi = "你好", "Xin chào."
    segment.audio_path = str(session.segments_dir / "seg_0.wav")
    with wave.open(segment.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 100)
    session.segments[0] = segment
    session.persist()
    old = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    yield session
    active_streaming_sessions.clear()
    active_streaming_sessions.update(old)


def test_corrupt_manifest_is_listed_but_cannot_open(persisted):
    _project_path(persisted.task_id).write_text("{\"version\":", encoding="utf-8")
    row = next(item for item in list_saved_sessions() if item["task_id"] == persisted.task_id)
    assert row["can_open"] is False and row["status"] == "FAILED"
    with pytest.raises((ValueError, json.JSONDecodeError)):
        restore_saved_session(persisted.task_id)


def test_concurrent_history_restore_returns_one_registered_owner(persisted, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import core.streaming.session_store as store
    original_read = store._read
    entered, second_entered, second_started, release = (threading.Event() for _ in range(4))
    count_lock = threading.Lock()
    reads = []
    def delayed_read(task_id):
        with count_lock:
            reads.append(task_id)
            (entered if len(reads) == 1 else second_entered).set()
        assert release.wait(5)
        return original_read(task_id)
    monkeypatch.setattr(store, "_read", delayed_read)
    def second_restore():
        second_started.set()
        return store.restore_saved_session(persisted.task_id)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(store.restore_saved_session, persisted.task_id)
        assert entered.wait(3)
        second = workers.submit(second_restore)
        assert second_started.wait(3)
        second_entered.wait(.1)
        release.set()
        first_owner, second_owner = first.result(5), second.result(5)
    assert first_owner is second_owner
    assert active_streaming_sessions[persisted.task_id] is first_owner
    assert reads == [persisted.task_id]


def test_restore_failure_releases_ownership_for_a_real_retry(persisted, monkeypatch):
    import core.streaming.session_store as store
    original_read = store._read
    attempts = []
    def retryable_read(task_id):
        attempts.append(task_id)
        if len(attempts) == 1:
            raise OSError("Controlled file-sharing failure")
        return original_read(task_id)
    monkeypatch.setattr(store, "_read", retryable_read)
    with pytest.raises(OSError):
        store.restore_saved_session(persisted.task_id)
    restored = store.restore_saved_session(persisted.task_id)
    assert active_streaming_sessions[persisted.task_id] is restored
    assert restored.segments[0].final_vi == persisted.segments[0].final_vi
    assert attempts == [persisted.task_id, persisted.task_id]


def test_saved_ready_wav_longer_than_dub_window_is_missing_audio_not_completed(persisted):
    segment = persisted.segments[0]
    with wave.open(segment.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * (3 * 24000))
    row = next(item for item in list_saved_sessions() if item["task_id"] == persisted.task_id)
    assert row["status"] == "FAILED"
    assert row["progress_pct"] is None
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].status == "FAILED"
    assert restored.segments[0].audio_path is None
    assert restored.can_retry


def test_intentional_preview_reopens_without_interrupted_error_and_retains_future_source(persisted):
    persisted.visual_translation = True
    persisted.translation_mode = "preview"
    persisted.total_duration = 100
    persisted._chunked_source_started = True
    persisted._source_prepared_seconds = 32
    persisted._visual_incremental_started = True
    persisted._visual_completed_seconds = 24
    persisted._preview_ready = True
    future = SegmentItem(1, 26, 28, 2)
    future.text_zh = "后面的话"
    persisted.segments[1] = future
    persisted.persist()
    listed = list_saved_sessions()[0]
    assert listed["status"] == "PREVIEW_READY"
    assert listed["can_translate_full"] is True
    assert listed["output_filename"] == "" and listed["missing_media"] == ""
    restored = restore_saved_session(persisted.task_id)
    assert restored.error is None and not restored._startup_failed
    assert restored.get_progress()["status"] == "PREVIEW_READY"
    assert restored.can_translate_full
    assert restored.segments[1].status == "WAITING"
    assert restored.segments[0].final_vi == persisted.segments[0].final_vi


def test_partial_preview_reopens_as_failed_but_keeps_explicit_full_action(persisted):
    persisted.translation_mode = "preview"
    persisted.total_duration = 100
    persisted._chunked_source_started = True
    persisted._source_prepared_seconds = 32
    persisted._visual_completed_seconds = 24
    persisted._preview_ready = True
    missing = SegmentItem(1, 4, 6, 2)
    missing.text_zh, missing.final_vi, missing.status, missing.failed_stage = "你好", "Xin chào.", "FAILED", "TTS"
    persisted.segments[1] = missing
    future = SegmentItem(2, 26, 28, 2)
    future.text_zh = "再见"
    persisted.segments[2] = future
    persisted.error = "One speech request failed"
    persisted.persist()
    row = list_saved_sessions()[0]
    assert row["status"] == "FAILED" and row["can_translate_full"]
    restored = restore_saved_session(persisted.task_id)
    assert restored.error and restored.get_progress()["status"] == "FAILED"
    assert restored.can_translate_full and restored.can_retry
    assert restored.segments[1].status == "FAILED"
    assert restored.segments[2].status == "WAITING"
    assert restored._startup_failed is False


def test_stop_during_private_chunk_review_remains_stopped_on_reopen(persisted):
    persisted.translation_mode = "preview"
    persisted._chunked_source_started = True
    persisted._source_prepared_seconds = 32
    persisted._visual_completed_seconds = 16
    persisted.total_duration = 100
    persisted.review_summary = {"status": "incomplete"}
    persisted.is_running = True
    persisted.stop()
    assert list_saved_sessions()[0]["status"] == "STOPPED"
    restored = restore_saved_session(persisted.task_id)
    assert restored.get_progress()["status"] == "STOPPED"
    assert restored.can_retry and not restored.can_translate_full


@pytest.mark.parametrize("style", [
    {"position": "diagonal"}, {"text_color": "red"}, {"background_color": "#zzzzzz"},
    {"blur_original": "false"}, {"position": []},
])
def test_corrupted_caption_style_cannot_restore(persisted, style):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["session"]["caption_style"] = style
    path.write_text(json.dumps(record), encoding="utf-8")
    assert list_saved_sessions()[0]["can_open"] is False
    with pytest.raises((ValueError, TypeError)):
        restore_saved_session(persisted.task_id)


def test_exact_nested_evidence_and_measured_cues_survive(persisted):
    segment = persisted.segments[0]
    segment.verification = {"status": "corrected", "provider": "opencode", "model": "muse",
        "evidence": [{"id": "r0", "start": 0, "end": 2, "text_zh": "你好", "confidence": .98,
                      "bbox": [.1, .8, .8, .05]}], "evidence_ids": ["r0"],
        "source_supported": True, "semantic_verified": True, "second_pass_status": "completed",
        "pacing": {"provider": "opencode", "status": "verified", "text": "Xin chào.", "reason": "Đủ nghĩa"}}
    segment.subtitle_cues = [{"text": "Xin chào.", "start": .1, "end": 1.8,
        "words": [{"text": "Xin", "start": .1, "end": .5}, {"text": "chào.", "start": .6, "end": 1.8}]}]
    segment.subtitle_timing_source = "edge-word-boundary"
    persisted.screen_texts = [{"start": 0, "end": 2, "bbox": [.1, .8, .8, .05],
        "source_method": "local-ocr", "kind": "subtitle", "confidence": .98,
        "needs_review": True, "source_region_verified": True}]
    persisted.caption_style = {"background_color": "#123456", "text_color": "#FFFFFF", "position": "top", "blur_original": True}
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].verification == segment.verification
    assert restored.segments[0].subtitle_cues == segment.subtitle_cues
    assert restored.screen_texts == persisted.screen_texts
    assert restored.caption_style == persisted.caption_style
    assert restored.caption_metadata(restored.segments[0].to_dict())["caption_layout"]["source_masks"]


@pytest.mark.parametrize("applicable,neutral_faithful,address_verified", [
    pytest.param(True, False, True, id="source-grounded-address"),
    pytest.param(False, True, False, id="faithful-neutral-no-address-claim"),
    pytest.param(True, False, False, id="applicable-but-unresolved"),
])
def test_address_evidence_pacing_and_context_survive_repeated_restore(
        persisted, applicable, neutral_faithful, address_verified):
    segment = persisted.segments[0]
    unresolved = applicable and not address_verified
    segment.needs_review = unresolved
    segment.review_reason = "Chưa xác minh được chiều xưng hô." if unresolved else None
    reading = {"id": 0, "self_address": "em", "listener_address": "chị", "uncertain": False,
               "reason": "Người đang nói gọi người nghe là chị.",
               "evidence": [{"id": 0, "quote": "拜托姐"}]}
    segment.verification = {"status": "unresolved" if unresolved else "corrected",
        "source_supported": True, "semantic_verified": not unresolved,
        "address_applicable": applicable, "address_neutral_faithful": neutral_faithful,
        "address_verified": address_verified, "address_reason": "Đã rà cách diễn đạt theo nguồn.",
        "address_context": reading,
        "evidence_ids": ["r0"],
        "evidence": [{"id": "r0", "start": 0.0, "end": 2.0, "text_zh": "拜托姐",
                      "confidence": .98, "bbox": [.1, .8, .8, .05]}],
        "address_context_sources": {"0": "拜托姐"},
        "pacing": {"status": "verified", "provider": "opencode", "text": segment.final_vi,
                   "reason": "Giữ cách xưng hô đã đối chiếu.", "address_preserved": True}}
    persisted.rolling_context = [{"id": 0, "start": 0.0, "end": 2.0, "zh": "拜托姐", "vi": "Thôi mà chị.",
        "is_focus": True, "speaker_id": "A", "addressee_id": "B", "source_needs_review": True,
        "source_truncated": True, "translation_is_draft": True, "reviewed_address_context": reading}]
    persisted.persist()
    for _ in range(2):
        active_streaming_sessions.clear()
        restored = restore_saved_session(persisted.task_id)
        assert restored.segments[0].verification == segment.verification
        audit = restored.segments[0].verification
        assert audit["address_applicable"] is applicable
        assert audit["address_neutral_faithful"] is neutral_faithful
        assert audit["address_verified"] is address_verified
        assert restored.segments[0].needs_review is unresolved
        assert audit["status"] == ("unresolved" if unresolved else "corrected")
        assert restored.rolling_context == persisted.rolling_context
        restored.persist()


def test_address_source_snapshot_and_stale_ids_round_trip(persisted):
    persisted.segments[0].verification = {
        "status": "unresolved", "address_verified": False,
        "address_context_sources": {"0": "拜托姐", "12": "妈妈，我饿了"},
        "address_stale_source_ids": [12],
        "address_reason": "Nguồn được dùng làm căn cứ đã thay đổi.",
    }
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    audit = restored.segments[0].verification
    assert audit["address_context_sources"] == {"0": "拜托姐", "12": "妈妈，我饿了"}
    assert audit["address_stale_source_ids"] == [12]


def test_granular_address_evidence_preserves_gate_and_pacing_context_after_restarts(persisted):
    from core.translation_context import dialogue_context
    from core.translation_review import AutomaticTranslationReviewer
    segment = persisted.segments[0]
    segment.text_zh = "拜托姐"
    segment.final_vi = "Chị ơi, nhờ chị đấy!"
    segment.needs_review = False
    reading = {"id": 0, "self_address": "", "listener_address": "chị", "uncertain": True,
        "self_uncertain": True, "listener_uncertain": False,
        "reason": "Lời gọi trực tiếp xác nhận chị, chưa xác định tự xưng.",
        "evidence": [{"id": 0, "quote": "拜托姐"}]}
    uses = [{"term": "Chị", "role": "listener"}, {"term": "chị", "role": "listener"}]
    segment.verification = {"status": "verified", "source_supported": True, "semantic_verified": True,
        "address_applicable": True, "address_verified": True, "address_reason": "Chỉ dùng lời gọi chị.",
        "address_uses": uses, "address_context": reading, "address_context_sources": {"0": "拜托姐"}}
    persisted.rolling_context = [{"id": 0, "text_zh": segment.text_zh,
        "reviewed_address_context": reading}]
    persisted.persist()
    for _ in range(2):
        active_streaming_sessions.clear()
        restored = restore_saved_session(persisted.task_id)
        row = restored.segments[0]
        assert row.verification == segment.verification
        assert restored.rolling_context == persisted.rolling_context
        audit = row.verification
        assert not AutomaticTranslationReviewer._address_gate(
            {0: audit["address_context"]}, 0, audit, row.text_zh, row.final_vi)
        # Source certainty still cannot authorize an added self address after reload.
        changed = {**audit, "address_uses": [{"term": "Em", "role": "self"},
                                              {"term": "chị", "role": "listener"}]}
        assert AutomaticTranslationReviewer._address_gate(
            {0: audit["address_context"]}, 0, changed, row.text_zh, "Em nhờ chị!")
        context = dialogue_context([row.to_dict()])
        assert context[0]["reviewed_address_context"] == reading
        assert context[0]["reviewed_address_context"]["self_uncertain"] is True
        restored.persist()


def test_competing_turn_reading_cannot_gain_certainty_after_restart(persisted):
    from core.translation_review import AutomaticTranslationReviewer
    segment = persisted.segments[0]
    segment.text_zh, segment.final_vi = "我十八", "Con 18 tuổi."
    reading = {"id": 0, "self_address": "con", "listener_address": "bố",
        "self_uncertain": False, "listener_uncertain": False, "uncertain": False,
        "reason": "Chưa loại được cách đọc hai người đáp nhau.",
        "evidence": [{"id": 0, "quote": "我十八"}],
        "turn_check": {"ambiguous_roles": ["self"], "reason": "Có hai cách phân lượt.",
                       "evidence": [{"id": 0, "quote": "我十八"}]}}
    segment.verification = {"status": "unresolved", "semantic_verified": True,
        "address_verified": True, "address_applicable": True,
        "address_reason": "Bản nháp.", "address_context": reading,
        "address_uses": [{"term": "Con", "role": "self"}]}
    persisted.persist()
    for _ in range(2):
        active_streaming_sessions.clear()
        restored = restore_saved_session(persisted.task_id)
        audit = restored.segments[0].verification
        context = audit["address_context"]
        assert context["turn_check"] == reading["turn_check"]
        assert context["self_uncertain"] is True and context["uncertain"] is True
        assert context["listener_uncertain"] is False
        assert AutomaticTranslationReviewer._address_gate(
            {0: context}, 0, audit, segment.text_zh, segment.final_vi)
        restored.persist()


@pytest.mark.parametrize("value", [None, {}, ["chị"], [{"term": "chị", "role": "both"}],
    [{"term": "", "role": "listener"}], [{"term": 12, "role": "listener"}]])
def test_corrupt_address_uses_cannot_restore_as_verified(persisted, value):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["segments"][0]["verification"] = {"status": "verified", "address_verified": True,
                                                "address_uses": value}
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="xưng hô"):
        restore_saved_session(persisted.task_id)
    assert list_saved_sessions()[0]["can_open"] is False


@pytest.mark.parametrize("field", ["self_uncertain", "listener_uncertain"])
@pytest.mark.parametrize("value", ["false", 0, None, []])
def test_role_certainty_never_coerces_non_boolean_values(persisted, field, value):
    persisted.segments[0].verification = {"address_context": {field: value}}
    with pytest.raises(ValueError, match="boolean"):
        persisted.persist()


def test_role_usage_scope_excludes_unrelated_fields_and_redacts_secrets(persisted, monkeypatch):
    secret = "private-address-term-fixture"
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", secret)
    persisted.segments[0].verification = {"status": "unresolved", "address_verified": False,
        "address_uses": [{"term": secret, "role": "listener", "api_key": "unknown-private-key",
                          "headers": {"Authorization": "private"}, "reason": "unexpected-provider-body"}],
        "term": "outside-approved-usage", "role": "outside-approved-role"}
    persisted.persist()
    audit = restore_saved_session(persisted.task_id).segments[0].verification
    assert audit["address_uses"] == [{"term": "[redacted]", "role": "listener"}]
    assert "term" not in audit and "role" not in audit
    text = _project_path(persisted.task_id).read_text(encoding="utf-8")
    for omitted in (secret, "unknown-private-key", "Authorization", "unexpected-provider-body",
                    "outside-approved-usage", "outside-approved-role"):
        assert omitted not in text


def test_address_source_snapshot_rejects_non_numeric_keys(persisted):
    persisted.segments[0].verification = {
        "status": "unresolved", "address_context_sources": {"source": "private"},
    }
    with pytest.raises(ValueError, match="Ảnh chụp nguồn xưng hô"):
        persisted.persist()


def test_address_metadata_filters_credentials_and_redacts_configured_secret(persisted, monkeypatch):
    secret = "address-storage-test-private-key"
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", secret)
    persisted.segments[0].verification = {"status": "verified", "address_verified": True,
        "address_reason": f"Giữ lời gọi {secret}", "address_context": {
            "id": 0, "self_address": "em", "listener_address": "chị", "uncertain": False,
            "reason": "Nguồn lời gọi chị", "api_key": secret,
            "headers": {"Authorization": "Bearer unconfigured-private-token"},
            "evidence": [{"id": 0, "quote": f"拜托姐 {secret}", "credentials": "private-session"}]},
        "pacing": {"status": "verified", "address_preserved": True, "authorization": "Bearer private-token"}}
    persisted.persist()
    text = _project_path(persisted.task_id).read_text(encoding="utf-8")
    for omitted in (secret, "private-token", "private-session", "Authorization", "authorization",
                    "headers", "credentials", "api_key"):
        assert omitted not in text
    audit = restore_saved_session(persisted.task_id).segments[0].verification
    assert audit["address_verified"] is True
    assert audit["pacing"]["address_preserved"] is True
    assert audit["address_reason"] == "Giữ lời gọi [redacted]"
    assert audit["address_context"]["evidence"] == [{"id": 0, "quote": "拜托姐 [redacted]"}]


@pytest.mark.parametrize("stage,code,consensus", [
    ("audio_evidence", "asr_failed", False),
    ("audio_semantic_review", "provider_timeout", True),
    ("semantic_second_pass", "invalid_response", False),
])
def test_review_diagnostics_and_independent_audio_evidence_round_trip(persisted, stage, code, consensus):
    segment = persisted.segments[0]
    segment.needs_review = True
    segment.verification = {"status": "incomplete", "semantic_verified": False,
        "diagnostic": {"stage": stage, "code": code, "run_id": "persist-test_review_1", "segment_ids": [0]},
        "audio_evidence": [
            {"engine": "sensevoice", "text_zh": "你好", "start": 0.0, "end": 2.0},
            {"engine": "faster-whisper-small", "text_zh": "你好" if consensus else "您好", "start": 0.0, "end": 2.0},
        ], "audio_consensus": consensus, "audio_audit_status": "failed"}
    persisted.review_summary = {"status": "incomplete", "checked": 1, "incomplete": 1}
    persisted.persist()
    saved = json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))
    assert saved["segments"][0]["verification"] == segment.verification
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].verification == segment.verification
    assert restored.get_progress()["status"] == "FAILED"
    assert restored.get_progress()["can_review"]
    restored.persist()
    active_streaming_sessions.clear()
    assert restore_saved_session(persisted.task_id).segments[0].verification == segment.verification


def test_new_diagnostic_fields_never_persist_secrets_or_raw_provider_data(persisted, monkeypatch):
    secret = "storage-test-private-key"
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", secret)
    persisted.segments[0].verification = {"status": "unresolved",
        "diagnostic": {"stage": "audio_evidence", "code": "asr_failed", "run_id": secret,
            "segment_ids": [0], "reason": "raw private response", "text": "raw private prompt",
            "headers": {"Authorization": "Bearer arbitrary-provider-token"},
            "response_body": "private provider body", "exception": "private decoder exception"},
        "audio_evidence": [{"engine": "sensevoice", "text_zh": f"你好 {secret}", "start": 0, "end": 2,
            "api_key": secret, "headers": {"Cookie": "private-session"}, "reason": "private provider response"}],
        "audio_consensus": False, "audio_audit_status": "failed"}
    path = persisted.persist()
    text = path.read_text(encoding="utf-8")
    for omitted in (secret, "raw private", "arbitrary-provider-token", "private provider", "private-session",
                    "private decoder", "Authorization", "headers", "response_body", "exception", "api_key"):
        assert omitted not in text
    restored = restore_saved_session(persisted.task_id).segments[0].verification
    assert restored["diagnostic"] == {"stage": "audio_evidence", "code": "asr_failed",
                                      "run_id": "[redacted]", "segment_ids": [0]}
    assert restored["audio_evidence"] == [{"engine": "sensevoice", "text_zh": "你好 [redacted]", "start": 0, "end": 2}]


def test_loaded_diagnostics_are_filtered_by_the_same_safe_schema(persisted):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["segments"][0]["verification"] = {"status": "unresolved",
        "diagnostic": {"stage": "Authorization: private", "code": "https://private.example/?token=private",
                       "run_id": "Bearer private-token", "segment_ids": [0, "private"], "text": "private"},
        "audio_evidence": [{"engine": "sensevoice", "text_zh": "你好", "start": 0, "end": 2,
                            "headers": "private", "diagnostic": {"code": "provider_failed"}},
                           {"engine": "private engine", "text_zh": "private", "start": 0, "end": 2}],
        "audio_consensus": "true", "audio_audit_status": {"error": "private"}}
    path.write_text(json.dumps(record), encoding="utf-8")
    audit = restore_saved_session(persisted.task_id).segments[0].verification
    assert audit == {"status": "unresolved", "diagnostic": {},
                     "audio_evidence": [{"engine": "sensevoice", "text_zh": "你好", "start": 0, "end": 2}]}


@pytest.mark.parametrize("content", [b"not wav", b"RIFF", b""])
def test_invalid_audio_never_restores_ready(persisted, content):
    Path(persisted.segments[0].audio_path).write_bytes(content)
    restored = restore_saved_session(persisted.task_id)
    assert restored.error and restored.can_retry
    assert restored.segments[0].audio_url is None
    assert restored.segments[0].status == "FAILED"
    assert list_saved_sessions()[0]["status"] == "FAILED"


def test_truncated_wav_with_valid_header_is_not_ready(persisted):
    path = Path(persisted.segments[0].audio_path)
    path.write_bytes(path.read_bytes()[:45])
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].status == "FAILED"


def test_interrupted_review_is_explicit_and_does_not_call_provider(persisted, monkeypatch):
    persisted.review_summary = {"status": "running", "checked": 0}
    persisted.persist()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    import core.video_intelligence as intelligence
    monkeypatch.setattr(intelligence, "validate_visual_provider", Mock(side_effect=AssertionError("no provider lookup")))
    restored = restore_saved_session(persisted.task_id)
    assert restored.review_summary["status"] == "incomplete"
    assert restored.get_progress()["status"] == "FAILED"
    assert restored.get_progress()["can_review"]
    assert not restored.is_running and restored.worker_task is None and restored.review_task is None


def test_stop_keeps_saved_work_and_completed_rows_reopen_editably(persisted):
    before = Path(persisted.segments[0].audio_path).read_bytes()
    persisted.stop()
    assert _project_path(persisted.task_id).exists()
    assert Path(persisted.segments[0].audio_path).read_bytes() == before
    restored = restore_saved_session(persisted.task_id)
    assert not restored.is_stopped and not restored.is_running
    assert restored.get_progress()["status"] == "COMPLETED"
    assert restored.segments[0].final_vi == persisted.segments[0].final_vi


def test_successful_atomic_save_clears_old_disk_warning_only(persisted, monkeypatch):
    from core.streaming.session_store import PERSISTENCE_FAILURE_WARNINGS
    path = _project_path(persisted.task_id)
    preserved = path.read_bytes()
    persisted.warnings = sorted(PERSISTENCE_FAILURE_WARNINGS) + ["Câu thoại cần xác minh."]
    prior_warnings = list(persisted.warnings)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", Mock(side_effect=OSError("disk full")))
        persisted._persist_if_enabled()
    assert persisted.warnings == prior_warnings
    assert path.read_bytes() == preserved

    persisted.persist()
    assert persisted.warnings == ["Câu thoại cần xác minh."]
    assert json.loads(path.read_text(encoding="utf-8"))["session"]["warnings"] == persisted.warnings
    restored = restore_saved_session(persisted.task_id)
    assert restored.warnings == persisted.warnings


def test_shutdown_preserves_idle_outcome_and_saved_media(persisted):
    audio = Path(persisted.segments[0].audio_path)
    before = audio.read_bytes()
    active_streaming_sessions[persisted.task_id] = persisted
    persisted.shutdown()
    assert not persisted.is_stopped
    assert persisted.task_id not in active_streaming_sessions
    assert json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))["state"] == "READY"
    assert audio.read_bytes() == before
    assert restore_saved_session(persisted.task_id).get_progress()["status"] == "COMPLETED"


def test_shutdown_cancels_unfinished_work(persisted):
    persisted.is_running = True
    persisted.shutdown()
    assert persisted.is_stopped and not persisted.is_running
    assert json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))["state"] == "STOPPED"


def test_stop_mid_synthesis_preserves_ready_rows_and_retries_only_missing(persisted, monkeypatch):
    first_audio = Path(persisted.segments[0].audio_path).read_bytes()
    later = SegmentItem(1, 2, 3, 1)
    later.text_zh, later.final_vi, later.status = "再见", "Tạm biệt.", "TTS"
    persisted.segments[1] = later
    persisted.total_duration = 3
    persisted.is_running = True
    persisted.stop()
    restored = restore_saved_session(persisted.task_id)
    assert restored.can_retry and restored.segments[0].status == "READY"
    assert restored.segments[1].status == "FAILED"
    assert restored.get_progress()["status"] == "STOPPED"
    assert list_saved_sessions()[0]["status"] == "STOPPED"
    restored.persist()
    active_streaming_sessions.pop(persisted.task_id)
    restored = restore_saved_session(persisted.task_id)
    assert restored.can_retry and restored.get_progress()["status"] == "STOPPED"
    calls = []

    async def synthesize(segment):
        calls.append(segment.id)
        segment.status = "READY"

    monkeypatch.setattr(restored, "_synthesize_segment", synthesize)

    async def resume():
        await restored.retry_failed_synthesis()
        assert not restored._restored_interrupted
        assert restored.get_progress()["status"] == "RUNNING"
        await restored.worker_task

    asyncio.run(resume())
    assert calls == [1]
    assert Path(persisted.segments[0].audio_path).read_bytes() == first_audio


def test_pending_download_restores_without_network(persisted):
    session = StreamingPipelineSession("pending-download", None)
    session.source_url = "https://www.douyin.com/jingxuan?modal_id=123&token=private"
    session.is_running = True
    session.persist()
    restored = restore_saved_session(session.task_id)
    assert restored.source_url == "https://www.douyin.com/jingxuan?modal_id=123"
    assert restored.can_retry and restored._startup_failed
    assert restored.start_task is None and restored.video_path is None


def test_manifest_never_serializes_credentials_engines_or_exception_text(persisted, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "secret-for-storage-test")
    persisted.error = "HTTP failed Authorization: secret-for-storage-test"
    persisted.segments[0].verification = {"status": "verified", "api_key": "secret-for-storage-test",
        "reason": "secret-for-storage-test", "headers": {"Authorization": "secret-for-storage-test"}}
    path = persisted.persist()
    text = path.read_text(encoding="utf-8")
    assert "secret-for-storage-test" not in text
    assert "Authorization" not in text and "api_key" not in text and "headers" not in text
    assert "worker_task" not in text and "translator" not in text


def test_unknown_project_is_file_not_found(persisted):
    with pytest.raises(FileNotFoundError):
        restore_saved_session("unknown-project")


@pytest.mark.parametrize("damage", ["foreign_audio", "output_traversal", "bad_id", "invalid_time"])
def test_unsafe_manifest_is_rejected(persisted, damage):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    if damage == "foreign_audio":
        record["segments"][0]["audio_path"] = str(settings.BASE_DIR / "secret.wav")
    elif damage == "output_traversal":
        record["session"]["output_filename"] = "../secret.mp4"
    elif damage == "bad_id":
        record["task_id"] = "different-project"
    else:
        record["segments"][0]["end"] = float("nan")
    path.write_text(json.dumps(record), encoding="utf-8")
    assert not list_saved_sessions()[0]["can_open"]
    with pytest.raises(ValueError):
        restore_saved_session(persisted.task_id)


def test_failed_atomic_save_keeps_previous_project(persisted, monkeypatch):
    path = _project_path(persisted.task_id)
    before = path.read_bytes()
    persisted.segments[0].final_vi = "Bản chưa lưu được."
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        persisted.persist()
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing errors are platform-specific")
def test_atomic_save_retries_transient_windows_sharing_violation(persisted, monkeypatch):
    import core.streaming.session_store as store
    real_replace = Path.replace
    attempts = []

    def flaky_replace(source, target):
        if len(attempts) < 2:
            attempts.append(len(attempts))
            error = PermissionError(32, "sharing violation")
            error.winerror = 32
            raise error
        attempts.append(len(attempts))
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    persisted.segments[0].final_vi = "Đã vượt qua khóa tệp tạm thời."
    path = persisted.persist()
    assert path.is_file()
    assert len(attempts) == 3
    assert json.loads(path.read_text(encoding="utf-8"))["segments"][0]["final_vi"] == persisted.segments[0].final_vi
    assert not list(path.parent.glob("*.tmp"))


def test_source_scope_provenance_survives_save_reload(persisted):
    segment = persisted.segments[0]
    segment.verification = {
        "status": "unresolved", "source_supported": False, "source_accepted": False,
        "source_scope_conflict": True, "source_scope_ambiguous": False,
        "evidence": [{"id": "review0", "text_zh": "小满", "full_text_zh": "小满今年19",
                      "source_scope_ids": [4, 5, 6],
                      "source_scope_window": {"start": 7.88, "end": 11.333}}],
    }
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].verification == segment.verification
    saved = json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))
    assert saved["segments"][0]["verification"]["evidence"][0]["source_scope_ids"] == [4, 5, 6]


@pytest.mark.skipif(os.name != "nt", reason="Real Windows file sharing contract")
def test_real_windows_reader_lock_releases_during_atomic_save(persisted):
    import ctypes
    from ctypes import wintypes
    import threading
    import core.streaming.session_store as store

    path = persisted.persist()
    before = path.read_bytes()
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    # Permit reads/writes but deny delete-sharing, reproducing Windows' actual
    # atomic-replace failure without substituting Path.replace or its error.
    handle = api.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    staging = path.parent / "sharing-probe.tmp"
    staging.write_bytes(before)
    released = threading.Event()
    thread = None
    try:
        with pytest.raises(PermissionError) as failure:
            staging.replace(path)
        assert failure.value.winerror in {5, 32, 33}
        assert path.read_bytes() == before

        def release_lock():
            released.wait(.12)
            api.CloseHandle(handle)
        thread = threading.Thread(target=release_lock)
        thread.start()
        store._replace_project_with_retry(staging, path)
        assert path.read_bytes() == before and not staging.exists()
    finally:
        released.set()
        if thread is not None:
            thread.join(timeout=2)
            assert not thread.is_alive()
        else:
            api.CloseHandle(handle)
        staging.unlink(missing_ok=True)


@pytest.mark.parametrize("field,value", [
    ("source_accepted", "true"),
    ("source_scope_conflict", 1),
    ("source_scope_ambiguous", None),
    ("source_scope_ids", ["5"]),
    ("source_scope_window", {"start": 1.0, "end": 1.0}),
])
def test_source_scope_provenance_rejects_untyped_or_invalid_values(persisted, field, value):
    persisted.segments[0].verification = {field: value}
    with pytest.raises(ValueError, match="phạm vi nguồn|câu nguồn đối chiếu|Khoảng thời gian"):
        persisted.persist()


def test_fresh_process_restores_visual_project_without_provider_credentials(persisted):
    persisted.visual_translation = True
    persisted.caption_style = {"background_color": "#113355", "text_color": "#FFFFFF", "position": "top", "blur_original": False}
    persisted.caption_style_revision = 4
    persisted.current_playback_time = 1.25
    persisted.segments[0].revision = 7
    persisted.segments[0].verification = {"status": "manual", "reason": "Đã sửa."}
    persisted.persist()
    code = """
import json, sys
from pathlib import Path
from config import settings
root = Path(sys.argv[1])
settings.BASE_DIR = root
settings.WORKSPACE_DIR = root / 'workspace'
settings.OUTPUT_DIR = root / 'workspace' / 'outputs'
settings.TEMP_DIR = root / 'workspace' / 'temp'
settings.LLM_PROVIDER = 'gemini'
settings.GEMINI_API_KEY = ''
settings.OPENCODE_API_KEY = ''
import core.video_intelligence as intelligence
def forbidden(*a, **kw):
    raise AssertionError('restoring must never initialize provider')
intelligence.validate_visual_provider = forbidden
intelligence.VideoIntelligence = forbidden
from core.streaming.session_store import restore_saved_session
s = restore_saved_session('persist-test')
assert s.video_intelligence is None and not s.is_running
assert s.start_task is None and s.worker_task is None
assert s.get_progress()['status'] == 'COMPLETED'
assert s.segments[0].final_vi == 'Xin chào.'
assert s.segments[0].revision == 7 and s.segments[0].verification['status'] == 'manual'
assert s.caption_style['position'] == 'top' and s.caption_style_revision == 4
assert s.current_playback_time == 1.25
assert s.segments[0].audio_url == '/api/streaming/audio/persist-test/0?rev=7'
assert s.source_video_url.startswith('/api/local-file?path=')
print(json.dumps({'restored': s.task_id}))
"""
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(settings.BASE_DIR)],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["restored"] == persisted.task_id


def test_missing_selected_voice_keeps_explicit_failure_on_retry(persisted, monkeypatch):
    session = StreamingPipelineSession("missing-voice", persisted.video_path, tts_engine_name="vieneu")
    monkeypatch.setattr("core.streaming.pipeline.VieNeuEngine", Mock(return_value=Mock(is_available=False)))
    for _ in range(2):
        with pytest.raises(ValueError, match="VieNeu"):
            session._ensure_tts_engine()
        assert session.tts_engine is session.edge_tts


def test_edit_after_restore_never_initializes_translation_provider(persisted, monkeypatch):
    persisted.visual_translation = True
    persisted.persist()
    monkeypatch.setattr("core.video_intelligence.validate_visual_provider", Mock(side_effect=AssertionError("provider not needed")))
    restored = restore_saved_session(persisted.task_id)

    def synthesis(**kwargs):
        with wave.open(str(kwargs["output_path"]), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x27" * 24000)
        return {"tts_duration": 1, "speed_ratio": 1, "boundaries": []}

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesis)
    asyncio.run(restored.edit_segment(0, "Lời do người dùng sửa."))
    active_streaming_sessions.clear()
    again = restore_saved_session(persisted.task_id)
    assert again.segments[0].final_vi == "Lời do người dùng sửa."
    assert again.segments[0].verification["status"] == "manual"
    assert again.segments[0].revision == 1
    assert again.segments[0].subtitle_cues
    assert again.video_intelligence is None


def test_deleted_audio_is_failed_and_resumeable(persisted):
    Path(persisted.segments[0].audio_path).unlink()
    row = next(item for item in list_saved_sessions() if item["task_id"] == persisted.task_id)
    assert row["status"] == "FAILED" and "âm thanh" in row["missing_media"]
    active_streaming_sessions.clear()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].status == "FAILED"
    assert restored.segments[0].failed_stage == "TTS"
    assert restored.can_retry is True


def test_deleted_source_is_not_reported_as_completed(persisted):
    persisted.video_path.unlink()
    row = next(item for item in list_saved_sessions() if item["task_id"] == persisted.task_id)
    assert row["can_open"] is False and row["status"] == "FAILED"


def test_truncated_nonempty_mp4_is_not_published_after_restore(persisted):
    output = settings.OUTPUT_DIR / "truncated.mp4"
    output.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2\x00\x10\x00\x00mdatcut")
    persisted.output_filename = output.name
    persisted.persist()
    row = list_saved_sessions()[0]
    assert row["output_video_url"] == ""
    assert row["video_url"] == ""
    assert row["can_open"] is True
    restored = restore_saved_session(persisted.task_id)
    assert restored.output_video_url == "" and restored.output_filename == ""
    assert restored.segments[0].final_vi == "Xin chào."
    assert restored.segments[0].status == "READY" and not restored.error


@pytest.fixture
def saved_mp4(persisted):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required for real saved media validation")
    output = settings.OUTPUT_DIR / "saved.mp4"
    result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
        "testsrc2=size=96x64:rate=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000",
        "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", str(output)],
        capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    persisted.output_filename = output.name
    persisted.output_review_url = "/api/outputs/saved.review.json"
    (settings.OUTPUT_DIR / "saved.review.json").write_text("{}")
    persisted.persist()
    return output


def test_saved_mp4_probes_and_decodes_once_then_history_reuses_fingerprint(persisted, saved_mp4, monkeypatch):
    from core.streaming import session_store as store
    probe = Mock(wraps=store._probe_saved_output)
    monkeypatch.setattr(store, "_probe_saved_output", probe)
    for _ in range(3):
        row = list_saved_sessions()[0]
        assert row["output_video_url"] == "/api/outputs/saved.mp4"
        assert row["review_url"] == "/api/outputs/saved.review.json"
        assert row["status"] == "COMPLETED"
    restored = restore_saved_session(persisted.task_id)
    assert restored.output_video_url == "/api/outputs/saved.mp4"
    assert restored.segments[0].final_vi == persisted.segments[0].final_vi
    assert probe.call_count == 1


@pytest.mark.parametrize("damage", ["truncate", "missing", "wrong_duration", "corrupt_samples"])
def test_bad_saved_result_never_hides_editable_translation_or_publishes_url(persisted, saved_mp4, damage):
    if damage == "missing":
        saved_mp4.unlink()
    elif damage == "truncate":
        data = saved_mp4.read_bytes()
        saved_mp4.write_bytes(data[:len(data) // 2])
        # The intact faststart moov still advertises the full source duration;
        # metadata-only validation would incorrectly pass this damaged output.
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(saved_mp4)],
            capture_output=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert probe.returncode == 0 and float(json.loads(probe.stdout)["format"]["duration"]) == 2
    elif damage == "wrong_duration":
        persisted.total_duration = 20
        persisted.persist()
    else:
        data = bytearray(saved_mp4.read_bytes())
        start = data.index(b"mdat") + 4
        data[start:start + 5000] = bytes(min(5000, len(data) - start))
        saved_mp4.write_bytes(data)
    row = list_saved_sessions()[0]
    assert row["output_filename"] == row["output_video_url"] == row["video_url"] == row["review_url"] == ""
    assert row["can_open"] and row["status"] == "FAILED" and row["progress_pct"] is None
    assert "xuất MP4 lại" in row["stage"]
    restored = restore_saved_session(persisted.task_id)
    assert restored.output_video_url == restored.output_filename == restored.output_review_url == ""
    assert restored.segments[0].final_vi == persisted.segments[0].final_vi
    assert restored.segments[0].status == "READY" and restored.error is None
    assert restored.segments[0].audio_path == persisted.segments[0].audio_path
    assert any("Chưa xác minh được video đã xuất" in warning for warning in restored.warnings)
    restored.persist()
    manifest = json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))
    assert all("Chưa xác minh được video đã xuất" not in warning for warning in manifest["session"].get("warnings", []))


def test_output_validation_cache_rejects_same_size_replacement_even_with_same_stat_identity(persisted, saved_mp4, monkeypatch):
    from core.streaming import session_store as store
    assert list_saved_sessions()[0]["output_video_url"]
    original = saved_mp4.stat()
    old_identity = store._output_identity(saved_mp4, 2)
    data = bytearray(saved_mp4.read_bytes())
    data[:4] = b"\x00\x00\x00\x00"
    saved_mp4.write_bytes(data)
    os.utime(saved_mp4, ns=(original.st_atime_ns, original.st_mtime_ns))
    real_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda path, **kwargs: original if path == saved_mp4 else real_stat(path, **kwargs))
    new_identity = store._output_identity(saved_mp4, 2)
    assert old_identity[:-2] == new_identity[:-2]
    assert old_identity[-2] != new_identity[-2], "Sampled content detects unchanged size/stat metadata"
    assert list_saved_sessions()[0]["output_video_url"] == ""


def test_output_validation_timeout_is_cached_briefly_and_recovers_without_losing_project(persisted, saved_mp4, monkeypatch):
    from core.streaming import session_store as store
    real_probe = store._probe_saved_output
    probe = Mock(side_effect=subprocess.TimeoutExpired("ffprobe", 4))
    clock = [0.0]
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(store, "_probe_saved_output", probe)
    for _ in range(3):
        assert list_saved_sessions()[0]["output_video_url"] == ""
    assert probe.call_count == 1
    monkeypatch.setattr(store, "_probe_saved_output", real_probe)
    clock[0] = 31
    assert list_saved_sessions()[0]["output_video_url"] == "/api/outputs/saved.mp4"


def test_old_caption_output_is_not_probed_or_republished(persisted, saved_mp4, monkeypatch):
    from core.streaming import session_store as store
    persisted.caption_output_outdated = True
    persisted.persist()
    probe = Mock(side_effect=AssertionError("Outdated caption output must stay unpublished"))
    monkeypatch.setattr(store, "_probe_saved_output", probe)
    row = list_saved_sessions()[0]
    assert row["output_video_url"] == "" and row["can_open"]
    assert row["missing_media"] == ""
    probe.assert_not_called()


def test_valid_reexport_removes_only_stale_output_warning(persisted, saved_mp4):
    from core.streaming import session_store as store
    persisted.warnings = [store.OUTPUT_FAILURE_WARNING, "Cảnh báo khác."]
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    assert restored.output_video_url == "/api/outputs/saved.mp4"
    assert restored.warnings == ["Cảnh báo khác."]


def test_real_failure_shape_resumes_tts_with_completed_review_and_auto_export(persisted, monkeypatch):
    """755a2fe4: reviewed visual rows, first TTS failed, next row waiting."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    persisted.visual_translation = True
    persisted.translation_sources = [{"provider": "opencode", "model": "muse-spark-1.3-contributor-free",
        "evidence_mode": "fresh-ocr-text-review"}]
    persisted.review_summary = {"status": "completed", "checked": 2, "verified": 0, "corrected": 0, "unresolved": 2}
    first = persisted.segments[0]
    first.status, first.failed_stage = "FAILED", "TTS"
    first.audio_path = None
    later = SegmentItem(1, 2, 3, 1)
    later.text_zh, later.final_vi, later.status = "再见", "Tạm biệt.", "WAITING"
    persisted.segments[1] = later
    for row in persisted.segments.values():
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.needs_review = True
        row.verification = {"status": "unresolved", "reason": "Cần đối chiếu thêm nguồn."}
    persisted.total_duration = 3
    persisted.error = "Edge-TTS chưa trả về âm thanh sau 3 lần thử."
    persisted.persist()
    assert "auto_export_result" not in json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))["session"]
    events, calls = [], []
    restored = restore_saved_session(persisted.task_id, lambda kind, data: events.append((kind, data)))
    assert restored.auto_export_result and restored.can_retry
    assert restored.review_summary == persisted.review_summary
    assert restored.source_url == persisted.source_url
    monkeypatch.setattr(restored, "_process_segment", Mock(side_effect=AssertionError("do not translate again")))
    monkeypatch.setattr(restored, "_review_translations", Mock(side_effect=AssertionError("do not review again")))
    monkeypatch.setattr(restored.faster_whisper, "transcribe", Mock(side_effect=AssertionError("do not recognize again")))

    async def synthesize(row):
        calls.append((row.id, row.final_vi))
        row.audio_path = str(restored.segments_dir / f"seg_{row.id}.wav")
        with wave.open(row.audio_path, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x27" * 100)
        row.status = "READY"

    monkeypatch.setattr(restored, "_synthesize_segment", synthesize)

    async def resume():
        await restored.retry_failed_synthesis()
        await restored.worker_task

    asyncio.run(resume())
    assert calls == [(0, first.final_vi), (1, later.final_vi)]
    assert events[-1][0] == "finished" and events[-1][1]["status"] == "finished"
    assert events[-1][1]["review_summary"] == persisted.review_summary
    assert not restored.is_running and not restored.error and restored.auto_export_result
    active_streaming_sessions.clear()
    again = restore_saved_session(restored.task_id)
    assert again.auto_export_result and again.get_progress()["status"] == "STOPPED"
    assert again.get_progress()["progress_pct"] is None
    assert "chưa có MP4" in again.get_progress()["stage"]


@pytest.mark.parametrize("explicit", [False, True])
def test_explicit_auto_export_setting_survives_restart(persisted, explicit):
    persisted.auto_export_result = explicit
    persisted.persist()
    assert restore_saved_session(persisted.task_id).auto_export_result is explicit


def test_legacy_non_visual_session_never_enables_auto_export(persisted):
    restored = restore_saved_session(persisted.task_id)
    assert restored.auto_export_result is False


def test_shutdown_between_review_and_init_resumes_without_provider(persisted, monkeypatch):
    persisted.visual_translation = True
    persisted.initialized = False
    persisted.review_summary = {"status": "completed", "checked": 1, "verified": 1}
    row = persisted.segments[0]
    row.status, row.source_method, row.translation_provider = "WAITING", "text-ai", "opencode"
    row.audio_path = None
    persisted.persist()
    monkeypatch.setattr("core.video_intelligence.validate_visual_provider", Mock(side_effect=AssertionError("no provider on open")))
    restored = restore_saved_session(persisted.task_id)
    assert restored.initialized and restored.can_retry and not restored._startup_failed
    assert restored.segments[0].failed_stage == "TTS"
    assert restored.review_summary == persisted.review_summary
    assert restored.video_intelligence is None and restored.worker_task is None


def test_partial_visual_prefix_survives_reopen_and_remains_resumable(persisted, monkeypatch):
    persisted.visual_translation = True
    persisted.total_duration = 48
    persisted._visual_incremental_started = True
    persisted._visual_completed_seconds = 24
    persisted._visual_prepass_complete = False
    prefix = persisted.segments[0]
    prefix.source_method, prefix.translation_provider = "text-ai", "opencode"
    later = SegmentItem(1, 24, 26, 2)
    later.text_zh = "下一句"
    persisted.segments[1] = later
    audio_before = Path(prefix.audio_path).read_bytes()
    persisted.error = "Muse timed out after first chunk"
    persisted.persist()
    monkeypatch.setattr("core.video_intelligence.validate_visual_provider", Mock(side_effect=AssertionError("no provider on history open")))
    for _ in range(2):
        active_streaming_sessions.clear()
        restored = restore_saved_session(persisted.task_id)
        assert restored.initialized and restored.can_retry and restored._startup_failed
        assert restored.get_progress()["status"] == "FAILED"
        assert restored.playable_until == 24
        assert restored.segments[0].final_vi == prefix.final_vi
        assert restored.segments[0].status == "READY"
        assert Path(restored.segments[0].audio_path).read_bytes() == audio_before
        assert restored.segments[1].status == "FAILED"
        assert restored.video_intelligence is None
        restored.persist()


def test_unfinished_silent_visual_tail_never_becomes_completed(persisted):
    persisted.visual_translation = True
    persisted.total_duration = 48
    persisted._visual_incremental_started = True
    persisted._visual_completed_seconds = 24
    persisted._visual_prepass_complete = False
    persisted.review_summary = {"status": "completed", "checked": 1, "verified": 1}
    persisted.segments[0].source_method = "text-ai"
    persisted.segments[0].translation_provider = "opencode"
    persisted.persist()
    listed = list_saved_sessions()[0]
    assert listed["status"] != "COMPLETED" and listed["progress_pct"] is None
    assert "Dịch video chưa xong" in listed["stage"]
    restored = restore_saved_session(persisted.task_id)
    assert restored.playable_until == 24
    assert restored.can_retry and restored._startup_failed
    assert restored.get_progress()["status"] == "STOPPED"


def test_finished_visual_prepass_survives_reopen(persisted):
    persisted.visual_translation = True
    persisted._visual_incremental_started = True
    persisted._visual_completed_seconds = persisted.total_duration
    persisted._visual_prepass_complete = True
    persisted.segments[0].source_method = "text-ai"
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    assert restored.get_progress()["status"] == "COMPLETED"
    assert not restored.can_retry and not restored._startup_failed
    assert restored.playable_until == persisted.total_duration


@pytest.mark.parametrize("field,value", [
    ("_visual_completed_seconds", -1), ("_visual_completed_seconds", 3),
    ("_visual_completed_seconds", True), ("_visual_incremental_started", "yes"),
    ("_visual_prepass_complete", 1),
])
def test_invalid_saved_visual_progress_is_rejected(persisted, field, value):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["session"][field] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        restore_saved_session(persisted.task_id)


def test_source_duration_alone_is_not_translated_ready(persisted):
    persisted.initialized = False
    persisted.total_duration = 320
    persisted.segments.clear()
    persisted._recalculate_telemetry()
    assert persisted.playable_until == 0 and persisted.buffer_ahead == 0
