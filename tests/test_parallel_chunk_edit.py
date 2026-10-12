"""Concurrent publication/admission contracts; providers are isolated."""
import asyncio
import threading
from pathlib import Path

from test_preview_chunked_pipeline import preview, wav


def test_parallel_draft_waits_for_atomic_manual_edit_and_keeps_its_mask(preview, monkeypatch):
    session, _, _, speech, _ = preview
    session.translation_mode, session.total_duration = "full", 120
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    entered, release_workers = threading.Barrier(3), threading.Event()
    original = session.video_intelligence.prepass

    class PrivateEngine:
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            entered.wait(5)
            assert release_workers.wait(5)
            return original(path, rows, **options)

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)

    async def run():
        owner = asyncio.create_task(session.start())
        edit = None
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), 6)
            focus = next(row for row in session.segments.values() if row.start >= 60)
            registered, commit = asyncio.Event(), asyncio.Event()
            manual_audio = session.segments_dir / "manual.wav"
            mask = {"id": "manual-mask", "start": focus.start, "end": focus.end,
                    "bbox": [.1, .8, .5, .1], "text_zh": focus.asr_text, "text_vi": "",
                    "kind": "subtitle", "confidence": .99, "needs_review": False,
                    "review_reason": "", "mask_only": True}

            async def manual_publication():
                current = asyncio.current_task()
                session.edit_tasks.add(current)
                registered.set()
                try:
                    await commit.wait()
                    wav(manual_audio)
                    focus.final_vi, focus.revision = "Lời người dùng đã sửa.", focus.revision + 1
                    focus.verification = {"status": "manual"}
                    focus.translation_provider, focus.status = "opencode", "READY"
                    focus.audio_path, focus.audio_url = str(manual_audio), "/audio/manual"
                    session.screen_texts.append(mask)
                finally:
                    session.edit_tasks.discard(current)

            edit = asyncio.create_task(manual_publication())
            await registered.wait()
            release_workers.set()
            # Neither text nor revision has committed yet. Publication must
            # still recognize the in-flight edit owner and wait for it.
            await asyncio.sleep(.03)
            assert not session._visual_incremental_started and not speech
            commit.set()
            await edit
            await asyncio.wait_for(owner, 5)
            await session.worker_task
            assert focus.final_vi == "Lời người dùng đã sửa."
            assert focus.verification == {"status": "manual"} and focus.revision == 1
            assert Path(focus.audio_path) == manual_audio and manual_audio.is_file()
            assert mask in session.screen_texts and focus.id not in speech
            assert all(job["state"] == "COMPLETED" for job in session._chunk_jobs)
        finally:
            release_workers.set()
            if edit is not None and not edit.done():
                edit.cancel()
                await asyncio.gather(edit, return_exceptions=True)
            if not owner.done():
                session.stop()
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)

    asyncio.run(run())


def test_parallel_pause_blocks_next_source_packet_until_resumed(preview, monkeypatch):
    session, prepared, _, speech, _ = preview
    session.translation_mode, session.total_duration = "full", 120
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    import core.streaming.chunked_source as source
    original_prepare, original_prepass = source.prepare_interval, session.video_intelligence.prepass
    paused = asyncio.Event()

    async def prepare(current, start, end):
        result = await original_prepare(current, start, end)
        if len(prepared) == 1:
            session.pause_event.clear()
            paused.set()
        return result

    class PrivateEngine:
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            return original_prepass(path, rows, **options)

    monkeypatch.setattr(source, "prepare_interval", prepare)
    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)

    async def run():
        owner = asyncio.create_task(session.start())
        try:
            await asyncio.wait_for(paused.wait(), 5)
            await asyncio.sleep(.03)
            assert len(prepared) == 1 and not speech and not session._chunk_jobs
            session.pause_event.set()
            await asyncio.wait_for(owner, 5)
            await session.worker_task
            assert len(prepared) > 1
            assert all(job["state"] == "COMPLETED" for job in session._chunk_jobs)
        finally:
            if not owner.done():
                session.stop()
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)

    asyncio.run(run())
