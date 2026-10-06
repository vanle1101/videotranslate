"""Private subprocess entry point. Emits JSON progress, never raw yt-dlp output."""
import json
import errno
import hashlib
import http.client
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.downloader import friendly_download_error
from core.douyin_cookies import DouyinCookieError, cookie_policy, is_douyin_url, load_douyin_cookiejar
from core.douyin_resolver import DouyinResolveError, open_public_media, resolve_douyin


class MediaDownloadError(RuntimeError):
    """A safe message, without signed CDN addresses or credentials."""

    def __init__(self, message, *, resumable=False, code=None):
        super().__init__(message)
        self.resumable = resumable
        self.code = code


MAX_MEDIA_RETRIES = 5
MAX_CHECKPOINT_BYTES = 4096
CHECKPOINT_MAX_AGE = 7 * 24 * 3600
_MEDIA_TYPES = {"video/mp4", "application/octet-stream", "binary/octet-stream"}
_SAFE_CAUSES = {"read_timeout", "connection_timeout", "connection_reset", "incomplete_read",
                "connection_closed", "tls_error", "transport_error", "http_error", "range_invalid",
                "range_ignored", "resource_changed", "content_type", "length_mismatch", "checkpoint_invalid",
                "disk_full", "disk_error", "resolve_failed", "resume_validators_missing"}
_SAFE_ERROR_TYPES = {"TimeoutError", "ConnectionResetError", "IncompleteRead", "RemoteDisconnected",
                     "SSLError", "TransportError", "HTTPError", "OSError", "DouyinResolveError",
                     "MediaResponseError"}


class _TransferIssue(Exception):
    def __init__(self, code, status_code=None):
        self.code, self.status_code = code, status_code


def _disk_error(error):
    if getattr(error, "errno", None) == errno.ENOSPC:
        return MediaDownloadError("Ổ đĩa đã hết dung lượng khi lưu video. Hãy giải phóng dung lượng rồi thử lại.", code="disk_full")
    return MediaDownloadError("Không đọc hoặc ghi được tệp video trên ổ đĩa. Kiểm tra dung lượng và quyền ghi của thư mục tải.", code="disk_error")


def _diagnostic(send, error, attempt, downloaded_bytes=0):
    code = getattr(error, "code", None)
    if code not in _SAFE_CAUSES:
        code = ("read_timeout" if isinstance(error, TimeoutError) else
                "connection_reset" if isinstance(error, ConnectionResetError) else
                "incomplete_read" if isinstance(error, http.client.IncompleteRead) else "transport_error")
    status = getattr(error, "status_code", None)
    safe_type = {TimeoutError: "TimeoutError", ConnectionResetError: "ConnectionResetError",
                 OSError: "OSError", http.client.IncompleteRead: "IncompleteRead"}.get(type(error))
    wrapped_type = getattr(error, "error_type", None)
    safe_type = (wrapped_type if isinstance(wrapped_type, str) and wrapped_type in _SAFE_ERROR_TYPES else None) or safe_type
    safe_type = safe_type or ("DouyinResolveError" if isinstance(error, DouyinResolveError) else "MediaResponseError")
    event = {"kind": "diagnostic", "source": "douyin-public", "code": code,
             "error_type": safe_type, "attempt": attempt + 1,
             "downloaded_bytes": downloaded_bytes if type(downloaded_bytes) is int and downloaded_bytes >= 0 else 0}
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        event["status_code"] = status
    send(event)
    return code, event.get("status_code")


def _positive_int(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _validators(headers):
    etag = headers.get("ETag", "")
    etag = etag if isinstance(etag, str) and re.fullmatch(r'"[\x21\x23-\x7e]{1,256}"', etag) else None
    modified = headers.get("Last-Modified", "")
    try:
        valid_date = (isinstance(modified, str) and len(modified) <= 64
                      and not re.search(r"[\r\n\x00]", modified)
                      and parsedate_to_datetime(modified).tzinfo is not None)
    except (TypeError, ValueError, OverflowError):
        valid_date = False
    return {"etag": etag, "last_modified": modified if valid_date else None}


def _same_entity(saved, headers):
    actual = _validators(headers)
    # A weak ETag is insufficient for joining independent HTTP responses.
    if saved.get("etag"):
        return actual["etag"] == saved["etag"]
    return bool(saved.get("last_modified") and actual["last_modified"] == saved["last_modified"])


def _format_identity(chosen):
    fields = {key: chosen.get(key) for key in ("codec", "width", "height", "bitrate", "size", "original")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load_checkpoint(path, partial, video_id=None, format_identity=None, *, truncate_tail=False):
    """Only trust our bounded, recent manifest when every saved byte still matches."""
    digest = hashlib.sha256()
    if not path.exists() or not partial.exists():
        return None, digest
    try:
        if path.stat().st_size > MAX_CHECKPOINT_BYTES:
            return None, digest
        with path.open("rb") as handle:
            checkpoint = json.loads(handle.read(MAX_CHECKPOINT_BYTES + 1))
        count, total = checkpoint.get("downloaded_bytes"), checkpoint.get("total_bytes")
        saved_time = checkpoint.get("updated_at")
        stored_id, stored_format = checkpoint.get("video_id"), checkpoint.get("format_identity")
        if (checkpoint.get("version") != 1 or not isinstance(stored_id, str)
                or not re.fullmatch(r"\d{15,22}", stored_id)
                or not isinstance(stored_format, str) or not re.fullmatch(r"[0-9a-f]{64}", stored_format)
                or (video_id is not None and stored_id != str(video_id))
                or (format_identity is not None and stored_format != format_identity)
                or type(count) is not int or count <= 0
                or (total is not None and (type(total) is not int or total < count))
                or not isinstance(saved_time, (int, float)) or not math.isfinite(saved_time)
                or not -60 <= time.time() - saved_time <= CHECKPOINT_MAX_AGE
                or partial.stat().st_size < count
                or not isinstance(checkpoint.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", checkpoint["sha256"])):
            return None, digest
        validators = _validators({"ETag": checkpoint.get("etag"), "Last-Modified": checkpoint.get("last_modified")})
        if any(checkpoint.get(key) != value for key, value in validators.items()):
            return None, digest
        with partial.open("rb") as handle:
            remaining = count
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    return None, hashlib.sha256()
                digest.update(chunk)
                remaining -= len(chunk)
        if partial.stat().st_size < count or digest.hexdigest() != checkpoint["sha256"]:
            return None, hashlib.sha256()
        # A process may stop between a media write and the next atomic
        # checkpoint. Only the verified prefix can be appended safely.
        if truncate_tail and partial.stat().st_size > count:
            with partial.open("r+b") as handle:
                handle.truncate(count)
        return checkpoint, digest
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        return None, hashlib.sha256()
    except OSError as error:
        raise _disk_error(error) from None


def _save_checkpoint(path, partial, *, video_id, format_identity, total, downloaded, validators, digest):
    """Atomically publish metadata, never signed media URLs or browser credentials."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        if not downloaded or partial.stat().st_size != downloaded:
            path.unlink(missing_ok=True)
            return False
        data = {"version": 1, "video_id": str(video_id), "format_identity": format_identity,
                "total_bytes": total, "downloaded_bytes": downloaded, **validators,
                "sha256": digest.hexdigest(), "updated_at": time.time()}
        temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError as error:
        raise _disk_error(error) from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            raise _disk_error(error) from None


def verify_video(path):
    """A completed HTTP response may still be an error page or truncated file."""
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=20, **flags)
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if result.returncode or not math.isfinite(duration) or duration <= 0 or not any(
            stream.get("codec_type") == "video" for stream in data.get("streams", [])
        ):
            raise ValueError
        return duration
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise MediaDownloadError("Dữ liệu tải về chưa phải video hoàn chỉnh. Hãy thử tải lại.") from None


def download_resolved_video(info, prefix, send):
    """Retry the selected source without mixing entities or lowering quality."""
    output = Path(prefix + ".mp4")
    partial = Path(prefix + ".mp4.part")
    checkpoint_path = Path(prefix + ".resume.json")
    # Preserve the source file when available. No transcode or quality reduction.
    chosen = max(info["formats"], key=lambda f: (bool(f.get("original")), f["width"] * f["height"], f["bitrate"], f["codec"] == "h264"))
    urls = chosen["urls"][:3]
    if not urls:
        raise MediaDownloadError("Nguồn chưa cung cấp địa chỉ tải bản video đã chọn.")
    if any(path.is_symlink() for path in (output, partial, checkpoint_path,
                                         checkpoint_path.with_name(checkpoint_path.name + ".tmp"))):
        raise MediaDownloadError("Đường dẫn lưu video không hợp lệ. Hãy chọn lại thư mục tải.")
    format_identity = _format_identity(chosen)
    checkpoint, digest = _load_checkpoint(checkpoint_path, partial, info["id"], format_identity, truncate_tail=True)
    downloaded = checkpoint["downloaded_bytes"] if checkpoint else 0
    total = checkpoint["total_bytes"] if checkpoint else None
    validators = {key: checkpoint.get(key) if checkpoint else None for key in ("etag", "last_modified")}
    started = last_emit = time.monotonic()
    starting_bytes = downloaded
    checkpoint_bytes, checkpoint_time = downloaded, time.monotonic()
    stage_base = "Đang tải bản gốc Douyin" if chosen.get("original") else "Đang tải video Douyin"
    if chosen.get("height"):
        stage_base += f" {chosen['height']}p"

    def report(stage=None, *, waiting=False):
        elapsed = max(time.monotonic() - started, 0.01)
        speed = None if waiting else max(0, downloaded - starting_bytes) / elapsed
        message = stage or stage_base + (f" ({total / 1024**2:,.0f} MB)" if total else "")
        send({"kind": "progress", "source": "douyin-public", "phase": "download", "stage": message,
              "progress_pct": round(min(100, downloaded * 100 / total), 1) if total else None,
              "downloaded_bytes": downloaded, "total_bytes": total, "speed": speed,
              "eta": (total - downloaded) / speed if total and speed else None})

    def restart(reason):
        nonlocal downloaded, total, validators, digest, starting_bytes
        downloaded, total, starting_bytes = 0, None, 0
        validators, digest = {"etag": None, "last_modified": None}, hashlib.sha256()
        try:
            checkpoint_path.unlink(missing_ok=True)
        except OSError as error:
            raise _disk_error(error) from None
        report(reason, waiting=True)

    if not checkpoint and (checkpoint_path.exists() or partial.exists()):
        _diagnostic(send, _TransferIssue("checkpoint_invalid"), 0, downloaded)
        restart("Phần tải cũ không còn đủ điều kiện nối tiếp; đang tải lại bản đã chọn từ đầu…")
    elif checkpoint:
        report(f"Đã khôi phục {downloaded / 1024**2:,.1f} MB; đang tiếp tục tải bản đã chọn…", waiting=True)

    retry_errors = (DouyinResolveError, OSError, http.client.HTTPException, _TransferIssue)
    last_code, last_status = "transport_error", None
    for attempt in range(MAX_MEDIA_RETRIES + 1):
        if total and downloaded == total:
            break
        response = None
        try:
            can_resume = downloaded > 0 and any(validators.values())
            request_headers = {"Range": f"bytes={downloaded}-", "If-Range": validators["etag"] or validators["last_modified"]} if can_resume else None
            url = urls[attempt % len(urls)]
            response = open_public_media(url, headers=request_headers) if request_headers else open_public_media(url)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if response.status not in (200, 206):
                raise _TransferIssue("http_error", response.status)
            if content_type not in _MEDIA_TYPES:
                raise _TransferIssue("content_type", response.status)
            length = _positive_int(response.headers.get("Content-Length"))
            if response.status == 206:
                if downloaded and not can_resume:
                    # We requested a full response to prove the old prefix.
                    # An unsolicited suffix cannot establish entity identity.
                    raise _TransferIssue("range_invalid", 206)
                match = re.fullmatch(r"bytes (\d{1,20})-(\d{1,20})/(\d{1,20})", response.headers.get("Content-Range", ""))
                if not match:
                    raise _TransferIssue("range_invalid", 206)
                first, last, entity_total = map(int, match.groups())
                if (first != downloaded or last < first or last >= entity_total
                        or (length is not None and length != last - first + 1)):
                    raise _TransferIssue("range_invalid", 206)
                if can_resume and (not _same_entity(validators, response.headers)
                                   or (total is not None and total != entity_total)):
                    restart("Máy chủ đã đổi tệp hoặc dấu xác nhận; đang tải lại đúng bản đã chọn từ đầu…")
                    raise _TransferIssue("resource_changed", 206)
                total = entity_total
                response_end = last + 1
            else:
                if can_resume:
                    _diagnostic(send, _TransferIssue("range_ignored", 200), attempt, downloaded)
                if downloaded and total is not None and length is not None and total != length:
                    restart("Nguồn đã đổi kích thước; đang tải lại đúng bản đã chọn…")
                    raise _TransferIssue("resource_changed", 200)
                total = length if length is not None else (total if downloaded else None)
                response_end = total
            chunks = iter(response.iter_content(256 * 1024))
            prefix_tail = b""
            if downloaded and response.status == 200:
                # With no trusted HTTP validator (or an ignored Range), prove
                # every retained byte against this ONE response before append.
                # The rest of the same response is the only permitted suffix.
                # This re-reads the prefix over the network without discarding it.
                verified, remote_digest = 0, hashlib.sha256()
                report("Đang đối chiếu phần đã tải với máy chủ trước khi nối tiếp…", waiting=True)
                for chunk in chunks:
                    if not chunk:
                        continue
                    take = min(len(chunk), downloaded - verified)
                    remote_digest.update(chunk[:take])
                    verified += take
                    if time.monotonic() - last_emit >= 0.2:
                        report(f"Đang đối chiếu phần đã tải: {verified / 1024**2:,.1f}/{downloaded / 1024**2:,.1f} MB…", waiting=True)
                        last_emit = time.monotonic()
                    if verified == downloaded:
                        prefix_tail = chunk[take:]
                        break
                if verified != downloaded:
                    raise _TransferIssue("incomplete_read", response.status)
                if remote_digest.digest() != digest.digest():
                    restart("Nội dung nguồn đã thay đổi; đang tải lại bản đã chọn…")
                    raise _TransferIssue("resource_changed", response.status)
                validators = _validators(response.headers)
                report("Đã đối chiếu đúng phần đã tải; đang tải tiếp…", waiting=True)
            if not downloaded:
                validators = _validators(response.headers)
                if not any(validators.values()):
                    _diagnostic(send, _TransferIssue("resume_validators_missing", response.status), attempt, downloaded)
            try:
                if total and shutil.disk_usage(output.parent).free < total - downloaded + 128 * 1024 * 1024:
                    raise MediaDownloadError(f"Không đủ dung lượng để tải video ({total / 1024**3:.2f} GB). Hãy giải phóng ổ đĩa rồi thử lại.", code="disk_full")
            except OSError as error:
                raise _disk_error(error) from None
            started = last_emit = time.monotonic()
            starting_bytes = downloaded
            report()
            network_error = None
            # Separate disk IO from socket reads: a write failure must not be
            # retried or described as the user's Internet disconnecting.
            try:
                with partial.open("ab" if downloaded else "wb") as target:
                    while True:
                        try:
                            if prefix_tail:
                                chunk, prefix_tail = prefix_tail, b""
                            else:
                                chunk = next(chunks)
                        except StopIteration:
                            break
                        except retry_errors as error:
                            network_error = error
                            break
                        if not chunk:
                            continue
                        if response_end and downloaded + len(chunk) > response_end:
                            network_error = _TransferIssue("length_mismatch", response.status)
                            break
                        written = target.write(chunk)
                        if written != len(chunk):
                            raise OSError(errno.EIO, "Incomplete local write")
                        digest.update(chunk)
                        downloaded += written
                        if (downloaded - checkpoint_bytes >= 4 * 1024 * 1024
                                or time.monotonic() - checkpoint_time >= 1
                                or not checkpoint_bytes):
                            target.flush()
                            _save_checkpoint(checkpoint_path, partial, video_id=info["id"],
                                             format_identity=format_identity, total=total, downloaded=downloaded,
                                             validators=validators, digest=digest)
                            checkpoint_bytes, checkpoint_time = downloaded, time.monotonic()
                        if time.monotonic() - last_emit >= 0.2:
                            report()
                            last_emit = time.monotonic()
            except OSError as error:
                raise _disk_error(error) from None
            if network_error:
                raise network_error
            if not downloaded or (total and downloaded != total):
                raise _TransferIssue("incomplete_read", response.status)
            report()
            break
        except MediaDownloadError as error:
            error.downloaded_bytes = downloaded
            raise
        except retry_errors as error:
            last_code, last_status = _diagnostic(send, error, attempt, downloaded)
            resumable = _save_checkpoint(checkpoint_path, partial, video_id=info["id"],
                                        format_identity=format_identity, total=total, downloaded=downloaded,
                                        validators=validators, digest=digest)
            if attempt == MAX_MEDIA_RETRIES:
                if last_code in {"read_timeout", "connection_timeout"}:
                    message = "Máy chủ video phản hồi quá chậm sau nhiều lần kết nối lại."
                elif last_code == "http_error":
                    message = f"Máy chủ video từ chối tải (HTTP {last_status})." if last_status else "Máy chủ video chưa chấp nhận tải."
                elif last_code in {"range_invalid", "resource_changed", "content_type", "length_mismatch"}:
                    message = "Máy chủ video trả dữ liệu không khớp nên chưa thể tải hoàn tất."
                else:
                    message = "Luồng tải từ máy chủ video bị ngắt sau nhiều lần kết nối lại."
                if resumable:
                    message += f" Đã giữ {downloaded / 1024**2:,.1f} MB; bấm Bắt đầu dịch để thử tải tiếp."
                else:
                    message += " Hãy thử lại sau; máy chủ chưa cho phép nối tiếp an toàn."
                raise MediaDownloadError(message, resumable=resumable) from None
            kept = f"giữ {downloaded / 1024**2:,.1f} MB đã tải" if resumable else "sẽ tải lại từ đầu để bảo đảm tệp đúng"
            report(f"Đang kết nối lại {attempt + 1}/{MAX_MEDIA_RETRIES}; {kept}…", waiting=True)
        finally:
            if response is not None:
                response.close()
        # This worker runs in a killable child process. Short bounded backoff
        # never delays the parent's explicit Stop/cancellation signal.
        time.sleep(min(0.5 * 2**attempt, 4.0))
    send({"kind": "progress", "source": "douyin-public", "phase": "prepare",
          "stage": "Đã tải xong; đang kiểm tra tệp video…", "progress_pct": None})
    duration = verify_video(partial)
    expected_duration = info.get("duration")
    if expected_duration and abs(duration - expected_duration) > max(5, expected_duration * 0.01):
        raise MediaDownloadError("Thời lượng video tải về không khớp nguồn. Hãy thử lại.")
    try:
        partial.replace(output)
        checkpoint_path.unlink(missing_ok=True)
    except OSError as error:
        raise _disk_error(error) from None
    return {"file_path": str(output), "title": info.get("title") or f"Douyin {info['id']}",
            "duration": duration, "source": "douyin-public"}


def progress_payload(data):
    status = data.get("status")
    if status == "finished":
        return {"phase": "prepare", "stage": "Đã tải dữ liệu; đang hoàn tất tệp video…", "progress_pct": None}
    downloaded = max(0, data.get("downloaded_bytes") or 0)
    # Estimated totals can change; only publish % when the server supplies a total.
    total = data.get("total_bytes")
    percent = round(min(100, downloaded * 100 / total), 1) if total and total > 0 else None
    result = {"phase": "download", "stage": "Đang tải video", "progress_pct": percent,
              "downloaded_bytes": downloaded, "total_bytes": total}
    for key in ("speed", "eta"):
        value = data.get(key)
        result[key] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
    return result


def single_video_downloader(options, url=None):
    import yt_dlp

    use_douyin_cookies = is_douyin_url(url) if url else False

    class SingleVideoYoutubeDL(yt_dlp.YoutubeDL):
        def build_request_director(self, handlers, preferences=None):
            if use_douyin_cookies:
                # Requests recreates cookie jars and drops host-only/HTTPS policy.
                # Urllib keeps the same jar through every request and redirect.
                handlers = [handler for handler in handlers if handler.RH_KEY == "Urllib"]
            return super().build_request_director(handlers, preferences)

        def process_ie_result(self, ie_result, download=True, extra_info=None):
            # noplaylist only selects a video when the URL also names a playlist.
            # Reject pure playlists before yt-dlp enumerates or downloads entries,
            # including playlists reached through a short-link/URL redirect.
            if ie_result and ie_result.get("_type") in {"playlist", "multi_video"}:
                raise ValueError("unsupported url: playlist")
            return super().process_ie_result(ie_result, download, extra_info)

    if use_douyin_cookies:
        # Session changes made by yt-dlp belong to this worker only. Never read
        # Chrome or pass the private import file as yt-dlp's persistent cookiefile.
        options = {**options, "cookiefile": None, "cookiesfrombrowser": None}
    downloader = SingleVideoYoutubeDL(options)
    if use_douyin_cookies:
        try:
            imported = load_douyin_cookiejar()
            downloader.cookiejar.set_policy(cookie_policy())
            for cookie in imported:
                downloader.cookiejar.set_cookie(cookie)
        except Exception:
            downloader.close()
            raise
    return downloader


def main():
    url, prefix = sys.argv[1:3]
    last_emit = 0

    def send(data):
        print(json.dumps(data, ensure_ascii=False), flush=True)

    def progress(data):
        nonlocal last_emit
        now = time.monotonic()
        if data.get("status") == "downloading" and now - last_emit < 0.2:
            return
        last_emit = now
        send({"kind": "progress", **progress_payload(data)})

    class QuietLogger:
        def debug(self, *_): pass
        def warning(self, *_): pass
        def error(self, *_): pass

    options = {
        "outtmpl": prefix + ".%(ext)s",
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
        "no_warnings": True, "noprogress": True, "logger": QuietLogger(),
        "socket_timeout": 12, "retries": 1, "extractor_retries": 1,
        "fragment_retries": 1, "file_access_retries": 1,
        "progress_hooks": [progress],
        "postprocessor_hooks": [lambda data: send({"kind": "progress", "phase": "prepare", "stage": "Đang ghép hình và âm thanh…", "progress_pct": None})],
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
    }
    try:
        if is_douyin_url(url):
            try:
                info = resolve_douyin(url, lambda data: send({"kind": "progress", "source": "douyin-public", **data}))
            except DouyinResolveError as error:
                partial, checkpoint_path = Path(prefix + ".mp4.part"), Path(prefix + ".resume.json")
                if partial.exists() or checkpoint_path.exists():
                    # A resumed public-source file must never enter yt-dlp's
                    # automatic .part append against a different rendition.
                    # Verify storage integrity now; source identity is checked
                    # again against fresh resolution before any later append.
                    send({"kind": "progress", "phase": "prepare", "source": "douyin-public",
                          "stage": "Chưa lấy được nguồn; đang kiểm tra phần tải đã giữ…", "progress_pct": None})
                    checkpoint = None
                    if not partial.is_symlink() and not checkpoint_path.is_symlink():
                        checkpoint, _ = _load_checkpoint(checkpoint_path, partial)
                    downloaded = checkpoint["downloaded_bytes"] if checkpoint else 0
                    _diagnostic(send, error, 0, downloaded)
                    if checkpoint:
                        raise MediaDownloadError(
                            f"Chưa lấy lại được thông tin bản video đã chọn. Đã giữ {downloaded / 1024**2:,.1f} MB; bấm Bắt đầu dịch để thử tải tiếp.",
                            resumable=True,
                        ) from None
                    raise MediaDownloadError("Chưa lấy lại được nguồn và phần tải cũ không đủ điều kiện nối tiếp. Hãy thử lại để tải từ đầu.") from None
                _diagnostic(send, error, 0)
                send({"kind": "progress", "phase": "resolve", "source": "yt-dlp",
                      "stage": "API công khai chưa lấy được video; đang thử trình tải dự phòng…", "progress_pct": None})
            else:
                result = download_resolved_video(info, prefix, send)
                send({"kind": "result", "result": result})
                return 0
        with single_video_downloader(options, url) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info or info.get("_type") in {"playlist", "multi_video"}:
                raise ValueError("unsupported url")
            output = Path(ydl.prepare_filename(info))
            if not output.exists():
                output = output.with_suffix(".mp4")
            if not output.is_file():
                raise ValueError("No completed video file")
        send({"kind": "result", "result": {"file_path": str(output), "title": info.get("title") or output.stem,
                                             "duration": info.get("duration")}})
    except (DouyinCookieError, MediaDownloadError) as error:
        if isinstance(error, MediaDownloadError) and error.code in {"disk_full", "disk_error"}:
            _diagnostic(send, error, 0, getattr(error, "downloaded_bytes", 0))
        event = {"kind": "error", "message": str(error)}
        if isinstance(error, MediaDownloadError) and error.resumable:
            event["resumable"] = True
        send(event)
        return 1
    except Exception as error:
        send({"kind": "error", "message": friendly_download_error(error, urlsplit(url).hostname)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
