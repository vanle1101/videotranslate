import asyncio
from unittest.mock import AsyncMock
import pytest
from test_preview_chunked_pipeline import preview
from core.streaming.pipeline import SegmentItem
from core.streaming.recovery import reserve, transition, input_hash
from config import settings


@pytest.mark.parametrize("failure", [None, TimeoutError("provider offline"), ValueError("bad schema")])
def test_saved_ready_retry_runs_once_before_blocked_source(preview, monkeypatch, failure):
    session, _, analyzed, _, _ = preview
    session.translation_mode = "full"
    session.total_duration = 180.
    session._chunked_source_started = True
    session._chunked_source_identity = "test-source"
    session._source_prepared_seconds = 80.
    session._visual_scanned_seconds = 60.
    session.is_running = True
    for sid, start in enumerate((0., 30., 65.)):
        row = SegmentItem(sid, start, start + 3., 3.)
        row.text_zh = row.asr_text = "你好"
        session.segments[sid] = row
    source, _ = session._parallel_source_snapshot(0.,60.)
    signature = input_hash(source, {"source":session._chunked_source_identity,
        "provider":settings.LLM_PROVIDER,"model":settings.OPENCODE_MODEL,
        "visual":True,"parallel_context_version":1})
    record = reserve(session._chunk_jobs,0.,60.,signature)
    transition(record,"RUNNING")
    transition(record,"RETRY_PENDING","old_failure")
    calls = []
    original = session.video_intelligence.prepass
    class Engine:
        class OCR:
            def close(self): pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            calls.append((options["start_time"], options["end_time"]))
            if failure: raise failure
            return original(path,rows,**options)
    async def prepare(end):
        if end <= 80.:
            return True
        calls.append("source_blocked")
        return False
    monkeypatch.setattr(session,"_new_chunk_intelligence",Engine)
    monkeypatch.setattr(session,"_prepare_source_until",prepare)
    monkeypatch.setattr(session,"_drain_chunk_followups",AsyncMock())
    monkeypatch.setattr(session,"_persist_if_enabled",lambda:None)
    async def publish(result,lo,hi,**options): pass
    monkeypatch.setattr(session,"_publish_visual_chunk",publish)
    asyncio.run(session._produce_parallel_visual(180.,2))
    assert calls == [(0., 60.), (60., 68.), "source_blocked"]
    assert record["state"] == ("RETRY_PENDING" if failure else "COMPLETED")
    assert session._source_prepared_seconds == 80.
    assert [(r["start"],r["end"]) for r in session._chunk_jobs] == [(0.,60.), (60.,68.)]


@pytest.mark.parametrize("prepared,bounds,expected", [
    (62.65, [(0., .18), (31.08, 34.36), (51.29, 52.55), (60.09, 60.73)], 52.55),
    (64., [(.82, 2.38), (43.03, 44.35), (53.65, 56.07), (60.98, 62.7)], 44.35),
    (70., [(0., .18), (40., 45.), (58., 64.), (66., 69.)], 45.),
])
@pytest.mark.parametrize("completed_prefix", [False, True])
def test_unreserved_prepared_sentences_publish_before_source_failure(
        preview, monkeypatch, prepared, bounds, expected, completed_prefix):
    from copy import deepcopy
    from core.streaming.recovery import validate_records
    session, _, analyzed, _, _ = preview
    session.translation_mode = "full"
    session.total_duration = 180.
    session._chunked_source_started = True
    session._chunked_source_identity = "test-source"
    session._source_prepared_seconds = prepared
    session._source_failure_code = "source_speech_unrecognized"
    session._source_missing_speech_spans = [[prepared + 3., prepared + 3.4]]
    session.initialized = session.is_running = True
    for sid, (start, end) in enumerate(bounds):
        row = SegmentItem(sid, start, end, end - start)
        row.text_zh = row.asr_text = "你好"
        row.asr_pretranscribed = True
        row.status, row.failed_stage = "FAILED", "TRANSLATING"
        session.segments[sid] = row
    # Existing completed media and manual edits must survive the normal
    # publication path, even when they are inside a newly admitted interval.
    first = session.segments[0]
    first.status, first.failed_stage = "READY", None
    first.final_vi = "Lời đã giữ."
    first.verification = {"status": "manual"}
    first.audio_path = "preserved-existing.wav"
    before = deepcopy(first.to_dict())
    prefix = 0.
    old_record = None
    if completed_prefix:
        prefix = 10.
        old_record = reserve(session._chunk_jobs, 0., prefix, "a" * 64)
        transition(old_record, "COMPLETED")
        old_record = deepcopy(old_record)
    calls = []
    original = session.video_intelligence.prepass
    class Engine:
        class OCR:
            def close(self): pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            calls.append("translate")
            assert options["end_time"] + 8. <= prepared
            return original(path, rows, **options)
    async def prepare(end):
        if end <= prepared:
            return True
        calls.append("source_blocked")
        return False
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 60.)
    monkeypatch.setattr(session, "_new_chunk_intelligence", Engine)
    monkeypatch.setattr(session, "_prepare_source_until", prepare)
    monkeypatch.setattr(session, "_drain_chunk_followups", AsyncMock())
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    asyncio.run(session._produce_parallel_visual(180., 20))
    assert calls == ["translate", "source_blocked"]
    assert analyzed[0][:3] == (prefix, expected, [sid for sid, row in session.segments.items()
                                                if prefix <= row.start < row.end <= expected])
    assert session._source_prepared_seconds == prepared
    assert session._source_failure_code == "source_speech_unrecognized"
    assert session._source_missing_speech_spans == [[prepared + 3., prepared + 3.4]]
    assert first.to_dict() == before
    assert session.segments[1].final_vi and session.segments[1].status == "READY"
    assert bool(session.segments[2].final_vi) == (bounds[2][1] <= expected)
    assert not session.segments[3].final_vi
    assert session._chunk_jobs[-1]["state"] == "COMPLETED"
    assert (session._chunk_jobs[-1]["start"], session._chunk_jobs[-1]["end"]) == (prefix, expected)
    validate_records(session._chunk_jobs, session.total_duration)
    if old_record is not None:
        assert session._chunk_jobs[0] == old_record


@pytest.mark.parametrize("bounds,start,expected", [
    ([], 0., None),
    ([(55., 58.)], 0., None),
    ([(0., 4.), (50., 55.), (54., 58.)], 0., 4.),
    ([(0., 4.), (20., 25.)], 22., None),
    ([(0., 4.), (55., 56.)], 0., 56.),
])
def test_prepared_boundary_requires_whole_sentences_and_lookahead(preview, bounds, start, expected):
    session = preview[0]
    session._source_prepared_seconds = 64.
    for sid, (lo, hi) in enumerate(bounds):
        session.segments[sid] = SegmentItem(sid, lo, hi, hi - lo)
    assert session._prepared_parallel_boundary(start, 60.) == expected


@pytest.mark.parametrize("bounds", [[], [(55., 58.)]])
def test_no_whole_prepared_work_never_reserves_an_empty_owner(preview, monkeypatch, bounds):
    session = preview[0]
    session.total_duration = 180.
    session._source_prepared_seconds = 64.
    for sid, (lo, hi) in enumerate(bounds):
        session.segments[sid] = SegmentItem(sid, lo, hi, hi - lo)
    prepare = AsyncMock(return_value=False)
    monkeypatch.setattr(session, "_prepare_source_until", prepare)
    monkeypatch.setattr(session, "_new_chunk_intelligence", lambda: pytest.fail("No useful batch"))
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 60.)
    asyncio.run(session._produce_parallel_visual(180., 20))
    prepare.assert_awaited_once_with(68.)
    assert session._chunk_jobs == []
    assert session._source_prepared_seconds == 64.


@pytest.mark.parametrize("crossing_failure", [False, True])
def test_first_run_publishes_newly_committed_prefix_when_later_source_fails(
        preview, monkeypatch, crossing_failure):
    session, _, analyzed, speech, _ = preview
    session.translation_mode = "full"
    session.total_duration = 409.13415
    session.initialized = session.is_running = True
    session._chunked_source_started = True
    session._chunked_source_identity = "first-run-source"
    assert session._source_prepared_seconds == 0 and not session._chunk_jobs
    prepared = 70. if crossing_failure else 32.
    bounds = ([(.15, 2.27), (23.39, 23.89), (58., 64.), (66., 69.)]
              if crossing_failure else
              [(.15, 2.27), (23.39, 23.89), (25.39, 26.53), (30.52, 31.64)])
    calls = []
    async def prepare(end):
        calls.append(end)
        if not session.segments:
            for sid, (lo, hi) in enumerate(bounds):
                row = SegmentItem(sid, lo, hi, hi - lo)
                row.text_zh = row.asr_text = "你好"
                row.asr_pretranscribed = True
                session.segments[sid] = row
            session._source_prepared_seconds = prepared
        if crossing_failure and len(calls) == 1:
            return True
        session._source_failure_code = "source_speech_unrecognized"
        session._source_failure_start = prepared
        session._source_missing_speech_spans = [[prepared + 14.068, prepared + 14.892]]
        return False
    original = session.video_intelligence.prepass
    class Engine:
        class OCR:
            def close(self): pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            assert options["end_time"] + 8. <= prepared
            return original(path, rows, **options)
    monkeypatch.setattr(session, "_prepare_source_until", prepare)
    monkeypatch.setattr(session, "_new_chunk_intelligence", Engine)
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    monkeypatch.setattr(session, "_drain_chunk_followups", AsyncMock())
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 60.)
    asyncio.run(session._produce_parallel_visual(session.total_duration, 20))
    assert calls == ([68., 72.] if crossing_failure else [68.])
    assert analyzed[0][:3] == (0., 23.89, [0, 1])
    assert speech == [0, 1]
    assert all(session.segments[sid].status == "READY" for sid in [0, 1])
    assert not session.segments[2].final_vi and not session.segments[3].final_vi
    assert len(session._chunk_jobs) == 1
    assert session._chunk_jobs[0]["state"] == "COMPLETED"
    assert session._source_prepared_seconds == prepared
    assert session._source_failure_code == "source_speech_unrecognized"
    assert session._source_missing_speech_spans == [[prepared + 14.068, prepared + 14.892]]
    assert not session._final_output_metadata()["media_complete"]


def test_failed_source_does_not_shorten_a_reserved_retry_owner(preview, monkeypatch):
    session = preview[0]
    session.total_duration = 180.
    row = SegmentItem(0, 0., 3., 3.)
    row.text_zh = row.asr_text = "你好"
    session.segments[0] = row
    record = reserve(session._chunk_jobs, 0., 60., "a" * 64)
    transition(record, "RETRY_PENDING", "previous_failure")
    session._visual_scanned_seconds = 180.
    async def prepare(end):
        session._source_prepared_seconds = 32.
        return False
    monkeypatch.setattr(session, "_prepare_source_until", prepare)
    monkeypatch.setattr(session, "_drain_chunk_followups", AsyncMock())
    monkeypatch.setattr(session, "_new_chunk_intelligence", lambda: pytest.fail("Reserved range cannot be shortened"))
    asyncio.run(session._produce_parallel_visual(180., 20))
    assert len(session._chunk_jobs) == 1
    assert (record["start"], record["end"], record["state"]) == (0., 60., "RETRY_PENDING")
