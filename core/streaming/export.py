import tempfile
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from config import settings

from core.engines.separator.roformer_engine import BSRoFormerSeparator
from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from core.audio_ducking import PremiumAudioMixer
from core.subtitle import SubtitleGenerator
from core.video_composer import VideoComposer

class HQExporter:
    """
    Offline High-Quality Final Video Exporter.
    Combines BS-RoFormer CUDA separation, dynamic sidechain ducking,
    and ASS subtitle burn-in / Chinese masking into a 1080x1920 MP4.
    """
    def __init__(self):
        self.separator = None
        self.suppressor = RealtimeVocalSuppressor()
        self.mixer = PremiumAudioMixer()
        self.sub_gen = SubtitleGenerator()
        self.composer = VideoComposer()

    def export(
        self,
        task_id: str,
        video_path: Path,
        segments: List[Dict[str, Any]],
        total_duration: float,
        mask_chinese: bool = True,
        progress_callback: Optional[Any] = None,
        cancel_check: Optional[Any] = None
    ) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix=f"hq_export_{task_id}_", dir=settings.TEMP_DIR) as temp_dir:
            return self._export(
                task_id, Path(video_path), segments, total_duration, Path(temp_dir),
                mask_chinese, progress_callback, cancel_check,
            )

    def _export(self, task_id, video_path, segments, total_duration, task_dir,
                mask_chinese, progress_callback, cancel_check):
        if total_duration <= 0:
            raise ValueError("Thời lượng video phải lớn hơn 0.")
        t0 = time.time()

        def _report(pct: int, stage: str):
            if progress_callback:
                try:
                    progress_callback(pct, stage)
                except Exception:
                    pass

        def _check_cancel():
            if cancel_check and cancel_check():
                raise RuntimeError("Tác vụ xuất video HQ đã bị hủy bởi người dùng.")

        # 1. Extract Master Audio
        _report(10, "1/6 Trích xuất âm thanh gốc (Master Audio)...")
        _check_cancel()
        raw_audio = task_dir / "raw_audio.wav"
        import subprocess
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", str(raw_audio)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 2. Use the configured separator; the CPU profile needs no large AI model.
        _report(35, "2/6 Xử lý giọng gốc và âm thanh nền...")
        _check_cancel()
        sep_dir = task_dir / "separated"
        separation_engine = settings.SEPARATION_ENGINE
        warnings = []
        if separation_engine == "roformer":
            self.separator = self.separator or BSRoFormerSeparator(settings.ROFORMER_MODEL)
            if self.separator.is_available:
                _, instrumental_path = self.separator.separate(raw_audio, sep_dir)
            else:
                warnings.append("RoFormer chưa được cài; đã dùng bộ giảm giọng DSP.")
                separation_engine = "dsp"
        if separation_engine == "none":
            instrumental_path = raw_audio
        elif separation_engine != "roformer":
            if separation_engine not in ("dsp", "realtime"):
                warnings.append(f"Export dùng DSP thay cho {separation_engine}.")
            instrumental_path = task_dir / "background.wav"
            self.suppressor.process_file(raw_audio, instrumental_path)
            separation_engine = "dsp"

        # 3. Assemble Voice Timeline
        _report(55, "3/6 Ráp timeline giọng đọc tiếng Việt...")
        _check_cancel()
        voice_wav = task_dir / "voice_timeline.wav"
        valid_items = [s for s in segments if s.get("audio_path") and Path(s["audio_path"]).exists()]

        if valid_items:
            inputs = []
            filter_parts = []
            mix_inputs = []

            for idx, item in enumerate(valid_items):
                inputs.extend(["-i", str(item["audio_path"])])
                delay_ms = int(item["start"] * 1000)
                # Do not let a long voice segment overlap the next segment.
                slot_duration = max(0.01, float(item["end"]) - float(item["start"]))
                filter_parts.append(f"[{idx}:a]atrim=0:{slot_duration},adelay={delay_ms}|{delay_ms}[a{idx}]")
                mix_inputs.append(f"[a{idx}]")

            mix_str = "".join(mix_inputs) + f"amix=inputs={len(valid_items)}:dropout_transition=0:normalize=0[mixed]"
            filter_complex = f"{';'.join(filter_parts)};{mix_str};[mixed]atrim=0:{total_duration},apad=whole_dur={total_duration}[out]"

            cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-ac", "2", "-ar", "44100", str(voice_wav)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        else:
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(total_duration), str(voice_wav)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 4. Sidechain Audio Ducking
        _report(70, "4/6 Dynamic Sidechain Ducking & Foley Master...")
        _check_cancel()
        master_audio = task_dir / "master_audio_ducked.wav"
        self.mixer.mix(
            instrumental_path=instrumental_path,
            voice_path=voice_wav,
            output_path=master_audio,
            total_duration=total_duration
        )

        # 5. Subtitles
        _report(85, "5/6 Tạo phụ đề tiếng Việt ASS & Masking Hardsub...")
        _check_cancel()
        srt_path = task_dir / "subtitles.srt"
        ass_path = task_dir / "subtitles.ass"
        subtitle_segments = [dict(s, vi_text=s.get("final_vi") or s.get("vi_text") or s.get("text_vi") or "") for s in segments]
        self.sub_gen.generate_srt(subtitle_segments, srt_path)
        self.sub_gen.generate_ass(subtitle_segments, ass_path)

        # 6. Render final TikTok 9:16 Video
        _report(95, "6/6 Render hoàn chỉnh TikTok 9:16 MP4...")
        _check_cancel()
        output_filename = f"douyin_translated_{task_id}_hq.mp4"
        final_video_path = settings.OUTPUT_DIR / output_filename

        self.composer.compose(
            video_path=video_path,
            audio_path=master_audio,
            subtitle_path=ass_path,
            output_path=final_video_path,
            mask_chinese_sub=mask_chinese
        )

        elapsed = round(time.time() - t0, 1)
        _report(100, "Xuất video hoàn tất thành công!")
        return {
            "output_filename": output_filename,
            "final_video_path": str(final_video_path.resolve()),
            "output_path": str(final_video_path.resolve()),
            "elapsed_seconds": elapsed,
            "separation_engine": separation_engine,
            "warnings": warnings
        }
