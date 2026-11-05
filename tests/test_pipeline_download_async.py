"""Regression tests for the legacy async pipeline's blocking download stage."""

import asyncio
import time

import pytest

from core.pipeline import VideoTranslationPipeline


class _BlockingDownloader:
    def __init__(self, delay=0.2):
        self.delay = delay

    def download(self, _video_input, *, cancel_check=None):
        # Simulate the synchronous network/file work performed by the real
        # downloader.  The test should continue servicing the event loop.
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            if cancel_check and cancel_check():
                raise RuntimeError("cancelled")
            time.sleep(0.005)
        raise RuntimeError("download sentinel")


def test_legacy_download_does_not_block_event_loop():
    asyncio.run(_test_legacy_download_does_not_block_event_loop())


async def _test_legacy_download_does_not_block_event_loop():
    pipeline = VideoTranslationPipeline.__new__(VideoTranslationPipeline)
    pipeline.downloader = _BlockingDownloader()

    ticks = 0
    running = True

    async def heartbeat():
        nonlocal ticks
        while running:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(heartbeat())
    try:
        with pytest.raises(RuntimeError, match="download sentinel"):
            await pipeline.run("https://example.invalid/video")
    finally:
        running = False
        await task

    # A synchronous call would starve this task until the 200 ms download
    # returns.  Keep the threshold well below that delay for stable CI runs.
    assert ticks >= 5


def test_legacy_download_passes_stop_callback_to_worker():
    asyncio.run(_test_legacy_download_passes_stop_callback_to_worker())


async def _test_legacy_download_passes_stop_callback_to_worker():
    pipeline = VideoTranslationPipeline.__new__(VideoTranslationPipeline)
    controller = type("Controller", (), {"is_stopped": False, "pause_event": asyncio.Event()})()
    controller.pause_event.set()

    class StopAwareDownloader:
        def download(self, _video_input, *, cancel_check=None):
            controller.is_stopped = True
            assert cancel_check and cancel_check()
            raise RuntimeError("cancelled")

    pipeline.downloader = StopAwareDownloader()

    with pytest.raises(RuntimeError, match="cancelled"):
        await pipeline.run("https://example.invalid/video", task_controller=controller)
