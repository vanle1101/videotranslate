"""Per-click worker caps validate before side effects and never overwrite restores."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
import httpx
import main
from config import TRANSLATION_CHUNK_WORKER_OPTIONS


@pytest.fixture
def starts(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "INPUT_DIR", tmp_path)
    monkeypatch.setattr(main.settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(main.settings, "TRANSLATION_CHUNK_WORKERS", 5)
    monkeypatch.setattr(main.settings, "OPENCODE_CONCURRENCY", 5)
    monkeypatch.setattr(main, "active_streaming_sessions", {})
    monkeypatch.setattr(main, "validate_visual_translation", lambda value: None)
    monkeypatch.setattr(main, "validated_voice", lambda *args: ("edge-tts", "vi-VN-HoaiMyNeural"))
    monkeypatch.setattr(main.downloader, "normalize_url", lambda value: value)
    restore = AsyncMock(return_value=(None, False))
    monkeypatch.setattr(main, "_restore_matching_saved_project", restore)
    monkeypatch.setattr(main, "run_session", AsyncMock())
    mutation = Mock(side_effect=AssertionError("Start must not update global settings"))
    monkeypatch.setattr(main, "update_env_file", mutation)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"isolated source")
    created = []
    def create(**kwargs):
        session = SimpleNamespace(task_id=kwargs["task_id"], initial_buffer_seconds=10.,
            translation_chunk_workers=main.settings.TRANSLATION_CHUNK_WORKERS,
            source_video_url=None, start_from_url=AsyncMock())
        session.get_progress = lambda: {"configured_chunk_workers": session.translation_chunk_workers}
        created.append(session)
        return session
    monkeypatch.setattr(main, "create_streaming_session", create)
    def request(path, value=None, *, omitted=False):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
                if path == "upload":
                    data = {} if omitted else {"translation_chunk_workers": value}
                    result = await api.post("/api/streaming/start-upload", data=data,
                        files={"file": ("clip.mp4", b"upload", "video/mp4")})
                else:
                    payload = {"url": "https://example.com/video"} if path == "url" else {"file_path": str(source)}
                    if not omitted:
                        payload["translation_chunk_workers"] = value
                    result = await api.post("/api/streaming/start-" + ("url" if path == "url" else "local-file"), json=payload)
                await asyncio.sleep(0)
                return result
        return asyncio.run(run())
    return SimpleNamespace(request=request, created=created, restore=restore, root=tmp_path, mutation=mutation)


@pytest.mark.parametrize("path", ["url", "local", "upload"])
@pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
def test_new_owner_uses_selected_workers_without_global_mutation(starts, path, selected):
    response = starts.request(path, str(selected) if path == "upload" else selected)
    assert response.status_code == 200, response.text
    assert starts.created[-1].translation_chunk_workers == selected
    assert main.settings.TRANSLATION_CHUNK_WORKERS == main.settings.OPENCODE_CONCURRENCY == 5
    starts.mutation.assert_not_called()


@pytest.mark.parametrize("path", ["url", "local", "upload"])
def test_omitted_choice_retains_api_global_default(starts, path):
    assert starts.request(path, omitted=True).status_code == 200
    assert starts.created[-1].translation_chunk_workers == 5


@pytest.mark.parametrize("path", ["url", "local"])
@pytest.mark.parametrize("invalid", [True, False, "20", 20.0, 2, 0, -1, 21, 51, [], {}])
def test_json_invalid_worker_types_and_presets_reject_before_create(starts, path, invalid):
    assert starts.request(path, invalid).status_code == 422
    assert starts.created == []
    starts.restore.assert_not_called()
    starts.mutation.assert_not_called()


@pytest.mark.parametrize("invalid", ["true", "false", "20.0", "2", "0", "-1", "51", "n/a", "1e1", " 20 ", "020"])
def test_form_invalid_worker_text_rejects_before_upload_write(starts, invalid):
    assert starts.request("upload", invalid).status_code == 422
    assert starts.created == []
    assert list(starts.root.glob("upload_*")) == []
    starts.mutation.assert_not_called()


@pytest.mark.parametrize("path", ["url", "local"])
def test_reopened_project_keeps_saved_cap_even_when_start_selects_other_cap(starts, path):
    restored = SimpleNamespace(task_id="saved", initial_buffer_seconds=10., source_video_url="/saved",
        translation_chunk_workers=3, get_progress=lambda: {"configured_chunk_workers": 3})
    starts.restore.return_value = restored, True
    response = starts.request(path, 50)
    assert response.status_code == 200
    assert response.json()["progress"]["configured_chunk_workers"] == 3
    assert restored.translation_chunk_workers == 3
    assert starts.created == []
    assert main.settings.TRANSLATION_CHUNK_WORKERS == 5


def test_each_new_video_keeps_its_own_workers(starts):
    starts.request("local", 3)
    starts.request("url", 40)
    assert [s.translation_chunk_workers for s in starts.created] == [3, 40]


def test_start_dropdown_is_accessible_default_twenty_and_keeps_settings_control():
    import re
    html = main.templates.get_template("index.html").render(settings=main.settings)
    control = re.search(r'<select id="start-translation-workers"[^>]*>(.*?)</select>', html, re.S)
    assert control
    assert 'aria-describedby="start-translation-workers-help"' in control.group(0)
    assert re.findall(r'<option value="(\d+)"', control.group(1)) == [str(n) for n in TRANSLATION_CHUNK_WORKER_OPTIONS]
    assert re.findall(r'<option value="(\d+)" selected', control.group(1)) == ["20"]
    assert 'for="start-translation-workers"' in html
    assert 'id="settings-translation-workers"' in html


@pytest.mark.parametrize("invalid", [True, False, 20, 20.0, [], {}])
def test_form_parser_does_not_coerce_python_boolean_or_numeric_values(invalid):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        main.parse_upload_workers(invalid)
    assert caught.value.status_code == 422
