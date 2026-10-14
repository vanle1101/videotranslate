"""Cross-language subtitles cannot become independently verified speech."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.source_language import source_ocr_script_conflict
from core.translation_review import AutomaticTranslationReviewer


def source(text="오늘 날씨가 정말 좋아요"):
    return {"id": 0, "start": 0., "end": 3., "asr_text": text, "text_zh": text,
        "literal_vi": "Hôm nay trời đẹp.", "natural_vi": "Hôm nay trời đẹp.",
        "final_vi": "Hôm nay trời đẹp.", "needs_review": True, "review_reason": "Chưa rõ nguồn."}


def answer(row, text):
    return {"segments": [{**row, "text_zh": text, "needs_review": False,
        "semantic_verified": True, "source_evidence_ids": ["review0"],
        "verification_reason": "Model tự tin OCR khớp."}], "screen_texts": [], "summary": ""}


def screen(text):
    return {"id": "subtitle", "start": 0., "end": 3., "text_zh": text,
        "confidence": .99, "bbox": [.1, .7, .7, .1], "kind": "subtitle"}


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")


@pytest.mark.parametrize("language", ["ko", "auto", "zh"])
@pytest.mark.parametrize("text", ["오늘 날씨가 정말 좋아요", "오늘 天气 很好"])
def test_chinese_subtitle_cannot_replace_korean_or_mixed_speech(language, text):
    row = source(text)
    chinese = "今天天气真的很好"
    client = Mock(has_credentials=True, model="muse")
    client.translate.return_value = answer(row, chinese)
    scanner = Mock()
    scanner.extract.return_value = [screen(chinese)]
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False, source_language=language)
    result = reviewer.review("unused", [row], [screen(chinese)])["segments"][0]
    assert result["text_zh"] == text
    assert result["asr_text"] == text
    assert result["needs_review"] is True
    assert result["verification"]["source_supported"] is False
    assert result["verification"]["source_accepted"] is False
    assert result["verification"]["semantic_verified"] is False
    assert result["verification"]["evidence"] == []


def test_same_language_korean_ocr_can_still_corroborate_measured_source():
    row = source()
    client = Mock(has_credentials=True, model="muse")
    client.translate.return_value = answer(row, row["text_zh"])
    scanner = Mock()
    scanner.extract.return_value = [screen(row["text_zh"])]
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False, source_language="ko")
    result = reviewer.review("unused", [row], [screen(row["text_zh"])])["segments"][0]
    assert result["text_zh"] == row["text_zh"]
    assert result["verification"]["source_supported"] is True


def test_review_restores_contaminated_source_before_first_request_without_mutating_input():
    row = source()
    measured = row["asr_text"]
    row.update(text_zh="今天天气真的很好", needs_review=False,
        verification={"status": "verified", "source_supported": True,
            "semantic_verified": True, "address_verified": True, "evidence_mode": "fresh-ocr-text-review"})
    original = deepcopy(row)
    client = Mock(has_credentials=True, model="muse")
    client.translate.return_value = answer(row, row["text_zh"])
    scanner = Mock()
    scanner.extract.return_value = [screen(row["text_zh"])]
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False, source_language="auto")
    fixed = reviewer.review("unused", [row], [screen(row["text_zh"])])["segments"][0]
    assert row == original
    assert fixed["text_zh"] == measured
    assert fixed["needs_review"] is True
    assert fixed["verification"]["semantic_verified"] is False
    assert '"text_zh": "' + measured + '"' in client.translate.call_args_list[0].args[0]


def test_manual_source_is_not_reset_or_sent_for_automatic_rewrite():
    row = source()
    row.update(text_zh="用户确认", verification={"status": "manual"})
    client = Mock(has_credentials=True)
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio_evidence=False, source_language="ko")
    assert reviewer.review("unused", [row], [])["segments"][0] == row
    client.translate.assert_not_called()


@pytest.mark.parametrize("real_audio_proof", [False, True])
def test_restore_respects_actual_independent_audio_proof_not_just_a_label(real_audio_proof):
    row = source()
    row["text_zh"] = "早上好"
    row["verification"] = {"status": "verified", "evidence_mode": "dual-local-asr-text-review",
        "source_supported": True, "audio_consensus": True, "semantic_verified": True,
        "audio_evidence": ([{"engine": engine, "text_zh": row["text_zh"]}
            for engine in ("sensevoice", "faster-whisper-small")] if real_audio_proof else [])}
    reviewer = AutomaticTranslationReviewer(Mock(), Mock(), audio_evidence=False, source_language="auto")
    restored = reviewer._restore_measured_source(row)
    assert restored["text_zh"] == (row["text_zh"] if real_audio_proof else row["asr_text"])
    assert restored["verification"]["semantic_verified"] is real_audio_proof


@pytest.mark.parametrize("language,label", [("ko", "tiếng Hàn"), ("ja", "tiếng Nhật"),
    ("en", "tiếng Anh"), ("zh", "tiếng Trung"), ("auto", "trộn nhiều ngôn ngữ")])
def test_prompt_labels_actual_source_and_explains_legacy_field(language, label):
    prompt = AutomaticTranslationReviewer._prompt([source()], [], [], language)
    assert label in prompt and "source_language=" + language in prompt
    assert "text_zh là tên trường cũ" in prompt
    assert "không thay nguồn Hangul bằng chữ Hán" in prompt
    assert "bản dịch Trung-Việt" not in prompt


def test_audio_review_uses_selected_language_and_stage_identity():
    row = source()
    row["verification"] = {"status": "unresolved", "source_supported": False}
    client = Mock(has_credentials=True, model="muse")
    client.translate.return_value = answer(row, row["text_zh"])
    audio = Mock()
    audio.collect.return_value = {0: {"sensevoice": row["text_zh"], "faster-whisper-small": row["text_zh"]}}
    checkpoint = Mock()
    checkpoint.load.return_value = None
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio_evidence=audio, source_language="ko")
    result = reviewer.resolve_audio_uncertainty("unused", {"segments": {0: row}, "translation_sources": []}, checkpoint=checkpoint)
    assert result["segments"][0]["text_zh"] == row["text_zh"]
    assert client.translate.call_count == 2
    for call in client.translate.call_args_list:
        assert "source_language=ko" in call.args[0]
        assert "bản dịch Trung-Việt" not in call.args[0]
    stages = [call.args[0] for call in checkpoint.load.call_args_list]
    assert stages[0]["inputs"]["source_language"] == "ko"


@pytest.mark.parametrize("measured,candidate,conflict", [
    ("안녕하세요", "你好", True), ("こんにちは", "你好", True),
    ("Good morning", "早上好", True), ("오늘 天气", "天气", True),
    ("오늘 天气", "오늘 天气很好", False), ("大家好", "大家好", False),
    ("Good morning", "Buenos dias", False),  # Same script is NOT language proof.
])
def test_script_conflict_is_conservative_rejection_only(measured, candidate, conflict):
    assert source_ocr_script_conflict(measured, candidate, "auto") is conflict


def test_checkpoint_namespaces_separate_language_configurations(tmp_path):
    video = tmp_path / "source.bin"
    video.write_bytes(b"identity-only fixture")
    first = AutomaticTranslationReviewer(Mock(), Mock(), False, checkpoint_dir=tmp_path, source_language="ko")
    second = AutomaticTranslationReviewer(Mock(), Mock(), False, checkpoint_dir=tmp_path, source_language="auto")
    a = first._checkpoint(video, "muse", lambda: None, False)
    b = second._checkpoint(video, "muse", lambda: None, False)
    a.store({"stage": "fixture"}, {"result": "one"})
    assert a.load({"stage": "fixture"}) == {"result": "one"}
    assert b.load({"stage": "fixture"}) is None
