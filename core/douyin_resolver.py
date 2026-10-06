"""Anonymous, bounded Douyin resolution without browser sessions or cookies.

The public feed and exact-ID matching strategy follows ucmao/media-parser
(MIT, copyright 2025-2026 ucmao), commit 48ac2b915e4a8df73c8faa54797b5d5b0e0a648c.
See THIRD_PARTY_NOTICES.md for the upstream notice. No upstream code is executed.
"""

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit


TIMEOUT = 6
MEDIA_READ_TIMEOUT = 30
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 4
SHARE_HOSTS = frozenset({"douyin.com", "www.douyin.com", "v.douyin.com",
                         "m.douyin.com", "www.iesdouyin.com"})
FEED_HOSTS = frozenset({"api5-normal-c-hl.amemv.com", "aweme.snssdk.com"})
MEDIA_HOSTS = frozenset({"api-play-hl.amemv.com", "api-hl.amemv.com", "www.iesdouyin.com"})
MEDIA_ROOTS = ("douyinvod.com", "zjcdn.com")
FEED_ENDPOINTS = (
    "https://api5-normal-c-hl.amemv.com/aweme/v1/feed/",
    "https://aweme.snssdk.com/aweme/v1/feed/",
)
USER_AGENT = (
    "com.ss.android.ugc.aweme/290101 (Linux; U; Android 10; zh_CN; Pixel 4; "
    "Build/QQ3A.200805.001; Cronet/TTNetVersion:5f9037be 2023-01-13 QuicVersion:4668bb42 2022-11-21)"
)
_SAFE_HEADERS = frozenset({"accept", "user-agent", "range", "if-range"})


class DouyinResolveError(RuntimeError):
    """A sanitized message that can be shown in Studio."""

    def __init__(self, message, *, code="resolve_failed", status_code=None,
                 error_type=None, stage=None):
        super().__init__(message)
        # Only fixed diagnostic labels are supplied here, never a raw upstream
        # exception, response body, or signed media URL.
        self.code = code
        self.status_code = status_code
        self.error_type = error_type
        self.stage = stage


def _transport_error(error, stage):
    """Keep the useful cause without serializing upstream exception text."""
    if isinstance(error, TimeoutError):
        code = "read_timeout" if stage == "read" else "connection_timeout"
        message = ("Máy chủ video Douyin phản hồi quá chậm khi tải. Hãy thử lại."
                   if stage == "read" else "Hết thời gian chờ kết nối máy chủ Douyin. Hãy thử lại.")
        error_type = "TimeoutError"
    elif isinstance(error, http.client.RemoteDisconnected):
        code, error_type = "connection_closed", "RemoteDisconnected"
        message = "Máy chủ video Douyin đóng kết nối trước khi trả dữ liệu. Hãy thử lại."
    elif isinstance(error, ConnectionResetError):
        code, error_type = "connection_reset", "ConnectionResetError"
        message = "Kết nối đến máy chủ video Douyin bị đặt lại khi tải. Hãy thử lại."
    elif isinstance(error, http.client.IncompleteRead):
        code, error_type = "incomplete_read", "IncompleteRead"
        message = "Máy chủ video Douyin ngắt luồng trước khi tải đủ tệp. Hãy thử lại."
    elif isinstance(error, ssl.SSLError):
        code, error_type = "tls_error", "SSLError"
        message = "Không thiết lập được kết nối bảo mật tới máy chủ Douyin. Hãy thử lại."
    else:
        code, error_type = "transport_error", "TransportError"
        message = "Kết nối đến máy chủ video Douyin bị gián đoạn. Hãy thử lại."
    return DouyinResolveError(message, code=code, error_type=error_type, stage=stage)


class PublicResponse:
    """Streaming response owning its connection, without an ambient cookie jar."""

    def __init__(self, response, connection, url):
        self._response = response
        self._connection = connection
        self.status = self.status_code = response.status
        self.headers = response.headers
        self.url = url

    def read(self, size=-1):
        try:
            return self._response.read(size)
        except (OSError, http.client.HTTPException) as error:
            raise _transport_error(error, "read") from None

    def set_read_timeout(self, timeout):
        # With Connection: close, HTTPConnection may already have detached its
        # socket; the response's buffered reader still owns it until EOF.
        sock = getattr(self._connection, "sock", None)
        if sock is None:
            reader = getattr(self._response, "fp", None)
            sock = getattr(getattr(reader, "raw", None), "_sock", None)
        if sock is not None:
            try:
                sock.settimeout(timeout)
            except OSError as error:
                raise _transport_error(error, "read") from None

    def iter_content(self, chunk_size=64 * 1024):
        while chunk := self.read(chunk_size):
            yield chunk

    def close(self):
        self._response.close()
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _validated_url(url, allowed_hosts, allowed_roots=()):
    """Validate every hop before a request; preserve signed paths and queries."""
    if not isinstance(url, str) or len(url) > 16384 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise DouyinResolveError("Địa chỉ tải Douyin không hợp lệ.")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid_host = host in allowed_hosts or any(
            host == root or host.endswith("." + root) for root in allowed_roots
        )
        if (parsed.scheme.lower() not in {"http", "https"} or not valid_host
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80 if parsed.scheme.lower() == "http" else 443)
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
                or ".." in host):
            raise ValueError
    except (ValueError, UnicodeError):
        raise DouyinResolveError("Địa chỉ tải Douyin không thuộc nguồn được hỗ trợ.") from None
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _public_addresses(host):
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError
        return addresses
    except (OSError, ValueError):
        raise DouyinResolveError("Không kết nối được máy chủ Douyin qua địa chỉ mạng công khai.") from None


def _request_once(url, headers):
    """Pin a validated public DNS answer while retaining HTTPS hostname checks."""
    parsed = urlsplit(url)
    addresses = _public_addresses(parsed.hostname)
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=TIMEOUT,
                                             context=ssl.create_default_context())
    # HTTPSConnection still uses its original hostname for TLS SNI and certificate
    # verification; only the TCP destination is pinned, preventing DNS rebinding.
    address = addresses[0]
    connection._create_connection = lambda destination, timeout, source_address=None: socket.create_connection(
        (address, 443), timeout, source_address
    )
    try:
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request("GET", target, headers={**headers, "Connection": "close", "Accept-Encoding": "identity"})
        return PublicResponse(connection.getresponse(), connection, url)
    except (OSError, http.client.HTTPException, UnicodeError, ValueError) as error:
        connection.close()
        raise _transport_error(error, "connect") from None


def _open_url(url, allowed_hosts, allowed_roots=(), headers=None, follow_redirects=True):
    safe_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    for name, value in (headers or {}).items():
        if (not isinstance(name, str) or name.lower() not in _SAFE_HEADERS
                or not isinstance(value, str) or re.search(r"[\r\n\x00]", value)):
            raise DouyinResolveError("Header tải Douyin không được hỗ trợ.")
        safe_headers[name] = value
    for hop in range(MAX_REDIRECTS + 1):
        url = _validated_url(url, allowed_hosts, allowed_roots)
        try:
            response = _request_once(url, safe_headers)
        except (OSError, http.client.HTTPException) as error:
            raise _transport_error(error, "connect") from None
        if response.status not in (301, 302, 303, 307, 308) or not follow_redirects:
            return response
        location = response.headers.get("Location")
        response.close()
        if not location or hop >= MAX_REDIRECTS:
            raise DouyinResolveError("Link Douyin chuyển hướng quá nhiều lần hoặc không còn hợp lệ.")
        url = urljoin(url, location)
    raise DouyinResolveError("Không mở được link Douyin.")


def validate_media_url(url):
    """Normalize and allowlist a media URL; connection-time checks also pin DNS."""
    return _validated_url(url, MEDIA_HOSTS, MEDIA_ROOTS)


def open_public_media(url, headers=None):
    """Open an allowed media stream. Callers must close it and bound disk writes."""
    response = _open_url(url, MEDIA_HOSTS, MEDIA_ROOTS, headers=headers)
    if response.status not in (200, 206):
        response.close()
        raise DouyinResolveError(
            f"Máy chủ video Douyin trả HTTP {response.status}, chưa tải được tệp. Hãy thử lại.",
            code="http_error", status_code=response.status, error_type="HTTPError", stage="http")
    if isinstance(response, PublicResponse):
        try:
            response.set_read_timeout(MEDIA_READ_TIMEOUT)
        except DouyinResolveError:
            response.close()
            raise
    return response


def _read_json(response):
    try:
        if response.status != 200:
            raise DouyinResolveError("Douyin chưa trả dữ liệu video. Hãy thử lại sau.")
        raw_length = response.headers.get("Content-Length")
        if raw_length and int(raw_length) > MAX_JSON_BYTES:
            raise DouyinResolveError("Dữ liệu Douyin vượt giới hạn xử lý.")
        chunks, size, started = [], 0, time.monotonic()
        while True:
            chunk = response.read(min(64 * 1024, MAX_JSON_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_JSON_BYTES or time.monotonic() - started > TIMEOUT:
                raise DouyinResolveError("Dữ liệu Douyin quá lớn hoặc phản hồi quá chậm.")
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise DouyinResolveError("Dữ liệu Douyin trả về chưa hợp lệ.") from None
    finally:
        response.close()


def _video_id(url):
    parsed = urlsplit(url)
    match = re.search(r"/(?:share/)?(?:video|note)/(\d{15,22})(?:/|$)", parsed.path)
    if match:
        return match.group(1)
    for key in ("modal_id", "aweme_id", "item_id"):
        value = parse_qs(parsed.query).get(key, [""])[0]
        if re.fullmatch(r"\d{15,22}", value):
            return value
    return None


def _resolve_id(url):
    url = _validated_url(url, SHARE_HOSTS)
    for hop in range(MAX_REDIRECTS + 1):
        video_id = _video_id(url)
        if video_id:
            return video_id
        response = _open_url(url, SHARE_HOSTS, follow_redirects=False)
        try:
            location = response.headers.get("Location")
            if response.status not in (301, 302, 303, 307, 308) or not location or hop >= MAX_REDIRECTS:
                break
            url = _validated_url(urljoin(url, location), SHARE_HOSTS)
        finally:
            response.close()
    raise DouyinResolveError("Không tìm thấy mã video trong link Douyin. Hãy sao chép lại link Chia sẻ.")


def _number(value, maximum=10**15):
    try:
        number = float(value)
        return int(number) if 0 <= number <= maximum else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _formats(video):
    result, seen = [], set()

    def add(address, metadata=None, codec=None, original=False):
        if not isinstance(address, dict):
            return
        metadata = metadata if isinstance(metadata, dict) else {}
        raw_urls = address.get("url_list", [])
        if not isinstance(raw_urls, list):
            return
        urls = []
        for raw in raw_urls[:20]:
            try:
                normalized = validate_media_url(raw)
            except DouyinResolveError:
                continue
            if normalized not in urls:
                urls.append(normalized)
        key = tuple(urls)
        if not urls or key in seen:
            return
        seen.add(key)
        declared_codec = str(metadata.get("codec_type") or address.get("codec_type") or "").lower()
        if codec is None:
            codec = "h265" if metadata.get("is_h265") in (1, True, "1") or declared_codec in ("h265", "hevc") else "h264"
        result.append({"urls": urls, "codec": codec, "original": original,
                       "width": _number(address.get("width") or metadata.get("width") or video.get("width"), 32768),
                       "height": _number(address.get("height") or metadata.get("height") or video.get("height"), 32768),
                       "size": _number(address.get("data_size") or metadata.get("data_size")),
                       "bitrate": _number(metadata.get("bit_rate") or address.get("bit_rate"))})

    rates = video.get("bit_rate") or []
    if isinstance(rates, list):
        for rate in rates[:40]:
            if isinstance(rate, dict):
                add(rate.get("play_addr"), rate)
    add(video.get("play_addr_h264"), codec="h264")
    add(video.get("play_addr"), video)
    add(video.get("play_addr_265"), codec="h265")
    download = video.get("download_addr")
    uri = download.get("uri") if isinstance(download, dict) else None
    if isinstance(uri, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", uri):
        # The source-file endpoint is different from the watermarked download
        # rendition. Its real size is read from the media response, not metadata.
        add({"url_list": ["https://www.iesdouyin.com/aweme/v1/play/?" + urlencode({"video_id": uri, "ratio": "default", "line": "0"})],
             "width": video.get("width"), "height": video.get("height")}, codec="h264", original=True)
    result.sort(key=lambda item: (item["codec"] != "h264", item["width"] * item["height"], item["bitrate"]))
    return result


def resolve_douyin(url, progress=None):
    """Resolve a cleaned public video URL; never substitute another feed item."""
    video_id = _resolve_id(url)
    if progress:
        progress({"phase": "resolve", "stage": "Đang lấy thông tin video Douyin…", "progress_pct": None})
    for endpoint in FEED_ENDPOINTS:
        try:
            response = _open_url(endpoint + "?" + urlencode({"aweme_id": video_id, "aid": "1128"}),
                                 FEED_HOSTS, headers={"Accept": "application/json"})
            payload = _read_json(response)
            if payload.get("status_code", 0) != 0:
                continue
            items = payload.get("aweme_list")
            if not isinstance(items, list):
                continue
            item = next((item for item in items if isinstance(item, dict)
                         and str(item.get("aweme_id", "")) == video_id), None)
            if item is None or not isinstance(item.get("video"), dict):
                continue
            formats = _formats(item["video"])
            if not formats:
                continue
            return {"id": video_id, "title": str(item.get("desc") or "Video Douyin")[:2000],
                    "duration": _number(item["video"].get("duration") or item.get("duration")) / 1000,
                    "formats": formats}
        except DouyinResolveError:
            continue
    raise DouyinResolveError("Douyin chưa cung cấp video công khai cho link này. Hãy thử lại sau hoặc chọn tệp trên máy.")
