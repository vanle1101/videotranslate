"""The acceptance runner mutates only explicit backend owners and preserves edits."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import httpx
import pytest

from scripts import acceptance_recovery as runner


def test_old_backend_revision_blocks_recovery_before_mutations(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from config import settings
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    calls = []
    async def observed(client, method, route, **kwargs):
        calls.append((method, route))
        if route == "/api/health":
            return {"status": "ok", "source_revision": "old"}
        return {"paths": {"/api/streaming/export-hq/jobs": {}}}
    monkeypatch.setattr(runner, "api", observed)
    monkeypatch.setattr(runner, "recover_one", lambda *a, **k: pytest.fail("Old runtime must not own recovery"))
    with pytest.raises(RuntimeError, match="predates"):
        asyncio.run(runner.run(SimpleNamespace(backend="http://127.0.0.1:8000", project=["video"],
                                              execute=True, timeout=10, poll=.1)))
    assert calls == [("GET", "/api/health"), ("GET", "/openapi.json")]
    evidence = json.loads(next((tmp_path / "audit").glob("*/backend.json")).read_text())
    assert evidence["source_revision_matches"] is False


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_poll_disconnect_is_retried_without_replaying_mutations(monkeypatch, method):
    calls = []
    async def no_sleep(seconds):
        pass
    monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)
    def handle(request):
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("connection closed", request=request)
        return httpx.Response(200, json={"status": "RUNNING"})
    async def run():
        async with httpx.AsyncClient(base_url="http://localhost:8000", transport=httpx.MockTransport(handle)) as client:
            return await runner.api(client, method, "/snapshot")
    if method == "GET":
        assert asyncio.run(run())["status"] == "RUNNING"
        assert calls == ["GET", "GET"]
    else:
        with pytest.raises(httpx.RemoteProtocolError):
            asyncio.run(run())
        assert calls == ["POST"]


@pytest.mark.parametrize("url", ["https://127.0.0.1:8000", "http://example.com:8000", "http://localhost",
                                 "http://user:password@localhost:8000", "http://localhost:8000/path"])
def test_only_explicit_local_backend_is_accepted(url):
    with pytest.raises(ValueError):
        runner.local_backend(url)


def test_manual_edits_cannot_disappear_or_change():
    rows = [{"id": 4, "start": 1., "end": 2., "revision": 2, "final_vi": "Đừng đi.", "verification": {"status": "manual"}}]
    baseline = runner.manual_rows(rows)
    runner.verify_manual(baseline, rows)
    with pytest.raises(RuntimeError, match="Manual rows"):
        runner.verify_manual(baseline, [dict(rows[0], final_vi="Đi đi.")])
    with pytest.raises(RuntimeError, match="Manual rows"):
        runner.verify_manual(baseline, [])


def test_actual_output_media_is_fully_decoded_and_duration_checked(tmp_path):
    output = tmp_path / "owned.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=64x96:r=10:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(output)],
        check=True, capture_output=True, timeout=30)
    evidence = runner.validate_media(output, 1., tmp_path)
    assert evidence["complete_decode"] and evidence["sha256"] == runner.digest(output)
    with pytest.raises(RuntimeError, match="duration differs"):
        runner.validate_media(output, 3., tmp_path)


def test_source_identity_must_match_the_backed_up_local_project(tmp_path):
    source = tmp_path / "source.mp4"
    runner.verify_source_url("/api/inputs/source.mp4", source, tmp_path)
    with pytest.raises(RuntimeError, match="does not match"):
        runner.verify_source_url("/api/inputs/other.mp4", source, tmp_path)
    with pytest.raises(RuntimeError, match="local media"):
        runner.verify_source_url("https://example.com/source.mp4", source, tmp_path)


def test_exclusive_recovery_lock_never_overwrites_another_owner(tmp_path):
    with runner.project_lock(tmp_path, "video"):
        before = (tmp_path / "video.lock").read_bytes()
        with pytest.raises(FileExistsError):
            with runner.project_lock(tmp_path, "video"):
                pytest.fail("Must not enter duplicate owner")
        assert (tmp_path / "video.lock").read_bytes() == before
    assert not (tmp_path / "video.lock").exists()


@pytest.fixture
def saved(tmp_path, monkeypatch):
    from core.streaming import session_store
    from config import settings
    source = tmp_path / "source.mp4"
    source.write_bytes(b"owned source")
    manifest = tmp_path / "project.json"
    manifest.write_bytes(b"original compressed or plain bytes retained verbatim")
    row = {"id": 4, "start": 0., "end": 1., "revision": 2, "final_vi": "Đừng đi.", "audio_path": None,
           "verification": {"status": "manual"}}
    record = {"session": {"video_path": str(source), "voice": "saved-voice", "tts_engine_name": "edge-tts"},
              "segments": [row]}
    monkeypatch.setattr(session_store, "_read", lambda task_id: deepcopy(record))
    monkeypatch.setattr(session_store, "_project_path", lambda task_id: manifest)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    return source, manifest, row


def test_default_plan_only_backs_up_without_http_or_project_write(saved, tmp_path):
    source, manifest, row = saved
    before = manifest.read_bytes()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("No HTTP mutation in planning"))) as client:
            return await runner.recover_one(client, "video", tmp_path / "plan")
    result = asyncio.run(run())
    assert result["status"] == "PLANNED"
    assert manifest.read_bytes() == before
    assert (tmp_path / "plan/project.before.json").read_bytes() == before
    assert json.loads((tmp_path / "plan/baseline.json").read_text(encoding="utf-8"))["source_sha256"] == runner.digest(source)


@pytest.mark.parametrize("gap_remains", [False, True])
def test_execute_retries_only_incomplete_then_requires_strict_media_export(saved, tmp_path, monkeypatch, gap_remains):
    source, manifest, row = saved
    before = manifest.read_bytes()
    calls = []
    retried = False
    snapshot = {"voice": "saved-voice", "tts_engine": "edge-tts", "duration": 1., "segments": [row],
                "video_url": "/api/local-file?path=" + source.as_posix(),
                "progress": {"status": "PREPARED", "translation_mode": "full", "can_retry": True}}
    def respond(request):
        nonlocal retried
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/streaming/video/retry":
            assert request.method == "POST"
            retried = True
            return httpx.Response(200, json={"status": "retrying"})
        if request.url.path == "/api/streaming/video":
            complete = retried and not gap_remains
            return httpx.Response(200, json={**snapshot, "media_complete": complete, "final_output_blocked": not complete})
        if request.url.path == "/api/tasks":
            return httpx.Response(200, json={"tasks": []})
        if request.url.path == "/api/streaming/export-hq/jobs":
            assert json.loads(request.content) == {"task_id": "video", "allow_incomplete": False}
            return httpx.Response(202, json={"status": "RUNNING"})
        if request.url.path == "/api/streaming/export-hq/status/video":
            return httpx.Response(200, json={"status": "COMPLETED", "output_filename": "result.mp4",
                                            "output_is_draft": True, "output_review_segment_ids": [4]})
        pytest.fail(str(request.url))
    monkeypatch.setattr(runner, "validate_media", lambda *args: {"complete_decode": True})
    async def run():
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(respond)) as client:
            return await runner.recover_one(client, "video", tmp_path / "audit", execute=True, poll=.001)
    if gap_remains:
        with pytest.raises(RuntimeError, match="Full media still incomplete"):
            asyncio.run(run())
        assert not any(route.endswith("/jobs") for _, route in calls)
    else:
        result = asyncio.run(run())
        assert result["status"] == "VALIDATED" and result["manual_rows_preserved"] == 1
        assert result["output_is_draft"] is True
    assert calls.count(("POST", "/api/streaming/video/retry")) == 1
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("preview_repaired", [False, True])
def test_failed_preview_is_retried_before_conditional_full_promotion(saved, tmp_path, monkeypatch, preview_repaired):
    source, _, row = saved
    actions = []
    snapshot = {"voice": "saved-voice", "tts_engine": "edge-tts", "duration": 1., "segments": [row],
                "video_url": "/api/local-file?path=" + source.as_posix()}
    def respond(request):
        path = request.url.path
        if request.method == "POST":
            actions.append(path)
            return httpx.Response(202, json={"status": "RUNNING"})
        if path == "/api/streaming/video":
            promoted = any(action.endswith("translate-full") for action in actions)
            repaired = bool(actions) and preview_repaired
            return httpx.Response(200, json={**snapshot, "media_complete": promoted, "final_output_blocked": not promoted,
                "progress": {"status": "PREPARED" if promoted else "PREVIEW_READY" if repaired else "FAILED",
                    "translation_mode": "full" if promoted else "preview", "can_retry": True,
                    "can_translate_full": repaired}})
        if path == "/api/tasks":
            return httpx.Response(200, json={"tasks": []})
        if path.endswith("status/video"):
            return httpx.Response(200, json={"status": "COMPLETED", "output_filename": "result.mp4"})
        pytest.fail(path)
    monkeypatch.setattr(runner, "validate_media", lambda *args: {"complete_decode": True})
    async def run():
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(respond)) as client:
            return await runner.recover_one(client, "video", tmp_path / "audit", execute=True, poll=.001)
    if preview_repaired:
        assert asyncio.run(run())["status"] == "VALIDATED"
        assert actions[:2] == ["/api/streaming/video/retry", "/api/streaming/video/translate-full"]
    else:
        with pytest.raises(RuntimeError, match="cannot be promoted"):
            asyncio.run(run())
        assert actions == ["/api/streaming/video/retry"]
