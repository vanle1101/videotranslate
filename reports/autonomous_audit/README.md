# Persistent audit artifacts

Start exactly one updater using the project Python, independently of the existing read-only watchdog:

```powershell
.\venv\Scripts\python.exe reports\autonomous_audit\report_updater.py --interval 60 --serve 8766
```

The dashboard is at `http://127.0.0.1:8766/`. The server exposes only sanitized report artifacts, binds localhost, and has no mutation route. The updater defaults to a maximum runtime of 24 hours; use `--max-runtime-seconds 0` for an explicitly indefinite run. It never invokes pipeline/task routes, repairs source, starts media jobs or restarts services. Root controls process launches and must stop the updater before replacing its final generated report.

Runner events go into `reports/autonomous_audit/runtime_events.jsonl`, UTF-8, one complete JSON object and newline per event. One writer is preferred; append and flush after each event. Partial trailing records are held until complete; each event should have a unique `event_id`. Nothing requires credentials, source dialogue, translations or full model payloads.

Common fields: `timestamp` (ISO 8601 with zone), `event`, `event_id`, `run_id`, `video_label` (`SHORT` then `LONG`), `video_id`, `task_id`, `stage`, `status`, `progress_pct`, `processed_seconds`, `total_seconds`, `elapsed_seconds`, `eta_seconds` (null if unknown), `completed_chunks`, `total_chunks`, `retry_chunks`, `ai_queue` (numeric counters), `segment_states`, `output_filename`.

Events accepted:

- `RUN_STARTED`, `PROGRESS`, `RUN_COMPLETED`: runner lifecycle and measured progress. `progress` can carry a nested pipeline snapshot with these same scalar fields.
- `ERROR` / `BLOCKED`: `error_type`, `severity`, `component`, `stage`, `summary`, `reason`, and sanitized diagnostic scalar fields under `details`. `BLOCKED` records an actual external limit; elapsed time alone is never treated as blocked.
- `FIX_APPLIED`: `fix_id`, `files`, `summary`, `incident_ids` (original diagnostic group IDs). This records `APPLIED_UNVERIFIED` only.
- `TEST_RESULT`: `test_id`, `test_name`, `test_kind`, `result` (`PASS` / `FAIL` / `SKIP`), `duration_seconds`, `evidence_path`. Reusing a test ID replaces its latest result, so regression invalidates verification.
- `FIX_VERIFIED`: `fix_id`, `result: "PASS"`, `test_refs` matching passing `test_id` entries, and optional `incident_ids`. Without those linked passing tests a fix cannot be marked verified.
- `FIX_ROLLED_BACK`: `fix_id`.
- `OUTPUT_VERIFIED`: under `details`, the measured booleans `picture_valid`, `audio_valid`, `subtitles_valid`, `duration_valid`, `sync_valid`, plus `evidence_path` pointing to an actual existing evidence file. All five checks and evidence are required. Extra fields such as `semantic_valid`, `subtitle_coverage_valid`, `subtitle_count`, `missing_segments` and `failed_segments` are supported.
- `BLOCKER_RESOLVED`: `incident_ids` referencing actual earlier `event_id` values plus an existing `evidence_path`. This records an external limit's demonstrated recovery; it does not claim a verified code fix.
- `BENCHMARK_RESULT`: `metrics` with measured numeric values and an optional brief `summary`. Never fabricate a comparison.

Initial evidence is linked from `workspace/audit/continuous-monitor`; live read-only samples come from `workspace/audit/continuous_monitor`. Counts distinguish diagnostic groups, task incident keys and logged occurrences. Existing source backup/patch/hash references remain linked. The previous 30-minute inactivity gate is explicitly informational after the user's immediate-repair authorization.

Frontend inspection: `static/app.js:1211` already renders actual pipeline stages, measured percentages, download bytes/speed/ETA, segment failure/retry counters, chunk counters, AI active/queued requests, speaker-frame progress and pipeline ETA. The separate dashboard adds ordered SHORT/LONG audit status, host resource/I/O/handle observations, honest fix/test counts and data age. Direct frontend console streaming and application-specific interaction timestamps remain unavailable to the read-only reporter.
