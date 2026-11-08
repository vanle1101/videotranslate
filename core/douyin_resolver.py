"""Anonymous, bounded Douyin resolution without browser sessions or cookies.

The public feed and exact-ID matching strategy follows ucmao/media-parser
(MIT, copyright 2025-2026 ucmao), commit 48ac2b915e4a8df73c8faa54797b5d5b0e0a648c.
See THIRD_PARTY_NOTICES.md for the upstream notice. No upstream code is executed.
"""

import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import time
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit


TIMEOUT = 6
MEDIA_READ_TIMEOUT = 30
# DNS can return many CDN addresses. Give the whole address set a bounded
# budget instead of paying the connect timeout once per address.
ADDRESS_FALLBACK_BUDGET = TIMEOUT * 2
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
                 error_type=None, stage=None, retry_after=None, category=None,
                 elapsed_ms=None, attempts=None, source_id=None):
        super().__init__(message)
        # Only fixed diagnostic labels are supplied here, never a raw upstream
        # exception, response body, or signed media URL.
        self.code = code
        self.status_code = status_code
        self.error_type = error_type
        self.stage = stage
        self.category = category
        self.source_id = source_id if isinstance(source_id, str) and re.fullmatch(r"\d{15,22}", source_id) else None
        self.elapsed_ms = (max(0, min(3_600_000, int(elapsed_ms)))
                           if isinstance(elapsed_ms, (int, float)) and not isinstance(elapsed_ms, bool)
                           and math.isfinite(elapsed_ms) else None)
        self.attempts = attempts if type(attempts) is int and 0 <= attempts <= 100 else None
        # Keep restrictions numeric and bounded. The worker honors short
        # waits and defers long waits rather than sleeping beyond its watchdog.
        self.retry_after = _retry_after_seconds(retry_after)


def _retry_after_seconds(value):
    """Return seconds, with a sentinel for waits longer than one hour.

    Never discard a valid long restriction and retry immediately. The worker
    defers such a request instead of sleeping beyond its watchdog budget.
    """
    try:
        if isinstance(value, bool):
            return None
        raw = str(value).strip()
        if len(raw) > 128:
            return None
        if re.fullmatch(r"\d+", raw):
            seconds = int(raw)
        else:
            date = parsedate_to_datetime(raw)
            if date.tzinfo is None:
                return None
            seconds = max(0, math.ceil(date.timestamp() - time.time()))
    except (TypeError, ValueError, OverflowError):
        return None
    return min(seconds, 3601) if seconds >= 0 else None


def _safe_url(url):
    """Keep endpoint identity for diagnostics while removing signed queries."""
    try:
        parsed = urlsplit(url)
        # Signed CDN paths can contain short opaque identifiers as well as
        # long tokens. Host identity is sufficient for diagnostics; never
        # persist or emit a path that could be replayed.
        return urlunsplit((parsed.scheme.lower(), (parsed.hostname or "").lower(), "", "", ""))
    except (TypeError, ValueError):
        return ""


def _http_protocol(response):
    version = getattr(response, "version", None)
    return {9: "HTTP/0.9", 10: "HTTP/1.0", 11: "HTTP/1.1", 20: "HTTP/2"}.get(version)


def _transport_error(error, stage):
    """Keep the useful cause without serializing upstream exception text."""
    if isinstance(error, socket.gaierror):
        code, error_type = "dns_failure", "DNSLookupError"
        message = "Không phân giải được tên máy chủ Douyin. Kiểm tra kết nối mạng rồi thử lại."
    elif isinstance(error, ssl.CertificateError):
        code, error_type = "tls_certificate_failure", "TLSCertificateError"
        message = "Chứng chỉ bảo mật của máy chủ Douyin không hợp lệ. Không thể tải an toàn."
    elif isinstance(error, ssl.SSLError):
        code, error_type = "tls_handshake_failure", "TLSHandshakeError"
        message = "Không thiết lập được kết nối bảo mật tới máy chủ Douyin. Hãy thử lại."
    elif isinstance(error, TimeoutError):
        code = "read_timeout" if stage == "read" else "connection_timeout"
        message = ("Máy chủ video Douyin phản hồi quá chậm khi tải. Hãy thử lại."
                   if stage == "read" else "Hết thời gian chờ kết nối máy chủ Douyin. Hãy thử lại.")
        error_type = "TimeoutError"
    elif isinstance(error, ConnectionRefusedError):
        code, error_type = "tcp_connection_failure", "ConnectionRefusedError"
        message = "Máy chủ Douyin từ chối kết nối mạng. Hãy kiểm tra mạng rồi thử lại."
    elif isinstance(error, http.client.RemoteDisconnected):
        code, error_type = "connection_closed", "RemoteDisconnected"
        message = "Máy chủ video Douyin đóng kết nối trước khi trả dữ liệu. Hãy thử lại."
    elif isinstance(error, ConnectionResetError):
        code, error_type = "connection_reset", "ConnectionResetError"
        message = "Kết nối đến máy chủ video Douyin bị đặt lại khi tải. Hãy thử lại."
    elif isinstance(error, http.client.IncompleteRead):
        code, error_type = "incomplete_read", "IncompleteRead"
        message = "Máy chủ video Douyin ngắt luồng trước khi tải đủ tệp. Hãy thử lại."
    elif isinstance(error, ConnectionError):
        code, error_type = "tcp_connection_failure", "ConnectionError"
        message = "Không thiết lập được kết nối mạng tới máy chủ Douyin. Hãy thử lại."
    else:
        code, error_type = ("tcp_connection_failure", "OSError") if stage == "connect" else ("transport_error", "TransportError")
        message = "Kết nối đến máy chủ video Douyin bị gián đoạn. Hãy thử lại."
    return DouyinResolveError(message, code=code, error_type=error_type, stage=stage,
                              category=code)


class PublicResponse:
    """Streaming response owning its connection, without an ambient cookie jar."""

    def __init__(self, response, connection, url, *, telemetry=None):
        self._response = response
        self._connection = connection
        self.status = self.status_code = response.status
        self.headers = response.headers
        self._request_url = url  # In-memory only; ranged requests need the intact signature.
        self.url = _safe_url(url)
        self.telemetry = telemetry if isinstance(telemetry, dict) else {}
        self.telemetry.setdefault("url", self.url)
        self.telemetry.setdefault("status_code", self.status)
        self.telemetry.setdefault("protocol", _http_protocol(response))
        self.telemetry.setdefault("bytes_received", 0)
        self._response_started = time.monotonic()
        self._first_byte_seen = False

    def read(self, size=-1):
        try:
            data = self._response.read(size)
            if data:
                if not self._first_byte_seen:
                    self._first_byte_seen = True
                    # read(size) returns a complete requested block, so this
                    # measures the first block after headers, not one byte.
                    self.telemetry.setdefault("first_chunk_ms", round(
                        max(0.0, (time.monotonic() - self._response_started) * 1000), 3))
                    self.telemetry.setdefault("first_byte_ms", self.telemetry["first_chunk_ms"])
                self.telemetry["bytes_received"] = self.telemetry.get("bytes_received", 0) + len(data)
            return data
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
        raise DouyinResolveError("Địa chỉ tải Douyin không hợp lệ.", code="invalid_url", stage="validate")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid_host = host in allowed_hosts or any(
            host == root or host.endswith("." + root) for root in allowed_roots
        )
        if parsed.scheme.lower() not in {"http", "https"} or not valid_host:
            raise DouyinResolveError("Địa chỉ video không thuộc miền Douyin được hỗ trợ.",
                                     code="unsupported_source", stage="validate")
        if (parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80 if parsed.scheme.lower() == "http" else 443)
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
                or ".." in host):
            raise DouyinResolveError("Địa chỉ tải Douyin không hợp lệ.", code="invalid_url", stage="validate")
    except DouyinResolveError:
        raise
    except (ValueError, UnicodeError):
        raise DouyinResolveError("Địa chỉ tải Douyin không hợp lệ.", code="invalid_url", stage="validate") from None
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _public_addresses(host):
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise DouyinResolveError("Máy chủ Douyin phân giải tới địa chỉ mạng không được phép.",
                                     code="invalid_dns_answer", error_type="DNSLookupError", stage="dns")
        return addresses
    except socket.gaierror:
        raise DouyinResolveError("Không phân giải được tên máy chủ Douyin. Kiểm tra kết nối mạng rồi thử lại.",
                                 code="dns_failure", error_type="DNSLookupError", stage="dns") from None
    except DouyinResolveError:
        raise
    except (OSError, ValueError):
        raise DouyinResolveError("Không nhận được địa chỉ mạng hợp lệ cho máy chủ Douyin.",
                                 code="dns_failure", error_type="DNSLookupError", stage="dns") from None


def _request_once(url, headers):
    """Pin a validated public DNS answer while retaining HTTPS hostname checks."""
    parsed = urlsplit(url)
    dns_started = time.monotonic()
    addresses = _public_addresses(parsed.hostname)
    dns_ms = round(max(0.0, (time.monotonic() - dns_started) * 1000), 3)
    last_error = None
    deadline = time.monotonic() + ADDRESS_FALLBACK_BUDGET
    # HTTPSConnection still uses the original hostname for TLS SNI and
    # certificate verification; only the TCP destination is pinned. If one
    # CDN address is unhealthy, try the next public answer before surfacing a
    # transport error to the bounded retry loop.
    for address in addresses:
        remaining = max(0.05, deadline - time.monotonic())
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=min(TIMEOUT, remaining),
                                                 context=ssl.create_default_context())
        timing = {"dns_ms": dns_ms, "peer_ip": address,
                  "url": _safe_url(url), "bytes_received": 0}
        tcp_ms = [None]
        original_connect = getattr(connection, "connect", None)

        def pinned_create(destination, timeout, source_address=None, *, bound_address=address):
            started = time.monotonic()
            try:
                bounded_timeout = min(timeout, max(0.05, deadline - time.monotonic()))
                return socket.create_connection((bound_address, 443), bounded_timeout, source_address)
            finally:
                tcp_ms[0] = round(max(0.0, (time.monotonic() - started) * 1000), 3)

        connection._create_connection = pinned_create

        if callable(original_connect):
            def timed_connect():
                started = time.monotonic()
                try:
                    return original_connect()
                finally:
                    connect_ms = max(0.0, (time.monotonic() - started) * 1000)
                    timing["tcp_ms"] = tcp_ms[0]
                    # HTTPSConnection.connect includes TLS wrapping after the
                    # pinned socket is established. This is the TLS portion;
                    # tiny clock noise is clamped to zero.
                    timing["tls_ms"] = round(max(0.0, connect_ms - (tcp_ms[0] or 0.0)), 3)
            connection.connect = timed_connect

        try:
            target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            request_started = time.monotonic()
            connection.request("GET", target,
                               headers={**headers, "Connection": "close", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            timing.setdefault("tcp_ms", tcp_ms[0])
            timing.setdefault("tls_ms", None)
            timing["header_ttfb_ms"] = round(max(0.0, (time.monotonic() - request_started) * 1000), 3)
            timing["ttfb_ms"] = timing["header_ttfb_ms"]  # Backwards-compatible alias.
            timing["protocol"] = _http_protocol(response)
            timing["status_code"] = response.status
            return PublicResponse(response, connection, url, telemetry=timing)
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as error:
            last_error = error
            connection.close()
        if time.monotonic() >= deadline:
            break
    raise _transport_error(last_error or OSError("connection failed"), "connect") from None


def _open_url(url, allowed_hosts, allowed_roots=(), headers=None, follow_redirects=True):
    safe_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    for name, value in (headers or {}).items():
        if (not isinstance(name, str) or name.lower() not in _SAFE_HEADERS
                or not isinstance(value, str) or re.search(r"[\r\n\x00]", value)):
            raise DouyinResolveError("Header tải Douyin không được hỗ trợ.")
        safe_headers[name] = value
    redirect_chain = []
    for hop in range(MAX_REDIRECTS + 1):
        url = _validated_url(url, allowed_hosts, allowed_roots)
        redirect_chain.append(_safe_url(url))
        try:
            response = _request_once(url, safe_headers)
        except (OSError, http.client.HTTPException) as error:
            raise _transport_error(error, "connect") from None
        if response.status not in (301, 302, 303, 307, 308) or not follow_redirects:
            if isinstance(response, PublicResponse):
                response.telemetry.setdefault("redirect_chain", list(redirect_chain))
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
        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        response.close()
        category = ({401: "login_required", 403: "access_denied", 404: "video_unavailable",
                     429: "rate_limited"}.get(response.status)
                    or ("server_error" if 500 <= response.status <= 599 else "http_error"))
        raise DouyinResolveError(
            f"Máy chủ video Douyin trả HTTP {response.status}, chưa tải được tệp. Hãy thử lại.",
            code="http_error", status_code=response.status, error_type="HTTPError", stage="http",
            retry_after=retry_after, category=category)
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
            category = ({401: "login_required", 403: "access_denied", 404: "video_unavailable",
                         429: "rate_limited"}.get(response.status)
                        or ("server_error" if 500 <= response.status <= 599 else "http_error"))
            raise DouyinResolveError(
                "Douyin chưa trả dữ liệu video. Hãy thử lại sau.", code="http_error",
                status_code=response.status, error_type="HTTPError", stage="http",
                retry_after=_retry_after_seconds(response.headers.get("Retry-After")), category=category)
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
        raise DouyinResolveError("Phản hồi từ Douyin chưa đúng định dạng video.",
                                 code="invalid_response", error_type="InvalidResponse", stage="response") from None
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
    saw_redirect = False
    for hop in range(MAX_REDIRECTS + 1):
        video_id = _video_id(url)
        if video_id:
            return video_id
        response = _open_url(url, SHARE_HOSTS, follow_redirects=False)
        try:
            location = response.headers.get("Location")
            if response.status not in (301, 302, 303, 307, 308):
                if response.status != 200:
                    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                    category = ({401: "login_required", 403: "access_denied", 404: "video_unavailable",
                                 429: "rate_limited"}.get(response.status)
                                or ("server_error" if 500 <= response.status <= 599 else "http_error"))
                    raise DouyinResolveError(
                        f"Douyin không cho phép mở link video (HTTP {response.status}).",
                        code="http_error", status_code=response.status, error_type="HTTPError",
                        stage="redirect", retry_after=retry_after, category=category)
                break
            if not location or hop >= MAX_REDIRECTS:
                break
            if response.status in (301, 302, 303, 307, 308) and location:
                saw_redirect = True
                url = _validated_url(urljoin(url, location), SHARE_HOSTS)
                continue
        finally:
            response.close()
    code = "redirect_failure" if saw_redirect else "invalid_video_id"
    message = ("Không thể hoàn tất chuyển hướng tới video Douyin." if saw_redirect
               else "Không tìm thấy mã video trong link Douyin. Hãy sao chép lại link Chia sẻ.")
    raise DouyinResolveError(message, code=code, stage="redirect")


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
    started = time.monotonic()
    try:
        video_id = _resolve_id(url)
    except DouyinResolveError as error:
        error.elapsed_ms = round(max(0.0, (time.monotonic() - started) * 1000))
        error.attempts = max(1, error.attempts or 0)
        error.source_id = _video_id(url)
        raise
    if progress:
        progress({"phase": "resolve", "stage": "Đang lấy thông tin video Douyin…", "progress_pct": None})
    last_error = None
    attempts = 0
    public_unavailable = False
    for endpoint in FEED_ENDPOINTS:
        try:
            attempts += 1
            response = _open_url(endpoint + "?" + urlencode({"aweme_id": video_id, "aid": "1128"}),
                                 FEED_HOSTS, headers={"Accept": "application/json"})
            payload = _read_json(response)
            if payload.get("status_code", 0) != 0:
                status_text = str(payload.get("status_msg") or payload.get("message") or "").lower()
                if any(token in status_text for token in ("captcha", "verification", "not a bot", "验证码", "人机验证")):
                    category = "captcha_required"
                elif any(token in status_text for token in ("login", "log in", "sign in", "登录", "未登录")):
                    category = "login_required"
                elif any(token in status_text for token in (
                        "private", "permission", "access denied", "forbidden", "not authorized",
                        "no permission", "not allowed", "权限", "无权", "私密", "拒绝访问")):
                    category = "access_denied"
                elif any(token in status_text for token in (
                        "video unavailable", "deleted", "removed", "not found", "does not exist",
                        "已删除", "已下架", "不存在", "不可用")):
                    category = "video_unavailable"
                else:
                    category = "api_rejected"
                last_error = DouyinResolveError(
                    "Douyin yêu cầu xác minh phiên trước khi trả thông tin video." if category in {"captcha_required", "login_required"}
                    else "Douyin từ chối quyền truy cập video." if category == "access_denied"
                    else "Video Douyin không còn khả dụng." if category == "video_unavailable"
                    else "API công khai Douyin chưa trả dữ liệu video.",
                    code=category, error_type="APIResponse", stage="resolve", category=category)
                if category != "api_rejected":
                    break
                continue
            items = payload.get("aweme_list")
            if not isinstance(items, list):
                last_error = DouyinResolveError(
                    "Phản hồi API Douyin chưa có danh sách video hợp lệ.", code="invalid_response",
                    error_type="InvalidResponse", stage="response", category="invalid_response")
                continue
            item = next((item for item in items if isinstance(item, dict)
                         and str(item.get("aweme_id", "")) == video_id), None)
            if item is None or not isinstance(item.get("video"), dict):
                public_unavailable = True
                continue
            formats = _formats(item["video"])
            if not formats:
                public_unavailable = True
                continue
            return {"id": video_id, "title": str(item.get("desc") or "Video Douyin")[:2000],
                    "duration": _number(item["video"].get("duration") or item.get("duration")) / 1000,
                    "formats": formats}
        except DouyinResolveError as error:
            last_error = error
            # A forbidden response is an access decision, not a transient
            # endpoint failure. Do not immediately repeat the same request on
            # another feed host (which can trigger additional rate limiting).
            if (error.status_code in {401, 403, 429} or error.code in {
                    "dns_failure", "invalid_dns_answer", "tcp_connection_failure", "tls_certificate_failure",
                    "tls_handshake_failure", "connection_timeout", "read_timeout", "invalid_url",
                    "unsupported_source", "redirect_failure", "invalid_video_id"}):
                break
            continue
    elapsed_ms = round(max(0.0, (time.monotonic() - started) * 1000))
    if last_error is not None:
        last_error.elapsed_ms = elapsed_ms
        last_error.attempts = max(1, attempts)
        last_error.source_id = video_id
        raise last_error
    if public_unavailable:
        raise DouyinResolveError(
            "API công khai Douyin không cung cấp media cho đúng video này.", code="public_unavailable",
            error_type="PublicUnavailable", stage="resolve", category="public_unavailable",
            elapsed_ms=elapsed_ms, attempts=max(1, attempts), source_id=video_id)
    raise DouyinResolveError(
        "API công khai Douyin chưa trả media cho video này.", code="public_unavailable",
        error_type="PublicUnavailable", stage="resolve", category="public_unavailable",
        elapsed_ms=elapsed_ms, attempts=max(1, attempts), source_id=video_id)
