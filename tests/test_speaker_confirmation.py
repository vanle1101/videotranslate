"""Offline regression tests for actual user-proof transactions, not provider acceptance."""
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import wave

import httpx
import pytest

import main
from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, SegmentEditConflict, ProjectEditSaveError
from core.streaming.session_store import _read, _project_path, restore_saved_session
from core.streaming.speaker_confirmation import validate_confirmation
from core.translation_context import dialogue_context, source_dialogue, address_reading_prompt


@pytest.fixture
def session(tmp_path, monkeypatch):
    for name, value in {"BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace/inputs", "OUTPUT_DIR": tmp_path / "workspace/outputs",
        "TEMP_DIR": tmp_path / "workspace/temp"}.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "offline-fixture.mp4"
    source.write_bytes(b"offline source fixture, no provider or media acceptance claim")
    sess = StreamingPipelineSession("speaker-proof-test", source, tts_engine_name="edge-tts",
                                    voice="vi-VN-HoaiMyNeural")
    sess.initialized, sess.total_duration = True, 12.
    for sid, speaker, scope in [(0, "voice-000000", "scope-a"), (1, "voice-000000", "scope-a"),
                                 (2, "voice-000000", "scope-b"), (3, None, "scope-a"),
                                 (4, "voice-000001", "scope-a")]:
        row = SegmentItem(sid, sid * 2., sid * 2. + 1.5, 1.5)
        row.status, row.text_zh, row.final_vi = "READY", "我很好", f"Lời nháp {sid}."
        row.speaker_id = speaker
        row.speaker_evidence = {"speaker_id": speaker, "scope_id": scope,
                                "method": "audio_diarization", "model": "offline-fixture", "verified": False}
        row.verification = {"status": "verified", "semantic_verified": True, "address_verified": True}
        row.audio_path = str(sess.segments_dir / f"seg_{sid}.wav")
        row.audio_url = f"/api/streaming/audio/{sess.task_id}/{sid}"
        with wave.open(row.audio_path, "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x00" * 2400)
        sess.segments[sid] = row
    sess.persist()
    registry = {sess.task_id: sess}
    monkeypatch.setattr(main, "active_streaming_sessions", registry)
    monkeypatch.setattr("core.streaming.pipeline.active_streaming_sessions", registry)
    monkeypatch.setattr(main, "active_export_tasks", {})
    return sess


def selection(session, sid=0, **extra):
    return dict(anchor_segment_id=sid, expected_revision=session.segments[sid].revision,
                label="Nhân vật A", **extra)


def test_scoped_voice_applies_only_same_non_null_identity_and_scope(session):
    old = {sid: deepcopy(vars(row)) for sid, row in session.segments.items()}
    files = {sid: Path(row.audio_path).read_bytes() for sid, row in session.segments.items()}
    result = session.confirm_speaker(**selection(session, apply_same_voice=True, self_address="con", listener_address="bố"))
    assert result["affected_ids"] == [0, 1]
    assert [row["id"] for row in result["segments"]] == [0, 1]
    for sid in (0, 1):
        row = session.segments[sid]
        assert row.speaker_confirmation["method"] == "user_confirmation"
        assert row.needs_review and row.verification["semantic_verified"] is False
        assert row.final_vi == old[sid]["final_vi"]
        assert row.speaker_evidence == old[sid]["speaker_evidence"]
        assert Path(row.audio_path).read_bytes() == files[sid]
    for sid in (2, 3, 4):
        assert vars(session.segments[sid]) == old[sid]
    assert session.get_progress()["content_review_state"] == "REVIEW_REQUIRED"
    assert session.segments[0].speaker_review_pending and session.segments[1].speaker_review_pending


@pytest.mark.parametrize("missing", ["speaker_id", "scope_id"])
def test_unknown_identity_or_scope_never_expands_to_other_unknown_rows(session, missing):
    row = session.segments[0]
    if missing == "speaker_id":
        row.speaker_id = row.speaker_evidence["speaker_id"] = None
    else:
        row.speaker_evidence["scope_id"] = None
    result = session.confirm_speaker(**selection(session, apply_same_voice=True))
    assert result["affected_ids"] == [0]
    assert result["speaker_confirmation"]["selection"] == "anchor"
    assert session.segments[3].speaker_confirmation is None


def test_confirmation_and_manual_text_survive_repeated_real_manifest_restore(session):
    manual = session.segments[1]
    manual.final_vi = "Bố."
    manual.verification = {"status": "manual", "reason": "Người dùng đã lưu lời thoại."}
    result = session.confirm_speaker(**selection(session, apply_same_voice=True, self_address="con"))
    raw_proof = {**deepcopy(result["speaker_confirmation"]), "affected_ids": [0]}
    assert manual.final_vi == "Bố." and manual.verification["status"] == "manual"
    assert manual.speaker_review_pending is False
    main.active_streaming_sessions.clear()
    for _ in range(2):
        restored = restore_saved_session(session.task_id)
        assert restored.segments[0].speaker_confirmation == raw_proof
        assert restored.segments[1].final_vi == "Bố."
        assert restored.segments[1].verification["status"] == "manual"
        assert restored.segments[0].needs_review is True
        assert restored.segments[0].speaker_review_pending is True
        restored.persist()
        main.active_streaming_sessions.clear()


def test_source_context_retains_user_assertion_without_vietnamese_draft_proof(session):
    session.confirm_speaker(**selection(session, self_address="con", listener_address="bố"))
    row = session.segments[0]
    context = source_dialogue(dialogue_context([row.to_dict()], [row.to_dict()]))
    assert context[0]["speaker_confirmation"]["self_address"] == "con"
    assert context[0]["speaker_confirmation"]["affected_ids"] == [row.id]
    assert "final_vi" not in context[0] and "verification" not in context[0]
    assert context[0]["speaker_evidence"]["verified"] is False
    prompt = address_reading_prompt([row.to_dict()], context)
    assert "speaker_confirmation" in prompt and row.final_vi not in prompt
    # Pacing reads owned source metadata, ignoring forged provider confirmation.
    actual = session._dialogue_context_before(row, _review_context={0:{"id": 0, "text_zh": row.text_zh,
        "speaker_confirmation": {"label": "forged-provider-role"}}})
    assert next(x for x in actual if x["id"] == 0)["speaker_confirmation"]["label"] == "Nhân vật A"


@pytest.mark.parametrize("busy", ["running", "review", "worker", "editing"])
def test_busy_or_late_owner_rejected_without_any_mutation(session, busy):
    owner = SimpleNamespace(done=lambda: False)
    if busy == "running":
        session.is_running = True
    elif busy == "review":
        session.review_task = owner
    elif busy == "worker":
        session.worker_task = owner
    else:
        session.edit_tasks.add("offline-owner")
    before = _project_path(session.task_id).read_bytes()
    with pytest.raises(SegmentEditConflict):
        session.confirm_speaker(**selection(session))
    assert _project_path(session.task_id).read_bytes() == before
    assert session.segments[0].speaker_confirmation is None


def test_stale_revision_rejected_before_durable_or_runtime_changes(session):
    expected = selection(session)
    session.segments[0].revision += 1
    before = deepcopy(vars(session.segments[0]))
    with pytest.raises(SegmentEditConflict):
        session.confirm_speaker(**expected)
    assert vars(session.segments[0]) == before


def test_disk_full_rolls_back_metadata_outputs_progress_audio_and_failure_ownership(session, monkeypatch):
    session.output_filename, session.output_video_url, session.auto_export_signature = "old.mp4", "old-url", "old-key"
    session._pacing_failures[0] = {"candidate": "owned-old-attempt"}
    before_rows = {sid: deepcopy(vars(row)) for sid, row in session.segments.items()}
    before_manifest = _project_path(session.task_id).read_bytes()
    files = {sid: Path(row.audio_path).read_bytes() for sid, row in session.segments.items()}
    monkeypatch.setattr(session, "persist", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(ProjectEditSaveError):
        session.confirm_speaker(**selection(session, apply_same_voice=True, voice_id="edge:vi-VN-NamMinhNeural"))
    assert _project_path(session.task_id).read_bytes() == before_manifest
    assert session.output_filename == "old.mp4" and session.auto_export_signature == "old-key"
    assert session._pacing_failures == {0: {"candidate": "owned-old-attempt"}}
    for sid, row in session.segments.items():
        assert vars(row) == before_rows[sid]
        assert Path(row.audio_path).read_bytes() == files[sid]


def test_only_traceable_address_dependents_are_invalidated(session):
    session.segments[4].verification["address_context_sources"] = {"0": "我很好"}
    result = session.confirm_speaker(**selection(session))
    assert [row["id"] for row in result["segments"]] == [0, 4]
    assert result["affected_ids"] == [0]
    assert session.segments[4].speaker_confirmation is None
    assert session.segments[4].needs_review
    assert session.segments[4].speaker_review_pending
    assert not session.segments[1].needs_review


def test_explicit_edge_voice_override_keeps_old_wav_and_can_retry_only_selected_speech(session):
    row = session.segments[0]
    old = Path(row.audio_path).read_bytes()
    result = session.confirm_speaker(**selection(session, voice_id="edge:vi-VN-NamMinhNeural"))
    assert row.voice_id == "edge:vi-VN-NamMinhNeural" and row.tts_voice_outdated
    assert row.status == "FAILED" and row.failed_stage == "TTS"
    assert session._segment_voice(row) == "vi-VN-NamMinhNeural"
    assert session._segment_voice(session.segments[1]) == "vi-VN-HoaiMyNeural"
    assert Path(row.audio_path).read_bytes() == old
    assert result["progress"]["can_retry"] is True
    session.is_stopped = True
    assert session.can_retry  # Retry explicitly clears Stop; confirmation does not.
    record = _read(session.task_id)
    assert record["segments"][0]["tts_voice_outdated"] is True
    assert record["segments"][0]["audio_path"] == row.audio_path


@pytest.mark.parametrize("payload", [{"label": " "}, {"label": "bad\x00name"},
    {"voice_id": "vieneu:Trúc Ly"}, {"voice_id": "edge:made-up"},
    {"anchor_segment_id": True}, {"expected_revision": True}])
def test_invalid_request_does_not_modify_manifest(session, payload):
    request = selection(session)
    request.update(payload)
    original = _project_path(session.task_id).read_bytes()
    with pytest.raises(ValueError):
        session.confirm_speaker(**request)
    assert _project_path(session.task_id).read_bytes() == original


def test_durable_schema_rejects_missing_fields_or_injected_verification(session):
    proof = session.confirm_speaker(**selection(session))["speaker_confirmation"]
    for altered in ({**proof, "semantic_verified": True}, {key: val for key, val in proof.items() if key != "method"},
                    {**proof, "affected_ids": [{"id": 0}]}, {**proof, "method": "model_guess"}):
        with pytest.raises(ValueError):
            validate_confirmation(altered)


def test_unchanged_confirmation_is_idempotent_and_does_not_force_another_review(session, monkeypatch):
    first = session.confirm_speaker(**selection(session, self_address="con"))
    original_manifest = _project_path(session.task_id).read_bytes()
    revision = session.segments[0].revision
    monkeypatch.setattr(session, "persist", lambda: (_ for _ in ()).throw(AssertionError("unexpected save")))
    second = session.confirm_speaker(**selection(session, self_address="con"))
    assert first["speaker_confirmation"] == second["speaker_confirmation"]
    assert session.segments[0].revision == revision
    assert _project_path(session.task_id).read_bytes() == original_manifest


def test_new_confirmation_changes_source_only_cache_identity_without_using_draft(session):
    from core.video_intelligence import VideoIntelligence
    row = session.segments[0]
    def identity():
        return VideoIntelligence._checkpoint_digest(source_dialogue(dialogue_context([row.to_dict()], [row.to_dict()])))
    old = identity()
    session.confirm_speaker(**selection(session, self_address="con"))
    new = identity()
    assert old != new
    row.final_vi = "Một nháp khác không thể làm bằng chứng."
    assert identity() == new


def test_large_scoped_selection_stores_linear_exact_row_claims_not_repeated_global_lists(session):
    # Simulated metadata workload only; no long-video/media/provider claim.
    session.segments.clear()
    count = 256
    session.total_duration = count
    for sid in range(count):
        row = SegmentItem(sid, float(sid), float(sid + 1), 1.)
        row.speaker_id = "voice-000000"
        row.speaker_evidence = {"speaker_id": row.speaker_id, "scope_id": "large-fixture",
                                "verified": False, "method": "audio_diarization"}
        row.text_zh = "原文"
        session.segments[sid] = row
    result = session.confirm_speaker(**selection(session, apply_same_voice=True))
    assert result["affected_ids"] == list(range(count))
    stored = _read(session.task_id)["segments"]
    assert all(row["speaker_confirmation"]["affected_ids"] == [row["id"]] for row in stored)
    assert len({row["speaker_confirmation"]["confirmation_id"] for row in stored}) == 1
    assert sum(len(row["speaker_confirmation"]["affected_ids"]) for row in stored) == count


@pytest.mark.parametrize("disk_failure", [False, True])
def test_voice_regeneration_publishes_versioned_wav_only_after_durable_commit(session, monkeypatch, disk_failure):
    # This offline test exercises real transaction/state logic with an injected
    # finite WAV producer. It makes no Edge provider quality/availability claim.
    row = session.segments[0]
    row.verification = {"status": "manual", "reason": "Lời người dùng đã xác nhận."}
    session.confirm_speaker(**selection(session, voice_id="edge:vi-VN-NamMinhNeural"))
    old_path = Path(row.audio_path)
    old_bytes, old_manifest = old_path.read_bytes(), _project_path(session.task_id).read_bytes()
    calls = []
    async def fit(segment, *, output_path, text, translator, **kwargs):
        calls.append((session._segment_voice(segment), text, translator))
        with wave.open(str(output_path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x02\x00" * 2400)
        return ({"text": text, "tts_duration": .1, "speed_ratio": 1., "boundaries": []},
                {"subtitle_cues": [], "subtitle_timing_source": "offline-fixture",
                 "speech_start": row.start, "speech_end": row.start + .1}, {})
    monkeypatch.setattr(session, "_fit_dub", fit)
    if disk_failure:
        monkeypatch.setattr(session, "persist", lambda: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(ProjectEditSaveError):
            asyncio.run(session._synthesize_segment(row))
        assert _project_path(session.task_id).read_bytes() == old_manifest
        assert row.audio_path == str(old_path) and row.tts_voice_outdated
        assert old_path.read_bytes() == old_bytes
        assert list(session.segments_dir.glob("seg_0_*.wav")) == []
    else:
        asyncio.run(session._synthesize_segment(row))
        assert row.status == "READY" and not row.tts_voice_outdated
        assert Path(row.audio_path) != old_path
        assert _read(session.task_id)["segments"][0]["audio_path"] == row.audio_path
        assert Path(row.audio_path).read_bytes() != old_bytes
        assert not old_path.exists()
    assert calls == [("vi-VN-NamMinhNeural", row.final_vi, None)]
    assert row.verification["status"] == "manual"  # never rewrite manual wording for timing


def test_api_event_failure_after_commit_does_not_report_the_durable_confirmation_failed(session, monkeypatch):
    async def fail_event(*args, **kwargs):
        raise RuntimeError("offline transport disconnected")
    monkeypatch.setattr(main, "broadcast_session_event", fail_event)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            result = await api.patch(f"/api/streaming/{session.task_id}/speaker-confirmation", json=selection(session))
            assert result.status_code == 200
            assert _read(session.task_id)["segments"][0]["speaker_confirmation"] == result.json()["speaker_confirmation"]
    asyncio.run(run())


def reviewed_rows(targets, *, status="verified"):
    rows = {}
    for row in targets:
        rows[row.id] = {**row.to_dict(), "needs_review": status == "unresolved", "review_reason": "Căn cứ mới đã được rà.",
                       "verification": {"status": status, "semantic_verified": status == "verified"}}
    return {"segments": rows, "summary": {"checked": len(rows), status: len(rows)}}


def test_explicit_review_targets_only_pending_confirmation_and_retains_full_context(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    session.segments[4].verification["address_context_sources"] = {"0": session.segments[0].text_zh}
    session.confirm_speaker(**selection(session))
    session.segments[0].source_method = session.segments[4].source_method = "video-ai"
    untouched = {sid: deepcopy(vars(row)) for sid, row in session.segments.items() if sid not in {0, 4}}
    calls = []
    def review(_self, source, targets, screens, **kwargs):
        calls.append(([row.id for row in targets], [row.id for row in kwargs["context_segments"]], kwargs["force_review"]))
        return reviewed_rows(targets)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    async def run():
        await session.start_automatic_review()
        await session.review_task
    asyncio.run(run())
    assert calls == [([0, 4], list(range(5)), True)]
    assert not session.segments[0].speaker_review_pending and not session.segments[4].speaker_review_pending
    assert all(vars(session.segments[sid]) == state for sid, state in untouched.items())
    assert not _read(session.task_id)["segments"][0]["speaker_review_pending"]


@pytest.mark.parametrize("fault", ["provider", "incomplete", "stale", "disk"])
def test_review_errors_incomplete_stale_and_disk_failure_keep_durable_pending_ownership(session, monkeypatch, fault):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    session.confirm_speaker(**selection(session))
    row = session.segments[0]
    proof = deepcopy(row.speaker_confirmation)
    old_manifest = _project_path(session.task_id).read_bytes()
    old_audio = Path(row.audio_path).read_bytes()
    def review(_self, source, targets, screens, **kwargs):
        if fault == "provider":
            raise RuntimeError("offline injected provider failure")
        if fault == "stale":
            row.revision += 1
            row.final_vi = "Bản sửa mới của người dùng."
            return reviewed_rows(targets)
        return reviewed_rows(targets, status="incomplete" if fault == "incomplete" else "verified")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    if fault == "disk":
        monkeypatch.setattr(session, "persist", lambda: (_ for _ in ()).throw(OSError("disk full")))
        # Avoid earlier generic status saves, target the actual verdict commit.
        monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    async def run():
        if fault == "disk":
            with pytest.raises(ProjectEditSaveError):
                await session._review_translations(segment_ids={0})
        else:
            await session.start_automatic_review()
            await session.review_task
    asyncio.run(run())
    assert row.speaker_review_pending is True and row.speaker_confirmation == proof
    assert Path(row.audio_path).read_bytes() == old_audio
    if fault == "stale":
        assert row.final_vi == "Bản sửa mới của người dùng."
    if fault == "disk":
        assert _project_path(session.task_id).read_bytes() == old_manifest
        assert row.verification["status"] == "incomplete"


@pytest.mark.parametrize("chunked", [False, True])
def test_resume_recovers_pending_video_ai_rows_only_with_manual_rows_untouched(session, monkeypatch, chunked):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    monkeypatch.setattr(settings, "DIARIZATION_ENABLED", False)
    session.segments[4].verification["address_context_sources"] = {"0": session.segments[0].text_zh}
    session.segments[1].verification = {"status": "manual"}
    manual = deepcopy(vars(session.segments[1]))
    session.confirm_speaker(**selection(session))
    for row in session.segments.values():
        row.source_method, row.translation_provider = "video-ai", "opencode"
    manual = deepcopy(vars(session.segments[1]))
    session._chunked_source_started, session.visual_translation = chunked, True
    session._visual_completed_seconds = session.total_duration
    calls = []
    def review(_self, source, targets, screens, **kwargs):
        calls.append(([row.id for row in targets], len(kwargs["context_segments"]), kwargs["force_review"]))
        return reviewed_rows(targets, status="unresolved")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    session.is_running = True
    asyncio.run(session._resume_pending_chunk_reviews())
    assert calls == [([0, 4], 5, True)]
    assert all(not session.segments[sid].speaker_review_pending for sid in (0, 4))
    assert session.segments[0].needs_review  # genuine negative verdict stays negative
    assert vars(session.segments[1]) == manual


def test_resume_provider_error_keeps_confirmation_pending_without_marking_unrelated_rows(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    monkeypatch.setattr(settings, "DIARIZATION_ENABLED", False)
    session.confirm_speaker(**selection(session))
    for row in session.segments.values():
        row.source_method = "video-ai"
    unrelated = {sid: deepcopy(vars(row)) for sid, row in session.segments.items() if sid != 0}
    def fail(*args, **kwargs):
        raise RuntimeError("offline injected provider error")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", fail)
    session.is_running = True
    asyncio.run(session._resume_pending_chunk_reviews())
    assert session.segments[0].speaker_review_pending
    assert _read(session.task_id)["segments"][0]["speaker_review_pending"]
    assert all(vars(session.segments[sid]) == before for sid, before in unrelated.items())


def test_changed_review_text_clears_marker_only_with_real_edit_transaction_commit(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    session.confirm_speaker(**selection(session))
    row = session.segments[0]
    old_path = Path(row.audio_path)
    def review(_self, source, targets, screens, **kwargs):
        result = reviewed_rows(targets)
        result["segments"][0]["final_vi"] = "Lời mới đã rà với bằng chứng người dùng."
        return result
    async def fit(segment, *, output_path, text, **kwargs):
        with wave.open(str(output_path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x02\x00" * 2400)
        return ({"text": text, "tts_duration": .1, "speed_ratio": 1., "boundaries": []},
                {"subtitle_cues": [], "subtitle_timing_source": "offline-fixture",
                 "speech_start": row.start, "speech_end": row.start + .1}, {})
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    monkeypatch.setattr(session, "_fit_dub", fit)
    asyncio.run(session._review_translations(regenerate_audio=True, segment_ids={0}, force_review=True))
    assert row.final_vi == "Lời mới đã rà với bằng chứng người dùng."
    assert not row.speaker_review_pending
    saved = _read(session.task_id)["segments"][0]
    assert not saved["speaker_review_pending"] and saved["final_vi"] == row.final_vi
    assert Path(saved["audio_path"]).is_file() and Path(saved["audio_path"]) != old_path


@pytest.mark.parametrize("value", ["false", 1, None, [], {}])
def test_pending_speaker_review_requires_strict_boolean_in_manifest(session, value):
    from core.streaming.session_store import save_session
    original = _project_path(session.task_id).read_bytes()
    session.segments[0].speaker_review_pending = value
    with pytest.raises(ValueError):
        save_session(session)
    assert _project_path(session.task_id).read_bytes() == original


@pytest.mark.parametrize("mode,code", [("normal", 200), ("stale", 409), ("busy", 409), ("export", 409),
                                      ("missing", 404), ("disk", 507), ("invalid", 422)])
def test_real_api_contract_validation_status_and_persistence(session, monkeypatch, mode, code):
    payload = selection(session)
    if mode == "stale":
        payload["expected_revision"] = 99
    elif mode == "busy":
        session.is_running = True
    elif mode == "export":
        main.active_export_tasks[f"export_{session.task_id}"] = {"status": "RUNNING"}
    elif mode == "missing":
        payload["anchor_segment_id"] = 99
    elif mode == "disk":
        monkeypatch.setattr(session, "persist", lambda: (_ for _ in ()).throw(OSError("full")))
    elif mode == "invalid":
        payload["unowned_provider_guess"] = True
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            response = await api.patch(f"/api/streaming/{session.task_id}/speaker-confirmation", json=payload)
            assert response.status_code == code, response.text
            if code == 200:
                result = response.json()
                assert set(result) == {"segments", "affected_ids", "speaker_confirmation", "progress"}
                assert result["affected_ids"] == [0] and result["segments"][0]["needs_review"] is True
                assert _read(session.task_id)["segments"][0]["speaker_confirmation"] == result["speaker_confirmation"]
    asyncio.run(run())
