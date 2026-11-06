"""Independent, fail-closed media acceptance checker. Never starts a media job.

Reads a frozen manifest/snapshot and existing source/output/WAV files; writes
only a uniquely named QA directory. Metadata cannot establish meaning, natural
speech, visual subtitle visibility or perceptual synchronization. Those checks
remain pending without an independently supplied, file-bound review receipt.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import unicodedata
import uuid
import warnings
import wave
import zlib
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop
    except ImportError:
        audioop = None

HERE = Path(__file__).resolve().parent
RECEIPT_CHECKS = ("semantic_fidelity", "speech_completeness", "audio_naturalness", "audio_no_clipped_words",
                  "perceptual_sync", "subtitle_visibility", "subtitle_content_completeness", "picture_quality")


def stamp():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def scalar(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def normalized(text):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFC", str(text or "")).casefold(), re.UNICODE))


def text_digest(text):
    return hashlib.sha256(normalized(text).encode("utf-8")).hexdigest()


def sanitized(text):
    text = re.sub(r"(?i)((?:api[_-]?key|authorization|password|secret|token|cookie)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", str(text))
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    return text[:1200]


def load_manifest(path):
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Manifest exceeds 32 MiB compressed read limit")
    raw = path.read_bytes()
    record = json.loads(raw.decode("utf-8-sig"))
    if isinstance(record, dict) and record.get("storage") == "zlib-project-1":
        count = record.get("raw_bytes")
        if type(count) is not int or not 0 < count <= 64 * 1024 * 1024:
            raise ValueError("Compressed manifest expanded size invalid")
        compressed = base64.b64decode(record["payload"], validate=True)
        decoder = zlib.decompressobj()
        expanded = decoder.decompress(compressed, count + 1)
        if len(expanded) != count or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("Compressed manifest boundary invalid")
        if hashlib.sha256(expanded).hexdigest() != record.get("sha256"):
            raise ValueError("Compressed manifest hash mismatch")
        record = json.loads(expanded)
    if not isinstance(record, dict):
        raise ValueError("Manifest must be an object")
    rows = record.get("segments")
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list) or len(rows) > 20000 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Manifest segment list invalid/beyond 20000-row bound")
    session = record.get("session", record)
    if not isinstance(session, dict):
        raise ValueError("Manifest session invalid")
    return record, session, rows, hashlib.sha256(raw).hexdigest()


def file_identity(path):
    info = path.stat()
    return {"path": str(path.resolve()), "bytes": info.st_size, "modified_ns": info.st_mtime_ns}


def sha256_file(path, deadline):
    digest = hashlib.sha256()
    before = file_identity(path)
    with path.open("rb") as stream:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Hash deadline exceeded")
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    after = file_identity(path)
    if before != after:
        raise ValueError("File changed during hash")
    return digest.hexdigest()


class Checker:
    def __init__(self, manifest, output, qa_root=HERE / "qa", *, source=None, subtitle_file=None,
                 receipt=None, decode_timeout=300, deadline_seconds=1200, frame_count=6,
                 review_gate_revision=2, video_label=None):
        self.manifest = Path(manifest).resolve()
        self.output = Path(output).resolve()
        self.source_override = Path(source).resolve() if source else None
        self.subtitle_file = Path(subtitle_file).resolve() if subtitle_file else None
        self.receipt = Path(receipt).resolve() if receipt else None
        self.decode_timeout, self.frame_count = decode_timeout, frame_count
        self.review_gate_revision, self.video_label = review_gate_revision, video_label
        self.started = time.monotonic()
        self.deadline = self.started + deadline_seconds
        self.check_id = "qa-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self.qa = Path(qa_root).resolve() / self.check_id
        self.qa.mkdir(parents=True, exist_ok=False)
        self.failures, self.pending, self.notes = [], [], []
        self.commands = []
        self.input_identities = {}
        self.row_results = []
        self.expected_cues = []
        self.ffmpeg, self.ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")

    def fail(self, code, *, row_id=None, detail=None):
        item = {"code": code}
        if row_id is not None:
            item["row_id"] = row_id
        if detail is not None:
            item["detail"] = sanitized(detail)
        if item not in self.failures:
            self.failures.append(item)

    def wait_review(self, code):
        if code not in self.pending:
            self.pending.append(code)

    def remember(self, path):
        self.input_identities[str(path)] = file_identity(path)

    def run_command(self, args, timeout, stage):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Global verification deadline reached")
        before = time.monotonic()
        try:
            process = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=min(timeout, remaining),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.commands.append({"stage": stage, "exit_code": process.returncode,
                                  "elapsed_seconds": round(time.monotonic() - before, 3),
                                  "error_excerpt": sanitized(process.stderr) if process.returncode else None})
            if process.returncode:
                raise ValueError(stage + " failed; exit " + str(process.returncode))
            return process.stdout
        except subprocess.TimeoutExpired:
            self.commands.append({"stage": stage, "timeout": True, "elapsed_seconds": round(time.monotonic() - before, 3)})
            raise TimeoutError(stage + " exceeded bounded timeout")

    def probe(self, path, stage):
        data = json.loads(self.run_command([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], 20, stage))
        streams = []
        for stream in data.get("streams", []):
            result = {key: stream.get(key) for key in ("index", "codec_name", "codec_type", "width", "height", "sample_rate", "channels", "duration", "start_time", "avg_frame_rate", "nb_frames") if key in stream}
            rotation = scalar(stream.get("tags", {}).get("rotate")) or 0
            for side in stream.get("side_data_list", []):
                if "rotation" in side:
                    rotation = scalar(side["rotation"]) or 0
            result["rotation"] = rotation
            if result.get("codec_type") == "video":
                dimensions = [result.get("width"), result.get("height")]
                if int(round(rotation)) % 180 == 90:
                    dimensions.reverse()
                result["display_dimensions"] = dimensions
            streams.append(result)
        return {"duration_seconds": scalar(data.get("format", {}).get("duration")),
                "start_time_seconds": scalar(data.get("format", {}).get("start_time")),
                "size_bytes": scalar(data.get("format", {}).get("size")), "streams": streams}

    def audio(self, path):
        before = file_identity(path)
        with wave.open(str(path), "rb") as stream:
            channels, width, rate, count = stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()
            if rate <= 0 or count <= 0 or not 1 <= channels <= 8 or width not in (1, 2, 3, 4):
                raise ValueError("WAV format invalid")
            duration = count / rate
            if duration > 300:
                raise ValueError("Segment WAV exceeds 300-second bounded inspection")
            max_sample = (1 << (width * 8 - 1)) - 1
            peak = energy = observed_frames = 0
            tail = b""
            while True:
                if time.monotonic() >= self.deadline:
                    raise TimeoutError("WAV scan deadline reached")
                pcm = stream.readframes(16384)
                if not pcm:
                    break
                frames = len(pcm) // (channels * width)
                observed_frames += frames
                tail = (tail + pcm)[-max(channels * width, int(rate * .02) * channels * width):]
                if audioop:
                    measured = audioop.bias(pcm, 1, -128) if width == 1 else pcm
                    peak = max(peak, audioop.max(measured, width))
                    energy += audioop.rms(measured, width) ** 2 * frames
            if observed_frames != count:
                raise ValueError("WAV PCM data shorter than header claims")
            tail_measured = audioop.bias(tail, 1, -128) if audioop and width == 1 else tail
            tail_rms = audioop.rms(tail_measured, width) if audioop and tail else None
            rms = math.sqrt(energy / count) if audioop else None
            result = {"duration_seconds": duration, "channels": channels, "sample_rate": rate,
                      "pcm_frames": count, "peak_ratio": peak / max_sample if audioop else None,
                      "rms_dbfs": 20 * math.log10(max(rms / max_sample, 1e-12)) if rms is not None else None,
                      "last_20ms_rms_dbfs": 20 * math.log10(max(tail_rms / max_sample, 1e-12)) if tail_rms is not None else None,
                      "meaning_or_naturalness_verified": False}
        if before != file_identity(path):
            raise ValueError("WAV changed during verification")
        self.input_identities[str(path)] = before
        return result

    def validate_row(self, row, rows_by_id, total):
        identity = row.get("id")
        result = {"row_id": identity, "status": row.get("status"), "revision": row.get("revision"),
                  "source_text_sha256": text_digest(row.get("text_zh") or row.get("asr_text")),
                  "translation_sha256": text_digest(row.get("final_vi")), "cues": [], "failures": []}
        before_failures = len(self.failures)
        start, end = scalar(row.get("start")), scalar(row.get("end"))
        dstart = scalar(row.get("dub_start")) if row.get("dub_start") is not None else start
        dend = scalar(row.get("dub_end")) if row.get("dub_end") is not None else end
        result.update(source_start=start, source_end=end, dub_start=dstart, dub_end=dend)
        valid_bounds = all(value is not None for value in (start, end, dstart, dend)) and 0 <= start < end and 0 <= dstart < dend and end <= total + .02 and dend <= total + .02
        if not valid_bounds:
            self.fail("INVALID_SEGMENT_OR_DUB_BOUNDS", row_id=identity)
        elif (row.get("dub_start") is None) != (row.get("dub_end") is None):
            self.fail("INCOMPLETE_DUB_TIMING_PAIR", row_id=identity)
        else:
            cap = end + .35
            tail_limit = scalar(row.get("dub_tail_limit"))
            if row.get("dub_tail_limit") is not None:
                if tail_limit is None or not end - .000001 <= tail_limit <= end + 1.000001:
                    self.fail("INVALID_DUB_TAIL_LIMIT", row_id=identity)
                else:
                    cap = max(cap, tail_limit)
            if abs(dstart - start) > .350001 or dend < end - .350001 or dend > cap + .000001:
                self.fail("DUB_SHIFT_OUTSIDE_ALLOWED_BOUNDS", row_id=identity)
        source_speech = bool(str(row.get("text_zh") or row.get("asr_text") or "").strip())
        spoken = bool(str(row.get("final_vi") or "").strip())
        result.update(known_source_speech=source_speech, has_translated_speech=spoken,
                      confirmed_silence=row.get("confirmed_silence") is True)
        if source_speech and not spoken:
            self.fail("EMPTY_KNOWN_SOURCE_SPEECH", row_id=identity)
        if row.get("status") not in {"READY", "PLAYED"}:
            self.fail("SEGMENT_NOT_READY", row_id=identity, detail=row.get("status"))
        if row.get("error") or row.get("failed_stage") or row.get("timing_issue"):
            self.fail("UNRESOLVED_SEGMENT_ERROR_OR_TIMING_CONFLICT", row_id=identity)
        if row.get("needs_review") or row.get("speaker_review_pending") or row.get("tts_voice_outdated"):
            self.fail("UNRESOLVED_CONTENT_SPEAKER_OR_VOICE_REVIEW", row_id=identity)
        audit = row.get("verification") or {}
        if not isinstance(audit, dict):
            audit = {}
        result["recorded_semantic_evidence"] = {key: audit.get(key) for key in ("status", "source_supported", "semantic_verified", "second_pass_status", "review_gate_revision", "address_verified", "source_scope_conflict", "audio_audit_status")}
        if spoken:
            if (audit.get("status") not in {"verified", "corrected"} or audit.get("source_supported") is not True or
                    audit.get("semantic_verified") is not True or audit.get("second_pass_status") != "completed"):
                self.fail("SEMANTIC_EVIDENCE_UNRESOLVED_OR_INCOMPLETE", row_id=identity)
            if audit.get("review_gate_revision") != self.review_gate_revision:
                self.fail("SEMANTIC_EVIDENCE_GATE_STALE_OR_MISSING", row_id=identity)
            if audit.get("source_scope_conflict") or audit.get("address_stale_source_ids"):
                self.fail("SEMANTIC_EVIDENCE_SOURCE_STALE_OR_CONFLICTED", row_id=identity)
            if audit.get("address_applicable") is True and audit.get("address_verified") is not True and audit.get("address_neutral_faithful") is not True:
                self.fail("UNRESOLVED_ADDRESS_EVIDENCE", row_id=identity)
            for sid, reviewed_source in (audit.get("address_context_sources") or {}).items():
                current = rows_by_id.get(str(sid))
                if current is None or str(current.get("text_zh") or "") != str(reviewed_source):
                    self.fail("SEMANTIC_EVIDENCE_CONTEXT_CHANGED", row_id=identity)
            pacing = audit.get("pacing") or {}
            if isinstance(pacing, dict) and pacing and (pacing.get("text") != row.get("final_vi") or pacing.get("status") != "verified" or pacing.get("meaning_preserved") is not True):
                self.fail("PACING_SEMANTIC_EVIDENCE_STALE_OR_INCOMPLETE", row_id=identity)
            path = Path(str(row.get("audio_path") or ""))
            if not row.get("audio_path") or not path.is_file():
                self.fail("SPOKEN_WAV_MISSING", row_id=identity)
            else:
                try:
                    audio = self.audio(path.resolve())
                    result["audio"] = audio
                    if valid_bounds and audio["duration_seconds"] > dend - dstart + max(.001, 2 / audio["sample_rate"]):
                        self.fail("WAV_WOULD_BE_TAIL_CLIPPED_BY_DUB_SLOT", row_id=identity)
                    if valid_bounds and dstart + audio["duration_seconds"] > total + .001:
                        self.fail("WAV_EXTENDS_PAST_VIDEO_TAIL", row_id=identity)
                    declared = scalar(row.get("tts_duration"))
                    # Production stores the raw TTS sample length and the
                    # applied speed separately. The fitted WAV must match
                    # their ratio, rather than the longer raw sample itself.
                    speed = scalar(row.get("speed_ratio", 1.0))
                    if speed is None or not .9 - 1e-9 <= speed <= 1.15 + 1e-9:
                        self.fail("SPOKEN_WAV_SPEED_OUTSIDE_NATURAL_LIMIT", row_id=identity)
                    elif declared is not None and abs(audio["duration_seconds"] - declared / speed) > .06:
                        self.fail("RECORDED_TTS_DURATION_MISMATCH", row_id=identity)
                    if audio["rms_dbfs"] is not None and audio["rms_dbfs"] < -65:
                        self.fail("SPOKEN_WAV_NEAR_SILENT", row_id=identity)
                    if audio["last_20ms_rms_dbfs"] is not None and audio["last_20ms_rms_dbfs"] > -35:
                        self.wait_review("AUDIBLE_WAV_TAIL_REQUIRES_CLIPPED_WORD_REVIEW")
                    if audio["peak_ratio"] is not None and audio["peak_ratio"] >= .999:
                        self.wait_review("FULL_SCALE_WAV_SAMPLES_REQUIRE_DISTORTION_REVIEW")
                except (OSError, ValueError, wave.Error, EOFError, TimeoutError) as error:
                    self.fail("SPOKEN_WAV_INVALID_OR_UNVERIFIED", row_id=identity, detail=type(error).__name__ + ": " + str(error))
        cues = row.get("subtitle_cues")
        if not isinstance(cues, list):
            cues = []
        if spoken and not cues:
            self.fail("SPOKEN_SUBTITLE_CUES_MISSING", row_id=identity)
        cue_texts = []
        word_texts = []
        previous_cue_end = -1
        for cue in cues:
            if not isinstance(cue, dict):
                self.fail("INVALID_SUBTITLE_CUE", row_id=identity)
                continue
            left, right = scalar(cue.get("start")), scalar(cue.get("end"))
            text = str(cue.get("text") or "")
            valid = bool(left is not None and right is not None and 0 <= left < right <= total + .02 and valid_bounds and left >= dstart - .05 and right <= dend + .05 and text.strip())
            if not valid:
                self.fail("SUBTITLE_CUE_BOUNDS_OR_TEXT_INVALID", row_id=identity)
                continue
            if left < previous_cue_end - .01:
                self.fail("SUBTITLE_CUES_OVERLAP_WITHIN_SEGMENT", row_id=identity)
            previous_cue_end = right
            cue_texts.append(text)
            result["cues"].append({"start": left, "end": right, "text_sha256": text_digest(text)})
            self.expected_cues.append({"row_id": identity, "start": left, "end": right, "text": text})
            for word in cue.get("words", []):
                if not isinstance(word, dict):
                    self.fail("SUBTITLE_WORD_METADATA_INVALID", row_id=identity)
                    continue
                wstart, wend = scalar(word.get("start")), scalar(word.get("end"))
                if wstart is None or wend is None or not left - .05 <= wstart < wend <= right + .05:
                    self.fail("SUBTITLE_WORD_BOUNDS_INVALID", row_id=identity)
                word_texts.append(str(word.get("text") or ""))
        if spoken and cue_texts:
            collapsed = [text for index, text in enumerate(cue_texts) if index == 0 or normalized(text) != normalized(cue_texts[index - 1])]
            versions = [normalized(" ".join(cue_texts)), normalized(" ".join(collapsed))]
            if word_texts:
                versions.append(normalized(" ".join(word_texts)))
            if normalized(row["final_vi"]) not in versions:
                self.fail("SUBTITLE_CUES_OMIT_OR_DUPLICATE_SPOKEN_TEXT", row_id=identity)
        result["failures"] = [item["code"] for item in self.failures[before_failures:] if item.get("row_id") == identity]
        return result

    @staticmethod
    def parse_subtitles(path):
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Subtitle file exceeds 8 MiB")
        text = path.read_text(encoding="utf-8-sig")
        cues = []
        def seconds(value):
            h, m, s = value.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        if path.suffix.lower() in {".ass", ".ssa"}:
            for line in text.splitlines():
                if not line.startswith("Dialogue:"):
                    continue
                columns = line.split(",", 9)
                if len(columns) != 10 or re.search(r"\\p[1-9]", columns[9]):
                    continue  # ASS drawing/mask events are not subtitle text.
                payload = re.sub(r"\{[^}]*\}", "", columns[9]).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
                if payload.strip():
                    cues.append({"start": seconds(columns[1]), "end": seconds(columns[2]), "text": payload})
        else:
            for block in re.split(r"\r?\n\s*\r?\n", text):
                lines = block.strip().splitlines()
                index = next((index for index, line in enumerate(lines) if "-->" in line), None)
                if index is None:
                    continue
                times = lines[index].split("-->")
                payload = re.sub(r"<[^>]+>", "", " ".join(lines[index + 1:]))
                cues.append({"start": seconds(times[0].strip()), "end": seconds(times[1].strip().split()[0]), "text": payload})
        return cues

    def subtitles(self, output_probe, total):
        result = {"manifest_cue_count": len(self.expected_cues), "embedded_subtitle_streams": sum(stream.get("codec_type") == "subtitle" for stream in output_probe["streams"]),
                  "sidecar_supplied": bool(self.subtitle_file), "sidecar_verified": False,
                  "burned_in_visibility_verified": False}
        subtitle = self.subtitle_file
        if not subtitle and result["embedded_subtitle_streams"]:
            subtitle = self.qa / "embedded_subtitles.srt"
            try:
                self.run_command([self.ffmpeg, "-nostdin", "-v", "error", "-n", "-i", str(self.output), "-map", "0:s:0", str(subtitle)], 30, "extract_embedded_subtitles")
            except (ValueError, TimeoutError):
                self.fail("EMBEDDED_SUBTITLE_EXTRACTION_FAILED")
                subtitle = None
        if subtitle:
            try:
                actual = self.parse_subtitles(subtitle)
                result["sidecar_cue_count"] = len(actual)
                self.remember(subtitle)
                invalid = [cue for cue in actual if not 0 <= cue["start"] < cue["end"] <= total + .1 or not cue["text"].strip()]
                if invalid:
                    self.fail("SIDECAR_SUBTITLE_INVALID_CUES")
                unmatched = []
                for expected in self.expected_cues:
                    if not any(abs(cue["start"] - expected["start"]) <= .06 and abs(cue["end"] - expected["end"]) <= .06 and normalized(cue["text"]) == normalized(expected["text"]) for cue in actual):
                        unmatched.append(expected["row_id"])
                result["unmatched_row_ids"] = sorted(set(unmatched), key=str)
                if unmatched:
                    self.fail("SIDECAR_SUBTITLE_CUE_COVERAGE_MISMATCH", detail=str(result["unmatched_row_ids"]))
                result["sidecar_verified"] = bool(actual and not invalid and not unmatched)
            except (OSError, ValueError, UnicodeError) as error:
                self.fail("SIDECAR_SUBTITLE_INVALID", detail=type(error).__name__)
        else:
            self.wait_review("BURNED_IN_SUBTITLES_REQUIRE_FULL_VISUAL_CONTENT_REVIEW")
        return result

    def frames(self, source, duration, frame_rate=None):
        try:
            numerator, denominator = str(frame_rate or "10/1").split("/")
            fps = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            fps = 10
        frame_margin = max(.05, 1.5 / fps) if fps > 0 else .2
        last_sample = max(0, duration - frame_margin)
        samples = {max(0.0, min(last_sample, duration * fraction)) for fraction in (0, .25, .5, .75, .98)}
        for cue in self.expected_cues[::max(1, len(self.expected_cues) // max(1, self.frame_count - 2))]:
            samples.add(min(last_sample, (cue["start"] + cue["end"]) / 2))
        samples = sorted(samples)
        if len(samples) > self.frame_count:
            samples = [samples[round(index * (len(samples) - 1) / (self.frame_count - 1))] for index in range(self.frame_count)]
        result = []
        for index, second in enumerate(samples):
            for label, path in (("source", source), ("output", self.output)):
                png = self.qa / f"{index:02d}-{label}-{second:.3f}s.png"
                try:
                    self.run_command([self.ffmpeg, "-nostdin", "-v", "error", "-n", "-threads", "1", "-filter_threads", "1", "-ss", f"{second:.6f}", "-i", str(path),
                                      "-frames:v", "1", "-vf", "scale=960:-2", str(png)], 30, "render_frame:" + label)
                    if not png.is_file() or png.stat().st_size < 100:
                        raise ValueError("No rendered PNG")
                    result.append({"media": label, "at_seconds": second, "path": str(png), "bytes": png.stat().st_size, "visual_reviewed": False})
                except (ValueError, TimeoutError) as error:
                    self.fail("SAMPLED_FRAME_RENDER_FAILED", detail=label + ": " + type(error).__name__)
        return result

    def review_receipt(self, bindings, row_ids):
        result = {"supplied": bool(self.receipt), "valid": False,
                  "scope": "Requires actual independent complete source/output watching and output audio audition; flags alone are not review"}
        if not self.receipt:
            self.wait_review("INDEPENDENT_CONTENT_AUDIO_PERCEPTUAL_REVIEW_PENDING")
            return result
        try:
            if self.receipt.stat().st_size > 1024 * 1024:
                raise ValueError("Review receipt exceeds 1 MiB")
            record = json.loads(self.receipt.read_text(encoding="utf-8"))
            checks = record.get("checks", {})
            result["checks"] = {key: checks.get(key) for key in RECEIPT_CHECKS}
            result["reviewer_type"] = record.get("reviewer_type")
            result["reviewed_at"] = record.get("reviewed_at")
            valid = record.get("schema") == 1 and record.get("scope") == "FULL_SOURCE_AND_OUTPUT" and record.get("reviewer_type") in {"human", "independent_multimodal_audio_reviewer"}
            valid = valid and bool(record.get("reviewer_id")) and bool(record.get("reviewed_at"))
            valid = valid and record.get("bindings") == bindings
            valid = valid and set(map(str, record.get("reviewed_segment_ids", []))) == set(map(str, row_ids))
            valid = valid and all(checks.get(key) is True for key in RECEIPT_CHECKS) and record.get("unresolved_count") == 0
            evidence_files = record.get("evidence_files", [])
            valid = valid and bool(evidence_files) and all(Path(str(path)).is_file() for path in evidence_files)
            result["valid"] = bool(valid)
            result["evidence_files"] = [str(path) for path in evidence_files]
            if not valid:
                self.fail("INDEPENDENT_REVIEW_RECEIPT_INVALID_STALE_OR_INCOMPLETE")
            else:
                self.pending = []  # Receipt must explicitly cover every listed content/audio concern.
        except (OSError, ValueError, TypeError) as error:
            self.fail("INDEPENDENT_REVIEW_RECEIPT_UNREADABLE", detail=type(error).__name__)
        return result

    def check(self):
        report = {"schema": 1, "check_id": self.check_id, "started_at": stamp(), "video_label": self.video_label,
                  "manifest": str(self.manifest), "output": str(self.output), "qa_directory": str(self.qa),
                  "verified_completed": False, "OUTPUT_VERIFIED": False,
                  "content_or_audio_quality_inferred_from_metadata": False}
        try:
            if not self.ffmpeg or not self.ffprobe:
                raise ValueError("ffmpeg/ffprobe unavailable")
            record, session, rows, manifest_hash = load_manifest(self.manifest)
            report["task_id"] = record.get("task_id")
            self.remember(self.manifest)
            source = self.source_override or Path(str(session.get("video_path") or "")).resolve()
            if not source.is_file() or not self.output.is_file() or source == self.output:
                raise ValueError("Existing distinct source/output files are required")
            if self.qa.is_relative_to(source) or self.qa.is_relative_to(self.output):
                raise ValueError("QA directory cannot be a media input")
            self.remember(source)
            self.remember(self.output)
            report["source"] = str(source)
            source_probe, output_probe = self.probe(source, "probe_source"), self.probe(self.output, "probe_output")
            report["source_probe"], report["output_probe"] = source_probe, output_probe
            duration = source_probe["duration_seconds"]
            if duration is None or duration <= 0:
                raise ValueError("Source duration invalid")
            output_duration = output_probe["duration_seconds"]
            duration_valid = bool(output_duration is not None and abs(duration - output_duration) <= .5)
            report["duration_difference_seconds"] = output_duration - duration if output_duration is not None else None
            if not duration_valid:
                self.fail("SOURCE_OUTPUT_DURATION_MISMATCH")
            source_video = next((stream for stream in source_probe["streams"] if stream.get("codec_type") == "video"), None)
            output_video = next((stream for stream in output_probe["streams"] if stream.get("codec_type") == "video"), None)
            output_audio = next((stream for stream in output_probe["streams"] if stream.get("codec_type") == "audio"), None)
            if not source_video or not output_video:
                self.fail("VIDEO_STREAM_MISSING")
            elif source_video.get("display_dimensions") != output_video.get("display_dimensions") or not all((scalar(dimension) or 0) > 0 for dimension in output_video["display_dimensions"]):
                self.fail("OUTPUT_DIMENSIONS_MISMATCH_OR_INVALID")
            if not output_audio or not (scalar(output_audio.get("sample_rate")) or 0) > 0 or not (scalar(output_audio.get("channels")) or 0) > 0:
                self.fail("OUTPUT_AUDIO_STREAM_MISSING_OR_INVALID")
            if output_audio:
                audio_duration = scalar(output_audio.get("duration"))
                if audio_duration is None or abs(audio_duration - duration) > .5:
                    self.fail("OUTPUT_AUDIO_DURATION_UNVERIFIED_OR_MISMATCH")
            status = str(record.get("state", session.get("status", record.get("status", "UNKNOWN")))).upper()
            progress = record.get("progress") if isinstance(record.get("progress"), dict) else {}
            report["pipeline_state"] = status
            if status in {"RUNNING", "FAILED", "STOPPED", "PAUSED", "CANCELLED", "CANCELLING", "UNKNOWN"} or progress.get("status") in {"RUNNING", "FAILED", "PREVIEW_READY", "PREPARED"}:
                self.fail("PIPELINE_NOT_COMPLETE_OR_CURRENTLY_RUNNING")
            if not rows:
                self.fail("EMPTY_SEGMENT_MANIFEST")
            filename = session.get("output_filename", record.get("output_filename"))
            if not filename or Path(str(filename)).name != self.output.name:
                self.fail("OUTPUT_NOT_PUBLISHED_BY_CURRENT_MANIFEST")
            if session.get("caption_output_outdated"):
                self.fail("CAPTION_OUTPUT_MARKED_STALE")
            summary = session.get("review_summary") or progress.get("review_summary") or {}
            report["review_summary_counts"] = {key: summary.get(key) for key in ("status", "checked", "verified", "corrected", "unresolved", "incomplete", "failed", "manual") if key in summary}
            if summary.get("status") != "completed" or any((scalar(summary.get(key)) or 0) > 0 for key in ("unresolved", "incomplete", "failed")):
                self.fail("GLOBAL_SEMANTIC_REVIEW_INCOMPLETE")
            if any(isinstance(item, dict) and item.get("needs_review") for item in session.get("screen_texts", [])):
                self.fail("UNRESOLVED_ONSCREEN_TEXT_REVIEW")
            total_declared = scalar(session.get("total_duration", progress.get("total_seconds")))
            if total_declared is None or abs(total_declared - duration) > .5:
                self.fail("MANIFEST_SOURCE_DURATION_MISMATCH")
            if session.get("_chunked_source_started"):
                prepared = scalar(session.get("_source_prepared_seconds"))
                if prepared is None or prepared < duration - .5:
                    self.fail("SOURCE_PREPARATION_COVERAGE_INCOMPLETE")
            for job in session.get("_chunk_jobs", []):
                if isinstance(job, dict) and job.get("state") not in {"COMPLETED", "READY", "PLAYED"}:
                    self.fail("UNRESOLVED_SOURCE_CHUNK")
            ids = [str(row.get("id")) for row in rows]
            if len(set(ids)) != len(ids):
                self.fail("DUPLICATE_SEGMENT_IDS")
            by_id = {str(row.get("id")): row for row in rows}
            for row in rows:
                self.row_results.append(self.validate_row(row, by_id, duration))
            ordered = sorted((row for row in self.row_results if finite(row.get("dub_start"))), key=lambda row: row["dub_start"])
            for left, right in zip(ordered, ordered[1:]):
                if finite(left.get("dub_end")) and left["dub_end"] > right["dub_start"] + .002:
                    self.fail("DUB_SLOTS_OVERLAP", row_id=left["row_id"], detail="Next row " + str(right["row_id"]))
                if left.get("audio") and left["dub_start"] + left["audio"]["duration_seconds"] > right["dub_start"] + .002:
                    self.fail("WAVS_OVERLAP_IN_MIX_TIMELINE", row_id=left["row_id"], detail="Next row " + str(right["row_id"]))
            report["segments"] = self.row_results
            report["segment_count"] = len(rows)
            report["subtitles"] = self.subtitles(output_probe, duration)
            decode_ok = False
            try:
                self.run_command([self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-threads", "1", "-filter_threads", "1", "-i", str(self.output),
                                  "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"], self.decode_timeout, "full_output_decode")
                decode_ok = True
            except (ValueError, TimeoutError) as error:
                self.fail("FULL_OUTPUT_DECODE_FAILED_OR_TIMED_OUT", detail=type(error).__name__)
            report["full_decode_verified"] = decode_ok
            report["sampled_frames"] = self.frames(source, duration, output_video.get("avg_frame_rate") if output_video else None)
            bindings = {"manifest_sha256": manifest_hash,
                        "source_sha256": sha256_file(source, self.deadline),
                        "output_sha256": sha256_file(self.output, self.deadline)}
            report["bindings"] = bindings
            report["independent_review"] = self.review_receipt(bindings, ids)
            if report["independent_review"]["valid"]:
                report["subtitles"]["burned_in_visibility_verified"] = True
            for path, before in self.input_identities.items():
                if file_identity(Path(path)) != before:
                    self.fail("INPUT_CHANGED_DURING_VERIFICATION", detail=Path(path).name)
            sound = not any(item["code"].startswith(("SPOKEN_WAV", "WAV_", "OUTPUT_AUDIO", "RECORDED_TTS")) for item in self.failures) and bool(output_audio)
            timing = not any(item["code"].startswith(("DUB_", "INVALID_SEGMENT_OR_DUB", "INCOMPLETE_DUB", "WAV_", "SUBTITLE_CUE_BOUNDS", "SUBTITLE_WORD_BOUNDS")) for item in self.failures)
            report["technical_checks"] = {"full_decode": decode_ok, "duration": duration_valid,
                "picture_stream_and_sampled_frames": bool(output_video and report["sampled_frames"]) and not any(item["code"] in {"OUTPUT_DIMENSIONS_MISMATCH_OR_INVALID", "VIDEO_STREAM_MISSING", "SAMPLED_FRAME_RENDER_FAILED"} for item in self.failures),
                "audio_stream_and_row_wav_bounds": sound, "timing_bounds": timing,
                "subtitle_manifest_complete": bool(self.expected_cues) and not any(item["code"].startswith(("SUBTITLE_", "SPOKEN_SUBTITLE")) for item in self.failures)}
        except (OSError, ValueError, TypeError, KeyError, TimeoutError) as error:
            self.fail("CHECKER_INPUT_OR_RUNTIME_FAILURE", detail=type(error).__name__ + ": " + str(error))
        report.update(completed_at=stamp(), elapsed_seconds=round(time.monotonic() - self.started, 3),
                      failures=self.failures, pending_content_audio_perceptual_review=self.pending,
                      subprocess_measurements=self.commands,
                      limits={"deadline_seconds": round(self.deadline - self.started), "decode_timeout_seconds": self.decode_timeout,
                              "frame_count": self.frame_count, "threads_per_ffmpeg": 1})
        accepted = not self.failures and not self.pending and bool(report.get("independent_review", {}).get("valid"))
        report["OUTPUT_VERIFIED"] = report["verified_completed"] = accepted
        report["status"] = "VERIFIED COMPLETED" if accepted else "REJECTED" if self.failures else "PENDING_CONTENT_AUDIO_PERCEPTUAL_REVIEW"
        report["acceptance_event_eligible"] = accepted
        report["evidence_path"] = str(self.qa / "acceptance.json")
        (self.qa / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["# Independent output acceptance", "", "Status: " + report["status"],
                 "Output: " + str(self.output), "Evidence: " + report["evidence_path"],
                 "Elapsed: " + str(report["elapsed_seconds"]) + " seconds.",
                 "No semantic accuracy, speech naturalness, subtitle visibility or perceptual sync is inferred from metadata.", "", "Failures:", ""]
        lines += ["- " + item["code"] + ("; row " + str(item["row_id"]) if "row_id" in item else "") for item in self.failures] or ["- None"]
        lines += ["", "Pending actual content/audio/perceptual review:", ""] + ["- " + code for code in self.pending]
        (self.qa / "acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--subtitle-file", type=Path)
    parser.add_argument("--perceptual-evidence", type=Path)
    parser.add_argument("--qa-root", type=Path, default=HERE / "qa")
    parser.add_argument("--decode-timeout", type=float, default=300)
    parser.add_argument("--deadline-seconds", type=float, default=1200)
    parser.add_argument("--frame-count", type=int, default=6)
    parser.add_argument("--review-gate-revision", type=int, default=2)
    parser.add_argument("--video-label", choices=("SHORT", "LONG"))
    parser.add_argument("--emit-verified-event", action="store_true", help="Append acceptance events only if every actual check passes")
    args = parser.parse_args()
    if not 10 <= args.decode_timeout <= 3600 or not 30 <= args.deadline_seconds <= 7200 or not 2 <= args.frame_count <= 20:
        parser.error("Use decode timeout 10..3600s, deadline 30..7200s, frames 2..20")
    checker = Checker(args.manifest, args.output, args.qa_root, source=args.source, subtitle_file=args.subtitle_file,
        receipt=args.perceptual_evidence, decode_timeout=args.decode_timeout, deadline_seconds=args.deadline_seconds,
        frame_count=args.frame_count, review_gate_revision=args.review_gate_revision, video_label=args.video_label)
    report = checker.check()
    if args.emit_verified_event and report["acceptance_event_eligible"]:
        common = {"timestamp": stamp(), "video_label": args.video_label, "task_id": report.get("task_id"),
                  "run_id": report["check_id"], "evidence_path": report["evidence_path"]}
        output_event = {**common, "event": "OUTPUT_VERIFIED", "event_id": uuid.uuid4().hex,
            "details": {"picture_valid": True, "audio_valid": True, "subtitles_valid": True,
                        "duration_valid": True, "sync_valid": True, "semantic_valid": True,
                        "subtitle_coverage_valid": True, "evidence_path": report["evidence_path"]}}
        test_event = {**common, "event": "TEST_RESULT", "event_id": uuid.uuid4().hex,
            "test_id": "output-acceptance-" + str(args.video_label or report.get("task_id")),
            "test_name": "Actual output acceptance incl. independent full content/audio review",
            "test_kind": "real-media-acceptance", "result": "PASS", "duration_seconds": report["elapsed_seconds"]}
        with (HERE / "runtime_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(output_event, ensure_ascii=False) + "\n" + json.dumps(test_event, ensure_ascii=False) + "\n")
            stream.flush()
    print(json.dumps({"check_id": report["check_id"], "status": report["status"], "OUTPUT_VERIFIED": report["OUTPUT_VERIFIED"],
                      "failures": len(report["failures"]), "pending_reviews": report["pending_content_audio_perceptual_review"],
                      "full_decode_verified": report.get("full_decode_verified", False), "evidence_path": report["evidence_path"]}), flush=True)
    return 0 if report["OUTPUT_VERIFIED"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
