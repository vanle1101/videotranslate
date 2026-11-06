# Douyin2TikTok AI Studio — deployment and test recovery report

**Status: PARTIAL.** The local desktop backend is running from this workspace with matching source/UI fingerprints, and the Python and JavaScript suites now pass. Product acceptance is still incomplete: both inspected projects remain DRAFT because their source coverage has unresolved intervals, and no unused local five-minute source was available for a clean E2E run.

## Runtime and deployment verification — 2026-11-06

- Branch: `codex/push-local-2026-11-05`; starting HEAD: `f6214985125f7c2844c9cb2256e85b93e86773d9`.
- No Studio process or backend listener was present when checked. The workspace Studio was then started in tray mode with `venv\Scripts\pythonw.exe -B desktop_app.py --start-in-tray`; no browser was opened or manipulated.
- Desktop launcher PID `22768`; backend listener PID `13696`, port `51908`. `venv\pyvenv.cfg` confirms that `E:\Train giọng\tools\python310\pythonw.exe` is the configured base interpreter for this workspace venv.
- `GET /api/health`: `status=ok`, FFmpeg and FFprobe present, `started_source_revision = disk_source_revision = 68c602f463c3042f0112a89335b7ec49a787ae1591a36925083de24def0dda26`, `source_matches_disk=true`.
- The live `GET /` page embeds the same source and backend revisions. This verifies the served Studio frontend build; the hidden tray window was not visually inspected.
- Ollama is idle (`/api/ps` returned no loaded models); no Studio job was running during inspection.

## The 52 baseline test failures

The first scoped full run had **5,026 passed, 52 failed, 5 skipped, 0 errors**. Failure tracebacks are preserved in `reports/autonomous-run/full-suite-failures-20261106.md`.

Root causes were grouped across 12 test modules:

- Host `.env` settings (`LLM_PROVIDER=ollama`, Piper selection, one-worker Ollama cap) leaked into tests written for OpenCode/Edge and multi-worker settings. Fixtures now set their own provider/engine/worker assumptions.
- Several expectations predated the current result contract: a prepared translation/draft without a validated MP4 must be `PREPARED`, not `COMPLETED`; the current start API defaults to full mode; the asset URL uses the source revision; review/progress callbacks are asynchronous and need explicit synchronization.
- A real pipeline defect remained: an empty translation could detach a previously saved WAV and publish the row as `READY`. The pipeline now preserves an existing WAV and leaves the row recoverable at `TRANSLATING`; an empty draft without prior audio remains reviewable and cannot pass the output gate.
- Progress accounting had counted ASR lookahead before its chunk entered the bounded translation queue. The denominator now counts only rows admitted to the current chunk.

Assertions were aligned only where the prior expectation contradicted the existing output gate/API behavior; tests were not removed and the product gate was not weakened.

## Verification

- Relevant 12-module rerun: **524 passed, 1 skipped, 19 subtests passed**.
- First post-fix full run exposed the two stale-WAV cases; the targeted regression group then passed **17 tests**.
- Final Python suite: **5,078 passed, 5 skipped, 0 failed/errors, 130 subtests passed** in 391.60 seconds. JUnit: `reports/autonomous-run/full-suite-final-20261106.xml`.
- Final narrow check after cleanup: **18 passed** (`test_history_api`, empty source readiness, and portable streaming regression).
- JavaScript UI suite: **290 passed**; `node --check static/app.js` and `git diff --check` passed. One expected negative-path UI test logs a rejected task-list read while passing.

## Saved projects and MP4s

| Project | Saved work observed through live API | Source coverage and controls | Existing output |
|---|---|---|---|
| `ac81cdd8` | 122 rows, all 122 translated with audio timing and subtitle cues; 8/8 chunks | One unresolved interval, `0.180–6.252s`; `can_retry=false`, `can_resume=false` | DRAFT; history link `/api/outputs/douyin_translated_ac81cdd8_hq.mp4` returns HTTP 200. FFprobe: 300.033s, H.264 video + AAC audio. |
| `c784f19f` | 187 rows, all 187 translated with audio timing and subtitle cues; saved checkpoint retained | Two unresolved intervals, `143.552–143.776s` and `282.504–282.632s`; Retry remains available, Resume is false | DRAFT; history link `/api/outputs/douyin_translated_c784f19f_hq.mp4` returns HTTP 200. FFprobe: 320.000s, H.264 video + AAC audio. |

Both projects remain `FAILED` at the processing lifecycle level and `DRAFT` at the result level; neither was relabeled complete. No ASR, translation, TTS, or render was started. Checkpoint file timestamps predate this verification; output files were not rewritten. The existing output link and media headers were confirmed, and the prior report records successful full decode of both files.

## E2E status and remaining acceptance gap

All five-minute media candidates under `workspace/inputs` were already linked to existing tasks. The only unused items were short test samples, so no clean five-minute E2E was run or claimed. Existing checkpoint restoration and unit tests are not a clean benchmark. End-to-end timing from a new source, content-quality review, and visible Studio window interaction remain unverified.

## Git and preserved data

No reset, merge, rebase, push, or deletion was performed. Source, test, and skill changes were committed locally as `cc7f546` (`Fix Studio runtime verification and recover tests`) on `codex/push-local-2026-11-05`. Untracked videos, models, recovery scripts, checkpoints, caches, outputs, and executables remain outside the commit.
