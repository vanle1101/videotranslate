"""A real partial MP4 remains downloadable after reloading its saved project."""
import asyncio
import hashlib
import shutil
import subprocess

import httpx
import pytest

import main
from config import settings
from core.streaming import session_store
from test_final_output_gate import omitted_speech


def test_partial_mp4_download_and_restore(omitted_speech, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    sess = omitted_speech
    sess.auto_export_result = False
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "none")
    output_app = next(route.app for route in main.app.routes if getattr(route, "name", "") == "outputs")
    monkeypatch.setattr(output_app, "directory", str(settings.OUTPUT_DIR))
    monkeypatch.setattr(output_app, "all_directories", [str(settings.OUTPUT_DIR)])
    def ffmpeg(*args):
        return subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *map(str, args)],
            capture_output=True, check=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=180x320:r=15:d=4",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", sess.video_path)
    sess.video_size = (180, 320)
    ready, failed = sess.segments.values()
    ready.final_vi = "Xin chào."
    ready.audio_path = str(sess.segments_dir / "seg_0.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=880:duration=1", ready.audio_path)
    failed.status, failed.final_vi = "FAILED", "Phụ đề vẫn giữ."
    sess.error = "TTS failed"
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            response = await api.post("/api/streaming/export-hq", json={"task_id":sess.task_id, "allow_incomplete":True})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["output_missing_segment_ids"] == [1]
            output = settings.OUTPUT_DIR / result["output_filename"]
            decoded = ffmpeg("-ss", "2.5", "-i", output, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1").stdout
            assert sum(r>180 and g>150 and b<100 for r,g,b in zip(decoded[::3],decoded[1::3],decoded[2::3])) > 10
            # Missing voice remains silent without dropping its translated subtitle.
            audio = ffmpeg("-ss", "2.5", "-i", output, "-t", "0.25", "-f", "s16le", "-ac", "1", "pipe:1").stdout
            import array
            samples=array.array("h",audio)
            assert max(map(abs,samples)) < 100
            response = await api.get(result["output_video_url"])
            assert response.status_code == 200
            assert hashlib.sha256(response.content).digest() == hashlib.sha256(output.read_bytes()).digest()
            main.active_streaming_sessions.clear()
            main.active_export_tasks.clear()
            restored = session_store.restore_saved_session(sess.task_id)
            assert restored.output_video_url == result["output_video_url"]
            assert restored.output_incomplete and restored.output_missing_segment_ids == [1]
            assert restored.segments[1].status == "FAILED" and restored.segments[1].final_vi == failed.final_vi
            tasks=(await api.get("/api/tasks")).json()["tasks"]
            row=next(row for row in tasks if row["task_id"] == sess.task_id)
            assert row["output_incomplete"] and row["output_video_url"] == result["output_video_url"]
    asyncio.run(run())
