"""Cookie import/scoping tests with synthetic values; no browser or network."""
import json
import os
import tempfile
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request

import pytest

from core import douyin_cookies as cookies
from core import download_worker


SENTINEL = "synthetic-secret-value"
FUTURE = 4102444800


def record(domain=".douyin.com", *, subdomains="TRUE", path="/", secure="TRUE",
           expiry=FUTURE, name="sessionid", value=SENTINEL):
    return f"{domain}\t{subdomains}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"


def data(*rows):
    return ("# Netscape HTTP Cookie File\n" + "".join(rows)).encode("utf-8")


@pytest.fixture
def cookie_file():
    with tempfile.TemporaryDirectory(prefix="douyin-cookie-test-") as directory:
        yield Path(directory) / "private" / "douyin-cookies.txt"


def header(jar, url):
    request = Request(url)
    jar.add_cookie_header(request)
    return request.get_header("Cookie") or ""


def test_import_filters_domains_expiry_and_preserves_http_only_session(cookie_file):
    status = cookies.import_douyin_cookies(data(
        record("#HttpOnly_.douyin.com", expiry=0),
        record(".iesdouyin.com", name="cross_site"),
        record("www.douyin.com", subdomains="FALSE", name="host_only"),
        record(name="expired", expiry=1),
        record(".google.com", name="unrelated", value="excluded-secret"),
        record(".douyin.com.evil.test", value="lookalike-secret"),
    ), path=cookie_file)
    assert status["configured"] is True
    assert status["count"] == 3
    assert status["imported_at"]
    assert SENTINEL not in json.dumps(status)
    assert "excluded-secret" not in cookie_file.read_text("utf-8")
    assert "lookalike-secret" not in cookie_file.read_text("utf-8")
    jar = cookies.load_douyin_cookiejar(path=cookie_file)
    session = next(cookie for cookie in jar if cookie.name == "sessionid")
    assert session.has_nonstandard_attr("HTTPOnly")
    assert session.expires is None and session.discard is True
    assert session.secure is True
    assert session.domain == ".douyin.com"
    assert len(list(cookie_file.parent.iterdir())) == 1
    assert cookies.clear_douyin_cookies(path=cookie_file) == {
        "configured": False, "stored": False, "count": 0, "imported_at": None, "message": "Chưa nhập cookie Douyin.",
    }
    assert not cookie_file.exists()


def test_export_with_empty_name_cookie_skips_only_that_unusable_row():
    jar = cookies.parse_douyin_cookies(data(
        record("www.douyin.com", subdomains="FALSE", secure="FALSE", name="", value="legacy-row"),
        record("www.douyin.com", subdomains="FALSE", name="sessionid", value="usable-row"),
    ))
    assert len(jar) == 1
    assert [cookie.name for cookie in jar] == ["sessionid"]
    assert "sessionid=" in header(jar, "https://www.douyin.com/video/123")


def test_netscape_header_crlf_bom_httponly_and_exact_www_domain():
    source = (
        "\ufeff# Netscape HTTP Cookie File\r\n"
        "# exported locally\r\n"
        "#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t0\tsecure_session\tsecret-one\r\n"
        "www.douyin.com\tFALSE\t/\tFALSE\t4102444800\tweb_session\tsecret-two\r\n"
        ".other.example\tTRUE\t/\tTRUE\t4102444800\tother\tsecret-three\r\n"
    ).encode("utf-8")
    jar = cookies.parse_douyin_cookies(source)
    assert len(jar) == 2
    assert {cookie.name for cookie in jar} == {"secure_session", "web_session"}
    assert "secure_session=" in header(jar, "https://v.douyin.com/short/abc")
    assert "web_session=" in header(jar, "https://www.douyin.com/video/123")
    assert "web_session=" not in header(jar, "https://v.douyin.com/short/abc")
    assert not header(jar, "http://www.douyin.com/video/123")


@pytest.mark.parametrize("domain", [
    ".notdouyin.com", ".douyin.com.evil.test", ".iesdouyin.com.evil.test",
    ".evil-iesdouyin.com", ".com", ".douyin.com.", "douyin.com:443", "douyin.com/path",
])
def test_domain_lookalikes_are_never_usable(domain):
    with pytest.raises(cookies.DouyinCookieError) as captured:
        cookies.parse_douyin_cookies(data(record(domain)))
    assert SENTINEL not in str(captured.value)


@pytest.mark.parametrize("url,allowed", [
    ("https://v.douyin.com/short/", True),
    ("https://www.douyin.com/video/123", True),
    ("https://www.iesdouyin.com/share/video/123", True),
    ("https://DOUYIN.COM/video/123", True),
    ("https://douyin.com.evil.test/video/123", False),
    ("https://notdouyin.com/video/123", False),
    ("https://douyin.com@evil.test/", False),
    ("https://user:password@douyin.com/", False),
    ("file://douyin.com/video/123", False),
])
def test_initial_url_allowlist(url, allowed):
    assert cookies.is_douyin_url(url) is allowed


@pytest.mark.parametrize("payload", [
    b"not Netscape synthetic-secret-value",
    data(record(secure="yes")),
    data(record(expiry="synthetic-secret-value")),
    data(record(expiry=253402300800)),
    data(record(subdomains="FALSE")),
    data(record(path="invalid-path")),
    data(record(name="bad;name")),
    data(record(value=SENTINEL + "\x00")),
    data(record(value=SENTINEL + "\tbad")),
    b"\xff" + SENTINEL.encode(),
    b"x" * (cookies.MAX_COOKIE_BYTES + 1),
    data(record(expiry=1)),
], ids=["malformed", "invalid-secure", "invalid-expiry", "overflow-expiry", "conflicting-domain",
        "invalid-path", "invalid-name", "null-value", "tab-value", "invalid-utf8", "oversize", "expired"])
def test_invalid_import_is_safe_and_preserves_previous_file(payload, cookie_file):
    cookies.import_douyin_cookies(data(record(value="original-value")), path=cookie_file)
    before = cookie_file.read_bytes()
    with pytest.raises(cookies.DouyinCookieError) as captured:
        cookies.import_douyin_cookies(payload, path=cookie_file)
    assert SENTINEL not in str(captured.value)
    assert str(cookie_file) not in str(captured.value)
    assert cookie_file.read_bytes() == before
    assert len(list(cookie_file.parent.iterdir())) == 1


def test_invalid_expiry_has_a_specific_safe_validation_reason():
    with pytest.raises(cookies.DouyinCookieError) as captured:
        cookies.parse_douyin_cookies(data(record(expiry="not-a-number", value=SENTINEL)))
    assert str(captured.value) == "Tệp Netscape cookies có lỗi tại dòng 2: cột thời hạn phải là số Unix hợp lệ."
    assert SENTINEL not in str(captured.value)


@pytest.mark.parametrize(("row", "reason"), [
    ("www.douyin.com\tFALSE\t/\tTRUE\t4102444800\tsessionid\n", "cần đúng 7 trường phân cách bằng tab"),
    (record(secure="yes"), "cờ bảo mật phải là TRUE hoặc FALSE"),
    (record(path="video"), "đường dẫn cookie phải bắt đầu bằng dấu /"),
    (record(name="bad;name"), "tên cookie không hợp lệ"),
    (record(".douyin.com", subdomains="FALSE", name=""), "miền có dấu chấm đầu phải cho phép miền phụ"),
    (record(expiry=253402300800, name=""), "cột thời hạn nằm ngoài phạm vi hợp lệ"),
])
def test_invalid_netscape_rows_report_only_a_safe_reason(row, reason):
    with pytest.raises(cookies.DouyinCookieError) as captured:
        cookies.parse_douyin_cookies(data(row))
    assert str(captured.value) == f"Tệp Netscape cookies có lỗi tại dòng 2: {reason}."
    assert SENTINEL not in str(captured.value)


def test_cookie_file_with_only_other_domains_reports_domain_filtering():
    with pytest.raises(cookies.DouyinCookieError) as captured:
        cookies.parse_douyin_cookies(data(record(".example.com")))
    assert str(captured.value) == "Tệp không có cookie thuộc miền Douyin được hỗ trợ. Cookie miền khác đã bị bỏ qua."
    assert SENTINEL not in str(captured.value)


def test_atomic_save_failure_preserves_prior_import_and_removes_own_temp(cookie_file, monkeypatch):
    cookies.import_douyin_cookies(data(record(value="original-value")), path=cookie_file)
    before = cookie_file.read_bytes()

    def fail_replace(*_):
        raise OSError(SENTINEL)

    monkeypatch.setattr(cookies.os, "replace", fail_replace)
    with pytest.raises(cookies.DouyinCookieError) as captured:
        cookies.import_douyin_cookies(data(record()), path=cookie_file)
    assert SENTINEL not in str(captured.value)
    assert cookie_file.read_bytes() == before
    assert len(list(cookie_file.parent.iterdir())) == 1


def test_missing_and_expired_storage_status_never_exposes_values(cookie_file):
    assert not cookies.get_douyin_cookie_status(path=cookie_file)["configured"]
    assert not len(cookies.load_douyin_cookiejar(path=cookie_file))
    cookie_file.parent.mkdir()
    cookie_file.write_bytes(data(record(expiry=1)))
    status = cookies.get_douyin_cookie_status(path=cookie_file)
    assert status["configured"] is False and status["count"] == 0
    assert status["imported_at"] is None and status["message"]
    assert SENTINEL not in json.dumps(status)


def test_cookie_revision_is_secret_free_and_changes_after_reimport(cookie_file):
    assert cookies.douyin_cookie_revision(path=cookie_file) is None
    cookies.import_douyin_cookies(data(record(value="first-session")), path=cookie_file)
    first = cookies.douyin_cookie_revision(path=cookie_file)
    assert isinstance(first, list) and len(first) == 2
    os.utime(cookie_file, ns=(first[0] + 10_000_000, first[0] + 10_000_000))
    cookies.import_douyin_cookies(data(record(value="second-session")), path=cookie_file)
    second = cookies.douyin_cookie_revision(path=cookie_file)
    assert second != first
    assert "first-session" not in json.dumps(second)
    assert "second-session" not in json.dumps(second)


def test_redirect_cookie_scoping_secure_host_only_and_path():
    jar = cookies.parse_douyin_cookies(data(
        record(),
        record("www.douyin.com", subdomains="FALSE", name="host_only"),
        record(".iesdouyin.com", name="ies"),
        record(name="private_path", path="/video/"),
        record(name="insecure_export", secure="FALSE"),
    ))
    assert "sessionid=" in header(jar, "https://v.douyin.com/short/")
    assert "sessionid=" in header(jar, "https://www.douyin.com/video/123")
    assert "host_only=" in header(jar, "https://www.douyin.com/video/123")
    assert "host_only=" not in header(jar, "https://nested.www.douyin.com/video/123")
    assert "host_only=" not in header(jar, "https://v.douyin.com/short/")
    assert "private_path=" not in header(jar, "https://v.douyin.com/short/")
    assert "private_path=" in header(jar, "https://www.douyin.com/video/123")
    assert "ies=" in header(jar, "https://www.iesdouyin.com/share/video/123")
    assert not header(jar, "http://www.douyin.com/video/123")
    for url in ("https://douyin.com.evil.test/", "https://cdn.example.test/", "https://notdouyin.com/"):
        assert not header(jar, url)


def test_worker_loads_only_for_douyin_and_never_persists_changes(cookie_file, monkeypatch):
    cookies.import_douyin_cookies(data(record()), path=cookie_file)
    saved = cookie_file.read_bytes()
    calls = []

    def load():
        calls.append(True)
        return cookies.load_douyin_cookiejar(path=cookie_file)

    monkeypatch.setattr(download_worker, "load_douyin_cookiejar", load)
    options = {"quiet": True, "no_warnings": True}
    with download_worker.single_video_downloader(options, "https://v.douyin.com/short/") as downloader:
        assert downloader.params.get("cookiefile") is None
        assert downloader.params.get("cookiesfrombrowser") is None
        assert "sessionid=" in downloader.cookiejar.get_cookie_header("https://www.douyin.com/video/123")
        assert not downloader.cookiejar.get_cookie_header("https://cdn.example.test/video.mp4")
        downloader.cookiejar.clear()
    assert cookie_file.read_bytes() == saved
    with download_worker.single_video_downloader(options, "https://www.youtube.com/watch?v=abc") as downloader:
        assert not len(downloader.cookiejar)
    with download_worker.single_video_downloader(options, "https://douyin.com.evil.test/") as downloader:
        assert not len(downloader.cookiejar)
    assert len(calls) == 1


def test_worker_transport_preserves_https_and_host_only_cookie_policy(monkeypatch):
    jar = cookies.parse_douyin_cookies(data(
        record("www.douyin.com", subdomains="FALSE", secure="FALSE"),
    ))
    monkeypatch.setattr(download_worker, "load_douyin_cookiejar", lambda: jar)
    with download_worker.single_video_downloader(
        {"quiet": True, "no_warnings": True}, "https://v.douyin.com/short/",
    ) as downloader:
        director = downloader._request_director
        assert set(director.handlers) == {"Urllib"}
        opener = director.handlers["Urllib"]._get_instance(cookiejar=downloader.cookiejar, proxies={})
        processor = next(handler for handler in opener.handlers if isinstance(handler, HTTPCookieProcessor))
        assert processor.cookiejar is downloader.cookiejar
        for url, allowed in [
            ("https://www.douyin.com/video/123", True),
            ("http://www.douyin.com/video/123", False),
            ("https://sub.www.douyin.com/video/123", False),
            ("https://www.douyin.com.evil.test/video/123", False),
        ]:
            request = processor.http_request(Request(url))
            assert bool(request.get_header("Cookie")) is allowed
