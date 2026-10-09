"""Fault injection for disposable native workers; never mocked real QA."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from config import settings
from core.engines.asr import whisper_process as module
from core.runtime_context import execution_context


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    """Launch a real child process with injected protocol/failure behavior."""
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"fixture")
    original = subprocess.Popen
    calls = []
    def install(program):
        def launch(command, **kwargs):
            calls.append({"command": command, "environment": kwargs["env"],
                          "request": json.loads(Path(command[-2]).read_text(encoding="utf-8"))})
            process = original([sys.executable, "-B", "-X", "utf8", "-c", program,
                                command[-1]], **kwargs)
            calls[-1]["process"] = process
            return process
        monkeypatch.setattr(module.subprocess, "Popen", launch)
        return calls
    return audio, install


def test_native_worker_preserves_configuration_validates_output_and_exits(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json, sys
with open(sys.argv[1], 'w', encoding='utf-8') as out:
    out.write(json.dumps({'event':'segment', 'segment': {'start':0, 'end':1.2,
        'text':'你好', 'words':[{'start':0.1,'end':1.1,'word':'你好'}]}})+'\\n')
    out.write(json.dumps({'event':'completed', 'segments':1})+'\\n')
''')
    rows, info = module.SubprocessWhisperModel("small").transcribe(audio, language="zh", beam_size=5,
                                                                        vad_filter=True)
    assert [(row.text, row.start, row.end, row.words[0].word) for row in rows] == [("你好", 0, 1.2, "你好")]
    assert info is None
    assert calls[0]["request"]["model_size"] == "small"
    assert calls[0]["request"]["compute_type"] == settings.WHISPER_COMPUTE_TYPE
    assert calls[0]["request"]["options"] == {"language": "zh", "beam_size": 5, "vad_filter": True}
    assert calls[0]["environment"]["MKL_DISABLE_FAST_MM"] == "1"
    assert calls[0]["process"].poll() == 0
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


@pytest.mark.parametrize("output", ["not-json\\n", "[]\\n", '{"event":"completed","segments":2}\\n',
    '{"event":"segment","segment":{"start":0,"end":1,"text":"x","words":[]}}\\n',
    '{"event":"segment","segment":{"start":1,"end":0,"text":"x","words":[]}}\\n'])
def test_invalid_or_partial_native_output_never_becomes_completed(fake_worker, output):
    audio, install = fake_worker
    calls = install("import sys; open(sys.argv[1], 'w').write(" + repr(output.replace("\\n", "\n")) + ")")
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    with pytest.raises(module.ASRProcessError):
        list(rows)
    assert calls[0]["process"].poll() is not None
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


def test_native_crash_does_not_publish_partial_transcription(fake_worker):
    audio, install = fake_worker
    calls = install("import sys; print('mkl_malloc: failed to allocate memory', file=sys.stderr); sys.exit(7)")
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    with pytest.raises(module.ASRProcessError, match="mkl_malloc"):
        list(rows)
    assert calls[0]["process"].poll() == 7


def test_timeout_kills_reaps_worker_and_releases_slot(fake_worker, monkeypatch):
    audio, install = fake_worker
    monkeypatch.setattr(settings, "ASR_PROCESS_TIMEOUT", .2, raising=False)
    calls = install("import time; time.sleep(30)")
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    with pytest.raises(TimeoutError):
        list(rows)
    assert calls[0]["process"].poll() is not None
    assert module._WORKER_SLOT.acquire(blocking=False)
    module._WORKER_SLOT.release()
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


def test_cancellation_interrupts_native_inference_without_waiting_for_segment(fake_worker):
    audio, install = fake_worker
    calls = install("import time; time.sleep(30)")
    started = time.monotonic()
    with execution_context("native-cancel", lambda: time.monotonic() - started > .25):
        rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
        with pytest.raises(asyncio.CancelledError):
            list(rows)
    assert calls[0]["process"].poll() is not None
    assert time.monotonic() - started < 5


def test_closing_iterator_drains_child_before_returning(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json, sys, time
with open(sys.argv[1], 'w') as out:
    out.write(json.dumps({'event':'segment', 'segment': {'start':0,'end':1,'text':'x','words':[]}})+'\\n')
    out.flush()
    time.sleep(30)
''')
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    assert next(rows).text == "x"
    rows.close()
    assert calls[0]["process"].poll() is not None


def test_cancelled_queued_request_never_spawns_second_native_worker(fake_worker):
    audio, install = fake_worker
    calls = install("raise AssertionError('must not launch')")
    assert module._WORKER_SLOT.acquire(blocking=False)
    started = time.monotonic()
    try:
        with execution_context("queued-cancel", lambda: time.monotonic() - started > .15):
            rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
            with pytest.raises(asyncio.CancelledError):
                list(rows)
    finally:
        module._WORKER_SLOT.release()
    assert not calls
