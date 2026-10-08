import os
import asyncio
import sys
import time
import socket
import logging
import threading
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

# Ensure BASE_DIR is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import settings

LOG_DIR = settings.WORKSPACE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Configure file logging for desktop & services
def setup_logging():
    log_format = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # App logger
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        app_handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
        app_handler.setFormatter(logging.Formatter(log_format, date_format))
        app_logger.addHandler(app_handler)

    # AI logger
    ai_logger = logging.getLogger("ai")
    ai_logger.setLevel(logging.INFO)
    if not ai_logger.handlers:
        ai_handler = logging.FileHandler(LOG_DIR / "ai.log", encoding="utf-8")
        ai_handler.setFormatter(logging.Formatter(log_format, date_format))
        ai_logger.addHandler(ai_handler)

    # Pipeline logger
    pipe_logger = logging.getLogger("pipeline")
    pipe_logger.setLevel(logging.INFO)
    if not pipe_logger.handlers:
        pipe_handler = logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")
        pipe_handler.setFormatter(logging.Formatter(log_format, date_format))
        pipe_logger.addHandler(pipe_handler)

    # Errors logger
    err_logger = logging.getLogger("errors")
    err_logger.setLevel(logging.ERROR)
    if not err_logger.handlers:
        err_handler = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
        err_handler.setFormatter(logging.Formatter(log_format, date_format))
        err_logger.addHandler(err_handler)

setup_logging()
logger = logging.getLogger("app")

class ServiceManager:
    """
    Internal Service Lifecycle & Process Manager for Douyin2TikTok AI Studio Desktop App.
    Controls FastAPI backend, child processes, model pre-warming, and graceful resource cleanup.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.port: Optional[int] = None
        self.server_thread: Optional[threading.Thread] = None
        self.uvicorn_server = None
        self.is_running: bool = False
        self.child_processes: List[subprocess.Popen] = []
        self.backend_url: str = ""
        self.lock = threading.Lock()
        self._prewarmed: Dict[str, Any] = {}

    @staticmethod
    def find_free_port() -> int:
        """Finds an unused ephemeral port on localhost."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def register_process(self, proc: subprocess.Popen):
        """Registers child processes (e.g. FFmpeg, downloader) for automatic cleanup."""
        with self.lock:
            self.child_processes.append(proc)

    def unregister_process(self, proc: subprocess.Popen):
        with self.lock:
            if proc in self.child_processes:
                self.child_processes.remove(proc)

    def start_backend(self, port: Optional[int] = None, timeout: float = 15.0) -> int:
        """
        Starts the FastAPI backend inside a background thread on 127.0.0.1:<port>.
        Blocks until the backend passes internal health check, then returns the port.
        """
        if self.is_running and self.port:
            return self.port

        self.port = port or self.find_free_port()
        settings.PORT = self.port
        self.backend_url = f"http://127.0.0.1:{self.port}"
        logger.info(f"Starting internal FastAPI service on {self.backend_url} (dynamic port)...")

        import uvicorn
        from main import app

        config = uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
            log_config=None,
            loop="asyncio",
            timeout_graceful_shutdown=3,
        )
        self.uvicorn_server = uvicorn.Server(config)

        def _run_server():
            logger.info("Internal uvicorn server thread started.")
            try:
                self.uvicorn_server.run()
            except Exception as e:
                logging.getLogger("errors").error(f"Uvicorn server crashed: {e}")
            logger.info("Internal uvicorn server thread terminated.")

        self.server_thread = threading.Thread(target=_run_server, daemon=True, name="InternalUvicornServer")
        self.server_thread.start()

        # Wait for health check response
        start_time = time.monotonic()
        health_ok = False
        while time.monotonic() - start_time < timeout:
            if not self.server_thread.is_alive():
                break
            try:
                req = urllib.request.Request(f"{self.backend_url}/api/health", headers={"User-Agent": "DesktopStudio/1.0"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        health_ok = True
                        break
            except (urllib.error.URLError, ConnectionRefusedError, socket.timeout):
                time.sleep(0.2)

        if not health_ok:
            self.shutdown_all()
            raise RuntimeError(f"Internal backend failed to initialize on {self.backend_url} within {timeout}s.")

        self.is_running = True
        logger.info(f"Internal backend healthy and responsive on port {self.port}")
        return self.port

    def prewarm_components(self, progress_callback: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
        """Check local prerequisites; load only selected models when explicitly enabled."""
        def update(pct: int, msg: str):
            logger.info(f"[Startup {pct}%] {msg}")
            if progress_callback:
                progress_callback(pct, msg)

        status = {"gpu": False, "sensevoice": False, "vieneu": False,
                  "gemini": False, "audio": False, "ffmpeg": False,
                  "ffprobe": False, "prewarm_enabled": bool(settings.PREWARM_MODELS)}
        update(10, "Checking local hardware...")
        try:
            from core.hardware import detect_hardware
            hw = detect_hardware()
            status["gpu"] = bool(hw.get("cuda_available"))
            update(25, f"Hardware ready: {hw.get('gpu_name') or 'CPU'}; processing on {settings.DEVICE}")
        except Exception as exc:
            logger.warning(f"Hardware detection warning: {exc}")

        for executable in ("ffmpeg", "ffprobe"):
            try:
                result = subprocess.run(
                    [executable, "-version"], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=3,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                status[executable] = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        status["audio"] = status["ffmpeg"] and status["ffprobe"]
        update(45, "FFmpeg ready" if status["audio"] else "FFmpeg / FFprobe missing or unavailable")

        # Presence of a key is configuration only; startup makes no paid API call.
        provider = settings.LLM_PROVIDER.lower()
        key_setting = {"gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
                       "openai": "OPENAI_API_KEY"}.get(provider, "")
        status["translation_configured"] = bool(getattr(settings, key_setting, "").strip()) if key_setting else False
        if not settings.PREWARM_MODELS:
            update(100, "Ready. Selected models will load when processing starts.")
            return status

        asr_choice = settings.ASR_ENGINE.lower()
        tts_choice = settings.TTS_ENGINE.lower()
        separator_choice = settings.SEPARATION_ENGINE.lower()
        try:
            asr = None
            if asr_choice == "sensevoice":
                from core.engines.asr.sensevoice_engine import SenseVoiceEngine
                asr = SenseVoiceEngine()
            elif asr_choice in {"faster-whisper", "whisper"}:
                from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
                asr = FasterWhisperFallbackEngine(model_size=settings.WHISPER_MODEL_SIZE)
            if asr is not None:
                update(55, f"Loading selected ASR: {asr_choice}...")
                asr._ensure_loaded()
                self._prewarmed["asr"] = asr
                status["asr"] = True
                status["sensevoice"] = asr_choice == "sensevoice"
        except Exception as exc:
            logger.warning(f"Selected ASR prewarm failed: {exc}")
            status["asr"] = False

        if tts_choice in {"vieneu", "vieneu-tts"}:
            try:
                update(75, "Loading selected VieNeu TTS...")
                from core.engines.tts.vieneu_engine import VieNeuEngine
                tts = VieNeuEngine()
                tts._ensure_loaded()
                self._prewarmed["tts"] = tts
                status["vieneu"] = True
            except Exception as exc:
                logger.warning(f"Selected TTS prewarm failed: {exc}")

        if separator_choice == "roformer":
            try:
                update(90, "Loading selected RoFormer separator...")
                from core.engines.separator.roformer_engine import BSRoFormerSeparator
                separator = BSRoFormerSeparator(model_name=settings.ROFORMER_MODEL)
                separator._ensure_loaded()
                self._prewarmed["separator"] = separator
                status["separator"] = True
            except Exception as exc:
                logger.warning(f"Selected separator prewarm failed: {exc}")
                status["separator"] = False

        update(100, "Startup checks complete.")
        return status

    def shutdown_all(self):
        """
        Gracefully cancels all active jobs, stops backend, kills child processes,
        and clears GPU VRAM without leaving zombie processes.
        """
        logger.info("[*] ServiceManager: Initiating graceful shutdown...")
        self.is_running = False

        # Export workers run in threads. Signal cancellation before stopping the
        # event loop so their media subprocesses can exit and remove partial files.
        api = sys.modules.get("main")
        for task in list(getattr(api, "active_export_tasks", {}).values()):
            if task.get("status") in {"RUNNING", "CANCELLING"}:
                task["cancelled"] = True
                task["status"] = "CANCELLING"
                task["stage"] = "Đang dừng xuất video..."

        # 1. Drain export owners before session.stop removes their source WAVs.
        # The desktop thread must hand this work to the owning asyncio loop.
        pending_stops = []

        async def stop_after_export(session):
            owners = {task for task in (getattr(session, "export_task", None),
                                        getattr(session, "auto_export_task", None))
                      if task is not None and not task.done()}
            for owner in owners:
                owner.cancel()
            if owners:
                await asyncio.gather(*owners, return_exceptions=True)
            getattr(session, "shutdown", session.stop)()

        try:
            pipeline = sys.modules.get("core.streaming.pipeline")
            active_streaming_sessions = getattr(pipeline, "active_streaming_sessions", {})
            for task_id, session in list(active_streaming_sessions.items()):
                try:
                    logger.info(f"Stopping active streaming session: {task_id}")
                    worker = (getattr(session, "export_task", None) or getattr(session, "auto_export_task", None)
                              or getattr(session, "review_task", None) or getattr(session, "worker_task", None)
                              or getattr(session, "start_task", None))
                    loop = worker.get_loop() if worker is not None else None
                    if loop is not None and loop.is_running():
                        pending_stops.append(asyncio.run_coroutine_threadsafe(stop_after_export(session), loop))
                    else:
                        getattr(session, "shutdown", session.stop)()
                except Exception as exc:
                    logger.warning(f"Could not stop streaming session {task_id}: {exc}")
        except Exception as e:
            logger.warning(f"Session cancellation error: {e}")

        deadline = time.monotonic() + 10
        for pending in pending_stops:
            try:
                pending.result(timeout=max(0, deadline - time.monotonic()))
            except TimeoutError:
                # Keep media owned until the worker acknowledges cancellation.
                # Never force session.stop while an export thread still reads it.
                logger.warning("Export is still stopping; its source media is retained until cleanup completes.")
            except Exception as exc:
                logger.warning(f"Could not finish session shutdown: {type(exc).__name__}")

        preview_module = sys.modules.get("core.media_preview")
        if preview_module is not None:
            try:
                preview_module.preview_manager.shutdown()
            except Exception:
                logger.warning("Could not fully stop the compatible video preview worker.")

        voice_preview_module = sys.modules.get("core.voice_preview")
        if voice_preview_module is not None:
            voice_preview_module.voice_preview_manager.shutdown()

        # Close only the browser bridge owned by this application.
        muse_module = sys.modules.get("core.services.muse_service")
        if muse_module is not None:
            try:
                muse_module.muse_service.shutdown()
            except Exception:
                logger.warning("Could not fully stop the Muse browser bridge.")

        # 2. Terminate any registered child processes (FFmpeg, downloaders)
        with self.lock:
            for proc in self.child_processes:
                try:
                    if proc.poll() is None:
                        logger.info(f"Terminating child process PID {proc.pid}")
                        proc.terminate()
                        proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
            self.child_processes.clear()

        # 3. Stop uvicorn server
        if self.uvicorn_server:
            try:
                self.uvicorn_server.should_exit = True
            except Exception:
                pass

        # Wait until the server has released its listening socket.
        if self.server_thread and self.server_thread is not threading.current_thread():
            self.server_thread.join(timeout=5.0)
            if self.server_thread.is_alive() and self.uvicorn_server:
                self.uvicorn_server.force_exit = True
                self.server_thread.join(timeout=2.0)

        # Release only libraries already loaded by this application.
        self._prewarmed.clear()
        try:
            torch = sys.modules.get("torch")
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info("Released PyTorch CUDA VRAM cache.")
        except Exception:
            pass

        if self.server_thread and self.server_thread.is_alive():
            logger.warning("Backend thread is still stopping after the shutdown timeout.")
        else:
            self.server_thread = None
            self.uvicorn_server = None
            self.port = None
            logger.info("[+] ServiceManager: All services shutdown cleanly.")

service_manager = ServiceManager()
