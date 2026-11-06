# Autonomous audit — current evidence

Updated: 2026-10-10T00:43:08+07:00
Status: **BLOCKED**

Observed audit elapsed: 1996.7 seconds. Actual video processing durations below come from runner events.
Diagnostic groups: 6; verified fixed: 0; unresolved: 6.
Per-task incident keys: 7; logged failure occurrences: 13; runner incident events: 2.

| Ordered video | State / stage | Progress | Elapsed / ETA (s) | Output independently verified |
| --- | --- | --- | --- | --- |
| SHORT 7692745161054506290 | FAILED / AI kiểm tra lại chưa hoàn tất. Bản nháp được giữ; bấm AI kiểm tra lại để thử tiếp. | None | 1867.9529999999995 / None | False |
| LONG 7666381909177470271 | WAITING / None | None | None / None | False |

Complete historical errors.log: 84 occurrences; 38 intentional test faults; 46 historical runtime failures.
These occurrences are not a count of current failed tasks. Evidence: historical_log_audit.json.

Root causes / important diagnostic groups:

- High **SOURCE_PREP_FAILURE** (OPEN_UNVERIFIED): Late source preparation validation, suppressor duration probe, runtime identity comparison or checkpoint JSON serialization. Exact cause unavailable because exception detail was omitted.
- High **FALSE_PREVIEW_READY** (OPEN_UNVERIFIED): The terminal preview branch treats any(nonready for s in owned) over an empty owned list as false and unconditionally sets _preview_ready=True despite the first interval source failure.
- Medium **SOURCE_DIAGNOSTIC_DETAIL_MISSING** (OPEN_UNVERIFIED): Exception logging at the source retry handler intentionally or accidentally omits diagnostic detail.
- Medium **HISTORICAL_PROJECT_INCOMPLETE** (OPEN_NEEDS_SCOPE_AND_MEDIA_VERIFICATION): Incomplete review or stale media publication; older PacingReviewRejected logs do not by themselves prove currently FAILED segment states.
- High **623c2b7ef93ddf7518b2** (OPEN_UNVERIFIED): runtime_error
- High **c198d26c9116d1375645** (OPEN_UNVERIFIED): VideoIntelligenceError

Modified source files:

- core/engines/alignment/timing_aligner.py
- core/streaming/chunked_source.py
- core/streaming/pipeline.py
- core/streaming/source_revision.py
- scripts/complete_and_export.py
- scripts/finalize_all_segments.py
- scripts/run_export.py
- scripts/run_task_e2e.py
- scripts/safe_legacy_helpers.py
- scripts/synthesize_remaining.py
- tests/test_legacy_helper_safety.py

Recorded actual tests:

- artifact-unit-integration: Audit reporter acceptance checks (4) — PASS (0.684 seconds); evidence: E:\Dịch video\reports\autonomous_audit\reporter_validation.json
- unit-integration-real-ffmpeg: Source identity, preparation and preview regressions (85) — PASS (6.39 seconds); evidence: E:\Dịch video\workspace\private\autonomous-tests\source\source-fix-report.json
- unit-integration-real-pcm: Timing publication, natural speech and speech cache (126) — PASS (14.77 seconds); evidence: E:\Dịch video\workspace\private\autonomous-tests\tts\tts_findings.json
- script-entry-point-regression: Retired helper CLI and import safety — PASS (15.25 seconds); evidence: E:\Dịch video\workspace\private\autonomous-tests\tts\helper_safety_tests.json
- script-entry-point-regression: Five retired helper CLI and import safety — PASS (7.26 seconds); evidence: E:\Dịch video\workspace\private\autonomous-tests\tts\helper_e2e_safety_tests.json
- offline-regression-isolated: python-offline — FAIL (418.328 seconds); evidence: E:\Dịch video\reports\autonomous_audit\regression-evidence.json
- offline-regression-isolated: python-qt-offscreen — PASS (22.687 seconds); evidence: E:\Dịch video\reports\autonomous_audit\regression-evidence.json
- offline-regression-isolated: node-ui-offline — PASS (2.937 seconds); evidence: E:\Dịch video\reports\autonomous_audit\regression-evidence.json

Remaining limitations:

- Reporter collects evidence; it never changes source, restarts services or calls task routes
- No ETA is invented; missing runner telemetry remains null
- Output verification requires measured picture/audio/subtitles/duration/sync checks and an evidence file
- Original frontend already shows stage, measured progress, download speed/ETA, segment/retry/chunk counts, AI queue and pipeline ETA
- Frontend lacks host I/O/handle metrics and the ordered audit comparison; this separate dashboard supplies available metrics
- Health sample stale: False
- Resource samples: 34; no optimization benefit claimed before measured comparison.
- External API/network restrictions are recorded only when an actual runner event supplies evidence.

Evidence and backup references:

- original_findings: E:\Dịch video\workspace\audit\continuous-monitor\initial-findings.json
- preexisting_patch: E:\Dịch video\workspace\audit\continuous-monitor\preexisting-source-changes.patch
- source_baseline_hashes: E:\Dịch video\workspace\audit\continuous-monitor\source-baseline-hashes.json
- initial_manifests: E:\Dịch video\workspace\audit\continuous-monitor\initial-evidence
- runtime_events: E:\Dịch video\reports\autonomous_audit\runtime_events.jsonl
