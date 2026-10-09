import json
from types import SimpleNamespace

import pytest

from config import settings
from core.streaming import preparation_checkpoint as checkpoint
from core.streaming.pipeline import SegmentItem


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    model = tmp_path / "workspace/models" / f"faster-whisper-{settings.WHISPER_MODEL_SIZE}"
    model.mkdir(parents=True)
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (model / name).write_bytes(b"isolated asset-identity fixture; never used for inference")
    root = tmp_path / "workspace" / "cache" / "source-run"
    root.mkdir(parents=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    raw, bgm = root / "raw.wav", root / "bgm.ogg"
    raw.write_bytes(b"raw fixture")
    bgm.write_bytes(b"bgm fixture")
    row = SegmentItem(0, 0, 2, 2)
    row.text_zh, row.asr_pretranscribed = "你好", True
    return SimpleNamespace(video_path=source, asr_engine_name="faster-whisper", visual_translation=True,
                           raw_audio_16k=raw, bgm_audio_path=bgm, total_duration=3,
                           video_size=(1920, 1080), suppression_stats={}, segments={0: row})


def test_preparation_survives_new_session_and_keeps_source_unchanged(prepared):
    checkpoint.save(prepared)
    fresh = SimpleNamespace(video_path=prepared.video_path, asr_engine_name="faster-whisper", visual_translation=True)
    saved = checkpoint.load(fresh)
    assert saved["segments"][0]["text_zh"] == "你好"
    assert saved["assets"]["raw_audio_16k"]["path"] == str(prepared.raw_audio_16k.resolve())
    assert not list(checkpoint.location(checkpoint.identity(prepared)).parent.glob("*.tmp"))


def test_preparation_cache_accepts_real_ct2_text_vocabulary_and_rejects_missing_assets(prepared):
    model = settings.BASE_DIR / "workspace/models" / f"faster-whisper-{settings.WHISPER_MODEL_SIZE}"
    (model / "vocabulary.json").unlink()
    (model / "vocabulary.txt").write_bytes(b"isolated CTranslate2 text-vocabulary identity fixture")
    checkpoint.save(prepared)
    assert checkpoint.identity(prepared)["runtime"]["whisper_asset_revision_known"]
    assert checkpoint.load(prepared) is not None
    (model / "model.bin").unlink()
    assert not checkpoint.identity(prepared)["runtime"]["whisper_asset_revision_known"]
    assert checkpoint.load(prepared) is None


@pytest.mark.parametrize("damage", ["source", "audio", "model", "json", "timing", "foreign", "missing"])
def test_changed_or_incomplete_preparation_is_never_reused(prepared, monkeypatch, tmp_path, damage):
    checkpoint.save(prepared)
    index = checkpoint.location(checkpoint.identity(prepared))
    if damage == "source":
        prepared.video_path.write_bytes(b"different source")
    elif damage == "audio":
        prepared.raw_audio_16k.write_bytes(b"")
    elif damage == "model":
        monkeypatch.setattr(settings, "WHISPER_MODEL_SIZE", "large-v3")
    elif damage == "json":
        index.write_text("{", encoding="utf-8")
    elif damage == "missing":
        prepared.bgm_audio_path.unlink()
    else:
        data = json.loads(index.read_text(encoding="utf-8"))
        if damage == "timing":
            data["segments"][0]["end"] = 900
        else:
            data["assets"]["raw_audio_16k"]["path"] = str(prepared.video_path)
        index.write_text(json.dumps(data), encoding="utf-8")
    assert checkpoint.load(prepared) is None
