"""Real file/range sockets and disconnect cleanup; no generated media claimed."""
import asyncio
import os
import socket
import struct
import sys
import threading
import time
import urllib.request

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
