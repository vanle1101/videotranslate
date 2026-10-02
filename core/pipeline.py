import asyncio
import os
import time
from pathlib import Path
from typing import Dict, Any, Callable, Optional, List
from config import settings
from core.downloader import VideoDownloader
from core.separator import AudioSeparator
from core.asr import ChineseASR
from core.translator import VideoTranslator
from core.tts import VietnameseTTS
from core.audio_ducking import AudioDucker
from core.subtitle import SubtitleGenerator
from core.video_composer import VideoComposer

class VideoTranslationPipeline:
    def __init__(self):
        self.downloader = VideoDownloader()
        self.separator = AudioSeparator()
        self.asr = ChineseASR()
        self.translator = VideoTranslator()
        self.tts = VietnameseTTS()
        self.ducker = AudioDucker()
        self.sub_gen = SubtitleGenerator()
        self.composer = VideoComposer()

    async def run(
        self,
        video_input: str, # URL or local file path
        voice: Optional[str] = None,
        mask_chinese: bool = True,
        custom_segments: Optional[List[Dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes the full automated translation pipeline:
        1. Download video
        2. Separate Vocals & BGM/SFX
        3. ASR Chinese
        4. Translate to Vietnamese (Time-budgeted)
        5. Generate Vietnamese TTS (Tempo-adjusted)
        6. Dynamic Audio Ducking
        7. Render Video + TikTok ASS Subtitles
        """
        def update_progress(percent: int, message: str):
            print(f"[{percent}%] {message}")
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    asyncio.create_task(progress_callback(percent, message))
                else:
                    progress_callback(percent, message)

        start_time = time.time()

        # Step 1: Download or locate video
        update_progress(10, "Đang tải video gốc / kiểm tra file...")
        video_info = self.downloader.download(video_input)
        task_id = video_info["id"]
        video_path = Path(video_info["file_path"])

        task_dir = settings.TEMP_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Step 2: Audio extraction and separation
        update_progress(25, "Đang trích xuất và tách riêng giọng thoại & nhạc nền (BGM/SFX)...")
        vocals_path, bgm_path = self.separator.separate(str(video_path), task_id)

        # Get total video duration
        total_duration = self.tts.get_audio_duration(vocals_path)
        if total_duration <= 0:
            total_duration = video_info.get("duration") or 60.0

        # Step 3: Chinese ASR
        if custom_segments:
            segments = custom_segments
            update_progress(45, "Sử dụng danh sách câu thoại đã chỉnh sửa...")
        else:
            update_progress(40, "Đang nhận dạng giọng nói tiếng Trung (ASR)...")
            segments = self.asr.transcribe(str(vocals_path))

        if not segments:
            # If no speech was detected, create at least one dummy or fallback
            segments = [{
                "id": 0, "start": 0.0, "end": min(total_duration, 5.0),
                "duration": min(total_duration, 5.0), "text": "（背景音）", "vi_text": "（Nhạc nền）"
            }]

        # Step 4: Translation
        update_progress(55, "Đang dịch sang tiếng Việt đời thường theo văn phong TikTok...")
        # Check if vi_text already present from custom edit
        needs_translation = any("vi_text" not in s or not s["vi_text"] for s in segments)
        if needs_translation:
            segments = self.translator.translate_segments(segments)

        # Save SRT & ASS subtitles
        update_progress(65, "Đang tạo phụ đề ASS & SRT chuẩn TikTok 9:16...")
        srt_path = task_dir / "subtitles.srt"
        ass_path = task_dir / "subtitles.ass"
        self.sub_gen.generate_srt(segments, srt_path)
        self.sub_gen.generate_ass(segments, ass_path)

        # Step 5: Vietnamese TTS
        update_progress(75, "Đang tổng hợp giọng lồng tiếng Việt & cân chỉnh nhịp điệu...")
        voice_wav = await self.tts.generate_full_dubbing(segments, total_duration, task_id)

        # Step 6: Audio Ducking
        update_progress(85, "Đang mix âm thanh: Audio Ducking nhạc nền và hiệu ứng...")
        master_audio = task_dir / "master_audio.wav"
        self.ducker.mix_and_duck(bgm_path, voice_wav, master_audio)

        # Step 7: Final Render
        update_progress(92, "Đang ghép video, xử lý che chữ cứng và burn phụ đề TikTok...")
        output_filename = f"tiktok_{task_id}.mp4"
        final_video_path = settings.OUTPUT_DIR / output_filename
        self.composer.compose(
            video_path=video_path,
            audio_path=master_audio,
            subtitle_path=ass_path,
            output_path=final_video_path,
            mask_chinese_sub=mask_chinese
        )

        elapsed = round(time.time() - start_time, 1)
        update_progress(100, f"Hoàn tất xuất sắc trong {elapsed}s!")

        return {
            "task_id": task_id,
            "title": video_info["title"],
            "video_path": str(final_video_path.resolve()),
            "output_filename": output_filename,
            "duration": total_duration,
            "segments": segments,
            "srt_path": str(srt_path.resolve()),
            "elapsed_seconds": elapsed
        }
