"""Native-language rewrites must preserve source, meaning and physical limits."""
import json
from unittest.mock import Mock

import pytest

from core.engines.asr.source_language import require_supported_source, source_row_language
from core.engines.translation.semantic_translator import PacingReviewRejected
from core.multilingual_translation import NativePacingTranslator
from core.streaming.native_recovery import native_language
from test_natural_speech import RecordedSynthesizer, aligner
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.alignment.timing_aligner import SpeechBudgetError


class Client:
    def __init__(self, verdict="equivalent", natural=True):
        self.prompts = []
        self.replies = [{"final_vi": "Chưa làm."}, {"candidate": "Chưa làm.", "verdict": verdict,
            "natural": natural, "reason": "Giữ phủ định, chưa rõ chủ thể.", "source_quote": "안 했어"}]

    def translate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return json.dumps(self.replies.pop(0), ensure_ascii=False)


@pytest.mark.parametrize("language", ["ko", "ja", "en", "de", "fr", "ar", "vi", "yue"])
def test_supported_languages_route_without_forcing_chinese(language):
    assert require_supported_source({"language": language, "probability": .99})["language"] == language
    assert native_language({"evidence_mode": "native-recovery-" + language}) == language


def test_mixed_script_preserves_native_row_language_without_certifying_it():
    metadata = {"language": "ko", "probability": .99}
    assert source_row_language("안녕하세요", metadata) == "ko"
    assert source_row_language("我们是朋友", metadata) == "zh"
    assert source_row_language("こんにちは", metadata) == "ja"
    assert source_row_language("日本", {"language": "ja", "probability": .99}) == "ja"


@pytest.mark.parametrize("verdict,status", [("equivalent", "verified"), ("uncertain", "unresolved")])
def test_native_pacing_separate_exact_candidate_audit(verdict, status):
    client = Client(verdict)
    result = NativePacingTranslator("ko", client).rewrite_for_pacing("아직 안 했어", "Tôi vẫn chưa làm.", 1.,
        measured_duration=2.5, feedback={"measured_candidates": []})
    assert result["pacing_verification"]["status"] == status
    assert result["pacing_verification"]["text"] == "Chưa làm."
    assert len(client.prompts) == 2
    assert '"source_language": "ko"' in client.prompts[1]
    assert '"measured_seconds": 2.5' in client.prompts[0]


@pytest.mark.parametrize("verdict,natural", [("wrong", True), ("uncertain", False)])
def test_known_wrong_or_unnatural_candidate_is_never_eligible(verdict, natural):
    with pytest.raises(PacingReviewRejected):
        NativePacingTranslator("ko", Client(verdict, natural)).rewrite_for_pacing("아직 안 했어", "Tôi chưa làm.", 1.)


def test_uncertain_native_rewrite_can_fit_only_as_explicit_draft(tmp_path, aligner):
    original = "Tôi vẫn chưa làm."
    engine = RecordedSynthesizer({original: 3., "Chưa làm.": .8})
    result = synthesize_natural_speech(text=original, source="아직 안 했어", duration=1.,
        output_path=tmp_path / "fit.wav", engine=engine, aligner=aligner,
        translator=NativePacingTranslator("ko", Client("uncertain")))
    assert result["text"] == "Chưa làm."
    assert result["pacing_verification"]["status"] == "unresolved"
    assert result["speed_ratio"] <= aligner.max_speed


def test_at_most_five_distinct_proposals_without_changing_existing_output(tmp_path, aligner):
    original = "Lời đầu tiên."
    candidates = [f"Lời thử {n}." for n in range(5)]
    adapter = NativePacingTranslator("ko")
    adapter.rewrite_for_pacing = Mock(side_effect=[{"final_vi": candidate, "pacing_verification": {
        "status": "verified", "text": candidate}} for candidate in candidates])
    output = tmp_path / "fit.wav"
    output.write_bytes(b"old valid publication")
    engine = RecordedSynthesizer(3.)
    with pytest.raises(SpeechBudgetError):
        synthesize_natural_speech(text=original, source="아직 안 했어", duration=1., output_path=output,
            engine=engine, aligner=aligner, translator=adapter)
    assert adapter.rewrite_for_pacing.call_count == 5
    assert engine.calls == [original, *candidates]
    assert output.read_bytes() == b"old valid publication"


def test_duplicate_native_proposals_never_consume_extra_tts(tmp_path, aligner):
    original = "Lời đầu tiên."
    adapter = NativePacingTranslator("ko")
    adapter.rewrite_for_pacing = Mock(return_value={"final_vi": original, "pacing_verification": {
        "status": "verified", "text": original}})
    engine = RecordedSynthesizer(3.)
    with pytest.raises(PacingReviewRejected):
        synthesize_natural_speech(text=original, source="아직 안 했어", duration=1., output_path=tmp_path / "fit.wav",
            engine=engine, aligner=aligner, translator=adapter)
    assert adapter.rewrite_for_pacing.call_count == 5
    assert engine.calls == [original]
