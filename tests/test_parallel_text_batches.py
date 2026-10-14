"""Offline scheduling contracts; these tests do not claim provider throughput."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.runtime_context import execution_context, current_execution_context
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError, _parallel_text_map, _TEXT_POOLS


def test_shared_video_pool_bounds_all_chunk_owners_and_propagates_context():
    lock, barrier = threading.Lock(), threading.Barrier(2)
    active = peak = 0
    def operation(index):
        nonlocal active, peak
        assert current_execution_context().run_id == "parallel-fixture"
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.025)
        with lock:
            active -= 1
        return index
    def owner():
        with execution_context("parallel-fixture"):
            barrier.wait(timeout=2)
            return _parallel_text_map("parallel-fixture", 3, operation, 9, lambda: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(owner) for _ in range(2)]
        assert [future.result() for future in futures] == [list(range(9))] * 2
    assert peak == 3
    assert "parallel-fixture" not in _TEXT_POOLS


@pytest.mark.parametrize("limit", [20, 30, 40, 50])
def test_text_workers_reach_selected_limit_without_hidden_lower_cap(limit):
    # Controlled workload, not a claim that Muse accepts this many requests.
    barrier = threading.Barrier(limit)
    run_id = f"text-limit-{limit}"
    seen = set()
    guard = threading.Lock()
    def operation(index):
        assert current_execution_context().run_id == run_id
        with guard:
            seen.add(threading.get_ident())
        barrier.wait(timeout=10)
        return index
    with execution_context(run_id):
        result = _parallel_text_map(run_id, limit, operation, limit, lambda: None)
    assert result == list(range(limit)) and len(seen) == limit
    assert run_id not in _TEXT_POOLS


@pytest.fixture
def translator(monkeypatch):
    engine = object.__new__(VideoIntelligence)
    engine.provider = "opencode"
    engine.OPENCODE_SPEECH_BATCH_SIZE = 8
    engine._checkpoint_context = None
    engine._source_dialogue = []
    engine._video_duration = 30
    engine.glossary = {"terms": [{"src": "小王", "tgt": "Tiểu Vương", "note": "supplied"}]}
    monkeypatch.setattr(engine, "_text_workers", lambda: 3)
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient",
        lambda **kwargs: SimpleNamespace(has_credentials=True, model="fixture-model"))
    monkeypatch.setattr(engine, "_review_primary_text", lambda result, *args: result)
    payload = [{"id": index, "start": float(index), "end": float(index + 1),
                "asr_text": "原文", "text_zh": "原文"} for index in range(24)]
    engine._source_dialogue = deepcopy(payload)
    contexts, prompts, saved = {}, [], {}
    lock, barrier = threading.Lock(), threading.Barrier(3)
    def correct(client, rows, observed, context, cancel):
        contexts[rows[0]["id"]] = json.loads(context)
        barrier.wait(timeout=3)
        # Force completion order away from source order.
        time.sleep((24 - rows[0]["id"]) / 1000)
        return {row["id"]: {"text_zh": f"修正{row['id']}", "needs_review": False,
                "source_supported": True, "review_reason": "", "evidence_ids": []} for row in rows}
    def request(client, prompt, rows, screens, start, end, cancel):
        with lock:
            prompts.append(prompt)
        result = {"segments": {row["id"]: {"id": row["id"], "start": row["start"],
            "end": row["end"], "text_zh": f"修正{row['id']}", "final_vi": "Câu dịch",
            "literal_vi": "Câu dịch", "natural_vi": "Câu dịch",
            "needs_review": False, "review_reason": ""} for row in rows},
            "screen_texts": [], "summary": f"batch-{rows[0]['id']}"}
        return json.dumps(result), result
    def checkpoint(stage, result, cancel=None):
        engine._check_cancelled(cancel)
        saved[stage["index"]] = deepcopy(result)
    monkeypatch.setattr(engine, "_correct_source", correct)
    monkeypatch.setattr(engine, "_request_text_result", request)
    monkeypatch.setattr(engine, "_write_checkpoint", checkpoint)
    return engine, payload, contexts, prompts, saved


def test_real_batch_path_frozen_context_deterministic_merge_and_checkpoints(translator):
    engine, payload, contexts, prompts, saved = translator
    original = deepcopy((payload, engine._source_dialogue, engine.glossary))
    with execution_context("frozen-fixture"):
        result = engine._translate_text(payload, [], "prior source", 0, 24)
    assert list(result["segments"]) == list(range(24))
    assert result["summary"] == "batch-16"
    assert set(saved) == {0, 1, 2}
    assert len(prompts) == 6  # Translation and independent verification per owner.
    assert all("Ngữ cảnh trước: prior source" in prompt for prompt in prompts)
    assert len({context["terminology_context"]["version"] for context in contexts.values()}) == 1
    assert (payload, engine._source_dialogue, engine.glossary) == original
    assert "frozen-fixture" not in _TEXT_POOLS


def test_one_failed_batch_keeps_successful_sibling_checkpoints(translator, monkeypatch):
    engine, payload, contexts, prompts, saved = translator
    original = engine._request_text_result
    def request(client, prompt, rows, *args):
        if rows[0]["id"] == 8:
            raise TimeoutError("injected provider timeout")
        return original(client, prompt, rows, *args)
    monkeypatch.setattr(engine, "_request_text_result", request)
    with execution_context("failure-fixture"), pytest.raises(TimeoutError):
        engine._translate_text(payload, [], "", 0, 24)
    assert set(saved) == {0, 2}
    assert "failure-fixture" not in _TEXT_POOLS
    monkeypatch.setattr(engine, "_read_checkpoint", lambda stage: saved.get(stage["index"]))
    monkeypatch.setattr(engine, "_request_text_result", original)
    retry_ids = []
    def correct_retry(client, rows, *args):
        retry_ids.extend(row["id"] for row in rows)
        return {row["id"]: {"text_zh": f"修正{row['id']}", "needs_review": False,
                "source_supported": True, "review_reason": "", "evidence_ids": []} for row in rows}
    monkeypatch.setattr(engine, "_correct_source", correct_retry)
    with execution_context("failure-fixture"):
        result = engine._translate_text(payload, [], "", 0, 24)
    assert retry_ids == list(range(8, 16))
    assert list(result["segments"]) == list(range(24))


def test_cancelled_parallel_batches_do_not_checkpoint_or_publish(translator, monkeypatch):
    engine, payload, contexts, prompts, saved = translator
    cancelled = threading.Event()
    original = engine._request_text_result
    def request(*args):
        result = original(*args)
        cancelled.set()
        return result
    monkeypatch.setattr(engine, "_request_text_result", request)
    with execution_context("cancel-fixture", cancelled.is_set), pytest.raises(VideoIntelligenceError, match="hủy"):
        engine._translate_text(payload, [], "", 0, 24)
    assert saved == {}
    assert "cancel-fixture" not in _TEXT_POOLS


def test_selected_limit_comes_from_same_provider_run_admission(monkeypatch):
    engine = object.__new__(VideoIntelligence)
    engine.provider = "opencode"
    layer = SimpleNamespace(snapshot=Mock(return_value={"selected_concurrency": 20}))
    monkeypatch.setattr("core.engines.translation.opencode_client._shared_execution_layer", lambda: layer)
    assert engine._text_workers() == 1
    with execution_context("video-selected-20"):
        assert engine._text_workers() == 20
    layer.snapshot.assert_called_once_with("video-selected-20")
