import shutil
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from config import settings

from core.engines.separator.roformer_engine import BSRoFormerSeparator
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
        self.separator = BSRoFormerSeparator()
        self.mixer = PremiumAudioMixer()
        self.sub_gen = SubtitleGenerator()
        self.composer = VideoComposer()

    def export(
        self,
        task_id: str,
        video_path: Path,
        segments: List[Dict[str, Any]],
        total_duration: float,
        mask_chinese: bool = True
    ) -> Dict[str, Any]:
        t0 = time.time()
        task_dir = settings.TEMP_DIR / f"hq_export_{task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        # 1. Extract Master Audio
        raw_audio = task_dir / "raw_audio.wav"
        import subprocess
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", str(raw_audio)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 2. BS-RoFormer Separation
        sep_dir = task_dir / "separated"
        vocals_path, instrumental_path = self.separator.separate(raw_audio, sep_dir)

        # 3. Assemble Voice Timeline
        voice_wav = task_dir / "voice_timeline.wav"
        valid_items = [s for s in segments if s.get("audio_path") and Path(s["audio_path"]).exists()]

        if valid_items:
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

            cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[out]", "-ac", "2", "-ar", "44100", str(voice_wav)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        else:
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(total_duration), str(voice_wav)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 4. Sidechain Audio Ducking
        master_audio = task_dir / "master_audio_ducked.wav"
        self.mixer.mix(
            instrumental_path=instrumental_path,
            voice_path=voice_wav,
            output_path=master_audio,
            total_duration=total_duration
        )

        # 5. Subtitles
        srt_path = task_dir / "subtitles.srt"
        ass_path = task_dir / "subtitles.ass"
        self.sub_gen.generate_srt(segments, srt_path)
        self.sub_gen.generate_ass(segments, ass_path)

        # 6. Render final TikTok 9:16 Video
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
        return {
            "output_filename": output_filename,
            "final_video_path": str(final_video_path.resolve()),
            "elapsed_seconds": elapsed
        }
