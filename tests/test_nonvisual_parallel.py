import asyncio
from copy import deepcopy
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import settings
from core.runtime_context import current_execution_context
from core.streaming.pipeline import StreamingPipelineSession, SegmentItem
from core.streaming.nonvisual_translation import translate_nonvisual_rows


@pytest.fixture
def make_session(tmp_path, monkeypatch):
    for name in ("BASE_DIR", "WORKSPACE_DIR", "INPUT_DIR", "OUTPUT_DIR", "TEMP_DIR"):
        value = tmp_path if name == "BASE_DIR" else tmp_path / name
        value.mkdir(exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    def create(name, count, cap):
        source = settings.INPUT_DIR / (name + ".mp4")
        source.write_bytes(b"offline fixture")
        session = StreamingPipelineSession(name, source)
        session.translation_chunk_workers = cap
        session.is_running = session.initialized = True
        session.total_duration = count * 2
        session._update_ready = AsyncMock()
        session._recover_transient_speech_after_source = AsyncMock()
        session._recover_pacing_after_source = AsyncMock()
        session._persist_if_enabled = lambda: None
        session.persist = lambda: None
        for sid in range(count):
            row = SegmentItem(sid, sid * 2, sid * 2 + 2, 2)
            row.text_zh, row.asr_pretranscribed = f"句子{sid}", True
            session.segments[sid] = row
            session.queue.put_nowait((row.start, sid))
        async def synthesize(row, **kwargs):
            row.status, row.failed_stage = "READY", None
        session._synthesize_segment = synthesize
        return session
    return create


@pytest.mark.parametrize("cap", [20, 30, 40, 50])
def test_full_nonvisual_worker_admits_selected_parallel_calls_for_two_videos(make_session, cap):
    sessions = [make_session(f"nonvisual-{index}", cap, cap) for index in range(2)]
    barrier = threading.Barrier(cap * 2, timeout=15)
    active, peaks, seen = {}, {}, []
    lock = threading.Lock()
    def provider(**kwargs):
        context = current_execution_context()
        with lock:
            active[context.run_id] = active.get(context.run_id, 0) + 1
            peaks[context.run_id] = max(peaks.get(context.run_id, 0), active[context.run_id])
            seen.append((context.run_id, context.ai_concurrency))
        try:
            barrier.wait()
            return {"final_vi": "Lời Việt."}
        finally:
            with lock:
                active[context.run_id] -= 1
    for session in sessions:
        session.translator = SimpleNamespace(translate_single_segment=provider)
    async def run():
        await asyncio.gather(*(session._worker_loop() for session in sessions))
    asyncio.run(run())
    assert peaks == {session.task_id: cap for session in sessions}
    assert all(value == cap for _, value in seen)
    assert all(row.status == "READY" for session in sessions for row in session.segments.values())


def test_nonvisual_context_is_local_and_manual_drafts_are_never_targets(make_session):
    session = make_session("neighbors", 12, 5)
    preserved = session.segments[4]
    preserved.final_vi, preserved.verification = "Người dùng.", {"status": "manual"}
    preserved.revision = 3
    before = deepcopy(vars(preserved))
    calls = []
    def provider(**kwargs):
        sid = int(kwargs["text_zh"].removeprefix("句子"))
        context = kwargs["rolling_context"]
        assert {row["id"] for row in context} <= set(range(max(0, sid - 3), min(12, sid + 4)))
        calls.append(sid)
        if sid == 5:
            assert any(row["id"] == 4 and row["verification"]["status"] == "manual" for row in context)
        return {"final_vi": "Dịch."}
    session.translator = SimpleNamespace(translate_single_segment=provider)
    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))
    assert sorted(calls) == [sid for sid in range(12) if sid != 4]
    assert vars(preserved) == before


def test_local_ollama_translation_does_not_chain_unverified_prior_drafts(make_session, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    session = make_session("ollama-context", 12, 1)
    calls = []

    def translate_single_segment(*, text_zh, duration, rolling_context):
        from core.engines.translation.semantic_translator import _compact_local_dialogue_context
        source_id = int(text_zh.removeprefix("句子"))
        context = _compact_local_dialogue_context(rolling_context)
        calls.append((source_id, context))
        return {"final_vi": f"Câu {source_id}.", "needs_review": False}

    session.translator = SimpleNamespace(translate_single_segment=translate_single_segment)
    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))

    assert [identity for identity, _ in calls] == list(range(12))
    assert all(context == "" for _, context in calls)
    assert all(row.status == "READY" for row in session.segments.values())
    assert all(row.final_vi == f"Câu {row.id}." for row in session.segments.values())
    assert all(row.needs_review and row.verification["status"] == "pending"
               for row in session.segments.values())


def test_ollama_preflight_failure_does_not_mark_rows_as_translation_failures(make_session, monkeypatch):
    from core.engines.translation.ollama_client import OllamaClientError
    from core.engines.translation.semantic_translator import SemanticTranslator

    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    session = make_session("ollama-preflight", 1, 1)
    translator = SemanticTranslator("ollama")
    error = OllamaClientError("Ollama local chưa chạy.", code="provider_unavailable")
    calls = []

    def unavailable():
        calls.append(True)
        raise error

    translator.ensure_provider_ready = unavailable
    session.translator = translator
    row = session.segments[0]

    with pytest.raises(OllamaClientError) as raised:
        asyncio.run(translate_nonvisual_rows(session, [row]))

    assert raised.value.code == "provider_unavailable"
    assert calls == [True]
    assert row.status == "WAITING"
    assert not row.error


def test_local_ollama_uses_sixteen_source_grounded_rows_per_request(make_session):
    from core.engines.translation.semantic_translator import SemanticTranslator

    session = make_session("ollama-batches", 9, 1)
    translator = SemanticTranslator("ollama")
    calls = []

    def translate_batch(payload, context, source_context=None):
        identities = [row["id"] for row in payload]
        calls.append((identities, {row["id"] for row in source_context}))
        assert 1 <= len(payload) <= 16
        return {row["id"]: {"literal_vi": f"VI {row['id']}.",
            "natural_vi": f"VI {row['id']}.", "final_vi": f"VI {row['id']}."}
            for row in payload}

    translator._execute_3tier_translation = translate_batch
    session.translator = translator
    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))

    assert [len(ids) for ids, _ in calls] == [9]
    assert all(source_ids == set(range(9)) for _, source_ids in calls)
    assert all(row.status == "READY" and row.final_vi == f"VI {row.id}."
               for row in session.segments.values())


def test_ollama_batches_chinese_rows_while_routing_a_misrecognized_language_separately(make_session, monkeypatch):
    from core.engines.translation.semantic_translator import SemanticTranslator
    import core.streaming.nonvisual_translation as module

    session = make_session("ollama-mixed-languages", 3, 1)
    foreign = session.segments[1]
    foreign.source_language = "ja"
    foreign.source_language_probability = .99
    foreign.evidence_mode = "native-recovery-ja"
    batches, native_calls = [], []
    translator = SemanticTranslator("ollama")

    def translate_batch(payload, context, source_context=None):
        identities = [row["id"] for row in payload]
        batches.append(identities)
        return {identity: {"final_vi": f"Tiếng Việt {identity}."} for identity in identities}

    translator._execute_3tier_translation = translate_batch
    session.translator = translator
    def native_review(rows, **_kwargs):
        native_calls.extend(row["id"] for row in rows)
        return {"segments": {rows[0]["id"]: {"final_vi": "Dịch riêng."}}}
    monkeypatch.setattr(module, "review_native_rows", native_review)

    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))

    assert batches == [[0, 2]]
    assert native_calls == [1]
    assert [row.final_vi for row in session.segments.values()] == [
        "Tiếng Việt 0.", "Dịch riêng.", "Tiếng Việt 2."]
    assert all(row.status == "READY" for row in session.segments.values())


def test_successful_draft_survives_speech_failure_without_retranslation(make_session):
    session = make_session("speech-failure", 2, 2)
    calls = []
    def provider(**kwargs):
        calls.append(kwargs["text_zh"])
        return {"final_vi": "Đã dịch."}
    session.translator = SimpleNamespace(translate_single_segment=provider)
    async def fail(row, **kwargs):
        row.status = "TTS"
        raise RuntimeError("speech unavailable")
    session._synthesize_segment = fail
    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))
    assert len(calls) == 2
    assert all(row.status == "FAILED" and row.failed_stage == "TTS" and row.final_vi == "Đã dịch." for row in session.segments.values())
    for row in session.segments.values():
        row.status = "WAITING"
    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))
    assert len(calls) == 2


def test_nonvisual_manual_revision_wins_while_provider_is_running(make_session):
    session = make_session("concurrent-edit", 1, 1)
    row = session.segments[0]
    def provider(**kwargs):
        row.revision += 1
        row.final_vi, row.verification = "Bản sửa.", {"status": "manual"}
        return {"final_vi": "Bản AI cũ."}
    session.translator = SimpleNamespace(translate_single_segment=provider)
    speech = AsyncMock()
    session._synthesize_segment = speech
    asyncio.run(translate_nonvisual_rows(session, [row]))
    assert row.final_vi == "Bản sửa." and row.verification == {"status": "manual"}
    speech.assert_not_awaited()


def test_pause_and_stop_prevent_new_nonvisual_provider_admission(make_session):
    session = make_session("pause-stop", 4, 2)
    calls = []
    session.translator = SimpleNamespace(translate_single_segment=lambda **kwargs:
        calls.append(kwargs["text_zh"]) or {"final_vi": "Dịch."})
    async def run():
        session.pause_event.clear()
        session.is_paused = True
        task = asyncio.create_task(translate_nonvisual_rows(session, session.segments.values()))
        await asyncio.sleep(.02)
        assert calls == []
        session.is_stopped = True
        session.pause_event.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert calls == []


def test_serial_source_and_speech_with_concurrent_translation(make_session):
    session = make_session("native-limits", 8, 4)
    active_asr = active_speech = 0
    peaks = {"asr": 0, "speech": 0}
    for row in session.segments.values():
        row.asr_pretranscribed = False
    async def recognize(row):
        nonlocal active_asr
        active_asr += 1
        peaks["asr"] = max(peaks["asr"], active_asr)
        await asyncio.sleep(.001)
        row.asr_pretranscribed = True
        active_asr -= 1
    async def speech(row, **kwargs):
        nonlocal active_speech
        active_speech += 1
        peaks["speech"] = max(peaks["speech"], active_speech)
        await asyncio.sleep(.001)
        row.status = "READY"
        active_speech -= 1
    session._recognize_nonvisual_segment = recognize
    session._synthesize_segment = speech
    session.translator = SimpleNamespace(translate_single_segment=lambda **kwargs: {"final_vi": "Dịch."})
    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))
    assert peaks == {"asr": 1, "speech": 1}


def test_nonvisual_transient_burst_retries_only_failed_row(make_session):
    from core.engines.translation.opencode_client import OpenCodeTimeoutError
    session = make_session("retry-one", 4, 4)
    calls = {}
    delays = []
    def provider(**kwargs):
        source = kwargs["text_zh"]
        calls[source] = calls.get(source, 0) + 1
        if source == "句子1" and calls[source] == 1:
            raise OpenCodeTimeoutError("timeout")
        return {"final_vi": "Dịch."}
    async def wait(label, attempt, delay):
        delays.append((label, attempt, delay))
        await asyncio.sleep(.01)
    session._wait_automatic_recovery = wait
    session.translator = SimpleNamespace(translate_single_segment=provider)
    asyncio.run(translate_nonvisual_rows(session, session.segments.values()))
    assert calls == {"句子0": 1, "句子1": 2, "句子2": 1, "句子3": 1}
    assert len(delays) == 1 and all(row.status == "READY" for row in session.segments.values())


def test_all_admitted_failures_release_slots_for_healthy_queued_rows(make_session):
    from core.engines.translation.opencode_client import OpenCodeTimeoutError
    session = make_session("retry-fairness", 6, 2)
    calls, healthy = {}, []
    async def run():
        release = asyncio.Event()
        def provider(**kwargs):
            source = kwargs["text_zh"]
            calls[source] = calls.get(source, 0) + 1
            if source in {"句子0", "句子1"} and calls[source] == 1:
                raise OpenCodeTimeoutError("timeout")
            return {"final_vi": "Dịch."}
        async def wait(*args):
            await asyncio.wait_for(release.wait(), timeout=2)
        async def speech(row, **kwargs):
            row.status = "READY"
            if row.id >= 2:
                healthy.append(row.id)
                if len(healthy) == 4:
                    release.set()
        session.translator = SimpleNamespace(translate_single_segment=provider)
        session._wait_automatic_recovery, session._synthesize_segment = wait, speech
        await translate_nonvisual_rows(session, session.segments.values())
    asyncio.run(run())
    assert sorted(healthy) == [2, 3, 4, 5]
    assert calls["句子0"] == calls["句子1"] == 2
    assert all(row.status == "READY" for row in session.segments.values())


def test_new_ordinary_draft_invalidates_old_verdict_and_keeps_source_proof(make_session):
    session = make_session("old-verdict", 1, 1)
    row = session.segments[0]
    row.verification = {"status": "verified", "semantic_verified": True,
        "source_supported": True, "audio_evidence": [{"text_zh": row.text_zh, "engine": "sensevoice"}],
        "pacing": {"text": "Old wording"}, "meaning_verified": True}
    session.translator = SimpleNamespace(translate_single_segment=lambda **kwargs: {"final_vi": "Dịch mới."})
    asyncio.run(translate_nonvisual_rows(session, [row]))
    assert row.verification["status"] == "pending" and row.verification["semantic_verified"] is False
    assert row.verification["source_supported"] is True and row.verification["audio_evidence"]
    assert "pacing" not in row.verification and "meaning_verified" not in row.verification
    assert row.needs_review


def test_tts_error_cannot_mark_replaced_row_failed(make_session):
    session = make_session("replaced-tts", 1, 1)
    row = session.segments[0]
    session.translator = SimpleNamespace(translate_single_segment=lambda **kwargs: {"final_vi": "Dịch."})
    async def speech(old, **kwargs):
        replacement = deepcopy(old)
        replacement.status, replacement.final_vi = "READY", "Bản mới."
        replacement.verification = {"status": "manual"}
        session.segments[old.id] = replacement
        raise RuntimeError("obsolete speech failed")
    session._synthesize_segment = speech
    asyncio.run(translate_nonvisual_rows(session, [row]))
    assert session.segments[0].status == "READY"
    assert session.segments[0].final_vi == "Bản mới."
    assert row.status != "FAILED"


def test_stop_joins_inflight_translation_workers_before_return(make_session):
    import time
    session = make_session("stop-drains", 4, 2)
    entered = threading.Barrier(3, timeout=2)
    finished = []
    def provider(**kwargs):
        context = current_execution_context()
        entered.wait()
        while not context.cancel_check():
            time.sleep(.005)
        finished.append(kwargs["text_zh"])
        raise asyncio.CancelledError
    session.translator = SimpleNamespace(translate_single_segment=provider)
    async def run():
        task = asyncio.create_task(translate_nonvisual_rows(session, session.segments.values()))
        await asyncio.to_thread(entered.wait)
        session.is_stopped = True
        session.is_running = False
        session.pause_event.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert len(finished) == 2
        assert not [other for other in asyncio.all_tasks() if other is not asyncio.current_task() and not other.done()]
    asyncio.run(run())

def test_selected_sensevoice_language_reaches_native_translation(make_session, monkeypatch):
    session = make_session('sensevoice-native', 1, 20)
    row = session.segments[0]
    row.asr_pretranscribed = False
    session.raw_audio_16k = session.cache_dir / 'raw.wav'
    session.asr_engine = SimpleNamespace(transcribe=lambda *args, **kwargs: [
        {'text_zh':'우리 친구잖아', 'source_language':'ko', 'source_language_probability':0.,
         'source_language_uncertain':True}])
    session._run_ffmpeg = AsyncMock()
    seen=[]
    def native(rows, **kwargs):
        seen.append(rows[0]['source_language'])
        return {'segments':{0:{**rows[0], 'final_vi':'Chúng ta là bạn mà.',
                'verification':{'status':'unresolved','source_supported':False}}}}
    monkeypatch.setattr('core.streaming.nonvisual_translation.review_native_rows', native)
    session.translator.translate_single_segment = lambda **kwargs: (_ for _ in ()).throw(AssertionError('Chinese route'))
    asyncio.run(session._worker_loop())
    assert seen==['ko'] and row.final_vi=='Chúng ta là bạn mà.'
    assert row.source_language_uncertain is True
