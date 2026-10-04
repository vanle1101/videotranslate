"""Offline regression checks for machine detection and a lightweight desktop startup."""
import importlib
import asyncio
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import hardware, model_manager
services = importlib.import_module("core.services.service_manager")


class HardwareTests(unittest.TestCase):
    def test_ctranslate2_cuda_and_ram_without_torch(self):
        modules = {
            "torch": None,
            "ctranslate2": SimpleNamespace(get_cuda_device_count=lambda: 1,
                                            get_supported_compute_types=lambda device: {"float16"}),
            "psutil": SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=16 * 1024 ** 3,
                                                                              available=4 * 1024 ** 3)),
            "onnxruntime": SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]),
        }
        gpu_output = SimpleNamespace(returncode=0, stdout="Test GPU, 4096, 3000\nSecond GPU, 8192, 7000")
        with patch.dict(sys.modules, modules), patch.object(hardware.subprocess, "run", return_value=gpu_output):
            result = hardware.detect_hardware()
        self.assertEqual(result["ram_gb"], 16)
        self.assertEqual(result["ram_available_gb"], 4)
        self.assertTrue(result["cuda_available"])
        self.assertEqual(result["cuda_backend"], "ctranslate2")
        self.assertEqual(result["gpu_name"], "Test GPU")
        self.assertEqual(result["vram_free_mb"], 3000)

    def test_nvidia_card_does_not_claim_working_cuda(self):
        with patch.dict(sys.modules, {"torch": None, "ctranslate2": None, "onnxruntime": None}), \
                patch.object(hardware.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="GPU, 4096, 3000")):
            self.assertFalse(hardware.detect_hardware()["cuda_available"])


class ModelStatusTests(unittest.TestCase):
    def test_missing_and_partial_whisper_models_are_not_downloaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SimpleNamespace(BASE_DIR=root, WORKSPACE_DIR=root / "workspace", DEVICE="cpu",
                                     ROFORMER_MODEL="roformer.ckpt", WHISPER_MODEL_SIZE="small")
            with patch.object(model_manager, "settings", config), \
                    patch.dict("os.environ", {"HF_HUB_CACHE": str(root / "hub")}):
                whisper = root / "workspace" / "models" / "faster-whisper-small"
                whisper.mkdir(parents=True)
                (root / "engines" / "video-subtitle-remover").mkdir(parents=True)
                self.assertFalse(any(model["downloaded"] for model in model_manager.ModelManager.get_all_models()))
                (whisper / "model.bin").write_bytes(b"test-checkpoint")
                self.assertFalse(model_manager.ModelManager.verify_model("whisper")["ok"])
                (whisper / "config.json").write_text("{}", encoding="utf-8")
                (whisper / "tokenizer.json").write_text("{}", encoding="utf-8")
                verified = model_manager.ModelManager.verify_model("whisper")
                self.assertTrue(verified["ok"])
                self.assertEqual(verified["status"], "FILES_PRESENT")
                record = next(model for model in model_manager.ModelManager.get_all_models() if "Whisper" in model["engine"])
                self.assertEqual(record["path"], str(whisper))
                self.assertEqual(record["device"], "CPU")
                self.assertFalse(model_manager.ModelManager.verify_model("")["ok"])


class ServiceStartupTests(unittest.TestCase):
    def setUp(self):
        services.ServiceManager._instance = None
        self.manager = services.ServiceManager()
        self.config = SimpleNamespace(PREWARM_MODELS=False, DEVICE="cpu", LLM_PROVIDER="gemini",
                                      GEMINI_API_KEY="test-configured-key", ASR_ENGINE="faster-whisper",
                                      WHISPER_MODEL_SIZE="small", TTS_ENGINE="edge-tts", SEPARATION_ENGINE="none")

    def tearDown(self):
        self.manager.shutdown_all()
        services.ServiceManager._instance = None

    def test_default_startup_does_not_import_models_or_call_network(self):
        blocked = {name: None for name in (
            "core.engines.asr.sensevoice_engine", "core.engines.asr.faster_whisper_engine",
            "core.engines.tts.vieneu_engine", "core.engines.translation.semantic_translator",
            "core.engines.separator.roformer_engine",
        )}
        progress = []
        with patch.object(services, "settings", self.config), patch.dict(sys.modules, blocked), \
                patch.object(hardware, "detect_hardware", return_value={"cuda_available": False}), \
                patch.object(services.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, \
                patch.object(services.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network call")):
            status = self.manager.prewarm_components(lambda pct, msg: progress.append(pct))
        self.assertTrue(status["ffmpeg"] and status["ffprobe"])
        self.assertFalse(status["prewarm_enabled"])
        self.assertTrue(status["translation_configured"])
        self.assertFalse(status["gemini"])
        self.assertEqual(progress[-1], 100)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(self.manager._prewarmed)

    def test_prewarm_loads_only_selected_engine(self):
        self.config.PREWARM_MODELS = True
        whisper = Mock()
        engine_module = SimpleNamespace(FasterWhisperFallbackEngine=whisper)
        with patch.object(services, "settings", self.config), \
                patch.object(hardware, "detect_hardware", return_value={}), \
                patch.object(services.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                patch.dict(sys.modules, {"core.engines.asr.faster_whisper_engine": engine_module,
                                         "core.engines.asr.sensevoice_engine": None,
                                         "core.engines.tts.vieneu_engine": None,
                                         "core.engines.separator.roformer_engine": None}):
            status = self.manager.prewarm_components()
        whisper.assert_called_once_with(model_size="small")
        whisper.return_value._ensure_loaded.assert_called_once_with()
        self.assertTrue(status["asr"])
        self.assertFalse(status["sensevoice"] or status["vieneu"])

    def test_backend_health_and_shutdown_release_port(self):
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/api/health")
        async def health():
            return {"status": "ok"}

        with patch.dict(sys.modules, {"main": SimpleNamespace(app=app)}):
            port = self.manager.start_backend(timeout=5)
        self.assertTrue(self.manager.is_running)
        self.manager.shutdown_all()
        self.assertIsNone(self.manager.server_thread)
        with socket.socket() as connection:
            connection.settimeout(0.2)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", port)), 0)

    def test_startup_timeout_stops_backend_thread(self):
        from fastapi import FastAPI
        app = FastAPI()  # Missing health route intentionally fails the startup check.
        with patch.dict(sys.modules, {"main": SimpleNamespace(app=app)}):
            with self.assertRaises(RuntimeError):
                self.manager.start_backend(timeout=0.4)
        self.assertIsNone(self.manager.server_thread)
        self.assertFalse(self.manager.is_running)

    def test_shutdown_stops_active_session_on_its_event_loop(self):
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        stopped = threading.Event()
        thread_ids = []

        def run_loop():
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            loop.run_forever()

        def stop_session():
            thread_ids.append(threading.get_ident())
            stopped.set()

        thread = threading.Thread(target=run_loop)
        session = SimpleNamespace(worker_task=SimpleNamespace(get_loop=lambda: loop), stop=Mock(side_effect=stop_session))
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            with patch.dict(sys.modules, {"core.streaming.pipeline": SimpleNamespace(active_streaming_sessions={"test": session})}):
                self.manager.shutdown_all()
            self.assertTrue(stopped.wait(2))
            session.stop.assert_called_once_with()
            self.assertEqual(thread_ids, [thread.ident])
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
            loop.close()

    def test_shutdown_stops_session_without_worker(self):
        session = SimpleNamespace(worker_task=None, stop=Mock())
        with patch.dict(sys.modules, {"core.streaming.pipeline": SimpleNamespace(active_streaming_sessions={"test": session})}):
            self.manager.shutdown_all()
        session.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
