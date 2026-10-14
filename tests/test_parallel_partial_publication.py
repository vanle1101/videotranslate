"""Real source scheduler, injected providers; partial speech is not coverage."""
import asyncio
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
        await publish(result, lo, hi, **kwargs)
        if kwargs.get("partial_only"):
            snapshots.append({"rows":{sid:deepcopy(owner.segments[sid].to_dict())
                for sid in result["segments"]},"coverage":owner._visual_completed_seconds})
            assert speech == before_speech, "Partial publication must not invoke TTS itself"
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
