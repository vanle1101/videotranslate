"""Read the complete error log without exporting dialogue or credentials.

Historical failure occurrences are kept separate from current task state and
deliberate test faults. No production modules or service routes are invoked.
"""
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[ERROR\] \[errors\]: (.*)$")
TEST_TASKS = {
    "preview-owned": "tests/test_preview_chunked_pipeline.py",
    "regression": "tests/test_portable_pipeline.py",
    "persist-test": "tests/test_session_store.py",
    "transcript-test": "tests/test_transcript_edit.py",
    "export_test": "tests/test_runtime_errors.py",
    "review-export": "tests/test_auto_review_export.py",
    "export_review-export": "tests/test_auto_review_export.py",
}


def split_records(text):
    records, current = [], None
    for number, line in enumerate(text.splitlines(), 1):
        header = HEADER.match(line)
        if header:
            if current:
                records.append(current)
            current = {"timestamp": header[1] + "+07:00", "line": number,
                       "message": header[2], "trace": []}
        elif current:
            current["trace"].append(line)
    if current:
        records.append(current)
    return records


def classify(record, root=ROOT):
    message = record["message"]
    bracket = re.match(r"\[([^\]]+)\]", message)
    run = re.search(r"\brun_id=([^\s]+)", message)
    task = bracket[1] if bracket else run[1] if run else None
    mock_trace = any("unittest" in line and "mock.py" in line for line in record["trace"])
    proof = TEST_TASKS.get(task)
    is_fixture = bool(proof and (root / proof).is_file())
    origin = "intentional_test_fault" if mock_trace or is_fixture else "historical_runtime_failure"
    error = re.search(r"error_type=([A-Za-z_][A-Za-z0-9_]*)", message)
    trailing = re.search(r"\(([A-Za-z_][A-Za-z0-9_]*)\)\s*$", message)
    error_type = error[1] if error else trailing[1] if trailing else "Unrecorded"
    code = next((name for name in (
        "SOURCE_RETRY_PENDING", "SEGMENT_FAILED_CONTINUING", "REVIEW_FAILED",
        "RESUMED_REVIEW_FAILED", "REVIEW_AUDIO_DEFERRED", "QUEUE_WORKER_FAILED",
        "PROJECT_CAPACITY_EXCEEDED", "PROJECT_SAVE_FAILED", "EXPORT_FAILED",
    ) if name in message), "PIPELINE_FAILURE")
    segment = re.search(r"segment_id=(\d+)", message)
    sentence = re.search(r"Lỗi câu (\d+)", message)
    segment_id = int(segment[1]) if segment else int(sentence[1]) - 1 if sentence else None
    stage = re.search(r"stage=([^ ]+)", message)
    spoken_stage = re.search(r"ở bước ([A-Z_]+)", message)
    stage_name = stage[1] if stage else spoken_stage[1] if spoken_stage else None
    cause, cause_status, component, severity = "Exception detail/traceback was not retained; exact cause cannot be established from this line.", "NOT_ESTABLISHED", "pipeline", "High"
    if "hashlib" in message and "file_digest" in message:
        error_type, component = "AttributeError", "runtime compatibility"
        cause = "Python 3.10 lacks hashlib.file_digest. Source/preparation checksums now have a streaming SHA256 fallback; isolated Python 3.10 regression passed."
        cause_status = "ISOLATED_VERIFIED_CURRENT_IMPLEMENTATION"
    elif code == "SOURCE_RETRY_PENDING":
        stage_name, component = "source preparation", "pipeline logic"
        cause = "Canonical fingerprint regression reproduced changing marshal CodeType reference flags within chunk_prepare, falsely rejecting unchanged source. Separate empty-preview gate also required correction. Historical line alone does not prove every occurrence has the same cause."
        cause_status = "REPRODUCED_DEFECT_RUNTIME_RELOAD_PENDING"
    elif error_type == "PacingReviewRejected":
        stage_name, component, severity = stage_name or "TTS pacing", "model / timeline constraints", "Medium"
        cause = "A proposed shorter utterance did not pass independent semantic/naturalness review. Rejection protects meaning; inspect source, full dialogue and measured speech budget per segment."
        cause_status = "EXPLAINED_GUARD_NOT_A_PASS"
    elif "Máy chủ video phản hồi quá chậm" in message:
        stage_name, component = "download", "network / remote media"
        cause = "Downloader reported repeated slow connections and retained a partial download. The log does not prove the remote service is the root cause."
        cause_status = "OBSERVED_FAILURE_PARTIAL_RETAINED"
    elif "VieNeu" in message:
        stage_name, component = "preflight", "model / local runtime"
        cause = "Selected VieNeu model/runtime was deliberately unavailable in an offline regression fixture."
        cause_status = "TEST_FIXTURE" if origin == "intentional_test_fault" else "NOT_ESTABLISHED"
    elif "REVIEW" in code:
        stage_name, component = stage_name or "review", "API / model / review pipeline"
        cause = "Review failed and did not authorize PASS. Transport, schema or evidence failure needs its actual diagnostic/trace; historical generic error type is insufficient."
    elif "PROJECT" in code or error_type == "ProjectCapacityError":
        component, stage_name = "persistence", stage_name or "checkpoint"
    elif code == "EXPORT_FAILED":
        component, stage_name = "media export", stage_name or "export"
    if origin == "intentional_test_fault":
        severity = "Low"
    frames = []
    for line in record["trace"]:
        match = re.search(r'File "([^"\n]+)", line (\d+), in ([^\s]+)', line)
        if match:
            frames.append({"file": Path(match[1]).name, "line": int(match[2]), "function": match[3]})
    category = re.search(r"category=([a-z_]+)", message)
    return {
        "timestamp": record["timestamp"], "severity": severity, "stage": stage_name,
        "run_id": task, "project_id": task, "segment_id": segment_id,
        "event_code": code, "error_type": error_type, "category": category[1] if category else None,
        "component": component, "origin": origin,
        "origin_evidence": proof if is_fixture else "unittest.mock traceback" if mock_trace else "Production-style task ID; no matched test fixture",
        "root_cause": cause, "root_cause_status": cause_status,
        "fix_applied": "See fixes_applied.md; historical occurrence is not current task acceptance",
        "test_result": "See linked test_results.json; no runtime recovery inferred",
        "recovery_status": "INTENTIONAL_TEST_FAILURE" if origin == "intentional_test_fault" else "CURRENT_CHECKPOINT_AND_MEDIA_VERIFICATION_REQUIRED",
        "traceback_frames": frames, "traceback_available": bool(frames),
        "source_log": str(ROOT / "workspace" / "logs" / "errors.log"), "source_line": record["line"],
        "raw_log_exported": False,
    }


def main():
    log = ROOT / "workspace" / "logs" / "errors.log"
    records = [classify(record) for record in split_records(log.read_text(encoding="utf-8"))]
    runtime = [row for row in records if row["origin"] == "historical_runtime_failure"]
    report = {
        "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "log_path": str(log), "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "counts": {"all_error_occurrences": len(records), "historical_runtime_occurrences": len(runtime),
                   "intentional_test_fault_occurrences": len(records) - len(runtime),
                   "runtime_by_type": dict(collections.Counter(row["error_type"] for row in runtime)),
                   "runtime_by_task": dict(collections.Counter(row["run_id"] for row in runtime))},
        "scope": "Complete existing errors.log, including multiline traceback frames. Current live status is reported separately.",
        "limitation": "Old unrecorded exception detail, retry histories and deleted worker state cannot be invented. Source pointers retain access to original evidence; no raw provider data is published.",
        "records": records,
    }
    path = HERE / "historical_log_audit.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
