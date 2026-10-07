"""Offline regression for full dialogue context in the batch speech path."""
from copy import deepcopy

from config import settings
from core.engines.alignment.timing_aligner import TimingBudgetAligner
from core.translation_context import focus_identity


def test_batch_alignment_passes_future_source_and_stable_focus_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    segments = [
        {"id": 4, "start": 0.0, "end": 2.0, "text_zh": "这话应该我来问吧",
         "final_vi": "Câu này phải để tôi hỏi chứ."},
        {"id": 9, "start": 2.0, "end": 4.0, "text_zh": "姐你听我说",
         "vi_text": "Chị nghe em nói này.", "is_focus": True},
    ]
    original = deepcopy(segments)
    contexts = []

    def synthesize(*, text, context=None, **kwargs):
        assert context is not None, "Batch pacing must receive source dialogue context"
        contexts.append(deepcopy(context))
        # A consumer must not mutate the caller's rows or the next request's
        # context. The actual synthesis adapter also hashes this full context.
        context[0]["text_zh"] = "mutated by consumer"
        return {"text": text, "tts_duration": 1.5, "speed_ratio": 1.0,
                "boundaries": [], "pacing_verification": None}

    monkeypatch.setattr("core.engines.alignment.natural_speech.synthesize_natural_speech", synthesize)
    monkeypatch.setattr("core.engines.alignment.timing_aligner.build_speech_timing", lambda *args: {})
    aligned = TimingBudgetAligner().align_and_budget(segments, object(), object())

    assert [row["id"] for row in aligned] == [4, 9]
    assert focus_identity(contexts[0]) == {"id": 4, "start": 0.0, "end": 2.0}
    assert focus_identity(contexts[1]) == {"id": 9, "start": 2.0, "end": 4.0}
    assert contexts[0][1]["text_zh"] == "姐你听我说"
    assert contexts[0][1]["final_vi"] == "Chị nghe em nói này."
    assert contexts[0][1]["translation_is_draft"] is True
    assert contexts[1][0]["text_zh"] == original[0]["text_zh"]
    assert segments == original
