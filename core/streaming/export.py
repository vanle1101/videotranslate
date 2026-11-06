import tempfile
import time
import math
import wave
import json
from contextlib import contextmanager
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
from core.streaming.background_cache import (build_background_identity, load_background_cache,
                                            store_background_cache)
from core.streaming.prepared_background import load_prepared_background, prepared_background_identity
from core.streaming.output_gate import (final_output_metadata, missing_spoken_output_ids,
                                       missing_speech_message, invalid_dub_timing_ids)


# Standard RIFF stores the entire file-minus-eight size in an unsigned 32-bit
# field. Stereo PCM16 at 44.1 kHz crosses that limit after roughly 6.76 hours.
RIFF_MAX_DATA_BYTES = 0xFFFFFFFF - 36
MAX_CAPTURE_PCM_SECONDS = 30.0


def _voice_container(total_frames, frame_bytes=4):
    return "RF64" if total_frames * frame_bytes > RIFF_MAX_DATA_BYTES else "WAV"


@contextmanager
def _voice_pcm_writer(output_path, total_frames, rate):
    """Sequential writes, with 64-bit sizes when the measured timeline needs them."""
    if _voice_container(total_frames) == "RF64":
        import soundfile
        with soundfile.SoundFile(str(output_path), mode="w", samplerate=rate,
                                 channels=2, subtype="PCM_16", format="RF64") as output:
            yield lambda data: output.buffer_write(data, dtype="int16")
    else:
        # Keep conventional WAV for short clips and existing wave-based tools.
        with wave.open(str(output_path), "wb") as output:
            output.setparams((2, 2, rate, total_frames, "NONE", "not compressed"))
            yield output.writeframesraw


@contextmanager
def _decoded_voice_pcm(audio_path, slot_frames, rate, output_dir, cancel_check):
    """Only short utterances enter RAM; longer clips decode to an owned scratch file."""
    command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(audio_path),
               "-t", str(slot_frames / rate + .1), "-f", "s16le", "-acodec", "pcm_s16le",
               "-ar", str(rate), "-ac", "2"]
    if slot_frames / rate <= MAX_CAPTURE_PCM_SECONDS:
        decoded = run_media([*command, "pipe:1"], cancel_check, capture_output=True)
        if not decoded or len(decoded) % 4:
            raise ValueError("Không giải mã được âm thanh lồng tiếng hợp lệ.")
        yield len(decoded) // 4, iter((decoded,))
        return

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix="voice-decode-", suffix=".s16le",
                                         dir=output_dir, delete=False) as output:
            temporary = Path(output.name)
        run_media([*command, "-y", str(temporary)], cancel_check)
        size = temporary.stat().st_size
        if not size or size % 4:
            raise ValueError("Không giải mã được âm thanh lồng tiếng hợp lệ.")
        with temporary.open("rb") as source:
            def chunks():
                while data := source.read(rate * 4):
                    if cancel_check and cancel_check():
                        raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")
                    yield data
            yield size // 4, chunks()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

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
        allow_incomplete: bool = False,
        source_audio_fallback_spans: Optional[List[List[float]]] = None,
    ) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix=f"hq_export_{task_id}_", dir=settings.TEMP_DIR) as temp_dir:
            return self._export(
                task_id, Path(video_path), segments, total_duration, Path(temp_dir),
                mask_chinese, progress_callback, cancel_check, screen_texts, publish_callback, caption_style, source_screen_texts,
                allow_incomplete, source_audio_fallback_spans,
            )

    def _export(self, task_id, video_path, segments, total_duration, task_dir,
                mask_chinese, progress_callback, cancel_check, screen_texts=None, publish_callback=None, caption_style=None, source_screen_texts=None,
                allow_incomplete=False, source_audio_fallback_spans=None):
        caption_style = normalize_caption_style(caption_style)
        if not math.isfinite(total_duration) or total_duration <= 0:
            raise ValueError("Thời lượng video phải lớn hơn 0.")
        if not allow_incomplete and any(row.get("omit_voice", False) for row in segments):
            raise ValueError("Cần chọn xuất phần đã xử lý trước khi bỏ qua câu thiếu giọng đọc.")
        spoken_rows = [row for row in segments if not row.get("omit_voice", False)]
        missing = missing_spoken_output_ids(spoken_rows)
        if missing:
            raise ValueError(missing_speech_message(missing))
        if invalid_dub_timing_ids(spoken_rows, total_duration):
            raise ValueError("Timeline lồng tiếng bị chồng lấn hoặc có thời gian không hợp lệ.")
        fallback_spans = self._validated_source_audio_fallback_spans(
            source_audio_fallback_spans, total_duration) if allow_incomplete else []
        # A reusable mix cannot waive the current text and timeline checks.
        # Semantic uncertainty is retained as draft metadata, never a media gap.
        quality = final_output_metadata(segments)
        t0 = time.monotonic()

        def _report(pct: int, stage: str):
            if progress_callback:
                try:
                    progress_callback(pct, stage)
                except Exception:
                    pass

        def _check_cancel():
            if cancel_check and cancel_check():
                raise RuntimeError("Tác vụ xuất video HQ đã bị hủy bởi người dùng.")

        phase_timings = {}
        phase_started = time.monotonic()
        _report(None, "Đang kiểm tra âm thanh đã xử lý để dùng lại...")
        prepared_identity = prepared_background_identity(video_path, task_id, total_duration, cancel_check)
        # Source-audio restoration is specific to this incomplete draft and
        # must never share a full narration cache entry.
        audio_identity = (None if fallback_spans else build_audio_cache_identity(
            video_path, segments, total_duration, self.mixer, self.suppressor, cancel_check,
            background_variant=prepared_identity))
        reused = load_audio_cache(audio_identity, task_dir, cancel_check)
        phase_timings["audio_cache_lookup"] = round(time.monotonic() - phase_started, 3)
        pending_audio = None
        if reused is not None:
            master_audio, audio_metadata = reused
            _report(None, "Đã dùng lại bản âm thanh đã kiểm tra; chỉ xuất lại hình và phụ đề...")
            phase_timings["audio_mix"] = 0.
        else:
            phase_started = time.monotonic()
            master_audio, audio_metadata = self._mix_audio(
                video_path, segments, total_duration, task_dir, _report, _check_cancel, cancel_check,
                task_id=task_id, source_audio_fallback_spans=fallback_spans)
            # A packet can disappear or fail decoding after the identity check.
            # Never publish a fallback mix under the prepared-background key.
            if fallback_spans or prepared_identity != audio_metadata.get("background_interval_digest"):
                audio_identity = None
            pending_audio = prepare_audio_cache(audio_identity, master_audio, audio_metadata, task_dir, cancel_check)
            phase_timings["audio_mix"] = round(time.monotonic() - phase_started, 3)
        separation_engine, warnings = audio_metadata["separation_engine"], audio_metadata["warnings"]

        # 5. Subtitles
        phase_started = time.monotonic()
        _report(None, "5/6 Tạo phụ đề tiếng Việt...")
        _check_cancel()
        srt_path = task_dir / "subtitles.srt"
        ass_path = task_dir / "subtitles.ass"
        # Keep every supplied row in the subtitle track.  Partial exports mark
        # rows with ``omit_voice`` while retaining their translated text; the
        # voice assembler omits only those rows and leaves the timeline silent.
        subtitle_segments = [dict(s, vi_text=s.get("final_vi") or s.get("vi_text") or s.get("text_vi") or "") for s in segments]
        if allow_incomplete:
            for row in subtitle_segments:
                if row.get("omit_voice"):
                    # Failed TTS never produced measured speech cues. Render
                    # its saved text on source bounds without inventing audio
                    # alignment or changing the editable project.
                    for field in ("subtitle_cues", "speech_start", "speech_end", "dub_start", "dub_end", "dub_tail_limit"):
                        row.pop(field, None)
                    row["preview_is_draft"] = True
        self.sub_gen.generate_srt(subtitle_segments, srt_path)
        video_size = self._video_size(video_path, cancel_check)
        caption_plan = build_caption_layout(subtitle_segments, screen_texts, video_size, caption_style=caption_style)
        if caption_style["blur_original"]:
            # Placement can be bottom-only while blur still uses observed source
            # intervals, including the Vietnamese silence between two cues.
            from core.subtitle_cues import build_source_subtitle_masks
            caption_plan["source_masks"] = build_source_subtitle_masks(
                screen_texts if source_screen_texts is None else source_screen_texts,
                subtitle_segments, video_size, duration=total_duration)
        self.sub_gen.generate_ass(
            subtitle_segments, ass_path,
            screen_texts=screen_texts,
            video_size=video_size,
            caption_layout=caption_plan,
        )
        phase_timings["subtitle_generation"] = round(time.monotonic() - phase_started, 3)

        # 6. Render final video at its original dimensions.
        _report(None, "6/6 Render MP4 với phụ đề và âm thanh tiếng Việt...")
        _check_cancel()
        output_filename = f"douyin_translated_{task_id}_hq.mp4"
        final_video_path = settings.OUTPUT_DIR / output_filename

        # Render into the temporary task directory. Failed/cancelled exports must
        # never leave a partial video in outputs or replace a previous result.
        rendered_video = task_dir / output_filename
        phase_started = time.monotonic()
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
        phase_timings["render_mp4"] = round(time.monotonic() - phase_started, 3)
        _check_cancel()
        phase_started = time.monotonic()
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
        phase_timings["validate_publish"] = round(time.monotonic() - phase_started, 3)

        elapsed = round(time.monotonic() - t0, 1)
        if reused is None:
            from core.streaming.progress_eta import export_profile, record_validated_export
            record_validated_export(export_profile(video_size, caption_style), total_duration, elapsed)
        _report(100, "Xuất video hoàn tất thành công!")
        return {
            "output_filename": output_filename,
            "final_video_path": str(final_video_path.resolve()),
            "output_path": str(final_video_path.resolve()),
            "elapsed_seconds": elapsed,
            "phase_timings_seconds": phase_timings,
            "audio_cache_reused": reused is not None,
            "separation_engine": separation_engine,
            "warnings": warnings,
            "output_is_draft": quality["output_is_draft"],
            "output_quality_label": quality["output_quality_label"],
            "output_review_segment_ids": quality["output_review_segment_ids"],
            "content_review_state": quality["content_review_state"],
            "output_incomplete": any(row.get("omit_voice", False) for row in segments),
        }

    @staticmethod
    def _validated_source_audio_fallback_spans(spans, total_duration):
        if spans is None:
            return []
        if not isinstance(spans, (list, tuple)):
            raise ValueError("Khoảng khôi phục âm thanh nguồn không hợp lệ.")
        parsed = []
        for span in spans:
            if (not isinstance(span, (list, tuple)) or len(span) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in span)):
                raise ValueError("Khoảng khôi phục âm thanh nguồn không hợp lệ.")
            start, end = map(float, span)
            if start < 0 or end <= start or end > total_duration + 1 / 44100:
                raise ValueError("Khoảng khôi phục âm thanh nguồn vượt timeline.")
            parsed.append((start, min(end, total_duration)))
        parsed.sort()
        merged = []
        for start, end in parsed:
            if merged and start <= merged[-1][1] + 1 / 44100:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _background(self, video_path, total_duration, task_dir, report, check_cancel, cancel_check, task_id=None):
        check_cancel()
        prepared = load_prepared_background(video_path, task_id, total_duration, task_dir, cancel_check)
        if prepared is not None:
            report(None, "2/6 Dùng lại nền đã xử lý khi dịch; không tách giọng lại...")
            return prepared

        separation_engine, warnings = settings.SEPARATION_ENGINE, []
        if separation_engine == "roformer":
            self.separator = self.separator or BSRoFormerSeparator(settings.ROFORMER_MODEL)
            if not self.separator.is_available:
                warnings.append("RoFormer chưa được cài; đã dùng bộ giảm giọng DSP.")
                separation_engine = "dsp"
        elif separation_engine != "none":
            if separation_engine not in ("dsp", "realtime"):
                warnings.append(f"Export dùng DSP thay cho {separation_engine}.")
            separation_engine = "dsp"
        metadata = {"separation_engine": separation_engine, "warnings": warnings}
        identity = build_background_identity(video_path, total_duration, self.suppressor,
                                             cancel_check, engine=separation_engine)
        background = load_background_cache(identity, task_dir, cancel_check)
        if background is not None:
            report(None, "2/6 Dùng lại nền đã tách; chỉ ráp và trộn giọng Việt...")
            return background, metadata

        report(None, "1/6 Trích xuất âm thanh gốc...")
        check_cancel()
        raw_audio = task_dir / "raw_audio.wav"
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video_path), "-vn",
                   "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", "-rf64", "auto", str(raw_audio)], cancel_check)

        report(None, "2/6 Xử lý giọng gốc và âm thanh nền...")
        check_cancel()
        if separation_engine == "roformer":
            _, instrumental_path = self.separator.separate(raw_audio, task_dir / "separated")
        elif separation_engine == "none":
            instrumental_path = raw_audio
        else:
            instrumental_path = task_dir / "background.wav"
            self.suppressor.process_file(raw_audio, instrumental_path,
                forced_mode=None if settings.SUPPRESSION_MODE == "AUTO" else settings.SUPPRESSION_MODE,
                cancel_check=cancel_check)
        check_cancel()
        # Store immediately: subsequent render failure or narration edits do not
        # invalidate a fully validated source stem. A first run may download its
        # checkpoint during separation, so resolve the identity again then.
        if identity is None:
            identity = build_background_identity(video_path, total_duration, self.suppressor,
                                                 cancel_check, engine=separation_engine)
        store_background_cache(identity, instrumental_path, cancel_check)
        return instrumental_path, metadata

    def _mix_audio(self, video_path, segments, total_duration, task_dir, report, check_cancel, cancel_check,
                   task_id=None, source_audio_fallback_spans=None):
        instrumental_path, metadata = self._background(
            video_path, total_duration, task_dir, report, check_cancel, cancel_check, task_id)

        report(None, "3/6 Ráp timeline giọng đọc tiếng Việt...")
        check_cancel()
        voice_wav = task_dir / "voice_timeline.wav"
        self._assemble_voice_timeline(segments, total_duration, voice_wav, cancel_check)

        report(None, "4/6 Trộn giọng đọc và âm thanh nền...")
        check_cancel()
        master_audio = task_dir / "master_audio_ducked.wav"
        self.mixer.mix(instrumental_path=instrumental_path, voice_path=voice_wav,
                       output_path=master_audio, total_duration=total_duration, cancel_check=cancel_check)
        if source_audio_fallback_spans:
            report(None, "Giữ âm thanh gốc tại câu chưa tạo được giọng; bản xuất vẫn là nháp...")
            restored = task_dir / "master_audio_with_source_fallback.wav"
            predicates = "+".join(
                f"between(t,{start:.6f},{end:.6f})" for start, end in source_audio_fallback_spans)
            filter_complex = (
                f"[0:a:0]asetpts=PTS-STARTPTS,apad,atrim=duration={total_duration:.6f},"
                f"aformat=sample_rates=44100:channel_layouts=stereo,"
                f"volume=0:enable='not({predicates})'[source];"
                f"[1:a:0]asetpts=PTS-STARTPTS[master];"
                f"[master][source]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                f"atrim=duration={total_duration:.6f}[out]"
            )
            run_media(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-i", str(video_path), "-i", str(master_audio),
                       "-filter_complex", filter_complex, "-map", "[out]", "-ac", "2", "-ar", "44100",
                       "-c:a", "pcm_s16le", "-rf64", "auto", str(restored)], cancel_check)
            master_audio = restored
        return master_audio, metadata

    @staticmethod
    def validate_output(path, expected_duration, cancel_check=None):
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("Chưa tạo được tệp video kết quả.")
        raw = run_media(["ffprobe", "-v", "error", "-show_entries",
                         "format=duration:stream=codec_type,width,height,start_time,duration", "-of", "json", str(path)],
                        cancel_check, capture_output=True)
        metadata = {}
        duration = None
        video = None
        audio = None
        try:
            metadata = json.loads(raw)
            duration = float(metadata["format"]["duration"])
            streams = metadata["streams"]
            video = next(item for item in streams if item.get("codec_type") == "video")
            audio = next(item for item in streams if item.get("codec_type") == "audio")

            def covers_timeline(stream):
                # Container duration is the longest track. A truncated but
                # otherwise decodable audio/video track must not borrow that
                # duration from the other track and be published as complete.
                start = float(stream["start_time"])
                length = float(stream["duration"])
                return (math.isfinite(start) and math.isfinite(length) and length > 0
                        and abs(start) <= 0.5
                        and abs(length - expected_duration) <= 0.5
                        and abs(start + length - expected_duration) <= 0.5)

            valid = (math.isfinite(duration) and duration > 0
                     # A percentage tolerance would permit minutes of missing
                     # media on multi-hour output. Encoder/frame rounding does
                     # not grow with video length, so use an absolute bound.
                     and abs(duration - expected_duration) <= 0.5
                     and video.get("width", 0) > 0 and video.get("height", 0) > 0
                     and covers_timeline(video) and covers_timeline(audio))
        except (ValueError, TypeError, KeyError, StopIteration):
            valid = False
        if not valid:
            def measured(value):
                try:
                    number = float(value)
                except (TypeError, ValueError, OverflowError):
                    return "missing/invalid"
                return f"{number:.3f}s" if math.isfinite(number) else "missing/invalid"

            streams = metadata.get("streams", []) if isinstance(metadata, dict) else []
            video = video or next((item for item in streams if item.get("codec_type") == "video"), {})
            audio = audio or next((item for item in streams if item.get("codec_type") == "audio"), {})
            expected = measured(expected_duration)
            details = (f"expected={expected}; container.duration={measured(duration)}; "
                       f"video.start={measured(video.get('start_time'))}; "
                       f"video.duration={measured(video.get('duration'))}; "
                       f"video.size={video.get('width', 'missing')}x{video.get('height', 'missing')}; "
                       f"audio.start={measured(audio.get('start_time'))}; "
                       f"audio.duration={measured(audio.get('duration'))}")
            raise ValueError(
                "Video kết quả thiếu hình, âm thanh hoặc thời lượng không khớp nguồn. "
                f"Đo được: {details}.")
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
        if not math.isfinite(total_duration) or total_duration <= 0:
            raise ValueError("Thời lượng timeline phải là số hữu hạn lớn hơn 0.")
        rate, frame_bytes = 44100, 4
        total_frames = round(total_duration * rate)
        position = 0
        silence = bytes(rate * frame_bytes)

        def check_cancel():
            if cancel_check and cancel_check():
                raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")

        with _voice_pcm_writer(output_path, total_frames, rate) as write_pcm:

            def pad_to(target):
                nonlocal position
                while position < target:
                    check_cancel()
                    frames = min(rate, target - position)
                    write_pcm(silence[:frames * frame_bytes])
                    position += frames

            # Omitted rows are intentionally not validated for audio or timing
            # here.  Their subtitles remain in the render, while this interval
            # is padded with silence by the surrounding valid rows.
            voice_segments = [item for item in segments if not item.get("omit_voice", False)]
            for item in sorted(voice_segments, key=lambda row: resolve_dub_timing(row)[0]):
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
                with _decoded_voice_pcm(item["audio_path"], slot_frames, rate,
                                        Path(output_path).parent, cancel_check) as (decoded_frames, chunks):
                    if decoded_frames > slot_frames + 1:
                        raise ValueError(
                            f"Âm thanh câu {item.get('id', '?')} dài {decoded_frames / rate:.4f}s, "
                            f"vượt khung lồng tiếng {slot_frames / rate:.4f}s; cần căn lại trước khi xuất.")
                    # One resampler sample may sit at the edge. Stream all
                    # actual speech; no speech-length truncation is permitted.
                    remaining = slot_frames
                    for decoded in chunks:
                        check_cancel()
                        decoded = decoded[:remaining * frame_bytes]
                        write_pcm(decoded)
                        frames = len(decoded) // frame_bytes
                        position += frames
                        remaining -= frames
            pad_to(total_frames)
