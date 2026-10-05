"""Real local HTTP download/cancellation; no platform credentials or Internet."""
import functools
import http.server
import io
import json
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
        try:
            events = []
            result = downloader.download(f'http://127.0.0.1:{server.server_port}/sample.mp4', events.append)
            assert Path(result['file_path']).read_bytes() == video.read_bytes()
            assert any(event.get('phase') == 'download' and event['progress_pct'] is not None for event in events)
            assert result['owned_prefix'] and result['owned_paths'] == [result['file_path']]
            existing = set(destination.iterdir())
            errors = []
            cancel = threading.Event()

            def slow_download():
                try:
                    downloader.download(f'http://127.0.0.1:{server.server_port}/slow.mp4', cancel_check=cancel.is_set)
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=slow_download)
            worker.start()
            assert entered.wait(10)
            started = time.monotonic()
            cancel.set()
            worker.join(timeout=5)
            assert not worker.is_alive(), 'network worker did not stop'
            assert time.monotonic() - started < 5
            assert len(errors) == 1 and isinstance(errors[0], VideoDownloadError)
            assert set(destination.iterdir()) == existing
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
