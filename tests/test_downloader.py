"""Real local HTTP download/cancellation; no platform credentials or Internet."""
import functools
import http.server
import io
import json
import logging
import queue
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import settings
from core.downloader import VideoDownloader, VideoDownloadError, friendly_download_error
from core.download_worker import progress_payload, single_video_downloader


def save_valid_partial(prefix, data=b'partial'):
    import hashlib
    from core.download_worker import _save_checkpoint
    partial = prefix.with_suffix('.mp4.part')
    partial.write_bytes(data)
    _save_checkpoint(prefix.with_suffix('.resume.json'), partial,
                     video_id='7688978448627473651', format_identity='a' * 64,
                     total=100000, downloaded=len(data),
                     validators={'etag': '"fixture"', 'last_modified': None}, digest=hashlib.sha256(data))


@pytest.fixture
def owned_video(tmp_path):
    output = tmp_path / ('video_' + 'a' * 32 + '.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=32x32:d=0.2',
                    '-c:v', 'libx264', '-y', str(output)], check=True, capture_output=True, timeout=15)
    return output


@pytest.mark.parametrize('text,expected', [
    ('https://v.douyin.com/_lAiSDH0bK8/', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('3.14 复制打开抖音 https://v.douyin.com/_lAiSDH0bK8/。 分享', 'https://v.douyin.com/_lAiSDH0bK8/'),
    (r'3.53 复制打开抖音，看看【斩漫的作品】《千金垂爱》被千金拉着结婚了 # ai动漫 # a... [**https://v.douyin.com/\_lAiSDH0bK8/**](https://v.douyin.com/_lAiSDH0bK8/) Rxf:/ 01/17 J@V.Lw :1pm', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('3.53 复制打开抖音，看看【斩漫的作品】《千金垂爱》被千金拉着结婚了 # ai动漫 # a... https://v.douyin.com/_lAiSDH0bK8/ Rxf:/ 01/17 J@V.Lw :1pm', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('复制打开抖音\nhttps://v.douyin.com/_lAiSDH0bK8/\nRxf:/ 01/17', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('[https://example.com/label](https://v.douyin.com/_lAiSDH0bK8/)分享', 'https://v.douyin.com/_lAiSDH0bK8/'),
    (r'**https://v.douyin.com/\_lAiSDH0bK8/**', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('__https://v.douyin.com/_lAiSDH0bK8/__', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('链接 `https://v.douyin.com/_lAiSDH0bK8/` 分享', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('<https://v.douyin.com/_lAiSDH0bK8/>', 'https://v.douyin.com/_lAiSDH0bK8/'),
    ('v.douyin.com/example/', 'https://v.douyin.com/example/'),
    ('https://example.com/clip.mp4?token=abc#anchor', 'https://example.com/clip.mp4?token=abc'),
    ('https://example.com/clip.mp4?token=A%2FB%2Bz%3D&expires=123&name=a_b*', 'https://example.com/clip.mp4?token=A%2FB%2Bz%3D&expires=123&name=a_b*'),
    ('https://example.com/clip.mp4?token=**https://nested.example/path**', 'https://example.com/clip.mp4?token=**https://nested.example/path**'),
])
def test_share_text_url_normalization(text, expected):
    assert VideoDownloader.normalize_url(text) == expected


@pytest.mark.parametrize('text', [
    '', 'text with no URL', 'file:///C:/user.mp4',
    'https://user:password@example.com/clip',
    '[https://v.douyin.com/example/](https://user:password@example.com/clip)',
    'https://example.com:99999/clip', 'https://example.com:invalid/clip',
])
def test_invalid_urls_are_rejected(text):
    with pytest.raises(ValueError):
        VideoDownloader.normalize_url(text)


def test_progress_never_invents_percent_from_estimate():
    assert progress_payload({'downloaded_bytes': 25, 'total_bytes': 100})['progress_pct'] == 25
    assert progress_payload({'downloaded_bytes': 25, 'total_bytes_estimate': 100})['progress_pct'] is None
    assert progress_payload({'status': 'finished'})['phase'] == 'prepare'
    message = friendly_download_error('HTTPSConnection at 0x000: connect timeout=20', 'www.douyin.com')
    assert 'Không kết nối được www.douyin.com' in message and '0x000' not in message


@pytest.mark.parametrize('host', ['douyin.com', 'v.douyin.com', 'www.iesdouyin.com'])
def test_douyin_login_error_explains_studio_session_without_echoing_secrets(host):
    message = friendly_download_error('403 fresh cookies: sessionid=private-session; api_key=private-key', host)
    assert 'Đăng nhập Chrome không tự chuyển phiên sang Studio' in message
    assert 'Cài đặt' in message and 'tệp video trên máy' in message
    assert 'private-session' not in message and 'private-key' not in message


@pytest.mark.parametrize('host', ['www.tiktok.com', 'douyin.com.example.com', 'notdouyin.com'])
def test_other_hosts_do_not_suggest_douyin_session_settings(host):
    message = friendly_download_error('403 sessionid=private-session', host)
    assert 'cookie đã nhập' not in message and 'Douyin' not in message
    assert 'private-session' not in message


@pytest.mark.parametrize('changed_field', ['stage', 'source', None])
def test_resolution_fallback_resets_idle_timeout_but_repeated_heartbeat_does_not(tmp_path, monkeypatch, changed_field):
    from core import downloader as module

    clock = SimpleNamespace(now=0)
    initial = {'kind': 'progress', 'phase': 'resolve', 'stage': 'Đang lấy video', 'source': 'api'}
    fallback = dict(initial)
    if changed_field:
        fallback[changed_field] = 'direct'

    class TimedQueue(queue.Queue):
        def __init__(self):
            super().__init__()
            self.times = iter([0, 50, 100, 100])

        def get(self, *args, **kwargs):
            event = super().get(*args, **kwargs)
            clock.now = next(self.times, 100)
            return event

    def start_worker(arguments, **kwargs):
        output = Path(arguments[-1] + '.mp4')
        output.write_bytes(b'completed fixture')
        # The fallback begins 50 seconds after API resolution; a heartbeat at
        # 100 seconds must not make either route look more active than it is.
        events = [initial, fallback, fallback,
                  {'kind': 'result', 'result': {'file_path': str(output)}}]
        return SimpleNamespace(stdout=io.StringIO('\n'.join(json.dumps(event) for event in events)), poll=lambda: 0)

    monkeypatch.setattr(module, 'queue', SimpleNamespace(Queue=TimedQueue, Empty=queue.Empty))
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(module.subprocess, 'Popen', start_worker)
    def register_completed(self, url, result):
        if not changed_field:
            raise VideoDownloadError('fixture is not a validated video')
        return result
    monkeypatch.setattr(VideoDownloader, 'register_completed', register_completed)
    downloader = VideoDownloader(tmp_path)
    if changed_field:
        result = downloader.download('https://v.douyin.com/test/')
        assert Path(result['file_path']).read_bytes() == b'completed fixture'
    else:
        with pytest.raises(VideoDownloadError, match='Không kết nối được'):
            downloader.download('https://v.douyin.com/test/')
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('playlist_type', ['playlist', 'multi_video'])
def test_playlist_is_rejected_before_enumerating_or_downloading(playlist_type, monkeypatch):
    import yt_dlp
    transfers = []
    monkeypatch.setattr(yt_dlp.YoutubeDL, 'process_info', lambda self, info: transfers.append(info))

    def entries():
        raise AssertionError('Playlist entries must not be read')
        yield

    with single_video_downloader({'quiet': True, 'no_warnings': True}) as downloader:
        with pytest.raises(ValueError, match='unsupported url: playlist'):
            downloader.process_ie_result({'_type': playlist_type, 'id': 'fixture', 'entries': entries()}, download=True)
    assert not transfers


def test_playlist_reached_through_redirect_is_rejected_before_transfer(monkeypatch):
    import yt_dlp
    from yt_dlp.extractor.common import InfoExtractor

    class FixturePlaylistIE(InfoExtractor):
        _VALID_URL = r'fixture:playlist'

        def _real_extract(self, url):
            return {'_type': 'playlist', 'id': 'fixture', 'entries': []}

    transfers = []
    monkeypatch.setattr(yt_dlp.YoutubeDL, 'process_info', lambda self, info: transfers.append(info))
    with single_video_downloader({'quiet': True, 'no_warnings': True}) as downloader:
        downloader.add_info_extractor(FixturePlaylistIE())
        with pytest.raises(ValueError, match='unsupported url: playlist'):
            downloader.process_ie_result({'_type': 'url', 'url': 'fixture:playlist', 'ie_key': 'FixturePlaylist'}, download=True)
    assert not transfers


def test_real_http_worker_download_and_cancel_without_leftovers():
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='url-download-', dir=settings.TEMP_DIR) as temporary:
        root = Path(temporary)
        video = root / 'sample.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=32x32:d=0.2',
                        '-c:v', 'libx264', '-y', str(video)], check=True, capture_output=True, timeout=15)
        release = threading.Event()
        entered = threading.Event()

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path == '/slow.mp4':
                    entered.set()
                    release.wait(10)
                    return
                super().do_GET()

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(root)))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        destination = root / 'downloads'
        downloader = VideoDownloader(destination)
        cancel = threading.Event()
        worker = None
        try:
            events = []
            result = downloader.download(f'http://127.0.0.1:{server.server_port}/sample.mp4', events.append)
            assert Path(result['file_path']).read_bytes() == video.read_bytes()
            assert any(event.get('phase') == 'download' and event['progress_pct'] is not None for event in events)
            assert result['owned_prefix'] and result['owned_paths'] == [result['file_path']]
            existing = set(destination.iterdir())
            errors = []

            def slow_download():
                try:
                    downloader.download(f'http://127.0.0.1:{server.server_port}/slow.mp4', cancel_check=cancel.is_set)
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=slow_download)
            worker.start()
            # Interpreter/yt-dlp startup may compete with real local ASR; the
            # cancellation deadline below begins only after the request exists.
            assert entered.wait(30), f'worker did not reach server: {errors!r}'
            started = time.monotonic()
            cancel.set()
            worker.join(timeout=5)
            assert not worker.is_alive(), 'network worker did not stop'
            assert time.monotonic() - started < 5
            assert len(errors) == 1 and isinstance(errors[0], VideoDownloadError)
            assert set(destination.iterdir()) == existing
        finally:
            cancel.set()
            release.set()
            if worker is not None:
                worker.join(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_resume_index_reuses_owned_partial_then_cleans_index_on_success(tmp_path, monkeypatch):
    from core import downloader as module
    prefixes = []

    def worker(arguments, **kwargs):
        prefix = arguments[-1]
        prefixes.append(prefix)
        if len(prefixes) == 1:
            Path(prefix + '.mp4.part').write_bytes(b'partial')
            save_valid_partial(Path(prefix))
            event = {'kind': 'error', 'message': 'Retry later', 'resumable': True}
        else:
            assert Path(prefix + '.mp4.part').read_bytes() == b'partial'
            Path(prefix + '.mp4.part').replace(prefix + '.mp4')
            Path(prefix + '.resume.json').unlink()
            event = {'kind': 'result', 'result': {'file_path': prefix + '.mp4'}}
        return SimpleNamespace(stdout=io.StringIO(json.dumps(event)), poll=lambda: 0)

    monkeypatch.setattr(module.subprocess, 'Popen', worker)
    monkeypatch.setattr(VideoDownloader, 'register_completed', lambda self, url, result: result)
    downloader = VideoDownloader(tmp_path)
    with pytest.raises(VideoDownloadError, match='Retry later'):
        downloader.download('https://v.douyin.com/retry/')
    assert len(list(tmp_path.glob('.download-*.json'))) == 1
    result = downloader.download('https://v.douyin.com/retry/')
    assert prefixes[0] == prefixes[1]
    assert set(tmp_path.iterdir()) == {Path(result['file_path'])}


def test_invalid_resume_index_cannot_own_another_file(tmp_path, monkeypatch):
    import hashlib
    from core import downloader as module
    url = 'https://v.douyin.com/retry/'
    unrelated = tmp_path / 'user.mp4'
    unrelated.write_bytes(b'user data')
    index = tmp_path / ('.download-' + hashlib.sha256(url.encode()).hexdigest() + '.json')
    index.write_text(json.dumps({'prefix': '../user'}))
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **kw: SimpleNamespace(
        stdout=io.StringIO(json.dumps({'kind': 'error', 'message': 'failed'})), poll=lambda: 0))
    with pytest.raises(VideoDownloadError):
        VideoDownloader(tmp_path).download(url)
    assert unrelated.read_bytes() == b'user data'


def test_concurrent_same_source_does_not_touch_existing_worker(tmp_path):
    import hashlib
    url = 'https://v.douyin.com/retry/'
    key = (str(tmp_path.resolve()), hashlib.sha256(url.encode()).hexdigest())
    VideoDownloader._active_sources.add(key)
    try:
        with pytest.raises(VideoDownloadError, match='đang được tải'):
            VideoDownloader(tmp_path).download(url)
    finally:
        VideoDownloader._active_sources.discard(key)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('failure', ['eof', 'error', 'timeout'])
def test_resume_candidate_is_not_retained_until_worker_validates_it(tmp_path, monkeypatch, caplog, failure):
    import hashlib
    from core import downloader as module
    url = 'https://v.douyin.com/retry/'
    prefix = tmp_path / ('video_' + 'a' * 32)
    partial, checkpoint = Path(str(prefix) + '.mp4.part'), Path(str(prefix) + '.resume.json')
    partial.write_bytes(b'not yet verified')
    checkpoint.write_text('{}')
    index = tmp_path / ('.download-' + hashlib.sha256(url.encode()).hexdigest() + '.json')
    index.write_text(json.dumps({'prefix': prefix.name}))
    user_file = tmp_path / 'user-video.mp4'
    user_file.write_bytes(b'keep user file')
    clock = SimpleNamespace(now=0)

    def worker(arguments, **kwargs):
        assert arguments[-1] == str(prefix)
        if failure == 'timeout':
            clock.now = 100
        event = {'kind': 'error', 'message': 'worker failed'} if failure == 'error' else None
        return SimpleNamespace(stdout=io.StringIO(json.dumps(event) if event else ''), poll=lambda: 0)

    monkeypatch.setattr(module.subprocess, 'Popen', worker)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    with caplog.at_level(logging.INFO, logger='pipeline'), pytest.raises(VideoDownloadError):
        VideoDownloader(tmp_path).download(url)
    assert set(tmp_path.iterdir()) == {user_file}
    assert 'Đã giữ' not in caplog.text
    assert 'Kiểm tra phần video đã giữ' in caplog.text


def test_diagnostic_log_preserves_safe_status_cause_and_exact_byte_count(tmp_path, monkeypatch, caplog):
    from core import downloader as module
    events = [
        {'kind': 'diagnostic', 'code': 'read_timeout', 'error_type': 'TimeoutError',
         'status_code': 206, 'downloaded_bytes': 87654321, 'attempt': 3},
        {'kind': 'diagnostic', 'code': 'http_error', 'error_type': 'HTTPError',
         'status_code': 503, 'downloaded_bytes': 87654321, 'attempt': 4},
        {'kind': 'diagnostic', 'code': 'https://private-code', 'error_type': 'private-url-token',
         'status_code': 'private-url-token', 'downloaded_bytes': -1, 'attempt': True},
        {'kind': 'error', 'message': 'Retry later'},
    ]
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: SimpleNamespace(
        stdout=io.StringIO('\n'.join(json.dumps(event) for event in events)), poll=lambda: 0))
    with caplog.at_level(logging.WARNING, logger='pipeline'), pytest.raises(VideoDownloadError):
        VideoDownloader(tmp_path).download('https://v.douyin.com/retry/')
    assert 'read_timeout; loại lỗi TimeoutError; HTTP 206; đã nhận 87654321 byte; lần thử 3' in caplog.text
    assert 'http_error; loại lỗi HTTPError; HTTP 503; đã nhận 87654321 byte; lần thử 4' in caplog.text
    assert 'loại lỗi unknown; HTTP unknown; đã nhận unknown byte; lần thử unknown' in caplog.text
    assert 'private-url-token' not in caplog.text and 'private-code' not in caplog.text


def test_download_logs_safe_run_id_mapping(tmp_path, monkeypatch, caplog):
    from core import downloader as module
    event = {'kind': 'error', 'message': 'failed'}
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: SimpleNamespace(
        stdout=io.StringIO(json.dumps(event)), poll=lambda: 0))
    from core.runtime_context import execution_context
    with caplog.at_level(logging.INFO, logger='pipeline'), execution_context('pipeline-run-42'):
        with pytest.raises(VideoDownloadError):
            VideoDownloader(tmp_path).download('https://v.douyin.com/retry/')
    assert 'run_id=pipeline-run-42' in caplog.text


def test_completed_source_survives_new_downloader_and_canonical_alias(tmp_path, owned_video, monkeypatch):
    url = 'https://www.douyin.com/jingxuan?modal_id=7688978448627473651&tracking=ignored'
    VideoDownloader(tmp_path).register_completed(url, {'file_path': str(owned_video), 'title': 'Original'})
    monkeypatch.setattr(VideoDownloader, '_download_remote', lambda *args: pytest.fail('must not download cached source'))
    events = []
    result = VideoDownloader(tmp_path).download('https://www.douyin.com/video/7688978448627473651', events.append)
    assert result['file_path'] == str(owned_video)
    assert result['title'] == 'Original' and result['duration'] > 0
    assert result['reused_source'] and result['reusable_source'] and result['owned_paths'] == []
    assert events[-1]['downloaded_bytes'] == owned_video.stat().st_size
    assert 'không tải lại' in events[-1]['stage']


@pytest.mark.parametrize('damage', ['deleted', 'truncated', 'nonvideo', 'external', 'wrong-id', 'duration', 'oversized'])
def test_invalid_completed_source_cannot_be_reported_as_reused(tmp_path, owned_video, monkeypatch, damage):
    url = 'https://www.douyin.com/video/7688978448627473651'
    downloader = VideoDownloader(tmp_path)
    downloader.register_completed(url, {'file_path': str(owned_video)})
    index = next(tmp_path.glob('.source-*.json'))
    record = json.loads(index.read_text())
    if damage == 'deleted':
        owned_video.unlink()
    elif damage == 'truncated':
        owned_video.write_bytes(owned_video.read_bytes()[:20])
    elif damage == 'nonvideo':
        owned_video.write_bytes(b'provider returned an error')
        record.update(size=owned_video.stat().st_size, mtime_ns=owned_video.stat().st_mtime_ns)
    elif damage == 'external':
        record['file_name'] = '../user.mp4'
    elif damage == 'wrong-id':
        record['source_identity'] = 'douyin:1234567890123456789'
    elif damage == 'duration':
        record['duration'] = 900
    index.write_text(json.dumps(record) if damage != 'oversized' else ' ' * 17000)
    calls = []
    monkeypatch.setattr(downloader, '_download_remote', lambda *args: calls.append(args) or {'new_request': True})
    assert downloader.download(url) == {'new_request': True}
    assert len(calls) == 1


def test_registration_rejects_non_video_and_external_files(tmp_path, owned_video):
    from core.download_worker import MediaDownloadError
    url = 'https://www.douyin.com/video/7688978448627473651'
    downloader = VideoDownloader(tmp_path / 'other')
    with pytest.raises(VideoDownloadError):
        downloader.register_completed(url, {'file_path': str(owned_video)})
    owned_video.write_bytes(b'not a video')
    with pytest.raises(MediaDownloadError):
        VideoDownloader(tmp_path).register_completed(url, {'file_path': str(owned_video)})
    assert not list(tmp_path.glob('.source-*.json'))


@pytest.mark.parametrize('failure', ['cancel', 'eof', 'error'])
def test_interruption_retains_valid_checkpoint_and_index(tmp_path, monkeypatch, failure):
    from core import downloader as module
    prefix_seen = []
    cancel = threading.Event()

    def worker(arguments, **kwargs):
        prefix = Path(arguments[-1])
        prefix_seen.append(prefix)
        # An app crash must already have a durable pointer to its partial file.
        index = next(tmp_path.glob('.download-*.json'))
        assert json.loads(index.read_text())['prefix'] == prefix.name
        save_valid_partial(prefix)
        if failure == 'cancel':
            cancel.set()
        event = {'kind': 'error', 'message': 'Interrupted'} if failure == 'error' else None
        return SimpleNamespace(stdout=io.StringIO(json.dumps(event) if event else ''), poll=lambda: 0)

    monkeypatch.setattr(module.subprocess, 'Popen', worker)
    with pytest.raises(VideoDownloadError):
        VideoDownloader(tmp_path).download('https://www.douyin.com/video/7688978448627473651',
                                           cancel_check=cancel.is_set)
    assert prefix_seen[0].with_suffix('.mp4.part').read_bytes() == b'partial'
    assert prefix_seen[0].with_suffix('.resume.json').is_file()
    assert len(list(tmp_path.glob('.download-*.json'))) == 1


def test_douyin_alias_concurrency_uses_same_source_lock(tmp_path):
    url = 'https://www.douyin.com/video/7688978448627473651'
    key = (str(tmp_path.resolve()), VideoDownloader.source_hash(url))
    VideoDownloader._active_sources.add(key)
    try:
        with pytest.raises(VideoDownloadError, match='đang được tải'):
            VideoDownloader(tmp_path).download('https://www.douyin.com/jingxuan?modal_id=7688978448627473651')
    finally:
        VideoDownloader._active_sources.discard(key)


def test_legacy_url_checkpoint_is_migrated_before_worker_start(tmp_path, monkeypatch):
    import hashlib
    from core import downloader as module
    url = 'https://www.douyin.com/jingxuan?modal_id=7688978448627473651'
    prefix = tmp_path / ('video_' + 'a' * 32)
    save_valid_partial(prefix)
    legacy = tmp_path / ('.download-' + hashlib.sha256(url.encode()).hexdigest() + '.json')
    legacy.write_text(json.dumps({'prefix': prefix.name}))

    def worker(arguments, **kwargs):
        assert arguments[-1] == str(prefix)
        assert not legacy.exists()
        assert (tmp_path / ('.download-' + VideoDownloader.source_hash(url) + '.json')).is_file()
        return SimpleNamespace(stdout=io.StringIO(''), poll=lambda: 0)

    monkeypatch.setattr(module.subprocess, 'Popen', worker)
    with pytest.raises(VideoDownloadError):
        VideoDownloader(tmp_path).download(url)
    assert prefix.with_suffix('.mp4.part').read_bytes() == b'partial'


def test_stop_racing_final_worker_message_keeps_completed_video(tmp_path, owned_video, monkeypatch):
    from core import downloader as module
    url = 'https://www.douyin.com/video/7688978448627473651'
    video_bytes = owned_video.read_bytes()
    cancel = threading.Event()
    prefixes = []
    real_popen = module.subprocess.Popen

    def worker(arguments, **kwargs):
        if 'download_worker.py' not in ' '.join(map(str, arguments)):
            return real_popen(arguments, **kwargs)
        prefix = arguments[-1]
        prefixes.append(prefix)
        Path(prefix + '.mp4').write_bytes(video_bytes)
        cancel.set()
        return SimpleNamespace(stdout=io.StringIO(''), poll=lambda: 0)

    monkeypatch.setattr(module.subprocess, 'Popen', worker)
    with pytest.raises(VideoDownloadError, match='Đã hủy'):
        VideoDownloader(tmp_path).download(url, cancel_check=cancel.is_set)
    cancel.clear()
    result = VideoDownloader(tmp_path).download(url, cancel_check=cancel.is_set)
    assert result['reused_source'] and result['file_path'] == prefixes[0] + '.mp4'
    assert len(prefixes) == 1


def test_hard_exit_after_final_rename_recovers_completed_source_without_worker(tmp_path, owned_video, monkeypatch):
    from core import downloader as module
    url = 'https://www.douyin.com/video/7688978448627473651'
    index = tmp_path / ('.download-' + VideoDownloader.source_hash(url) + '.json')
    index.write_text(json.dumps({'prefix': owned_video.stem}))
    real_popen = module.subprocess.Popen

    def no_download(arguments, **kwargs):
        assert 'download_worker.py' not in ' '.join(map(str, arguments))
        return real_popen(arguments, **kwargs)

    monkeypatch.setattr(module.subprocess, 'Popen', no_download)
    events = []
    result = VideoDownloader(tmp_path).download(url, events.append)
    assert result['reused_source'] and result['file_path'] == str(owned_video)
    assert not index.exists()
    assert len(list(tmp_path.glob('.source-*.json'))) == 1
    assert events[-1]['downloaded_bytes'] == owned_video.stat().st_size


def test_completed_source_is_not_owned_for_deletion_when_index_write_fails(tmp_path, owned_video, monkeypatch):
    downloader = VideoDownloader(tmp_path)
    monkeypatch.setattr(downloader, '_write_index', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    result = {'file_path': str(owned_video), 'owned_paths': [str(owned_video)]}
    with pytest.raises(OSError):
        downloader.register_completed('https://www.douyin.com/video/7688978448627473651', result)
    assert result['reusable_source'] and result['owned_paths'] == []


@pytest.mark.parametrize('finish_before_stop', [False, True], ids=['partial-range-resume', 'completed-before-stop'])
def test_real_worker_stop_and_fresh_downloader_resumes_http_range(tmp_path, owned_video, monkeypatch, finish_before_stop):
    """Exercise process termination, buffered writes and reload against real HTTP."""
    from core import downloader as module
    video_bytes = owned_video.read_bytes() + b'\0' * (3 * 1024 * 1024)
    requests = []
    release_first_response = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            range_header = self.headers.get('Range')
            requests.append(range_header)
            offset = int(range_header.removeprefix('bytes=').removesuffix('-')) if range_header else 0
            self.send_response(206 if range_header else 200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Length', str(len(video_bytes) - offset))
            self.send_header('ETag', '"stable-local-entity"')
            if range_header:
                self.send_header('Content-Range', f'bytes {offset}-{len(video_bytes)-1}/{len(video_bytes)}')
            self.end_headers()
            try:
                for start in range(offset, len(video_bytes), 65536):
                    self.wfile.write(video_bytes[start:start + 65536])
                    self.wfile.flush()
                    if not range_header and not finish_before_stop and start >= 1024 * 1024:
                        # Do not let a tiny fixture finish while taskkill starts
                        # under ASR/system load. The test must actually stop an
                        # incomplete response to establish Range-resume proof.
                        release_first_response.wait(30)
                    time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    worker_script = tmp_path / 'local_worker.py'
    worker_script.write_text(
        'import sys, requests\n'
        f'sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n'
        'from core import download_worker as worker\n'
        'worker.resolve_douyin = lambda *args: {"id":"7688978448627473651", "duration":0.2, '
        '"formats":[{"original":True,"width":32,"height":32,"bitrate":1000,"codec":"h264",'
        f'"size":{len(video_bytes)},"urls":["http://127.0.0.1:{server.server_port}/video.mp4"]' + '}]}\n'
        'class Response:\n'
        ' def __init__(self, response):\n'
        '  self.response=response; self.headers=response.headers; self.status=response.status_code\n'
        ' def iter_content(self, size): return self.response.iter_content(size)\n'
        ' def close(self): self.response.close()\n'
        'worker.open_public_media=lambda url, headers=None: Response(requests.get(url, headers=headers, stream=True, timeout=5))\n'
        'raise SystemExit(worker.main())\n', encoding='utf-8')
    real_popen = module.subprocess.Popen

    def local_worker(arguments, **kwargs):
        arguments = list(arguments)
        if any(str(arg).endswith('download_worker.py') for arg in arguments):
            arguments = [str(worker_script) if str(arg).endswith('download_worker.py') else arg for arg in arguments]
        return real_popen(arguments, **kwargs)

    monkeypatch.setattr(module.subprocess, 'Popen', local_worker)
    if finish_before_stop:
        real_stop = VideoDownloader._stop_worker

        def delayed_stop(process):
            # Reproduce the legitimate race seen in the full suite: the parent
            # cancels while the real child finishes HTTP, validates and renames.
            # A final MP4 must be retained, not required to leave a checkpoint.
            deadline = time.monotonic() + 10
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(.025)
            assert process.poll() is not None, 'real worker did not finish during stop race'
            real_stop(process)

        monkeypatch.setattr(VideoDownloader, '_stop_worker', staticmethod(delayed_stop))
    destination = tmp_path / 'downloads'
    cancel = threading.Event()

    def progress(event):
        if event.get('downloaded_bytes', 0) >= 512 * 1024:
            cancel.set()

    try:
        with pytest.raises(VideoDownloadError, match='Đã hủy'):
            VideoDownloader(destination).download('https://www.douyin.com/video/7688978448627473651',
                                                  progress, cancel.is_set)
        if finish_before_stop:
            assert not list(destination.glob('*.resume.json'))
            assert not list(destination.glob('*.mp4.part'))
            assert len(list(destination.glob('.source-*.json'))) == 1
            count = None
        else:
            checkpoint = json.loads(next(destination.glob('*.resume.json')).read_text())
            count = checkpoint['downloaded_bytes']
            assert 0 < count < len(video_bytes)
            partial = next(destination.glob('*.mp4.part'))
            assert partial.stat().st_size == count
            assert partial.read_bytes() == video_bytes[:count]
        result = VideoDownloader(destination).download('https://www.douyin.com/jingxuan?modal_id=7688978448627473651')
        assert Path(result['file_path']).read_bytes() == video_bytes
        if finish_before_stop:
            assert result['reused_source']
            assert requests == [None]
        else:
            assert requests == [None, f'bytes={count}-']
    finally:
        release_first_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
