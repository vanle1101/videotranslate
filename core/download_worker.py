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
import uuid
from collections import deque
from pathlib import Path
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.downloader import friendly_download_error
from core.douyin_cookies import (
    DouyinCookieError, cookie_policy, douyin_cookie_revision, get_douyin_cookie_status,
    is_douyin_url, load_douyin_cookiejar,
)
from core.douyin_resolver import DouyinResolveError, open_public_media, resolve_douyin, _retry_after_seconds, _video_id
from core.download_ranges import adaptive_chunks, RangeTransferError


class MediaDownloadError(RuntimeError):
    """A safe message, without signed CDN addresses or credentials."""

    def __init__(self, message, *, resumable=False, code=None, status_code=None,
                 retry_after_seconds=None, cookie_revision=None):
        super().__init__(message)
        self.resumable = resumable
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = _retry_after_seconds(retry_after_seconds)
        self.cookie_revision = cookie_revision


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
    def __init__(self, code, status_code=None, *, retry_after=None):
        self.code, self.status_code = code, status_code
        # Keep only the resolver's already-sanitized value.  A raw
        # Retry-After header must never reach the worker's sleep loop.
        self.retry_after = _retry_after_seconds(retry_after)


def _disk_error(error):
    if getattr(error, "errno", None) == errno.ENOSPC:
        return MediaDownloadError("Ổ đĩa đã hết dung lượng khi lưu video. Hãy giải phóng dung lượng rồi thử lại.", code="disk_full")
    return MediaDownloadError("Không đọc hoặc ghi được tệp video trên ổ đĩa. Kiểm tra dung lượng và quyền ghi của thư mục tải.", code="disk_error")


def _has_imported_douyin_cookies():
    try:
        status = get_douyin_cookie_status()
        return bool(isinstance(status, dict) and status.get("configured") is True
                    and type(status.get("count")) is int and status["count"] > 0)
    except Exception:
        return False


def _missing_douyin_cookie_error(*, resumable=False):
    """Distinguish a never-imported session from an expired saved cookie file."""
    try:
        status = get_douyin_cookie_status()
    except Exception:
        status = {}
    code = "LOGIN_REQUIRED" if isinstance(status, dict) and status.get("stored") is True else "AUTH_REQUIRED"
    return MediaDownloadError(
        _safe_download_failure(code), resumable=resumable, code=code, status_code=403,
        cookie_revision=douyin_cookie_revision())


def _classify_cookie_download_error(error):
    """Map downloader diagnostics to safe, stable codes without returning text."""
    detail = str(error).lower()
    status = getattr(error, "status_code", None)
    if status == 429 or "http error 429" in detail or "too many requests" in detail:
        return "RATE_LIMITED"
    if any(token in detail for token in ("captcha", "verify you are human", "verification required",
                                         "complete the verification", "verify you are not")):
        return "CAPTCHA_REQUIRED"
    if any(token in detail for token in ("login", "log in", "sign in", "fresh cookies",
                                         "cookies are needed", "session expired", "session has expired")):
        return "LOGIN_REQUIRED"
    if any(token in detail for token in ("signed url expired", "signature expired", "url has expired",
                                         "link expired", "source expired")):
        return "SOURCE_EXPIRED"
    if status == 404 or "http error 404" in detail or "video unavailable" in detail:
        return "SOURCE_EXPIRED"
    if status == 403 or "http error 403" in detail or "forbidden" in detail:
        return "AUTH_REJECTED"
    return "COOKIE_DOWNLOAD_FAILED"


def _retry_after_from_exception(error):
    """Read only a bounded Retry-After value from a wrapped local error object."""
    seen = set()
    current = error
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        value = getattr(current, "retry_after", None)
        if value is not None:
            delay = _retry_after_seconds(value)
            if delay is not None:
                return delay
        headers = getattr(current, "headers", None) or getattr(current, "hdrs", None)
        response = getattr(current, "response", None)
        if headers is None and response is not None:
            headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                value = headers.get("Retry-After") or headers.get("retry-after")
            except Exception:
                value = None
            delay = _retry_after_seconds(value)
            if delay is not None:
                return delay
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None


def _safe_download_failure(code, *, retry_after=None):
    messages = {
        "AUTH_REQUIRED": "Douyin yêu cầu phiên đăng nhập. Nhập tệp Netscape cookies.txt trong Cài đặt rồi tiếp tục tác vụ.",
        "CAPTCHA_REQUIRED": "Douyin vẫn yêu cầu CAPTCHA sau một lượt thử có xác thực. Hãy xác minh phiên bằng cách thông thường rồi nhập lại cookies.txt; Studio không vượt CAPTCHA.",
        "LOGIN_REQUIRED": "Phiên Douyin hết hạn hoặc chưa đăng nhập. Hãy đăng nhập bằng cách thông thường, xuất cookies.txt mới rồi nhập vào Cài đặt.",
        "SOURCE_EXPIRED": "Link hoặc địa chỉ video Douyin đã hết hạn/không còn khả dụng. Hãy lấy lại link từ video gốc.",
        "AUTH_REJECTED": "Douyin không chấp nhận cookie đã nhập hoặc video bị giới hạn quyền truy cập. Kiểm tra phiên đăng nhập và quyền xem video.",
        "RATE_LIMITED": "Máy chủ Douyin yêu cầu chờ trước khi tải tiếp.",
        "COOKIE_DOWNLOAD_FAILED": "Một lượt tải Douyin có cookie đã thất bại. Kiểm tra link, phiên đăng nhập và kết nối rồi thử lại.",
    }
    message = messages.get(code, "Không tải được video Douyin.")
    if code == "RATE_LIMITED" and retry_after is not None:
        message += f" Thử lại sau ít nhất {int(math.ceil(retry_after))} giây."
    return message


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
    retry_after = getattr(error, "retry_after", None)
    if (isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool)
            and 0 <= retry_after <= 3601):
        event["retry_after_seconds"] = retry_after
    send(event)
    return code, event.get("status_code")


def _positive_int(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _bounded_retry_after(value):
    return _retry_after_seconds(value)


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
    transfer_started = time.monotonic()
    transfer_bytes = 0
    network_seconds = disk_seconds = 0.0
    first_byte_seconds = None
    peak_speed = 0.0
    retry_count = 0
    strategy, connections = "single-stream", 1
    range_enabled = True
    samples = deque([(transfer_started, 0)])
    requests = []
    transports = []

    def open_media(url, headers=None):
        requests.append(1)
        opened = open_public_media(url, headers=headers) if headers else open_public_media(url)
        telemetry = getattr(opened, "telemetry", None)
        if isinstance(telemetry, dict):
            transports.append(telemetry)
        return opened

    def observe_ranges(selected, count, probing):
        nonlocal strategy, connections
        strategy, connections = selected, count
        report("Đang đo tốc độ tải Range…" if probing else None)
    stage_base = "Đang tải bản gốc Douyin" if chosen.get("original") else "Đang tải video Douyin"
    if chosen.get("height"):
        stage_base += f" {chosen['height']}p"

    def report(stage=None, *, waiting=False):
        nonlocal peak_speed
        now = time.monotonic()
        elapsed = max(now - transfer_started, .01)
        average = transfer_bytes / elapsed
        samples.append((now, transfer_bytes))
        while len(samples) > 2 and samples[1][0] < now - 3:
            samples.popleft()
        speed = None if waiting else (transfer_bytes - samples[0][1]) / max(now - samples[0][0], .01)
        peak_speed = max(peak_speed, speed or 0)
        message = stage or stage_base + (f" ({total / 1024**2:,.0f} MB)" if total else "")
        send({"kind": "progress", "source": "douyin-public", "phase": "download", "stage": message,
              "progress_pct": round(min(100, downloaded * 100 / total), 1) if total else None,
              "downloaded_bytes": downloaded, "total_bytes": total, "speed": speed,
              "average_speed": average, "strategy": strategy, "connections": connections,
              "retry_count": retry_count,
              "eta": (total - downloaded) / average if total and average and not waiting else None})

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

    retry_errors = (DouyinResolveError, OSError, http.client.HTTPException, _TransferIssue, RangeTransferError)
    last_code, last_status = "transport_error", None
    # A rate-limited CDN endpoint remains the same entity/candidate. Rotating
    # to another signed URL on every 429 can turn a temporary server decision
    # into a burst against all mirrors and also discard a useful Retry-After.
    retry_url_index = None
    rate_limit_retry_used = False
    for attempt in range(MAX_MEDIA_RETRIES + 1):
        retry_count = attempt
        if total and downloaded == total:
            break
        response = None
        sleep_after = None
        try:
            can_resume = downloaded > 0 and any(validators.values())
            request_headers = {"Range": f"bytes={downloaded}-", "If-Range": validators["etag"] or validators["last_modified"]} if can_resume else None
            current_url_index = retry_url_index if retry_url_index is not None else attempt % len(urls)
            retry_url_index = None
            url = urls[current_url_index]
            response = open_media(url, headers=request_headers)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if response.status not in (200, 206):
                retry_after = getattr(response, "retry_after", None)
                if retry_after is None and response.status == 429:
                    retry_after = _bounded_retry_after(response.headers.get("Retry-After"))
                raise _TransferIssue("http_error", response.status,
                                     retry_after=retry_after)
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
            chunks = iter(adaptive_chunks(response, total=total, offset=downloaded,
                                         etag=validators.get("etag") if downloaded else _validators(response.headers)["etag"],
                                         opener=open_media, observe=observe_ranges)
                          if range_enabled and response.status == 200 and not downloaded else response.iter_content(256 * 1024))
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
                                read_started = time.monotonic()
                                try:
                                    chunk = next(chunks)
                                finally:
                                    network_seconds += time.monotonic() - read_started
                        except StopIteration:
                            break
                        except retry_errors as error:
                            network_error = error
                            break
                        if not chunk:
                            continue
                        if first_byte_seconds is None:
                            first_byte_seconds = time.monotonic() - transfer_started
                        if response_end and downloaded + len(chunk) > response_end:
                            network_error = _TransferIssue("length_mismatch", response.status)
                            break
                        write_started = time.monotonic()
                        written = target.write(chunk)
                        disk_seconds += time.monotonic() - write_started
                        if written != len(chunk):
                            raise OSError(errno.EIO, "Incomplete local write")
                        digest.update(chunk)
                        downloaded += written
                        transfer_bytes += written
                        if (downloaded - checkpoint_bytes >= 4 * 1024 * 1024
                                or time.monotonic() - checkpoint_time >= 1
                                or not checkpoint_bytes):
                            write_started = time.monotonic()
                            target.flush()
                            _save_checkpoint(checkpoint_path, partial, video_id=info["id"],
                                             format_identity=format_identity, total=total, downloaded=downloaded,
                                             validators=validators, digest=digest)
                            checkpoint_bytes, checkpoint_time = downloaded, time.monotonic()
                            disk_seconds += time.monotonic() - write_started
                        if time.monotonic() - last_emit >= 0.2:
                            report()
                            last_emit = time.monotonic()
                    write_started = time.monotonic()
                    target.flush()
                    disk_seconds += time.monotonic() - write_started
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
            status_code = getattr(error, "status_code", None)
            # HTTP 403 is an access/authentication decision. Retrying the same
            # signed URL (or falling through to yt-dlp) cannot repair it and
            # may repeatedly trigger service-side protection.
            if status_code == 403:
                _diagnostic(send, error, attempt, downloaded)
                resumable = _save_checkpoint(checkpoint_path, partial, video_id=info["id"],
                                            format_identity=format_identity, total=total, downloaded=downloaded,
                                            validators=validators, digest=digest)
                raise MediaDownloadError(
                    "Máy chủ Douyin từ chối truy cập video (HTTP 403).", resumable=resumable,
                    code="http_error", status_code=403) from None
            if status_code == 429:
                _diagnostic(send, error, attempt, downloaded)
                resumable = _save_checkpoint(checkpoint_path, partial, video_id=info["id"],
                                            format_identity=format_identity, total=total, downloaded=downloaded,
                                            validators=validators, digest=digest)
                sleep_after = getattr(error, "retry_after", None)
                if (rate_limit_retry_used or sleep_after is None or sleep_after > 60):
                    raise MediaDownloadError(
                        _safe_download_failure("RATE_LIMITED", retry_after=sleep_after) + " (HTTP 429)",
                        resumable=resumable, code="RATE_LIMITED", status_code=429,
                        retry_after_seconds=sleep_after) from None
                rate_limit_retry_used = True
                retry_url_index = current_url_index
            else:
                if isinstance(error, RangeTransferError) or strategy == "parallel-range":
                    range_enabled = False
                    strategy, connections = "single-stream", 1
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
        # Long server restrictions were deferred above. Short valid waits
        # take precedence over the normal bounded exponential backoff.
        delay = max(sleep_after, min(0.5 * 2**attempt, 4.0)) if sleep_after is not None else min(0.5 * 2**attempt, 4.0)
        time.sleep(delay)
    transfer_elapsed = max(time.monotonic() - transfer_started, .01)
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
    elapsed = max(time.monotonic() - transfer_started, .01)
    return {"file_path": str(output), "title": info.get("title") or f"Douyin {info['id']}",
            "duration": duration, "source": "douyin-public",
            "download_metrics": {"downloaded_bytes": transfer_bytes, "file_bytes": downloaded,
                                 "elapsed_seconds": transfer_elapsed, "end_to_end_seconds": elapsed,
                                 "network_read_seconds": network_seconds,
                                 "disk_write_seconds": disk_seconds, "ttfb_seconds": first_byte_seconds,
                                 "average_speed": transfer_bytes / transfer_elapsed, "peak_speed": peak_speed,
                                 "retry_count": retry_count, "http_requests": len(requests),
                                 "strategy": strategy, "connections": connections,
                                 "wire_bytes": sum(item.get("bytes_received", 0) for item in transports),
                                 "transport_samples": transports}}


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


def single_video_downloader(options, url=None, *, use_imported_douyin_cookies=True):
    import yt_dlp

    use_douyin_cookies = bool(use_imported_douyin_cookies and is_douyin_url(url)) if url else False

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


def _download_with_imported_douyin_cookies(url, prefix, options, send):
    """Make exactly one yt-dlp attempt using only the imported Douyin cookie jar."""
    if not _has_imported_douyin_cookies():
        raise _missing_douyin_cookie_error()

    revision = douyin_cookie_revision()
    temporary_prefix = Path(f"{prefix}.cookie-{uuid.uuid4().hex}")
    target_prefix = Path(prefix)
    fallback_options = {
        **options,
        "outtmpl": str(temporary_prefix) + ".%(ext)s",
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
        "file_access_retries": 0,
        "continuedl": False,
        "overwrites": False,
    }
    send({"kind": "progress", "source": "douyin-cookie-ytdlp", "phase": "resolve",
          "stage": "Nguồn công khai yêu cầu quyền truy cập; đang thử một lượt bằng cookie Douyin đã nhập…",
          "progress_pct": None})
    try:
        with single_video_downloader(fallback_options, url, use_imported_douyin_cookies=True) as ydl:
            info = ydl.extract_info(url, download=True)
            if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                raise ValueError("unsupported url")
            prepared = Path(ydl.prepare_filename(info))
            candidates = [prepared]
            if not prepared.exists():
                candidates.extend(temporary_prefix.parent.glob(temporary_prefix.name + ".*"))
            output = next((path for path in candidates
                           if path.is_file() and not path.is_symlink()
                           and path.resolve().parent == temporary_prefix.parent.resolve()
                           and path.name.startswith(temporary_prefix.name + ".")
                           and not path.name.endswith((".part", ".ytdl", ".tmp"))), None)
            if output is None or output.stat().st_size <= 0:
                raise ValueError("video output missing")
        duration = verify_video(output)
        suffix = output.suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,8}", output.suffix.lower()) else ".mp4"
        # Keep the canonical video_<id>.<ext> name so VideoDownloader can
        # validate and index the result for restart-safe cache reuse.
        published = target_prefix.with_suffix(suffix)
        if published.exists() or published.is_symlink():
            raise OSError("fallback output already exists")
        output.replace(published)
        return {"file_path": str(published), "title": str(info.get("title") or f"Douyin {output.stem}")[:2000],
                "duration": duration, "source": "douyin-cookie-ytdlp"}
    except Exception as error:
        code = _classify_cookie_download_error(error)
        retry_after = _retry_after_from_exception(error)
        error_status = getattr(error, "status_code", None)
        if type(error_status) is not int or not 100 <= error_status <= 599:
            error_status = 429 if code == "RATE_LIMITED" else 403 if code == "AUTH_REJECTED" else None
        raise MediaDownloadError(_safe_download_failure(code, retry_after=retry_after), code=code,
                                 status_code=error_status,
                                 retry_after_seconds=retry_after, cookie_revision=revision) from None


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
                if error.status_code == 403:
                    resumable = False
                    downloaded = 0
                    if partial.exists() or checkpoint_path.exists():
                        send({"kind": "progress", "phase": "prepare", "source": "douyin-public",
                              "stage": "Chưa lấy được nguồn; đang kiểm tra phần tải đã giữ…", "progress_pct": None})
                        checkpoint = None
                        if not partial.is_symlink() and not checkpoint_path.is_symlink():
                            checkpoint, _ = _load_checkpoint(checkpoint_path, partial)
                        downloaded = checkpoint["downloaded_bytes"] if checkpoint else 0
                        resumable = checkpoint is not None
                    _diagnostic(send, error, 0, downloaded)
                    if _has_imported_douyin_cookies():
                        fallback_video_id = _video_id(url)
                        fallback_url = (f"https://www.douyin.com/video/{fallback_video_id}"
                                        if fallback_video_id else url)
                        result = _download_with_imported_douyin_cookies(fallback_url, prefix, options, send)
                        send({"kind": "result", "result": result})
                        return 0
                    raise _missing_douyin_cookie_error(resumable=resumable) from None
                if error.status_code == 429:
                    partial, checkpoint_path = Path(prefix + ".mp4.part"), Path(prefix + ".resume.json")
                    checkpoint = None
                    if partial.exists() and checkpoint_path.exists() and not partial.is_symlink() and not checkpoint_path.is_symlink():
                        checkpoint, _ = _load_checkpoint(checkpoint_path, partial)
                    downloaded = checkpoint["downloaded_bytes"] if checkpoint else 0
                    _diagnostic(send, error, 0, downloaded)
                    delay = _retry_after_seconds(error.retry_after)
                    raise MediaDownloadError(
                        _safe_download_failure("RATE_LIMITED", retry_after=delay),
                        resumable=checkpoint is not None, code="RATE_LIMITED", status_code=429,
                        retry_after_seconds=delay) from None
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
                        restriction = "Chưa lấy lại được thông tin bản video đã chọn. "
                        raise MediaDownloadError(
                            restriction + f"Đã giữ {downloaded / 1024**2:,.1f} MB; hãy thử lại khi nguồn cho phép tải tiếp.",
                            resumable=True,
                        ) from None
                    raise MediaDownloadError("Chưa lấy lại được nguồn và phần tải cũ không đủ điều kiện nối tiếp. Hãy thử lại để tải từ đầu.") from None
                _diagnostic(send, error, 0)
                send({"kind": "progress", "phase": "resolve", "source": "yt-dlp",
                      "stage": "API công khai chưa lấy được video; đang thử trình tải dự phòng…", "progress_pct": None})
            else:
                try:
                    result = download_resolved_video(info, prefix, send)
                except MediaDownloadError as error:
                    if error.status_code != 403:
                        raise
                    if not _has_imported_douyin_cookies():
                        raise _missing_douyin_cookie_error(resumable=error.resumable) from None
                    fallback_video_id = _video_id(url)
                    fallback_url = (f"https://www.douyin.com/video/{fallback_video_id}"
                                    if fallback_video_id else url)
                    result = _download_with_imported_douyin_cookies(fallback_url, prefix, options, send)
                send({"kind": "result", "result": result})
                return 0
        video_id = _video_id(url) if is_douyin_url(url) else None
        fallback_url = f"https://www.douyin.com/video/{video_id}" if video_id else url
        with single_video_downloader(options, fallback_url,
                                     use_imported_douyin_cookies=False) as ydl:
            info = ydl.extract_info(fallback_url, download=True)
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
        if isinstance(error, MediaDownloadError) and isinstance(error.code, str):
            event["code"] = error.code
        if isinstance(error, MediaDownloadError) and type(error.status_code) is int:
            event["status_code"] = error.status_code
        if isinstance(error, MediaDownloadError) and error.retry_after_seconds is not None:
            event["retry_after_seconds"] = error.retry_after_seconds
        if isinstance(error, MediaDownloadError) and error.cookie_revision is not None:
            event["cookie_revision"] = error.cookie_revision
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
