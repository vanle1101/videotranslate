# Applied fixes

Updated: 2026-10-10T00:43:08+07:00

Source/config repairs are authorized immediately. The inactivity gate is informational.
A fix remains unverified until explicit verification references passing recorded tests.

- **stable-source-fingerprint — APPLIED_UNVERIFIED**: Stable code fingerprint, fail-closed empty preview and safe source diagnostics; 85 isolated tests pass. Original desktop retains loaded code; production reload is pending.
  Files: core/streaming/source_revision.py, core/streaming/chunked_source.py, core/streaming/pipeline.py
  Linked tests: (none)
- **atomic-timing-publication — APPLIED_UNVERIFIED**: Publish fitted WAV atomically only after finite positive duration fits; 126 isolated tests pass. Production reload is pending.
  Files: core/engines/alignment/timing_aligner.py
  Linked tests: (none)
- **retire-unsafe-helpers — APPLIED_UNVERIFIED**: Retired four ad hoc helper scripts which bypassed semantics/pacing or removed prior output. CLI refuses before project imports with exit 2 and supported Studio instructions; importing is inert. Twelve isolated safety tests pass. No production session mutation, provider request or service restart.
  Files: scripts/complete_and_export.py, scripts/finalize_all_segments.py, scripts/synthesize_remaining.py, scripts/run_export.py, scripts/safe_legacy_helpers.py, tests/test_legacy_helper_safety.py
  Linked tests: (none)
- **retire-unsafe-e2e-helper — APPLIED_UNVERIFIED**: Retired run_task_e2e: removed fixed task recreation, READY-subset export and stale-output success paths. All five legacy helpers refuse before project imports with exit 2; 15 isolated CLI/import safety checks pass. No production session mutation or provider calls.
  Files: scripts/run_task_e2e.py, scripts/safe_legacy_helpers.py, tests/test_legacy_helper_safety.py
  Linked tests: (none)
