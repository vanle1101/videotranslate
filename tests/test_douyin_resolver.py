"""Offline acceptance for guest Douyin resolution and public-media guards."""
import io
import json
import socket
from email.message import Message
from urllib.parse import parse_qs, urlsplit

import pytest

from core import douyin_resolver as resolver


VIDEO_ID = "7688769264395767049"
VIDEO_URL = f"https://www.douyin.com/video/{VIDEO_ID}"
MEDIA_URL = "https://v26-web.douyinvod.com/media.mp4?token=opaque%2Fsignature"
SECRET = "synthetic-private-value"
REQUEST_ONCE = resolver._request_once


class Response(io.BytesIO):
    def __init__(self, body=b"", *, status=200, headers=None, url=VIDEO_URL):
        super().__init__(body)
        self.status = self.status_code = status
        self.reason = SECRET
        self.url = url
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = str(value)

    def iter_content(self, chunk_size=65536):
        while chunk := self.read(chunk_size):
            yield chunk


def item(video_id=VIDEO_ID, *, title="Requested video"):
    return {
        "aweme_id": video_id,
        "desc": title,
        "video": {
            "duration": 12500,
            "play_addr": {"url_list": [MEDIA_URL], "data_size": 4096,
                          "width": 720, "height": 1280},
            "width": 720, "height": 1280,
        },
    }


@pytest.fixture(autouse=True)
def offline_network(monkeypatch):
    # No test may reach a real service, even when the resolver adds a fallback.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
    ])

    def unexpected_request(*args, **kwargs):
        raise AssertionError("Unexpected network request")

    monkeypatch.setattr(resolver, "_request_once", unexpected_request)


def serve_json(monkeypatch, payload):
    calls = []
    body = json.dumps(payload).encode()

    def request(url, headers):
        calls.append((url, dict(headers)))
        return Response(body, headers={"Content-Type": "application/json"}, url=url)

    monkeypatch.setattr(resolver, "_request_once", request)
    return calls


def test_resolver_selects_exact_requested_id_and_preserves_signed_media_url(monkeypatch):
    calls = serve_json(monkeypatch, {"status_code": 0, "aweme_list": [
        item("7688769264395767000", title="Unrelated recommendation"), item(),
    ]})
    progress = []
    result = resolver.resolve_douyin(VIDEO_URL, progress.append)
    assert result["id"] == VIDEO_ID
    assert result["title"] == "Requested video"
    assert result["duration"] == 12.5
    assert result["formats"] and result["formats"][0]["urls"] == [MEDIA_URL]
    assert all(urlsplit(url).scheme == "https" for url, _ in calls)
    assert all(parse_qs(urlsplit(url).query).get("aweme_id") == [VIDEO_ID] for url, _ in calls)
    assert all(not {"cookie", "authorization", "x-api-key"} & {key.lower() for key in headers}
               for _, headers in calls)
    assert progress


def test_recommendations_never_replace_requested_video(monkeypatch):
    serve_json(monkeypatch, {"status_code": 0, "aweme_list": [item("7688769264395767000")]})
    with pytest.raises(resolver.DouyinResolveError):
        resolver.resolve_douyin(VIDEO_URL)


@pytest.mark.parametrize("url", [
    "https://douyin.com.evil.test/video/7688769264395767049",
    "https://notdouyin.com/video/7688769264395767049",
    "https://private:secret@www.douyin.com/video/7688769264395767049",
    "https://127.0.0.1/video/7688769264395767049",
    "file:///video/7688769264395767049",
])
def test_source_url_guard_rejects_foreign_hosts_and_credentials_before_request(url):
    with pytest.raises(resolver.DouyinResolveError):
        resolver.resolve_douyin(url)


@pytest.mark.parametrize("target", [
    "https://example.com/video/7688769264395767049",
    "https://www.douyin.com.evil.test/video/7688769264395767049",
    "https://127.0.0.1/video/7688769264395767049",
    "http://169.254.169.254/latest/meta-data/",
])
def test_short_link_redirect_cannot_leave_allowed_hosts(monkeypatch, target):
    calls = []

    def request(url, headers):
        calls.append(url)
        return Response(status=302, headers={"Location": target}, url=url)

    monkeypatch.setattr(resolver, "_request_once", request)
    with pytest.raises(resolver.DouyinResolveError):
        resolver.resolve_douyin("https://v.douyin.com/_lAiSDH0bK8/")
    assert target not in calls


@pytest.mark.parametrize("body", [b"", b"{invalid-json", b"[]", b"null",
                                        b'{"status_code":0}', b"<html>Login required</html>"])
def test_empty_or_invalid_resolver_response_is_an_actionable_error(monkeypatch, body):
    monkeypatch.setattr(resolver, "_request_once", lambda url, headers: Response(
        body, headers={"Content-Type": "application/json"}, url=url))
    with pytest.raises(resolver.DouyinResolveError) as captured:
        resolver.resolve_douyin(VIDEO_URL)
    assert SECRET not in str(captured.value)


@pytest.mark.parametrize("url", [
    "https://example.com/media.mp4", "https://douyinvod.com.evil.test/media.mp4",
    "https://localhost/media.mp4", "https://127.0.0.1/media.mp4",
    "https://[::1]/media.mp4", "https://169.254.169.254/media.mp4",
    "https://private:secret@v26-web.douyinvod.com/media.mp4",
    "file:///C:/private.mp4",
])
def test_media_url_guard_rejects_foreign_hosts_and_credentials(url):
    with pytest.raises(resolver.DouyinResolveError):
        resolver.open_public_media(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fd00::1"])
def test_allowed_media_hostname_with_private_dns_is_rejected(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)),
    ])
    monkeypatch.setattr(resolver, "_request_once", REQUEST_ONCE)
    with pytest.raises(resolver.DouyinResolveError):
        resolver.open_public_media(MEDIA_URL)


@pytest.mark.parametrize("header", ["Cookie", "Authorization", "X-Api-Key", "Proxy-Authorization"])
def test_media_headers_never_forward_cookies_keys_or_authorization(header):
    with pytest.raises(resolver.DouyinResolveError) as captured:
        resolver.open_public_media(MEDIA_URL, headers={header: SECRET})
    assert SECRET not in str(captured.value)


def test_media_range_header_remains_usable_without_credentials(monkeypatch):
    calls = []

    def request(url, headers):
        calls.append((url, dict(headers)))
        return Response(b"media", headers={"Content-Type": "video/mp4"}, url=url)

    monkeypatch.setattr(resolver, "_request_once", request)
    with resolver.open_public_media(MEDIA_URL, headers={"Range": "bytes=0-4"}) as response:
        assert response.read() == b"media"
    assert calls and SECRET not in json.dumps(calls)
    assert calls[0][1]["Range"] == "bytes=0-4"


@pytest.mark.parametrize("target", ["https://example.com/video.mp4", "https://127.0.0.1/video.mp4",
                                     "https://douyinvod.com.evil.test/video.mp4"])
def test_media_redirect_is_guarded_before_following(monkeypatch, target):
    calls = []

    def request(url, headers):
        calls.append(url)
        return Response(status=302, headers={"Location": target}, url=url)

    monkeypatch.setattr(resolver, "_request_once", request)
    with pytest.raises(resolver.DouyinResolveError):
        resolver.open_public_media(MEDIA_URL)
    assert target not in calls


def test_transport_failure_does_not_expose_signed_url_or_raw_exception(monkeypatch):
    def request(url, headers):
        raise OSError(f"connect failed token={SECRET} sessionid={SECRET}")

    monkeypatch.setattr(resolver, "_request_once", request)
    with pytest.raises(resolver.DouyinResolveError) as captured:
        resolver.resolve_douyin(VIDEO_URL)
    assert SECRET not in str(captured.value)


def test_media_http_url_is_upgraded_without_corrupting_signature():
    assert resolver.validate_media_url(MEDIA_URL.replace("https:", "http:")) == MEDIA_URL


@pytest.mark.parametrize("declared_size", [None, "129"])
def test_json_size_limit_applies_to_declared_and_actual_response(monkeypatch, declared_size):
    monkeypatch.setattr(resolver, "MAX_JSON_BYTES", 128)
    headers = {"Content-Length": declared_size} if declared_size else {}
    response = Response(b'{"value":"' + b"a" * 256 + b'"}', headers=headers)
    with pytest.raises(resolver.DouyinResolveError):
        resolver._read_json(response)
    assert response.closed


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors_hide_upstream_body_and_close_response(status):
    response = Response(SECRET.encode(), status=status)
    with pytest.raises(resolver.DouyinResolveError) as captured:
        resolver._read_json(response)
    assert SECRET not in str(captured.value) and response.closed


def test_formats_prefer_compatible_codec_and_filter_duplicate_unsafe_urls(monkeypatch):
    video = item()
    video["video"]["bit_rate"] = [
        {"is_h265": 1, "play_addr": {"url_list": [MEDIA_URL.replace("media.mp4", "hevc.mp4")],
                                     "width": 1080, "height": 1920}},
        {"play_addr": {"url_list": [MEDIA_URL, MEDIA_URL, "https://127.0.0.1/private.mp4"],
                       "width": 720, "height": 1280}},
    ]
    serve_json(monkeypatch, {"status_code": 0, "aweme_list": [video]})
    formats = resolver.resolve_douyin(VIDEO_URL)["formats"]
    assert len(formats) == 2
    assert [entry["codec"] for entry in formats] == ["h264", "h265"]
    assert formats[0]["urls"] == [MEDIA_URL]


def test_short_link_redirect_finds_exact_video_without_reading_page(monkeypatch):
    calls = []

    def request(url, headers):
        calls.append(url)
        if urlsplit(url).hostname == "v.douyin.com":
            return Response(status=302, headers={"Location": VIDEO_URL}, url=url)
        return Response(json.dumps({"aweme_list": [item()]}).encode(), url=url)

    monkeypatch.setattr(resolver, "_request_once", request)
    assert resolver.resolve_douyin("https://v.douyin.com/_lAiSDH0bK8/")["id"] == VIDEO_ID
    assert VIDEO_URL not in calls


def test_transport_pins_public_ip_and_keeps_original_tls_hostname(monkeypatch):
    calls = {}

    class Connection:
        def __init__(self, host, timeout, context):
            calls.update(host=host, verify_hostname=context.check_hostname, timeout=timeout)

        def request(self, method, target, headers):
            self._create_connection((calls["host"], 443), calls["timeout"])
            calls.update(method=method, target=target, headers=headers)

        def getresponse(self):
            return Response(b"media")

        def close(self):
            calls["closed"] = True

    def connect(destination, timeout, source_address):
        calls["destination"] = destination
        return object()

    monkeypatch.setattr(resolver.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(socket, "create_connection", connect)
    with REQUEST_ONCE(MEDIA_URL, {"Accept": "video/mp4"}) as response:
        assert response.read() == b"media"
    assert calls["destination"] == ("8.8.8.8", 443)
    assert calls["host"] == "v26-web.douyinvod.com" and calls["verify_hostname"]
    assert calls["target"] == "/media.mp4?token=opaque%2Fsignature"
    assert calls["closed"]
