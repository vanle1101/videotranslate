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
    return _parse_douyin_cookies(content, now=now)[0]


def _parse_douyin_cookies(content, *, now=None):
    """Return the validated jar and count of unusable empty-name rows."""
    if not isinstance(content, bytes) or len(content) > MAX_COOKIE_BYTES:
        raise DouyinCookieError("Tệp cookie phải có dung lượng tối đa 1 MiB.")
    try:
        source = content.decode("utf-8-sig")
    except UnicodeError:
        raise DouyinCookieError("Không đọc được tệp cookie UTF-8. Hãy xuất lại dưới dạng Netscape UTF-8.") from None
    jar = CookieJar(policy=cookie_policy())
    current_time = time.time() if now is None else now
    saw_douyin_domain = False
    saw_expired_cookie = False
    empty_name_rows = 0
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        if not raw_line.strip():
            continue
        http_only = raw_line.startswith("#HttpOnly_")
        if raw_line.startswith("#") and not http_only:
            continue
        line = raw_line[len("#HttpOnly_"):] if http_only else raw_line
        fields = line.split("\t")
        if len(fields) != 7:
            raise DouyinCookieError(_cookie_line_error(line_number, "cần đúng 7 trường phân cách bằng tab"))
        domain, include_subdomains, cookie_path, secure, expiry, name, value = fields
        if not _allowed_domain(domain):
            continue
        saw_douyin_domain = True
        if include_subdomains not in {"TRUE", "FALSE"}:
            raise DouyinCookieError(_cookie_line_error(line_number, "cờ miền phụ phải là TRUE hoặc FALSE"))
        if secure not in {"TRUE", "FALSE"}:
            raise DouyinCookieError(_cookie_line_error(line_number, "cờ bảo mật phải là TRUE hoặc FALSE"))
        if not expiry.isascii() or not expiry.isdigit():
            raise DouyinCookieError(_cookie_line_error(line_number, "cột thời hạn phải là số Unix hợp lệ"))
        if not cookie_path.startswith("/"):
            raise DouyinCookieError(_cookie_line_error(line_number, "đường dẫn cookie phải bắt đầu bằng dấu /"))
        if any(ord(char) < 32 or ord(char) == 127 for char in cookie_path + value):
            raise DouyinCookieError(_cookie_line_error(line_number, "đường dẫn hoặc giá trị có ký tự điều khiển"))
        if name and not _COOKIE_NAME.fullmatch(name):
            raise DouyinCookieError(_cookie_line_error(line_number, "tên cookie không hợp lệ"))
        has_subdomains = include_subdomains == "TRUE"
        if domain.startswith(".") and not has_subdomains:
            raise DouyinCookieError(_cookie_line_error(line_number, "miền có dấu chấm đầu phải cho phép miền phụ"))
        try:
            expires = int(expiry)
        except ValueError:
            raise DouyinCookieError(_cookie_line_error(line_number, "cột thời hạn nằm ngoài phạm vi hợp lệ")) from None
        if expires > 253402300799:
            raise DouyinCookieError(_cookie_line_error(line_number, "cột thời hạn nằm ngoài phạm vi hợp lệ"))
        if expires and expires <= current_time:
            saw_expired_cookie = True
            continue
        # Netscape exports occasionally include a blank-name placeholder row.
        # An empty name cannot form an HTTP cookie, so skip just that row while
        # continuing to validate and import the remaining Douyin records.
        if not name:
            empty_name_rows += 1
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
        if saw_douyin_domain and saw_expired_cookie:
            raise DouyinCookieError("Cookie Douyin trong tệp đã hết hạn. Hãy đăng nhập và xuất phiên mới.")
        if saw_douyin_domain:
            raise DouyinCookieError("Tệp không có cookie Douyin dùng được; bản ghi thiếu tên không thể sử dụng.")
        raise DouyinCookieError("Tệp không có cookie thuộc miền Douyin được hỗ trợ. Cookie miền khác đã bị bỏ qua.")
    return jar, empty_name_rows


def _cookie_line_error(line_number, reason):
    """Build a fixed, secret-free validation message for one Netscape row."""
    return f"Tệp Netscape cookies có lỗi tại dòng {line_number}: {reason}."


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


def douyin_cookie_revision(*, path=None):
    """Return local file metadata for detecting an explicit cookie re-import.

    The revision contains no cookie bytes, names, values, or content digest and
    is only intended for the private project checkpoint, never a UI response.
    """
    target = cookie_path() if path is None else Path(path)
    try:
        if target.is_symlink() or not target.is_file():
            return None
        info = target.stat()
        return [int(info.st_mtime_ns), int(info.st_size)]
    except (OSError, OverflowError, ValueError):
        return None


def import_douyin_cookies(content, *, path=None):
    jar, skipped_empty_names = _parse_douyin_cookies(content)
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
    status = get_douyin_cookie_status(path=target)
    if skipped_empty_names:
        status["warnings"] = [
            f"Đã bỏ qua {skipped_empty_names} bản ghi không có tên cookie vì không thể gửi qua HTTP."
        ]
    return status


def clear_douyin_cookies(*, path=None):
    target = cookie_path() if path is None else Path(path)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        raise DouyinCookieError("Không xóa được cookie Douyin đã lưu. Kiểm tra quyền ghi thư mục ứng dụng.") from None
    return get_douyin_cookie_status(path=target)
