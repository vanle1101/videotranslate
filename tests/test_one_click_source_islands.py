"""One full run continues translations on both sides of a flagged source gap."""
import asyncio
from unittest.mock import AsyncMock

from test_preview_chunked_pipeline import preview
import main
from core.streaming.pipeline import SegmentItem


def test_full_run_translates_prepared_source_after_vad_gap_and_retries_provider(preview, monkeypatch):
    session, _, _, _, _ = preview
    session.translation_mode = "full"
    session.total_duration = 64.
    session._chunked_source_started = True
    session._chunked_source_identity = "islands-v1"
    session._source_prepared_seconds = 20.
    session._source_scanned_seconds = 64.
    session._source_coverage_intervals = [
        {"start": 0., "end": 20., "state": "prepared", "missing_speech_spans": [], "error_code": None},
        {"start": 20., "end": 21., "state": "unresolved",
         "missing_speech_spans": [[20., 21.]], "error_code": "source_speech_unrecognized"},
        {"start": 21., "end": 64., "state": "prepared", "missing_speech_spans": [], "error_code": None},
    ]
    session._source_missing_speech_spans = [[20., 21.]]
    session._source_failure_code = "source_speech_unrecognized"
    session._source_failure_start = 20.
    session.is_running = session.initialized = True
    for sid, start in enumerate((0., 8., 16., 21., 29., 37., 45., 53., 61.)):
        row = SegmentItem(sid, start, start + 3., 3.)
        row.text_zh = row.asr_text = f"source-{sid}"
        row.asr_pretranscribed = True
        session.segments[sid] = row

    source_preparations, provider_calls = [], []
    async def already_prepared(end):
        source_preparations.append(end)
        return True
    monkeypatch.setattr(session, "_prepare_source_until", already_prepared)
    monkeypatch.setattr(session, "_drain_chunk_followups", lambda: asyncio.sleep(0))
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    monkeypatch.setattr("core.streaming.pipeline.settings.TRANSLATION_CHUNK_SECONDS", 16.)

    class OCR:
        def close(self):
            pass
    class Engine:
        screen_ocr = OCR()

    monkeypatch.setattr(session, "_new_chunk_intelligence", Engine)

    def native_prepass(engine, path, rows, **options):
        start, end = options["start_time"], options["end_time"]
        provider_calls.append((start, end))
        if start >= 21. and not getattr(session, "_test_failed_once", False):
            session._test_failed_once = True
            raise TimeoutError("provider timeout")
        result = {"segments": {row.id: {"id": row.id, "start": row.start, "end": row.end,
            "text_zh": row.text_zh, "literal_vi": f"Vi {row.id}", "natural_vi": f"Vi {row.id}",
            "final_vi": f"Vi {row.id}", "needs_review": True, "review_reason": "draft",
            "translation_provider": "opencode", "translation_model": "test-model"} for row in rows},
            "screen_texts": [], "translation_sources": [], "summary": ""}
        options["chunk_callback"](result, start, end)
        return result
    monkeypatch.setattr(session, "_native_aware_prepass", native_prepass)

    asyncio.run(session._produce_parallel_visual(64., 2))

    assert source_preparations
    assert any(start == 0. for start, _ in provider_calls)
    assert any(start >= 21. for start, _ in provider_calls)
    assert session._test_failed_once
    assert all(not (row["start"] < 21. and row["end"] > 20.) for row in session._chunk_jobs)
    assert all(row["state"] == "COMPLETED" for row in session._chunk_jobs)
    assert all(session.segments[sid].translation_provider == "opencode" for sid in (0, 1, 2, 3, 4, 5, 6, 7, 8))
    assert session._source_missing_speech_spans == [[20., 21.]]
    assert session._final_output_metadata()["final_output_blocked"]


def test_resume_requeues_interrupted_translation_only_inside_verified_islands(preview, monkeypatch):
    session, _, _, _, _ = preview
    session.translation_mode = "full"
    session.visual_translation = False
    session.total_duration = 64.
    session._chunked_source_started = True
    session._source_prepared_seconds = 20.
    session._source_scanned_seconds = 64.
    session._visual_completed_seconds = .18
    session._visual_scanned_seconds = 64.
    session._source_coverage_intervals = [
        {"start": 0., "end": 20., "state": "prepared", "missing_speech_spans": [], "error_code": None},
        {"start": 20., "end": 21., "state": "unresolved",
         "missing_speech_spans": [[20., 21.]], "error_code": "source_speech_unrecognized"},
        {"start": 21., "end": 64., "state": "prepared", "missing_speech_spans": [], "error_code": None},
    ]
    recovered = SegmentItem(1, 21.2, 22.4, 1.2)
    recovered.text_zh = "已经保存的源句"
    recovered.status, recovered.failed_stage = "FAILED", "TRANSLATING"
    recovered.error = "task interrupted"
    uncertain = SegmentItem(2, 20.2, 20.8, .6)
    uncertain.text_zh = "尚未验证的源句"
    uncertain.status, uncertain.failed_stage = "FAILED", "TRANSLATING"
    uncertain.error = "task interrupted"
    session.segments = {recovered.id: recovered, uncertain.id: uncertain}
    session.initialized = True
    session._startup_failed = True
    session.error = "Dịch video chưa xong; các câu đã dịch và giọng đọc được giữ. Bấm Tiếp tục để xử lý phần còn lại."
    observed = {}

    async def resume_saved_source(_downloader, _url):
        observed["recovered"] = (recovered.status, recovered.failed_stage, recovered.error)
        observed["uncertain"] = (uncertain.status, uncertain.failed_stage, uncertain.error)

    monkeypatch.setattr(session, "_start_guarded", resume_saved_source)

    async def run():
        assert session._published_row(recovered)
        assert not session._published_row(uncertain)
        await session.retry_failed_synthesis()
        await session.start_task

    asyncio.run(run())

    assert observed["recovered"] == ("WAITING", "TRANSLATING", None)
    assert observed["uncertain"] == ("FAILED", "TRANSLATING", "task interrupted")


def test_source_gap_repair_precedes_parallel_chunk_fast_path(preview, monkeypatch):
    session, _, _, _, _ = preview
    session.translation_mode = "full"
    session.visual_translation = True
    session.total_duration = 64.
    session._chunked_source_started = True
    session._chunked_source_identity = "source-gap-v1"
    session._source_prepared_seconds = .18
    session._source_scanned_seconds = 64.
    session._visual_scanned_seconds = 64.
    session._source_coverage_intervals = [
        {"start": 0., "end": .18, "state": "prepared", "missing_speech_spans": [], "error_code": None},
        {"start": .18, "end": 6.252, "state": "unresolved",
         "missing_speech_spans": [[.18, 6.252]], "error_code": "source_speech_unrecognized"},
        {"start": 6.252, "end": 64., "state": "prepared", "missing_speech_spans": [], "error_code": None},
    ]
    session._parallel_chunk_planning = True
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    monkeypatch.setattr("core.streaming.chunked_source.ensure_source_identity", lambda _session: None)
    parallel = []
    monkeypatch.setattr(session, "_produce_parallel_visual",
                        lambda *_args: parallel.append(True))
    prepare = AsyncMock(return_value=False)
    repair = AsyncMock(return_value=False)
    finish = AsyncMock()
    monkeypatch.setattr(session, "_prepare_source_until", prepare)
    monkeypatch.setattr("core.streaming.source_repair.repair_unresolved_source_interval", repair)
    monkeypatch.setattr(session, "_complete_chunked_source", finish)
    monkeypatch.setattr("core.streaming.pipeline.settings.LLM_PROVIDER", "ollama")

    asyncio.run(session._produce_chunked_visual())

    assert parallel == []
    repair.assert_awaited_once()
    finish.assert_awaited_once()


def test_repaired_source_gap_gets_its_own_completed_translation_chunk(preview, monkeypatch):
    from core.streaming.recovery import reserve, transition
    session, _, _, _, _ = preview
    session.translation_mode = "full"
    session.visual_translation = False
    session.total_duration = 64.
    session._chunked_source_started = True
    session._chunked_source_identity = "source-gap-v2"
    session._source_prepared_seconds = .18
    session._source_scanned_seconds = 64.
    session._visual_scanned_seconds = 64.
    session._visual_completed_seconds = .18
    session._source_coverage_intervals = [
        {"start": 0., "end": .18, "state": "prepared", "missing_speech_spans": [], "error_code": None},
        {"start": .18, "end": 6.252, "state": "unresolved",
         "missing_speech_spans": [[.18, 6.252]], "error_code": "source_speech_unrecognized"},
        {"start": 6.252, "end": 64., "state": "prepared", "missing_speech_spans": [], "error_code": None},
    ]
    for start, end in ((0., .18), (6.252, 64.)):
        job = reserve(session._chunk_jobs, start, end, "a" * 64)
        transition(job, "RUNNING")
        transition(job, "COMPLETED")
    session.is_running = session.initialized = True
    session.segments = {}
    finish = AsyncMock()

    async def repair(owner, interval):
        assert (interval["start"], interval["end"]) == (.18, 6.252)
        owner._source_coverage_intervals = [
            {"start": 0., "end": .18, "state": "prepared", "missing_speech_spans": [], "error_code": None},
            {"start": .18, "end": 6.252, "state": "prepared", "missing_speech_spans": [], "error_code": None},
            {"start": 6.252, "end": 64., "state": "prepared", "missing_speech_spans": [], "error_code": None},
        ]
        owner._source_prepared_seconds = 64.
        owner._source_missing_speech_spans = []
        for sid, (start, end, source) in enumerate(((.5, 2.5, "早上好"), (3.6, 5.9, "谢谢")), 122):
            row = SegmentItem(sid, start, end, end - start)
            row.text_zh = row.asr_text = source
            row.asr_pretranscribed = True
            owner.segments[sid] = row
        return True

    async def prepare(_end):
        return True

    async def translate(_owner, rows):
        assert {row.id for row in rows} == {122, 123}
        for row in rows:
            row.final_vi = f"Câu dịch {row.id}."
            row.status = "WAITING"

    async def process(row):
        row.status = "READY"

    monkeypatch.setattr("core.streaming.chunked_source.ensure_source_identity", lambda _owner: None)
    monkeypatch.setattr(session, "_prepare_source_until", prepare)
    monkeypatch.setattr(session, "_process_segment", process)
    monkeypatch.setattr(session, "_complete_chunked_source", finish)
    monkeypatch.setattr("core.streaming.source_repair.repair_unresolved_source_interval", repair)
    monkeypatch.setattr("core.streaming.nonvisual_translation.translate_nonvisual_rows", translate)
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)

    asyncio.run(session._produce_chunked_visual())

    repaired_jobs = [row for row in session._chunk_jobs
                     if row["start"] == .18 and row["end"] == 6.252]
    assert len(repaired_jobs) == 1 and repaired_jobs[0]["state"] == "COMPLETED"
    assert session._visual_completed_seconds == 64.
    assert [row.status for row in session.segments.values()] == ["READY", "READY"]
    finish.assert_awaited_once()


def test_renderable_verified_islands_finish_as_source_gap_draft(preview, monkeypatch):
    session, _, _, _, _ = preview
    row = SegmentItem(0, 21.2, 22.4, 1.2)
    row.text_zh, row.final_vi, row.status = "已经保存的源句", "Câu đã được dịch.", "READY"
    session.segments = {row.id: row}
    session.translation_mode = "full"
    session._chunked_source_started = True
    session.total_duration = 64.
    session._source_prepared_seconds = 20.
    session._source_missing_speech_spans = [[20., 21.]]
    session._source_coverage_intervals = [{
        "start": 0., "end": 20., "state": "prepared", "missing_speech_spans": [], "error_code": None,
    }, {
        "start": 20., "end": 21., "state": "unresolved",
        "missing_speech_spans": [[20., 21.]], "error_code": "source_speech_unrecognized",
    }, {
        "start": 21., "end": 64., "state": "prepared", "missing_speech_spans": [], "error_code": None,
    }]
    session._visual_completed_seconds = 20.
    session._visual_scanned_seconds = 64.
    session._visual_prepass_complete = False
    session._chunk_jobs = [{
        "id": "source-0-64", "start": 0., "end": 64., "state": "COMPLETED",
        "attempts": 1, "input_hash": "fixture",
    }]
    session.is_running = True
    session.event_callback = lambda *_: None

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(session, "_update_ready", noop)
    monkeypatch.setattr(session, "_recover_transient_speech_after_source", noop)
    monkeypatch.setattr(session, "_recover_pacing_after_source", noop)
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    monkeypatch.setattr("core.streaming.retry_lifecycle.prepare_auto_review", lambda _session: None)

    asyncio.run(session._worker_loop())

    assert session.error == (
        "Dịch video chưa xong; các câu đã dịch và giọng đọc được giữ. "
        "Bấm Tiếp tục để xử lý phần còn lại."
    )
    assert session._startup_failed
    assert main._source_gap_draft_ready(session, {
        "source_coverage_complete": False,
        "missing_speech_ids": [], "missing_audio_ids": [], "invalid_timing_ids": [],
    })
