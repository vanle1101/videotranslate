"""Fatal checkpoint failures must stop parallel admission and native owners."""
import asyncio
import threading
import time

import pytest

from config import settings
from core.streaming.recovery import contiguous_coverage
from test_preview_chunked_pipeline import preview


def test_parallel_checkpoint_failure_never_admits_peer_or_retries_interval(preview, monkeypatch):
    session, _, _, speech, events = preview
    session.translation_mode = "full"
    session.total_duration = 120
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 60)
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    admissions = []
    original = session.video_intelligence.prepass

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            admissions.append(options["start_time"])
            return original(path, rows, **options)

    def fail_admission_checkpoint():
        if (not getattr(session, "_persistence_capacity_failed", False)
                and any(row["state"] == "RUNNING" for row in session._chunk_jobs)):
            session._persistence_capacity_failed = True
            session.is_running = False
            session.error = "injected disk-full checkpoint failure"
            raise RuntimeError(session.error)

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)
    monkeypatch.setattr(session, "_persist_if_enabled", fail_admission_checkpoint)

    async def run():
        with pytest.raises(RuntimeError, match="disk-full checkpoint failure"):
            await asyncio.wait_for(session.start(), 5)
        assert session._chunk_followup_task is None and session.worker_task is None
        assert session._persistence_capacity_failed and not session.is_running
        assert not session.can_retry
        assert session._visual_completed_seconds == 0
        assert contiguous_coverage(session._chunk_jobs) == 0
        assert all(row["state"] != "COMPLETED" for row in session._chunk_jobs)
        assert sum(row["attempts"] for row in session._chunk_jobs) == 1
        assert not any(task is not asyncio.current_task() and not task.done()
                       for task in asyncio.all_tasks())

    asyncio.run(run())
    assert admissions == speech == []
    assert any(event == "error" and "disk-full checkpoint failure" in payload["message"]
               for event, payload in events)


def test_parallel_checkpoint_failure_cancels_and_drains_already_running_native_owners(preview, monkeypatch):
    session, _, _, speech, events = preview
    session.translation_mode = "full"
    session.total_duration = 120
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 60)
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    entered, exited = threading.Barrier(3), []
    closed = []

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                closed.append(1)
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            entered.wait(5)
            try:
                while not options["cancel_check"]():
                    time.sleep(.005)
                raise asyncio.CancelledError
            finally:
                exited.append(options["start_time"])

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)

    async def run():
        owner = asyncio.create_task(session.start())
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
            session._persistence_capacity_failed = True
            session.is_running = False
            session.error = "injected checkpoint failure while native work is active"
            with pytest.raises(RuntimeError, match="checkpoint failure while native work"):
                await asyncio.wait_for(owner, 5)
        finally:
            if not owner.done():
                session.is_stopped = True
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
        assert sorted(exited) == [0., 60.] and len(closed) == 2
        assert session._chunk_followup_task is None and session.worker_task is None
        assert not session.is_running and not session.can_retry
        assert all(row["state"] == "RETRY_PENDING" for row in session._chunk_jobs)
        assert session._visual_completed_seconds == contiguous_coverage(session._chunk_jobs) == 0
        assert not any(task is not asyncio.current_task() and not task.done()
                       for task in asyncio.all_tasks())

    asyncio.run(run())
    assert speech == []
    assert any(event == "error" and "checkpoint failure while native work" in payload["message"]
               for event, payload in events)


def test_parallel_post_completion_checkpoint_failure_does_not_leave_completed_ghost(preview, monkeypatch):
    """A completion followed by a failed save must be retryable on reload."""
    session, _, _, speech, events = preview
    session.translation_mode = "full"
    session.total_duration = 120
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 60)
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    original = session.video_intelligence.prepass
    admissions = []

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            admissions.append(options["start_time"])
            return original(path, rows, **options)

    save_failed = False
    def fail_after_completed_save():
        nonlocal save_failed
        if (not save_failed and session._chunk_jobs
                and any(row["state"] == "COMPLETED" for row in session._chunk_jobs)):
            save_failed = True
            session._persistence_capacity_failed = True
            session.is_running = False
            session.error = "injected post-completion checkpoint failure"
            raise RuntimeError(session.error)

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)
    monkeypatch.setattr(session, "_persist_if_enabled", fail_after_completed_save)

    async def run():
        with pytest.raises(RuntimeError, match="post-completion checkpoint failure"):
            await asyncio.wait_for(session.start(), 5)
        # The in-memory manifest must not claim a durable success when the
        # publication checkpoint failed.
        assert all(row["state"] != "COMPLETED" for row in session._chunk_jobs)
        assert all(row["state"] == "RETRY_PENDING" for row in session._chunk_jobs)
        assert session._visual_completed_seconds == 0
        assert not session.can_retry and not session.is_running
        assert not any(task is not asyncio.current_task() and not task.done()
                       for task in asyncio.all_tasks())

    asyncio.run(run())
    assert sorted(admissions) == [0.0, 60.0]
    # The first wave may already have published valid speech before the later
    # checkpoint failure. It must remain usable and must never be synthesized
    # twice while the failed durable run is recovered.
    assert speech and len(speech) == len(set(speech))
    assert all(row.status in {"READY", "WAITING", "FAILED"} for row in session.segments.values())
