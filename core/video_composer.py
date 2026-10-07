import math
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


def _normalize_source_masks(source_masks):
    """Keep only finite source regions, with a bounded FFmpeg graph.

    Boxes are normalized xywh, not a guessed full-width subtitle strip. Merge
    repeated OCR observations at the same position, including touching windows.
    Invalid OCR evidence is omitted rather than accidentally blurring the frame.
    """
    if source_masks is None:
        return []
    if not isinstance(source_masks, (list, tuple)):
        raise ValueError("Vùng làm mờ phụ đề gốc phải là một danh sách.")
    grouped = {}
    for mask in source_masks:
        if not isinstance(mask, dict):
            continue
        bbox = mask.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            start, end = float(mask["start"]), float(mask["end"])
            x, y, width, height = map(float, bbox)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(v) for v in (start, end, x, y, width, height)):
            continue
        start = max(0.0, start)
        if end <= start or width <= 0 or height <= 0:
            continue
        right, bottom = min(1.0, x + width), min(1.0, y + height)
        x, y = max(0.0, x), max(0.0, y)
        if right <= x or bottom <= y:
            continue
        box = tuple(round(v, 8) for v in (x, y, right - x, bottom - y))
        if box[0] >= 1 or box[1] >= 1 or box[2] <= 0 or box[3] <= 0:
            continue
        grouped.setdefault(box, []).append((start, end))
    normalized = []
    for box, windows in grouped.items():
        merged = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        normalized.append({"bbox": box, "windows": merged})
    # A malformed/unbounded OCR plan must not create hundreds of full-frame
    # branches and exhaust memory. Never claim an incompletely masked export.
    if len(normalized) > 128 or sum(len(mask["windows"]) for mask in normalized) > 4096:
        raise ValueError("Có quá nhiều vùng phụ đề gốc để làm mờ an toàn; hãy tắt làm mờ hoặc chia video thành đoạn ngắn hơn.")
    return normalized


def _composition_filter(subtitle_path, source_masks=None):
    """Blur original pixels first, then paint sharp Vietnamese captions."""
    graph = []
    current = "0:v"
    for index, mask in enumerate(_normalize_source_masks(source_masks)):
        x, y, width, height = mask["bbox"]
        # yuv420p needs even crop coordinates and dimensions. A two-pixel
        # minimum also keeps tiny valid observations safe for the blur filter.
        crop_x = f"trunc(iw*{x:.8f}/2)*2"
        crop_y = f"trunc(ih*{y:.8f}/2)*2"
        crop_w = f"min(iw-({crop_x}),max(2,trunc(iw*{width:.8f}/2)*2))"
        crop_h = f"min(ih-({crop_y}),max(2,trunc(ih*{height:.8f}/2)*2))"
        enabled = "+".join(f"gte(t,{start:.8f})*lt(t,{end:.8f})" for start, end in mask["windows"])
        graph.extend([
            f"[{current}]split=2[base{index}][region{index}]",
            f"[region{index}]crop=w='{crop_w}':h='{crop_h}':x='{crop_x}':y='{crop_y}',"
            "boxblur=luma_radius='min(20,min(w,h)/10)':luma_power=2:"
            "chroma_radius='min(10,min(cw,ch)/10)':chroma_power=2"
            f"[blur{index}]",
            f"[base{index}][blur{index}]overlay=x='trunc(main_w*{x:.8f}/2)*2':"
            f"y='trunc(main_h*{y:.8f}/2)*2':enable='{enabled}'[masked{index}]",
        ])
        current = f"masked{index}"
    graph.append(f"[{current}]subtitles=filename={_escape_filter_filename(subtitle_path)}[outv]")
    return ";".join(graph)


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
        source_masks=None,
    ) -> Path:
        """
        Merges video, ducked Vietnamese audio, and burns ASS subtitles.
        Optional observed source regions are blurred before burning ASS captions.
        """
        print(f"[*] Composing final video with FFmpeg...")
        print(f"    Video: {video_path.name}")
        print(f"    Audio: {audio_path.name}")
        print(f"    Subtitle: {subtitle_path.name}")
        print(f"    Mask Chinese Sub: {mask_chinese_sub}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        # The legacy flag is accepted for callers, but never invent a full-width
        # subtitle region. A caller must provide actual time-bound source boxes.
        filters = _composition_filter(subtitle_path, source_masks)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-filter_complex", filters,
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
