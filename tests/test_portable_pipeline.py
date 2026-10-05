"""CPU/Windows regression checks; no model download or network access."""
import asyncio
import math
import shutil
import struct
import subprocess
import tempfile
import time
import threading
import sys
import json
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import settings
from core.asr import load_whisper_model
from core.engines.translation.semantic_translator import SemanticTranslator
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.streaming.segmenter import AudioSegmenter
from core.streaming.export import HQExporter
from core.media_process import run_media


def write_wave(path, duration=1, silent=False):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        samples = [0 if silent else int(5000 * math.sin(i * 2 * math.pi * 440 / 16000))
                   for i in range(int(duration * 16000))]
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class PortablePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portable_pipeline_")
        self.root = Path(self.temp.name)
        self.patches = [patch.object(settings, "BASE_DIR", self.root),
                        patch.object(settings, "TEMP_DIR", self.root),
                        patch.object(settings, "OUTPUT_DIR", self.root)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def session(self, **kwargs):
        return StreamingPipelineSession("regression", self.root / "video.mp4", **kwargs)

    def test_defaults_come_from_machine_settings(self):
        session = self.session()
        self.assertEqual(session.asr_engine_name, settings.ASR_ENGINE)
        self.assertEqual(session.tts_engine_name, settings.TTS_ENGINE)
        self.assertEqual(session.faster_whisper.model_size, settings.WHISPER_MODEL_SIZE)
        self.assertIsNone(session.vieneu)
        self.assertEqual(session.initial_buffer_seconds, settings.INITIAL_BUFFER_SECONDS)

    def test_stop_waits_for_thread_before_removing_its_input(self):
        session = self.session()
        marker = self.root / "thread_input.wav"
        marker.write_bytes(b"in use")
        started, release = threading.Event(), threading.Event()

        def work():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test thread timeout")
            self.assertTrue(marker.exists())

        async def run():
            async def stage():
                try:
                    await session._run_blocking(work)
                finally:
                    marker.unlink()

            task = asyncio.create_task(stage())
            try:
                while not started.is_set():
                    await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.sleep(0.03)
                task.cancel()
                await asyncio.sleep(0.03)
                self.assertFalse(task.done())
                self.assertTrue(marker.exists())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(task.cancelled())
            self.assertFalse(marker.exists())

        asyncio.run(run())

    def test_ffmpeg_cancellation_reaps_process(self):
        processes = []
        original = subprocess.Popen

        def record(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with patch("core.media_process.subprocess.Popen", side_effect=record):
            with self.assertRaisesRegex(RuntimeError, "hủy"):
                run_media([sys.executable, "-B", "-c", "import time; time.sleep(20)"],
                          lambda: time.monotonic() - started > 0.1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNotNone(processes[0].poll())

    def test_stopped_completed_session_removes_only_its_generated_cache(self):
        session = self.session()
        segment = SegmentItem(0, 0, 1, 1)
        session.segments = {0: segment}
        segment_audio = session.segments_dir / "seg_0.wav"
        segment_audio.write_bytes(b"generated audio")
        unrelated = session.cache_dir / "user_notes.txt"
        unrelated.write_text("keep this", encoding="utf-8")
        session.stop()
        self.assertFalse(segment_audio.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(session.is_stopped)

    def test_cpu_model_uses_local_files_and_no_torch(self):
        local = self.root / "workspace" / "models" / "faster-whisper-test"
        local.mkdir(parents=True)
        (local / "config.json").write_text("{}")
        factory = Mock()
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=factory)}), \
                patch.object(settings, "DEVICE", "cpu"):
            load_whisper_model("test")
        self.assertEqual(factory.call_args.args, (str(local),))
        self.assertEqual(factory.call_args.kwargs["device"], "cpu")
        self.assertEqual(factory.call_args.kwargs["compute_type"], "int8")
        self.assertEqual(factory.call_args.kwargs["cpu_threads"], settings.ASR_CPU_THREADS)

    def test_cuda_failure_can_use_cpu(self):
        factory = Mock(side_effect=[RuntimeError("CUDA DLL missing"), "cpu model"])
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=factory)}):
            result = load_whisper_model("test", "cuda")
        self.assertEqual(result, "cpu model")
        self.assertEqual(factory.call_args.kwargs["device"], "cpu")

    def test_free_provider_does_not_use_existing_paid_keys(self):
        with patch.object(settings, "GEMINI_API_KEY", "existing-key"), \
                patch.object(settings, "DEEPSEEK_API_KEY", "existing-key"), \
                patch.object(settings, "OPENAI_API_KEY", "existing-key"):
            self.assertEqual(SemanticTranslator("free")._api_keys(), (None, None, None))

    def test_failed_translation_is_not_returned_as_chinese(self):
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "Internet"):
                SemanticTranslator("free")._fallback_translate("你好")

    def test_free_translation_keeps_the_complete_sentence(self):
        translator = SemanticTranslator("free")
        full_sentence = "Một câu rất dài vẫn phải được giữ đầy đủ tất cả nội dung."
        with patch.object(translator, "_fallback_translate", return_value=full_sentence):
            self.assertEqual(translator.translate_single_segment("你好", 1)["final_vi"], full_sentence)

    def test_silent_gaps_are_playable(self):
        session = self.session()
        session.total_duration = 20
        session.start_wall_time = time.time()
        first = SegmentItem(0, 5, 7, 2)
        first.status = "READY"
        session.segments = {0: first, 1: SegmentItem(1, 15, 18, 3)}
        session._recalculate_telemetry()
        self.assertEqual(session.playable_until, 15)
        session.segments[1].status = "READY"
        session._recalculate_telemetry()
        self.assertEqual(session.playable_until, 20)

    def test_worker_failure_stops_and_emits_error(self):
        events = []
        session = self.session(event_callback=lambda event, data: events.append((event, data)))
        session.total_duration = 2
        session.start_wall_time = time.time()
        session.is_running = True
        session.segments = {0: SegmentItem(0, 0, 2, 2)}

        async def run():
            await session.queue.put((0, 0))
            with patch.object(session, "_process_segment", new=AsyncMock(side_effect=RuntimeError("ASR failed"))):
                await session._worker_loop()

        asyncio.run(run())
        self.assertFalse(session.is_running)
        self.assertEqual(session.segments[0].status, "FAILED")
        self.assertIn("error", [event for event, _ in events])
        self.assertEqual(events[-1][1]["status"], "failed")

    def test_missing_optional_models_fall_back_and_start_failure_is_visible(self):
        events = []
        session = self.session(
            asr_engine_name="sensevoice", tts_engine_name="vieneu",
            event_callback=lambda event, data: events.append((event, data)),
        )
        with patch("core.streaming.pipeline.VieNeuEngine", return_value=Mock(is_available=False)), \
                patch.object(session, "_run_ffmpeg", new=AsyncMock(side_effect=RuntimeError("input missing"))):
            with self.assertRaisesRegex(RuntimeError, "input missing"):
                asyncio.run(session.start())
        self.assertIs(session.asr_engine, session.faster_whisper)
        self.assertIs(session.tts_engine, session.edge_tts)
        self.assertEqual(len(session.warnings), 2)
        self.assertFalse(session.is_running)
        self.assertEqual([event for event, _ in events], ["progress", "progress", "error", "finished"])
        self.assertEqual(events[0][1]["phase"], "prepare")
        self.assertIsNone(events[0][1]["progress_pct"])
        self.assertEqual(events[1][1]["status"], "FAILED")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_silent_unicode_input_is_not_a_speech_segment(self):
        audio = self.root / "âm thanh im lặng.wav"
        write_wave(audio, 1, silent=True)
        self.assertEqual(AudioSegmenter().segment_audio(audio), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_edge_output_is_real_wav_and_accepts_vieneu_preset(self):
        source = self.root / "nguồn.wav"
        mp3 = self.root / "source.mp3"
        destination = self.root / "tiếng Việt.wav"
        write_wave(source)
        subprocess.run(["ffmpeg", "-y", "-i", str(source), str(mp3)], capture_output=True, check=True)
        communicate = Mock()

        async def save(path):
            Path(path).write_bytes(mp3.read_bytes())

        communicate.save = save
        with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=communicate) as factory:
            EdgeTTSFallbackEngine().synthesize("Xin chào", destination, voice="Trúc Ly")
        self.assertEqual(factory.call_args.args[1], settings.EDGE_VOICE)
        with wave.open(str(destination)) as output:
            self.assertEqual(output.getframerate(), 24000)
            self.assertGreater(output.getnframes(), 0)
        self.assertFalse(list(self.root.glob("edge_tts_*")))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_streaming_keeps_all_asr_sentences_and_cleans_intermediates(self):
        audio = self.root / "hội thoại.wav"
        write_wave(audio, 2)
        events = []
        session = self.session(event_callback=lambda event, data: events.append((event, data)))
        session.video_path = audio
        session.faster_whisper.transcribe = Mock(return_value=[{"text_zh": "第一句。"}, {"text_zh": "第二句。"}])
        session.translator.translate_single_segment = Mock(return_value={"final_vi": "Xin chào tất cả."})
        session.edge_tts.synthesize = lambda text, output_path, **kwargs: write_wave(output_path)

        async def run():
            await session.start()
            await session.worker_task

        with patch.object(settings, "TTS_ENGINE", "edge-tts"), patch.object(settings, "ASR_ENGINE", "faster-whisper"), \
                patch.object(settings, "SUPPRESSION_MODE", "DSP_MONO_ADAPTIVE_FORMANT"):
            asyncio.run(run())
        self.assertIsNone(session.error)
        self.assertTrue(session.first_play_emitted)
        self.assertEqual(session.segments[0].text_zh, "第一句。 第二句。")
        self.assertTrue(Path(session.segments[0].audio_path).is_file())
        self.assertFalse(list(session.cache_dir.glob("slice_*.wav")))
        self.assertFalse(list(session.cache_dir.glob("tts_*_raw.wav")))
        self.assertFalse(session.raw_audio_16k.exists())
        self.assertIsNone(session.faster_whisper.model)
        self.assertEqual(session.suppression_stats["mode"], "DSP_MONO_ADAPTIVE_FORMANT")
        self.assertEqual(session.bgm_audio_path.suffix, ".ogg")
        progress = [data for event, data in events if event == "progress"]
        self.assertTrue({"prepare", "asr", "translate", "tts", "align", "complete"}.issubset({item["phase"] for item in progress}))
        self.assertIsNone(progress[0]["progress_pct"])
        self.assertEqual(progress[-1]["progress_pct"], 100)
        self.assertEqual(progress[-1]["status"], "COMPLETED")
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(session.bgm_audio_path),
        ], capture_output=True, check=True)
        self.assertEqual(json.loads(probe.stdout)["streams"][0]["codec_name"], "opus")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_long_speech_fits_slot_without_losing_translated_text(self):
        session = self.session()
        session.raw_audio_16k = self.root / "source.wav"
        write_wave(session.raw_audio_16k, 0.5)
        session.asr_engine = Mock(transcribe=Mock(return_value=[{"text_zh": "完整的句子"}]))
        text = "Giữ lại toàn bộ nội dung cuối câu."
        session.translator.translate_single_segment = Mock(return_value={"final_vi": text})
        session.tts_engine = Mock()
        session.tts_engine.synthesize = lambda text, output_path, **kw: write_wave(output_path, 2)
        segment = SegmentItem(0, 0, 0.5, 0.5)
        session.total_duration = 0.5
        session.segments = {0: segment}
        asyncio.run(session._process_segment(segment))
        duration = session.aligner.get_audio_duration(Path(segment.audio_path))
        self.assertLessEqual(duration, 0.5001)
        self.assertGreater(duration, 0.35)
        self.assertEqual(segment.final_vi, text)
        self.assertGreater(segment.speed_ratio, 1.15)
        self.assertTrue(session.warnings)

    def test_cancelled_final_render_preserves_previous_output(self):
        exporter = HQExporter()
        destination = self.root / "douyin_translated_regression_hq.mp4"
        destination.write_bytes(b"previous completed export")
        cancelled = False

        def compose(**kwargs):
            nonlocal cancelled
            kwargs["output_path"].write_bytes(b"partial render")
            cancelled = True

        with patch.object(settings, "SEPARATION_ENGINE", "none"), \
                patch("core.streaming.export.run_media"), \
                patch.object(exporter, "_assemble_voice_timeline"), \
                patch.object(exporter.mixer, "mix"), \
                patch.object(exporter.composer, "compose", side_effect=compose):
            with self.assertRaisesRegex(RuntimeError, "hủy"):
                exporter.export("regression", self.root / "video.mp4", [], 1,
                                cancel_check=lambda: cancelled)
        self.assertEqual(destination.read_bytes(), b"previous completed export")
        self.assertFalse(list(self.root.glob("hq_export_*")))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_offline_alignment_does_not_shorten_translation_by_deleting_words(self):
        aligner = self.session().aligner
        text = "Giữ nguyên đầy đủ hành động và ý nghĩa ở cuối câu."
        segment = {"id": 1, "start": 0, "end": 0.5, "final_vi": text}
        tts = SimpleNamespace(synthesize=lambda text, path, **kw: write_wave(path, 2))
        result = aligner.align_and_budget([segment], tts, None)
        self.assertEqual(segment["final_vi"], text)
        self.assertEqual(result[0]["final_vi"], text)
        self.assertLessEqual(aligner.get_audio_duration(Path(result[0]["audio_path"])), 0.5001)
        self.assertFalse(list(aligner.temp_dir.glob("*_raw.wav")))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_alignment_checks_actual_duration_for_short_sentence_slots(self):
        aligner = self.session().aligner
        source = self.root / "complete speech.wav"
        write_wave(source, 1)
        for duration in (0.2, 0.35, 0.9, 1.1):
            with self.subTest(duration=duration):
                destination = self.root / f"fitted_{duration}.wav"
                speed = aligner.apply_atempo(source, destination, 1 / duration, fit_duration=duration)
                self.assertGreaterEqual(speed, 1 / duration)
                self.assertLessEqual(aligner.get_audio_duration(destination), duration + 0.0001)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_timeline_keeps_silence_and_exact_segment_positions(self):
        speech = self.root / "speech.wav"
        write_wave(speech, 0.25)
        timeline = self.root / "timeline.wav"
        HQExporter._assemble_voice_timeline([
            {"start": 0.5, "end": 0.75, "audio_path": str(speech)},
            {"start": 1.25, "end": 1.5, "audio_path": str(speech)},
        ], 2, timeline)
        with wave.open(str(timeline)) as result:
            self.assertEqual(result.getnframes(), 88200)
            self.assertEqual(result.readframes(22050), bytes(22050 * 4))
            self.assertNotEqual(result.readframes(11025), bytes(11025 * 4))
            self.assertEqual(result.readframes(22050), bytes(22050 * 4))
            self.assertNotEqual(result.readframes(11025), bytes(11025 * 4))
            self.assertEqual(result.readframes(22050), bytes(22050 * 4))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_cpu_export_needs_no_roformer_and_removes_temp(self):
        video = self.root / "video tiếng Việt.mp4"
        speech = self.root / "giọng đọc.wav"
        write_wave(speech)
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x480:r=10:d=1",
            "-i", str(speech), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video),
        ], capture_output=True, check=True)
        with patch.object(settings, "SEPARATION_ENGINE", "dsp"):
            result = HQExporter().export("regression", video, [
                {"start": 0, "end": 1, "final_vi": "Xin chào", "audio_path": str(speech)}
            ], 1, mask_chinese=False)
        self.assertEqual(result["separation_engine"], "dsp")
        self.assertTrue(Path(result["final_video_path"]).is_file())
        self.assertFalse(list(self.root.glob("hq_export_*")))


if __name__ == "__main__":
    unittest.main()
