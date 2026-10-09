import asyncio
import contextvars
from contextlib import contextmanager, nullcontext
import time
import subprocess
import functools
import logging
import hashlib
import math
import uuid
import os
import threading
import wave
from concurrent.futures import TimeoutError as FutureTimeoutError
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from config import settings
from core.runtime_context import execution_context, current_execution_context
from core.runtime_errors import redacted_detail

from core.streaming.segmenter import AudioSegmenter
from core.engines.asr.sensevoice_engine import SenseVoiceEngine
from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.engines.tts.vieneu_engine import VieNeuEngine
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.voice_catalog import resolve_voice
from core.engines.alignment.timing_aligner import TimingBudgetAligner, SpeechBudgetError
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from core.engines.alignment.speech_timing import build_speech_timing, take_tts_word_boundaries, trim_tts_padding
from core.streaming.audio_cache import resolve_dub_timing
from core.streaming.output_gate import final_output_metadata, missing_spoken_output_ids, missing_speech_message
from core.engines.alignment.dub_timing import available_reflow_duration, plan_reflow, MAX_TAIL_LIMIT_EXTENSION

class SegmentEditConflict(RuntimeError):
    """Editing would conflict with the current session state."""


class ProjectEditSaveError(RuntimeError):
    """An edit was not durably committed; its prior text and media remain."""


REVIEW_FAILURE_WARNINGS = frozenset((
    "AI kiểm tra lại chưa hoàn tất. Các câu đã lưu và âm thanh sẵn có được giữ; có thể thử lại.",
    "AI kiểm tra lại chưa hoàn tất; giữ bản nháp và cho phép thử lại.",
    "AI kiểm tra lại chưa hoàn tất; mở dự án và bấm AI kiểm tra lại để tiếp tục.",
))

PROJECT_SAVE_FAILURE_WARNING = "Không lưu được dự án xuống đĩa; giữ cửa sổ mở và kiểm tra dung lượng/quyền ghi."

SOURCE_METADATA_FIELDS = ("speaker_id", "speaker_evidence", "speaker_diagnostics", "speaker_confirmation", "utterance_id", "utterance_evidence",
                          "source_asr_row_id", "source_asr_start", "source_asr_end",
                          "source_piece_index", "source_piece_count")


class SegmentItem:
    def __init__(self, seg_id: int, start: float, end: float, duration: float):
        self.id = seg_id
        self.start = start
        self.end = end
        self.duration = duration
        self.dub_start = None
        self.dub_end = None
        self.dub_tail_limit = None
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
        self.asr_text = ""
        self.verification: Optional[Dict[str, Any]] = None
        self.confirmed_silence = False
        self.subtitle_cues = []
        self.subtitle_timing_source = "pending"
        self.speech_start = None
        self.speech_end = None
        self.timing_issue = None
        # Durable marker for a confirmation-dependent semantic review.
        self.speaker_review_pending = False
        self.voice_id = None
        self.tts_voice_outdated = False
        for name in SOURCE_METADATA_FIELDS:
            setattr(self, name, None)
    def to_dict(self) -> Dict[str, Any]:
        from core.streaming.recovery import segment_state
        return {
            **{name: deepcopy(getattr(self, name, None)) for name in SOURCE_METADATA_FIELDS
               if getattr(self, name, None) is not None},
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "dub_start": self.dub_start,
            "dub_end": self.dub_end,
            "dub_tail_limit": self.dub_tail_limit,
            "status": self.status,
            "processing_state": segment_state(self),
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
            "asr_text": self.asr_text,
            "verification": self.verification,
            "preview_is_draft": bool((self.needs_review or missing_spoken_output_ids([self]))
                                     and self.status in ("READY", "PLAYED")),
            "confirmed_silence": self.confirmed_silence,
            "subtitle_cues": self.subtitle_cues,
            "subtitle_timing_source": self.subtitle_timing_source,
            "speech_start": self.speech_start,
            "speech_end": self.speech_end,
            "timing_issue": deepcopy(self.timing_issue),
            "speaker_review_pending": self.speaker_review_pending,
            "voice_id": self.voice_id,
            "tts_voice_outdated": self.tts_voice_outdated,
        }


def _naturalize_spoken_line(source_text: str, translated_text: str) -> str:
    """Give TTS a sentence boundary without changing the spoken wording."""
    text = str(translated_text or "").strip()
    if not text or text.endswith((".", "?", "!", "…", "。", "！", "？")):
        return text
    source = str(source_text or "").strip()
    if source.endswith(("?", "？")):
        return text + "?"
    if source.endswith(("!", "！")):
        return text + "!"
    return text + "."

class StreamingPipelineSession:
    """
    Real-Time Streaming Localization Session.
    Processes segments ahead of playback using an asynchronous priority queue.
    """
    VISUAL_REVIEW_GROUP_SIZE = 4
    PREVIEW_SECONDS = 24.0
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
        translation_mode: str = "full",
        _restoring: bool = False,
    ):
        self.task_id = task_id
        # Directly constructed test/utility sessions are not silently persisted.
        # The session manager opts real application projects in explicitly.
        self._persistence_enabled = False
        self.video_path = video_path
        self.source_video_url: Optional[str] = None
        self.initialized = False
        self._download_info: Optional[Dict[str, Any]] = None
        self.source_url = None
        self._source_downloader = None
        self._startup_failed = False
        self._prepared = False
        if translation_mode not in ("preview", "full"):
            raise ValueError("Chế độ dịch phải là preview hoặc full.")
        self.translation_mode = translation_mode
        self.preview_seconds = self.PREVIEW_SECONDS
        self._preview_ready = False
        self._chunked_source_started = False
        self._source_prepared_seconds = 0.0
        self._visual_result = None
        self._visual_published_ids = set()
        self._visual_completed_seconds = 0.0
        self._visual_incremental_started = False
        self._visual_prepass_complete = False
        self._visual_scanned_seconds = 0.0
        self._chunk_jobs = []
        self._legacy_visual_prefix = 0.0
        self._chunk_followups = None
        self._chunk_followup_task = None
        self._visual_reviewed_drafts = {}
        # Measured failures belong to this process only. Restored projects use
        # explicit Retry; never infer a speech budget from an error string.
        self._pacing_failures = {}
        self._run_started_at = None
        self._run_ready_baseline = 0
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
        self.caption_style: Dict[str, Any] = {}
        self.caption_style_revision = 0
        self.caption_output_outdated = False
        self.translation_sources: List[Dict[str, str]] = []
        self.review_summary: Dict[str, Any] = {}
        self.review_task: Optional[asyncio.Task] = None
        self._review_scope = None
        self._reviewing_segment_ids = []
        if self.visual_translation and not _restoring:
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
        if self.visual_translation and not _restoring:
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
        return bool(self.edit_tasks) or bool(self.review_task and not self.review_task.done())

    def persist(self):
        """Save an atomic project snapshot without running engines or providers."""
        from core.streaming.session_store import save_session
        path = save_session(self)
        self._persistence_enabled = True
        return path

    def confirm_speaker(self, **selection):
        from core.streaming.speaker_confirmation import confirm_speaker
        return confirm_speaker(self, **selection)

    def _segment_voice(self, segment):
        """Use a catalog-validated override without mutating the shared engine."""
        override = getattr(segment, "voice_id", None)
        if override is None:
            return self.voice
        if self.tts_engine_name != "edge-tts":
            raise ValueError("Giọng riêng theo người nói hiện hỗ trợ Microsoft Edge.")
        return resolve_voice(override, "edge-tts")[1]

    def _persist_if_enabled(self):
        if not self._persistence_enabled or getattr(self, "_persistence_capacity_failed", False):
            return
        from core.streaming.session_store import ProjectCapacityError
        try:
            self.persist()
        except ProjectCapacityError as exc:
            self._persistence_capacity_failed = True
            message = str(exc)
            if message not in self.warnings:
                self.warnings.append(message)
            logging.getLogger("errors").error("[%s] PROJECT_CAPACITY_EXCEEDED", self.task_id)
            raise
        except (OSError, ValueError, TypeError) as exc:
            message = PROJECT_SAVE_FAILURE_WARNING
            if message not in self.warnings:
                self.warnings.append(message)
            logging.getLogger("errors").error("[%s] PROJECT_SAVE_FAILED error_type=%s detail=%s",
                self.task_id, type(exc).__name__, redacted_detail(exc))
            # A bounded session must never keep advancing after its latest
            # durable checkpoint can no longer be written.  Otherwise a
            # multi-hour run can appear to continue successfully in memory,
            # then lose all work after a restart.  Reuse the existing terminal
            # persistence guard so every provider/chunk callback re-raises and
            # terminal event emission cannot recurse into a failing save.
            if ((self._chunked_source_started or self.translation_mode == "preview")
                    and self.is_running and not self.is_stopped):
                self._persistence_failure_blocked = True
                self._persistence_capacity_failed = True
                self.is_running = False
                self.error = message
                raise RuntimeError(message) from None

    def _dialogue_context_before(self, segment, *, focus_source=None, focus_vi=None,
                                 _review_context=None):
        """Return bounded source-first context for the whole known exchange.

        A fixed preceding window can discard the only evidence that
        distinguishes chị–em from tôi–mày. Keep every currently known source
        turn (including the current and later turns) and let the shared
        selector prioritize the focused turn plus address cues. ``focus_source``
        is used while a review edit is still in memory so the provider never
        receives the stale pre-review source for the focused segment.
        """
        from core.translation_context import dialogue_context
        rows = []
        for item in sorted(self.segments.values(), key=lambda value: (value.start, value.id)):
            reviewed = (_review_context or {}).get(item.id, {})
            if not isinstance(reviewed, dict) or reviewed.get("id") != item.id:
                reviewed = {}
            # Review can correct a later source before this earlier sentence
            # is fitted. Use that immutable review snapshot for context without
            # publishing later text/audio or accepting different timeline IDs.
            text_source = reviewed.get("text_zh", getattr(item, "text_zh", ""))
            asr_source = reviewed.get("asr_text", getattr(item, "asr_text", ""))
            source = str(text_source or asr_source or "").strip()
            if item.id == segment.id and isinstance(focus_source, str) and focus_source.strip():
                source = focus_source.strip()
            if not source:
                continue
            row = {"id": item.id, "start": item.start, "end": item.end,
                   "text_zh": source, "asr_text": asr_source}
            # Timing/pacing must receive the same measured provenance as the
            # original translation. Read owned source metadata, never role
            # labels invented in a provider's translated review response.
            for name in SOURCE_METADATA_FIELDS:
                evidence = getattr(item, name, None)
                if evidence is not None:
                    row[name] = deepcopy(evidence)
            # Keep the focused review's independent address verdict in the
            # private context used by pacing.  It is evidence for the model,
            # never a replacement for the source-only reading.
            if isinstance(reviewed.get("verification"), dict):
                row["verification"] = reviewed["verification"]
            elif isinstance(getattr(item, "verification", None), dict):
                row["verification"] = dict(item.verification)
            if item.id == segment.id:
                row["is_focus"] = True
            draft = (focus_vi if item.id == segment.id and isinstance(focus_vi, str)
                     else reviewed.get("final_vi", getattr(item, "final_vi", "")))
            if isinstance(draft, str) and draft:
                row["final_vi"] = draft
            if reviewed.get("needs_review", getattr(item, "needs_review", False)):
                row["needs_review"] = True
            if reviewed.get("source_needs_review") or (not text_source and asr_source):
                row["source_needs_review"] = True
            if reviewed.get("source_truncated"):
                row["source_truncated"] = True
            rows.append(row)
        focus_rows = [{"id": segment.id, "start": segment.start, "end": segment.end,
                       "text_zh": focus_source or getattr(segment, "text_zh", "") or getattr(segment, "asr_text", "") or "",
                       "is_focus": True}]
        selected = dialogue_context(rows, focus_rows, max_rows=64)
        # Keep the long-lived pipeline contract (`zh`/`vi`) alongside the
        # stable IDs/timestamps now used by the selector. Draft translations
        # remain explicitly labelled by dialogue_context for the provider.
        result = []
        for row in selected:
            value = dict(row)
            value["zh"] = value.get("text_zh", "")
            value["vi"] = value.get("final_vi", "")
            result.append(value)
        return result

    def _ensure_tts_engine(self):
        if getattr(self, "tts_engine", None) is not None and not getattr(self, "_tts_init_failed", False):
            return
        self.tts_engine = self.edge_tts
        if self.tts_engine_name in ("vieneu", "vieneu-tts"):
            self.vieneu = VieNeuEngine()
            if not self.vieneu.is_available:
                self._tts_init_failed = True
                raise ValueError("Giọng VieNeu đã chọn chưa sẵn sàng. Hãy cài model và runtime VieNeu.")
            self.tts_engine = self.vieneu
        elif self.tts_engine_name == "piper-tts":
            from core.engines.tts.piper_engine import PiperEngine
            selected = PiperEngine()
            if not selected.is_available:
                self._tts_init_failed = True
                raise ValueError("Giọng Piper đã chọn chưa sẵn sàng. Hãy cài model và runtime Piper.")
            self.tts_engine = selected
        elif self.tts_engine_name != "edge-tts":
            self._tts_init_failed = True
            raise ValueError(f"TTS engine không được hỗ trợ: {self.tts_engine_name}")
        self._tts_init_failed = False

    @property
    def can_retry(self):
        if getattr(self, "_persistence_capacity_failed", False):
            return False
        if getattr(self, "_restored_source_missing", False):
            return False
        if (self._startup_failed and not self.is_running
                and not self.is_stopped and not self.is_editing and self.error):
            return bool((self.video_path and self.video_path.is_file())
                        or (self.source_url and self._source_downloader))
        failed = [segment for segment in self.segments.values() if segment.status == "FAILED"]
        restored_stages = {"TTS", "ALIGNING", "ASR", "TRANSLATING", "WAITING"} if getattr(self, "_restored_can_resume", False) else {"TTS", "ALIGNING"}
        voice_retry = any(getattr(row, "tts_voice_outdated", False) for row in failed)
        return bool(self.initialized and (not self.is_stopped or voice_retry) and not self.is_running
                    and not self.is_editing and self.error and failed
                    and all(segment.failed_stage in restored_stages for segment in failed)
                    and all(segment.status in ("READY", "PLAYED", "FAILED", "WAITING")
                            for segment in self.segments.values()))

    async def retry_failed_synthesis(self):
        """Continue the failed phase, retaining completed downloads and processing."""
        if (not self.can_retry or (self.worker_task and not self.worker_task.done())
                or (self.start_task and not self.start_task.done())):
            raise SegmentEditConflict("Chỉ tiếp tục khi tác vụ đã dừng do lỗi và không có thao tác khác đang chạy.")
        self._run_started_at = time.monotonic()
        self._run_ready_baseline = sum(row.status in ("READY", "PLAYED") for row in self.segments.values())
        if self._startup_failed:
            # Resume may advance source analysis from its durable cursor while
            # earlier speech failed. Explicit Retry must also schedule those
            # known translations; otherwise the cursor skips them forever.
            for segment in self.segments.values():
                if (segment.status == "FAILED" and segment.failed_stage in {"TTS", "ALIGNING"}
                        and (segment.final_vi.strip() or segment.confirmed_silence)):
                    segment.status = "WAITING"
                    segment.error = None
                    segment._retry_synthesis = True
            self._restored_interrupted = False
            self.is_stopped = False
            self.is_running = True
            self.error = None
            self._startup_failed = False
            self.progress = {"phase": "prepare", "stage": "Đang tiếp tục từ phần đã lưu…",
                             "progress_pct": None, "status": "RUNNING"}
            async def restart():
                try:
                    await self._start_guarded(self._source_downloader, self.source_url)
                except Exception:
                    # _start_guarded publishes the actionable error and retains state.
                    pass
            self.start_task = asyncio.create_task(restart())
            return self.get_progress()
        # There is no await before reservation and worker creation, so concurrent
        # API requests cannot start two workers for the same saved session.
        if getattr(self, "_restored_project", False):
            try:
                self._ensure_tts_engine()
            except ValueError as exc:
                raise SegmentEditConflict(str(exc)) from None
            self.asr_engine = self.faster_whisper
            if self.asr_engine_name == "sensevoice" and self.sensevoice.is_available:
                self.asr_engine = self.sensevoice
        self._restored_interrupted = False
        self.is_stopped = False
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
                segment._retry_synthesis = segment.failed_stage in ("TTS", "ALIGNING")
                retried.append(segment)
            if (segment.status == "WAITING" and (not self._chunked_source_started
                    or self._published_row(segment))):
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
                await self._resume_pending_chunk_reviews(synthesize_pending=True)
                # Review may replace a playable draft and defer failed speech.
                # Rebuild the reserved queue before its sole worker starts so
                # newly missing audio is retried with its accepted audit.
                while not self.queue.empty():
                    self.queue.get_nowait()
                    self.queue.task_done()
                for segment in self.segments.values():
                    if (segment.status == "WAITING" and (not self._chunked_source_started
                            or self._published_row(segment))):
                        self.queue.put_nowait((segment.start, segment.id))
            except asyncio.CancelledError:
                self.is_stopped = True
                self.is_running = False
                self._release_runtime()
                raise
            except Exception as exc:
                if getattr(self, "_persistence_capacity_failed", False):
                    self.error = str(exc)
                    self.is_running = False
                    self._release_runtime()
                    await self.emit("progress", self.get_progress())
                    await self.emit("error", {"message": self.error})
                    await self.emit("finished", self.get_telemetry())
                    return
                logging.getLogger("errors").warning("[%s] Không gửi được trạng thái thử lại tạo giọng", self.task_id)
            await self._worker_loop()

        self.worker_task = asyncio.create_task(resumed_worker())
        self.worker_task.add_done_callback(lambda task: self._release_runtime() if task.cancelled() else None)
        return self.get_progress()

    @property
    def can_translate_full(self):
        return bool(self.translation_mode == "preview" and self._preview_ready
                    and self.initialized and not self.is_running and not self.is_stopped
                    and not self.is_editing and self.video_path
                    and self.video_path.is_file())

    async def translate_full(self):
        """Promote a measured preview without replacing its source or edited rows."""
        if (not self.can_translate_full or (self.start_task and not self.start_task.done())
                or (self.worker_task and not self.worker_task.done())):
            raise SegmentEditConflict("Hãy chờ bản xem trước sẵn sàng trước khi dịch toàn bộ.")
        if self._chunked_source_started:
            from core.streaming.chunked_source import ensure_source_identity
            ensure_source_identity(self)
        # Reserve before yielding. Two clicks cannot launch two providers/workers.
        self.translation_mode = "full"
        self._preview_ready = False
        self.is_running = True
        self.is_paused = False
        self.pause_event.set()
        self.error = None
        self._visual_result = None
        self.progress = {"phase": "prepare", "stage": "Đang dịch phần còn lại, giữ các câu đã sửa…",
                         "progress_pct": None, "status": "RUNNING"}
        self._persist_if_enabled()
        async def continue_source():
            try:
                await self._start_guarded()
            except Exception:
                # The guarded owner records an actionable failure and checkpoint.
                pass
        self.start_task = asyncio.create_task(continue_source())
        return self.get_progress()

    async def edit_segment(self, segment_id: int, text: str, *, confirm_silence: bool = False,
                           _review_result: Optional[Dict[str, Any]] = None,
                           _review_context: Optional[Dict[int, Dict[str, Any]]] = None):
        """Publish text and fitted audio together only after synthesis succeeds."""
        review_empty = (isinstance(_review_result, dict) and isinstance(text, str) and not text.strip()
                        and _review_result.get("final_vi") == "" and _review_result.get("needs_review") is True)
        without_audio = confirm_silence or review_empty
        if (not isinstance(confirm_silence, bool) or not isinstance(text, str)
                or len(text) > 2000 or "\x00" in text
                or (confirm_silence and text.strip()) or (not without_audio and not text.strip())):
            raise ValueError("Nội dung tiếng Việt phải có từ 1 đến 2.000 ký tự và không chứa ký tự NUL.")
        seg = self.segments.get(segment_id)
        if seg is None:
            raise KeyError(segment_id)
        if self.is_stopped or seg.status not in ("READY", "PLAYED", "NEEDS_REVIEW"):
            raise SegmentEditConflict("Hãy chờ câu này dịch và tạo giọng xong trước khi sửa.")
        if self.is_editing and not (_review_result is not None and self.review_task is asyncio.current_task()
                                    and not self.edit_tasks):
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
        if text == seg.final_vi and not was_review and _review_result is None and not seg.tts_voice_outdated:
            return seg.to_dict()
        review_result = deepcopy(_review_result) if _review_result is not None else None
        old_source = seg.text_zh
        old_audio_path = seg.audio_path
        task = asyncio.current_task()
        self.edit_tasks.add(task)
        stem = f"edit_{seg.id}_{uuid.uuid4().hex}"
        raw_path = self.cache_dir / f"{stem}_raw.wav"
        fitted_path = self.segments_dir / f"{stem}.wav"
        dub_plan = {}
        try:
            if without_audio:
                tts_duration, ratio = 0.0, 1.0
                timing = {"subtitle_cues": [], "subtitle_timing_source": "silence" if confirm_silence else "unresolved",
                          "speech_start": None, "speech_end": None}
            else:
                self._ensure_tts_engine()
                speech_stage = None
                if review_result is not None:
                    loop = asyncio.get_running_loop()
                    async def publish_stage(stage):
                        if self.is_stopped:
                            raise asyncio.CancelledError
                        label = {"TTS": "Đang tạo lại giọng đọc",
                                 "ALIGNING": "Đang căn nhịp lời đã kiểm tra",
                                 "REWRITING": "AI đang rút gọn và kiểm tra nghĩa lời đọc"}[stage]
                        await self.report_progress("review", f"{label} câu {seg.id + 1}…", None,
                                                   review_stage=stage.lower(), segment_id=seg.id)
                    def speech_stage(stage):
                        asyncio.run_coroutine_threadsafe(publish_stage(stage), loop).result()
                    await publish_stage("TTS")
                async with self._tts_lock:
                    spoken, timing, dub_plan = await self._fit_dub(seg,
                        text=text, source=review_result.get("text_zh", old_source) if review_result is not None else old_source,
                        output_path=fitted_path,
                        translator=self.translator if review_result is not None else None,
                        on_stage=speech_stage,
                        context=(self._dialogue_context_before(
                            seg,
                            focus_source=review_result.get("text_zh", old_source),
                            focus_vi=text,
                            _review_context=_review_context,
                        ) if review_result is not None else None))
                spoken_text = spoken.get("text", text)
                if spoken_text != text:
                    proof = spoken.get("pacing_verification")
                    if (review_result is None or not isinstance(spoken_text, str) or not spoken_text.strip()
                            or not isinstance(proof, dict) or proof.get("status") != "verified"
                            or proof.get("text") != spoken_text):
                        raise RuntimeError("Thiếu xác minh cho lời đọc đã rút gọn.")
                    verification = {**(review_result.get("verification") or {}),
                        "pacing": deepcopy(proof), "before_pacing": text, "translation_changed": True}
                    if not review_result.get("needs_review") and verification.get("status") == "verified":
                        verification["status"] = "corrected"
                    review_result["verification"] = verification
                    text = spoken_text
                if review_result is not None:
                    review_result["final_vi"] = text
                tts_duration, ratio, boundaries = spoken["tts_duration"], spoken["speed_ratio"], spoken["boundaries"]
            if self.is_stopped:
                raise asyncio.CancelledError
            # A persistent edit must never overwrite the WAV referenced by the
            # previous manifest before its new text/reference is durable.
            final_path = self.segments_dir / (f"seg_{seg.id}_{uuid.uuid4().hex}.wav"
                if self._persistence_enabled else f"seg_{seg.id}.wav")
            # No await between atomic file replacement and metadata publication.
            if without_audio and not self._persistence_enabled:
                # Remove stale speech before publishing silence or an uncertain
                # empty review. A locked file leaves the previous state intact.
                final_path.unlink(missing_ok=True)
            elif not without_audio:
                fitted_path.replace(final_path)
            with self._durable_edit_publication(seg, dub_plan, final_path):
                self._publish_dub_plan(dub_plan, seg.id)
                old_text = seg.final_vi
                seg.final_vi = text
                seg.confirmed_silence = confirm_silence
                if confirm_silence:
                    seg.literal_vi = seg.natural_vi = ""
                seg.tts_duration = tts_duration
                seg.speed_ratio = round(ratio, 2)
                seg.audio_path = None if without_audio else str(final_path.resolve())
                seg.tts_voice_outdated = False
                for key, value in timing.items():
                    setattr(seg, key, value)
                seg.revision += 1
                seg.audio_url = None if without_audio else f"/api/streaming/audio/{self.task_id}/{seg.id}?rev={seg.revision}"
                if was_review:
                    seg.needs_review = False
                    seg.review_reason = None
                    seg.error = None
                    seg.status = "READY"
                    if not was_playable:
                        self.total_processed_duration += seg.duration
                    if was_review_error:
                        self.error = None
                if review_result is not None:
                    self._apply_review_metadata(seg, review_result)
                else:
                    seg.verification = {"status": "manual", "reason": "Người dùng đã lưu lời thoại."}
                if self.review_summary.get("status") == "completed":
                    self._refresh_review_counts()
                # The file belongs to the old text/audio revision. Keep it on disk,
                # but never expose it as this session's current final result.
                self._invalidate_output()
                if self.visual_translation and not without_audio:
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
                    if context.get("zh") == old_source and context.get("vi") == old_text:
                        context["zh"] = seg.text_zh
                        context["vi"] = text
            # Neighbor rescue publishes versioned WAVs.  A later manual edit
            # must retire the superseded version after the new metadata is
            # durable, otherwise repeated edits accumulate orphan audio.
            if old_audio_path and Path(old_audio_path).resolve().parent == self.segments_dir.resolve():
                current_audio = Path(seg.audio_path).resolve() if seg.audio_path else None
                old_audio = Path(old_audio_path).resolve()
                if current_audio != old_audio:
                    try:
                        old_audio.unlink(missing_ok=True)
                    except OSError:
                        pass
            snapshot = seg.to_dict()
            try:
                await self.emit("result_invalidated", {"reason": "transcript_changed",
                    "output_video_url": "", "output_filename": "", "review_summary": dict(self.review_summary)})
                for identity in dub_plan:
                    if identity != seg.id:
                        await self.emit("segment_update", self.segments[identity].to_dict())
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

    @contextmanager
    def _durable_edit_publication(self, segment, plan, staged_audio):
        """Commit edit metadata before any event or retirement of old media."""
        owners = {sid: self.segments[sid] for sid in {*plan, segment.id}}
        old_rows = {sid: deepcopy(vars(row)) for sid, row in owners.items()}
        fields = ("screen_texts", "rolling_context", "review_summary", "error", "total_processed_duration",
                  "output_video_url", "output_filename", "output_review_url", "auto_export_signature")
        old_fields = {name: deepcopy(getattr(self, name)) for name in fields if hasattr(self, name)}
        try:
            yield
            if self._persistence_enabled:
                # The ordinary progress-save helper deliberately tolerates an
                # idle save failure. User edits must report it to the caller.
                self.persist()
        except BaseException as error:
            for sid, values in old_rows.items():
                owners[sid].__dict__.clear()
                owners[sid].__dict__.update(values)
            for name, value in old_fields.items():
                setattr(self, name, value)
            for name in set(fields) - old_fields.keys():
                self.__dict__.pop(name, None)
            if self._persistence_enabled:
                staged_audio.unlink(missing_ok=True)
            for bounds in plan.values():
                path = bounds.get("_audio_update", {}).get("audio_path")
                if path:
                    Path(path).unlink(missing_ok=True)
            if isinstance(error, (OSError, ValueError, TypeError)):
                logging.getLogger("errors").error(
                    "PROJECT_EDIT_SAVE_FAILED run_id=%s segment_id=%s error_type=%s",
                    self.task_id, segment.id, type(error).__name__)
                raise ProjectEditSaveError(
                    "Chưa lưu được lời thoại xuống ổ đĩa. Nội dung và giọng cũ được giữ; "
                    "kiểm tra dung lượng/quyền ghi rồi thử lưu lại.") from None
            raise

    @staticmethod
    def _apply_review_metadata(segment, row):
        for field in ("text_zh", "literal_vi", "natural_vi", "final_vi", "needs_review", "review_reason", "verification"):
            if field in row:
                setattr(segment, field, row[field])
        audit = row.get("verification") or {}
        if isinstance(audit, dict) and audit.get("status") in {"verified", "corrected", "unresolved"}:
            segment.speaker_review_pending = False

    @contextmanager
    def _durable_speaker_review_publication(self, segment):
        """Clear user-confirmation review ownership only with a durable verdict."""
        pending = segment.speaker_review_pending
        before = deepcopy(vars(segment)) if pending else None
        try:
            yield
            if pending and not segment.speaker_review_pending:
                self.persist()
        except BaseException as error:
            if before is not None:
                segment.__dict__.clear()
                segment.__dict__.update(before)
            if isinstance(error, (OSError, ValueError, TypeError)):
                raise ProjectEditSaveError("Chưa lưu được kết quả rà người nói xuống ổ đĩa; "
                    "giữ bằng chứng và trạng thái chờ để thử lại.") from None
            raise

    def _refresh_review_counts(self):
        statuses = [(segment.verification or {}).get("status") for segment in self.segments.values()]
        self.review_summary = {**self.review_summary,
            "checked": sum(status in ("verified", "corrected", "unresolved", "incomplete") for status in statuses),
            **{status: statuses.count(status) for status in ("verified", "corrected", "unresolved", "manual")}}
        incomplete = statuses.count("incomplete") + statuses.count("pending")
        if "incomplete" in self.review_summary or incomplete:
            self.review_summary["incomplete"] = incomplete
        if incomplete:
            self.review_summary["status"] = "incomplete"

    def _invalidate_output(self):
        self.output_video_url = ""
        self.output_filename = ""
        self.output_review_url = ""
        self.auto_export_signature = None

    async def _invalidate_changed_address_verifications(self):
        from core.translation_review import AutomaticTranslationReviewer
        snapshots = {sid: deepcopy(segment.to_dict()) for sid, segment in self.segments.items()}
        AutomaticTranslationReviewer._invalidate_changed_address_sources(snapshots)
        for sid, snapshot in snapshots.items():
            segment = self.segments[sid]
            if snapshot.get("verification") != segment.verification:
                segment.verification = snapshot.get("verification")
                segment.needs_review = bool(snapshot.get("needs_review"))
                segment.review_reason = snapshot.get("review_reason")
                await self.emit("segment_update", segment.to_dict())
        await self._invalidate_reviewed_screen_texts()

    async def _invalidate_reviewed_screen_texts(self):
        from core.subtitle_cues import invalidate_screen_texts_for_segments
        updated = invalidate_screen_texts_for_segments(
            self.screen_texts, [segment.to_dict() for segment in self.segments.values()])
        if updated != self.screen_texts:
            self.screen_texts = updated
            await self.emit("screen_update", {"screen_texts": self.screen_texts})

    async def _review_translations(self, *, regenerate_audio=False, force_review=False, segment_ids=None,
                                   defer_audio=False):
        from core.translation_review import AutomaticTranslationReviewer
        self._review_scope = "chunk" if segment_ids is not None else "all"
        self._reviewing_segment_ids = sorted(segment_ids) if segment_ids is not None else sorted(self.segments)
        await self.report_progress("review", "AI đang kiểm tra lại từng câu với nguồn…", None)
        loop = asyncio.get_running_loop()
        semantic_active = True
        async def publish_review_progress(percent):
            if semantic_active and not self.is_stopped:
                # Reviewer percentages weight OCR/request stages, rather than
                # measured provider completion. Keep this stage indeterminate;
                # the snapshot still reports measured source coverage separately.
                await self.report_progress("review", "AI đang đối chiếu nguồn và sửa bản dịch…",
                                           None,
                                           review_stage="semantic")
        def progress(percent):
            if semantic_active and not self.is_stopped:
                loop.call_soon_threadsafe(lambda: asyncio.create_task(publish_review_progress(percent)))
        targets = [segment for segment in self.segments.values()
                   if segment_ids is None or segment.id in segment_ids]
        target_revisions = {segment.id: segment.revision for segment in targets}
        options = {"cancel_check": lambda: self.is_stopped, "progress_callback": progress,
                   "force_review": force_review}
        if segment_ids is not None:
            options["context_segments"] = list(self.segments.values())
        try:
            result = await self._run_blocking(
                AutomaticTranslationReviewer().review, self.video_path, deepcopy(targets), deepcopy(self.screen_texts), **options)
        finally:
            semantic_active = False
            self._review_scope = None
            self._reviewing_segment_ids = []
        # Audio edits publish one row at a time. Every pacing request must see
        # the same complete reviewed exchange, including future source fixes,
        # while each row's visible text/audio still commits transactionally.
        review_context = deepcopy(result["segments"])
        for sid, row in sorted(result["segments"].items(), key=lambda item: (self.segments[item[0]].start, item[0])):
            segment = self.segments[sid]
            if (sid not in target_revisions or segment.revision != target_revisions[sid]
                    or (segment.verification or {}).get("status") == "manual"):
                # A newer user commit wins over an older review response.
                continue
            if (regenerate_audio and segment.status in ("READY", "PLAYED", "NEEDS_REVIEW")
                    and (row["final_vi"] != segment.final_vi or segment.tts_voice_outdated)):
                if defer_audio:
                    # Recovery publishes the complete reviewed group before
                    # fitting it. Inline edit synthesis may ask Muse to pace
                    # an early corrected READY row for minutes and strand all
                    # later review groups. An old WAV is retained on disk but
                    # must never remain served for the changed wording.
                    reason = "wording_changed" if row["final_vi"] != segment.final_vi else "voice_changed"
                    with self._durable_speaker_review_publication(segment):
                        self._apply_review_metadata(segment, row)
                        segment.revision += 1
                        segment.status, segment.failed_stage = "WAITING", "TTS"
                        segment.error = None
                        segment.audio_path = segment.audio_url = None
                        segment.tts_duration, segment.speed_ratio = 0., 1.
                        segment.subtitle_cues = []
                        segment.speech_start = segment.speech_end = None
                        segment.subtitle_timing_source = "pending"
                        segment._retry_synthesis = True
                        segment.timing_issue = None
                    self._pacing_failures.pop(sid, None)
                    self._invalidate_output()
                    logging.getLogger("pipeline").info(
                        "REVIEW_AUDIO_QUEUED run_id=%s segment_id=%s reason=%s", self.task_id, sid, reason)
                    await self.emit("segment_update", segment.to_dict())
                    continue
                try:
                    await self.edit_segment(sid, row["final_vi"], _review_result=row,
                                            _review_context=review_context)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if not self._chunked_source_started or getattr(self, "_persistence_capacity_failed", False):
                        raise
                    if segment.revision != target_revisions[sid]:
                        continue
                    # Semantic review succeeded. Retain that evidence even if
                    # rebuilding speech fails; an old WAV must not be served as
                    # audio for the corrected text. The old file stays on disk
                    # until the resumed worker atomically replaces it.
                    with self._durable_speaker_review_publication(segment):
                        self._apply_review_metadata(segment, row)
                        segment.revision += 1
                        segment.status, segment.failed_stage = "FAILED", "TTS"
                        segment.error = str(error)
                        segment.audio_path = segment.audio_url = None
                        segment.tts_duration, segment.speed_ratio = 0., 1.
                        segment.subtitle_cues = []
                        segment.speech_start = segment.speech_end = None
                        segment.subtitle_timing_source = "pending"
                        segment._retry_synthesis = True
                        segment.timing_issue = None
                    if isinstance(error, (SpeechBudgetError, PacingReviewRejected)):
                        # Record the corrected text/source revision, rather than
                        # the pre-review draft which edit_segment kept atomic.
                        # The frozen reviewed exchange includes later source
                        # corrections that are not yet published in this loop.
                        self._remember_pacing_failure(segment, error,
                            self._dialogue_context_before(segment,
                                _review_context=review_context))
                    self._invalidate_output()
                    logging.getLogger("errors").error(
                        "[%s] REVIEW_AUDIO_DEFERRED segment_id=%s error_type=%s",
                        self.task_id, sid, type(error).__name__)
                    await self.emit("segment_update", segment.to_dict())
                    await self._update_ready()
            else:
                with self._durable_speaker_review_publication(segment):
                    self._apply_review_metadata(segment, row)
                if regenerate_audio:
                    await self.emit("segment_update", segment.to_dict())
        for source in result.get("translation_sources", []):
            if source not in self.translation_sources:
                self.translation_sources.append(source)
        self.review_summary = {"status": "completed", **result["summary"]}
        if segment_ids is not None:
            await self._invalidate_changed_address_verifications()
        # Pacing may change a verified translation while rebuilding its audio.
        # Report the committed text/audio state, not the earlier review draft.
        self._refresh_review_counts()
        await self._invalidate_reviewed_screen_texts()
        if self.review_summary.get("status") == "completed":
            self.warnings[:] = [warning for warning in self.warnings if warning not in REVIEW_FAILURE_WARNINGS]
        await self.emit("review_complete", {"review_summary": self.review_summary,
            "warnings": list(self.warnings), "screen_texts": self.screen_texts})

    async def _resume_speech_rows(self, rows, *, defer_pacing=False):
        """Publish simple fits before admitting slower semantic pacing requests.

        The first pass measures real TTS and performs only acoustic/timeline
        fitting. Overlong rows retain their checkpoint for a second pass; a
        slow rewrite cannot delay every independent missing WAV behind it.
        Both passes share one owner and the existing TTS lock/cache. Recovery
        can collect the second pass across review groups, so a difficult early
        sentence cannot delay all later groups' independent speech.
        """
        deferred = []
        ordered = sorted(rows, key=lambda item: (item.start, item.id))
        for row in ordered:
            if row.status != "WAITING" or not self._published_row(row):
                continue
            await self.pause_event.wait()
            if self.is_stopped or not self.is_running:
                raise asyncio.CancelledError
            revision = row.revision
            try:
                await self._synthesize_segment(row, allow_pacing=False)
            except asyncio.CancelledError:
                raise
            except SpeechBudgetError:
                if row.revision != revision:
                    continue
                # The complete measured candidate remains in the stage cache.
                # Do not publish an overlong WAV or repeat synthesis remotely.
                row.status, row.error, row.failed_stage = "WAITING", None, "ALIGNING"
                row._retry_synthesis = True
                deferred.append((row, revision))
                await self.emit("segment_update", row.to_dict())
                self._persist_if_enabled()
            except Exception as error:
                if getattr(self, "_persistence_capacity_failed", False):
                    raise
                if row.revision != revision:
                    continue
                row.failed_stage = row.status if row.status in {"TTS", "ALIGNING"} else "TTS"
                row.status, row.error = "FAILED", str(error)
                warning = f"Câu {row.id + 1} chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
                if warning not in self.warnings:
                    self.warnings.append(warning)
                logging.getLogger("errors").error(
                    "[%s] RECOVERY_SPEECH_FAILED segment_id=%s stage=%s error_type=%s",
                    self.task_id, row.id, row.failed_stage, type(error).__name__)
                await self.emit("segment_update", row.to_dict())
                self._persist_if_enabled()
        if not defer_pacing:
            await self._resume_deferred_speech_rows(deferred)
        return deferred

    async def _resume_deferred_speech_rows(self, deferred):
        """Run bounded pacing only after independent recovery has published."""
        seen = set()
        for row, revision in deferred:
            if (row.id, revision) in seen:
                continue
            seen.add((row.id, revision))
            if row.revision != revision or row.status != "WAITING" or not self._published_row(row):
                continue
            await self.pause_event.wait()
            if self.is_stopped or not self.is_running:
                raise asyncio.CancelledError
            try:
                await self._synthesize_segment(row)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if getattr(self, "_persistence_capacity_failed", False):
                    raise
                if row.revision != revision:
                    continue
                row.failed_stage = row.status if row.status in {"TTS", "ALIGNING"} else "TTS"
                row.status, row.error = "FAILED", str(error)
                warning = f"Câu {row.id + 1} chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
                if warning not in self.warnings:
                    self.warnings.append(warning)
                logging.getLogger("errors").error(
                    "[%s] RECOVERY_PACING_FAILED segment_id=%s stage=%s error_type=%s",
                    self.task_id, row.id, row.failed_stage, type(error).__name__)
                await self.emit("segment_update", row.to_dict())
                self._persist_if_enabled()

    async def _resume_pending_chunk_reviews(self, *, stale_address_only=False, synthesize_pending=False):
        """Finish interrupted review ownership before retrying its saved speech.

        Source coverage is committed when a validated draft arrives. A Stop in
        the later independent review therefore leaves translated rows behind
        that cursor; resuming source preparation alone cannot revisit them.
        Manual edits and verified WAVs remain outside this selection. A draft
        synthesized by an older retry implementation still needs real review.
        Address readings invalidated by a later source correction need a fresh
        bounded review, even when their existing speech is already READY. The
        end-of-source pass can request only those rows so it cannot turn an
        unrelated unresolved draft into an unbounded review loop.
        """
        pending_speaker_review = any(row.speaker_review_pending
            and (row.verification or {}).get("status") != "manual" for row in self.segments.values())
        if (settings.LLM_PROVIDER != "opencode" or
                (not (self._chunked_source_started and self.visual_translation) and not pending_speaker_review)):
            return
        deferred_speech = []
        if synthesize_pending:
            from core.streaming.speaker_source import recover_speaker_evidence
            await recover_speaker_evidence(self)
        from core.translation_review import AutomaticTranslationReviewer
        def needs_source_scope_recheck(row):
            # A saved OCR correction may have imported the next utterance's
            # words. Revalidate failed speech against measured neighbouring
            # ASR before retrying the same overlong text indefinitely.
            audit = row.verification or {}
            retried_speech = row.status == "FAILED" or (
                row.status == "WAITING" and row._retry_synthesis)
            return (retried_speech and row.failed_stage in {"TTS", "ALIGNING"}
                and audit.get("status") != "manual"
                and bool(row.asr_text) and row.text_zh != row.asr_text
                and audit.get("source_supported") is True
                and any(isinstance(proof, dict) and proof.get("text_zh")
                        for proof in audit.get("evidence", [])))
        def needs_address_source_recheck(row):
            audit = row.verification or {}
            return (audit.get("status") == "unresolved"
                and isinstance(audit.get("address_stale_source_ids"), list)
                and bool(audit["address_stale_source_ids"]))
        def needs_obsolete_gate_recheck(row):
            audit = row.verification or {}
            # Gate/schema fixes cannot repair a historical negative verdict
            # by flipping a boolean. Re-audit each obsolete unresolved verdict
            # once, using current evidence; a current negative stays negative.
            return (audit.get("status") == "unresolved"
                and audit.get("review_gate_revision") != AutomaticTranslationReviewer.REVIEW_GATE_REVISION
                and any(key in audit for key in ("address_context", "address_applicable", "audio_evidence")))
        def needs_numeric_audio_recheck(row):
            # New exact spelling equivalence can resolve an old ASR comparison
            # false negative. It is only a reason to perform genuine semantic
            # review again, never permission to flip its saved verdict.
            from core.chinese_text import comparable_audio_chinese
            audit = row.verification or {}
            readings = audit.get("audio_evidence", [])
            if (audit.get("status") != "unresolved" or audit.get("audio_consensus") is not False
                    or not isinstance(readings, list)):
                return False
            valid = [item for item in readings if isinstance(item, dict)
                     and item.get("engine") in {"sensevoice", "faster-whisper-small"}
                     and isinstance(item.get("text_zh"), str) and item["text_zh"].strip()]
            keys = {comparable_audio_chinese(item["text_zh"]) for item in valid}
            return len({item["engine"] for item in valid}) == 2 and len(keys) == 1 and "" not in keys
        def include(row):
            if stale_address_only:
                return needs_address_source_recheck(row)
            return ((row.verification or {}).get("status") in (None, "pending", "incomplete")
                    or needs_source_scope_recheck(row) or needs_address_source_recheck(row)
                    or needs_obsolete_gate_recheck(row) or needs_numeric_audio_recheck(row))
        pending = sorted((row for row in self.segments.values()
            if (row.speaker_review_pending or
                 (self._chunked_source_started and self.visual_translation and self._published_row(row)
                  and row.source_method == "text-ai" and row.translation_provider == "opencode"
                  and include(row)))),
            key=lambda row: (row.start, row.id))
        if synthesize_pending:
            # These rows already own accepted translation/review state. A
            # missing WAV must not wait behind unrelated slow Muse requests.
            # No second speech worker runs until this recovery owner finishes.
            reserved = {row.id for row in pending}
            deferred_speech.extend(await self._resume_speech_rows(
                (row for row in self.segments.values() if row.id not in reserved), defer_pacing=True))
        for offset in range(0, len(pending), self.VISUAL_REVIEW_GROUP_SIZE):
            await self.pause_event.wait()
            if self.is_stopped:
                raise asyncio.CancelledError
            # A user may commit an edit while the previous group is reviewed.
            # Do not include that now-manual row in the next automatic request.
            group = [row for row in pending[offset:offset + self.VISUAL_REVIEW_GROUP_SIZE]
                     if (row.verification or {}).get("status") != "manual"]
            if not group:
                continue
            revisions = {row.id: row.revision for row in group}
            try:
                options = {"regenerate_audio": True, "segment_ids": {row.id for row in group}}
                if synthesize_pending:
                    options["defer_audio"] = True
                if any(needs_address_source_recheck(row) or row.speaker_review_pending for row in group):
                    # Reuse source/audio files, but never reuse the address
                    # verdict made before these accepted source words changed.
                    options["force_review"] = True
                await self._review_translations(**options)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if getattr(self, "_persistence_capacity_failed", False):
                    raise
                self.review_summary = {"status": "incomplete"}
                warning = "AI kiểm tra lại chưa hoàn tất; giữ bản nháp và cho phép thử lại."
                if warning not in self.warnings:
                    self.warnings.append(warning)
                logging.getLogger("errors").error(
                    "[%s] RESUMED_REVIEW_FAILED error_type=%s", self.task_id, type(error).__name__)
                for row in group:
                    if row.revision != revisions[row.id]:
                        continue
                    row.needs_review = True
                    row.review_reason = "AI kiểm tra nguồn chưa hoàn tất; đây là bản nháp cần kiểm tra."
                    row.verification = {"status": "incomplete", "semantic_verified": False,
                                        "reason": row.review_reason}
            for row in group:
                # Restore can identify an interrupted reviewed draft as missing
                # TTS even when its defensible translation is empty. Keep that
                # uncertainty; it must not remain permanently stranded FAILED.
                if row.status == "FAILED" and row.failed_stage in {"TTS", "ALIGNING"}:
                    row.status, row.error = "WAITING", None
                    row._retry_synthesis = True
                await self.emit("segment_update", row.to_dict())
            self._refresh_review_counts()
            self._persist_if_enabled()
            if synthesize_pending:
                # Persist the reviewed text first, then publish this group's
                # valid WAVs before admitting the next semantic request. A
                # failed row stays FAILED and is not retried twice by the later
                # queue in the same run. Its siblings continue independently.
                deferred_speech.extend(await self._resume_speech_rows(group, defer_pacing=True))
        if synthesize_pending:
            await self._resume_deferred_speech_rows(deferred_speech)

    async def start_automatic_review(self):
        if (not self.initialized or self.is_running or self.is_stopped or self.is_editing or self.error
                or any(s.status not in ("READY", "PLAYED") for s in self.segments.values())):
            raise SegmentEditConflict("Hãy chờ xử lý xong trước khi AI kiểm tra lại.")
        if settings.LLM_PROVIDER != "opencode":
            raise SegmentEditConflict("Kiểm tra lại miễn phí hiện dùng OpenCode trong Cài đặt.")
        self._run_started_at = time.monotonic()
        self._run_ready_baseline = sum(row.status in ("READY", "PLAYED") for row in self.segments.values())
        self.is_running = True
        self.review_summary = {"status": "running"}
        self._invalidate_output()
        self.progress = {"phase": "review", "stage": "AI đang kiểm tra lại từng câu…", "progress_pct": 0, "status": "RUNNING"}
        async def run_review():
            try:
                await self.emit("result_invalidated", {"reason": "review_started",
                    "output_video_url": "", "output_filename": "", "review_summary": dict(self.review_summary)})
                # An explicit user retry is a fresh audit; automatic resume
                # inside the initial pipeline may reuse exact verified batches.
                pending_ids = {row.id for row in self.segments.values()
                               if row.speaker_review_pending and (row.verification or {}).get("status") != "manual"}
                await self._review_translations(regenerate_audio=True, force_review=True,
                    segment_ids=pending_ids or None)
            except asyncio.CancelledError:
                self.is_stopped = True
                raise
            except Exception as error:
                self.review_summary = {"status": "failed"}
                logging.getLogger("errors").error(
                    "[%s] REVIEW_FAILED error_type=%s detail=%s",
                    self.task_id, type(error).__name__, redacted_detail(error))
                warning = "AI kiểm tra lại chưa hoàn tất. Các câu đã lưu và âm thanh sẵn có được giữ; có thể thử lại."
                if warning not in self.warnings:
                    self.warnings.append(warning)
            finally:
                self.is_running = False
                self.review_task = None
                if not self.is_stopped:
                    failed_audio = [segment.id for segment in self.segments.values()
                                    if segment.status == "FAILED"
                                    or (segment.final_vi.strip() and segment.status in ("READY", "PLAYED")
                                        and not segment.audio_path)]
                    if failed_audio:
                        self.error = (f"{len(failed_audio)} câu chưa tạo được giọng; bản dịch được giữ, "
                                      "hãy bấm Tiếp tục để thử lại.")
                        await self.report_progress("failed", self.error, None,
                                                   failed_segment_ids=failed_audio)
                    else:
                        await self.report_progress("complete", self._review_message(),
                                                   100 if self.review_summary.get("status") == "completed" else None)
                    await self.emit("finished", self.get_telemetry())
                self._release_runtime()
        self.review_task = asyncio.create_task(run_review())
        return self.get_progress()

    async def emit(self, event_type: str, data: Dict[str, Any]):
        if event_type in {"init", "segment_update", "screen_update", "finished", "error", "review_complete", "result_invalidated", "source_ready"}:
            self._persist_if_enabled()
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
        style = getattr(self, "caption_style", None)
        layout = build_caption_layout([data], self.screen_texts, video_size=size, caption_style=style)
        bottom = build_caption_layout([data], [], video_size=size, caption_style=style)
        if layout.get("source_masks"):
            bottom["source_masks"] = layout["source_masks"]
        return {**data, "caption_layout": layout, "caption_bottom_layout": bottom}

    def segment_snapshot(self, segment):
        return self.caption_metadata(segment.to_dict())

    def get_progress(self) -> Dict[str, Any]:
        snapshot = dict(self.progress)
        output_gate = final_output_metadata(self.segments.values())
        snapshot.update(output_gate)
        from core.streaming.recovery import segment_state
        counts = {state: 0 for state in ("PENDING", "RUNNING", "COMPLETED", "RETRY_PENDING", "REVIEW_REQUIRED", "FAILED")}
        for row in self.segments.values():
            counts[segment_state(row)] += 1
        snapshot.update(segment_states=counts, completed_chunks=sum(row["state"] == "COMPLETED" for row in self._chunk_jobs),
                        total_chunks=len(self._chunk_jobs), scanned_seconds=self._visual_scanned_seconds,
                        retry_chunks=sum(row["state"] == "RETRY_PENDING" for row in self._chunk_jobs))
        snapshot["elapsed_seconds"] = (max(0, time.monotonic() - self._run_started_at)
                                       if self._run_started_at is not None else None)
        snapshot["eta_seconds"] = None
        if self.is_running and not self.is_stopped and self._run_started_at is not None and self._visual_scanned_seconds >= self.total_duration > 0:
            elapsed = snapshot["elapsed_seconds"]
            ready = sum(row.status in {"READY", "PLAYED"} for row in self.segments.values())
            produced = ready - self._run_ready_baseline
            remaining = len(self.segments) - ready
            if elapsed > 0 and produced >= 3 and remaining > 0 and counts["FAILED"] == 0:
                snapshot["eta_seconds"] = elapsed * remaining / produced
        if settings.LLM_PROVIDER == "opencode":
            from core.engines.translation.opencode_client import _shared_execution_layer
            snapshot["ai_queue"] = _shared_execution_layer().snapshot()
        snapshot.update(self._review_metadata())
        snapshot["can_retry"] = self.can_retry
        snapshot.update(translation_mode=self.translation_mode, preview_seconds=self.preview_seconds,
                        source_prepared_seconds=self._source_prepared_seconds,
                        processed_seconds=self._visual_completed_seconds,
                        total_seconds=self.total_duration, can_translate_full=self.can_translate_full)
        snapshot["review_summary"] = self.review_summary
        snapshot["review_scope"] = self._review_scope
        snapshot["reviewing_segment_ids"] = list(self._reviewing_segment_ids)
        snapshot["can_review"] = bool(self.initialized and not self.is_running and not self.is_stopped
                                      and not self.is_editing and not self.error and settings.LLM_PROVIDER == "opencode"
                                      and all(s.status in ("READY", "PLAYED") for s in self.segments.values()))
        snapshot["can_pause"] = bool(self.initialized and self.is_running and not self.is_paused and not self.is_stopped and not self.error
                                    and not (self.review_task and not self.review_task.done()))
        if getattr(self, "_stop_draining", False):
            snapshot.update(status="CANCELLING", phase="stopping",
                stage="Đang dừng xử lý; chờ tác vụ đang chạy trả quyền điều khiển…", can_pause=False)
        elif self.is_stopped:
            snapshot.update(status="STOPPED", phase="stopped", stage="Đã dừng bởi người dùng")
        elif self.error:
            if getattr(self, "_restored_interrupted", False):
                snapshot.update(status="STOPPED", phase="restored", stage=self.error)
            else:
                snapshot.update(status="FAILED", phase="failed", stage=self.error)
        elif self.is_paused and self.is_running:
            snapshot["status"] = "PAUSED"
        elif self._preview_ready and not self.is_running:
            snapshot.update(status="PREVIEW_READY", phase="preview", stage="Bản xem trước đã sẵn sàng. Có thể sửa lời và dịch toàn bộ.")
        elif not self.is_running and self.review_summary.get("status") in {"failed", "incomplete"}:
            snapshot.update(status="FAILED", phase="review", stage=self._review_message())
        elif (not self.is_running and output_gate["final_output_blocked"]
              and not self._reviewing_segment_ids and not self.is_editing):
            snapshot.update(status="PREPARED", phase="prepared", progress_pct=None,
                            stage=missing_speech_message(output_gate["missing_speech_ids"]))
        snapshot["can_resume"] = bool(self.initialized and self.is_running and snapshot["status"] == "PAUSED")
        snapshot["can_stop"] = snapshot["status"] in {"RUNNING", "PAUSED"}
        return snapshot

    async def report_progress(self, phase: str, stage: str, progress_pct=None, **details):
        missing_speech = missing_spoken_output_ids(self.segments.values())
        if phase == "complete" and missing_speech:
            phase, stage, progress_pct = "prepared", missing_speech_message(missing_speech), None
        if (phase == "complete" and getattr(self, "auto_export_result", False)
                and (not getattr(self, "output_filename", "") or self.caption_output_outdated)):
            phase, stage = "prepared", "Lời dịch và giọng đã xử lý; đang chờ xuất và kiểm định MP4."
            progress_pct = None
        if (phase != "complete" and getattr(self, "auto_export_result", False)
                and progress_pct is not None and progress_pct >= 100):
            # A measured stage may finish while review, speech or final media
            # validation is still pending. Preserve that stage measurement,
            # but never publish overall 100% for an unvalidated result.
            details["stage_progress_pct"] = progress_pct
            progress_pct = None
        phase_changed = self.progress.get("phase") != phase
        self.progress = {
            "phase": phase, "stage": stage, "progress_pct": progress_pct,
            "status": "COMPLETED" if phase == "complete" else "PREVIEW_READY" if phase == "preview" else "PREPARED" if phase == "prepared" else "RUNNING", **details,
        }
        logging.getLogger("pipeline").info("[%s] %s%s", self.task_id, stage,
                                           "" if progress_pct is None else f" ({progress_pct:g}%)")
        if phase_changed:
            self._persist_if_enabled()
        await self.emit("progress", self.get_progress())

    async def _segment_progress(self, phase: str, stage: str):
        count = sum(s.status in ("READY", "PLAYED") for s in self.segments.values())
        total = len(self.segments)
        await self.report_progress(
            phase, f"{stage} · Đã hoàn tất {count}/{total} câu",
            round(count * 100 / total, 1) if total else None,
            completed_segments=count, total_segments=total,
        )

    async def start(self):
        await self._start_guarded()

    async def start_from_url(self, downloader, url: str):
        self.source_url, self._source_downloader = url, downloader
        await self._start_guarded(downloader, url)

    async def _start_guarded(self, downloader=None, url=None):
        self.start_task = asyncio.current_task()
        self._run_started_at = time.monotonic()
        self._run_ready_baseline = sum(row.status in {"READY", "PLAYED"} for row in self.segments.values())
        try:
            if self.is_stopped:
                raise asyncio.CancelledError
            self.is_running = True
            if not self.start_wall_time:
                self.start_wall_time = time.time()
            self._persist_if_enabled()
            if downloader is not None and (self.video_path is None or not self.video_path.is_file()):
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
            self._startup_failed = (not self._visual_prepass_complete if self._chunked_source_started
                                    else self._visual_result is None if self.visual_translation else not self.initialized)
            logging.getLogger("errors").error("[%s] Không thể xử lý video: %s", self.task_id, self.error)
            self._release_runtime()
            await self.emit("progress", self.get_progress())
            await self.emit("error", {"message": self.error})
            await self.emit("finished", self.get_telemetry())
            raise
        finally:
            self.start_task = None

    async def _emit_initialization(self):
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
            "warnings": list(self.warnings),
            "visual_translation": self.visual_translation,
            "screen_texts": self.screen_texts,
            "translation_sources": self.translation_sources,
        })

    @staticmethod
    def _visual_review_state(item):
        from core.video_intelligence import VideoIntelligence
        return VideoIntelligence._checkpoint_digest({field: getattr(item, field) for field in (
            "start", "end", "text_zh", "asr_text", "literal_vi", "natural_vi", "final_vi",
            "translation_provider", "translation_model", "needs_review", "review_reason", "verification",
            "speaker_confirmation", "voice_id")})

    def _visual_source_context_identity(self):
        from core.video_intelligence import VideoIntelligence
        return VideoIntelligence._checkpoint_digest([
            {"id": item.id, "start": item.start, "end": item.end,
             "text_zh": item.text_zh, "asr_text": item.asr_text,
             **{name: deepcopy(value) for name in SOURCE_METADATA_FIELDS
                if (value := getattr(item, name, None)) is not None}}
            for item in sorted(self.segments.values(), key=lambda value: (value.start, value.id))])

    async def _publish_visual_chunk(self, result, start, end):
        """Commit and synthesize one measured chunk before translating another."""
        from core.video_intelligence import VideoIntelligence
        if self.is_stopped:
            raise asyncio.CancelledError
        # The measured prefix already published in this session (or restored
        # from its manifest) can contain manual masks and split screen regions.
        # Checkpoint drafts must never replace that committed screen metadata.
        published_until = self._visual_completed_seconds
        self._visual_incremental_started = True
        if self._chunk_followups is None:
            self._visual_completed_seconds = max(self._visual_completed_seconds, end)
        summary = result.get("summary")
        if isinstance(summary, str) and summary.strip():
            self._visual_context_summary = summary[:4000]
        if end > published_until:
            screen_start = max(start, published_until)
            self.screen_texts = [row for row in self.screen_texts
                                 if not (screen_start <= row["start"] and row["end"] <= end)]
            for screen in result.get("screen_texts", []):
                if screen["end"] > screen_start:
                    self.screen_texts.append({**deepcopy(screen), "start": max(screen_start, screen["start"])})
        pending = set()
        review_candidates = set()
        draft_identities = {}
        for sid, data in result.get("segments", {}).items():
            item = self.segments.get(sid)
            if item is None or not start <= item.start < item.end <= end:
                raise ValueError("Đoạn dịch không khớp mốc câu thoại nguồn.")
            # A restored chunk must never replace an already committed review
            # or user edit with its earlier prepass draft.
            if item.status in ("READY", "PLAYED"):
                self._visual_published_ids.add(sid)
                continue
            draft_identity = VideoIntelligence._checkpoint_digest({
                "row": data, "start": item.start, "end": item.end,
                "provider": settings.LLM_PROVIDER, "review_model": settings.OPENCODE_MODEL})
            draft_identities[sid] = draft_identity
            reviewed = self._visual_reviewed_drafts.get(sid)
            if (reviewed and reviewed["draft"] == draft_identity
                    and reviewed["state"] == self._visual_review_state(item)):
                # An exact in-session retry after TTS failure keeps the accepted
                # source/text/audit. A changed draft or audit still reviews anew.
                item.status, item.error, item.failed_stage = "WAITING", None, None
                pending.add(sid)
                continue
            self._visual_reviewed_drafts.pop(sid, None)
            item.asr_text = item.asr_text or item.text_zh
            item.text_zh = data.get("text_zh", "").strip()
            item.literal_vi = data.get("literal_vi", "").strip()
            item.natural_vi = data.get("natural_vi", "").strip()
            item.final_vi = _naturalize_spoken_line(item.text_zh, data.get("final_vi", ""))
            provider = data.get("translation_provider") or self.video_intelligence.provider
            if provider not in ("gemini", "openrouter-free", "opencode"):
                raise ValueError("Bản dịch chưa xác định đúng nhà cung cấp đã xử lý.")
            defaults = {"opencode": settings.OPENCODE_MODEL,
                        "openrouter-free": settings.OPENROUTER_MODEL, "gemini": settings.GEMINI_MODEL}
            item.source_method = "video-ai" if provider == "gemini" else "text-ai"
            item.translation_provider = provider
            item.translation_model = str(data.get("translation_model") or defaults[provider])[:200]
            item.evidence_mode = "audio-video" if provider == "gemini" else "asr-ocr-text"
            source = {"provider": provider, "model": item.translation_model, "evidence_mode": item.evidence_mode}
            if source not in self.translation_sources:
                self.translation_sources.append(source)
            item.needs_review = bool(data.get("needs_review", False))
            item.review_reason = data.get("review_reason")
            item.status, item.error, item.failed_stage = "WAITING", None, None
            if provider == "opencode":
                item.needs_review = True
                item.review_reason = item.review_reason or "Đang chờ AI đối chiếu độc lập với nguồn."
                item.verification = {"status": "pending", "semantic_verified": False}
            pending.add(sid)
            review_candidates.add(sid)
        for source in result.get("translation_sources", []):
            if source not in self.translation_sources:
                self.translation_sources.append(dict(source))
        await self._invalidate_changed_address_verifications()
        source_context = self._visual_source_context_identity()
        for sid in pending - review_candidates:
            reviewed = self._visual_reviewed_drafts[sid]
            if (reviewed["context"] != source_context
                    or reviewed["state"] != self._visual_review_state(self.segments[sid])):
                self._visual_reviewed_drafts.pop(sid, None)
                review_candidates.add(sid)
        if not self.initialized:
            await self._emit_initialization()
        await self.emit("screen_update", {"screen_texts": self.screen_texts,
                                         "segments": [self.segment_snapshot(row) for row in self.segments.values()
                                                      if row.status in ("READY", "PLAYED")],
                                         "processed_seconds": self._visual_completed_seconds,
                                         "total_seconds": self.total_duration})
        await self._update_ready()
        for sid in pending:
            await self.emit("segment_update", {**self.segments[sid].to_dict(), "screen_texts": self.screen_texts})
        if self._chunk_followups is not None:
            await self._enqueue_chunk_followup((pending, review_candidates, draft_identities))
            return
        await self._finish_visual_chunk(pending, review_candidates, draft_identities)

    async def _finish_visual_chunk(self, pending, review_candidates, draft_identities):
        """One review/TTS consumer; source preparation has bounded backpressure."""
        groups = []
        for sid in sorted(pending, key=lambda key: self.segments[key].start):
            if (not groups or len(groups[-1]) == self.VISUAL_REVIEW_GROUP_SIZE
                    or (sid in review_candidates) != (groups[-1][0] in review_candidates)):
                groups.append([])
            groups[-1].append(sid)
        for group in groups:
            await self.pause_event.wait()
            if self.is_stopped:
                raise asyncio.CancelledError
            targets = review_candidates.intersection(group)
            if targets and settings.LLM_PROVIDER == "opencode":
                try:
                    await self._review_translations(segment_ids=targets)
                    for sid in targets:
                        self._visual_reviewed_drafts[sid] = {"draft": draft_identities[sid],
                            "state": self._visual_review_state(self.segments[sid]),
                            "context": self._visual_source_context_identity()}
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if getattr(self, "_persistence_capacity_failed", False):
                        raise
                    self.review_summary = {"status": "incomplete"}
                    warning = "AI kiểm tra lại chưa hoàn tất; giữ bản nháp và cho phép thử lại."
                    if warning not in self.warnings:
                        self.warnings.append(warning)
                    logging.getLogger("errors").error("[%s] REVIEW_FAILED error_type=%s", self.task_id, type(error).__name__)
                    for sid in targets:
                        item = self.segments[sid]
                        item.needs_review = True
                        item.review_reason = "AI kiểm tra nguồn chưa hoàn tất; đây là bản nháp cần kiểm tra."
                        item.verification = {"status": "incomplete", "semantic_verified": False,
                                             "reason": item.review_reason}
            for sid in group:
                await self.pause_event.wait()
                if self.is_stopped:
                    raise asyncio.CancelledError
                segment = self.segments[sid]
                try:
                    await self._process_segment(segment)
                except Exception as error:
                    if getattr(self, "_persistence_capacity_failed", False):
                        raise
                    segment.failed_stage, segment.status = segment.status, "FAILED"
                    segment.error = str(error)
                    await self.emit("segment_update", segment.to_dict())
                    # A single unfit sentence or transient speech request must
                    # not hide all following chunks of a long video. Keep the
                    # failed row actionable and carry on; final completion is
                    # decided by the worker from all rows, never this callback.
                    logging.getLogger("errors").error(
                        "[%s] SEGMENT_FAILED_CONTINUING segment_id=%s stage=%s error_type=%s",
                        self.task_id, sid, segment.failed_stage, type(error).__name__)
                    warning = f"Câu {sid + 1} chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
                    if warning not in self.warnings:
                        self.warnings.append(warning)
                    continue
                self._visual_published_ids.add(sid)
        await self.report_progress("visual", "Đang đối chiếu lời thoại, phụ đề và tiêu đề",
            round(100 * self._visual_completed_seconds / self.total_duration, 1) if self.total_duration else None,
            processed_seconds=self._visual_completed_seconds, total_seconds=self.total_duration,
            completed_segments=sum(s.status in ("READY", "PLAYED") for s in self.segments.values()),
            total_segments=len(self.segments))
        await self.pause_event.wait()

    async def _consume_chunk_followups(self):
        while True:
            work = await self._chunk_followups.get()
            try:
                if work is None:
                    return
                await self._finish_visual_chunk(*work)
            finally:
                self._chunk_followups.task_done()

    async def _enqueue_chunk_followup(self, work):
        put = asyncio.create_task(self._chunk_followups.put(work))
        try:
            await asyncio.wait((put, self._chunk_followup_task), return_when=asyncio.FIRST_COMPLETED)
            if self._chunk_followup_task.done():
                await self._chunk_followup_task
                if work is None and put.done() and not put.cancelled():
                    return
                raise RuntimeError("Worker kiểm định đã dừng trước khi nhận hết các đoạn.")
            await put
        finally:
            if not put.done():
                put.cancel()
                await asyncio.gather(put, return_exceptions=True)

    def _published_row(self, row):
        return (row.end <= self._visual_completed_seconds + .001
                or (row.source_method in {"text-ai", "video-ai"} and bool(row.translation_provider)
                    and row.end <= self._visual_scanned_seconds + .001))

    async def _start(self):
        """Initializes audio extraction, segmentation, and launches worker loop."""
        if self.is_stopped:
            raise asyncio.CancelledError
        self.is_running = True
        if self.visual_translation and self.video_intelligence is None:
            from core.video_intelligence import VideoIntelligence, validate_visual_provider
            validate_visual_provider()
            self.video_intelligence = VideoIntelligence()
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
        self._ensure_tts_engine()

        if (self.translation_mode == "preview" or self._chunked_source_started
                or (not getattr(self, "_force_legacy_visual", False)
                    and self.visual_translation and not self._prepared and not self.segments)):
            return await self._start_chunked_visual()
        return await self._start_legacy_source()

    async def _start_legacy_source(self):
        """Retain preparation/execution for existing whole-source checkpoints."""
        if not self._prepared:
            from core.streaming.preparation_checkpoint import load
            saved = await self._run_blocking(load, self)
            if saved:
                self.raw_audio_16k = Path(saved["assets"]["raw_audio_16k"]["path"])
                self.bgm_audio_path = Path(saved["assets"]["bgm_audio_path"]["path"])
                self.bgm_url = f"/api/streaming/bgm/{self.task_id}"
                self.total_duration, self.video_size = saved["duration"], tuple(saved["video_size"])
                self.suppression_stats = saved["suppression_stats"]
                for row in saved["segments"]:
                    segment = SegmentItem(row["id"], row["start"], row["end"], row["duration"])
                    segment.text_zh, segment.emotion = row["text_zh"], row["emotion"]
                    segment.asr_pretranscribed = row["asr_pretranscribed"]
                    self.segments[segment.id] = segment
                self._prepared = True
                await self.report_progress("prepare", "Đã khôi phục âm thanh và lời nhận diện đã lưu")
        if not self._prepared:
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
                    asr_loop = asyncio.get_running_loop()
                    def asr_progress(end):
                        if not self.is_stopped and self.total_duration > 0:
                            pct = round(min(99.9, max(0, end / self.total_duration * 100)), 1)
                            asr_loop.call_soon_threadsafe(lambda: asyncio.create_task(self.report_progress(
                                "asr", f"Đang nhận diện lời nói · {end:.0f}/{self.total_duration:.0f} giây", pct)))
                    recognized = await self._run_blocking(
                        self.faster_whisper.transcribe, self.raw_audio_16k, language="zh", progress_callback=asr_progress,
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
                    for name in SOURCE_METADATA_FIELDS:
                        if name in s:
                            setattr(item, name, deepcopy(s[name]))
                self.segments[item.id] = item

            self._prepared = True
            from core.streaming.preparation_checkpoint import save
            try:
                await self._run_blocking(save, self)
            except OSError:
                self.warnings.append("Không lưu được tiến độ nhận diện xuống đĩa; giữ trong phiên hiện tại.")

        if self.visual_translation and self.video_intelligence:
            await self.report_progress("visual", "Đang đọc phụ đề và tiêu đề trong khung hình...")
            event_loop = asyncio.get_running_loop()
            def visual_progress(percent):
                if not self.is_stopped:
                    event_loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self.report_progress(
                            "visual", "Đang đối chiếu lời thoại, phụ đề và tiêu đề", percent)))
            def visual_detail(detail):
                if not self.is_stopped:
                    labels = {"decode": "Đang chuẩn bị khung hình", "ocr": "Đang đọc chữ trong khung hình",
                              "source": "Đang đối chiếu lời nhận diện với chữ trên hình",
                              "translate": "Đang chờ OpenCode dịch đoạn video",
                              "verify": "Đang chờ OpenCode kiểm tra bản dịch"}
                    step = detail.get("visual_step", "")
                    processed = detail.get("processed_seconds", self._visual_completed_seconds)
                    percent = round(100 * processed / self.total_duration, 1) if self.total_duration else None
                    event_loop.call_soon_threadsafe(lambda: asyncio.create_task(self.report_progress(
                        "visual", labels.get(step, "Đang phân tích đoạn video"), percent, **detail)))
            def visual_chunk(result, start, end):
                entered = threading.Event()
                completed = threading.Event()
                async def publish():
                    entered.set()
                    try:
                        await self._publish_visual_chunk(result, start, end)
                    finally:
                        completed.set()
                future = asyncio.run_coroutine_threadsafe(publish(), event_loop)
                check = current_execution_context().cancel_check
                while True:
                    if self.is_stopped or (check and check()):
                        # Cancelling a coroutine before its first instruction
                        # skips its finally; let it enter before cancellation.
                        if not entered.wait(.1):
                            continue
                        future.cancel()
                        # A coroutine may still own a native review/TTS thread.
                        # Wait for its finally before releasing shared models.
                        while not completed.wait(.1):
                            pass
                        raise asyncio.CancelledError
                    try:
                        return future.result(timeout=.1)
                    except FutureTimeoutError:
                        if future.done():
                            # A TTS/provider coroutine can itself raise
                            # TimeoutError; it is a failure, not a poll timeout.
                            raise
                        continue
            try:
                if self._visual_result is None:
                    source_rows = []
                    for item in self.segments.values():
                        source = SegmentItem(item.id, item.start, item.end, item.duration)
                        source.text_zh = item.asr_text or item.text_zh
                        source_rows.append(source)
                    self._visual_result = await self._run_blocking(
                        self.video_intelligence.prepass,
                        self.video_path,
                        source_rows,
                        total_duration=self.total_duration,
                        cancel_check=lambda: self.is_stopped,
                        progress_callback=visual_progress,
                        chunk_callback=visual_chunk,
                        detail_callback=visual_detail,
                    )
                visual_result = self._visual_result
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
            if not self._visual_incremental_started:
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
                if item.id in self._visual_published_ids:
                    continue
                item.asr_text = item.text_zh
                item.text_zh = data.get("text_zh", "").strip()
                item.literal_vi = data.get("literal_vi", "").strip()
                item.natural_vi = data.get("natural_vi", "").strip()
                item.final_vi = _naturalize_spoken_line(item.text_zh, data.get("final_vi", ""))
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
            self._visual_prepass_complete = True

        # The selected free provider performs a separate evidence/meaning pass
        # before speech is generated, including rows the draft called clear.
        if self.visual_translation and settings.LLM_PROVIDER == "opencode" and not self._visual_incremental_started:
            try:
                await self._review_translations()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.review_summary = {"status": "failed"}
                logging.getLogger("errors").error("[%s] REVIEW_FAILED error_type=%s", self.task_id, type(error).__name__)
                self.warnings.append("AI kiểm tra lại chưa hoàn tất; giữ bản nháp và cho phép thử lại.")

        # 3. Enqueue all segments with initial priority based on time
        for item in self.segments.values():
            if item.status == "WAITING":
                await self.queue.put((item.start, item.id))

        # 4. Emit initialization info to client
        if not self.initialized:
            await self._emit_initialization()

        # 5. Launch background worker
        await self._segment_progress("asr", "Đang chuẩn bị nhận diện lời nói")
        self.worker_task = asyncio.create_task(self._worker_loop())
        self.worker_task.add_done_callback(
            lambda task: self._release_runtime() if task.cancelled() else None
        )

    async def _start_chunked_visual(self):
        if not self.visual_translation:
            return await self._produce_chunked_visual()
        self._chunk_followups = asyncio.Queue(maxsize=2)
        self._chunk_followup_task = asyncio.create_task(self._consume_chunk_followups())
        try:
            await self._produce_chunked_visual()
        finally:
            if self._chunk_followup_task and not self._chunk_followup_task.done():
                self._chunk_followup_task.cancel()
            await asyncio.gather(self._chunk_followup_task, return_exceptions=True)
            self._chunk_followup_task = None
            self._chunk_followups = None

    async def _produce_chunked_visual(self):
        """Prepare, translate and publish one source interval at a time."""
        from core.streaming.chunked_source import prepare_interval, publish_background_prefix, ensure_source_identity
        from core.streaming.export import HQExporter
        from core.video_intelligence import VideoIntelligence
        self._chunked_source_started = True
        self._preview_ready = False
        await self._run_blocking(ensure_source_identity, self)
        if not self.total_duration:
            info = await self._run_blocking(VideoIntelligence.media_info, self.video_path,
                                            cancel_check=lambda: self.is_stopped)
            if not info["has_audio"]:
                raise ValueError("Video không có luồng âm thanh để dịch.")
            self.total_duration = float(info["duration"])
            self.video_size = await self._run_blocking(HQExporter._video_size, self.video_path, lambda: self.is_stopped)
            self._persist_if_enabled()
        from core.streaming.recovery import reserve, input_hash, transition, contiguous_coverage, recover_interrupted
        if not self._chunk_jobs:
            self._legacy_visual_prefix = self._visual_completed_seconds
        else:
            self._visual_completed_seconds = contiguous_coverage(self._chunk_jobs, self._legacy_visual_prefix)
        recover_interrupted(self._chunk_jobs)
        self._visual_scanned_seconds = max(self._visual_scanned_seconds, self._visual_completed_seconds)
        target = min(self.total_duration, self.preview_seconds) if self.translation_mode == "preview" else self.total_duration
        # New source is admitted before retrying old holes. A bounded review/TTS
        # queue keeps source decoding and provider work from monopolizing RAM.
        retry_intervals = [(row["start"], row["end"]) for row in self._chunk_jobs
                           if row["state"] != "COMPLETED" and row["start"] < target]
        source_blocked = False
        while self._visual_scanned_seconds < target - .001 or retry_intervals:
            await self.pause_event.wait()
            if self.is_stopped:
                raise asyncio.CancelledError
            if self._visual_scanned_seconds < target - .001:
                cursor = self._visual_scanned_seconds
                nominal_end = min(target, cursor + self.preview_seconds)
            else:
                cursor, nominal_end = retry_intervals.pop(0)
            while self._source_prepared_seconds < nominal_end - .001:
                try:
                    record = await prepare_interval(self, self._source_prepared_seconds,
                        min(self.total_duration, self._source_prepared_seconds + self.preview_seconds))
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if getattr(self, "_persistence_capacity_failed", False):
                        raise
                    # A native recognizer/resource failure in the source tail
                    # must not cancel already admitted review/TTS work. Keep
                    # the exact prepared cursor for Resume, drain its healthy
                    # predecessors, and report the incomplete source at the
                    # terminal gate. A failed durable write still propagates.
                    source_blocked = True
                    self._startup_failed = True
                    warning = (f"Chưa nhận diện được đoạn từ {self._source_prepared_seconds:.2f} giây; "
                               "các đoạn đã nhận diện tiếp tục xử lý. Tiếp tục sẽ thử đúng đoạn còn thiếu.")
                    if warning not in self.warnings:
                        self.warnings.append(warning)
                    logging.getLogger("errors").error(
                        "[%s] SOURCE_RETRY_PENDING source_start=%s error_type=%s",
                        self.task_id, self._source_prepared_seconds, type(error).__name__)
                    self._persist_if_enabled()
                    await self.emit("progress", self.get_progress())
                    break
                old_background = self.bgm_audio_path
                background = await publish_background_prefix(self, record)
                next_id = max(self.segments, default=-1) + 1
                for row in record["rows"]:
                    item = SegmentItem(next_id, row["start"], row["end"], row["duration"])
                    item.text_zh = item.asr_text = row["text_zh"]
                    item.emotion = row.get("emotion") or "<|NEUTRAL|>"
                    item.asr_pretranscribed = True
                    for name in SOURCE_METADATA_FIELDS:
                        if name in row:
                            setattr(item, name, deepcopy(row[name]))
                    self.segments[next_id] = item
                    next_id += 1
                self.raw_audio_16k = Path(record["raw_audio"]["path"])
                self.bgm_audio_path = background
                self.suppression_stats = record["suppression_stats"]
                self._source_prepared_seconds = record["end"]
                self._prepared = True
                self.bgm_url = f"/api/streaming/bgm/{self.task_id}?coverage={round(record['end'] * 1000)}"
                self._persist_if_enabled()
                # Old containers are task-generated prefix views. Keep all
                # immutable per-interval packets/checkpoints for exact Retry.
                if old_background and old_background != background and old_background.parent == background.parent:
                    try:
                        old_background.unlink(missing_ok=True)
                    except OSError:
                        pass
                await self.emit("background_update", {"bgm_url": self.bgm_url,
                    "source_prepared_seconds": self._source_prepared_seconds})
            if source_blocked:
                break
            end = min(nominal_end, self._source_prepared_seconds)
            crossing = [row for row in self.segments.values() if row.start < end < row.end]
            if crossing:
                end = max(row.end for row in crossing)
            if end <= cursor or end > self._source_prepared_seconds + .05:
                raise ValueError("Không tìm được ranh giới câu thoại trong đoạn đã nhận diện.")
            source_rows = []
            for row in self.segments.values():
                if cursor <= row.start < row.end <= end + .001:
                    source = SegmentItem(row.id, row.start, row.end, row.duration)
                    source.text_zh = row.asr_text or row.text_zh
                    source.asr_text = source.text_zh
                    for name in SOURCE_METADATA_FIELDS:
                        setattr(source, name, deepcopy(getattr(row, name, None)))
                    source_rows.append(source)
            configuration = {"source": self._chunked_source_identity, "provider": settings.LLM_PROVIDER,
                             "model": settings.OPENCODE_MODEL, "visual": self.visual_translation}
            interval = reserve(self._chunk_jobs, cursor, end, input_hash(source_rows, configuration))
            transition(interval, "RUNNING")
            self._persist_if_enabled()
            if not self.visual_translation:
                # Turning OCR review off must still honor preview-first. Use
                # the same bounded recognition timeline and selected text
                # provider, rather than eagerly recognizing the entire video.
                self.asr_engine = self.faster_whisper
                if not self.initialized:
                    await self._emit_initialization()
                for source in source_rows:
                    row = self.segments[source.id]
                    if row.status in ("READY", "PLAYED"):
                        continue
                    await self.pause_event.wait()
                    try:
                        await self._process_segment(row)
                    except Exception as error:
                        if getattr(self, "_persistence_capacity_failed", False):
                            raise
                        row.failed_stage, row.status, row.error = row.status, "FAILED", str(error)
                        await self.emit("segment_update", row.to_dict())
                        logging.getLogger("errors").error(
                            "[%s] SEGMENT_FAILED_CONTINUING segment_id=%s stage=%s error_type=%s",
                            self.task_id, row.id, row.failed_stage, type(error).__name__)
                self._visual_incremental_started = True
                self._visual_completed_seconds = end
                self._visual_scanned_seconds = max(self._visual_scanned_seconds, end)
                transition(interval, "COMPLETED")
                await self._update_ready()
                self._persist_if_enabled()
                continue
            event_loop = asyncio.get_running_loop()
            active = True
            def detail_callback(detail):
                if active and not self.is_stopped:
                    labels = {"decode": "Đang chuẩn bị khung hình", "ocr": "Đang đọc chữ trong khung hình",
                              "source": "Đang đối chiếu lời nguồn", "translate": "Đang chờ Muse dịch đoạn video",
                              "verify": "Đang chờ Muse kiểm tra bản dịch"}
                    value = dict(detail)
                    stage = labels.get(value.get("visual_step"), "Đang phân tích đoạn video")
                    async def publish_detail():
                        if active and self.is_running and not self.is_stopped and not self.error:
                            await self.report_progress("visual", stage,
                                round(100 * self._visual_completed_seconds / self.total_duration, 1), **value)
                    event_loop.call_soon_threadsafe(lambda: asyncio.create_task(publish_detail()))
            def chunk_callback(result, start, stop):
                entered, finished = threading.Event(), threading.Event()
                async def publish():
                    entered.set()
                    try:
                        await self._publish_visual_chunk(result, start, stop)
                    finally:
                        finished.set()
                future = asyncio.run_coroutine_threadsafe(publish(), event_loop)
                check = current_execution_context().cancel_check
                while True:
                    if self.is_stopped or (check and check()):
                        if not entered.wait(.1):
                            continue
                        future.cancel()
                        while not finished.wait(.1):
                            pass
                        raise asyncio.CancelledError
                    try:
                        return future.result(timeout=.1)
                    except FutureTimeoutError:
                        if future.done():
                            raise
            try:
                # Only known source is provided. Recognition lookahead retains
                # adjacent turns with stable global IDs; later hours are unknown.
                context = [{**{name: deepcopy(getattr(row, name)) for name in SOURCE_METADATA_FIELDS
                              if getattr(row, name, None) is not None},
                            "id": row.id, "start": row.start, "end": row.end,
                            "text_zh": row.text_zh or row.asr_text, "asr_text": row.asr_text,
                            "source_needs_review": row.source_method == "audio"}
                           for row in self.segments.values()]
                result = await self._run_blocking(self.video_intelligence.prepass, self.video_path, source_rows,
                    total_duration=self.total_duration, start_time=cursor, end_time=end,
                    context_segments=context, previous_summary=getattr(self, "_visual_context_summary", ""),
                    cancel_check=lambda: self.is_stopped,
                    chunk_callback=chunk_callback, detail_callback=detail_callback)
                if set(result.get("segments", {})) != {row.id for row in source_rows}:
                    raise ValueError("Đoạn dịch chưa trả đủ các ID nguồn.")
                if any(not (self.segments[row.id].source_method in {"text-ai", "video-ai"}
                            and self.segments[row.id].translation_provider) for row in source_rows):
                    raise ValueError("Đoạn dịch chưa được xuất bản vào checkpoint câu thoại.")
                transition(interval, "COMPLETED")
            except asyncio.CancelledError:
                transition(interval, "RETRY_PENDING", "cancelled")
                self._persist_if_enabled()
                raise
            except Exception as error:
                if getattr(self, "_persistence_capacity_failed", False):
                    raise
                transition(interval, "RETRY_PENDING", type(error).__name__)
                if interval["attempts"] < 2 and (cursor, end) not in retry_intervals:
                    retry_intervals.append((cursor, end))
                for source in source_rows:
                    row = self.segments[source.id]
                    if row.status in {"READY", "PLAYED"} or row.source_method in {"text-ai", "video-ai"}:
                        continue
                    row.status, row.failed_stage = "FAILED", "TRANSLATING"
                    row.error = "AI chưa trả bản dịch hợp lệ; đoạn này được lưu để thử lại, các đoạn khác tiếp tục."
                    await self.emit("segment_update", row.to_dict())
                logging.getLogger("errors").warning(
                    "[%s] CHUNK_RETRY_PENDING chunk_id=%s attempts=%s error_type=%s",
                    self.task_id, interval["id"], interval["attempts"], type(error).__name__)
            finally:
                active = False
                # This source OCR owner is idle after prepass returns. Keeping
                # its native ONNX session through the next ASR/review batch
                # unnecessarily retains model RAM on long videos.
                self._release_visual_runtime()
            self._visual_scanned_seconds = max(self._visual_scanned_seconds, end)
            self._visual_completed_seconds = contiguous_coverage(self._chunk_jobs, self._legacy_visual_prefix)
            self._persist_if_enabled()
        if self._chunk_followups is not None:
            await self._enqueue_chunk_followup(None)
            await self._chunk_followup_task
        # Recover interrupted semantic review after all newly admitted source;
        # it must not keep a long video's untouched tail waiting behind it.
        await self._resume_pending_chunk_reviews(synthesize_pending=True)
        self._visual_prepass_complete = self._visual_completed_seconds >= self.total_duration - .05
        # A later bounded chunk may correct source words cited by an earlier
        # address audit. Re-review that finite stale set once before speech
        # starts so the final queue never presents an obsolete role verdict.
        stale_address_rows = any(
            row.end <= self._visual_completed_seconds + .001
            and row.source_method == "text-ai"
            and row.translation_provider == "opencode"
            and (row.verification or {}).get("status") == "unresolved"
            and isinstance((row.verification or {}).get("address_stale_source_ids"), list)
            and bool((row.verification or {}).get("address_stale_source_ids"))
            for row in self.segments.values())
        if stale_address_rows:
            await self._resume_pending_chunk_reviews(stale_address_only=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        for row in self.segments.values():
            if row.status == "WAITING" and self._published_row(row):
                self.queue.put_nowait((row.start, row.id))
        if not self.initialized:
            await self._emit_initialization()
        self.worker_task = asyncio.create_task(self._worker_loop())
        self.worker_task.add_done_callback(lambda task: self._release_runtime() if task.cancelled() else None)

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
                               "emotion": row.get("emotion"),
                               **{name: deepcopy(row[name]) for name in SOURCE_METADATA_FIELDS if name in row},
                               **({"speaker_id": row.get("speaker_id", row.get("speaker"))}
                                  if row.get("speaker_id", row.get("speaker")) is not None else {})})
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
        if not self.initialized and not self.segments:
            # Knowing the source duration does not mean its translation/audio
            # is ready. The previous empty-loop else advertised the full clip.
            self.playable_until = 0.0
            self.buffer_ahead = 0.0
            return
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

        if self._visual_incremental_started and not self._visual_prepass_complete:
            playable = min(playable, self._visual_completed_seconds)
        if self._chunked_source_started:
            playable = min(playable, self._visual_completed_seconds)
        self.playable_until = round(playable, 2)
        self.buffer_ahead = round(max(0.0, self.playable_until - self.current_playback_time), 2)

        elapsed = time.time() - self.start_wall_time
        if elapsed > 0 and self.total_processed_duration > 0:
            self.realtime_factor = round(self.total_processed_duration / elapsed, 2)

    def get_telemetry(self) -> Dict[str, Any]:
        output_gate = final_output_metadata(self.segments.values())
        return {
            "current_playback_time": round(self.current_playback_time, 2),
            "playable_until": self.playable_until,
            "buffer_ahead": self.buffer_ahead,
            "realtime_factor": self.realtime_factor,
            "time_to_first_play": self.time_to_first_play,
            "ready_to_play": self.first_play_emitted,
            "status": "cancelling" if getattr(self, "_stop_draining", False) else "cancelled" if self.is_stopped else ("failed" if self.error else ("running" if self.is_running else "prepared" if self.progress.get("phase") == "prepared" or output_gate["final_output_blocked"] else "finished")),
            **output_gate,
            "error": self.error,
            "warnings": list(self.warnings),
            "review_summary": dict(self.review_summary),
            "translation_sources": list(self.translation_sources),
            **self._review_metadata(),
            "can_retry": self.can_retry,
            "translation_mode": self.translation_mode,
            "preview_seconds": self.preview_seconds,
            "processed_seconds": self._visual_completed_seconds,
            "source_prepared_seconds": self._source_prepared_seconds,
            "can_translate_full": self.can_translate_full,
            "vocal_removal_engine": self.vocal_suppressor.name,
            "suppression_level": f"{self.vocal_suppressor.suppression_level_db:.1f} dB",
            "suppression_rtf": self.suppression_stats.get("throughput_rtf", "0.0x")
        }

    async def _run_ffmpeg(self, cmd):
        from core.media_process import run_media
        def prepare_media():
            # The cancellation context covers both session Stop and direct
            # cancellation of this waiter. _run_blocking drains the worker;
            # run_media kills/reaps its child before temporary files can go.
            run_media(cmd, current_execution_context().cancel_check)
        await self._run_blocking(prepare_media)

    async def _run_blocking(self, function, *args, **kwargs):
        """Cancellation cannot kill a Python worker thread; wait before removing its files."""
        if self.is_stopped:
            raise asyncio.CancelledError
        cancellation = threading.Event()

        def invoke():
            with execution_context(self.task_id, lambda: self.is_stopped or cancellation.is_set()):
                return function(*args, **kwargs)

        work = asyncio.get_running_loop().run_in_executor(
            None, contextvars.copy_context().run, invoke,
        )
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(work)
                break
            except asyncio.CancelledError:
                cancellation.set()
                # A worker may raise CancelledError itself. Its executor future
                # is then done with an exception, not marked cancelled; awaiting
                # it repeatedly would spin forever and block the event loop.
                if work.done():
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
        if self.edit_tasks or (self.review_task and not self.review_task.done()):
            return
        if self.is_stopped:
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if any(task is not None and task is not current and not task.done()
                   for task in (self.start_task, self.worker_task, self._chunk_followup_task)):
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
        # These files back a durable preparation checkpoint and may already be
        # reused by another session. They are not disposable failure scratch.
        keep_preparation = self._prepared or self._persistence_enabled
        if raw_audio and not needs_remaining_asr and not self._startup_failed and not keep_preparation:
            remove_generated(raw_audio)
        if self.is_stopped and self._owns_cache and not keep_preparation:
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
        if (self.is_stopped and not self._persistence_enabled and self._download_info and not self._download_info.get("is_local")
                and not self._download_info.get("reusable_source")):
            # The downloader reports only uniquely named artifacts it created.
            # Never glob a prefix or remove a local user input here.
            prefix = self._download_info.get("owned_prefix")
            if prefix:
                prefix_path = Path(prefix).resolve()
                for value in self._download_info.get("owned_paths", []):
                    owned_path = Path(value).resolve()
                    if owned_path.parent == prefix_path.parent and owned_path.name.startswith(prefix_path.name + "."):
                        remove_generated(owned_path)
        if self.is_stopped and active_streaming_sessions.get(self.task_id) is self:
            # A History read must not restore a second writer while cancelled
            # native/provider work still owns this project's checkpoint.
            active_streaming_sessions.pop(self.task_id, None)

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
                    rolling_context=self._dialogue_context_before(seg),
                )
                seg.literal_vi = trans.get("literal_vi", "")
                seg.natural_vi = trans.get("natural_vi", "")
                seg.final_vi = _naturalize_spoken_line(
                    seg.text_zh, trans.get("final_vi") or seg.natural_vi or seg.literal_vi
                )
                if trans.get("needs_review"):
                    seg.needs_review = True
                    seg.review_reason = trans.get("review_reason") or "Nhận dạng câu thoại chưa chắc chắn; hãy kiểm tra và sửa tiếng Việt."
                if not seg.final_vi.strip() and not seg.needs_review:
                    raise RuntimeError("Dịch thuật trả về nội dung trống.")
                if seg.final_vi.strip() and not seg.needs_review:
                    self.rolling_context.append({"zh": seg.text_zh, "vi": seg.final_vi})
                    self.rolling_context = self.rolling_context[-64:]
            await self._synthesize_segment(seg)
        finally:
            slice_wav.unlink(missing_ok=True)

    @staticmethod
    def _dub_audio_duration(path):
        with wave.open(str(path), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            width, channels = audio.getsampwidth(), audio.getnchannels()
            if (frames <= 0 or not 8000 <= rate <= 192000 or not 1 <= channels <= 2
                    or width not in (1, 2, 3, 4) or audio.getcomptype() != "NONE"):
                raise ValueError("Âm thanh lồng tiếng trống hoặc không hợp lệ.")
            remaining = frames
            while remaining:
                count = min(remaining, rate)
                if len(audio.readframes(count)) != count * width * channels:
                    raise ValueError("Âm thanh lồng tiếng bị thiếu dữ liệu.")
                remaining -= count
            duration = frames / rate
            return duration

    def _dub_rows(self):
        rows = []
        for item in sorted(self.segments.values(), key=lambda row: row.start):
            row = item.to_dict()
            if item.audio_path and item.status in ("READY", "PLAYED"):
                try:
                    row["audio_duration"] = self._cached_dub_audio_duration(item.audio_path)
                    row["audio_path"] = item.audio_path
                except (OSError, EOFError, wave.Error, ValueError, ZeroDivisionError):
                    pass  # Unknown audio remains an occupied, immovable slot.
            rows.append(row)
        return rows

    def _cached_dub_audio_duration(self, path):
        """Read PCM once per file revision, retaining full corruption checks."""
        path = Path(path).resolve(strict=True)
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        cache = getattr(self, "_dub_duration_cache", None)
        if cache is None:
            cache = self._dub_duration_cache = {}
        saved = cache.get(str(path))
        if saved and saved[0] == identity:
            return saved[1]
        duration = self._dub_audio_duration(path)
        after = path.stat()
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Âm thanh đã thay đổi trong khi kiểm tra thời lượng.")
        cache[str(path)] = (identity, duration)
        while len(cache) > 10000:
            cache.pop(next(iter(cache)))
        return duration

    async def _prepare_dub_rescue(self, rows, focus, required_duration):
        """Stage bounded automatic neighbor repairs; publish nothing here."""
        shadow = deepcopy(rows)
        staged = {}
        known = self._source_prepared_seconds
        ordered = [row["id"] for row in shadow]
        index = ordered.index(focus.id)
        nearby = [ordered[pos] for distance in (1, 2)
                  for pos in (index - distance, index + distance) if 0 <= pos < len(ordered)]
        def capacity():
            return available_reflow_duration(shadow, focus.id, total_duration=known)
        def eligible(item):
            return (item.status in {"READY", "PLAYED"} and item.audio_path
                    and item.final_vi.strip() and (item.verification or {}).get("status") != "manual")
        async def stage(item, path, spoken):
            measured = self._dub_audio_duration(path)
            original = next(row for row in shadow if row["id"] == item.id)
            if measured >= original["audio_duration"] - .001:
                path.unlink(missing_ok=True)
                return False
            original["audio_duration"] = measured
            staged[item.id] = {"audio_path": str(path.resolve()), "spoken": spoken}
            logging.getLogger("pipeline").info(
                "PACING_NEIGHBOR_STAGED run_id=%s focus_id=%s neighbor_id=%s measured_seconds=%.6f rewritten=%s",
                self.task_id, focus.id, item.id, measured, spoken["text"] != item.final_vi)
            return True
        try:
            # Reuse every spoken sample; the remaining speed allowance applies
            # to the combined rate, not another independent 1.15x pass.
            for sid in nearby:
                item = self.segments[sid]
                if not eligible(item):
                    continue
                row = next(row for row in shadow if row["id"] == sid)
                measured = row.get("audio_duration")
                if not measured or item.tts_duration <= 0:
                    continue
                prior_speed = max(float(item.speed_ratio), item.tts_duration / measured, 1.)
                relative_cap = self.aligner.max_speed / prior_speed
                if relative_cap <= 1.01:
                    continue
                path = self.segments_dir / f"seg_{sid}_{uuid.uuid4().hex}.wav"
                staged[sid] = {"audio_path": str(path.resolve())}
                local = TimingBudgetAligner((.9, relative_cap))
                try:
                    relative = await self._run_blocking(local.apply_atempo,
                        Path(item.audio_path), path, relative_cap,
                        fit_duration=measured / relative_cap + .012)
                except SpeechBudgetError:
                    path.unlink(missing_ok=True)
                    staged.pop(sid, None)
                    continue
                start, _ = resolve_dub_timing(item.to_dict())
                boundaries = [{"text": word["text"], "start": word["start"] - start,
                               "end": word["end"] - start}
                              for cue in item.subtitle_cues for word in cue.get("words", [])]
                spoken = {"text": item.final_vi, "tts_duration": item.tts_duration,
                    "speed_ratio": min(self.aligner.max_speed, prior_speed * relative),
                    "boundaries": boundaries, "timing_ratio": relative, "pacing_verification": None}
                if not await stage(item, path, spoken):
                    staged.pop(sid, None)
                if capacity() >= required_duration:
                    return shadow, staged
            # At most one nearby translation changes, only after the same
            # independent source/meaning/address check used for focus rewrites.
            for sid in [ordered[pos] for pos in (index - 1, index - 2) if pos >= 0]:
                item = self.segments[sid]
                if (sid in staged or not eligible(item)
                        or (item.verification or {}).get("status") not in {"verified", "corrected"}):
                    continue
                row = next(row for row in shadow if row["id"] == sid)
                measured = row.get("audio_duration")
                if not measured:
                    continue
                budget = measured - max(.05, required_duration - capacity()) - .012
                if budget <= .1:
                    continue
                context = self._dialogue_context_before(item)
                feedback = {"raw_budget_seconds": budget * self.aligner.max_speed,
                            "measured_candidates": [{"text": item.final_vi, "measured_seconds": item.tts_duration}]}
                proposal = None
                for _ in range(3):
                    try:
                        proposal = await self._run_blocking(self.translator.rewrite_for_pacing,
                            item.text_zh, item.final_vi, budget * self.aligner.max_speed * .98,
                            context, measured_duration=item.tts_duration, feedback=feedback)
                        break
                    except PacingReviewRejected as exc:
                        feedback.update(exc.feedback)
                if proposal is None:
                    continue
                proof = proposal.get("pacing_verification")
                if (not isinstance(proof, dict) or proof.get("status") != "verified"
                        or proof.get("text") != proposal.get("final_vi")):
                    raise RuntimeError("Thiếu xác minh cho lời đọc liền kề đã rút gọn.")
                path = self.segments_dir / f"seg_{sid}_{uuid.uuid4().hex}.wav"
                staged[sid] = {"audio_path": str(path.resolve())}
                try:
                    spoken = await self._run_blocking(synthesize_natural_speech,
                        text=proposal["final_vi"], source=item.text_zh, duration=budget,
                        output_path=path, engine=self.tts_engine, aligner=self.aligner,
                        voice=self._segment_voice(item), ref_audio=self.ref_audio, context=context)
                except SpeechBudgetError:
                    path.unlink(missing_ok=True)
                    staged.pop(sid, None)
                    continue
                spoken["pacing_verification"] = proposal["pacing_verification"]
                if not await stage(item, path, spoken):
                    staged.pop(sid, None)
                break
            return shadow, staged
        except BaseException:
            for update in staged.values():
                Path(update["audio_path"]).unlink(missing_ok=True)
            raise

    def _dub_fit_window(self, seg, rows):
        """Share the same proven timeline budget between fitting and recovery."""
        start, end = resolve_dub_timing(seg.to_dict())
        duration = end - start
        known_duration = self._source_prepared_seconds if self._chunked_source_started else self.total_duration
        tail_limit = None
        if self._chunked_source_started and known_duration > seg.end:
            # Only a prepared following dialogue gap may extend the endpoint.
            # Anchor the ceiling to source time, so retries cannot compound it.
            following = next((row for row in rows if row["start"] >= seg.end and row["id"] != seg.id), None)
            tail_limit = min(seg.end + MAX_TAIL_LIMIT_EXTENSION, known_duration)
            if following is not None:
                following_start, _ = resolve_dub_timing(following)
                tail_limit = min(tail_limit, following["start"], following_start)
            if tail_limit > seg.end + .35 + 1e-9:
                rows = [{**row, "dub_tail_limit": tail_limit} if row["id"] == seg.id else row for row in rows]
            else:
                tail_limit = None
        capacity = max(duration, available_reflow_duration(rows, seg.id, total_duration=known_duration))
        capacity = min(capacity, duration + 1.35) if tail_limit is not None else capacity
        if not self._chunked_source_started:
            # Existing full-source projects retain their original fitting
            # contract; only the new bounded timeline opts into two-sided fit.
            capacity = min(capacity, duration + .35)
        return rows, duration, capacity, known_duration, tail_limit

    async def _fit_dub(self, seg, *, text, source, output_path, translator=None, context=None, on_stage=None, neighbor_rescue=False, pacing_verification=None, preserve_manual_timing=False):
        rows, duration, capacity, known_duration, tail_limit = self._dub_fit_window(seg, self._dub_rows())
        revision = {row["id"]: (row["revision"], row["dub_start"], row["dub_end"]) for row in rows}
        start, end = resolve_dub_timing(seg.to_dict())
        # The planner bounds both source endpoints to +/-350 ms and can borrow
        # on both sides. Capping the *duration* to +350 ms discarded a valid
        # additional 350 ms at the other endpoint (the real 1.3 s row could
        # safely fit 2.0 s, yet pacing was told its limit was only 1.65 s).
        staged = {}
        returned = False
        retry_text, retry_proof, retry_translator = text, None, translator
        try:
            try:
                spoken = await self._run_blocking(synthesize_natural_speech,
                    text=text, source=source, duration=duration, max_duration=capacity,
                    output_path=output_path, engine=self.tts_engine, aligner=self.aligner,
                    translator=translator, voice=self._segment_voice(seg), ref_audio=self.ref_audio,
                    context=context, on_stage=on_stage,
                    allow_bidirectional_reflow=self._chunked_source_started,
                    **({"max_duration_limit": capacity} if tail_limit is not None else {}),
                    **({"pacing_verification": pacing_verification} if pacing_verification is not None else {}))
            except (SpeechBudgetError, PacingReviewRejected) as exc:
                required = getattr(exc, "required_dub_duration", None)
                if (not neighbor_rescue or not self._chunked_source_started or translator is None
                        or not isinstance(required, (int, float)) or not math.isfinite(required)):
                    raise
                rows, staged = await self._prepare_dub_rescue(rows, seg, required)
                capacity = available_reflow_duration(rows, seg.id, total_duration=known_duration)
                if capacity < required or not staged:
                    raise
                # If the failed focus attempt actually measured a shorter,
                # independently verified rewrite, retry that exact wording.
                # Neighbor rescue must never trigger a second semantic rewrite
                # that silently replaces the candidate which was measured.
                candidate = getattr(exc, "candidate_text", None)
                proof = getattr(exc, "pacing_verification", None)
                if (isinstance(candidate, str) and candidate.strip()
                        and isinstance(proof, dict)
                        and proof.get("status") == "verified"
                        and proof.get("text") == candidate):
                    retry_text, retry_proof, retry_translator = candidate.strip(), proof, None
                retry_kwargs = {}
                if retry_proof is not None:
                    retry_kwargs["pacing_verification"] = retry_proof
                spoken = await self._run_blocking(synthesize_natural_speech,
                    text=retry_text, source=source, duration=duration, max_duration=capacity,
                    output_path=output_path, engine=self.tts_engine, aligner=self.aligner,
                    translator=retry_translator, voice=self._segment_voice(seg), ref_audio=self.ref_audio,
                    context=context, on_stage=on_stage, allow_bidirectional_reflow=True,
                    **({"max_duration_limit": capacity} if tail_limit is not None else {}),
                    **retry_kwargs)
            measured = self._dub_audio_duration(output_path)
            plan = {}
            if measured > duration + 1e-9:
                plan = plan_reflow(rows, seg.id, measured, total_duration=known_duration)
                if plan is None:
                    raise SpeechBudgetError("Không còn khoảng nghỉ phù hợp để căn đủ lời thoại.")
                if tail_limit is not None:
                    plan[seg.id]["dub_tail_limit"] = tail_limit
                for identity, bounds in plan.items():
                    resolve_dub_timing({**self.segments[identity].to_dict(), **bounds})
                start, end = plan[seg.id]["dub_start"], plan[seg.id]["dub_end"]
            if preserve_manual_timing and any(
                    (self.segments[sid].verification or {}).get("status") == "manual"
                    and (bounds["dub_start"], bounds["dub_end"]) != resolve_dub_timing(self.segments[sid].to_dict())
                    for sid, bounds in plan.items()):
                raise SpeechBudgetError("Căn lại sẽ thay đổi thời gian câu đã sửa tay; giữ nguyên bản sửa.")
            timing = await self._run_blocking(build_speech_timing, spoken.get("text", text), start, end,
                output_path, spoken["speed_ratio"], spoken["boundaries"])
            if self.is_stopped:
                raise asyncio.CancelledError
            current = {item.id: (item.revision, item.dub_start, item.dub_end) for item in self.segments.values()}
            if any(current.get(sid) != state for sid, state in revision.items()):
                raise SegmentEditConflict("Timeline đã thay đổi trong khi tạo giọng; hãy thử lại câu này.")
            # Appending a later source interval is independent of an earlier edit.
            # A newly discovered row near the fitted plan is a genuine conflict.
            affected_end = max([end, *[bounds["dub_end"] for bounds in plan.values()]])
            affected_start = min([start, *[bounds["dub_start"] for bounds in plan.values()]])
            if any(item.id not in revision and item.start < affected_end and item.end > affected_start
                   for item in self.segments.values()):
                raise SegmentEditConflict("Một câu nguồn mới trùng thời gian giọng đang tạo; hãy thử lại câu này.")
            for sid, update in staged.items():
                item = self.segments[sid]
                bounds = plan.setdefault(sid, dict(zip(("dub_start", "dub_end"), resolve_dub_timing(item.to_dict()))))
                data = update["spoken"]
                update["timing"] = await self._run_blocking(build_speech_timing,
                    data["text"], bounds["dub_start"], bounds["dub_end"], update["audio_path"],
                    data.get("timing_ratio", data["speed_ratio"]), data["boundaries"])
                update["old_audio_path"] = item.audio_path
                bounds["_audio_update"] = update
            if self.is_stopped:
                raise asyncio.CancelledError
            current = {item.id: (item.revision, item.dub_start, item.dub_end) for item in self.segments.values()}
            if any(current.get(sid) != state for sid, state in revision.items()):
                raise SegmentEditConflict("Timeline đã thay đổi trong khi căn các câu liền kề.")
            affected_end = max([end, *[bounds["dub_end"] for bounds in plan.values()]])
            affected_start = min([start, *[bounds["dub_start"] for bounds in plan.values()]])
            if any(item.id not in revision and item.start < affected_end and item.end > affected_start
                   for item in self.segments.values()):
                raise SegmentEditConflict("Một câu nguồn mới trùng thời gian các câu đang căn.")
            returned = True
            return spoken, timing, plan
        finally:
            if not returned:
                for update in staged.values():
                    Path(update["audio_path"]).unlink(missing_ok=True)

    def _publish_dub_plan(self, plan, focus_id):
        # No await: the whole block must become visible in one runtime snapshot.
        for identity, bounds in plan.items():
            item = self.segments[identity]
            old_start, _ = resolve_dub_timing(item.to_dict())
            delta = bounds["dub_start"] - old_start
            item.dub_start, item.dub_end = bounds["dub_start"], bounds["dub_end"]
            update = bounds.get("_audio_update")
            if update is not None:
                spoken = update["spoken"]
                old_text = item.final_vi
                item.final_vi = spoken["text"]
                item.audio_path = update["audio_path"]
                item.tts_duration, item.speed_ratio = spoken["tts_duration"], round(spoken["speed_ratio"], 2)
                for key, value in update["timing"].items():
                    setattr(item, key, value)
                if spoken["text"] != old_text:
                    item.verification = {**(item.verification or {}), "pacing": spoken["pacing_verification"],
                        "before_pacing": old_text, "translation_changed": True}
                    if item.verification.get("status") == "verified":
                        item.verification["status"] = "corrected"
                item.revision += 1
                item.audio_url = f"/api/streaming/audio/{self.task_id}/{item.id}?rev={item.revision}"
                continue
            if "dub_tail_limit" in bounds:
                item.dub_tail_limit = bounds["dub_tail_limit"]
            if identity == focus_id:
                continue
            item.subtitle_cues = [{**cue, "start": cue["start"] + delta,
                                   "end": min(item.dub_end, cue["end"] + delta),
                                   **({"words": [{**word, "start": word["start"] + delta,
                                                  "end": min(item.dub_end, word["end"] + delta)}
                                                 for word in cue["words"]]} if "words" in cue else {})}
                                  for cue in item.subtitle_cues]
            for key in ("speech_start", "speech_end"):
                value = getattr(item, key)
                if value is not None:
                    setattr(item, key, min(item.dub_end, value + delta))
            item.revision += 1
            if item.status in ("READY", "PLAYED") and item.audio_path and Path(item.audio_path).is_file():
                item.audio_url = f"/api/streaming/audio/{self.task_id}/{item.id}?rev={item.revision}"

    @staticmethod
    def _pacing_source_context(context):
        return [{**{key: row.get(key) for key in ("id", "start", "end", "text_zh", "asr_text",
                                               "source_needs_review", "source_truncated")},
                 **{name: deepcopy(row[name]) for name in SOURCE_METADATA_FIELDS if name in row}}
                for row in (context or []) if isinstance(row, dict) and "id" in row]

    @staticmethod
    def _pacing_following_audio_identity(rows, source_end):
        # Long retries may already have thousands of following WAVs. Retaining
        # their whole set for every failure grows quadratically; only their
        # measured identity needs to be compared before the finite recovery.
        digest, count = hashlib.sha256(), 0
        for row in rows:
            if row["start"] >= source_end and row.get("audio_duration"):
                identity = (row["id"], row["revision"], row.get("audio_path"), row["audio_duration"])
                digest.update(repr(identity).encode("utf-8"))
                count += 1
        return count, digest.hexdigest()

    def _remember_pacing_failure(self, seg, error, context):
        """Keep measured, independently checked evidence for one later retry."""
        required = getattr(error, "required_dub_duration", None)
        candidate = getattr(error, "candidate_text", None)
        proof = getattr(error, "pacing_verification", None)
        if type(required) in (int, float) and math.isfinite(required) and required > 0:
            _, _, capacity, _, _ = self._dub_fit_window(seg, self._dub_rows())
            seg.timing_issue = {
                "code": "TIMING_CONFLICT", "required_seconds": float(required),
                "available_seconds": capacity, "max_speed": self.aligner.max_speed,
                "remedy": "Rà lại cách diễn đạt giữ nguyên nghĩa hoặc chọn giọng khác; không cắt từ hay chồng tiếng.",
            }
        if (not self._chunked_source_started or (seg.verification or {}).get("status") == "manual"
                or isinstance(required, bool) or not isinstance(required, (int, float))
                or not math.isfinite(required) or required <= 0
                or not isinstance(candidate, str) or not candidate.strip()
                or candidate != seg.final_vi and (not isinstance(proof, dict)
                    or proof.get("status") != "verified" or proof.get("text") != candidate)):
            return
        rows, _, capacity, _, _ = self._dub_fit_window(seg, self._dub_rows())
        self._pacing_failures[seg.id] = {
            "required": float(required), "candidate": candidate, "proof": deepcopy(proof),
            "revision": seg.revision, "owner": self._visual_review_state(seg), "capacity": capacity,
            "context": self._pacing_source_context(context), "voice": self.voice,
            "segment_voice": self._segment_voice(seg),
            "engine": self.tts_engine, "tts_engine_name": self.tts_engine_name,
            "aligner": self.aligner, "max_speed": self.aligner.max_speed, "ref_audio": self.ref_audio,
            "following_audio": self._pacing_following_audio_identity(rows, seg.end),
        }

    def _pacing_failure_owned(self, seg, record):
        if (self.segments.get(seg.id) is not seg or seg.revision != record["revision"]
                or (seg.verification or {}).get("status") == "manual"
                or self._visual_review_state(seg) != record["owner"]
                or self.voice != record["voice"] or self.tts_engine is not record["engine"]
                or self._segment_voice(seg) != record.get("segment_voice", self.voice)
                or self.tts_engine_name != record["tts_engine_name"]
                or self.aligner is not record["aligner"] or self.aligner.max_speed != record["max_speed"]
                or self.ref_audio != record["ref_audio"]):
            return False
        current = {row["id"]: row for row in self._pacing_source_context(self._dialogue_context_before(seg))}
        return all(current.get(row["id"]) == row for row in record["context"])

    async def _recover_pacing_after_source(self):
        """One finite pass; retry only when later real WAVs prove enough room."""
        if (not self._chunked_source_started or not self._visual_prepass_complete
                or not self.is_running or self.is_stopped):
            return
        pending = sorted(tuple(self._pacing_failures), key=lambda sid: self.segments[sid].start
                         if sid in self.segments else float("inf"))
        for sid in pending:
            await self.pause_event.wait()
            if self.is_stopped or not self.is_running:
                raise asyncio.CancelledError
            record = self._pacing_failures.pop(sid, None)
            seg = self.segments.get(sid)
            if (record is None or seg is None or seg.status != "FAILED"
                    or seg.failed_stage not in {"TTS", "ALIGNING"}
                    or seg.audio_path or not self._pacing_failure_owned(seg, record)):
                continue
            rows, _, capacity, known, _ = self._dub_fit_window(seg, self._dub_rows())
            following = self._pacing_following_audio_identity(rows, seg.end)
            plan = plan_reflow(rows, sid, record["required"], total_duration=known)
            if (not following[0] or following == record["following_audio"] or capacity <= record["capacity"] + .001
                    or capacity + 1e-9 < record["required"] or plan is None
                    or any((self.segments[identity].verification or {}).get("status") == "manual"
                           and (bounds["dub_start"], bounds["dub_end"]) != resolve_dub_timing(self.segments[identity].to_dict())
                           for identity, bounds in (plan or {}).items())):
                continue
            logging.getLogger("pipeline").info(
                "PACING_FINAL_RECOVERY run_id=%s segment_id=%s required_seconds=%.6f capacity_seconds=%.6f",
                self.task_id, sid, record["required"], capacity)
            try:
                seg.error = None
                await self._synthesize_segment(seg, pacing_recovery=record)
                warning = f"Câu {sid + 1} chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
                self.warnings = [value for value in self.warnings if value != warning]
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if getattr(self, "_persistence_capacity_failed", False):
                    raise
                # An edit/review owns its new state. Never rewrite it with the
                # outcome of this older automatic retry.
                if self._pacing_failure_owned(seg, record):
                    seg.failed_stage, seg.status, seg.error = seg.status, "FAILED", str(error)
                    await self.emit("segment_update", seg.to_dict())
                logging.getLogger("errors").error(
                    "[%s] PACING_FINAL_RECOVERY_FAILED segment_id=%s error_type=%s",
                    self.task_id, sid, type(error).__name__)

    async def _synthesize_segment(self, seg, *, pacing_recovery=None, allow_pacing=True):
        """Generate playable audio without approving an uncertain translation."""
        raw_tts_wav = self.cache_dir / f"tts_{seg.id}_raw.wav"
        pending_path = self.segments_dir / f"pending_{seg.id}_{uuid.uuid4().hex}.wav"
        dub_plan = {}
        old_audio_path = seg.audio_path
        context = None
        if pacing_recovery is None:
            self._pacing_failures.pop(seg.id, None)
        try:
            if not seg.final_vi.strip():
                if seg.text_zh.strip() and not seg.needs_review:
                    raise RuntimeError("Dịch thuật trả về nội dung trống.")
                # An empty uncertain draft has nothing defensible to read.
                # Keep its review flag, but allow playback past its time span.
                seg.status = "READY"
                seg.timing_issue = None
                seg.failed_stage = None
                seg._retry_synthesis = False
                self.total_processed_duration += seg.duration
                await self.emit("segment_update", seg.to_dict())
                await self._update_ready()
                return
            self._ensure_tts_engine()
            if pacing_recovery and not self._pacing_failure_owned(seg, pacing_recovery):
                raise SegmentEditConflict("Lời thoại đã thay đổi trước khi căn lại; giữ bản sửa mới.")
            seg.status = "TTS"
            await self._segment_progress("tts", f"Đang tạo giọng đọc câu {seg.id + 1}")
            await self.emit("segment_update", seg.to_dict())
            loop = asyncio.get_running_loop()
            async def publish_stage(stage):
                if self.is_stopped:
                    raise asyncio.CancelledError
                if pacing_recovery and not self._pacing_failure_owned(seg, pacing_recovery):
                    raise SegmentEditConflict("Lời thoại đã thay đổi trong khi căn lại; giữ bản sửa mới.")
                seg.status = "ALIGNING" if stage == "ALIGNING" else "TTS"
                label = {"TTS": "Đang tạo giọng đọc", "ALIGNING": "Đang căn nhịp đọc",
                         "REWRITING": "AI đang rút gọn và kiểm tra nghĩa lời đọc"}[stage]
                await self._segment_progress("align" if stage == "ALIGNING" else "tts", f"{label} câu {seg.id + 1}")
                await self.emit("segment_update", seg.to_dict())
            def speech_stage(stage):
                asyncio.run_coroutine_threadsafe(publish_stage(stage), loop).result()
            async with self._tts_lock:
                context = self._dialogue_context_before(seg)
                if pacing_recovery and not self._pacing_failure_owned(seg, pacing_recovery):
                    raise SegmentEditConflict("Lời thoại đã thay đổi trước khi tạo giọng lại; giữ bản sửa mới.")
                # A voice override keeps the old valid WAV until its replacement
                # and metadata commit. Never overwrite the prior manifest file.
                final_path = self.segments_dir / (f"seg_{seg.id}_{uuid.uuid4().hex}.wav"
                    if seg.tts_voice_outdated and old_audio_path else f"seg_{seg.id}.wav")
                spoken, timing, dub_plan = await self._fit_dub(seg,
                    text=pacing_recovery["candidate"] if pacing_recovery else seg.final_vi,
                    source=seg.text_zh, output_path=pending_path,
                    translator=self.translator if allow_pacing and pacing_recovery is None
                        and (seg.verification or {}).get("status") != "manual" else None, on_stage=speech_stage,
                    context=context, neighbor_rescue=allow_pacing and pacing_recovery is None
                        and (seg.verification or {}).get("status") != "manual",
                    **({"pacing_verification": pacing_recovery["proof"], "preserve_manual_timing": True} if pacing_recovery else {}))
            # Fitting already emitted ALIGNING. Publish the complete WAV and
            # every changed timing without yielding to a concurrent edit/Stop.
            tts_dur, ratio, boundaries = spoken["tts_duration"], spoken["speed_ratio"], spoken["boundaries"]
            if self.is_stopped:
                raise asyncio.CancelledError
            if pacing_recovery and not self._pacing_failure_owned(seg, pacing_recovery):
                raise SegmentEditConflict("Lời thoại đã thay đổi trong khi thử căn lại; giữ bản sửa mới.")
            seg.status = "ALIGNING"
            pending_path.replace(final_path)
            publication = (self._durable_edit_publication(seg, dub_plan, final_path)
                           if seg.tts_voice_outdated else nullcontext())
            with publication:
                self._publish_dub_plan(dub_plan, seg.id)
                if spoken["text"] != seg.final_vi:
                    previous = seg.final_vi
                    seg.final_vi = spoken["text"]
                    proof = spoken["pacing_verification"]
                    seg.verification = {**(seg.verification or {}), "pacing": proof,
                        "before_pacing": previous, "translation_changed": True}
                    if not seg.needs_review and seg.verification.get("status") == "verified":
                        seg.verification["status"] = "corrected"
                        if self.review_summary.get("status") == "completed":
                            self.review_summary["verified"] = max(0, self.review_summary.get("verified", 0) - 1)
                            self.review_summary["corrected"] = self.review_summary.get("corrected", 0) + 1
                    seg.revision += 1
                seg.tts_duration = tts_dur
                seg.speed_ratio = round(ratio, 2)
                seg.audio_path = str(final_path.resolve())
                for key, value in timing.items():
                    setattr(seg, key, value)
                seg.audio_url = f"/api/streaming/audio/{self.task_id}/{seg.id}?rev={seg.revision}"
                seg.tts_voice_outdated = False
                seg.status = "READY"
                seg.timing_issue = None
                seg.failed_stage = None
                seg._retry_synthesis = False
                self.total_processed_duration += seg.duration
            for identity in dub_plan:
                if identity != seg.id:
                    await self.emit("segment_update", self.segments[identity].to_dict())
            await self.emit("segment_update", seg.to_dict())
            await self._update_ready()
            if any("_audio_update" in bounds for bounds in dub_plan.values()):
                self._refresh_review_counts()
                self._persist_if_enabled()
            for bounds in dub_plan.values():
                old = bounds.get("_audio_update", {}).get("old_audio_path")
                if old and Path(old).resolve().parent == self.segments_dir.resolve():
                    try:
                        Path(old).unlink(missing_ok=True)
                    except OSError:
                        pass  # A playing Windows audio handle can close later.
            if old_audio_path and Path(old_audio_path).resolve().parent == self.segments_dir.resolve():
                current_audio = Path(seg.audio_path).resolve() if seg.audio_path else None
                old_audio = Path(old_audio_path).resolve()
                if current_audio != old_audio:
                    try:
                        old_audio.unlink(missing_ok=True)
                    except OSError:
                        pass  # A playing Windows audio handle can close later.
        except (SpeechBudgetError, PacingReviewRejected) as error:
            if pacing_recovery is None:
                self._remember_pacing_failure(seg, error, context)
            raise
        finally:
            for sid, bounds in dub_plan.items():
                path = bounds.get("_audio_update", {}).get("audio_path")
                if path and self.segments[sid].audio_path != path:
                    Path(path).unlink(missing_ok=True)
            raw_tts_wav.unlink(missing_ok=True)
            pending_path.unlink(missing_ok=True)

    def _review_message(self):
        count = sum(s.needs_review for s in self.segments.values())
        summary = self.review_summary
        if summary.get("status") == "completed":
            return (f"AI đã kiểm tra {summary.get('checked', 0)} câu, tự sửa {summary.get('corrected', 0)} câu. "
                    + (f"{summary.get('manual')} câu do bạn sửa sau kiểm tra. " if summary.get("manual") else "")
                    + (f"Còn {count} câu chưa đủ bằng chứng nguồn; xem lý do trong Transcript." if count else "Bản dịch đã sẵn sàng."))
        if summary.get("status") in {"failed", "incomplete"}:
            return "AI kiểm tra lại chưa hoàn tất. Bản nháp được giữ; bấm AI kiểm tra lại để thử tiếp."
        return f"Có {count} câu chưa đủ bằng chứng nguồn. AI có thể kiểm tra lại và tự sửa bản dịch."

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
                    if getattr(self, "_persistence_capacity_failed", False):
                        raise
                    message = str(exc)
                    # SDK exception strings can contain prompts or credentials.
                    # Keep a useful task/stage/type diagnostic without raw text.
                    logging.getLogger("errors").error(
                        "[%s] Lỗi câu %s ở bước %s (%s)", self.task_id, seg_id + 1,
                        seg.status if seg else "UNKNOWN", type(exc).__name__,
                    )
                    if seg:
                        seg.failed_stage = seg.status
                        seg.status = "FAILED"
                        seg.error = message
                        await self.emit("segment_update", seg.to_dict())
                    if self._chunked_source_started:
                        # Recovery uses this queue too. A failed earlier row
                        # must not strand healthy later rows on a saved run.
                        warning = f"Câu {seg_id + 1} chưa tạo được giọng; giữ phần đã xong và tiếp tục các câu sau."
                        if warning not in self.warnings:
                            self.warnings.append(warning)
                        continue
                    self.error = message
                    await self.emit("error", {"message": self.error, "segment_id": seg_id})
                    break
                finally:
                    self.queue.task_done()
            if self.is_running and not self.is_stopped and self.queue.empty():
                await self._recover_pacing_after_source()
        except asyncio.CancelledError:
            self.is_stopped = True
            raise
        except Exception as exc:
            self.error = str(exc)
            logging.getLogger("errors").error(
                "[%s] QUEUE_WORKER_FAILED error_type=%s", self.task_id, type(exc).__name__)
            await self.emit("error", {"message": self.error})
        finally:
            self.is_running = False
            self._release_runtime()
            owned = [s for s in self.segments.values() if not self._chunked_source_started
                     or s.end <= max(self._visual_scanned_seconds, self._visual_completed_seconds) + .001]
            # A failed sentence keeps FAILED status, but must not strand the
            # entire remaining video behind the preview button. The user may
            # inspect its completed rows, Retry missing speech, or explicitly
            # process later intervals. Full export still refuses every gap.
            if self.translation_mode == "preview" and self._chunked_source_started and not self.is_stopped:
                self._preview_ready = bool(self._visual_completed_seconds > 0
                    and (not owned or any(s.status in {"READY", "PLAYED"} for s in owned)))
            failed = [s for s in owned if s.status == "FAILED"]
            if any(row["state"] != "COMPLETED" for row in self._chunk_jobs):
                self._startup_failed = True
            ready = sum(s.status in {"READY", "PLAYED"} for s in owned)
            self.progress.update(completed_segments=ready, total_segments=len(owned))
            if failed:
                self.progress["progress_pct"] = round(100 * ready / len(owned), 1)
            if failed and not self.is_stopped and not self.error:
                self.error = f"{len(failed)} câu chưa tạo được giọng. Phần đã xong được giữ; bấm Tiếp tục để thử lại."
                await self.emit("error", {"message": self.error, "segment_ids": [s.id for s in failed]})
            if (not self.is_stopped and not self.error and self.translation_mode == "preview"
                    and self._chunked_source_started):
                if any(s.status not in ("READY", "PLAYED") for s in owned):
                    self.error = "Bản xem trước còn câu chưa xử lý xong; phần đã lưu được giữ."
                    await self.emit("progress", self.get_progress())
                else:
                    self._preview_ready = True
                    await self.report_progress("preview", "Bản xem trước đã sẵn sàng. Có thể sửa lời và dịch toàn bộ.",
                                               None, completed_segments=len(owned), total_segments=len(self.segments))
            elif not self.is_stopped and not self.error:
                if self._chunked_source_started and not self._visual_prepass_complete:
                    self.error = "Chưa dịch hết video; các đoạn đã hoàn tất được giữ để tiếp tục."
                    self._startup_failed = True
                    await self.emit("progress", self.get_progress())
                else:
                    stage = self._review_message() if self.review_summary or any(s.needs_review for s in self.segments.values()) else "Dịch và lồng tiếng hoàn tất"
                    await self.report_progress("complete", stage, 100)
            else:
                await self.emit("progress", self.get_progress())
            await self.emit("finished", self.get_telemetry())

    def pause(self):
        self.is_paused = True
        self.pause_event.clear()
        self._persist_if_enabled()

    def resume(self):
        self.is_paused = False
        self.pause_event.set()
        self._persist_if_enabled()

    def shutdown(self):
        """Close idle projects without changing their completed/failed outcome.

        Explicit Stop remains a cancellation. Application shutdown only cancels
        work still owned by a running task; merely closing a completed project
        must not rewrite its durable outcome to STOPPED.
        """
        owners = [self.start_task, self.worker_task, self.review_task, self._chunk_followup_task,
                  getattr(self, "export_task", None), getattr(self, "auto_export_task", None),
                  *self.edit_tasks]
        if self.is_running or self.is_editing or any(owner is not None and not owner.done() for owner in owners):
            self.stop()
            return
        self._persist_if_enabled()
        self._release_runtime()
        if not any(task is not None and not task.done() for task in
                   (self.start_task, self.worker_task, self.review_task, self._chunk_followup_task, *self.edit_tasks)):
            if active_streaming_sessions.get(self.task_id) is self:
                active_streaming_sessions.pop(self.task_id, None)

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
        self._persist_if_enabled()
        self.pause_event.set()
        if self.start_task and not self.start_task.done():
            self.start_task.cancel()
        if self.worker_task and not self.worker_task.done():
            self.worker_task.cancel()
        if self.review_task and not self.review_task.done():
            self.review_task.cancel()
        if self._chunk_followup_task and not self._chunk_followup_task.done():
            self._chunk_followup_task.cancel()
        for task in tuple(self.edit_tasks):
            if not task.done():
                task.cancel()
        if not self.start_task and (not self.worker_task or self.worker_task.done()):
            self._release_runtime()
        if not any(task is not None and not task.done() for task in
                   (self.start_task, self.worker_task, self.review_task, self._chunk_followup_task, *self.edit_tasks)):
            if active_streaming_sessions.get(self.task_id) is self:
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
    translation_mode: str = "full",
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
        translation_mode=translation_mode,
    )
    active_streaming_sessions[task_id] = sess
    sess._persistence_enabled = True
    return sess
