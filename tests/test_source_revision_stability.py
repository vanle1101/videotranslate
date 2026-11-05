"""Source fingerprints remain stable while nested functions are alive."""
import asyncio
import hashlib
import json
import os
import subprocess

from config import settings
from core.streaming.chunked_source import prepare_interval, source_failure_diagnostic
from core.streaming.pipeline import StreamingPipelineSession
from core.streaming.source_revision import _code
import pytest


def test_loaded_code_fingerprint_is_independent_of_nested_function_lifetime():
    def parent():
        def child():
            return "measured source"
        return child
    before = _code(parent)
    live_child = parent()
    assert _code(parent) == before
    assert live_child() == "measured source"
    assert _code(parent) == before


def test_loaded_code_fingerprint_detects_changed_nested_implementation():
    original, changed = {}, {}
    exec(compile("def parent():\n def child():\n  return 1\n return child\n", "fixture.py", "exec"), original)
    exec(compile("def parent():\n def child():\n  return 2\n return child\n", "fixture.py", "exec"), changed)
    assert _code(original["parent"]) != _code(changed["parent"])


def test_actual_source_preparation_commits_and_reuses_after_nested_callbacks(tmp_path, monkeypatch):
    for field, relative in {"BASE_DIR": "", "WORKSPACE_DIR": "workspace", "INPUT_DIR": "workspace/inputs",
                            "OUTPUT_DIR": "workspace/outputs", "TEMP_DIR": "workspace/temp"}.items():
        path = tmp_path / relative
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, field, path)
    monkeypatch.setattr(settings, "DIARIZATION_ENABLED", False)
    model = settings.WORKSPACE_DIR / "models" / f"faster-whisper-{settings.WHISPER_MODEL_SIZE}"
    model.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (model / name).write_bytes(b"isolated identity fixture; never loaded")
    source = settings.INPUT_DIR / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=400:sample_rate=48000:duration=1", "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", str(source)], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    session = StreamingPipelineSession("source-stability", source, visual_translation=True)
    session.total_duration = 1.
    session.is_running = True
    calls = []
    def recognize(*args, **kwargs):
        calls.append(1)
        kwargs["progress_callback"](.8)
        return [{"start": 0., "end": .8, "text_zh": "fixture"}]
    monkeypatch.setattr(session.faster_whisper, "transcribe", recognize)
    async def run():
        first = await prepare_interval(session, 0., 1.)
        second = await prepare_interval(session, 0., 1.)
        assert first == second
        assert first["end"] == 1.
        saved = session.cache_dir / "source_preparation" / "000000000000.json"
        assert json.loads(saved.read_text(encoding="utf-8"))["rows"][0]["text_zh"] == "fixture"
        assert not list(saved.parent.glob("*.tmp"))
    asyncio.run(run())
    assert len(calls) == 1


def test_source_failure_diagnostic_omits_unknown_error_payload():
    try:
        raise ValueError("sensitive-transcript Authorization: Bearer credential")
    except ValueError as error:
        stage, code, trace = source_failure_diagnostic(error, "asr")
    assert stage == "asr" and code == "source_preparation_failed"
    assert "test_source_revision_stability.py:" in trace
    assert "credential" not in trace and "sensitive-transcript" not in trace


@pytest.mark.parametrize("module,function", [("core.streaming.chunked_source", "_digest"),
                                             ("core.streaming.preparation_checkpoint", "digest")])
def test_source_checksum_supports_python_without_file_digest(tmp_path, monkeypatch, module, function):
    monkeypatch.delattr(hashlib, "file_digest", raising=False)
    path = tmp_path / "checksum.bin"
    path.write_bytes(b"abc")
    target = getattr(__import__(module, fromlist=[function]), function)
    assert target(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
