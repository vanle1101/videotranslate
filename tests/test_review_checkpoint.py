"""Review identity must follow address policy, including policy-only updates."""
from pathlib import Path
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
    monkeypatch.setattr(cache, "_PROCESS_IMPLEMENTATION_REVISION", cache.implementation_revision())
    options = dict(directory=tmp_path / "checkpoints", runtime_revision={"test": True})
    first = cache.ReviewCheckpoint(video, "offline-model", **options)
    stage = {"kind": "review_batch", "rows": [{"text_zh": "这话应该我来问吧"}]}
    payload = {"final_vi": "Câu này phải để tao hỏi mới đúng chứ."}
    assert first.namespace["code"]["translation_context.py"] == policy_revision
    assert first.store(stage, payload)
    assert cache.ReviewCheckpoint(video, "offline-model", **options).load(stage) == payload

    policy_revision = "address-policy-after"
    # Editing disk does not change the code already loaded in this process.
    old_process = cache.ReviewCheckpoint(video, "offline-model", **options)
    assert old_process.load(stage) == payload
    assert old_process._path(stage) == first._path(stage)
    # Simulate a fresh process capturing the newly loaded implementation.
    monkeypatch.setattr(cache, "_PROCESS_IMPLEMENTATION_REVISION", cache.implementation_revision())
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


def test_loaded_review_revision_is_a_defensive_copy():
    expected = cache.loaded_implementation_revision()
    changed = cache.loaded_implementation_revision()
    changed["translation_context.py"] = "mutated"
    assert cache.loaded_implementation_revision() == expected


def test_missing_loaded_revision_disables_optional_checkpoint(tmp_path, monkeypatch):
    import pytest
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    monkeypatch.setattr(cache, "_PROCESS_IMPLEMENTATION_REVISION", None)
    with pytest.raises(OSError, match="runtime revision"):
        cache.ReviewCheckpoint(video, "offline-model", runtime_revision={})


def test_content_digest_reuses_unchanged_reads_but_invalidates_replaced_source(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"first-real-content")
    opens = []
    original = Path.open
    def counted(path, *args, **kwargs):
        if path == source and args == ("rb",):
            opens.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", counted)
    first = cache.file_digest(source)
    assert cache.file_digest(source) == first
    assert opens == [str(source)]
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"other-real-content")
    replacement.replace(source)
    assert cache.file_digest(source) != first
    assert opens == [str(source), str(source)]


def test_cached_digest_still_honors_stop(tmp_path):
    import asyncio
    import pytest
    source = tmp_path / "source.bin"
    source.write_bytes(b"real-content")
    cache.file_digest(source)
    def stopped():
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        cache.file_digest(source, stopped)


def test_visual_checkpoint_uses_loaded_code_until_process_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import video_intelligence as visual
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    processor = object.__new__(visual.VideoIntelligence)
    processor.provider = "opencode"
    processor.client = SimpleNamespace(model="offline-model")
    rows = [{"id": 0, "start": 0, "end": 1, "text_zh": "你好"}]
    first = processor._checkpoint_identity(video, rows, 1)
    assert first is not None
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"changed disk code" if p.suffix == ".py" else original(p))
    assert processor._checkpoint_identity(video, rows, 1)["key"] == first["key"]
    monkeypatch.setattr(visual, "_PROCESS_VISUAL_REVISION", visual._capture_visual_revision())
    assert processor._checkpoint_identity(video, rows, 1)["key"] != first["key"]
    monkeypatch.setattr(visual, "_PROCESS_VISUAL_REVISION", None)
    assert processor._checkpoint_identity(video, rows, 1) is None
