"""Source checkpoints identify loaded code, not live constant refcounts.

These isolated lifecycle regressions do not stand in for real ASR/provider QA.
"""
import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

from config import settings
from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.streaming.source_revision import _code, source_runtime_identity


def test_source_identity_survives_first_asr_result_retaining_emotion_constant():
    engine = FasterWhisperFallbackEngine()
    engine.model = SimpleNamespace(transcribe=lambda *args, **kwargs: (
        iter([SimpleNamespace(start=0., end=1., text="测试", words=[], speaker=None)]), None))
    session = SimpleNamespace(faster_whisper=engine)
    before = source_runtime_identity(session)

    retained_rows = engine.transcribe("isolated-iterator-no-native-inference")

    assert retained_rows[0]["emotion"] == "<|NEUTRAL|>"
    assert source_runtime_identity(session) == before
    assert before["revision"] == 2


def test_source_identity_survives_retained_speaker_progress(monkeypatch):
    from core.streaming import speaker_source

    monkeypatch.setattr(settings, "DIARIZATION_ENABLED", True)
    monkeypatch.setattr("core.streaming.chunked_source.ensure_source_identity", lambda _: "source-id")

    class ProgressAdapter:
        def diarize(self, *args, progress_callback, **kwargs):
            progress_callback({"processed": 1, "total": 1})
            return {}

        def annotate(self, rows, result):
            return rows

    monkeypatch.setattr(speaker_source, "_speaker_adapter", lambda *args: ProgressAdapter())
    retained_progress = []

    async def blocking(function, *args, **kwargs):
        result = function(*args, **kwargs)
        # Deliver the callback while the annotation operation is still active.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return result

    async def report(*args, **kwargs):
        retained_progress.append((args, kwargs))

    session = SimpleNamespace(faster_whisper=FasterWhisperFallbackEngine(),
        cache_dir=None, video_path=None, total_duration=10., is_stopped=False,
        _run_blocking=blocking, report_progress=report, warnings=[])
    before = source_runtime_identity(session)
    rows = [{"start": 0., "end": 1., "text_zh": "测试"}]

    assert asyncio.run(speaker_source.annotate_source_rows(session, rows, 0., 2.)) == rows

    assert retained_progress
    assert retained_progress[0][0][1] == "Đang phân biệt giọng nói trong đoạn…"
    assert source_runtime_identity(session) == before


def test_code_identity_changes_for_changed_loaded_implementation():
    def loaded(source):
        namespace = {}
        exec(compile(source, "loaded-source.py", "exec"), namespace)
        return namespace["candidate"]

    original = loaded("def candidate():\n    return 'original source'\n")
    changed = loaded("def candidate():\n    return 'corrected source'\n")
    assert _code(original) != _code(changed)
    assert _code(original) == _code(loaded("def candidate():\n    return 'original source'\n"))
    assert _code(object()) is None


def test_code_identity_survives_restart_and_hash_randomization():
    script = """from core.streaming.source_revision import _code
def candidate(value):
    return value in {'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta'}
print(_code(candidate))
"""
    identities = []
    for seed in ("1", "2"):
        result = subprocess.run([sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True,
            text=True, check=True, timeout=30)
        identities.append(result.stdout.strip())
    assert len(identities[0]) == 64
    assert identities[0] == identities[1]
