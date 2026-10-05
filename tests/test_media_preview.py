"""Offline preview API/cache regressions; no configuration writes or AI calls."""
import json
import subprocess
import time
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from core.media_preview import PreviewManager


@pytest.fixture
def preview(tmp_path):
    manager = PreviewManager()
    video = tmp_path / 'source tiếng Việt.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x240:d=0.5',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5', '-c:v', 'libx264', '-c:a', 'aac',
        '-pix_fmt', 'yuv420p', '-shortest', str(video),
    ], check=True, capture_output=True, timeout=30)
    with patch.object(settings, 'TEMP_DIR', tmp_path), patch.object(main, 'preview_manager', manager):
        with TestClient(main.app) as client:
            yield client, manager, video
        manager.shutdown()
    assert video.exists()
    assert not list(tmp_path.glob('media-preview-*'))


def wait_ready(client, status):
    deadline = time.monotonic() + 10
    while status['status'] == 'PROCESSING' and time.monotonic() < deadline:
        time.sleep(0.05)
        status = client.get('/api/preview/' + status['preview_id']).json()
    assert status['status'] == 'READY', status
    return status


def test_local_preview_transcodes_once_returns_seekable_webm_and_preserves_source(preview):
    client, manager, source = preview
    original = source.read_bytes()
    response = client.post('/api/preview', json={'file_path': str(source)})
    assert response.status_code == 200
    ready = wait_ready(client, response.json())
    repeated = client.post('/api/preview', json={'file_path': str(source)}).json()
    assert repeated['preview_id'] == ready['preview_id']
    output = manager.media(ready['preview_id'])
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)],
                           capture_output=True, check=True, timeout=10)
    codecs = {stream['codec_name'] for stream in json.loads(probe.stdout)['streams']}
    assert codecs == {'vp8', 'opus'}
    media = client.get(ready['video_url'], headers={'Range': 'bytes=0-99'})
    assert media.status_code == 206 and len(media.content) == 100
    assert media.headers['content-type'] == 'video/webm'
    assert source.read_bytes() == original


def test_session_source_covers_downloaded_url_and_uploaded_video(preview):
    client, manager, source = preview
    with patch.object(main, 'get_streaming_session', return_value=SimpleNamespace(video_path=source)):
        response = client.post('/api/preview', json={'task_id': 'download-or-upload'})
    ready = wait_ready(client, response.json())
    assert client.get(ready['video_url']).status_code == 200


def test_browser_file_preview_upload_is_owned_and_cleaned(preview):
    client, manager, source = preview
    response = client.post('/api/preview/upload', files={'file': ('video.mp4', source.read_bytes(), 'video/mp4')})
    assert response.status_code == 200
    ready = wait_ready(client, response.json())
    assert manager.media(ready['preview_id']).exists()
    deadline = time.monotonic() + 2
    while list(manager._root.glob('source-*')) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not list(manager._root.glob('source-*'))


def test_invalid_preview_reports_failure_and_leaves_no_partial_video(preview):
    client, manager, source = preview
    invalid = source.parent / 'invalid.mp4'
    invalid.write_bytes(b'not video')
    response = client.post('/api/preview', json={'file_path': str(invalid)})
    key = response.json()['preview_id']
    deadline = time.monotonic() + 5
    while manager.status(key)['status'] == 'PROCESSING' and time.monotonic() < deadline:
        time.sleep(0.05)
    assert manager.status(key)['status'] == 'FAILED'
    assert manager.status(key)['error']
    assert not list(manager._root.glob('*.webm'))
    assert client.get(f'/api/preview/{key}/media').status_code == 404


def test_missing_preview_source_and_unknown_job_return_404(preview):
    client, _, source = preview
    assert client.post('/api/preview', json={'file_path': str(source.parent / 'absent')}).status_code == 404
    assert client.get('/api/preview/unknown').status_code == 404


def test_shutdown_cancels_active_conversion_and_removes_owned_directory(preview):
    _, manager, source = preview
    started = threading.Event()
    def media_process(command, cancel_check, **kwargs):
        started.set()
        deadline = time.monotonic() + 5
        while not cancel_check() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cancel_check(), 'Preview shutdown failed to request process cancellation'
        raise RuntimeError('cancelled')
    with patch('core.media_preview.run_media', media_process):
        manager.start(source)
        assert started.wait(2)
        root = manager._root
        manager.shutdown()
    assert not root.exists()
    assert source.exists()
