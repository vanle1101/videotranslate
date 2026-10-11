"""Real source-video covers for history, with bounded decoding and RAM cache.

This module never restores a project, creates an output file, or starts a
background worker. The API runs its blocking methods outside the event loop.
"""
import hashlib
import re
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path

from core.media_process import run_media


SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
                            ".mpeg", ".mpg", ".ts", ".m2ts", ".wmv"})
THUMBNAIL_VERSION = "whole-frame-jpeg-1"


class ThumbnailError(RuntimeError):
    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def source_signature(value):
    """Only accept a retained regular local video; include replacement identity."""
    if not value:
        raise ThumbnailError("Không còn video gốc để tạo ảnh xem trước.", 404)
    value = str(value)
    if "\x00" in value or value.startswith(("\\\\", "//")):
        raise ThumbnailError("Đường dẫn video gốc không hợp lệ.")
    source = Path(value)
    try:
        if (not source.is_absolute() or ".." in source.parts
                or source.is_symlink() or any(parent.is_symlink() for parent in source.parents)):
            raise ThumbnailError("Đường dẫn video gốc không hợp lệ.")
        source = source.resolve(strict=True)
        info = source.stat()
    except (OSError, ValueError) as error:
        raise ThumbnailError("Không tìm thấy video gốc để tạo ảnh xem trước.", 404) from error
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise ThumbnailError("Video gốc thiếu hoặc rỗng; không tạo được ảnh xem trước.", 404)
    if source.suffix.lower() not in VIDEO_SUFFIXES:
        raise ThumbnailError("Tệp nguồn không phải định dạng video được hỗ trợ.")
    identity = (str(source), info.st_size, info.st_mtime_ns,
                getattr(info, "st_ctime_ns", 0), getattr(info, "st_ino", 0), THUMBNAIL_VERSION)
    fingerprint = hashlib.sha256(repr(identity).encode("utf-8")).hexdigest()[:24]
    return source, fingerprint


def attach_history_thumbnails(videos, tasks):
    """Attach one cover per grouped source without probing/decoding media.

    Only members of the already established group are fallback candidates.
    Actions/status continue to belong to the representative, even when an
    older retained source provides the cover.
    """
    by_id = {row.get("task_id"): row for row in tasks if row.get("task_id")}
    for video in videos:
        video["thumbnail_url"] = ""
        candidates = [video.get("session_id")]
        candidates.extend(row.get("task_id") for row in video.get("versions", []))
        for session_id in dict.fromkeys(candidates):
            if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
                continue
            row = by_id.get(session_id)
            if not row:
                continue
            try:
                _, fingerprint = source_signature(row.get("video_path"))
            except ThumbnailError:
                continue
            video["thumbnail_url"] = f"/api/history/thumbnail/{session_id}?v={fingerprint}"
            break
    return videos


def resolve_history_source(session_id, live_sessions):
    """Resolve ID from live state or a validated manifest; never resume/start."""
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        raise ThumbnailError("Mã dự án không hợp lệ.", 400)
    live = live_sessions.get(session_id)
    if live is not None:
        return source_signature(getattr(live, "video_path", None))[0]
    from core.streaming.session_store import _read
    try:
        record = _read(session_id)
    except FileNotFoundError as error:
        raise ThumbnailError("Không tìm thấy dự án đã lưu.", 404) from error
    except (OSError, ValueError) as error:
        raise ThumbnailError("Dự án đã lưu thiếu hoặc hỏng; không đọc được video gốc.", 422) from error
    return source_signature(record["session"].get("video_path"))[0]


class HistoryThumbnailCache:
    """A single decoder with deadline-aware backpressure and bounded LRU bytes."""
    def __init__(self, *, max_entries=64, max_bytes=8 * 1024 * 1024, timeout=12, max_pending=8):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.timeout = timeout
        self._cache = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._decoder = threading.BoundedSemaphore(1)
        self._requests = threading.BoundedSemaphore(max_pending)

    def acquire_request(self):
        """Reject overload before creating a task or queuing an executor job."""
        return self._requests.acquire(blocking=False)

    def release_request(self):
        self._requests.release()

    def _cached(self, key):
        with self._lock:
            result = self._cache.get(key)
            if result is not None:
                self._cache.move_to_end(key)
            return result

    def _remember(self, key, image):
        with self._lock:
            old = self._cache.pop(key, None)
            if old is not None:
                self._bytes -= len(old)
            if len(image) <= self.max_bytes:
                self._cache[key] = image
                self._bytes += len(image)
            while self._cache and (len(self._cache) > self.max_entries or self._bytes > self.max_bytes):
                _, dropped = self._cache.popitem(last=False)
                self._bytes -= len(dropped)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._bytes = 0

    def get(self, value, *, cancel=None):
        cancel = cancel or threading.Event()
        source, key = source_signature(value)
        if cancel.is_set():
            raise ThumbnailError("Đã hủy tải ảnh xem trước.", 499)
        cached = self._cached(key)
        if cached is not None:
            return cached, key
        deadline = time.monotonic() + self.timeout
        acquired = False
        try:
            # Waiting requests do not spawn FFmpeg processes or an unbounded
            # queue. Their queue time belongs to the same request deadline.
            while not acquired:
                if cancel.is_set():
                    raise ThumbnailError("Đã hủy tải ảnh xem trước.", 499)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ThumbnailError("Ảnh xem trước đang bận; hãy tải lại lịch sử.", 503)
                acquired = self._decoder.acquire(timeout=min(.1, remaining))
            source, key = source_signature(source)
            cached = self._cached(key)
            if cached is not None:
                return cached, key
            image = b""
            for seek in ("1", "0"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ThumbnailError("Tạo ảnh xem trước quá thời gian; hãy thử lại.", 504)
                try:
                    image = run_media([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-threads", "1", "-protocol_whitelist", "file,pipe", "-ss", seek, "-i", str(source), "-map", "0:v:0",
                        "-an", "-sn", "-dn", "-frames:v", "1", "-filter_threads", "1",
                        "-vf", "scale=w='min(320,iw)':h='min(320,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                        "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "-threads", "1", "-q:v", "3", "-f", "image2pipe", "pipe:1",
                    ], cancel_check=cancel.is_set, capture_output=True, timeout=remaining,
                        max_capture_bytes=1024 * 1024, max_diagnostic_bytes=64 * 1024)
                except TimeoutError as error:
                    raise ThumbnailError("Tạo ảnh xem trước quá thời gian; hãy thử lại.", 504) from error
                except (OSError, RuntimeError) as error:
                    if cancel.is_set():
                        raise ThumbnailError("Đã hủy tải ảnh xem trước.", 499) from error
                    raise ThumbnailError("Không giải mã được video gốc để tạo ảnh xem trước.") from error
                if image:
                    break
            if cancel.is_set():
                raise ThumbnailError("Đã hủy tải ảnh xem trước.", 499)
            if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
                raise ThumbnailError("Video gốc không có khung hình hợp lệ để xem trước.")
            if source_signature(source)[1] != key:
                raise ThumbnailError("Video gốc vừa thay đổi; hãy tải lại lịch sử.", 409)
            self._remember(key, image)
            return image, key
        finally:
            if acquired:
                self._decoder.release()


history_thumbnail_cache = HistoryThumbnailCache()
