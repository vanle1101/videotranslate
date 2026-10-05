from pathlib import Path
from typing import List, Dict, Any
from config import settings


def _ass_text(text: str) -> str:
    """Keep user text literal; only actual line breaks become ASS controls."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    # Keep deliberate line breaks. Wrap a single long sentence only when the
    # editor has not already supplied its own lines.
    if len(lines) == 1:
        words = text.split()
        if len(words) > 7:
            middle = len(words) // 2
            lines = [" ".join(words[:middle]), " ".join(words[middle:])]
    # libass does not treat a doubled backslash as an escape: \\N would
    # still introduce a line break. A zero-width word joiner after user-supplied
    # backslashes keeps their visible glyph while preventing ASS escape parsing.
    return "\\N".join(line.replace("\\", "\\\u2060").replace("{", "\\{").replace("}", "\\}")
                       for line in lines)


class SubtitleGenerator:
    def __init__(self):
        self.font = settings.SUBTITLE_FONT
        self.primary_color = settings.SUBTITLE_PRIMARY_COLOR # Yellow
        self.outline_color = settings.SUBTITLE_OUTLINE_COLOR # Black

    def _format_time_srt(self, seconds: float) -> str:
        total_millis = max(0, round(seconds * 1000))
        total_seconds, millis = divmod(total_millis, 1000)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _format_time_ass(self, seconds: float) -> str:
        total_centis = max(0, round(seconds * 100))
        total_seconds, centis = divmod(total_centis, 100)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:01d}:{minutes:02d}:{secs:02d}.{centis:02d}"

    def generate_srt(self, segments: List[Dict[str, Any]], output_path: Path) -> Path:
        """Export clean standard SRT file."""
        lines = []
        for seg in segments:
            text = seg.get("vi_text", seg.get("text", "")).strip()
            if not text:
                continue
            start_str = self._format_time_srt(seg["start"])
            end_str = self._format_time_srt(seg["end"])
            lines.append(f"{len(lines) + 1}\n{start_str} --> {end_str}\n{text}\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    def generate_ass(self, segments: List[Dict[str, Any]], output_path: Path) -> Path:
        """
        Export TikTok-optimized ASS subtitle file.
        Features:
        - 1080x1920 layout
        - High-contrast yellow text with thick black outline
        - Safe bottom margin above TikTok UI buttons
        """
        ass_header = f"""[Script Info]
Title: TikTok Vietnamese Dub
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTokStyle,Arial,58,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,7,2,2,60,60,380,1
Style: TikTokWhite,Arial,56,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,6,2,2,60,60,380,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        event_lines = []
        for seg in segments:
            text = seg.get("vi_text", seg.get("text", "")).strip()
            if not text:
                continue
            
            text = _ass_text(text)

            start_ass = self._format_time_ass(seg["start"])
            end_ass = self._format_time_ass(seg["end"])

            # Use yellow style
            event_lines.append(f"Dialogue: 0,{start_ass},{end_ass},TikTokStyle,,0,0,0,,{text}")

        full_ass = ass_header + "\n".join(event_lines)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(full_ass, encoding="utf-8")
        return output_path
