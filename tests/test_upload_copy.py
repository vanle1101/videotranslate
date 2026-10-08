"""Upload I/O must leave health/cancel traffic responsive; no generation mocks certify runtime."""
import asyncio
import io
import threading
from pathlib import Path
import pytest
from fastapi import HTTPException
import httpx
import main

class BlockingInput(io.BytesIO):
    def __init__(self):
        super().__init__(b"actual upload bytes")
        self.entered, self.release = threading.Event(), threading.Event()
    def read(self, size):
        assert size == 1024 * 1024
        self.entered.set()
        self.release.wait(3)
        return super().read(size)

async def entered(source):
    for _ in range(100):
        if source.entered.is_set():
            return
        await asyncio.sleep(.005)
    raise AssertionError("Upload worker did not start")

def test_copy_runs_off_loop_and_health_responds_during_real_io(tmp_path):
    source = BlockingInput()
    destination = tmp_path / "uploaded.mp4"
    async def run():
        upload = asyncio.create_task(main.save_uploaded_inputs([(source, destination)], "copy-test"))
        try:
            await entered(source)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://local") as client:
                response = await asyncio.wait_for(client.get("/api/health"), .25)
                assert response.status_code == 200 and response.json()["status"] == "ok"
            source.release.set()
            await upload
            assert destination.read_bytes() == b"actual upload bytes"
        finally:
            source.release.set()
            await upload
    asyncio.run(run())

def test_cancel_joins_worker_removes_partial_files_and_allows_retry(tmp_path):
    source = BlockingInput()
    destination = tmp_path / "uploaded.mp4"
    async def run():
        upload = asyncio.create_task(main.save_uploaded_inputs([(source, destination)], "cancel-test"))
        await entered(source)
        upload.cancel()
        await asyncio.sleep(.01)
        source.release.set()
        with pytest.raises(asyncio.CancelledError):
            await upload
        assert not destination.exists()
        await main.save_uploaded_inputs([(io.BytesIO(b"retry bytes"), destination)], "retry-test")
        assert destination.read_bytes() == b"retry bytes"
    asyncio.run(run())

def test_disk_failure_is_actionable_and_never_creates_a_completed_upload(tmp_path):
    class BrokenInput:
        def read(self, size):
            raise OSError("private disk detail")
    destination = tmp_path / "uploaded.mp4"
    with pytest.raises(HTTPException) as failed:
        asyncio.run(main.save_uploaded_inputs([(BrokenInput(), destination)], "disk-test"))
    assert failed.value.status_code == 507
    assert "private" not in failed.value.detail and "dung lượng" in failed.value.detail
    assert not destination.exists()
