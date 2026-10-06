"""Public-download acceptance using a real, tiny MP4 and offline HTTP doubles."""
import io
import json
import errno
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
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)


class MediaResponse(io.BytesIO):
    def __init__(self, data, *, status=200, content_type="video/mp4", length="auto", failure=None, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(len(data) if length == "auto" else length)
        self.headers.update(headers or {})
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
    progress = [event for event in events if event.get("phase") == "download"]
    assert any(event.get("code") == "resume_validators_missing" for event in events)
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
    progress = [event for event in events if event.get("phase") == "download"]
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
    responses = []
    def open_media(url):
        response = MediaResponse(mp4_bytes, length=len(mp4_bytes) + body_delta)
        responses.append(response)
        return response
    monkeypatch.setattr(worker, "open_public_media", open_media)
    with pytest.raises(worker.MediaDownloadError):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert not (work_directory / "video.mp4").exists()
    assert all(response.closed for response in responses)


def test_empty_body_never_publishes_a_video(monkeypatch, work_directory):
    responses = []
    def open_media(url):
        response = MediaResponse(b"")
        responses.append(response)
        return response
    monkeypatch.setattr(worker, "open_public_media", open_media)
    with pytest.raises(worker.MediaDownloadError):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert not (work_directory / "video.mp4").exists()
    assert all(response.closed for response in responses)


def test_interrupted_transfer_is_sanitized_and_not_published(monkeypatch, work_directory, mp4_bytes):
    responses = []
    def open_media(url):
        response = MediaResponse(mp4_bytes, failure=OSError("private-signature=session-secret"))
        responses.append(response)
        return response
    monkeypatch.setattr(worker, "open_public_media", open_media)
    with pytest.raises(worker.MediaDownloadError) as captured:
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert "private-signature" not in str(captured.value)
    assert "session-secret" not in str(captured.value)
    assert not (work_directory / "video.mp4").exists()
    assert all(response.closed for response in responses)


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
    assert calls == [urls[index % 3] for index in range(worker.MAX_MEDIA_RETRIES + 1)]
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


ETAG = '"source-original-123"'
MODIFIED = "Mon, 05 Oct 2026 04:00:00 GMT"


def ranged_response(data, start, *, total=None, etag=ETAG, headers=None, failure=None):
    return MediaResponse(data[start:], status=206, failure=failure, headers={
        "Content-Range": f"bytes {start}-{len(data) - 1}/{total or len(data)}",
        "ETag": etag, **(headers or {}),
    })


@pytest.mark.parametrize("validator", [{"ETag": ETAG}, {"Last-Modified": MODIFIED}])
def test_interruption_resumes_exact_bytes_same_original_mirrors(monkeypatch, work_directory, mp4_bytes, validator):
    calls, responses, events = [], [], []
    urls = [ORIGINAL_URL, ORIGINAL_URL + "&mirror=2"]

    def open_media(url, headers=None):
        calls.append((url, headers))
        if len(calls) == 1:
            response = MediaResponse(mp4_bytes, headers=validator, failure=TimeoutError("secret-url"))
        else:
            assert headers == {"Range": "bytes=512-", "If-Range": next(iter(validator.values()))}
            response = ranged_response(mp4_bytes, 512, etag=validator.get("ETag"), headers=validator)
        responses.append(response)
        return response

    monkeypatch.setattr(worker, "open_public_media", open_media)
    prefix = str(work_directory / "video")
    result = worker.download_resolved_video(video_info(urls=urls), prefix, events.append)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert [call[0] for call in calls] == urls
    retry = next(event for event in events if "kết nối lại" in event.get("stage", ""))
    assert retry["downloaded_bytes"] == 512 and retry["speed"] is None
    assert "giữ" in retry["stage"]
    assert any(event.get("code") == "read_timeout" for event in events)
    assert "secret-url" not in json.dumps(events)
    assert all(response.closed for response in responses)
    assert not Path(prefix + ".resume.json").exists()


def test_unknown_total_becomes_known_only_from_validated_range(monkeypatch, work_directory, mp4_bytes):
    responses = iter([
        MediaResponse(mp4_bytes, length=None, headers={"ETag": ETAG}, failure=OSError("reset")),
        ranged_response(mp4_bytes, 512),
    ])
    monkeypatch.setattr(worker, "open_public_media", lambda *args, **kwargs: next(responses))
    events = []
    result = worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    progress = [event for event in events if event.get("kind") == "progress" and event.get("phase") == "download"]
    assert progress[0]["total_bytes"] is None
    assert progress[-1]["total_bytes"] == len(mp4_bytes)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes


@pytest.mark.parametrize("validator", [{}, {"ETag": 'W/"weak-123"'}])
def test_no_strong_validator_verifies_entire_prefix_before_append(monkeypatch, work_directory, mp4_bytes, validator):
    calls, events = [], []

    def open_media(url, headers=None):
        calls.append(headers)
        assert headers is None, "Never issue a Range for an unverified entity"
        return MediaResponse(mp4_bytes, headers=validator, failure=OSError("reset") if len(calls) == 1 else None)

    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert len(calls) == 2
    assert any("Đã đối chiếu đúng" in event.get("stage", "") for event in events)
    counts = [e["downloaded_bytes"] for e in events if e.get("phase") == "download"]
    assert counts == sorted(counts), "Verified bytes must not fall back to zero"


def test_server_ignoring_range_200_verifies_old_partial(monkeypatch, work_directory, mp4_bytes):
    calls, events = [], []

    def open_media(url, headers=None):
        calls.append(headers)
        return MediaResponse(mp4_bytes, headers={"ETag": ETAG}, failure=OSError("reset") if len(calls) == 1 else None)

    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    assert calls == [None, {"Range": "bytes=512-", "If-Range": ETAG}]
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert any(event.get("code") == "range_ignored" for event in events)
    assert any("Đã đối chiếu đúng" in event.get("stage", "") for event in events)


def test_validatorless_checkpoint_survives_new_worker_and_chunk_boundary(monkeypatch, work_directory, mp4_bytes):
    prefix = str(work_directory / "video")
    monkeypatch.setattr(worker, "MAX_MEDIA_RETRIES", 0)
    monkeypatch.setattr(worker, "open_public_media", lambda url: MediaResponse(mp4_bytes, failure=TimeoutError()))
    with pytest.raises(worker.MediaDownloadError) as failed:
        worker.download_resolved_video(video_info(), prefix, lambda event: None)
    assert failed.value.resumable
    # Retain an unaligned prefix so the verification chunk also contains new bytes.
    partial, manifest = Path(prefix + ".mp4.part"), Path(prefix + ".resume.json")
    partial.write_bytes(mp4_bytes[:377])
    saved = json.loads(manifest.read_text())
    saved.update(downloaded_bytes=377, sha256=worker.hashlib.sha256(mp4_bytes[:377]).hexdigest())
    manifest.write_text(json.dumps(saved))
    monkeypatch.setattr(worker, "open_public_media", lambda url: MediaResponse(mp4_bytes))
    events = []
    result = worker.download_resolved_video(video_info(), prefix, events.append)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert any(e.get("downloaded_bytes") == 377 and "khôi phục" in e.get("stage", "") for e in events)
    assert any("Đã đối chiếu đúng" in e.get("stage", "") for e in events)


def test_validatorless_changed_prefix_is_never_joined(monkeypatch, work_directory, mp4_bytes):
    calls, events = [], []
    def open_media(url):
        calls.append(url)
        if len(calls) == 1:
            return MediaResponse(b"x" * 512 + mp4_bytes[512:], failure=TimeoutError())
        return MediaResponse(mp4_bytes)
    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    assert len(calls) == 3
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert any(e.get("code") == "resource_changed" for e in events)


def test_validatorless_resume_rejects_unsolicited_suffix(monkeypatch, work_directory, mp4_bytes):
    calls, events = [], []
    def open_media(url):
        calls.append(url)
        if len(calls) == 1:
            return MediaResponse(mp4_bytes, failure=TimeoutError())
        if len(calls) == 2:
            return ranged_response(mp4_bytes, 512)
        return MediaResponse(mp4_bytes)
    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    assert len(calls) == 3
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert any(e.get("code") == "range_invalid" for e in events)


def test_validatorless_resume_keeps_known_length_when_header_disappears(monkeypatch, work_directory, mp4_bytes):
    calls = []
    def open_media(url):
        calls.append(url)
        if len(calls) == 1:
            return MediaResponse(mp4_bytes, failure=TimeoutError())
        return MediaResponse(mp4_bytes[:-200], length=None)
    monkeypatch.setattr(worker, "open_public_media", open_media)
    monkeypatch.setattr(worker, "MAX_MEDIA_RETRIES", 1)
    with pytest.raises(worker.MediaDownloadError):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda e: None)
    assert not (work_directory / "video.mp4").exists()
    saved = json.loads((work_directory / "video.resume.json").read_text())
    assert saved["total_bytes"] == len(mp4_bytes)


@pytest.mark.parametrize("changed_headers", [
    {"ETag": '"different-file"'},
    {"ETag": None},
    {"Content-Range": "bytes 512-999/1000", "Content-Length": "488"},
])
def test_changed_resource_never_appends_and_restarts_full(monkeypatch, work_directory, mp4_bytes, changed_headers):
    calls, events = [], []

    def open_media(url, headers=None):
        calls.append(headers)
        if len(calls) == 1:
            return MediaResponse(mp4_bytes, headers={"ETag": ETAG}, failure=OSError("reset"))
        if len(calls) == 2:
            return ranged_response(mp4_bytes, 512, headers=changed_headers)
        assert headers is None
        return MediaResponse(mp4_bytes, headers={"ETag": '"different-file"'})

    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert len(calls) == 3
    assert any(event.get("code") == "resource_changed" for event in events)


@pytest.mark.parametrize("headers", [
    {"Content-Range": "bytes 511-1000/1001"},
    {"Content-Range": "bytes 512-511/1001"},
    {"Content-Range": "bytes 512-1001/1001"},
    {"Content-Range": "bytes 512-1000/*"},
    {"Content-Range": "malformed"},
    {"Content-Range": "bytes 512-" + "9" * 5000 + "/" + "9" * 5000},
    {"Content-Length": "100"},
])
def test_invalid_content_range_never_appends_untrusted_bytes(monkeypatch, work_directory, mp4_bytes, headers):
    calls = []
    prefix = str(work_directory / "video")

    def open_media(url, headers=None):
        calls.append(headers)
        if len(calls) == 1:
            return MediaResponse(mp4_bytes, headers={"ETag": ETAG}, failure=OSError("reset"))
        assert Path(prefix + ".mp4.part").read_bytes() == mp4_bytes[:512]
        if len(calls) == 2:
            return ranged_response(mp4_bytes, 512, headers=invalid_headers)
        return ranged_response(mp4_bytes, 512)

    invalid_headers = headers
    monkeypatch.setattr(worker, "open_public_media", open_media)
    events = []
    result = worker.download_resolved_video(video_info(), prefix, events.append)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert len(calls) == 3
    assert any(event.get("code") == "range_invalid" for event in events)


def save_interrupted_checkpoint(monkeypatch, work_directory, data):
    prefix = str(work_directory / "video")
    calls = []

    def open_media(url, headers=None):
        calls.append(headers)
        if len(calls) == 1:
            return MediaResponse(data, headers={"ETag": ETAG}, failure=TimeoutError("private-secret"))
        raise TimeoutError("private-secret")

    monkeypatch.setattr(worker, "open_public_media", open_media)
    with pytest.raises(worker.MediaDownloadError) as captured:
        worker.download_resolved_video(video_info(), prefix, lambda event: None)
    assert captured.value.resumable is True
    assert "private-secret" not in str(captured.value)
    assert len(calls) == worker.MAX_MEDIA_RETRIES + 1
    return prefix


def test_retry_exhaustion_persists_verified_checkpoint_and_next_job_resumes(monkeypatch, work_directory, mp4_bytes):
    prefix = save_interrupted_checkpoint(monkeypatch, work_directory, mp4_bytes)
    checkpoint_path = Path(prefix + ".resume.json")
    text = checkpoint_path.read_text(encoding="utf-8")
    checkpoint = json.loads(text)
    assert checkpoint["downloaded_bytes"] == 512
    assert checkpoint["total_bytes"] == len(mp4_bytes)
    assert checkpoint["video_id"] == VIDEO_ID
    assert "private" not in text and "https" not in text and "urls" not in text
    assert Path(prefix + ".mp4.part").read_bytes() == mp4_bytes[:512]
    calls = []

    def open_media(url, headers=None):
        calls.append(headers)
        assert headers == {"Range": "bytes=512-", "If-Range": ETAG}
        return ranged_response(mp4_bytes, 512)

    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(), prefix, lambda event: None)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert len(calls) == 1 and not checkpoint_path.exists()


def test_hard_stop_after_uncheckpointed_write_resumes_only_verified_prefix(monkeypatch, work_directory, mp4_bytes):
    prefix = save_interrupted_checkpoint(monkeypatch, work_directory, mp4_bytes)
    partial = Path(prefix + '.mp4.part')
    # The parent can retain this checkpoint without touching media. The worker
    # checks the same rendition and hash before discarding the uncommitted tail.
    with partial.open('ab') as handle:
        handle.write(b'uncommitted bytes at process exit')
    checkpoint, _ = worker._load_checkpoint(Path(prefix + '.resume.json'), partial)
    assert checkpoint['downloaded_bytes'] == 512 and partial.stat().st_size > 512

    def open_media(url, headers=None):
        assert headers == {'Range': 'bytes=512-', 'If-Range': ETAG}
        assert partial.stat().st_size == 512
        return ranged_response(mp4_bytes, 512)

    monkeypatch.setattr(worker, 'open_public_media', open_media)
    result = worker.download_resolved_video(video_info(), prefix, lambda event: None)
    assert Path(result['file_path']).read_bytes() == mp4_bytes


def test_checkpoint_is_published_during_healthy_transfer_before_process_stop(monkeypatch, work_directory, mp4_bytes):
    prefix = str(work_directory / 'video')

    class HardStop(BaseException):
        pass

    class InterruptedResponse(MediaResponse):
        def iter_content(self, chunk_size):
            yield mp4_bytes[:512]
            saved = json.loads(Path(prefix + '.resume.json').read_text())
            assert saved['downloaded_bytes'] == 512
            raise HardStop()

    monkeypatch.setattr(worker, 'open_public_media', lambda url: InterruptedResponse(mp4_bytes, headers={'ETag': ETAG}))
    with pytest.raises(HardStop):
        worker.download_resolved_video(video_info(), prefix, lambda event: None)
    saved, _ = worker._load_checkpoint(Path(prefix + '.resume.json'), Path(prefix + '.mp4.part'))
    assert saved['downloaded_bytes'] == 512


@pytest.mark.parametrize("damage", ["hash", "size", "stale", "format", "video", "malformed", "oversized", "validator"])
def test_invalid_checkpoint_restarts_instead_of_appending(monkeypatch, work_directory, mp4_bytes, damage):
    prefix = save_interrupted_checkpoint(monkeypatch, work_directory, mp4_bytes)
    checkpoint_path, partial = Path(prefix + ".resume.json"), Path(prefix + ".mp4.part")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if damage == "hash":
        partial.write_bytes(b"x" * 512)
    elif damage == "size":
        partial.write_bytes(mp4_bytes[:100])
    elif damage == "stale":
        checkpoint["updated_at"] = 1
    elif damage == "format":
        checkpoint["format_identity"] = "0" * 64
    elif damage == "video":
        checkpoint["video_id"] = "1234567890123456789"
    elif damage == "validator":
        checkpoint["etag"] = 'W/"invalid-weak-validator"'
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    if damage == "malformed":
        checkpoint_path.write_text("[", encoding="utf-8")
    elif damage == "oversized":
        checkpoint_path.write_text(" " * (worker.MAX_CHECKPOINT_BYTES + 1), encoding="utf-8")
    calls, events = [], []

    def open_media(url, headers=None):
        calls.append(headers)
        assert headers is None
        return MediaResponse(mp4_bytes, headers={"ETag": ETAG})

    monkeypatch.setattr(worker, "open_public_media", open_media)
    result = worker.download_resolved_video(video_info(), prefix, events.append)
    assert Path(result["file_path"]).read_bytes() == mp4_bytes
    assert len(calls) == 1
    assert any(event.get("code") == "checkpoint_invalid" for event in events)


@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EACCES, errno.EIO])
def test_local_write_error_is_not_retried_or_blamed_on_network(monkeypatch, work_directory, mp4_bytes, error_number):
    calls, events = [], []
    original_open = Path.open

    def failing_open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".mp4.part") and mode in ("wb", "ab"):
            raise OSError(error_number, "private-path-secret")
        return original_open(path, mode, *args, **kwargs)

    def open_media(url):
        response = MediaResponse(mp4_bytes, headers={"ETag": ETAG})
        calls.append(response)
        return response

    monkeypatch.setattr(Path, "open", failing_open)
    monkeypatch.setattr(worker, "open_public_media", open_media)
    with pytest.raises(worker.MediaDownloadError) as captured:
        worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    assert len(calls) == 1 and calls[0].closed
    assert not captured.value.resumable
    assert "ổ đĩa" in str(captured.value).lower()
    assert "mạng" not in str(captured.value) and "private-path-secret" not in str(captured.value)
    assert not any("kết nối lại" in event.get("stage", "") for event in events)


def test_diagnostic_uses_safe_http_status_and_no_exception_text(monkeypatch, work_directory):
    def open_media(url):
        raise worker.DouyinResolveError("private-url-token", code="http_error", status_code=503)

    monkeypatch.setattr(worker, "open_public_media", open_media)
    events = []
    with pytest.raises(worker.MediaDownloadError, match="HTTP 503"):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    diagnostics = [event for event in events if event["kind"] == "diagnostic"]
    assert len(diagnostics) == worker.MAX_MEDIA_RETRIES + 1
    assert all(event["status_code"] == 503 and event["code"] == "http_error" for event in diagnostics)
    assert "private-url-token" not in json.dumps(events)


def test_main_emits_resumable_flag_only_for_validated_checkpoint(monkeypatch, work_directory, capsys):
    prefix = str(work_directory / "video")
    monkeypatch.setattr(worker.sys, "argv", ["download_worker.py", VIDEO_URL, prefix])
    monkeypatch.setattr(worker, "resolve_douyin", lambda *args: video_info())

    def fail_transfer(*args):
        raise worker.MediaDownloadError("Đã giữ phần tải.", resumable=True)

    monkeypatch.setattr(worker, "download_resolved_video", fail_transfer)
    assert worker.main() == 1
    assert read_events(capsys) == [{"kind": "error", "message": "Đã giữ phần tải.", "resumable": True}]


def test_backoff_is_bounded_and_cancellation_is_not_swallowed(monkeypatch, work_directory, mp4_bytes):
    waits = []

    def unavailable(url):
        raise TimeoutError("private-server")

    monkeypatch.setattr(worker, "open_public_media", unavailable)
    monkeypatch.setattr(worker.time, "sleep", waits.append)
    with pytest.raises(worker.MediaDownloadError):
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert waits == [0.5, 1.0, 2.0, 4.0, 4.0]
    response = MediaResponse(mp4_bytes, headers={"ETag": ETAG}, failure=KeyboardInterrupt())
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    with pytest.raises(KeyboardInterrupt):
        worker.download_resolved_video(video_info(), str(work_directory / "cancelled"), lambda event: None)
    assert response.closed
    assert not (work_directory / "cancelled.mp4").exists()
    assert waits == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_checkpoint_disk_error_has_no_resumable_claim(monkeypatch, work_directory, mp4_bytes):
    response = MediaResponse(mp4_bytes, headers={"ETag": ETAG}, failure=TimeoutError())
    monkeypatch.setattr(worker, "open_public_media", lambda url: response)
    original_open = Path.open

    def failing_open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".resume.json.tmp"):
            raise OSError(errno.ENOSPC, "private-location")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(worker.MediaDownloadError) as captured:
        worker.download_resolved_video(video_info(), str(work_directory / "video"), lambda event: None)
    assert not captured.value.resumable and captured.value.code == "disk_full"
    assert "private-location" not in str(captured.value)
    assert response.closed
    assert not (work_directory / "video.resume.json").exists()


def test_main_emits_disk_cause_for_diagnostics(monkeypatch, work_directory, capsys):
    prefix = str(work_directory / "video")
    monkeypatch.setattr(worker.sys, "argv", ["download_worker.py", VIDEO_URL, prefix])
    monkeypatch.setattr(worker, "resolve_douyin", lambda *args: video_info())

    def fail_transfer(*args):
        raise worker._disk_error(OSError(errno.ENOSPC, "private-path"))

    monkeypatch.setattr(worker, "download_resolved_video", fail_transfer)
    assert worker.main() == 1
    events = read_events(capsys)
    assert events[0]["kind"] == "diagnostic" and events[0]["code"] == "disk_full"
    assert events[1]["kind"] == "error" and "resumable" not in events[1]
    assert "private-path" not in json.dumps(events)


def test_resolver_failure_retains_validated_source_checkpoint_without_rendition_fallback(monkeypatch, work_directory, mp4_bytes, capsys):
    prefix = save_interrupted_checkpoint(monkeypatch, work_directory, mp4_bytes)
    before = {suffix: Path(prefix + suffix).read_bytes() for suffix in (".mp4.part", ".resume.json")}
    monkeypatch.setattr(worker.sys, "argv", ["download_worker.py", VIDEO_URL, prefix])
    error = worker.DouyinResolveError("private-server-url", code="http_error", status_code=503, error_type="HTTPError")
    def resolve(*args):
        raise error
    monkeypatch.setattr(worker, "resolve_douyin", resolve)
    assert worker.main() == 1
    events = read_events(capsys)
    assert events[-1]["kind"] == "error" and events[-1]["resumable"] is True
    assert not any(event.get("source") == "yt-dlp" for event in events)
    diagnostic = next(event for event in events if event["kind"] == "diagnostic")
    assert diagnostic["downloaded_bytes"] == 512
    assert diagnostic["status_code"] == 503 and diagnostic["error_type"] == "HTTPError"
    assert "private-server-url" not in json.dumps(events)
    assert all(Path(prefix + suffix).read_bytes() == data for suffix, data in before.items())


@pytest.mark.parametrize("damage", ["missing", "hash", "stale", "oversized", "malformed"])
def test_resolver_failure_never_hands_unverified_partial_to_ytdlp(monkeypatch, work_directory, mp4_bytes, capsys, damage):
    prefix = save_interrupted_checkpoint(monkeypatch, work_directory, mp4_bytes)
    path = Path(prefix + ".resume.json")
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if damage == "hash":
        Path(prefix + ".mp4.part").write_bytes(b"x" * 512)
    elif damage == "missing":
        path.unlink()
    elif damage == "stale":
        checkpoint["updated_at"] = 1
        path.write_text(json.dumps(checkpoint), encoding="utf-8")
    elif damage == "oversized":
        path.write_text(" " * (worker.MAX_CHECKPOINT_BYTES + 1), encoding="utf-8")
    else:
        path.write_text("[", encoding="utf-8")
    monkeypatch.setattr(worker.sys, "argv", ["download_worker.py", VIDEO_URL, prefix])
    def resolve(*args):
        raise worker.DouyinResolveError("Unavailable")
    monkeypatch.setattr(worker, "resolve_douyin", resolve)
    assert worker.main() == 1
    events = read_events(capsys)
    assert events[-1]["kind"] == "error" and "resumable" not in events[-1]
    assert not any(event.get("source") == "yt-dlp" for event in events)


def test_diagnostic_preserves_wrapped_cause_status_and_exact_bytes():
    events = []
    error = worker.DouyinResolveError("private-data", code="read_timeout", status_code=206, error_type="TimeoutError")
    worker._diagnostic(events.append, error, 2, downloaded_bytes=87654321)
    assert events == [{"kind": "diagnostic", "source": "douyin-public", "code": "read_timeout",
                       "error_type": "TimeoutError", "attempt": 3, "downloaded_bytes": 87654321, "status_code": 206}]
    error.error_type, error.status_code = "private-url-token", "private-url-token"
    worker._diagnostic(events.append, error, 0)
    assert "private" not in json.dumps(events)
    assert events[-1]["error_type"] == "DouyinResolveError" and "status_code" not in events[-1]


def test_transfer_diagnostic_reports_bytes_already_written(monkeypatch, work_directory, mp4_bytes):
    calls, events = [], []
    def open_media(url, headers=None):
        calls.append(headers)
        if len(calls) == 1:
            return MediaResponse(mp4_bytes, headers={"ETag": ETAG}, failure=worker.DouyinResolveError(
                "private", code="read_timeout", error_type="TimeoutError"))
        return ranged_response(mp4_bytes, 512)
    monkeypatch.setattr(worker, "open_public_media", open_media)
    worker.download_resolved_video(video_info(), str(work_directory / "video"), events.append)
    diagnostic = next(event for event in events if event["kind"] == "diagnostic")
    assert diagnostic["downloaded_bytes"] == 512 and diagnostic["error_type"] == "TimeoutError"
