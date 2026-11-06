"""Actual artifact tests with explicitly synthetic media; never a SHORT PASS."""
import array
import copy
import datetime as dt
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid
import wave

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("independent_output_checker", HERE / "verify_output.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OutputCheckerAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = HERE / "qa" / ("checker-selftest-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
        cls.directory.mkdir(parents=True)
        assert cls.directory.resolve().is_relative_to(HERE.resolve())
        cls.source = cls.directory / "synthetic-source.mp4"
        cls.output = cls.directory / "synthetic-output.mp4"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg required for actual synthetic media tests")
        command = [ffmpeg, "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i", "testsrc2=size=128x96:rate=10:duration=2",
                   "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=16000:duration=2", "-c:v", "libx264", "-threads", "1",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cls.source)]
        subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        shutil.copyfile(cls.source, cls.output)
        cls.wav = cls.directory / "synthetic-utterance.wav"
        samples = array.array("h", [int(7000 * math.sin(2 * math.pi * 550 * index / 16000)) if 800 < index < 7200 else 0 for index in range(8000)])
        with wave.open(str(cls.wav), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(samples.tobytes())
        cls.subtitle = cls.directory / "synthetic.srt"
        cls.subtitle.write_text("1\n00:00:00,450 --> 00:00:00,850\nXin chào\n", encoding="utf-8")
        cls.row = {"id": 0, "start": .4, "end": 1, "status": "READY", "text_zh": "source fixture",
                   "asr_text": "source fixture", "final_vi": "Xin chào", "audio_path": str(cls.wav),
                   "tts_duration": .5, "revision": 0, "needs_review": False,
                   "verification": {"status": "verified", "source_supported": True, "semantic_verified": True,
                                    "second_pass_status": "completed", "review_gate_revision": 2},
                   "subtitle_cues": [{"start": .45, "end": .85, "text": "Xin chào"}]}
        cls.manifest = cls.directory / "synthetic-manifest.json"
        cls.manifest.write_text(json.dumps({"state": "READY", "task_id": "synthetic-only",
            "session": {"video_path": str(cls.source), "total_duration": 2, "output_filename": cls.output.name,
                        "caption_output_outdated": False, "review_summary": {"status": "completed"}}, "segments": [cls.row]}), encoding="utf-8")

    def checker(self):
        return module.Checker(self.manifest, self.output, self.directory / "cases", source=self.source,
                              subtitle_file=self.subtitle, frame_count=2, decode_timeout=10, deadline_seconds=60)

    def codes(self, checker):
        return {item["code"] for item in checker.failures}

    def test_real_synthetic_decode_frames_and_cues_do_not_establish_meaning(self):
        report = self.checker().check()
        self.assertTrue(report["full_decode_verified"])
        self.assertTrue(report["subtitles"]["sidecar_verified"])
        self.assertEqual(len(report["sampled_frames"]), 4)
        self.assertEqual(report["failures"], [])
        self.assertFalse(report["OUTPUT_VERIFIED"])
        self.assertEqual(report["status"], "PENDING_CONTENT_AUDIO_PERCEPTUAL_REVIEW")

    def test_tail_clipping_and_overlap_are_rejected(self):
        checker = self.checker()
        row = copy.deepcopy(self.row)
        row["end"] = .8  # PCM is 0.5 s, slot is 0.4 s.
        row["subtitle_cues"] = []
        checker.validate_row(row, {"0": row}, 2)
        self.assertIn("WAV_WOULD_BE_TAIL_CLIPPED_BY_DUB_SLOT", self.codes(checker))

    def test_known_source_empty_speech_is_rejected_even_when_status_ready(self):
        checker = self.checker()
        row = copy.deepcopy(self.row)
        row["final_vi"] = ""
        checker.validate_row(row, {"0": row}, 2)
        self.assertIn("EMPTY_KNOWN_SOURCE_SPEECH", self.codes(checker))

    def test_raw_tts_duration_is_compared_after_recorded_speed(self):
        checker = self.checker()
        row = copy.deepcopy(self.row)
        row.update(tts_duration=.5 * 1.15, speed_ratio=1.15)
        checker.validate_row(row, {"0": row}, 2)
        self.assertNotIn("RECORDED_TTS_DURATION_MISMATCH", self.codes(checker))
        row["tts_duration"] = 1.0
        wrong = self.checker()
        wrong.validate_row(row, {"0": row}, 2)
        self.assertIn("RECORDED_TTS_DURATION_MISMATCH", self.codes(wrong))

    def test_forged_speed_cannot_hide_duration_mismatch(self):
        checker = self.checker()
        row = copy.deepcopy(self.row)
        row.update(tts_duration=1.0, speed_ratio=2.0)
        checker.validate_row(row, {"0": row}, 2)
        self.assertIn("SPOKEN_WAV_SPEED_OUTSIDE_NATURAL_LIMIT", self.codes(checker))

    def test_stale_address_context_and_old_gate_are_rejected(self):
        checker = self.checker()
        row = copy.deepcopy(self.row)
        row["verification"].update(review_gate_revision=1, address_context_sources={"0": "old source"})
        checker.validate_row(row, {"0": row}, 2)
        self.assertIn("SEMANTIC_EVIDENCE_GATE_STALE_OR_MISSING", self.codes(checker))
        self.assertIn("SEMANTIC_EVIDENCE_CONTEXT_CHANGED", self.codes(checker))

    def test_subtitle_omitted_tokens_are_rejected(self):
        checker = self.checker()
        row = copy.deepcopy(self.row)
        row["final_vi"] = "Xin chào bạn"
        checker.validate_row(row, {"0": row}, 2)
        self.assertIn("SUBTITLE_CUES_OMIT_OR_DUPLICATE_SPOKEN_TEXT", self.codes(checker))

    def test_incomplete_or_misbound_review_receipt_is_rejected(self):
        checker = self.checker()
        receipt = self.directory / "intentionally-stale-review-receipt.json"
        receipt.write_text(json.dumps({"schema": 1, "scope": "FULL_SOURCE_AND_OUTPUT", "reviewer_type": "human",
            "reviewer_id": "intentionally-invalid-test-fixture", "reviewed_at": module.stamp(),
            "bindings": {"manifest_sha256": "wrong"}, "reviewed_segment_ids": [0], "unresolved_count": 0,
            "checks": {key: True for key in module.RECEIPT_CHECKS}, "evidence_files": [str(self.manifest)]}), encoding="utf-8")
        checker.receipt = receipt
        result = checker.review_receipt({"manifest_sha256": "current", "output_sha256": "actual", "source_sha256": "actual"}, [0])
        self.assertFalse(result["valid"])
        self.assertIn("INDEPENDENT_REVIEW_RECEIPT_INVALID_STALE_OR_INCOMPLETE", self.codes(checker))

    def test_ass_masks_do_not_count_as_subtitles(self):
        path = self.directory / "test-mask.ass"
        path.write_text("[Events]\nDialogue: 0,0:00:00.45,0:00:00.85,Caption,,0,0,0,,{\\p1}m 0 0 l 3 3{\\p0}\n"
                        "Dialogue: 1,0:00:00.45,0:00:00.85,Caption,,0,0,0,,{\\an5}Xin chào\n", encoding="utf-8")
        cues = module.Checker.parse_subtitles(path)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "Xin chào")

    def test_unpublished_stale_output_is_rejected(self):
        current = json.loads(self.manifest.read_text(encoding="utf-8"))
        current["session"]["output_filename"] = ""
        current["session"]["caption_output_outdated"] = True
        current["session"]["review_summary"] = {"status": "running"}
        path = self.directory / "stale-output-manifest.json"
        path.write_text(json.dumps(current), encoding="utf-8")
        checker = module.Checker(path, self.output, self.directory / "cases", source=self.source,
                               subtitle_file=self.subtitle, frame_count=2, decode_timeout=10, deadline_seconds=60)
        report = checker.check()
        self.assertFalse(report["OUTPUT_VERIFIED"])
        self.assertIn("OUTPUT_NOT_PUBLISHED_BY_CURRENT_MANIFEST", self.codes(checker))
        self.assertIn("CAPTION_OUTPUT_MARKED_STALE", self.codes(checker))
        self.assertIn("GLOBAL_SEMANTIC_REVIEW_INCOMPLETE", self.codes(checker))


if __name__ == "__main__":
    unittest.main()
