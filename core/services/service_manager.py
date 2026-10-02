import os
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
    app_handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    app_handler.setFormatter(logging.Formatter(log_format, date_format))
    if not app_logger.handlers:
        app_logger.addHandler(app_handler)

    # AI logger
    ai_logger = logging.getLogger("ai")
    ai_logger.setLevel(logging.INFO)
    ai_handler = logging.FileHandler(LOG_DIR / "ai.log", encoding="utf-8")
    ai_handler.setFormatter(logging.Formatter(log_format, date_format))
    if not ai_logger.handlers:
        ai_logger.addHandler(ai_handler)

    # Pipeline logger
    pipe_logger = logging.getLogger("pipeline")
    pipe_logger.setLevel(logging.INFO)
    pipe_handler = logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")
    pipe_handler.setFormatter(logging.Formatter(log_format, date_format))
    if not pipe_logger.handlers:
        pipe_logger.addHandler(pipe_handler)

    # Errors logger
    err_logger = logging.getLogger("errors")
    err_logger.setLevel(logging.ERROR)
    err_handler = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
    err_handler.setFormatter(logging.Formatter(log_format, date_format))
    if not err_logger.handlers:
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
            loop="asyncio"
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
        start_time = time.time()
        health_ok = False
        while time.time() - start_time < timeout:
            try:
                req = urllib.request.Request(f"{self.backend_url}/api/hardware", headers={"User-Agent": "DesktopStudio/1.0"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        health_ok = True
                        break
            except (urllib.error.URLError, ConnectionRefusedError, socket.timeout):
                time.sleep(0.2)

        if not health_ok:
            raise RuntimeError(f"Internal backend failed to initialize on {self.backend_url} within {timeout}s.")

        self.is_running = True
        logger.info(f"Internal backend healthy and responsive on port {self.port}")
        return self.port

    def prewarm_components(self, progress_callback: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
        """
        Sequentially initializes core AI models with splash screen telemetry:
        0% -> 100%
        """
        def update(pct: int, msg: str):
            logger.info(f"[Pre-warm {pct}%] {msg}")
            if progress_callback:
                progress_callback(pct, msg)

        status = {
            "gpu": False,
            "sensevoice": False,
            "vieneu": False,
            "gemini": False,
            "audio": False
        }

        # 1. GPU & Hardware
        update(15, "Checking GPU & CUDA acceleration...")
        try:
            from core.hardware import detect_hardware
            hw = detect_hardware()
            status["gpu"] = hw.get("cuda_available", False)
            gpu_name = hw.get("gpu_name", "CPU")
            update(25, f"Hardware Ready: {gpu_name}")
        except Exception as e:
            logging.getLogger("errors").warning(f"Hardware detection warning: {e}")

        # 2. SenseVoice ASR
        update(35, "Pre-warming SenseVoice ASR engine...")
        try:
            from core.engines.asr.sensevoice_engine import SenseVoiceEngine
            asr = SenseVoiceEngine()
            asr._ensure_loaded()
            status["sensevoice"] = True
            update(50, "SenseVoice ASR Ready")
        except Exception as e:
            logging.getLogger("errors").warning(f"SenseVoice pre-warm warning: {e}")
            update(50, "SenseVoice Standby")

        # 3. VieNeu-TTS
        update(55, "Pre-warming VieNeu-TTS v3 Turbo engine...")
        try:
            from core.engines.tts.vieneu_engine import VieNeuEngine
            tts = VieNeuEngine()
            tts._ensure_loaded()
            status["vieneu"] = True
            update(70, "VieNeu-TTS v3 Turbo Ready")
        except Exception as e:
            logging.getLogger("errors").warning(f"VieNeu pre-warm warning: {e}")
            update(70, "VieNeu-TTS Standby")

        # 4. Gemini / LLM Provider
        update(75, "Connecting Gemini Translation engine...")
        try:
            from core.engines.translation.semantic_translator import SemanticTranslator
            trans = SemanticTranslator()
            gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or settings.GEMINI_API_KEY.strip()
            if gemini_key:
                t0 = time.time()
                res = trans.translate_single_segment(text_zh="你好，欢迎来到这里。", duration=3.0)
                lat = round((time.time() - t0) * 1000, 1)
                status["gemini"] = True
                status["gemini_latency_ms"] = lat
                logging.getLogger("ai").info(f"Gemini Diagnostics: Provider=Google Gemini | Model={settings.GEMINI_MODEL} | Latency={lat}ms | Status=Success")
                update(82, f"Gemini Connected ({settings.GEMINI_MODEL}, {lat}ms)")
            else:
                status["gemini"] = False
                logging.getLogger("ai").info("Gemini Diagnostics: Provider=Google Gemini | Model=gemini-2.0-flash | Latency=N/A | Status=Standby (Set API Key in Settings)")
                update(82, "Gemini Standby (Enter API Key in Settings)")
        except Exception as e:
            logging.getLogger("errors").warning(f"Gemini connection check warning: {e}")
            update(82, "Translation Engine Ready (Offline Fallback)")

        # 5. Audio Vocal Suppressor
        update(85, "Configuring Realtime Vocal Suppressor...")
        try:
            from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
            _ = RealtimeVocalSuppressor()
            status["audio"] = True
            update(92, "Vocal Suppressor Ready")
        except Exception as e:
            logging.getLogger("errors").warning(f"Vocal suppressor warning: {e}")

        # 6. Streaming Session & Cache
        update(95, "Starting Streaming Engine & Cache...")
        try:
            cache_root = settings.BASE_DIR / "workspace" / "cache"
            cache_root.mkdir(parents=True, exist_ok=True)
            update(100, "Douyin2TikTok AI Studio Ready!")
        except Exception:
            pass

        return status

    def shutdown_all(self):
        """
        Gracefully cancels all active jobs, stops backend, kills child processes,
        and clears GPU VRAM without leaving zombie processes.
        """
        logger.info("[*] ServiceManager: Initiating graceful shutdown...")
        self.is_running = False

        # 1. Cancel and stop all streaming sessions
        try:
            from core.streaming.pipeline import active_streaming_sessions
            for task_id, session in list(active_streaming_sessions.items()):
                try:
                    logger.info(f"Cancelling active streaming session: {task_id}")
                    session.cancel()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Session cancellation error: {e}")

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
                    except Exception:
                        pass
            self.child_processes.clear()

        # 3. Stop uvicorn server
        if self.uvicorn_server:
            try:
                self.uvicorn_server.should_exit = True
            except Exception:
                pass

        # 4. Release GPU VRAM
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info("Released PyTorch CUDA VRAM cache.")
        except Exception:
            pass

        # 5. Wait for server thread
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=1.5)

        logger.info("[+] ServiceManager: All services shutdown cleanly.")

service_manager = ServiceManager()
