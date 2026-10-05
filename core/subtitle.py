from pathlib import Path
from typing import List, Dict, Any
from config import settings
from core.subtitle_cues import build_subtitle_cues, fit_title_text, normalize_screen_texts, uncovered_intervals


def _ass_text(text: str) -> str:
    """Keep user text literal; only actual line breaks become ASS controls."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
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
            text = seg.get("vi_text", seg.get("text", ""))
            for cue in build_subtitle_cues(text, seg["start"], seg["end"]):
                start_str = self._format_time_srt(cue["start"])
                end_str = self._format_time_srt(cue["end"])
                lines.append(f"{len(lines) + 1}\n{start_str} --> {end_str}\n{cue['text']}\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    def generate_ass(self, segments: List[Dict[str, Any]], output_path: Path,
                     screen_texts=None, video_size=None, mask_screen_text=True) -> Path:
        """Render compact speech cues and positioned translations of visible text."""
        width, height = video_size or (1080, 1920)
        width, height = max(16, int(width)), max(16, int(height))
        font_size = max(8, round(min(width * 0.045, height * 0.043)))
        outline = max(1, round(font_size * 0.065, 1))
        margin_x, margin_y = round(width * 0.07), round(height * 0.08)
        screen_texts = normalize_screen_texts(screen_texts)
        ass_header = f"""[Script Info]
Title: TikTok Vietnamese Dub
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTokStyle,{self.font},{font_size},{self.primary_color},&H000000FF,{self.outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,{margin_x},{margin_x},{margin_y},1
Style: ScreenText,{self.font},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        event_lines = []

        def event(layer, start, end, style, text):
            event_lines.append(f"Dialogue: {layer},{self._format_time_ass(start)},"
                               f"{self._format_time_ass(end)},{style},,0,0,0,,{text}")

        for seg in segments:
            text = seg.get("vi_text", seg.get("text", ""))
            for cue in build_subtitle_cues(text, seg["start"], seg["end"]):
                for start, end in uncovered_intervals(cue["start"], cue["end"], screen_texts):
                    # Spoken text sits above source masks, as in the preview.
                    event(2, start, end, "TikTokStyle", _ass_text(cue["text"]))

        for item in screen_texts:
            x, y, w, h = item["bbox"]
            left, top = x * width, y * height
            box_width, box_height = w * width, h * height
            # Mask only the detected source glyph area, never an entire video strip.
            if mask_screen_text:
                rect = (f"{{\\an7\\pos({left:.2f},{top:.2f})\\p1\\bord0\\shad0\\1c&H121212&\\1a&H00&}}"
                        f"m 0 0 l {box_width:.2f} 0 l {box_width:.2f} {box_height:.2f} l 0 {box_height:.2f}{{\\p0}}")
                event(0, item["start"], item["end"], "ScreenText", rect)
            if item.get("mask_only"):
                continue
            # Paginate verbose translations instead of growing a face-sized box.
            text = " ".join(item["text_vi"].split())
            target_size = min(font_size, box_height * .9 / 1.25)
            line_count = min(2, max(1, int(box_height * .9 / (target_size * 1.25))))
            line_chars = max(8, min(32, int(box_width * .92 / max(1, target_size * .62))))
            max_chars = min(60, line_chars * line_count)
            cues = ([{"start": item["start"], "end": item["end"],
                      "text": fit_title_text(text, box_width, box_height, font_size)}]
                    if item["kind"] == "title" else
                    build_subtitle_cues(text, item["start"], item["end"],
                                        max_chars=max_chars, line_chars=line_chars))
            for cue in cues:
                lines = cue["text"].split("\n")
                longest = max(len(line) for line in lines)
                # Conservative glyph width fits accented Vietnamese in the region.
                size = min(font_size, box_width * 0.92 / max(1, longest * 0.62),
                           box_height * 0.90 / (len(lines) * 1.25))
                size = max(1, size)
                position = (f"{{\\an5\\q2\\pos({left + box_width / 2:.2f},"
                            f"{top + box_height / 2:.2f})\\fs{size:.2f}\\bord{max(.5, size * .045):.2f}}}")
                event(1, cue["start"], cue["end"], "ScreenText", position + _ass_text(cue["text"]))

        full_ass = ass_header + "\n".join(event_lines)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(full_ass, encoding="utf-8")
        return output_path
