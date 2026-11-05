import asyncio
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import socket
import struct
import wave

import httpx
import pytest

import main
from core.media_process import run_media
from core.translation_review import AutomaticTranslationReviewer
from core.streaming import pipeline
from core.streaming.export import HQExporter


@pytest.mark.parametrize("mode", [None, "full"], ids=["default-full", "explicit-full"])
def test_link_and_selected_voice_publish_complete_draft_without_more_actions(monkeypatch, tmp_path, mode):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real artifact acceptance requires local ffmpeg and ffprobe")

    workspace = tmp_path / "workspace"
    for name, value in {
        "BASE_DIR": tmp_path,
        "WORKSPACE_DIR": workspace,
        "INPUT_DIR": workspace / "inputs",
        "OUTPUT_DIR": workspace / "outputs",
        "TEMP_DIR": workspace / "temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(main.settings, name, value)
    monkeypatch.setattr(main.settings, "ASR_ENGINE", "sensevoice")
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", "opencode")
    monkeypatch.setattr(main.settings, "SEPARATION_ENGINE", "dsp")
    monkeypatch.setattr(main.settings, "DIARIZATION_ENABLED", False)
    monkeypatch.setattr(main.settings, "TRANSLATION_CHUNK_WORKERS", 1)
    registry = {}
    monkeypatch.setattr(main, "active_streaming_sessions", registry)
    monkeypatch.setattr(pipeline, "active_streaming_sessions", registry)
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "task_history", [])
    monkeypatch.setattr(main, "stream_sockets", {})
    for route in main.app.routes:
        if getattr(route, "path", None) == "/api/outputs":
            monkeypatch.setattr(route.app, "directory", str(main.settings.OUTPUT_DIR))
            monkeypatch.setattr(route.app, "all_directories", [str(main.settings.OUTPUT_DIR)])

    source = main.settings.INPUT_DIR / "fixture.mp4"
    run_media([
        "ffmpeg", "-v", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=navy:s=96x160:r=10:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(source),
    ], timeout=30)
    calls = {"download": [], "asr": [], "translate": [], "tts": [], "export": [], "requests": [], "events": [],
             "review_llm": [], "review_ocr": [], "review_audio": []}
    selected_voice = "vi-VN-NamMinhNeural"
    url = "https://www.douyin.com/video/1234567890123456789"

    def download(request_url, *, progress_callback, cancel_check):
        assert request_url == url and not cancel_check()
        calls["download"].append(request_url)
        progress_callback({"phase": "download", "stage": "Fixture download", "progress_pct": 100})
        return {"file_path": str(source)}

    class ASR:
        name = "Offline fixture ASR"
        is_available = True

        def transcribe(self, audio_path, **kwargs):
            with wave.open(str(audio_path), "rb") as audio:
                assert audio.getnframes() > 0
            calls["asr"].append(str(audio_path))
            return [{"text_zh": "早上好", "emotion": "<|NEUTRAL|>"}]

    class Translator:
        def translate_single_segment(self, *, text_zh, duration, rolling_context):
            assert text_zh == "早上好" and duration > 0
            calls["translate"].append(text_zh)
            return {"literal_vi": "Chào buổi sáng.", "natural_vi": "Chào buổi sáng.", "final_vi": "Chào buổi sáng.",
                    "needs_review": True, "review_reason": "Nguồn chưa được kiểm chứng độc lập."}

    class TTS:
        name = "Offline fixture TTS"

        def synthesize(self, *, text, output_path, voice, ref_audio=None):
            calls["tts"].append({"voice": voice, "text": text})
            assert voice == selected_voice
            frames = b"".join(struct.pack("<h", round(5000 * math.sin(2 * math.pi * 660 * i / 24000)))
                              for i in range(19200))
            with wave.open(str(output_path), "wb") as audio:
                audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                audio.writeframes(frames)
            return output_path

    class ReviewClient:
        has_credentials = True
        model = "offline-review-fixture"

        def translate(self, prompt, **kwargs):
            payload = prompt.split("Câu cần kiểm định: ", 1)[1].split("\n", 1)[0]
            rows = json.loads(payload)
            calls["review_llm"].append(deepcopy(rows))
            return {"segments": [{**row, "semantic_verified": True, "source_evidence_ids": [],
                    "verification_reason": "Lời chào buổi sáng giữ nghĩa nguồn, chưa đủ bằng chứng độc lập."}
                    for row in rows], "screen_texts": [], "summary": ""}

    class ReviewOCR:
        def extract(self, video_path, start, end, cancel_check):
            assert Path(video_path).is_file() and not cancel_check()
            calls["review_ocr"].append((start, end))
            return []

        def close(self):
            pass

    class ReviewAudio:
        def collect(self, video_path, rows, cancel_check, progress_callback):
            assert Path(video_path).is_file() and not cancel_check()
            calls["review_audio"].append(deepcopy(rows))
            progress_callback(100)
            return {row["id"]: {"sensevoice": "早上好", "faster-whisper-small": "晚上好"}
                    for row in rows}

    real_review_init = AutomaticTranslationReviewer.__init__

    def review_init(self, *args, **kwargs):
        assert not args and not kwargs
        real_review_init(self, client=ReviewClient(), screen_ocr=ReviewOCR(), audio_evidence=ReviewAudio())

    monkeypatch.setattr(AutomaticTranslationReviewer, "__init__", review_init)
    monkeypatch.setattr(main.downloader, "download", download)
    monkeypatch.setattr(pipeline, "SenseVoiceEngine", ASR)
    monkeypatch.setattr(pipeline, "FasterWhisperFallbackEngine", ASR)
    monkeypatch.setattr(pipeline, "SemanticTranslator", Translator)
    monkeypatch.setattr(pipeline, "EdgeTTSFallbackEngine", TTS)
    real_export = HQExporter.export

    def observed_export(self, *args, **kwargs):
        assert kwargs["allow_incomplete"] is False
        calls["export"].append({"allow_incomplete": kwargs["allow_incomplete"],
                                "segments": deepcopy(kwargs["segments"])})
        return real_export(self, *args, **kwargs)

    monkeypatch.setattr(HQExporter, "export", observed_export)
    real_broadcast = main.broadcast_session_event

    async def observed_broadcast(task_id, event_type, data):
        calls["events"].append((event_type, deepcopy(data)))
        await real_broadcast(task_id, event_type, data)

    monkeypatch.setattr(main, "broadcast_session_event", observed_broadcast)

    def deny_network(*args, **kwargs):
        raise AssertionError("Offline acceptance cannot open a network connection")

    async def exercise():
        monkeypatch.setattr(socket.socket, "connect", deny_network)
        monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
        request = {"url": url, "voice_id": selected_voice}
        if mode is not None:
            request["translation_mode"] = mode
        assert main.StreamUrlRequest(**request).translation_mode == "full"
        assert main.ExportHQRequest(task_id="fixture").allow_incomplete is False

        async def record_request(req):
            calls["requests"].append((req.method, req.url.path))

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test",
                                     event_hooks={"request": [record_request]}) as client:
            response = await client.post("/api/streaming/start-url", json=request)
            assert response.status_code == 200, response.text
            task_id = response.json()["task_id"]
            session = registry[task_id]
            assert session.voice == selected_voice
            assert session.tts_engine_name == "edge-tts"
            assert session.translation_mode == "full"
            assert session.auto_export_result is True
            assert session.event_callback is not None
            try:
                async def wait_for_result():
                    while not getattr(session, "output_filename", ""):
                        assert not session.error, session.error
                        failures = [payload for kind, payload in calls["events"] if kind == "result_error"]
                        assert not failures, failures
                        await asyncio.sleep(.02)
                    await session.auto_export_task

                await asyncio.wait_for(wait_for_result(), 90)
                first_export = session.auto_export_task
                await session.emit("finished", session.get_telemetry())
                await session.emit("finished", session.get_telemetry())
                assert session.auto_export_task is first_export
                assert len(calls["export"]) == 1
                assert calls["requests"] == [("POST", "/api/streaming/start-url")]
                assert main.stream_sockets == {}
                assert len(calls["download"]) == 1
                assert calls["asr"] and calls["translate"] and calls["tts"]
                assert all(call["voice"] == selected_voice for call in calls["tts"])
                assert len(calls["review_llm"]) == 2
                assert calls["review_ocr"] and calls["review_audio"]
                assert session.review_summary["status"] == "completed"
                assert session.review_summary["unresolved"] == len(session.segments)
                event_types = [kind for kind, _ in calls["events"]]
                assert event_types.count("review_complete") == 1
                assert event_types.index("review_complete") < event_types.index("result_ready")
                ready = [data for kind, data in calls["events"] if kind == "result_ready"]
                assert len(ready) == 1
                result = ready[0]
                assert result["output_incomplete"] is False
                assert result["output_is_draft"] is True
                assert result["content_review_state"] == "REVIEW_REQUIRED"
                assert result["review_warning"]
                assert all(row.needs_review for row in session.segments.values())
                assert all(row.verification.get("status") == "unresolved" for row in session.segments.values())
                assert all(row.verification["source_supported"] is False for row in session.segments.values())
                assert all(row.verification["audio_consensus"] is False for row in session.segments.values())
                assert all(row.verification["second_pass_status"] == "completed" for row in session.segments.values())
                final = main.settings.OUTPUT_DIR / session.output_filename
                assert final.is_file() and final.stat().st_size > 0
                HQExporter.validate_output(final, session.total_duration)
                assert session.total_duration == pytest.approx(2, abs=.1)
                digest = hashlib.sha256(final.read_bytes()).hexdigest()
                served = await client.get(result["output_video_url"])
                assert served.status_code == 200
                assert hashlib.sha256(served.content).hexdigest() == digest
                sidecar = final.with_suffix(".review.json")
                audit = json.loads(sidecar.read_text(encoding="utf-8"))
                assert audit["output_is_draft"] is True
                assert audit["output_incomplete"] is False
                (tmp_path / "artifact-evidence.json").write_text(json.dumps({
                    "path": str(final), "sha256": digest, "bytes": final.stat().st_size,
                    "duration": session.total_duration, "voice": session.voice,
                    "export_count": len(calls["export"]), "initial_request": request,
                    "output_is_draft": result["output_is_draft"],
                    "content_review_state": result["content_review_state"],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                owned = {getattr(session, name, None) for name in (
                    "start_task", "worker_task", "auto_export_task", "export_task", "native_audit_task", "review_task")}
                running = [task for task in owned if task is not None and not task.done()]
                if running:
                    session.stop()
                    await asyncio.gather(*running, return_exceptions=True)
                session._release_runtime()

    asyncio.run(exercise())
