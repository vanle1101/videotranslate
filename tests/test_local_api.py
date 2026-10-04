"""Local API contracts using stub sessions; no AI models or external services."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import main
from config import settings
from core.streaming.pipeline import SegmentItem


class LocalAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="local_api_")
        self.root = Path(self.temp.name)
        self.video = self.root / "video thử & kiểm tra.mp4"
        self.video.write_bytes(b"local video fixture")
        self.client = TestClient(main.app)
        self.patches = [
            patch.dict(main.active_streaming_sessions, {}, clear=True),
            patch.dict(main.active_export_tasks, {}, clear=True),
            patch.dict(main.stream_sockets, {}, clear=True),
            patch.object(main, "task_history", []),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def session(self, **kwargs):
        segment = SegmentItem(0, 0, 1, 1)
        segment.status = "READY"
        segment.final_vi = "Xin chào"
        segment.audio_path = str(self.root / "giọng Việt.wav")
        Path(segment.audio_path).write_bytes(b"audio fixture")
        attrs = dict(
            task_id="local-test", video_path=self.video, total_duration=1.0,
            initial_buffer_seconds=10.0, segments={0: segment},
            is_running=False, is_paused=False, error=None,
            start_wall_time=0, first_play_emitted=True, bgm_url=None,
            vocal_suppressor=SimpleNamespace(name="DSP"), suppression_stats={},
            start=AsyncMock(),
            get_telemetry=Mock(return_value={"status": "finished", "error": None}),
        )
        attrs["vocal_suppressor"].suppression_level_db = -20
        attrs.update(kwargs)
        return SimpleNamespace(**attrs)

    def test_health_checks_tools_without_loading_hardware_or_models(self):
        with patch.object(main, "detect_hardware", side_effect=AssertionError("heavy probe")), \
                patch.object(main.ModelManager, "get_all_models", side_effect=AssertionError("model scan")):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIsInstance(response.json()["ffmpeg"], bool)
        self.assertIsInstance(response.json()["ffprobe"], bool)

    def test_home_renders_configured_engines_and_voice(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'data-asr-engine="{settings.ASR_ENGINE}"', response.text)
        self.assertIn(f'data-tts-engine="{settings.TTS_ENGINE}"', response.text)
        self.assertIn(settings.WHISPER_MODEL_SIZE, response.text)
        self.assertIn(settings.EDGE_VOICE, response.text)

    def test_local_file_start_uses_settings_and_encodes_unicode_path(self):
        session = self.session()
        with patch.object(main, "create_streaming_session", return_value=session) as create:
            response = self.client.post("/api/streaming/start-local-file", json={"file_path": str(self.video)})
        self.assertEqual(response.status_code, 200)
        params = create.call_args.kwargs
        self.assertEqual(params["asr_engine_name"], settings.ASR_ENGINE)
        self.assertEqual(params["tts_engine_name"], settings.TTS_ENGINE)
        self.assertEqual(params["voice"], settings.EDGE_VOICE)
        self.assertEqual(params["video_path"], self.video)
        session.start.assert_called_once()
        source_url = response.json()["video_url"]
        self.assertEqual(parse_qs(urlparse(source_url).query)["path"], [self.video.as_posix()])
        self.assertEqual(self.client.get(source_url).content, self.video.read_bytes())

    def test_local_file_start_preserves_explicit_options(self):
        with patch.object(main, "create_streaming_session", return_value=self.session()) as create:
            response = self.client.post("/api/streaming/start-local-file", json={
                "file_path": str(self.video), "initial_buffer_seconds": 3,
                "voice": "vi-VN-NamMinhNeural", "tts_engine": "edge-tts", "asr_engine": "sensevoice",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs["voice"], "vi-VN-NamMinhNeural")
        self.assertEqual(create.call_args.kwargs["asr_engine_name"], "sensevoice")
        self.assertEqual(create.call_args.kwargs["initial_buffer_seconds"], 3)

    def test_missing_local_file_does_not_create_a_session(self):
        with patch.object(main, "create_streaming_session") as create:
            response = self.client.post("/api/streaming/start-local-file", json={"file_path": str(self.root / "absent.mp4")})
        self.assertEqual(response.status_code, 404)
        create.assert_not_called()

    def test_upload_start_uses_defaults_without_touching_real_inputs(self):
        with patch.object(settings, "INPUT_DIR", self.root), \
                patch.object(main, "create_streaming_session", return_value=self.session()) as create:
            response = self.client.post("/api/streaming/start-upload", files={
                "file": ("đầu vào.mp4", b"fixture", "video/mp4"),
            })
        self.assertEqual(response.status_code, 200)
        params = create.call_args.kwargs
        self.assertEqual(params["asr_engine_name"], settings.ASR_ENGINE)
        self.assertEqual(params["tts_engine_name"], settings.TTS_ENGINE)
        self.assertEqual(params["voice"], settings.EDGE_VOICE)
        self.assertEqual(params["video_path"].read_bytes(), b"fixture")

    def test_hq_export_receives_private_audio_path_and_records_completion(self):
        session = self.session()
        self.assertNotIn("audio_path", session.segments[0].to_dict())
        main.active_streaming_sessions[session.task_id] = session
        exporter = Mock()
        exporter.export.return_value = {"output_filename": "translated.mp4", "elapsed_seconds": 1.2}
        with patch.object(main, "HQExporter", return_value=exporter):
            response = self.client.post("/api/streaming/export-hq", json={"task_id": session.task_id, "mask_chinese": False})
        self.assertEqual(response.status_code, 200)
        exported = exporter.export.call_args.kwargs
        self.assertEqual(exported["segments"][0]["audio_path"], session.segments[0].audio_path)
        self.assertEqual(exported["segments"][0]["final_vi"], "Xin chào")
        self.assertFalse(exported["mask_chinese"])
        status = self.client.get(f"/api/streaming/export-hq/status/{session.task_id}").json()
        self.assertEqual(status["status"], "COMPLETED")
        self.assertEqual(response.json()["video_url"], "/api/outputs/translated.mp4")

    def test_export_rejects_running_or_failed_sessions(self):
        for state in ({"is_running": True}, {"error": "TTS offline"}):
            with self.subTest(state=state):
                session = self.session(**state)
                main.active_streaming_sessions[session.task_id] = session
                with patch.object(main, "HQExporter") as exporter:
                    response = self.client.post("/api/streaming/export-hq", json={"task_id": session.task_id})
                self.assertEqual(response.status_code, 409)
                exporter.assert_not_called()

    def test_export_rejects_incomplete_segments_even_when_worker_stopped(self):
        session = self.session()
        session.segments[1] = SegmentItem(1, 1, 2, 1)
        main.active_streaming_sessions[session.task_id] = session
        exporter = Mock()
        exporter.export.return_value = {"output_filename": "partial.mp4", "elapsed_seconds": 1}
        with patch.object(main, "HQExporter", return_value=exporter):
            response = self.client.post("/api/streaming/export-hq", json={"task_id": session.task_id})
        self.assertEqual(response.status_code, 409)
        exporter.export.assert_not_called()

    def test_failed_stream_is_not_reported_as_completed_in_tasks(self):
        session = self.session(error="ASR failed")
        main.active_streaming_sessions[session.task_id] = session
        response = self.client.get("/api/tasks")
        task = response.json()["tasks"][0]
        self.assertEqual(task["status"], "FAILED")
        self.assertIn("ASR failed", task["stage"])

    def test_websocket_replays_startup_failure_to_late_connection(self):
        session = self.session(error="Input audio missing", first_play_emitted=False)
        session.get_telemetry.return_value = {"status": "failed", "error": session.error}
        main.active_streaming_sessions[session.task_id] = session
        with self.client.websocket_connect(f"/ws/stream/{session.task_id}") as websocket:
            messages = [websocket.receive_json() for _ in range(3)]
        self.assertEqual([message["type"] for message in messages], ["init", "telemetry", "error"])
        self.assertEqual(messages[-1]["message"], session.error)


if __name__ == "__main__":
    unittest.main()
