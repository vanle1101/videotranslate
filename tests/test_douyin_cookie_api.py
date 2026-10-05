"""Manual cookie import contracts; fixtures only, never browser credentials."""
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

import main
from config import settings


HEADERS = {"X-Studio-Request": "douyin-cookies"}
ENDPOINT = "/api/douyin/cookies"
FIXTURE = (b"# Netscape HTTP Cookie File\n"
           b".douyin.com\tTRUE\t/\tTRUE\t4102444800\tsessionid\tfixture-private-value\n"
           b".example.com\tTRUE\t/\tTRUE\t4102444800\tother\tunrelated-private-value\n")


@pytest.fixture
def cookie_api(monkeypatch):
    with TemporaryDirectory(prefix="douyin-cookie-api-") as directory:
        monkeypatch.setattr(settings, "WORKSPACE_DIR", Path(directory))
        with TestClient(main.app, base_url="http://127.0.0.1") as client:
            yield client, Path(directory)


def upload(client, data=FIXTURE, headers=None):
    return client.post(ENDPOINT, headers=HEADERS if headers is None else headers,
                       files={"file": ("cookies.txt", data, "text/plain")})


def test_import_reports_presence_without_values_and_clear_removes_only_local_copy(cookie_api):
    client, root = cookie_api
    assert client.get(ENDPOINT).json()["configured"] is False
    result = upload(client)
    assert result.status_code == 200
    assert result.json()["configured"] is True
    assert result.json()["count"] == 1
    status = client.get(ENDPOINT)
    assert status.json()["configured"] is True
    for response in (result, status):
        assert "fixture-private-value" not in response.text
        assert "unrelated-private-value" not in response.text
    source = root / "user-export.txt"
    source.write_bytes(FIXTURE)
    cleared = client.delete(ENDPOINT, headers=HEADERS)
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    assert source.read_bytes() == FIXTURE
    assert client.get(ENDPOINT).json()["configured"] is False


@pytest.mark.parametrize("headers", [
    {},
    {**HEADERS, "Origin": "https://unrelated.example"},
    {**HEADERS, "Sec-Fetch-Site": "cross-site"},
])
def test_mutations_reject_cross_origin_and_form_requests(cookie_api, headers):
    client, _ = cookie_api
    assert upload(client, headers=headers).status_code == 403
    assert client.delete(ENDPOINT, headers=headers).status_code == 403
    assert client.get(ENDPOINT).json()["configured"] is False


def test_bad_file_preserves_working_copy_and_never_echoes_its_content(cookie_api):
    client, _ = cookie_api
    assert upload(client).status_code == 200
    response = upload(client, b"invalid-secret-content")
    assert response.status_code == 422
    assert "invalid-secret-content" not in response.text
    assert client.get(ENDPOINT).json()["configured"] is True
    assert client.get(ENDPOINT).json()["count"] == 1


def test_oversize_file_rejected_without_replacing_working_copy(cookie_api):
    client, _ = cookie_api
    assert upload(client).status_code == 200
    assert upload(client, b"x" * (1024 * 1024 + 1)).status_code == 413
    assert client.get(ENDPOINT).json()["configured"] is True


def test_cookie_storage_cannot_be_fetched_or_used_as_video(cookie_api):
    client, root = cookie_api
    assert upload(client).status_code == 200
    path = root / "private" / "douyin-cookies.txt"
    assert client.get("/api/local-file", params={"path": str(path)}).status_code == 404
    assert client.post("/api/streaming/start-local-file", json={"file_path": str(path)}).status_code == 404
    assert client.post("/api/preview", json={"file_path": str(path)}).status_code == 404


def test_same_origin_import_allowed(cookie_api):
    client, _ = cookie_api
    assert upload(client, headers={**HEADERS, "Origin": "http://127.0.0.1"}).status_code == 200


def test_expired_saved_cookies_remain_removable(cookie_api):
    client, root = cookie_api
    assert upload(client).status_code == 200
    path = root / "private" / "douyin-cookies.txt"
    path.write_bytes(FIXTURE.replace(b"4102444800", b"1"))
    state = client.get(ENDPOINT).json()
    assert state["stored"] is True and state["configured"] is False
    assert client.delete(ENDPOINT, headers=HEADERS).json()["stored"] is False


def test_nonlocal_host_cannot_replace_cookie_file(cookie_api):
    client, _ = cookie_api
    assert upload(client, headers={**HEADERS, "Host": "external.example", "Origin": "http://external.example"}).status_code == 403
    assert client.get(ENDPOINT).json()["configured"] is False
