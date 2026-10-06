import asyncio
import os
import shutil
import stat
import uuid
import time
from pathlib import Path
from urllib.parse import quote
from typing import Dict, Any, Optional, List, Literal
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel, Field

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
)
from core.streaming.export import HQExporter
from core.media_preview import preview_manager
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
    return detect_hardware()

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

@app.get("/api/tasks")
async def list_tasks():
    tasks = []

    # 1. Streaming Realtime Sessions
    for task_id, sess in list(active_streaming_sessions.items()):
        ready_cnt = sum(1 for s in sess.segments.values() if s.status in ["READY", "PLAYED"])
        tot_cnt = max(1, len(sess.segments))
        pct = int((ready_cnt / tot_cnt) * 100) if tot_cnt > 0 else 0

        status_str = "STOPPED" if getattr(sess, "is_stopped", False) else ("FAILED" if sess.error else ("PAUSED" if sess.is_paused else ("RUNNING" if sess.is_running else "COMPLETED")))
        progress = sess.get_progress() if hasattr(sess, "get_progress") else {
            "progress_pct": pct, "status": status_str,
            "stage": sess.error or f"Đã dịch {ready_cnt}/{tot_cnt} câu ({pct}%)",
            "can_pause": status_str == "RUNNING",
        }
        status_str = progress["status"]
        tasks.append({
            "task_id": task_id,
            "task_type": "Realtime Dubbing",
            "status": status_str,
            **progress,
            "duration": sess.total_duration,
            "elapsed_seconds": round(time.time() - sess.start_wall_time, 1) if sess.start_wall_time else 0,
            "video_url": "",  # A completed translation is not an exported dubbed video.
            "can_resume": status_str == "PAUSED",
            "can_stop": status_str in ["RUNNING", "PAUSED"]
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
            "can_pause": False,
            "can_resume": False,
            "can_stop": exp.get("status") == "RUNNING"
        })

    # 3. Add finished history if empty
    for hist in task_history[-5:]:
        if not any(t["task_id"] == hist["task_id"] for t in tasks):
            tasks.append(hist)

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
        workers = [worker for worker in (getattr(sess, "start_task", None), getattr(sess, "worker_task", None))
                   if worker is not None and not worker.done()]
        workers.extend(task for task in getattr(sess, "edit_tasks", ()) if not task.done())
        progress = sess.get_progress() if hasattr(sess, "get_progress") else {}
        sess.stop()
        # A Python download thread must acknowledge cancellation before this
        # endpoint reports a completed stop or any owned files are removed.
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        task_history.append({
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

async def broadcast_session_event(task_id: str, event_type: str, data: Dict[str, Any]):
    if task_id in stream_sockets:
        dead_conns = []
        payload = {"type": event_type, "task_id": task_id, **data}
        for ws in stream_sockets[task_id]:
            try:
                await ws.send_json(payload)
            except Exception:
                dead_conns.append(ws)
        for ws in dead_conns:
            stream_sockets[task_id].remove(ws)

async def run_session(session):
    try:
        await session.start()
    except Exception:
        # The pipeline records and broadcasts its startup error.
        pass

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
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )

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

@app.post("/api/streaming/start-upload")
async def start_streaming_upload(
    file: UploadFile = File(...),
    initial_buffer_seconds: Optional[float] = Form(None, gt=0, le=120),
    voice: Optional[str] = Form(None),
    voice_id: Optional[str] = Form(None),
    tts_engine: Optional[str] = Form(None),
    asr_engine: str = Form(settings.ASR_ENGINE),
    visual_translation: bool = Form(False),
    ref_audio: Optional[UploadFile] = File(None)
):
    validate_visual_translation(visual_translation)
    tts_engine, voice = validated_voice(voice_id, tts_engine, voice)
    if ref_audio and ref_audio.filename and tts_engine != "vieneu-tts":
        raise HTTPException(status_code=422, detail="Mẫu giọng riêng chỉ được hỗ trợ khi chọn VieNeu.")
    task_id = str(uuid.uuid4())[:8]
    ext = Path(file.filename).suffix or ".mp4"
    saved_path = settings.INPUT_DIR / f"upload_{task_id}{ext}"

    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    ref_audio_path = None
    if ref_audio and ref_audio.filename:
        ref_ext = Path(ref_audio.filename).suffix or ".wav"
        ref_path = settings.INPUT_DIR / f"ref_{task_id}{ref_ext}"
        with open(ref_path, "wb") as buffer:
            shutil.copyfileobj(ref_audio.file, buffer)
        ref_audio_path = ref_path

    # Create session
    session = create_streaming_session(
        task_id=task_id,
        video_path=saved_path,
        initial_buffer_seconds=initial_buffer_seconds or settings.INITIAL_BUFFER_SECONDS,
        voice=voice,
        tts_engine_name=tts_engine,
        asr_engine_name=asr_engine,
        visual_translation=visual_translation,
        ref_audio=ref_audio_path,
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )

    asyncio.create_task(run_session(session))

    return {
        "task_id": task_id,
        "video_url": f"/api/inputs/{saved_path.name}",
        "initial_buffer_seconds": session.initial_buffer_seconds,
        "status": "started"
    }

@app.get("/api/streaming/audio/{task_id}/{seg_id}")
async def get_segment_audio(task_id: str, seg_id: int):
    wav_path = settings.BASE_DIR / "workspace" / "cache" / task_id / "segments" / f"seg_{seg_id}.wav"
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Segment audio not found or not yet synthesized")
    return FileResponse(wav_path, media_type="audio/wav", headers={"Cache-Control": "no-store"})

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
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu thoại.") from None
    except SegmentEditConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError:
        raise HTTPException(status_code=422, detail="Nội dung hoặc thời lượng câu thoại không hợp lệ. Nhập từ 1 đến 2.000 ký tự.") from None
    except Exception:
        raise HTTPException(status_code=503, detail="Chưa tạo lại được giọng đọc. Nội dung và âm thanh cũ vẫn được giữ; hãy thử lại.") from None
    return {"segment": segment, "screen_texts": getattr(session, "screen_texts", [])}

@app.get("/api/streaming/bgm/{task_id}")
async def get_streaming_bgm(task_id: str):
    bgm_path = settings.BASE_DIR / "workspace" / "cache" / task_id / "bgm_suppressed.ogg"
    if not bgm_path.exists():
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

    if getattr(session, "is_editing", False):
        raise HTTPException(status_code=409, detail="Đang lưu lời thoại và tạo lại giọng đọc. Hãy chờ lưu xong trước khi xuất.")
    if session.is_running or session.error or any(
        s.status not in ("READY", "PLAYED") for s in session.segments.values()
    ):
        raise HTTPException(status_code=409, detail="Hãy chờ dịch xong toàn bộ video trước khi xuất.")
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
        "progress": 5,
        "stage": "Khởi động xuất video HQ...",
        "start_time": time.time(),
        "duration": session.total_duration,
        "video_url": "",
        "output_filename": "",
        "cancelled": False
    }

    loop = asyncio.get_event_loop()

    def _prog_cb(pct: int, stage: str):
        if export_id in active_export_tasks:
            active_export_tasks[export_id]["progress"] = pct
            active_export_tasks[export_id]["stage"] = stage
        asyncio.run_coroutine_threadsafe(
            broadcast_session_event(req.task_id, "export_progress", {"progress": pct, "stage": stage}),
            loop
        )

    def _cancel_chk():
        return active_export_tasks.get(export_id, {}).get("cancelled", False)

    try:
        exporter = HQExporter()
        result = await asyncio.to_thread(
            exporter.export,
            task_id=req.task_id,
            video_path=session.video_path,
            segments=segments_data,
            total_duration=session.total_duration,
            # Do not cover source pixels when screen-text translation is disabled.
            mask_chinese=(req.mask_chinese if req.mask_chinese is not None else True)
                and req.translate_screen_text,
            screen_texts=(getattr(session, "screen_texts", []) if req.translate_screen_text else [])
                if getattr(session, "visual_translation", False) else None,
            progress_callback=_prog_cb,
            cancel_check=_cancel_chk
        )

        active_export_tasks[export_id]["status"] = "COMPLETED"
        active_export_tasks[export_id]["progress"] = 100
        active_export_tasks[export_id]["stage"] = "Xuất video hoàn tất thành công!"
        active_export_tasks[export_id]["video_url"] = f"/api/outputs/{result['output_filename']}"
        active_export_tasks[export_id]["output_filename"] = result["output_filename"]

        return {
            "status": "ok",
            "export_id": export_id,
            "output_filename": result["output_filename"],
            "video_url": f"/api/outputs/{result['output_filename']}",
            "elapsed_seconds": result["elapsed_seconds"]
        }
    except Exception as e:
        status_name = "CANCELLED" if active_export_tasks.get(export_id, {}).get("cancelled") else "FAILED"
        if export_id in active_export_tasks:
            active_export_tasks[export_id]["status"] = status_name
            active_export_tasks[export_id]["stage"] = "Đã hủy bởi người dùng" if status_name == "CANCELLED" else f"Lỗi: {e}"
        raise HTTPException(status_code=409 if status_name == "CANCELLED" else 500, detail="Đã hủy xuất video" if status_name == "CANCELLED" else str(e))

@app.get("/api/streaming/export-hq/status/{task_id}")
async def get_export_hq_status(task_id: str):
    export_id = task_id if task_id.startswith("export_") else f"export_{task_id}"
    if export_id in active_export_tasks:
        return active_export_tasks[export_id]
    raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ export")

@app.post("/api/streaming/export-hq/cancel/{task_id}")
async def cancel_export_hq(task_id: str):
    export_id = task_id if task_id.startswith("export_") else f"export_{task_id}"
    if export_id in active_export_tasks:
        if active_export_tasks[export_id]["status"] not in {"RUNNING", "CANCELLING"}:
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
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )
    asyncio.create_task(run_session(session))

    return {
        "task_id": task_id,
        "video_url": f"/api/local-file?path={quote(p.as_posix(), safe='')}",
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


@app.post("/api/preview")
async def create_media_preview(req: PreviewRequest):
    session = get_streaming_session(req.task_id) if req.task_id else None
    source = session.video_path if session else req.file_path
    if not source:
        raise HTTPException(status_code=404, detail="Không tìm thấy video cần xem trước")
    reject_private_media_path(source)
    try:
        return preview_manager.start(source)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Video nguồn không tồn tại") from None
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.post("/api/preview/upload")
async def create_uploaded_preview(file: UploadFile = File(...)):
    try:
        return await asyncio.to_thread(preview_manager.start_upload, file.file, Path(file.filename or "video").suffix)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.get("/api/preview/{preview_id}")
async def get_media_preview_status(preview_id: str):
    try:
        return preview_manager.status(preview_id)
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
            client.models.generate_content(
                model=model,
                contents="Trả về chữ OK.",
                config={"temperature": 0.1, "max_output_tokens": 128},
            )
        finally:
            client.close()

    try:
        started = time.monotonic()
        await asyncio.to_thread(ping)
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

    session = get_streaming_session(task_id)
    if session:
        if hasattr(session, "get_progress"):
            await websocket.send_json({"type": "progress", "task_id": task_id, **session.get_progress()})
        if getattr(session, "source_video_url", None):
            await websocket.send_json({"type": "source_ready", "task_id": task_id, "video_url": session.source_video_url})
    if session and getattr(session, "initialized", True):
        # Send current state immediately upon connection
        await websocket.send_json({
            "type": "init",
            "task_id": task_id,
            "duration": session.total_duration,
            "segments_count": len(session.segments),
            "segments": [s.to_dict() for s in session.segments.values()],
            "screen_texts": getattr(session, "screen_texts", []),
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
        await websocket.send_json({
            "type": "telemetry",
            "task_id": task_id,
            **session.get_telemetry()
        })
        if not session.error and session.first_play_emitted:
            await websocket.send_json({"type": "ready_to_play", "task_id": task_id})
    if session and session.error:
        await websocket.send_json({"type": "error", "message": session.error, "task_id": task_id})

    try:
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
        if task_id in stream_sockets and websocket in stream_sockets[task_id]:
            stream_sockets[task_id].remove(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
