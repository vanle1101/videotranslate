"""Real thumbnail decoding plus bounded cache/cancellation failure regression."""
import asyncio
import hashlib
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
import core.history_thumbnail as thumbnail
from config import settings
from core.streaming.pipeline import StreamingPipelineSession
from core.streaming.video_history import group_video_history


def make_video(path, *, color="blue", size="160x240", duration=.5):
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        f"color=c={color}:s={size}:d={duration}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], capture_output=True, check=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return path


@pytest.fixture
def source(tmp_path):
    return make_video(tmp_path / "video tiếng Việt.mp4")


@pytest.fixture
def api(tmp_path, monkeypatch, source):
    for name in ("BASE_DIR", "WORKSPACE_DIR", "INPUT_DIR", "OUTPUT_DIR", "TEMP_DIR"):
        directory = tmp_path if name == "BASE_DIR" else tmp_path / name
        directory.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, directory)
    cache = thumbnail.HistoryThumbnailCache()
    monkeypatch.setattr(main, "history_thumbnail_cache", cache)
    monkeypatch.setattr(main, "active_streaming_sessions", {})
    with TestClient(main.app) as client:
        yield client, cache
    cache.clear()


def test_real_jpeg_preserves_whole_frame_and_short_source(source):
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    cache = thumbnail.HistoryThumbnailCache()
    image, version = cache.get(source)
    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (240, 160, 3)
    assert len(image) > 100 and len(version) == 24
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert list(source.parent.iterdir()) == [source]


@pytest.mark.parametrize("size,shape", [("640x360", (180, 320)), ("480x800", (320, 192))])
def test_large_landscape_and_portrait_frame_fit_without_cropping(tmp_path, size, shape):
    source = make_video(tmp_path / "larger.mp4", size=size, duration=2)
    image, _ = thumbnail.HistoryThumbnailCache().get(source)
    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == shape


def test_real_api_uses_live_session_and_etag_without_media_path_in_request(api, source):
    client, _ = api
    main.active_streaming_sessions["live-cover"] = SimpleNamespace(video_path=source)
    version = thumbnail.source_signature(source)[1]
    url = f"/api/history/thumbnail/live-cover?v={version}"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR) is not None
    repeat = client.get(url, headers={"If-None-Match": response.headers["etag"]})
    assert repeat.status_code == 304 and not repeat.content


def test_saved_source_resolves_without_restore_start_or_manifest_write(api, source, monkeypatch):
    client, _ = api
    project = StreamingPipelineSession("saved-cover", source)
    manifest = project.persist()
    before = manifest.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("cover must never restore/resume/start a project")
    monkeypatch.setattr(main, "restore_saved_session", forbidden)
    monkeypatch.setattr(StreamingPipelineSession, "start", forbidden)
    response = client.get("/api/history/thumbnail/saved-cover")
    assert response.status_code == 200
    assert manifest.read_bytes() == before and not main.active_streaming_sessions


def test_missing_and_invalid_project_or_asset_never_return_placeholder(api, source):
    client, _ = api
    assert client.get("/api/history/thumbnail/unknown").status_code == 404
    assert client.get("/api/history/thumbnail/not.valid").status_code == 400
    main.active_streaming_sessions["missing"] = SimpleNamespace(video_path=source.with_name("missing.mp4"))
    assert client.get("/api/history/thumbnail/missing").status_code == 404
    bad = source.with_name("broken.mp4")
    bad.write_bytes(b"this is not a video")
    main.active_streaming_sessions["corrupt"] = SimpleNamespace(video_path=bad)
    result = client.get("/api/history/thumbnail/corrupt")
    assert result.status_code == 422 and "giải mã" in result.json()["detail"]
    source.unlink()
    assert client.get("/api/history/thumbnail/missing").status_code == 404


@pytest.mark.parametrize("value", ["blob:any", "https://example.com/a.mp4", "file:///tmp/a.mp4", "../source.mp4", "//host/video.mp4"])
def test_url_and_unretained_relative_paths_rejected(value):
    with pytest.raises(thumbnail.ThumbnailError):
        thumbnail.source_signature(value)


def test_group_cover_fallback_does_not_change_representative_status(source):
    tasks = [
        {"task_id": "old", "video_path": str(source), "source_url": "https://www.douyin.com/video/7692745161054506290", "updated_at": 1},
        {"task_id": "new", "video_path": str(source.with_name("missing.mp4")),
         "source_url": "https://www.douyin.com/jingxuan?modal_id=7692745161054506290", "updated_at": 2,
         "status": "FAILED", "stage": "Thiếu video gốc"},
        {"task_id": "other", "video_path": str(source.with_name("another-missing.mp4")), "updated_at": 3},
    ]
    result = thumbnail.attach_history_thumbnails(group_video_history(tasks), tasks)
    row = next(row for row in result if row["session_id"] == "new")
    assert row["thumbnail_url"].startswith("/api/history/thumbnail/old?v=")
    assert row["status"] == "FAILED" and row["stage"] == "Thiếu video gốc"
    assert next(row for row in result if row["session_id"] == "other")["thumbnail_url"] == ""


def test_simultaneous_real_requests_decode_once_then_cache(source, monkeypatch):
    cache = thumbnail.HistoryThumbnailCache()
    original = thumbnail.run_media
    calls = []
    def counted(command, **kwargs):
        calls.append(command)
        return original(command, **kwargs)
    monkeypatch.setattr(thumbnail, "run_media", counted)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: cache.get(source), range(4)))
    # The short source requires a real second seek at 0; no extra concurrent
    # decodes occur after the first successful JPEG is cached.
    assert len(calls) == 2 and len({row[1] for row in results}) == 1
    assert cache.get(source) == results[0] and len(calls) == 2


def test_source_replacement_invalidates_byte_cache_and_old_browser_url(api, source):
    client, _ = api
    main.active_streaming_sessions["changing"] = SimpleNamespace(video_path=source)
    version = thumbnail.source_signature(source)[1]
    url = f"/api/history/thumbnail/changing?v={version}"
    before = client.get(url)
    replacement = make_video(source.with_name("replacement.mp4"), color="red")
    info = source.stat()
    os.replace(replacement, source)
    os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
    after = client.get(url, headers={"If-None-Match": before.headers["etag"]})
    assert after.status_code == 200 and after.content != before.content
    assert after.headers["etag"] != before.headers["etag"]
    assert after.headers["cache-control"] == "no-cache"
    source.unlink()
    assert client.get(url).status_code == 404  # cached bytes cannot hide deletion


def test_lru_eviction_respects_byte_and_entry_limit(source):
    cache = thumbnail.HistoryThumbnailCache(max_entries=1, max_bytes=8192)
    first = cache.get(source)
    second_source = make_video(source.with_name("second.mp4"), color="red", duration=2)
    second = cache.get(second_source)
    assert len(cache._cache) == 1 and first[1] not in cache._cache and second[1] in cache._cache
    assert 0 < cache._bytes <= cache.max_bytes


def test_queue_deadline_and_cancel_do_not_start_another_decoder(source):
    cache = thumbnail.HistoryThumbnailCache(timeout=.15)
    assert cache._decoder.acquire(timeout=.1)
    try:
        began = time.monotonic()
        with pytest.raises(thumbnail.ThumbnailError) as error:
            cache.get(source)
        assert error.value.status_code == 503 and time.monotonic() - began < .5
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(thumbnail.ThumbnailError) as error:
            cache.get(source, cancel=cancelled)
        assert error.value.status_code == 499
    finally:
        cache._decoder.release()


def test_real_ffmpeg_cancellation_reaps_process(source, monkeypatch):
    cache = thumbnail.HistoryThumbnailCache(timeout=2)
    original = thumbnail.run_media
    reached = threading.Event()
    processes = []
    original_popen = subprocess.Popen
    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        reached.set()
        return process
    def long_decode(command, **kwargs):
        return original([
            "ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i",
            "color=c=blue:s=160x240:d=30", "-f", "null", "-",
        ], **kwargs)
    monkeypatch.setattr(thumbnail, "run_media", long_decode)
    monkeypatch.setattr(subprocess, "Popen", popen)
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(cache.get, source, cancel=cancel)
        assert reached.wait(2)
        cancel.set()
        with pytest.raises(thumbnail.ThumbnailError) as error:
            work.result(timeout=3)
        assert error.value.status_code == 499
    assert processes and all(process.poll() is not None for process in processes)
    assert not cache._cache


def test_thumbnail_request_does_not_block_health(api, source, monkeypatch):
    _, cache = api
    main.active_streaming_sessions["offloop"] = SimpleNamespace(video_path=source)
    entered, release = threading.Event(), threading.Event()
    original = cache.get
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(cache, "get", blocked)
    async def never_disconnected():
        return False
    request = SimpleNamespace(is_disconnected=never_disconnected, headers={})
    async def run():
        work = asyncio.create_task(main.history_thumbnail("offloop", request))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            assert (await asyncio.wait_for(main.health(), .2))["status"] == "ok"
        finally:
            release.set()
        assert (await work).status_code == 200
    asyncio.run(run())


def test_api_overload_rejected_before_decode_and_request_slot_released(api, source, monkeypatch):
    client, cache = api
    main.active_streaming_sessions["busy"] = SimpleNamespace(video_path=source)
    for _ in range(8):
        assert cache.acquire_request()
    try:
        response = client.get("/api/history/thumbnail/busy")
        assert response.status_code == 503
        assert not cache._cache
    finally:
        for _ in range(8):
            cache.release_request()
    assert client.get("/api/history/thumbnail/busy").status_code == 200
    assert cache.acquire_request()
    cache.release_request()


def test_async_api_cancel_reaps_real_ffmpeg_before_returning(api, source, monkeypatch):
    _, cache = api
    main.active_streaming_sessions["cancel-api"] = SimpleNamespace(video_path=source)
    original_run, original_popen = thumbnail.run_media, subprocess.Popen
    reached, exited = threading.Event(), threading.Event()
    processes = []
    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        reached.set()
        return process
    def long_decode(command, **kwargs):
        try:
            return original_run([
                "ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i",
                "color=c=blue:s=160x240:d=30", "-f", "null", "-",
            ], **kwargs)
        finally:
            exited.set()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(thumbnail, "run_media", long_decode)
    async def connected():
        return False
    request = SimpleNamespace(is_disconnected=connected, headers={})
    async def run():
        work = asyncio.create_task(main.history_thumbnail("cancel-api", request))
        for _ in range(100):
            if reached.is_set():
                break
            await asyncio.sleep(.01)
        assert reached.is_set()
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(work, 3)
        assert exited.is_set() and all(process.poll() is not None for process in processes)
    asyncio.run(run())
    assert not cache._cache


def test_real_ffmpeg_timeout_reaps_and_missing_output_never_cached(source, monkeypatch):
    cache = thumbnail.HistoryThumbnailCache(timeout=.2)
    original_run, original_popen = thumbnail.run_media, subprocess.Popen
    processes = []
    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process
    def long_decode(command, **kwargs):
        return original_run([
            "ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i",
            "color=c=blue:s=160x240:d=30", "-f", "null", "-",
        ], **kwargs)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(thumbnail, "run_media", long_decode)
    with pytest.raises(thumbnail.ThumbnailError) as error:
        cache.get(source)
    assert error.value.status_code == 504
    assert processes and all(process.poll() is not None for process in processes)
    assert not cache._cache
    monkeypatch.setattr(thumbnail, "run_media", lambda *args, **kwargs: b"")
    with pytest.raises(thumbnail.ThumbnailError) as error:
        cache.get(source)
    assert error.value.status_code == 422 and not cache._cache
