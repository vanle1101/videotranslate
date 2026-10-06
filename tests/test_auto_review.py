"""Source grounding and independent semantic review of playable drafts."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VideoIntelligenceError


def segment(**changes):
    return {"id": 0, "start": 1.0, "end": 3.0, "text_zh": "假的反而会限流",
            "literal_vi": "Sai. Ngược lại còn bị hạn chế lượt tiếp cận.",
            "natural_vi": "Sai. Ngược lại còn bị hạn chế lượt tiếp cận.",
            "final_vi": "Sai. Ngược lại còn bị hạn chế lượt tiếp cận.",
            "needs_review": True, "review_reason": "OCR thiếu câu nguồn", **changes}


def ocr(text="假的反而会限流", **changes):
    return {"id": "o0", "start": 1.0, "end": 2.8, "text_zh": text,
            "confidence": .97, "bbox": [.1, .6, .4, .07], **changes}


def answer(source=None, **changes):
    row = {**(source or segment()), "needs_review": False, "review_reason": "",
           "semantic_verified": True, "source_evidence_ids": ["review0"],
           "verification_reason": "Giữ phủ định và nghĩa ngược lại của 反而.", **changes}
    return {"segments": [row], "screen_texts": [], "summary": ""}


@pytest.fixture
def review(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    client = Mock(has_credentials=True, model="muse-spark-1.3-contributor-free")
    client.translate.return_value = answer()
    scanner = Mock()
    scanner.extract.return_value = [ocr()]
    return AutomaticTranslationReviewer(client, scanner, audio_evidence=False)


def run(reviewer, source=None, screens=None, **kwargs):
    return reviewer.review("sample.mp4", [source or segment()], screens or [], **kwargs)


def test_fresh_evidence_and_separate_semantic_audit_can_resolve_prior_flag(review):
    source = segment()
    original = deepcopy(source)
    progress = []
    result = run(review, source, progress_callback=progress.append)
    row = result["segments"][0]
    assert row["needs_review"] is False and row["verification"]["status"] == "verified"
    assert row["verification"]["evidence"][0]["text_zh"] == "假的反而会限流"
    assert result["summary"] == {"checked": 1, "verified": 1, "corrected": 0, "unresolved": 0}
    assert source == original
    assert progress == [30.0, 75.0, 100.0]
    assert review.client.translate.call_count == 2
    review.screen_ocr.close.assert_called_once()


@pytest.mark.parametrize("evidence", [[], [ocr(confidence=.85)], [ocr("反而会限流")],
                                      [ocr("假的反而不限流")]])
def test_model_confidence_never_clears_missing_partial_or_opposite_source(review, evidence):
    review.screen_ocr.extract.return_value = evidence
    result = run(review)["segments"][0]
    assert result["needs_review"] is True
    assert result["verification"]["status"] == "unresolved"
    assert result["verification"]["source_supported"] is False


def test_exact_verdict_and_independently_covered_explanation_are_valid(review):
    review.screen_ocr.extract.return_value = [ocr("假的", bbox=[.7, .4, .1, .07]), ocr("反而会限流")]
    review.client.translate.return_value = answer(source_evidence_ids=["review0", "review1"])
    screens = [ocr("假的", kind="title", bbox=[.7, .4, .1, .07]), ocr("反而会限流", kind="subtitle")]
    assert run(review, screens=screens)["segments"][0]["needs_review"] is False


def test_standalone_spoken_verdict_may_be_corroborated_by_visible_verdict(review):
    source = segment(text_zh="真的", literal_vi="Đúng.", natural_vi="Đúng.", final_vi="Đúng.")
    review.screen_ocr.extract.return_value = [ocr("真的")]
    review.client.translate.return_value = answer(source)
    assert run(review, source, [ocr("真的", kind="title")])["segments"][0]["needs_review"] is False


def test_delayed_verdict_animation_does_not_reverse_spoken_order(review):
    review.screen_ocr.extract.return_value = [ocr("反而会限流"), ocr("假的", start=1.4, bbox=[.7, .4, .1, .07])]
    review.client.translate.return_value = answer(source_evidence_ids=["review0", "review1"])
    screens = [ocr("假的", kind="title", bbox=[.7, .4, .1, .07]), ocr("反而会限流", kind="subtitle")]
    assert run(review, screens=screens)["segments"][0]["needs_review"] is False


def test_spoken_prefix_does_not_authorize_arbitrary_title_claims(review):
    source = segment(text_zh="连赞会限流")
    review.screen_ocr.extract.return_value = [ocr("连赞会限流")]
    review.client.translate.return_value = answer(source)
    result = run(review, source, [ocr("连赞会限流", kind="title")])["segments"][0]
    assert result["needs_review"] is True


def test_fresh_source_correction_requires_an_established_subtitle_region(review):
    source = segment(text_zh="假的反而会陷流")
    result = run(review, source)["segments"][0]
    assert result["needs_review"] is True and result["text_zh"] == source["text_zh"]
    result = run(review, source, [ocr("反而会陷流", kind="subtitle")])["segments"][0]
    assert result["needs_review"] is False and result["text_zh"] == "假的反而会限流"
    assert result["verification"]["status"] == "corrected"


def test_supported_semantic_correction_is_applied_without_changing_source(review):
    source = segment(final_vi="Sai. Ngược lại còn được đề xuất.")
    result = run(review, source)["segments"][0]
    assert result["final_vi"] == segment()["final_vi"]
    assert result["verification"]["status"] == "corrected"


def test_clean_sentence_also_gets_independent_semantic_check(review):
    source = segment(needs_review=False, review_reason=None)
    assert run(review, source)["summary"]["checked"] == 1


@pytest.mark.parametrize("refs", [["nonexistent"], ["review0", "invented"], ["review1"]])
def test_bad_citations_never_apply_changed_translation(review, refs):
    review.screen_ocr.extract.return_value = [ocr(), ocr(start=4, end=5)]
    review.client.translate.return_value = answer(source_evidence_ids=refs, final_vi="Lời sửa chưa đủ căn cứ")
    result = run(review)["segments"][0]
    assert result["needs_review"] is True
    assert result["final_vi"] == segment()["final_vi"]
    assert "sai ID hoặc sai thời điểm" in result["review_reason"]


@pytest.mark.parametrize("changes", [{"semantic_verified": False},
                                     {"needs_review": True, "review_reason": "Chưa rõ chủ thể"}])
def test_source_match_alone_does_not_approve_semantic_uncertainty(review, changes):
    review.client.translate.return_value = answer(**changes)
    result = run(review)["segments"][0]
    assert result["needs_review"] is True and result["verification"]["source_supported"] is True


def test_changed_source_with_no_support_retains_source_and_translation_together(review):
    review.client.translate.return_value = answer(text_zh="假的反而不会限流", final_vi="Sai. Không bị hạn chế.")
    result = run(review)["segments"][0]
    assert result["text_zh"] == segment()["text_zh"]
    assert result["final_vi"] == segment()["final_vi"]
    assert result["needs_review"] is True


@pytest.mark.parametrize("changes", [{"start": 0}, {"id": 2}, {"semantic_verified": "yes"},
                                     {"source_evidence_ids": None}, {"verification_reason": None}])
def test_bad_contract_fails_without_mutating_existing_draft(review, changes):
    source = segment()
    original = deepcopy(source)
    review.client.translate.return_value = answer(**changes)
    with pytest.raises(VideoIntelligenceError):
        run(review, source)
    assert source == original


def test_cancel_after_ocr_does_not_call_model_and_releases_scanner(review):
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls >= 3
    with pytest.raises(VideoIntelligenceError, match="Đã hủy"):
        run(review, cancel_check=cancel)
    review.client.translate.assert_not_called()
    review.screen_ocr.close.assert_called_once()


def test_does_not_silently_use_paid_or_other_provider(review, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    with pytest.raises(VideoIntelligenceError, match="OpenCode"):
        run(review)
    review.client.translate.assert_not_called()
    review.screen_ocr.extract.assert_not_called()


def test_fresh_ids_are_unique_across_windows_and_existing_screen_ids_untouched(review):
    sources = [segment(), segment(id=1, start=46, end=48)]
    review.screen_ocr.extract.side_effect = [[ocr()], [ocr(start=46, end=48)]]
    data = answer()
    data["segments"].append(answer(sources[1], source_evidence_ids=["review1"])["segments"][0])
    review.client.translate.return_value = json.dumps(data, ensure_ascii=False)
    screens = [ocr(), ocr(start=46, end=48)]
    original = deepcopy(screens)
    result = review.review("sample.mp4", sources, screens)
    assert result["summary"]["verified"] == 2
    assert result["segments"][1]["verification"]["evidence_ids"] == ["review1"]
    assert result["screen_texts"] == screens == original


def test_uncertain_empty_source_cannot_be_silently_approved_as_silence(review):
    source = segment(text_zh="", literal_vi="", natural_vi="", final_vi="")
    review.client.translate.return_value = answer(source)
    result = run(review, source)["segments"][0]
    assert result["needs_review"] is True


def test_confirmed_silence_requires_no_model_or_ocr(review):
    source = segment(text_zh="", literal_vi="", natural_vi="", final_vi="", needs_review=False)
    assert run(review, source)["summary"]["checked"] == 0
    review.client.translate.assert_not_called()
    review.screen_ocr.extract.assert_not_called()


def audio_result(source=None):
    source = source or segment()
    return {"segments": {source["id"]: {**source, "verification": {
                "status": "unresolved", "source_supported": False, "semantic_verified": True}}},
            "screen_texts": [], "translation_sources": [],
            "summary": {"checked": 1, "verified": 0, "corrected": 0, "unresolved": 1}}


def test_independent_audio_agreement_can_verify_video_with_no_subtitles(review):
    review.audio_evidence = Mock()
    review.audio_evidence.collect.return_value = {0: {"sensevoice": "假的，反而会限流。",
                                                      "faster-whisper-small": "假的反而会限流"}}
    result = review.resolve_audio_uncertainty("source.mp4", audio_result())
    row = result["segments"][0]
    assert row["needs_review"] is False
    assert row["verification"]["evidence_mode"] == "dual-local-asr-text-review"
    assert row["verification"]["audio_consensus"] is True
    assert result["summary"] == {"checked": 1, "verified": 1, "corrected": 0, "unresolved": 0}
    assert review.client.translate.call_count == 2


def test_audio_source_correction_requires_semantic_audit_and_regenerable_text(review):
    review.audio_evidence = Mock()
    review.audio_evidence.collect.return_value = {0: {"sensevoice": "假的反而会限流。",
                                                      "faster-whisper-small": "假的反而会限流"}}
    source = segment(text_zh="假的反而会陷流", final_vi="Sai. Sẽ bị lưu lượng.")
    result = review.resolve_audio_uncertainty("source.mp4", audio_result(source))
    assert result["segments"][0]["text_zh"] == segment()["text_zh"]
    assert result["segments"][0]["final_vi"] == segment()["final_vi"]
    assert result["summary"]["corrected"] == 1


@pytest.mark.parametrize("a,b", [("支持", "不支持"), ("24小时", "48小时"),
    ("1.5天", "15天"), ("减少10%", "减少10"), ("还有不会进粉丝群", "还有不会的进入粉丝圈"),
    ("", ""), ("真的", "")])
def test_audio_mismatch_is_never_erased_by_fuzzy_match_or_model(review, a, b):
    review.audio_evidence = Mock()
    review.audio_evidence.collect.return_value = {0: {"sensevoice": a, "faster-whisper-small": b}}
    original = audio_result()
    result = review.resolve_audio_uncertainty("source.mp4", original)
    assert result["segments"][0]["needs_review"] is True
    assert result["segments"][0]["final_vi"] == original["segments"][0]["final_vi"]
    assert result["segments"][0]["verification"]["audio_consensus"] is False
    assert "audio_evidence" not in original["segments"][0]["verification"]
    review.client.translate.assert_not_called()


def test_model_cannot_change_agreed_audio_source(review):
    review.audio_evidence = Mock()
    review.audio_evidence.collect.return_value = {0: {"sensevoice": "假的反而会限流",
                                                      "faster-whisper-small": "假的反而会限流"}}
    review.client.translate.return_value = answer(text_zh="假的反而不会限流", final_vi="Sai. Không bị hạn chế.")
    result = review.resolve_audio_uncertainty("source.mp4", audio_result())["segments"][0]
    assert result["needs_review"] is True
    assert result["final_vi"] == segment()["final_vi"]
    assert result["verification"]["source_supported"] is False


def test_already_ocr_verified_rows_do_not_load_audio_models(review):
    review.audio_evidence = Mock()
    result = audio_result(segment(needs_review=False))
    result["segments"][0]["verification"].update(status="verified", source_supported=True)
    assert review.resolve_audio_uncertainty("source.mp4", result) == result
    review.audio_evidence.collect.assert_not_called()
    review.client.translate.assert_not_called()


def test_audio_extraction_error_preserves_verified_ocr_results(review):
    review.audio_evidence = Mock()
    review.audio_evidence.collect.side_effect = RuntimeError("private media path or process diagnostics")
    original = audio_result()
    result = review.resolve_audio_uncertainty("source.mp4", original)
    assert result["segments"][0]["needs_review"] is True
    assert "private" not in result["segments"][0]["review_reason"]
    review.client.translate.assert_not_called()


def test_cancelled_audio_extraction_is_not_swallowed(review):
    review.audio_evidence = Mock()
    cancelled = False
    def extract(*args):
        nonlocal cancelled
        cancelled = True
        raise RuntimeError("cancelled")
    review.audio_evidence.collect.side_effect = extract
    with pytest.raises(VideoIntelligenceError, match="Đã hủy"):
        review.resolve_audio_uncertainty("source.mp4", audio_result(), cancel_check=lambda: cancelled)
    review.client.translate.assert_not_called()


def test_review_automatically_falls_back_to_audio_for_missing_ocr(review):
    review.screen_ocr.extract.return_value = []
    review.audio_evidence = Mock()
    review.audio_evidence.collect.return_value = {0: {"sensevoice": "假的反而会限流",
                                                      "faster-whisper-small": "假的反而会限流"}}
    result = run(review)
    assert result["segments"][0]["needs_review"] is False
    assert review.client.translate.call_count == 4


def test_audio_model_discovery_never_downloads_missing_weights(tmp_path, monkeypatch):
    from core.translation_review import _LocalAudioEvidence
    from core.engines.asr.sensevoice_engine import SenseVoiceEngine
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    loader = Mock(side_effect=AssertionError("must not load"))
    monkeypatch.setattr(SenseVoiceEngine, "_ensure_loaded", loader)
    assert _LocalAudioEvidence().collect("source.mp4", [segment()]) == {}
    loader.assert_not_called()


@pytest.mark.parametrize("invalid", [RuntimeError("transport"), {"segments": []},
                                     answer(start=0), answer(semantic_verified="yes")])
def test_audio_semantic_failure_keeps_completed_ocr_and_old_draft(review, invalid):
    review.audio_evidence = Mock()
    review.audio_evidence.collect.return_value = {0: {"sensevoice": "假的反而会限流",
                                                      "faster-whisper-small": "假的反而会限流"}}
    if isinstance(invalid, Exception):
        review.client.translate.side_effect = invalid
    else:
        review.client.translate.return_value = invalid
    original = audio_result()
    verified = segment(id=1, needs_review=False, verification={"status": "verified", "source_supported": True})
    original["segments"][1] = verified
    result = review.resolve_audio_uncertainty("source.mp4", original)
    assert result["segments"][0]["final_vi"] == original["segments"][0]["final_vi"]
    assert result["segments"][0]["needs_review"] is True
    assert result["segments"][0]["verification"]["audio_audit_status"] == "failed"
    assert result["segments"][1] == verified


@pytest.mark.parametrize("failed", [False, True])
def test_audio_models_run_sequentially_and_clean_task_pcm_even_on_failure(tmp_path, monkeypatch, failed):
    import sys
    from types import SimpleNamespace
    import core.translation_review as module
    from core.engines.asr.sensevoice_engine import SenseVoiceEngine
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    model_root = tmp_path / "workspace" / "models"
    sense = model_root / "sensevoice_onnx"
    sense.mkdir(parents=True)
    for name in ("model.int8.onnx", "tokens.txt"):
        (sense / name).touch()
    whisper = model_root / "faster-whisper-small"
    whisper.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json"):
        (whisper / name).touch()
    engines = []
    def sense_load(engine):
        engines.append(engine)
        stream = SimpleNamespace(result=SimpleNamespace(text="真的。"), accept_waveform=Mock())
        engine.recognizer = SimpleNamespace(create_stream=lambda: stream, decode_stream=Mock())
    monkeypatch.setattr(SenseVoiceEngine, "_ensure_loaded", sense_load)
    wav_paths = []
    def ffmpeg(argv, cancel):
        from pathlib import Path
        path = Path(argv[-1])
        path.write_bytes(b"pcm")
        wav_paths.append(path)
    monkeypatch.setattr(module, "run_media", ffmpeg)
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(read=lambda *a, **kw: ([.1], 16000)))
    class Whisper:
        def __init__(self, path, **kwargs):
            assert engines[0].recognizer is None
            assert path == str(whisper)
            assert kwargs["local_files_only"] is True
        def transcribe(self, path, **kwargs):
            if failed:
                raise RuntimeError("test inference failure")
            return iter([SimpleNamespace(text="真的")]), None
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Whisper))
    if failed:
        with pytest.raises(RuntimeError, match="inference failure"):
            module._LocalAudioEvidence().collect("source.mp4", [segment()])
    else:
        assert module._LocalAudioEvidence().collect("source.mp4", [segment()]) == {
            0: {"sensevoice": "真的。", "faster-whisper-small": "真的"}}
    assert all(not path.exists() and not path.parent.exists() for path in wav_paths)
    assert engines[0].recognizer is None


def test_saved_evidence_review_does_not_reload_ocr_or_audio(review):
    initial = run(review)
    original = deepcopy(initial)
    review.screen_ocr.extract.side_effect = AssertionError("must not rescan video")
    review.audio_evidence = Mock()
    result = review.review_saved_evidence(initial)
    assert result["segments"][0]["needs_review"] is False
    assert initial == original
    review.audio_evidence.collect.assert_not_called()
    assert review.client.translate.call_count == 4
