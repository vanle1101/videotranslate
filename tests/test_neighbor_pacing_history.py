"""Offline durable history for the separately owned neighbor rescue loop."""
from types import SimpleNamespace
import asyncio

import pytest

from core.engines.alignment import pacing_checkpoint as cache
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.engines.translation.semantic_translator import PacingReviewRejected
from test_pacing_checkpoint import durable, aligner
from test_neighbor_silence_recovery import rescue, _fixture_speech
from unittest.mock import Mock
from pathlib import Path


def history(aligner, **changes):
    args = dict(scope={"focus_id": 87, "neighbor_id": 86, "revision": 1},
        feedback={"raw_budget_seconds": 1.15, "measured_candidates": [{"text": "Câu gốc.", "measured_seconds": 2.}]},
        translator=object(), source="来源", text="Câu gốc.", duration=1., voice="fixture", engine=object(),
        aligner=aligner, context=[], ref_audio=None, max_duration=1., max_duration_limit=None,
        allow_bidirectional_reflow=False, source_language="zh", max_pacing_proposals=5, allow_review_draft=True)
    return cache.NeighborPacingHistory(**{**args, **changes})


def test_neighbor_history_reopens_rejections_and_measurements_without_approval(durable, aligner):
    first = history(aligner)
    first.rejected(PacingReviewRejected("Bad", candidate="Sai.", reason="Sai nghĩa", code="semantic_mismatch"))
    error = SpeechBudgetError("too long")
    error.measured_candidate = {"measured_seconds": 1.8}
    first.timing_failed(text="Dài.", proof={"status": "verified", "text": "Dài."}, error=error, max_speed=1.15)
    second = history(aligner)
    assert second.feedback["rejected_candidates"][0]["reason"] == "Sai nghĩa"
    assert {r["text"] for r in second.feedback["measured_candidates"]} == {"Dài.", "Câu gốc."}
    for text in ["Sai.", "Dài.", "Câu gốc."]:
        with pytest.raises(PacingReviewRejected):
            second.reject_duplicate(text)
    assert second.reject_duplicate("Ứng viên mới.") is None  # Still requires caller's validators/TTS.


def test_neighbor_scope_change_does_not_reuse_proof(durable, aligner):
    first = history(aligner)
    first.rejected(PacingReviewRejected("Bad", candidate="Sai.", code="semantic_mismatch"))
    changed = history(aligner, scope={"focus_id": 87, "neighbor_id": 86, "revision": 2})
    assert changed.key != first.key
    assert not changed.feedback.get("rejected_candidates")


def test_neighbor_cancel_never_publishes_late_rejection(durable, aligner):
    stopped = [False]
    first = history(aligner, cancel_check=lambda: stopped[0])
    stopped[0] = True
    with pytest.raises(asyncio.CancelledError):
        first.rejected(PacingReviewRejected("Late", candidate="Sai.", code="semantic_mismatch"))
    assert not (durable / "cache/pacing_history").exists()


def test_neighbor_unknown_duration_still_retains_failed_wording(durable, aligner):
    first = history(aligner)
    first.timing_failed(text="Dài.", proof={"status": "verified", "text": "Dài."},
                        error=SpeechBudgetError("no reliable measurement"), max_speed=1.15)
    second = history(aligner)
    assert second.measured == []
    with pytest.raises(PacingReviewRejected):
        second.reject_duplicate("Dài.")


def test_pipeline_retry_reopens_neighbor_history_and_synthesizes_only_new_wording(durable, rescue, monkeypatch):
    row = rescue.segments[0]
    row.verification = {"status": "verified"}
    original = Path(row.audio_path).read_bytes()
    rescue.tts_engine = object()
    rescue.translator = Mock()
    monkeypatch.setattr(rescue, "_dialogue_context_before", lambda *args: [])
    bad = PacingReviewRejected("Invalid meaning", candidate="Sai.", code="semantic_mismatch")
    rescue.translator.rewrite_for_pacing.side_effect = bad
    _, staged = asyncio.run(rescue._prepare_dub_rescue(rescue._dub_rows(), rescue.segments[1], .85))
    for update in staged.values():
        Path(update["audio_path"]).unlink()
    assert rescue.translator.rewrite_for_pacing.call_count == 5
    def candidate(text):
        return {"final_vi": text, "pacing_verification": {"status": "verified", "text": text}}
    rescue.translator = Mock(rewrite_for_pacing=Mock(side_effect=[candidate("Sai."), candidate("Lời mới.")]))
    calls = []
    def synthesize(**kwargs):
        calls.append(kwargs["text"])
        _fixture_speech(kwargs["output_path"], .4)
        return {"text": kwargs["text"], "tts_duration": .4, "speed_ratio": 1., "boundaries": []}
    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesize)
    _, staged = asyncio.run(rescue._prepare_dub_rescue(rescue._dub_rows(), rescue.segments[1], .85))
    assert calls == ["Lời mới."]
    assert rescue.translator.rewrite_for_pacing.call_args_list[0].kwargs["feedback"]["rejected_candidates"][0]["text"] == "Sai."
    assert staged[0]["spoken"]["text"] == "Lời mới."
    assert row.final_vi == "Giữ nguyên lời."
    assert Path(row.audio_path).read_bytes() == original
    assert rescue.segments[2].verification["status"] == "manual"
