# Runtime QA report

Date: 2026-10-06, Asia/Saigon. **Overall verdict: FAIL / audit still in progress.**

## Scope and evidence rules

The user confirmed **Douyin2TikTok AI Studio**, specifically its download failure, in this repository. Seedream, Seedance, image-generation nodes, graph fan-in/fan-out and remote image uploads are **not applicable** to this application. The separate AI Video Workflow Studio was not modified.

Baseline commit: `dc9b567756d2b441427f41bdb08d436c09d112d4`. Runtime fixes were tested in the working tree and recorded in commit eb8cddf (later report-only corrections may follow). No UI redesign or new provider was added. Offline regression results are kept separate from actual desktop/provider runs. PASS is bounded to the exact scenario tested.

## Original failure and root cause

| Evidence | Captured value |
| --- | --- |
| Actual action | Paste Douyin link and click Bắt đầu dịch |
| URL | https://www.douyin.com/jingxuan?modal_id=7663686335764516115 |
| Production task | `6667481f`, started 19:13:19, failed 19:20:01 |
| State | UI and backend FAILED; download 27.5%; no source/output media |
| Actual bytes | 881,590,272 / 3,207,219,218 |
| Error | Kết nối tải video bị gián đoạn. Kiểm tra mạng rồi thử lại. |
| Provider | douyin-public; downstream ASR/AI never received a valid file |
| Lost evidence | Original HTTP status, exception/stack and response body were not retained; old partial was already deleted |

**Root cause established:** recovery and diagnostics were missing: the media read timeout was only six seconds, interruption became a generic network message, no safe reconnect/resume existed and cleanup deleted the partial file. The exact historical transport interruption cannot be recovered. This evidence does **not** establish that the user's network was broken.

A bounded live check resolved the same original-quality video (1080x1920, about2606.8 seconds). Two 1 MiB Range requests returned HTTP206/video-mp4, identical total size and matching overlapping524,288bytes. One response had no validator; the other supplied ETag and Last-Modified. No artifact was saved. **PASS for range access**. Full download evidence follows.

### Full original download retest: PASS

UI task `42b996fd`, downloader `9db952f3`, started20:32:11. At20:44:39 the real CDN stream failed at1,652,031,488bytes: `incomplete_read`, `MediaResponseError`, HTTP200, attempt1. Because no trusted validator was available, the worker safely restarted the same original rendition from zero. At21:01:04 it completed **3,207,219,218bytes**, passed worker ffprobe validation and began downstream extraction. **PASS for full production download and actual interruption recovery**, not validator-based Range resume or crash-resume. Saved original: `workspace/inputs/douyin-7663686335764516115.mp4`, HEVC1080x1920+AAC, **43:26.816**. Earlier three-hour claims were incorrect.

### Fresh translated output and Stop regression

`c06917ab` completed real ASR/OCR/OpenCode translation/review/Edge-TTS/export:14checked,12verified,2unresolved. `workspace/outputs/douyin_translated_c06917ab_hq.mp4` is11,968,957bytes, H264/AAC1920x1080,15.016667s; full audio/video FFmpeg decode passed. Segment10's rejected filler is now empty with no audio; segment8 remains uncertain. **PASS execution/media, FAIL semantic acceptance**. The first result-modal retest failed in Qt with “Unable to play media”; existing WebM conversion is now wired into the result player and passed the later live retest documented below.

Stopping long-source ASR exposed another real failure: `session.stop()` removed the active task before the native thread drained, so the UI reported no task while Whisper continued. User exit21:11:09 timed out; old backend finally ended21:25:24. Fixes add CANCELLING history before removal, cancellation checks at ASR segment boundaries, actual recognized-seconds progress, and propagation of a completed executor's CancelledError rather than endlessly re-awaiting it. Native model loading or an individual decoded segment cannot stop instantaneously. Offline regression passed; live native Stop retest pending.

Normal restart21:33:59, ready21:34:04 on62695. Through the native picker, `142bb90d` started21:36:52 using the first15seconds of the requested source (20,077,190bytes,15.05s). ASR finished21:37:02 with real progress. Real OpenCode Muse translation plus independent review produced8verified segments,0unresolved; Edge-TTS and automatic export completed around21:42:42. `workspace/outputs/douyin_translated_142bb90d_hq.mp4`:7,499,712bytes, H264/AAC1080x1920,15.05s; full FFmpeg audio/video decode PASS.

Actual UI **Mở video kết quả** first triggered automatic compatible-preview conversion, then played visibly to15/15seconds. Replay showed the first translated sentence below the original Chinese subtitle in its yellow box. Source/transcript remained intact. **PASS production result-button playback, including fallback**. Accessibility retained a stale “Unable to play media” label from the failed initial codec despite successful video playback; screenshot and advancing/finished seek time, not that stale label, establish playback. The verified final file remains original export quality; only the local preview is converted. This excerpt does not establish full43-minute translation acceptance, perfect OCR for all on-screen title regions, or perfect language style.

## Production UI scenarios

| Test and exact steps | Expected | Actual / verdict | Fix and retest |
| --- | --- | --- | --- |
| Original download: inspect failed task, UI retry same source, then Dừng hẳn | Real byte progress and stop without false success | Task `b62d21ca`, 19:35:15-19:35:49, reached1.4%, STOPPED, partial removed, controls restored. **PASS cancellation; original full transfer NOT VERIFIED** | Recovery changes covered by transport regression tests; full 3.21GB retest subsequently passed; see above |
| Native file picker -> select `workspace/inputs/douyin-preview-7688769264395767049.mp4` -> Bắt đầu dịch with AI review, OpenCode Muse and Hoài My -> await auto export | Real ASR/OCR, provider translation/review, TTS, playable MP4 | Task `f8500b88` started19:37:53;14segments;10verified/2corrected/2unresolved. Real12MB MP4 H2641920x1080+AAC,15.016667s; full FFmpeg decode passed; preview reached15s. **PASS execution/media; FAIL final semantic quality** | One unresolved line spoke diagnostic 'nghe chưa rõ'. Placeholder guard and stale-audio removal added; clean-run retest pending below. Baseline used already-loaded modules; not proof of final code |
| Click Mở video kết quả in desktop after export | Play saved MP4 | No new playback/window: **FAIL reproduced** | `target=_blank` had no Qt createWindow handler. Link now uses in-app result player; actual retest pending |
| Tray -> Thoát hoàn toàn -> normal launcher/runtime check -> reopen -> inspect tasks and select same file | Preserve saved task/transcript and allow rerun | User exited20:10:41; log confirms clean shutdown20:10:42. Normal runtime check/desktop_app launch20:11:47; UI ready20:11:57 on49891. Settings retained, MP4 retained, **task history empty**. **FAIL session persistence** | Settings persistence works; task/session restoration remains unimplemented. Do not equate tray hiding with restart persistence |
| Fresh app -> native picker -> same15s file -> Bắt đầu -> while real Muse request active click Dừng hẳn -> inspect UI/backend/process -> Bắt đầu again | Cancel owned operation, no late success, retry available | Task `129449dc`: PROVIDER_CANCELLED20:14:46; no OpenCode child left; backend STOPPED and UI restored Start. New task `c06917ab` started20:15:03. **PASS local cancellation/retry start** | Remote provider cancellation is not exposed by CLI; verified local CLI/poll cancellation only. Old task remained stopped while next run progressed |
| Running task -> Diagnostics -> inspect Tác vụ logs -> Sao chép Log | Current run logs visible, copy works | Actual `129449dc`/`c06917ab` timestamps visible; UI confirmed Đã sao chép log Tác vụ. **PASS** | Prior test-generated lines also exist in original logs; real evidence is identified by run IDs, not test labels |
| New clean run `c06917ab` -> real provider/review/TTS/export/result playback | Final artifact from updated code | **PASS execution/file decode, FAIL semantics and Qt MP4 playback** | Details and new fallback above; two unresolved lines retained |

The restart command was initially rejected by automatic approval review. The user then exited through the application; no process-kill workaround was used. The actual graceful exit is evidenced by application logs.

## Fixes and regression evidence

| Root cause | Files changed | Correction | Retest scope |
| --- | --- | --- | --- |
| Media interruption loses partial and cause | `core/douyin_resolver.py`, `core/download_worker.py`, `core/downloader.py` | Separate30s media read timeout; bounded retries; validate Range/If-Range/Content-Range/identity; restart when validators unsafe; verify total size/media; guarded checkpoint; cancellation cleanup; safe typed diagnostics | Real bounded CDN check, real UI cancellation, offline interrupted transport/status/resume cases; full original transfer/recovery subsequently passed; validator-based resume remains unverified |
| Resume resolver fallback could append another rendition | worker/downloader | Refuse fallback over retained original partial; preserve retry candidate without claiming it was validated; propagate status/type/bytes | Regression suite; production interruption across fresh process not yet verified |
| Failed second review could appear complete | `core/translation_review.py`, `core/streaming/pipeline.py`, `main.py`, frontend | Review failure/incomplete stays visible, blocks final export, permits retry | Offline provider-failure and state tests; actual provider failure injection not run |
| Diagnostic fillers reached spoken text | `core/video_intelligence.py`, review/pipeline/frontend | Reject spoken diagnostic filler; keep source and unresolved reason; remove stale TTS/cues when review clears text; empty final text cannot revive discarded draft; literal hearing-related dialogue remains allowed | Source frame/OCR verified;122review/video-intelligence tests and73transcript tests; fresh real run pending |
| Missing Vietnamese fields could return source text to TTS | `core/engines/translation/semantic_translator.py` | Validate required nonempty Vietnamese result and reject untranslated CJK | Offline contract regression |
| Settings mutable during consumers | `main.py` | Reject settings changes409 while processing/editing/exporting | API regression; no user credential modified |
| Provider call lacked run correlation/cancellation | `core/runtime_context.py`, OpenCode client, pipeline | Context propagated into worker threads; task/request/model/attempt/timing logs; cancel and reap owned CLI | Real cancellation above plus offline CLI cases |
| Gemini test falsely accepted empty/blocked result | `main.py` | Require usable response/finish state | Offline schema regression; Gemini real request not run |
| Fixed export percentages and no decoded-output guard | `core/streaming/export.py`, `main.py` | Indeterminate stage progress, validated nonzero media/streams/duration/decode before atomic publication and100%; cancel checked before publish | Actual baseline MP4 full decode, exporter integration/invalid-output tests; final new-code export pending |
| WS gaps, late pause/export replies, unintended autoplay | `static/app.js` | Reconnect+serialized snapshots; revisions/run guards; state from backend; replay does not autoplay; serialized export polls; preserve failed export | Offline UI lifecycle tests; actual desktop reconnection not yet exercised |
| Result link fails in Qt | frontend/template and preview endpoint | In-app saved-result player; stop source audio; unload result on close; preserve transcript; reuse compatible WebM preview | Actual `142bb90d` result-button playback through15seconds PASS; offline stale-request/cancel/codec tests |
| SenseVoice temp collision/leak | `core/engines/asr/sensevoice_engine.py` | Unique temp directory and finally cleanup | Offline concurrency/failure tests; optional SenseVoice real inference not run |

## Execution path audited

Desktop file picker / shared-text URL normalization -> start-local or start-url API -> task session -> downloader subprocess and validated local input -> audio extraction/background audio -> ASR -> OCR -> OpenCode CLI request -> parsed/validated translation -> independent semantic review and evidence checks -> Edge-TTS -> fitted segment audio/caption timing -> runtime snapshot/WebSocket -> HQ exporter -> validated MP4 -> output route -> preview.

No remote image/video generation adapter exists here. Media stays local for ASR/OCR; the selected OpenCode text path consumes extracted text/evidence, rather than sending local file URLs as remotely accessible assets. Cancellation is cooperative across the session/thread boundary; the owned CLI is reaped. A cancelled result is not published to a later run. UI progress for operations without a measurable percentage is indeterminate. Translation completion and verified meaning are separate; unresolved content remains explicitly flagged.

## REAL PROVIDER TESTS PERFORMED

- OpenCode `muse-spark-1.3-contributor-free`: real translation + independent review in `f8500b88`; output parsed and translated audio/video produced. **Execution PASS, semantic acceptance FAIL (2unresolved lines)**.
- OpenCode same model: real in-flight cancellation `129449dc`; local CLI terminated; no result published. **PASS**.
- Edge-TTS Hoài My: real synthesis in baseline, included in decoded exported MP4. **PASS for baseline**.
- Fresh updated-code runs `c06917ab` and `142bb90d`: real provider calls and validated MP4s; latest requested-source excerpt passed result playback and all8automatic sentence checks.
- Douyin public media: real range requests plus full3.21GB production UI download after actual interruption. **PASS full transfer/recovery and cancellation**; validator-based resume remains unverified.
- Seedream / Seedance: **N/A**, absent from confirmed application.

## Automated checks

Final integrated offline run: **992passed,1skipped,1deselected,125subtests passed**,58.45seconds. Skip: Windows symlink privilege. One legacy backend lifecycle test explicitly deselected. Seven standalone/manual legacy files excluded before import because they start servers/models or use historical local assets; exclusions are not counted as passes.

Earlier failures were stale exporter fixtures omitting required audio/duration metadata; the validator remained enabled. A later integrated run found two ASR mocks missing the new progress callback and one downloader request-start deadline exceeded under concurrent ASR load. Updated mocks assert a callable callback; download test now allows30seconds for process startup, still requires cancellation within5seconds, and always joins its worker in cleanup. Focused26tests and the full final run passed. **Node163passed**. Final syntax: **116Python files,5JavaScript files passed**. Runtime dependency/FFmpeg/OpenCode readiness passed before normal desktop launch. No TypeScript/npm production build exists in this Python/Qt application; those checks are N/A, not claimed PASS. Four deprecation warnings concern websockets/uvicorn compatibility; no lint tool is configured or reported as passing.

## Remaining issues / unverified acceptance

- Full source download `42b996fd` completed after safe restart, and latest Qt result playback passed. Native ASR cancellation still needs retesting with the latest executor fix.
- Full restart loses editable task/transcript runtime state, although settings and exported media persist.
- Baseline source contains unresolved speech; a valid playable MP4 does not prove correct translation. Latest requested-source excerpt has eight verified sentences; earlier source remains unresolved as documented above.
- Actual UI drag/drop, crash-resume, reconnect/stale-event races, invalid key/model,429/5xx, deleted-source and network fault injection have not all been performed. SenseVoice was used as reviewer evidence in the baseline sidecar, but a primary SenseVoice-ASR run was not performed. Offline coverage is not a manual QA pass.
- Cache-off/on/force-rerun acceptance was not performed as a separate matrix; fresh session IDs rerun ASR/OCR/provider, while compatible media preview may reuse a local cache.
- No full audit success is claimed while these runtime cases remain failing/unverified.

## Finalization status

The current15s result `142bb90d` is retained with its review sidecar; original source and user media remain unchanged. User was asked to exit via the tray so the latest executor-cancellation fix can load; that live Stop retest is pending. No test server or watcher is intentionally left running; the normal Studio application remains open for the user.

Automatic policy review rejected cleanup of the superseded task-generated `f8500b88` MP4/sidecar and task-created pytest temporary directories220–222, returning only “blocked by policy”. The cleanup command did not execute; those files remain. No alternative deletion route was attempted. No backup repository or dependency reinstall was created.
