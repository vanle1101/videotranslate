"""Local API contracts using stub sessions; no AI models or external services."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import main
from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


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
            screen_texts=[], video_size=(1080, 1920),
            is_running=False, is_paused=False, error=None,
            start_wall_time=0, first_play_emitted=True, bgm_url=None,
            vocal_suppressor=SimpleNamespace(name="DSP"), suppression_stats={},
            start=AsyncMock(),
            get_telemetry=Mock(return_value={"status": "finished", "error": None}),
        )
        attrs["vocal_suppressor"].suppression_level_db = -20
        attrs.update(kwargs)
        session = SimpleNamespace(**attrs)
        # Keep reconnect serialization on the real contract: every session
        # snapshot includes the same caption layouts used by preview/export.
        session.caption_metadata = MethodType(StreamingPipelineSession.caption_metadata, session)
        session.segment_snapshot = MethodType(StreamingPipelineSession.segment_snapshot, session)
        return session

    def test_health_checks_tools_without_loading_hardware_or_models(self):
        with patch.object(main, "detect_hardware", side_effect=AssertionError("heavy probe")), \
                patch.object(main.ModelManager, "get_all_models", side_effect=AssertionError("model scan")):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIsInstance(response.json()["ffmpeg"], bool)
        self.assertIsInstance(response.json()["ffprobe"], bool)

    def test_library_lists_only_direct_completed_media_without_reading_contents(self):
        nested = self.root / "private"
        nested.mkdir()
        (nested / "private.mp4").write_bytes(b"private nested media")
        (self.root / "folder.mp4").mkdir()
        for name in (".private.mp4", ".env", "cookies.txt", "clip.mp4.part", "clip.tmp.mp4", "clip.temp.MOV", "voice.wav"):
            (self.root / name).write_bytes(b"excluded fixture")
        for name in ("new.MOV", "sample.webm", "sample.mkv", "sample.avi"):
            (self.root / name).write_bytes(b"media fixture")
        os.utime(self.video, (1000, 1000))
        newest = self.root / "new.MOV"
        os.utime(newest, (2000000000, 2000000000))
        expected = {self.video.name, "new.MOV", "sample.webm", "sample.mkv", "sample.avi"}
        before = {path.name: path.stat().st_size for path in self.root.iterdir() if path.is_file()}
        with patch.object(settings, "INPUT_DIR", self.root), \
                patch.object(main, "create_streaming_session", side_effect=AssertionError("read-only API")), \
                patch.object(main.downloader, "download", side_effect=AssertionError("read-only API")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("metadata only")):
            response = self.client.get("/api/library")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual({item["name"] for item in items}, expected)
        self.assertEqual(items[0]["name"], "new.MOV")
        self.assertEqual(items[-1]["name"], self.video.name)
        self.assertEqual(items[0]["modified_at"], 2000000000)
        for item in items:
            self.assertEqual(set(item), {"name", "file_path", "size", "modified_at"})
            self.assertEqual(Path(item["file_path"]).parent, self.root.resolve())
            self.assertEqual(item["size"], Path(item["file_path"]).stat().st_size)
        self.assertEqual(before, {path.name: path.stat().st_size for path in self.root.iterdir() if path.is_file()})

    def test_library_caps_newest_media_and_does_not_accept_an_outside_directory(self):
        for index in range(105):
            video = self.root / f"clip_{index:03d}.mp4"
            video.write_bytes(b"tiny media fixture")
            os.utime(video, (2000 + index, 2000 + index))
        os.utime(self.video, (1000, 1000))
        with patch.object(settings, "INPUT_DIR", self.root):
            response = self.client.get("/api/library", params={"path": str(self.root.parent), "directory": ".."})
        items = response.json()["items"]
        self.assertEqual(len(items), 100)
        self.assertEqual(items[0]["name"], "clip_104.mp4")
        self.assertEqual(items[-1]["name"], "clip_005.mp4")
        self.assertTrue(all(Path(item["file_path"]).parent == self.root.resolve() for item in items))

    def test_library_missing_directory_is_empty_and_is_not_created(self):
        absent = self.root / "not-created"
        with patch.object(settings, "INPUT_DIR", absent):
            response = self.client.get("/api/library")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": []})
        self.assertFalse(absent.exists())

    def test_library_skips_symlinks_even_when_the_target_is_inside_the_library(self):
        inside_link = self.root / "alias.mp4"
        try:
            inside_link.symlink_to(self.video)
        except (OSError, NotImplementedError):
            self.skipTest("File symlinks are unavailable for this test user")
        outside_dir = self.root / "private"
        outside_dir.mkdir()
        outside = outside_dir / "outside.mp4"
        outside.write_bytes(b"private target")
        (self.root / "outside-link.mp4").symlink_to(outside)
        (self.root / "dangling.mp4").symlink_to(self.root / "absent.mp4")
        with patch.object(settings, "INPUT_DIR", self.root):
            response = self.client.get("/api/library")
        self.assertEqual([item["name"] for item in response.json()["items"]], [self.video.name])

    def test_library_skips_hidden_system_and_reparse_files_on_windows(self):
        original_lstat = Path.lstat
        for attributes in (0x2, 0x4, 0x400):
            with self.subTest(attributes=attributes):
                def simulated_lstat(path):
                    info = original_lstat(path)
                    if path == self.video:
                        return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                                               st_mtime=info.st_mtime, st_file_attributes=attributes)
                    return info
                with patch.object(settings, "INPUT_DIR", self.root), \
                        patch.object(Path, "lstat", simulated_lstat):
                    response = self.client.get("/api/library")
                self.assertEqual(response.json(), {"items": []})

    def test_library_rejects_link_file_modes_without_resolving_the_target(self):
        original_lstat, original_resolve = Path.lstat, Path.resolve

        def link_lstat(path):
            if path == self.video:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0)
            return original_lstat(path)

        def refuse_target_resolution(path, *args, **kwargs):
            if path == self.video:
                raise AssertionError("Library must not follow file links")
            return original_resolve(path, *args, **kwargs)

        with patch.object(settings, "INPUT_DIR", self.root), \
                patch.object(Path, "lstat", link_lstat), \
                patch.object(Path, "resolve", refuse_target_resolution):
            response = self.client.get("/api/library")
        self.assertEqual(response.json(), {"items": []})

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
        self.assertEqual(session.source_video_url, source_url)
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

    def test_settings_persist_and_apply_buffer_suppression_and_fractional_ducking(self):
        with patch.object(main, "update_env_file") as persist, \
                patch.object(settings, "INITIAL_BUFFER_SECONDS", 10.0), \
                patch.object(settings, "SUPPRESSION_MODE", "AUTO"), \
                patch.object(settings, "BGM_VOLUME_DUCKED_DB", -14.0):
            response = self.client.post("/api/settings", json={
                "buffer_target": "30", "suppression_mode": "DSP_MONO_ADAPTIVE_FORMANT", "ducking_level": "-18.5",
            })
            self.assertEqual(response.status_code, 200)
            persist.assert_called_once_with({
                "INITIAL_BUFFER_SECONDS": "30.0", "SUPPRESSION_MODE": "DSP_MONO_ADAPTIVE_FORMANT", "BGM_VOLUME_DUCKED_DB": "-18.5",
            })
            current = self.client.get("/api/settings").json()
            self.assertEqual(current["buffer_target"], "30")
            self.assertEqual(current["suppression_mode"], "DSP_MONO_ADAPTIVE_FORMANT")
            self.assertEqual(current["ducking_level"], "-18.5")
            with patch.object(main, "create_streaming_session", return_value=self.session()) as create:
                self.client.post("/api/streaming/start-local-file", json={"file_path": str(self.video)})
            self.assertEqual(create.call_args.kwargs["initial_buffer_seconds"], 30.0)

    def test_invalid_audio_settings_are_rejected_before_persistence(self):
        for payload in ({"buffer_target": "nan"}, {"buffer_target": "0"}, {"buffer_target": "121"},
                        {"suppression_mode": "unknown"}, {"ducking_level": "nan"}):
            with self.subTest(payload=payload), patch.object(main, "update_env_file") as persist:
                response = self.client.post("/api/settings", json=payload)
                self.assertEqual(response.status_code, 422)
                persist.assert_not_called()

    def test_invalid_stream_buffer_does_not_create_session(self):
        for buffer_seconds in (0, -1, 121):
            with self.subTest(buffer_seconds=buffer_seconds), patch.object(main, "create_streaming_session") as create:
                response = self.client.post("/api/streaming/start-local-file", json={
                    "file_path": str(self.video), "initial_buffer_seconds": buffer_seconds,
                })
                self.assertEqual(response.status_code, 422)
                create.assert_not_called()

    def test_pause_and_resume_reject_finished_session(self):
        session = self.session(pause=Mock(), resume=Mock())
        main.active_streaming_sessions[session.task_id] = session
        for action in ("pause", "resume"):
            response = self.client.post(f"/api/tasks/{session.task_id}/{action}")
            self.assertEqual(response.status_code, 409)
            getattr(session, action).assert_not_called()

    def test_export_rejects_duplicate_while_running_or_cancelling(self):
        session = self.session()
        main.active_streaming_sessions[session.task_id] = session
        for status in ("RUNNING", "CANCELLING"):
            export = {"status": status, "progress": 25}
            main.active_export_tasks[f"export_{session.task_id}"] = export
            with self.subTest(status=status), patch.object(main, "HQExporter") as create:
                response = self.client.post("/api/streaming/export-hq", json={"task_id": session.task_id})
                self.assertEqual(response.status_code, 409)
                create.assert_not_called()
                self.assertIs(main.active_export_tasks[f"export_{session.task_id}"], export)

    def test_cancel_export_keeps_worker_busy_until_it_acknowledges(self):
        main.active_export_tasks["export_fixture"] = {"status": "RUNNING", "cancelled": False}
        response = self.client.post("/api/streaming/export-hq/cancel/fixture")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(main.active_export_tasks["export_fixture"]["status"], "CANCELLING")
        self.assertTrue(main.active_export_tasks["export_fixture"]["cancelled"])
        main.active_export_tasks["export_fixture"]["status"] = "COMPLETED"
        response = self.client.post("/api/streaming/export-hq/cancel/fixture")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(main.active_export_tasks["export_fixture"]["status"], "COMPLETED")

    def test_websocket_replays_startup_failure_to_late_connection(self):
        session = self.session(error="Input audio missing", first_play_emitted=False)
        session.get_telemetry.return_value = {"status": "failed", "error": session.error}
        main.active_streaming_sessions[session.task_id] = session
        with self.client.websocket_connect(f"/ws/stream/{session.task_id}") as websocket:
            messages = [websocket.receive_json() for _ in range(3)]
        self.assertEqual([message["type"] for message in messages], ["init", "telemetry", "error"])
        self.assertEqual(messages[-1]["message"], session.error)

    def test_websocket_reconnect_reports_actual_fallback_provider_and_evidence(self):
        sources = [{"provider": "openrouter-free", "model": "example/text:free", "evidence_mode": "asr-ocr-text"}]
        warnings = ["OpenRouter dùng bản chép và OCR; chưa xem/nghe video."]
        session = self.session(visual_translation=True, translation_sources=sources, warnings=warnings,
                               source_processing_label=lambda: "Faster-Whisper + OCR tại máy; OpenRouter · dịch văn bản")
        segment = session.segments[0]
        segment.source_method = "text-ai"
        segment.translation_provider = "openrouter-free"
        segment.translation_model = "example/text:free"
        segment.evidence_mode = "asr-ocr-text"
        segment.subtitle_cues = [{"start": .15, "end": .8, "text": "Xin chào"}]
        segment.subtitle_timing_source = "tts-boundaries"
        segment.speech_start, segment.speech_end = .15, .8
        main.active_streaming_sessions[session.task_id] = session
        with self.client.websocket_connect(f"/ws/stream/{session.task_id}") as websocket:
            initial = websocket.receive_json()
        self.assertEqual(initial["type"], "init")
        self.assertEqual(initial["translation_sources"], sources)
        self.assertEqual(initial["warnings"], warnings)
        self.assertIn("OpenRouter", initial["asr_engine"])
        self.assertNotIn("Gemini", initial["asr_engine"])
        self.assertEqual(initial["segments"][0]["source_method"], "text-ai")
        self.assertEqual(initial["segments"][0]["evidence_mode"], "asr-ocr-text")
        snapshot = initial["segments"][0]
        self.assertEqual(snapshot["caption_layout"], session.segment_snapshot(segment)["caption_layout"])
        self.assertEqual(snapshot["caption_bottom_layout"], session.segment_snapshot(segment)["caption_bottom_layout"])
        cue = snapshot["caption_layout"]["cues"][0]
        self.assertEqual((cue["start"], cue["end"], cue["text"]), (.15, .8, "Xin chào"))
        self.assertEqual(cue["background"], "white")
        self.assertEqual(snapshot["caption_layout"]["video_size"], [1080, 1920])
        self.assertNotIn("audio_path", snapshot)


if __name__ == "__main__":
    unittest.main()
