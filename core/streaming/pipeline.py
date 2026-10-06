import asyncio
import time
import subprocess
import functools
import logging
import math
import uuid
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from config import settings

from core.streaming.segmenter import AudioSegmenter
from core.engines.asr.sensevoice_engine import SenseVoiceEngine
from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.engines.translation.semantic_translator import SemanticTranslator
from core.engines.tts.vieneu_engine import VieNeuEngine
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.voice_catalog import resolve_voice
from core.engines.alignment.timing_aligner import TimingBudgetAligner
from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from core.engines.alignment.speech_timing import build_speech_timing, take_tts_word_boundaries, trim_tts_padding

class SegmentEditConflict(RuntimeError):
    """Editing would conflict with the current session state."""


class SegmentItem:
    def __init__(self, seg_id: int, start: float, end: float, duration: float):
        self.id = seg_id
        self.start = start
        self.end = end
        self.duration = duration
        self.status = "WAITING" # WAITING, ASR, TRANSLATING, TTS, ALIGNING, READY, PLAYED, FAILED
        self.text_zh = ""
        self.emotion = ""
        self.literal_vi = ""
        self.natural_vi = ""
        self.final_vi = ""
        self.tts_duration = 0.0
        self.speed_ratio = 1.0
        self.audio_path: Optional[str] = None
        self.audio_url: Optional[str] = None
        self.error: Optional[str] = None
        self.failed_stage: Optional[str] = None
        self._retry_synthesis = False
        self.revision = 0
        self.source_method = "audio"
        self.translation_provider: Optional[str] = None
        self.translation_model: Optional[str] = None
        self.evidence_mode: Optional[str] = None
        self.needs_review = False
        self.review_reason: Optional[str] = None
        self.confirmed_silence = False
        self.subtitle_cues = []
        self.subtitle_timing_source = "pending"
        self.speech_start = None
        self.speech_end = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "status": self.status,
            "text_zh": self.text_zh,
            "emotion": self.emotion,
            "literal_vi": self.literal_vi,
            "natural_vi": self.natural_vi,
            "final_vi": self.final_vi,
            "text_vi": self.final_vi or self.literal_vi,
            "tts_duration": self.tts_duration,
            "speed_ratio": self.speed_ratio,
            "audio_url": self.audio_url,
            "error": self.error,
            "failed_stage": self.failed_stage,
            "revision": self.revision,
            "source_method": self.source_method,
            "translation_provider": self.translation_provider,
            "translation_model": self.translation_model,
            "evidence_mode": self.evidence_mode,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "preview_is_draft": bool(self.needs_review and self.status in ("READY", "PLAYED")),
            "confirmed_silence": self.confirmed_silence,
            "subtitle_cues": self.subtitle_cues,
            "subtitle_timing_source": self.subtitle_timing_source,
            "speech_start": self.speech_start,
            "speech_end": self.speech_end,
        }

class StreamingPipelineSession:
    """
    Real-Time Streaming Localization Session.
    Processes segments ahead of playback using an asynchronous priority queue.
    """
    def __init__(
        self,
        task_id: str,
        video_path: Optional[Path],
        initial_buffer_seconds: Optional[float] = None,
        voice: Optional[str] = None,
        tts_engine_name: Optional[str] = None,
        asr_engine_name: Optional[str] = None,
        ref_audio: Optional[Path] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        visual_translation: bool = False,
    ):
        self.task_id = task_id
        self.video_path = video_path
        self.source_video_url: Optional[str] = None
        self.initialized = False
        self._download_info: Optional[Dict[str, Any]] = None
        self.progress = {
            "phase": "prepare" if video_path else "resolve",
            "stage": "Đang chuẩn bị video..." if video_path else "Đang nhận diện link video...",
            "progress_pct": None, "status": "RUNNING", "can_pause": False,
        }
        self.initial_buffer_seconds = settings.INITIAL_BUFFER_SECONDS if initial_buffer_seconds is None else initial_buffer_seconds
        self.tts_engine_name, self.voice = resolve_voice(voice, tts_engine_name)
        self.asr_engine_name = asr_engine_name or settings.ASR_ENGINE
        self.ref_audio = ref_audio
        self.event_callback = event_callback
        self.visual_translation = bool(visual_translation)
        self.screen_texts: List[Dict[str, Any]] = []
        self.translation_sources: List[Dict[str, str]] = []
        if self.visual_translation:
            from core.video_intelligence import validate_visual_provider, VideoIntelligenceError
            try:
                validate_visual_provider()
            except VideoIntelligenceError as exc:
                raise ValueError(str(exc)) from None

        # Cache directories
        self.cache_dir = settings.BASE_DIR / "workspace" / "cache" / task_id
        self._owns_cache = not self.cache_dir.exists()
        self.segments_dir = self.cache_dir / "segments"
        self.segments_dir.mkdir(parents=True, exist_ok=True)

        # Engines (Lazy loaded or shared)
        self.segmenter = AudioSegmenter()
        self.sensevoice = SenseVoiceEngine()
        self.faster_whisper = FasterWhisperFallbackEngine()
        self.translator = SemanticTranslator()
        self.video_intelligence = None
        if self.visual_translation:
            from core.video_intelligence import VideoIntelligence
            self.video_intelligence = VideoIntelligence()
        self.vieneu = None
        self.edge_tts = EdgeTTSFallbackEngine()
        self.aligner = TimingBudgetAligner()
        self.vocal_suppressor = RealtimeVocalSuppressor()
        self.bgm_audio_path: Optional[Path] = None
        self.bgm_url: Optional[str] = None
        self.suppression_stats: Dict[str, Any] = {}

        # Telemetry & State
        self.total_duration = 0.0
        self.segments: Dict[int, SegmentItem] = {}
        self.current_playback_time = 0.0
        self.playable_until = 0.0
        self.buffer_ahead = 0.0
        self.start_wall_time = 0.0
        self.time_to_first_play: Optional[float] = None
        self.first_play_emitted = False
        self.total_processed_duration = 0.0
        self.realtime_factor = 0.0
        self.error: Optional[str] = None
        self.warnings: List[str] = []

        # Control flags & queues
        self.is_running = False
        self.is_stopped = False
        self.is_paused = False
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.worker_task: Optional[asyncio.Task] = None
        self.start_task: Optional[asyncio.Task] = None
        self.rolling_context: List[Dict[str, str]] = []
        self.edit_tasks: set[asyncio.Task] = set()
        self._tts_lock = asyncio.Lock()

    @property
    def is_editing(self):
        return bool(self.edit_tasks)

    @property
    def can_retry(self):
        failed = [segment for segment in self.segments.values() if segment.status == "FAILED"]
        return bool(self.initialized and not self.is_stopped and not self.is_running
                    and not self.is_editing and self.error and failed
                    and all(segment.failed_stage in ("TTS", "ALIGNING") for segment in failed)
                    and all(segment.status in ("READY", "PLAYED", "FAILED", "WAITING")
                            for segment in self.segments.values()))

    async def retry_failed_synthesis(self):
        """Resume saved translations after a TTS failure, retaining ready audio."""
        if (not self.can_retry or (self.worker_task and not self.worker_task.done())
                or (self.start_task and not self.start_task.done())):
            raise SegmentEditConflict("Chỉ thử lại khi phiên đã dừng do lỗi tạo giọng hoặc khớp thời lượng.")
        # There is no await before reservation and worker creation, so concurrent
        # API requests cannot start two workers for the same saved session.
        self.is_running = True
        self.is_paused = False
        self.pause_event.set()
        self.error = None
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        retried = []
        for segment in self.segments.values():
            if segment.status == "FAILED":
                segment.status = "WAITING"
                segment.error = None
                segment._retry_synthesis = True
                retried.append(segment)
            if segment.status == "WAITING":
                self.queue.put_nowait((segment.start, segment.id))
        ready = sum(segment.status in ("READY", "PLAYED") for segment in self.segments.values())
        self.progress = {"phase": "tts", "stage": "Đang thử lại tạo giọng, giữ nguyên các câu đã xong…",
                         "progress_pct": round(ready * 100 / len(self.segments), 1), "status": "RUNNING",
                         "completed_segments": ready, "total_segments": len(self.segments)}

        async def resumed_worker():
            try:
                await self.emit("progress", self.get_progress())
                for segment in retried:
                    await self.emit("segment_update", segment.to_dict())
            except asyncio.CancelledError:
                self.is_stopped = True
                self.is_running = False
                self._release_runtime()
                raise
            except Exception:
                logging.getLogger("errors").warning("[%s] Không gửi được trạng thái thử lại tạo giọng", self.task_id)
            await self._worker_loop()

        self.worker_task = asyncio.create_task(resumed_worker())
        self.worker_task.add_done_callback(lambda task: self._release_runtime() if task.cancelled() else None)
        return self.get_progress()

    async def edit_segment(self, segment_id: int, text: str, *, confirm_silence: bool = False):
        """Publish text and fitted audio together only after synthesis succeeds."""
        if (not isinstance(confirm_silence, bool) or not isinstance(text, str)
                or len(text) > 2000 or "\x00" in text
                or (confirm_silence and text.strip()) or (not confirm_silence and not text.strip())):
            raise ValueError("Nội dung tiếng Việt phải có từ 1 đến 2.000 ký tự và không chứa ký tự NUL.")
        seg = self.segments.get(segment_id)
        if seg is None:
            raise KeyError(segment_id)
        if self.is_stopped or seg.status not in ("READY", "PLAYED", "NEEDS_REVIEW"):
            raise SegmentEditConflict("Hãy chờ câu này dịch và tạo giọng xong trước khi sửa.")
        if self.is_editing:
            raise SegmentEditConflict("Đang tạo lại giọng cho một câu. Hãy chờ lưu xong rồi sửa tiếp.")
        if not all(math.isfinite(value) for value in (seg.start, seg.end, seg.duration)) or seg.duration <= 0:
            raise ValueError("Thời lượng câu thoại không hợp lệ.")
        text = text.strip()
        was_review = seg.needs_review or seg.status == "NEEDS_REVIEW"
        was_playable = seg.status in ("READY", "PLAYED")
        was_review_error = was_review and self.error == self._review_message()
        if confirm_silence and not was_review:
            if seg.confirmed_silence:
                return seg.to_dict()
            raise SegmentEditConflict("Chỉ xác nhận im lặng cho câu đang cần kiểm tra.")
        if text == seg.final_vi and not was_review:
            return seg.to_dict()
        task = asyncio.current_task()
        self.edit_tasks.add(task)
        stem = f"edit_{seg.id}_{uuid.uuid4().hex}"
        raw_path = self.cache_dir / f"{stem}_raw.wav"
        fitted_path = self.segments_dir / f"{stem}.wav"
        try:
            if confirm_silence:
                tts_duration, ratio = 0.0, 1.0
                timing = {"subtitle_cues": [], "subtitle_timing_source": "silence",
                          "speech_start": None, "speech_end": None}
            else:
                async with self._tts_lock:
                    await self._run_blocking(self.tts_engine.synthesize, text=text,
                                             output_path=raw_path, voice=self.voice, ref_audio=self.ref_audio)
                    boundaries = take_tts_word_boundaries(self.tts_engine, raw_path)
                    await self._run_blocking(trim_tts_padding, raw_path)
                tts_duration = await self._run_blocking(self.aligner.get_audio_duration, raw_path)
                if not math.isfinite(tts_duration) or tts_duration <= 0:
                    raise RuntimeError("Không đọc được âm thanh từ TTS.")
                ratio = max(self.aligner.min_speed, tts_duration / seg.duration)
                ratio = await self._run_blocking(self.aligner.apply_atempo, raw_path, fitted_path,
                                                 ratio, fit_duration=seg.duration)
                if not math.isfinite(ratio) or ratio <= 0 or not fitted_path.is_file() or not fitted_path.stat().st_size:
                    raise RuntimeError("Không tạo được giọng đọc hợp lệ.")
                timing = await self._run_blocking(build_speech_timing, text, seg.start, seg.end,
                                                  fitted_path, ratio, boundaries)
            if self.is_stopped:
                raise asyncio.CancelledError
            final_path = self.segments_dir / f"seg_{seg.id}.wav"
            # No await between atomic file replacement and metadata publication.
            if confirm_silence:
                # Delete only this segment's generated audio, before approving
                # silence. A locked file leaves the previous state intact.
                final_path.unlink(missing_ok=True)
            else:
                fitted_path.replace(final_path)
            old_text = seg.final_vi
            seg.final_vi = text
            seg.confirmed_silence = confirm_silence
            if confirm_silence:
                seg.literal_vi = seg.natural_vi = ""
            seg.tts_duration = tts_duration
            seg.speed_ratio = round(ratio, 2)
            seg.audio_path = None if confirm_silence else str(final_path.resolve())
            for key, value in timing.items():
                setattr(seg, key, value)
            seg.revision += 1
            seg.audio_url = None if confirm_silence else f"/api/streaming/audio/{self.task_id}/{seg.id}?rev={seg.revision}"
            if was_review:
                seg.needs_review = False
                seg.review_reason = None
                seg.error = None
                seg.status = "READY"
                if not was_playable:
                    self.total_processed_duration += seg.duration
                if was_review_error:
                    self.error = None
            if self.visual_translation and not confirm_silence:
                updated_screens = []
                for screen in self.screen_texts:
                    if (screen.get("kind") == "subtitle" and screen.get("start", 0) < seg.end
                            and screen.get("end", 0) > seg.start):
                        box = screen.get("bbox")
                        box_ok = (isinstance(box, (list, tuple)) and len(box) == 4
                                  and all(isinstance(value, (int, float)) and math.isfinite(value) for value in box)
                                  and 0 < box[2] <= 1 and 0 < box[3] <= 0.35 and box[2] * box[3] <= 0.30
                                  and 0 <= box[0] <= 1 and 0 <= box[1] <= 1
                                  and box[0] + box[2] <= 1 and box[1] + box[3] <= 1)
                        screen_review = screen.get("needs_review", False) or not box_ok
                        screen_reason = screen.get("review_reason") or ("Vị trí phụ đề chưa đủ chắc chắn." if not box_ok else "")
                        if screen["start"] < seg.start:
                            updated_screens.append({**screen, "end": seg.start})
                        updated_screens.append({**screen, "start": max(screen["start"], seg.start),
                                                "end": min(screen["end"], seg.end), "text_vi": "", "mask_only": True,
                                                "needs_review": screen_review, "review_reason": screen_reason})
                        if screen["end"] > seg.end:
                            updated_screens.append({**screen, "start": seg.end})
                    else:
                        updated_screens.append(screen)
                self.screen_texts = updated_screens
            for context in self.rolling_context:
                if context.get("zh") == seg.text_zh and context.get("vi") == old_text:
                    context["vi"] = text
            snapshot = seg.to_dict()
            try:
                await self.emit("segment_update", {**snapshot, "screen_texts": self.screen_texts})
            except Exception:
                logging.getLogger("errors").warning("[%s] Không gửi được cập nhật câu %s", self.task_id, seg.id)
            if was_review:
                pending = [s for s in self.segments.values() if s.needs_review]
                if not pending and not self.is_running and all(s.status in ("READY", "PLAYED") for s in self.segments.values()):
                    self.error = None
                    await self.report_progress("complete", "Đã kiểm tra, dịch và lồng tiếng hoàn tất", 100)
                elif not self.is_running and pending and not any(s.status == "FAILED" for s in self.segments.values()):
                    await self.report_progress("complete", self._review_message(), 100)
                await self._update_ready()
            return snapshot
        finally:
            for path in (raw_path, fitted_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.edit_tasks.discard(task)
            if self.is_stopped or not self.is_running:
                self._release_runtime()

    async def emit(self, event_type: str, data: Dict[str, Any]):
        if event_type == "segment_update":
            data = self.caption_metadata(data)
        if self.event_callback:
            payload = {"type": event_type, "task_id": self.task_id, **data}
            if asyncio.iscoroutinefunction(self.event_callback):
                await self.event_callback(event_type, payload)
            else:
                res = self.event_callback(event_type, payload)
                if asyncio.iscoroutine(res):
                    await res

    def caption_metadata(self, data):
        """Preview and export consume the same measured caption layout."""
        from core.subtitle_cues import build_caption_layout
        size = getattr(self, "video_size", (1080, 1920))
        return {**data,
                "caption_layout": build_caption_layout([data], self.screen_texts, video_size=size),
                "caption_bottom_layout": build_caption_layout([data], [], video_size=size)}

    def segment_snapshot(self, segment):
        return self.caption_metadata(segment.to_dict())

    def get_progress(self) -> Dict[str, Any]:
        snapshot = dict(self.progress)
        snapshot.update(self._review_metadata())
        snapshot["can_retry"] = self.can_retry
        snapshot["can_pause"] = bool(self.initialized and self.is_running and not self.is_paused and not self.is_stopped and not self.error)
        if self.is_stopped:
            snapshot.update(status="STOPPED", phase="stopped", stage="Đã dừng bởi người dùng")
        elif self.error:
            snapshot.update(status="FAILED", phase="failed", stage=self.error)
        elif self.is_paused and self.is_running:
            snapshot["status"] = "PAUSED"
        snapshot["can_resume"] = bool(self.initialized and self.is_running and snapshot["status"] == "PAUSED")
        snapshot["can_stop"] = snapshot["status"] in {"RUNNING", "PAUSED"}
        return snapshot

    async def report_progress(self, phase: str, stage: str, progress_pct=None, **details):
        self.progress = {
            "phase": phase, "stage": stage, "progress_pct": progress_pct,
            "status": "COMPLETED" if phase == "complete" else "RUNNING", **details,
        }
        logging.getLogger("pipeline").info("[%s] %s%s", self.task_id, stage,
                                           "" if progress_pct is None else f" ({progress_pct:g}%)")
        await self.emit("progress", self.get_progress())

    async def _segment_progress(self, phase: str, stage: str):
        count = sum(s.status in ("READY", "PLAYED") for s in self.segments.values())
        total = len(self.segments)
        await self.report_progress(
            phase, f"{stage} · Đã hoàn tất {count}/{total} câu",
            round(count * 100 / total, 1) if total else 100,
            completed_segments=count, total_segments=total,
        )

    async def start(self):
        await self._start_guarded()

    async def start_from_url(self, downloader, url: str):
        await self._start_guarded(downloader, url)

    async def _start_guarded(self, downloader=None, url=None):
        self.start_task = asyncio.current_task()
        try:
            if self.is_stopped:
                raise asyncio.CancelledError
            self.is_running = True
            self.start_wall_time = time.time()
            if downloader is not None:
                loop = asyncio.get_running_loop()

                def publish_progress(data):
                    if self.is_stopped or self.video_path is not None:
                        return
                    self.progress = {**data, "status": "RUNNING", "can_pause": False}
                    asyncio.create_task(self.emit("progress", self.get_progress()))

                def download_source():
                    self._download_info = downloader.download(
                        url, progress_callback=lambda data: loop.call_soon_threadsafe(publish_progress, dict(data)),
                        cancel_check=lambda: self.is_stopped,
                    )
                    return self._download_info

                await self.report_progress("resolve", "Đang nhận diện link video...")
                info = await self._run_blocking(download_source)
                self.video_path = Path(info["file_path"])
                self.source_video_url = f"/api/inputs/{self.video_path.name}"
                await self.emit("source_ready", {"video_url": self.source_video_url})
            await self._start()
        except asyncio.CancelledError:
            self.is_stopped = True
            self.is_running = False
            self._release_runtime()
            await self.emit("progress", self.get_progress())
            await self.emit("finished", self.get_telemetry())
            raise
        except Exception as exc:
            self.is_running = False
            self.error = str(exc)
            logging.getLogger("errors").error("[%s] Không thể xử lý video: %s", self.task_id, self.error)
            self._release_runtime()
            await self.emit("progress", self.get_progress())
            await self.emit("error", {"message": self.error})
            await self.emit("finished", self.get_telemetry())
            raise
        finally:
            self.start_task = None

    async def _start(self):
        """Initializes audio extraction, segmentation, and launches worker loop."""
        if self.is_stopped:
            raise asyncio.CancelledError
        self.is_running = True
        if not self.start_wall_time:
            self.start_wall_time = time.time()
        await self.report_progress("prepare", "Đang tách âm thanh từ video...")
        self.asr_engine = self.faster_whisper
        if not self.visual_translation and self.asr_engine_name == "sensevoice":
            if self.sensevoice.is_available:
                self.asr_engine = self.sensevoice
            else:
                self.warnings.append("SenseVoice chưa có model; sử dụng Faster-Whisper.")
        elif not self.visual_translation and self.asr_engine_name not in ("faster-whisper", "whisper"):
            raise ValueError(f"ASR engine không được hỗ trợ: {self.asr_engine_name}")
        self.tts_engine = self.edge_tts
        if self.tts_engine_name in ("vieneu", "vieneu-tts"):
            self.vieneu = VieNeuEngine()
            if self.vieneu.is_available:
                self.tts_engine = self.vieneu
            else:
                raise ValueError("Giọng VieNeu đã chọn chưa sẵn sàng. Hãy cài model và runtime VieNeu.")
        elif self.tts_engine_name == "piper-tts":
            from core.engines.tts.piper_engine import PiperEngine
            self.tts_engine = PiperEngine()
            if not self.tts_engine.is_available:
                raise ValueError("Giọng Piper đã chọn chưa sẵn sàng. Hãy cài model và runtime Piper.")
        elif self.tts_engine_name != "edge-tts":
            raise ValueError(f"TTS engine không được hỗ trợ: {self.tts_engine_name}")

        media_info = None
        if self.visual_translation:
            media_info = await self._run_blocking(self.video_intelligence.media_info, self.video_path,
                                                   cancel_check=lambda: self.is_stopped)
        if media_info is not None and not media_info["has_audio"]:
            raise ValueError("Video không có luồng âm thanh. Studio hiện cần video có âm thanh để xử lý và xuất bản dịch.")
        else:
            from core.streaming.export import HQExporter
            self.video_size = await self._run_blocking(
                HQExporter._video_size, self.video_path, lambda: self.is_stopped)
            # 1. Extract 16kHz mono audio for VAD and legacy ASR.
            self.raw_audio_16k = self.cache_dir / "raw_audio_16k.wav"
            await self._run_ffmpeg([
                "ffmpeg", "-y", "-i", str(self.video_path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(self.raw_audio_16k),
            ])
            self.total_duration = await self._run_blocking(self.segmenter.get_audio_duration, self.raw_audio_16k)
            if self.total_duration <= 0:
                raise ValueError("Video không có âm thanh hợp lệ để dịch.")
            if media_info:
                self.total_duration = max(self.total_duration, media_info["duration"])

            # QtWebEngine requires an open codec for separated background audio.
            self.bgm_audio_path = self.cache_dir / "bgm_suppressed.ogg"
            await self.report_progress("prepare", "Đang xử lý nhạc nền và lọc thoại gốc...")
            self.suppression_stats = await self._run_blocking(
                self.vocal_suppressor.process_file, input_audio_path=self.video_path,
                output_audio_path=self.bgm_audio_path,
                forced_mode=None if settings.SUPPRESSION_MODE == "AUTO" else settings.SUPPRESSION_MODE,
                cancel_check=lambda: self.is_stopped,
            )
            self.bgm_url = f"/api/streaming/bgm/{self.task_id}"
            if self.visual_translation or self.asr_engine is self.faster_whisper:
                await self.report_progress("asr", "Đang nhận diện lời nói và mốc thời gian bằng Faster-Whisper...")
                recognized = await self._run_blocking(
                    self.faster_whisper.transcribe, self.raw_audio_16k, language="zh",
                )
                raw_segs = await self._run_blocking(self._grounded_visual_segments, recognized)
            else:
                await self.report_progress("prepare", "Đang phân chia câu thoại...")
                raw_segs = await self._run_blocking(self.segmenter.segment_audio, self.raw_audio_16k)
        for s in raw_segs:
            item = SegmentItem(s["id"], s["start"], s["end"], s["duration"])
            if self.visual_translation or self.asr_engine is self.faster_whisper:
                item.text_zh = s["text_zh"]
                item.emotion = s.get("emotion") or "<|NEUTRAL|>"
                item.asr_pretranscribed = True
            self.segments[item.id] = item

        if self.visual_translation and self.video_intelligence:
            await self.report_progress("visual", "Đang đọc phụ đề và tiêu đề trong khung hình...")
            event_loop = asyncio.get_running_loop()
            def visual_progress(percent):
                if not self.is_stopped:
                    event_loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self.report_progress(
                            "visual", "Đang đối chiếu lời thoại, phụ đề và tiêu đề", percent)))
            try:
                visual_result = await self._run_blocking(
                    self.video_intelligence.prepass,
                    self.video_path,
                    list(self.segments.values()),
                    total_duration=self.total_duration,
                    cancel_check=lambda: self.is_stopped,
                    progress_callback=visual_progress,
                )
            finally:
                # _run_blocking waits for cancelled native work to return; only
                # then is it safe to release this session's OCR/ASR models.
                self._release_visual_runtime()
            for source in visual_result.get("translation_sources", []):
                provider = source.get("provider") if isinstance(source, dict) else None
                if provider not in ("gemini", "openrouter-free", "opencode"):
                    raise ValueError("Bản dịch chưa xác định đúng nhà cung cấp đã xử lý.")
                fallback = provider in ("openrouter-free", "opencode")
                default_model = {"opencode": settings.OPENCODE_MODEL, "openrouter-free": settings.OPENROUTER_MODEL,
                                 "gemini": settings.GEMINI_MODEL}[provider]
                normalized_source = {"provider": provider,
                                     "model": str(source.get("model") or default_model)[:200],
                                     "evidence_mode": "asr-ocr-text" if fallback else "audio-video"}
                if normalized_source not in self.translation_sources:
                    self.translation_sources.append(normalized_source)
            self.screen_texts = list(visual_result.get("screen_texts", []))
            uncertain_screens = sum(bool(item.get("needs_review")) for item in self.screen_texts)
            if uncertain_screens:
                self.warnings.append(
                    f"Có {uncertain_screens} vùng chữ chưa chắc chắn về nội dung hoặc vị trí; giữ nguyên hình gốc tại các vùng này."
                )
            for item in self.segments.values():
                data = visual_result.get("segments", {}).get(item.id)
                if not data:
                    raise ValueError("Phân tích hình ảnh thiếu câu thoại; không tự chuyển sang dịch âm thanh.")
                item.text_zh = data.get("text_zh", "").strip()
                item.literal_vi = data.get("literal_vi", "").strip()
                item.natural_vi = data.get("natural_vi", "").strip()
                item.final_vi = data.get("final_vi", "").strip()
                provider = data.get("translation_provider")
                if provider is None:
                    provider = ("openrouter-free" if getattr(self.video_intelligence, "used_text_fallback", False)
                                else self.video_intelligence.provider)
                if provider not in ("gemini", "openrouter-free", "opencode"):
                    raise ValueError("Bản dịch chưa xác định đúng nhà cung cấp đã xử lý.")
                fallback = provider in ("openrouter-free", "opencode")
                default_model = {"opencode": settings.OPENCODE_MODEL, "openrouter-free": settings.OPENROUTER_MODEL,
                                 "gemini": settings.GEMINI_MODEL}[provider]
                item.source_method = "text-ai" if fallback else "video-ai"
                item.translation_provider = provider
                item.translation_model = str(data.get("translation_model") or default_model)[:200]
                item.evidence_mode = "asr-ocr-text" if fallback else "audio-video"
                source = {"provider": item.translation_provider, "model": item.translation_model,
                          "evidence_mode": item.evidence_mode}
                if source not in self.translation_sources:
                    self.translation_sources.append(source)
                item.needs_review = bool(data.get("needs_review", False))
                item.review_reason = data.get("review_reason")
            if (self.video_intelligence.provider == "gemini"
                    and any(source["provider"] == "openrouter-free" for source in self.translation_sources)):
                self.warnings.append(
                    "Gemini hết hạn mức; một phần bản dịch dùng OpenRouter miễn phí từ lời nhận diện Faster-Whisper "
                    "và chữ OCR trên máy. Phần này chưa được AI xem/nghe video để kiểm chứng; "
                    "cần duyệt lại lời thoại trước khi tạo giọng. "
                    "Chữ trên hình của phần dự phòng chưa được đối chiếu hình ảnh nên giữ nguyên khi xuất."
                )

        # 3. Enqueue all segments with initial priority based on time
        for item in self.segments.values():
            await self.queue.put((item.start, item.id))

        # 4. Emit initialization info to client
        self.initialized = True
        await self.emit("init", {
            "duration": self.total_duration,
            "segments_count": len(self.segments),
            "segments": [self.segment_snapshot(s) for s in self.segments.values()],
            "initial_buffer_seconds": self.initial_buffer_seconds,
            "bgm_url": self.bgm_url,
            "vocal_removal_engine": self.vocal_suppressor.name,
            "suppression_level": f"{self.vocal_suppressor.suppression_level_db:.1f} dB",
            "suppression_rtf": self.suppression_stats.get("throughput_rtf", "0.0x"),
            "asr_engine": self.source_processing_label(),
            "tts_engine": self.tts_engine.name,
            "warnings": self.warnings,
            "visual_translation": self.visual_translation,
            "screen_texts": self.screen_texts,
            "translation_sources": self.translation_sources,
        })

        # 5. Launch background worker
        await self._segment_progress("asr", "Đang chuẩn bị nhận diện lời nói")
        self.worker_task = asyncio.create_task(self._worker_loop())
        self.worker_task.add_done_callback(
            lambda task: self._release_runtime() if task.cancelled() else None
        )

    def _grounded_visual_segments(self, recognized):
        """Retain ASR sentence boundaries and words instead of fixed VAD slots."""
        if not isinstance(recognized, list):
            raise ValueError("Faster-Whisper chưa trả danh sách lời thoại có mốc thời gian.")
        result = []
        previous_end = 0.0
        max_duration = self.video_intelligence.MAX_CHUNK_SECONDS if self.video_intelligence else 45
        for row in recognized:
            if not isinstance(row, dict):
                raise ValueError("Faster-Whisper trả câu thoại không hợp lệ.")
            text = row.get("text_zh", row.get("text", ""))
            if not isinstance(text, str):
                raise ValueError("Faster-Whisper trả nội dung lời thoại không hợp lệ.")
            text = text.strip()
            if not text:
                continue
            start, end = row.get("start"), row.get("end")
            if (any(isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) for value in (start, end))
                    or start < previous_end or end <= start or end > self.total_duration + 0.05):
                raise ValueError("Faster-Whisper trả mốc lời thoại không hợp lệ; không tự thay đổi thời gian câu.")
            # Retain measured sentences within the media limit. Longer rows need
            # real word timestamps; uniform text/time cuts are not alignment.
            if end - start > max_duration:
                parts = self._split_long_visual_sentence(row)
            else:
                parts = [{"start": start, "end": end, "text_zh": text}]
            for part in parts:
                if not 0 < part["end"] - part["start"] <= max_duration:
                    raise ValueError("Không tìm được mốc từ để chia câu dài trước khi đối chiếu hình ảnh.")
                result.append({"id": len(result), "start": part["start"], "end": part["end"],
                               "duration": part["end"] - part["start"], "text_zh": part["text_zh"],
                               "emotion": row.get("emotion")})
            previous_end = end
        return result

    def source_processing_label(self):
        if not self.visual_translation:
            return getattr(getattr(self, "asr_engine", None), "name", self.asr_engine_name)
        providers = {source["provider"] for source in self.translation_sources}
        if not providers and self.video_intelligence:
            providers = {self.video_intelligence.provider}
        if providers == {"opencode"}:
            return "ASR + OCR miễn phí · Faster-Whisper + OpenCode · dịch văn bản"
        if providers == {"openrouter-free"}:
            return "ASR + OCR miễn phí · Faster-Whisper + OpenRouter · dịch văn bản"
        if "openrouter-free" in providers:
            return "Faster-Whisper + OCR tại máy; Gemini + OpenRouter · xem chi tiết từng câu"
        return "Faster-Whisper · lời nói và thời gian; Gemini · đối chiếu hình ảnh"

    def _release_visual_runtime(self):
        if self.visual_translation:
            ocr = getattr(self.video_intelligence, "screen_ocr", None)
            if ocr is not None:
                ocr.close()
            self.faster_whisper.model = None
            self.sensevoice.recognizer = None

    def _split_long_visual_sentence(self, row):
        """Use the already loaded model to measure words only when a row is too long."""
        start, end = row["start"], row["end"]
        words = row.get("words")
        if words is None:
            if self.faster_whisper.model is None:
                raise ValueError("Chưa có model nhận diện để đo mốc từ của câu dài.")
            measured, _ = self.faster_whisper.model.transcribe(
                str(self.raw_audio_16k), language="zh", beam_size=5,
                word_timestamps=True, vad_filter=False, clip_timestamps=[start, end],
            )
            words = []
            for sentence in measured:
                if self.is_stopped:
                    raise RuntimeError("Đã hủy nhận diện mốc từ của câu dài.")
                words.extend({"word": word.word, "start": word.start, "end": word.end}
                             for word in (sentence.words or []))
        if not isinstance(words, list) or not words:
            raise ValueError("Không nhận diện được mốc từ để chia câu dài.")
        groups, group = [], []
        previous_end = start
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("word"), str):
                raise ValueError("Mốc từ của câu dài không hợp lệ.")
            left, right = word.get("start"), word.get("end")
            if (any(isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) for value in (left, right))
                    or left < previous_end or right < left or right > end):
                raise ValueError("Mốc từ của câu dài không nằm đúng trong câu thoại.")
            if not word["word"].strip():
                continue
            if group and right - group[0]["start"] > 8:
                groups.append(group)
                group = []
            group.append(word)
            previous_end = right
        if group:
            groups.append(group)
        if not groups:
            raise ValueError("Không có từ để chia câu dài.")
        return [{"start": start if index == 0 else group[0]["start"],
                 "end": end if index == len(groups) - 1 else group[-1]["end"],
                 "text_zh": "".join(word["word"] for word in group).strip()}
                for index, group in enumerate(groups)]

    async def seek(self, target_time: float):
        """Re-prioritizes processing to immediately serve the target playback position."""
        print(f"[*] Seek requested to {target_time:.2f}s in task {self.task_id}")
        self.current_playback_time = target_time

        # Drain unstarted items from queue
        unprocessed_ids = []
        while not self.queue.empty():
            try:
                _, seg_id = self.queue.get_nowait()
                self.queue.task_done()
                if self.segments[seg_id].status in ["WAITING", "FAILED"]:
                    unprocessed_ids.append(seg_id)
            except Exception:
                break

        # Re-insert with new priority: distance to target_time
        for seg_id in unprocessed_ids:
            seg = self.segments[seg_id]
            if seg.start >= target_time:
                prio = seg.start - target_time
            else:
                prio = 5000.0 + abs(target_time - seg.start)
            await self.queue.put((prio, seg_id))

        self._recalculate_telemetry()
        await self.emit("telemetry", self.get_telemetry())

    def update_playback_position(self, current_time: float):
        """Tracks the video playback head from client and updates buffer metrics."""
        self.current_playback_time = current_time
        # Mark passed segments
        for seg in self.segments.values():
            if seg.status == "READY" and seg.end < current_time - 1.0:
                seg.status = "PLAYED"

        self._recalculate_telemetry()

    def _recalculate_telemetry(self):
        # Calculate continuous playable range from current position
        now = self.current_playback_time
        playable = now

        sorted_segs = sorted(self.segments.values(), key=lambda s: s.start)
        for s in sorted_segs:
            if s.end <= now:
                continue
            if s.status in ["READY", "PLAYED"]:
                # Silence between speech segments needs no synthesis.
                playable = max(playable, s.end)
            else:
                playable = max(playable, s.start)
                break
        else:
            playable = max(playable, self.total_duration)

        self.playable_until = round(playable, 2)
        self.buffer_ahead = round(max(0.0, self.playable_until - self.current_playback_time), 2)

        elapsed = time.time() - self.start_wall_time
        if elapsed > 0 and self.total_processed_duration > 0:
            self.realtime_factor = round(self.total_processed_duration / elapsed, 2)

    def get_telemetry(self) -> Dict[str, Any]:
        return {
            "current_playback_time": round(self.current_playback_time, 2),
            "playable_until": self.playable_until,
            "buffer_ahead": self.buffer_ahead,
            "realtime_factor": self.realtime_factor,
            "time_to_first_play": self.time_to_first_play,
            "ready_to_play": self.first_play_emitted,
            "status": "cancelled" if self.is_stopped else ("failed" if self.error else ("running" if self.is_running else "finished")),
            "error": self.error,
            "warnings": list(self.warnings),
            "translation_sources": list(self.translation_sources),
            **self._review_metadata(),
            "can_retry": self.can_retry,
            "vocal_removal_engine": self.vocal_suppressor.name,
            "suppression_level": f"{self.vocal_suppressor.suppression_level_db:.1f} dB",
            "suppression_rtf": self.suppression_stats.get("throughput_rtf", "0.0x")
        }

    async def _run_ffmpeg(self, cmd):
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            _, stderr = await proc.communicate()
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[-1500:])

    async def _run_blocking(self, function, *args, **kwargs):
        """Cancellation cannot kill a Python worker thread; wait before removing its files."""
        if self.is_stopped:
            raise asyncio.CancelledError
        work = asyncio.get_running_loop().run_in_executor(
            None, functools.partial(function, *args, **kwargs),
        )
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(work)
                break
            except asyncio.CancelledError:
                if work.cancelled():
                    raise
                cancelled = True
                continue
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
        if cancelled or self.is_stopped:
            raise asyncio.CancelledError
        return result

    def _release_runtime(self):
        if self.edit_tasks:
            return
        if self.is_stopped:
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if any(task is not None and task is not current and not task.done()
                   for task in (self.start_task, self.worker_task)):
                return
        # Finished sessions retain media for replay/export, not another copy of
        # Whisper/SenseVoice in RAM for every video processed in this app.
        self.faster_whisper.model = None
        self.sensevoice.recognizer = None
        self._release_visual_runtime()
        # VieNeu/Piper share one locked CPU model across previews and sessions.
        # Dropping it here would interrupt another session or reload it per sample.
        def remove_generated(path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Windows may still have an HTTP media read open. Releasing
                # runtime state must not turn successful work into a failure.
                pass

        raw_audio = getattr(self, "raw_audio_16k", None)
        needs_remaining_asr = (self.can_retry and not self.visual_translation and any(
            segment.status == "WAITING" and not getattr(segment, "asr_pretranscribed", False)
            for segment in self.segments.values()))
        if raw_audio and not needs_remaining_asr:
            remove_generated(raw_audio)
        if self.is_stopped and self._owns_cache:
            # A stopped session is removed from the registry, so its generated
            # media can no longer be replayed/exported. Remove only paths that
            # this session generated; never recurse into user-owned content.
            if self.bgm_audio_path:
                remove_generated(self.bgm_audio_path)
            for segment_id in self.segments:
                remove_generated(self.segments_dir / f"seg_{segment_id}.wav")
            for directory in (self.segments_dir, self.cache_dir):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        if self.is_stopped and self._download_info and not self._download_info.get("is_local"):
            # The downloader reports only uniquely named artifacts it created.
            # Never glob a prefix or remove a local user input here.
            prefix = self._download_info.get("owned_prefix")
            if prefix:
                prefix_path = Path(prefix).resolve()
                for value in self._download_info.get("owned_paths", []):
                    owned_path = Path(value).resolve()
                    if owned_path.parent == prefix_path.parent and owned_path.name.startswith(prefix_path.name + "."):
                        remove_generated(owned_path)

    async def _update_ready(self):
        self._recalculate_telemetry()
        if not self.first_play_emitted and (
            self.buffer_ahead >= self.initial_buffer_seconds
            or self.playable_until >= self.total_duration - 0.05
        ):
            self.first_play_emitted = True
            self.time_to_first_play = round(time.time() - self.start_wall_time, 2)
            await self.emit("ready_to_play", {
                "time_to_first_play": self.time_to_first_play,
                "playable_until": self.playable_until,
            })
        await self.emit("telemetry", self.get_telemetry())

    async def _process_segment(self, seg):
        if self.visual_translation and seg.source_method not in ("video-ai", "text-ai"):
            raise RuntimeError("Thiếu bản dịch hình ảnh đã kiểm tra; không tự chuyển nhà cung cấp hoặc dịch âm thanh.")
        if self.visual_translation and seg.source_method in ("video-ai", "text-ai"):
            # The prepass supplied the transcript and translation. Do not
            # run a second ASR/translation pass that could overwrite it.
            # Review is an editorial flag, independent of playable draft audio.
            return await self._synthesize_segment(seg)
        seg.status = "ASR"
        await self._segment_progress("asr", f"Đang nhận diện câu {seg.id + 1}")
        await self.emit("segment_update", seg.to_dict())
        slice_wav = self.cache_dir / f"slice_{seg.id}.wav"
        try:
            if not getattr(seg, "asr_pretranscribed", False):
                await self._run_ffmpeg([
                    "ffmpeg", "-y", "-ss", f"{seg.start:.3f}",
                    "-i", str(self.raw_audio_16k), "-t", f"{seg.duration:.3f}",
                    "-c:a", "pcm_s16le", str(slice_wav),
                ])
                asr_res = await self._run_blocking(self.asr_engine.transcribe, slice_wav, language="zh")
                seg.text_zh = " ".join(
                    part.get("text_zh", part.get("text", "")).strip() for part in (asr_res or [])
                ).strip()
                seg.emotion = asr_res[0].get("emotion", "<|NEUTRAL|>") if asr_res else "<|NEUTRAL|>"
            if seg.text_zh:
                seg.status = "TRANSLATING"
                await self._segment_progress("translate", f"Đang dịch câu {seg.id + 1}")
                await self.emit("segment_update", seg.to_dict())
                trans = await self._run_blocking(
                    self.translator.translate_single_segment,
                    text_zh=seg.text_zh, duration=seg.duration,
                    rolling_context=self.rolling_context,
                )
                seg.literal_vi = trans.get("literal_vi", "")
                seg.natural_vi = trans.get("natural_vi", "")
                seg.final_vi = trans.get("final_vi") or seg.natural_vi or seg.literal_vi
                if trans.get("needs_review"):
                    seg.needs_review = True
                    seg.review_reason = trans.get("review_reason") or "Nhận dạng câu thoại chưa chắc chắn; hãy kiểm tra và sửa tiếng Việt."
                if not seg.final_vi.strip() and not seg.needs_review:
                    raise RuntimeError("Dịch thuật trả về nội dung trống.")
                if seg.final_vi.strip() and not seg.needs_review:
                    self.rolling_context.append({"zh": seg.text_zh, "vi": seg.final_vi})
                    self.rolling_context = self.rolling_context[-10:]
            await self._synthesize_segment(seg)
        finally:
            slice_wav.unlink(missing_ok=True)

    async def _synthesize_segment(self, seg):
        """Generate playable audio without approving an uncertain translation."""
        raw_tts_wav = self.cache_dir / f"tts_{seg.id}_raw.wav"
        try:
            if not seg.final_vi.strip():
                if seg.text_zh.strip() and not seg.needs_review:
                    raise RuntimeError("Dịch thuật trả về nội dung trống.")
                # An empty uncertain draft has nothing defensible to read.
                # Keep its review flag, but allow playback past its time span.
                seg.status = "READY"
                seg.failed_stage = None
                seg._retry_synthesis = False
                self.total_processed_duration += seg.duration
                await self.emit("segment_update", seg.to_dict())
                await self._update_ready()
                return
            seg.status = "TTS"
            await self._segment_progress("tts", f"Đang tạo giọng đọc câu {seg.id + 1}")
            await self.emit("segment_update", seg.to_dict())
            async with self._tts_lock:
                await self._run_blocking(self.tts_engine.synthesize, text=seg.final_vi,
                                         output_path=raw_tts_wav, voice=self.voice, ref_audio=self.ref_audio)
                boundaries = take_tts_word_boundaries(self.tts_engine, raw_tts_wav)
                await self._run_blocking(trim_tts_padding, raw_tts_wav)
            seg.status = "ALIGNING"
            await self._segment_progress("align", f"Đang khớp thời lượng câu {seg.id + 1}")
            await self.emit("segment_update", seg.to_dict())
            tts_dur = await self._run_blocking(self.aligner.get_audio_duration, raw_tts_wav)
            if tts_dur <= 0:
                raise RuntimeError("Không đọc được âm thanh từ TTS.")
            ratio = max(self.aligner.min_speed, tts_dur / max(0.01, seg.duration))
            if ratio > self.aligner.max_speed:
                self.warnings.append(
                    f"Đoạn {seg.id + 1} cần đọc {ratio:.2f}x để giữ đủ lời trong thời lượng gốc."
                )
            final_path = self.segments_dir / f"seg_{seg.id}.wav"
            ratio = await self._run_blocking(self.aligner.apply_atempo, raw_tts_wav, final_path, ratio,
                                             fit_duration=seg.duration)
            seg.tts_duration = tts_dur
            seg.speed_ratio = round(ratio, 2)
            seg.audio_path = str(final_path.resolve())
            timing = await self._run_blocking(build_speech_timing, seg.final_vi, seg.start, seg.end,
                                              final_path, ratio, boundaries)
            for key, value in timing.items():
                setattr(seg, key, value)
            seg.audio_url = f"/api/streaming/audio/{self.task_id}/{seg.id}"
            seg.status = "READY"
            seg.failed_stage = None
            seg._retry_synthesis = False
            self.total_processed_duration += seg.duration
            await self.emit("segment_update", seg.to_dict())
            await self._update_ready()
        finally:
            raw_tts_wav.unlink(missing_ok=True)

    def _review_message(self):
        count = sum(s.needs_review for s in self.segments.values())
        return f"Có {count} câu cần kiểm tra. Bạn có thể nghe bản nháp rồi sửa trực tiếp trong Transcript."

    def _review_metadata(self):
        count = sum(s.needs_review for s in self.segments.values())
        return {"review_count": count, "review_message": self._review_message() if count else None}

    async def _worker_loop(self):
        try:
            # Videos with no speech must still become playable.
            await self._update_ready()
            while self.is_running and not self.queue.empty():
                await self.pause_event.wait()
                if not self.is_running:
                    break
                _, seg_id = await self.queue.get()
                seg = self.segments.get(seg_id)
                try:
                    if seg and seg.status not in ("READY", "PLAYED"):
                        if seg._retry_synthesis:
                            await self._synthesize_segment(seg)
                        else:
                            await self._process_segment(seg)
                except Exception as exc:
                    self.error = str(exc)
                    # SDK exception strings can contain prompts or credentials.
                    # Keep a useful task/stage/type diagnostic without raw text.
                    logging.getLogger("errors").error(
                        "[%s] Lỗi câu %s ở bước %s (%s)", self.task_id, seg_id + 1,
                        seg.status if seg else "UNKNOWN", type(exc).__name__,
                    )
                    if seg:
                        seg.failed_stage = seg.status
                        seg.status = "FAILED"
                        seg.error = self.error
                        await self.emit("segment_update", seg.to_dict())
                    await self.emit("error", {"message": self.error, "segment_id": seg_id})
                    break
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            self.is_stopped = True
            raise
        finally:
            self.is_running = False
            self._release_runtime()
            if not self.is_stopped and not self.error:
                stage = self._review_message() if any(s.needs_review for s in self.segments.values()) else "Dịch và lồng tiếng hoàn tất"
                await self.report_progress("complete", stage, 100)
            else:
                await self.emit("progress", self.get_progress())
            await self.emit("finished", self.get_telemetry())

    def pause(self):
        self.is_paused = True
        self.pause_event.clear()

    def resume(self):
        self.is_paused = False
        self.pause_event.set()

    def stop(self):
        # Desktop shutdown may call from another thread after normal processing
        # has completed, while a transcript edit still owns the backend loop.
        owner = next(iter(self.edit_tasks), None)
        if owner is not None:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            if owner.get_loop().is_running() and owner.get_loop() is not current_loop:
                owner.get_loop().call_soon_threadsafe(self.stop)
                return
        if self.is_stopped:
            return
        self.is_stopped = True
        self.is_running = False
        self.pause_event.set()
        if self.start_task and not self.start_task.done():
            self.start_task.cancel()
        if self.worker_task and not self.worker_task.done():
            self.worker_task.cancel()
        for task in tuple(self.edit_tasks):
            if not task.done():
                task.cancel()
        if not self.start_task and (not self.worker_task or self.worker_task.done()):
            self._release_runtime()
        active_streaming_sessions.pop(self.task_id, None)

# Session Manager
active_streaming_sessions: Dict[str, StreamingPipelineSession] = {}

def get_streaming_session(task_id: str) -> Optional[StreamingPipelineSession]:
    return active_streaming_sessions.get(task_id)

def create_streaming_session(
    task_id: str,
    video_path: Optional[Path],
    initial_buffer_seconds: Optional[float] = None,
    voice: Optional[str] = None,
    tts_engine_name: Optional[str] = None,
    asr_engine_name: Optional[str] = None,
    ref_audio: Optional[Path] = None,
    event_callback: Optional[Callable] = None,
    visual_translation: bool = False,
) -> StreamingPipelineSession:
    sess = StreamingPipelineSession(
        task_id=task_id,
        video_path=video_path,
        initial_buffer_seconds=initial_buffer_seconds,
        voice=voice,
        tts_engine_name=tts_engine_name,
        asr_engine_name=asr_engine_name,
        ref_audio=ref_audio,
        event_callback=event_callback,
        visual_translation=visual_translation,
    )
    active_streaming_sessions[task_id] = sess
    return sess
