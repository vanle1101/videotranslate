# Recovery acceptance runner

Use the project venv and the **refreshed desktop backend's current port**:

Execution also compares the backend's source fingerprint captured at startup
with the current application code. A healthy older backend is insufficient;
a mismatch blocks mutations until Studio has been fully exited and reopened.

```powershell
.\venv\Scripts\python.exe scripts/acceptance_recovery.py --backend http://127.0.0.1:PORT --project 265796c3
```

This defaults to backup/checksum planning. Add `--execute` only when launching
the authorized recovery. Repeat `--project ID` to process selected projects
sequentially; the first failure stops the runner. Each ID is scoped to its own
saved source/version and stable IDs. The runner does not merge projects.

It records the exact original compressed/plain manifest, source checksum,
manual row text/timing/revision/speaker confirmation, and valid audio checksums
under `workspace/audit/acceptance_<UTC>_<PID>`. It verifies the backend source,
voice and engine match that saved project. A per-project exclusive runner lock
prevents two acceptance scripts from operating on the same ID. A stale lock
after a forced script termination must be inspected before removing it.

Execution uses the running backend's normal snapshot/retry/translate-full APIs.
It does not create a second in-process pipeline, delete caches, reset progress,
change provider/voice, request another full review, or use partial export.
Existing running tasks are observed until terminal. Full export must pass
`media_complete` and uses `/api/streaming/export-hq/jobs` plus status polling.
Unresolved semantic review remains a labeled draft with exact review IDs.

Acceptance rechecks manual rows, source bytes, timeline bounds and overlap;
records row timing and draft reasons; probes the published MP4 and decodes its
complete audio and video. Duration must match the source within 0.5 seconds.
This establishes media integrity, not semantic correctness or coverage of
untested languages/videos. Interrupting/timing out the runner leaves backend
ownership intact; it never sends Stop implicitly. Inspect backend tasks before
starting another run.

## Desktop restart findings (read-only inspection)

At inspection, localhost port 52939 belonged to PID 8004 (base Python redirect
child), parent 15960 (`E:\Dịch video\venv\Scripts\pythonw.exe -B desktop_app.py`).
All listed tasks were terminal with `can_stop=false` and `can_pause=false`.
Recheck PID/port/task state immediately before any later restart.

The existing process has **no external graceful shutdown/restart API**.
`Process.CloseMainWindow()` / WM_CLOSE enters `closeEvent`, which hides a live
tray application instead of terminating it. The single-instance local server
accepts only `ACTIVATE`/`PING`; launching another desktop just activates the old
instance. Ctrl+Q / tray “Thoát hoàn toàn” calls `request_exit()` and the normal
service drain, but requires native interaction. Do not misreport WM_CLOSE as a
successful restart or force-kill active work. No process was stopped by this
investigation.

After a separately authorized, verified termination, start using the existing
launcher or the project's venv `pythonw.exe -B desktop_app.py --start-in-tray` in a hidden
process. `scripts/check_runtime.py` should pass first. Discover its new dynamic
port from the new startup log/listener and verify health plus the async export
route in OpenAPI before running acceptance. Do not reuse the old port blindly.
