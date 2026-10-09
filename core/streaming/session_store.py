"""Durable editable projects. Runtime tasks, engines and credentials never enter a manifest."""
import json
import hashlib
import base64
import zlib
import math
import numbers
import os
import re
import subprocess
import tempfile
import threading
import time
import wave
import weakref
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode

from config import settings
from core.streaming.audio_cache import resolve_dub_timing
from core.streaming.output_gate import final_output_metadata, missing_speech_message

VERSION = 1
MAX_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_ROWS = 50000
COMPRESS_AFTER_BYTES = 1024 * 1024


class ProjectCapacityError(ValueError):
    """Stop bounded execution before durable project capacity is exhausted."""


def _encode_project(payload):
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_EXPANDED_BYTES:
        raise ProjectCapacityError("Dự án đạt giới hạn dữ liệu lưu; phần đã lưu vẫn giữ nguyên. Hãy dùng video ngắn hơn.")
    if len(raw) <= COMPRESS_AFTER_BYTES:
        return raw.decode("utf-8")
    envelope = {"storage": "zlib-project-1", "raw_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "payload": base64.b64encode(zlib.compress(raw, 6)).decode("ascii")}
    encoded = json.dumps(envelope, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ProjectCapacityError("Dự án đạt giới hạn dữ liệu lưu; phần đã lưu vẫn giữ nguyên. Hãy dùng video ngắn hơn.")
    return encoded


def _decode_project(encoded):
    record = json.loads(encoded)
    if not isinstance(record, dict) or record.get("storage") != "zlib-project-1":
        return record
    count = record.get("raw_bytes")
    if type(count) is not int or not 0 < count <= MAX_EXPANDED_BYTES:
        raise ValueError("Dung lượng dự án nén không hợp lệ.")
    try:
        compressed = base64.b64decode(record["payload"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, count + 1)
        if (len(raw) != count or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data
                or hashlib.sha256(raw).hexdigest() != record.get("sha256")):
            raise ValueError("Dữ liệu dự án nén thiếu hoặc hỏng.")
        return json.loads(raw)
    except (KeyError, TypeError, zlib.error) as error:
        raise ValueError("Dữ liệu dự án nén không hợp lệ.") from error
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
SEGMENT_FIELDS = frozenset((
    "id start end duration status text_zh emotion literal_vi natural_vi final_vi tts_duration speed_ratio "
    "audio_path failed_stage error revision source_method translation_provider translation_model evidence_mode "
    "needs_review review_reason asr_text verification confirmed_silence subtitle_cues subtitle_timing_source "
    "speech_start speech_end asr_pretranscribed dub_start dub_end dub_tail_limit timing_issue"
    " speaker_id speaker_evidence utterance_id utterance_evidence source_asr_row_id source_asr_start source_asr_end source_piece_index source_piece_count"
).split())
SESSION_FIELDS = frozenset((
    "initial_buffer_seconds voice tts_engine_name asr_engine_name visual_translation total_duration video_size "
    "screen_texts caption_style caption_style_revision caption_output_outdated translation_sources review_summary "
    "suppression_stats current_playback_time warnings rolling_context initialized auto_export_result "
    "_visual_incremental_started _visual_completed_seconds _visual_prepass_complete"
    " translation_mode preview_seconds _chunked_source_started _source_prepared_seconds _preview_ready"
    " _chunked_source_identity _visual_context_summary"
    " _chunk_jobs _visual_scanned_seconds _legacy_visual_prefix"
).split())
PATH_FIELDS = ("video_path", "ref_audio", "raw_audio_16k", "bgm_audio_path")
META_FIELDS = frozenset((
    "status reason provider model evidence_mode evidence_ids evidence id start end text_zh confidence bbox "
    "source_supported semantic_verified second_pass_status translation_changed before_pacing pacing "
    "source text verified meaning_preserved natural accepted candidate measured_seconds max_speed "
    "kind text_vi mask_only needs_review review_reason source_method text word words "
    "checked corrected unresolved manual incomplete version method speech_id duration ratio "
    "background_color text_color position blur_original suppression_level_db throughput_rtf "
    "zh vi source_evidence_ids speaker theme terms pronouns name src tgt note "
    "address_context address_context_sources address_stale_source_ids address_applicable address_neutral_faithful address_verified address_reason address_preserved self_address listener_address self_uncertain listener_uncertain address_uses uncertain quote "
    "source_accepted source_scope_conflict full_text_zh source_scope_ids source_scope_window source_scope_ambiguous "
    "reviewed_address_context turn_check ambiguous_roles is_focus speaker_id addressee_id source_needs_review source_truncated translation_is_draft "
    "tts_seconds slot_seconds fit_ratio semantic_status acoustic_status source_region_verified "
    "reference_zh reference_vi equivalent different_source same_meaning text_preserved "
    "mode input_duration output_duration sample_rate channels elapsed_seconds rtf "
    "diagnostic audio_evidence audio_consensus audio_audit_status review_gate_revision"
    " state attempts input_hash error_code updated_at"
    " scope_id confirmation_id code required_seconds available_seconds max_speed remedy"
    " speaker_evidence utterance_id utterance_evidence source_asr_row_id source_asr_start source_asr_end source_piece_index source_piece_count"
).split())
DIAGNOSTIC_STAGES = frozenset((
    "semantic_request semantic_schema semantic_second_pass ocr_evidence audio_evidence audio_semantic_review"
).split())
DIAGNOSTIC_CODES = frozenset((
    "invalid_response provider_configuration provider_model provider_timeout asr_timeout "
    "asr_unavailable runtime_unavailable asr_failed provider_failed"
).split())
_OUTPUT_CHECKS = OrderedDict()
_OUTPUT_CHECK_LOCK = threading.Lock()
_SAVE_REPLACE_LOCK = threading.RLock()
_RESTORE_LOCKS_GUARD = threading.Lock()
_RESTORE_LOCKS = weakref.WeakValueDictionary()
OUTPUT_FAILURE_WARNING = ("Chưa xác minh được video đã xuất (có thể thiếu, hỏng hoặc kiểm tra hết thời gian); "
                          "bản dịch và giọng đọc vẫn được giữ. Hãy xuất MP4 lại.")
PERSISTENCE_FAILURE_WARNINGS = frozenset((
    "Không lưu được dự án xuống đĩa; giữ cửa sổ mở và kiểm tra dung lượng/quyền ghi.",
    "Không lưu được phiên xuống ổ đĩa; giữ ứng dụng mở và kiểm tra dung lượng/quyền ghi.",
))


def _project_path(task_id):
    if not isinstance(task_id, str) or not ID_PATTERN.fullmatch(task_id):
        raise ValueError("Mã dự án không hợp lệ.")
    root = settings.BASE_DIR / "workspace" / "projects"
    if root.is_symlink():
        raise ValueError("Thư mục dự án không được là liên kết.")
    return root / f"{task_id}.json"


def _secrets():
    model_fields = getattr(type(settings), "model_fields", {})
    return [value for key in model_fields
            if any(word in key.upper() for word in ("API_KEY", "TOKEN", "PASSWORD"))
            and isinstance(value := getattr(settings, key, None), str) and len(value) >= 4]


def _clean_diagnostic(value, *, depth):
    """Persist the reviewer's fixed diagnostic schema, never arbitrary errors."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, allowed in (("stage", DIAGNOSTIC_STAGES), ("code", DIAGNOSTIC_CODES)):
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    run_id = value.get("run_id")
    if isinstance(run_id, str) and (run_id == "[redacted]" or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id)):
        result["run_id"] = _clean(run_id, depth=depth + 1)
    segment_ids = value.get("segment_ids")
    if (isinstance(segment_ids, list) and len(segment_ids) <= 10000
            and all(type(item) is int and item >= 0 for item in segment_ids)):
        result["segment_ids"] = list(segment_ids)
    return result


def _clean_audio_evidence(value, *, depth):
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError("Dữ liệu đối chiếu âm thanh không hợp lệ.")
    result = []
    for item in value:
        if (not isinstance(item, dict) or item.get("engine") not in ("sensevoice", "faster-whisper-small")
                or not isinstance(item.get("text_zh"), str)
                or not _finite(item.get("start")) or not _finite(item.get("end"))
                or item["end"] <= item["start"]):
            continue
        result.append({"engine": item["engine"], "text_zh": _clean(item["text_zh"], depth=depth + 1),
                       "start": item["start"], "end": item["end"]})
    return result


def _clean_address_context_sources(value, *, depth):
    """Keep the source snapshot keyed by segment ID, never arbitrary metadata."""
    if not isinstance(value, dict) or len(value) > 10000:
        raise ValueError("Ảnh chụp nguồn xưng hô không hợp lệ.")
    result = {}
    for key, item in value.items():
        # JSON object keys are strings after persistence. Reject booleans,
        # negative IDs and non-numeric provider payload keys.
        if not isinstance(key, str) or not re.fullmatch(r"[0-9]{1,10}", key) or not isinstance(item, str):
            raise ValueError("Ảnh chụp nguồn xưng hô chứa ID không hợp lệ.")
        result[key] = _clean(item, depth=depth + 1)
    return result


def _clean_address_uses(value, *, depth):
    """Keep only candidate terms and roles, never arbitrary provider metadata."""
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError("Các cách xưng hô đã dùng không hợp lệ.")
    result = []
    for item in value:
        if (not isinstance(item, dict) or not isinstance(item.get("term"), str)
                or not item["term"].strip() or len(item["term"]) > 100
                or item.get("role") not in ("self", "listener")):
            raise ValueError("Cách xưng hô đã dùng thiếu từ hoặc vai hợp lệ.")
        result.append({"term": _clean(item["term"], depth=depth + 1), "role": item["role"]})
    return result


def _clean_source_scope_ids(value):
    """Persist only non-negative dialogue row IDs used to scope OCR evidence."""
    if (not isinstance(value, list) or len(value) > MAX_ROWS
            or any(type(item) is not int or item < 0 for item in value)):
        raise ValueError("Danh sách câu nguồn đối chiếu không hợp lệ.")
    return list(value)


def _clean_source_scope_window(value):
    """Persist a finite, ordered ownership interval and no arbitrary fields."""
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError("Khoảng thời gian câu nguồn đối chiếu không hợp lệ.")
    start, end = value.get("start"), value.get("end")
    if not _finite(start) or not _finite(end) or end <= start or end > 86400:
        raise ValueError("Khoảng thời gian câu nguồn đối chiếu không hợp lệ.")
    return {"start": float(start), "end": float(end)}


def _identity(value):
    if value is None:
        return None
    if ((type(value) is int and value >= 0) or (isinstance(value, str)
            and value.strip() and len(value) <= 200 and "\x00" not in value)):
        return _clean(value)
    raise ValueError("Danh tính nguồn thoại không hợp lệ.")


def _clean_identity_evidence(value, kind):
    """Incomplete evidence stays unverified; only recognized proof can claim authority."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Dẫn chứng danh tính nguồn thoại không hợp lệ.")
    identity_key = kind + "_id"
    allowed = {identity_key, "verified", "method", "confidence", "model", "scope_id",
               "confirmation_id", "reason", "start", "end"}
    result = {key: _clean(item) for key, item in value.items() if key in allowed}
    if identity_key in result:
        result[identity_key] = _identity(result[identity_key])
    if "verified" in result and result["verified"] is not None and type(result["verified"]) is not bool:
        raise ValueError("Kết luận danh tính nguồn thoại phải là boolean.")
    for key in ("method", "model", "scope_id", "confirmation_id", "reason"):
        if key in result and result[key] is not None and (not isinstance(result[key], str)
                or not result[key].strip() or len(result[key]) > (2000 if key == "reason" else 200)):
            raise ValueError("Thông tin dẫn chứng nguồn thoại không hợp lệ.")
    if "confidence" in result and result["confidence"] is not None and (
            not _finite(result["confidence"]) or result["confidence"] > 1):
        raise ValueError("Độ tin cậy danh tính nguồn thoại không hợp lệ.")
    for key in ("start", "end"):
        if key in result and result[key] is not None and (not _finite(result[key]) or result[key] > 86400):
            raise ValueError("Mốc dẫn chứng nguồn thoại không hợp lệ.")
    if result.get("start") is not None and result.get("end") is not None and result["end"] <= result["start"]:
        raise ValueError("Khoảng dẫn chứng nguồn thoại không hợp lệ.")
    if result.get("verified") is True:
        if (result.get(identity_key) is None or not result.get("scope_id")
                or result.get("method") not in {"audio_diarization", "user_confirmation"}):
            raise ValueError("Danh tính được xác nhận thiếu dẫn chứng âm thanh hoặc người dùng.")
        if result["method"] == "audio_diarization" and (result.get("confidence") is None or not result.get("model")):
            raise ValueError("Dẫn chứng phân biệt giọng thiếu model hoặc độ tin cậy.")
        if result["method"] == "user_confirmation" and not result.get("confirmation_id"):
            raise ValueError("Dẫn chứng người dùng thiếu mã xác nhận.")
    return result


def _clean_timing_issue(value):
    if value is None:
        return None
    expected = {"code", "required_seconds", "available_seconds", "max_speed", "remedy"}
    if (not isinstance(value, dict) or set(value) != expected or value["code"] != "TIMING_CONFLICT"
            or not _finite(value["required_seconds"], .000001) or value["required_seconds"] > 86400
            or not _finite(value["available_seconds"]) or value["available_seconds"] > 86400
            or not _finite(value["max_speed"], 1) or value["max_speed"] > 3
            or not isinstance(value["remedy"], str) or not value["remedy"].strip()
            or len(value["remedy"]) > 2000 or "\x00" in value["remedy"]):
        raise ValueError("Thông tin xung đột thời lượng giọng đọc không hợp lệ.")
    return {**value, "remedy": _clean(value["remedy"])}


def _clean_segment_field(key, value):
    if key == "timing_issue":
        return _clean_timing_issue(value)
    if key in {"speaker_evidence", "utterance_evidence"}:
        return _clean_identity_evidence(value, key.split("_", 1)[0])
    return _clean(value)


def _validate_source_metadata(row):
    for kind in ("speaker", "utterance"):
        identity = _identity(row.get(kind + "_id"))
        evidence = _clean_identity_evidence(row.get(kind + "_evidence"), kind)
        if evidence and evidence.get("verified") is True and evidence.get(kind + "_id") != identity:
            raise ValueError("Dẫn chứng đã xác nhận không khớp danh tính câu thoại.")
    _identity(row.get("source_asr_row_id"))
    for key in ("source_asr_start", "source_asr_end"):
        if row.get(key) is not None and (not _finite(row[key]) or row[key] > 86400):
            raise ValueError("Mốc ASR nguồn không hợp lệ.")
    if row.get("source_asr_start") is not None and row.get("source_asr_end") is not None:
        if (not row["source_asr_start"] < row["source_asr_end"]
                or row["source_asr_start"] > row["start"] + .02
                or row["source_asr_end"] < row["end"] - .02):
            raise ValueError("Mốc câu thoại vượt phạm vi ASR nguồn.")
    for key in ("source_piece_index", "source_piece_count"):
        if row.get(key) is not None and (type(row[key]) is not int or row[key] < (1 if key.endswith("count") else 0)
                or row[key] > MAX_ROWS):
            raise ValueError("Vị trí mảnh ASR nguồn không hợp lệ.")
    if row.get("source_piece_index") is not None and row.get("source_piece_count") is not None:
        if row["source_piece_index"] >= row["source_piece_count"]:
            raise ValueError("Chỉ số mảnh ASR vượt số mảnh đã nhận dạng.")


def _clean(value, *, depth=0):
    if depth > 12:
        raise ValueError("Dữ liệu dự án lồng quá sâu.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, numbers.Real):
        if not math.isfinite(value):
            raise ValueError("Dự án chứa mốc số không hợp lệ.")
        return int(value) if isinstance(value, numbers.Integral) else float(value)
    if isinstance(value, str):
        if len(value) > 100_000 or "\x00" in value:
            raise ValueError("Nội dung dự án quá dài hoặc không hợp lệ.")
        for secret in _secrets():
            value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ROWS:
            raise ProjectCapacityError("Dự án đạt giới hạn số phần tử lưu; phần đã lưu vẫn giữ nguyên. Hãy dùng video ngắn hơn.")
        return [_clean(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key not in META_FIELDS:
                continue
            if key == "diagnostic":
                result[key] = _clean_diagnostic(item, depth=depth + 1)
            elif key == "audio_evidence":
                result[key] = _clean_audio_evidence(item, depth=depth + 1)
            elif key == "address_context_sources":
                result[key] = _clean_address_context_sources(item, depth=depth + 1)
            elif key == "address_uses":
                result[key] = _clean_address_uses(item, depth=depth + 1)
            elif key == "source_scope_ids":
                result[key] = _clean_source_scope_ids(item)
            elif key == "source_scope_window":
                result[key] = _clean_source_scope_window(item)
            elif key in {"speaker_evidence", "utterance_evidence"}:
                result[key] = _clean_identity_evidence(item, key.split("_", 1)[0])
            elif key == "full_text_zh":
                if not isinstance(item, str) or len(item) > 100_000 or "\x00" in item:
                    raise ValueError("Văn bản nguồn đối chiếu không hợp lệ.")
                result[key] = item
            elif key in ("source_accepted", "source_scope_conflict", "source_scope_ambiguous"):
                if type(item) is not bool:
                    raise ValueError("Trạng thái đối chiếu phạm vi nguồn phải là boolean.")
                result[key] = item
            elif key == "ambiguous_roles":
                if (not isinstance(item, list) or len(item) > 2
                        or any(role not in ("self", "listener") for role in item)):
                    raise ValueError("Các vai phân lượt chưa rõ không hợp lệ.")
                result[key] = list(item)
            elif key in ("self_uncertain", "listener_uncertain"):
                if type(item) is not bool:
                    raise ValueError("Kết luận từng vai xưng hô phải là boolean.")
                result[key] = item
            elif key == "audio_consensus":
                if type(item) is bool:
                    result[key] = item
            elif key == "audio_audit_status":
                if item == "failed":
                    result[key] = item
            else:
                result[key] = _clean(item, depth=depth + 1)
        # Reload must retain the same conservative gate as a fresh source
        # reading, even when contradictory confidence fields were saved.
        turn = result.get("turn_check")
        if isinstance(turn, dict):
            for role in turn.get("ambiguous_roles", []):
                result[f"{role}_uncertain"] = True
                result["uncertain"] = True
        return result
    raise ValueError("Dự án chứa dữ liệu không thể lưu.")


def _local_path(value, *, generated=False):
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith(("\\\\", "//")):
        raise ValueError("Đường dẫn phương tiện không hợp lệ.")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
            or (os.name == "nt" and ":" in value[2:])):
        raise ValueError("Đường dẫn phương tiện không an toàn.")
    resolved = path.resolve()
    if generated and not resolved.is_relative_to((settings.BASE_DIR / "workspace" / "cache").resolve()):
        raise ValueError("Âm thanh của dự án phải nằm trong cache ứng dụng.")
    return str(resolved)


def _source_url(value):
    if not isinstance(value, str) or not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return None
    query = [(key, val) for key, val in parse_qsl(parsed.query)
             if not any(word in key.lower() for word in ("token", "key", "password", "auth", "cookie"))]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _filename(value):
    if not value:
        return ""
    if not isinstance(value, str) or Path(value).name != value or "/" in value or "\\" in value or ":" in value:
        raise ValueError("Tên video kết quả không hợp lệ.")
    return value


def save_session(session):
    target = _project_path(session.task_id)
    fields = {key: _clean(getattr(session, key)) for key in SESSION_FIELDS if hasattr(session, key)}
    for key in PATH_FIELDS:
        path = getattr(session, key, None)
        fields[key] = _local_path(str(Path(path).resolve()), generated=key in ("raw_audio_16k", "bgm_audio_path")) if path else None
    fields["source_url"] = _source_url(getattr(session, "source_url", None))
    # Output validation is transient: a history scan must never write its
    # current probe warning back into the project manifest.
    if "warnings" in fields:
        fields["warnings"] = [warning for warning in fields["warnings"]
                              if warning != OUTPUT_FAILURE_WARNING and warning not in PERSISTENCE_FAILURE_WARNINGS]
    fields["output_filename"] = _filename(getattr(session, "output_filename", ""))
    review_url = getattr(session, "output_review_url", "")
    fields["review_filename"] = _filename(review_url.rsplit("/", 1)[-1]) if review_url else ""
    rows = []
    for segment in session.segments.values():
        row = {key: _clean_segment_field(key, getattr(segment, key)) for key in SEGMENT_FIELDS if hasattr(segment, key)}
        row["audio_path"] = _local_path(row.get("audio_path"), generated=True)
        rows.append(row)
    inferred_duration = max((float(row.get("end", 0)) for row in rows), default=0.0)
    if not fields.get("total_duration") and inferred_duration:
        fields["total_duration"] = inferred_duration
    payload = {"version": VERSION, "task_id": session.task_id, "updated_at": time.time(),
               "state": "STOPPED" if session.is_stopped or getattr(session, "_restored_interrupted", False) else "FAILED" if session.error else
               "RUNNING" if session.is_running or session.is_editing else "READY",
               "session": fields, "segments": rows}
    _validate(payload, session.task_id)
    encoded = _encode_project(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError("Tệp dự án không được là liên kết.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows can briefly deny replacing a manifest that another process
        # has opened without delete sharing (Defender/indexing and a concurrent
        # history read are common examples). Retry only the documented sharing
        # errors, while serializing in-process replacements so two saves from
        # the same runtime cannot contend for the destination.
        _replace_project_with_retry(temporary, target)
        # Only a committed atomic save clears the live disk-failure warning.
        # A failed write must retain it; unrelated review/media warnings stay.
        session.warnings[:] = [warning for warning in session.warnings
                               if warning not in PERSISTENCE_FAILURE_WARNINGS]
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return target


def _replace_project_with_retry(temporary, target):
    """Atomically replace a project, bounded to transient Windows locks."""
    delays = (0.05, 0.1, 0.2, 0.4)
    with _SAVE_REPLACE_LOCK:
        for attempt, delay in enumerate(delays):
            try:
                temporary.replace(target)
                return
            except PermissionError as error:
                # ERROR_ACCESS_DENIED (5), ERROR_SHARING_VIOLATION (32), and
                # ERROR_LOCK_VIOLATION (33) are the only retryable Windows
                # replace failures. Permanent ACL/path failures must surface.
                if (os.name != "nt"
                        or getattr(error, "winerror", None) not in {5, 32, 33}
                        or attempt == len(delays) - 1):
                    raise
                time.sleep(delay)


def _finite(value, minimum=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


def _validate(data, task_id):
    if (not isinstance(data, dict) or set(data) != {"version", "task_id", "updated_at", "state", "session", "segments"}
            or data["version"] != VERSION or data["task_id"] != task_id or not _finite(data["updated_at"])
            or data["state"] not in {"STOPPED", "FAILED", "RUNNING", "READY"}):
        raise ValueError("Tệp dự án không đúng định dạng.")
    fields, rows = data["session"], data["segments"]
    if not isinstance(fields, dict) or set(fields) - (SESSION_FIELDS | set(PATH_FIELDS) | {"source_url", "output_filename", "review_filename"}):
        raise ValueError("Cấu hình dự án không hợp lệ.")
    for key in PATH_FIELDS:
        fields[key] = _local_path(fields.get(key), generated=key in ("raw_audio_16k", "bgm_audio_path"))
    for key in ("output_filename", "review_filename"):
        fields[key] = _filename(fields.get(key))
    fields["source_url"] = _source_url(fields.get("source_url"))
    duration = fields.get("total_duration", 0)
    if not _finite(duration) or duration > 86400:
        raise ValueError("Thời lượng dự án không hợp lệ.")
    for key in ("voice", "tts_engine_name", "asr_engine_name"):
        if not isinstance(fields.get(key), str) or not fields[key] or len(fields[key]) > 200:
            raise ValueError("Cấu hình giọng hoặc nhận diện không hợp lệ.")
    for key in ("visual_translation", "initialized", "caption_output_outdated", "auto_export_result",
                "_visual_incremental_started", "_visual_prepass_complete", "_chunked_source_started", "_preview_ready"):
        if key in fields and type(fields[key]) is not bool:
            raise ValueError("Trạng thái dự án không hợp lệ.")
    completed_visual = fields.get("_visual_completed_seconds", 0)
    if not _finite(completed_visual) or completed_visual > duration + .1:
        raise ValueError("Mốc phân tích video đã lưu không hợp lệ.")
    from core.streaming.recovery import validate_records
    validate_records(fields.get("_chunk_jobs", []), duration)
    for key in ("_visual_scanned_seconds", "_legacy_visual_prefix"):
        if not _finite(fields.get(key, 0)) or fields.get(key, 0) > duration + .1:
            raise ValueError("Mốc xử lý đoạn đã lưu không hợp lệ.")
    if fields.get("translation_mode", "full") not in {"preview", "full"}:
        raise ValueError("Chế độ dịch đã lưu không hợp lệ.")
    if not _finite(fields.get("preview_seconds", 24), .01) or fields.get("preview_seconds", 24) > 120:
        raise ValueError("Thời lượng xem trước không hợp lệ.")
    if not _finite(fields.get("_source_prepared_seconds", 0)) or fields.get("_source_prepared_seconds", 0) > duration + .1:
        raise ValueError("Mốc chuẩn bị video đã lưu không hợp lệ.")
    for key in ("screen_texts", "translation_sources", "warnings", "rolling_context"):
        if not isinstance(fields.get(key, []), list):
            raise ValueError("Dữ liệu dự án không hợp lệ.")
    for key in ("caption_style", "review_summary", "suppression_stats"):
        if not isinstance(fields.get(key, {}), dict):
            raise ValueError("Siêu dữ liệu dự án không hợp lệ.")
    style = fields.get("caption_style", {})
    if (set(style) - {"background_color", "text_color", "position", "blur_original"}
            or style.get("position", "auto") not in {"auto", "top", "middle", "bottom"}
            or type(style.get("blur_original", False)) is not bool
            or any(not isinstance(style[key], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", style[key])
                   for key in ("text_color", "background_color") if key in style and not (key == "background_color" and style[key] is None))):
        raise ValueError("Kiểu phụ đề đã lưu không hợp lệ.")
    revision = fields.get("caption_style_revision", 0)
    if type(revision) is not int or revision < 0:
        raise ValueError("Phiên bản phụ đề không hợp lệ.")
    summary = fields.get("review_summary", {})
    if summary.get("status") not in {None, "running", "completed", "failed", "incomplete"}:
        raise ValueError("Trạng thái kiểm tra bản dịch không hợp lệ.")
    for key in ("checked", "verified", "corrected", "unresolved", "manual", "incomplete"):
        if key in summary and (type(summary[key]) is not int or summary[key] < 0):
            raise ValueError("Số câu đã kiểm tra không hợp lệ.")
    for screen in fields.get("screen_texts", []):
        if (not isinstance(screen, dict) or not _finite(screen.get("start")) or not _finite(screen.get("end"))
                or not screen["start"] < screen["end"] <= duration + .1):
            raise ValueError("Vùng chữ trên hình không hợp lệ.")
        box = screen.get("bbox")
        if box is not None and (not isinstance(box, list) or len(box) != 4 or any(not _finite(v) for v in box)):
            raise ValueError("Vị trí chữ trên hình không hợp lệ.")
    for source in fields.get("translation_sources", []):
        if not isinstance(source, dict) or not isinstance(source.get("provider"), str):
            raise ValueError("Nguồn bản dịch đã lưu không hợp lệ.")
    if not _finite(fields.get("current_playback_time", 0)) or not _finite(fields.get("initial_buffer_seconds", 0)):
        raise ValueError("Mốc phát video không hợp lệ.")
    size = fields.get("video_size", [1080, 1920])
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or not 0 < v <= 16384 for v in size):
        raise ValueError("Kích thước video không hợp lệ.")
    if isinstance(rows, list) and len(rows) > MAX_ROWS:
        raise ProjectCapacityError("Dự án đạt giới hạn số câu lưu; phần đã lưu vẫn giữ nguyên. Hãy dùng video ngắn hơn.")
    if not isinstance(rows, list):
        raise ValueError("Danh sách câu thoại không hợp lệ.")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - SEGMENT_FIELDS or type(row.get("id")) is not int or row["id"] < 0 or row["id"] in ids:
            raise ValueError("Mã câu thoại không hợp lệ.")
        ids.add(row["id"])
        if (not all(_finite(row.get(k)) for k in ("start", "end", "duration"))
                or not row["start"] < row["end"] <= duration + .1
                or abs(row["end"] - row["start"] - row["duration"]) > .02):
            raise ValueError("Thời gian câu thoại không hợp lệ.")
        dub_start, dub_end = resolve_dub_timing(row)
        if row.get("dub_end") is not None and dub_end > duration + 1e-9:
            raise ValueError("Mốc lồng tiếng vượt quá thời lượng video.")
        if row.get("status") not in {"WAITING", "ASR", "TRANSLATING", "TTS", "ALIGNING", "READY", "PLAYED", "FAILED", "NEEDS_REVIEW"}:
            raise ValueError("Trạng thái câu thoại không hợp lệ.")
        for key in ("text_zh", "final_vi", "literal_vi", "natural_vi", "emotion", "asr_text", "subtitle_timing_source"):
            if key in row and not isinstance(row[key], str):
                raise ValueError("Nội dung câu thoại không hợp lệ.")
        if not isinstance(row.get("subtitle_cues", []), list) or not isinstance(row.get("verification") or {}, dict):
            raise ValueError("Mốc phụ đề không hợp lệ.")
        if type(row.get("revision", 0)) is not int or row.get("revision", 0) < 0:
            raise ValueError("Phiên bản câu thoại không hợp lệ.")
        _clean_timing_issue(row.get("timing_issue"))
        _validate_source_metadata(row)
        if row.get("error") is not None and (not isinstance(row["error"], str) or len(row["error"]) > 2000):
            raise ValueError("Lỗi câu thoại đã lưu không hợp lệ.")
        for key in ("needs_review", "confirmed_silence", "asr_pretranscribed"):
            if key in row and type(row[key]) is not bool:
                raise ValueError("Trạng thái câu thoại không hợp lệ.")
        for key in ("tts_duration", "speed_ratio"):
            if key in row and not _finite(row[key]):
                raise ValueError("Thời lượng giọng đọc không hợp lệ.")
        for cue in row.get("subtitle_cues", []):
            if (not isinstance(cue, dict) or not isinstance(cue.get("text"), str)
                    or not _finite(cue.get("start")) or not _finite(cue.get("end"))
                    or not dub_start - .05 <= cue["start"] < cue["end"] <= dub_end + .05):
                raise ValueError("Mốc phụ đề không khớp câu thoại.")
            if not isinstance(cue.get("words", []), list):
                raise ValueError("Mốc từ trong phụ đề không hợp lệ.")
            for word in cue.get("words", []):
                if (not isinstance(word, dict) or not isinstance(word.get("text"), str)
                        or not _finite(word.get("start")) or not _finite(word.get("end"))
                        or not cue["start"] - .05 <= word["start"] <= word["end"] <= cue["end"] + .05):
                    raise ValueError("Mốc từ không khớp phụ đề.")
        row["audio_path"] = _local_path(row.get("audio_path"), generated=True)
        if row["audio_path"]:
            audio = Path(row["audio_path"])
            owner = (settings.BASE_DIR / "workspace" / "cache" / task_id / "segments").resolve()
            if audio.parent != owner or not re.fullmatch(rf"seg_{row['id']}(?:_[0-9a-f]{{32}})?\.wav", audio.name):
                raise ValueError("Âm thanh câu thoại không thuộc dự án này.")
    ordered = sorted(rows, key=lambda item: item["start"])
    for index, row in enumerate(ordered):
        if row.get("dub_tail_limit") is None:
            continue
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        ceiling = fields.get("_source_prepared_seconds", 0)
        if following is not None:
            ceiling = min(ceiling, following["start"])
            if resolve_dub_timing(row)[1] > resolve_dub_timing(following)[0] + 1e-9:
                raise ValueError("Khoảng nghỉ lồng tiếng trùng câu kế tiếp.")
        if (not fields.get("_chunked_source_started")
                or row["dub_tail_limit"] > ceiling + 1e-9):
            raise ValueError("Khoảng nghỉ lồng tiếng chưa được chuẩn bị hoặc trùng câu kế tiếp.")
    # Bounded tree and finite numbers; do not permit manifest-injected credentials.
    for key, value in fields.items():
        fields[key] = _clean(value)
    for row in rows:
        for key, value in row.items():
            row[key] = _clean_segment_field(key, value)
    return data


def _read(task_id):
    path = _project_path(task_id)
    # Keep internal history reads from overlapping the atomic replace.  The
    # lock only covers the short open/read window; validation runs afterwards
    # on the detached string, so large manifests do not block a later save.
    with _SAVE_REPLACE_LOCK:
        if not path.exists():
            raise FileNotFoundError("Không tìm thấy dự án đã lưu.")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("Không tìm thấy tệp dự án hợp lệ.")
        encoded = path.read_text(encoding="utf-8")
    return _validate(_decode_project(encoded), task_id)


def _exists(value):
    try:
        return bool(value and Path(value).is_file() and Path(value).stat().st_size > 0)
    except OSError:
        return False


def _valid_audio(value):
    if not _exists(value):
        return False
    try:
        with wave.open(str(value), "rb") as audio:
            frames, width, channels = audio.getnframes(), audio.getsampwidth(), audio.getnchannels()
            if not frames or not 1 <= channels <= 2 or width not in (1, 2, 3, 4) or audio.getframerate() <= 0:
                return False
            # Check actual last frame too: a surviving header is not valid TTS.
            audio.setpos(frames - 1)
            return len(audio.readframes(1)) == width * channels
    except (OSError, EOFError, ValueError, wave.Error):
        return False


def _valid_row_audio(row, value):
    """Validate an audio file and reject samples wider than its saved dub slot."""
    if not _valid_audio(value):
        return False
    try:
        start, end = resolve_dub_timing(row)
        with wave.open(str(value), "rb") as audio:
            rate = audio.getframerate()
            duration = audio.getnframes() / rate
        return duration <= end - start + (1 / rate) + 1e-9
    except (OSError, EOFError, ValueError, ZeroDivisionError, wave.Error, TypeError):
        return False


def _audio_recovery_issue(row, value):
    """Explain the measured rejection; a Stop marker is not a provider failure."""
    if not _exists(value):
        return "missing", "Thiếu tệp giọng đọc đã lưu; bấm Tiếp tục để tạo lại riêng câu này."
    if not _valid_audio(value):
        return "invalid", "Tệp giọng đọc đã lưu bị hỏng hoặc rỗng; bấm Tiếp tục để tạo lại riêng câu này."
    try:
        start, end = resolve_dub_timing(row)
        with wave.open(str(value), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
        return "timing", (f"Giọng đã lưu dài {duration:.3f} giây, vượt khoảng {end - start:.3f} giây; "
                          "cần căn lại lời/giọng, không cắt từ hoặc chồng tiếng.")
    except (OSError, EOFError, ValueError, ZeroDivisionError, wave.Error, TypeError):
        return "invalid", "Không đọc được thời lượng giọng đã lưu; bấm Tiếp tục để kiểm tra lại câu này."
def _output_identity(path, expected_duration):
    """Cheap change detection; never hash or decode a whole multi-GB export."""
    info = path.stat()
    if path.is_symlink() or not path.is_file() or info.st_size < 32:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for offset in sorted({0, max(0, info.st_size // 2 - 16384), max(0, info.st_size - 32768)}):
            handle.seek(offset)
            digest.update(handle.read(32768))
    return (str(path.resolve()), info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, digest.digest(), expected_duration)


def _complete_mp4_container(path):
    """A faststart moov may still probe successfully after mdat was truncated."""
    size = path.stat().st_size
    offset, seen = 0, set()
    with path.open("rb") as handle:
        for _ in range(4096):
            if offset == size:
                return {b"ftyp", b"moov", b"mdat"}.issubset(seen)
            if size - offset < 8:
                return False
            handle.seek(offset)
            header = handle.read(8)
            if len(header) != 8:
                return False
            length, kind = int.from_bytes(header[:4], "big"), header[4:]
            header_size = 8
            if length == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    return False
                length, header_size = int.from_bytes(extended, "big"), 16
            elif length == 0:
                length = size - offset
            if length < header_size or length > size - offset:
                return False
            if kind in {b"ftyp", b"moov", b"mdat"}:
                if length <= header_size:
                    return False
                seen.add(kind)
            offset += length
    return False


def _probe_saved_output(path, expected_duration):
    if not _complete_mp4_container(path):
        return False
    command = ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
               "-probesize", "4194304", "-analyzeduration", "2000000", "-show_entries",
               "format=duration,format_name:stream=codec_type,width,height", "-of", "json", str(path)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, capture_output=True, timeout=4, creationflags=flags)
    if result.returncode or result.stderr.strip():
        return False
    data = json.loads(result.stdout)
    actual = float(data["format"]["duration"])
    streams = data["streams"]
    if (not math.isfinite(actual) or actual <= 0
            or abs(actual - expected_duration) > .5
            or "mp4" not in data["format"].get("format_name", "").split(",")
            or not any(item.get("codec_type") == "video" and item.get("width", 0) > 0
                       and item.get("height", 0) > 0 for item in streams)
            or not any(item.get("codec_type") == "audio" for item in streams)):
        return False
    # Decode short beginning/tail samples once per file identity. This catches
    # unreadable streams without repeatedly decoding long exports in history.
    for start in sorted({0.0, max(0.0, actual - .5)}):
        command = ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-threads", "1",
                   "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(path),
                   "-t", "0.25", "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"]
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=3, creationflags=flags)
        if result.returncode or result.stderr.strip():
            return False
    return True


def _valid_output(path, expected_duration):
    try:
        if not _finite(expected_duration) or expected_duration <= 0:
            return False
        identity = _output_identity(path, expected_duration)
        if identity is None:
            return False
        with _OUTPUT_CHECK_LOCK:
            cached = _OUTPUT_CHECKS.get(identity)
            if cached and (cached[0] or time.monotonic() - cached[1] < 30):
                _OUTPUT_CHECKS.move_to_end(identity)
                return cached[0]
        # Do not hold the cache mutex while ffprobe/ffmpeg runs: opening one
        # large history entry must not serialize every other history row.
        try:
            valid = _probe_saved_output(path, expected_duration)
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            valid = False
        if identity != _output_identity(path, expected_duration):
            return False
        with _OUTPUT_CHECK_LOCK:
            _OUTPUT_CHECKS[identity] = (valid, time.monotonic())
            _OUTPUT_CHECKS.move_to_end(identity)
            while len(_OUTPUT_CHECKS) > 128:
                _OUTPUT_CHECKS.popitem(last=False)
            return valid
    except (OSError, ValueError, TypeError):
        return False


def _availability(data):
    fields, rows = data["session"], data["segments"]
    output_gate = final_output_metadata(rows)
    missing = []
    pending_download = not fields.get("video_path") and bool(fields.get("source_url"))
    if not _exists(fields.get("video_path")) and not pending_download:
        missing.append("Thiếu video gốc; hãy khôi phục tệp về vị trí đã lưu.")
    if fields.get("bgm_audio_path") and not _exists(fields["bgm_audio_path"]):
        missing.append("Thiếu âm thanh nền đã lưu; bản xem trước chưa phát đầy đủ.")
    absent_audio = [row["id"] for row in rows if row.get("final_vi", "").strip()
                    and row["status"] in {"READY", "PLAYED"} and not _valid_row_audio(row, row.get("audio_path"))]
    if absent_audio:
        missing.append(f"Thiếu âm thanh của {len(absent_audio)} câu; bấm Tiếp tục để tạo lại phần thiếu.")
    ready = bool(fields.get("initialized") and all(row["status"] in {"READY", "PLAYED"} for row in rows)
                 and not absent_audio and not missing)
    visual_incomplete = bool((fields.get("_chunked_source_started") or
                             (fields.get("visual_translation") and fields.get("_visual_incremental_started")))
                             and not fields.get("_visual_prepass_complete"))
    if visual_incomplete:
        ready = False
    preview_ready = bool(fields.get("translation_mode") == "preview" and fields.get("_preview_ready")
                         and fields.get("initialized") and not missing and not absent_audio
                         and fields.get("_visual_completed_seconds", 0) > 0
                         and data["state"] == "READY"
                         and all(row["status"] in {"READY", "PLAYED"} for row in rows
                                 if row["start"] < fields.get("_visual_completed_seconds", 0) - .001))
    preview_can_continue = bool(fields.get("translation_mode") == "preview" and fields.get("_preview_ready")
        and fields.get("initialized") and not missing and not absent_audio
        and fields.get("_visual_completed_seconds", 0) > 0
        and (preview_ready or any(row["status"] in {"READY", "PLAYED"} for row in rows
                                 if row["end"] <= fields.get("_visual_completed_seconds", 0) + .001)))
    if fields.get("review_summary", {}).get("status") in {"failed", "incomplete", "running"}:
        ready = False
    unresolved_speech = bool(ready and output_gate["final_output_blocked"])
    if output_gate["final_output_blocked"]:
        ready = False
    output = fields.get("output_filename", "")
    current_output = bool(output and not fields.get("caption_output_outdated"))
    valid_output = bool(current_output and _valid_output(settings.OUTPUT_DIR / output, fields.get("total_duration", 0)))
    output_warning = OUTPUT_FAILURE_WARNING if current_output and not valid_output else ""
    review_incomplete = fields.get("review_summary", {}).get("status") in {"failed", "incomplete", "running"}
    waiting_export = bool(ready and fields.get("auto_export_result") and not valid_output)
    status = ("FAILED" if output_warning else "PREVIEW_READY" if preview_ready else "PREPARED" if waiting_export or unresolved_speech else "COMPLETED" if ready else
              "STOPPED" if data["state"] == "STOPPED" and not missing else
              "FAILED" if missing or data["state"] == "FAILED" or review_incomplete else "STOPPED")
    message = " ".join(missing)
    if pending_download:
        message = "Tải video chưa xong; mở dự án và bấm Tiếp tục để khôi phục phần đã tải."
    elif preview_ready:
        message = ""
    elif visual_incomplete and not message:
        message = "Dịch video chưa xong; phần đã dịch được giữ. Mở dự án và bấm Tiếp tục để xử lý phần còn lại."
    elif review_incomplete and not message:
        message = "AI kiểm tra lại chưa hoàn tất; mở dự án và bấm AI kiểm tra lại để tiếp tục."
    elif output_gate["final_output_blocked"] and not message:
        message = missing_speech_message(output_gate["missing_speech_ids"])
    elif not ready and not message and fields.get("initialized") and rows and all(
            row["status"] in {"READY", "PLAYED"} or row.get("final_vi", "").strip()
            or row.get("confirmed_silence") or row.get("needs_review") for row in rows):
        message = "Phần tạo giọng chưa xong; bấm Tiếp tục. Video và bản dịch đã kiểm tra được giữ nguyên."
    if not ready and not preview_ready and not message:
        message = "Tác vụ trước đã gián đoạn; mở dự án để xem phần đã lưu và tiếp tục."
    if output_warning:
        message = " ".join(filter(None, (message, output_warning)))
    elif waiting_export:
        message = "Lời dịch và giọng đã lưu; chưa có MP4 được kiểm định. Mở dự án và xuất video."
    return {"status": status, "missing_media": message, "ready": ready, "preview_ready": preview_ready,
            **output_gate,
            "preview_can_continue": preview_can_continue, "source_exists": _exists(fields.get("video_path")),
            "pending_download": pending_download, "output_warning": output_warning,
            "missing_audio_ids": absent_audio, "output_filename": output if valid_output and ready else ""}


def list_saved_sessions():
    root = _project_path("index").parent
    result = []
    if not root.exists():
        return result
    for path in root.glob("*.json"):
        if not ID_PATTERN.fullmatch(path.stem):
            continue
        try:
            data = _read(path.stem)
            available = _availability(data)
            fields = data["session"]
            title = Path(fields["video_path"]).name if fields.get("video_path") else fields.get("source_url") or path.stem
            result.append({"task_id": path.stem, "title": title, "updated_at": data["updated_at"],
                "status": available["status"], "can_open": available["source_exists"] or available["pending_download"], "duration": fields.get("total_duration", 0),
                "saved": True, "task_type": "Phiên đã lưu", "progress_pct": 100 if available["status"] == "COMPLETED" else None,
                **{key: available[key] for key in ("missing_speech_ids", "final_output_blocked", "content_review_state")},
                "missing_media": available["missing_media"], "stage": available["missing_media"] or "Dự án đã lưu",
                "can_translate_full": available["preview_can_continue"], "translation_mode": fields.get("translation_mode", "full"),
                "processed_seconds": fields.get("_visual_completed_seconds", 0),
                "output_filename": available["output_filename"],
                "output_video_url": f"/api/outputs/{available['output_filename']}" if available["output_filename"] else "",
                "video_url": f"/api/outputs/{available['output_filename']}" if available["output_filename"] else "",
                "review_url": f"/api/outputs/{fields['review_filename']}" if available["output_filename"] and fields.get("review_filename") and _exists(settings.OUTPUT_DIR / fields['review_filename']) else ""})
        except (OSError, ValueError, TypeError, KeyError):
            result.append({"task_id": path.stem, "title": path.stem, "updated_at": 0,
                "status": "FAILED", "can_open": False, "output_filename": "", "output_video_url": "",
                "missing_media": "Tệp dự án bị hỏng hoặc không hợp lệ; video đã xuất vẫn được giữ."})
    return sorted(result, key=lambda row: row["updated_at"], reverse=True)


def restore_saved_session(task_id, event_callback=None):
    # History snapshots and editing endpoints restore off the backend loop.
    # Concurrent readers must share one owner for all later Retry/edit work.
    # A local reference retains the lock for both its holder and waiters; idle
    # locks disappear instead of accumulating for every project ever opened.
    with _RESTORE_LOCKS_GUARD:
        owner_lock = _RESTORE_LOCKS.get(task_id)
        if owner_lock is None:
            owner_lock = threading.Lock()
            _RESTORE_LOCKS[task_id] = owner_lock
    with owner_lock:
        return _restore_saved_session_owned(task_id, event_callback)


def _restore_saved_session_owned(task_id, event_callback=None):
    from core.streaming.pipeline import StreamingPipelineSession, SegmentItem, active_streaming_sessions
    if task_id in active_streaming_sessions:
        return active_streaming_sessions[task_id]
    data = _read(task_id)
    fields = data["session"]
    available = _availability(data)
    session = StreamingPipelineSession(task_id, Path(fields["video_path"]) if fields.get("video_path") else None,
        voice=fields["voice"], tts_engine_name=fields["tts_engine_name"], asr_engine_name=fields["asr_engine_name"],
        visual_translation=fields.get("visual_translation", False), event_callback=event_callback, _restoring=True)
    for key in SESSION_FIELDS:
        if key in fields:
            setattr(session, key, fields[key])
    from core.streaming.recovery import recover_interrupted
    recover_interrupted(session._chunk_jobs)
    for key in PATH_FIELDS:
        setattr(session, key, Path(fields[key]) if fields.get(key) else None)
    if "auto_export_result" not in fields:
        # The first manifest version omitted this existing UI default. Recover
        # it only for the same reviewed OpenCode visual workflow that enabled
        # automatic export originally; never change an explicit false setting.
        sources = fields.get("translation_sources", [])
        session.auto_export_result = bool(session.visual_translation
            and settings.LLM_PROVIDER == "opencode"
            and fields.get("review_summary", {}).get("status") == "completed"
            and sources and all(item.get("provider") == "opencode" for item in sources))
    session.source_url = fields.get("source_url")
    session.video_size = tuple(fields.get("video_size", [1080, 1920]))
    session.source_video_url = f"/api/local-file?path={quote(session.video_path.as_posix(), safe='')}" if session.video_path else None
    session.bgm_url = f"/api/streaming/bgm/{task_id}" if _exists(session.bgm_audio_path) else None
    needs_preparation_resume = False
    visual_incomplete = bool((session._chunked_source_started and not session._visual_prepass_complete) or
        (session.visual_translation and (
        (session._visual_incremental_started and not session._visual_prepass_complete)
        or any(row.get("source_method") not in {"text-ai", "video-ai"}
               and row.get("status") not in {"READY", "PLAYED"}
               and not row.get("final_vi", "").strip() and not row.get("confirmed_silence")
               for row in data["segments"]))))
    for row in data["segments"]:
        segment = SegmentItem(row["id"], row["start"], row["end"], row["duration"])
        for key, value in row.items():
            setattr(segment, key, value)
        untouched_preview_row = bool(available["preview_can_continue"] and row["status"] == "WAITING"
                                     and row["start"] >= session._visual_completed_seconds - .001)
        valid_audio = _valid_row_audio(row, segment.audio_path)
        if not untouched_preview_row and (row["id"] in available["missing_audio_ids"] or row["status"] not in {"READY", "PLAYED", "NEEDS_REVIEW"}):
            # A saved spoken line can resume directly at TTS. A row without a
            # translation needs the normal preparation/translation path again.
            if segment.final_vi or segment.confirmed_silence or (segment.source_method in {"text-ai", "video-ai"} and segment.needs_review):
                segment.failed_stage = (row.get("failed_stage")
                    if row["status"] == "FAILED" and row.get("failed_stage") in {"TTS", "ALIGNING"} else "TTS")
            else:
                segment.failed_stage = row.get("failed_stage") or row["status"]
                needs_preparation_resume = True
            segment.status = "FAILED"
            if row["status"] == "FAILED" and row.get("error"):
                segment.error = row["error"]
            elif not valid_audio and (segment.audio_path or row["id"] in available["missing_audio_ids"]):
                issue, segment.error = _audio_recovery_issue(row, segment.audio_path)
                if issue == "timing" and segment.final_vi:
                    segment.failed_stage = "ALIGNING"
            elif data["state"] == "STOPPED" or row["status"] in {"WAITING", "ASR", "TRANSLATING", "TTS", "ALIGNING"}:
                segment.error = "Câu chưa xử lý xong khi tác vụ dừng; bấm Tiếp tục từ bước đã lưu."
            else:
                segment.error = "Câu chưa có giọng hợp lệ; bấm Tiếp tục để xử lý riêng phần còn thiếu."
        if valid_audio:
            segment.audio_url = f"/api/streaming/audio/{task_id}/{segment.id}?rev={segment.revision}"
        else:
            segment.audio_path = segment.audio_url = None
        session.segments[segment.id] = segment
    if session.review_summary.get("status") == "running":
        session.review_summary["status"] = "incomplete"
    reviewed_before_init = bool(not session.initialized and session.visual_translation and session.segments
        and session.review_summary.get("status") == "completed"
        and all(s.source_method in {"text-ai", "video-ai"} and s.translation_provider
                and (s.final_vi.strip() or s.needs_review or s.confirmed_silence)
                for s in session.segments.values()))
    if reviewed_before_init:
        # review_complete is durable before the later init event. A shutdown in
        # that small interval must resume at TTS, not submit the draft/review again.
        session.initialized = True
    session.output_filename = available["output_filename"]
    session.output_video_url = f"/api/outputs/{session.output_filename}" if session.output_filename else ""
    review = fields.get("review_filename", "")
    session.output_review_url = f"/api/outputs/{review}" if session.output_filename and _exists(settings.OUTPUT_DIR / review) else ""
    session.total_processed_duration = sum(s.duration for s in session.segments.values() if s.status in {"READY", "PLAYED"})
    session.current_playback_time = min(session.current_playback_time, session.total_duration)
    session._recalculate_telemetry()
    session.first_play_emitted = bool(session.initialized and _exists(session.video_path) and session.bgm_url)
    session._prepared = _exists(session.raw_audio_16k) and _exists(session.bgm_audio_path)
    incomplete = any(s.status == "FAILED" for s in session.segments.values())
    source_missing = not _exists(session.video_path)
    session._restored_source_missing = source_missing and not available["pending_download"]
    if incomplete or source_missing:
        session.error = available["missing_media"] or "Tác vụ trước bị gián đoạn; bấm Tiếp tục để xử lý phần còn lại."
    else:
        session.warnings[:] = [warning for warning in session.warnings if warning != OUTPUT_FAILURE_WARNING]
        if available["output_warning"]:
            session.warnings.append(available["output_warning"])
    session.progress = {"phase": "prepared" if available["status"] == "PREPARED" else "complete" if available["ready"] else "restored",
        "stage": available["missing_media"] or "Đã khôi phục bản dịch và giọng đọc đã lưu.",
        "progress_pct": 100 if available["status"] == "COMPLETED" else None, "status": available["status"]}
    # Fully prepared translations can retry TTS without ASR or a provider request.
    # Earlier interrupted preparation is restarted explicitly using the same source/checkpoints.
    session._restored_can_resume = bool(session.initialized and not source_missing
        and (not needs_preparation_resume or _exists(session.raw_audio_16k)))
    if (not session.initialized or visual_incomplete) and not available["preview_can_continue"] and (not source_missing or available["pending_download"]):
        # A published prefix is initialized/playable while later visual chunks
        # are still missing. Retry must resume prepass checkpoints, never send
        # untouched source rows straight to speech generation.
        session._startup_failed = True
        session.error = ("Dịch video chưa xong; các câu đã dịch và giọng đọc được giữ. Bấm Tiếp tục để xử lý phần còn lại."
                         if visual_incomplete else "Chuẩn bị video bị gián đoạn; bấm Tiếp tục để khôi phục phần đã lưu.")
        if available["pending_download"]:
            session._source_downloader = _SavedSourceDownloader()
    if available["preview_ready"]:
        session.error = None
        session._startup_failed = False
        session.progress.update(phase="preview_ready", stage="Bản xem trước đã sẵn sàng. Bấm Dịch toàn bộ để tiếp tục.",
                                status="PREVIEW_READY", progress_pct=None)
    session._persistence_enabled = True
    session._restored_project = True
    # Interrupted work uses error internally to enable the existing retry path;
    # that recovery marker is not a provider/runtime failure. Preserve the saved
    # stop state across snapshots and subsequent reopen cycles until retry starts.
    session._restored_interrupted = available["status"] == "STOPPED"
    # A separately created live owner must also win over a slow disk restore.
    return active_streaming_sessions.setdefault(task_id, session)


class _SavedSourceDownloader:
    def download(self, *args, **kwargs):
        # Restoring history never contacts a network service. Resolve the normal
        # downloader only after the user explicitly resumes a pending download.
        from core.downloader import VideoDownloader
        return VideoDownloader().download(*args, **kwargs)
