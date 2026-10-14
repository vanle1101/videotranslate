"""Detached review/TTS scheduling; fixtures never invoke real providers."""
import asyncio
import threading
import time
from copy import deepcopy

import pytest

from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.translation_review import AutomaticTranslationReviewer
from core.runtime_context import current_execution_context
from core.streaming.glossary import build_glossary
from test_preview_chunked_pipeline import preview


@pytest.fixture
def lookahead(preview, monkeypatch):
    session, *_ = preview
    for name in ("_review_translations", "_prefetch_visual_review"):
        monkeypatch.setattr(session, name, getattr(StreamingPipelineSession, name).__get__(session))
    session.VISUAL_REVIEW_GROUP_SIZE = 1
    session.is_running = session.initialized = True
    session._retry_schedule = []
    session.segments = {}
    for sid in range(3):
        row = SegmentItem(sid, sid * 3, sid * 3 + 1, 1)
        row.text_zh = row.asr_text = f"来源 {sid}"
        row.final_vi = row.literal_vi = row.natural_vi = f"Lời {sid}."
        row.translation_provider = "opencode"
        row.translation_model = "isolated-model"
        row.verification = {"status": "pending"}
        row.needs_review = True
        session.segments[sid] = row
    return session


def result(rows):
    return {"segments": {row.id: {**deepcopy(row.to_dict()), "needs_review": False,
        "verification": {"status": "verified", "semantic_verified": True}} for row in rows},
        "summary": {"checked": len(rows), "verified": len(rows)}, "translation_sources": []}


async def event_set(event):
    async def poll():
        while not event.is_set():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(), 5)


def test_next_review_runs_during_current_speech_without_duplicate_calls(lookahead, monkeypatch):
    session = lookahead
    second_started = threading.Event()
    calls, speech, active = [], [], []

    def review(_reviewer, path, rows, screens, **options):
        assert not active, "Two native reviews must never overlap"
        active.append(rows[0].id)
        calls.append(rows[0].id)
        if rows[0].id == 1:
            second_started.set()
        active.clear()
        return result(rows)

    async def synthesize(row):
        speech.append(row.id)
        if row.id == 0:
            await event_set(second_started)
            assert session.segments[1].verification["status"] == "pending"
            assert session._review_scope is None
        row.status = "READY"
        row.audio_url = f"/isolated/{row.id}"
        row.tts_duration = .9

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    monkeypatch.setattr(session, "_process_segment", synthesize)
    asyncio.run(session._finish_visual_chunk({0, 1, 2}, {0, 1, 2}, {i: str(i) for i in range(3)}))
    assert calls == speech == [0, 1, 2]
    assert session._visual_published_ids == {0, 1, 2}


@pytest.mark.parametrize("change", ["target", "neighbor_source", "neighbor_draft", "glossary", "model", "language", "screens", "source_file"])
def test_stale_prefetch_is_discarded_before_publication(lookahead, monkeypatch, change):
    session = lookahead
    calls = []

    def review(_reviewer, path, rows, screens, **options):
        calls.append(deepcopy(options.get("context_segments")))
        value = result(rows)
        value["segments"][1]["final_vi"] = f"Review {len(calls)}."
        return value

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)

    async def run():
        packet = await session._prefetch_visual_review({1})
        assert session.segments[1].verification["status"] == "pending"
        assert not session._retry_schedule
        if change == "target":
            session.segments[1].revision += 1
            session.segments[1].final_vi = "Bản vừa sửa."
        elif change == "neighbor_source":
            session.segments[0].text_zh = "不同来源"
        elif change == "neighbor_draft":
            session.segments[0].final_vi = "Lời lân cận đã thay đổi."
        elif change == "glossary":
            session.shared_glossary = build_glossary([{"src": "a", "tgt": "b", "note": "",
                "provenance": {"kind": "user_supplied", "reference": "isolated"}}])
        elif change == "model":
            monkeypatch.setattr(settings, "OPENCODE_MODEL", "changed-isolated-model")
        elif change == "language":
            session.source_language = "ko"
        elif change == "screens":
            session.screen_texts = [{"id": "new", "start": 0, "end": 1}]
        else:
            session.video_path.write_bytes(b"a changed isolated source file")
        await session._review_translations(segment_ids={1}, _prefetched=packet)

    asyncio.run(run())
    assert len(calls) == 2
    assert session.segments[1].final_vi == "Review 2."


def test_prefetched_failure_is_scheduled_once_without_reissuing_request(lookahead, monkeypatch):
    from core.engines.translation.opencode_client import OpenCodeTimeoutError
    session = lookahead
    calls = []

    def review(*args, **options):
        calls.append(1)
        raise OpenCodeTimeoutError("isolated transport timeout")

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)

    async def run():
        packet = await session._prefetch_visual_review({1})
        assert not session._retry_schedule
        with pytest.raises(OpenCodeTimeoutError):
            await session._review_translations(segment_ids={1}, _prefetched=packet)

    asyncio.run(run())
    assert calls == [1]
    assert len(session._retry_schedule) == 1
    assert session._retry_schedule[0]["error_code"] == "provider_timeout"


@pytest.mark.parametrize("stop_session", [False, True])
def test_cancellation_drains_native_lookahead_and_does_not_publish_it(lookahead, monkeypatch, stop_session):
    session = lookahead
    entered, reaped = threading.Event(), threading.Event()
    calls = []

    def review(_reviewer, path, rows, screens, **options):
        calls.append(rows[0].id)
        if rows[0].id == 1:
            entered.set()
            deadline = time.monotonic() + 5
            while not current_execution_context().cancel_check():
                if time.monotonic() > deadline:
                    raise AssertionError("Native reviewer was abandoned")
                time.sleep(.001)
            reaped.set()
            raise asyncio.CancelledError
        return result(rows)

    async def speech(row):
        await event_set(entered)
        if stop_session:
            session.is_stopped = True
        raise asyncio.CancelledError

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    monkeypatch.setattr(session, "_process_segment", speech)

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await session._finish_visual_chunk({0, 1}, {0, 1}, {0: "0", 1: "1"})
        assert reaped.is_set()
        assert session.segments[1].verification["status"] == "pending"
        assert not session._visual_published_ids
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(run())
    assert calls == [0, 1]


def test_waits_for_pending_editor_before_accepting_prefetched_context(lookahead, monkeypatch):
    session = lookahead
    calls = []

    def review(_reviewer, path, rows, screens, **options):
        calls.append(deepcopy(options["context_segments"]))
        return result(rows)

    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)

    async def run():
        packet = await session._prefetch_visual_review({1})
        release = asyncio.Event()

        async def editor():
            await release.wait()
            session.segments[0].final_vi = "Ngữ cảnh vừa sửa."
            session.segments[0].revision += 1

        edit = asyncio.create_task(editor())
        session.edit_tasks.add(edit)
        owner = asyncio.create_task(session._review_translations(segment_ids={1}, _prefetched=packet))
        await asyncio.sleep(0)
        assert not owner.done()
        release.set()
        await owner
        assert len(calls) == 2
        assert calls[-1][0]["final_vi"] == "Ngữ cảnh vừa sửa."

    asyncio.run(run())


def test_optional_lookahead_initialization_failure_uses_normal_review_owner(lookahead, monkeypatch):
    session = lookahead
    original = session._review_invocation_snapshot
    failed_once = False
    calls = []

    def snapshot(ids, force_review=False):
        nonlocal failed_once
        if ids == {1} and not failed_once:
            failed_once = True
            raise RuntimeError("isolated optional lookahead preparation failure")
        return original(ids, force_review)

    def review(_reviewer, path, rows, screens, **options):
        calls.extend(row.id for row in rows)
        return result(rows)

    async def speech(row):
        await asyncio.sleep(0)
        row.status = "READY"

    monkeypatch.setattr(session, "_review_invocation_snapshot", snapshot)
    monkeypatch.setattr(session, "_process_segment", speech)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    asyncio.run(session._finish_visual_chunk({0, 1, 2}, {0, 1, 2}, {i: str(i) for i in range(3)}))
    assert failed_once
    assert calls == [0, 1, 2]


def test_manual_edit_wins_over_detached_review(lookahead, monkeypatch):
    session = lookahead
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda _r, _p, rows, _s, **kw: result(rows))

    async def run():
        packet = await session._prefetch_visual_review({1})
        row = session.segments[1]
        row.final_vi = "Giữ lời người dùng."
        row.revision += 1
        row.verification = {"status": "manual"}
        await session._review_translations(segment_ids={1}, _prefetched=packet)
        assert row.final_vi == "Giữ lời người dùng."
        assert row.verification == {"status": "manual"}

    asyncio.run(run())
