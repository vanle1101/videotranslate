import subprocess
from pathlib import Path
from typing import Optional
from config import settings

class VideoComposer:
    def __init__(self):
        self.output_dir = settings.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def compose(
        self,
        video_path: Path,
        audio_path: Path,
        subtitle_path: Path,
        output_path: Path,
        mask_chinese_sub: bool = True
    ) -> Path:
        """
        Merges video, ducked Vietnamese audio, and burns ASS subtitles.
        Optionally applies a frosted-glass blur bar over the Chinese hard subtitle zone.
        """
        print(f"[*] Composing final video with FFmpeg...")
        print(f"    Video: {video_path.name}")
        print(f"    Audio: {audio_path.name}")
        print(f"    Subtitle: {subtitle_path.name}")
        print(f"    Mask Chinese Sub: {mask_chinese_sub}")

        # Escape path for FFmpeg subtitles filter
        # Windows requires escaping backslashes and colons (e.g. C\\:/path/to/file.ass)
        sub_escaped = str(subtitle_path.resolve()).replace('\\', '/').replace(':', '\\:')

        # Video filter construction
        if mask_chinese_sub:
            # 1. Split video into base and blur crop
            # 2. Crop area around 72% - 86% of vertical height where Douyin subtitles live
            # 3. Apply strong boxblur to frosted-glass the Chinese text
            # 4. Overlay blurred strip back and burn Vietnamese ASS on top
            vf = (
                f"split=2[base][sub_area];"
                f"[sub_area]crop=iw:ih*0.14:0:ih*0.72,boxblur=15:1[blurred];"
                f"[base][blurred]overlay=0:H*0.72[masked];"
                f"[masked]subtitles='{sub_escaped}'"
            )
        else:
            vf = f"subtitles='{sub_escaped}'"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-filter_complex", f"[0:v]{vf}[outv]",
            "-map", "[outv]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_path)
        ]

        print(f"[*] Running FFmpeg render...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            err_msg = res.stderr.decode('utf-8', errors='ignore')
            print(f"[!] Warning: Advanced filter failed ({err_msg[:200]}), falling back to direct stream mix...")
            # Fallback without subtitle filter if libass has path issue
            cmd_fallback = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-i", str(audio_path),
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                str(output_path)
            ]
            subprocess.run(cmd_fallback, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        print(f"[+] Final Video Ready: {output_path}")
        return output_path
