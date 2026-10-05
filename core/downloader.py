"""Video URL validation and a cancellable, progress-reporting yt-dlp worker."""
import json
import logging
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

logger = logging.getLogger("pipeline")


class VideoDownloadError(RuntimeError):
    """An actionable message suitable for the Studio and Tasks views."""


def friendly_download_error(error, host="trang video"):
    detail = str(error).lower()
    if any(word in detail for word in ("timed out", "timeout", "connection refused", "name resolution", "getaddrinfo", "unable to connect")):
        return f"Không kết nối được {host}. Kiểm tra mạng rồi thử lại hoặc chọn video đã tải về máy."
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
        host = urlsplit(clean_url).hostname
        task_id = uuid.uuid4().hex
        prefix = self.output_dir.resolve() / f"video_{task_id}"
        worker = Path(__file__).with_name("download_worker.py")
        process = None
        reader = None
        succeeded = False
        events = queue.Queue()
        phase = "resolve"
        last_activity = time.monotonic()
        last_marker = None

        def emit(data):
            if progress_callback:
                progress_callback(data)

        try:
            if cancel_check and cancel_check():
                raise VideoDownloadError("Đã hủy tải video.")
            emit({"phase": "resolve", "stage": f"Đang kết nối {host} và kiểm tra video…", "progress_pct": None})
            logger.info("Bắt đầu tải video từ %s; đang kiểm tra link.", host)
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
                    emit(event)
                elif kind == "result":
                    result = event["result"]
                    output = Path(result["file_path"]).resolve()
                    if output.parent != prefix.parent or not output.name.startswith(prefix.name + ".") or not output.is_file():
                        raise VideoDownloadError("Trình tải chưa tạo được tệp video hoàn chỉnh.")
                    if cancel_check and cancel_check():
                        raise VideoDownloadError("Đã hủy tải video.")
                    result.update(id=task_id, is_local=False, owned_prefix=str(prefix),
                                  owned_paths=[str(p) for p in prefix.parent.glob(prefix.name + ".*") if p.is_file()])
                    succeeded = True
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
                if not succeeded:
                    # Only this call's random basename, including .part/merge files.
                    for artifact in prefix.parent.glob(prefix.name + ".*"):
                        if artifact.is_file():
                            artifact.unlink(missing_ok=True)
