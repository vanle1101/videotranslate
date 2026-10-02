import asyncio
import os
import shutil
import uuid
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
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

@app.on_event("startup")
async def startup_warmup():
    """Pre-loads models in background thread so Time To First Play is ~3-5s."""
    def _warmup():
        print("[*] Pre-warming models for zero-latency streaming...")
        try:
            from core.engines.asr.sensevoice_engine import SenseVoiceEngine
            SenseVoiceEngine()._ensure_loaded()
        except Exception as e:
            print(f"[!] SenseVoice pre-warm warning: {e}")
        try:
            from core.engines.tts.vieneu_engine import VieNeuEngine
            VieNeuEngine()._ensure_loaded()
        except Exception as e:
            print(f"[!] VieNeu pre-warm warning: {e}")
        print("[+] Engines pre-warmed and ready for instant playback!")

    asyncio.get_event_loop().run_in_executor(None, _warmup)

class StreamUrlRequest(BaseModel):
    url: str
    initial_buffer_seconds: Optional[float] = 10.0
    voice: Optional[str] = "Trúc Ly"
    tts_engine: Optional[str] = "vieneu"
    asr_engine: Optional[str] = "sensevoice"

class SeekRequest(BaseModel):
    task_id: str
    time: float

class ExportHQRequest(BaseModel):
    task_id: str
    mask_chinese: Optional[bool] = True

class ConfigRequest(BaseModel):
    gemini_key: Optional[str] = None
    deepseek_key: Optional[str] = None

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/hardware")
async def get_hardware():
    return detect_hardware()

@app.get("/api/models")
async def get_models():
    return {"models": ModelManager.get_all_models()}

@app.post("/api/config")
async def update_config(req: ConfigRequest):
    if req.gemini_key:
        settings.GEMINI_API_KEY = req.gemini_key
        os.environ["GEMINI_API_KEY"] = req.gemini_key
    if req.deepseek_key:
        settings.DEEPSEEK_API_KEY = req.deepseek_key
        os.environ["DEEPSEEK_API_KEY"] = req.deepseek_key
    return {"status": "ok", "message": "API keys saved successfully"}

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

@app.post("/api/streaming/start-url")
async def start_streaming_url(req: StreamUrlRequest):
    if not req.url:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp link video")

    task_id = str(uuid.uuid4())[:8]

    # Resolve / Download video
    try:
        video_info = downloader.download(req.url)
        video_path = Path(video_info["file_path"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể tải video: {e}")

    # Create session
    session = create_streaming_session(
        task_id=task_id,
        video_path=video_path,
        initial_buffer_seconds=req.initial_buffer_seconds or 10.0,
        voice=req.voice or "Trúc Ly",
        tts_engine_name=req.tts_engine or "vieneu",
        asr_engine_name=req.asr_engine or "sensevoice",
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )

    asyncio.create_task(session.start())

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
    voice: str = Form("Trúc Ly"),
    tts_engine: str = Form("vieneu"),
    asr_engine: str = Form("sensevoice"),
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

    asyncio.create_task(session.start())

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

    segments_data = [s.to_dict() for s in session.segments.values() if s.status in ["READY", "PLAYED"]]
    if not segments_data:
        raise HTTPException(status_code=400, detail="Chưa có câu thoại nào sẵn sàng để xuất HQ")

    exporter = HQExporter()
    result = await asyncio.to_thread(
        exporter.export,
        task_id=req.task_id,
        video_path=session.video_path,
        segments=segments_data,
        total_duration=session.total_duration,
        mask_chinese=req.mask_chinese if req.mask_chinese is not None else True
    )

    return {
        "status": "ok",
        "output_filename": result["output_filename"],
        "video_url": f"/api/outputs/{result['output_filename']}",
        "elapsed_seconds": result["elapsed_seconds"]
    }

class StreamLocalFileRequest(BaseModel):
    file_path: str
    initial_buffer_seconds: Optional[float] = 10.0
    voice: Optional[str] = "Trúc Ly"
    tts_engine: Optional[str] = "vieneu"
    asr_engine: Optional[str] = "sensevoice"

@app.post("/api/streaming/start-local-file")
async def start_streaming_local_file(req: StreamLocalFileRequest):
    p = Path(req.file_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File không tồn tại: {req.file_path}")

    task_id = str(uuid.uuid4())[:8]
    session = create_streaming_session(
        task_id=task_id,
        video_path=p,
        initial_buffer_seconds=req.initial_buffer_seconds or 10.0,
        voice=req.voice or "Trúc Ly",
        tts_engine_name=req.tts_engine or "vieneu",
        asr_engine_name=req.asr_engine or "sensevoice",
        event_callback=lambda event_type, data: broadcast_session_event(task_id, event_type, data)
    )
    asyncio.create_task(session.start())

    return {
        "task_id": task_id,
        "video_url": f"/api/local-file?path={p.as_posix()}",
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
