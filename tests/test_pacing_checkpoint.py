"""Durable observations are feedback, never approval or real-provider evidence."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.alignment import pacing_checkpoint as cache
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.alignment.speech_cache import build_speech_cache_identity
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.engines.translation.semantic_translator import PacingReviewRejected
from core.runtime_context import execution_context
from test_natural_speech import RecordedSynthesizer, aligner, verified_candidate, reject_network


@pytest.fixture
def durable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    def adapter(**kwargs):
        return build_speech_cache_identity(source=kwargs["source"], text=kwargs["text"],
            duration=kwargs["duration"], voice=kwargs["voice"], engine="fixture",
            engine_revision="1", provider="fixture", model="fixture-v1", context=kwargs["context"],
            max_speed=kwargs["aligner"].max_speed)
    monkeypatch.setattr(cache, "synthesis_cache_identity", adapter)
    return tmp_path


def options(tmp_path, aligner, translator, engine):
    return dict(text="Lời ban đầu.", source="原文", duration=1., output_path=tmp_path / "result.wav",
        engine=engine, aligner=aligner, translator=translator, voice="fixture-voice",
        context=[{"id": 1, "text_zh": "原文"}])


def test_restart_keeps_feedback_rejects_repeated_candidate_and_new_proposal_fits(durable, aligner):
    wrong = "Lời sai."
    first = Mock(rewrite_for_pacing=Mock(side_effect=PacingReviewRejected(
        "Semantic failure", candidate=wrong, reason="Sai nghĩa", code="semantic_mismatch")))
    args = options(durable, aligner, first, RecordedSynthesizer(2.))
    with pytest.raises(PacingReviewRejected):
        synthesize_natural_speech(**args)
    assert not args["output_path"].exists()
    assert len(list((durable / "cache/pacing_history").glob("*.json"))) == 1
    second = Mock(rewrite_for_pacing=Mock(side_effect=[verified_candidate(wrong), verified_candidate("Lời mới.")]))
    engine = RecordedSynthesizer({"Lời ban đầu.": 2., wrong: .3, "Lời mới.": .7})
    result = synthesize_natural_speech(**options(durable, aligner, second, engine))
    feedback = second.rewrite_for_pacing.call_args_list[0].kwargs["feedback"]
    assert feedback["rejected_candidates"][0]["text"] == wrong
    assert feedback["rejected_candidates"][0]["code"] == "semantic_mismatch"
    assert engine.calls == ["Lời ban đầu.", "Lời mới."]
    assert result["text"] == "Lời mới." and result["speed_ratio"] <= 1.15


def test_measured_verified_history_does_not_approve_or_synthesize_cached_candidate(durable, aligner):
    first = Mock(rewrite_for_pacing=Mock(return_value=verified_candidate("Quá dài.")))
    with pytest.raises((SpeechBudgetError, PacingReviewRejected)):
        synthesize_natural_speech(**options(durable, aligner, first, RecordedSynthesizer(2.)))
    stored = json.loads(next((durable / "cache/pacing_history").glob("*.json")).read_text(encoding="utf-8"))
    assert any(r.get("pacing_verification", {}).get("status") == "verified"
               for r in stored["data"]["measured"] if r.get("pacing_verification"))
    second = Mock(rewrite_for_pacing=Mock(return_value=verified_candidate("Quá dài.")))
    engine = RecordedSynthesizer({"Lời ban đầu.": 2., "Quá dài.": .3})
    with pytest.raises(PacingReviewRejected):
        synthesize_natural_speech(**options(durable, aligner, second, engine))
    assert engine.calls == ["Lời ban đầu."]
    assert not (durable / "result.wav").exists()


@pytest.mark.parametrize("field,value", [("source", "新来源"), ("voice", "other-voice"),
    ("context", [{"id": 2, "text_zh": "另一句"}]), ("duration", 1.1),
    ("source_language", "ko"), ("allow_review_draft", True), ("max_duration", 1.2)])
def test_changed_inputs_never_seed_stale_history(durable, aligner, field, value):
    first = Mock(rewrite_for_pacing=Mock(side_effect=PacingReviewRejected(
        "Bad", candidate="Sai.", code="semantic_mismatch")))
    with pytest.raises(PacingReviewRejected):
        synthesize_natural_speech(**options(durable, aligner, first, RecordedSynthesizer(2.)))
    second = Mock(rewrite_for_pacing=Mock(return_value=verified_candidate("Mới.")))
    args = options(durable, aligner, second, RecordedSynthesizer({"Lời ban đầu.": 2., "Mới.": .7}))
    args[field] = value
    synthesize_natural_speech(**args)
    assert not second.rewrite_for_pacing.call_args.kwargs["feedback"].get("rejected_candidates")


def test_corruption_and_bad_proof_are_not_trusted_and_cancel_preserves_record(durable):
    key = "a" * 64
    measured = [{"text": "Lời.", "measured_seconds": 2., "pacing_verification": None}]
    cache.store(key, measured, {})
    path = durable / "cache/pacing_history" / (key + ".json")
    original = path.read_bytes()
    with pytest.raises(asyncio.CancelledError):
        cache.store(key, [], {}, cancel_check=lambda: True)
    assert path.read_bytes() == original
    bad = deepcopy(measured)
    bad[0]["pacing_verification"] = {"status": "rejected", "text": "Lời."}
    cache.store(key, bad, {})
    assert path.read_bytes() == original
    path.write_text("broken JSON")
    assert cache.load(key) == {"measured": [], "feedback": {}}
    assert not list(path.parent.glob("*.tmp"))


def test_manual_mode_does_not_load_or_write_history(durable, aligner, monkeypatch):
    check = Mock(wraps=cache.synthesis_cache_identity)
    monkeypatch.setattr(cache, "synthesis_cache_identity", check)
    args = options(durable, aligner, None, RecordedSynthesizer(2.))
    with pytest.raises(SpeechBudgetError):
        synthesize_natural_speech(**args)
    check.assert_not_called()
    assert not (durable / "cache/pacing_history").exists()


@pytest.mark.parametrize("raw", ["[]", "null", '"wrong"', "5"])
def test_nonobject_history_is_ignored(durable, raw):
    key = "b" * 64
    cache.store(key, [], {})
    (durable / "cache/pacing_history" / (key + ".json")).write_text(raw)
    assert cache.load(key) == {"measured": [], "feedback": {}}


@pytest.mark.parametrize("key", ["../outside", "", 12])
def test_invalid_history_key_never_creates_files(durable, key):
    cache.store(key, [], {})
    assert cache.load(key) == {"measured": [], "feedback": {}}
    assert not (durable / "cache/pacing_history").exists()


def test_changed_loaded_policy_model_and_speed_change_identity(durable, aligner, monkeypatch):
    args = options(durable, aligner, object(), object())
    args.pop("output_path")
    args.update(max_duration=1., max_duration_limit=None, allow_bidirectional_reflow=False,
                source_language="zh", max_pacing_proposals=5, allow_review_draft=False)
    first = cache.identity(**args)
    monkeypatch.setattr(cache, "_REVISION", "different-loaded-policy")
    assert cache.identity(**args) != first
    second = cache.identity(**args)
    base = cache.synthesis_cache_identity
    from dataclasses import replace
    monkeypatch.setattr(cache, "synthesis_cache_identity", lambda **kw: replace(base(**kw), key="different-model-key"))
    assert cache.identity(**args) != second
    monkeypatch.setattr(cache, "synthesis_cache_identity", base)
    changed = deepcopy(aligner)
    changed.max_speed = 1.1
    assert cache.identity(**{**args, "aligner": changed}) != second


def test_cancelled_round_preserves_prior_rejections(durable, aligner):
    stopped = [False]
    def proposal(*args, **kwargs):
        if stopped[0]:
            raise AssertionError("No request after cancel")
        stopped[0] = True
        raise PacingReviewRejected("Rejected then stopped", candidate="Sai.", code="semantic_mismatch")
    translator = Mock(rewrite_for_pacing=Mock(side_effect=proposal))
    with execution_context("cancel-history", lambda: stopped[0]), pytest.raises(asyncio.CancelledError):
        synthesize_natural_speech(**options(durable, aligner, translator, RecordedSynthesizer(2.)))
    record = json.loads(next((durable / "cache/cancel-history/pacing_history").glob("*.json")).read_text(encoding="utf-8"))
    assert record["data"]["feedback"] == {}  # Cancel cannot publish its late response.
    assert not (durable / "result.wav").exists()


def test_long_project_rejections_survive_other_rows_projects_and_restart(durable):
    first_key = f"{0:064x}"
    with execution_context("long-project"):
        for index in range(70):
            cache.store(f"{index:064x}", [], {"rejected_candidates": [
                {"text": f"Draft {index}", "reason": "Lost meaning", "code": "semantic_mismatch"}]})
    with execution_context("other-project"):
        cache.store(first_key, [], {"rejected_candidates": []})
    # Restore the first job's execution identity after the other job finishes.
    with execution_context("long-project"):
        assert cache.load(first_key)["feedback"]["rejected_candidates"][0]["text"] == "Draft 0"
    assert len(list((durable / "cache/long-project/pacing_history").glob("*.json"))) == 70


def test_scoped_history_reads_legacy_without_overwriting_shared_record(durable):
    key = "e" * 64
    cache.store(key, [], {"rejected_candidates": []})
    legacy = (durable / "cache/pacing_history" / (key + ".json")).read_bytes()
    with execution_context("restored-job"):
        assert cache.load(key)["feedback"] == {"rejected_candidates": []}
        cache.store(key, [], {"rejected_candidates": [
            {"text": "Rejected", "reason": "Mismatch", "code": "semantic_mismatch"}]})
        assert cache.load(key)["feedback"]["rejected_candidates"]
    assert (durable / "cache/pacing_history" / (key + ".json")).read_bytes() == legacy
