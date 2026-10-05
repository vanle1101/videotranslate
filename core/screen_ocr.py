"""Local, measured screen-text boxes and timing from sampled video frames."""
from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path
from typing import Any

from config import settings
from core.media_process import run_media


class ScreenOCRError(RuntimeError):
    pass


class ScreenOCR:
    FPS = 3
    MAX_SECONDS = 45.0
    MAX_DIMENSION = 960
    MIN_CONFIDENCE = .75

    def __init__(self, engine=None):
        self._engine = engine

    @staticmethod
    def _check_cancelled(cancel_check=None):
        if cancel_check and cancel_check():
            raise ScreenOCRError("Đã hủy nhận dạng chữ trong hình.")

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=1,
                                    text_score=self.MIN_CONFIDENCE, print_verbose=False)
        return self._engine

    def close(self):
        """Release this session's ONNX models after its visual prepass finishes."""
        self._engine = None

    @classmethod
    def detections(cls, result, width: int, height: int) -> list[dict[str, Any]]:
        """Keep confident Chinese text with a tight normalized axis-aligned box."""
        if width <= 0 or height <= 0:
            return []
        rows = []
        for item in result or []:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            points, text, confidence = item[:3]
            if not isinstance(text, str) or not re.search(r"[\u3400-\u9fff]", text):
                continue
            text = re.sub(r"\s+", "", text).strip()
            try:
                confidence = float(confidence)
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
            except (ValueError, TypeError, IndexError):
                continue
            if (not xs or len(xs) != len(ys) or not all(math.isfinite(value) for value in [confidence, *xs, *ys])
                    or confidence < cls.MIN_CONFIDENCE):
                continue
            left, top = max(0.0, min(xs) - 2), max(0.0, min(ys) - 2)
            right, bottom = min(float(width), max(xs) + 2), min(float(height), max(ys) + 2)
            if right <= left or bottom <= top:
                continue
            rows.append({"text_zh": text, "confidence": min(1.0, confidence),
                         "bbox": [left / width, top / height, (right - left) / width, (bottom - top) / height]})
        return rows

    @staticmethod
    def _overlap(a, b):
        left, top = max(a[0], b[0]), max(a[1], b[1])
        right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
        intersection = max(0, right - left) * max(0, bottom - top)
        union = a[2] * a[3] + b[2] * b[3] - intersection
        return intersection / union if union else 0

    @classmethod
    def track_samples(cls, samples: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
        """Merge only adjacent samples with identical text at the same position."""
        active = []
        completed = []
        for frame_index, sample in enumerate(samples):
            timestamp = max(start, min(end, float(sample["time"])))
            next_time = min(end, timestamp + 1 / cls.FPS)
            if timestamp >= end or next_time <= timestamp:
                continue
            used = set()
            current = []
            for detection in sample.get("detections", []):
                candidates = [(cls._overlap(track["_last_bbox"], detection["bbox"]), index, track)
                              for index, track in enumerate(active)
                              if index not in used and track["text_zh"] == detection["text_zh"]
                              and track["_frame"] == frame_index - 1
                              and timestamp - track["_time"] <= 1 / cls.FPS + .001]
                score, index, previous = max(candidates, key=lambda item: item[0], default=(0, -1, None))
                if previous is not None and score >= .5:
                    used.add(index)
                    a, b = previous["bbox"], detection["bbox"]
                    left, top = min(a[0], b[0]), min(a[1], b[1])
                    right, bottom = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
                    track = {**previous, "end": next_time, "bbox": [left, top, right - left, bottom - top],
                             "confidence": min(previous["confidence"], detection["confidence"])}
                else:
                    track = {**detection, "start": timestamp, "end": next_time}
                track.update(_frame=frame_index, _time=timestamp, _last_bbox=detection["bbox"])
                current.append(track)
            completed.extend(track for index, track in enumerate(active) if index not in used)
            active = current
        completed.extend(active)
        completed.sort(key=lambda row: (row["start"], row["bbox"][1], row["bbox"][0]))
        return [{"id": f"o{index}", "start": round(row["start"], 6), "end": round(row["end"], 6),
                 "text_zh": row["text_zh"], "bbox": row["bbox"], "confidence": row["confidence"]}
                for index, row in enumerate(completed)]

    def extract(self, video_path: Path, start: float, end: float, cancel_check=None) -> list[dict[str, Any]]:
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            raise ScreenOCRError("Mốc thời gian OCR không hợp lệ.") from None
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or not 0 < end - start <= self.MAX_SECONDS + .001:
            raise ScreenOCRError("OCR nhận từng đoạn video tối đa 45 giây.")
        self._check_cancelled(cancel_check)
        samples = []
        with tempfile.TemporaryDirectory(prefix="screen_ocr_", dir=settings.TEMP_DIR) as directory:
            pattern = Path(directory) / "frame_%04d.jpg"
            frame_count = max(1, math.ceil((end - start) * self.FPS - 1e-6))
            run_media([
                "ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.6f}",
                "-t", f"{end - start:.6f}", "-i", str(video_path), "-an",
                "-vf", f"fps={self.FPS}:start_time=0:round=up,scale='min({self.MAX_DIMENSION},iw)':'min({self.MAX_DIMENSION},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuvj420p",
                "-frames:v", str(frame_count), "-q:v", "3", str(pattern),
            ], cancel_check)
            self._check_cancelled(cancel_check)
            import cv2
            import numpy as np
            engine = self._get_engine()
            frames = sorted(Path(directory).glob("frame_*.jpg"))
            if not frames:
                raise ScreenOCRError("Không đọc được khung hình để nhận dạng chữ.")
            for index, path in enumerate(frames):
                self._check_cancelled(cancel_check)
                # cv2.imread on Windows cannot reliably open Vietnamese paths.
                frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise ScreenOCRError("Không đọc được khung hình OCR.")
                result, _ = engine(frame)
                self._check_cancelled(cancel_check)
                height, width = frame.shape[:2]
                samples.append({"time": start + index / self.FPS,
                                "detections": self.detections(result, width, height)})
            self._check_cancelled(cancel_check)
        return self.track_samples(samples, start, end)
