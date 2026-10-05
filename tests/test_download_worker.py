"""Public-download acceptance using a real, tiny MP4 and offline HTTP doubles."""
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import download_worker as worker


VIDEO_ID = "7688769264395767049"
VIDEO_URL = f"https://www.douyin.com/video/{VIDEO_ID}"
ORIGINAL_URL = "https://v3-source.zjcdn.com/original.mp4?sign=private-signature"
RENDITION_URL = "https://v26-web.douyinvod.com/rendition.mp4"


@pytest.fixture(scope="module")
def mp4_bytes():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for media acceptance")
    with tempfile.TemporaryDirectory(prefix="studio-worker-fixture-") as directory:
        output = Path(directory) / "sample.mp4"
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if worker.os.name == "nt" else {}
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=10:d=0.5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", "-shortest", str(output),
        ], check=True, capture_output=True, timeout=20, **flags)
        yield output.read_bytes()


@pytest.fixture
def work_directory():
    with tempfile.TemporaryDirectory(prefix="studio-download-worker-") as directory:
        yield Path(directory)


@pytest.fixture(autouse=True)
def no_live_downloads(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Test attempted an unmocked Internet request")

    monkeypatch.setattr(worker, "open_public_media", unexpected)
    monkeypatch.setattr(worker, "resolve_douyin", unexpected)
    monkeypatch.setattr(worker, "single_video_downloader", unexpected)


class MediaResponse(io.BytesIO):
    def __init__(self, data, *, status=200, content_type="video/mp4", length="auto", failure=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(len(data) if length == "auto" else length)
        self.failure = failure

    def iter_content(self, chunk_size=65536):
        while chunk := self.read(min(chunk_size, 512)):
            yield chunk
            if self.failure:
                raise self.failure


def video_info(*, original=True, urls=None, duration=0.5):
    return {
        "id": VIDEO_ID, "title": "Sample Douyin video", "duration": duration,
        "formats": [
            {"urls": [RENDITION_URL], "codec": "h264", "width": 3840, "height": 2160,
             "size": 900, "bitrate": 20_000_000, "original": False},
            {"urls": urls or [ORIGINAL_URL], "codec": "h264", "width": 1920, "height": 1080,
             "size": 400, "bitrate": 1_000_000, "original": original},
        ],
    }


def test_original_download_preserves_bytes_and_reports_measured_progress(monkeypatch, work_directory, mp4_bytes):
    response = MediaResponse(mp4_bytes)
    calls, events = [], []

    def open_media(url):
        calls.append(url)
        return response

    monkeypatch.setattr(worker, "open_public_media", open_media)
    ticks = iter(index / 4 for index in range(1000))
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))
    result = worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    output = Path(result["file_path"])
    assert calls == [ORIGINAL_URL], "Original must win even over a larger advertised rendition"
    assert output.read_bytes() == mp4_bytes
    assert not output.with_suffix(".mp4.part").exists()
    assert 0 < result["duration"] < 1
    assert result["title"] == "Sample Douyin video" and result["source"] == "douyin-public"
    assert response.closed
    progress = [event for event in events if event["phase"] == "download"]
    assert progress[0]["downloaded_bytes"] == 0
    assert progress[-1]["downloaded_bytes"] == progress[-1]["total_bytes"] == len(mp4_bytes)
    assert progress[-1]["progress_pct"] == 100
    assert any(0 < event["progress_pct"] < 100 for event in progress)
    assert all(event["total_bytes"] == len(mp4_bytes) for event in progress)
    assert all("bản gốc" in event["stage"] for event in progress)
    assert events[-1]["phase"] == "prepare"
    assert "private-signature" not in json.dumps(events)


@pytest.mark.parametrize("length", [None, "unknown", "0"])
def test_unknown_transfer_total_does_not_invent_a_percentage(monkeypatch, work_directory, mp4_bytes, length):
    response = MediaResponse(mp4_bytes, length=length)
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    events = []
    worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    progress = [event for event in events if event["phase"] == "download"]
    assert all(event["total_bytes"] is None and event["progress_pct"] is None for event in progress)
    assert progress[-1]["downloaded_bytes"] == len(mp4_bytes)


def test_without_original_uses_best_available_rendition(monkeypatch, work_directory, mp4_bytes):
    response = MediaResponse(mp4_bytes)
    calls, events = [], []

    def open_media(url):
        calls.append(url)
        return response

    monkeypatch.setattr(worker, "open_public_media", open_media)
    worker.download_resolved_video(video_info(original=False), str(work_directory / "video"), events.append)
    assert calls == [RENDITION_URL]
    assert all("bản gốc" not in event.get("stage", "") for event in events)


@pytest.mark.parametrize("body_delta", [-1, 1])
def test_content_length_mismatch_never_publishes_completed_file(monkeypatch, work_directory, mp4_bytes, body_delta):
    response = MediaResponse(mp4_bytes, length=len(mp4_bytes) + body_delta)
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    with pytest.raises(worker.MediaDownloadError):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert not (work_directory / "video.mp4").exists()
    assert response.closed


def test_empty_body_never_publishes_a_video(monkeypatch, work_directory):
    response = MediaResponse(b"")
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    with pytest.raises(worker.MediaDownloadError):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert not (work_directory / "video.mp4").exists()
    assert response.closed


def test_interrupted_transfer_is_sanitized_and_not_published(monkeypatch, work_directory, mp4_bytes):
    response = MediaResponse(mp4_bytes, failure=OSError("private-signature=session-secret"))
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    with pytest.raises(worker.MediaDownloadError) as captured:
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert "private-signature" not in str(captured.value)
    assert "session-secret" not in str(captured.value)
    assert not (work_directory / "video.mp4").exists()
    assert response.closed


def test_html_disguised_as_video_is_rejected_by_real_ffprobe(monkeypatch, work_directory, mp4_bytes):
    response = MediaResponse(b"<html>private-signature: please sign in</html>")
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    with pytest.raises(worker.MediaDownloadError) as captured:
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert "video hoàn chỉnh" in str(captured.value)
    assert "private-signature" not in str(captured.value)
    assert not (work_directory / "video.mp4").exists()
    assert response.closed


def test_duration_mismatch_rejects_different_video(monkeypatch, work_directory, mp4_bytes):
    response = MediaResponse(mp4_bytes)
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    with pytest.raises(worker.MediaDownloadError, match="Thời lượng"):
        worker.download_resolved_video(video_info(duration=3600), str(work_directory / "video"), lambda event: None)
    assert not (work_directory / "video.mp4").exists()
    assert response.closed


def test_candidate_failover_keeps_original_and_closes_failed_response(monkeypatch, work_directory, mp4_bytes):
    urls = [ORIGINAL_URL, ORIGINAL_URL + "&mirror=2", ORIGINAL_URL + "&mirror=3"]
    bad_response = MediaResponse(b"<html>unavailable</html>", content_type="text/html")
    good_response = MediaResponse(mp4_bytes)
    calls = []

    def open_media(url):
        calls.append(url)
        if len(calls) == 1:
            raise worker.DouyinResolveError("Mirror unavailable")
        return bad_response if len(calls) == 2 else good_response

    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(urls=urls), str(work_directory / "video"), lambda event: None)
    assert calls == urls
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert bad_response.closed and good_response.closed


def test_exhausted_original_mirrors_do_not_silently_download_rendition(monkeypatch, work_directory):
    urls = [ORIGINAL_URL + f"&mirror={index}" for index in range(5)]
    calls = []

    def open_media(url):
        calls.append(url)
        raise worker.DouyinResolveError("Unavailable private-signature")

    monkeypatch.setattr(worker, "open_public_media", open_media)
    with pytest.raises(worker.MediaDownloadError) as captured:
        worker.download_resolved_video(video_info(urls=urls), str(work_directory / "video"), lambda event: None)
    assert calls == urls[:3]
    assert RENDITION_URL not in calls
    assert "private-signature" not in str(captured.value)
    assert list(work_directory.iterdir()) == []


def test_insufficient_space_closes_response_before_writing(monkeypatch, work_directory, mp4_bytes):
    response = MediaResponse(mp4_bytes)
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(worker.MediaDownloadError, match="Không đủ dung lượng"):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert response.closed and list(work_directory.iterdir()) == []


def read_events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_worker_public_success_emits_result_without_entering_fallback(monkeypatch, work_directory, capsys):
    prefix = str(work_directory / "video")
    monkeypatch.setattr(worker.sys, "argv", ["download_worker.py", VIDEO_URL, prefix])
    info = video_info()

    def resolve(url, progress):
        assert url == VIDEO_URL
        progress({"phase": "resolve", "stage": "Resolving", "progress_pct": None})
        return info

    def transfer(actual_info, actual_prefix, send):
        assert actual_info is info and actual_prefix == prefix
        return {"file_path": prefix + ".mp4", "duration": 0.5, "source": "douyin-public"}

    monkeypatch.setattr(worker, "resolve_douyin", resolve)
    monkeypatch.setattr(worker, "download_resolved_video", transfer)
    assert worker.main() == 0
    events = read_events(capsys)
    assert events[0]["kind"] == "progress" and events[0]["source"] == "douyin-public"
    assert events[-1]["kind"] == "result" and events[-1]["result"]["source"] == "douyin-public"


def test_worker_resolver_failure_enters_existing_fallback(monkeypatch, work_directory, mp4_bytes, capsys):
    prefix = str(work_directory / "video")
    monkeypatch.setattr(worker.sys, "argv", ["download_worker.py", VIDEO_URL, prefix])

    def resolve(*args):
        raise worker.DouyinResolveError("private-signature should never be logged")

    closed = []

    class Fallback:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def extract_info(self, url, download):
            assert url == VIDEO_URL and download is True
            Path(prefix + ".mp4").write_bytes(mp4_bytes)
            return {"id": VIDEO_ID, "title": "Fallback video", "duration": 0.5}

        def prepare_filename(self, info):
            return prefix + ".mp4"

    def fallback(options, url):
        assert url == VIDEO_URL and options["noplaylist"] is True
        assert "cookiesfrombrowser" not in options
        return Fallback()

    monkeypatch.setattr(worker, "resolve_douyin", resolve)
    monkeypatch.setattr(worker, "single_video_downloader", fallback)
    assert worker.main() == 0
    events = read_events(capsys)
    assert any(event.get("source") == "yt-dlp" for event in events)
    assert events[-1]["kind"] == "result"
    assert "private-signature" not in json.dumps(events)
    assert closed


def test_worker_transfer_failure_does_not_redownload_lower_quality(monkeypatch, work_directory, capsys):
    prefix = str(work_directory / "video")
    monkeypatch.setattr(worker.sys, "argv", ["download_worker.py", VIDEO_URL, prefix])
    monkeypatch.setattr(worker, "resolve_douyin", lambda *args: video_info())

    def fail_transfer(*args):
        raise worker.MediaDownloadError("Không tải đủ bản gốc.")

    monkeypatch.setattr(worker, "download_resolved_video", fail_transfer)
    assert worker.main() == 1
    events = read_events(capsys)
    assert events == [{"kind": "error", "message": "Không tải đủ bản gốc."}]
