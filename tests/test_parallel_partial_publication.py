"""Real source scheduler, injected providers; partial speech is not coverage."""
import asyncio
import threading
from copy import deepcopy

import pytest

from core.engines.translation.opencode_client import OpenCodeRequestError
from test_preview_chunked_pipeline import preview


@pytest.mark.parametrize("edit", [None, "manual", "stale"])
def test_parallel_ocr_failure_retains_speech_without_completing_coverage(preview, monkeypatch, edit):
    owner, _, _, speech, events = preview
    owner.translation_mode, owner.total_duration = "full", 120
    monkeypatch.setattr(owner, "_parallel_chunk_capacity", lambda: 2)
    original = owner.video_intelligence.prepass
    partial_ids = []
    snapshots = []
    publish = owner._publish_visual_chunk
    async def capture(result, lo, hi, **kwargs):
        before_speech = list(speech)
        states = await publish(result, lo, hi, **kwargs)
        if kwargs.get("partial_only"):
            snapshots.append({"rows":{sid:deepcopy(owner.segments[sid].to_dict())
                for sid in result["segments"]},"coverage":owner._visual_completed_seconds})
            assert speech == before_speech, "Partial publication must not invoke TTS itself"
        return states
    monkeypatch.setattr(owner,"_publish_visual_chunk",capture)
    class Engine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            if options["start_time"] != 0:
                return original(path, rows, **options)
            # Obtain normal validated fixture output but suppress complete
            # publication. Model one successful speech batch and failed OCR.
            result = original(path, rows, **{**options,"chunk_callback":lambda *a:None})
            ids = [row.id for row in rows[:2]]
            partial_ids.extend(ids)
            result["segments"] = {sid:result["segments"][sid] for sid in ids}
            options["partial_callback"](result, options["start_time"], options["end_time"])
            if edit:
                # Provider is still in flight; a committed newer row wins at
                # publication. No Qt/UI or actual provider participates here.
                target = owner.segments[ids[0]]
                target.final_vi = "Lời mới được giữ."
                target.revision += 1
                target.translation_provider = "opencode"
                target.verification = {"status":"manual" if edit == "manual" else "unresolved"}
            raise OpenCodeRequestError("Unknown OCR failure", code="provider_failed")
    monkeypatch.setattr(owner,"_new_chunk_intelligence",Engine)
    async def run():
        await owner.start()
        await owner.worker_task
    asyncio.run(run())
    first, second = owner._chunk_jobs
    assert first["state"] == "FAILED" and second["state"] == "COMPLETED"
    assert owner._visual_completed_seconds == 0
    assert not owner._visual_prepass_complete
    assert not owner._retry_schedule
    assert owner.can_retry
    retained = owner.segments[partial_ids[1]]
    assert retained.final_vi == f"Lời {retained.id}."
    assert retained.translation_provider == "opencode"
    at_publication = next(s["rows"][retained.id] for s in snapshots if retained.id in s["rows"])
    assert at_publication["needs_review"] and at_publication["verification"]["semantic_verified"] is False
    assert all(s["coverage"] == 0 for s in snapshots)
    assert any(kind == "segment_update" and data["id"] == retained.id
               and data["final_vi"] == retained.final_vi for kind,data in events)
    assert any(row.status == "FAILED" and row.failed_stage == "TRANSLATING"
               for row in owner.segments.values() if row.start < first["end"])
    if edit:
        changed = owner.segments[partial_ids[0]]
        assert changed.final_vi == "Lời mới được giữ."
        assert changed.verification["status"] == ("manual" if edit == "manual" else "unresolved")


@pytest.mark.parametrize("edit", [None, "manual", "stale", "during_emit"])
def test_later_worker_publishes_draft_before_first_finishes_and_full_outcome_keeps_ownership(preview, monkeypatch, edit):
    owner, _, _, speech, events = preview
    owner.translation_mode, owner.total_duration = "full", 120
    monkeypatch.setattr(owner, "_parallel_chunk_capacity", lambda: 2)
    original = owner.video_intelligence.prepass
    released, acknowledged = threading.Event(), threading.Event()
    partial_ids = []
    if edit == "during_emit":
        emit = owner.emit
        async def edit_on_emit(kind, data, **options):
            await emit(kind, data, **options)
            if kind == "segment_update" and data.get("id") in partial_ids and data.get("final_vi"):
                target = owner.segments[data["id"]]
                if target.final_vi != "Nội dung mới giữ nguyên.":
                    target.final_vi = "Nội dung mới giữ nguyên."
                    target.revision += 1
                    target.verification = {"status": "manual"}
        monkeypatch.setattr(owner, "emit", edit_on_emit)
    class Engine:
        provider = "opencode"
        class OCR:
            def close(self): pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            result = original(path, rows, **{**options, "chunk_callback": lambda *a: None})
            if options["start_time"] > 0:
                partial = deepcopy(result)
                partial["segments"] = {rows[0].id: partial["segments"][rows[0].id]}
                partial_ids.append(rows[0].id)
                options["partial_callback"](partial, options["start_time"], options["end_time"])
                acknowledged.set()
                # A later validated whole result is allowed to refine its own
                # early draft, never to overwrite intervening user/review work.
                result["segments"][rows[0].id]["final_vi"] = "Bản đủ đã đối chiếu."
            assert released.wait(8), "test coordinator did not release native fixture"
            options["chunk_callback"](result, options["start_time"], options["end_time"])
            return result
    monkeypatch.setattr(owner, "_new_chunk_intelligence", Engine)
    async def run():
        task = asyncio.create_task(owner.start())
        try:
            for _ in range(600):
                if acknowledged.is_set(): break
                await asyncio.sleep(.01)
            assert acknowledged.is_set(), "Partial draft stayed buffered behind whole/earlier workers"
            sid = partial_ids[0]
            row = owner.segments[sid]
            assert not task.done()
            assert row.final_vi == ("Nội dung mới giữ nguyên." if edit == "during_emit" else f"Lời {sid}.")
            assert owner._visual_completed_seconds == 0
            assert all(chunk["state"] == "RUNNING" for chunk in owner._chunk_jobs)
            assert speech == []
            if edit != "during_emit":
                assert row.verification["semantic_verified"] is False
            assert any(kind == "segment_update" and data["id"] == sid and data["final_vi"] for kind, data in events)
            if edit and edit != "during_emit":
                row.final_vi = "Nội dung mới giữ nguyên."
                row.revision += 1
                row.verification = {"status": "manual" if edit == "manual" else "unresolved"}
            released.set()
            await asyncio.wait_for(task, 10)
            await asyncio.wait_for(owner.worker_task, 10)
            assert all(chunk["state"] == "COMPLETED" for chunk in owner._chunk_jobs)
            assert owner._visual_completed_seconds == 120
            assert row.final_vi == ("Nội dung mới giữ nguyên." if edit else "Bản đủ đã đối chiếu.")
            assert len(speech) == len(set(speech)), "Partial publication caused duplicate TTS"
        finally:
            released.set()
            if not task.done():
                owner.is_stopped = True
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["glossary", "persist"])
def test_partial_publication_rejects_changed_glossary_or_failed_checkpoint(preview, monkeypatch, failure):
    from core.streaming import pipeline
    owner, _, _, speech, events = preview
    owner.translation_mode, owner.total_duration = "full", 60
    monkeypatch.setattr(owner, "_parallel_chunk_capacity", lambda: 2)
    original = owner.video_intelligence.prepass
    glossary_version = [None]
    snapshot = pipeline.glossary_snapshot
    def glossary(session):
        value = snapshot(session)
        return {**value, "version": glossary_version[0]} if glossary_version[0] is not None else value
    monkeypatch.setattr(pipeline, "glossary_snapshot", glossary)
    if failure == "persist":
        def persist():
            if any(row.final_vi for row in owner.segments.values()):
                owner._persistence_capacity_failed = True
                owner.error = "injected partial checkpoint failure"
                raise RuntimeError(owner.error)
        monkeypatch.setattr(owner, "_persist_if_enabled", persist)
    class Engine:
        provider = "opencode"
        class OCR:
            def close(self): pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            result = original(path, rows, **{**options, "chunk_callback": lambda *a: None})
            result["segments"] = {rows[0].id:result["segments"][rows[0].id]}
            if failure == "glossary": glossary_version[0] = 999
            options["partial_callback"](result, options["start_time"], options["end_time"])
            pytest.fail("Rejected callback must not acknowledge success")
    monkeypatch.setattr(owner, "_new_chunk_intelligence", Engine)
    async def run():
        if failure == "persist":
            with pytest.raises(RuntimeError, match="checkpoint failure"):
                await asyncio.wait_for(owner.start(), 8)
        else:
            await asyncio.wait_for(owner.start(), 8)
            await asyncio.wait_for(owner.worker_task, 8)
            assert all(not row.final_vi for row in owner.segments.values())
        assert owner._visual_completed_seconds == 0 and not speech
        assert all(chunk["state"] != "COMPLETED" for chunk in owner._chunk_jobs)
        assert not any(kind == "segment_update" and data.get("final_vi") for kind,data in events)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["stop", "disk"])
def test_pending_partial_acknowledgments_cancel_and_drain_without_late_writes(preview, monkeypatch, failure):
    owner, _, _, speech, events = preview
    owner.translation_mode, owner.total_duration = "full", 120
    monkeypatch.setattr(owner, "_parallel_chunk_capacity", lambda: 2)
    original = owner.video_intelligence.prepass
    requested = threading.Barrier(3)
    closed = []
    entered = asyncio.Event()
    hold = asyncio.Event()
    publish = owner._publish_visual_chunk
    async def blocked(result, lo, hi, **options):
        if options.get("partial_only"):
            entered.set()
            await hold.wait()
        return await publish(result, lo, hi, **options)
    monkeypatch.setattr(owner, "_publish_visual_chunk", blocked)
    class Engine:
        provider = "opencode"
        class OCR:
            def close(self): closed.append(True)
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            result = original(path, rows, **{**options, "chunk_callback": lambda *a: None})
            result["segments"] = {rows[0].id:result["segments"][rows[0].id]}
            requested.wait(6)
            options["partial_callback"](result, options["start_time"], options["end_time"])
            pytest.fail("Cancelled callback must not acknowledge success")
    monkeypatch.setattr(owner, "_new_chunk_intelligence", Engine)
    async def run():
        task = asyncio.create_task(owner.start())
        try:
            await asyncio.wait_for(asyncio.to_thread(requested.wait, 6), 7)
            await asyncio.wait_for(entered.wait(), 5)
            if failure == "disk":
                owner._persistence_capacity_failed = True
                owner.error = "injected disk capacity failure"
            else:
                owner.is_stopped = True
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 6)
            before = [(row.id, row.final_vi) for row in owner.segments.values()]
            hold.set()
            await asyncio.sleep(.05)
            assert [(row.id, row.final_vi) for row in owner.segments.values()] == before
            assert all(not row.final_vi for row in owner.segments.values())
            assert owner._visual_completed_seconds == 0 and not speech
            assert len(closed) == 2
            assert not any(t is not asyncio.current_task() and not t.done() for t in asyncio.all_tasks())
        finally:
            hold.set()
            if not task.done():
                owner.is_stopped = True
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
