"""Backend-only loop ownership and real cancellable preparation subprocesses."""
import asyncio
import socket
import struct
import subprocess
import shutil
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.streaming.pipeline import StreamingPipelineSession
from core.services.backend_runtime import create_backend_loop, run_backend_server

CHILD_PYTHON = getattr(sys, "_base_executable", sys.executable)


def selector_run(coroutine):
    loop = asyncio.SelectorEventLoop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


def preparation_owner():
    owner = SimpleNamespace(task_id="backend-subprocess-qa", is_stopped=False)
    owner._run_blocking = StreamingPipelineSession._run_blocking.__get__(owner)
    return owner


def test_preparation_process_works_on_selector_without_blocking_http_loop(tmp_path):
    target = tmp_path / "prepared.txt"
    owner = preparation_owner()
    ticks = []
    command = [CHILD_PYTHON, "-B", "-c",
        "import pathlib,sys,time; time.sleep(.25); pathlib.Path(sys.argv[1]).write_text('prepared')", str(target)]

    async def run():
        async def pulse():
            while not task.done():
                ticks.append(time.monotonic())
                await asyncio.sleep(.01)
        task = asyncio.create_task(StreamingPipelineSession._run_ffmpeg(owner, command))
        await asyncio.gather(task, pulse())
    selector_run(run())
    assert target.read_text() == "prepared" and len(ticks) >= 5


@pytest.mark.parametrize("session_stop", [False, True])
def test_cancelled_selector_preparation_reaps_child_before_return(tmp_path, monkeypatch, session_stop):
    owner = preparation_owner()
    marker = tmp_path / "started.txt"
    late = tmp_path / "late.txt"
    processes = []
    real_popen = subprocess.Popen
    def observed(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr("core.media_process.subprocess.Popen", observed)
    command = [CHILD_PYTHON, "-B", "-c",
        "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('started'); "
        "time.sleep(20); pathlib.Path(sys.argv[2]).write_text('late')", str(marker), str(late)]

    async def run():
        task = asyncio.create_task(StreamingPipelineSession._run_ffmpeg(owner, command))
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and not task.done() and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            assert marker.exists()
            start = time.monotonic()
            if session_stop:
                owner.is_stopped = True
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert time.monotonic() - start < 3
            assert processes and all(process.poll() is not None for process in processes)
            await asyncio.sleep(.05)
            assert not late.exists()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    selector_run(run())


def test_preparation_failure_is_not_converted_to_success():
    owner = preparation_owner()
    with pytest.raises(RuntimeError, match="actual stderr fixture"):
        selector_run(StreamingPipelineSession._run_ffmpeg(owner,
            [CHILD_PYTHON, "-B", "-c", "import sys; sys.stderr.write('actual stderr fixture'); sys.exit(3)"]))


def test_real_ffmpeg_preparation_produces_readable_pcm_on_selector(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not installed")
    target = tmp_path / "prepared.wav"
    selector_run(StreamingPipelineSession._run_ffmpeg(preparation_owner(),
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=0.2", "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", str(target)]))
    with wave.open(str(target), "rb") as audio:
        assert audio.getframerate() == 16000 and audio.getnchannels() == 1
        assert audio.getnframes() == 3200 and len(audio.readframes(3200)) == 6400


def test_stopped_preparation_does_not_start_a_child(monkeypatch):
    owner = preparation_owner()
    owner.is_stopped = True
    monkeypatch.setattr("core.media_process.subprocess.Popen", lambda *args, **kwargs: pytest.fail("already stopped"))
    with pytest.raises(asyncio.CancelledError):
        selector_run(StreamingPipelineSession._run_ffmpeg(owner, [CHILD_PYTHON, "-B", "-c", "pass"]))


@pytest.mark.parametrize("legacy_runner", [False, True])
def test_backend_loop_is_local_and_preserves_provider_default_policy(monkeypatch, legacy_runner):
    if legacy_runner:
        monkeypatch.delattr(asyncio, "Runner", raising=False)
    policy = asyncio.get_event_loop_policy()
    default = asyncio.new_event_loop()
    provider_loop_type = type(default)
    default.close()
    observed = {}
    async def provider_probe():
        return type(asyncio.get_running_loop())
    class Server:
        async def serve(self):
            observed["backend"] = asyncio.get_running_loop()
            observed["provider"] = await asyncio.to_thread(lambda: asyncio.run(provider_probe()))
    run_backend_server(Server())
    assert asyncio.get_event_loop_policy() is policy
    assert observed["provider"] is provider_loop_type
    assert observed["backend"].is_closed()
    if sys.platform == "win32":
        assert isinstance(observed["backend"], asyncio.SelectorEventLoop)
        assert issubclass(provider_loop_type, asyncio.ProactorEventLoop)


@pytest.mark.parametrize("legacy_runner", [False, True])
def test_backend_runner_drains_cancelled_process_owner_and_async_generator(tmp_path, monkeypatch, legacy_runner):
    if legacy_runner:
        monkeypatch.delattr(asyncio, "Runner", raising=False)
    owner = preparation_owner()
    marker = tmp_path / "started.txt"
    processes = []
    original_popen = subprocess.Popen
    def observed(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr("core.media_process.subprocess.Popen", observed)
    state = {}
    async def source():
        try:
            yield "first"
        finally:
            state["generator_closed"] = True
    class Server:
        async def serve(self):
            state["loop"] = asyncio.get_running_loop()
            state["source"] = source()
            assert await state["source"].__anext__() == "first"
            task = asyncio.create_task(StreamingPipelineSession._run_ffmpeg(owner,
                [CHILD_PYTHON, "-B", "-c", "import pathlib,sys,time; "
                 "pathlib.Path(sys.argv[1]).write_text('started'); time.sleep(20)", str(marker)]))
            state["task"] = task
            deadline = time.monotonic() + 5
            while not marker.exists() and not task.done() and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            assert marker.exists()
    run_backend_server(Server())
    assert state["task"].cancelled()
    assert state["generator_closed"] and state["loop"].is_closed()
    assert processes and all(process.poll() is not None for process in processes)


def test_normal_launcher_uses_explicit_backend_runner_without_global_uvicorn_loop(monkeypatch):
    import importlib
    import uvicorn
    from core.services.service_manager import ServiceManager
    manager_module = importlib.import_module("core.services.service_manager")
    seen = {}
    monkeypatch.setattr(ServiceManager, "_instance", None)
    from config import settings
    monkeypatch.setattr(settings, "PORT", settings.PORT)
    def config(**kwargs):
        seen["config"] = kwargs
        return SimpleNamespace()
    def server(config):
        result = SimpleNamespace(run=lambda: pytest.fail("must use backend-only runner"))
        seen["server"] = result
        return result
    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
        def is_alive(self):
            return True
    class Healthy:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(uvicorn, "Config", config)
    monkeypatch.setattr(uvicorn, "Server", server)
    monkeypatch.setattr(manager_module, "threading", SimpleNamespace(Thread=Thread, Lock=threading.Lock))
    monkeypatch.setattr(manager_module.urllib.request, "urlopen", lambda *args, **kwargs: Healthy())
    monkeypatch.setattr("core.services.backend_runtime.run_backend_server", lambda value: seen.update(runner=value))
    manager = ServiceManager()
    assert manager.start_backend(port=12345) == 12345
    assert seen["config"]["loop"] == "none"
    assert seen["runner"] is seen["server"]


def test_peer_reset_http_and_websocket_release_backend_socket_owners(caplog):
    """Use an isolated real Uvicorn socket; never start the production app."""
    import uvicorn
    import websockets
    from starlette.responses import StreamingResponse
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    errors = []
    state = {}
    async def body():
        for _ in range(300):
            yield b"x" * 8192
            await asyncio.sleep(.01)
    async def app(scope, receive, send):
        state["loop"] = asyncio.get_running_loop()
        state["loop"].set_exception_handler(lambda loop, context: errors.append(context))
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.accept"})
            request = await receive()
            await send({"type": "websocket.send", "text": request["text"]})
            await receive()
        elif scope["path"] == "/media":
            await StreamingResponse(body(), media_type="video/mp4")(scope, receive, send)
        else:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"healthy"})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        loop="none", lifespan="off", log_config=None, access_log=False, timeout_graceful_shutdown=1))
    worker_errors = []
    def run():
        try:
            run_backend_server(server)
        except BaseException as error:
            worker_errors.append(error)
    thread = threading.Thread(target=run, name="isolated-backend-loop-qa")
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, worker_errors
        for _ in range(5):
            client = socket.create_connection(("127.0.0.1", port), timeout=2)
            try:
                client.sendall(b"GET /media HTTP/1.1\r\nHost: localhost\r\n\r\n")
                assert client.recv(32768).startswith(b"HTTP/1.1 200")
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                    struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0))
            finally:
                client.close()
        async def echo():
            async with websockets.connect(f"ws://127.0.0.1:{port}/echo") as socket_client:
                await socket_client.send("real socket echo")
                assert await socket_client.recv() == "real socket echo"
        asyncio.run(echo())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            assert response.read() == b"healthy"
        until = time.monotonic() + 3
        while (server.server_state.connections or server.server_state.tasks) and time.monotonic() < until:
            time.sleep(.01)
        assert not server.server_state.connections and not server.server_state.tasks
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive() and not worker_errors and not errors
    assert state["loop"].is_closed()
    assert "timeout graceful shutdown exceeded" not in caplog.text
    assert "ConnectionResetError" not in caplog.text
    with socket.socket() as closed:
        closed.settimeout(.5)
        assert closed.connect_ex(("127.0.0.1", port)) != 0
