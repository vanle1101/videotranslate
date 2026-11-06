"""Measured media checks; never certify semantic quality from metadata alone."""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
import wave

ROOT = Path(__file__).resolve().parents[2]


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=45)
    if result.returncode:
        return {"probe_pass": False, "returncode": result.returncode}
    data = json.loads(result.stdout)
    return {"probe_pass": True, "duration_seconds": float(data["format"]["duration"]),
            "size_bytes": path.stat().st_size,
            "streams": [{key: stream.get(key) for key in ("codec_type", "codec_name", "width", "height", "sample_rate", "channels", "duration", "nb_frames")}
                        for stream in data["streams"]]}


def decode(path):
    started = time.monotonic()
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-threads", "1",
                             "-filter_threads", "1", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0",
                             "-threads", "1", "-f", "null", "-"], capture_output=True, timeout=900)
    # Media paths are local. Keep error payload private; only actual result and
    # byte count enter reports, without URLs or any provider response text.
    return {"full_decode_pass": result.returncode == 0, "returncode": result.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3), "decoder_error_bytes": len(result.stderr)}


def wav_duration(path):
    with wave.open(str(path), "rb") as stream:
        if stream.getnframes() <= 0 or stream.getframerate() <= 0:
            raise ValueError("Empty PCM")
        return stream.getnframes() / stream.getframerate()


def inspect_rows(rows, total_duration):
    issues, measured_audio = [], []
    ids = collections.Counter(row.get("id") for row in rows)
    ordered = sorted(rows, key=lambda row: (row.get("start", 0), row.get("id", 0)))
    for index, row in enumerate(ordered):
        sid = row.get("id")
        if ids[sid] > 1:
            issues.append({"id": sid, "code": "duplicate_id"})
        start, end = row.get("start"), row.get("end")
        if not (isinstance(start, (int, float)) and isinstance(end, (int, float)) and
                math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= total_duration + .2):
            issues.append({"id": sid, "code": "invalid_source_bounds"})
            continue
        spoken = bool(str(row.get("text_zh") or row.get("asr_text") or "").strip())
        text = str(row.get("final_vi") or "").strip()
        if spoken and not text and row.get("confirmed_silence") is not True:
            issues.append({"id": sid, "code": "missing_spoken_translation"})
        if row.get("status") not in {"READY", "PLAYED"}:
            issues.append({"id": sid, "code": "nonready_status", "status": row.get("status")})
        if row.get("needs_review") or row.get("speaker_review_pending"):
            issues.append({"id": sid, "code": "pending_content_review"})
        if text:
            audio = row.get("audio_path")
            if not audio or not Path(audio).is_file():
                issues.append({"id": sid, "code": "missing_wav"})
                continue
            try:
                duration = wav_duration(audio)
            except (OSError, EOFError, wave.Error, ValueError):
                issues.append({"id": sid, "code": "invalid_wav"})
                continue
            audio_start = row.get("dub_start")
            if not isinstance(audio_start, (int, float)):
                audio_start = start
            audio_end = audio_start + duration
            tail = row.get("dub_tail_limit")
            ceiling = min(total_duration, ordered[index + 1]["start"]) if index + 1 < len(ordered) else total_duration
            if isinstance(tail, (int, float)) and math.isfinite(tail):
                ceiling = min(ceiling, tail)
            if audio_end > ceiling + .06:
                issues.append({"id": sid, "code": "audio_exceeds_next_utterance", "measured_end": round(audio_end, 6), "ceiling": ceiling})
            measured_audio.append({"id": sid, "pcm_seconds": round(duration, 6), "start": audio_start,
                                   "end": round(audio_end, 6), "cue_count": len(row.get("subtitle_cues") or [])})
            if not row.get("subtitle_cues"):
                issues.append({"id": sid, "code": "missing_subtitle_cues"})
    return {"segment_count": len(rows), "audio_measured_count": len(measured_audio),
            "issues_by_code": dict(collections.Counter(row["code"] for row in issues)),
            "issues": issues, "audio_measurements": measured_audio,
            "content_complete": not issues,
            "semantic_qa_pass": False,
            "semantic_qa_note": "Requires fresh source evidence and context review; PCM duration and text presence cannot verify meaning, naturalness or spoken completeness."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-decode", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    session = manifest["session"]
    source = Path(session["video_path"])
    result = {"observed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "task_id": manifest["task_id"], "manifest_state": manifest["state"],
              "manifest_updated_at": manifest.get("updated_at"),
              "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "source_path": str(source), "source": probe(source),
              "verified_completed": False, "output_verified": False,
              "inspection_scope": "Read-only snapshot; no output acceptance is inferred from metadata or preexisting candidate."}
    result["segments"] = inspect_rows(manifest["segments"], result["source"].get("duration_seconds", 0))
    if args.full_decode:
        result["source"].update(decode(source))
    if args.candidate:
        result["candidate_path"] = str(args.candidate)
        result["candidate"] = probe(args.candidate)
        if args.full_decode:
            result["candidate"].update(decode(args.candidate))
        result["candidate"]["duration_matches_source"] = abs(result["candidate"].get("duration_seconds", -100)
                                                            - result["source"].get("duration_seconds", 0)) <= .2
        result["candidate"]["current_project_publication"] = session.get("output_filename") == args.candidate.name
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"output_verified": False, "segment_count": result["segments"]["segment_count"],
                      "issues": result["segments"]["issues_by_code"], "source": result["source"], "candidate": result.get("candidate")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
