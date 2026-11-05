"""Recovery budgets keep owners live without replaying successful output."""
import asyncio
from collections import Counter
from unittest.mock import AsyncMock

import pytest

from core.engines.tts.edge_fallback import EdgeTTSRequestError
from core.engines.translation.opencode_client import OpenCodeTimeoutError
from core.engines.translation.semantic_translator import PacingReviewRejected
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.streaming.pipeline import _transient_failure
from test_preview_chunked_pipeline import preview
from test_initial_transient_speech_retry import session, install_failure, transient
from test_review_pacing_recovery import reviewed_session, review_response
from core.translation_review import AutomaticTranslationReviewer


@pytest.fixture(autouse=True)
def immediate_recovery(monkeypatch):
    monkeypatch.setattr("core.streaming.pipeline.AUTOMATIC_RECOVERY_DELAYS", (0., 0., 0.))


@pytest.mark.parametrize("parallel", [False, True])
def test_provider_recovers_third_outer_attempt_and_retains_other_chunks(preview, monkeypatch, parallel):
    current, _, _, speech, events = preview
    current.translation_mode, current.total_duration = "full", 64
    monkeypatch.setattr(current, "_parallel_chunk_capacity", lambda: 2 if parallel else 1)
    original = current.video_intelligence.prepass
    calls = Counter()

    def prepass(path, rows, **options):
        start = options["start_time"]
        calls[start] += 1
        if start == 0 and calls[start] < 3:
            try:
                raise OpenCodeTimeoutError("provider timeout")
            except OpenCodeTimeoutError:
                raise RuntimeError("wrapped provider failure") from None
        return original(path, rows, **options)

    class Engine:
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
    Engine.prepass = staticmethod(prepass)

    monkeypatch.setattr(current.video_intelligence, "prepass", prepass)
    monkeypatch.setattr(current, "_new_chunk_intelligence", Engine)

    async def run():
        await current.start()
        await current.worker_task

    asyncio.run(run())
    assert calls[0] == 3
    assert all(count == 1 for start, count in calls.items() if start != 0)
    assert all(row["state"] == "COMPLETED" for row in current._chunk_jobs)
    assert len(speech) == len(set(speech))
    assert any(data.get("phase") == "recovery_wait" for event, data in events if event == "progress")


def test_source_transient_retries_exact_cursor_and_preserves_prior_rows(preview, monkeypatch):
    current, _, _, _, _ = preview
    import core.streaming.chunked_source as source
    original = source.prepare_interval
    calls = []

    async def prepare(owner, start, end):
        calls.append(start)
        if len(calls) < 3:
            raise TimeoutError("source service temporary failure")
        return await original(owner, start, end)

    monkeypatch.setattr(source, "prepare_interval", prepare)
    current.is_running = True
    assert asyncio.run(current._prepare_source_until(20))
    assert calls == [0, 0, 0]
    assert len(current.segments) == len(set((r.start, r.end) for r in current.segments.values()))


def test_language_mismatch_reports_real_cause_without_network_retries(preview, monkeypatch):
    from core.engines.asr.source_language import SourceLanguageMismatchError
    current, _, _, _, events = preview
    failure = AsyncMock(side_effect=SourceLanguageMismatchError(
        {"language": "ko", "probability": .96, "speech_segments": 3}))
    monkeypatch.setattr("core.streaming.chunked_source.prepare_interval", failure)
    current.is_running = True
    assert not asyncio.run(current._prepare_source_until(20))
    assert failure.await_count == 1
    assert any("tiếng Hàn" in warning for warning in current.warnings)
    assert any(data.get("phase") == "source_blocked" and "tiếng Hàn" in data["stage"]
               for event, data in events if event == "progress")
    assert not any(data.get("phase") == "recovery_wait" for event, data in events if event == "progress")


def test_speech_rounds_recover_without_redoing_translation_or_healthy_rows(session, monkeypatch):
    calls = install_failure(session, monkeypatch)
    retries = []

    async def speech(row):
        retries.append(row.id)
        row.status = "TTS"
        if len(retries) < 3:
            raise transient()
        row.status = "READY"

    monkeypatch.setattr(session, "_synthesize_segment", speech)
    for row in session.segments.values():
        session.queue.put_nowait((row.start, row.id))
    asyncio.run(session._worker_loop())
    assert calls == [("process", 0), ("process", 1), ("process", 2)]
    assert retries == [0, 0, 0]
    assert session.segments[0].status == "READY"
    assert not session.error and not session._transient_speech_failures


def test_exhausted_speech_is_finite_and_keeps_text(session, monkeypatch):
    install_failure(session, monkeypatch)
    async def speech(row):
        row.status = "TTS"
        raise transient()
    attempt = AsyncMock(side_effect=speech)
    monkeypatch.setattr(session, "_synthesize_segment", attempt)
    for row in session.segments.values():
        session.queue.put_nowait((row.start, row.id))
    asyncio.run(session._worker_loop())
    assert attempt.await_count == 3
    assert session.segments[0].final_vi == "Lời đã dịch 0."
    assert session.segments[1].status == session.segments[2].status == "READY"
    assert not session._transient_speech_failures
    assert any("Đã hết 3 lượt" in message for message in session.warnings)


def test_retry_wait_pauses_and_cancellation_clears_live_countdown(session):
    async def run():
        session.pause()
        wait = asyncio.create_task(session._wait_automatic_recovery("giọng đọc", 1, 5))
        await asyncio.sleep(0)
        assert session.get_progress()["recovery_remaining_seconds"] == 5
        assert not wait.done()
        wait.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait
        assert session._automatic_recovery_wait is None
    asyncio.run(run())


@pytest.mark.parametrize("cancel_newer", [True, False])
def test_overlapping_recovery_waits_keep_the_remaining_owner_visible(session, cancel_newer):
    async def run():
        session.pause()
        first = asyncio.create_task(session._wait_automatic_recovery("source", 1, 5))
        await asyncio.sleep(0)
        second = asyncio.create_task(session._wait_automatic_recovery("review", 1, 15))
        await asyncio.sleep(0)
        cancelled, remaining = (second, first) if cancel_newer else (first, second)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert session._automatic_recovery_wait["label"] == ("source" if cancel_newer else "review")
        assert session.get_progress()["recovery_remaining_seconds"] == (5 if cancel_newer else 15)
        remaining.cancel()
        with pytest.raises(asyncio.CancelledError):
            await remaining
        assert session._automatic_recovery_wait is None
        assert not session._automatic_recovery_waits
    asyncio.run(run())


def test_edit_during_retry_cooldown_wins_before_synthesis(session, monkeypatch):
    install_failure(session, monkeypatch)
    speech = AsyncMock()
    monkeypatch.setattr(session, "_synthesize_segment", speech)

    async def wait(*args):
        row = session.segments[0]
        row.final_vi = "Người dùng đã sửa trong lúc chờ."
        row.verification, row.status = {"status": "manual"}, "READY"

    monkeypatch.setattr(session, "_wait_automatic_recovery", wait)
    async def run():
        await session._finish_visual_chunk([0], set(), {})
        await session._recover_transient_speech_after_source()
    asyncio.run(run())
    speech.assert_not_awaited()
    assert session.segments[0].status == "READY"


def test_review_retry_keeps_immutable_inputs_and_preserves_newer_manual_edit(reviewed_session, monkeypatch):
    current = reviewed_session
    seen = []

    def review(self, video, rows, screen_texts, **options):
        seen.append(rows[0].final_vi)
        if len(seen) < 3:
            raise OpenCodeTimeoutError("timeout")
        return review_response()

    async def wait(*args):
        focus = current.segments[112]
        focus.revision += 1
        focus.final_vi = "Lời người dùng vừa sửa."
        focus.verification = {"status": "manual"}

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    monkeypatch.setattr(current, "_wait_automatic_recovery", wait)
    asyncio.run(current._review_translations(segment_ids={112, 113}, defer_audio=True))
    assert seen == ["Lời cũ."] * 3
    assert current.segments[112].final_vi == "Lời người dùng vừa sửa."
    assert current.segments[112].verification == {"status": "manual"}
    assert current.segments[113].verification["status"] == "verified"


def test_review_second_pass_transient_draft_is_retried_before_publication(reviewed_session, monkeypatch):
    current = reviewed_session
    attempts = []

    def review(*args, **kwargs):
        attempts.append(1)
        result = review_response()
        if len(attempts) < 3:
            for row in result["segments"].values():
                row["verification"] = {"status": "incomplete", "semantic_verified": False,
                    "diagnostic": {"code": "provider_timeout", "stage": "semantic_second_pass"}}
        return result

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(current._review_translations(segment_ids={112, 113}, defer_audio=True))
    assert len(attempts) == 3
    assert current.segments[112].verification["status"] == "corrected"


@pytest.mark.parametrize("error", [ValueError("bad bounds"), MemoryError(),
    SpeechBudgetError("too long"), PacingReviewRejected("meaning rejected"),
    EdgeTTSRequestError("bad certificate", code="tts_tls_certificate")])
def test_permanent_failures_are_not_retried(error):
    assert not _transient_failure(error)
