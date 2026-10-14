"""Production batch/checkpoint paths with offline controlled provider responses."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from core.video_intelligence import VideoIntelligence, VideoIntelligenceError
from core.engines.translation.opencode_client import OpenCodeClientError
from test_visual_resume import source, result_for


def configured(monkeypatch, *, workers=2):
    owner = VideoIntelligence()
    monkeypatch.setattr(owner, "_text_workers", lambda: workers)
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient",
        lambda **kwargs: SimpleNamespace(has_credentials=True, model="test-model"))
    monkeypatch.setattr(owner, "_correct_source", lambda client, payload, *args:
        {row["id"]:{"text_zh":row["asr_text"],"evidence_ids":[],
                    "needs_review":True,"review_reason":"Source uncertain"} for row in payload})
    screens = [{"id":f"o{i}","start":0.,"end":1.,"text_zh":f"标题{i}",
                "bbox":[.1,.1,.5,.1],"confidence":.95} for i in range(9)]
    monkeypatch.setattr(owner.screen_ocr, "extract", Mock(return_value=screens))
    return owner


def response(payload):
    rows = [SimpleNamespace(id=row["id"],start=row["start"],end=row["end"],text_zh=row["asr_text"])
            for row in payload]
    return "validated fixture", result_for(rows)


@pytest.mark.parametrize("workers", [1, 2])
def test_successful_speech_visible_before_failing_ocr_and_cached_restart(source, monkeypatch, workers):
    owner = configured(monkeypatch, workers=workers)
    rows = [SimpleNamespace(id=0,start=0.,end=1.,text_zh="你好")]
    published, complete, requests = [], [], []
    speech_ready = threading.Event()
    def request(client, prompt, payload, observed, *args):
        requests.append([row["id"] for row in payload])
        if not payload:
            assert speech_ready.wait(3)
            raise OpenCodeClientError("Terminal OCR sibling failure")
        return response(payload)
    monkeypatch.setattr(owner, "_request_text_result", request)
    def partial(result, lo, hi):
        assert list(source.parent.glob("cache/visual_checkpoints/*/*.json"))
        published.append(deepcopy(result))
        assert result["segments"][0]["needs_review"]
        assert result["segments"][0]["translation_provider"] == "opencode"
        assert result["screen_texts"] == []
        result["segments"][0]["final_vi"] = "Callback mutation must not reach cache"
        speech_ready.set()
    for attempt in range(2):
        speech_ready.clear()
        with pytest.raises(OpenCodeClientError, match="sibling"):
            owner.prepass(source, rows, 2, partial_callback=partial,
                          chunk_callback=lambda *args: complete.append(args))
        assert owner._partial_callback is None
    assert len(published) == 2 and not complete
    assert requests.count([0]) == 2  # First draft+verification only; restart restores batch.
    assert all(item["segments"][0]["final_vi"] == "Xin chào." for item in published)


def test_later_ready_speech_does_not_wait_for_first_slow_future(source, monkeypatch):
    owner = configured(monkeypatch)
    monkeypatch.setattr(owner.screen_ocr, "extract", Mock(return_value=[]))
    rows = [SimpleNamespace(id=i,start=float(i),end=i+.8,text_zh="你好") for i in range(8)]
    later_published = threading.Event()
    events = []
    def request(client,prompt,payload,*args):
        if payload[0]["id"] == 0:
            assert later_published.wait(3)
        return response(payload)
    monkeypatch.setattr(owner,"_request_text_result",request)
    def partial(result,*args):
        events.append(set(result["segments"]))
        if 4 in result["segments"]:
            later_published.set()
    final = owner.prepass(source,rows,8,partial_callback=partial)
    assert events == [{4,5,6,7},{0,1,2,3}]
    assert set(final["segments"]) == set(range(8))


def test_cancelled_batch_never_publishes_and_callback_state_restores(source, monkeypatch):
    owner = configured(monkeypatch,workers=1)
    rows = [SimpleNamespace(id=0,start=0.,end=1.,text_zh="你好")]
    cancelled = [False]
    count = [0]
    def request(client,prompt,payload,*args):
        count[0] += 1
        if count[0] == 2:
            cancelled[0] = True
        return response(payload)
    monkeypatch.setattr(owner,"_request_text_result",request)
    previous = Mock()
    owner._partial_callback = previous
    callback = Mock()
    with pytest.raises(VideoIntelligenceError):
        owner.prepass(source,rows,2,cancel_check=lambda:cancelled[0],partial_callback=callback)
    callback.assert_not_called()
    assert owner._partial_callback is previous
