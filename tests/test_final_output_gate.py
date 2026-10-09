"""Real API/manifest contracts for omitted source speech; no provider acceptance claim."""
import asyncio
from copy import deepcopy
import wave
from unittest.mock import Mock

import httpx
import pytest

import main
from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.streaming import session_store as store
from core.streaming.output_gate import final_output_metadata, missing_spoken_output_ids


@pytest.fixture
def omitted_speech(tmp_path, monkeypatch):
    for name, relative in {
        "BASE_DIR": "", "WORKSPACE_DIR": "workspace", "INPUT_DIR": "workspace/inputs",
        "OUTPUT_DIR": "workspace/outputs", "TEMP_DIR": "workspace/temp",
    }.items():
        path = tmp_path / relative
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, path)
    source = settings.INPUT_DIR / "source.mp4"
    source.write_bytes(b"source fixture for API/manifest contracts")
    registry = {}
    monkeypatch.setattr(main, "active_streaming_sessions", registry)
    monkeypatch.setattr("core.streaming.pipeline.active_streaming_sessions", registry)
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "task_history", [])
    sess = StreamingPipelineSession("omitted-speech", source, visual_translation=True,
                                    voice="vi-VN-HoaiMyNeural", tts_engine_name="edge-tts")
    sess.initialized = True
    sess.auto_export_result = True
    sess.total_duration = 4.
    sess.video_size = (1920, 1080)
    sess.review_summary = {"status": "completed", "checked": 2, "unresolved": 2}
    # Exact failure shape from actual rows 120/121: READY is a preview policy,
    # there is known source speech, and neither defensible Vietnamese nor WAV.
    for index, text in enumerate(("那你呢", "我")):
        row = SegmentItem(index, index * 2., index * 2. + 2., 2.)
        row.text_zh = text
        row.source_method = "text-ai"
        row.status = "READY"
        row.needs_review = True
        row.review_reason = "Chưa xác minh vai người nói."
        row.verification = {"status": "unresolved", "semantic_verified": False}
        sess.segments[index] = row
    registry[sess.task_id] = sess
    return sess


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


def test_preview_readiness_never_proves_empty_source_speech_is_completed(omitted_speech):
    sess = omitted_speech
    sess._recalculate_telemetry()
    assert sess.playable_until == sess.total_duration
    asyncio.run(sess.report_progress("complete", "Finished", 100))
    progress = sess.get_progress()
    assert progress["status"] == "PREPARED" and progress["progress_pct"] is None
    assert progress["segment_states"]["COMPLETED"] == 0
    assert progress["segment_states"]["REVIEW_REQUIRED"] == 2
    assert progress["missing_speech_ids"] == [0, 1]
    assert progress["final_output_blocked"] is True
    assert progress["content_review_state"] == "REVIEW_REQUIRED"
    assert sess.get_telemetry()["status"] == "prepared"
    assert all(row.to_dict()["preview_is_draft"] for row in sess.segments.values())


@pytest.mark.parametrize("source_field", ["text_zh", "asr_text"])
@pytest.mark.parametrize("needs_review", [True, False])
def test_source_speech_cannot_become_implicit_silence_when_flags_are_missing(source_field, needs_review):
    row = SegmentItem(113, 0., .4, .4)
    row.status = "PLAYED"
    row.needs_review = needs_review
    setattr(row, source_field, "我")
    assert missing_spoken_output_ids([row]) == [113]
    assert missing_spoken_output_ids([row.to_dict()]) == [113]
    assert row.to_dict()["processing_state"] == "REVIEW_REQUIRED"
    assert row.to_dict()["preview_is_draft"] is True
    row.confirmed_silence = True
    assert missing_spoken_output_ids([row]) == []


def test_api_rejects_full_export_and_exposes_exact_missing_ids_without_losing_preview(omitted_speech, monkeypatch):
    sess = omitted_speech
    factory = Mock()
    monkeypatch.setattr(main, "HQExporter", factory)
    before = deepcopy([row.to_dict() for row in sess.segments.values()])
    async def run():
        async with client() as api:
            response = await api.post("/api/streaming/export-hq", json={"task_id": sess.task_id})
            assert response.status_code == 409
            assert "2 câu" in response.json()["detail"] and "câu 1, 2" in response.json()["detail"]
            snapshot = (await api.get(f"/api/streaming/{sess.task_id}")).json()
            assert snapshot["progress"]["status"] == "PREPARED"
            assert snapshot["missing_speech_ids"] == [0, 1]
            assert snapshot["final_output_blocked"] is True and snapshot["output_video_url"] == ""
            tasks = (await api.get("/api/tasks")).json()
            task = next(row for row in tasks["tasks"] if row["task_id"] == sess.task_id)
            assert task["status"] == "PREPARED" and task["progress_pct"] is None
            assert task["missing_speech_ids"] == [0, 1] and task["video_url"] == ""
    asyncio.run(run())
    factory.assert_not_called()
    assert not main.active_export_tasks
    assert [row.to_dict() for row in sess.segments.values()] == before


def test_automatic_export_preserves_pending_review_without_starting_renderer(omitted_speech, monkeypatch):
    sess = omitted_speech
    factory = Mock()
    monkeypatch.setattr(main, "HQExporter", factory)
    asyncio.run(main.broadcast_session_event(sess.task_id, "finished", {}))
    assert not getattr(sess, "auto_export_task", None)
    assert not main.active_export_tasks
    assert all(row.needs_review for row in sess.segments.values())
    factory.assert_not_called()


def test_durable_history_and_reload_preserve_uncertain_rows_without_false_full_result(omitted_speech):
    sess = omitted_speech
    sess.persist()
    before = store._project_path(sess.task_id).read_bytes()
    available = store._availability(store._read(sess.task_id))
    assert not available["ready"] and available["status"] == "PREPARED"
    listed = store.list_saved_sessions()[0]
    assert listed["status"] == "PREPARED" and listed["progress_pct"] is None
    assert listed["missing_speech_ids"] == [0, 1] and listed["output_video_url"] == ""
    assert "2 câu" in listed["missing_media"]
    main.active_streaming_sessions.clear()
    restored = store.restore_saved_session(sess.task_id)
    assert restored.get_progress()["status"] == "PREPARED"
    assert restored.get_progress()["content_review_state"] == "REVIEW_REQUIRED"
    assert all(row.status == "READY" and row.needs_review and row.audio_path is None
               and row.confirmed_silence is False for row in restored.segments.values())
    assert [row.text_zh for row in restored.segments.values()] == ["那你呢", "我"]
    assert all(row.verification == {"status": "unresolved", "semantic_verified": False}
               for row in restored.segments.values())
    assert store._project_path(sess.task_id).read_bytes() == before


def test_saved_partial_preview_can_continue_while_omitted_speech_blocks_full_export(omitted_speech):
    sess = omitted_speech
    sess.translation_mode = "preview"
    sess.total_duration = 40.
    sess._preview_ready = True
    sess._visual_incremental_started = True
    sess._visual_prepass_complete = False
    sess._visual_completed_seconds = sess._visual_scanned_seconds = 4.
    sess.persist()
    available = store._availability(store._read(sess.task_id))
    assert available["status"] == "PREVIEW_READY" and available["preview_can_continue"]
    assert not available["ready"] and available["final_output_blocked"]
    assert available["missing_speech_ids"] == [0, 1]
    assert store.list_saved_sessions()[0]["can_translate_full"] is True


def test_existing_result_link_is_not_published_as_complete_for_omitted_speech(omitted_speech):
    sess = omitted_speech
    sess.output_filename = "previous-result.mp4"
    sess.output_video_url = "/api/outputs/previous-result.mp4"
    sess.output_review_url = "/api/outputs/previous-result.review.json"
    details = main.session_output_details(sess)
    assert details["output_video_url"] == details["output_filename"] == details["review_url"] == ""
    # Gating the current result does not erase or delete the user's previous file.
    assert sess.output_filename == "previous-result.mp4"
    assert details["review_report"][0]["verification"]["semantic_verified"] is False


def test_only_explicit_silence_confirmation_can_resolve_an_empty_spoken_row(omitted_speech):
    sess = omitted_speech
    async def run():
        async with client() as api:
            route = f"/api/streaming/{sess.task_id}/segments/0"
            rejected = await api.patch(route, json={"final_vi": ""})
            assert rejected.status_code == 422
            accepted = await api.patch(route, json={"final_vi": "", "confirm_silence": True})
            assert accepted.status_code == 200
    asyncio.run(run())
    row = sess.segments[0]
    assert row.text_zh == "那你呢" and row.confirmed_silence is True
    assert row.audio_path is None and row.needs_review is False
    assert missing_spoken_output_ids(sess.segments.values()) == [1]
    assert sess.segments[1].needs_review is True and sess.segments[1].confirmed_silence is False


def test_spoken_uncertain_draft_keeps_review_without_becoming_a_missing_speech_gap():
    row = SegmentItem(3, 0., 2., 2.)
    row.text_zh, row.final_vi = "那你呢", "Còn anh thì sao?"
    row.needs_review = True
    gate = final_output_metadata([row])
    assert gate["missing_speech_ids"] == [] and gate["final_output_blocked"] is False
    assert gate["content_review_state"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("change", ["omit_speech", "revise_spoken_text", "change_silence_confirmation"])
def test_publication_rechecks_current_speech_and_revision_without_overwriting_prior_file(omitted_speech, monkeypatch, tmp_path, change):
    sess = omitted_speech
    for row in sess.segments.values():
        row.final_vi = "Bản nháp còn chờ xác minh."
        row.audio_path = str(sess.segments_dir / f"seg_{row.id}.wav")
        with wave.open(row.audio_path, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x00" * 100)
    final = settings.OUTPUT_DIR / "kept.mp4"
    final.write_bytes(b"previous user output must remain")
    pending = settings.OUTPUT_DIR / "pending.mp4"
    async def mutate():
        if change == "omit_speech":
            sess.segments[0].final_vi = ""
        elif change == "revise_spoken_text":
            sess.segments[0].final_vi = "Nội dung mới trong lượt khác."
        else:
            sess.segments[0].confirmed_silence = True

    async def run():
        loop = asyncio.get_running_loop()
        def render(**kwargs):
            pending.write_bytes(b"renderer publication contract fixture, not validated media")
            asyncio.run_coroutine_threadsafe(mutate(), loop).result()
            try:
                kwargs["publish_callback"](pending, final)
            finally:
                pending.unlink(missing_ok=True)
            pytest.fail("A stale render must not publish a successful result")
        monkeypatch.setattr(main, "HQExporter", Mock(return_value=Mock(export=Mock(side_effect=render))))
        async with client() as api:
            response = await api.post("/api/streaming/export-hq", json={"task_id": sess.task_id})
            assert response.status_code == 500
    asyncio.run(run())
    assert final.read_bytes() == b"previous user output must remain"
    task = main.active_export_tasks[f"export_{sess.task_id}"]
    assert task["status"] == "FAILED" and not task.get("published")
    assert not getattr(sess, "output_filename", "") and not getattr(sess, "output_video_url", "")
