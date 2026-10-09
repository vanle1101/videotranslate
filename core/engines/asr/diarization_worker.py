"""One bounded, real speaker diarization; native allocations die with the child.

Only audio identity hypotheses are emitted. Silhouette and cosine embeddings
are diagnostic measurements, never a calibrated probability or character role.
"""
from __future__ import annotations

import contextlib
import json
import math
from pathlib import Path
import sys


def exclusive_spans(interval, others):
    """Subtract every other local voice before extracting a voice embedding."""
    spans = [(interval["start"], interval["end"])]
    for other in others:
        if other["local_speaker"] == interval["local_speaker"]:
            continue
        next_spans = []
        for start, end in spans:
            if other["end"] <= start or other["start"] >= end:
                next_spans.append((start, end))
            else:
                if start < other["start"]:
                    next_spans.append((start, other["start"]))
                if other["end"] < end:
                    next_spans.append((other["end"], end))
        spans = next_spans
    return spans


def collect(request, emit):
    import numpy as np
    import sherpa_onnx
    import soundfile as sf
    from core.media_process import run_media

    start, end = request["start"], request["end"]
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(end)
            or not 0 <= start < end or end - start > 90):
        raise ValueError("Diarization needs a bounded, finite audio interval")
    threads = request["threads"]
    if type(threads) is not int or not 1 <= threads <= 16:
        raise ValueError("Invalid diarization thread count")
    policy = request["policy"]
    wav = Path(request["work_directory"]) / "speaker.wav"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.6f}",
               "-i", request["media_path"], "-t", f"{end - start:.6f}",
               "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], timeout=120)
    info = sf.info(str(wav))
    if (info.samplerate != 16000 or info.channels != 1 or info.frames <= 0
            or info.frames > math.ceil((end - start + .1) * 16000)):
        raise ValueError("Extracted diarization audio exceeds its bounded interval")
    samples, rate = sf.read(str(wav), dtype="float32")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(
        sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=request["segmentation_model"], window_shift_ratio=policy["window_shift_ratio"]),
                num_threads=threads, provider="cpu"),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=request["embedding_model"], num_threads=threads, provider="cpu"),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=-1, threshold=policy["cluster_distance"], compute_confidence=True),
            min_duration_on=policy["min_duration_on"], min_duration_off=policy["min_duration_off"]))
    if diarizer.sample_rate != rate:
        raise ValueError("Diarization model sample rate does not match audio")
    def progress(done, total):
        emit({"event": "progress", "processed": int(done), "total": int(total)})
        return 0
    segments = diarizer.process(samples, callback=progress).sort_by_start_time()
    intervals = []
    for item in segments:
        left, right = max(0., float(item.start)), min(float(item.end), len(samples) / rate)
        if right <= left:
            continue
        score = float(item.confidence)
        if not math.isfinite(score) or score != -2. and not -1 <= score <= 1:
            raise ValueError("Unexpected raw diarization silhouette value")
        silhouette = score if score != -2. else None
        intervals.append({"start": start + left, "end": start + right,
                          "local_speaker": int(item.speaker), "silhouette": silhouette,
                          "raw_silhouette": score,
                          "silhouette_status": "unavailable" if score == -2. else "available"})
    # The result owns plain intervals; a second extractor does not need the
    # segmentation model's live native session or its internal sample arrays.
    del segments, diarizer
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=request["embedding_model"],
                                                   num_threads=threads, provider="cpu"))
    embeddings = []
    for speaker in sorted({row["local_speaker"] for row in intervals}):
        candidates = [span for row in intervals if row["local_speaker"] == speaker
                      for span in exclusive_spans(row, intervals) if span[1] > span[0]]
        candidates.sort(key=lambda span: (-(span[1] - span[0]), span[0]))
        groups, short = [], []
        for left, right in candidates:
            if right - left >= policy["min_embedding_seconds"]:
                groups.append([(left, min(right, left + policy["max_embedding_seconds"]))])
            else:
                short.append((left, right))
        # Dialogue often has only sub-second turns. Accumulate exclusive
        # samples assigned by this real local cluster rather than padding a
        # short turn with another voice, silence or invented audio. These
        # measured spans remain a provisional identity hypothesis.
        selected, seconds = [], 0.
        for left, right in short:
            if seconds >= policy["max_embedding_seconds"]:
                break
            right = min(right, left + policy["max_embedding_seconds"] - seconds)
            selected.append((left, right))
            seconds += right - left
        if seconds >= policy["min_embedding_seconds"]:
            groups.append(sorted(selected))
        for spans in groups[:policy["max_references"]]:
            clip = np.concatenate([samples[max(0, round((left - start) * rate)):min(len(samples), round((right - start) * rate))]
                                   for left, right in spans])
            stream = extractor.create_stream()
            stream.accept_waveform(rate, np.ascontiguousarray(clip))
            stream.input_finished()
            if not extractor.is_ready(stream):
                continue
            vector = np.asarray(extractor.compute(stream), dtype="float64")
            norm = float(np.linalg.norm(vector))
            if (not 16 <= vector.size <= 2048 or not np.all(np.isfinite(vector)) or norm <= 1e-8):
                raise ValueError("Speaker embedding is empty or not finite")
            embeddings.append({"local_speaker": speaker, "start": min(span[0] for span in spans),
                               "end": max(span[1] for span in spans),
                               "source_spans": [{"start": left, "end": right} for left, right in spans],
                               "audio_seconds": len(clip) / rate,
                               "embedding": (vector / norm).tolist()})
    emit({"event": "diarization", "start": start, "end": end, "sample_rate": rate,
          "intervals": intervals, "embeddings": embeddings, "engine": "sherpa_onnx",
          "runtime_version": sherpa_onnx.__version__, "confidence_kind": "silhouette",
          "calibrated": False})
    emit({"event": "completed", "intervals": len(intervals), "embeddings": len(embeddings)})


def run(request_path, result_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    with contextlib.redirect_stdout(sys.stderr), Path(result_path).open("w", encoding="utf-8") as output:
        def emit(record):
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
        collect(request, emit)


if __name__ == "__main__":
    run(*sys.argv[1:])
