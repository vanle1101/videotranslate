import asyncio
import os
import time
from pathlib import Path
from typing import Dict, Any, Callable, Optional, List
from config import settings

from core.downloader import VideoDownloader
from core.engines.separator.roformer_engine import BSRoFormerSeparator
from core.engines.asr.sensevoice_engine import SenseVoiceEngine
from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.engines.translation.semantic_translator import SemanticTranslator
from core.engines.tts.vieneu_engine import VieNeuEngine
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.engines.alignment.timing_aligner import TimingBudgetAligner
from core.engines.subtitle_removal.propainter_engine import SmartSubtitleRemovalEngine
from core.audio_ducking import PremiumAudioMixer
from core.subtitle import SubtitleGenerator
from core.video_composer import VideoComposer
from core.task_controller import TaskController

class VideoTranslationPipeline:
    def __init__(self):
        self.downloader = VideoDownloader()
        self.separator = BSRoFormerSeparator()
        self.sensevoice_asr = SenseVoiceEngine()
        self.whisper_fallback_asr = FasterWhisperFallbackEngine()
        self.translator = SemanticTranslator()
        self.vieneu_tts = VieNeuEngine()
        self.edge_fallback_tts = EdgeTTSFallbackEngine()
        self.aligner = TimingBudgetAligner()
        self.subtitle_remover = SmartSubtitleRemovalEngine()
        self.mixer = PremiumAudioMixer()
        self.sub_gen = SubtitleGenerator()
        self.composer = VideoComposer()

    async def run(
        self,
        video_input: str,
        asr_engine_name: str = "sensevoice", # 'sensevoice' or 'whisper'
        tts_engine_name: str = "vieneu",     # 'vieneu' or 'edge-tts'
        voice: Optional[str] = "Trúc Ly",
        ref_audio: Optional[Path] = None,
        subtitle_mode: str = "auto",         # 'auto', 'fast', 'ai'
        custom_segments: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[int, str, Optional[str]], Any]] = None,
        task_controller: Optional[TaskController] = None
    ) -> Dict[str, Any]:
        """
        Executes the 12-stage premium automated translation and dubbing pipeline:
        1. Download & Verify Video (stage-download)
        2. Extract Master Audio (stage-extract)
        3. BS-RoFormer: Separate Vocals & Instrumental (stage-roformer)
        4. FunAudioLLM/SenseVoice: Chinese ASR + Emotion + Timestamp (stage-asr)
        5. VideoLingo Glossary & Context Extraction (stage-glossary)
        6. VideoLingo 3-Tier Reflection Translation (stage-translate)
        7. VieNeu-TTS v3 Turbo: Vietnamese speech synthesis / voice cloning (stage-tts)
        8. Timing Alignment: Enforces natural speech budget (stage-timing)
        9. Speech Timeline Assembly (stage-timeline)
        10. Premium Audio Mixing: Dynamic Sidechain Ducking (stage-ducking)
        11. Subtitle Treatment & ASS Generation (stage-subtitles)
        12. Final 9:16 TikTok Render (stage-render)
        """
        async def check_control():
            if task_controller:
                if task_controller.is_stopped:
                    raise asyncio.CancelledError("Tiến trình đã bị người dùng hủy.")
                await task_controller.pause_event.wait()

        async def update_progress(percent: int, message: str, stage_id: Optional[str] = None):
            print(f"[{percent}%][{stage_id or ''}] {message}")
            await check_control()
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(percent, message, stage_id)
                else:
                    progress_callback(percent, message, stage_id)

        start_time = time.time()

        # Stage 1: Download & Verify Video
        await update_progress(5, "Stage 1/12: Đang tải video gốc / kiểm tra file...", "stage-download")
        video_info = self.downloader.download(video_input)
        task_id = video_info["id"]
        video_path = Path(video_info["file_path"])

        task_dir = settings.TEMP_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Stage 2: Extract Master Audio
        await update_progress(12, "Stage 2/12: Đang trích xuất Master Audio...", "stage-extract")
        raw_audio = task_dir / "raw_audio.wav"
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", str(raw_audio)]
        import subprocess
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # Stage 3: BS-RoFormer Audio Separation
        await update_progress(22, "Stage 3/12: BS-RoFormer đang tách thoại Trung và nhạc nền/SFX...", "stage-roformer")
        separation_dir = task_dir / "separated"
        vocals_path, instrumental_path = self.separator.separate(raw_audio, separation_dir)

        # Stage 4: Chinese ASR (SenseVoice or Fallback)
        await update_progress(35, "Stage 4/12: FunAudioLLM/SenseVoice đang bóc tách tiếng Trung & cảm xúc...", "stage-asr")
        if custom_segments:
            segments = custom_segments
        else:
            if asr_engine_name == "whisper" and not self.sensevoice_asr.is_available:
                segments = self.whisper_fallback_asr.transcribe(vocals_path, language="zh")
            else:
                segments = self.sensevoice_asr.transcribe(vocals_path, language="zh")

        if not segments:
            segments = [{
                "id": 0, "start": 0.0, "end": 5.0, "duration": 5.0,
                "text_zh": "（背景音）", "text": "（背景音）", "emotion": "<|NEUTRAL|>", "speaker": None
            }]

        # Stage 5: VideoLingo Glossary Extraction
        await update_progress(45, "Stage 5/12: VideoLingo đang phân tích ngữ cảnh & trích xuất thuật ngữ...", "stage-glossary")
        full_transcript = " ".join([seg.get("text_zh", seg.get("text", "")) for seg in segments])
        glossary_info = self.translator._extract_context_and_glossary(full_transcript)

        # Stage 6: VideoLingo 3-Tier Reflection Translation
        await update_progress(55, "Stage 6/12: VideoLingo đang dịch phản chiếu 3 cấp độ (Literal -> Natural -> Final)...", "stage-translate")
        segments = self.translator.translate(segments, target_lang="vi")

        # Stage 7: TTS Engine Selection & Preparation
        tts_engine = self.vieneu_tts if tts_engine_name == "vieneu" else self.edge_fallback_tts
        await update_progress(68, f"Stage 7/12: {tts_engine.name} đang tổng hợp giọng lồng tiếng Việt...", "stage-tts")

        # Stage 8: Timing Budgeting & Alignment
        await update_progress(76, "Stage 8/12: Timing Aligner đang căn chỉnh nhịp điệu & kiểm tra time-budget...", "stage-timing")
        aligned_segments = self.aligner.align_and_budget(
            segments=segments,
            tts_engine=tts_engine,
            translation_engine=self.translator,
            speed_limits=(0.90, 1.15),
            voice=voice,
            ref_audio=ref_audio
        )

        # Stage 9: Speech Timeline Assembly
        await update_progress(82, "Stage 9/12: Đang ghép chuỗi thoại lồng tiếng lên master timeline...", "stage-timeline")
        voice_wav = task_dir / "vietnamese_voice_aligned.wav"
        total_duration = self.aligner.get_audio_duration(raw_audio) or 5.0
        self._assemble_voice_timeline(aligned_segments, total_duration, voice_wav)

        # Stage 10: Dynamic Sidechain Ducking
        await update_progress(88, "Stage 10/12: Sidechain Ducking - Tự động ép nhỏ nhạc nền khi có lời nói...", "stage-ducking")
        master_audio = task_dir / "master_audio_ducked.wav"
        self.mixer.mix(
            instrumental_path=instrumental_path,
            voice_path=voice_wav,
            output_path=master_audio,
            total_duration=total_duration
        )

        # Stage 11: Subtitle Treatment & ASS Generation
        await update_progress(94, "Stage 11/12: Đang xử lý che/xóa chữ phụ đề Trung & sinh phụ đề ASS...", "stage-subtitles")
        srt_path = task_dir / "subtitles.srt"
        ass_path = task_dir / "subtitles.ass"
        self.sub_gen.generate_srt(aligned_segments, srt_path)
        self.sub_gen.generate_ass(aligned_segments, ass_path)

        # Stage 12: Final 9:16 TikTok Render
        await update_progress(97, "Stage 12/12: Đang biên dịch video TikTok 9:16 H.264 AAC xuất sắc...", "stage-render")
        output_filename = f"tiktok_premium_{task_id}.mp4"
        final_video_path = settings.OUTPUT_DIR / output_filename

        self.composer.compose(
            video_path=video_path,
            audio_path=master_audio,
            subtitle_path=ass_path,
            output_path=final_video_path,
            mask_chinese_sub=True
        )

        elapsed = round(time.time() - start_time, 1)
        await update_progress(100, f"Hoàn tất toàn bộ pipeline cao cấp trong {elapsed}s!", "stage-render")

        return {
            "task_id": task_id,
            "title": video_info["title"],
            "video_path": str(final_video_path.resolve()),
            "output_filename": output_filename,
            "duration": total_duration,
            "segments": aligned_segments,
            "srt_path": str(srt_path.resolve()),
            "elapsed_seconds": elapsed,
            "glossary": glossary_info,
            "engines_used": {
                "separator": self.separator.name,
                "asr": self.sensevoice_asr.name if asr_engine_name == "sensevoice" else self.whisper_fallback_asr.name,
                "translation": self.translator.name,
                "tts": tts_engine.name,
                "mixer": "Sidechain Dynamic Ducking",
                "alignment": self.aligner.name
            }
        }

    def _assemble_voice_timeline(self, aligned_segments: List[Dict[str, Any]], total_duration: float, output_path: Path):
        """Assembles all aligned audio segment chunks at exact millisecond timeline positions."""
        import subprocess
        valid_items = [s for s in aligned_segments if "audio_path" in s and Path(s["audio_path"]).exists()]
        if not valid_items:
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(total_duration), str(output_path)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return

        inputs = []
        filter_parts = []
        mix_inputs = []

        for idx, item in enumerate(valid_items):
            inputs.extend(["-i", item["audio_path"]])
            delay_ms = int(item["start"] * 1000)
            filter_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
            mix_inputs.append(f"[a{idx}]")

        mix_str = "".join(mix_inputs) + f"amix=inputs={len(valid_items)}:dropout_transition=0:normalize=0[mixed]"
        filter_complex = f"{';'.join(filter_parts)};{mix_str};[mixed]atrim=0:{total_duration},apad=whole_dur={total_duration}[out]"

        cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-ac", "2", "-ar", "44100", str(output_path)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
