"""Fault injection for a reviewed correction whose replacement speech cannot fit.

Synthetic PCM and a deterministic review response exercise publication and
recovery ownership only; these tests do not claim provider or real-video QA.
"""
import asyncio
from copy import deepcopy
from pathlib import Path
import wave

import pytest

from config import settings
from core.streaming import pipeline
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.streaming.session_store import _read
from core.translation_review import AutomaticTranslationReviewer


@pytest.fixture
def reviewed_session(tmp_path, monkeypatch):
    for name, value in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace/inputs", "OUTPUT_DIR": tmp_path / "workspace/outputs",
        "TEMP_DIR": tmp_path / "workspace/temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "fixture.mp4"
    source.write_bytes(b"source placeholder for isolated lifecycle test; never rendered")
    session = StreamingPipelineSession("review-pacing", source, tts_engine_name="edge-tts")
    session.initialized, session.total_duration = True, 180.
    session._chunked_source_started, session._source_prepared_seconds = True, 180.
    session.is_running = True
    session.tts_engine = object()
    monkeypatch.setattr(session, "_ensure_tts_engine", lambda: None)
    focus = SegmentItem(112, 169.9, 170.84, .94)
    focus.status, focus.text_zh, focus.final_vi = "READY", "旧文本", "Lời cũ."
    focus.audio_path = str(session.segments_dir / "seg_112.wav")
    with wave.open(focus.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x10\x00" * 12000)
    focus.verification = {"status": "verified", "semantic_verified": True}
    following = SegmentItem(113, 170.84, 171.68, .84)
    following.status, following.text_zh, following.final_vi = "WAITING", "旧续句", "Để đùa đâu."
    session.segments = {112: focus, 113: following}
    return session


def review_response():
    return {"segments": {
        112: {"id": 112, "text_zh": "我真的不是回来", "final_vi": "Thật sự không phải về đây.",
            "needs_review": False, "verification": {"status": "corrected", "semantic_verified": True}},
        113: {"id": 113, "text_zh": "跟你开玩笑的", "final_vi": "Để đùa đâu.",
            "needs_review": False, "verification": {"status": "verified", "semantic_verified": True}},
    }, "summary": {"checked": 2, "verified": 1, "corrected": 1, "unresolved": 0, "manual": 0}}


def source_scope_recovery_row(session):
    session.visual_translation = True
    session._visual_completed_seconds = session.total_duration
    session._visual_prepass_complete = True
    focus = session.segments[112]
    focus.asr_text, focus.text_zh = "小满", "小满今年十九"
    focus.final_vi = "Tiểu Mãn, năm nay mười chín."
    focus.source_method, focus.translation_provider = "text-ai", "opencode"
    focus.status, focus.failed_stage = "FAILED", "TTS"
    focus.verification = {"status": "corrected", "source_supported": True,
        "semantic_verified": True, "evidence": [{"text_zh": focus.text_zh}]}
    return focus


def source_scope_terminal_response(session, status):
    focus = session.segments[112]
    return {"segments": {112: {"id": 112, "text_zh": focus.text_zh,
        "final_vi": focus.final_vi, "needs_review": status == "unresolved",
        "review_reason": "Role remains uncertain" if status == "unresolved" else None,
        "verification": {"status": status, "provider": "opencode", "model": settings.OPENCODE_MODEL,
            "source_supported": True, "semantic_verified": status != "unresolved",
            "second_pass_status": "completed",
            "review_gate_revision": AutomaticTranslationReviewer.REVIEW_GATE_REVISION,
            "evidence": [{"text_zh": focus.text_zh}]}}},
        "summary": {"checked": 1, status: 1}}


@pytest.mark.parametrize("status", ["corrected", "unresolved"])
def test_unchanged_source_scope_review_survives_restart_without_repeat_provider_call(
        reviewed_session, monkeypatch, status):
    from core.streaming.session_store import restore_saved_session
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    calls = []
    def review(*args, **kwargs):
        calls.append([row.id for row in args[2]])
        return source_scope_terminal_response(session, status)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    session.persist()
    restored = restore_saved_session(session.task_id)
    try:
        assert restored.segments[112].status == "FAILED"
        asyncio.run(restored._resume_pending_chunk_reviews())
        assert calls == [[112]], "Unchanged scoped speech retry must not rerun semantic review"
        assert restored.segments[112].verification["status"] == status
        assert restored.segments[112].verification["semantic_verified"] is (status != "unresolved")
        assert restored.segments[112].final_vi == focus.final_vi
    finally:
        pipeline.active_streaming_sessions.pop(session.task_id, None)


@pytest.mark.parametrize("change", ["source", "asr", "neighbor", "neighbor_asr", "confirmation",
                                   "model", "gate", "implementation", "translation", "video"])
def test_source_scope_resume_identity_invalidates_changed_owned_inputs(reviewed_session, monkeypatch, change):
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    calls = []
    def review(*args, **kwargs):
        calls.append(1)
        return source_scope_terminal_response(session, "unresolved")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert session._source_scope_review_is_current(focus)
    if change == "source":
        focus.text_zh += "岁"
    elif change == "asr":
        focus.asr_text = "我是小满"
    elif change == "neighbor":
        session.segments[113].text_zh = "爸爸"
    elif change == "neighbor_asr":
        session.segments[113].asr_text = "爸爸我回来了"
    elif change == "confirmation":
        focus.speaker_confirmation = {"speaker_id": None, "self_address": "con",
            "listener_address": "bố", "method": "user_confirmation", "anchor_segment_id": focus.id,
            "confirmation_id": "b" * 32, "affected_ids": [focus.id], "scope_id": None,
            "label": "Con gái", "voice_id": None, "created_at": 10., "selection": "anchor"}
    elif change == "model":
        monkeypatch.setattr(settings, "OPENCODE_MODEL", "different-real-model-identity")
    elif change == "gate":
        monkeypatch.setattr(AutomaticTranslationReviewer, "REVIEW_GATE_REVISION",
                            AutomaticTranslationReviewer.REVIEW_GATE_REVISION + 1)
    elif change == "implementation":
        monkeypatch.setattr("core.review_checkpoint.loaded_implementation_revision", lambda: {"review": "changed"})
    elif change == "translation":
        focus.final_vi += " Có một bản nháp khác."
    else:
        session.video_path.write_bytes(b"changed source identity")
    assert not session._source_scope_review_is_current(focus)
    focus.status, focus._retry_synthesis = "WAITING", True
    asyncio.run(session._resume_pending_chunk_reviews())
    assert calls == [1, 1]
    assert focus.verification["status"] == "unresolved"
    assert focus.verification["semantic_verified"] is False


def test_source_scope_stamp_ignores_speech_lifecycle_and_keeps_tts_recovery(reviewed_session, monkeypatch):
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    calls, speech = [], []
    def review(*args, **kwargs):
        calls.append(1)
        return source_scope_terminal_response(session, "unresolved")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    focus.status, focus.failed_stage = "WAITING", "ALIGNING"
    focus.revision += 3
    focus._retry_synthesis, focus.error = True, "Transient speech error"
    focus.audio_path = None
    focus.tts_duration, focus.speed_ratio = 1.7, 1.15
    async def synthesize(row, **kwargs):
        speech.append(row.id)
        row.status = "READY"
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    monkeypatch.setattr("core.streaming.speaker_source.recover_speaker_evidence", _no_speaker_recovery)
    asyncio.run(session._resume_pending_chunk_reviews(synthesize_pending=True))
    assert calls == [1]
    assert 112 in speech and focus.status == "READY"
    assert focus.verification["status"] == "unresolved" and focus.needs_review


async def _no_speaker_recovery(session):
    return None


@pytest.mark.parametrize("failure", ["incomplete", "provider", "source_changed_during_review",
                                     "video_changed_during_review", "cancelled", "forged_stamp"])
def test_interrupted_or_changed_review_cannot_publish_source_scope_stamp(reviewed_session, monkeypatch, failure):
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    calls = []
    def review(*args, **kwargs):
        calls.append(1)
        if failure == "provider":
            raise TimeoutError("injected provider failure")
        if failure == "cancelled":
            session.is_stopped = True
            raise asyncio.CancelledError
        response = source_scope_terminal_response(session, "corrected")
        audit = response["segments"][112]["verification"]
        if failure in {"incomplete", "forged_stamp"}:
            audit.update(status="incomplete", semantic_verified=False, second_pass_status="failed")
            if failure == "forged_stamp":
                audit["source"] = {"method": session.SOURCE_SCOPE_REVIEW_METHOD,
                    "version": session.SOURCE_SCOPE_REVIEW_VERSION,
                    "input_hash": session._source_scope_review_identity(focus)}
        elif failure == "source_changed_during_review":
            session.segments[113].text_zh = "An independently changed source"
        elif failure == "video_changed_during_review":
            session.video_path.write_bytes(b"source changed while reviewer read it")
        return response
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(session._resume_pending_chunk_reviews())
    else:
        asyncio.run(session._resume_pending_chunk_reviews())
    assert not session._source_scope_review_is_current(focus)
    assert "source" not in focus.verification
    assert calls == [1]


def test_group_source_corrections_are_visible_before_scope_stamp_publication(reviewed_session, monkeypatch):
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    following = session.segments[113]
    following.text_zh, following.asr_text, following.final_vi = "片段", "片", "Phần thoại."
    following.status, following.failed_stage = "FAILED", "TTS"
    following.source_method, following.translation_provider = "text-ai", "opencode"
    following.verification = deepcopy(focus.verification)
    calls = []
    def review(*args, **kwargs):
        calls.append(1)
        response = source_scope_terminal_response(session, "unresolved")
        response["segments"][113] = {"id": 113, "text_zh": "爸爸我回来了", "final_vi": "Con về rồi, bố.",
            "verification": {**deepcopy(response["segments"][112]["verification"]),
                             "evidence": [{"text_zh": "爸爸我回来了"}]}, "needs_review": True}
        return response
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert session._source_scope_review_is_current(focus)
    assert session._source_scope_review_is_current(following)
    focus.status = following.status = "FAILED"
    asyncio.run(session._resume_pending_chunk_reviews())
    assert calls == [1]


def test_scope_stamp_disk_failure_rolls_back_only_unpublished_stamp(reviewed_session, monkeypatch):
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review",
                        lambda *args, **kwargs: source_scope_terminal_response(session, "unresolved"))
    persist = session.persist
    def fail_stamp():
        if (focus.verification or {}).get("source"):
            raise OSError("injected scope-stamp disk full")
        return persist()
    monkeypatch.setattr(session, "persist", fail_stamp)
    with pytest.raises(pipeline.ProjectEditSaveError):
        asyncio.run(session._review_translations(segment_ids={112}))
    assert focus.verification["status"] == "unresolved"
    assert not session._source_scope_review_is_current(focus)
    assert "source" not in focus.verification
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == 112)
    assert saved["verification"]["status"] == "unresolved" and "source" not in saved["verification"]


def test_distant_unselected_source_does_not_invalidate_scope_retry_identity(reviewed_session, monkeypatch):
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    for sid in range(200, 300):
        row = SegmentItem(sid, sid - 199., sid - 198.5, .5)
        row.text_zh = "别的句子"
        session.segments[sid] = row
    calls = []
    def review(*args, **kwargs):
        calls.append(1)
        return source_scope_terminal_response(session, "unresolved")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    earlier = SegmentItem(400, 0., .5, .5)
    earlier.text_zh = "一个很早的句子"
    session.segments[400] = earlier
    assert session._source_scope_review_is_current(focus)
    focus.status = "FAILED"
    asyncio.run(session._resume_pending_chunk_reviews())
    assert calls == [1]


def test_fresh_manual_commit_cannot_receive_older_scope_stamp(reviewed_session, monkeypatch):
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    def review(*args, **kwargs):
        response = source_scope_terminal_response(session, "corrected")
        focus.revision += 1
        focus.final_vi = "Lời người dùng đã sửa trong lúc đang rà."
        focus.verification = {"status": "manual"}
        return response
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert focus.verification == {"status": "manual"}
    assert focus.final_vi == "Lời người dùng đã sửa trong lúc đang rà."
    assert not session._source_scope_review_is_current(focus)


def test_source_scope_stamp_covers_context_selected_for_other_batch_targets(reviewed_session, monkeypatch):
    from core.streaming.session_store import restore_saved_session
    session = reviewed_session
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    focus = source_scope_recovery_row(session)
    session.total_duration = 700.
    for sid in range(200, 300):
        row = SegmentItem(sid, 180. + (sid - 200) * 2, 181. + (sid - 200) * 2, 1.)
        row.text_zh = "别的句子。"
        session.segments[sid] = row
    sibling = SegmentItem(1000, 600., 601., 1.)
    sibling.text_zh, sibling.asr_text, sibling.final_vi = "另一句话。", "另一句", "Một câu khác."
    neighbor = SegmentItem(1001, 602., 603., 1.)
    neighbor.text_zh = "最后一句。"
    session.segments.update({1000: sibling, 1001: neighbor})
    calls = []
    def review(*args, **kwargs):
        calls.append(1)
        response = source_scope_terminal_response(session, "unresolved")
        row = deepcopy(response["segments"][112])
        row.update(id=1000, text_zh=sibling.text_zh, final_vi=sibling.final_vi)
        response["segments"][1000] = row
        return response
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._review_translations(segment_ids={112, 1000}))
    assert set(focus.verification["source"]["focus_ids"]) == {112, 1000}
    assert session._source_scope_review_is_current(focus)
    session.persist()
    restored = restore_saved_session(session.task_id)
    try:
        saved_focus = restored.segments[focus.id]
        assert set(saved_focus.verification["source"]["focus_ids"]) == {112, 1000}
        assert restored._source_scope_review_is_current(saved_focus)
        asyncio.run(restored._resume_pending_chunk_reviews())
        assert calls == [1], "Reopened bounded group must not repeat unchanged Muse review"
        restored.segments[neighbor.id].asr_text = "改变了后面一句的独立音频证据"
        assert not restored._source_scope_review_is_current(saved_focus)
    finally:
        pipeline.active_streaming_sessions.pop(session.task_id, None)
    neighbor.asr_text = "改变了后面一句的独立音频证据"
    assert not session._source_scope_review_is_current(focus)


@pytest.mark.parametrize("ids", [[], [True], [-1], [1, 1], list(range(17)), "1"])
def test_review_focus_ids_reject_invalid_durable_ownership(ids):
    from core.streaming.session_store import _clean
    with pytest.raises(ValueError, match="nhóm rà nguồn"):
        _clean({"source": {"focus_ids": ids}})


@pytest.mark.parametrize("silence", [False, True])
def test_successful_speech_publication_retires_only_its_technical_warning(reviewed_session, monkeypatch, silence):
    session, focus = reviewed_session, reviewed_session.segments[112]
    own = "Câu 113 chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
    other = "Câu 114 chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
    semantic = "Cách xưng hô cần kiểm tra."
    session.warnings = [own, other, semantic]
    focus.status, focus.failed_stage, focus.error = "WAITING", "TTS", "old Edge transport failure"
    session.persist()
    if silence:
        focus.final_vi, focus.confirmed_silence = "", True
    else:
        async def fit(row, *, text, output_path, **kwargs):
            return fitted_speech(row, text, output_path)
        monkeypatch.setattr(session, "_fit_dub", fit)
    asyncio.run(session._synthesize_segment(focus, allow_pacing=False))
    assert focus.status == "READY" and focus.error is None and focus.failed_stage is None
    assert session.warnings == [other, semantic]
    saved = _read(session.task_id)
    assert saved["session"]["warnings"] == [other, semantic]
    if silence:
        assert focus.audio_path is None


def test_failed_speech_commit_preserves_warning_and_detached_audio(reviewed_session, monkeypatch):
    session, focus = reviewed_session, reviewed_session.segments[112]
    old_audio = Path(focus.audio_path)
    warning = "Câu 113 chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
    session.warnings = [warning]
    focus.superseded_audio_paths = [str(old_audio)]
    focus.audio_path = None
    session.persist()
    async def fit(row, *, text, output_path, **kwargs):
        return fitted_speech(row, text, output_path)
    monkeypatch.setattr(session, "_fit_dub", fit)
    persist = session.persist
    def fail():
        if focus.status == "READY":
            raise OSError("injected disk full")
        return persist()
    monkeypatch.setattr(session, "persist", fail)
    with pytest.raises(pipeline.ProjectEditSaveError):
        asyncio.run(session._synthesize_segment(focus, allow_pacing=False))
    assert session.warnings == [warning]
    assert old_audio.is_file() and focus.superseded_audio_paths == [str(old_audio)]


def test_deferred_audio_retirement_survives_restore_and_windows_handle_failure(reviewed_session, monkeypatch):
    from core.streaming.session_store import restore_saved_session
    session, focus = reviewed_session, reviewed_session.segments[112]
    old_audio = Path(focus.audio_path)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: review_response())
    asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
    restored = restore_saved_session(session.task_id)
    try:
        assert restored.segments[112].superseded_audio_paths == [str(old_audio)]
        async def fit(row, *, text, output_path, **kwargs):
            return fitted_speech(row, text, output_path)
        monkeypatch.setattr(session, "_fit_dub", fit)
        unlink = Path.unlink
        def busy(path, *args, **kwargs):
            if path == old_audio:
                raise PermissionError("player owns old WAV")
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", busy)
        asyncio.run(session._synthesize_segment(focus, allow_pacing=False))
        assert old_audio.is_file() and focus.superseded_audio_paths == [str(old_audio)]
        current = focus.audio_path
        monkeypatch.setattr(Path, "unlink", unlink)
        session._retire_superseded_speech(focus)
        assert not old_audio.exists() and Path(current).is_file()
        assert focus.superseded_audio_paths == []
    finally:
        pipeline.active_streaming_sessions.pop(session.task_id, None)


def test_manual_speech_commit_clears_stale_warning_and_retires_its_old_wav(reviewed_session, monkeypatch):
    session, focus = reviewed_session, reviewed_session.segments[112]
    old_audio = Path(focus.audio_path)
    session.warnings = ["Câu 113 chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau.",
                        "Một câu khác vẫn cần kiểm tra."]
    session.persist()
    async def fit(row, *, text, output_path, **kwargs):
        return fitted_speech(row, text, output_path)
    monkeypatch.setattr(session, "_fit_dub", fit)
    asyncio.run(session.edit_segment(112, "Lời người dùng đã sửa."))
    assert focus.verification["status"] == "manual" and focus.error is None
    assert session.warnings == ["Một câu khác vẫn cần kiểm tra."]
    assert not old_audio.exists() and Path(focus.audio_path).is_file()


def test_retirement_metadata_failure_does_not_fail_already_committed_speech(reviewed_session, monkeypatch):
    session, focus = reviewed_session, reviewed_session.segments[112]
    old_audio = Path(focus.audio_path)
    focus.superseded_audio_paths = [str(old_audio)]
    focus.audio_path = None
    session.persist()
    async def fit(row, *, text, output_path, **kwargs):
        return fitted_speech(row, text, output_path)
    monkeypatch.setattr(session, "_fit_dub", fit)
    persist = session.persist
    def fail_only_retirement():
        if focus.status == "READY" and not focus.superseded_audio_paths:
            raise OSError("injected retirement-list save failure")
        return persist()
    monkeypatch.setattr(session, "persist", fail_only_retirement)
    asyncio.run(session._synthesize_segment(focus, allow_pacing=False))
    assert focus.status == "READY" and not old_audio.exists()
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == 112)
    assert saved["status"] == "READY" and saved["audio_path"] == focus.audio_path
    assert focus.superseded_audio_paths == [str(old_audio)]
    monkeypatch.setattr(session, "persist", persist)
    session._retire_superseded_speech(focus)
    assert focus.superseded_audio_paths == [] and Path(focus.audio_path).is_file()


def test_retirement_never_deletes_a_foreign_source_path(reviewed_session):
    session, focus = reviewed_session, reviewed_session.segments[112]
    original = session.video_path.read_bytes()
    focus.superseded_audio_paths = [str(session.video_path)]
    session._retire_superseded_speech(focus)
    assert session.video_path.read_bytes() == original
    with pytest.raises(ValueError, match="phải nằm trong cache ứng dụng"):
        session.persist()


@pytest.mark.parametrize("failure_kind", ["budget", "rejected_rewrite", "unmeasured_rewrite", "provider"])
@pytest.mark.parametrize("chunked", [True, False])
def test_review_audio_failure_retains_corrected_revision_and_only_measured_timing(
        reviewed_session, monkeypatch, failure_kind, chunked):
    session = reviewed_session
    session._chunked_source_started = chunked
    focus = session.segments[112]
    old_path, old_bytes = focus.audio_path, Path(focus.audio_path).read_bytes()
    response = review_response()
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: deepcopy(response))
    if failure_kind == "provider":
        error = RuntimeError("TTS provider disconnected")
    elif failure_kind == "budget":
        error = pipeline.SpeechBudgetError("Measured waveform exceeds safe slot")
    else:
        error = pipeline.PacingReviewRejected("Rejected shortening is not publishable",
            candidate="Một đề xuất sai.", reason="Does not preserve source", code="semantic_mismatch")
    measured = failure_kind in {"budget", "rejected_rewrite"}
    if measured:
        error.required_dub_duration = 1.398
        error.candidate_text = response["segments"][112]["final_vi"]
        error.pacing_verification = None

    async def fail_fit(*args, **kwargs):
        raise error
    monkeypatch.setattr(session, "_fit_dub", fail_fit)
    asyncio.run(session._review_translations(regenerate_audio=True))

    assert focus.status == "FAILED" and focus.failed_stage == "TTS"
    assert focus.revision == 1
    assert focus.text_zh == "我真的不是回来"
    assert focus.final_vi == "Thật sự không phải về đây."
    assert focus.verification["status"] == "corrected"
    assert focus.audio_path is None and focus.subtitle_cues == []
    assert Path(old_path).read_bytes() == old_bytes
    assert focus.error == str(error)
    assert 112 in session._pacing_failures if measured and chunked else not session._pacing_failures
    if measured:
        assert focus.timing_issue["code"] == "TIMING_CONFLICT"
        assert focus.timing_issue["required_seconds"] == 1.398
        if chunked:
            record = session._pacing_failures[112]
            assert record["candidate"] == focus.final_vi and record["revision"] == focus.revision
            assert {row["id"]: row["text_zh"] for row in record["context"]} == {
                112: "我真的不是回来", 113: "跟你开玩笑的"}
            assert session._pacing_failure_owned(focus, record)
            session.segments[113].text_zh = "A later independent source correction"
            assert not session._pacing_failure_owned(focus, record)
    else:
        assert focus.timing_issue is None
    session.persist()
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == 112)
    assert saved["timing_issue"] == focus.timing_issue
    assert saved["status"] == "FAILED" and saved["audio_path"] is None
    assert saved["text_zh"] == focus.text_zh and saved["final_vi"] == focus.final_vi
    assert saved["superseded_audio_paths"] == [old_path]


def test_inline_review_failed_speech_retires_old_wav_only_after_durable_retry(reviewed_session, monkeypatch):
    from core.streaming.session_store import restore_saved_session
    session, focus = reviewed_session, reviewed_session.segments[112]
    old_path, old_bytes = Path(focus.audio_path), Path(focus.audio_path).read_bytes()
    session.persist()
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: review_response())
    async def fail_fit(*args, **kwargs):
        raise RuntimeError("injected TTS disconnect during inline review")
    monkeypatch.setattr(session, "_fit_dub", fail_fit)
    asyncio.run(session._review_translations(regenerate_audio=True))
    assert focus.audio_path is None and old_path.read_bytes() == old_bytes
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == focus.id)
    assert saved["superseded_audio_paths"] == [str(old_path)]
    restored = restore_saved_session(session.task_id)
    try:
        focus = restored.segments[112]
        assert focus.audio_path is None and focus.superseded_audio_paths == [str(old_path)]
        monkeypatch.setattr(restored, "_ensure_tts_engine", lambda: None)
        async def fit(row, *, text, output_path, **kwargs):
            return fitted_speech(row, text, output_path)
        monkeypatch.setattr(restored, "_fit_dub", fit)
        persist = restored.persist
        def fail_commit():
            if focus.status == "READY":
                raise OSError("injected disk full at replacement publication")
            return persist()
        monkeypatch.setattr(restored, "persist", fail_commit)
        with pytest.raises(pipeline.ProjectEditSaveError):
            asyncio.run(restored._synthesize_segment(focus, allow_pacing=False))
        assert old_path.read_bytes() == old_bytes
        assert focus.audio_path is None and focus.superseded_audio_paths == [str(old_path)]
        saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == focus.id)
        assert saved["audio_path"] is None and saved["superseded_audio_paths"] == [str(old_path)]

        monkeypatch.setattr(restored, "persist", persist)
        unlink = Path.unlink
        retirements = []
        def verify_committed_before_retirement(path, *args, **kwargs):
            if path == old_path:
                saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == focus.id)
                assert saved["status"] == "READY" and saved["audio_path"] == focus.audio_path
                assert Path(saved["audio_path"]).is_file()
                retirements.append(path)
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", verify_committed_before_retirement)
        asyncio.run(restored._synthesize_segment(focus, allow_pacing=False))
        assert retirements == [old_path] and not old_path.exists()
        saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == focus.id)
        assert saved["status"] == "READY" and saved["audio_path"] == focus.audio_path
        assert saved["superseded_audio_paths"] == [] and focus.superseded_audio_paths == []
        assert Path(focus.audio_path).is_file() and focus.final_vi == "Thật sự không phải về đây."
    finally:
        pipeline.active_streaming_sessions.pop(session.task_id, None)


@pytest.mark.parametrize("change", ["wording", "voice"])
def test_recovery_commits_review_without_inline_pacing_and_hides_old_audio(
        reviewed_session, monkeypatch, change):
    session = reviewed_session
    focus = session.segments[112]
    old_path, old_bytes = focus.audio_path, Path(focus.audio_path).read_bytes()
    response = review_response()
    if change == "voice":
        response["segments"][112]["final_vi"] = focus.final_vi
        focus.tts_voice_outdated = True
    session._pacing_failures[112] = {"old": "measured revision"}
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: deepcopy(response))
    async def no_inline_speech(*args, **kwargs):
        raise AssertionError("Review blocked later groups on inline speech/pacing")
    monkeypatch.setattr(session, "edit_segment", no_inline_speech)
    asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
    assert focus.status == "WAITING" and focus.failed_stage == "TTS" and focus._retry_synthesis
    assert focus.error is None and focus.revision == 1
    assert focus.final_vi == response["segments"][112]["final_vi"]
    assert focus.audio_path is focus.audio_url is None and focus.subtitle_cues == []
    assert focus.tts_duration == 0 and focus.speed_ratio == 1
    assert Path(old_path).read_bytes() == old_bytes
    assert 112 not in session._pacing_failures
    assert session.segments[113].text_zh == response["segments"][113]["text_zh"]
    session.persist()
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == 112)
    assert saved["status"] == "WAITING" and saved["audio_path"] is None
    assert saved["final_vi"] == focus.final_vi and saved["verification"]["status"] == "corrected"


def test_deferred_review_disk_failure_restores_pending_assertion_and_prior_media(reviewed_session, monkeypatch):
    session = reviewed_session
    focus = session.segments[112]
    focus.speaker_review_pending = True
    before = deepcopy(vars(focus))
    old_bytes = Path(focus.audio_path).read_bytes()
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: review_response())
    monkeypatch.setattr(session, "persist", lambda: (_ for _ in ()).throw(OSError("injected disk full")))
    with pytest.raises(pipeline.ProjectEditSaveError):
        asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
    assert vars(focus) == before
    assert Path(focus.audio_path).read_bytes() == old_bytes


def test_deferred_review_disk_failure_restores_ordinary_row_metadata(reviewed_session, monkeypatch):
    session = reviewed_session
    focus = session.segments[112]
    assert not focus.speaker_review_pending
    before = deepcopy(vars(focus))
    old_bytes = Path(focus.audio_path).read_bytes()
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: review_response())
    monkeypatch.setattr(session, "persist", lambda: (_ for _ in ()).throw(OSError("injected disk full")))
    with pytest.raises(pipeline.ProjectEditSaveError):
        asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
    assert vars(focus) == before
    assert Path(focus.audio_path).read_bytes() == old_bytes


def test_deferred_review_cannot_replace_a_newer_manual_edit(reviewed_session, monkeypatch):
    session = reviewed_session
    focus = session.segments[112]
    def review(*args, **kwargs):
        focus.revision += 1
        focus.final_vi = "Lời người dùng vừa sửa."
        focus.verification = {"status": "manual"}
        return review_response()
    old_path = focus.audio_path
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
    assert focus.final_vi == "Lời người dùng vừa sửa." and focus.status == "READY"
    assert focus.audio_path == old_path and focus.revision == 1
    assert focus.verification["status"] == "manual"


def write_pcm(path, *, sample=b"\x20\x00", frames=9600):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(sample * frames)


def fitted_speech(row, text, output_path):
    write_pcm(output_path)
    return ({"text": text, "tts_duration": .4, "speed_ratio": 1., "boundaries": []},
            {"speech_start": row.start, "speech_end": row.start + .4,
             "subtitle_timing_source": "measured", "subtitle_cues": []}, {})


@pytest.mark.parametrize("outdated_voice", [False, True])
def test_deferred_synthesis_save_failure_never_overwrites_retained_legacy_wav(
        reviewed_session, monkeypatch, outdated_voice):
    session = reviewed_session
    focus = session.segments[112]
    legacy_path = session.segments_dir / "seg_112.wav"
    if Path(focus.audio_path) != legacy_path:
        Path(focus.audio_path).replace(legacy_path)
    focus.audio_path = str(legacy_path.resolve())
    legacy_bytes = legacy_path.read_bytes()
    focus.tts_voice_outdated = outdated_voice
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: review_response())
    asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
    session.persist()
    manifest_path = session.persist()
    before = deepcopy(vars(focus))
    async def fit(row, *, text, output_path, **kwargs):
        return fitted_speech(row, text, output_path)
    monkeypatch.setattr(session, "_fit_dub", fit)
    real_persist = session.persist
    publication_snapshot = []
    def persist():
        if focus.status == "READY":
            publication_snapshot.append(manifest_path.read_bytes())
            raise OSError("injected disk full at media publication")
        return real_persist()
    monkeypatch.setattr(session, "persist", persist)
    with pytest.raises(pipeline.ProjectEditSaveError):
        asyncio.run(session._synthesize_segment(focus, allow_pacing=False))
    assert legacy_path.read_bytes() == legacy_bytes
    assert publication_snapshot and manifest_path.read_bytes() == publication_snapshot[0]
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == focus.id)
    assert saved["audio_path"] is None and saved["final_vi"] == before["final_vi"]
    assert focus.audio_path is None and focus.final_vi == before["final_vi"]
    assert focus.revision == before["revision"] and focus.tts_voice_outdated == before["tts_voice_outdated"]
    assert list(session.segments_dir.glob("seg_112_*.wav")) == []
    assert list(session.segments_dir.glob("pending_*.wav")) == []


def test_deferred_synthesis_success_retires_detached_old_wav_only_after_durable_replacement(
        reviewed_session, monkeypatch):
    session = reviewed_session
    focus = session.segments[112]
    legacy_path = session.segments_dir / "seg_112.wav"
    if Path(focus.audio_path) != legacy_path:
        Path(focus.audio_path).replace(legacy_path)
    focus.audio_path = str(legacy_path.resolve())
    legacy_bytes = legacy_path.read_bytes()
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: review_response())
    asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
    session.persist()
    async def fit(row, *, text, output_path, **kwargs):
        return fitted_speech(row, text, output_path)
    monkeypatch.setattr(session, "_fit_dub", fit)
    asyncio.run(session._synthesize_segment(focus, allow_pacing=False))
    assert Path(focus.audio_path) != legacy_path.resolve()
    assert Path(focus.audio_path).read_bytes() != legacy_bytes
    assert not legacy_path.exists()
    assert focus.superseded_audio_paths == []
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == focus.id)
    assert saved["audio_path"] == focus.audio_path and saved["status"] == "READY"


@pytest.mark.parametrize("cancel_review", [False, True])
def test_deferred_review_waits_for_real_inflight_manual_edit_before_any_publication(
        reviewed_session, monkeypatch, cancel_review):
    session = reviewed_session
    focus = session.segments[112]
    focus.speaker_review_pending = True
    started, release, review_returned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    old_path, old_source = focus.audio_path, focus.text_zh
    async def fit(row, *, text, output_path, **kwargs):
        started.set()
        await release.wait()
        return fitted_speech(row, text, output_path)
    async def blocking(function, *args, **kwargs):
        result = function(*args, **kwargs)
        review_returned.set()
        return result
    monkeypatch.setattr(session, "_fit_dub", fit)
    monkeypatch.setattr(session, "_run_blocking", blocking)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: review_response())
    async def race():
        manual = asyncio.create_task(session.edit_segment(112, "Lời người dùng đang lưu."))
        await started.wait()
        review = asyncio.create_task(session._review_translations(regenerate_audio=True, defer_audio=True))
        try:
            await review_returned.wait()
            await asyncio.sleep(0)
            assert not review.done(), "Deferred review published while manual fit still owned the row"
            assert focus.audio_path == old_path and focus.text_zh == old_source and focus.revision == 0
            assert focus.speaker_review_pending, "Pending source review cleared before user edit completed"
            if cancel_review:
                review.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await review
            release.set()
            await manual
            if not cancel_review:
                await review
        finally:
            release.set()
            await asyncio.gather(manual, review, return_exceptions=True)
    asyncio.run(race())
    assert focus.final_vi == "Lời người dùng đang lưu." and focus.status == "READY"
    assert focus.revision == 1 and focus.verification["status"] == "manual"
    assert focus.speaker_review_pending  # The automatic response never owns the manual wording.


def test_repeated_review_synthesis_credits_only_current_ready_source_duration(reviewed_session, monkeypatch):
    session = reviewed_session
    focus = session.segments[112]
    session.total_processed_duration = focus.duration
    async def fit(row, *, text, output_path, **kwargs):
        return fitted_speech(row, text, output_path)
    monkeypatch.setattr(session, "_fit_dub", fit)
    for iteration in range(3):
        response = review_response()
        response["segments"][112]["final_vi"] += f" {iteration}."
        monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: deepcopy(response))
        asyncio.run(session._review_translations(regenerate_audio=True, defer_audio=True))
        assert session.total_processed_duration == 0, "Stale speech still received completed-duration credit"
        asyncio.run(session._synthesize_segment(focus, allow_pacing=False))
        assert session.total_processed_duration == focus.duration, "Rebuilding one row was counted more than once"
    session.start_wall_time = pipeline.time.time() - 10
    session.total_processed_duration = focus.duration * 100
    session._recalculate_telemetry()
    assert session.total_processed_duration == focus.duration
    assert session.realtime_factor == round(focus.duration / 10, 2)
