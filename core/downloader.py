"""Video URL validation and a cancellable, progress-reporting yt-dlp worker."""
import hashlib
import json
import logging
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from config import settings
from core.douyin_cookies import is_douyin_url
from core.runtime_context import current_execution_context

logger = logging.getLogger("pipeline")


class VideoDownloadError(RuntimeError):
    """An actionable message suitable for the Studio and Tasks views."""


def friendly_download_error(error, host="trang video"):
    detail = str(error).lower()
    if any(word in detail for word in ("timed out", "timeout", "connection refused", "name resolution", "getaddrinfo", "unable to connect")):
        return f"Không kết nối được {host} hoặc máy chủ phản hồi quá chậm. Hãy thử lại hoặc chọn video đã tải về máy."
    if any(word in detail for word in ("fresh cookies", "cookies are needed", "login", "sign in", "403", "captcha", "verify you")):
        if re.fullmatch(r"(?:[a-z0-9-]+\.)*(?:douyin|iesdouyin)\.com", str(host).lower()):
            return ("Douyin từ chối tải trực tiếp hoặc yêu cầu xác minh. Đăng nhập Chrome không tự chuyển phiên sang Studio. "
                    "Kiểm tra API tải video hoặc cookie đã nhập trong Cài đặt; nếu vẫn không tải được, chọn tệp video trên máy.")
        return "Trang video từ chối tải trực tiếp hoặc yêu cầu đăng nhập/xác minh. Hãy thử lại hoặc chọn tệp video đã lưu trên máy."
    if any(word in detail for word in ("404", "not available", "unavailable", "private video", "removed")):
        return "Video không còn công khai hoặc link đã hết hạn. Hãy sao chép lại link từ video gốc."
    if "unsupported url" in detail:
        return "Link này chưa được trình tải hỗ trợ. Hãy dùng link của một video cụ thể hoặc chọn tệp video trên máy."
    return "Không tải được video từ link này. Kiểm tra link còn công khai, thử lại hoặc chọn tệp video trên máy."


class VideoDownloader:
    _active_guard = threading.Lock()
    _active_sources = set()

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = Path(output_dir or settings.INPUT_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_url(text: str) -> str:
        text = str(text or "").strip()
        # Rich-text chat copies may include a formatted URL label and a separate
        # Markdown target. Use the target, keeping nearby share text separate.
        text = re.sub(r"\[[^\]\r\n]*\]\(\s*(https?://[^\s<>()]+)\s*\)",
                      lambda match: " " + match.group(1) + " ", text, flags=re.I)
        text = re.sub(r"(^|[\s(\[<{])(\*\*|__|`)(https?://[^\s<>]+?)\2",
                      lambda match: match.group(1) + match.group(3) + " ", text, flags=re.I)
        match = re.search(r"https?://[^\s<>\"'，。！？、；（）【】]+", text, re.I)
        if match:
            candidate = match.group(0).rstrip(".,;!?)\\]}»”’")
        elif re.match(r"^(?:www\.)?(?:v\.)?(?:douyin\.com|tiktok\.com|youtu\.be|youtube\.com|bilibili\.com)/", text, re.I):
            candidate = "https://" + text.split()[0].rstrip(".,;!?)\\]}»”’")
        else:
            raise ValueError("Không tìm thấy link video hợp lệ. Dán link bắt đầu bằng https:// hoặc toàn bộ nội dung Chia sẻ có link.")
        candidate = candidate.replace("\\_", "_")
        try:
            parsed = urlsplit(candidate)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError("Link video không hợp lệ. Hãy sao chép lại từ nút Chia sẻ của video.") from None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))

    _extract_clean_url = normalize_url

    @staticmethod
    def source_identity(url):
        """Recognize the same Douyin post across desktop/share URL variants."""
        if is_douyin_url(url):
            from core.douyin_resolver import _video_id
            video_id = _video_id(url)
            if video_id:
                return "douyin:" + video_id
        return url

    @classmethod
    def source_hash(cls, url):
        return hashlib.sha256(cls.source_identity(url).encode("utf-8")).hexdigest()

    def _write_index(self, path, data):
        temporary = path.with_name(path.name + ".tmp")
        if path.is_symlink() or temporary.is_symlink():
            raise OSError("Unsafe download index")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
            temporary.replace(path)
        finally:
            if not temporary.is_symlink():
                temporary.unlink(missing_ok=True)

    def register_completed(self, url, result):
        """Persist a validated source; callers may register an existing owned download."""
        from core.download_worker import verify_video
        clean_url = self.normalize_url(url)
        if not is_douyin_url(clean_url):
            return result
        output = Path(result["file_path"])
        root = self.output_dir.resolve()
        if (output.is_symlink() or output.resolve().parent != root
                or not re.fullmatch(r"video_[a-f0-9]{32}\.(mp4|mkv|mov|webm|avi)", output.name)
                or not output.is_file() or output.stat().st_size <= 0):
            raise VideoDownloadError("Tệp video đã tải không hợp lệ để sử dụng lại.")
        duration = verify_video(output)
        stat = output.stat()
        record = {"version": 1, "source_identity": self.source_identity(clean_url),
                  "file_name": output.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                  "duration": duration, "title": str(result.get("title") or output.stem)[:2000],
                  "source": str(result.get("source") or "douyin")[:64]}
        result.update(duration=duration, reusable_source=True, owned_paths=[])
        self._write_index(root / f".source-{self.source_hash(clean_url)}.json", record)
        return result

    def _completed_source(self, clean_url, source_hash):
        from core.download_worker import verify_video, MediaDownloadError
        index = self.output_dir.resolve() / f".source-{source_hash}.json"
        if not is_douyin_url(clean_url) or not index.is_file() or index.is_symlink():
            return None
        try:
            if index.stat().st_size > 16384:
                return None
            record = json.loads(index.read_text(encoding="utf-8"))
            name = record.get("file_name")
            if (record.get("version") != 1 or record.get("source_identity") != self.source_identity(clean_url)
                    or not isinstance(name, str)
                    or not re.fullmatch(r"video_[a-f0-9]{32}\.(mp4|mkv|mov|webm|avi)", name)):
                return None
            output = index.parent / name
            if output.is_symlink() or not output.is_file() or output.resolve().parent != index.parent:
                return None
            stat = output.stat()
            if (type(record.get("size")) is not int or stat.st_size <= 0 or stat.st_size != record["size"]
                    or stat.st_mtime_ns != record.get("mtime_ns")):
                return None
            duration = verify_video(output)
            expected = float(record.get("duration", 0))
            if not math.isfinite(expected) or expected <= 0 or abs(expected - duration) > 0.05:
                return None
            return {"id": output.stem[6:], "file_path": str(output), "title": record.get("title") or output.stem,
                    "duration": duration, "source": record.get("source") or "douyin", "is_local": False,
                    "reused_source": True, "reusable_source": True, "owned_paths": []}
        except (OSError, ValueError, TypeError, AttributeError, MediaDownloadError):
            return None

    @staticmethod
    def _valid_partial(prefix):
        from core.download_worker import _load_checkpoint, MediaDownloadError
        partial, checkpoint = prefix.with_suffix(".mp4.part"), prefix.with_suffix(".resume.json")
        if partial.is_symlink() or checkpoint.is_symlink():
            logger.warning("[%s] Không nối tiếp: checkpoint_path_unsafe.", prefix.name[6:14])
            return False
        try:
            # The worker has already stopped before this retention check. A
            # hard stop can leave bytes after its last atomic checkpoint; only
            # the hashed prefix is durable/reusable. Trim the uncommitted tail
            # now so retained byte diagnostics match the actual resume offset.
            saved, _ = _load_checkpoint(checkpoint, partial, truncate_tail=True)
            if saved is None:
                logger.warning("[%s] Không nối tiếp: checkpoint_validation_failed; partial_bytes=%s; checkpoint_bytes=%s.",
                               prefix.name[6:14], partial.stat().st_size if partial.is_file() else 0,
                               checkpoint.stat().st_size if checkpoint.is_file() else 0)
            return saved is not None
        except (MediaDownloadError, OSError):
            logger.warning("[%s] Không nối tiếp: checkpoint_disk_read_failed.", prefix.name[6:14])
            return False

    @staticmethod
    def _stop_worker(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=8, check=False)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def download(self, url_or_path: str, progress_callback=None, cancel_check=None) -> Dict[str, Any]:
        # The CLI also accepts local files; never own or remove these paths.
        if not re.search(r"https?://", url_or_path, re.I):
            local_path = Path(url_or_path)
            if local_path.is_file() and local_path.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".avi"}:
                return {"id": uuid.uuid4().hex, "file_path": str(local_path.resolve()),
                        "title": local_path.stem, "duration": None, "is_local": True}
        clean_url = self.normalize_url(url_or_path)
        source_key = (str(self.output_dir.resolve()), self.source_hash(clean_url))
        with self._active_guard:
            if source_key in self._active_sources:
                raise VideoDownloadError("Video này đang được tải. Xem tiến độ trong tab Tác vụ.")
            self._active_sources.add(source_key)
        try:
            if cancel_check and cancel_check():
                raise VideoDownloadError("Đã hủy tải video.")
            cached = self._completed_source(clean_url, source_key[1])
            if cached:
                if cancel_check and cancel_check():
                    raise VideoDownloadError("Đã hủy tải video.")
                size = Path(cached["file_path"]).stat().st_size
                if progress_callback:
                    progress_callback({"phase": "prepare", "stage": "Dùng lại video gốc đã tải và kiểm tra; không tải lại.",
                                       "progress_pct": 100.0, "downloaded_bytes": size, "total_bytes": size})
                logger.info("[%s] run_id=%s Dùng lại video gốc đã kiểm tra (%s byte).", cached["id"][:8],
                            current_execution_context().run_id, size)
                return cached
            return self._download_remote(clean_url, source_key[1], progress_callback, cancel_check)
        finally:
            with self._active_guard:
                self._active_sources.discard(source_key)

    def _download_remote(self, clean_url, source_hash, progress_callback, cancel_check):
        host = urlsplit(clean_url).hostname
        task_id = uuid.uuid4().hex
        prefix = self.output_dir.resolve() / f"video_{task_id}"
        resume_index = prefix.parent / f".download-{source_hash}.json" if is_douyin_url(clean_url) else None
        resume_candidate = False
        # Prior releases indexed the literal URL. Adopt that checkpoint once,
        # then publish the canonical key before launching the next worker.
        candidate_index = resume_index
        if resume_index and not resume_index.exists():
            legacy_index = prefix.parent / f".download-{hashlib.sha256(clean_url.encode('utf-8')).hexdigest()}.json"
            if legacy_index.is_file() and not legacy_index.is_symlink():
                candidate_index = legacy_index
        if candidate_index and candidate_index.is_file() and not candidate_index.is_symlink():
            try:
                if candidate_index.stat().st_size > 512:
                    raise ValueError
                name = json.loads(candidate_index.read_text(encoding="utf-8")).get("prefix", "")
                if not isinstance(name, str) or not re.fullmatch(r"video_[a-f0-9]{32}", name):
                    raise ValueError
                candidate = prefix.parent / name
                completed = candidate.with_suffix(".mp4")
                if completed.is_file() and not completed.is_symlink():
                    # A hard exit may happen after the worker's final rename
                    # but before the parent publishes the completed index.
                    try:
                        self.register_completed(clean_url, {"file_path": str(completed)})
                        recovered = self._completed_source(clean_url, source_hash)
                    except (OSError, ValueError, RuntimeError):
                        recovered = None
                    if recovered:
                        if cancel_check and cancel_check():
                            raise VideoDownloadError("Đã hủy tải video.")
                        if progress_callback:
                            size = completed.stat().st_size
                            progress_callback({"phase": "prepare", "stage": "Đã khôi phục video tải xong trước khi ứng dụng dừng.",
                                               "progress_pct": 100.0, "downloaded_bytes": size, "total_bytes": size})
                        try:
                            candidate_index.unlink(missing_ok=True)
                        except OSError:
                            logger.warning("[%s] Chưa dọn được chỉ mục tải cũ.", name[6:14])
                        logger.info("[%s] Đã khôi phục video hoàn chỉnh sau lần ứng dụng dừng.", name[6:14])
                        return recovered
                partial = candidate.with_suffix(".mp4.part")
                checkpoint = candidate.with_suffix(".resume.json")
                if (partial.is_file() and not partial.is_symlink() and partial.stat().st_size > 0
                        and checkpoint.is_file() and not checkpoint.is_symlink()
                        and not candidate.with_suffix(".mp4").exists()):
                    prefix, task_id, resume_candidate = candidate, name[6:], True
            except (OSError, ValueError, AttributeError):
                pass
        worker = Path(__file__).with_name("download_worker.py")
        process = None
        reader = None
        succeeded = False
        events = queue.Queue()
        phase = "resolve"
        last_activity = time.monotonic()
        last_marker = None
        last_log_time = 0
        last_log_stage = None
        run_id = current_execution_context().run_id

        def emit(data):
            if progress_callback:
                progress_callback(data)

        try:
            if cancel_check and cancel_check():
                raise VideoDownloadError("Đã hủy tải video.")
            emit({"phase": "resolve", "stage": f"Đang kết nối {host} và kiểm tra video…", "progress_pct": None})
            logger.info("[%s] run_id=%s %s từ %s; đang kiểm tra link.", task_id[:8], run_id,
                        "Kiểm tra phần video đã giữ" if resume_candidate else "Bắt đầu tải video", host)
            if resume_index:
                self._write_index(resume_index, {"prefix": prefix.name})
                if resume_candidate and candidate_index != resume_index:
                    try:
                        candidate_index.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("[%s] Chưa dọn được chỉ mục tải cũ.", task_id[:8])
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen([sys.executable, "-B", "-u", str(worker), clean_url, str(prefix)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding="utf-8", bufsize=1, **kwargs)

            def read_events():
                try:
                    for line in process.stdout:
                        try:
                            data = json.loads(line)
                            if isinstance(data, dict):
                                events.put(data)
                        except ValueError:
                            continue
                finally:
                    events.put({"kind": "eof"})

            reader = threading.Thread(target=read_events, name="VideoDownloadProgress", daemon=True)
            reader.start()
            while True:
                if cancel_check and cancel_check():
                    raise VideoDownloadError("Đã hủy tải video.")
                idle_limit = 60 if phase == "resolve" else (180 if phase == "prepare" else 90)
                if time.monotonic() - last_activity > idle_limit:
                    raise VideoDownloadError(friendly_download_error("timeout", host))
                try:
                    event = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                kind = event.pop("kind", "")
                if kind == "progress":
                    marker = (event.get("phase"), event.get("stage"), event.get("source"),
                              event.get("downloaded_bytes"))
                    if marker != last_marker:
                        last_activity = time.monotonic()
                        last_marker = marker
                    phase = event.get("phase", phase)
                    stage = event.get("stage", "")
                    now = time.monotonic()
                    if stage != last_log_stage or now - last_log_time >= 10:
                        downloaded = event.get("downloaded_bytes") or 0
                        total = event.get("total_bytes") or 0
                        logger.info("[%s] run_id=%s %s%s", task_id[:8], run_id, stage,
                                    f" · {downloaded / 1024**2:.1f}/{total / 1024**2:.1f} MB" if total else "")
                        last_log_time, last_log_stage = now, stage
                    emit(event)
                elif kind == "diagnostic":
                    # Only typed codes/counts, never raw exception strings or CDN URLs.
                    code = event.get("code", event.get("reason", "unknown"))
                    code = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", code) else "unknown"
                    error_type = event.get("error_type")
                    allowed_types = {"TimeoutError", "ConnectionResetError", "IncompleteRead", "RemoteDisconnected",
                                     "SSLError", "TransportError", "HTTPError", "OSError", "DouyinResolveError", "MediaResponseError"}
                    error_type = error_type if isinstance(error_type, str) and error_type in allowed_types else "unknown"
                    status, count, attempt = event.get("status_code"), event.get("downloaded_bytes"), event.get("attempt")
                    status = status if type(status) is int and 100 <= status <= 599 else "unknown"
                    count = count if type(count) is int and count >= 0 else "unknown"
                    attempt = attempt if type(attempt) is int and 1 <= attempt <= 100 else "unknown"
                    logger.warning("[%s] run_id=%s Tải video: %s; loại lỗi %s; HTTP %s; đã nhận %s byte; lần thử %s.",
                                   task_id[:8], run_id, code, error_type, status, count, attempt)
                elif kind == "result":
                    result = event["result"]
                    output = Path(result["file_path"])
                    if (output.is_symlink() or output.resolve().parent != prefix.parent
                            or not output.name.startswith(prefix.name + ".") or not output.is_file()
                            or output.stat().st_size <= 0):
                        raise VideoDownloadError("Trình tải chưa tạo được tệp video hoàn chỉnh.")
                    result.update(id=task_id, is_local=False, owned_prefix=str(prefix),
                                  owned_paths=[str(p) for p in prefix.parent.glob(prefix.name + ".*") if p.is_file()])
                    if resume_index:
                        try:
                            self.register_completed(clean_url, result)
                        except OSError:
                            logger.warning("[%s] Không lưu được chỉ mục video đã tải.", task_id[:8])
                    succeeded = True
                    if cancel_check and cancel_check():
                        raise VideoDownloadError("Đã hủy tải video.")
                    logger.info("Đã tải video thành công (%s).", task_id[:8])
                    return result
                elif kind == "error":
                    raise VideoDownloadError(event.get("message") or friendly_download_error("", host))
                elif kind == "eof":
                    raise VideoDownloadError("Trình tải video đã dừng trước khi hoàn tất. Hãy thử lại.")
        except VideoDownloadError as error:
            logger.warning("Tải video từ %s: %s", host, error)
            raise
        finally:
            try:
                if process:
                    self._stop_worker(process)
            finally:
                if reader:
                    reader.join(timeout=2)
                if process and process.stdout and not (reader and reader.is_alive()):
                    process.stdout.close()
                if not succeeded and resume_index:
                    # Stop may race the worker's final rename/result message.
                    # Keep a fully validated source even though its caller was
                    # cancelled; cancellation must not destroy finished media.
                    completed = prefix.with_suffix(".mp4")
                    if completed.is_file() and not completed.is_symlink():
                        try:
                            self.register_completed(clean_url, {"file_path": str(completed)})
                            succeeded = True
                        except (OSError, ValueError, RuntimeError):
                            logger.warning("[%s] Tệp lúc dừng chưa vượt qua kiểm tra để dùng lại.", task_id[:8])
                retained = False
                if not succeeded and resume_index:
                    partial, checkpoint = prefix.with_suffix(".mp4.part"), prefix.with_suffix(".resume.json")
                    if (partial.is_file() and not partial.is_symlink() and partial.stat().st_size > 0
                            and checkpoint.is_file() and not checkpoint.is_symlink()
                            and not resume_index.is_symlink() and self._valid_partial(prefix)):
                        try:
                            self._write_index(resume_index, {"prefix": prefix.name})
                            retained = True
                            logger.info("[%s] Đã giữ %.1f MB để lần Bắt đầu dịch tiếp theo tải tiếp.",
                                        task_id[:8], partial.stat().st_size / 1024**2)
                        except OSError:
                            logger.warning("[%s] Không lưu được thông tin tải tiếp.", task_id[:8])
                    elif partial.is_file():
                        logger.warning("[%s] Không giữ phần tải: checkpoint_missing_or_invalid; partial_bytes=%s.",
                                       task_id[:8], partial.stat().st_size)
                if not retained and resume_index and not resume_index.is_symlink():
                    try:
                        resume_index.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("[%s] Không xóa được chỉ mục tải tiếp.", task_id[:8])
                if not succeeded and not retained:
                    # Only this call's random basename, including .part/merge files.
                    for artifact in prefix.parent.glob(prefix.name + ".*"):
                        if artifact.is_file():
                            try:
                                artifact.unlink(missing_ok=True)
                            except OSError:
                                logger.warning("[%s] Không xóa được tệp tải dở.", task_id[:8])
