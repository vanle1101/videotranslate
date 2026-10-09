import asyncio
import hashlib
import json
import os
import shutil
import stat
import uuid
import time
import logging
import traceback
import re
import threading
from pathlib import Path
from urllib.parse import quote
from typing import Dict, Any, Optional, List, Literal
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel, Field, ConfigDict

from config import settings
from core.downloader import VideoDownloader
from core.douyin_cookies import (
    DouyinCookieError, import_douyin_cookies, get_douyin_cookie_status,
    clear_douyin_cookies,
)
from core.hardware import detect_hardware
from core.model_manager import ModelManager
from core.engines.translation.opencode_client import (
    OpenCodeZenClient, OpenCodeClientError, resolve_api_key, find_opencode_executable,
)
from core.engines.translation.openrouter_client import OpenRouterFreeClient, OpenRouterClientError, resolve_openrouter_key
from core.streaming.pipeline import (
    create_streaming_session,
    get_streaming_session,
    active_streaming_sessions,
    SegmentEditConflict,
    ProjectEditSaveError,
)
from core.streaming.export import HQExporter
from core.streaming.session_store import list_saved_sessions, restore_saved_session
from core.streaming.output_gate import final_output_metadata, missing_spoken_output_ids, missing_speech_message
from core.runtime_errors import export_failure
from core.media_preview import preview_manager, DEFAULT_COMPATIBILITY_SECONDS
from core.voice_catalog import list_voices, resolve_voice
from core.voice_preview import select_voice, voice_preview_manager, VoicePreviewBusy
from core.services.muse_service import ERRORS as MUSE_ERRORS, MuseError, muse_service

app = FastAPI(title=f"{settings.APP_NAME} - Realtime Streaming Studio")

# Static and Mounts
app.mount("/static", StaticFiles(directory=str(settings.BASE_DIR / "static")), name="static")
app.mount("/api/outputs", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="outputs")
app.mount("/api/inputs", StaticFiles(directory=str(settings.INPUT_DIR)), name="inputs")
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

# Active WebSocket connections per session
stream_sockets: Dict[str, List[WebSocket]] = {}
_stream_send_states = {}
STREAM_SEND_TIMEOUT_SECONDS = 2.0
STREAM_CLOSE_TIMEOUT_SECONDS = 0.5
downloader = VideoDownloader()

@app.get("/api/health")
async def health():
    return {"status": "ok", "ffmpeg": bool(shutil.which("ffmpeg")),
            "ffprobe": bool(shutil.which("ffprobe"))}

@app.get("/api/library")
def list_library():
    """List completed source videos without following links or scanning subfolders."""
    source_dir = Path(settings.INPUT_DIR)
    try:
        if source_dir.is_symlink() or source_dir.is_junction():
            return {"items": []}
        root = source_dir.resolve(strict=True)
        entries = list(root.iterdir())
    except OSError:
        return {"items": []}

    items = []
    for entry in entries:
        if (entry.name.startswith(".") or entry.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
                or any(suffix.lower() in {".part", ".tmp", ".temp"} for suffix in entry.suffixes)):
            continue
        try:
            info = entry.lstat()
            # Reparse points include Windows junctions; hidden/system files are
            # not user-facing library media even if they have a video suffix.
            if (not stat.S_ISREG(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & (0x2 | 0x4 | 0x400)):
                continue
            resolved = entry.resolve(strict=True)
            if resolved.parent != root:
                continue
        except OSError:
            continue
        items.append({"name": entry.name, "file_path": str(resolved),
                      "size": info.st_size, "modified_at": info.st_mtime})
    items.sort(key=lambda item: (-item["modified_at"], item["name"].casefold()))
    return {"items": items[:100]}

@app.get("/qtwebchannel.js")
def qt_webchannel_client():
    # Use the client matching the installed Qt version, including its license header.
    from PySide6.QtCore import QFile, QIODevice
    from PySide6 import QtWebChannel
    resource = QFile(":/qtwebchannel/qwebchannel.js")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise HTTPException(status_code=503, detail="Qt WebChannel client unavailable")
    return Response(bytes(resource.readAll()), media_type="application/javascript")

class StreamUrlRequest(BaseModel):
    url: str
    initial_buffer_seconds: Optional[float] = Field(default=None, gt=0, le=120)
    voice: Optional[str] = None
    voice_id: Optional[str] = None
    tts_engine: Optional[str] = None
    asr_engine: Optional[str] = None
    visual_translation: bool = False
    translation_mode: Literal["preview", "full"] = "preview"

class SeekRequest(BaseModel):
    task_id: str
    time: float

class ExportHQRequest(BaseModel):
    task_id: str
    mask_chinese: Optional[bool] = True
    translate_screen_text: bool = True

class SegmentEditRequest(BaseModel):
    final_vi: str = Field(max_length=2000, strict=True)
    confirm_silence: bool = Field(default=False, strict=True)


class SpeakerConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor_segment_id: int = Field(ge=0, le=2147483647, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    apply_same_voice: bool = Field(default=False, strict=True)
    label: str = Field(min_length=1, max_length=100, strict=True)
    self_address: str = Field(default="", max_length=80, strict=True)
    listener_address: str = Field(default="", max_length=80, strict=True)
    voice_id: Optional[str] = Field(default=None, max_length=160, strict=True)

class CaptionStyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    background_color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$", strict=True)
    text_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$", strict=True)
    position: Literal["auto", "top", "middle", "bottom"] = "auto"
    blur_original: bool = Field(default=False, strict=True)

class ConfigRequest(BaseModel):
    gemini_key: Optional[str] = None
    deepseek_key: Optional[str] = None
    llm_provider: Optional[str] = None
    gemini_model: Optional[str] = None
    opencode_model: Optional[str] = None
    openrouter_model: Optional[str] = None
    muse_browser_mode: Optional[str] = None
    suppression_mode: Optional[str] = None
    ducking_level: Optional[str] = None
    buffer_target: Optional[str] = None

def update_env_file(updates: Dict[str, str]):
    """Safely updates or adds configuration keys in .env file on disk."""
    env_path = settings.BASE_DIR / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"settings": settings})

@app.get("/api/hardware")
async def get_hardware():
    # Even a bounded driver query must not block Stop/WebSocket/health on the
    # shared backend loop. The UI only needs CPU/RAM/card display metadata.
    return await asyncio.to_thread(detect_hardware, probe_native=False)

@app.get("/api/models")
async def get_models():
    return {"models": ModelManager.get_all_models()}


@app.get("/api/voices")
async def get_voices():
    voices = await asyncio.to_thread(list_voices)
    try:
        default_engine, default_voice = resolve_voice()
        default_id = next((item["id"] for item in voices
                           if resolve_voice(item["id"]) == (default_engine, default_voice)), None)
    except ValueError:
        default_id = None
    return {"voices": voices, "default_voice_id": default_id}


class VoicePreviewRequest(BaseModel):
    voice_id: str = Field(min_length=1, max_length=160)


def validated_voice(voice_id=None, engine=None, legacy_voice=None):
    try:
        return select_voice(voice_id, engine, legacy_voice)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@app.post("/api/voices/preview")
async def create_voice_preview(req: VoicePreviewRequest):
    try:
        return await asyncio.to_thread(voice_preview_manager.create, req.voice_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except VoicePreviewBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        raise HTTPException(status_code=503, detail="Không tạo được mẫu giọng. Với giọng Edge, hãy kiểm tra mạng và thử lại.") from None


@app.get("/api/voices/preview/{preview_id}/audio")
async def get_voice_preview_audio(preview_id: str):
    try:
        return Response(voice_preview_manager.audio(preview_id), media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})
    except KeyError:
        raise HTTPException(status_code=404, detail="Mẫu giọng đã hết hạn. Hãy bấm Nghe thử lại.") from None


@app.delete("/api/voices/preview/{preview_id}")
async def delete_voice_preview(preview_id: str):
    voice_preview_manager.delete(preview_id)
    return {"status": "ok"}

class ModelVerifyRequest(BaseModel):
    query: str

@app.post("/api/models/verify")
async def verify_model_endpoint(req: ModelVerifyRequest):
    return ModelManager.verify_model(req.query)

@app.post("/api/models/download")
async def download_model_endpoint(req: ModelVerifyRequest):
    result = ModelManager.verify_model(req.query)
    if not result.get("ok"):
        result["message"] += " Chạy setup.bat để tải Whisper; các model nâng cao cần cài riêng."
    return result

@app.get("/api/settings")
async def get_settings():
    return {
        "asr_engine": settings.ASR_ENGINE,
        "tts_engine": settings.TTS_ENGINE,
        "edge_voice": settings.EDGE_VOICE,
        "device": settings.DEVICE,
        "whisper_model": settings.WHISPER_MODEL_SIZE,
        "llm_provider": settings.LLM_PROVIDER,
        "muse_browser_mode": settings.MUSE_BROWSER_MODE,
        "opencode_model": settings.OPENCODE_MODEL,
        "opencode_configured": bool(resolve_api_key()),
        "opencode_cli_available": bool(find_opencode_executable()),
        "opencode_free_models": OpenCodeZenClient.free_models(),
        "openrouter_configured": bool(resolve_openrouter_key()),
        "openrouter_model": settings.OPENROUTER_MODEL,
        "gemini_configured": bool(settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")),
        "gemini_model": settings.GEMINI_MODEL,
        "deepseek_configured": bool(settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY")),
        "suppression_mode": getattr(settings, "SUPPRESSION_MODE", "AUTO"),
        "ducking_level": f"{settings.BGM_VOLUME_DUCKED_DB:g}",
        "buffer_target": f"{settings.INITIAL_BUFFER_SECONDS:g}"
    }


def require_douyin_cookie_request(request: Request):
    # A cross-origin HTML form cannot provide this header. No CORS permissions
    # are granted to external origins, so a website cannot replace a local login.
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=403, detail="Chỉ cho phép thao tác từ Studio trên máy này.")
    if request.headers.get("x-studio-request") != "douyin-cookies":
        raise HTTPException(status_code=403, detail="Hãy nhập phiên từ Cài đặt trong Studio.")
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Chỉ cho phép thao tác từ Studio trên máy này.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Chỉ cho phép thao tác từ Studio trên máy này.")


@app.get("/api/douyin/cookies")
async def douyin_cookie_status():
    return await asyncio.to_thread(get_douyin_cookie_status)


@app.post("/api/douyin/cookies")
async def import_douyin_cookie_file(request: Request, file: UploadFile = File(...)):
    try:
        require_douyin_cookie_request(request)
        content = await file.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise HTTPException(status_code=413, detail="Tệp cookie vượt quá giới hạn 1 MiB.")
        return await asyncio.to_thread(import_douyin_cookies, content)
    except DouyinCookieError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except OSError:
        raise HTTPException(status_code=503, detail="Không lưu được phiên Douyin. Hãy thử lại.") from None
    finally:
        await file.close()


@app.delete("/api/douyin/cookies")
async def delete_douyin_cookie_file(request: Request):
    require_douyin_cookie_request(request)
    try:
        return await asyncio.to_thread(clear_douyin_cookies)
    except (OSError, DouyinCookieError):
        raise HTTPException(status_code=503, detail="Không xóa được phiên Douyin. Hãy thử lại.") from None

@app.post("/api/settings")
@app.post("/api/config")
async def update_settings(req: ConfigRequest):
    # Validate before changing any active settings or persisting the local file.
    if req.llm_provider and req.llm_provider not in {"openrouter-free", "opencode", "free", "gemini", "deepseek", "openai", "muse"}:
        raise HTTPException(status_code=422, detail="Nhà cung cấp dịch không hợp lệ")
    if req.muse_browser_mode is not None and req.muse_browser_mode not in {"dedicated", "existing"}:
        raise HTTPException(status_code=422, detail="Chế độ trình duyệt Muse không hợp lệ")
    opencode_model = None
    if req.opencode_model is not None:
        try:
            opencode_model = OpenCodeZenClient(model=req.opencode_model).validate_model()
        except OpenCodeClientError:
            raise HTTPException(status_code=422, detail="Hãy chọn model OpenCode miễn phí trong danh sách") from None
    for value in (req.gemini_key, req.deepseek_key, req.gemini_model):
        if value is not None and ("\n" in value or "\r" in value):
            raise HTTPException(status_code=422, detail="Cấu hình không được chứa ký tự xuống dòng")
    env_updates = {}
    if req.suppression_mode is not None:
        if req.suppression_mode not in {"AUTO", "DSP_MONO_ADAPTIVE_FORMANT", "DSP_STEREO_CENTER_CANCEL"}:
            raise HTTPException(status_code=422, detail="Chế độ tách thoại không hợp lệ")
        env_updates["SUPPRESSION_MODE"] = req.suppression_mode
    if req.buffer_target is not None:
        try:
            value = float(req.buffer_target)
            if not 0 < value <= 120:
                raise ValueError
            env_updates["INITIAL_BUFFER_SECONDS"] = str(value)
        except ValueError:
            raise HTTPException(status_code=422, detail="Đệm trước phát phải lớn hơn 0 và tối đa 120 giây") from None
    if req.muse_browser_mode is not None:
        env_updates["MUSE_BROWSER_MODE"] = req.muse_browser_mode
    if req.openrouter_model is not None:
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+:free", req.openrouter_model):
            raise HTTPException(status_code=422, detail="Model OpenRouter phải là bản :free")
        env_updates["OPENROUTER_MODEL"] = req.openrouter_model
    if req.gemini_key is not None:
        env_updates["GEMINI_API_KEY"] = req.gemini_key
    if req.deepseek_key is not None:
        env_updates["DEEPSEEK_API_KEY"] = req.deepseek_key
    if req.llm_provider:
        env_updates["LLM_PROVIDER"] = req.llm_provider
    if req.gemini_model:
        env_updates["GEMINI_MODEL"] = req.gemini_model
    if opencode_model:
        env_updates["OPENCODE_MODEL"] = opencode_model
    if req.ducking_level:
        try:
            value = float(req.ducking_level)
            if not -60 <= value <= 0:
                raise ValueError
            env_updates["BGM_VOLUME_DUCKED_DB"] = str(value)
        except ValueError:
            raise HTTPException(status_code=422, detail="Mức giảm BGM phải từ -60 đến 0 dB") from None

    def apply_settings():
        # Session engines capture some settings while later stages read globals.
        # Reject mutations until all consumers finish rather than mixing models
        # or silently skipping the final review of an existing run.
        changed = any(str(getattr(settings, name, "")) != str(value)
                      for name, value in env_updates.items())
        if changed and (any(getattr(sess, "is_running", False) or getattr(sess, "is_editing", False)
                            for sess in active_streaming_sessions.values())
                        or any(job.get("status") in {"RUNNING", "CANCELLING"}
                               for job in active_export_tasks.values())):
            raise HTTPException(status_code=409, detail="Đang có tác vụ xử lý. Hãy chờ xong hoặc dừng tác vụ trước khi đổi cấu hình.")
        try:
            update_env_file(env_updates)
        except OSError:
            raise HTTPException(status_code=500, detail="Không thể ghi cấu hình vào .env") from None
        for name, value in env_updates.items():
            setattr(settings, name, float(value) if name in {"BGM_VOLUME_DUCKED_DB", "INITIAL_BUFFER_SECONDS"} else value)
            if name in {"GEMINI_API_KEY", "DEEPSEEK_API_KEY"}:
                os.environ[name] = value

    if env_updates:
        if req.muse_browser_mode is not None and req.muse_browser_mode != settings.MUSE_BROWSER_MODE:
            try:
                await asyncio.to_thread(muse_service.change_browser_mode, req.muse_browser_mode, apply_settings)
            except MuseError:
                raise HTTPException(status_code=409, detail="Muse đang đổi chế độ hoặc ứng dụng đang đóng. Hãy thử lưu lại.") from None
        else:
            apply_settings()

    return {"status": "ok", "message": "Đã lưu cấu hình. Phiên dịch và lần xuất tiếp theo sẽ dùng cấu hình mới."}

# -------------------------------------------------------------
# TASK MANAGER API
# -------------------------------------------------------------
active_export_tasks: Dict[str, Dict[str, Any]] = {}
task_history: List[Dict[str, Any]] = []


def review_result_details(session):
    """Keep unresolved findings visible even when the user receives a final file."""
    summary = dict(getattr(session, "review_summary", {}) or {})
    findings = [{"segment_id": s.id, "start": s.start, "end": s.end,
                 "text_zh": s.text_zh, "final_vi": s.final_vi,
                 "reason": s.review_reason or "Nguồn chưa đủ rõ để xác minh chắc chắn.",
                 "verification": getattr(s, "verification", None)}
                for s in session.segments.values() if getattr(s, "needs_review", False)]
    return {"review_summary": summary, "review_report": findings,
            "review_warning": (f"AI đã kiểm tra toàn bộ; {len(findings)} câu còn thiếu bằng chứng rõ từ nguồn. "
                               "Video giữ bản dịch tốt nhất hiện có; chi tiết nằm trong báo cáo kiểm tra.")
            if findings and summary.get("status") == "completed" else ""}


def review_sidecar_payload(session):
    """Persist an auditable row for every spoken segment beside the MP4."""
    rows = []
    for segment in session.segments.values():
        verification = getattr(segment, "verification", None) or {}
        rows.append({
            "segment_id": segment.id, "start": segment.start, "end": segment.end,
            "source_text": segment.text_zh, "translation": segment.final_vi,
            "needs_review": bool(getattr(segment, "needs_review", False)),
            "review_reason": segment.review_reason or "",
            "verification": verification,
            "source_method": getattr(segment, "source_method", ""),
            "translation_provider": getattr(segment, "translation_provider", None),
        })
    return {"task_id": session.task_id, "review_summary": dict(getattr(session, "review_summary", {}) or {}),
            "caption_style": dict(getattr(session, "caption_style", {}) or {}),
            "segments": rows,
            **review_result_details(session)}


def session_output_details(session):
    gate = final_output_metadata(session.segments.values())
    return {"output_video_url": "" if gate["final_output_blocked"] else getattr(session, "output_video_url", ""),
            "output_filename": "" if gate["final_output_blocked"] else getattr(session, "output_filename", ""),
            "review_url": "" if gate["final_output_blocked"] else getattr(session, "output_review_url", ""),
            "output_outdated": getattr(session, "caption_output_outdated", False),
            **gate,
            **review_result_details(session)}


async def validate_live_output(session):
    """A still-open project must not keep a deleted/corrupt download link."""
    name = getattr(session, "output_filename", "")
    if not name:
        return
    signature = (name, getattr(session, "output_video_url", ""),
                 getattr(session, "caption_style_revision", 0))
    path = settings.OUTPUT_DIR / name if isinstance(name, str) and Path(name).name == name else None
    def file_revision():
        try:
            info = path.stat() if path is not None else None
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) if info else None
        except OSError:
            return None
    prior_file = file_revision()
    from core.streaming.session_store import _valid_output
    valid = path is not None and await asyncio.to_thread(_valid_output, path, session.total_duration)
    current_file = file_revision()
    if valid or signature != (getattr(session, "output_filename", ""),
                             getattr(session, "output_video_url", ""),
                             getattr(session, "caption_style_revision", 0)) or (current_file is not None and current_file != prior_file):
        return
    invalidate = getattr(session, "_invalidate_output", None)
    if callable(invalidate):
        invalidate()
    else:
        # Keep the live-output contract safe for restored/legacy session
        # objects that predate the helper method.  Never leave a stale URL
        # visible merely because the compatibility object lacks a method.
        session.output_video_url = ""
        session.output_filename = ""
        session.output_review_url = ""
        session.auto_export_signature = None
    session.caption_output_outdated = True
    message = "Tệp video kết quả bị thiếu hoặc hỏng. Bản dịch và giọng đọc được giữ; hãy xuất video lại."
    if message not in session.warnings:
        session.warnings.append(message)
    task = active_export_tasks.get(f"export_{session.task_id}")
    if task and task.get("status") == "COMPLETED":
        task.update(status="FAILED", stage=message, progress=None, video_url="", output_filename="")
    persist_session(session)


async def editable_session(task_id):
    session = get_streaming_session(task_id)
    if session is None:
        try:
            # Restore probes saved media; keep that synchronous work off the
            # ASGI event loop so history cannot block websocket/cancel traffic.
            session = await asyncio.to_thread(
                restore_saved_session, task_id,
                event_callback=lambda event, data: broadcast_session_event(task_id, event, data))
        except FileNotFoundError:
            return None
        except (ValueError, OSError, TypeError, KeyError):
            raise HTTPException(status_code=409, detail="Không mở được phiên đã lưu. Kiểm tra video nguồn và dữ liệu trong Lịch sử.") from None
    if session is not None:
        await validate_live_output(session)
    return session


def persist_session(session):
    save = getattr(session, "persist", None)
    if callable(save):
        try:
            save()
        except (OSError, ValueError, TypeError) as error:
            logging.getLogger("errors").error("PROJECT_SAVE_FAILED run_id=%s error_type=%s", session.task_id, type(error).__name__)
            warning = "Không lưu được phiên xuống ổ đĩa; giữ ứng dụng mở và kiểm tra dung lượng/quyền ghi."
            if warning not in getattr(session, "warnings", []):
                session.warnings.append(warning)


def export_revision_signature(session):
    content = {"segments": [{"id": s.id, "revision": getattr(s, "revision", 0),
                             "start": s.start, "end": s.end, "final_vi": s.final_vi,
                             "confirmed_silence": getattr(s, "confirmed_silence", False),
                             "needs_review": getattr(s, "needs_review", False),
                             "audio_path": getattr(s, "audio_path", None),
                             "dub_start": getattr(s, "dub_start", None), "dub_end": getattr(s, "dub_end", None),
                             "verification": getattr(s, "verification", None)}
                            for s in session.segments.values()],
               "screen_texts": getattr(session, "screen_texts", []),
               "caption_style": getattr(session, "caption_style", {})}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def schedule_reviewed_export(task_id):
    session = get_streaming_session(task_id)
    if (session is None or getattr(session, "auto_export_result", False) is not True
            or getattr(session, "review_summary", {}).get("status") != "completed"
            or session.is_running or session.error or getattr(session, "is_stopped", False)
            or getattr(session, "_stop_draining", False)
            or getattr(session, "is_editing", False) or not session.segments
            or (getattr(session, "translation_mode", "full") == "preview"
                and not getattr(session, "_visual_prepass_complete", False))
            or missing_spoken_output_ids(session.segments.values())
            or any(s.status not in ("READY", "PLAYED") for s in session.segments.values())):
        return
    owner = getattr(session, "auto_export_task", None)
    if (owner is not None and not owner.done()) or active_export_tasks.get(
            f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}:
        return
    signature = export_revision_signature(session)
    if getattr(session, "auto_export_signature", None) == signature:
        return
    session.auto_export_signature = signature
    session.output_video_url = ""
    session.output_filename = ""

    async def generate_result():
        failed = False
        previous_export = active_export_tasks.get(f"export_{task_id}")
        try:
            if (get_streaming_session(task_id) is not session
                    or getattr(session, "is_stopped", False)
                    or getattr(session, "_stop_draining", False)
                    or getattr(session, "auto_export_signature", None) != signature):
                return
            await export_hq(ExportHQRequest(task_id=task_id))
        except asyncio.CancelledError:
            # export_hq owns cancellation and drains its media thread. Do not
            # mutate whichever export now occupies this ID after it returns.
            raise
        except Exception as error:
            failed = True
            # export_hq already emitted its classified error. A second generic
            # event would replace the useful cause in the user's status panel.
            if getattr(error, "result_error_reported", False):
                return
            message = (error.detail if isinstance(error, HTTPException) and isinstance(error.detail, str)
                       else export_failure(error, f"export_{task_id}", "automatic_export"))
            await broadcast_session_event(task_id, "result_error", {
                "message": message,
                **review_result_details(session),
            })
        finally:
            export = active_export_tasks.get(f"export_{task_id}", {})
            # A preflight rejection creates no runtime record; an older valid
            # or cancelled export must not hold this new failed reservation.
            owned_export = export if export is not previous_export else {}
            if (failed and get_streaming_session(task_id) is session
                    and getattr(session, "auto_export_task", None) is asyncio.current_task()
                    and getattr(session, "auto_export_signature", None) == signature
                    and not getattr(session, "is_stopped", False)
                    and not getattr(session, "_stop_draining", False)
                    and export.get("status") not in {"RUNNING", "CANCELLING"}
                    and not owned_export.get("published") and not owned_export.get("cancelled")
                    and owned_export.get("status") not in {"CANCELLED", "COMPLETED"}):
                # Reserve successful/cancelled publication, not a failed attempt.
                # Only a later genuine finished event may retry; never reschedule
                # from this cleanup or persistent render errors would loop.
                session.auto_export_signature = None

    session.auto_export_task = asyncio.create_task(generate_result())

@app.get("/api/tasks")
async def list_tasks():
    tasks = []

    # 1. Streaming Realtime Sessions
    for task_id, sess in list(active_streaming_sessions.items()):
        await validate_live_output(sess)
        ready_cnt = sum(1 for s in sess.segments.values() if s.status in ["READY", "PLAYED"])
        tot_cnt = max(1, len(sess.segments))
        pct = int((ready_cnt / tot_cnt) * 100) if tot_cnt > 0 else 0

        status_str = "STOPPED" if getattr(sess, "is_stopped", False) else ("FAILED" if sess.error else ("PAUSED" if sess.is_paused else ("RUNNING" if sess.is_running else "COMPLETED")))
        progress = sess.get_progress() if hasattr(sess, "get_progress") else {
            "progress_pct": pct, "status": status_str,
            "stage": sess.error or f"Đã dịch {ready_cnt}/{tot_cnt} câu ({pct}%)",
            "can_pause": status_str == "RUNNING",
            "elapsed_seconds": round(time.time() - sess.start_wall_time, 1) if sess.start_wall_time else 0,
        }
        status_str = progress["status"]
        tasks.append({
            "task_id": task_id,
            "task_type": "Realtime Dubbing",
            "status": status_str,
            **progress,
            "duration": sess.total_duration,
            "video_url": session_output_details(sess)["output_video_url"],
            **session_output_details(sess),
            "can_resume": status_str == "PAUSED",
            "can_stop": progress.get("can_stop", status_str in ["RUNNING", "PAUSED"])
        })

    # 2. HQ Export Tasks
    for export_id, exp in list(active_export_tasks.items()):
        tasks.append({
            "task_id": export_id,
            "task_type": "HQ Export (BS-RoFormer)",
            "status": exp.get("status", "RUNNING"),
            "progress_pct": exp.get("progress", 0),
            "stage": exp.get("stage", "Đang xử lý..."),
            "duration": exp.get("duration", 0),
            "elapsed_seconds": round(time.time() - exp.get("start_time", time.time()), 1),
            "video_url": exp.get("video_url", ""),
            "output_filename": exp.get("output_filename", ""),
            "output_video_url": exp.get("video_url", ""),
            "review_summary": exp.get("review_summary", {}),
            "review_warning": exp.get("review_warning", ""),
            "review_report": exp.get("review_report", []),
            "review_url": exp.get("review_url", ""),
            "can_pause": False,
            "can_resume": False,
            "can_stop": exp.get("status") == "RUNNING"
        })

    # 3. Add finished history if empty
    for hist in task_history[-5:]:
        if not any(t["task_id"] == hist["task_id"] for t in tasks):
            tasks.append(hist)

    existing = frozenset(task["task_id"] for task in tasks)
    saved_sessions = await asyncio.to_thread(list_saved_sessions, excluded_task_ids=existing)
    for saved in saved_sessions:
        if saved["task_id"] not in existing:
            tasks.append({**saved, "saved": True, "task_type": "Phiên đã lưu",
                          "video_url": saved.get("output_video_url", ""),
                          "stage": saved.get("stage") or saved.get("missing_media", ""),
                          "can_pause": False, "can_resume": False, "can_stop": False})
    return {"tasks": tasks}

@app.post("/api/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    sess = get_streaming_session(task_id)
    if sess:
        if not sess.is_running or getattr(sess, "is_stopped", False):
            raise HTTPException(status_code=409, detail="Phiên dịch đã kết thúc")
        if not getattr(sess, "initialized", True):
            raise HTTPException(status_code=409, detail="Chưa thể tạm dừng ở bước tải và chuẩn bị video. Bạn có thể bấm Dừng.")
        sess.pause()
        if hasattr(sess, "get_progress"):
            await sess.emit("progress", sess.get_progress())
        return {"status": "ok", "task_id": task_id, "action": "paused"}
    raise HTTPException(status_code=404, detail="Task không tồn tại hoặc không thể tạm dừng")

@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    sess = get_streaming_session(task_id)
    if sess:
        if not sess.is_running or getattr(sess, "is_stopped", False):
            raise HTTPException(status_code=409, detail="Phiên dịch đã kết thúc")
        sess.resume()
        if hasattr(sess, "get_progress"):
            await sess.emit("progress", sess.get_progress())
        return {"status": "ok", "task_id": task_id, "action": "resumed"}
    raise HTTPException(status_code=404, detail="Task không tồn tại hoặc không thể tiếp tục")

@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    sess = get_streaming_session(task_id)
    if sess:
        workers = [worker for worker in (getattr(sess, "start_task", None), getattr(sess, "worker_task", None),
                                        getattr(sess, "review_task", None), getattr(sess, "auto_export_task", None),
                                        getattr(sess, "_chunk_followup_task", None))
                   if worker is not None and not worker.done()]
        workers.extend(task for task in getattr(sess, "edit_tasks", ()) if not task.done())
        progress = sess.get_progress() if hasattr(sess, "get_progress") else {}
        if active_export_tasks.get(f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}:
            await cancel_export_hq(task_id)
            # Export threads may still hold the session's WAVs. Drain them before
            # session.stop removes that owned media from its cache.
            export_worker = getattr(sess, "export_task", None) or getattr(sess, "auto_export_task", None)
            if export_worker is not None and not export_worker.done():
                await asyncio.gather(export_worker, return_exceptions=True)
        stopping = {
            "task_id": task_id, "task_type": "Realtime Dubbing", "status": "CANCELLING",
            "phase": "stopping", "stage": "Đang dừng xử lý; chờ bộ nhận giọng trả quyền điều khiển…",
            "progress_pct": progress.get("progress_pct"), "duration": sess.total_duration,
            "video_url": "", "can_pause": False, "can_resume": False, "can_stop": False,
        }
        task_history.append(stopping)
        sess._stop_draining = True
        sess.stop()
        # A Python download thread must acknowledge cancellation before this
        # endpoint reports a completed stop or any owned files are removed.
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        sess._stop_draining = False
        await sess.emit("progress", sess.get_progress())
        stopping.update({
            "task_id": task_id,
            "task_type": "Realtime Dubbing",
            "status": "STOPPED",
            "progress_pct": progress.get("progress_pct"),
            "phase": "stopped",
            "stage": "Đã dừng bởi người dùng",
            "duration": sess.total_duration,
            "elapsed_seconds": round(time.time() - sess.start_wall_time, 1) if sess.start_wall_time else 0,
            "video_url": ""
        })
        return {"status": "ok", "task_id": task_id, "action": "stopped"}

    if task_id in active_export_tasks:
        await cancel_export_hq(task_id)
        return {"status": "ok", "task_id": task_id, "action": "cancelled"}

    raise HTTPException(status_code=404, detail="Task không tồn tại")

# -------------------------------------------------------------
# REAL-TIME STREAMING API
# -------------------------------------------------------------

def _remove_stream_socket(task_id, websocket):
    _stream_send_states.pop((task_id, id(websocket)), None)
    connections = stream_sockets.get(task_id)
    if connections is None:
        return
    if websocket in connections:
        connections.remove(websocket)
    if not connections and stream_sockets.get(task_id) is connections:
        stream_sockets.pop(task_id, None)


async def _send_stream_payload(task_id, websocket, payload):
    """Serialize one client's replay/live events without blocking its siblings."""
    key = (task_id, id(websocket))
    if not any(client is websocket for client in stream_sockets.get(task_id, ())):
        return
    state = _stream_send_states.get(key)
    if state is None:
        state = asyncio.Lock()
        _stream_send_states[key] = state
    async def send_locked():
        async with state:
            if (_stream_send_states.get(key) is not state
                    or not any(client is websocket for client in stream_sockets.get(task_id, ()))):
                return
            await websocket.send_json(payload)
    try:
        # Include the lock wait in the bound: a stalled client must not retain
        # an ever-growing queue of old progress or hold up provider callbacks.
        await asyncio.wait_for(send_locked(), timeout=STREAM_SEND_TIMEOUT_SECONDS)
    except BaseException:
        # A cancelled/failed send can leave a partial transport write. Drop its
        # ownership and close it; queued sends recheck ownership before writing.
        _remove_stream_socket(task_id, websocket)
        close = getattr(websocket, "close", None)
        if callable(close):
            try:
                await asyncio.wait_for(close(code=1011), timeout=STREAM_CLOSE_TIMEOUT_SECONDS)
            except Exception:
                pass
        raise


async def broadcast_session_event(task_id: str, event_type: str, data: Dict[str, Any]):
    payload = {"type": event_type, "task_id": task_id, **data}
    async def deliver(websocket):
        try:
            await _send_stream_payload(task_id, websocket, payload)
        except Exception as error:
            logging.getLogger("errors").warning(
                "STREAM_SEND_FAILED run_id=%s error_type=%s", task_id, type(error).__name__)
    # Each connection owns a FIFO lock. Different connections send in parallel,
    # so an unresponsive UI cannot prevent healthy clients receiving this event.
    await asyncio.gather(*(deliver(ws) for ws in list(stream_sockets.get(task_id, ()))))
    if event_type == "finished":
        schedule_reviewed_export(task_id)

async def run_session(session):
    try:
        await session.start()
    except Exception:
        # The pipeline records and broadcasts its startup error.
        pass

def _retry_configuration_signature():
    """Compare captured engines without recording keys in logs or checkpoints."""
    names = ("LLM_PROVIDER", "GEMINI_MODEL", "OPENCODE_MODEL", "OPENROUTER_MODEL",
             "WHISPER_MODEL_SIZE", "WHISPER_COMPUTE_TYPE", "SUPPRESSION_MODE",
             "MUSE_BROWSER_MODE", "OPENCODE_API_KEY", "OPENROUTER_API_KEY",
             "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    values = {name: getattr(settings, name, None) or os.getenv(name, "") for name in names}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


@app.post("/api/streaming/start-url")
async def start_streaming_url(req: StreamUrlRequest):
    validate_visual_translation(req.visual_translation)
    if not req.url:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp link video")

    try:
        url = downloader.normalize_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    engine, voice = validated_voice(req.voice_id, req.tts_engine, req.voice)
    config_signature = _retry_configuration_signature()
    # The same source/config must not silently create another download when the
    # previous attempt only failed in ASR/OCR/translation. Retry its saved state.
    for existing in reversed(list(active_streaming_sessions.values())):
        if (not getattr(existing, "source_url", None)
                or downloader.source_identity(existing.source_url) != downloader.source_identity(url)
                or existing.voice != voice or existing.tts_engine_name != engine
                or existing.asr_engine_name != (req.asr_engine or settings.ASR_ENGINE)
                or existing.visual_translation != req.visual_translation
                or getattr(existing, "translation_mode", "full") != req.translation_mode
                or getattr(existing, "_retry_config_signature", None) != config_signature
                or existing.is_stopped):
            continue
        if existing.is_running or (not existing.initialized and not existing.error):
            raise HTTPException(status_code=409, detail="Video này đang xử lý. Mở tác vụ hiện tại để xem tiến độ.")
        if existing.can_retry:
            try:
                await existing.retry_failed_synthesis()
            except SegmentEditConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            return {"task_id": existing.task_id, "video_url": existing.source_video_url,
                    "initial_buffer_seconds": existing.initial_buffer_seconds,
                    "progress": existing.get_progress(), "status": "started", "resumed": True}
    task_id = str(uuid.uuid4())[:8]

    # Create session
    session = create_streaming_session(
        task_id=task_id,
        video_path=None,
        initial_buffer_seconds=req.initial_buffer_seconds or settings.INITIAL_BUFFER_SECONDS,
        voice=voice,
        tts_engine_name=engine,
        asr_engine_name=req.asr_engine or settings.ASR_ENGINE,
        visual_translation=req.visual_translation,
        translation_mode=req.translation_mode,
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )
    session.auto_export_result = bool(req.visual_translation and settings.LLM_PROVIDER == "opencode")
    session.source_url, session._source_downloader = url, downloader
    session._retry_config_signature = config_signature

    async def start_source():
        try:
            await session.start_from_url(downloader, url)
        except Exception:
            # The session persists and broadcasts actionable startup errors.
            pass

    asyncio.create_task(start_source())

    return {
        "task_id": task_id,
        "video_url": None,
        "initial_buffer_seconds": session.initial_buffer_seconds,
        **session.get_progress(),
        "status": "started",
    }

async def save_uploaded_inputs(uploads, task_id):
    """Copy in bounded blocks off the ASGI loop; join before removing drafts."""
    cancelled = threading.Event()
    def copy():
        for source, target in uploads:
            with target.open("wb") as destination:
                while not cancelled.is_set():
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    destination.write(block)
                if cancelled.is_set():
                    return
    work = asyncio.create_task(asyncio.to_thread(copy))
    try:
        await asyncio.shield(work)
    except BaseException as error:
        cancelled.set()
        try:
            await asyncio.shield(work)
        except (OSError, ValueError):
            pass
        for _, target in uploads:
            target.unlink(missing_ok=True)
        if isinstance(error, (OSError, ValueError)):
            logging.getLogger("errors").error("UPLOAD_SAVE_FAILED run_id=%s error_type=%s", task_id, type(error).__name__)
            raise HTTPException(status_code=507, detail="Không lưu được video/mẫu giọng. Kiểm tra dung lượng và quyền ghi thư mục inputs.") from None
        raise


@app.post("/api/streaming/start-upload")
async def start_streaming_upload(
    file: UploadFile = File(...),
    initial_buffer_seconds: Optional[float] = Form(None, gt=0, le=120),
    voice: Optional[str] = Form(None),
    voice_id: Optional[str] = Form(None),
    tts_engine: Optional[str] = Form(None),
    asr_engine: str = Form(settings.ASR_ENGINE),
    visual_translation: bool = Form(False),
    translation_mode: Literal["preview", "full"] = Form("preview"),
    ref_audio: Optional[UploadFile] = File(None)
):
    validate_visual_translation(visual_translation)
    tts_engine, voice = validated_voice(voice_id, tts_engine, voice)
    if ref_audio and ref_audio.filename and tts_engine != "vieneu-tts":
        raise HTTPException(status_code=422, detail="Mẫu giọng riêng chỉ được hỗ trợ khi chọn VieNeu.")
    task_id = str(uuid.uuid4())[:8]
    ext = Path(file.filename or "").suffix or ".mp4"
    saved_path = settings.INPUT_DIR / f"upload_{task_id}{ext}"

    ref_audio_path = None
    uploads = [(file.file, saved_path)]
    if ref_audio and ref_audio.filename:
        ref_ext = Path(ref_audio.filename).suffix or ".wav"
        ref_path = settings.INPUT_DIR / f"ref_{task_id}{ref_ext}"
        uploads.append((ref_audio.file, ref_path))
        ref_audio_path = ref_path
    await save_uploaded_inputs(uploads, task_id)

    # Create session
    session = create_streaming_session(
        task_id=task_id,
        video_path=saved_path,
        initial_buffer_seconds=initial_buffer_seconds or settings.INITIAL_BUFFER_SECONDS,
        voice=voice,
        tts_engine_name=tts_engine,
        asr_engine_name=asr_engine,
        visual_translation=visual_translation,
        translation_mode=translation_mode,
        ref_audio=ref_audio_path,
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )
    session.auto_export_result = bool(visual_translation and settings.LLM_PROVIDER == "opencode")

    asyncio.create_task(run_session(session))

    session.source_video_url = f"/api/inputs/{saved_path.name}"

    return {
        "task_id": task_id,
        "video_url": f"/api/inputs/{saved_path.name}",
        "initial_buffer_seconds": session.initial_buffer_seconds,
        "status": "started"
    }

@app.get("/api/streaming/audio/{task_id}/{seg_id}")
async def get_segment_audio(task_id: str, seg_id: int):
    # Audio is an output of a live/saved session, not a public file server.
    # Validate both the task/segment state and the exact path recorded by that
    # session before handing it to FileResponse.  This also prevents Windows
    # backslashes in a task id from escaping the cache directory.
    if (not isinstance(task_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", task_id)
            or not isinstance(seg_id, int) or isinstance(seg_id, bool)
            or not 0 <= seg_id <= 2147483647):
        raise HTTPException(status_code=404, detail="Segment audio not found or not yet synthesized")
    session = get_streaming_session(task_id)
    segment = getattr(session, "segments", {}).get(seg_id) if session is not None else None
    if segment is None or getattr(segment, "status", "") not in {"READY", "PLAYED"}:
        raise HTTPException(status_code=404, detail="Segment audio not found or not yet synthesized")
    recorded = getattr(segment, "audio_path", None)
    if not isinstance(recorded, str) or not recorded:
        raise HTTPException(status_code=404, detail="Segment audio not found or not yet synthesized")

    cache_root = (settings.BASE_DIR / "workspace" / "cache").absolute()
    expected = cache_root / task_id / "segments" / Path(recorded).name
    try:
        path = Path(recorded)
        # Session manifests record absolute paths.  Refuse relative values so
        # the route never interprets a path relative to the process CWD.
        lexical = path if path.is_absolute() else None
        # Do not resolve before checking: resolve() hides a lexical symlink or
        # Windows junction and would make an escape look like an in-cache file.
        if (lexical is None or not re.fullmatch(rf"seg_{seg_id}(?:_[0-9a-f]{{32}})?\.wav", path.name)
                or os.path.normcase(str(lexical)) != os.path.normcase(str(expected))):
            raise ValueError("audio path is not the current session segment")

        # Walk the original lexical path all the way to the filesystem root.
        # This catches a symlink/junction at cache, task, workspace, or any
        # other ancestor before resolve() can hide the redirection.
        current = lexical
        while True:
            info = current.lstat()
            if (current.is_symlink() or bool(getattr(current, "is_junction", lambda: False)())
                    or bool(getattr(info, "st_file_attributes", 0) & 0x400)):
                raise ValueError("audio path uses a redirected filesystem entry")
            if current.parent == current:
                break
            current = current.parent
        if not lexical.is_relative_to(cache_root):
            raise ValueError("audio path is outside the cache")
        resolved = lexical.resolve(strict=True)
        expected_resolved = expected.resolve(strict=True)
        resolved_cache = cache_root.resolve(strict=True)
        resolved_task = (resolved_cache / task_id / "segments").resolve(strict=True)
        if (resolved != expected_resolved
                or not resolved.is_relative_to(resolved_task)
                or not resolved.is_relative_to(resolved_cache)
                or not resolved.is_file() or resolved.stat().st_size <= 0):
            raise ValueError("audio path is not the current session segment")
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="Segment audio not found or not yet synthesized")
    return FileResponse(resolved, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/streaming/{task_id}")
async def streaming_snapshot(task_id: str):
    session = await editable_session(task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên dịch không còn tồn tại.")
    return {
        "task_id": task_id, "initialized": session.initialized,
        "video_url": session.source_video_url,
        "voice": getattr(session, "voice", ""),
        "tts_engine": getattr(session, "tts_engine_name", ""),
        **session_output_details(session),
        **caption_style_details(session),
        "progress": session.get_progress(), "telemetry": session.get_telemetry(),
        "duration": session.total_duration, "initial_buffer_seconds": session.initial_buffer_seconds,
        "segments": [session.segment_snapshot(segment) for segment in session.segments.values()],
        "segments_count": len(session.segments), "screen_texts": session.screen_texts,
        "bgm_url": session.bgm_url, "visual_translation": session.visual_translation,
        "translation_sources": session.translation_sources, "warnings": list(session.warnings),
        "asr_engine": session.source_processing_label(),
        "vocal_removal_engine": session.vocal_suppressor.name,
        "suppression_level": f"{session.vocal_suppressor.suppression_level_db:.1f} dB",
        "suppression_rtf": session.suppression_stats.get("throughput_rtf", "0.0x"),
    }


def caption_style_details(session):
    from core.subtitle_cues import build_caption_layout, normalize_caption_style
    style = normalize_caption_style(getattr(session, "caption_style", None))
    plan = build_caption_layout([s.to_dict() for s in session.segments.values()],
        getattr(session, "screen_texts", []), getattr(session, "video_size", (1080, 1920)),
        caption_style={**style, "blur_original": True})
    return {"caption_style": style,
            "caption_style_revision": getattr(session, "caption_style_revision", 0),
            "has_subtitle_regions": bool(plan.get("source_masks")),
            "output_outdated": getattr(session, "caption_output_outdated", False)}


@app.post("/api/streaming/{task_id}/caption-style")
async def update_caption_style(task_id: str, req: CaptionStyleRequest):
    session = get_streaming_session(task_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên dịch không còn tồn tại.")
    if (session.is_running or getattr(session, "is_editing", False)
            or active_export_tasks.get(f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}):
        raise HTTPException(status_code=409, detail="Hãy chờ xử lý hoặc xuất video xong trước khi chỉnh phụ đề.")
    if (session.error or not session.segments
            or any(s.status not in ("READY", "PLAYED") for s in session.segments.values())):
        raise HTTPException(status_code=409, detail="Hãy hoàn tất bản dịch trước khi chỉnh phụ đề.")
    from core.subtitle_cues import normalize_caption_style
    style = normalize_caption_style(req.model_dump())
    previous = caption_style_details(session)
    if style["blur_original"] and not previous["has_subtitle_regions"]:
        raise HTTPException(status_code=422, detail="Chưa xác định được vùng phụ đề gốc đủ rõ để làm mờ.")
    if style != previous["caption_style"]:
        fields = ("caption_style", "caption_style_revision", "caption_output_outdated",
                  "output_video_url", "output_filename", "output_review_url", "auto_export_signature")
        prior = {key: getattr(session, key) for key in fields if hasattr(session, key)}
        session.caption_style = style
        session.caption_style_revision = previous["caption_style_revision"] + 1
        session.caption_output_outdated = True
        session._invalidate_output()
        try:
            # A style update acknowledged by the UI must survive a full exit.
            session.persist()
        except (OSError, ValueError, TypeError) as error:
            for key, value in prior.items():
                setattr(session, key, value)
            for key in set(fields) - prior.keys():
                session.__dict__.pop(key, None)
            logging.getLogger("errors").error(
                "CAPTION_STYLE_SAVE_FAILED run_id=%s error_type=%s", task_id, type(error).__name__)
            raise HTTPException(status_code=507, detail="Chưa lưu được phụ đề xuống ổ đĩa. "
                "Cấu hình và video trước đó được giữ; kiểm tra dung lượng/quyền ghi rồi thử lại.") from None
        # A previous export is valid only for its old style. Drop its published
        # task entry so task polling cannot present it as the new result.
        active_export_tasks.pop(f"export_{task_id}", None)
    payload = {**caption_style_details(session), **session_output_details(session),
               "segments": [session.segment_snapshot(s) for s in session.segments.values()]}
    await broadcast_session_event(task_id, "caption_style", payload)
    return payload


@app.post("/api/streaming/{task_id}/retry")
async def retry_streaming_synthesis(task_id: str):
    session = get_streaming_session(task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên dịch không còn tồn tại.")
    if active_export_tasks.get(f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}:
        raise HTTPException(status_code=409, detail="Hãy chờ tác vụ xuất video kết thúc trước khi thử lại.")
    try:
        progress = await session.retry_failed_synthesis()
    except SegmentEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"task_id": task_id, "status": "retrying", "progress": progress}


@app.post("/api/streaming/{task_id}/translate-full")
async def translate_full_video(task_id: str):
    session = await editable_session(task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên dịch không còn tồn tại.")
    if active_export_tasks.get(f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}:
        raise HTTPException(status_code=409, detail="Hãy chờ xuất video kết thúc trước khi dịch tiếp.")
    try:
        progress = await session.translate_full()
    except SegmentEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    persist_session(session)
    return {"task_id": task_id, "status": "started", "progress": progress}

@app.post("/api/streaming/{task_id}/review")
async def review_streaming_translation(task_id: str):
    session = get_streaming_session(task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên dịch không còn tồn tại.")
    if active_export_tasks.get(f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}:
        raise HTTPException(status_code=409, detail="Hãy chờ xuất xong trước khi AI kiểm tra lại.")
    try:
        progress = await session.start_automatic_review()
    except SegmentEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    session.auto_export_result = True
    return {"task_id": task_id, "status": "reviewing", "progress": progress}


@app.patch("/api/streaming/{task_id}/speaker-confirmation")
async def confirm_streaming_speaker(task_id: str, req: SpeakerConfirmationRequest):
    session = await editable_session(task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên dịch không còn tồn tại.")
    if active_export_tasks.get(f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}:
        raise HTTPException(status_code=409, detail="Hãy chờ xuất video kết thúc trước khi xác nhận người nói.")
    try:
        result = session.confirm_speaker(**req.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu thoại.") from None
    except SegmentEditConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ProjectEditSaveError as error:
        raise HTTPException(status_code=507, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    active_export_tasks.pop(f"export_{task_id}", None)
    try:
        await broadcast_session_event(task_id, "speaker_confirmation", result)
    except Exception as error:
        logging.getLogger("errors").warning("SPEAKER_CONFIRMATION_EVENT_FAILED run_id=%s error_type=%s",
            task_id, type(error).__name__)
    return result


@app.patch("/api/streaming/{task_id}/segments/{segment_id}")
async def edit_streaming_segment(task_id: str, segment_id: int, req: SegmentEditRequest):
    if not 0 <= segment_id <= 2147483647:
        raise HTTPException(status_code=422, detail="Mã câu thoại không hợp lệ.")
    session = get_streaming_session(task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên dịch không còn tồn tại.")
    if active_export_tasks.get(f"export_{task_id}", {}).get("status") in {"RUNNING", "CANCELLING"}:
        raise HTTPException(status_code=409, detail="Video đang được xuất. Hãy chờ xuất xong trước khi sửa lời thoại.")
    try:
        segment = await session.edit_segment(segment_id, req.final_vi, confirm_silence=req.confirm_silence)
    except ProjectEditSaveError as error:
        raise HTTPException(status_code=507, detail=str(error)) from None
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu thoại.") from None
    except SegmentEditConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError:
        raise HTTPException(status_code=422, detail="Nội dung hoặc thời lượng câu thoại không hợp lệ. Nhập từ 1 đến 2.000 ký tự.") from None
    except Exception as error:
        attempt_id = uuid.uuid4().hex[:12]
        # Log frame locations rather than exception text/locals: provider
        # errors can echo transcript content or credentials. The attempt ID
        # links this user-visible failure to its actual runtime path.
        frames = traceback.extract_tb(error.__traceback__)
        trace = " > ".join(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                           for frame in frames[-12:])
        logging.getLogger("errors").error(
            "TRANSCRIPT_EDIT_FAILED run_id=%s segment_id=%s attempt_id=%s provider=%s error_type=%s trace=%s",
            task_id, segment_id, attempt_id, getattr(session, "tts_engine_name", "unknown"),
            type(error).__name__, trace)
        raise HTTPException(status_code=503, detail="Chưa tạo lại được giọng đọc. Nội dung và âm thanh cũ vẫn được giữ; hãy thử lại. "
                            f"Mã lỗi: {attempt_id}.") from None
    return {"segment": session.caption_metadata(segment), "screen_texts": getattr(session, "screen_texts", [])}

@app.get("/api/streaming/bgm/{task_id}")
async def get_streaming_bgm(task_id: str):
    session = get_streaming_session(task_id)
    root = (settings.BASE_DIR / "workspace" / "cache").resolve()
    value = getattr(session, "bgm_audio_path", None)
    bgm_path = Path(value) if value else root / task_id / "bgm_suppressed.ogg"
    if (bgm_path.is_symlink() or not bgm_path.resolve().is_relative_to(root)
            or not bgm_path.is_file() or bgm_path.stat().st_size <= 0):
        raise HTTPException(status_code=404, detail="BGM stream not found or still generating")
    return FileResponse(bgm_path, media_type="audio/ogg")

@app.post("/api/streaming/seek")
async def streaming_seek(req: SeekRequest):
    session = get_streaming_session(req.task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await session.seek(req.time)
    return {"status": "ok", "seek_time": req.time}

@app.post("/api/streaming/export-hq")
async def export_hq(req: ExportHQRequest):
    session = get_streaming_session(req.task_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if getattr(session, "_chunked_source_started", False):
        from core.streaming.chunked_source import ensure_source_identity
        try:
            ensure_source_identity(session)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(status_code=409, detail="Video nguồn đã thay đổi hoặc mất; hãy tạo tác vụ mới trước khi xuất.") from None
    if getattr(session, "is_editing", False):
        raise HTTPException(status_code=409, detail="Đang lưu lời thoại và tạo lại giọng đọc. Hãy chờ lưu xong trước khi xuất.")
    if getattr(session, "_persistence_capacity_failed", False):
        raise HTTPException(status_code=409, detail="Dự án đạt giới hạn dữ liệu lưu. Phần đã lưu được giữ; hãy dùng video ngắn hơn.")
    if (getattr(session, "translation_mode", "full") == "preview"
            and not getattr(session, "_visual_prepass_complete", False)):
        raise HTTPException(status_code=409, detail="Đây là bản xem trước. Bấm Dịch toàn bộ trước khi xuất video đầy đủ.")
    missing_speech = missing_spoken_output_ids(session.segments.values())
    if missing_speech:
        raise HTTPException(status_code=409, detail=missing_speech_message(missing_speech))
    review_status = getattr(session, "review_summary", {}).get("status")
    if review_status in {"failed", "running", "incomplete"}:
        raise HTTPException(status_code=409, detail="AI kiểm tra lại chưa hoàn tất. Bấm AI kiểm tra lại để tiếp tục trước khi xuất.")
    uncertain = [s for s in session.segments.values() if getattr(s, "needs_review", False)]
    if uncertain and not (review_status == "completed" and all(
            (getattr(s, "verification", None) or {}).get("status") == "unresolved" for s in uncertain)):
        raise HTTPException(status_code=409, detail="Bản dịch chưa được AI kiểm tra đầy đủ. Bấm AI kiểm tra lại trước khi xuất.")
    if session.is_running or session.error or any(
        s.status not in ("READY", "PLAYED") for s in session.segments.values()
    ):
        raise HTTPException(status_code=409, detail="Hãy chờ dịch xong toàn bộ video trước khi xuất.")
    export_signature = export_revision_signature(session)
    segments_data = [dict(s.to_dict(), audio_path=s.audio_path)
                     for s in session.segments.values() if s.status in ["READY", "PLAYED"]]
    if not segments_data:
        raise HTTPException(status_code=400, detail="Chưa có câu thoại nào sẵn sàng để xuất HQ")

    export_id = f"export_{req.task_id}"
    if active_export_tasks.get(export_id, {}).get("status") in {"RUNNING", "CANCELLING"}:
        raise HTTPException(status_code=409, detail="Video này đang được xuất. Hãy chờ tác vụ hiện tại kết thúc.")
    active_export_tasks[export_id] = {
        "task_id": export_id,
        "parent_session_id": req.task_id,
        "status": "RUNNING",
        "progress": None,
        "stage": "Khởi động xuất video HQ...",
        "start_time": time.time(),
        "duration": session.total_duration,
        "video_url": "",
        "output_filename": "",
        **review_result_details(session),
        "cancelled": False
    }
    session.export_task = asyncio.current_task()

    loop = asyncio.get_event_loop()

    def _prog_cb(pct, stage: str):
        if export_id in active_export_tasks:
            active_export_tasks[export_id]["progress"] = pct
            active_export_tasks[export_id]["stage"] = stage
        asyncio.run_coroutine_threadsafe(
            broadcast_session_event(req.task_id, "export_progress", {"progress": pct, "stage": stage}),
            loop
        )

    def _cancel_chk():
        return active_export_tasks.get(export_id, {}).get("cancelled", False)

    async def _commit_output(rendered, final):
        # Same event loop as cancellation: no await between the decision,
        # atomic publication and commit flag. Late Stop cannot undo this file.
        if _cancel_chk() or getattr(session, "is_stopped", False):
            raise RuntimeError("Export cancelled before publishing")
        missing_speech = missing_spoken_output_ids(session.segments.values())
        if missing_speech:
            raise RuntimeError(missing_speech_message(missing_speech))
        if export_revision_signature(session) != export_signature:
            raise RuntimeError("Lời thoại hoặc cấu hình đã thay đổi trong khi xuất; giữ video trước đó và xuất lại từ bản đang lưu.")
        rendered.replace(final)
        active_export_tasks[export_id]["published"] = True

    def _publish_output(rendered, final):
        asyncio.run_coroutine_threadsafe(_commit_output(rendered, final), loop).result()

    render_worker = None
    try:
        await broadcast_session_event(req.task_id, "export_progress", {
            "progress": None, "stage": "Đang tạo video kết quả từ bản dịch đã xử lý...",
        })
        exporter = HQExporter()
        render_worker = asyncio.create_task(asyncio.to_thread(
            exporter.export,
            task_id=req.task_id,
            video_path=session.video_path,
            segments=segments_data,
            total_duration=session.total_duration,
            # Keep the Chinese subtitles, placing Vietnamese beneath them.
            mask_chinese=False,
            screen_texts=(getattr(session, "screen_texts", []) if req.translate_screen_text else [])
                if getattr(session, "visual_translation", False) else None,
            progress_callback=_prog_cb,
            cancel_check=_cancel_chk,
            publish_callback=_publish_output,
            caption_style=dict(getattr(session, "caption_style", {}) or {}),
            source_screen_texts=getattr(session, "screen_texts", []),
        ))
        try:
            result = await asyncio.shield(render_worker)
        except asyncio.CancelledError:
            if not active_export_tasks[export_id].get("published"):
                raise
            # Publication already committed before Stop. Drain the worker and
            # publish its valid result rather than report a false cancellation.
            while not render_worker.done():
                try:
                    await asyncio.shield(render_worker)
                except asyncio.CancelledError:
                    continue
            result = render_worker.result()

        if not active_export_tasks[export_id].get("published") and (_cancel_chk() or getattr(session, "is_stopped", False)):
            raise RuntimeError("Export cancelled before publishing")

        review_url = ""
        metadata_warning = ""
        sidecar_warning = ("Video MP4 đã xuất và có thể tải, nhưng chưa lưu được báo cáo kiểm tra. "
                           "Kiểm tra dung lượng/quyền ghi thư mục kết quả rồi xuất lại để lưu báo cáo.")
        if review_status == "completed":
            sidecar_name = f"{Path(result['output_filename']).stem}.review.json"
            sidecar_path = settings.OUTPUT_DIR / sidecar_name
            sidecar_temp = sidecar_path.with_name(f".{sidecar_name}.{uuid.uuid4().hex}.tmp")
            try:
                sidecar_temp.write_text(json.dumps(review_sidecar_payload(session), ensure_ascii=False, indent=2), encoding="utf-8")
                sidecar_temp.replace(sidecar_path)
                review_url = f"/api/outputs/{sidecar_name}"
                if sidecar_warning in session.warnings:
                    session.warnings.remove(sidecar_warning)
            except (OSError, ValueError, TypeError) as error:
                # The validated MP4 has already been published. Report this
                # separate metadata failure without discarding a usable video
                # or linking a stale report from an earlier export.
                metadata_warning = sidecar_warning
                if sidecar_warning not in session.warnings:
                    session.warnings.append(sidecar_warning)
                logging.getLogger("errors").warning(
                    "EXPORT_REPORT_SAVE_FAILED run_id=%s error_type=%s errno=%s",
                    export_id, type(error).__name__, getattr(error, "errno", None))
            finally:
                try:
                    sidecar_temp.unlink(missing_ok=True)
                except OSError as error:
                    logging.getLogger("errors").warning(
                        "EXPORT_REPORT_TEMP_CLEANUP_FAILED run_id=%s error_type=%s",
                        export_id, type(error).__name__)
        session.output_video_url = f"/api/outputs/{result['output_filename']}"
        session.output_filename = result["output_filename"]
        session.output_review_url = review_url
        session.caption_output_outdated = False
        await session.report_progress("complete", "MP4 đã xuất và giải mã kiểm tra thành công.", 100)
        warnings_before_save = list(session.warnings)
        persist_session(session)
        save_warnings = [warning for warning in session.warnings if warning not in warnings_before_save]
        metadata_warning = " ".join(filter(None, [metadata_warning, *save_warnings]))
        active_export_tasks[export_id].update(
            status="COMPLETED", progress=100,
            stage=metadata_warning or "Xuất video hoàn tất thành công!",
            video_url=session.output_video_url, output_filename=session.output_filename,
            review_url=review_url, warnings=list(session.warnings), metadata_warning=metadata_warning)
        output_details = {**session_output_details(session), "warnings": list(session.warnings),
                          "metadata_warning": metadata_warning}
        await broadcast_session_event(req.task_id, "result_ready", output_details)

        return {
            "status": "ok",
            "export_id": export_id,
            "output_filename": result["output_filename"],
            "video_url": f"/api/outputs/{result['output_filename']}",
            "elapsed_seconds": result["elapsed_seconds"],
            **output_details,
            "review_url": review_url,
        }
    except asyncio.CancelledError:
        active_export_tasks[export_id].update(
            cancelled=True, status="CANCELLING", stage="Đang dừng xuất video...")
        # Cancelling an asyncio.to_thread waiter does not stop the media thread.
        # Keep ownership until the exporter acknowledges its cancel callback.
        while render_worker is not None and not render_worker.done():
            try:
                await asyncio.shield(render_worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        active_export_tasks[export_id].update(status="CANCELLED", stage="Đã hủy xuất video")
        raise
    except Exception as error:
        status_name = "CANCELLED" if active_export_tasks.get(export_id, {}).get("cancelled") else "FAILED"
        stage = active_export_tasks.get(export_id, {}).get("stage", "export")
        message = "Đã hủy xuất video" if status_name == "CANCELLED" else export_failure(error, export_id, stage)
        if export_id in active_export_tasks:
            active_export_tasks[export_id]["status"] = status_name
            active_export_tasks[export_id]["stage"] = message
        await broadcast_session_event(req.task_id, "result_error", {"message": message, **session_output_details(session)})
        persist_session(session)
        failure = HTTPException(status_code=409 if status_name == "CANCELLED" else 500, detail=message)
        failure.result_error_reported = True
        raise failure from None
    finally:
        session.export_task = None

@app.get("/api/streaming/export-hq/status/{task_id}")
async def get_export_hq_status(task_id: str):
    export_id = task_id if task_id.startswith("export_") else f"export_{task_id}"
    session = get_streaming_session(export_id.removeprefix("export_"))
    if session is not None:
        await validate_live_output(session)
    if export_id in active_export_tasks:
        return active_export_tasks[export_id]
    raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ export")

@app.post("/api/streaming/export-hq/cancel/{task_id}")
async def cancel_export_hq(task_id: str):
    export_id = task_id if task_id.startswith("export_") else f"export_{task_id}"
    if export_id in active_export_tasks:
        if (active_export_tasks[export_id].get("published")
                or active_export_tasks[export_id]["status"] not in {"RUNNING", "CANCELLING"}):
            raise HTTPException(status_code=409, detail="Tác vụ xuất đã kết thúc")
        active_export_tasks[export_id]["cancelled"] = True
        active_export_tasks[export_id]["status"] = "CANCELLING"
        active_export_tasks[export_id]["stage"] = "Đang dừng xuất video..."
        return {"status": "ok", "message": "Đã yêu cầu hủy xuất video"}
    raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ export")

class StreamLocalFileRequest(BaseModel):
    file_path: str
    initial_buffer_seconds: Optional[float] = Field(default=None, gt=0, le=120)
    voice: Optional[str] = None
    voice_id: Optional[str] = None
    tts_engine: Optional[str] = None
    asr_engine: Optional[str] = None
    visual_translation: bool = False
    translation_mode: Literal["preview", "full"] = "preview"


def validate_visual_translation(enabled):
    if enabled:
        from core.video_intelligence import validate_visual_provider, VideoIntelligenceError
        try:
            validate_visual_provider()
        except VideoIntelligenceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None


def reject_private_media_path(path):
    if Path(path).resolve().is_relative_to((settings.WORKSPACE_DIR / "private").resolve()):
        raise HTTPException(status_code=404, detail="Không tìm thấy video.")


@app.post("/api/streaming/start-local-file")
async def start_streaming_local_file(req: StreamLocalFileRequest):
    validate_visual_translation(req.visual_translation)
    reject_private_media_path(req.file_path)
    p = Path(req.file_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"File không tồn tại: {req.file_path}")

    engine, voice = validated_voice(req.voice_id, req.tts_engine, req.voice)
    task_id = str(uuid.uuid4())[:8]
    session = create_streaming_session(
        task_id=task_id,
        video_path=p,
        initial_buffer_seconds=req.initial_buffer_seconds or settings.INITIAL_BUFFER_SECONDS,
        voice=voice,
        tts_engine_name=engine,
        asr_engine_name=req.asr_engine or settings.ASR_ENGINE,
        visual_translation=req.visual_translation,
        translation_mode=req.translation_mode,
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )
    session.source_video_url = f"/api/local-file?path={quote(p.as_posix(), safe='')}"
    session.auto_export_result = bool(req.visual_translation and settings.LLM_PROVIDER == "opencode")
    asyncio.create_task(run_session(session))

    return {
        "task_id": task_id,
        "video_url": session.source_video_url,
        "initial_buffer_seconds": session.initial_buffer_seconds,
        "status": "started"
    }

@app.get("/api/local-file")
async def get_local_file(path: str):
    reject_private_media_path(path)
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="File không tồn tại")
    return FileResponse(p)


class PreviewRequest(BaseModel):
    file_path: Optional[str] = None
    task_id: Optional[str] = None
    output_filename: Optional[str] = None
    # For a chunked task this asks the Qt compatibility preview to transcode
    # only the prepared prefix.  Leaving it unset preserves the full-result
    # preview contract used by exported MP4 files.
    coverage_seconds: Optional[float] = Field(default=None, gt=0, le=86400)
    start_seconds: float = Field(default=0, ge=0, le=86400)
    end_seconds: Optional[float] = Field(default=None, gt=0, le=86400)


@app.post("/api/preview")
async def create_media_preview(req: PreviewRequest):
    session = None
    if req.output_filename is not None:
        name = req.output_filename
        if (req.file_path is not None or req.task_id is not None or req.coverage_seconds is not None
                or req.start_seconds != 0 or req.end_seconds is not None or not name
                or name != name.strip() or Path(name).suffix.lower() != ".mp4"
                or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)):
            raise HTTPException(status_code=400, detail="Tên video kết quả không hợp lệ")
        try:
            root = settings.OUTPUT_DIR.resolve(strict=True)
            candidate = root / name
            info = candidate.lstat()
            source = candidate.resolve(strict=True)
            if (candidate.is_symlink() or candidate.is_junction()
                    or getattr(info, "st_file_attributes", 0) & 0x400
                    or source.parent != root or not source.is_file()):
                raise ValueError("Invalid output")
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(status_code=404, detail="Không tìm thấy video kết quả") from None
    else:
        session = get_streaming_session(req.task_id) if req.task_id else None
        source = session.video_path if session else req.file_path
    if not source:
        raise HTTPException(status_code=404, detail="Không tìm thấy video cần xem trước")
    reject_private_media_path(source)
    if req.end_seconds is not None and req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=422, detail="Khoảng xem trước phải kết thúc sau vị trí bắt đầu")
    coverage_seconds = req.coverage_seconds
    start_seconds = req.start_seconds
    end_seconds = req.end_seconds
    # A compatibility fallback must never launch a full multi-hour transcode.
    # For a chunked task keep the request inside the globally measured source
    # cursor; when no task exists use one short prefix until the user starts it.
    # Exported result previews bypass this block and retain full-file semantics.
    if req.output_filename is None:
        try:
            prepared = float(getattr(session, "_source_prepared_seconds", 0) or 0)
        except (TypeError, ValueError):
            prepared = 0
        if prepared > 0:
            if start_seconds >= prepared:
                raise HTTPException(status_code=409, detail="Đoạn xem trước chưa được chuẩn bị")
            requested_end = end_seconds
            if requested_end is None:
                requested_end = start_seconds + (coverage_seconds or DEFAULT_COMPATIBILITY_SECONDS)
            end_seconds = min(requested_end, prepared)
            if end_seconds <= start_seconds:
                raise HTTPException(status_code=409, detail="Đoạn xem trước chưa được chuẩn bị")
            coverage_seconds = end_seconds - start_seconds
        elif coverage_seconds is None and end_seconds is None:
            coverage_seconds = DEFAULT_COMPATIBILITY_SECONDS
            end_seconds = start_seconds + coverage_seconds
        elif coverage_seconds is None:
            coverage_seconds = end_seconds - start_seconds
    try:
        if coverage_seconds is None and start_seconds == 0 and end_seconds is None:
            # Preserve the original full-result call shape for integrations
            # that provide a minimal PreviewManager implementation.
            return preview_manager.start(source)
        return preview_manager.start(source, coverage_seconds=coverage_seconds,
                                     start_seconds=start_seconds, end_seconds=end_seconds)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Video nguồn không tồn tại") from None
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.post("/api/preview/upload")
async def create_uploaded_preview(file: UploadFile = File(...),
                                  coverage_seconds: Optional[float] = Form(default=None, gt=0, le=86400),
                                  start_seconds: float = Form(default=0, ge=0, le=86400),
                                  end_seconds: Optional[float] = Form(default=None, gt=0, le=86400)):
    if end_seconds is not None and end_seconds <= start_seconds:
        raise HTTPException(status_code=422, detail="Khoảng xem trước phải kết thúc sau vị trí bắt đầu")
    try:
        if coverage_seconds is None:
            coverage_seconds = DEFAULT_COMPATIBILITY_SECONDS
        return await asyncio.to_thread(preview_manager.start_upload, file.file,
                                       Path(file.filename or "video").suffix, coverage_seconds,
                                       start_seconds, end_seconds)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@app.get("/api/preview/{preview_id}")
async def get_media_preview_status(preview_id: str):
    try:
        return preview_manager.status(preview_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Bản xem trước không tồn tại") from None


@app.delete("/api/preview/{preview_id}")
async def cancel_media_preview(preview_id: str):
    try:
        return preview_manager.cancel(preview_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Bản xem trước không tồn tại") from None


@app.get("/api/preview/{preview_id}/media")
async def get_compatible_preview(preview_id: str):
    try:
        return FileResponse(preview_manager.media(preview_id), media_type="video/webm")
    except KeyError:
        raise HTTPException(status_code=404, detail="Bản xem trước chưa sẵn sàng") from None

@app.get("/api/diagnostics/logs")
async def get_diagnostics_logs(category: Literal["app", "ai", "errors", "pipeline"] = "app", lines: int = 100):
    log_file = settings.WORKSPACE_DIR / "logs" / f"{category}.log"
    if not log_file.exists():
        return {"category": category, "logs": "(Không có dữ liệu log)"}
    try:
        content = log_file.read_text(encoding="utf-8", errors="ignore")
        log_lines = content.strip().splitlines()[-max(1, min(lines, 1000)):]
        return {"category": category, "logs": "\n".join(log_lines) if log_lines else "(Log rỗng)"}
    except Exception as e:
        return {"category": category, "logs": f"Lỗi đọc log: {e}"}

@app.post("/api/diagnostics/logs/clear")
async def clear_diagnostics_logs(category: Optional[Literal["app", "ai", "errors", "pipeline"]] = None):
    log_dir = settings.WORKSPACE_DIR / "logs"
    if category:
        targets = [log_dir / f"{category}.log"]
    else:
        targets = list(log_dir.glob("*.log"))
    for t in targets:
        if t.exists():
            t.write_text("", encoding="utf-8")
    return {"status": "ok", "message": "Đã xóa log thành công"}

@app.post("/api/diagnostics/open-folder")
async def open_diagnostics_folder():
    log_dir = settings.WORKSPACE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(log_dir))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/test-gemini")
async def test_gemini_connection():
    gemini_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return {"ok": False, "error_code": "missing_key",
                "error": "Chưa có GEMINI_API_KEY. Vui lòng cấu hình trong Cài đặt."}
    model = settings.GEMINI_MODEL

    def ping():
        from google import genai
        client = genai.Client(
            api_key=gemini_key,
            http_options={"timeout": 20000, "retry_options": {"attempts": 1}},
        )
        try:
            response = client.models.generate_content(
                model=model,
                contents="Trả về chữ OK.",
                config={"temperature": 0.1, "max_output_tokens": 128},
            )
            candidates = getattr(response, "candidates", None)
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                return False
            if candidates:
                reason = getattr(candidates[0], "finish_reason", None)
                reason = getattr(reason, "value", reason)
                if reason not in (None, "STOP"):
                    return False
            return True
        finally:
            client.close()

    try:
        started = time.monotonic()
        usable = await asyncio.to_thread(ping)
        if not usable:
            return {"ok": False, "error_code": "invalid_response",
                    "error": "Gemini đã phản hồi nhưng chưa trả được nội dung hoàn chỉnh. Kiểm tra model rồi thử lại."}
        return {
            "ok": True,
            "model": model,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except Exception as exc:
        # SDK errors can include request data or credentials; return fixed messages only.
        code = str(getattr(exc, "code", ""))
        details = str(getattr(exc, "details", "")).upper()
        if code in {"401", "403"} or (code == "400" and "API_KEY_INVALID" in details):
            return {"ok": False, "error_code": "authentication",
                    "error": "Gemini từ chối API key hoặc quyền truy cập. Kiểm tra key trong Google AI Studio và giới hạn của key."}
        if code == "429":
            return {"ok": False, "error_code": "quota",
                    "error": "Gemini đã hết hạn mức hoặc đang giới hạn tốc độ. Kiểm tra quota trong Google AI Studio rồi thử lại."}
        if code == "404":
            return {"ok": False, "error_code": "model_unavailable",
                    "error": "Model Gemini không tồn tại hoặc key chưa được dùng model này. Chọn model khác trong Cài đặt."}
        if code == "503":
            return {"ok": False, "error_code": "busy",
                    "error": "Model Gemini đang quá tải hoặc tạm ngừng phục vụ. Đợi một lúc rồi thử lại, hoặc chọn model khác trong Cài đặt."}
        return {"ok": False, "error_code": "connection_failed",
                "error": "Chưa kết nối được Gemini. Kiểm tra mạng và model đã chọn rồi thử lại."}


@app.post("/api/test-opencode")
async def test_opencode_connection():
    try:
        client = OpenCodeZenClient(model=settings.OPENCODE_MODEL, timeout=settings.OPENCODE_TIMEOUT, max_retries=0)
        started = time.monotonic()
        await asyncio.to_thread(client.translate, "Reply with OK.", max_tokens=16)
        return {"ok": True, "model": client.model,
                "latency_ms": round((time.monotonic() - started) * 1000)}
    except OpenCodeClientError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "Không thể kiểm tra OpenCode lúc này. Vui lòng thử lại."}


@app.post("/api/test-openrouter")
async def test_openrouter_connection():
    try:
        client = OpenRouterFreeClient(model=settings.OPENROUTER_MODEL, timeout=settings.OPENCODE_TIMEOUT)
        started = time.monotonic()
        await asyncio.to_thread(client.translate, "Reply with OK.", max_tokens=1024)
        return {"ok": True, "model": client.model,
                "latency_ms": round((time.monotonic() - started) * 1000)}
    except OpenRouterClientError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "Không thể kiểm tra OpenRouter Free lúc này. Vui lòng thử lại."}


def _public_muse_status(state):
    # Only expose lifecycle flags, never profile paths, browser text or credentials.
    result = {name: state.get(name) is True for name in
              ("installed", "running", "logged_in", "composer_ready")}
    mode = state.get("browser_mode")
    result["browser_mode"] = mode if mode in {"dedicated", "existing"} else "dedicated"
    return result


def _known_muse_failure(error):
    # Reconstruct messages only from our code allowlist, never echo exception text.
    code = error.code if isinstance(error, MuseError) else None
    if code in MUSE_ERRORS:
        return {"ok": False, "error_code": code, "error": MUSE_ERRORS[code]}
    return None


@app.get("/api/muse/status")
async def muse_status():
    try:
        state = await asyncio.to_thread(muse_service.status)
        return {"ok": True, **_public_muse_status(state)}
    except Exception:
        return {"ok": False, "error_code": "status_failed",
                "error": "Chưa kiểm tra được Muse. Hãy kết nối lại rồi thử lại."}


@app.post("/api/muse/login")
async def muse_login():
    try:
        state = await asyncio.to_thread(muse_service.start_login)
        return {"ok": True, **_public_muse_status(state)}
    except Exception as error:
        known = _known_muse_failure(error)
        if known:
            return known
        return {"ok": False, "error_code": "login_failed",
                "error": "Chưa mở được Muse. Chạy setup_muse.bat, kiểm tra Chrome rồi thử kết nối lại."}


@app.post("/api/muse/stop")
async def muse_stop():
    try:
        await asyncio.to_thread(muse_service.stop)
        state = await asyncio.to_thread(muse_service.status)
        return {"ok": True, **_public_muse_status(state)}
    except Exception:
        return {"ok": False, "error_code": "stop_failed",
                "error": "Chưa đóng được phiên Muse. Hãy đóng cửa sổ Muse rồi thử lại."}


@app.post("/api/test-muse")
async def test_muse_connection():
    try:
        state = await asyncio.to_thread(muse_service.status)
        if not state.get("installed"):
            return {"ok": False, "error_code": "not_installed",
                    "error": "Chưa cài cầu nối Muse. Chạy setup_muse.bat trước."}
        if not state.get("logged_in"):
            return {"ok": False, "error_code": "login_required",
                    "error": "Bấm Đăng nhập Muse và đăng nhập Meta trong cửa sổ Chrome trước."}
        if not state.get("composer_ready"):
            return {"ok": False, "error_code": "not_ready",
                    "error": "Muse chưa sẵn sàng. Mở cửa sổ Muse, hoàn tất bước đang hiển thị rồi thử lại."}
        started = time.monotonic()
        reply = await asyncio.to_thread(muse_service.translate, "Reply with exactly OK.")
        if not isinstance(reply, str) or reply.strip().rstrip(".").upper() != "OK":
            return {"ok": False, "error_code": "unexpected_reply",
                    "error": "Muse đã trả lời nhưng chưa đúng yêu cầu kiểm tra. Kiểm tra cửa sổ Muse rồi thử lại."}
        # Browser access cannot attest which underlying model the Muse account uses.
        return {"ok": True, "model": "muse-browser",
                "latency_ms": round((time.monotonic() - started) * 1000)}
    except Exception as error:
        known = _known_muse_failure(error)
        if known:
            return known
        return {"ok": False, "error_code": "connection_failed",
                "error": "Muse chưa hoàn tất kiểm tra. Kiểm tra đăng nhập hoặc yêu cầu xác nhận trong Chrome rồi thử lại."}


@app.websocket("/ws/stream/{task_id}")
async def websocket_stream(websocket: WebSocket, task_id: str):
    await websocket.accept()
    if task_id not in stream_sockets:
        stream_sockets[task_id] = []
    stream_sockets[task_id].append(websocket)

    try:
        session = get_streaming_session(task_id)
        if session:
            if hasattr(session, "get_progress"):
                await _send_stream_payload(task_id, websocket, {"type": "progress", "task_id": task_id, **session.get_progress()})
            if getattr(session, "source_video_url", None):
                await _send_stream_payload(task_id, websocket, {"type": "source_ready", "task_id": task_id, "video_url": session.source_video_url})
        if session and getattr(session, "initialized", True):
            # Send current state immediately upon connection
            await _send_stream_payload(task_id, websocket, {
                "type": "init",
                "task_id": task_id,
                "duration": session.total_duration,
                "segments_count": len(session.segments),
                "segments": [session.segment_snapshot(s) for s in session.segments.values()],
                "screen_texts": getattr(session, "screen_texts", []),
                **caption_style_details(session),
                "visual_translation": getattr(session, "visual_translation", False),
                "translation_sources": getattr(session, "translation_sources", []),
                "asr_engine": session.source_processing_label() if hasattr(session, "source_processing_label") else "",
                "warnings": list(getattr(session, "warnings", [])),
                "initial_buffer_seconds": session.initial_buffer_seconds,
                "bgm_url": session.bgm_url,
                "vocal_removal_engine": session.vocal_suppressor.name,
                "suppression_level": f"{session.vocal_suppressor.suppression_level_db:.1f} dB",
                "suppression_rtf": session.suppression_stats.get("throughput_rtf", "75.0x")
            })
            await _send_stream_payload(task_id, websocket, {
                "type": "telemetry",
                "task_id": task_id,
                **session.get_telemetry()
            })
            if not session.error and session.first_play_emitted:
                await _send_stream_payload(task_id, websocket, {"type": "ready_to_play", "task_id": task_id})
        if session and session.error:
            await _send_stream_payload(task_id, websocket, {"type": "error", "message": session.error, "task_id": task_id})

        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                continue
            act = data.get("type") or data.get("action")
            sess = get_streaming_session(task_id)
            if not sess:
                continue

            if act == "playback_position":
                sess.update_playback_position(float(data.get("time", 0.0)))
            elif act == "seek":
                await sess.seek(float(data.get("time", 0.0)))
            elif act == "pause":
                if sess.is_running and getattr(sess, "initialized", True):
                    await pause_task(task_id)
            elif act == "resume":
                if sess.is_running:
                    await resume_task(task_id)
            elif act == "stop":
                await stop_task(task_id)
    except WebSocketDisconnect:
        pass
    finally:
        _remove_stream_socket(task_id, websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
