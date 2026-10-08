"""On-demand WebM preview for Qt builds without proprietary media codecs.

One FFmpeg process at a time, at most two encoder threads, source files remain
untouched, and every generated file lives in an owned temporary directory.
"""
import hashlib
import json
import logging
import math
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import settings
from core.media_process import run_media

logger = logging.getLogger("app")


# Qt's compatibility player consumes a bounded WebM instead of the source
# asset.  The bound is deliberately a request from the caller, rather than a
# property of the source file: a chunked task can ask for a larger prefix as
# preparation advances while a completed result still gets the full file.
MAX_PREVIEW_BYTES = 90 * 1024 * 1024
# Compatibility conversion is an emergency path for Qt builds that cannot
# decode the source codec.  Keep every prefix bounded so a multi-hour task
# never starts a whole-source transcode or an unbounded sequence of growing
# conversions.  The exported MP4 path does not pass coverage_seconds and is
# therefore unaffected.
MAX_COMPATIBILITY_SECONDS = 10 * 60
DEFAULT_COMPATIBILITY_SECONDS = 24.0


def _normalise_coverage(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("Khoảng xem trước không hợp lệ") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Khoảng xem trước không hợp lệ")
    # Keeping the cache key stable avoids one conversion per progress event.
    return min(round(value, 3), MAX_COMPATIBILITY_SECONDS)


class PreviewManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._executor = None
        self._root = None
        self._jobs = {}
        self._closed = False

    def _directory(self):
        if self._root is None:
            settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
            self._root = Path(tempfile.mkdtemp(prefix="media-preview-", dir=settings.TEMP_DIR))
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MediaPreview")
        return self._root

    def start(self, source, coverage_seconds=None):
        source = Path(source).resolve(strict=True)
        if not source.is_file():
            raise ValueError("Không tìm thấy video để tạo bản xem trước")
        coverage_seconds = _normalise_coverage(coverage_seconds)
        stat = source.stat()
        coverage_key = "full" if coverage_seconds is None else f"prefix:{coverage_seconds:.3f}"
        key = hashlib.sha256(f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{coverage_key}".encode()).hexdigest()[:24]
        with self._lock:
            if self._closed:
                raise RuntimeError("Ứng dụng đang đóng")
            if key in self._jobs:
                return self.status(key)
            root = self._directory()
            job = {"status": "PROCESSING", "cancel": threading.Event(), "output": root / f"{key}.webm",
                   "coverage_seconds": coverage_seconds, "source_duration": None, "partial": False}
            self._jobs[key] = job
            job["future"] = self._executor.submit(self._convert, source, job)
            return self.status(key)

    def start_upload(self, stream, suffix, coverage_seconds=None):
        with self._lock:
            if self._closed:
                raise RuntimeError("Ứng dụng đang đóng")
            coverage_seconds = _normalise_coverage(coverage_seconds)
            root = self._directory()
            temporary = tempfile.NamedTemporaryFile(prefix="source-", suffix=suffix, dir=root, delete=False)
            try:
                # Shutdown cannot remove the owned directory while an upload is
                # still writing into it. Copy in fixed-size chunks, never RAM.
                with temporary:
                    shutil.copyfileobj(stream, temporary, length=1024 * 1024)
                result = self.start(temporary.name, coverage_seconds=coverage_seconds)
                self._jobs[result["preview_id"]]["uploaded_source"] = Path(temporary.name)
                return result
            except Exception:
                Path(temporary.name).unlink(missing_ok=True)
                raise

    def _convert(self, source, job):
        output = job["output"]
        try:
            probe = json.loads(run_media([
                "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source),
            ], cancel_check=job["cancel"].is_set, capture_output=True))
            duration = float(probe.get("format", {}).get("duration", 0))
            if duration <= 0:
                raise RuntimeError("Không đọc được thời lượng video")
            requested = job.get("coverage_seconds")
            effective_duration = min(duration, requested) if requested is not None else duration
            if effective_duration <= 0:
                raise RuntimeError("Không đọc được khoảng xem trước")
            partial = effective_duration < duration - .05
            with self._lock:
                job["source_duration"] = round(duration, 3)
                job["coverage_seconds"] = round(effective_duration, 3)
                job["partial"] = partial
            # Keep the compatibility file below 90 MiB.  For a bounded prefix
            # size the bitrate against that prefix, not the multi-hour source.
            bitrate = min(900_000, int(85 * 1024 * 1024 * 8 / effective_duration) - 64_000)
            if bitrate < 32_000:
                raise RuntimeError("Video quá dài để tạo bản xem trước dưới 90 MB. Hãy chia thành các video ngắn hơn.")
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "2",
                "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale=w='min(640,iw)':h='min(640,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=24",
                "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-threads", "2",
                "-b:v", str(bitrate), "-maxrate", str(bitrate), "-bufsize", str(bitrate * 2),
                "-c:a", "libopus", "-b:a", "64k", "-ac", "2",
            ]
            # -t is intentionally omitted for full result previews.  A task
            # compatibility preview is a prefix and must never be presented as
            # the complete source.
            if partial:
                command.extend(["-t", f"{effective_duration:.3f}"])
            command.extend(["-fs", str(MAX_PREVIEW_BYTES), str(output)])
            run_media(command, cancel_check=job["cancel"].is_set)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError("Bản xem trước không tạo được tệp hợp lệ")
            if output.stat().st_size >= MAX_PREVIEW_BYTES:
                raise RuntimeError("Bản xem trước vượt giới hạn dung lượng 90 MB. Hãy chia video ngắn hơn.")
            with self._lock:
                job["status"] = "READY"
        except Exception as error:
            output.unlink(missing_ok=True)
            logger.warning("Compatible video preview failed: %s", error)
            with self._lock:
                job["status"] = "CANCELLED" if job["cancel"].is_set() else "FAILED"
                job["error"] = "Đã dừng tạo bản xem trước" if job["cancel"].is_set() else str(error)
        finally:
            with self._lock:
                uploaded = job.get("uploaded_source")
            if uploaded:
                uploaded.unlink(missing_ok=True)

    def status(self, key):
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                raise KeyError(key)
            return {"preview_id": key, "status": job["status"],
                    "video_url": f"/api/preview/{key}/media" if job["status"] == "READY" else None,
                    "error": job.get("error"),
                    "source_duration": job.get("source_duration"),
                    "coverage_seconds": job.get("coverage_seconds"),
                    "partial": bool(job.get("partial", False))}

    def media(self, key):
        with self._lock:
            job = self._jobs.get(key)
            if not job or job["status"] != "READY":
                raise KeyError(key)
            return job["output"]

    def shutdown(self):
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            executor = self._executor
            for job in jobs:
                job["cancel"].set()
        if executor:
            executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            if self._root:
                # Only this manager's mkdtemp directory is removed.
                shutil.rmtree(self._root)
            self._jobs.clear()
            self._root = None
            self._executor = None
            self._closed = False


preview_manager = PreviewManager()
