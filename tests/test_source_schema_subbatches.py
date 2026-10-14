"""Source-correction repair, isolation and durable sibling reuse, offline only."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import settings
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    processor = object.__new__(VideoIntelligence)
    processor.provider = "opencode"
    source = tmp_path / "identity.txt"
    source.write_text("Offline source identity")
    stat = source.stat()
    processor._checkpoint_context = {"key": "fixture", "directory": tmp_path / "checkpoints",
        "source": {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}}
    return processor


def targets(count=2):
    return [{"id": i, "start": float(i), "end": i + 1., "asr_text": "你好"} for i in range(count)]


def selected(prompt):
    return json.loads(next(line[len("Requested ASR: "):] for line in prompt.splitlines()
                           if line.startswith("Requested ASR: ")))


def answer(rows):
    return {"segments": [{"id": row["id"], "text_zh": row["asr_text"],
        "needs_review": False, "review_reason": "", "evidence_ids": []} for row in reversed(rows)]}


def test_two_repairs_recursive_split_and_only_missing_sibling_after_reopen(engine):
    calls, fail_right = [], [True]
    def translate(prompt, **kwargs):
        rows = selected(prompt)
        ids = [row["id"] for row in rows]
        calls.append(ids)
        return "bad JSON" if len(rows) > 1 or ids == [3] and fail_right[0] else json.dumps(answer(rows))
    client = SimpleNamespace(model="fixture", translate=translate)
    with pytest.raises(VideoIntelligenceError) as failure:
        engine._correct_source_checked(client, targets(4), [], "frozen context")
    assert set(failure.value.partial_corrections) == {0, 1, 2}
    assert calls == [[0, 1, 2, 3]] * 3 + [[0, 1]] * 3 + [[0]] + [[1]] + [[2, 3]] * 3 + [[2]] + [[3]] * 3
    reopened = object.__new__(VideoIntelligence)
    reopened.provider, reopened._checkpoint_context = engine.provider, engine._checkpoint_context
    calls.clear()
    fail_right[0] = False
    result = reopened._correct_source_checked(client, targets(4), [], "frozen context")
    assert calls == [[3]] and list(result) == [0, 1, 2, 3]
    calls.clear()
    reopened._correct_source_checked(client, targets(4), [], "frozen context")
    assert calls == []
    reopened._correct_source_checked(client, targets(4), [], "changed context")
    assert calls[:3] == [[0, 1, 2, 3]] * 3


def test_unrecoverable_singleton_does_not_replace_healthy_sibling_with_fallback(engine):
    calls = []
    def translate(prompt, **kwargs):
        rows = selected(prompt)
        calls.append([r["id"] for r in rows])
        return "bad JSON" if len(rows) > 1 or rows[0]["id"] == 0 else json.dumps(answer(rows))
    result = engine._correct_source(SimpleNamespace(model="fixture", translate=translate), targets(), [], "context")
    assert result[0]["needs_review"] and result[0]["text_zh"] == "你好"
    assert not result[1]["needs_review"] and result[1]["source_supported"]
    assert calls == [[0, 1]] * 3 + [[0]] * 3 + [[1]]


@pytest.mark.parametrize("failure", [ConnectionError("offline"), TimeoutError("timeout"), asyncio.CancelledError()])
def test_transport_and_cancellation_do_not_amplify_attempts(engine, failure):
    client = SimpleNamespace(model="fixture", translate=Mock(side_effect=failure))
    with pytest.raises(type(failure)):
        engine._correct_source(client, targets(), [], "context")
    assert client.translate.call_count == 1
    assert not engine._checkpoint_context["directory"].exists()


def test_parent_uncertainty_survives_repair_split_and_checkpoint(engine):
    def translate(prompt, **kwargs):
        rows = selected(prompt)
        raw = answer(rows)
        if len(rows) > 1:
            raw["segments"][0].update(needs_review=True, review_reason="Nguồn chưa rõ")
            raw["segments"][1]["text_zh"] = None
        return json.dumps(raw)
    client = SimpleNamespace(model="fixture", translate=translate)
    for _ in range(2):
        result = engine._correct_source_checked(client, targets(), [], "context")
        assert result[1]["needs_review"] and result[1]["review_reason"] == "Nguồn chưa rõ"
        assert not result[1]["source_supported"]


def test_wrong_singleton_id_never_accepted_and_global_cap_can_lower_repairs(engine, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 1)
    raw = answer(targets(1))
    raw["segments"][0]["id"] = 99
    client = SimpleNamespace(model="fixture", translate=Mock(return_value=json.dumps(raw)))
    with pytest.raises(VideoIntelligenceError):
        engine._correct_source_checked(client, targets(1), [], "context")
    assert client.translate.call_count == 2
    assert not engine._checkpoint_context["directory"].exists()


def test_cancel_after_request_prevents_checkpoint_and_further_splitting(engine):
    cancelled = [False]
    def translate(prompt, **kwargs):
        cancelled[0] = True
        return json.dumps(answer(selected(prompt)))
    client = SimpleNamespace(model="fixture", translate=Mock(side_effect=translate))
    with pytest.raises(VideoIntelligenceError, match="hủy"):
        engine._correct_source_checked(client, targets(), [], "context", lambda: cancelled[0])
    assert client.translate.call_count == 1
    assert not engine._checkpoint_context["directory"].exists()
