"""Offline preview API/cache regressions; no configuration writes or AI calls."""
import json
import os
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
    with patch.object(settings, 'TEMP_DIR', tmp_path), patch.object(settings, 'OUTPUT_DIR', tmp_path), \
            patch.object(main, 'preview_manager', manager):
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


def test_chunked_preview_uses_a_bounded_prefix_and_exposes_coverage(preview):
    client, manager, source = preview
    response = client.post('/api/preview', json={
        'file_path': str(source), 'coverage_seconds': 0.2,
    })
    assert response.status_code == 200
    ready = wait_ready(client, response.json())
    assert ready['partial'] is True
    assert ready['source_duration'] >= 0.45
    assert 0.15 <= ready['coverage_seconds'] <= 0.25
    probe = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(manager.media(ready['preview_id'])),
    ], capture_output=True, check=True, timeout=10)
    assert float(probe.stdout) <= ready['coverage_seconds'] + 0.1
    # A later prepared cursor gets its own cache entry; it must not reuse a
    # shorter prefix or silently claim that prefix is the complete source.
    later = client.post('/api/preview', json={
        'file_path': str(source), 'coverage_seconds': 0.4,
    }).json()
    assert later['preview_id'] != ready['preview_id']


def test_source_replacement_with_preserved_size_and_mtime_does_not_reuse_old_preview(preview):
    client, _, source = preview
    ready = wait_ready(client, client.post('/api/preview', json={'file_path': str(source)}).json())
    before = source.stat()
    # An atomic replacement has a new file identity while preserving the
    # fields used by the old cache key.  Both assets are owned test files.
    replacement_source = source.with_name('replacement.mp4')
    replacement_source.write_bytes(source.read_bytes())
    os.replace(replacement_source, source)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement = client.post('/api/preview', json={'file_path': str(source)}).json()
    assert replacement['preview_id'] != ready['preview_id']


def test_preview_failure_can_be_retried_instead_of_staying_cached_forever(preview):
    _, manager, source = preview
    original = source.read_bytes()
    invalid = source.parent / 'retry.mp4'
    invalid.write_bytes(b'not a video')
    first = manager.start(invalid, coverage_seconds=.2)
    deadline = time.monotonic() + 5
    while manager.status(first['preview_id'])['status'] == 'PROCESSING' and time.monotonic() < deadline:
        time.sleep(.02)
    assert manager.status(first['preview_id'])['status'] == 'FAILED'
    # Same invalid file still needs a fresh attempt rather than reusing a
    # terminal failure.  It can be corrected independently afterward.
    second = manager.start(invalid, coverage_seconds=.2)
    assert second['preview_id'] == first['preview_id']
    assert second['status'] == 'PROCESSING'
    invalid.write_bytes(original)


def test_later_source_window_has_global_offsets_and_separate_cache_entry(preview):
    client, manager, source = preview
    first = wait_ready(client, client.post('/api/preview', json={
        'file_path': str(source), 'start_seconds': 0, 'end_seconds': .2,
    }).json())
    later = wait_ready(client, client.post('/api/preview', json={
        'file_path': str(source), 'start_seconds': .2, 'end_seconds': .4,
    }).json())
    assert later['preview_id'] != first['preview_id']
    assert later['partial'] is True
    assert later['start_seconds'] == pytest.approx(.2, abs=.01)
    assert later['end_seconds'] == pytest.approx(.4, abs=.05)
    probe = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(manager.media(later['preview_id'])),
    ], capture_output=True, check=True, timeout=10)
    assert float(probe.stdout) <= .3


def test_preview_cancel_stops_conversion_and_deletes_media_on_eviction(preview):
    _, manager, source = preview
    original_limit = __import__('core.media_preview', fromlist=['MAX_RETAINED_JOBS']).MAX_RETAINED_JOBS
    with patch('core.media_preview.MAX_RETAINED_JOBS', 1):
        first = manager.start(source, coverage_seconds=.2)
        assert manager.cancel(first['preview_id'])['status'] == 'PROCESSING'
        deadline = time.monotonic() + 5
        while manager.status(first['preview_id'])['status'] == 'PROCESSING' and time.monotonic() < deadline:
            time.sleep(.02)
        assert manager.status(first['preview_id'])['status'] in {'CANCELLED', 'FAILED'}
        second = manager.start(source, coverage_seconds=.3)
        deadline = time.monotonic() + 5
        while manager.status(second['preview_id'])['status'] == 'PROCESSING' and time.monotonic() < deadline:
            time.sleep(.02)
        assert manager.status(second['preview_id'])['status'] == 'READY'
    assert original_limit == 8


def test_preview_api_rejects_reversed_window_before_start(preview):
    client, _, source = preview
    with patch.object(main.preview_manager, 'start') as start:
        response = client.post('/api/preview', json={
            'file_path': str(source), 'start_seconds': 1, 'end_seconds': .5,
        })
    assert response.status_code == 422
    start.assert_not_called()


def test_session_source_covers_downloaded_url_and_uploaded_video(preview):
    client, manager, source = preview
    with patch.object(main, 'get_streaming_session', return_value=SimpleNamespace(video_path=source)):
        response = client.post('/api/preview', json={'task_id': 'download-or-upload'})
    ready = wait_ready(client, response.json())
    assert client.get(ready['video_url']).status_code == 200


def test_chunked_session_preview_defaults_to_prepared_source_cursor(preview):
    client, _, source = preview
    session = SimpleNamespace(video_path=source, translation_mode='preview',
                              _source_prepared_seconds=0.2)
    with patch.object(main, 'get_streaming_session', return_value=session):
        response = client.post('/api/preview', json={'task_id': 'chunked-task'})
    assert response.status_code == 200
    ready = wait_ready(client, response.json())
    assert ready['partial'] is True
    assert ready['coverage_seconds'] <= 0.25


def test_initial_source_preview_requests_a_short_compatibility_prefix(preview):
    client, _, source = preview
    expected = {'preview_id': 'initial', 'status': 'PROCESSING', 'video_url': None}
    with patch.object(main.preview_manager, 'start', return_value=expected) as start:
        response = client.post('/api/preview', json={'file_path': str(source)})
    assert response.status_code == 200
    start.assert_called_once_with(str(source), coverage_seconds=24.0,
                                  start_seconds=0, end_seconds=24.0)


def test_chunked_preview_never_requests_beyond_prepared_cursor(preview):
    client, _, source = preview
    session = SimpleNamespace(video_path=source, translation_mode='full',
                              _source_prepared_seconds=0.2)
    expected = {'preview_id': 'clamped', 'status': 'PROCESSING', 'video_url': None}
    with patch.object(main, 'get_streaming_session', return_value=session), \
            patch.object(main.preview_manager, 'start', return_value=expected) as start:
        response = client.post('/api/preview', json={
            'task_id': 'chunked-task', 'coverage_seconds': 3,
        })
    assert response.status_code == 200
    start.assert_called_once_with(source, coverage_seconds=0.2,
                                  start_seconds=0, end_seconds=0.2)


def test_exported_result_preview_uses_existing_output_and_preserves_final_mp4(preview):
    client, manager, source = preview
    original = source.read_bytes()
    response = client.post('/api/preview', json={'output_filename': source.name})
    assert response.status_code == 200
    ready = wait_ready(client, response.json())
    assert ready['partial'] is False
    assert ready['coverage_seconds'] == pytest.approx(ready['source_duration'], abs=.05)
    media = client.get(ready['video_url'], headers={'Range': 'bytes=0-99'})
    assert media.status_code == 206 and media.headers['content-type'] == 'video/webm'
    assert source.read_bytes() == original
    repeated = client.post('/api/preview', json={'output_filename': source.name}).json()
    assert repeated['preview_id'] == ready['preview_id']
    assert not list(manager._root.glob('source-*')), 'Output previews must not upload or copy the MP4'


@pytest.mark.parametrize('name', [
    '', '../source.mp4', '..\\source.mp4', '/source.mp4', 'D:\\source.mp4',
    'nested/source.mp4', 'nested\\source.mp4', 'source.mp4:private.mp4', ' source.mp4',
    'source.mp4 ', 'source.webm', 'source\x00.mp4', 'source?.mp4',
])
def test_output_preview_rejects_invalid_filename_before_start(tmp_path, monkeypatch, name):
    monkeypatch.setattr(settings, 'OUTPUT_DIR', tmp_path)
    with TestClient(main.app) as client, patch.object(main.preview_manager, 'start') as start:
        response = client.post('/api/preview', json={'output_filename': name})
    assert response.status_code == 400
    start.assert_not_called()


@pytest.mark.parametrize('other', ['file_path', 'task_id'])
def test_output_preview_rejects_ambiguous_source(preview, other):
    client, manager, source = preview
    with patch.object(manager, 'start') as start:
        response = client.post('/api/preview', json={'output_filename': source.name, other: str(source)})
    assert response.status_code == 400
    start.assert_not_called()


@pytest.mark.parametrize('kind', ['missing', 'directory', 'symlink', 'junction', 'reparse'])
def test_output_preview_rejects_missing_or_redirected_file(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(settings, 'OUTPUT_DIR', tmp_path)
    source = tmp_path / 'result.mp4'
    if kind == 'directory':
        source.mkdir()
    elif kind != 'missing':
        source.write_bytes(b'existing result')
    if kind == 'symlink':
        monkeypatch.setattr(Path, 'is_symlink', lambda path: path == source)
    elif kind == 'junction':
        monkeypatch.setattr(Path, 'is_junction', lambda path: path == source)
    elif kind == 'reparse':
        original_lstat = Path.lstat
        monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(
            st_file_attributes=0x400, st_mode=original_lstat(path).st_mode,
        ) if path == source else original_lstat(path))
    with TestClient(main.app) as client, patch.object(main.preview_manager, 'start') as start:
        response = client.post('/api/preview', json={'output_filename': source.name})
    assert response.status_code == 404
    start.assert_not_called()


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


def test_browser_file_preview_upload_accepts_bounded_coverage(preview):
    client, manager, source = preview
    response = client.post('/api/preview/upload', data={'coverage_seconds': '0.2'},
                           files={'file': ('video.mp4', source.read_bytes(), 'video/mp4')})
    assert response.status_code == 200
    ready = wait_ready(client, response.json())
    assert ready['partial'] is True
    assert ready['coverage_seconds'] <= 0.25
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


def test_stop_at_conversion_return_cannot_publish_ready_media(preview):
    _, manager, source = preview
    def media(command, cancel_check, **kwargs):
        if command[0] == 'ffprobe':
            return json.dumps({'format': {'duration': '0.5'}}).encode()
        Path(command[-1]).write_bytes(b'nonempty conversion output')
        # Model the narrow race after the subprocess's own cancellation check
        # but before the preview manager commits READY.
        cancel_check.__self__.set()
        return b''
    with patch('core.media_preview.run_media', media):
        job = manager.start(source, coverage_seconds=.2)
        manager._jobs[job['preview_id']]['future'].result(timeout=3)
    status = manager.status(job['preview_id'])
    assert status['status'] == 'CANCELLED' and status['video_url'] is None
    with pytest.raises(KeyError):
        manager.media(job['preview_id'])


def test_queued_conversions_enforce_retention_after_they_finish(preview):
    _, manager, source = preview
    release = threading.Event()
    def media(command, cancel_check, **kwargs):
        if command[0] == 'ffprobe':
            assert release.wait(3)
            return json.dumps({'format': {'duration': '0.5'}}).encode()
        Path(command[-1]).write_bytes(b'owned temporary output')
        return b''
    with patch('core.media_preview.run_media', media), patch('core.media_preview.MAX_RETAINED_JOBS', 2):
        submitted = [manager.start(source, coverage_seconds=seconds) for seconds in (.2, .3, .4)]
        futures = [manager._jobs[job['preview_id']]['future'] for job in submitted]
        assert len(manager._jobs) == 3  # All queued/inflight jobs retain ownership.
        release.set()
        for future in futures:
            future.result(timeout=3)
        assert len(manager._jobs) == len(list(manager._root.glob('*.webm'))) == 2
