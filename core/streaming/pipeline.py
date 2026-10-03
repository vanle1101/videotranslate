import asyncio
import os
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from config import settings

from core.streaming.segmenter import AudioSegmenter
from core.engines.asr.sensevoice_engine import SenseVoiceEngine
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
            "audio_url": self.audio_url
        }

class StreamingPipelineSession:
    """
    Real-Time Streaming Localization Session.
    Processes segments ahead of playback using an asynchronous priority queue.
    """
    def __init__(
        self,
        task_id: str,
        video_path: Path,
        initial_buffer_seconds: float = 10.0,
        voice: str = "Trúc Ly",
        tts_engine_name: str = "vieneu",
        asr_engine_name: str = "sensevoice",
        ref_audio: Optional[Path] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], Any]] = None
    ):
        self.task_id = task_id
        self.video_path = video_path
        self.initial_buffer_seconds = initial_buffer_seconds
        self.voice = voice
        self.tts_engine_name = tts_engine_name
        self.asr_engine_name = asr_engine_name
        self.ref_audio = ref_audio
        self.event_callback = event_callback

        # Cache directories
        self.cache_dir = settings.BASE_DIR / "workspace" / "cache" / task_id
        self.segments_dir = self.cache_dir / "segments"
        self.segments_dir.mkdir(parents=True, exist_ok=True)

        # Engines (Lazy loaded or shared)
        self.segmenter = AudioSegmenter()
        self.sensevoice = SenseVoiceEngine()
        self.translator = SemanticTranslator()
        self.vieneu = VieNeuEngine()
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

        # Control flags & queues
        self.is_running = False
        self.is_paused = False
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.worker_task: Optional[asyncio.Task] = None
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

    async def start(self):
        """Initializes audio extraction, segmentation, and launches worker loop."""
        self.is_running = True
        self.start_wall_time = time.time()

        # 1. Extract 16kHz mono audio for fast slicing and ASR
        self.raw_audio_16k = self.cache_dir / "raw_audio_16k.wav"
        cmd = [
            "ffmpeg", "-y", "-i", str(self.video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(self.raw_audio_16k)
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()

        self.total_duration = self.segmenter.get_audio_duration(self.raw_audio_16k)

        # 1.5 Extract suppressed BGM & SFX (Removes Chinese Speech by -26dB, preserves BGM & Foley)
        self.bgm_audio_path = self.cache_dir / "bgm_suppressed.m4a"
        self.suppression_stats = await asyncio.to_thread(
            self.vocal_suppressor.process_file,
            input_audio_path=self.video_path,
            output_audio_path=self.bgm_audio_path
        )
        self.bgm_url = f"/api/streaming/bgm/{self.task_id}"

        # 2. Discover natural sentence segments
        raw_segs = self.segmenter.segment_audio(self.raw_audio_16k)
        for s in raw_segs:
            item = SegmentItem(s["id"], s["start"], s["end"], s["duration"])
            self.segments[item.id] = item

        # 3. Enqueue all segments with initial priority based on time
        for item in self.segments.values():
            await self.queue.put((item.start, item.id))

        # 4. Emit initialization info to client
        await self.emit("init", {
            "duration": self.total_duration,
            "segments_count": len(self.segments),
            "segments": [s.to_dict() for s in self.segments.values()],
            "initial_buffer_seconds": self.initial_buffer_seconds,
            "bgm_url": self.bgm_url,
            "vocal_removal_engine": self.vocal_suppressor.name,
            "suppression_level": f"{self.vocal_suppressor.suppression_level_db:.1f} dB",
            "suppression_rtf": self.suppression_stats.get("throughput_rtf", "75.0x")
        })

        # 5. Launch background worker
        self.worker_task = asyncio.create_task(self._worker_loop())

    async def seek(self, target_time: float):
        """Re-prioritizes processing to immediately serve the target playback position."""
        print(f"[*] Seek requested to {target_time:.2f}s in task {self.task_id}")
        self.current_playback_time = target_time

        # Drain unstarted items from queue
        unprocessed_ids = []
        while not self.queue.empty():
            try:
                _, seg_id = self.queue.get_nowait()
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
                if s.start <= playable + 0.8: # small gap tolerance
                    playable = max(playable, s.end)
                else:
                    break
            else:
                break

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
            "status": "running" if self.is_running else "finished",
            "vocal_removal_engine": self.vocal_suppressor.name,
            "suppression_level": f"{self.vocal_suppressor.suppression_level_db:.1f} dB",
            "suppression_rtf": self.suppression_stats.get("throughput_rtf", "75.0x")
        }

    async def _worker_loop(self):
        """Sequential/Parallel Worker executing ASR -> Translation -> TTS -> Alignment."""
        tts_engine = self.vieneu if self.tts_engine_name == "vieneu" else self.edge_tts

        while self.is_running and not self.queue.empty():
            await self.pause_event.wait()

            try:
                _, seg_id = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            seg = self.segments.get(seg_id)
            if not seg or seg.status == "READY":
                continue

            # Stage 1: Slicing & ASR
            seg.status = "ASR"
            await self.emit("segment_update", seg.to_dict())

            slice_wav = self.cache_dir / f"slice_{seg.id}.wav"
            cmd = [
                "ffmpeg", "-y", "-ss", f"{seg.start:.3f}", "-to", f"{seg.end:.3f}",
                "-i", str(self.raw_audio_16k), "-c", "copy", str(slice_wav)
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await proc.wait()

            # FunAudioLLM / SenseVoice ASR
            asr_res = await asyncio.to_thread(self.sensevoice.transcribe, slice_wav, language="zh")
            if asr_res and len(asr_res) > 0:
                seg.text_zh = asr_res[0].get("text_zh", "")
                seg.emotion = asr_res[0].get("emotion", "<|NEUTRAL|>")
            else:
                seg.text_zh = ""
                seg.emotion = "<|NEUTRAL|>"

            # If no speech detected in slice
            if not seg.text_zh.strip():
                seg.status = "READY"
                self.total_processed_duration += seg.duration
                self._recalculate_telemetry()
                await self.emit("segment_update", seg.to_dict())
                continue

            # Stage 2: VideoLingo Translation with Rolling Context & Time-Budget
            seg.status = "TRANSLATING"
            await self.emit("segment_update", seg.to_dict())

            trans = await asyncio.to_thread(
                self.translator.translate_single_segment,
                text_zh=seg.text_zh,
                duration=seg.duration,
                rolling_context=self.rolling_context,
                pronouns="mình - các bạn"
            )
            seg.literal_vi = trans.get("literal_vi", "")
            seg.natural_vi = trans.get("natural_vi", "")
            seg.final_vi = trans.get("final_vi", seg.natural_vi)

            self.rolling_context.append({"zh": seg.text_zh, "vi": seg.final_vi})
            if len(self.rolling_context) > 10:
                self.rolling_context.pop(0)

            # Stage 3: VieNeu-TTS v3 Turbo Synthesis
            seg.status = "TTS"
            await self.emit("segment_update", seg.to_dict())

            raw_tts_wav = self.cache_dir / f"tts_{seg.id}_raw.wav"
            final_seg_wav = self.segments_dir / f"seg_{seg.id}.wav"

            await asyncio.to_thread(
                tts_engine.synthesize,
                text=seg.final_vi,
                output_path=raw_tts_wav,
                voice=self.voice,
                ref_audio=self.ref_audio
            )

            # Stage 4: Timing Alignment & Clamp (0.90x - 1.15x)
            seg.status = "ALIGNING"
            tts_dur = self.aligner.get_audio_duration(raw_tts_wav)
            speed_ratio = round(tts_dur / max(0.5, seg.duration), 2)

            self.aligner.apply_atempo(raw_tts_wav, final_seg_wav, speed_ratio)

            seg.tts_duration = tts_dur
            seg.speed_ratio = speed_ratio
            seg.audio_path = str(final_seg_wav.resolve())
            seg.audio_url = f"/api/streaming/audio/{self.task_id}/{seg.id}"
            seg.status = "READY"

            self.total_processed_duration += seg.duration
            self._recalculate_telemetry()

            # Check Initial Buffer Trigger
            if not self.first_play_emitted:
                if self.playable_until >= self.initial_buffer_seconds or self.playable_until >= self.total_duration - 1.0:
                    self.first_play_emitted = True
                    self.time_to_first_play = round(time.time() - self.start_wall_time, 2)
                    print(f"[🎯] TIME TO FIRST PLAY: {self.time_to_first_play}s (Buffered {self.playable_until:.1f}s)")
                    await self.emit("ready_to_play", {
                        "time_to_first_play": self.time_to_first_play,
                        "playable_until": self.playable_until
                    })

            await self.emit("segment_update", seg.to_dict())
            await self.emit("telemetry", self.get_telemetry())

        print(f"[*] Worker completed all segments for task {self.task_id}")
        self.is_running = False
        await self.emit("finished", self.get_telemetry())

    def pause(self):
        self.is_paused = True
        self.pause_event.clear()

    def resume(self):
        self.is_paused = False
        self.pause_event.set()

    def stop(self):
        self.is_running = False
        self.pause_event.set()
        if self.worker_task:
            self.worker_task.cancel()
        active_streaming_sessions.pop(self.task_id, None)

# Session Manager
active_streaming_sessions: Dict[str, StreamingPipelineSession] = {}

def get_streaming_session(task_id: str) -> Optional[StreamingPipelineSession]:
    return active_streaming_sessions.get(task_id)

def create_streaming_session(
    task_id: str,
    video_path: Path,
    initial_buffer_seconds: float = 10.0,
    voice: str = "Trúc Ly",
    tts_engine_name: str = "vieneu",
    asr_engine_name: str = "sensevoice",
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
