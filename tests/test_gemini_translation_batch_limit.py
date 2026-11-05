"""Gemini media requests keep bounded targets and durable completed prefixes."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from config import settings
from core.engines.translation.gemini_client import GeminiError
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


class MediaProvider:
    model = "offline-gemini"

    def __init__(self):
        self.calls = []
        self.fail_id = None

    def analyze_media(self, media, prompt, **kwargs):
        verify = "KIỂM TRA ĐỘ CHÍNH XÁC" in prompt
        marker = "Mốc câu cố định: " if verify else "Các câu của đoạn này: "
        rows = json.loads(prompt.split(marker, 1)[1].split("\n", 1)[0])
        neighbors = json.loads(prompt.split(
            "Thoại nguồn rộng hơn, chỉ tham khảo ngữ cảnh, có thể còn lỗi ASR: ", 1)[1].split("\n", 1)[0])
        self.calls.append((verify, rows, neighbors))
        if rows and rows[0]["id"] == self.fail_id:
            raise GeminiError("offline interruption")
        return json.dumps({"segments": [
            {"id": row["id"], "start": row["start"], "end": row["end"],
             "text_zh": row["asr_text"], "literal_vi": "Xin chào",
             "natural_vi": "Xin chào", "final_vi": "Xin chào",
             "needs_review": True, "review_reason": "Cần kiểm tra"}
            for row in reversed(rows)], "screen_texts": [], "summary": "Ngữ cảnh trước"})


@pytest.fixture
def media_case(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-only")
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    path = tmp_path / "source.mp4"
    path.write_bytes(b"isolated identity; no actual media decoding")
    client = MediaProvider()
    processor = VideoIntelligence(client=client)
    monkeypatch.setattr(processor.screen_ocr, "extract", lambda *args, **kwargs: [])
    monkeypatch.setattr(processor, "_encode_chunk", lambda *args, **kwargs: b"offline clip")
    monkeypatch.setattr(processor, "_encode_contact_sheet", lambda *args, **kwargs: b"offline image")
    context = [SimpleNamespace(id=10 + i * 3, start=float(i), end=i + .6, text_zh="你好") for i in range(31)]
    return processor, client, path, context


@pytest.mark.parametrize("bounded", [False, True])
def test_gemini_requests_have_eight_targets_three_neighbors_and_original_timeline(media_case, bounded):
    processor, client, path, context = media_case
    targets = context[5:24]
    original = deepcopy([vars(row) for row in targets])
    options = {"end_time": 26} if bounded else {}
    result = processor.prepass(path, targets, total_duration=26, context_segments=context, **options)
    assert [len(rows) for verify, rows, _ in client.calls if not verify] == [8, 8, 3]
    assert len(client.calls) == 6
    for index, (verify, rows, neighbors) in enumerate(client.calls):
        offset = index // 2 * 8
        assert verify is bool(index % 2)
        assert [row["id"] for row in rows] == [row.id for row in targets[offset:offset + 8]]
        first = 5 + offset
        assert [row["id"] for row in neighbors] == [row.id for row in context[first - 3:first + len(rows) + 3]]
        assert len(rows) <= 8
    assert set(result["segments"]) == {row.id for row in targets}
    for row in targets:
        translated = result["segments"][row.id]
        assert (translated["start"], translated["end"]) == (row.start, row.end)
        assert translated["needs_review"] is True
    assert [vars(row) for row in targets] == original


def test_interrupted_gemini_resumes_after_eight_saved_rows_without_duplicate_requests(media_case):
    processor, client, path, context = media_case
    targets = context[5:24]
    client.fail_id = targets[8].id
    published = []
    def publish(result, start, end):
        published.append(set(result["segments"]))
    with pytest.raises(GeminiError, match="offline interruption"):
        processor.prepass(path, targets, total_duration=26, context_segments=context, chunk_callback=publish)
    assert published == [{row.id for row in targets[:8]}]
    client.fail_id = None
    client.calls.clear()
    result = processor.prepass(path, targets, total_duration=26, context_segments=context)
    assert [len(rows) for verify, rows, _ in client.calls if not verify] == [8, 3]
    assert all(row["id"] not in published[0] for _, rows, _ in client.calls for row in rows)
    assert set(result["segments"]) == {row.id for row in targets}


def test_direct_oversized_media_request_is_rejected_before_provider(media_case):
    processor, client, path, context = media_case
    with pytest.raises(VideoIntelligenceError, match="8 câu"):
        processor.analyze_chunk(path, 0, 10, context[:9])
    assert not client.calls


def test_sentence_overlap_at_batch_boundary_is_not_silently_dropped(media_case):
    processor, client, path, context = media_case
    context[7].end = context[8].start + .2
    with pytest.raises(VideoIntelligenceError, match="chồng lấn"):
        processor.prepass(path, context[:10], total_duration=26)
    assert not client.calls
