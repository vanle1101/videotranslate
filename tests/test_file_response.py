"""Real file/range sockets and disconnect cleanup; no generated media claimed."""
import asyncio
import os
import socket
import struct
import sys
import threading
import time
import urllib.request
from types import SimpleNamespace

import psutil
import pytest
import uvicorn

from core.services.backend_runtime import run_backend_server
from core.services.file_response import FileResponse


def test_real_file_ranges_disconnect_and_shutdown_release_transfer_and_handle(tmp_path, caplog):
    source = tmp_path / "range-fixture.bin"
    source.write_bytes(bytes(range(256)) * 8192)

    class SmallChunkResponse(FileResponse):
        # A real file with small chunks keeps a disconnected legacy file reader
        # busy long enough to expose its missing disconnect listener.
        chunk_size = 64

    async def app(scope, receive, send):
        await SmallChunkResponse(source, media_type="application/octet-stream")(scope, receive, send)

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, loop="none",
        lifespan="off", log_config=None, access_log=False, timeout_graceful_shutdown=1))
    errors = []
    def serve():
        try:
            run_backend_server(server)
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=serve, name="real-file-range-qa")
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, errors
        request = urllib.request.Request(f"http://127.0.0.1:{port}/media", headers={"Range": "bytes=0-7"})
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 206 and response.read() == bytes(range(8))
            assert response.headers["Content-Range"] == f"bytes 0-7/{source.stat().st_size}"
        head = urllib.request.Request(f"http://127.0.0.1:{port}/media", method="HEAD")
        with urllib.request.urlopen(head, timeout=2) as response:
            assert response.status == 200 and response.read() == b""
        for _ in range(3):
            client = socket.create_connection(("127.0.0.1", port), timeout=2)
            try:
                client.sendall(b"GET /media HTTP/1.1\r\nHost: localhost\r\nRange: bytes=0-\r\n\r\n")
                assert client.recv(8192).startswith(b"HTTP/1.1 206")
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                    struct.pack("HH" if sys.platform == "win32" else "ii", 1, 0))
            finally:
                client.close()
        deadline = time.monotonic() + 2
        while (server.server_state.connections or server.server_state.tasks) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not server.server_state.connections and not server.server_state.tasks
        assert not any(os.path.normcase(item.path) == os.path.normcase(str(source))
                       for item in psutil.Process().open_files())
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    assert "timeout graceful shutdown exceeded" not in caplog.text
    assert "Exception in ASGI application" not in caplog.text
    with socket.socket() as closed:
        closed.settimeout(.5)
        assert closed.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("fault", ["missing-file", "send-failure", "receive-failure"])
def test_file_transfer_errors_are_not_converted_into_success(tmp_path, fault):
    source = tmp_path / "fixture.bin"
    if fault != "missing-file":
        source.write_bytes(b"real fixture bytes")
    scope = {"type": "http", "method": "GET", "headers": [], "extensions": {}}
    async def run():
        gate = asyncio.Event()
        async def receive():
            if fault == "receive-failure":
                raise OSError("receive fault")
            await gate.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            if fault == "send-failure":
                raise OSError("send fault")
        expected = RuntimeError if fault == "missing-file" else OSError
        with pytest.raises(expected):
            await FileResponse(source)(scope, receive, send)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(run())


@pytest.mark.parametrize("interrupt", ["disconnect", "cancel"])
def test_interrupt_during_native_file_open_drains_and_closes_handle(tmp_path, monkeypatch, interrupt):
    import anyio
    import starlette.responses
    path = tmp_path / "slow-open.bin"
    path.write_bytes(b"content" * 100)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    handles = []
    def slow_open(*args, **kwargs):
        handle = open(*args, **kwargs)
        handles.append(handle)
        entered.set()
        release.wait(3)
        finished.set()
        return handle
    # mode is a keyword at the Starlette boundary, not run_sync's arguments.
    async def wrapped_open(file, mode="rb"):
        return anyio.AsyncFile(await anyio.to_thread.run_sync(slow_open, file, mode))
    monkeypatch.setattr(starlette.responses.anyio, "open_file", wrapped_open)
    async def run():
        disconnected = asyncio.Event()
        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            pass
        task = asyncio.create_task(FileResponse(path)(
            {"type":"http", "method":"GET", "headers":[], "extensions":{}}, receive, send))
        try:
            for _ in range(300):
                if entered.is_set(): break
                await asyncio.sleep(.01)
            assert entered.is_set()
            if interrupt == "cancel": task.cancel()
            else: disconnected.set()
            await asyncio.sleep(.05)
            assert not task.done(), "Native open must drain before the response owner exits"
            release.set()
            if interrupt == "cancel":
                with pytest.raises(asyncio.CancelledError): await task
            else: await task
            assert finished.is_set() and all(h.closed for h in handles)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            for handle in handles: handle.close()
    asyncio.run(run())


@pytest.mark.parametrize("blocked_socket", [False, True])
def test_multiple_connected_slow_transfers_shutdown_drains_handles_despite_expected_cancel_logs(tmp_path, caplog, blocked_socket):
    """Grace timeout cancels live requests; its traceback alone is not a leak."""
    source = tmp_path / "multi-client.bin"
    source.write_bytes(bytes(range(256)) * 32768)
    clients, errors, drained = [], [], []
    class SlowResponse(FileResponse):
        chunk_size = 65536 if blocked_socket else 1024
    async def app(scope, receive, send):
        async def slow_send(message):
            if not blocked_socket and message["type"] == "http.response.body":
                await asyncio.sleep(.05)
            await send(message)
        try:
            await SlowResponse(source)(scope, receive, slow_send)
        finally:
            drained.append(scope["client"])
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,loop="none",
        lifespan="off",log_config=None,access_log=False,timeout_graceful_shutdown=.15))
    def serve():
        try:
            run_backend_server(server)
        except BaseException as error:
            errors.append(error)
    thread=threading.Thread(target=serve,name="multi-client-shutdown-qa")
    thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.01)
        assert server.started,errors
        for _ in range(3):
            client=socket.socket()
            client.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
            client.settimeout(2)
            client.connect(("127.0.0.1",port))
            clients.append(client)
            client.sendall(b"GET /media HTTP/1.1\r\nHost: localhost\r\nRange: bytes=0-\r\n\r\n")
            assert client.recv(8192).startswith(b"HTTP/1.1 206")
        assert len(server.server_state.tasks)==3
        if blocked_socket:
            deadline=time.monotonic()+3
            while not all(connection.flow.write_paused for connection in server.server_state.connections) and time.monotonic()<deadline:
                time.sleep(.01)
            assert all(connection.flow.write_paused for connection in server.server_state.connections)
        server.should_exit=True
        thread.join(5)

        assert not thread.is_alive() and not errors
        assert len(drained)==3 and not server.server_state.tasks
        assert not server.server_state.connections
        assert not any(os.path.normcase(item.path)==os.path.normcase(str(source))
                       for item in psutil.Process().open_files())
        traces=[record for record in caplog.records if "Exception in ASGI application" in record.message]
        assert traces and all(isinstance(record.exc_info[1],asyncio.CancelledError) for record in traces)
        assert "Task was destroyed" not in caplog.text and "unhandled exception during" not in caplog.text.lower()
        with socket.socket() as closed:
            closed.settimeout(.5)
            assert closed.connect_ex(("127.0.0.1",port))!=0
    finally:
        for client in clients:
            client.close()
        server.should_exit=True
        thread.join(5)


@pytest.mark.parametrize("legacy_runner", [False, True])
def test_backend_shutdown_drains_cancelled_response_before_global_native_open_cancellation(tmp_path,monkeypatch,legacy_runner):
    import anyio
    import starlette.responses
    if legacy_runner:
        monkeypatch.delattr(asyncio,"Runner",raising=False)
    source=tmp_path/"native-open.bin"
    source.write_bytes(b"existing fixture")
    entered,release=threading.Event(),threading.Event()
    handles=[]
    def native_open(path,mode):
        handle=open(path,mode)
        handles.append(handle)
        entered.set()
        assert release.wait(4)
        return handle
    async def open_file(path,mode="rb"):
        return anyio.AsyncFile(await anyio.to_thread.run_sync(native_open,path,mode))
    monkeypatch.setattr(starlette.responses.anyio,"open_file",open_file)
    class Server:
        server_state=SimpleNamespace(connections=set(),tasks=set())
        async def serve(self):
            async def receive():
                await asyncio.Event().wait()
            async def send(message):
                pass
            self.response=asyncio.create_task(FileResponse(source)(
                {"type":"http","method":"GET","headers":[],"extensions":{}},receive,send))
            self.server_state.tasks.add(self.response)
            deadline=time.monotonic()+2
            while not entered.is_set() and time.monotonic()<deadline:
                await asyncio.sleep(.01)
            assert entered.is_set()
            self.response.cancel("simulated expired graceful shutdown")
            self.release_timer=threading.Timer(.1,release.set)
            self.release_timer.start()
    server=Server()
    try:
        run_backend_server(server)
        assert server.response.cancelled()
        assert handles and all(handle.closed for handle in handles)
    finally:
        release.set()
        if hasattr(server,"release_timer"):
            server.release_timer.join(2)
        for handle in handles:
            handle.close()
