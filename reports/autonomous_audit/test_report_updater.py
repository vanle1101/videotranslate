"""Artifact verification only. Fixtures never enter real runtime event history."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("audit_reporter", HERE / "report_updater.py")
reporter_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter_module)


class ReporterAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="artifact-check-", dir=HERE)
        self.directory = Path(self.temporary.name).resolve()
        # All automatic fixture cleanup is confined to this audit artifact directory.
        assert self.directory.is_relative_to(HERE.resolve())
        self.output = self.directory / "reports"
        self.reporter = reporter_module.Reporter(self.directory, self.output)

    def tearDown(self):
        self.temporary.cleanup()

    def append(self, events, complete=True):
        with self.reporter.event_file.open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event) + ("\n" if complete else ""))

    def test_partial_event_waits_and_restart_does_not_duplicate(self):
        event = {"event_id": "partial", "event": "ERROR", "error_type": "ConnectionError"}
        self.append([event], complete=False)
        self.reporter.update()
        self.assertEqual(len(self.reporter.state["runtime_errors"]), 0)
        with self.reporter.event_file.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        self.reporter.update()
        self.assertEqual(len(self.reporter.state["runtime_errors"]), 1)
        restarted = reporter_module.Reporter(self.directory, self.output)
        restarted.update()
        self.assertEqual(len(restarted.state["runtime_errors"]), 1)

    def test_fix_requires_linked_passing_test_and_regression_reopens(self):
        self.append([
            {"event_id": "fix", "event": "FIX_APPLIED", "fix_id": "fix-source", "incident_ids": ["SOURCE_PREP_FAILURE"]},
            {"event_id": "claimed", "event": "FIX_VERIFIED", "fix_id": "fix-source", "result": "PASS", "test_refs": ["source-test"]},
        ])
        self.reporter.update()
        self.assertEqual(self.reporter.state["fixes"]["fix-source"]["status"], "APPLIED_UNVERIFIED")
        self.append([{ "event_id": "testpass", "event": "TEST_RESULT", "test_id": "source-test", "result": "PASS" }])
        self.reporter.update()
        self.assertEqual(self.reporter.state["fixes"]["fix-source"]["status"], "VERIFIED_FIXED")
        self.append([{ "event_id": "testfail", "event": "TEST_RESULT", "test_id": "source-test", "result": "FAIL" }])
        self.reporter.update()
        self.assertEqual(self.reporter.state["fixes"]["fix-source"]["status"], "VERIFICATION_REGRESSED")
        self.assertNotIn("SOURCE_PREP_FAILURE", self.reporter.state["closed_group_ids"])

    def test_done_does_not_verify_output_and_missing_evidence_is_rejected(self):
        self.append([{ "event_id": "done", "event": "RUN_COMPLETED", "video_label": "SHORT", "status": "COMPLETED" }])
        live = self.reporter.update()
        self.assertFalse(live["verified_completed"])
        self.assertFalse(live["ordered_runs"][0]["actual_output_verified"])
        checks = {key: True for key in ("picture_valid", "audio_valid", "subtitles_valid", "duration_valid", "sync_valid")}
        checks["evidence_path"] = str(self.directory / "does-not-exist.json")
        self.append([{ "event_id": "fake-evidence", "event": "OUTPUT_VERIFIED", "video_label": "SHORT", "details": checks }])
        live = self.reporter.update()
        self.assertFalse(live["ordered_runs"][0]["actual_output_verified"])

    def test_dashboard_server_denies_nonpublic_state_and_mutation_routes(self):
        self.reporter.update()
        instance = reporter_module.server(self.output, 0)
        port = instance.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/live_progress.json", timeout=2) as response:
                self.assertEqual(response.status, 200)
            for path in ("/_reporter_state.json", "/runtime_events.jsonl", "/../../.env"):
                with self.assertRaises(urllib.error.HTTPError) as exception:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
                self.assertEqual(exception.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as exception:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/live_progress.json", method="POST"), timeout=2)
            self.assertEqual(exception.exception.code, 501)
        finally:
            instance.shutdown()
            instance.server_close()

    def test_diagnosis_and_isolated_test_never_claim_runtime_recovery(self):
        initial = self.reporter.original
        initial.mkdir(parents=True)
        (initial / "initial-findings.json").write_text(json.dumps({"incidents": [
            {"id": "SOURCE_PREP_FAILURE", "suspected_cause": "Unknown", "status": "OPEN_UNVERIFIED"}
        ]}), encoding="utf-8")
        proof = self.directory / "real-evidence.json"
        proof.write_text("{}", encoding="utf-8")
        self.append([
            {"event_id": "d", "event": "DIAGNOSIS", "incident_ids": ["SOURCE_PREP_FAILURE"],
             "root_cause": "Verified isolated code identity defect", "evidence_path": str(proof),
             "verification_scope": "isolated reproduction; production reload pending"},
            {"event_id": "f", "event": "FIX_APPLIED", "fix_id": "source"},
            {"event_id": "t", "event": "TEST_RESULT", "test_id": "test-source", "result": "PASS"},
            {"event_id": "it", "event": "FIX_TESTED", "fix_id": "source", "test_refs": ["test-source"]},
        ])
        live = self.reporter.update()
        history = json.loads((self.output / "error_history.json").read_text(encoding="utf-8"))
        self.assertEqual(history["groups"][0]["suspected_cause"], "Verified isolated code identity defect")
        self.assertEqual(history["groups"][0]["status"], "OPEN_UNVERIFIED")
        self.assertEqual(self.reporter.state["fixes"]["source"]["isolated_test_status"], "PASS")
        self.assertEqual(self.reporter.state["fixes"]["source"]["status"], "APPLIED_UNVERIFIED")
        self.assertFalse(live["verified_completed"])


if __name__ == "__main__":
    unittest.main()
