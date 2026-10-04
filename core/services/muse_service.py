"""Own the optional Muse browser bridge without borrowing browser credentials."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import subprocess
import threading
import urllib.error
import urllib.request

BASE_DIR = Path(__file__).resolve().parents[2]
ERRORS = {
    "login_required": "Hãy đăng nhập Meta trong cửa sổ Muse rồi thử lại.",
    "approval_required": "Muse đang chờ bạn xác nhận trong cửa sổ trình duyệt.",
    "timed_out": "Muse trả lời quá lâu. Bản dịch chưa hoàn tất, hãy thử lại.",
    "empty_reply": "Muse chưa trả về bản dịch.",
    "browser_error": "Không thể sử dụng trình duyệt Muse. Đóng cửa sổ Muse cũ rồi kết nối lại.",
    "invalid_request": "Yêu cầu gửi đến Muse không hợp lệ hoặc quá dài.",
    "unauthorized": "Phiên kết nối Muse đã hết hiệu lực. Hãy kết nối lại.",
}


class MuseError(RuntimeError):
    """Safe user-facing failure; never contains browser state or credentials."""


class MuseService:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = Path(base_dir)
        self.runtime_dir = self.base_dir / "workspace" / "tools" / "muse-chat-mcp"
        self.profile_dir = self.base_dir / "workspace" / "muse-profile"
        self._process = None
        self._port = None
        self._token = None
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._shutdown = threading.Event()
        self._http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _installed(self):
        return bool(shutil.which("node") and
                    (self.runtime_dir / "muse-driver.mjs").is_file() and
                    (self.runtime_dir / "node_modules/playwright-core/package.json").is_file())

    def _running(self):
        process = self._process
        return process is not None and process.poll() is None

    def _check_open(self):
        if self._shutdown.is_set():
            raise MuseError("Ứng dụng đang đóng; Muse đã dừng.")

    def _request(self, path, body=None, timeout=15):
        if path != "/shutdown":
            self._check_open()
        if not self._running() or not self._port or not self._token:
            raise MuseError("Muse chưa được kết nối.")
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._port}{path}", data=data,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method="GET" if body is None else "POST",
        )
        try:
            with self._http.open(request, timeout=timeout) as response:
                result = json.loads(response.read(1024 * 1024))
        except urllib.error.HTTPError as exc:
            try:
                code = json.loads(exc.read(2048)).get("error")
            except (ValueError, OSError):
                code = None
            raise MuseError(ERRORS.get(code, "Muse không thể hoàn tất yêu cầu.")) from None
        except (OSError, ValueError, TimeoutError):
            raise MuseError("Mất kết nối với Muse. Hãy kết nối lại rồi thử lại.") from None
        if not isinstance(result, dict):
            raise MuseError("Muse trả về dữ liệu không hợp lệ.")
        return result

    def status(self):
        result = {"installed": self._installed(), "running": self._running(),
                  "logged_in": False, "composer_ready": False}
        if result["running"]:
            try:
                state = self._request("/health", timeout=5)
                result.update(logged_in=state.get("logged_in") is True,
                              composer_ready=state.get("composer_ready") is True)
            except MuseError:
                pass
        return result

    def _ensure_started(self):
        self._check_open()
        if self._running():
            return
        if not self._installed():
            raise MuseError("Chưa cài cầu nối Muse. Chạy setup_muse.bat rồi thử lại.")
        token = secrets.token_urlsafe(32)
        env = os.environ.copy()
        # Do not pass inherited provider credentials to third-party browser code.
        for name in list(env):
            if any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")) or name.startswith("MUSE_"):
                env.pop(name, None)
        env.update(MUSE_BRIDGE_TOKEN=token, MUSE_RUNTIME_DIR=str(self.runtime_dir),
                   MUSE_PROFILE_DIR=str(self.profile_dir), MUSE_HEADLESS="0", MUSE_CHANNEL="chrome")
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        try:
            # Publish ownership atomically with shutdown so a process cannot escape
            # between the closed check and Popen returning.
            with self._lifecycle_lock:
                self._check_open()
                self._process = subprocess.Popen(
                    [shutil.which("node"), str(self.base_dir / "integrations/muse/bridge.mjs")],
                    cwd=str(self.base_dir), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", **kwargs,
                )
                self._token = token
                process = self._process
            ready = queue.Queue(maxsize=1)
            def read_ready():
                try:
                    ready.put(process.stdout.readline())
                except (OSError, ValueError):
                    ready.put("")
            threading.Thread(target=read_ready, daemon=True).start()
            state = json.loads(ready.get(timeout=12))
            port = state.get("port")
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise ValueError("invalid port")
            with self._lifecycle_lock:
                self._check_open()
                if self._process is not process:
                    raise MuseError("Muse đã dừng kết nối.")
                self._port = port
        except (OSError, ValueError, queue.Empty):
            self.stop()
            raise MuseError("Không khởi động được cầu nối Muse.") from None

    def start_login(self):
        self._check_open()
        with self._lock:
            self._ensure_started()
            self._request("/login", {}, timeout=100)
            return self.status()

    def translate(self, prompt: str, system: str | None = None) -> str:
        self._check_open()
        if not isinstance(prompt, str) or not prompt.strip():
            raise MuseError("Không có văn bản để gửi đến Muse.")
        with self._lock:
            self._ensure_started()
            state = self._request("/health")
            if not state.get("logged_in"):
                self._request("/login", {}, timeout=100)
                state = self._request("/health")
            if not state.get("logged_in"):
                raise MuseError(ERRORS["login_required"])
            result = self._request("/chat", {"prompt": prompt, "system": system or ""}, timeout=260)
            reply = result.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                raise MuseError(ERRORS["empty_reply"])
            return reply.strip()

    def stop(self):
        # Do not wait on the translation lock: shutdown also cancels an active request.
        with self._lifecycle_lock:
            self._stop_process()

    def shutdown(self):
        # Cancelling asyncio.to_thread does not cancel a thread queued on _lock.
        # Fence those calls before stopping the current browser, permanently.
        self._shutdown.set()
        self.stop()

    def _stop_process(self):
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self._request("/shutdown", {}, timeout=8)
                process.wait(timeout=8)
            except (MuseError, subprocess.TimeoutExpired, OSError):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        if self._process is process:
            self._process = self._port = self._token = None


muse_service = MuseService()
atexit.register(muse_service.shutdown)
