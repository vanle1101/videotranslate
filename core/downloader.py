import os
import re
import uuid
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp
from config import settings

class VideoDownloader:
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or settings.INPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def download(self, url_or_path: str) -> Dict[str, Any]:
        """
        Download video from URL (Douyin, TikTok, Bilibili, YouTube, etc.) or validate local file.
        Returns metadata including downloaded file path.
        """
        # If already a valid local file
        local_path = Path(url_or_path)
        if local_path.is_file() and local_path.suffix.lower() in ['.mp4', '.mkv', '.mov', '.webm', '.avi']:
            return {
                "id": str(uuid.uuid4())[:8],
                "file_path": str(local_path.resolve()),
                "title": local_path.stem,
                "duration": None,
                "is_local": True
            }

        # If it's a URL
        task_id = str(uuid.uuid4())[:8]
        output_template = str(self.output_dir / f"video_{task_id}.%(ext)s")

        ydl_opts = {
            'outtmpl': output_template,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.douyin.com/'
            }
        }

        # Handle clean Douyin URLs (strip share text)
        clean_url = self._extract_clean_url(url_or_path)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            video_title = info.get('title', f"video_{task_id}")
            duration = info.get('duration', 0)
            downloaded_file = ydl.prepare_filename(info)
            # In case it got merged into .mp4
            if not os.path.exists(downloaded_file):
                mp4_file = Path(downloaded_file).with_suffix('.mp4')
                if mp4_file.exists():
                    downloaded_file = str(mp4_file)

        return {
            "id": task_id,
            "file_path": downloaded_file,
            "title": video_title,
            "duration": duration,
            "is_local": False
        }

    def _extract_clean_url(self, text: str) -> str:
        # Match standard URLs inside Douyin share text (e.g. 7.12 复制打开抖音... https://v.douyin.com/xyz/ )
        url_match = re.search(r'(https?://[^\s]+)', text)
        if url_match:
            return url_match.group(1)
        return text.strip()
