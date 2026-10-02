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
    voice: Optional[str] = "vi-VN-HoaiMyNeural"
    llm_provider: Optional[str] = "gemini"
    mask_chinese: Optional[bool] = True

class ReRenderRequest(BaseModel):
    task_id: str
    segments: List[Dict[str, Any]]
    voice: Optional[str] = "vi-VN-HoaiMyNeural"
    mask_chinese: Optional[bool] = True

class ConfigRequest(BaseModel):
    gemini_key: Optional[str] = None
    deepseek_key: Optional[str] = None

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/config")
async def update_config(req: ConfigRequest):
    if req.gemini_key:
        settings.GEMINI_API_KEY = req.gemini_key
        os.environ["GEMINI_API_KEY"] = req.gemini_key
    if req.deepseek_key:
        settings.DEEPSEEK_API_KEY = req.deepseek_key
        os.environ["DEEPSEEK_API_KEY"] = req.deepseek_key
    return {"status": "ok", "message": "API keys saved successfully"}

@app.websocket("/ws/progress/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str):
    await websocket.accept()
    if task_id not in active_connections:
        active_connections[task_id] = []
    active_connections[task_id].append(websocket)

    # If task is already finished, send result immediately
    if task_id in task_results:
        await websocket.send_json({"type": "done", "result": task_results[task_id]})

    try:
        while True:
            # Keep socket alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        if task_id in active_connections and websocket in active_connections[task_id]:
            active_connections[task_id].remove(websocket)

async def notify_progress(task_id: str, percent: int, message: str):
    if task_id in active_connections:
        dead_conns = []
        for ws in active_connections[task_id]:
            try:
                await ws.send_json({
                    "type": "progress",
                    "percent": percent,
                    "message": message
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

async def run_pipeline_task(task_id: str, video_input: str, voice: str, mask_chinese: bool, custom_segments: Optional[List[Dict[str, Any]]] = None):
    pipeline = VideoTranslationPipeline()

    async def progress_cb(percent: int, message: str):
        await notify_progress(task_id, percent, message)

    try:
        result = await pipeline.run(
            video_input=video_input,
            voice=voice,
            mask_chinese=mask_chinese,
            custom_segments=custom_segments,
            progress_callback=progress_cb
        )
        await notify_done(task_id, result)
    except Exception as e:
        print(f"[!] Pipeline error: {e}")
        if task_id in active_connections:
            for ws in active_connections[task_id]:
                try:
                    await ws.send_json({"type": "error", "message": str(e)})
                except Exception:
                    pass

@app.post("/api/process-url")
async def process_url(req: ProcessUrlRequest):
    if not req.url:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp link video")
    
    task_id = str(uuid.uuid4())[:8]
    asyncio.create_task(run_pipeline_task(
        task_id=task_id,
        video_input=req.url,
        voice=req.voice or "vi-VN-HoaiMyNeural",
        mask_chinese=req.mask_chinese if req.mask_chinese is not None else True
    ))
    return {"task_id": task_id, "status": "processing"}

@app.post("/api/process-upload")
async def process_upload(
    file: UploadFile = File(...),
    voice: str = Form("vi-VN-HoaiMyNeural"),
    llm_provider: str = Form("gemini"),
    mask_chinese: bool = Form(True)
):
    task_id = str(uuid.uuid4())[:8]
    ext = Path(file.filename).suffix or ".mp4"
    saved_path = settings.INPUT_DIR / f"upload_{task_id}{ext}"

    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    asyncio.create_task(run_pipeline_task(
        task_id=task_id,
        video_input=str(saved_path),
        voice=voice,
        mask_chinese=mask_chinese
    ))
    return {"task_id": task_id, "status": "processing"}

@app.post("/api/re-render")
async def re_render(req: ReRenderRequest):
    task_dir = settings.TEMP_DIR / req.task_id
    if not task_dir.exists():
        raise HTTPException(status_code=404, detail="Task ID không tồn tại")

    # Find original video in inputs or temp
    possible_videos = list(settings.INPUT_DIR.glob(f"*{req.task_id}*"))
    if not possible_videos:
        raise HTTPException(status_code=404, detail="Không tìm thấy video gốc")

    orig_video = str(possible_videos[0])
    new_task_id = str(uuid.uuid4())[:8]

    asyncio.create_task(run_pipeline_task(
        task_id=new_task_id,
        video_input=orig_video,
        voice=req.voice or "vi-VN-HoaiMyNeural",
        mask_chinese=req.mask_chinese,
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
