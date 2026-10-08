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
import time
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
MAX_COMPATIBILITY_SECONDS = 120
MAX_RETAINED_JOBS = 8
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


def _normalise_start(value):
    if value is None:
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError("Vị trí bắt đầu xem trước không hợp lệ") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError("Vị trí bắt đầu xem trước không hợp lệ")
    return round(value, 3)


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

    def _drop_job_locked(self, key, job):
        if job.get("status") == "PROCESSING":
            return False
        try:
            job.get("output", Path()).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stale preview output %s", job.get("output"))
        uploaded = job.get("uploaded_source")
        if uploaded:
            try:
                Path(uploaded).unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary preview source %s", uploaded)
        self._jobs.pop(key, None)
        return True

    def _evict_jobs_locked(self):
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        terminal = sorted(
            ((key, job) for key, job in self._jobs.items()
             if job.get("status") in {"READY", "FAILED", "CANCELLED"}),
            key=lambda item: (item[1].get("last_access", 0), item[1].get("finished_at") or 0),
        )
        for key, job in terminal:
            if len(self._jobs) <= MAX_RETAINED_JOBS:
                break
            self._drop_job_locked(key, job)

    def start(self, source, coverage_seconds=None, start_seconds=0, end_seconds=None):
        source = Path(source).resolve(strict=True)
        if not source.is_file():
            raise ValueError("Không tìm thấy video để tạo bản xem trước")
        coverage_seconds = _normalise_coverage(coverage_seconds)
        start_seconds = _normalise_start(start_seconds)
        if end_seconds is not None:
            try:
                end_seconds = float(end_seconds)
            except (TypeError, ValueError):
                raise ValueError("Vị trí kết thúc xem trước không hợp lệ") from None
            if not math.isfinite(end_seconds) or end_seconds <= start_seconds:
                raise ValueError("Vị trí kết thúc xem trước không hợp lệ")
            end_seconds = round(end_seconds, 3)
            if end_seconds - start_seconds > MAX_COMPATIBILITY_SECONDS:
                end_seconds = round(start_seconds + MAX_COMPATIBILITY_SECONDS, 3)
        elif coverage_seconds is not None:
            end_seconds = round(start_seconds + coverage_seconds, 3)
        elif start_seconds > 0:
            end_seconds = round(start_seconds + MAX_COMPATIBILITY_SECONDS, 3)
        stat = source.stat()
        window_key = "full" if end_seconds is None else f"window:{start_seconds:.3f}-{end_seconds:.3f}"
        identity = f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{getattr(stat, 'st_ctime_ns', 0)}|{getattr(stat, 'st_ino', 0)}|{window_key}"
        key = hashlib.sha256(identity.encode()).hexdigest()[:24]
        with self._lock:
            if self._closed:
                raise RuntimeError("Ứng dụng đang đóng")
            if key in self._jobs:
                existing = self._jobs[key]
                if existing["status"] in {"FAILED", "CANCELLED"}:
                    self._drop_job_locked(key, existing)
                else:
                    return self.status(key)
            root = self._directory()
            job = {"status": "PROCESSING", "cancel": threading.Event(), "output": root / f"{key}.webm",
                   "coverage_seconds": coverage_seconds, "source_duration": None, "partial": False,
                   "start_seconds": start_seconds, "end_seconds": end_seconds,
                   "created_at": time.monotonic(), "finished_at": None, "last_access": time.monotonic()}
            self._jobs[key] = job
            job["future"] = self._executor.submit(self._convert, source, job)
            self._evict_jobs_locked()
            return self.status(key)

    def start_upload(self, stream, suffix, coverage_seconds=None, start_seconds=0, end_seconds=None):
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
                result = self.start(temporary.name, coverage_seconds=coverage_seconds,
                                    start_seconds=start_seconds, end_seconds=end_seconds)
                uploaded = Path(temporary.name)
                self._jobs[result["preview_id"]]["uploaded_source"] = uploaded
                # A tiny upload may finish before the worker gets a chance to
                # observe uploaded_source in its finally block.  Clean it
                # immediately if the job is already terminal.
                if self._jobs[result["preview_id"]]["status"] != "PROCESSING":
                    uploaded.unlink(missing_ok=True)
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
            requested_start = float(job.get("start_seconds") or 0)
            requested_end = job.get("end_seconds")
            if requested_start >= duration:
                raise RuntimeError("Vị trí xem trước vượt quá thời lượng video")
            effective_start = min(duration, requested_start)
            effective_end = min(duration, float(requested_end)) if requested_end is not None else duration
            if effective_end <= effective_start:
                raise RuntimeError("Không đọc được khoảng xem trước")
            effective_duration = effective_end - effective_start
            partial = effective_start > .05 or effective_end < duration - .05
            with self._lock:
                job["source_duration"] = round(duration, 3)
                job["start_seconds"] = round(effective_start, 3)
                job["end_seconds"] = round(effective_end, 3)
                job["coverage_seconds"] = round(effective_duration, 3)
                job["partial"] = partial
            # Keep the compatibility file below 90 MiB.  For a bounded prefix
            # size the bitrate against that prefix, not the multi-hour source.
            bitrate = min(900_000, int(85 * 1024 * 1024 * 8 / effective_duration) - 64_000)
            if bitrate < 32_000:
                raise RuntimeError("Video quá dài để tạo bản xem trước dưới 90 MB. Hãy chia thành các video ngắn hơn.")
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "2",
                *(["-ss", f"{effective_start:.3f}"] if effective_start > .001 else []),
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
                if job["cancel"].is_set():
                    raise RuntimeError("Đã dừng tạo bản xem trước")
                job["status"] = "READY"
                job["finished_at"] = time.monotonic()
                self._evict_jobs_locked()
        except Exception as error:
            output.unlink(missing_ok=True)
            logger.warning("Compatible video preview failed: %s", error)
            with self._lock:
                job["status"] = "CANCELLED" if job["cancel"].is_set() else "FAILED"
                job["error"] = "Đã dừng tạo bản xem trước" if job["cancel"].is_set() else str(error)
                job["finished_at"] = time.monotonic()
                self._evict_jobs_locked()
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
            job["last_access"] = time.monotonic()
            return {"preview_id": key, "status": job["status"],
                    "video_url": f"/api/preview/{key}/media" if job["status"] == "READY" else None,
                    "error": job.get("error"),
                    "source_duration": job.get("source_duration"),
                    "coverage_seconds": job.get("coverage_seconds"),
                    "start_seconds": job.get("start_seconds", 0),
                    "end_seconds": job.get("end_seconds"),
                    "partial": bool(job.get("partial", False))}

    def media(self, key):
        with self._lock:
            job = self._jobs.get(key)
            if (not job or job["status"] != "READY" or
                    not job.get("output", Path()).is_file() or
                    job["output"].stat().st_size <= 0):
                raise KeyError(key)
            job["last_access"] = time.monotonic()
            return job["output"]

    def cancel(self, key):
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                raise KeyError(key)
            if job["status"] == "PROCESSING":
                job["cancel"].set()
            return self.status(key)

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
