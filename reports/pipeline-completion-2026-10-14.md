# Pipeline repair and acceptance record

Status: implementation and verification in progress. This is not a completion claim.

Authoritative objective: Codex attachment `goal-objective.md` dated 2026-10-14.
Recovery baseline: `workspace/update_backups/goal-fix-20261014-231531`.
Existing staged/unstaged changes and manually edited projects are retained.

## Required outcomes

- [ ] Per-video AI concurrency 20 by default; 1/3/5/10/15/20/30/40/50 presets persisted; two independent videos can each reach selected capacity; native resources separately limited.
- [ ] Source-language routing, ASR boundary fix and CPU OOM fallback verified in the main pipeline, including Korean with Chinese captions, mixed speech and silence.
- [ ] AI batches have <=8 targets and 3 context neighbors; two schema repairs then recursive splitting; stable source IDs and user-edit protection.
- [ ] One bounded six-attempt transport burst, then durable >=300-second cooldown, unlimited bursts for transient failures; pause/stop/restart tested.
- [ ] Native-language pacing uses <=5 distinct candidates; original video duration/speed and existing speech-speed ceiling preserved; no clipping.
- [ ] Media completeness distinct from review; unresolved review produces “Bản nháp — cần kiểm tra”; missing media/timeline errors block full export.
- [ ] Long export uses a job and polling; published MP4 changes only after validation succeeds.
- [ ] Runtime: Edge audio smoke, downloads/range resume, WebSocket cancellation, formatter without console, ONNX/CUDA/VieNeu status.
- [ ] Recovery: aeb source-empty family, 265796c3 ID38, original/native recovered 2b5ef22b, 0805d444 full video, c1cad video.
- [ ] Playable actual exports checked with duration/audio/timeline evidence.
- [ ] Additional new Chinese, Korean/Chinese-captioned, mixed-language and long inputs tested; results reported individually.

## Evidence collected

- Baseline ASR/download/Edge suites: 88 passed.
- Root retry/progress/session suites before final media-gate integration: 250 passed.
- Native agent: 295 focused native/preview/session tests passed at its integration checkpoint.
- `venv/Scripts/python.exe scripts/check_runtime.py`: passed all imports, pinned versions, FFmpeg/ffprobe, local model and configured OpenCode credentials checks. This is a readiness check, not proof of successful provider requests.
- Desktop was gracefully exited at 00:03 on 2026-10-15 (Asia/Saigon), with no active processing. New runtime started on localhost:62213; health and async export API verified by the acceptance runner.
- Real recovery started for `248460b3` through the refreshed backend. Its failed, empty preview must first use Retry before Translate Full. At the first checkpoint, source preparation reached 32 seconds and OCR was active. This is progress, not an accepted output.
- Actual Edge-TTS smoke generated 3.552 seconds of 24 kHz PCM. Actual OpenCode smoke returned a Vietnamese translation in 10.422 seconds; evidence is under `workspace/audit/goal_runtime_20261014`.
- Full regression run found outdated draft-export expectations and invalid mock WAV fixtures; focused follow-up passed 258 tests with 3 remaining transcript tests subsequently corrected and passing. A fresh complete regression run is underway.
- 265796c3 inventory: 195 source rows, 44 manually edited rows, missing Vietnamese ID38. Do not mix with differently segmented older projects.
- recovered_2b5ef22b_kozh: 100 rows and 35 missing WAVs; original 2b5ef22b: 146 rows. IDs are project-specific.
- 0805d444 is a 31.28-second preview of a 212.556-second video, not a complete video.

## Pending verification and integration

Current implementation has not yet passed final combined regression or real-video acceptance. Focused integration checks passed and production recovery is underway, preserving original manifests and outputs. The additional full regression is running independently.

Live recovery exposed an old manifest defect in `265796c3`: row 38 has speech source but empty Vietnamese, with stale `PLAYED` status and no retry action. A general restore-state repair is implemented and tested (287 focused tests); the 44 manually edited rows remain protected. It still needs a runtime reload and actual recovery acceptance.

## 2026-10-15 subsequent live findings

- Full regression completed: 3,942 passed, 2 skipped, 127 subtests passed, one obsolete progress fixture failed. The fixture was corrected and its focused regression passed; subsequent combined focused run passed 290 tests.
- Native publication first failed with `KeyError` because the independently reviewed native draft lacked its review checkpoint. Fixed and covered by a full preview integration regression. Actual retry reached PREVIEW_READY, promoted to full, recognized 87 rows across 332.672993 seconds, and synthesized at least 63 rows.
- Real semantic responses frequently cite legitimate read-only neighbors. Validation now permits exact measured context citations, including siblings after split, while retaining exact target output IDs and requiring each target to cite itself. No fabricated citations are accepted. Contract version advanced to 5.
- Actual ASR produced gibberish in some spans. Some provider replies used `[không rõ nghĩa]` as if it were narration. These are now blocked before TTS, pacing, and full export, including parenthetical uncertainty notes embedded in a proposed line. Targeted restore/retry preserves source and manual changes.
- Latest root combined multilingual/pacing/audio/media/runner regression: 159 passed. Polled-read connection failures are retried without replaying mutations (16 runner tests passed).
- Source probes are in `automatic_multilingual_probe.json`, `large_multilingual_windows.json`, and `local_language_probe.json`. Automatic multilingual decoding helps routing but has not established accurate source in every span. Forced Chinese on Korean speech generated hallucinated generic credits; it is NOT an accepted recovery technique.
- `248460b3` was deliberately stopped through its normal backend API after its progress was saved, to avoid certifying known untranslated/uncertain source. The backend remains on port 65190 with older loaded code. No accepted complete output exists yet for this recovery.
- The tool's automatic approval review rejected the command to reopen `desktop_app.py` for a graceful repair reload with `blocked by policy`, without additional reason. Computer Use separately stopped browser control because it could not verify the current URL. These restrictions have not been bypassed; code/tests can still proceed.
- User supplied the existing `0805d444` source URL again and authorized finding additional Douyin inputs using their signed-in browser. Additional-video acceptance is still outstanding.

## 2026-10-15 latest verification

- Fresh full regression: **3,973 passed, 127 subtests passed, 2 skipped, 4 warnings**, 593.83 seconds (`workspace/audit/goal_runtime_20261014/full-suite-latest.log`). Subsequent VAD coverage guard has 70 focused passing tests; acceptance planning diagnostics have 16 passing tests. This establishes the tested contracts, not successful real exports.
- Real read-only preflight of four projects saved at `workspace/audit/acceptance_20261014T175525Z_15936`. `265796c3` still lacks VI38 and retains 44 manual rows; `0805d444` lacks VI7/8/9; native recovered project lacks 35 WAVs and has invalid timing21–26/94–95. Original `2b5ef22b` has 30 missing VI rows and four invalid/missing WAVs. Planning checks audio contents and timestamps but deliberately leaves source completeness unproven.
- User-supplied Douyin7686836817752050994 has downloaded successfully through the app's public resolver: 432.425215 seconds, 352,939,130 bytes, H2641922×1080+AAC. Source metadata, SHA256 and probe live under `workspace/audit/goal_runtime_20261014/additional_sources_20261015`. No pipeline/output acceptance yet.
- Additional long public Douyin7696088077957090575 resolved to2282.434 seconds; its ordinary resumable download is underway. Browser title/language categories are not proof of spoken language.
- Utterance-local language decoding improves code-switch recognition, but real probes prove VAD alone misses quiet speech. Original/stem evidence is documented in `workspace/audit/goal_runtime_20261014/utterance_probe/completeness_findings.md`. AEB91.7–94.5 remains unreliable, so no complete source claim is warranted.
- Runtime reload remains pending. Existing backend is healthy on65190 but predates recent parent-code changes; restarting via rejected launch commands or bypassing the browser-control restriction has not been attempted. User was asked to exit completely and reopen Studio while independent work continues.
- Added startup source fingerprint to health and an acceptance-runner comparison against current source. The live old backend was correctly rejected before project mutations. Focused health/revision/runner tests:42 passed,1 skipped,15 subtests. This prevents another recovery being evaluated against stale parent code after an edit.
- Original+vocal-stem speech detection is integrated with isolated RoFormer execution and validated evidence caching. Real7-second mixed-source probe restored a previously omitted utterance using the configured large-v3 model; stem-only source remains explicitly unverified. Full regression after this integration is running in `full-suite-vocal-integration.log`.
- New432-second input passed complete FFmpeg audio/video decode. Nine automatic CPU SenseVoice samples support mixed Chinese/Korean content; several low-confidence spans remain unverified (`workspace/audit/additional_sources_20261015/7686836817752050994.evidence.json`).
- Narrow AEB91.5–95.5 source probe changes the next recovery action: automatic SenseVoice now suggests Chinese, and diagnostic zh decodes from SenseVoice/large-v3 agree on 怪头怪脑的 and much of 韩国男娃儿说话说一半, matching retained OCR. Whisper automatic still emits Korean phonetic gibberish. These are candidate readings with low-confidence words, not approved edits; a general disagreement-resolution path is still needed. See `utterance_probe/aeb91_language_candidates.json` and `aeb91_whisper_candidates.json`.

## 2026-10-15 01:30 current runtime acceptance

- Full regression after vocal integration finished: **3,985 passed, 127 subtests passed, 2 skipped, 4 warnings**, 521.29 seconds. JavaScript suites: **326 passed**, no failures (`js-suite-final.log`). Language-disagreement and source-gap diagnostics were subsequently covered by focused agent suites (87/38 and 372/14); they are not all part of the earlier collected full suite.
- Source language disagreement is bounded to two independent original-audio readings and at most one conditional Whisper decode. It preserves alternatives, uncertainty and source time coverage. Actual AEB91.5–95.5 remained unresolved; Korean control remained Korean. No automatic source correction was certified.
- Missing-speech spans now survive source preparation, logging, progress, persistence and history. Deterministic source gaps do not enter network retry loops; only committed source retires corresponding measured gaps.
- User fully exited and reopened Studio. Backend **57375**, PID21180 (parent6380), startup source revision **035042e86ae58377e8edae0f29c0614ffd7b09af8468df33c4e19ebd12c07910** matched disk before mutations. The runtime reload blocker is resolved for this acceptance run.
- Recovery of **265796c3** is running under `acceptance_20261014T182533Z_3248`, exec53794. Missing VI38 was restored, actual speech created, media now195/195; full asynchronous render is underway. It is labeled draft with62 review IDs. Final decode/duration/manual proof is still pending.
- New normal API tasks: **8174800f** for c1cad73.7s and **7db6ae6d** for newly downloaded Douyin7686836817752050994 (432.425215s). Both use existing large-v3/OpenCode/HoaiMyEdge settings, visual text review, full mode, independent saved cap20. They are processing source; no accepted exports yet.
- Read-only audit found remaining nonvisual-mode gaps: serial request production despite selected capacity; only one schema repair; no per-click start-request worker field; context broader than three neighbors. Changes are being prepared separately while current runtime owns production. See `nonvisual-ai-readiness-audit.md`.
- Long Douyin7696088077957090575 download remains live, ~28% at01:29. Its complete source and acceptance are pending.

- 01:40 actual output265796c3 VALIDATED: full audio/video decode passed, source320.040635s/output320.000s,379494119bytes,SHA256657ba3ccf47d5cd85ef778b2886511e4a9e3cb54cb31e5005c861949c51ad645. All44manual rows preserved;194 existing audio hashes unchanged; only38 replaced. Draft62review IDs. Evidence acceptance_20261014T182533Z_3248.
- Real c1 task8174800f stopped correctly at deterministic source gap19.712–20.224s, retained diagnostics. SenseVoice original/stem contextual reads disagree (punctuation/The/I/あ/Yeah); no words or silence certified. New mixed7db6ae6d recovered automatically from native-slot admission timeout after durable300s wait and is decoding. Root translation schema repair now initial+2 for translation tasks only;38tests and17subtests passed.

- 01:44 remaining integration applied: nonvisual translation-only producer reaches2x20/30/40/50 in controlled production-route tests; per-click worker selection on all start routes/UI is now on disk; initial+2 schema repairs for translation. Focused combined176passed,4warnings;329JS passed. A fresh full regression is running in full-suite-nonvisual-start-persistence.log.
- New mixed7db6ae6d reached31.08s then failed persistence: generic metadata cleaning dropped language-disagreement proof keys. Root added schema-scoped evidence cleaning (no global metadata widening) and actual save/restore regression;216tests passed. Live9-row snapshot saved to7db6ae6d-save-failure-snapshot.json; immutable source checkpoint remains. Disk has~281GB free. Current backend still owns pre-fix parent modules; user asked once for another full reopen, citing prior launch rejection. All active processing is now terminal; long downloader is separate and still running.
- Further review found nonvisual producer fairness/ownership issues before declaring it final: transient cooldown can occupy all translation workers; speech queue needs object-identity guard; ordinary new draft must invalidate prior semantic verdict. Agent is preparing fixes and waiting for current suite to finish before applying. Native audit timeout currently retains draft but lacks durable pending-review retry. Fresh full nonvisual still uses legacy forced-zh source; bounded multilingual routing for fresh Faster-Whisper sessions remains required. These findings are tracked rather than claimed complete.
- C1 gap classified diagnostically as stem-only uncertain vocal event, not established missing dialogue. Original/stem large-v3 and SenseVoice probes conflict; no silence or source words certified. Evidence: utterance_probe/c1_gap_cpu/findings.md.
- Full integration result:4088passed,127subtests,2skipped,8failures. Fixed:3obsolete initial+one-repair assertions;2visual retry fixture scope expectations;missing translation progress event;API-test startup incorrectly resumed real saved projects. Added autouse test guard preventing resume when BASE_DIR is real checkout while preserving temporary-root lifecycle tests. No native workers remain from completed tests. New mixed saved manifest is idle FAILED/0rows;9-row live snapshot and immutable31.08s source checkpoint retained.
- Applied staged nonvisual fairness fixes: cooldown timers release worker admission; identity guards on speech; prior semantic verdict reset for new ordinary draft; stop drains all owners. Combined affected suites226passed,4subtests,4warnings. User reload still pending; production backend57375 remains prior revision.

- Fresh full nonvisual Faster-Whisper now routes through bounded automatic-language preparation; existing rows/prepared/valid legacy checkpoint retain old ownership; SenseVoice selection preserved.136focused+244regression passed. Full final combined run started in full-suite-final-routing.log; no additional implementation edits planned during this run.


- Visual spot-check source/output at25/160/290s saved in output265_visual/comparison.jpg: original framing and corresponding scenes retained; Vietnamese caption visible at25s, no corruption. At160s saved Vietnamese cue159.12–159.83 has already ended; absent caption there matches timing. Chinese burned-in text remains visible in reviewed frames; not claimed removed. This is a spot-check, not full semantic certification.


## 2026-10-15 02:28 continuation

- Final-routing full regression ended 4,106 passed, 127 subtests, 2 skipped, one outdated WAV-only legacy test failed because fresh full videos now use bounded source preparation. Scoped that test to the retained legacy path; 33 affected tests passed. No production routing bypass added.
- Live language-disagreement evidence from mixed project snapshot rows7/8 roundtrips exactly through the corrected cleaner (3,703 bytes each): language-proof-live-roundtrip.json.
- Added audit-only native semantic retry checkpoint and separate lifecycle owner. A valid playable draft remains unchanged on transient independent-review failure; saved deadline is at least300sec/Retry-After, later requests audit existing text only. Source/text/model/contract/revision/manual guards retire stale work. Proposed text corrections stay unresolved and do not silently invalidate existing audio. Pause/Stop/shutdown/startup integrate with ownership; complete draft export remains eligible. Focused integration326passed,4warnings,4subtests (native-audit-integration.log). New combined full suite underway in full-suite-native-audit.log, not yet accepted.
- At02:16 discovered previous long-download processes9528/14968 no longer existed; watcher was reporting unchanged progress57%. Validated saved partial/checkpoint and resumed through normal download worker. Server ignoredRange then changed Last-Modified; worker correctly restarted instead of joining unlike entities. New transfer reached98.9% by02:27. Session80781 owns downloader; do not launch duplicate. Full media decode still pending.
- Production backend remains57375/start01:24:59, stale relative to disk changes; pending user reopen request remains unanswered. No policy-rejected app relaunch workaround used. Real video recovery cannot use latest changes until runtime revision matches.

- 02:35 verification: full suite native-audit snapshot **4143passed,2skipped,4warnings,127subtests**,482.29sec. Post-review fixes (paused restored audit Resume, saved remaining cooldown, inflight provider/model change guard, stale marker retirement, UI controls) passed **309focused,1skipped,15subtests** plus10lifecycle. SenseVoice decoder language now reaches native translation without engine switch;81focused tests/4subtests passed. SenseVoice times remain approximate; no claim of measured alignment.
- Long source validation finished:3097668748bytes,2282.428662s,HEVC1758x1080+AAC,SHA256e1186f124a82ee54d0a0cc9402a864a0d794afdd43b52bc12c233d7de627f6b9. Full FFmpeg video/audio decode exit0. Evidence long-source-validation.json. Pipeline/export acceptance still pending runtime reload.

- Long source language probe (9x8s, SenseVoice) confirms mixed/uncertain source: zh samples 10,260,520,1560,1820; ko 780,1300; en 1040,2080. Result labels are routing only and source_supported=false. Evidence long-language-probe/results.json. This satisfies mixed-language source input evidence; full pipeline translation/export remains pending runtime reload.

- FINAL combined source regression (all latest audit lifecycle/SenseVoice edits) completed successfully; exact pytest summary in full-suite-final-20261015.log. Runtime still57375/stale; goal incomplete pending actual recovery/export acceptance. No active download/full-decode/test processes remain from this turn.

## 2026-10-15 follow-up runtime checks

- Previous goal turn classified progress:4165tests passed plus new validated38minsource and implementation. Current backend health and OS process checked again:57375/PID21180 still old revision035042, startup01:24:33. Runtime reload remains pending.
- Actual VieNeu TrúcLy smoke succeeded:48kHzmonoWAV,1.84seconds,176684bytes,6.687sec elapsed,CPUExecutionProvider. Independent diagnostic only; no source project mutation. Evidence workspace/audit/goal_runtime_20261014/vieneu-smoke/result.json. This proves selected engine can synthesize, not complete0805acceptance.
- Additional SenseVoice sound-event probe on c1 original/stem windows remains conflicting: original broad windows BGM, tinygapSpeech; stemSpeech with inconsistent snippets. Retain uncertain gap, no fabricated silence/source. Evidence utterance_probe/c1_event_evidence.json.

- Final cross-review found two additional integration defects, now repaired: audit pending revision rebinding at authorized deferred/failed/inline review publication and harmless timeline reflow; Studio terminal media no longer hides explicit Pause/Resume capabilities, and legacy terminal events clear stale controls. Validation184Pythonpassed plus30focused (including actual inline review speech publication and deferred/failure paths),286UIpassed. Full4165suite was prior to these narrow changes; no claim final combined rerun after them. Changed files pipeline.py/native_audit_retry.py/staticapp plus targetedtests. No production project mutations.
- Current goal turn classified progress: actualVieNeu evidence and repaired review/UI integration defects. Runtime reload still blocks real recovery/export acceptance; backend PID21180 live/57375/oldrevision revalidated. Goal remains incomplete.

## Blocked audit — 2026-10-15 02:55

Same external blocker revalidated across three consecutive goal turns: production Studio still loads revision035042e86ae58377e8edae0f29c0614ffd7b09af8468df33c4e19ebd12c07910, PID21180, startup01:24:33, healthy57375. Current disk revision80f75b16e43c42d0298993158049a847f3b4b75dc2173b7985e1c6f10b9976e9. Desktop relaunch was rejected by automatic approval review with blocked by policy; no alternate launcher/runtime bypass attempted. User's earlier reopen loaded an intermediate revision and was used successfully for265 export; latest reload request unanswered.

Previous two turns made concrete code/test/diagnostic progress while blocked. This turn revalidated authoritative health/OS process/source fingerprint and terminal test states; no remaining independently actionable work identified that can prove actual video acceptance without current runtime. All subagents terminal; downloads/decode/tests terminal. Completion audit still fails recovery/export for7db6ae6d,0805d444,248460b3,2b5 projects,c1,newlong. Thus goal blocked, not complete. Resume after user fully closes/reopensStudio. Preserve all source/caches/manifests and accepted265draft.
