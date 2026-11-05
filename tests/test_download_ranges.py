"""Offline contract tests for adaptive byte-range downloading.

The real Douyin service is never contacted.  Small thresholds make the range
engine exercise its probe and fallback paths with a deterministic in-memory
entity.
"""

from __future__ import annotations

import errno
import hashlib
from pathlib import Path

import pytest

from core import download_ranges as ranges
from core import download_worker as worker


ETAG = '"local-range-v1"'


class Response:
    def __init__(self, body=b"", *, status=200, headers=None, url="http://local/video.mp4"):
        self._body = bytes(body)
        self._cursor = 0
        self.status = status
        self.headers = {"Content-Type": "video/mp4", "ETag": ETAG,
                        "Content-Length": str(len(self._body)), **(headers or {})}
        self._request_url = url
        self.closed = False

    def iter_content(self, chunk_size=65536):
        while self._cursor < len(self._body):
            end = min(len(self._body), self._cursor + min(chunk_size, 17))
            chunk = self._body[self._cursor:end]
            self._cursor = end
            yield chunk

    def close(self):
        self.closed = True


def _range_response(payload, start, end, *, status=206, etag=ETAG,
                    content_range=None, body=None):
    body = payload[start:end + 1] if body is None else body
    return Response(body, status=status, headers={
                        "ETag": etag,
                        "Content-Range": content_range or f"bytes {start}-{end}/{len(payload)}",
                        "Content-Length": str(len(body)),
                    })


@pytest.fixture
def range_tuning(monkeypatch):
    monkeypatch.setattr(ranges, "MIN_REMAINING", 512)
    monkeypatch.setattr(ranges, "SAMPLE_BYTES", 128)
    monkeypatch.setattr(ranges, "SEGMENT_BYTES", 64)
    monkeypatch.setattr(ranges, "FAST_ENOUGH", 10**12)


def _adaptive(payload, opener, *, offset=0, etag=ETAG, observe=None):
    initial = Response(payload[offset:], status=200, headers={"ETag": etag},
                       url="http://local/video.mp4")
    return b"".join(bytes(chunk) for chunk in ranges.adaptive_chunks(
        initial,
        total=len(payload),
        offset=offset,
        etag=etag,
        opener=opener,
        observe=observe or (lambda *_args: None),
    ))


def test_parallel_ranges_preserve_order_and_exact_hash(range_tuning):
    payload = bytes((index * 37) % 251 for index in range(2048))
    calls = []

    def opener(_url, headers=None):
        calls.append(dict(headers or {}))
        first, last = headers["Range"][6:].split("-")
        start, end = int(first), int(last) if last else len(payload) - 1
        return _range_response(payload, start, end)

    result = _adaptive(payload, opener)
    assert result == payload
    assert hashlib.sha256(result).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert calls and all(call["If-Range"] == ETAG for call in calls)
    assert any(call["Range"].startswith("bytes=136-") for call in calls)


@pytest.mark.parametrize("status", [200, 403, 429])
def test_range_unsupported_or_http_failure_is_reported_without_append(range_tuning, status):
    payload = b"x" * 2048

    def opener(_url, headers=None):
        del headers
        return Response(payload, status=status, headers={"ETag": ETAG})

    with pytest.raises(ranges.RangeTransferError) as error:
        _adaptive(payload, opener)
    assert error.value.code == "range_ignored"
    assert error.value.status_code == status


def test_range_rate_limit_preserves_bounded_retry_after(range_tuning):
    payload = b"x" * 2048

    def opener(_url, headers=None):
        del headers
        return Response(payload, status=429,
                        headers={"Retry-After": "7"})

    with pytest.raises(ranges.RangeTransferError) as error:
        _adaptive(payload, opener)
    assert error.value.status_code == 429
    assert error.value.retry_after == 7

    def oversized(_url, headers=None):
        del headers
        return Response(payload, status=429,
                        headers={"Retry-After": "999999"})

    with pytest.raises(ranges.RangeTransferError) as error:
        _adaptive(payload, oversized)
    assert error.value.retry_after == 3601


def test_incorrect_content_range_is_rejected(range_tuning):
    payload = b"x" * 2048

    def opener(_url, headers=None):
        first, last = headers["Range"][6:].split("-")
        start, end = int(first), int(last) if last else len(payload) - 1
        return _range_response(payload, start, end,
                               content_range=f"bytes {start + 1}-{end + 1}/{len(payload)}")

    with pytest.raises(ranges.RangeTransferError) as error:
        _adaptive(payload, opener)
    assert error.value.code == "range_invalid"


def test_changed_etag_is_rejected(range_tuning):
    payload = b"x" * 2048

    def opener(_url, headers=None):
        first, last = headers["Range"][6:].split("-")
        return _range_response(payload, int(first), int(last), etag='"changed"')

    with pytest.raises(ranges.RangeTransferError) as error:
        _adaptive(payload, opener)
    assert error.value.code == "resource_changed"


def test_incomplete_range_is_rejected(range_tuning):
    payload = b"x" * 2048

    def opener(_url, headers=None):
        first, last = headers["Range"][6:].split("-")
        start, end = int(first), int(last)
        body = payload[start:end]  # one byte short
        return _range_response(payload, start, end, body=body)

    with pytest.raises(ranges.RangeTransferError) as error:
        _adaptive(payload, opener)
    assert error.value.code == "length_mismatch"


def test_corrupt_chunk_changes_integrity_even_when_length_matches(range_tuning):
    payload = b"x" * 2048

    def opener(_url, headers=None):
        first, last = headers["Range"][6:].split("-")
        start, end = int(first), int(last) if last else len(payload) - 1
        body = bytearray(payload[start:end + 1])
        body[0] ^= 0xFF
        return _range_response(payload, start, end, body=body)

    result = _adaptive(payload, opener)
    assert result != payload
    assert hashlib.sha256(result).hexdigest() != hashlib.sha256(payload).hexdigest()


def test_worker_disk_failure_is_not_retried_or_mislabeled(monkeypatch, tmp_path):
    payload = b"not a real video"
    response = Response(payload, headers={"Content-Length": str(len(payload))})
    monkeypatch.setattr(worker, "open_public_media", lambda *_args, **_kwargs: response)
    original_open = Path.open

    def fail_partial(path, mode="r", *args, **kwargs):
        if path.name.endswith(".mp4.part") and any(flag in mode for flag in ("w", "a", "+")):
            raise OSError(errno.ENOSPC, "synthetic disk full")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_partial)
    with pytest.raises(worker.MediaDownloadError) as error:
        worker.download_resolved_video(
            {
                "id": "7688769264395767049",
                "title": "Synthetic",
                "duration": 1,
                "formats": [{"urls": ["http://local/video.mp4"], "codec": "h264",
                             "width": 64, "height": 64, "bitrate": 1, "original": True}],
            },
            str(tmp_path / "video"),
            lambda _event: None,
        )
    assert error.value.code == "disk_full"
    assert response.closed
    assert not (tmp_path / "video.mp4").exists()
