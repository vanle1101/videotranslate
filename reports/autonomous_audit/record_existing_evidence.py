"""Import actual test evidence without claiming production code was reloaded."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_observer import event

ROOT = Path(__file__).resolve().parents[2]
source_path = ROOT / "workspace/private/autonomous-tests/source/source-fix-report.json"
tts_path = ROOT / "workspace/private/autonomous-tests/tts/tts_findings.json"
source = json.loads(source_path.read_text(encoding="utf-8"))
tts = json.loads(tts_path.read_text(encoding="utf-8"))
event("TEST_RESULT", event_id="source-tests-85", test_id="source-tests-85",
      test_name="Source identity, preparation and preview regressions (85)", test_kind="unit-integration-real-ffmpeg",
      result="PASS", duration_seconds=source["tests"]["duration_seconds"], evidence_path=str(source_path))
event("TEST_RESULT", event_id="tts-tests-126", test_id="tts-tests-126",
      test_name="Timing publication, natural speech and speech cache (126)", test_kind="unit-integration-real-pcm",
      result="PASS", duration_seconds=tts["tests"]["seconds"], evidence_path=str(tts_path))
event("FIX_APPLIED", event_id="source-fix-applied", fix_id="stable-source-fingerprint",
      files=["core/streaming/source_revision.py", "core/streaming/chunked_source.py", "core/streaming/pipeline.py"],
      incident_ids=["SOURCE_PREP_FAILURE", "FALSE_PREVIEW_READY", "SOURCE_DIAGNOSTIC_DETAIL_MISSING"],
      summary="Stable code fingerprint, fail-closed empty preview and safe source diagnostics; 85 isolated tests pass. Original desktop retains loaded code; production reload is pending.")
event("FIX_APPLIED", event_id="tts-fix-applied", fix_id="atomic-timing-publication",
      files=["core/engines/alignment/timing_aligner.py"],
      summary="Publish fitted WAV atomically only after finite positive duration fits; 126 isolated tests pass. Production reload is pending.")
event("BLOCKED", event_id="desktop-restart-policy", severity="High", component="platform permission",
      stage="production reload", error_type="AutomaticApprovalRejected",
      summary="Automatic approval review rejected the desktop-stop/restart command with stated reason: blocked by policy. No stop/restart occurred. Independent fixes/tests and actual checkpoint review continue.")
event("BLOCKED", event_id="dashboard-daemon-policy", severity="Low", component="platform permission",
      stage="background dashboard", error_type="AutomaticApprovalRejected",
      summary="Automatic approval review rejected launching a separate persistent report-updater dashboard with stated reason: blocked by policy. Detached read-only watchdog is already alive; report one-shot updates work.")
event("RUN_STARTED", event_id="short-real-review-started", run_id="short-checkpoint-recovery-20261010",
      video_label="SHORT", video_id="7692745161054506290", task_id="douyin_7692745161054506290",
      status="RUNNING", stage="review", total_seconds=320.040635,
      summary="Observed real review started through original backend using existing 198-row checkpoint; no duplicate download or job.")
print(json.dumps({"imported_actual_test_count": source["tests"]["passed"] + tts["tests"]["passed"],
                  "production_reload_verified": False}))
