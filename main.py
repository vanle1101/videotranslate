import asyncio
import os
import shutil
import uuid
import time
from pathlib import Path
from urllib.parse import quote
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel

from config import settings
from core.downloader import VideoDownloader
from core.hardware import detect_hardware
from core.model_manager import ModelManager
from core.streaming.pipeline import (
    create_streaming_session,
    get_streaming_session,
    active_streaming_sessions
)
from core.streaming.export import HQExporter

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
    initial_buffer_seconds: Optional[float] = 10.0
    voice: Optional[str] = None
    tts_engine: Optional[str] = None
    asr_engine: Optional[str] = None

class SeekRequest(BaseModel):
    task_id: str
    time: float

class ExportHQRequest(BaseModel):
    task_id: str
    mask_chinese: Optional[bool] = True

class ConfigRequest(BaseModel):
    gemini_key: Optional[str] = None
    deepseek_key: Optional[str] = None
    llm_provider: Optional[str] = None
    gemini_model: Optional[str] = None
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
        "llm_provider": getattr(settings, "LLM_PROVIDER", "gemini"),
        "gemini_key": getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", ""),
        "gemini_model": getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash"),
        "deepseek_key": getattr(settings, "DEEPSEEK_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", ""),
        "suppression_mode": getattr(settings, "SUPPRESSION_MODE", "AUTO"),
        "ducking_level": str(int(getattr(settings, "BGM_VOLUME_DUCKED_DB", -14))),
        "buffer_target": "10"
    }

@app.post("/api/settings")
@app.post("/api/config")
async def update_settings(req: ConfigRequest):
    env_updates = {}
    if req.gemini_key is not None:
        settings.GEMINI_API_KEY = req.gemini_key
        os.environ["GEMINI_API_KEY"] = req.gemini_key
        env_updates["GEMINI_API_KEY"] = req.gemini_key
    if req.deepseek_key is not None:
        settings.DEEPSEEK_API_KEY = req.deepseek_key
        os.environ["DEEPSEEK_API_KEY"] = req.deepseek_key
        env_updates["DEEPSEEK_API_KEY"] = req.deepseek_key
    if req.llm_provider:
        settings.LLM_PROVIDER = req.llm_provider
        env_updates["LLM_PROVIDER"] = req.llm_provider
    if req.gemini_model:
        settings.GEMINI_MODEL = req.gemini_model
        env_updates["GEMINI_MODEL"] = req.gemini_model
    if req.ducking_level:
        try:
            settings.BGM_VOLUME_DUCKED_DB = float(req.ducking_level)
            env_updates["BGM_VOLUME_DUCKED_DB"] = req.ducking_level
        except ValueError:
            pass

    if env_updates:
        try:
            update_env_file(env_updates)
        except Exception as e:
            print(f"[!] Warning updating .env: {e}")

    return {"status": "ok", "message": "Cấu hình đã được lưu an toàn vào .env và áp dụng tức thì!"}

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

        status_str = "FAILED" if sess.error else ("PAUSED" if sess.is_paused else ("RUNNING" if sess.is_running else "COMPLETED"))
        tasks.append({
            "task_id": task_id,
            "task_type": "Realtime Dubbing",
            "status": status_str,
            "progress_pct": pct,
            "stage": sess.error or f"Đã dịch {ready_cnt}/{tot_cnt} câu ({pct}%)",
            "duration": sess.total_duration,
            "elapsed_seconds": round(time.time() - sess.start_wall_time, 1) if sess.start_wall_time else 0,
            "video_url": f"/api/inputs/{sess.video_path.name}" if hasattr(sess, "video_path") and sess.video_path else "",
            "can_pause": status_str == "RUNNING",
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
        sess.pause()
        return {"status": "ok", "task_id": task_id, "action": "paused"}
    raise HTTPException(status_code=404, detail="Task không tồn tại hoặc không thể tạm dừng")

@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    sess = get_streaming_session(task_id)
    if sess:
        sess.resume()
        return {"status": "ok", "task_id": task_id, "action": "resumed"}
    raise HTTPException(status_code=404, detail="Task không tồn tại hoặc không thể tiếp tục")

@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    sess = get_streaming_session(task_id)
    if sess:
        sess.stop()
        task_history.append({
            "task_id": task_id,
            "task_type": "Realtime Dubbing",
            "status": "STOPPED",
            "progress_pct": 100,
            "stage": "Đã dừng bởi người dùng",
            "duration": sess.total_duration,
            "elapsed_seconds": round(time.time() - sess.start_wall_time, 1) if sess.start_wall_time else 0,
            "video_url": ""
        })
        return {"status": "ok", "task_id": task_id, "action": "stopped"}

    if task_id in active_export_tasks:
        active_export_tasks[task_id]["cancelled"] = True
        active_export_tasks[task_id]["status"] = "CANCELLED"
        active_export_tasks[task_id]["stage"] = "Đã hủy bởi người dùng"
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
    if not req.url:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp link video")

    task_id = str(uuid.uuid4())[:8]

    # Resolve / Download video
    try:
        video_info = await asyncio.to_thread(downloader.download, req.url)
        video_path = Path(video_info["file_path"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể tải video: {e}")

    # Create session
    session = create_streaming_session(
        task_id=task_id,
        video_path=video_path,
        initial_buffer_seconds=req.initial_buffer_seconds or 10.0,
        voice=req.voice or settings.EDGE_VOICE,
        tts_engine_name=req.tts_engine or settings.TTS_ENGINE,
        asr_engine_name=req.asr_engine or settings.ASR_ENGINE,
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )

    asyncio.create_task(run_session(session))

    return {
        "task_id": task_id,
        "video_url": f"/api/inputs/{video_path.name}",
        "initial_buffer_seconds": session.initial_buffer_seconds,
        "status": "started"
    }

@app.post("/api/streaming/start-upload")
async def start_streaming_upload(
    file: UploadFile = File(...),
    initial_buffer_seconds: float = Form(10.0),
    voice: str = Form(settings.EDGE_VOICE),
    tts_engine: str = Form(settings.TTS_ENGINE),
    asr_engine: str = Form(settings.ASR_ENGINE),
    ref_audio: Optional[UploadFile] = File(None)
):
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
        initial_buffer_seconds=initial_buffer_seconds,
        voice=voice,
        tts_engine_name=tts_engine,
        asr_engine_name=asr_engine,
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
    return FileResponse(wav_path, media_type="audio/wav")

@app.get("/api/streaming/bgm/{task_id}")
async def get_streaming_bgm(task_id: str):
    bgm_path = settings.BASE_DIR / "workspace" / "cache" / task_id / "bgm_suppressed.m4a"
    if not bgm_path.exists():
        raise HTTPException(status_code=404, detail="BGM stream not found or still generating")
    return FileResponse(bgm_path, media_type="audio/mp4")

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

    if session.is_running or session.error or any(
        s.status not in ("READY", "PLAYED") for s in session.segments.values()
    ):
        raise HTTPException(status_code=409, detail="Hãy chờ dịch xong toàn bộ video trước khi xuất.")
    segments_data = [dict(s.to_dict(), audio_path=s.audio_path)
                     for s in session.segments.values() if s.status in ["READY", "PLAYED"]]
    if not segments_data:
        raise HTTPException(status_code=400, detail="Chưa có câu thoại nào sẵn sàng để xuất HQ")

    export_id = f"export_{req.task_id}"
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
            mask_chinese=req.mask_chinese if req.mask_chinese is not None else True,
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
            active_export_tasks[export_id]["stage"] = f"Lỗi: {e}"
        raise HTTPException(status_code=500, detail=str(e))

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
        active_export_tasks[export_id]["cancelled"] = True
        active_export_tasks[export_id]["status"] = "CANCELLED"
        active_export_tasks[export_id]["stage"] = "Đã hủy bởi người dùng"
        return {"status": "ok", "message": "Đã yêu cầu hủy xuất video"}
    raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ export")

class StreamLocalFileRequest(BaseModel):
    file_path: str
    initial_buffer_seconds: Optional[float] = 10.0
    voice: Optional[str] = None
    tts_engine: Optional[str] = None
    asr_engine: Optional[str] = None

@app.post("/api/streaming/start-local-file")
async def start_streaming_local_file(req: StreamLocalFileRequest):
    p = Path(req.file_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"File không tồn tại: {req.file_path}")

    task_id = str(uuid.uuid4())[:8]
    session = create_streaming_session(
        task_id=task_id,
        video_path=p,
        initial_buffer_seconds=req.initial_buffer_seconds or 10.0,
        voice=req.voice or settings.EDGE_VOICE,
        tts_engine_name=req.tts_engine or settings.TTS_ENGINE,
        asr_engine_name=req.asr_engine or settings.ASR_ENGINE,
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
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="File không tồn tại")
    return FileResponse(p)

@app.get("/api/diagnostics/logs")
async def get_diagnostics_logs(category: str = "app", lines: int = 100):
    log_file = settings.WORKSPACE_DIR / "logs" / f"{category}.log"
    if not log_file.exists():
        return {"category": category, "logs": "(Không có dữ liệu log)"}
    try:
        content = log_file.read_text(encoding="utf-8", errors="ignore")
        log_lines = content.strip().splitlines()[-lines:]
        return {"category": category, "logs": "\n".join(log_lines) if log_lines else "(Log rỗng)"}
    except Exception as e:
        return {"category": category, "logs": f"Lỗi đọc log: {e}"}

@app.post("/api/diagnostics/logs/clear")
async def clear_diagnostics_logs(category: Optional[str] = None):
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
        return {"ok": False, "error": "Chưa có GEMINI_API_KEY. Vui lòng cấu hình trong Cài đặt."}
    try:
        t0 = time.time()
        from google import genai
        client = genai.Client(api_key=gemini_key)
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=["Ping test: Trả về chữ OK."],
            config=dict(temperature=0.1)
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        return {
            "ok": True,
            "model": settings.GEMINI_MODEL,
            "latency_ms": latency_ms,
            "response": resp.text.strip()[:60]
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.websocket("/ws/stream/{task_id}")
async def websocket_stream(websocket: WebSocket, task_id: str):
    await websocket.accept()
    if task_id not in stream_sockets:
        stream_sockets[task_id] = []
    stream_sockets[task_id].append(websocket)

    session = get_streaming_session(task_id)
    if session:
        # Send current state immediately upon connection
        await websocket.send_json({
            "type": "init",
            "task_id": task_id,
            "duration": session.total_duration,
            "segments_count": len(session.segments),
            "segments": [s.to_dict() for s in session.segments.values()],
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
        if session.error:
            await websocket.send_json({"type": "error", "message": session.error, "task_id": task_id})
        elif session.first_play_emitted:
            await websocket.send_json({"type": "ready_to_play", "task_id": task_id})

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
                sess.pause()
            elif act == "resume":
                sess.resume()
            elif act == "stop":
                sess.stop()
    except WebSocketDisconnect:
        if task_id in stream_sockets and websocket in stream_sockets[task_id]:
            stream_sockets[task_id].remove(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
