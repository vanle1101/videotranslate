import asyncio
import time
import subprocess
import functools
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from config import settings

from core.streaming.segmenter import AudioSegmenter
from core.engines.asr.sensevoice_engine import SenseVoiceEngine
from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.engines.translation.semantic_translator import SemanticTranslator
from core.engines.tts.vieneu_engine import VieNeuEngine
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.engines.alignment.timing_aligner import TimingBudgetAligner
from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor

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
            "error": self.error
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
        event_callback: Optional[Callable[[str, Dict[str, Any]], Any]] = None
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
        self.voice = voice or settings.EDGE_VOICE
        self.tts_engine_name = tts_engine_name or settings.TTS_ENGINE
        self.asr_engine_name = asr_engine_name or settings.ASR_ENGINE
        self.ref_audio = ref_audio
        self.event_callback = event_callback

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

    async def emit(self, event_type: str, data: Dict[str, Any]):
        if self.event_callback:
            payload = {"type": event_type, "task_id": self.task_id, **data}
            if asyncio.iscoroutinefunction(self.event_callback):
                await self.event_callback(event_type, payload)
            else:
                res = self.event_callback(event_type, payload)
                if asyncio.iscoroutine(res):
                    await res

    def get_progress(self) -> Dict[str, Any]:
        snapshot = dict(self.progress)
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
        if self.asr_engine_name == "sensevoice":
            if self.sensevoice.is_available:
                self.asr_engine = self.sensevoice
            else:
                self.warnings.append("SenseVoice chưa có model; sử dụng Faster-Whisper.")
        elif self.asr_engine_name not in ("faster-whisper", "whisper"):
            raise ValueError(f"ASR engine không được hỗ trợ: {self.asr_engine_name}")
        self.tts_engine = self.edge_tts
        if self.tts_engine_name in ("vieneu", "vieneu-tts"):
            self.vieneu = VieNeuEngine()
            if self.vieneu.is_available:
                self.tts_engine = self.vieneu
            else:
                self.warnings.append("VieNeu chưa được cài; sử dụng giọng Edge-TTS.")
        elif self.tts_engine_name != "edge-tts":
            raise ValueError(f"TTS engine không được hỗ trợ: {self.tts_engine_name}")

        # 1. Extract 16kHz mono audio for fast slicing and ASR
        self.raw_audio_16k = self.cache_dir / "raw_audio_16k.wav"
        cmd = [
            "ffmpeg", "-y", "-i", str(self.video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(self.raw_audio_16k)
        ]
        await self._run_ffmpeg(cmd)

        self.total_duration = await self._run_blocking(self.segmenter.get_audio_duration, self.raw_audio_16k)
        if self.total_duration <= 0:
            raise ValueError("Video không có âm thanh hợp lệ để dịch.")

        # 1.5 Extract suppressed BGM & SFX (Removes Chinese Speech by -26dB, preserves BGM & Foley)
        # The bundled QtWebEngine does not ship proprietary AAC decoding.
        self.bgm_audio_path = self.cache_dir / "bgm_suppressed.ogg"
        await self.report_progress("prepare", "Đang xử lý nhạc nền và lọc thoại gốc...")
        self.suppression_stats = await self._run_blocking(
            self.vocal_suppressor.process_file,
            input_audio_path=self.video_path,
            output_audio_path=self.bgm_audio_path,
            forced_mode=None if settings.SUPPRESSION_MODE == "AUTO" else settings.SUPPRESSION_MODE,
            cancel_check=lambda: self.is_stopped,
        )
        self.bgm_url = f"/api/streaming/bgm/{self.task_id}"

        # 2. Discover natural sentence segments
        await self.report_progress("prepare", "Đang phân chia câu thoại...")
        raw_segs = await self._run_blocking(self.segmenter.segment_audio, self.raw_audio_16k)
        for s in raw_segs:
            item = SegmentItem(s["id"], s["start"], s["end"], s["duration"])
            self.segments[item.id] = item

        # 3. Enqueue all segments with initial priority based on time
        for item in self.segments.values():
            await self.queue.put((item.start, item.id))

        # 4. Emit initialization info to client
        self.initialized = True
        await self.emit("init", {
            "duration": self.total_duration,
            "segments_count": len(self.segments),
            "segments": [s.to_dict() for s in self.segments.values()],
            "initial_buffer_seconds": self.initial_buffer_seconds,
            "bgm_url": self.bgm_url,
            "vocal_removal_engine": self.vocal_suppressor.name,
            "suppression_level": f"{self.vocal_suppressor.suppression_level_db:.1f} dB",
            "suppression_rtf": self.suppression_stats.get("throughput_rtf", "0.0x"),
            "asr_engine": self.asr_engine.name,
            "tts_engine": self.tts_engine.name,
            "warnings": self.warnings
        })

        # 5. Launch background worker
        await self._segment_progress("asr", "Đang chuẩn bị nhận diện lời nói")
        self.worker_task = asyncio.create_task(self._worker_loop())
        self.worker_task.add_done_callback(
            lambda task: self._release_runtime() if task.cancelled() else None
        )

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
        # Finished sessions retain media for replay/export, not another copy of
        # Whisper/SenseVoice in RAM for every video processed in this app.
        self.faster_whisper.model = None
        self.sensevoice.recognizer = None
        if self.vieneu is not None:
            self.vieneu.model = None
        def remove_generated(path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Windows may still have an HTTP media read open. Releasing
                # runtime state must not turn successful work into a failure.
                pass

        raw_audio = getattr(self, "raw_audio_16k", None)
        if raw_audio:
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
        seg.status = "ASR"
        await self._segment_progress("asr", f"Đang nhận diện câu {seg.id + 1}")
        await self.emit("segment_update", seg.to_dict())
        slice_wav = self.cache_dir / f"slice_{seg.id}.wav"
        raw_tts_wav = self.cache_dir / f"tts_{seg.id}_raw.wav"
        try:
            await self._run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{seg.start:.3f}",
                "-i", str(self.raw_audio_16k), "-t", f"{seg.duration:.3f}",
                "-c:a", "pcm_s16le", str(slice_wav),
            ])
            asr_res = await self._run_blocking(self.asr_engine.transcribe, slice_wav, language="zh")
            # A slice can contain several sentences. Keep every recognized word.
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
                    rolling_context=self.rolling_context, pronouns="mình - các bạn",
                )
                seg.literal_vi = trans.get("literal_vi", "")
                seg.natural_vi = trans.get("natural_vi", "")
                seg.final_vi = trans.get("final_vi") or seg.natural_vi or seg.literal_vi
                if not seg.final_vi.strip():
                    raise RuntimeError("Dịch thuật trả về nội dung trống.")
                self.rolling_context.append({"zh": seg.text_zh, "vi": seg.final_vi})
                self.rolling_context = self.rolling_context[-10:]

                seg.status = "TTS"
                await self._segment_progress("tts", f"Đang tạo giọng đọc câu {seg.id + 1}")
                await self.emit("segment_update", seg.to_dict())
                await self._run_blocking(
                    self.tts_engine.synthesize, text=seg.final_vi,
                    output_path=raw_tts_wav, voice=self.voice, ref_audio=self.ref_audio,
                )
                seg.status = "ALIGNING"
                await self._segment_progress("align", f"Đang khớp thời lượng câu {seg.id + 1}")
                await self.emit("segment_update", seg.to_dict())
                tts_dur = await self._run_blocking(self.aligner.get_audio_duration, raw_tts_wav)
                if tts_dur <= 0:
                    raise RuntimeError("Không đọc được âm thanh từ TTS.")
                speed_ratio = max(self.aligner.min_speed, tts_dur / max(0.01, seg.duration))
                if speed_ratio > self.aligner.max_speed:
                    self.warnings.append(
                        f"Đoạn {seg.id + 1} cần đọc {speed_ratio:.2f}x để giữ đủ lời trong thời lượng gốc."
                    )
                final_seg_wav = self.segments_dir / f"seg_{seg.id}.wav"
                speed_ratio = await self._run_blocking(
                    self.aligner.apply_atempo, raw_tts_wav, final_seg_wav,
                    speed_ratio, fit_duration=seg.duration,
                )
                seg.tts_duration = tts_dur
                seg.speed_ratio = round(speed_ratio, 2)
                seg.audio_path = str(final_seg_wav.resolve())
                seg.audio_url = f"/api/streaming/audio/{self.task_id}/{seg.id}"
            seg.status = "READY"
            self.total_processed_duration += seg.duration
            await self._segment_progress("translate", "Đang dịch và lồng tiếng")
            await self.emit("segment_update", seg.to_dict())
            await self._update_ready()
        finally:
            slice_wav.unlink(missing_ok=True)
            raw_tts_wav.unlink(missing_ok=True)

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
                await self.report_progress("complete", "Dịch và lồng tiếng hoàn tất", 100)
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
        if self.is_stopped:
            return
        self.is_stopped = True
        self.is_running = False
        self.pause_event.set()
        if self.start_task and not self.start_task.done():
            self.start_task.cancel()
        if self.worker_task and not self.worker_task.done():
            self.worker_task.cancel()
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
    event_callback: Optional[Callable] = None
) -> StreamingPipelineSession:
    sess = StreamingPipelineSession(
        task_id=task_id,
        video_path=video_path,
        initial_buffer_seconds=initial_buffer_seconds,
        voice=voice,
        tts_engine_name=tts_engine_name,
        asr_engine_name=asr_engine_name,
        ref_audio=ref_audio,
        event_callback=event_callback
    )
    active_streaming_sessions[task_id] = sess
    return sess
