"""Native ASR cancellation at decoded segment boundaries; no model download."""
import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.runtime_context import execution_context


def test_whisper_stops_before_consuming_remaining_transcript():
    engine = FasterWhisperFallbackEngine()
    consumed, progress = [], []
    cancelled = False
    def segments():
        for index in range(100):
            consumed.append(index)
            yield SimpleNamespace(start=index, end=index + 1, text="你好", words=[])
    engine.model = SimpleNamespace(transcribe=lambda *a, **kw: (segments(), None))
    def on_progress(end):
        nonlocal cancelled
        progress.append(end)
        cancelled = True
    with execution_context("asr-test", lambda: cancelled):
        with pytest.raises(asyncio.CancelledError):
            engine.transcribe(Path("fixture.wav"), progress_callback=on_progress)
    assert progress == [1.0]
    assert consumed == [0, 1]


def test_cancelled_asr_never_loads_model():
    engine = FasterWhisperFallbackEngine()
    with execution_context("asr-test", lambda: True):
        with pytest.raises(asyncio.CancelledError):
            engine.transcribe(Path("fixture.wav"))
    assert engine.model is None


def test_pipeline_drains_native_cancellation_without_blocking_event_loop():
    # A regression here spins without yielding, so an asyncio timeout cannot
    # protect pytest. A bounded subprocess exercises the real executor safely.
    program = textwrap.dedent('''
        import asyncio
        import logging
        import threading
        from types import SimpleNamespace
        for name in ("app", "ai", "pipeline", "errors"):
            logging.getLogger(name).addHandler(logging.NullHandler())
        from core.runtime_context import current_execution_context
        from core.streaming.pipeline import StreamingPipelineSession

        async def main():
            session = SimpleNamespace(task_id="cancel-regression", is_stopped=False)
            def cancelled_worker():
                raise asyncio.CancelledError("ASR acknowledged stop")
            try:
                await StreamingPipelineSession._run_blocking(session, cancelled_worker)
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Worker cancellation became success")

            entered, exited = threading.Event(), threading.Event()
            def native_worker():
                context = current_execution_context()
                entered.set()
                while not context.cancel_check():
                    exited.wait(0.001)
                exited.set()
                raise asyncio.CancelledError("ASR acknowledged stop")
            task = asyncio.create_task(StreamingPipelineSession._run_blocking(session, native_worker))
            while not entered.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Stop became success")
            assert exited.is_set(), "Caller returned before worker drained"
            await asyncio.sleep(0)
            print("cancellation drained; event loop responsive")
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", program],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    assert "cancellation drained; event loop responsive" in result.stdout
