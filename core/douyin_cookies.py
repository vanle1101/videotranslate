"""User-imported Douyin cookies, stored locally and never read from a browser."""
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from pathlib import Path
from urllib.parse import urlsplit

from config import settings


MAX_COOKIE_BYTES = 1024 * 1024
_ALLOWED_DOMAINS = ("douyin.com", "iesdouyin.com")
_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MALFORMED = "Tệp cookie không đúng định dạng Netscape. Hãy xuất lại cookie Douyin."


class DouyinCookieError(ValueError):
    """A fixed, safe message; never includes cookie content or source paths."""


def cookie_path():
    return settings.WORKSPACE_DIR / "private" / "douyin-cookies.txt"


def _allowed_domain(domain):
    host = domain.lower().removeprefix(".")
    return (
        len(host) <= 253
        and all(_DOMAIN_LABEL.fullmatch(label) for label in host.split("."))
        and any(host == root or host.endswith("." + root) for root in _ALLOWED_DOMAINS)
    )


def is_douyin_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme.lower() in {"http", "https"}
                and parsed.username is None and parsed.password is None
                and bool(parsed.hostname) and _allowed_domain(parsed.hostname))
    except (ValueError, TypeError):
        return False


def cookie_policy():
    # Honor host-only records as well as Netscape domain cookies.
    class HttpsOnlyCookiePolicy(DefaultCookiePolicy):
        def return_ok(self, cookie, request):
            return request.type == "https" and super().return_ok(cookie, request)

    return HttpsOnlyCookiePolicy(strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain)


def parse_douyin_cookies(content, *, now=None):
    """Validate Netscape input; discard unrelated domains and expired records."""
    if not isinstance(content, bytes) or len(content) > MAX_COOKIE_BYTES:
        raise DouyinCookieError("Tệp cookie phải có dung lượng tối đa 1 MiB.")
    try:
        source = content.decode("utf-8-sig")
    except UnicodeError:
        raise DouyinCookieError(_MALFORMED) from None
    jar = CookieJar(policy=cookie_policy())
    current_time = time.time() if now is None else now
    for raw_line in source.splitlines():
        if not raw_line.strip():
            continue
        http_only = raw_line.startswith("#HttpOnly_")
        if raw_line.startswith("#") and not http_only:
            continue
        line = raw_line[len("#HttpOnly_"):] if http_only else raw_line
        fields = line.split("\t")
        if len(fields) != 7:
            raise DouyinCookieError(_MALFORMED)
        domain, include_subdomains, cookie_path, secure, expiry, name, value = fields
        if not _allowed_domain(domain):
            continue
        if (include_subdomains not in {"TRUE", "FALSE"}
                or secure not in {"TRUE", "FALSE"}
                or not expiry.isascii() or not expiry.isdigit()
                or not cookie_path.startswith("/")
                or not _COOKIE_NAME.fullmatch(name)
                or any(ord(char) < 32 or ord(char) == 127 for char in cookie_path + value)):
            raise DouyinCookieError(_MALFORMED)
        has_subdomains = include_subdomains == "TRUE"
        if domain.startswith(".") and not has_subdomains:
            raise DouyinCookieError(_MALFORMED)
        try:
            expires = int(expiry)
        except ValueError:
            raise DouyinCookieError(_MALFORMED) from None
        if expires > 253402300799:
            raise DouyinCookieError(_MALFORMED)
        if expires and expires <= current_time:
            continue
        host = domain.lower().removeprefix(".")
        jar.set_cookie(Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=("." + host if has_subdomains else host), domain_specified=has_subdomains,
            domain_initial_dot=has_subdomains, path=cookie_path, path_specified=True,
            secure=secure == "TRUE", expires=expires or None, discard=expires == 0,
            comment=None, comment_url=None, rest={"HTTPOnly": ""} if http_only else {},
            rfc2109=False,
        ))
    if not len(jar):
        raise DouyinCookieError("Tệp không có cookie Douyin còn hiệu lực. Hãy đăng nhập và xuất lại cookie.")
    return jar


def _serialize(jar):
    lines = ["# Netscape HTTP Cookie File", "# Local Douyin cookies. Do not share this file."]
    for cookie in jar:
        domain = ("#HttpOnly_" if cookie.has_nonstandard_attr("HTTPOnly") else "") + cookie.domain
        lines.append("\t".join((domain, "TRUE" if cookie.domain_specified else "FALSE", cookie.path,
                                "TRUE" if cookie.secure else "FALSE", str(cookie.expires or 0),
                                cookie.name, cookie.value)))
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_douyin_cookiejar(*, path=None):
    target = cookie_path() if path is None else Path(path)
    try:
        with target.open("rb") as source:
            content = source.read(MAX_COOKIE_BYTES + 1)
    except FileNotFoundError:
        return CookieJar(policy=cookie_policy())
    except OSError:
        raise DouyinCookieError("Không đọc được cookie Douyin đã lưu. Hãy nhập lại tệp.") from None
    return parse_douyin_cookies(content)


def get_douyin_cookie_status(*, path=None):
    target = cookie_path() if path is None else Path(path)
    try:
        jar = load_douyin_cookiejar(path=target)
        if jar:
            imported_at = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat()
            return {"configured": True, "stored": True, "count": len(jar), "imported_at": imported_at,
                    "message": "Đã lưu cookie Douyin trên máy này."}
    except (DouyinCookieError, OSError, ValueError):
        return {"configured": False, "stored": True, "count": 0, "imported_at": None,
                "message": "Cookie Douyin đã hết hạn hoặc không còn hợp lệ. Hãy nhập lại."}
    return {"configured": False, "stored": False, "count": 0, "imported_at": None,
            "message": "Chưa nhập cookie Douyin."}


def import_douyin_cookies(content, *, path=None):
    jar = parse_douyin_cookies(content)
    serialized = _serialize(jar)
    if len(serialized) > MAX_COOKIE_BYTES:
        raise DouyinCookieError("Tệp cookie phải có dung lượng tối đa 1 MiB.")
    target = cookie_path() if path is None else Path(path)
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".douyin-cookies-", suffix=".tmp",
                                         dir=target.parent, delete=False) as destination:
            temporary = Path(destination.name)
            destination.write(serialized)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        temporary = None
    except OSError:
        raise DouyinCookieError("Không lưu được cookie Douyin. Kiểm tra quyền ghi thư mục ứng dụng.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return get_douyin_cookie_status(path=target)


def clear_douyin_cookies(*, path=None):
    target = cookie_path() if path is None else Path(path)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        raise DouyinCookieError("Không xóa được cookie Douyin đã lưu. Kiểm tra quyền ghi thư mục ứng dụng.") from None
    return get_douyin_cookie_status(path=target)
