"""Readable subtitle pages with estimated timing, never word-level timestamps."""
import math
import re


def _wrap_words(text, line_chars):
    words = text.split()
    if len(text) <= line_chars or len(words) < 2:
        return text
    # Prefer two balanced lines; do not split a word or delete punctuation.
    options = [(abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))), i)
               for i in range(1, len(words))]
    split = min(options)[1]
    return " ".join(words[:split]) + "\n" + " ".join(words[split:])


def fit_title_text(text, box_width, box_height, font_size):
    """Keep a title complete for its entire interval, choosing one or two lines."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    candidates = [text]
    if len(text.split()) > 1:
        candidates.append(_wrap_words(text, 0))

    def fit(candidate):
        lines = candidate.split("\n")
        size = min(font_size, box_width * .92 / max(1, max(map(len, lines)) * .62),
                   box_height * .90 / (len(lines) * 1.25))
        return size, -len(lines)

    return max(candidates, key=fit)


def build_subtitle_cues(text, start, end, *, max_chars=60, line_chars=32):
    """Split long speech into small pages, with proportional estimated timing.

    Manual line breaks are retained and paged in pairs for edited subtitles.
    Automatic pages use at most two lines, prefer punctuation near the end,
    and retain every word.
    This is display pagination, not forced alignment or measured word timing.
    """
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(start) or not math.isfinite(end) or end <= max(0, start):
        return []
    start = max(0.0, start)
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    if "\n" in text:
        lines = text.split("\n")
        pages = ["\n".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]
        weight = sum(max(1, len(page)) for page in pages)
        elapsed, cues = 0, []
        for index, page in enumerate(pages):
            cue_start = start + (end - start) * elapsed / weight
            elapsed += max(1, len(page))
            cue_end = end if index == len(pages) - 1 else start + (end - start) * elapsed / weight
            cues.append({"start": cue_start, "end": cue_end, "text": page})
        return cues

    words = text.split()
    pages = []
    while words:
        count, size = 0, 0
        for word in words:
            proposed = size + (1 if count else 0) + len(word)
            if count and proposed > max_chars:
                break
            size = proposed
            count += 1
        # Prefer a sentence/clause boundary in the last half of the page.
        if count < len(words):
            prefix_size = 0
            candidates = []
            for i, word in enumerate(words[:count]):
                prefix_size += len(word) + (1 if i else 0)
                if prefix_size >= max_chars * 0.5 and re.search(r"[.!?;:,。！？；，][\"'”’)]?$", word):
                    candidates.append(i + 1)
            if candidates:
                count = candidates[-1]
        page = " ".join(words[:count])
        pages.append(page)
        words = words[count:]

    # Avoid a final word flashing on its own when the first page nearly fits.
    # Rebalance only the last two pages; keep earlier punctuation boundaries.
    if len(pages) > 1 and (len(pages[-1]) < min(20, max_chars / 2) or len(pages[-1].split()) == 1):
        tail_words = (pages[-2] + " " + pages[-1]).split()
        joined = " ".join(tail_words)
        if len(joined) <= max_chars:
            pages[-2:] = [joined]
        else:
            choices = []
            for i in range(1, len(tail_words)):
                left, right = " ".join(tail_words[:i]), " ".join(tail_words[i:])
                if max(len(left), len(right)) <= max_chars:
                    choices.append((abs(len(left) - len(right)), i, left, right))
            if choices:
                _, _, left, right = min(choices)
                pages[-2:] = [left, right]

    weight = sum(len(page) for page in pages)
    elapsed = 0
    cues = []
    for index, page in enumerate(pages):
        cue_start = start + (end - start) * elapsed / weight
        elapsed += len(page)
        cue_end = end if index == len(pages) - 1 else start + (end - start) * elapsed / weight
        cues.append({"start": cue_start, "end": cue_end,
                     "text": _wrap_words(page, line_chars)})
    return cues


def normalize_screen_texts(items, duration=None):
    """Clamp trusted display metadata; uncertain OCR never covers the source."""
    result = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("needs_review"):
            continue
        mask_only = item.get("mask_only") is True
        text = str(item.get("text_vi") or "").strip()
        box = item.get("bbox")
        if (not text and not mask_only) or not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
            x, y, w, h = map(float, box)
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (start, end, x, y, w, h)) or w <= 0 or h <= 0:
            continue
        # An edited sentence approves its wording, not an uncertain OCR region.
        if mask_only and (h > .35 or w * h > .30 or x < 0 or y < 0 or x + w > 1 or y + h > 1):
            continue
        left, top = max(0.0, x), max(0.0, y)
        right, bottom = min(1.0, x + w), min(1.0, y + h)
        start = max(0.0, start)
        if duration is not None:
            end = min(end, duration)
        if right <= left or bottom <= top or end <= start:
            continue
        result.append({**item, "start": start, "end": end, "text_vi": "" if mask_only else text,
                       "mask_only": mask_only,
                       "kind": "title" if item.get("kind") == "title" else "subtitle",
                       "bbox": [left, top, right - left, bottom - top]})
    return result


def uncovered_intervals(start, end, screen_texts):
    """Avoid duplicate speech subtitles where translated hardsubs are visible."""
    intervals = [(start, end)]
    for item in screen_texts:
        if item["kind"] != "subtitle" or item.get("mask_only"):
            continue
        remaining = []
        for left, right in intervals:
            a, b = max(left, item["start"]), min(right, item["end"])
            if b <= a:
                remaining.append((left, right))
            else:
                if left < a:
                    remaining.append((left, a))
                if b < right:
                    remaining.append((b, right))
        intervals = remaining
    return intervals


def speech_caption_cues(segment):
    """Keep explicit measured utterances; legacy callers retain their old cues."""
    if not isinstance(segment, dict) or segment.get("confirmed_silence"):
        return []
    # Draft captions are useful while listening and editing. Only a synthesized
    # preview may opt in; unreviewed export/legacy inputs remain excluded.
    if segment.get("needs_review") and not segment.get("preview_is_draft"):
        return []
    try:
        start, end = float(segment["start"]), float(segment["end"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= max(0, start):
            return []
    except (KeyError, TypeError, ValueError):
        return []
    if "subtitle_cues" in segment:
        supplied = segment["subtitle_cues"]
        if not isinstance(supplied, list):
            return []
    else:
        text = segment.get("final_vi") or segment.get("vi_text") or segment.get("text_vi") or segment.get("text") or ""
        supplied = build_subtitle_cues(text, start, end)
    bounds = [max(0, start), end]
    for index, name in enumerate(("speech_start", "speech_end")):
        value = segment.get(name)
        if value is not None:
            try:
                value = float(value)
                if not math.isfinite(value):
                    return []
                bounds[index] = max(bounds[index], value) if index == 0 else min(bounds[index], value)
            except (TypeError, ValueError):
                return []
    cues = []
    for item in supplied:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            continue
        try:
            left, right = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(left) or not math.isfinite(right):
            continue
        left, right = max(bounds[0], left), min(bounds[1], right)
        if right > left:
            cues.append({**item, "start": left, "end": right, "text": item["text"].strip()})
    return cues


def _trusted_speech_regions(screen_texts):
    regions = []
    for row in screen_texts or []:
        if (not isinstance(row, dict) or row.get("kind") != "subtitle"
                or (row.get("needs_review") and row.get("source_region_verified") is not True)
                or row.get("source_method") != "local-ocr"):
            continue
        box = row.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            start, end = float(row["start"]), float(row["end"])
            x, y, w, h = map(float, box)
            confidence = float(row.get("confidence", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if (not all(math.isfinite(v) for v in (start, end, x, y, w, h, confidence))
                or end <= max(0, start) or confidence < .90 or x < 0 or y < 0
                or w <= 0 or h <= 0 or h > .15 or w * h > .20 or x + w > 1 or y + h > 1):
            continue
        regions.append({**row, "start": max(0, start), "end": end, "bbox": [x, y, w, h]})
    return regions


def build_caption_layout(segments, screen_texts=None, video_size=None):
    """One normalized caption plan for preview and ASS, preserving source text.

    A trusted Chinese speech region selects a compact yellow caption immediately
    below it (above only when necessary). No-region captions use a small white
    box near the bottom. OCR translations/titles never become spoken captions.
    """
    width, height = video_size or (1080, 1920)
    width, height = max(16, int(width)), max(16, int(height))
    regions = _trusted_speech_regions(screen_texts)
    base_font = max(8, round(min(width * .035, height * .028)))
    margin_x, gap = width * .04, max(2, height * .006)
    output = []
    for index, segment in enumerate(segments or []):
        for cue in speech_caption_cues(segment):
            source_lines = [" ".join(line.split()) for line in cue["text"].splitlines() if line.strip()]
            text = " ".join(source_lines)
            wrapped = "\n".join(source_lines) if 1 < len(source_lines) <= 2 else _wrap_words(text, 32)
            lines = wrapped.split("\n")
            longest = max(map(len, lines))
            font = min(base_font, (width - 2 * margin_x) / max(1, longest * .62 + 1.1))
            pad_x, pad_y = font * .55, font * .20
            box_w = min(width - 2 * margin_x, longest * font * .62 + 2 * pad_x)
            box_h = len(lines) * font * 1.25 + 2 * pad_y
            active = [row for row in regions if row["start"] < cue["end"] and row["end"] > cue["start"]]
            # Keep one stable position throughout an utterance. Sampling gaps
            # must not turn the same line from white to yellow or make it jump.
            target = max(active, key=lambda row: (
                min(row["end"], cue["end"]) - max(row["start"], cue["start"]),
                row["confidence"]), default=None)
            for left, right in [(cue["start"], cue["end"])]:
                placement, background, source_bbox = "bottom", "white", None
                x, y = (width - box_w) / 2, height * .90 - box_h
                if target:
                    rx, ry, rw, rh = target["bbox"]
                    source_bbox = target["bbox"]
                    x = min(width - margin_x - box_w, max(margin_x, (rx + rw / 2) * width - box_w / 2))
                    # Another measured source line may appear lower during this
                    # utterance. Keep one position that clears every intersecting
                    # source box, rather than covering that later line.
                    intersecting = [row["bbox"] for row in active
                                    if row["bbox"][0] * width < x + box_w
                                    and (row["bbox"][0] + row["bbox"][2]) * width > x]
                    lower_edge = max(box[1] + box[3] for box in intersecting)
                    upper_edge = min(box[1] for box in intersecting)
                    y = lower_edge * height + gap
                    placement, background = "below-source", "yellow"
                    if y + box_h > height * .98:
                        y = upper_edge * height - gap - box_h
                        placement = "above-source"
                    if y < height * .02:
                        # Pathologically crowded source: never cover its pixels.
                        x, y = (width - box_w) / 2, height * .90 - box_h
                        placement, background, source_bbox = "bottom", "white", None
                output.append({"segment_id": segment.get("id", index), "start": left, "end": right,
                               "text": wrapped, "placement": placement,
                               "bbox": [x / width, y / height, box_w / width, box_h / height],
                               "source_bbox": source_bbox, "font_size": round(font, 3),
                               "video_size": [width, height],
                               "background": background, "color": "black", "border_radius": round(font * .18, 3)})
    return {"video_size": [width, height], "cues": output}
