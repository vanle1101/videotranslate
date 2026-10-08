"""Offline regression for chunk publication, source review and prefix recovery."""
import asyncio
import threading
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.opencode_client import OpenCodeClientError
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.video_intelligence import VideoIntelligenceError
from core.runtime_context import current_execution_context


@pytest.fixture
def partial_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path / "w")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"isolated source identity")
    session = StreamingPipelineSession("partial-visual", source, visual_translation=True,
                                       tts_engine_name="edge-tts", initial_buffer_seconds=1)
    session._prepared = True
    session._ensure_tts_engine()
    session.total_duration = 48
    session.bgm_url = "/isolated-bgm"
    for sid, start in enumerate((0., 24.)):
        item = SegmentItem(sid, start, start + 2, 2)
        item.text_zh = "拜托姐" if sid == 0 else "下一句"
        session.segments[sid] = item
    events = []
    session.event_callback = lambda event, payload: events.append((event, deepcopy(payload)))
    return session, events


def result_for(rows, summary="previous context"):
    return {"segments": {row.id: {"id": row.id, "start": row.start, "end": row.end,
        "text_zh": row.text_zh, "literal_vi": "Chị giúp em nhé.", "natural_vi": "Chị giúp em nhé.",
        "final_vi": "Chị giúp em nhé.", "needs_review": False, "review_reason": "",
        "translation_provider": "opencode", "translation_model": "offline-muse"} for row in rows},
        "screen_texts": [], "summary": summary,
        "translation_sources": [{"provider": "opencode", "model": "offline-muse", "evidence_mode": "asr-ocr-text"}]}


def screen_at(start, end, text="Chữ trên hình"):
    return {"id": "o0", "start": start, "end": end, "text_zh": "你好", "text_vi": text,
            "bbox": [.1, .8, .5, .1], "confidence": .99, "kind": "subtitle",
            "needs_review": False, "review_reason": "", "source_region_verified": True}


def dense_rows(session, count=9):
    session.segments = {}
    for sid in range(count):
        item = SegmentItem(sid, float(sid * 2), float(sid * 2 + 1), 1)
        item.text_zh = "你好"
        session.segments[sid] = item
    return list(session.segments.values())


def install_review_and_speech(session, monkeypatch, *, review_error=None):
    reviews, speech = [], []

    def review(reviewer, path, targets, screens, **options):
        reviews.append(([row.id for row in targets], deepcopy(options["context_segments"])))
        if review_error:
            raise review_error
        return {"segments": {row.id: {**row.to_dict(), "final_vi": "Chị giúp em nhé.",
            "needs_review": False, "review_reason": "", "verification": {
            "status": "verified", "source_supported": True, "source_accepted": True,
            "semantic_verified": True, "address_verified": True}} for row in targets},
            "translation_sources": [], "summary": {"checked": len(targets), "verified": len(targets)}}

    monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", review)

    async def synthesize(segment):
        speech.append(segment.id)
        segment.status = "READY"
        segment.audio_url = f"/isolated-speech/{segment.id}"
        await session.emit("segment_update", segment.to_dict())
        await session._update_ready()

    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    return reviews, speech


def test_completed_prefix_publishes_before_next_timeout_and_retry_keeps_audio(partial_session, monkeypatch):
    session, events = partial_session
    reviews, speech = install_review_and_speech(session, monkeypatch)
    requests = []

    def analyze(path, start, end, rows, summary, check):
        requests.append(start)
        if start:
            assert session.initialized
            assert session.segments[0].status == "READY"
            assert session.segments[0].verification["address_verified"] is True
            assert any(event == "segment_update" and payload["id"] == 0 and payload["audio_url"]
                       for event, payload in events)
            raise OpenCodeClientError("provider timeout after translated prefix")
        return result_for(rows)

    monkeypatch.setattr(session.video_intelligence, "analyze_chunk", analyze)

    async def run():
        with pytest.raises(OpenCodeClientError, match="timeout"):
            await session.start()
        assert session.initialized and session.can_retry
        assert session._startup_failed
        assert session.segments[1].source_method == "audio"
        assert session.segments[1].status == "WAITING"
        assert session.playable_until == 24
        assert session.get_progress()["status"] == "FAILED"
        assert session.get_progress()["progress_pct"] == 50
        first_audio = session.segments[0].audio_url
        monkeypatch.setattr(session.video_intelligence, "analyze_chunk",
                            Mock(side_effect=lambda path, start, end, rows, summary, check: result_for(rows)))
        await session.retry_failed_synthesis()
        await session.start_task
        await session.worker_task
        assert session.segments[0].audio_url == first_audio
        assert session.error is None
        assert all(row.status == "READY" for row in session.segments.values())

    asyncio.run(run())
    assert requests == [0., 24.]
    assert speech == [0, 1], "Retry must keep the existing reviewed audio prefix"
    assert reviews[0][0] == [0]
    assert [row.id for row in reviews[0][1]] == [0, 1], "Review subset retains later ASR source context"
    assert reviews[1][0] == [1]
    assert len([event for event, payload in events if event == "init"]) == 1


def test_review_timeout_publishes_draft_with_incomplete_audit(partial_session, monkeypatch):
    session, events = partial_session
    session.is_running = True
    reviews, speech = install_review_and_speech(session, monkeypatch, review_error=RuntimeError("review timeout"))
    asyncio.run(session._publish_visual_chunk(result_for([session.segments[0]]), 0, 24))
    item = session.segments[0]
    assert speech == [0]
    assert item.status == "READY" and item.needs_review
    assert item.verification["status"] == "incomplete"
    assert item.verification["semantic_verified"] is False
    assert item.to_dict()["preview_is_draft"] is True
    assert session.review_summary["status"] == "incomplete"
    assert session.playable_until <= 24


def test_cancelled_chunk_never_publishes_unfinished_result(partial_session, monkeypatch):
    session, events = partial_session
    reviews, speech = install_review_and_speech(session, monkeypatch)

    def cancel_during_analyze(path, start, end, rows, summary, check):
        session.is_stopped = True
        return result_for(rows)

    monkeypatch.setattr(session.video_intelligence, "analyze_chunk", cancel_during_analyze)

    async def run():
        with pytest.raises(VideoIntelligenceError, match="hủy"):
            await session.start()

    asyncio.run(run())
    assert not session.initialized
    assert not reviews and not speech
    assert not any(event == "init" for event, payload in events)
    assert not list((settings.WORKSPACE_DIR / "cache" / "visual_checkpoints").glob("*/*.json"))


def test_later_source_correction_revokes_old_address_confirmation(partial_session, monkeypatch):
    session, events = partial_session
    install_review_and_speech(session, monkeypatch)
    old = session.segments[0]
    old.status, old.source_method, old.final_vi = "READY", "text-ai", "Chị giúp em nhé."
    old.verification = {"status": "verified", "semantic_verified": True, "address_verified": True,
                        "address_context_sources": {"1": "拜托姐"}}
    old.needs_review = False

    async def run():
        session.is_running = True
        await session._publish_visual_chunk(result_for([session.segments[1]]), 24, 48)

    asyncio.run(run())
    assert old.status == "READY", "Existing media stays playable as a draft"
    assert old.needs_review and old.verification["address_verified"] is False
    assert old.verification["status"] == "unresolved"
    assert old.verification["address_stale_source_ids"] == [1]


def test_cancel_waits_for_active_chunk_review_and_never_synthesizes(partial_session, monkeypatch):
    session, events = partial_session
    _, speech = install_review_and_speech(session, monkeypatch)
    entered, released = threading.Event(), threading.Event()

    def review(reviewer, *args, **kwargs):
        entered.set()
        check = current_execution_context().cancel_check
        while not check():
            time.sleep(.01)
        released.set()
        raise asyncio.CancelledError

    monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", review)
    monkeypatch.setattr(session.video_intelligence, "analyze_chunk",
                        lambda path, start, end, rows, summary, check: result_for(rows))

    async def run():
        work = asyncio.create_task(session.start())
        assert await asyncio.to_thread(entered.wait, 2)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(work, 3)
        assert released.is_set(), "Shared runtime releases only after active review thread exits"

    asyncio.run(run())
    assert session.is_stopped
    assert not speech
    assert session.segments[0].status != "READY"


def test_pause_after_prefix_blocks_next_provider_chunk(partial_session, monkeypatch):
    session, events = partial_session
    install_review_and_speech(session, monkeypatch)
    requests = []

    def analyze(path, start, end, rows, summary, check):
        requests.append(start)
        return result_for(rows)

    monkeypatch.setattr(session.video_intelligence, "analyze_chunk", analyze)

    async def run():
        prefix_ready = asyncio.Event()
        def event(event, payload):
            events.append((event, deepcopy(payload)))
            if event == "segment_update" and payload["id"] == 0 and payload["status"] == "READY":
                session.pause()
                prefix_ready.set()
        session.event_callback = event
        work = asyncio.create_task(session.start())
        await asyncio.wait_for(prefix_ready.wait(), 2)
        await asyncio.sleep(.02)
        assert requests == [0.]
        assert session.get_progress()["status"] == "PAUSED"
        session.resume()
        await asyncio.wait_for(work, 3)
        await session.worker_task

    asyncio.run(run())
    assert requests == [0., 24.]


def test_tts_timeout_in_chunk_callback_propagates_instead_of_polling_forever(partial_session, monkeypatch):
    session, events = partial_session
    install_review_and_speech(session, monkeypatch)
    monkeypatch.setattr(session.video_intelligence, "analyze_chunk",
                        lambda path, start, end, rows, summary, check: result_for(rows))

    async def fail_speech(segment):
        segment.status = "TTS"
        raise TimeoutError("isolated speech request timed out")

    monkeypatch.setattr(session, "_synthesize_segment", fail_speech)

    async def run():
        with pytest.raises(TimeoutError, match="isolated speech"):
            await asyncio.wait_for(session.start(), 2)

    asyncio.run(run())
    assert session.can_retry
    assert session.segments[0].status == "FAILED"
    assert session.segments[0].failed_stage == "TTS"


def test_ocr_only_chunks_emit_full_screen_snapshots_and_advance_silent_tail(partial_session):
    session, events = partial_session
    session.segments = {}
    session.is_running = True

    async def run():
        first = {**result_for([]), "screen_texts": [screen_at(1., 2., "Chữ đầu") ]}
        await session._publish_visual_chunk(first, 0, 24)
        assert session.playable_until == session.buffer_ahead == 24
        second = {**result_for([]), "screen_texts": [screen_at(25., 26., "Chữ sau") ]}
        await session._publish_visual_chunk(second, 24, 48)
        assert session.playable_until == session.buffer_ahead == 48

    asyncio.run(run())
    snapshots = [payload for event, payload in events if event == "screen_update"]
    assert [len(payload["screen_texts"]) for payload in snapshots] == [1, 2]
    assert snapshots[0]["screen_texts"][0]["text_vi"] == "Chữ đầu"
    assert [screen["text_vi"] for screen in snapshots[1]["screen_texts"]] == ["Chữ đầu", "Chữ sau"]
    assert [payload["playable_until"] for event, payload in events if event == "telemetry"] == [24, 48]
    assert any(event == "ready_to_play" for event, payload in events)


def test_restored_ready_chunk_preserves_manual_screens_and_publishes_snapshot(partial_session):
    session, events = partial_session
    session.initialized = session.is_running = True
    session._visual_incremental_started = True
    session._visual_completed_seconds = 24
    session.segments[0].status = "READY"
    session.segments[0].source_method = "text-ai"
    session.segments[0].final_vi = "Lời đã sửa"
    original = screen_at(0., 3.)
    session.screen_texts = [{**original, "end": 2., "mask_only": True, "text_vi": ""},
                            {**original, "start": 2.}]
    committed = deepcopy(session.screen_texts)
    restored = {**result_for([session.segments[0]]), "screen_texts": [original]}

    asyncio.run(session._publish_visual_chunk(restored, 0, 24))
    assert session.screen_texts == committed
    assert session.segments[0].final_vi == "Lời đã sửa"
    snapshots = [payload for event, payload in events if event == "screen_update"]
    assert len(snapshots) == 1 and snapshots[0]["screen_texts"] == committed
    assert any(event == "telemetry" and payload["playable_until"] == 24 for event, payload in events)


def test_manual_screen_splits_survive_prepass_retry_and_full_result(partial_session, monkeypatch):
    session, events = partial_session
    _, speech = install_review_and_speech(session, monkeypatch)
    session.segments[0].start, session.segments[0].duration = .5, 1.5
    original = screen_at(0., 3., "Bản dịch trước sửa")

    def analyze(path, start, end, rows, summary, check):
        if start:
            raise OpenCodeClientError("later timeout")
        return {**result_for(rows), "screen_texts": [deepcopy(original)]}

    monkeypatch.setattr(session.video_intelligence, "analyze_chunk", analyze)

    async def fit(segment, *, text, output_path, **options):
        output_path.write_bytes(b"isolated fitted speech")
        return ({"text": text, "tts_duration": 1, "speed_ratio": 1, "boundaries": []},
                {"subtitle_cues": [], "subtitle_timing_source": "offline-test"}, {})

    monkeypatch.setattr(session, "_fit_dub", fit)

    async def run():
        with pytest.raises(OpenCodeClientError):
            await session.start()
        await session.edit_segment(0, "Lời do người dùng sửa.")
        committed = deepcopy(session.screen_texts)
        assert [(screen["start"], screen["end"]) for screen in committed] == [(0., .5), (.5, 2.), (2., 3.)]
        assert committed[1]["mask_only"] is True and committed[1]["text_vi"] == ""
        monkeypatch.setattr(session.video_intelligence, "analyze_chunk",
            lambda path, start, end, rows, summary, check: {
                **result_for(rows), "screen_texts": [screen_at(25., 26., "Chữ ở đoạn sau")]})
        await session.retry_failed_synthesis()
        await session.start_task
        await session.worker_task
        assert session.screen_texts[:3] == committed
        assert session.screen_texts[3]["text_vi"] == "Chữ ở đoạn sau"
        assert session.segments[0].final_vi == "Lời do người dùng sửa."
        assert session._visual_prepass_complete

    asyncio.run(run())
    assert speech == [0, 1]
    assert any(event == "screen_update" and len(payload["screen_texts"]) >= 3
               and payload["screen_texts"][1].get("mask_only") is True
               for event, payload in events)


def test_each_small_review_group_publishes_audio_before_next_group(partial_session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, events = partial_session
    rows = dense_rows(session)
    reviews, speech = install_review_and_speech(session, monkeypatch)
    original_review = AutomaticTranslationReviewer.review

    def review(reviewer, path, targets, screens, **options):
        first = targets[0].id
        assert speech == list(range(first)), "Earlier reviewed group must already have playable speech"
        assert all(session.segments[sid].status == "READY" for sid in speech)
        assert len(targets) <= 4
        assert len(options["context_segments"]) == len(rows)
        return original_review(reviewer, path, targets, screens, **options)

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    session.is_running = True
    asyncio.run(session._publish_visual_chunk(result_for(rows), 0, 24))
    assert [ids for ids, context in reviews] == [[0, 1, 2, 3], [4, 5, 6, 7], [8]]
    assert speech == list(range(9))


def test_later_group_review_failure_keeps_prior_audio_and_flags_only_failed_group(partial_session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, events = partial_session
    rows = dense_rows(session)
    reviews, speech = install_review_and_speech(session, monkeypatch)
    original_review = AutomaticTranslationReviewer.review

    def review(reviewer, path, targets, screens, **options):
        if targets[0].id == 4:
            assert speech == [0, 1, 2, 3]
            raise OpenCodeClientError("later group transport failed")
        return original_review(reviewer, path, targets, screens, **options)

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    session.is_running = True
    asyncio.run(session._publish_visual_chunk(result_for(rows), 0, 24))
    assert speech == list(range(9))
    assert all(session.segments[sid].verification["status"] == "verified" for sid in (0, 1, 2, 3, 8))
    assert all(session.segments[sid].verification["status"] == "incomplete" for sid in (4, 5, 6, 7))
    assert all(session.segments[sid].needs_review for sid in (4, 5, 6, 7))
    assert all(not session.segments[sid].needs_review for sid in (0, 1, 2, 3, 8))
    assert session.review_summary["status"] == "incomplete"


def test_cancelling_later_group_review_keeps_already_published_audio(partial_session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, events = partial_session
    dense_rows(session)
    reviews, speech = install_review_and_speech(session, monkeypatch)
    original_review = AutomaticTranslationReviewer.review
    entered, released = threading.Event(), threading.Event()

    def review(reviewer, path, targets, screens, **options):
        if targets[0].id != 4:
            return original_review(reviewer, path, targets, screens, **options)
        assert speech == [0, 1, 2, 3]
        entered.set()
        check = current_execution_context().cancel_check
        while not check():
            time.sleep(.01)
        released.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    monkeypatch.setattr(session.video_intelligence, "analyze_chunk",
                        lambda path, start, end, rows, summary, check: result_for(rows))

    async def run():
        work = asyncio.create_task(session.start())
        assert await asyncio.to_thread(entered.wait, 3)
        assert all(session.segments[sid].status == "READY" for sid in range(4))
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(work, 3)
        assert released.is_set()

    asyncio.run(run())
    assert speech == [0, 1, 2, 3]
    assert all(session.segments[sid].audio_url for sid in range(4))
    assert all(session.segments[sid].status != "READY" for sid in range(4, 9))


def test_retry_after_group_tts_failure_keeps_exact_review_before_new_requests(partial_session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, events = partial_session
    dense_rows(session)
    reviews, speech = install_review_and_speech(session, monkeypatch)
    failed_once = False
    original_speech = session._synthesize_segment
    original_review = AutomaticTranslationReviewer.review

    async def synthesize(item):
        nonlocal failed_once
        if item.id == 1 and not failed_once:
            failed_once = True
            item.status = "TTS"
            raise TimeoutError("isolated failed speech")
        await original_speech(item)

    def review(reviewer, path, targets, screens, **options):
        if targets[0].id == 4:
            assert speech[:4] == [0, 1, 2, 3], "Reused audits synthesize before any fresh review"
        return original_review(reviewer, path, targets, screens, **options)

    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    analyzed = Mock(side_effect=lambda path, start, end, rows, summary, check: result_for(rows))
    monkeypatch.setattr(session.video_intelligence, "analyze_chunk", analyzed)

    async def run():
        with pytest.raises(TimeoutError, match="isolated failed speech"):
            await session.start()
        assert session.segments[1].verification["status"] == "verified"
        assert speech == [0]
        await session.retry_failed_synthesis()
        await session.start_task
        await session.worker_task

    asyncio.run(run())
    assert [ids for ids, context in reviews] == [[0, 1, 2, 3], [4, 5, 6, 7], [8]]
    assert speech == list(range(9))
    assert analyzed.call_count == 2, "Restored first prepass chunk never repeats its provider request"


@pytest.mark.parametrize("change", ["draft", "audit", "context", "model"])
def test_changed_draft_audit_or_context_cannot_reuse_review(partial_session, monkeypatch, change):
    session, events = partial_session
    rows = dense_rows(session, 2)
    reviews, speech = install_review_and_speech(session, monkeypatch)

    async def fail_speech(item):
        item.status = "TTS"
        raise TimeoutError("speech failed")

    monkeypatch.setattr(session, "_synthesize_segment", fail_speech)
    first = result_for(rows)

    async def run():
        session.is_running = True
        with pytest.raises(TimeoutError):
            await session._publish_visual_chunk(first, 0, 24)
        changed = deepcopy(first)
        if change == "draft":
            changed["segments"][0]["final_vi"] = "Bản nháp khác"
        elif change == "audit":
            session.segments[0].verification["semantic_verified"] = False
        elif change == "model":
            monkeypatch.setattr(settings, "OPENCODE_MODEL", "other-review-model")
        else:
            later = SegmentItem(99, 30, 31, 1)
            later.text_zh = "拜托哥"
            session.segments[99] = later
        with pytest.raises(TimeoutError):
            await session._publish_visual_chunk(changed, 0, 24)

    asyncio.run(run())
    assert 0 in reviews[-1][0]
    assert len(reviews) == 2, "Changed validated inputs or accepted audit force independent review"

