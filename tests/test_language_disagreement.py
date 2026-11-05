from types import SimpleNamespace
import numpy as np
import pytest

from core.engines.asr.language_disagreement import recover_language, validate_language_review
from core.engines.asr.utterance_language import transcribe_local_speech


def row(text, start=.2, end=2.):
    return SimpleNamespace(start=start, end=end, text=text,
        words=[SimpleNamespace(word=text, start=start, end=end)])


def test_independent_language_can_select_only_one_conditional_unverified_hypothesis():
    calls = []
    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter([row("韩国男娃说话说一般怪头怪脑的")]), SimpleNamespace(language="zh", language_probability=1.)
    evidence = lambda audio: {"language": "zh", "text": "韩国男娃说话说一般怪头怪脑的", "tokens": []}
    original = [row("내 한그나와 소화소위반")]
    rows, language, probability, proof = recover_language(Model(), np.zeros(128000),
        32000, 96000, (0, 128000), original, SimpleNamespace(language="ko", language_probability=.6),
        {}, evidence)
    assert language == "zh" and probability == 0.
    assert len(calls) == 1 and calls[0]["language"] == "zh" and calls[0]["multilingual"] is False
    assert proof["source_supported"] is False and proof["status"] == "unresolved"
    assert proof["automatic_probability"] == .6 and len(proof["alternatives"]) == 2
    assert proof["alternatives"][0]["rows"][0]["text"] == original[0].text
    assert rows[0].words[0].start == .2
    validate_language_review(proof)


@pytest.mark.parametrize("evidence", [
    [{"language": "ko", "text": "우리 친구잖아"}] * 2,
    [{"language": "zh", "text": "你好朋友你好"}, {"language": "ko", "text": "우리 친구잖아"}],
    [{"language": "zh", "text": "你好 우리 친구잖아"}] * 2,
    [{"language": "fr", "text": "bonjour les amis"}] * 2])
def test_agreement_contradiction_or_mixed_script_never_forces_chinese(evidence):
    it = iter(evidence)
    original = [row("우리 친구잖아")]
    rows, language, probability, proof = recover_language(object(), np.zeros(128000),
        32000, 96000, (0, 128000), original, SimpleNamespace(language="ko", language_probability=.6),
        {}, lambda audio: next(it))
    assert rows is original and language == "ko" and probability == .6
    assert proof["conditional_selected"] is False


def test_healthy_original_window_does_not_run_disagreement_reader():
    class Model:
        def transcribe(self, audio, **kwargs):
            return iter([row("우리 친구잖아", .3, 1.2)]), SimpleNamespace(language="ko", language_probability=.99)
    rows, _ = transcribe_local_speech(Model(), np.zeros(64000), {},
        regions=[{"start": 16000, "end": 32000}], language_reader=lambda audio: pytest.fail("healthy row"))
    assert list(rows)[0].source_language_review is None


def test_independent_failure_preserves_original():
    def unavailable(audio):
        raise RuntimeError("model unavailable")
    original = [row("우리 친구잖아")]
    rows, language, probability, proof = recover_language(object(), np.zeros(128000),
        32000, 96000, (0, 128000), original, SimpleNamespace(language="ko", language_probability=.6), {}, unavailable)
    assert rows is original and proof["reason"].startswith("independent_evidence_unavailable")


def test_durable_contract_rejects_fake_source_certification():
    proof = {"status": "unresolved", "source_supported": False, "automatic_language": "ko",
        "selected_language": "zh", "automatic_probability": .6, "conditional_selected": True,
        "independent": [], "alternatives": [{}]}
    from core.streaming.session_store import SEGMENT_FIELDS, _validate_source_metadata
    from core.streaming.pipeline import SOURCE_METADATA_FIELDS
    assert "source_language_review" in SEGMENT_FIELDS and "source_language_review" in SOURCE_METADATA_FIELDS
    _validate_source_metadata({"source_language_review": proof})
    with pytest.raises(ValueError):
        _validate_source_metadata({"source_language_review": {**proof, "source_supported": True}})


def test_conditional_candidate_losing_measured_coverage_is_rejected():
    class Model:
        def transcribe(self, audio, **kwargs):
            return iter([row("韩国男娃说话", .0, .5)]), SimpleNamespace(language="zh", language_probability=1.)
    evidence = lambda audio: {"language": "zh", "text": "韩国男娃说话说一般怪头怪脑的", "tokens": []}
    original = [row("내 한그나와 소화소위반", .0, 1.2), row("과이토과이", 1.2, 2.8)]
    rows, language, probability, proof = recover_language(Model(), np.zeros(128000),
        32000, 96000, (0, 128000), original, SimpleNamespace(language="ko", language_probability=.6),
        {}, evidence)
    assert rows is original and language == "ko" and proof["reason"] == "conditional_coverage_loss"
