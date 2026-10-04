import subprocess
from pathlib import Path
from config import settings


def _escape_filter_filename(path: Path) -> str:
    """Escape both the filter option parser and the filtergraph parser.

    Shell quoting does not apply: subprocess receives an argument list. Windows
    drive colons and apostrophes still need these two FFmpeg escaping layers.
    """
    value = path.resolve().as_posix()
    value = "".join("\\" + char if char in "\\': " else char for char in value)
    return "".join("\\" + char if char in "\\'[],; " else char for char in value)


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

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sub_escaped = _escape_filter_filename(subtitle_path)

        # Video filter construction
        if mask_chinese_sub:
            # 1. Split video into base and blur crop
            # 2. Crop area around 72% - 86% of vertical height where Douyin subtitles live
            # 3. Apply strong boxblur to frosted-glass the Chinese text
            # 4. Overlay blurred strip back and burn Vietnamese ASS on top
            vf = (
                f"split=2[base][sub_area];"
                f"[sub_area]crop=iw:ih*0.14:0:ih*0.72,gblur=sigma=12[blurred];"
                f"[base][blurred]overlay=0:H*0.72[masked];"
                f"[masked]subtitles=filename={sub_escaped}"
            )
        else:
            vf = f"subtitles=filename={sub_escaped}"

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-filter_complex", f"[0:v]{vf}[outv]",
            "-map", "[outv]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-af", "apad",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path)
        ]

        print(f"[*] Running FFmpeg render...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            err_msg = res.stderr.decode('utf-8', errors='replace').strip()
            # A successful export must contain the requested subtitles. Surface
            # the actual failure instead of silently producing an incomplete clip.
            raise RuntimeError(f"FFmpeg video composition failed: {err_msg}")

        print(f"[+] Final Video Ready: {output_path}")
        return output_path
