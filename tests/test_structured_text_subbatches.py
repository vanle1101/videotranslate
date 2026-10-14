"""Offline schema-fault recovery with real atomic checkpoint files."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from config import settings
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


@pytest.fixture
def translator(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    value = object.__new__(VideoIntelligence)
    value.provider = "opencode"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source identity fixture, not video QA")
    stat = source.stat()
    value._checkpoint_context = {"key": "fixture", "directory": tmp_path / "checkpoints",
        "source": {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}}
    return value


def targets(count=2):
    return [{"id": i, "start": float(i), "end": float(i + 1), "asr_text": "你好"} for i in range(count)]


def result(rows):
    return {"segments": [{**row, "text_zh": "你好", "literal_vi": "Chào.", "natural_vi": "Chào.",
        "final_vi": "Chào.", "needs_review": False, "review_reason": ""} for row in reversed(rows)],
        "screen_texts": [], "summary": ""}


def scoped_targets(prompt, original):
    if "PHẠM VI LÔ CON DUY NHẤT" in prompt:
        return json.loads(prompt.split("\n")[-1])["target_segments"]
    return original


def test_two_repairs_then_split_resume_keeps_successful_sibling(translator, monkeypatch):
    rows = targets()
    calls, fail_right = [], [True]

    def request(client, prompt, validator, **kwargs):
        selected = scoped_targets(prompt.split("\nSỬA ĐỊNH DẠNG JSON:")[0], rows)
        ids = [r["id"] for r in selected]
        calls.append(ids)
        raw = "malformed" if len(ids) > 1 or ids == [1] and fail_right[0] else json.dumps(result(selected))
        try:
            return raw, validator(raw)
        except VideoIntelligenceError as exc:
            exc.raw_response = raw
            raise

    monkeypatch.setattr("core.video_intelligence.request_structured", request)
    client = SimpleNamespace(model="test")
    with pytest.raises(VideoIntelligenceError):
        translator._request_text_result(client, "Original instructions", rows, [], 0., 2.)
    assert calls == [[0, 1]] * 3 + [[0]] + [[1]] * 3
    fail_right[0] = False
    calls.clear()
    raw, checked = translator._request_text_result(client, "Original instructions", rows, [], 0., 2.)
    assert calls == [[1]]
    assert set(checked["segments"]) == {0, 1}
    assert [(r["start"], r["end"]) for r in json.loads(raw)["segments"]] == [(0., 1.), (1., 2.)]
    calls.clear()
    translator._request_text_result(client, "Original instructions", rows, [], 0., 2.)
    assert calls == []
    translator._request_text_result(client, "Changed source instructions", rows, [], 0., 2.)
    assert calls[:3] == [[0, 1]] * 3


@pytest.mark.parametrize("failure", [ConnectionError("offline"), asyncio.CancelledError()])
def test_transport_or_cancel_does_not_split_or_cache(translator, monkeypatch, failure):
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        raise failure
    monkeypatch.setattr("core.video_intelligence.request_structured", request)
    with pytest.raises(type(failure)):
        translator._request_text_result(SimpleNamespace(model="test"), "prompt", targets(), [], 0, 2)
    assert len(calls) == 1
    assert not translator._checkpoint_context["directory"].exists()


def test_bad_ids_and_timestamps_never_publish_singletons(translator, monkeypatch):
    rows = targets(1)
    calls = []
    def request(client, prompt, validator, **kwargs):
        calls.append(1)
        broken = result(rows)
        broken["segments"][0]["start"] = 20
        return json.dumps(broken), validator(broken)
    monkeypatch.setattr("core.video_intelligence.request_structured", request)
    with pytest.raises(VideoIntelligenceError):
        translator._request_text_result(SimpleNamespace(model="test"), "prompt", rows, [], 0, 1)
    assert len(calls) == 3
    assert not translator._checkpoint_context["directory"].exists()


def test_readonly_neighbors_are_three_each_side_and_never_targets():
    rows = targets(12)
    context = VideoIntelligence._batch_readonly_context(list(reversed(rows)), rows[4:6])
    assert [r["id"] for r in context] == [1, 2, 3, 6, 7, 8]
    context[0]["asr_text"] = "changed copy"
    assert rows[1]["asr_text"] == "你好"


def test_ignored_screen_roundtrips_checkpoint(translator, monkeypatch):
    screen = {"id": "o0", "start": 0., "end": 1., "bbox": [.1, .1, .1, .1],
              "text_zh": "你好", "confidence": .99}
    calls = []
    def request(client, prompt, validator, **kwargs):
        calls.append(1)
        raw = json.dumps({"segments": [], "screen_texts": [{"id": "o0", "kind": "ignore",
            "text_vi": "", "needs_review": False, "review_reason": ""}], "summary": ""})
        return raw, validator(raw)
    monkeypatch.setattr("core.video_intelligence.request_structured", request)
    for _ in range(2):
        _, checked = translator._request_text_result(SimpleNamespace(model="test"), "prompt", [], [screen], 0, 1)
        assert checked["screen_texts"] == []
    assert calls == [1]


def test_parent_uncertainty_survives_split_and_reopen(translator, monkeypatch):
    rows = targets()
    def request(client, prompt, validator, **kwargs):
        selected = scoped_targets(prompt.split("\nSỬA ĐỊNH DẠNG JSON:")[0], rows)
        data = result(selected)
        if len(selected) > 1:
            data["segments"][0].update(needs_review=True, review_reason="Uncertain parent source.")
            data["summary"] = None
        raw = json.dumps(data)
        try:
            return raw, validator(raw)
        except VideoIntelligenceError as exc:
            exc.raw_response = raw
            raise
    monkeypatch.setattr("core.video_intelligence.request_structured", request)
    for _ in range(2):
        _, checked = translator._request_text_result(SimpleNamespace(model="test"), "prompt", rows, [], 0, 2)
        assert checked["segments"][1]["needs_review"] is True
        assert checked["segments"][1]["review_reason"] == "Uncertain parent source."
