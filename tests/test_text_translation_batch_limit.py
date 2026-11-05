"""Bound real text request targets independently of provider and context size."""
from copy import deepcopy
import json

import pytest

from config import settings
from core.video_intelligence import VideoIntelligence


class TextProvider:
    has_credentials = True
    model = "offline-test"

    def __init__(self):
        self.calls = []

    def translate(self, prompt, **kwargs):
        source_pass = "Requested ASR: " in prompt
        marker = "Requested ASR: " if source_pass else "Mốc câu cần xuất: "
        targets = json.loads(prompt.split(marker, 1)[1].split("\n", 1)[0])
        context_marker = "Context: " if source_pass else "Toàn bộ ngữ cảnh đoạn (chỉ tham khảo): "
        context = json.loads(prompt.split(context_marker, 1)[1].split("\n", 1)[0])
        kind = "source" if source_pass else "verify" if "KIỂM TRA BẢN DỊCH RIÊNG BIỆT" in prompt else "translate"
        self.calls.append((kind, deepcopy(targets), context))
        # Deliberately return IDs out of order: merging must use stable IDs,
        # including non-contiguous IDs, rather than response/list position.
        if source_pass:
            return json.dumps({"segments": [
                {"id": row["id"], "text_zh": row["asr_text"], "evidence_ids": [],
                 "needs_review": True, "review_reason": "Nguồn cần kiểm tra"}
                for row in reversed(targets)]})
        return json.dumps({"segments": [
            {"id": row["id"], "start": row["start"], "end": row["end"],
             "text_zh": row["corrected_text_zh"], "literal_vi": "Anh đến rồi",
             "natural_vi": "Anh đến rồi", "final_vi": "Anh đến rồi",
             "needs_review": False, "review_reason": ""}
            for row in reversed(targets)], "screen_texts": [], "summary": ""})


@pytest.mark.parametrize("provider,quota_fallback,limit", [
    ("gemini", True, 8), ("openrouter-free", False, 8), ("opencode", False, 4),
])
def test_text_batches_bound_targets_preserve_context_and_resume(
        tmp_path, monkeypatch, provider, quota_fallback, limit):
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    fake = TextProvider()
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", lambda **kwargs: fake)
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient", lambda **kwargs: fake)
    processor = VideoIntelligence(client=fake)
    processor.provider = provider
    dialogue = [{"id": 100 + 7 * i, "start": i * 1.375, "end": i * 1.375 + .875,
                 "asr_text": "你来了"} for i in range(31)]
    payload = deepcopy(dialogue[5:24])
    original = deepcopy(payload)
    processor._source_dialogue = deepcopy(dialogue)
    # Real checkpoint serialization/validation, isolated from user projects.
    source = tmp_path / "fixture-source"
    source.write_bytes(b"fixture-identity-only")
    stat = source.stat()
    processor._checkpoint_context = {
        "key": "test", "directory": tmp_path / "checkpoints",
        "source": {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}}

    result = processor._translate_text(payload, [], "", 0, 50, quota_fallback=quota_fallback)

    source_calls = [call for call in fake.calls if call[0] == "source"]
    assert [len(call[1]) for call in source_calls] == ([8, 8, 3] if limit == 8 else [4, 4, 4, 4, 3])
    assert [row["id"] for _, targets, _ in source_calls for row in targets] == [row["id"] for row in payload]
    for index, (_, targets, _) in enumerate(source_calls):
        expected = [row["id"] for row in targets]
        group = fake.calls[index * 3:index * 3 + 3]
        assert [kind for kind, _, _ in group] == ["source", "translate", "verify"]
        first = 5 + index * limit
        neighbors = dialogue[first - 3:first + len(targets) + 3]
        for _, requested, context in group:
            assert [row["id"] for row in requested] == expected
            assert len(requested) <= limit
            assert [row["id"] for row in context["wider_source_dialogue"]] == [row["id"] for row in neighbors]
    assert set(result["segments"]) == {row["id"] for row in payload}
    for row in payload:
        translated = result["segments"][row["id"]]
        assert (translated["start"], translated["end"]) == (row["start"], row["end"])
        assert translated["needs_review"] is True
    assert payload == original
    # Restarting uses successful batches without repeating translation/review.
    count = len(fake.calls)
    processor._source_dialogue = deepcopy(dialogue)
    restored = processor._translate_text(payload, [], "", 0, 50, quota_fallback=quota_fallback)
    assert len(fake.calls) == count
    assert restored == result
