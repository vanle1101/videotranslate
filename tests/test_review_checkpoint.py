"""Review identity must follow address policy, including policy-only updates."""
from core import review_checkpoint as cache


def test_address_policy_only_change_invalidates_prior_review(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"offline-source-identity")
    policy_revision = "address-policy-before"
    real_digest = cache.file_digest

    def file_digest(path, check=lambda: None):
        if path.name == "translation_context.py":
            return policy_revision
        return real_digest(path, check)

    monkeypatch.setattr(cache, "file_digest", file_digest)
    options = dict(directory=tmp_path / "checkpoints", runtime_revision={"test": True})
    first = cache.ReviewCheckpoint(video, "offline-model", **options)
    stage = {"kind": "review_batch", "rows": [{"text_zh": "这话应该我来问吧"}]}
    payload = {"final_vi": "Câu này phải để tao hỏi mới đúng chứ."}
    assert first.namespace["code"]["translation_context.py"] == policy_revision
    assert first.store(stage, payload)
    assert cache.ReviewCheckpoint(video, "offline-model", **options).load(stage) == payload

    policy_revision = "address-policy-after"
    changed = cache.ReviewCheckpoint(video, "offline-model", **options)
    assert changed.load(stage) is None
    assert changed._path(stage) != first._path(stage)
    assert first._path(stage).exists()  # An invalidated cache is not user-data deletion.


def test_changed_dialogue_context_cannot_reuse_prior_review(tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"offline-source-identity")
    checkpoint = cache.ReviewCheckpoint(video, "offline-model", directory=tmp_path / "checkpoints",
                                        runtime_revision={"test": True})
    before = {"kind": "review_batch", "rows": [{"text_zh": "这话应该我来问吧"}], "context": []}
    assert checkpoint.store(before, {"final_vi": "Câu này phải để tôi hỏi mới đúng chứ."})
    after = {**before, "context": [{"text_zh": "拜托姐", "source_status": "corroborated"}]}
    assert checkpoint.load(after) is None
