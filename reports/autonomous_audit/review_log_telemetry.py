"""Read bounded existing structured logs; never equate provider replies with review success."""
import datetime as dt
from pathlib import Path
import re


def review_telemetry(log_path, task_id, since=None, total_segments=None, summary=None):
    result = {"source": "existing structured ai.log", "task_id": task_id,
              "address_context": {"read_segment_count": 0, "uncertain_segment_count": 0,
                                  "completed_subreads": 0, "active_segment_ids": [], "last_read_at": None,
                                  "eta_seconds": None, "total_required_address_segments": None},
              "semantic_review": {"completed_segment_count": None, "status": None,
                                  "checkpoint_batches_seen": 0, "provider_response_is_semantic_completion": False},
              "provider": {"requests_seen": 0, "completed_seen": 0, "active_request_count": 0,
                           "request_durations_seconds": [], "active_phase": None},
              "manifest_total_segments": total_segments,
              "note": "Address READ confirms a structured role/context reading, including uncertain IDs; it does not complete semantic/source/audio review. Total address work and stage ETA are unknown."}
    if not task_id:
        result["available"] = False
        return result
    try:
        baseline = dt.datetime.fromisoformat(since).timestamp() if since else 0
    except (ValueError, TypeError):
        baseline = 0
    requests, read_ids, uncertain_ids, completed_sets = {}, set(), set(), set()
    active_address = []
    try:
        path = Path(log_path)
        with path.open("rb") as stream:
            offset = max(0, path.stat().st_size - 8 * 1024 * 1024)
            stream.seek(offset)
            if offset:
                stream.readline()
            text = stream.read(8 * 1024 * 1024).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "run_id=" + task_id + " " not in line:
                continue
            match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
            if not match or dt.datetime.fromisoformat(match.group(1)).timestamp() < baseline:
                continue
            observed = dt.datetime.fromisoformat(match.group(1)).astimezone().isoformat(timespec="seconds")
            identifiers = re.search(r"segment_ids=\[([^\]]*)\]", line)
            ids = [int(value) for value in re.findall(r"\d+", identifiers.group(1))] if identifiers else []
            if "ADDRESS_CONTEXT_REQUEST " in line:
                active_address = ids
                result["address_context"]["active_segment_ids"] = ids
            elif "ADDRESS_CONTEXT_READ " in line:
                read_ids.update(ids)
                completed_sets.add(tuple(ids))
                unknown = re.search(r"uncertain_ids=\[([^\]]*)\]", line)
                uncertain_ids.update(int(value) for value in re.findall(r"\d+", unknown.group(1))) if unknown else None
                result["address_context"]["last_read_at"] = observed
                if ids == active_address:
                    active_address = []
                    result["address_context"]["active_segment_ids"] = []
            elif "REVIEW_CHECKPOINT_HIT " in line:
                result["semantic_review"]["checkpoint_batches_seen"] += 1
            request = re.search(r"request_id=([a-zA-Z0-9_-]+)", line)
            if request and "PROVIDER_REQUEST " in line:
                requests[request.group(1)] = {"completed": False, "phase": "address_context" if active_address else "unattributed_review_provider_request"}
            elif request and "PROVIDER_COMPLETED " in line:
                item = requests.get(request.group(1))
                if item:
                    item["completed"] = True
                    elapsed = re.search(r"\b(?:total_elapsed_ms|elapsed_ms)=(\d+)", line)
                    if elapsed:
                        item["duration"] = int(elapsed.group(1)) / 1000
            elif request and any(token in line for token in ("PROVIDER_FAILED ", "PROVIDER_CANCELLED ")):
                if request.group(1) in requests:
                    requests[request.group(1)]["completed"] = True
                    requests[request.group(1)]["failed"] = True
        result["address_context"].update(read_segment_count=len(read_ids), uncertain_segment_count=len(uncertain_ids), completed_subreads=len(completed_sets))
        active = [item for item in requests.values() if not item["completed"]]
        result["provider"].update(requests_seen=len(requests), completed_seen=sum(item["completed"] and not item.get("failed") for item in requests.values()),
                                  active_request_count=len(active), request_durations_seconds=[item["duration"] for item in requests.values() if "duration" in item],
                                  active_phase=active[-1]["phase"] if active else None)
        if isinstance(summary, dict):
            result["semantic_review"]["status"] = summary.get("status")
            checked = summary.get("checked")
            if type(checked) is int and checked >= 0:
                result["semantic_review"]["completed_segment_count"] = checked
                result["semantic_review"]["count_source"] = "current review_summary.checked; checked is not equivalent to accepted"
            result["semantic_review"]["accepted_segments"] = sum(summary.get(key, 0) for key in ("verified", "corrected") if type(summary.get(key, 0)) is int)
        result["available"] = True
    except (OSError, ValueError, TypeError) as error:
        result.update(available=False, observer_error=type(error).__name__)
    return result
