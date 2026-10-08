import tempfile
import time
import math
import wave
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from config import settings

from core.engines.separator.roformer_engine import BSRoFormerSeparator
from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from core.audio_ducking import PremiumAudioMixer
from core.subtitle import SubtitleGenerator
from core.video_composer import VideoComposer
from core.media_process import run_media
from core.subtitle_cues import build_caption_layout, normalize_caption_style
from core.streaming.audio_cache import (build_audio_cache_identity, load_audio_cache,
                                       prepare_audio_cache, commit_audio_cache, resolve_dub_timing)

class HQExporter:
    """
    Offline High-Quality Final Video Exporter.
    Combines configured separation, dynamic ducking, and ASS subtitles,
    preserving the source video dimensions and aspect ratio.
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
        cancel_check: Optional[Any] = None,
        screen_texts: Optional[List[Dict[str, Any]]] = None,
        publish_callback: Optional[Any] = None,
        caption_style: Optional[Dict[str, Any]] = None,
        source_screen_texts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix=f"hq_export_{task_id}_", dir=settings.TEMP_DIR) as temp_dir:
            return self._export(
                task_id, Path(video_path), segments, total_duration, Path(temp_dir),
                mask_chinese, progress_callback, cancel_check, screen_texts, publish_callback, caption_style, source_screen_texts,
            )

    def _export(self, task_id, video_path, segments, total_duration, task_dir,
                mask_chinese, progress_callback, cancel_check, screen_texts=None, publish_callback=None, caption_style=None, source_screen_texts=None):
        caption_style = normalize_caption_style(caption_style)
        if not math.isfinite(total_duration) or total_duration <= 0:
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

        _report(None, "Đang kiểm tra âm thanh đã xử lý để dùng lại...")
        audio_identity = build_audio_cache_identity(video_path, segments, total_duration,
                                                    self.mixer, self.suppressor, cancel_check)
        reused = load_audio_cache(audio_identity, task_dir, cancel_check)
        pending_audio = None
        if reused is not None:
            master_audio, audio_metadata = reused
            _report(None, "Đã dùng lại bản âm thanh đã kiểm tra; chỉ xuất lại hình và phụ đề...")
        else:
            master_audio, audio_metadata = self._mix_audio(
                video_path, segments, total_duration, task_dir, _report, _check_cancel, cancel_check)
            pending_audio = prepare_audio_cache(audio_identity, master_audio, audio_metadata, task_dir, cancel_check)
        separation_engine, warnings = audio_metadata["separation_engine"], audio_metadata["warnings"]

        # 5. Subtitles
        _report(None, "5/6 Tạo phụ đề tiếng Việt...")
        _check_cancel()
        srt_path = task_dir / "subtitles.srt"
        ass_path = task_dir / "subtitles.ass"
        subtitle_segments = [dict(s, vi_text=s.get("final_vi") or s.get("vi_text") or s.get("text_vi") or "") for s in segments]
        self.sub_gen.generate_srt(subtitle_segments, srt_path)
        video_size = self._video_size(video_path, cancel_check)
        caption_plan = build_caption_layout(subtitle_segments, screen_texts, video_size, caption_style=caption_style)
        if caption_style["blur_original"] and source_screen_texts is not None:
            # Placement can be bottom-only while blur still uses observed source
            # regions, matching the preview's caption_bottom_layout contract.
            caption_plan["source_masks"] = build_caption_layout(
                subtitle_segments, source_screen_texts, video_size,
                caption_style=caption_style).get("source_masks", [])
        self.sub_gen.generate_ass(
            subtitle_segments, ass_path,
            screen_texts=screen_texts,
            video_size=video_size,
            caption_layout=caption_plan,
        )

        # 6. Render final video at its original dimensions.
        _report(None, "6/6 Render MP4 với phụ đề và âm thanh tiếng Việt...")
        _check_cancel()
        output_filename = f"douyin_translated_{task_id}_hq.mp4"
        final_video_path = settings.OUTPUT_DIR / output_filename

        # Render into the temporary task directory. Failed/cancelled exports must
        # never leave a partial video in outputs or replace a previous result.
        rendered_video = task_dir / output_filename
        self.composer.compose(
            video_path=video_path,
            audio_path=master_audio,
            subtitle_path=ass_path,
            output_path=rendered_video,
            # Blur only approved OCR regions before drawing sharp Vietnamese
            # captions. An empty detection list must not create a blanket strip.
            mask_chinese_sub=False,
            cancel_check=cancel_check,
            source_masks=caption_plan.get("source_masks"),
        )
        _check_cancel()
        _report(None, "Đang kiểm tra hình ảnh, âm thanh và thời lượng video kết quả...")
        self.validate_output(rendered_video, total_duration, cancel_check)
        _check_cancel()
        if publish_callback:
            publish_callback(rendered_video, final_video_path)
        else:
            rendered_video.replace(final_video_path)

        # Cancelled or invalid video exports never publish a reusable mix. Once
        # the MP4 commit succeeded, cache I/O cannot change its success state.
        commit_audio_cache(audio_identity, pending_audio)

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

    def _mix_audio(self, video_path, segments, total_duration, task_dir, report, check_cancel, cancel_check):
        report(None, "1/6 Trích xuất âm thanh gốc...")
        check_cancel()
        raw_audio = task_dir / "raw_audio.wav"
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video_path), "-vn",
                   "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", str(raw_audio)], cancel_check)

        report(None, "2/6 Xử lý giọng gốc và âm thanh nền...")
        check_cancel()
        separation_engine, warnings = settings.SEPARATION_ENGINE, []
        if separation_engine == "roformer":
            self.separator = self.separator or BSRoFormerSeparator(settings.ROFORMER_MODEL)
            if self.separator.is_available:
                _, instrumental_path = self.separator.separate(raw_audio, task_dir / "separated")
            else:
                warnings.append("RoFormer chưa được cài; đã dùng bộ giảm giọng DSP.")
                separation_engine = "dsp"
        if separation_engine == "none":
            instrumental_path = raw_audio
        elif separation_engine != "roformer":
            if separation_engine not in ("dsp", "realtime"):
                warnings.append(f"Export dùng DSP thay cho {separation_engine}.")
            instrumental_path = task_dir / "background.wav"
            self.suppressor.process_file(raw_audio, instrumental_path,
                forced_mode=None if settings.SUPPRESSION_MODE == "AUTO" else settings.SUPPRESSION_MODE,
                cancel_check=cancel_check)
            separation_engine = "dsp"

        report(None, "3/6 Ráp timeline giọng đọc tiếng Việt...")
        check_cancel()
        voice_wav = task_dir / "voice_timeline.wav"
        self._assemble_voice_timeline(segments, total_duration, voice_wav, cancel_check)

        report(None, "4/6 Trộn giọng đọc và âm thanh nền...")
        check_cancel()
        master_audio = task_dir / "master_audio_ducked.wav"
        self.mixer.mix(instrumental_path=instrumental_path, voice_path=voice_wav,
                       output_path=master_audio, total_duration=total_duration, cancel_check=cancel_check)
        return master_audio, {"separation_engine": separation_engine, "warnings": warnings}

    @staticmethod
    def validate_output(path, expected_duration, cancel_check=None):
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("Chưa tạo được tệp video kết quả.")
        raw = run_media(["ffprobe", "-v", "error", "-show_entries",
                         "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
                        cancel_check, capture_output=True)
        try:
            metadata = json.loads(raw)
            duration = float(metadata["format"]["duration"])
            streams = metadata["streams"]
            video = next(item for item in streams if item.get("codec_type") == "video")
            valid = (math.isfinite(duration) and duration > 0
                     and abs(duration - expected_duration) <= max(0.5, expected_duration * 0.01)
                     and video.get("width", 0) > 0 and video.get("height", 0) > 0
                     and any(item.get("codec_type") == "audio" for item in streams))
        except (ValueError, TypeError, KeyError, StopIteration):
            valid = False
        if not valid:
            raise ValueError("Video kết quả thiếu hình, âm thanh hoặc thời lượng không khớp nguồn.")
        # Confirm that both media streams can actually be decoded before publishing.
        # A one-second probe is insufficient for long exports: a truncated or
        # corrupt tail can still pass while the user receives an unplayable
        # result.  Decode the complete output and discard the frames.  FFmpeg
        # remains cancellable through run_media, so Stop still interrupts this
        # validation instead of publishing a partial file.
        run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
                   "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], cancel_check)

    @staticmethod
    def _video_size(video_path, cancel_check=None):
        metadata = run_media([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
            "-of", "json", str(video_path),
        ], cancel_check, capture_output=True)
        try:
            stream = json.loads(metadata)["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
            rotation = float(stream.get("tags", {}).get("rotate", 0))
            for side_data in stream.get("side_data_list", []):
                rotation = float(side_data.get("rotation", rotation))
            if round(rotation / 90) % 2:
                width, height = height, width
            if width > 0 and height > 0:
                return width, height
        except (ValueError, TypeError, KeyError, IndexError):
            pass
        return 1080, 1920

    @staticmethod
    def _assemble_voice_timeline(segments, total_duration, output_path, cancel_check=None):
        """Write PCM sequentially; never open hundreds of decoders or hold a video in RAM."""
        rate, frame_bytes = 44100, 4
        total_frames = round(total_duration * rate)
        position = 0
        silence = bytes(rate * frame_bytes)

        def check_cancel():
            if cancel_check and cancel_check():
                raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")

        with wave.open(str(output_path), "wb") as output:
            output.setparams((2, 2, rate, 0, "NONE", "not compressed"))

            def pad_to(target):
                nonlocal position
                while position < target:
                    check_cancel()
                    frames = min(rate, target - position)
                    output.writeframesraw(silence[:frames * frame_bytes])
                    position += frames

            for item in sorted(segments, key=lambda row: resolve_dub_timing(row)[0]):
                check_cancel()
                dub_start, dub_end = resolve_dub_timing(item)
                if not item.get("audio_path"):
                    if item.get("final_vi") or item.get("text_vi"):
                        raise ValueError("Đoạn có bản dịch nhưng thiếu âm thanh lồng tiếng.")
                    continue
                if not Path(item["audio_path"]).is_file():
                    raise FileNotFoundError("Không tìm thấy âm thanh lồng tiếng. Hãy dịch lại video.")
                start = round(dub_start * rate)
                end = round(dub_end * rate)
                if end > total_frames + 1:
                    raise ValueError("Timeline lồng tiếng vượt quá thời lượng video.")
                end = min(total_frames, end)
                if start < position or end <= start:
                    raise ValueError("Timeline lồng tiếng bị chồng lấn hoặc có thời gian không hợp lệ.")
                pad_to(start)
                # Decode beyond the slot so oversize audio fails instead of
                # silently losing its final words. The extra bounded window
                # detects overflow even for malformed, hours-long inputs.
                slot_frames = end - start
                decoded = run_media([
                    "ffmpeg", "-v", "error", "-nostdin", "-i", str(item["audio_path"]),
                    "-t", str(slot_frames / rate + .1), "-f", "s16le", "-acodec", "pcm_s16le",
                    "-ar", str(rate), "-ac", "2", "pipe:1",
                ], cancel_check, capture_output=True)
                if not decoded or len(decoded) % frame_bytes:
                    raise ValueError("Không giải mã được âm thanh lồng tiếng hợp lệ.")
                decoded_frames = len(decoded) // frame_bytes
                if decoded_frames > slot_frames + 1:
                    raise ValueError(
                        f"Âm thanh câu {item.get('id', '?')} dài {decoded_frames / rate:.4f}s, "
                        f"vượt khung lồng tiếng {slot_frames / rate:.4f}s; cần căn lại trước khi xuất.")
                # One sample of resampler/float rounding may sit at the edge;
                # no speech-length truncation is permitted.
                decoded = decoded[:slot_frames * frame_bytes]
                output.writeframesraw(decoded)
                position += len(decoded) // frame_bytes
            pad_to(total_frames)
