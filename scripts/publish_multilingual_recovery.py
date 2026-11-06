"""Publish an audited native-language recovery into a NEW durable project.

Input: {duration, source_prepared_seconds, segments:[{id,start,end,text_zh,
text_vi,source_language,multilingual_review,source_supported?}]}.
Coverage must come from a completed source audit, never the last speech time.
Run only while the destination project is not open in the application. The
original project's files and manual edits are never modified.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate_input(data, *, original_duration):
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("Input must contain a segments list")
    duration = data.get("duration", original_duration)
    if (type(duration) not in (int, float) or not math.isfinite(duration)
            or duration <= 0 or abs(duration - original_duration) > .1):
        raise ValueError("Recovery duration differs from original video")
    coverage = data.get("source_prepared_seconds", 0)
    if (type(coverage) not in (int, float) or not math.isfinite(coverage)
            or not 0 <= coverage <= duration):
        raise ValueError("Invalid explicitly audited source coverage")
    rows, ids = [], set()
    for row in data["segments"]:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] < 0 or row["id"] in ids:
            raise ValueError("Invalid/duplicate source row ID")
        ids.add(row["id"])
        start, end = row.get("start"), row.get("end")
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                or not 0 <= start < end <= duration + .001):
            raise ValueError(f"Invalid measured source timing: {row['id']}")
        if not isinstance(row.get("text_zh"), str) or not row["text_zh"].strip():
            raise ValueError(f"Missing native source: {row['id']}")
        if not isinstance(row.get("text_vi", ""), str):
            raise ValueError("Vietnamese text must be a string")
        if row.get("source_language") not in {"ko", "zh", "yue", "ja", "en", "mixed", "und"}:
            raise ValueError("Every row needs an explicit native source language")
        if not isinstance(row.get("multilingual_review", {}), dict):
            raise ValueError("Invalid multilingual review")
        if "source_supported" in row and type(row["source_supported"]) is not bool:
            raise ValueError("source_supported must be a boolean")
        if re.search(r"[\u3400-\u9fff\uac00-\ud7af\u3040-\u30ff]", row.get("text_vi", "")):
            raise ValueError(f"Untranslated CJK/Hangul in Vietnamese row {row['id']}")
        rows.append(dict(row))
    rows.sort(key=lambda row: (row["start"], row["end"], row["id"]))
    if not rows:
        raise ValueError("No source rows; empty input cannot prove silence")
    return {"duration": duration, "source_prepared_seconds": coverage, "segments": rows}


def evidence_for(row):
    semantic = row.get("multilingual_review", {}).get("semantic_verified") is True
    source = row.get("source_supported") is True
    accepted = source and semantic and bool(row.get("text_vi", "").strip())
    reason = ("Đã đối chiếu nguồn và nghĩa bản dịch." if accepted else
              "Bản nháp phục hồi; " + ("nguồn âm thanh chưa được xác minh độc lập. " if not source else "")
              + ("nghĩa bản dịch chưa được xác minh." if not semantic else ""))
    issues = [value for value in row.get("issues", []) if isinstance(value, str)]
    if issues:
        reason += " Vấn đề nguồn: " + ", ".join(issues) + "."
    audio = [{k: item[k] for k in ("start", "end", "text") if k in item}
             for item in row.get("independent_audio_evidence", [])[:24] if isinstance(item, dict)]
    ocr = [{k: item[k] for k in ("start", "end", "text_zh", "kind") if k in item}
           for item in row.get("ocr_evidence", [])[:24] if isinstance(item, dict)]
    return {"status": "verified" if accepted else "unresolved", "source_supported": source,
            "semantic_verified": semantic, "reason": reason.strip(),
            "audio_evidence": audio, "evidence": ocr,
            "evidence_mode": "native-recovery-" + row["source_language"]}


def timestamp(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def srt_text(rows):
    subtitles = []
    for row in rows:
        text = row.get("text_vi", "").strip()
        if text:
            subtitles.append(f"{len(subtitles)+1}\n{timestamp(row['start'])} --> {timestamp(row['end'])}\n{text}\n")
    return "\n".join(subtitles)


def atomic_json(path, data):
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
    pending.replace(path)


async def publish(args):
    from core.streaming.session_store import _read, _project_path, _valid_row_audio
    from core.streaming.pipeline import StreamingPipelineSession, SegmentItem
    from core.streaming.status_history import record_status
    from core.runtime_errors import redacted_detail
    from urllib.parse import quote

    if args.task_id == args.original_task:
        raise ValueError("Destination must differ from original project")
    destination = _project_path(args.task_id)
    original = _read(args.original_task)
    fields = original["session"]
    data = validate_input(json.loads(args.input.read_text(encoding="utf8")),
                          original_duration=fields["total_duration"])
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    audit = ROOT / "workspace" / "audit" / ("published_" + args.task_id)
    marker = audit / "publication.json"
    identity = {"original_task": args.original_task, "task_id": args.task_id, "input_sha256": digest}
    if destination.exists():
        if not marker.exists() or any(json.loads(marker.read_text(encoding="utf8")).get(k) != v for k, v in identity.items()):
            raise ValueError("Destination is not this recovery or input changed; use a new task ID")
    if args.dry_run:
        print(json.dumps({**identity, "rows": len(data["segments"]),
                          "translated": sum(bool(r.get("text_vi", "").strip()) for r in data["segments"]),
                          "source_prepared_seconds": data["source_prepared_seconds"], "dry_run": True}))
        return

    audit.mkdir(parents=True, exist_ok=True)
    # Exclusive file creation prevents concurrent script publishers. A stale lock
    # after a killed process is deliberately not removed without inspection.
    lock = audit / "publisher.lock"
    with lock.open("x", encoding="ascii") as handle:
        import os
        handle.write(str(os.getpid()))
    try:
        old_rows = {r["id"]: r for r in _read(args.task_id)["segments"]} if destination.exists() else {}
        session = StreamingPipelineSession(args.task_id, Path(fields["video_path"]),
            voice=fields["voice"], tts_engine_name=fields["tts_engine_name"],
            asr_engine_name=fields["asr_engine_name"], _restoring=True)
        for name in ("source_url", "video_size", "caption_style", "suppression_stats"):
            if name in fields:
                setattr(session, name, fields[name])
        for name in ("bgm_audio_path", "raw_audio_16k", "ref_audio"):
            value = fields.get(name)
            if value and Path(value).is_file():
                setattr(session, name, Path(value))
        session.source_video_url = "/api/local-file?path=" + quote(session.video_path.as_posix(), safe="")
        session.bgm_url = f"/api/streaming/bgm/{args.task_id}" if session.bgm_audio_path else None
        session.total_duration = data["duration"]
        session.initialized = True
        session.auto_export_result = False
        session._prepared = bool(getattr(session, "raw_audio_16k", None) and session.bgm_audio_path)
        session._chunked_source_started = True
        session._source_prepared_seconds = data["source_prepared_seconds"]
        session._visual_completed_seconds = data["source_prepared_seconds"]
        session._visual_scanned_seconds = data["source_prepared_seconds"]
        session._visual_prepass_complete = data["source_prepared_seconds"] >= data["duration"]
        session.warnings = ["Dự án phục hồi riêng từ " + args.original_task + "; bản cũ và các chỉnh sửa vẫn được giữ. Các câu chưa xác minh nguồn được đánh dấu bản nháp."]
        session.translation_sources = [{"provider": "opencode", "model": "native-language-recovery"}]
        for row in data["segments"]:
            seg = SegmentItem(row["id"], row["start"], row["end"], row["end"] - row["start"])
            seg.text_zh = seg.asr_text = row["text_zh"]
            seg.final_vi = row.get("text_vi", "").strip()
            seg.literal_vi = row.get("literal_vi", seg.final_vi)
            seg.natural_vi = row.get("natural_vi", seg.final_vi)
            # Recovery input may contain subtitle-derived source. Publication
            # must not relabel those observations as independently heard audio.
            seg.source_method = row.get("source_method") or "unknown"
            seg.source_language = row["source_language"]
            seg.asr_pretranscribed = True
            seg.translation_provider = "opencode"
            seg.evidence_mode = "native-recovery-" + row["source_language"]
            seg.verification = evidence_for(row)
            seg.needs_review = seg.verification["status"] != "verified"
            seg.review_reason = seg.verification["reason"] if seg.needs_review else None
            saved = old_rows.get(seg.id)
            if saved and (saved["text_zh"] != seg.text_zh or saved["final_vi"] != seg.final_vi
                          or saved["start"] != seg.start or saved["end"] != seg.end
                          or (saved.get("verification") or {}).get("status") == "manual"
                          or saved.get("voice_id") or saved.get("speaker_confirmation")):
                raise ValueError("Destination has been edited; refusing to overwrite it")
            if saved and _valid_row_audio(saved, saved.get("audio_path")):
                for key, value in saved.items():
                    setattr(seg, key, value)
                seg.audio_url = f"/api/streaming/audio/{args.task_id}/{seg.id}?rev={seg.revision}"
                seg.status = "READY"
            else:
                seg.status = "FAILED"
                seg.failed_stage = "TTS" if seg.final_vi else "TRANSLATING"
                seg.error = "Chưa tạo giọng bản phục hồi." if seg.final_vi else "Chưa có bản dịch có căn cứ."
            session.segments[seg.id] = seg
        unresolved = sum(s.needs_review for s in session.segments.values())
        session.review_summary = {"status": "incomplete" if unresolved else "completed", "unresolved": unresolved,
                                  "checked": len(session.segments), "verified": len(session.segments) - unresolved}
        atomic_json(marker, {**identity, "input_path": str(args.input.resolve()), "started_at": time.time()})
        atomic_json(audit / "source_translation.json", data)
        (audit / "recovered.vi.srt").write_text(srt_text(data["segments"]), encoding="utf-8-sig")
        session.is_running = bool(args.synthesize)
        session.start_wall_time = time.time()
        record_status(session, "RUNNING" if args.synthesize else "PREPARED", "native_recovery", "Đã lưu bản dịch phục hồi riêng; tạo giọng từ lời Việt đã lưu.")
        session.persist()
        try:
            if args.synthesize:
                for seg in session.segments.values():
                    if seg.status == "READY" or not seg.final_vi:
                        continue
                    try:
                        await session._synthesize_segment(seg, allow_pacing=False)
                        seg.error = None
                    except Exception as error:
                        previous_stage = seg.status
                        seg.status = "FAILED"
                        seg.failed_stage = "ALIGNING" if previous_stage == "ALIGNING" or type(error).__name__ == "SpeechBudgetError" else "TTS"
                        seg.error = redacted_detail(error)
                    session.persist()
                    print(json.dumps({"id": seg.id, "status": seg.status, "error": seg.error}, ensure_ascii=False), flush=True)
        finally:
            session.is_running = False
            failed = sum(s.status != "READY" for s in session.segments.values())
            session.error = f"Còn {failed} câu chưa có giọng; các câu đã tạo vẫn phát được." if failed else None
            session._refresh_processed_duration()
            session._recalculate_telemetry()
            record_status(session, "FAILED" if failed else "COMPLETED", "native_recovery",
                          session.error or "Đã tạo giọng cho toàn bộ các câu phục hồi; xem các cờ kiểm tra nguồn trong Transcript.")
            session.persist()
        print(json.dumps({"task_id": args.task_id, "ready": len(session.segments) - failed, "failed": failed,
                          "manifest": str(destination), "srt": str(audit / "recovered.vi.srt")}, ensure_ascii=False))
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--original-task", default="2b5ef22b")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--synthesize", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf8")
    asyncio.run(publish(args))


if __name__ == "__main__":
    main()
