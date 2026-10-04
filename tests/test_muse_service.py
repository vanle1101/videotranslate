"""Exercise the local bridge with a fake driver: no browser, credentials or network API."""
import importlib.util
import json
from pathlib import Path
import shutil
import threading
import urllib.error
import urllib.request

import pytest

from core.services.muse_service import BASE_DIR, MuseError, MuseService
from config import settings


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MUSE_BROWSER_MODE", "dedicated")
    if not shutil.which("node"):
        pytest.skip("Node is required only for optional Muse bridge tests")
    bridge = tmp_path / "integrations/muse/bridge.mjs"
    bridge.parent.mkdir(parents=True)
    bridge.write_text((BASE_DIR / "integrations/muse/bridge.mjs").read_text(encoding="utf-8"), encoding="utf-8")
    runtime = tmp_path / "workspace/tools/muse-chat-mcp"
    runtime.mkdir(parents=True)
    (runtime / "muse-driver.mjs").write_text("""
export const SELECTORS = {editor:'textarea'};
let running = false;
export const driver = {
 isRunning: () => running,
 page: {locator: () => ({first: () => ({isVisible: async () => running})})},
 checkAuth: async () => ({ok: running}),
 launch: async () => {running = true},
 close: async () => {running = false},
 chat: async (prompt, options) => {
   if (!options.newThread || options.files) throw Error('unsafe-options');
   if (prompt === 'timeout') return {reply:'partial',timedOut:true};
   if (prompt === 'approval') return {reply:'partial',needsApproval:true};
   if (prompt === 'error') return {reply:'partial',error:'private-transcript-secret'};
   if (prompt === 'empty') return {reply:''};
   if (prompt === 'crash') throw Error('private-transcript-secret');
   if (prompt === 'env-check') return {reply:String(!process.env.MUSE_CDP && !process.env.GEMINI_API_KEY)};
   return {reply:prompt};
 }
};
""", encoding="utf-8")
    result = MuseService(tmp_path)
    monkeypatch.setattr(result, "_installed", lambda: True)
    yield result
    result.stop()


def test_status_never_starts_browser_or_process(service):
    assert service.status() == {"installed": True, "running": False, "logged_in": False, "composer_ready": False, "browser_mode": "dedicated"}
    assert service._process is None
    service._ensure_started()
    assert service.status()["logged_in"] is False


def test_login_translate_and_owned_shutdown(service):
    state = service.start_login()
    assert state["running"] and state["logged_in"] and state["composer_ready"]
    process = service._process
    assert service.translate("xin chao", "Translate to Vietnamese.") == "Translate to Vietnamese.\n\nxin chao"
    assert service._process is process
    service.stop()
    assert process.poll() is not None
    assert service._token is None and service._port is None
    assert not service.status()["running"]
    service.stop()


def test_stop_allows_reconnect_but_shutdown_rejects_queued_translation(service):
    service.start_login()
    first_process = service._process
    service.stop()
    service.start_login()
    assert service._process is not first_process
    process = service._process
    started = threading.Event()
    errors = []

    def translate_queued():
        started.set()
        try:
            service.translate("queued transcript")
        except MuseError as error:
            errors.append(error)

    with service._lock:
        worker = threading.Thread(target=translate_queued)
        worker.start()
        assert started.wait(5)
        service.shutdown()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(errors) == 1
    assert process.poll() is not None
    assert service._process is None
    with pytest.raises(MuseError, match="đang đóng"):
        service.start_login()
    service.shutdown()


def test_shutdown_during_process_creation_cannot_leave_an_owned_child(service, monkeypatch):
    import core.services.muse_service as module

    real_popen = module.subprocess.Popen
    creating = threading.Event()
    release = threading.Event()
    children = []
    errors = []

    def delayed_popen(*args, **kwargs):
        creating.set()
        assert release.wait(5)
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def login():
        try:
            service.start_login()
        except MuseError as error:
            errors.append(error)

    monkeypatch.setattr(module.subprocess, "Popen", delayed_popen)
    worker = threading.Thread(target=login)
    closer = threading.Thread(target=service.shutdown)
    worker.start()
    assert creating.wait(5)
    closer.start()
    assert service._shutdown.wait(5)
    release.set()
    worker.join(timeout=15)
    closer.join(timeout=15)
    assert not worker.is_alive() and not closer.is_alive()
    assert len(errors) == 1
    assert children and all(child.poll() is not None for child in children)
    assert service._process is None
    assert service._token is None and service._port is None
    assert not service.status()["running"]
    service.stop()


@pytest.mark.parametrize("prompt", ["timeout", "approval", "error", "empty", "crash"])
def test_incomplete_and_failed_replies_never_become_translations(service, prompt):
    service.start_login()
    with pytest.raises(MuseError) as caught:
        service.translate(prompt)
    assert "partial" not in str(caught.value)
    assert "private-transcript-secret" not in str(caught.value)


def test_bridge_rejects_unauthenticated_and_browser_origin_requests(service):
    service._ensure_started()
    for headers in ({}, {"Authorization": "Bearer wrong"},
                    {"Authorization": f"Bearer {service._token}", "Origin": "https://example.com"}):
        req = urllib.request.Request(f"http://127.0.0.1:{service._port}/health", headers=headers)
        with pytest.raises(urllib.error.HTTPError) as caught:
            service._http.open(req, timeout=5)
        assert caught.value.code == 401
        assert caught.value.headers.get("Access-Control-Allow-Origin") is None


def test_only_text_chat_exposed_and_missing_login_does_not_send(service):
    service._ensure_started()
    with pytest.raises(MuseError, match="đăng nhập"):
        service._request("/chat", {"prompt": "test"})
    service.start_login()
    with pytest.raises(MuseError, match="không hợp lệ"):
        service._request("/chat", {"prompt": "test", "files": ["private.txt"]})
    with pytest.raises(MuseError):
        service._request("/v1/muse/chats")


def test_inherited_credentials_and_cdp_are_not_forwarded(service, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-not-forwarded")
    monkeypatch.setenv("MUSE_CDP", "http://127.0.0.1:9222")
    assert service.translate("env-check") == "true"


def test_missing_install_and_blank_prompt_do_not_launch(tmp_path):
    service = MuseService(tmp_path)
    assert not service.status()["installed"]
    with pytest.raises(MuseError, match="setup_muse"):
        service.start_login()
    with pytest.raises(MuseError):
        service.translate(" ")
    assert service._process is None


def test_setup_removes_cdp_and_stealth_launch_paths():
    spec = importlib.util.spec_from_file_location("setup_muse", BASE_DIR / "integrations/muse/setup_muse.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    source = """const source = 1;
    const launchPersistent = () =>
    ignoreDefaultArgs: ['--enable-automation'];
    connectOverCDP('external');
    '--disable-blink-features=AutomationControlled';
    this.ctx.on('close', () => {});
"""
    patched = setup.safe_driver(source)
    assert "connectOverCDP" not in patched
    assert "ignoreDefaultArgs" not in patched
    assert "AutomationControlled" not in patched
    assert "chromium.launchPersistentContext(PROFILE_DIR" in patched


def test_mode_change_stops_old_bridge_before_applying_configuration(service, monkeypatch):
    service.start_login()
    process = service._process
    seen = []

    def apply():
        assert process.poll() is not None
        seen.append(True)
        monkeypatch.setattr(settings, "MUSE_BROWSER_MODE", "existing")

    old_generation = service._request_generation()
    service.change_browser_mode("existing", apply)
    assert seen and service._process is None
    assert service.status()["browser_mode"] == "existing"
    with pytest.raises(MuseError, match="thay đổi"):
        service._check_generation(old_generation)


def test_mode_change_failure_releases_fence_and_invalid_mode_keeps_connection(service):
    service.start_login()
    process = service._process
    with pytest.raises(MuseError):
        service.change_browser_mode("other", lambda: None)
    assert service._process is process and process.poll() is None
    def fail():
        raise OSError("write failed")
    with pytest.raises(OSError):
        service.change_browser_mode("existing", fail)
    assert not service._reconfiguring
    assert service.start_login()["logged_in"]
