import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel

from config import settings
from core.pipeline import VideoTranslationPipeline
from core.hardware import detect_hardware
from core.model_manager import ModelManager
from core.task_controller import get_task_controller, remove_task_controller

app = FastAPI(title=settings.APP_NAME)

# Static and Templates
app.mount("/static", StaticFiles(directory=str(settings.BASE_DIR / "static")), name="static")
app.mount("/api/outputs", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="outputs")
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

# Active WebSockets: task_id -> list of WebSockets
active_connections: Dict[str, List[WebSocket]] = {}
task_results: Dict[str, Any] = {}

class ProcessUrlRequest(BaseModel):
    url: str
    asr_engine: Optional[str] = "sensevoice"
    tts_engine: Optional[str] = "vieneu"
    voice: Optional[str] = "Trúc Ly"
    separator_engine: Optional[str] = "bs_roformer"
    mask_chinese: Optional[bool] = True

class ReRenderRequest(BaseModel):
    task_id: str
    segments: List[Dict[str, Any]]
    voice: Optional[str] = "Trúc Ly"
    tts_engine: Optional[str] = "vieneu"
    mask_chinese: Optional[bool] = True

class ConfigRequest(BaseModel):
    gemini_key: Optional[str] = None
    deepseek_key: Optional[str] = None

class TaskActionRequest(BaseModel):
    action: str

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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

@app.post("/api/task/{task_id}/action")
async def control_task(task_id: str, req: TaskActionRequest):
    controller = get_task_controller(task_id)
    action = req.action.lower()
    if action == "pause":
        controller.pause()
        return {"status": "ok", "state": "paused"}
    elif action == "resume":
        controller.resume()
        return {"status": "ok", "state": "resumed"}
    elif action == "stop":
        controller.stop()
        return {"status": "ok", "state": "stopped"}
    else:
        raise HTTPException(status_code=400, detail="Hành động không hợp lệ (chỉ chấp nhận: pause, resume, stop)")

@app.websocket("/ws/progress/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str):
    await websocket.accept()
    if task_id not in active_connections:
        active_connections[task_id] = []
    active_connections[task_id].append(websocket)

    # If task is already finished, send result immediately
    if task_id in task_results:
        await websocket.send_json({"type": "done", "result": task_results[task_id]})

    controller = get_task_controller(task_id)

    try:
        while True:
            data = await websocket.receive_json()
            if isinstance(data, dict):
                act = data.get("action")
                if act == "pause":
                    controller.pause()
                    await websocket.send_json({"type": "status", "state": "paused"})
                elif act == "resume":
                    controller.resume()
                    await websocket.send_json({"type": "status", "state": "resumed"})
                elif act == "stop":
                    controller.stop()
                    await websocket.send_json({"type": "status", "state": "stopped"})
    except WebSocketDisconnect:
        if task_id in active_connections and websocket in active_connections[task_id]:
            active_connections[task_id].remove(websocket)

async def notify_progress(task_id: str, percent: int, message: str, stage: Optional[str] = None):
    if task_id in active_connections:
        dead_conns = []
        for ws in active_connections[task_id]:
            try:
                await ws.send_json({
                    "type": "progress",
                    "percent": percent,
                    "message": message,
                    "stage": stage
                })
            except Exception:
                dead_conns.append(ws)
        for ws in dead_conns:
            active_connections[task_id].remove(ws)

async def notify_done(task_id: str, result: Dict[str, Any]):
    task_results[task_id] = result
    if task_id in active_connections:
        for ws in active_connections[task_id]:
            try:
                await ws.send_json({
                    "type": "done",
                    "result": result
                })
            except Exception:
                pass
    remove_task_controller(task_id)

async def run_pipeline_task(
    task_id: str,
    video_input: str,
    asr_engine_name: str = "sensevoice",
    tts_engine_name: str = "vieneu",
    voice: str = "Trúc Ly",
    ref_audio: Optional[Path] = None,
    mask_chinese: bool = True,
    custom_segments: Optional[List[Dict[str, Any]]] = None
):
    pipeline = VideoTranslationPipeline()
    controller = get_task_controller(task_id)

    async def progress_cb(percent: int, message: str, stage: Optional[str] = None):
        await notify_progress(task_id, percent, message, stage)

    try:
        result = await pipeline.run(
            video_input=video_input,
            asr_engine_name=asr_engine_name,
            tts_engine_name=tts_engine_name,
            voice=voice,
            ref_audio=ref_audio,
            custom_segments=custom_segments,
            progress_callback=progress_cb,
            task_controller=controller
        )
        await notify_done(task_id, result)
    except asyncio.CancelledError:
        print(f"[*] Task {task_id} was stopped by user.")
        if task_id in active_connections:
            for ws in active_connections[task_id]:
                try:
                    await ws.send_json({"type": "error", "message": "Tiến trình đã bị người dùng hủy bỏ."})
                except Exception:
                    pass
        remove_task_controller(task_id)
    except Exception as e:
        print(f"[!] Pipeline error in task {task_id}: {e}")
        if task_id in active_connections:
            for ws in active_connections[task_id]:
                try:
                    await ws.send_json({"type": "error", "message": str(e)})
                except Exception:
                    pass
        remove_task_controller(task_id)

@app.post("/api/process-url")
async def process_url(req: ProcessUrlRequest):
    if not req.url:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp link video")
    
    task_id = str(uuid.uuid4())[:8]
    asyncio.create_task(run_pipeline_task(
        task_id=task_id,
        video_input=req.url,
        asr_engine_name=req.asr_engine or "sensevoice",
        tts_engine_name=req.tts_engine or "vieneu",
        voice=req.voice or "Trúc Ly",
        mask_chinese=req.mask_chinese if req.mask_chinese is not None else True
    ))
    return {"task_id": task_id, "status": "processing"}

@app.post("/api/process-upload")
async def process_upload(
    file: UploadFile = File(...),
    asr_engine: str = Form("sensevoice"),
    tts_engine: str = Form("vieneu"),
    voice: str = Form("Trúc Ly"),
    separator_engine: str = Form("bs_roformer"),
    ref_audio: Optional[UploadFile] = File(None),
    mask_chinese: bool = Form(True)
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

    asyncio.create_task(run_pipeline_task(
        task_id=task_id,
        video_input=str(saved_path),
        asr_engine_name=asr_engine,
        tts_engine_name=tts_engine,
        voice=voice,
        ref_audio=ref_audio_path,
        mask_chinese=mask_chinese
    ))
    return {"task_id": task_id, "status": "processing"}

@app.post("/api/re-render")
async def re_render(req: ReRenderRequest):
    task_dir = settings.TEMP_DIR / req.task_id
    if not task_dir.exists():
        raise HTTPException(status_code=404, detail="Task ID không tồn tại")

    possible_videos = list(settings.INPUT_DIR.glob(f"*{req.task_id}*"))
    if not possible_videos:
        raise HTTPException(status_code=404, detail="Không tìm thấy video gốc")

    orig_video = str(possible_videos[0])
    new_task_id = str(uuid.uuid4())[:8]

    asyncio.create_task(run_pipeline_task(
        task_id=new_task_id,
        video_input=orig_video,
        asr_engine_name="sensevoice",
        tts_engine_name=req.tts_engine or "vieneu",
        voice=req.voice or "Trúc Ly",
        mask_chinese=req.mask_chinese if req.mask_chinese is not None else True,
        custom_segments=req.segments
    ))
    return {"task_id": new_task_id, "status": "processing"}

@app.get("/api/download-srt/{task_id}")
async def download_srt(task_id: str):
    srt_file = settings.TEMP_DIR / task_id / "subtitles.srt"
    if not srt_file.exists():
        raise HTTPException(status_code=404, detail="SRT không tìm thấy")
    return FileResponse(srt_file, media_type="application/x-subrip", filename=f"subtitles_{task_id}.srt")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
