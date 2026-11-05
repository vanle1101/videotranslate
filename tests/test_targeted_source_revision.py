"""A contextual ASR repair can update a draft without claiming verification."""
from types import SimpleNamespace

from core.streaming.pipeline import StreamingPipelineSession


def test_review_publication_retains_unresolved_source_and_asr_text():
    row = SimpleNamespace(text_zh="玩拖拉", asr_text="玩拖拉", final_vi="Chơi kéo.",
        literal_vi="Chơi kéo.", natural_vi="Chơi kéo.", needs_review=True,
        review_reason="Source candidate not independently confirmed.",
        verification={"status": "pending", "semantic_verified": False})
    StreamingPipelineSession._apply_review_metadata(row, {
        "text_zh": "玩脱啦", "asr_text": "玩脱啦", "literal_vi": "Chơi quá trớn rồi.",
        "natural_vi": "Chơi lố rồi.", "final_vi": "Chơi lố rồi.", "needs_review": True,
        "review_reason": "Contextual ASR supports this source candidate; keep it reviewable.",
        "verification": {"status": "unresolved", "semantic_verified": False,
                          "reason": "Contextual source candidate is not independently verified."},
    })
    assert row.text_zh == row.asr_text == "玩脱啦"
    assert row.final_vi == "Chơi lố rồi."
    assert row.needs_review is True
    assert row.verification["status"] == "unresolved"
    assert row.verification["semantic_verified"] is False

