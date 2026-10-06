from pathlib import Path
from config import settings
from core.media_process import run_media


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
        mask_chinese_sub: bool = False,
        cancel_check=None,
    ) -> Path:
        """
        Merges video, ducked Vietnamese audio, and burns ASS subtitles.
        Keeps source pixels intact; caption geometry is supplied by ASS.
        """
        print(f"[*] Composing final video with FFmpeg...")
        print(f"    Video: {video_path.name}")
        print(f"    Audio: {audio_path.name}")
        print(f"    Subtitle: {subtitle_path.name}")
        print(f"    Mask Chinese Sub: {mask_chinese_sub}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        sub_escaped = _escape_filter_filename(subtitle_path)

        # The legacy flag is accepted for callers, but never invent a full-width
        # subtitle region. Automatic captions deliberately retain Chinese text.
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
        try:
            run_media(cmd, cancel_check=cancel_check)
        except RuntimeError as exc:
            # A successful export must contain the requested subtitles. Surface
            # the actual failure instead of silently producing an incomplete clip.
            raise RuntimeError(f"FFmpeg video composition failed: {exc}") from exc

        print(f"[+] Final Video Ready: {output_path}")
        return output_path
