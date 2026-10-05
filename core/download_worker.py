"""Private subprocess entry point. Emits JSON progress, never raw yt-dlp output."""
import json
import math
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.downloader import friendly_download_error
from core.douyin_cookies import DouyinCookieError, cookie_policy, is_douyin_url, load_douyin_cookiejar


def progress_payload(data):
    status = data.get("status")
    if status == "finished":
        return {"phase": "prepare", "stage": "Đã tải dữ liệu; đang hoàn tất tệp video…", "progress_pct": None}
    downloaded = max(0, data.get("downloaded_bytes") or 0)
    # Estimated totals can change; only publish % when the server supplies a total.
    total = data.get("total_bytes")
    percent = round(min(100, downloaded * 100 / total), 1) if total and total > 0 else None
    result = {"phase": "download", "stage": "Đang tải video", "progress_pct": percent,
              "downloaded_bytes": downloaded, "total_bytes": total}
    for key in ("speed", "eta"):
        value = data.get(key)
        result[key] = value if isinstance(value, (int, float)) and math.isfinite(value) else None
    return result


def single_video_downloader(options, url=None):
    import yt_dlp

    use_douyin_cookies = is_douyin_url(url) if url else False

    class SingleVideoYoutubeDL(yt_dlp.YoutubeDL):
        def build_request_director(self, handlers, preferences=None):
            if use_douyin_cookies:
                # Requests recreates cookie jars and drops host-only/HTTPS policy.
                # Urllib keeps the same jar through every request and redirect.
                handlers = [handler for handler in handlers if handler.RH_KEY == "Urllib"]
            return super().build_request_director(handlers, preferences)

        def process_ie_result(self, ie_result, download=True, extra_info=None):
            # noplaylist only selects a video when the URL also names a playlist.
            # Reject pure playlists before yt-dlp enumerates or downloads entries,
            # including playlists reached through a short-link/URL redirect.
            if ie_result and ie_result.get("_type") in {"playlist", "multi_video"}:
                raise ValueError("unsupported url: playlist")
            return super().process_ie_result(ie_result, download, extra_info)

    if use_douyin_cookies:
        # Session changes made by yt-dlp belong to this worker only. Never read
        # Chrome or pass the private import file as yt-dlp's persistent cookiefile.
        options = {**options, "cookiefile": None, "cookiesfrombrowser": None}
    downloader = SingleVideoYoutubeDL(options)
    if use_douyin_cookies:
        try:
            imported = load_douyin_cookiejar()
            downloader.cookiejar.set_policy(cookie_policy())
            for cookie in imported:
                downloader.cookiejar.set_cookie(cookie)
        except Exception:
            downloader.close()
            raise
    return downloader


def main():
    url, prefix = sys.argv[1:3]
    last_emit = 0

    def send(data):
        print(json.dumps(data, ensure_ascii=False), flush=True)

    def progress(data):
        nonlocal last_emit
        now = time.monotonic()
        if data.get("status") == "downloading" and now - last_emit < 0.2:
            return
        last_emit = now
        send({"kind": "progress", **progress_payload(data)})

    class QuietLogger:
        def debug(self, *_): pass
        def warning(self, *_): pass
        def error(self, *_): pass

    options = {
        "outtmpl": prefix + ".%(ext)s",
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
        "no_warnings": True, "noprogress": True, "logger": QuietLogger(),
        "socket_timeout": 12, "retries": 1, "extractor_retries": 1,
        "fragment_retries": 1, "file_access_retries": 1,
        "progress_hooks": [progress],
        "postprocessor_hooks": [lambda data: send({"kind": "progress", "phase": "prepare", "stage": "Đang ghép hình và âm thanh…", "progress_pct": None})],
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
    }
    try:
        with single_video_downloader(options, url) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info or info.get("_type") in {"playlist", "multi_video"}:
                raise ValueError("unsupported url")
            output = Path(ydl.prepare_filename(info))
            if not output.exists():
                output = output.with_suffix(".mp4")
            if not output.is_file():
                raise ValueError("No completed video file")
        send({"kind": "result", "result": {"file_path": str(output), "title": info.get("title") or output.stem,
                                             "duration": info.get("duration")}})
    except DouyinCookieError as error:
        send({"kind": "error", "message": str(error)})
        return 1
    except Exception as error:
        send({"kind": "error", "message": friendly_download_error(error, urlsplit(url).hostname)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
