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
