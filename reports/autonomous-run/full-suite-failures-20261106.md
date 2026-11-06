# Full suite failure tracebacks — 2026-11-06

JUnit reports 52 failed test cases; no test errors. Source: `full-suite-20261106-104620.xml`.

## `tests.test_history_api::test_history_then_attach_restores_exact_edits_without_provider`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
saved_project = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C051A8F190>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C051A8C4C0>

    def test_history_then_attach_restores_exact_edits_without_provider(saved_project, monkeypatch):
        monkeypatch.setattr(StreamingPipelineSession, "start", Mock(side_effect=AssertionError("must not start")))
        async def run():
            history = (await main.list_tasks())["tasks"]
            row = next(row for row in history if row["task_id"] == saved_project.task_id)
            assert row["saved"] and row["can_open"] and row["title"] == "source.mp4"
            assert not row["video_url"]
            snapshot = await main.streaming_snapshot(saved_project.task_id)
            assert snapshot["segments"][0]["final_vi"] == "Lời đã sửa."
            assert snapshot["segments"][0]["revision"] == 3
            from core.subtitle_cues import normalize_caption_style
            assert snapshot["caption_style"] == normalize_caption_style(saved_project.caption_style)
            assert snapshot["voice"] == saved_project.voice
            assert snapshot["tts_engine"] == saved_project.tts_engine_name
            assert snapshot["output_outdated"] is True
            assert snapshot["progress"]["status"] == "COMPLETED"
            restored = active_streaming_sessions[saved_project.task_id]
            assert restored.worker_task is None and restored.start_task is None
            assert Path(restored.segments[0].audio_path).read_bytes() == Path(saved_project.segments[0].audio_path).read_bytes()
>       asyncio.run(run())

tests\test_history_api.py:71: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_history_then_attach_restores_exact_edits_without_provider.<locals>.run at 0x000001C051FBBD10>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-1725' coro=<test_history_then_attach_restores_exact_edits_without_provider.<locals>.run() do...\test_history_api.py:54> exception=AssertionError("assert 'PREPARED' == 'COMPLETED'\n  \n  - COMPLETED\n  + PREPARED")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        history = (await main.list_tasks())["tasks"]
        row = next(row for row in history if row["task_id"] == saved_project.task_id)
        assert row["saved"] and row["can_open"] and row["title"] == "source.mp4"
        assert not row["video_url"]
        snapshot = await main.streaming_snapshot(saved_project.task_id)
        assert snapshot["segments"][0]["final_vi"] == "Lời đã sửa."
        assert snapshot["segments"][0]["revision"] == 3
        from core.subtitle_cues import normalize_caption_style
        assert snapshot["caption_style"] == normalize_caption_style(saved_project.caption_style)
        assert snapshot["voice"] == saved_project.voice
        assert snapshot["tts_engine"] == saved_project.tts_engine_name
        assert snapshot["output_outdated"] is True
>       assert snapshot["progress"]["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_history_api.py:67: AssertionError
```

## `tests.test_local_api.LocalAPITests::test_home_renders_configured_engines_and_voice`

**Failure:** `AssertionError: 'vi-VN-HoaiMyNeural' not found in '<!DOCTYPE html>\n<html lang="vi">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n  <title>Douyin2TikTok AI Studio - Desktop Application</title>\n  <meta name="studio-source-revision" content="f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527">\n  <meta name="studio-backend-revision" content="f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527">\n  <script src="https://cdn.tailwindcss.com"></script>\n  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">\n  <link rel="stylesheet" href="/static/style.css?v=1793889200510015900">\n  <link rel="stylesheet" href="/static/studio-compact-panels.css?v=1791943729577814900">\n</head>\n<body data-asr-engine="faster-whisper" data-tts-engine="piper-tts" data-whisper-model="small" class="bg-[#0b0d13] text-gray-100 min-h-screen font-sans antialiased selection:bg-pink-500 selection:text-white">\n  <div id="runtime-build-warning" role="alert" hidden style="background:#7f1d1d;color:#fff;padding:10px 16px;font-weight:600">\n    Giao diện và backend đang dùng khác phiên bản source. Hãy thoát hẳn Studio rồi mở lại; Retry/Tiếp tục đang bị khóa để bảo vệ checkpoint.\n  </div>\n  \n  <header class="app-header">\n    <div class="app-brand">\n      <div class="app-logo" aria-hidden="true"><img src="/static/studio-mark.svg" alt=""></div>\n      <div class="app-brand-copy">\n        <h1>Douyin2TikTok AI Studio</h1>\n        <p>Dịch video · Lồng tiếng · Biên tập phụ đề</p>\n      </div>\n    </div>\n    <nav class="app-navigation" aria-label="Điều hướng ứng dụng">\n      <button id="tab-studio" class="nav-btn text-white bg-pink-600/80"><i class="fa-solid fa-house" aria-hidden="true"></i> Studio</button>\n      <button id="tab-tasks" class="nav-btn text-gray-400"><i class="fa-solid fa-list-check" aria-hidden="true"></i> Lịch sử video</button>\n      <button id="tab-models" class="nav-btn text-gray-400"><i class="fa-solid fa-layer-group" aria-hidden="true"></i> Models</button>\n      <button id="tab-diagnostics" class="nav-btn text-gray-400"><i class="fa-solid fa-wave-square" aria-hidden="true"></i> Diagnostics</button>\n      <button id="tab-settings" class="nav-btn text-gray-400"><i class="fa-solid fa-gear" aria-hidden="true"></i> Cài đặt</button>\n    </nav>\n    <div id="hardware-pill" class="hardware-pill"><i class="fa-solid fa-microchip" aria-hidden="true"></i><span id="gpu-info-text">Đang kiểm tra phần cứng…</span></div>\n  </header>\n\n  <div id="view-studio" class="app-view">\n    <section class="telemetry-ribbon" aria-label="Trạng thái phát và xử lý">\n      <div class="telemetry-metrics">\n        <div class="telemetry-item"><i class="fa-solid fa-circle" aria-hidden="true"></i><span>Đang phát:</span><strong id="tel-playing">00:00</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-play" aria-hidden="true"></i><span>Đã sẵn sàng:</span><strong id="tel-playable">00:00</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-database" aria-hidden="true"></i><span>Buffer:</span><strong id="tel-buffer">+0.0s</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-stopwatch" aria-hidden="true"></i><span>TTFP:</span><strong id="tel-ttfp">--</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-gauge-high" aria-hidden="true"></i><span>RTF:</span><strong id="tel-rtf">0.0x</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-sliders" aria-hidden="true"></i><span>Lọc thoại (ước tính):</span><strong id="tel-suppression">--</strong></div>\n      </div>\n      <div class="telemetry-workers"><span>Workers:</span><span id="worker-asr-badge">ASR: Idle</span><span id="worker-trans-badge">Dịch: Idle</span><span id="worker-tts-badge">TTS: Idle</span></div>\n    </section>\n    <div class="studio-layout-toolbar">\n      <span id="studio-layout-hint">Kéo đường chia giữa các cột hoặc các khối để đổi kích thước.</span>\n      <button id="btn-reset-layout" type="button"><i class="fa-solid fa-up-right-and-down-left-from-center" aria-hidden="true"></i> Khôi phục bố cục</button>\n      <span id="studio-layout-status" class="sr-only" role="status" aria-live="polite"></span>\n    </div>\n    <main id="studio-layout" class="studio-workspace" data-layout-group="columns" aria-describedby="studio-layout-hint">\n      <aside id="studio-left" class="studio-controls studio-column" data-layout-group="left" aria-label="Nguồn video và giọng lồng tiếng">\n        <section id="studio-source" class="studio-source-card studio-card" aria-labelledby="source-heading">\n          <h2 id="source-heading" class="studio-card-heading"><span class="step-number" aria-hidden="true">1</span>Video nguồn <small>(Douyin / URL / File)</small></h2>\n          <p class="panel-description">Dán nội dung chia sẻ hoặc tải video từ máy.</p>\n          <div class="source-tabs" role="group" aria-label="Kiểu nguồn video">\n            <button id="source-tab-url" type="button" class="source-tab is-active" aria-pressed="true"><i class="fa-solid fa-link" aria-hidden="true"></i> URL</button>\n            <button id="source-tab-file" type="button" class="source-tab" aria-pressed="false"><i class="fa-regular fa-file-video" aria-hidden="true"></i> Tải file</button>\n            <button id="source-tab-library" type="button" class="source-tab" aria-pressed="false"><i class="fa-regular fa-folder-open" aria-hidden="true"></i> Thư viện</button>\n          </div>\n          <div id="source-url-panel">\n            <div class="source-url-field">\n              <i class="fa-solid fa-link" aria-hidden="true"></i>\n              <textarea id="video-url" rows="1" wrap="off" spellcheck="false" aria-label="Link video hoặc nội dung chia sẻ" aria-describedby="video-url-help video-url-status" placeholder="Dán link video…"></textarea>\n              <button id="clear-video-url" type="button" aria-label="Xóa link video" title="Xóa link"><i class="fa-solid fa-circle-xmark" aria-hidden="true"></i></button>\n            </div>\n            <p id="video-url-help" class="source-help">Tự nhận link từ nội dung chia sẻ.</p>\n            <p id="video-url-status" role="status" aria-live="polite" class="hidden source-help"></p>\n          </div>\n          <div id="source-library-panel" class="hidden source-library-panel">\n            <p id="library-status" class="source-help" role="status">Chọn video đã có trong thư viện.</p>\n            <div id="library-list" class="source-library-list" aria-label="Video trong thư viện"></div>\n          </div>\n          <div id="drop-zone" role="button" tabindex="0" aria-label="Chọn video từ máy" class="source-drop-zone">\n            <input type="file" id="video-file" accept="video/*" class="hidden">\n            <i class="fa-solid fa-cloud-arrow-up" aria-hidden="true"></i>\n            <p id="file-name-display">Kéo &amp; thả video vào đây</p>\n            <span>Hoặc bấm để chọn file từ máy tính<br>MP4, MOV, WebM, MKV</span>\n          </div>\n        </section>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="0" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="studio-source studio-voice" aria-label="Chiều cao nguồn video và giọng lồng tiếng" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <section id="studio-voice" class="voice-panel studio-card" aria-labelledby="voice-list-title">\n          <div class="voice-list-heading"><h3 id="voice-list-title"><i class="fa-solid fa-microphone-lines" aria-hidden="true"></i>2. Giọng lồng tiếng</h3><span id="voice-count" class="voice-count" role="status" aria-live="polite"></span></div>\n          <p id="voice-source" class="voice-source" aria-live="polite">Đang tải danh sách giọng…</p>\n          <input type="hidden" id="voice-select" value="Trúc Ly">\n          <label for="voice-search" class="sr-only">Tìm theo tên, vùng miền hoặc nguồn</label>\n          <div class="voice-search-controls"><div class="voice-search-field"><i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i><input id="voice-search" type="search" placeholder="Tìm theo tên, vùng miền hoặc nguồn" autocomplete="off" class="voice-search" aria-controls="voice-list"></div><button id="voice-filter" type="button" aria-label="Lọc giọng theo nguồn" title="Lọc giọng theo nguồn"><i class="fa-solid fa-filter" aria-hidden="true"></i><span>Tất cả</span></button></div>\n          <div id="voice-list" class="voice-list" role="group" aria-labelledby="voice-list-title" tabindex="0"></div>\n          <p id="voice-empty" class="hidden voice-source">Không tìm thấy giọng phù hợp. Thử tên hoặc nguồn khác.</p>\n          <p id="voice-catalog-status" class="voice-source" role="status"></p>\n          <div class="voice-preview-panel"><div class="voice-preview-controls"><audio id="voice-preview-audio" class="hidden" controls preload="none" aria-label="Mẫu giọng lồng tiếng"></audio></div><p id="voice-preview-status" class="voice-preview-status" role="status" aria-live="polite" aria-atomic="true"></p></div>\n        </section>\n      </aside>\n        <div class="studio-splitter" data-layout-axis="x" data-layout-index="0" role="separator" tabindex="0" aria-orientation="vertical" aria-controls="studio-left studio-middle" aria-label="Chiều rộng cột nguồn và cột video" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n      <section id="studio-middle" class="studio-preview studio-column" data-layout-group="middle" aria-label="Video, phụ đề và âm thanh">\n        <div id="studio-player" class="studio-player-card studio-card">\n          <div class="studio-player-heading">\n            <h2 class="studio-card-heading"><i class="fa-solid fa-video" aria-hidden="true"></i>Xem trước video</h2>\n            <div class="player-options">\n              <label title="Hiện phụ đề lời thoại tiếng Việt ở dưới video."><input type="checkbox" id="toggle-subtitles" checked> Hiện Sub Việt</label>\n              <label title="Giữ sub Trung và đặt lời Việt ngay bên dưới. Nếu không thấy sub gốc, đặt lời Việt nhỏ ở đáy hình."><input type="checkbox" id="toggle-screen-text" checked aria-label="Tự đặt lời Việt dưới phụ đề Trung"> Tự đặt vị trí sub</label>\n              <input type="checkbox" id="toggle-mask-chinese" hidden disabled aria-hidden="true">\n            </div>\n          </div>\n          <div class="player-surface">\n            <div class="studio-video-stage">\n              <div id="player-container">\n                <video id="video-player" playsinline></video>\n                <div id="chinese-sub-mask" class="absolute bottom-16 left-0 right-0 h-14 bg-black/65 backdrop-blur-md pointer-events-none transition-opacity duration-300"></div>\n                <div id="subtitle-overlay" class="absolute bottom-16 left-4 right-4 text-center pointer-events-none z-10"><span id="subtitle-text"></span></div>\n                <div id="screen-text-overlay" class="screen-text-overlay" aria-hidden="true"></div>\n                <div id="buffering-alert" role="status" aria-live="polite" class="hidden player-buffering-overlay">\n                  <i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i>\n                  <p id="buffering-text">Đang tải giọng lồng tiếng...</p>\n                  <div id="player-download-progress" class="player-download-progress hidden" aria-live="polite">\n                    <div class="player-progress-heading"><strong id="player-progress-value">Chưa có %</strong><span id="player-progress-detail">Đang chờ số liệu từ máy chủ…</span></div>\n                    <div id="player-progress-track" class="player-progress-track" role="progressbar" aria-label="Tiến độ tải video" aria-valuemin="0" aria-valuemax="100"><div id="player-progress-bar" class="player-progress-bar"></div></div>\n                  </div>\n                </div>\n                <div id="player-placeholder" class="player-placeholder"><img class="empty-video-art" src="/static/studio-empty.svg" alt=""><p>Chưa có video được nạp</p><span>Dán link Douyin/TikTok hoặc tải video từ máy tính để bắt đầu dịch và lồng tiếng.</span><div class="preview-features" aria-hidden="true"><span><i class="fa-solid fa-film"></i>Nhiều định dạng</span><span><i class="fa-regular fa-circle-play"></i>Xem trước trực tiếp</span><span><i class="fa-solid fa-font"></i>Chỉnh phụ đề</span><span><i class="fa-solid fa-volume-high"></i>Giọng lồng tiếng</span></div></div>\n              </div>\n            </div>\n            <p id="player-task-status" class="player-task-status hidden" role="status"></p>\n            <div class="player-transport">\n              <div id="segments-timeline-track" role="slider" tabindex="0" aria-label="Vị trí phát video" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"><div id="playback-head-marker" style="left: 0%;"></div></div>\n              <div class="player-transport-row">\n                <button id="player-seek-backward" class="player-seek" type="button" aria-label="Lùi 5 giây" title="Lùi 5 giây"><i class="fa-solid fa-rotate-left" aria-hidden="true"></i><span aria-hidden="true">5</span></button>\n                <button id="player-play-toggle" type="button" aria-label="Phát video" title="Phát / tạm dừng"><i class="fa-solid fa-play" aria-hidden="true"></i></button>\n                <button id="player-seek-forward" class="player-seek" type="button" aria-label="Tiến 5 giây" title="Tiến 5 giây"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i><span aria-hidden="true">5</span></button>\n                <span class="player-time"><span id="bar-current-time">00:00</span><span class="player-time-divider">/</span><span id="bar-total-time">00:00</span></span>\n                <span id="bar-buffer-info" class="player-buffer-info">Buffer: 0.0s</span>\n                <button id="player-mute-toggle" type="button" aria-label="Tắt tiếng video" title="Bật / tắt tiếng"><i class="fa-solid fa-volume-high" aria-hidden="true"></i></button>\n                <input id="player-volume" type="range" min="0" max="1" step="0.05" value="1" aria-label="Âm lượng video">\n                <button id="player-fullscreen" type="button" aria-label="Xem video toàn màn hình" title="Toàn màn hình"><i class="fa-solid fa-expand" aria-hidden="true"></i></button>\n                <button id="player-settings" type="button" aria-label="Mở cài đặt phát và xử lý" title="Cài đặt"><i class="fa-solid fa-gear" aria-hidden="true"></i></button>\n              </div>\n            </div>\n          </div>\n        </div>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="0" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="studio-player caption-style-panel" aria-label="Chiều cao video và chỉnh phụ đề" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <details id="caption-style-panel" class="caption-style-panel" open>\n          <summary>Chỉnh phụ đề <span>Nền · font · cỡ chữ · vị trí</span></summary>\n          <fieldset id="caption-style-controls" disabled aria-describedby="caption-style-availability caption-style-status">\n            <div class="caption-style-grid">\n              <label>Màu chữ<input id="caption-text-color" type="color" value="#000000" aria-label="Màu chữ phụ đề"></label>\n              <label>Màu nền<input id="caption-background-color" type="color" value="#FFE500" aria-label="Màu nền phụ đề"></label>\n              <label>Vị trí<select id="caption-position"><option value="auto">Tự động</option><option value="top">Phía trên</option><option value="middle">Ở giữa</option><option value="bottom">Phía dưới</option></select></label>\n            </div>\n            <div class="caption-typography-grid">\n              <label class="caption-font-control" for="caption-font-family">Font chữ<select id="caption-font-family"><option value="Arial" style="font-family:Arial,sans-serif">Arial</option><option value="Segoe UI" style="font-family:\'Segoe UI\',sans-serif">Segoe UI</option><option value="Tahoma" style="font-family:Tahoma,sans-serif">Tahoma</option><option value="Verdana" style="font-family:Verdana,sans-serif">Verdana</option><option value="Times New Roman" style="font-family:\'Times New Roman\',serif">Times New Roman</option></select></label>\n              <div class="caption-size-control"><label for="caption-font-size-pt">Cỡ chữ</label><span class="caption-point-input"><input id="caption-font-size-pt" type="number" list="caption-font-sizes" min="6" max="96" step="0.5" placeholder="Tự động" aria-label="Cỡ chữ phụ đề theo point, từ 6 đến 96"><span aria-hidden="true">pt</span></span><button id="caption-font-size-auto" class="caption-auto-size" type="button" aria-pressed="true" title="Tự căn cỡ chữ theo video và độ dài câu">Tự động</button><label class="caption-bold-button" title="Chữ đậm"><input id="caption-font-bold" type="checkbox" checked aria-label="Chữ đậm phụ đề"><span aria-hidden="true">B</span></label></div>\n              <datalist id="caption-font-sizes"><option value="8"></option><option value="9"></option><option value="10"></option><option value="11"></option><option value="12"></option><option value="14"></option><option value="16"></option><option value="18"></option><option value="20"></option><option value="24"></option><option value="28"></option><option value="32"></option><option value="36"></option><option value="48"></option><option value="72"></option><option value="96"></option></datalist>\n              <div hidden class="hidden" aria-hidden="true"><input id="caption-font-scale" type="range" min="50" max="200" step="1" value="100" tabindex="-1"><input id="caption-font-scale-number" type="number" min="50" max="200" step="1" value="100" tabindex="-1"></div>\n              <div class="caption-opacity-control"><label for="caption-background-opacity">Độ đậm nền</label><input id="caption-background-opacity" type="range" min="0" max="100" step="1" value="100" aria-label="Độ đậm nền phụ đề"><output id="caption-background-opacity-value" for="caption-background-opacity">100%</output></div>\n            </div>\n            <div class="caption-style-options">\n              <label title="Dùng nền vàng thống nhất cho tất cả câu; bỏ chọn để chọn màu khác."><input id="caption-auto-background" type="checkbox" checked> Dùng nền mặc định (vàng)</label>\n              <label title="Bỏ nền phụ đề, giữ chữ và vùng làm mờ sub gốc nếu đã bật"><input id="caption-transparent-background" type="checkbox"> Nền trong suốt</label>\n              <label title="Viền tương phản giúp đọc rõ trên nền trong suốt"><input id="caption-font-outline" type="checkbox"> Viền chữ</label>\n              <label title="Chỉ làm mờ vùng phụ đề gốc đã nhận diện"><input id="caption-blur-original" type="checkbox"> Làm mờ sub gốc</label>\n              <button id="caption-style-reset" type="button">Mặc định</button>\n            </div>\n          </fieldset>\n          <p id="caption-style-availability" class="mt-2 text-[10px] leading-relaxed text-slate-300" role="status" aria-live="polite">Mở dự án đã dịch hoặc tạo bản xem trước có giọng Việt để chỉnh phụ đề.</p>\n          <p id="caption-style-status" role="status" aria-live="polite">Kiểu phụ đề được lưu cho bản xem trước và video xuất.</p>\n        </details>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="1" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="caption-style-panel studio-audio" aria-label="Chiều cao phụ đề và âm thanh" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <section id="studio-audio" class="studio-audio-panel" aria-labelledby="audio-heading">\n          <h3 id="audio-heading"><i class="fa-solid fa-headphones" aria-hidden="true"></i>Điều chỉnh âm thanh</h3>\n          <div class="audio-mixers">\n            <div class="audio-mixer"><i class="fa-solid fa-microphone-lines" aria-hidden="true"></i><label for="vol-dub">Giọng Việt</label><input type="range" id="vol-dub" min="0" max="1" step="0.05" value="1.0"><span id="vol-dub-val">100%</span></div>\n            <div class="audio-mixer audio-mixer-bgm"><i class="fa-solid fa-music" aria-hidden="true"></i><label for="vol-bgm">BGM & SFX</label><input type="range" id="vol-bgm" min="0" max="1" step="0.05" value="0.30"><span id="vol-bgm-val">30%</span></div>\n          </div>\n          <div class="audio-suppression"><i class="fa-solid fa-wave-square" aria-hidden="true"></i><span>Lọc thoại Trung:</span><strong id="player-suppression-badge" title="Mức giảm ước tính của bộ lọc">--</strong><span class="audio-divider">|</span><span class="audio-bgm-label"><i class="fa-solid fa-music" aria-hidden="true"></i> BGM:</span><span id="player-bgm-status">Chưa có</span></div>\n        </section>\n      </section>\n        <div class="studio-splitter" data-layout-axis="x" data-layout-index="1" role="separator" tabindex="0" aria-orientation="vertical" aria-controls="studio-middle studio-right" aria-label="Chiều rộng cột video và cột transcript" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n      <aside id="studio-right" class="studio-sidebar studio-column" data-layout-group="right" aria-label="Transcript, dịch và trạng thái xử lý">\n        <section id="studio-transcript" class="transcript-panel studio-card" aria-labelledby="transcript-heading">\n          <div class="transcript-heading"><h2 id="transcript-heading"><i class="fa-regular fa-file-lines" aria-hidden="true"></i>Transcript</h2><span id="segments-count-badge" class="transcript-count">0 câu</span><p>Bấm vào câu để tua video. Bấm bản dịch để sửa từng chữ.</p></div>\n          <div class="transcript-toolbar"><label class="transcript-follow"><input id="transcript-follow" type="checkbox" checked> Theo vị trí phát</label><label class="transcript-option"><input id="transcript-show-time" type="checkbox" checked> Hiện thời gian</label><span class="transcript-time-note">Theo câu</span></div>\n          <div id="segments-list" class="transcript-list" aria-label="Câu thoại gốc và bản dịch" tabindex="0"><div class="transcript-empty"><i class="fa-regular fa-file-lines" aria-hidden="true"></i><p>Chưa có nội dung transcript</p><span>Nạp video và bắt đầu dịch để xem, sửa và nghe lại cạnh khung hình.</span></div></div>\n          <p id="transcript-status" class="transcript-status" role="status" aria-live="polite">Bản sửa sẽ cập nhật phụ đề và tạo lại giọng đọc, giữ nguyên mốc thời gian.</p>\n        </section>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="0" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="studio-transcript studio-action" aria-label="Chiều cao transcript và phần dịch" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <section id="studio-action" class="studio-action-card studio-card" aria-labelledby="action-heading">\n          <h2 id="action-heading" class="studio-card-heading"><i class="fa-solid fa-microphone-lines" aria-hidden="true"></i>3. Dịch và lồng tiếng</h2>\n          <div class="buffer-setting"><label for="start-translation-workers">Luồng dịch video này</label><select id="start-translation-workers" aria-describedby="start-translation-workers-help">\n            <option value="1" >1 luồng</option><option value="3" >3 luồng</option><option value="5" >5 luồng</option><option value="10" >10 luồng</option><option value="15" >15 luồng</option><option value="20" selected>20 luồng</option><option value="30" >30 luồng</option><option value="40" >40 luồng</option><option value="50" >50 luồng</option>\n          </select></div>\n          <p id="start-translation-workers-help" class="task-progress-detail">Mặc định 20. Áp dụng khi bắt đầu video mới; dự án mở lại giữ số luồng đã lưu.</p>\n          <div class="studio-primary-actions" role="group" aria-label="Dịch và lưu video">\n            <button id="btn-start" class="studio-start-button"><i class="fa-solid fa-play" aria-hidden="true"></i>Bắt đầu dịch</button>\n            <button id="btn-retry-worker" class="hidden" title="Tiếp tục tác vụ từ phần đã lưu" aria-label="Tiếp tục tác vụ"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i> Tiếp tục</button>\n            <button id="btn-export-hq" aria-label="Xuất video MP4" disabled title="Xuất video từ lời và giọng hiện đã lưu" class="studio-export-button"><i class="fa-solid fa-arrow-up-from-bracket" aria-hidden="true"></i>Xuất MP4</button>\n            <button id="btn-save-result" class="studio-export-button" disabled title="Có thể lưu sau khi xuất xong toàn bộ video"><i class="fa-solid fa-download" aria-hidden="true"></i>Tải video</button>\n            <button id="btn-pause-worker" class="hidden"><i class="fa-solid fa-pause" aria-hidden="true"></i> Tạm dừng</button>\n            <button id="btn-resume-worker" class="hidden"><i class="fa-solid fa-play" aria-hidden="true"></i> Tiếp tục</button>\n            <button id="btn-stop-worker" class="hidden"><i class="fa-solid fa-stop" aria-hidden="true"></i> Dừng hẳn</button>\n          <button id="btn-translate-full" class="studio-start-button hidden" type="button"><i class="fa-solid fa-forward-step" aria-hidden="true"></i>Dịch toàn bộ</button>\n            <button id="btn-review-worker" class="hidden"><i class="fa-solid fa-check-double" aria-hidden="true"></i> AI kiểm tra lại</button>\n          </div>\n          <div class="studio-action-options">\n          <label class="visual-translation-option" title="Đối chiếu lời nói với chữ trên video, rồi tự rà và sửa bản dịch trước khi tạo giọng."><input id="visual-translation" type="checkbox" aria-describedby="visual-translation-help" ><span>AI kiểm tra bản dịch</span></label>\n          <span id="visual-translation-help" class="sr-only">Đối chiếu lời nói với chữ trên video, rồi tự rà và sửa bản dịch trước khi tạo giọng.</span>\n          <div class="buffer-setting"><label for="buffer-select">Đệm trước khi phát</label><select id="buffer-select"><option value="5">5 giây (Nhanh nhất)</option><option value="10" selected>10 giây (Khuyên dùng)</option><option value="15">15 giây (Ổn định)</option><option value="30">30 giây (An toàn)</option></select></div>\n          </div>\n          <div id="task-progress" class="hidden task-progress" aria-label="Tiến độ xử lý video">\n            <p id="task-activity" class="task-activity" role="status" aria-live="polite">Đang kiểm tra trạng thái xử lý</p>\n            <p id="task-activity-age" class="hidden task-activity-age"></p>\n            <p id="task-countdown" class="task-countdown" role="timer">Thời gian còn lại: chưa đủ dữ liệu</p>\n            <div class="task-progress-heading" role="status" aria-live="polite"><span id="task-progress-stage">Đang gửi yêu cầu…</span><span class="flex flex-wrap items-baseline justify-end gap-x-2"><span id="task-progress-value">Chưa có %</span><span id="task-progress-eta" class="hidden text-gray-400"></span></span></div>\n            <div id="task-progress-track" class="task-progress-track" role="progressbar" aria-label="Tiến độ bước hiện tại" aria-valuemin="0" aria-valuemax="100"><div id="task-progress-bar" class="task-progress-bar"></div></div>\n            <p id="task-media-progress" class="hidden task-media-progress"></p>\n            <p id="task-progress-detail" class="task-progress-detail">Phần trăm được tính riêng cho từng bước.</p>\n            <p id="task-connection-status" role="status" class="hidden task-connection-status"></p>\n            <details class="task-status-history" open>\n              <summary>Lịch sử trạng thái</summary>\n              <ol id="task-status-history"><li>Chưa có hoạt động được ghi nhận.</li></ol>\n            </details>\n          </div>\n          <div id="task-result" class="hidden" role="status">\n            <p id="task-result-status" class="task-progress-detail"></p>\n            <a id="task-result-link" class="studio-export-button hidden">Xem trước kết quả</a>\n          </div>\n        </section>\n      </aside>\n    </main>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 2. TASKS VIEW (TASK MANAGER) -->\n  <!-- ======================================================== -->\n  <div id="view-tasks" class="app-view hidden max-w-6xl mx-auto p-6 space-y-6">\n    <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-list-check text-teal-400"></i> Lịch sử video\n        </h2>\n        <p class="text-xs text-gray-400">Mỗi video hiện một lần cùng tiến độ mới nhất. Khi có nhiều bản, hệ thống tự mở bản có dữ liệu đã lưu; bản xem trước rỗng vẫn nằm trong “Các bản đã lưu”.</p>\n      </div>\n      <button id="btn-refresh-tasks" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n        <i class="fa-solid fa-rotate-right"></i> Làm mới\n      </button>\n    </div>\n\n    <!-- Active Tasks Table -->\n    <div class="history-table-container bg-[#12151f] border border-gray-800 rounded-2xl shadow-xl">\n      <table class="history-table w-full text-left text-xs text-gray-300">\n        <thead class="bg-gray-900/80 text-gray-400 uppercase text-[10px] border-b border-gray-800">\n          <tr>\n            <th class="p-3.5">Video</th>\n            <th class="p-3.5">Trạng thái &amp; Tiến độ</th>\n            <th class="p-3.5">Thời Lượng</th>\n            <th class="p-3.5 text-right">Thao tác</th>\n          </tr>\n        </thead>\n        <tbody id="tasks-table-body" class="divide-y divide-gray-800/60">\n          <!-- Dynamic Task Row -->\n          <tr>\n            <td colspan="4" class="p-8 text-center text-gray-500 font-sans">\n              Chưa có video trong lịch sử.\n            </td>\n          </tr>\n        </tbody>\n      </table>\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 3. MODELS VIEW (MODEL MANAGER) -->\n  <!-- ======================================================== -->\n  <div id="view-models" class="app-view hidden max-w-6xl mx-auto p-6 space-y-6">\n    <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-server text-violet-400"></i> Quản Lý Models & Checkpoints\n        </h2>\n        <p class="text-xs text-gray-400">Kiểm tra tính sẵn sàng, dung lượng bộ nhớ và trạng thái thiết bị của các AI Engine.</p>\n      </div>\n      <button id="btn-reload-models" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n        <i class="fa-solid fa-arrows-rotate"></i> Kiểm tra lại\n      </button>\n    </div>\n\n    <div class="bg-[#12151f] border border-gray-800 rounded-2xl overflow-hidden shadow-xl">\n      <table class="w-full text-left text-xs text-gray-300">\n        <thead class="bg-gray-900/80 text-gray-400 uppercase text-[10px] border-b border-gray-800">\n          <tr>\n            <th class="p-3.5">Engine</th>\n            <th class="p-3.5">Tên Model / Checkpoint</th>\n            <th class="p-3.5">Kích thước</th>\n            <th class="p-3.5">Thiết bị</th>\n            <th class="p-3.5">Trạng Thái</th>\n            <th class="p-3.5 text-right">Thao Tác</th>\n          </tr>\n        </thead>\n        <tbody id="models-full-table-body" class="divide-y divide-gray-800/60 font-mono">\n          <!-- Dynamic Models populated by JS -->\n        </tbody>\n      </table>\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 4. DIAGNOSTICS VIEW (LOG VIEWER) -->\n  <!-- ======================================================== -->\n  <div id="view-diagnostics" class="app-view hidden max-w-6xl mx-auto p-6 space-y-4">\n    <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-terminal text-yellow-400"></i> Nhật Ký & Chẩn Đoán (Diagnostics)\n        </h2>\n        <p class="text-xs text-gray-400">Theo dõi tải và dịch video · tự cập nhật mỗi 3 giây.</p>\n      </div>\n      <div class="flex items-center gap-2">\n        <button id="btn-copy-log" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n          <i class="fa-regular fa-copy"></i> Sao chép Log\n        </button>\n        <button id="btn-open-log-folder" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n          <i class="fa-regular fa-folder-open text-pink-400"></i> Mở Thư Mục Logs\n        </button>\n        <button id="btn-clear-log" class="px-3 py-1.5 rounded-lg border border-rose-800/60 bg-rose-950/40 hover:bg-rose-900/60 text-xs text-rose-300 flex items-center gap-1.5">\n          <i class="fa-solid fa-trash-can"></i> Xóa Log\n        </button>\n      </div>\n    </div>\n\n    <!-- Log Category Tabs -->\n    <div class="flex items-center gap-2 border-b border-gray-800 pb-2">\n      <button id="diag-warnings-tab" data-cat="warnings" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">Thông báo tác vụ</button>\n      <button data-cat="pipeline" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono font-bold bg-pink-600/80 text-white">Tác vụ</button>\n      <button data-cat="ai" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">AI</button>\n      <button data-cat="errors" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">Lỗi</button>\n      <button data-cat="app" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">Ứng dụng</button>\n      <div class="ml-auto flex items-center gap-2">\n        <label class="text-[11px] text-gray-400 flex items-center gap-1 cursor-pointer">\n          <input type="checkbox" id="chk-auto-scroll-log" checked class="rounded border-gray-700 text-pink-600">\n          Theo dòng mới\n        </label>\n        <button id="btn-refresh-log" aria-label="Làm mới nhật ký" title="Làm mới nhật ký" class="text-xs text-gray-400 hover:text-white p-1">\n          <i class="fa-solid fa-arrows-rotate"></i>\n        </button>\n      </div>\n    </div>\n\n    <section id="diagnostics-warnings-panel" aria-labelledby="diag-warnings-tab" class="hidden">\n      <p class="text-xs text-gray-400 mb-3">Thông báo của video đang mở trong Studio, tự cập nhật theo tác vụ.</p>\n      <p id="diagnostics-warnings-empty" class="text-sm text-gray-400">Chưa có thông báo cho video đang mở.</p>\n      <p id="pipeline-warning" role="status" class="hidden pipeline-warning"></p>\n    </section>\n    <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-400">\n      <p id="log-summary" role="status">Chọn danh mục để xem nhật ký.</p>\n      <p id="log-copy-status" role="status" aria-live="polite"></p>\n    </div>\n    <!-- Log Console Output -->\n    <div tabindex="0" aria-label="Nội dung nhật ký" class="bg-[#07090e] border border-gray-800 rounded-2xl p-4 font-mono text-xs text-gray-300 h-[520px] overflow-y-auto leading-relaxed shadow-inner" id="log-console-output">\n      Đang tải log...\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 5. SETTINGS VIEW (SYSTEM & ENGINE CONFIGURATION) -->\n  <!-- ======================================================== -->\n  <div id="view-settings" class="app-view hidden max-w-4xl mx-auto p-6 space-y-6">\n    <div class="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-gear text-cyan-400"></i> Cài Đặt Hệ Thống & AI Engines\n        </h2>\n        <p class="text-xs text-gray-400">Cấu hình API dịch thuật, mô hình giọng đọc và quy tắc lọc âm thanh.</p>\n      </div>\n      <button id="btn-settings-back" type="button" class="px-3 py-2 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400">\n        <i class="fa-solid fa-arrow-left mr-1" aria-hidden="true"></i> Quay lại video\n      </button>\n    </div>\n\n    <section id="douyin-cookies-panel" aria-labelledby="douyin-cookies-heading" class="bg-[#12151f] border border-gray-800 rounded-2xl p-5 shadow-xl space-y-3">\n      <h3 id="douyin-cookies-heading" class="text-sm font-bold text-cyan-300">Tải video Douyin</h3>\n      <p class="text-xs text-gray-300 leading-relaxed">Dán toàn bộ nội dung chia sẻ vào ô <strong>Video nguồn</strong>, rồi bấm <strong>Bắt đầu</strong>. Studio thử tải video công khai miễn phí, không cần API key hoặc mở Chrome.</p>\n      <p class="text-xs text-gray-400 leading-relaxed">Khả năng tải phụ thuộc video và kết nối. Nếu cách tải công khai không dùng được, Studio thử cách tải dự phòng và cookie đã nhập nếu có.</p>\n      <details class="rounded-xl border border-gray-800 bg-[#08090d]/50">\n        <summary class="cursor-pointer rounded-xl px-3 py-3 text-xs font-semibold text-gray-300 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400">Nâng cao: cookie cho video cần đăng nhập</summary>\n        <div class="px-3 pb-3 space-y-3">\n          <p id="douyin-cookies-help" class="text-xs text-gray-400 leading-relaxed">Chỉ cần thử cách này khi bộ tải yêu cầu phiên đăng nhập. Phiên Chrome không tự chuyển sang Studio. Chọn tệp Netscape cookies <span class="font-mono">.txt</span> chỉ xuất từ Douyin sau khi đăng nhập (tối đa 1 MiB). Bản sao được lưu trên máy và chỉ dùng để tải video Douyin.</p>\n          <p id="douyin-cookies-state" class="text-xs text-gray-300">Chưa kiểm tra cookie đã nhập.</p>\n          <div class="flex flex-wrap items-center gap-2">\n            <input id="douyin-cookies-file" type="file" accept=".txt,text/plain" aria-label="Chọn tệp cookie Douyin" aria-describedby="douyin-cookies-help" class="hidden">\n            <button id="btn-import-douyin-cookies" type="button" class="px-3 py-2 rounded-xl border border-cyan-500/50 hover:bg-cyan-600/20 text-cyan-300 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed">Nhập cookie từ tệp…</button>\n            <button id="btn-refresh-douyin-cookies" type="button" class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-gray-800 text-gray-300 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed">Kiểm tra trạng thái</button>\n            <button id="btn-delete-douyin-cookies" type="button" disabled class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-rose-900/20 text-rose-300 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed">Xóa cookie đã nhập</button>\n          </div>\n          <p id="douyin-cookies-status" role="status" aria-live="polite" aria-atomic="true" class="text-xs text-gray-400 leading-relaxed">Nhập cookie chưa xác nhận tải được video; cần chạy lại link Douyin để kiểm tra.</p>\n        </div>\n      </details>\n    </section>\n\n    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">\n      \n      <!-- AI Translation Settings -->\n      <div class="bg-[#12151f] border border-gray-800 rounded-2xl p-5 shadow-xl space-y-4">\n        <h3 class="text-xs font-bold text-pink-400 uppercase tracking-wider flex items-center gap-2">\n          <i class="fa-solid fa-language"></i> 1. AI Dịch Thuật (Translation)\n        </h3>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Nhà cung cấp (LLM Provider):</label>\n          <select id="settings-llm-provider" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="openrouter-free" >OpenRouter Free (miễn phí)</option>\n            <option value="ollama" selected>Ollama local · Qwen2.5 7B</option>\n            <option value="gemini" >Google Gemini (tùy chọn, cần key)</option>\n            <option value="deepseek">DeepSeek AI (Văn phong kịch tính)</option>\n            <option value="openai">OpenAI Compatible API</option>\n            <option value="free">Google / MyMemory miễn phí</option>\n            <option value="opencode" >OpenCode Zen · Muse Spark Free</option>\n            <option value="muse">Muse qua Chrome (thử nghiệm, tài khoản Meta)</option>\n          </select>\n        </div>\n\n        <div>\n          <div class="flex items-center justify-between mb-1">\n            <label class="text-xs text-gray-300">Google Gemini API Key:</label>\n            <span class="text-[10px] text-gray-500">Lưu an toàn tại .env</span>\n          </div>\n          <div class="relative">\n            <input type="password" id="settings-gemini-key" placeholder="AIzaSy..." class="w-full bg-[#08090d] border border-gray-700 rounded-xl pl-3 pr-10 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500 font-mono">\n            <button type="button" id="btn-toggle-gemini-key" class="absolute right-3 top-2.5 text-gray-500 hover:text-gray-300">\n              <i class="fa-regular fa-eye text-xs"></i>\n            </button>\n          </div>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Gemini Model:</label>\n          <input id="settings-gemini-model" list="gemini-model-options" value="gemini-2.5-flash" placeholder="Tên model Gemini" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n          <datalist id="gemini-model-options">\n            <option value="gemini-2.5-flash">Ưu tiên trên máy này — đã kiểm tra dịch</option>\n            <option value="gemini-3.6-flash">Flash 3.6 — có thể thử khi hết quá tải</option>\n            <option value="gemini-3.8-flash">Flash mới nhất — có thể thử khi còn công suất</option>\n            <option value="gemini-3.7-flash">Flash thế hệ trước</option>\n            <option value="gemini-3.5-flash">Flash 3.5</option>\n            <option value="gemini-3.1-pro-preview">Pro — cần quota phù hợp</option>\n          </datalist>\n          <p class="text-[11px] text-gray-500 mt-2">Lưu cấu hình trước khi kiểm tra. Chất lượng, tốc độ và hạn mức phụ thuộc model và tài khoản.</p>\n        </div>\n\n        <!-- Gemini Test Connection Button & Result -->\n        <div class="pt-2 flex items-center justify-between">\n          <button id="btn-test-gemini" class="px-3 py-1.5 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs font-semibold flex items-center gap-1.5 transition">\n            <i class="fa-solid fa-plug-circle-check"></i> Kiểm tra kết nối Gemini\n          </button>\n          <span id="gemini-test-result" class="text-xs font-mono text-gray-400"></span>\n        </div>\n\n        <div class="rounded-xl border border-pink-500/30 p-3 space-y-2">\n          <label for="settings-openrouter-model" class="text-xs text-gray-300 block">Model OpenRouter Free:</label>\n          <input id="settings-openrouter-model" value="inclusionai/ling-3.0-flash-sante:free" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n          <p id="openrouter-auth-status" role="status" class="text-xs text-gray-400">Đang kiểm tra key…</p>\n          <p class="text-[11px] text-gray-500">Tự dùng key OpenRouter Free trong OpenCode. Chỉ nhận model có đuôi :free; cần Internet và có giới hạn lượt dùng.</p>\n          <button type="button" id="btn-test-openrouter" class="px-3 py-1.5 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs font-semibold transition">Kiểm tra OpenRouter Free</button>\n          <span id="openrouter-test-result" role="status" class="block text-xs text-gray-400"></span>\n        </div>\n\n        <details class="rounded-xl border border-gray-700 p-3 space-y-2">\n          <summary class="text-xs text-gray-300 cursor-pointer">OpenCode Zen · Muse Spark Free</summary>\n          <label for="settings-opencode-model" class="text-xs text-gray-300 block">Model OpenCode Free:</label>\n          <select id="settings-opencode-model" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="muse-spark-1.3-contributor-free">muse-spark-1.3-contributor-free</option>\n          </select>\n          <p id="opencode-auth-status" role="status" class="text-xs text-gray-400">Đang kiểm tra key OpenCode trên máy…</p>\n          <p class="text-[11px] text-gray-500">Dùng model miễn phí qua OpenCode đã đăng nhập trên máy, không cần Chrome. Nếu dịch vụ từ chối hoặc hết hạn mức, tác vụ báo lỗi và giữ nguyên nhà cung cấp đã chọn.</p>\n          <button type="button" id="btn-test-opencode" class="px-3 py-1.5 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs font-semibold transition">Kiểm tra kết nối OpenCode</button>\n          <span id="opencode-test-result" role="status" class="block text-xs text-gray-400"></span>\n        </details>\n\n        <div>\n          <label for="settings-translation-workers" class="text-xs text-gray-300 block mb-1">Luồng dịch song song</label>\n          <select id="settings-translation-workers" aria-describedby="settings-translation-workers-help" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pink-500">\n            \n            <option value="1" selected>1 luồng</option>\n            \n            <option value="3" >3 luồng</option>\n            \n            <option value="5" >5 luồng</option>\n            \n            <option value="10" >10 luồng</option>\n            \n            <option value="15" >15 luồng</option>\n            \n            <option value="20" >20 luồng</option>\n            \n            <option value="30" >30 luồng</option>\n            \n            <option value="40" >40 luồng</option>\n            \n            <option value="50" >50 luồng</option>\n            \n          </select>\n          <p id="settings-translation-workers-help" class="text-[11px] text-gray-400 mt-1">Số yêu cầu dịch và kiểm tra AI đồng thời tối đa cho mỗi video. Mặc định 20; có thể chọn 30, 40 hoặc 50. Nhận giọng, tạo giọng và dựng video dùng giới hạn riêng.</p>\n        </div>\n\n        <div class="border-t border-gray-800 pt-3">\n          <label class="text-xs text-gray-300 block mb-1">DeepSeek API Key (Tùy chọn):</label>\n          <input type="password" id="settings-deepseek-key" placeholder="sk-..." class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500 font-mono">\n        </div>\n\n        <details class="rounded-xl border border-gray-700 p-3 space-y-3">\n          <summary class="text-xs text-gray-300 cursor-pointer">Muse qua tài khoản Meta (thử nghiệm)</summary>\n          <p class="text-xs text-gray-400">Kết nối phiên Muse đã đăng nhập trong Chrome của bạn hoặc dùng Chrome riêng. Hạn mức và model phía sau do Muse quyết định.</p>\n          <label for="settings-muse-browser-mode" class="text-xs text-gray-300 block">Phiên Chrome cho Muse</label>\n          <select id="settings-muse-browser-mode" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="existing">Chrome đang dùng — giữ phiên Muse đã đăng nhập</option>\n            <option value="dedicated">Chrome riêng của tool — đăng nhập một lần</option>\n          </select>\n          <p class="text-xs text-gray-400">Lưu cấu hình trước khi kết nối. Với Chrome đang dùng, mở <code>chrome://inspect/#remote-debugging</code>, bật <strong>Allow remote debugging for this browser instance</strong>, rồi cho phép khi Chrome hỏi kết nối. Quyền này cho ứng dụng local điều khiển trình duyệt; tool chỉ thao tác tab Muse do nó mở. Có thể tắt lại sau khi dùng.</p>\n          <p id="muse-status" role="status" class="text-xs text-gray-400">Đang kiểm tra cài đặt Muse…</p>\n          <div class="flex flex-wrap gap-2">\n            <button type="button" id="btn-muse-login" class="px-3 py-2 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs">Kết nối Muse</button>\n            <button type="button" id="btn-muse-status" class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-gray-800 text-gray-300 text-xs">Kiểm tra đăng nhập</button>\n            <button type="button" id="btn-test-muse" class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-gray-800 text-gray-300 text-xs">Thử dịch Muse</button>\n          </div>\n        </details>\n      </div>\n\n      <!-- Voice & Audio Settings -->\n      <div class="bg-[#12151f] border border-gray-800 rounded-2xl p-5 shadow-xl space-y-4">\n        <h3 class="text-xs font-bold text-violet-400 uppercase tracking-wider flex items-center gap-2">\n          <i class="fa-solid fa-headphones"></i> 2. Âm Thanh & Tách Giọng\n        </h3>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Engine Tách Thoại Realtime:</label>\n          <select id="settings-suppression-mode" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="AUTO" selected>Tự động nhận diện (Stereo Cancel hoặc Formant Notch - Khuyên dùng)</option>\n            <option value="DSP_MONO_ADAPTIVE_FORMANT">Đơn kênh: Multi-band Formant Notch (-20dB)</option>\n            <option value="DSP_STEREO_CENTER_CANCEL">Đa kênh: Mid-Side Phase Cancel (-26dB)</option>\n          </select>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Engine Xuất Video Hoàn Chỉnh (HQ):</label>\n          <select class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500" disabled>\n            <option selected>realtime (theo cấu hình máy)</option>\n          </select>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Mức giảm BGM khi có thoại (Sidechain Ducking):</label>\n          <select id="settings-ducking-level" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="-12">-12 dB (Nhẹ nhàng)</option>\n            <option value="-14" selected>-14 dB (Tiêu chuẩn TikTok - Khuyên dùng)</option>\n            <option value="-18">-18 dB (Rõ thoại tối đa)</option>\n            <option value="-24">-24 dB (Ngắt gần hết nhạc nền)</option>\n          </select>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Độ trễ đệm tối thiểu (Buffer Target):</label>\n          <select id="settings-buffer-target" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="5">5s (Nhanh nhất)</option>\n            <option value="10" selected>10s (Cân bằng tốt)</option>\n            <option value="15">15s (An toàn không giật)</option>\n            <option value="30">30s (Đệm dài)</option>\n          </select>\n        </div>\n      </div>\n\n    </div>\n\n    <!-- Save Settings Action -->\n    <div class="flex justify-end gap-3 pt-2">\n      <button id="btn-save-settings" class="bg-gradient-to-r from-pink-500 via-rose-500 to-violet-600 hover:opacity-95 text-white font-bold text-xs px-6 py-2.5 rounded-xl shadow-lg transition active:scale-[0.98]">\n        <i class="fa-solid fa-floppy-disk mr-1.5"></i> Lưu Cấu Hình Hệ Thống\n      </button>\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- MODAL: HQ EXPORT -->\n  <!-- ======================================================== -->\n  <div id="export-modal" role="dialog" aria-modal="true" aria-labelledby="export-modal-title" class="fixed inset-0 bg-black/85 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">\n    <div class="bg-[#161922] border border-gray-800 rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl">\n      <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n        <h3 id="export-modal-title" class="text-sm font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-film text-pink-500"></i> Xuất video MP4\n        </h3>\n        <button id="btn-close-export-modal" aria-label="Đóng hộp thoại xuất video" class="text-gray-400 hover:text-white">\n          <i class="fa-solid fa-xmark text-lg"></i>\n        </button>\n      </div>\n\n      <div id="export-modal-body" class="space-y-4 text-xs text-gray-300">\n        <p>Xuất MP4 với giọng Việt và phụ đề, giữ tỉ lệ video gốc. Dùng lại âm thanh nền đã xử lý khi phù hợp với chế độ realtime đang chọn. Lần đầu tách nền bằng RoFormer có thể mất nhiều thời gian; những lần sửa lời sau sẽ dùng lại nền đã lưu.</p>\n        <p id="export-screen-text-summary" class="export-screen-text-summary" role="status"></p>\n        <p id="export-incomplete-warning" class="hidden p-3 rounded-xl bg-amber-950/60 border border-amber-700 text-amber-200" role="status"></p>\n        \n        <div id="export-status-box" class="hidden bg-[#08090d] p-3.5 rounded-xl border border-gray-800 space-y-3">\n          <div class="flex items-center justify-between text-xs">\n            <span id="export-status-text" class="text-pink-300 font-medium flex items-center gap-2">\n              <i class="fa-solid fa-spinner fa-spin text-pink-400"></i> Đang chuẩn bị xuất video...\n            </span>\n            <span class="flex flex-wrap items-baseline justify-end gap-x-2"><span id="export-progress-pct" class="font-mono font-bold text-pink-400">0%</span><span id="export-progress-eta" class="hidden text-gray-400"></span></span>\n          </div>\n          <div class="w-full bg-gray-800 h-2 rounded-full overflow-hidden">\n            <div id="export-progress-bar" class="bg-gradient-to-r from-pink-500 via-rose-500 to-violet-500 h-full transition-all duration-300" style="width: 0%"></div>\n          </div>\n          <div class="flex justify-end pt-1">\n            <button id="btn-cancel-export" class="px-3 py-1 rounded-lg bg-rose-950/80 hover:bg-rose-900 border border-rose-800 text-rose-300 text-[11px] font-semibold flex items-center gap-1.5 transition">\n              <i class="fa-solid fa-ban"></i> Hủy xuất video\n            </button>\n          </div>\n        </div>\n\n        <div id="export-result-box" class="hidden space-y-3">\n          <div class="p-3 bg-emerald-950/60 border border-emerald-800/80 rounded-xl text-emerald-300 flex items-center gap-2">\n            <i class="fa-solid fa-circle-check text-lg"></i>\n            <span>Xuất video hoàn tất thành công!</span>\n          </div>\n          <p id="export-result-warning" class="hidden text-amber-200" role="status"></p>\n          <button id="btn-save-as-native" class="w-full py-2.5 bg-pink-600 hover:bg-pink-500 text-white font-bold rounded-xl transition flex items-center justify-center gap-2">\n            <i class="fa-solid fa-floppy-disk"></i> Lưu Video Về Máy (Save Video As...)\n          </button>\n        </div>\n\n        <div id="export-actions" class="flex justify-end gap-2 pt-2">\n          <button id="btn-confirm-export" class="py-2.5 px-4 bg-gradient-to-r from-pink-500 to-violet-600 text-white font-bold rounded-xl transition hover:opacity-95">\n            Bắt đầu xuất video HQ\n          </button>\n        </div>\n      </div>\n    </div>\n  </div>\n\n  <div id="result-preview-modal" role="dialog" aria-modal="true" aria-labelledby="result-preview-title" class="fixed inset-0 bg-black/85 z-50 flex items-center justify-center p-4 hidden">\n    <div class="bg-[#161922] border border-gray-800 rounded-2xl max-w-4xl w-full p-4 space-y-3 shadow-2xl">\n      <div class="flex items-center justify-between gap-3">\n        <h3 id="result-preview-title" class="text-sm font-bold text-white">Xem trước kết quả</h3>\n        <button id="btn-save-preview-result" class="text-xs text-pink-200 px-3 py-1.5 rounded-lg border border-pink-700">Tải video đầy đủ đã dịch</button>\n        <button id="btn-close-result-preview" class="text-xs text-gray-300 px-3 py-1.5 rounded-lg border border-gray-700">Đóng</button>\n      </div>\n      <video id="result-preview-video" controls playsinline preload="metadata" class="w-full bg-black rounded-lg" style="max-height: 75vh;"></video>\n      <p id="result-preview-status" role="status" class="text-xs text-rose-300"></p>\n    </div>\n  </div>\n\n  <script src="/static/studio-layout.js?v=1791942773275170000"></script>\n  <script src="/qtwebchannel.js"></script>\n  <script src="/static/app.js?v=f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527"></script>\n</body>\n</html>'`

```text
self = <test_local_api.LocalAPITests testMethod=test_home_renders_configured_engines_and_voice>

    def test_home_renders_configured_engines_and_voice(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'data-asr-engine="{settings.ASR_ENGINE}"', response.text)
        self.assertIn(f'data-tts-engine="{settings.TTS_ENGINE}"', response.text)
        self.assertIn(settings.WHISPER_MODEL_SIZE, response.text)
>       self.assertIn(settings.EDGE_VOICE, response.text)
E       AssertionError: 'vi-VN-HoaiMyNeural' not found in '<!DOCTYPE html>\n<html lang="vi">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n  <title>Douyin2TikTok AI Studio - Desktop Application</title>\n  <meta name="studio-source-revision" content="f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527">\n  <meta name="studio-backend-revision" content="f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527">\n  <script src="https://cdn.tailwindcss.com"></script>\n  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">\n  <link rel="stylesheet" href="/static/style.css?v=1793889200510015900">\n  <link rel="stylesheet" href="/static/studio-compact-panels.css?v=1791943729577814900">\n</head>\n<body data-asr-engine="faster-whisper" data-tts-engine="piper-tts" data-whisper-model="small" class="bg-[#0b0d13] text-gray-100 min-h-screen font-sans antialiased selection:bg-pink-500 selection:text-white">\n  <div id="runtime-build-warning" role="alert" hidden style="background:#7f1d1d;color:#fff;padding:10px 16px;font-weight:600">\n    Giao diện và backend đang dùng khác phiên bản source. Hãy thoát hẳn Studio rồi mở lại; Retry/Tiếp tục đang bị khóa để bảo vệ checkpoint.\n  </div>\n  \n  <header class="app-header">\n    <div class="app-brand">\n      <div class="app-logo" aria-hidden="true"><img src="/static/studio-mark.svg" alt=""></div>\n      <div class="app-brand-copy">\n        <h1>Douyin2TikTok AI Studio</h1>\n        <p>Dịch video · Lồng tiếng · Biên tập phụ đề</p>\n      </div>\n    </div>\n    <nav class="app-navigation" aria-label="Điều hướng ứng dụng">\n      <button id="tab-studio" class="nav-btn text-white bg-pink-600/80"><i class="fa-solid fa-house" aria-hidden="true"></i> Studio</button>\n      <button id="tab-tasks" class="nav-btn text-gray-400"><i class="fa-solid fa-list-check" aria-hidden="true"></i> Lịch sử video</button>\n      <button id="tab-models" class="nav-btn text-gray-400"><i class="fa-solid fa-layer-group" aria-hidden="true"></i> Models</button>\n      <button id="tab-diagnostics" class="nav-btn text-gray-400"><i class="fa-solid fa-wave-square" aria-hidden="true"></i> Diagnostics</button>\n      <button id="tab-settings" class="nav-btn text-gray-400"><i class="fa-solid fa-gear" aria-hidden="true"></i> Cài đặt</button>\n    </nav>\n    <div id="hardware-pill" class="hardware-pill"><i class="fa-solid fa-microchip" aria-hidden="true"></i><span id="gpu-info-text">Đang kiểm tra phần cứng…</span></div>\n  </header>\n\n  <div id="view-studio" class="app-view">\n    <section class="telemetry-ribbon" aria-label="Trạng thái phát và xử lý">\n      <div class="telemetry-metrics">\n        <div class="telemetry-item"><i class="fa-solid fa-circle" aria-hidden="true"></i><span>Đang phát:</span><strong id="tel-playing">00:00</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-play" aria-hidden="true"></i><span>Đã sẵn sàng:</span><strong id="tel-playable">00:00</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-database" aria-hidden="true"></i><span>Buffer:</span><strong id="tel-buffer">+0.0s</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-stopwatch" aria-hidden="true"></i><span>TTFP:</span><strong id="tel-ttfp">--</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-gauge-high" aria-hidden="true"></i><span>RTF:</span><strong id="tel-rtf">0.0x</strong></div>\n        <div class="telemetry-item"><i class="fa-solid fa-sliders" aria-hidden="true"></i><span>Lọc thoại (ước tính):</span><strong id="tel-suppression">--</strong></div>\n      </div>\n      <div class="telemetry-workers"><span>Workers:</span><span id="worker-asr-badge">ASR: Idle</span><span id="worker-trans-badge">Dịch: Idle</span><span id="worker-tts-badge">TTS: Idle</span></div>\n    </section>\n    <div class="studio-layout-toolbar">\n      <span id="studio-layout-hint">Kéo đường chia giữa các cột hoặc các khối để đổi kích thước.</span>\n      <button id="btn-reset-layout" type="button"><i class="fa-solid fa-up-right-and-down-left-from-center" aria-hidden="true"></i> Khôi phục bố cục</button>\n      <span id="studio-layout-status" class="sr-only" role="status" aria-live="polite"></span>\n    </div>\n    <main id="studio-layout" class="studio-workspace" data-layout-group="columns" aria-describedby="studio-layout-hint">\n      <aside id="studio-left" class="studio-controls studio-column" data-layout-group="left" aria-label="Nguồn video và giọng lồng tiếng">\n        <section id="studio-source" class="studio-source-card studio-card" aria-labelledby="source-heading">\n          <h2 id="source-heading" class="studio-card-heading"><span class="step-number" aria-hidden="true">1</span>Video nguồn <small>(Douyin / URL / File)</small></h2>\n          <p class="panel-description">Dán nội dung chia sẻ hoặc tải video từ máy.</p>\n          <div class="source-tabs" role="group" aria-label="Kiểu nguồn video">\n            <button id="source-tab-url" type="button" class="source-tab is-active" aria-pressed="true"><i class="fa-solid fa-link" aria-hidden="true"></i> URL</button>\n            <button id="source-tab-file" type="button" class="source-tab" aria-pressed="false"><i class="fa-regular fa-file-video" aria-hidden="true"></i> Tải file</button>\n            <button id="source-tab-library" type="button" class="source-tab" aria-pressed="false"><i class="fa-regular fa-folder-open" aria-hidden="true"></i> Thư viện</button>\n          </div>\n          <div id="source-url-panel">\n            <div class="source-url-field">\n              <i class="fa-solid fa-link" aria-hidden="true"></i>\n              <textarea id="video-url" rows="1" wrap="off" spellcheck="false" aria-label="Link video hoặc nội dung chia sẻ" aria-describedby="video-url-help video-url-status" placeholder="Dán link video…"></textarea>\n              <button id="clear-video-url" type="button" aria-label="Xóa link video" title="Xóa link"><i class="fa-solid fa-circle-xmark" aria-hidden="true"></i></button>\n            </div>\n            <p id="video-url-help" class="source-help">Tự nhận link từ nội dung chia sẻ.</p>\n            <p id="video-url-status" role="status" aria-live="polite" class="hidden source-help"></p>\n          </div>\n          <div id="source-library-panel" class="hidden source-library-panel">\n            <p id="library-status" class="source-help" role="status">Chọn video đã có trong thư viện.</p>\n            <div id="library-list" class="source-library-list" aria-label="Video trong thư viện"></div>\n          </div>\n          <div id="drop-zone" role="button" tabindex="0" aria-label="Chọn video từ máy" class="source-drop-zone">\n            <input type="file" id="video-file" accept="video/*" class="hidden">\n            <i class="fa-solid fa-cloud-arrow-up" aria-hidden="true"></i>\n            <p id="file-name-display">Kéo &amp; thả video vào đây</p>\n            <span>Hoặc bấm để chọn file từ máy tính<br>MP4, MOV, WebM, MKV</span>\n          </div>\n        </section>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="0" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="studio-source studio-voice" aria-label="Chiều cao nguồn video và giọng lồng tiếng" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <section id="studio-voice" class="voice-panel studio-card" aria-labelledby="voice-list-title">\n          <div class="voice-list-heading"><h3 id="voice-list-title"><i class="fa-solid fa-microphone-lines" aria-hidden="true"></i>2. Giọng lồng tiếng</h3><span id="voice-count" class="voice-count" role="status" aria-live="polite"></span></div>\n          <p id="voice-source" class="voice-source" aria-live="polite">Đang tải danh sách giọng…</p>\n          <input type="hidden" id="voice-select" value="Trúc Ly">\n          <label for="voice-search" class="sr-only">Tìm theo tên, vùng miền hoặc nguồn</label>\n          <div class="voice-search-controls"><div class="voice-search-field"><i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i><input id="voice-search" type="search" placeholder="Tìm theo tên, vùng miền hoặc nguồn" autocomplete="off" class="voice-search" aria-controls="voice-list"></div><button id="voice-filter" type="button" aria-label="Lọc giọng theo nguồn" title="Lọc giọng theo nguồn"><i class="fa-solid fa-filter" aria-hidden="true"></i><span>Tất cả</span></button></div>\n          <div id="voice-list" class="voice-list" role="group" aria-labelledby="voice-list-title" tabindex="0"></div>\n          <p id="voice-empty" class="hidden voice-source">Không tìm thấy giọng phù hợp. Thử tên hoặc nguồn khác.</p>\n          <p id="voice-catalog-status" class="voice-source" role="status"></p>\n          <div class="voice-preview-panel"><div class="voice-preview-controls"><audio id="voice-preview-audio" class="hidden" controls preload="none" aria-label="Mẫu giọng lồng tiếng"></audio></div><p id="voice-preview-status" class="voice-preview-status" role="status" aria-live="polite" aria-atomic="true"></p></div>\n        </section>\n      </aside>\n        <div class="studio-splitter" data-layout-axis="x" data-layout-index="0" role="separator" tabindex="0" aria-orientation="vertical" aria-controls="studio-left studio-middle" aria-label="Chiều rộng cột nguồn và cột video" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n      <section id="studio-middle" class="studio-preview studio-column" data-layout-group="middle" aria-label="Video, phụ đề và âm thanh">\n        <div id="studio-player" class="studio-player-card studio-card">\n          <div class="studio-player-heading">\n            <h2 class="studio-card-heading"><i class="fa-solid fa-video" aria-hidden="true"></i>Xem trước video</h2>\n            <div class="player-options">\n              <label title="Hiện phụ đề lời thoại tiếng Việt ở dưới video."><input type="checkbox" id="toggle-subtitles" checked> Hiện Sub Việt</label>\n              <label title="Giữ sub Trung và đặt lời Việt ngay bên dưới. Nếu không thấy sub gốc, đặt lời Việt nhỏ ở đáy hình."><input type="checkbox" id="toggle-screen-text" checked aria-label="Tự đặt lời Việt dưới phụ đề Trung"> Tự đặt vị trí sub</label>\n              <input type="checkbox" id="toggle-mask-chinese" hidden disabled aria-hidden="true">\n            </div>\n          </div>\n          <div class="player-surface">\n            <div class="studio-video-stage">\n              <div id="player-container">\n                <video id="video-player" playsinline></video>\n                <div id="chinese-sub-mask" class="absolute bottom-16 left-0 right-0 h-14 bg-black/65 backdrop-blur-md pointer-events-none transition-opacity duration-300"></div>\n                <div id="subtitle-overlay" class="absolute bottom-16 left-4 right-4 text-center pointer-events-none z-10"><span id="subtitle-text"></span></div>\n                <div id="screen-text-overlay" class="screen-text-overlay" aria-hidden="true"></div>\n                <div id="buffering-alert" role="status" aria-live="polite" class="hidden player-buffering-overlay">\n                  <i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i>\n                  <p id="buffering-text">Đang tải giọng lồng tiếng...</p>\n                  <div id="player-download-progress" class="player-download-progress hidden" aria-live="polite">\n                    <div class="player-progress-heading"><strong id="player-progress-value">Chưa có %</strong><span id="player-progress-detail">Đang chờ số liệu từ máy chủ…</span></div>\n                    <div id="player-progress-track" class="player-progress-track" role="progressbar" aria-label="Tiến độ tải video" aria-valuemin="0" aria-valuemax="100"><div id="player-progress-bar" class="player-progress-bar"></div></div>\n                  </div>\n                </div>\n                <div id="player-placeholder" class="player-placeholder"><img class="empty-video-art" src="/static/studio-empty.svg" alt=""><p>Chưa có video được nạp</p><span>Dán link Douyin/TikTok hoặc tải video từ máy tính để bắt đầu dịch và lồng tiếng.</span><div class="preview-features" aria-hidden="true"><span><i class="fa-solid fa-film"></i>Nhiều định dạng</span><span><i class="fa-regular fa-circle-play"></i>Xem trước trực tiếp</span><span><i class="fa-solid fa-font"></i>Chỉnh phụ đề</span><span><i class="fa-solid fa-volume-high"></i>Giọng lồng tiếng</span></div></div>\n              </div>\n            </div>\n            <p id="player-task-status" class="player-task-status hidden" role="status"></p>\n            <div class="player-transport">\n              <div id="segments-timeline-track" role="slider" tabindex="0" aria-label="Vị trí phát video" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"><div id="playback-head-marker" style="left: 0%;"></div></div>\n              <div class="player-transport-row">\n                <button id="player-seek-backward" class="player-seek" type="button" aria-label="Lùi 5 giây" title="Lùi 5 giây"><i class="fa-solid fa-rotate-left" aria-hidden="true"></i><span aria-hidden="true">5</span></button>\n                <button id="player-play-toggle" type="button" aria-label="Phát video" title="Phát / tạm dừng"><i class="fa-solid fa-play" aria-hidden="true"></i></button>\n                <button id="player-seek-forward" class="player-seek" type="button" aria-label="Tiến 5 giây" title="Tiến 5 giây"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i><span aria-hidden="true">5</span></button>\n                <span class="player-time"><span id="bar-current-time">00:00</span><span class="player-time-divider">/</span><span id="bar-total-time">00:00</span></span>\n                <span id="bar-buffer-info" class="player-buffer-info">Buffer: 0.0s</span>\n                <button id="player-mute-toggle" type="button" aria-label="Tắt tiếng video" title="Bật / tắt tiếng"><i class="fa-solid fa-volume-high" aria-hidden="true"></i></button>\n                <input id="player-volume" type="range" min="0" max="1" step="0.05" value="1" aria-label="Âm lượng video">\n                <button id="player-fullscreen" type="button" aria-label="Xem video toàn màn hình" title="Toàn màn hình"><i class="fa-solid fa-expand" aria-hidden="true"></i></button>\n                <button id="player-settings" type="button" aria-label="Mở cài đặt phát và xử lý" title="Cài đặt"><i class="fa-solid fa-gear" aria-hidden="true"></i></button>\n              </div>\n            </div>\n          </div>\n        </div>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="0" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="studio-player caption-style-panel" aria-label="Chiều cao video và chỉnh phụ đề" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <details id="caption-style-panel" class="caption-style-panel" open>\n          <summary>Chỉnh phụ đề <span>Nền · font · cỡ chữ · vị trí</span></summary>\n          <fieldset id="caption-style-controls" disabled aria-describedby="caption-style-availability caption-style-status">\n            <div class="caption-style-grid">\n              <label>Màu chữ<input id="caption-text-color" type="color" value="#000000" aria-label="Màu chữ phụ đề"></label>\n              <label>Màu nền<input id="caption-background-color" type="color" value="#FFE500" aria-label="Màu nền phụ đề"></label>\n              <label>Vị trí<select id="caption-position"><option value="auto">Tự động</option><option value="top">Phía trên</option><option value="middle">Ở giữa</option><option value="bottom">Phía dưới</option></select></label>\n            </div>\n            <div class="caption-typography-grid">\n              <label class="caption-font-control" for="caption-font-family">Font chữ<select id="caption-font-family"><option value="Arial" style="font-family:Arial,sans-serif">Arial</option><option value="Segoe UI" style="font-family:\'Segoe UI\',sans-serif">Segoe UI</option><option value="Tahoma" style="font-family:Tahoma,sans-serif">Tahoma</option><option value="Verdana" style="font-family:Verdana,sans-serif">Verdana</option><option value="Times New Roman" style="font-family:\'Times New Roman\',serif">Times New Roman</option></select></label>\n              <div class="caption-size-control"><label for="caption-font-size-pt">Cỡ chữ</label><span class="caption-point-input"><input id="caption-font-size-pt" type="number" list="caption-font-sizes" min="6" max="96" step="0.5" placeholder="Tự động" aria-label="Cỡ chữ phụ đề theo point, từ 6 đến 96"><span aria-hidden="true">pt</span></span><button id="caption-font-size-auto" class="caption-auto-size" type="button" aria-pressed="true" title="Tự căn cỡ chữ theo video và độ dài câu">Tự động</button><label class="caption-bold-button" title="Chữ đậm"><input id="caption-font-bold" type="checkbox" checked aria-label="Chữ đậm phụ đề"><span aria-hidden="true">B</span></label></div>\n              <datalist id="caption-font-sizes"><option value="8"></option><option value="9"></option><option value="10"></option><option value="11"></option><option value="12"></option><option value="14"></option><option value="16"></option><option value="18"></option><option value="20"></option><option value="24"></option><option value="28"></option><option value="32"></option><option value="36"></option><option value="48"></option><option value="72"></option><option value="96"></option></datalist>\n              <div hidden class="hidden" aria-hidden="true"><input id="caption-font-scale" type="range" min="50" max="200" step="1" value="100" tabindex="-1"><input id="caption-font-scale-number" type="number" min="50" max="200" step="1" value="100" tabindex="-1"></div>\n              <div class="caption-opacity-control"><label for="caption-background-opacity">Độ đậm nền</label><input id="caption-background-opacity" type="range" min="0" max="100" step="1" value="100" aria-label="Độ đậm nền phụ đề"><output id="caption-background-opacity-value" for="caption-background-opacity">100%</output></div>\n            </div>\n            <div class="caption-style-options">\n              <label title="Dùng nền vàng thống nhất cho tất cả câu; bỏ chọn để chọn màu khác."><input id="caption-auto-background" type="checkbox" checked> Dùng nền mặc định (vàng)</label>\n              <label title="Bỏ nền phụ đề, giữ chữ và vùng làm mờ sub gốc nếu đã bật"><input id="caption-transparent-background" type="checkbox"> Nền trong suốt</label>\n              <label title="Viền tương phản giúp đọc rõ trên nền trong suốt"><input id="caption-font-outline" type="checkbox"> Viền chữ</label>\n              <label title="Chỉ làm mờ vùng phụ đề gốc đã nhận diện"><input id="caption-blur-original" type="checkbox"> Làm mờ sub gốc</label>\n              <button id="caption-style-reset" type="button">Mặc định</button>\n            </div>\n          </fieldset>\n          <p id="caption-style-availability" class="mt-2 text-[10px] leading-relaxed text-slate-300" role="status" aria-live="polite">Mở dự án đã dịch hoặc tạo bản xem trước có giọng Việt để chỉnh phụ đề.</p>\n          <p id="caption-style-status" role="status" aria-live="polite">Kiểu phụ đề được lưu cho bản xem trước và video xuất.</p>\n        </details>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="1" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="caption-style-panel studio-audio" aria-label="Chiều cao phụ đề và âm thanh" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <section id="studio-audio" class="studio-audio-panel" aria-labelledby="audio-heading">\n          <h3 id="audio-heading"><i class="fa-solid fa-headphones" aria-hidden="true"></i>Điều chỉnh âm thanh</h3>\n          <div class="audio-mixers">\n            <div class="audio-mixer"><i class="fa-solid fa-microphone-lines" aria-hidden="true"></i><label for="vol-dub">Giọng Việt</label><input type="range" id="vol-dub" min="0" max="1" step="0.05" value="1.0"><span id="vol-dub-val">100%</span></div>\n            <div class="audio-mixer audio-mixer-bgm"><i class="fa-solid fa-music" aria-hidden="true"></i><label for="vol-bgm">BGM & SFX</label><input type="range" id="vol-bgm" min="0" max="1" step="0.05" value="0.30"><span id="vol-bgm-val">30%</span></div>\n          </div>\n          <div class="audio-suppression"><i class="fa-solid fa-wave-square" aria-hidden="true"></i><span>Lọc thoại Trung:</span><strong id="player-suppression-badge" title="Mức giảm ước tính của bộ lọc">--</strong><span class="audio-divider">|</span><span class="audio-bgm-label"><i class="fa-solid fa-music" aria-hidden="true"></i> BGM:</span><span id="player-bgm-status">Chưa có</span></div>\n        </section>\n      </section>\n        <div class="studio-splitter" data-layout-axis="x" data-layout-index="1" role="separator" tabindex="0" aria-orientation="vertical" aria-controls="studio-middle studio-right" aria-label="Chiều rộng cột video và cột transcript" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n      <aside id="studio-right" class="studio-sidebar studio-column" data-layout-group="right" aria-label="Transcript, dịch và trạng thái xử lý">\n        <section id="studio-transcript" class="transcript-panel studio-card" aria-labelledby="transcript-heading">\n          <div class="transcript-heading"><h2 id="transcript-heading"><i class="fa-regular fa-file-lines" aria-hidden="true"></i>Transcript</h2><span id="segments-count-badge" class="transcript-count">0 câu</span><p>Bấm vào câu để tua video. Bấm bản dịch để sửa từng chữ.</p></div>\n          <div class="transcript-toolbar"><label class="transcript-follow"><input id="transcript-follow" type="checkbox" checked> Theo vị trí phát</label><label class="transcript-option"><input id="transcript-show-time" type="checkbox" checked> Hiện thời gian</label><span class="transcript-time-note">Theo câu</span></div>\n          <div id="segments-list" class="transcript-list" aria-label="Câu thoại gốc và bản dịch" tabindex="0"><div class="transcript-empty"><i class="fa-regular fa-file-lines" aria-hidden="true"></i><p>Chưa có nội dung transcript</p><span>Nạp video và bắt đầu dịch để xem, sửa và nghe lại cạnh khung hình.</span></div></div>\n          <p id="transcript-status" class="transcript-status" role="status" aria-live="polite">Bản sửa sẽ cập nhật phụ đề và tạo lại giọng đọc, giữ nguyên mốc thời gian.</p>\n        </section>\n        <div class="studio-splitter" data-layout-axis="y" data-layout-index="0" role="separator" tabindex="0" aria-orientation="horizontal" aria-controls="studio-transcript studio-action" aria-label="Chiều cao transcript và phần dịch" title="Kéo để đổi kích thước; dùng phím mũi tên khi chọn đường chia"></div>\n        <section id="studio-action" class="studio-action-card studio-card" aria-labelledby="action-heading">\n          <h2 id="action-heading" class="studio-card-heading"><i class="fa-solid fa-microphone-lines" aria-hidden="true"></i>3. Dịch và lồng tiếng</h2>\n          <div class="buffer-setting"><label for="start-translation-workers">Luồng dịch video này</label><select id="start-translation-workers" aria-describedby="start-translation-workers-help">\n            <option value="1" >1 luồng</option><option value="3" >3 luồng</option><option value="5" >5 luồng</option><option value="10" >10 luồng</option><option value="15" >15 luồng</option><option value="20" selected>20 luồng</option><option value="30" >30 luồng</option><option value="40" >40 luồng</option><option value="50" >50 luồng</option>\n          </select></div>\n          <p id="start-translation-workers-help" class="task-progress-detail">Mặc định 20. Áp dụng khi bắt đầu video mới; dự án mở lại giữ số luồng đã lưu.</p>\n          <div class="studio-primary-actions" role="group" aria-label="Dịch và lưu video">\n            <button id="btn-start" class="studio-start-button"><i class="fa-solid fa-play" aria-hidden="true"></i>Bắt đầu dịch</button>\n            <button id="btn-retry-worker" class="hidden" title="Tiếp tục tác vụ từ phần đã lưu" aria-label="Tiếp tục tác vụ"><i class="fa-solid fa-rotate-right" aria-hidden="true"></i> Tiếp tục</button>\n            <button id="btn-export-hq" aria-label="Xuất video MP4" disabled title="Xuất video từ lời và giọng hiện đã lưu" class="studio-export-button"><i class="fa-solid fa-arrow-up-from-bracket" aria-hidden="true"></i>Xuất MP4</button>\n            <button id="btn-save-result" class="studio-export-button" disabled title="Có thể lưu sau khi xuất xong toàn bộ video"><i class="fa-solid fa-download" aria-hidden="true"></i>Tải video</button>\n            <button id="btn-pause-worker" class="hidden"><i class="fa-solid fa-pause" aria-hidden="true"></i> Tạm dừng</button>\n            <button id="btn-resume-worker" class="hidden"><i class="fa-solid fa-play" aria-hidden="true"></i> Tiếp tục</button>\n            <button id="btn-stop-worker" class="hidden"><i class="fa-solid fa-stop" aria-hidden="true"></i> Dừng hẳn</button>\n          <button id="btn-translate-full" class="studio-start-button hidden" type="button"><i class="fa-solid fa-forward-step" aria-hidden="true"></i>Dịch toàn bộ</button>\n            <button id="btn-review-worker" class="hidden"><i class="fa-solid fa-check-double" aria-hidden="true"></i> AI kiểm tra lại</button>\n          </div>\n          <div class="studio-action-options">\n          <label class="visual-translation-option" title="Đối chiếu lời nói với chữ trên video, rồi tự rà và sửa bản dịch trước khi tạo giọng."><input id="visual-translation" type="checkbox" aria-describedby="visual-translation-help" ><span>AI kiểm tra bản dịch</span></label>\n          <span id="visual-translation-help" class="sr-only">Đối chiếu lời nói với chữ trên video, rồi tự rà và sửa bản dịch trước khi tạo giọng.</span>\n          <div class="buffer-setting"><label for="buffer-select">Đệm trước khi phát</label><select id="buffer-select"><option value="5">5 giây (Nhanh nhất)</option><option value="10" selected>10 giây (Khuyên dùng)</option><option value="15">15 giây (Ổn định)</option><option value="30">30 giây (An toàn)</option></select></div>\n          </div>\n          <div id="task-progress" class="hidden task-progress" aria-label="Tiến độ xử lý video">\n            <p id="task-activity" class="task-activity" role="status" aria-live="polite">Đang kiểm tra trạng thái xử lý</p>\n            <p id="task-activity-age" class="hidden task-activity-age"></p>\n            <p id="task-countdown" class="task-countdown" role="timer">Thời gian còn lại: chưa đủ dữ liệu</p>\n            <div class="task-progress-heading" role="status" aria-live="polite"><span id="task-progress-stage">Đang gửi yêu cầu…</span><span class="flex flex-wrap items-baseline justify-end gap-x-2"><span id="task-progress-value">Chưa có %</span><span id="task-progress-eta" class="hidden text-gray-400"></span></span></div>\n            <div id="task-progress-track" class="task-progress-track" role="progressbar" aria-label="Tiến độ bước hiện tại" aria-valuemin="0" aria-valuemax="100"><div id="task-progress-bar" class="task-progress-bar"></div></div>\n            <p id="task-media-progress" class="hidden task-media-progress"></p>\n            <p id="task-progress-detail" class="task-progress-detail">Phần trăm được tính riêng cho từng bước.</p>\n            <p id="task-connection-status" role="status" class="hidden task-connection-status"></p>\n            <details class="task-status-history" open>\n              <summary>Lịch sử trạng thái</summary>\n              <ol id="task-status-history"><li>Chưa có hoạt động được ghi nhận.</li></ol>\n            </details>\n          </div>\n          <div id="task-result" class="hidden" role="status">\n            <p id="task-result-status" class="task-progress-detail"></p>\n            <a id="task-result-link" class="studio-export-button hidden">Xem trước kết quả</a>\n          </div>\n        </section>\n      </aside>\n    </main>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 2. TASKS VIEW (TASK MANAGER) -->\n  <!-- ======================================================== -->\n  <div id="view-tasks" class="app-view hidden max-w-6xl mx-auto p-6 space-y-6">\n    <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-list-check text-teal-400"></i> Lịch sử video\n        </h2>\n        <p class="text-xs text-gray-400">Mỗi video hiện một lần cùng tiến độ mới nhất. Khi có nhiều bản, hệ thống tự mở bản có dữ liệu đã lưu; bản xem trước rỗng vẫn nằm trong “Các bản đã lưu”.</p>\n      </div>\n      <button id="btn-refresh-tasks" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n        <i class="fa-solid fa-rotate-right"></i> Làm mới\n      </button>\n    </div>\n\n    <!-- Active Tasks Table -->\n    <div class="history-table-container bg-[#12151f] border border-gray-800 rounded-2xl shadow-xl">\n      <table class="history-table w-full text-left text-xs text-gray-300">\n        <thead class="bg-gray-900/80 text-gray-400 uppercase text-[10px] border-b border-gray-800">\n          <tr>\n            <th class="p-3.5">Video</th>\n            <th class="p-3.5">Trạng thái &amp; Tiến độ</th>\n            <th class="p-3.5">Thời Lượng</th>\n            <th class="p-3.5 text-right">Thao tác</th>\n          </tr>\n        </thead>\n        <tbody id="tasks-table-body" class="divide-y divide-gray-800/60">\n          <!-- Dynamic Task Row -->\n          <tr>\n            <td colspan="4" class="p-8 text-center text-gray-500 font-sans">\n              Chưa có video trong lịch sử.\n            </td>\n          </tr>\n        </tbody>\n      </table>\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 3. MODELS VIEW (MODEL MANAGER) -->\n  <!-- ======================================================== -->\n  <div id="view-models" class="app-view hidden max-w-6xl mx-auto p-6 space-y-6">\n    <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-server text-violet-400"></i> Quản Lý Models & Checkpoints\n        </h2>\n        <p class="text-xs text-gray-400">Kiểm tra tính sẵn sàng, dung lượng bộ nhớ và trạng thái thiết bị của các AI Engine.</p>\n      </div>\n      <button id="btn-reload-models" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n        <i class="fa-solid fa-arrows-rotate"></i> Kiểm tra lại\n      </button>\n    </div>\n\n    <div class="bg-[#12151f] border border-gray-800 rounded-2xl overflow-hidden shadow-xl">\n      <table class="w-full text-left text-xs text-gray-300">\n        <thead class="bg-gray-900/80 text-gray-400 uppercase text-[10px] border-b border-gray-800">\n          <tr>\n            <th class="p-3.5">Engine</th>\n            <th class="p-3.5">Tên Model / Checkpoint</th>\n            <th class="p-3.5">Kích thước</th>\n            <th class="p-3.5">Thiết bị</th>\n            <th class="p-3.5">Trạng Thái</th>\n            <th class="p-3.5 text-right">Thao Tác</th>\n          </tr>\n        </thead>\n        <tbody id="models-full-table-body" class="divide-y divide-gray-800/60 font-mono">\n          <!-- Dynamic Models populated by JS -->\n        </tbody>\n      </table>\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 4. DIAGNOSTICS VIEW (LOG VIEWER) -->\n  <!-- ======================================================== -->\n  <div id="view-diagnostics" class="app-view hidden max-w-6xl mx-auto p-6 space-y-4">\n    <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-terminal text-yellow-400"></i> Nhật Ký & Chẩn Đoán (Diagnostics)\n        </h2>\n        <p class="text-xs text-gray-400">Theo dõi tải và dịch video · tự cập nhật mỗi 3 giây.</p>\n      </div>\n      <div class="flex items-center gap-2">\n        <button id="btn-copy-log" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n          <i class="fa-regular fa-copy"></i> Sao chép Log\n        </button>\n        <button id="btn-open-log-folder" class="px-3 py-1.5 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 flex items-center gap-1.5">\n          <i class="fa-regular fa-folder-open text-pink-400"></i> Mở Thư Mục Logs\n        </button>\n        <button id="btn-clear-log" class="px-3 py-1.5 rounded-lg border border-rose-800/60 bg-rose-950/40 hover:bg-rose-900/60 text-xs text-rose-300 flex items-center gap-1.5">\n          <i class="fa-solid fa-trash-can"></i> Xóa Log\n        </button>\n      </div>\n    </div>\n\n    <!-- Log Category Tabs -->\n    <div class="flex items-center gap-2 border-b border-gray-800 pb-2">\n      <button id="diag-warnings-tab" data-cat="warnings" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">Thông báo tác vụ</button>\n      <button data-cat="pipeline" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono font-bold bg-pink-600/80 text-white">Tác vụ</button>\n      <button data-cat="ai" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">AI</button>\n      <button data-cat="errors" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">Lỗi</button>\n      <button data-cat="app" class="diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white">Ứng dụng</button>\n      <div class="ml-auto flex items-center gap-2">\n        <label class="text-[11px] text-gray-400 flex items-center gap-1 cursor-pointer">\n          <input type="checkbox" id="chk-auto-scroll-log" checked class="rounded border-gray-700 text-pink-600">\n          Theo dòng mới\n        </label>\n        <button id="btn-refresh-log" aria-label="Làm mới nhật ký" title="Làm mới nhật ký" class="text-xs text-gray-400 hover:text-white p-1">\n          <i class="fa-solid fa-arrows-rotate"></i>\n        </button>\n      </div>\n    </div>\n\n    <section id="diagnostics-warnings-panel" aria-labelledby="diag-warnings-tab" class="hidden">\n      <p class="text-xs text-gray-400 mb-3">Thông báo của video đang mở trong Studio, tự cập nhật theo tác vụ.</p>\n      <p id="diagnostics-warnings-empty" class="text-sm text-gray-400">Chưa có thông báo cho video đang mở.</p>\n      <p id="pipeline-warning" role="status" class="hidden pipeline-warning"></p>\n    </section>\n    <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-400">\n      <p id="log-summary" role="status">Chọn danh mục để xem nhật ký.</p>\n      <p id="log-copy-status" role="status" aria-live="polite"></p>\n    </div>\n    <!-- Log Console Output -->\n    <div tabindex="0" aria-label="Nội dung nhật ký" class="bg-[#07090e] border border-gray-800 rounded-2xl p-4 font-mono text-xs text-gray-300 h-[520px] overflow-y-auto leading-relaxed shadow-inner" id="log-console-output">\n      Đang tải log...\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- 5. SETTINGS VIEW (SYSTEM & ENGINE CONFIGURATION) -->\n  <!-- ======================================================== -->\n  <div id="view-settings" class="app-view hidden max-w-4xl mx-auto p-6 space-y-6">\n    <div class="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-gray-800">\n      <div>\n        <h2 class="text-base font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-gear text-cyan-400"></i> Cài Đặt Hệ Thống & AI Engines\n        </h2>\n        <p class="text-xs text-gray-400">Cấu hình API dịch thuật, mô hình giọng đọc và quy tắc lọc âm thanh.</p>\n      </div>\n      <button id="btn-settings-back" type="button" class="px-3 py-2 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400">\n        <i class="fa-solid fa-arrow-left mr-1" aria-hidden="true"></i> Quay lại video\n      </button>\n    </div>\n\n    <section id="douyin-cookies-panel" aria-labelledby="douyin-cookies-heading" class="bg-[#12151f] border border-gray-800 rounded-2xl p-5 shadow-xl space-y-3">\n      <h3 id="douyin-cookies-heading" class="text-sm font-bold text-cyan-300">Tải video Douyin</h3>\n      <p class="text-xs text-gray-300 leading-relaxed">Dán toàn bộ nội dung chia sẻ vào ô <strong>Video nguồn</strong>, rồi bấm <strong>Bắt đầu</strong>. Studio thử tải video công khai miễn phí, không cần API key hoặc mở Chrome.</p>\n      <p class="text-xs text-gray-400 leading-relaxed">Khả năng tải phụ thuộc video và kết nối. Nếu cách tải công khai không dùng được, Studio thử cách tải dự phòng và cookie đã nhập nếu có.</p>\n      <details class="rounded-xl border border-gray-800 bg-[#08090d]/50">\n        <summary class="cursor-pointer rounded-xl px-3 py-3 text-xs font-semibold text-gray-300 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400">Nâng cao: cookie cho video cần đăng nhập</summary>\n        <div class="px-3 pb-3 space-y-3">\n          <p id="douyin-cookies-help" class="text-xs text-gray-400 leading-relaxed">Chỉ cần thử cách này khi bộ tải yêu cầu phiên đăng nhập. Phiên Chrome không tự chuyển sang Studio. Chọn tệp Netscape cookies <span class="font-mono">.txt</span> chỉ xuất từ Douyin sau khi đăng nhập (tối đa 1 MiB). Bản sao được lưu trên máy và chỉ dùng để tải video Douyin.</p>\n          <p id="douyin-cookies-state" class="text-xs text-gray-300">Chưa kiểm tra cookie đã nhập.</p>\n          <div class="flex flex-wrap items-center gap-2">\n            <input id="douyin-cookies-file" type="file" accept=".txt,text/plain" aria-label="Chọn tệp cookie Douyin" aria-describedby="douyin-cookies-help" class="hidden">\n            <button id="btn-import-douyin-cookies" type="button" class="px-3 py-2 rounded-xl border border-cyan-500/50 hover:bg-cyan-600/20 text-cyan-300 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed">Nhập cookie từ tệp…</button>\n            <button id="btn-refresh-douyin-cookies" type="button" class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-gray-800 text-gray-300 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed">Kiểm tra trạng thái</button>\n            <button id="btn-delete-douyin-cookies" type="button" disabled class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-rose-900/20 text-rose-300 text-xs font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed">Xóa cookie đã nhập</button>\n          </div>\n          <p id="douyin-cookies-status" role="status" aria-live="polite" aria-atomic="true" class="text-xs text-gray-400 leading-relaxed">Nhập cookie chưa xác nhận tải được video; cần chạy lại link Douyin để kiểm tra.</p>\n        </div>\n      </details>\n    </section>\n\n    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">\n      \n      <!-- AI Translation Settings -->\n      <div class="bg-[#12151f] border border-gray-800 rounded-2xl p-5 shadow-xl space-y-4">\n        <h3 class="text-xs font-bold text-pink-400 uppercase tracking-wider flex items-center gap-2">\n          <i class="fa-solid fa-language"></i> 1. AI Dịch Thuật (Translation)\n        </h3>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Nhà cung cấp (LLM Provider):</label>\n          <select id="settings-llm-provider" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="openrouter-free" >OpenRouter Free (miễn phí)</option>\n            <option value="ollama" selected>Ollama local · Qwen2.5 7B</option>\n            <option value="gemini" >Google Gemini (tùy chọn, cần key)</option>\n            <option value="deepseek">DeepSeek AI (Văn phong kịch tính)</option>\n            <option value="openai">OpenAI Compatible API</option>\n            <option value="free">Google / MyMemory miễn phí</option>\n            <option value="opencode" >OpenCode Zen · Muse Spark Free</option>\n            <option value="muse">Muse qua Chrome (thử nghiệm, tài khoản Meta)</option>\n          </select>\n        </div>\n\n        <div>\n          <div class="flex items-center justify-between mb-1">\n            <label class="text-xs text-gray-300">Google Gemini API Key:</label>\n            <span class="text-[10px] text-gray-500">Lưu an toàn tại .env</span>\n          </div>\n          <div class="relative">\n            <input type="password" id="settings-gemini-key" placeholder="AIzaSy..." class="w-full bg-[#08090d] border border-gray-700 rounded-xl pl-3 pr-10 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500 font-mono">\n            <button type="button" id="btn-toggle-gemini-key" class="absolute right-3 top-2.5 text-gray-500 hover:text-gray-300">\n              <i class="fa-regular fa-eye text-xs"></i>\n            </button>\n          </div>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Gemini Model:</label>\n          <input id="settings-gemini-model" list="gemini-model-options" value="gemini-2.5-flash" placeholder="Tên model Gemini" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n          <datalist id="gemini-model-options">\n            <option value="gemini-2.5-flash">Ưu tiên trên máy này — đã kiểm tra dịch</option>\n            <option value="gemini-3.6-flash">Flash 3.6 — có thể thử khi hết quá tải</option>\n            <option value="gemini-3.8-flash">Flash mới nhất — có thể thử khi còn công suất</option>\n            <option value="gemini-3.7-flash">Flash thế hệ trước</option>\n            <option value="gemini-3.5-flash">Flash 3.5</option>\n            <option value="gemini-3.1-pro-preview">Pro — cần quota phù hợp</option>\n          </datalist>\n          <p class="text-[11px] text-gray-500 mt-2">Lưu cấu hình trước khi kiểm tra. Chất lượng, tốc độ và hạn mức phụ thuộc model và tài khoản.</p>\n        </div>\n\n        <!-- Gemini Test Connection Button & Result -->\n        <div class="pt-2 flex items-center justify-between">\n          <button id="btn-test-gemini" class="px-3 py-1.5 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs font-semibold flex items-center gap-1.5 transition">\n            <i class="fa-solid fa-plug-circle-check"></i> Kiểm tra kết nối Gemini\n          </button>\n          <span id="gemini-test-result" class="text-xs font-mono text-gray-400"></span>\n        </div>\n\n        <div class="rounded-xl border border-pink-500/30 p-3 space-y-2">\n          <label for="settings-openrouter-model" class="text-xs text-gray-300 block">Model OpenRouter Free:</label>\n          <input id="settings-openrouter-model" value="inclusionai/ling-3.0-flash-sante:free" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n          <p id="openrouter-auth-status" role="status" class="text-xs text-gray-400">Đang kiểm tra key…</p>\n          <p class="text-[11px] text-gray-500">Tự dùng key OpenRouter Free trong OpenCode. Chỉ nhận model có đuôi :free; cần Internet và có giới hạn lượt dùng.</p>\n          <button type="button" id="btn-test-openrouter" class="px-3 py-1.5 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs font-semibold transition">Kiểm tra OpenRouter Free</button>\n          <span id="openrouter-test-result" role="status" class="block text-xs text-gray-400"></span>\n        </div>\n\n        <details class="rounded-xl border border-gray-700 p-3 space-y-2">\n          <summary class="text-xs text-gray-300 cursor-pointer">OpenCode Zen · Muse Spark Free</summary>\n          <label for="settings-opencode-model" class="text-xs text-gray-300 block">Model OpenCode Free:</label>\n          <select id="settings-opencode-model" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="muse-spark-1.3-contributor-free">muse-spark-1.3-contributor-free</option>\n          </select>\n          <p id="opencode-auth-status" role="status" class="text-xs text-gray-400">Đang kiểm tra key OpenCode trên máy…</p>\n          <p class="text-[11px] text-gray-500">Dùng model miễn phí qua OpenCode đã đăng nhập trên máy, không cần Chrome. Nếu dịch vụ từ chối hoặc hết hạn mức, tác vụ báo lỗi và giữ nguyên nhà cung cấp đã chọn.</p>\n          <button type="button" id="btn-test-opencode" class="px-3 py-1.5 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs font-semibold transition">Kiểm tra kết nối OpenCode</button>\n          <span id="opencode-test-result" role="status" class="block text-xs text-gray-400"></span>\n        </details>\n\n        <div>\n          <label for="settings-translation-workers" class="text-xs text-gray-300 block mb-1">Luồng dịch song song</label>\n          <select id="settings-translation-workers" aria-describedby="settings-translation-workers-help" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pink-500">\n            \n            <option value="1" selected>1 luồng</option>\n            \n            <option value="3" >3 luồng</option>\n            \n            <option value="5" >5 luồng</option>\n            \n            <option value="10" >10 luồng</option>\n            \n            <option value="15" >15 luồng</option>\n            \n            <option value="20" >20 luồng</option>\n            \n            <option value="30" >30 luồng</option>\n            \n            <option value="40" >40 luồng</option>\n            \n            <option value="50" >50 luồng</option>\n            \n          </select>\n          <p id="settings-translation-workers-help" class="text-[11px] text-gray-400 mt-1">Số yêu cầu dịch và kiểm tra AI đồng thời tối đa cho mỗi video. Mặc định 20; có thể chọn 30, 40 hoặc 50. Nhận giọng, tạo giọng và dựng video dùng giới hạn riêng.</p>\n        </div>\n\n        <div class="border-t border-gray-800 pt-3">\n          <label class="text-xs text-gray-300 block mb-1">DeepSeek API Key (Tùy chọn):</label>\n          <input type="password" id="settings-deepseek-key" placeholder="sk-..." class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500 font-mono">\n        </div>\n\n        <details class="rounded-xl border border-gray-700 p-3 space-y-3">\n          <summary class="text-xs text-gray-300 cursor-pointer">Muse qua tài khoản Meta (thử nghiệm)</summary>\n          <p class="text-xs text-gray-400">Kết nối phiên Muse đã đăng nhập trong Chrome của bạn hoặc dùng Chrome riêng. Hạn mức và model phía sau do Muse quyết định.</p>\n          <label for="settings-muse-browser-mode" class="text-xs text-gray-300 block">Phiên Chrome cho Muse</label>\n          <select id="settings-muse-browser-mode" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="existing">Chrome đang dùng — giữ phiên Muse đã đăng nhập</option>\n            <option value="dedicated">Chrome riêng của tool — đăng nhập một lần</option>\n          </select>\n          <p class="text-xs text-gray-400">Lưu cấu hình trước khi kết nối. Với Chrome đang dùng, mở <code>chrome://inspect/#remote-debugging</code>, bật <strong>Allow remote debugging for this browser instance</strong>, rồi cho phép khi Chrome hỏi kết nối. Quyền này cho ứng dụng local điều khiển trình duyệt; tool chỉ thao tác tab Muse do nó mở. Có thể tắt lại sau khi dùng.</p>\n          <p id="muse-status" role="status" class="text-xs text-gray-400">Đang kiểm tra cài đặt Muse…</p>\n          <div class="flex flex-wrap gap-2">\n            <button type="button" id="btn-muse-login" class="px-3 py-2 rounded-xl border border-pink-500/50 hover:bg-pink-600/20 text-pink-300 text-xs">Kết nối Muse</button>\n            <button type="button" id="btn-muse-status" class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-gray-800 text-gray-300 text-xs">Kiểm tra đăng nhập</button>\n            <button type="button" id="btn-test-muse" class="px-3 py-2 rounded-xl border border-gray-700 hover:bg-gray-800 text-gray-300 text-xs">Thử dịch Muse</button>\n          </div>\n        </details>\n      </div>\n\n      <!-- Voice & Audio Settings -->\n      <div class="bg-[#12151f] border border-gray-800 rounded-2xl p-5 shadow-xl space-y-4">\n        <h3 class="text-xs font-bold text-violet-400 uppercase tracking-wider flex items-center gap-2">\n          <i class="fa-solid fa-headphones"></i> 2. Âm Thanh & Tách Giọng\n        </h3>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Engine Tách Thoại Realtime:</label>\n          <select id="settings-suppression-mode" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="AUTO" selected>Tự động nhận diện (Stereo Cancel hoặc Formant Notch - Khuyên dùng)</option>\n            <option value="DSP_MONO_ADAPTIVE_FORMANT">Đơn kênh: Multi-band Formant Notch (-20dB)</option>\n            <option value="DSP_STEREO_CENTER_CANCEL">Đa kênh: Mid-Side Phase Cancel (-26dB)</option>\n          </select>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Engine Xuất Video Hoàn Chỉnh (HQ):</label>\n          <select class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500" disabled>\n            <option selected>realtime (theo cấu hình máy)</option>\n          </select>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Mức giảm BGM khi có thoại (Sidechain Ducking):</label>\n          <select id="settings-ducking-level" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="-12">-12 dB (Nhẹ nhàng)</option>\n            <option value="-14" selected>-14 dB (Tiêu chuẩn TikTok - Khuyên dùng)</option>\n            <option value="-18">-18 dB (Rõ thoại tối đa)</option>\n            <option value="-24">-24 dB (Ngắt gần hết nhạc nền)</option>\n          </select>\n        </div>\n\n        <div>\n          <label class="text-xs text-gray-300 block mb-1">Độ trễ đệm tối thiểu (Buffer Target):</label>\n          <select id="settings-buffer-target" class="w-full bg-[#08090d] border border-gray-700 rounded-xl px-3 py-2 text-xs text-gray-100 focus:outline-none focus:border-pink-500">\n            <option value="5">5s (Nhanh nhất)</option>\n            <option value="10" selected>10s (Cân bằng tốt)</option>\n            <option value="15">15s (An toàn không giật)</option>\n            <option value="30">30s (Đệm dài)</option>\n          </select>\n        </div>\n      </div>\n\n    </div>\n\n    <!-- Save Settings Action -->\n    <div class="flex justify-end gap-3 pt-2">\n      <button id="btn-save-settings" class="bg-gradient-to-r from-pink-500 via-rose-500 to-violet-600 hover:opacity-95 text-white font-bold text-xs px-6 py-2.5 rounded-xl shadow-lg transition active:scale-[0.98]">\n        <i class="fa-solid fa-floppy-disk mr-1.5"></i> Lưu Cấu Hình Hệ Thống\n      </button>\n    </div>\n  </div>\n\n  <!-- ======================================================== -->\n  <!-- MODAL: HQ EXPORT -->\n  <!-- ======================================================== -->\n  <div id="export-modal" role="dialog" aria-modal="true" aria-labelledby="export-modal-title" class="fixed inset-0 bg-black/85 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">\n    <div class="bg-[#161922] border border-gray-800 rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl">\n      <div class="flex items-center justify-between pb-3 border-b border-gray-800">\n        <h3 id="export-modal-title" class="text-sm font-bold text-white flex items-center gap-2">\n          <i class="fa-solid fa-film text-pink-500"></i> Xuất video MP4\n        </h3>\n        <button id="btn-close-export-modal" aria-label="Đóng hộp thoại xuất video" class="text-gray-400 hover:text-white">\n          <i class="fa-solid fa-xmark text-lg"></i>\n        </button>\n      </div>\n\n      <div id="export-modal-body" class="space-y-4 text-xs text-gray-300">\n        <p>Xuất MP4 với giọng Việt và phụ đề, giữ tỉ lệ video gốc. Dùng lại âm thanh nền đã xử lý khi phù hợp với chế độ realtime đang chọn. Lần đầu tách nền bằng RoFormer có thể mất nhiều thời gian; những lần sửa lời sau sẽ dùng lại nền đã lưu.</p>\n        <p id="export-screen-text-summary" class="export-screen-text-summary" role="status"></p>\n        <p id="export-incomplete-warning" class="hidden p-3 rounded-xl bg-amber-950/60 border border-amber-700 text-amber-200" role="status"></p>\n        \n        <div id="export-status-box" class="hidden bg-[#08090d] p-3.5 rounded-xl border border-gray-800 space-y-3">\n          <div class="flex items-center justify-between text-xs">\n            <span id="export-status-text" class="text-pink-300 font-medium flex items-center gap-2">\n              <i class="fa-solid fa-spinner fa-spin text-pink-400"></i> Đang chuẩn bị xuất video...\n            </span>\n            <span class="flex flex-wrap items-baseline justify-end gap-x-2"><span id="export-progress-pct" class="font-mono font-bold text-pink-400">0%</span><span id="export-progress-eta" class="hidden text-gray-400"></span></span>\n          </div>\n          <div class="w-full bg-gray-800 h-2 rounded-full overflow-hidden">\n            <div id="export-progress-bar" class="bg-gradient-to-r from-pink-500 via-rose-500 to-violet-500 h-full transition-all duration-300" style="width: 0%"></div>\n          </div>\n          <div class="flex justify-end pt-1">\n            <button id="btn-cancel-export" class="px-3 py-1 rounded-lg bg-rose-950/80 hover:bg-rose-900 border border-rose-800 text-rose-300 text-[11px] font-semibold flex items-center gap-1.5 transition">\n              <i class="fa-solid fa-ban"></i> Hủy xuất video\n            </button>\n          </div>\n        </div>\n\n        <div id="export-result-box" class="hidden space-y-3">\n          <div class="p-3 bg-emerald-950/60 border border-emerald-800/80 rounded-xl text-emerald-300 flex items-center gap-2">\n            <i class="fa-solid fa-circle-check text-lg"></i>\n            <span>Xuất video hoàn tất thành công!</span>\n          </div>\n          <p id="export-result-warning" class="hidden text-amber-200" role="status"></p>\n          <button id="btn-save-as-native" class="w-full py-2.5 bg-pink-600 hover:bg-pink-500 text-white font-bold rounded-xl transition flex items-center justify-center gap-2">\n            <i class="fa-solid fa-floppy-disk"></i> Lưu Video Về Máy (Save Video As...)\n          </button>\n        </div>\n\n        <div id="export-actions" class="flex justify-end gap-2 pt-2">\n          <button id="btn-confirm-export" class="py-2.5 px-4 bg-gradient-to-r from-pink-500 to-violet-600 text-white font-bold rounded-xl transition hover:opacity-95">\n            Bắt đầu xuất video HQ\n          </button>\n        </div>\n      </div>\n    </div>\n  </div>\n\n  <div id="result-preview-modal" role="dialog" aria-modal="true" aria-labelledby="result-preview-title" class="fixed inset-0 bg-black/85 z-50 flex items-center justify-center p-4 hidden">\n    <div class="bg-[#161922] border border-gray-800 rounded-2xl max-w-4xl w-full p-4 space-y-3 shadow-2xl">\n      <div class="flex items-center justify-between gap-3">\n        <h3 id="result-preview-title" class="text-sm font-bold text-white">Xem trước kết quả</h3>\n        <button id="btn-save-preview-result" class="text-xs text-pink-200 px-3 py-1.5 rounded-lg border border-pink-700">Tải video đầy đủ đã dịch</button>\n        <button id="btn-close-result-preview" class="text-xs text-gray-300 px-3 py-1.5 rounded-lg border border-gray-700">Đóng</button>\n      </div>\n      <video id="result-preview-video" controls playsinline preload="metadata" class="w-full bg-black rounded-lg" style="max-height: 75vh;"></video>\n      <p id="result-preview-status" role="status" class="text-xs text-rose-300"></p>\n    </div>\n  </div>\n\n  <script src="/static/studio-layout.js?v=1791942773275170000"></script>\n  <script src="/qtwebchannel.js"></script>\n  <script src="/static/app.js?v=f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527"></script>\n</body>\n</html>'

tests\test_local_api.py:236: AssertionError
```

## `tests.test_local_api.LocalAPITests::test_local_file_start_uses_settings_and_encodes_unicode_path`

**Failure:** `AssertionError: '0' != 'vi-VN-HoaiMyNeural'
- 0
+ vi-VN-HoaiMyNeural`

```text
self = <test_local_api.LocalAPITests testMethod=test_local_file_start_uses_settings_and_encodes_unicode_path>

    def test_local_file_start_uses_settings_and_encodes_unicode_path(self):
        session = self.session()
        with patch.object(main, "create_streaming_session", return_value=session) as create:
            response = self.client.post("/api/streaming/start-local-file", json={"file_path": str(self.video)})
        self.assertEqual(response.status_code, 200)
        params = create.call_args.kwargs
        self.assertEqual(params["asr_engine_name"], settings.ASR_ENGINE)
        self.assertEqual(params["tts_engine_name"], settings.TTS_ENGINE)
>       self.assertEqual(params["voice"], settings.EDGE_VOICE)
E       AssertionError: '0' != 'vi-VN-HoaiMyNeural'
E       - 0
E       + vi-VN-HoaiMyNeural

tests\test_local_api.py:246: AssertionError
```

## `tests.test_local_api.LocalAPITests::test_ollama_local_provider_requires_installed_model_before_persisting`

**Failure:** `AssertionError: expected call not found.
Expected: update_env_file({'LLM_PROVIDER': 'ollama'})
Actual: update_env_file({'TRANSLATION_CHUNK_WORKERS': '1', 'LLM_PROVIDER': 'ollama'})`

```text
self = <test_local_api.LocalAPITests testMethod=test_ollama_local_provider_requires_installed_model_before_persisting>

    def test_ollama_local_provider_requires_installed_model_before_persisting(self):
        with patch.object(settings, "LLM_PROVIDER", "opencode"), \
             patch("main.update_env_file") as persist, \
             patch("core.engines.translation.ollama_client.OllamaClient.verify_model", return_value=True):
            response = self.client.post("/api/settings", json={"llm_provider": "ollama"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(settings.LLM_PROVIDER, "ollama")
>           persist.assert_called_once_with({"LLM_PROVIDER": "ollama"})

tests\test_local_api.py:111: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <MagicMock name='update_env_file' id='1925514022624'>
args = ({'LLM_PROVIDER': 'ollama'},), kwargs = {}

    def assert_called_once_with(self, /, *args, **kwargs):
        """assert that the mock was called exactly once and that that call was
        with the specified arguments."""
        if not self.call_count == 1:
            msg = ("Expected '%s' to be called once. Called %s times.%s"
                   % (self._mock_name or 'mock',
                      self.call_count,
                      self._calls_repr()))
            raise AssertionError(msg)
>       return self.assert_called_with(*args, **kwargs)

E:\Train giọng\tools\python310\lib\unittest\mock.py:941: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <MagicMock name='update_env_file' id='1925514022624'>
args = ({'LLM_PROVIDER': 'ollama'},), kwargs = {}

    def assert_called_with(self, /, *args, **kwargs):
        """assert that the last call was made with the specified arguments.
    
        Raises an AssertionError if the args and keyword args passed in are
        different to the last call to the mock."""
        if self.call_args is None:
            expected = self._format_mock_call_signature(args, kwargs)
            actual = 'not called.'
            error_message = ('expected call not found.\nExpected: %s\nActual: %s'
                    % (expected, actual))
            raise AssertionError(error_message)
    
        def _error_message():
            msg = self._format_mock_failure_message(args, kwargs)
            return msg
        expected = self._call_matcher(_Call((args, kwargs), two=True))
        actual = self._call_matcher(self.call_args)
        if actual != expected:
            cause = expected if isinstance(expected, Exception) else None
>           raise AssertionError(_error_message()) from cause
E           AssertionError: expected call not found.
E           Expected: update_env_file({'LLM_PROVIDER': 'ollama'})
E           Actual: update_env_file({'TRANSLATION_CHUNK_WORKERS': '1', 'LLM_PROVIDER': 'ollama'})

E:\Train giọng\tools\python310\lib\unittest\mock.py:929: AssertionError
```

## `tests.test_local_api.LocalAPITests::test_settings_persist_and_apply_buffer_suppression_and_fractional_ducking`

**Failure:** `AssertionError: expected call not found.
Expected: update_env_file({'INITIAL_BUFFER_SECONDS': '30.0', 'SUPPRESSION_MODE': 'DSP_MONO_ADAPTIVE_FORMANT', 'BGM_VOLUME_DUCKED_DB': '-18.5'})
Actual: update_env_file({'TRANSLATION_CHUNK_WORKERS': '1', 'SUPPRESSION_MODE': 'DSP_MONO_ADAPTIVE_FORMANT', 'INITIAL_BUFFER_SECONDS': '30.0', 'BGM_VOLUME_DUCKED_DB': '-18.5'})`

```text
self = <test_local_api.LocalAPITests testMethod=test_settings_persist_and_apply_buffer_suppression_and_fractional_ducking>

    def test_settings_persist_and_apply_buffer_suppression_and_fractional_ducking(self):
        with patch.object(main, "update_env_file") as persist, \
                patch.object(settings, "INITIAL_BUFFER_SECONDS", 10.0), \
                patch.object(settings, "SUPPRESSION_MODE", "AUTO"), \
                patch.object(settings, "BGM_VOLUME_DUCKED_DB", -14.0):
            response = self.client.post("/api/settings", json={
                "buffer_target": "30", "suppression_mode": "DSP_MONO_ADAPTIVE_FORMANT", "ducking_level": "-18.5",
            })
            self.assertEqual(response.status_code, 200)
>           persist.assert_called_once_with({
                "INITIAL_BUFFER_SECONDS": "30.0", "SUPPRESSION_MODE": "DSP_MONO_ADAPTIVE_FORMANT", "BGM_VOLUME_DUCKED_DB": "-18.5",
            })

tests\test_local_api.py:350: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <MagicMock name='update_env_file' id='1925522608464'>
args = ({'INITIAL_BUFFER_SECONDS': '30.0', 'SUPPRESSION_MODE': 'DSP_MONO_ADAPTIVE_FORMANT', 'BGM_VOLUME_DUCKED_DB': '-18.5'},)
kwargs = {}

    def assert_called_once_with(self, /, *args, **kwargs):
        """assert that the mock was called exactly once and that that call was
        with the specified arguments."""
        if not self.call_count == 1:
            msg = ("Expected '%s' to be called once. Called %s times.%s"
                   % (self._mock_name or 'mock',
                      self.call_count,
                      self._calls_repr()))
            raise AssertionError(msg)
>       return self.assert_called_with(*args, **kwargs)

E:\Train giọng\tools\python310\lib\unittest\mock.py:941: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <MagicMock name='update_env_file' id='1925522608464'>
args = ({'INITIAL_BUFFER_SECONDS': '30.0', 'SUPPRESSION_MODE': 'DSP_MONO_ADAPTIVE_FORMANT', 'BGM_VOLUME_DUCKED_DB': '-18.5'},)
kwargs = {}

    def assert_called_with(self, /, *args, **kwargs):
        """assert that the last call was made with the specified arguments.
    
        Raises an AssertionError if the args and keyword args passed in are
        different to the last call to the mock."""
        if self.call_args is None:
            expected = self._format_mock_call_signature(args, kwargs)
            actual = 'not called.'
            error_message = ('expected call not found.\nExpected: %s\nActual: %s'
                    % (expected, actual))
            raise AssertionError(error_message)
    
        def _error_message():
            msg = self._format_mock_failure_message(args, kwargs)
            return msg
        expected = self._call_matcher(_Call((args, kwargs), two=True))
        actual = self._call_matcher(self.call_args)
        if actual != expected:
            cause = expected if isinstance(expected, Exception) else None
>           raise AssertionError(_error_message()) from cause
E           AssertionError: expected call not found.
E           Expected: update_env_file({'INITIAL_BUFFER_SECONDS': '30.0', 'SUPPRESSION_MODE': 'DSP_MONO_ADAPTIVE_FORMANT', 'BGM_VOLUME_DUCKED_DB': '-18.5'})
E           Actual: update_env_file({'TRANSLATION_CHUNK_WORKERS': '1', 'SUPPRESSION_MODE': 'DSP_MONO_ADAPTIVE_FORMANT', 'INITIAL_BUFFER_SECONDS': '30.0', 'BGM_VOLUME_DUCKED_DB': '-18.5'})

E:\Train giọng\tools\python310\lib\unittest\mock.py:929: AssertionError
```

## `tests.test_local_api.LocalAPITests::test_upload_start_uses_defaults_without_touching_real_inputs`

**Failure:** `AssertionError: '0' != 'vi-VN-HoaiMyNeural'
- 0
+ vi-VN-HoaiMyNeural`

```text
self = <test_local_api.LocalAPITests testMethod=test_upload_start_uses_defaults_without_touching_real_inputs>

    def test_upload_start_uses_defaults_without_touching_real_inputs(self):
        with patch.object(settings, "INPUT_DIR", self.root), \
                patch.object(main, "create_streaming_session", return_value=self.session()) as create:
            response = self.client.post("/api/streaming/start-upload", files={
                "file": ("đầu vào.mp4", b"fixture", "video/mp4"),
            })
        self.assertEqual(response.status_code, 200)
        params = create.call_args.kwargs
        self.assertEqual(params["asr_engine_name"], settings.ASR_ENGINE)
        self.assertEqual(params["tts_engine_name"], settings.TTS_ENGINE)
>       self.assertEqual(params["voice"], settings.EDGE_VOICE)
E       AssertionError: '0' != 'vi-VN-HoaiMyNeural'
E       - 0
E       + vi-VN-HoaiMyNeural

tests\test_local_api.py:281: AssertionError
```

## `tests.test_muse_api::test_existing_mode_saved_only_after_service_reconfiguration`

**Failure:** `AssertionError: expected call not found.
Expected: mock({'MUSE_BROWSER_MODE': 'existing'})
Actual: mock({'TRANSLATION_CHUNK_WORKERS': '1', 'MUSE_BROWSER_MODE': 'existing'})`

```text
muse = (<Mock id='1925521207216'>, <starlette.testclient.TestClient object at 0x000001C05222E470>)
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C039F45E40>

    def test_existing_mode_saved_only_after_service_reconfiguration(muse, monkeypatch):
        service, client = muse
        monkeypatch.setattr(settings, "MUSE_BROWSER_MODE", "dedicated")
        save = Mock()
        monkeypatch.setattr(main, "update_env_file", save)
        def change(mode, apply):
            assert mode == "existing"
            save.assert_not_called()
            apply()
        service.change_browser_mode.side_effect = change
        result = client.post("/api/settings", json={"muse_browser_mode": "existing"})
        assert result.status_code == 200
        assert client.get("/api/settings").json()["muse_browser_mode"] == "existing"
>       save.assert_called_once_with({"MUSE_BROWSER_MODE": "existing"})

tests\test_muse_api.py:126: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <Mock id='1925117662144'>, args = ({'MUSE_BROWSER_MODE': 'existing'},)
kwargs = {}

    def assert_called_once_with(self, /, *args, **kwargs):
        """assert that the mock was called exactly once and that that call was
        with the specified arguments."""
        if not self.call_count == 1:
            msg = ("Expected '%s' to be called once. Called %s times.%s"
                   % (self._mock_name or 'mock',
                      self.call_count,
                      self._calls_repr()))
            raise AssertionError(msg)
>       return self.assert_called_with(*args, **kwargs)

E:\Train giọng\tools\python310\lib\unittest\mock.py:941: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <Mock id='1925117662144'>, args = ({'MUSE_BROWSER_MODE': 'existing'},)
kwargs = {}

    def assert_called_with(self, /, *args, **kwargs):
        """assert that the last call was made with the specified arguments.
    
        Raises an AssertionError if the args and keyword args passed in are
        different to the last call to the mock."""
        if self.call_args is None:
            expected = self._format_mock_call_signature(args, kwargs)
            actual = 'not called.'
            error_message = ('expected call not found.\nExpected: %s\nActual: %s'
                    % (expected, actual))
            raise AssertionError(error_message)
    
        def _error_message():
            msg = self._format_mock_failure_message(args, kwargs)
            return msg
        expected = self._call_matcher(_Call((args, kwargs), two=True))
        actual = self._call_matcher(self.call_args)
        if actual != expected:
            cause = expected if isinstance(expected, Exception) else None
>           raise AssertionError(_error_message()) from cause
E           AssertionError: expected call not found.
E           Expected: mock({'MUSE_BROWSER_MODE': 'existing'})
E           Actual: mock({'TRANSLATION_CHUNK_WORKERS': '1', 'MUSE_BROWSER_MODE': 'existing'})

E:\Train giọng\tools\python310\lib\unittest\mock.py:929: AssertionError
```

## `tests.test_opencode_client::test_forced_review_bypasses_response_cache_without_mutating_resume_client`

**Failure:** `AssertionError: assert (<class 'core.engines.translation.ollama_client.OllamaClient'> is <class 'core.engines.translation.opencode_client.OpenCodeZenClient'>)
 +  where <class 'core.engines.translation.ollama_client.OllamaClient'> = type(<core.engines.translation.ollama_client.OllamaClient object at 0x000001C0520C9FF0>)
 +  and   <class 'core.engines.translation.opencode_client.OpenCodeZenClient'> = oc.OpenCodeZenClient`

```text
isolated = WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_forced_review_bypasses_re0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C0520C97E0>

    def test_forced_review_bypasses_response_cache_without_mutating_resume_client(isolated, monkeypatch):
        from core.translation_review import AutomaticTranslationReviewer
        start = Mock(return_value=make_process(json.dumps({"type": "text", "part": {"text": '{"text":"valid"}'}})))
        monkeypatch.setattr(oc.subprocess, "Popen", start)
        client = oc.OpenCodeZenClient(api_key="fixture-key")
        reviewer = AutomaticTranslationReviewer(client=client)
        options = {"response_validator": lambda raw: isinstance(json.loads(raw)["text"], str), "schema_id": "text-v1"}
        reviewer._review_client(False).translate("same review", **options)
        fresh = reviewer._review_client(True)
>       assert type(fresh) is oc.OpenCodeZenClient and fresh.execution_layer is client.execution_layer
E       AssertionError: assert (<class 'core.engines.translation.ollama_client.OllamaClient'> is <class 'core.engines.translation.opencode_client.OpenCodeZenClient'>)
E        +  where <class 'core.engines.translation.ollama_client.OllamaClient'> = type(<core.engines.translation.ollama_client.OllamaClient object at 0x000001C0520C9FF0>)
E        +  and   <class 'core.engines.translation.opencode_client.OpenCodeZenClient'> = oc.OpenCodeZenClient

tests\test_opencode_client.py:1048: AssertionError
```

## `tests.test_portable_pipeline.PortablePipelineTests::test_legacy_streaming_keeps_asr_preparation_for_resume_and_cleans_scratch`

**Failure:** `ValueError: Giọng Piper đã chọn chưa sẵn sàng. Hãy cài model và runtime Piper.`

```text
self = <test_portable_pipeline.PortablePipelineTests testMethod=test_legacy_streaming_keeps_asr_preparation_for_resume_and_cleans_scratch>

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_legacy_streaming_keeps_asr_preparation_for_resume_and_cleans_scratch(self):
        audio = self.root / "hội thoại.wav"
        write_wave(audio, 2)
        events = []
        session = self.session(event_callback=lambda event, data: events.append((event, data)))
        session.video_path = audio
        session.faster_whisper.transcribe = Mock(return_value=[
            {"start": 0, "end": 1, "text_zh": "第一句。"}, {"start": 1, "end": 2, "text_zh": "第二句。"}])
        session.translator.translate_single_segment = Mock(return_value={"final_vi": "Xin chào tất cả."})
        session.edge_tts.synthesize = lambda text, output_path, **kwargs: write_wave(output_path)
        session.segmenter.segment_audio = Mock(side_effect=AssertionError("Do not replace measured sentences with VAD chunks"))
    
        async def run():
            await session.start()
            await session.worker_task
    
        # This WAV fixture exercises retained legacy preparation and cleanup.
        # Fresh video routing/automatic language detection has separate coverage.
        with patch.object(session, "_start_chunked_visual", session._start_legacy_source), \
                patch.object(settings, "TTS_ENGINE", "edge-tts"), patch.object(settings, "ASR_ENGINE", "faster-whisper"), \
                patch.object(settings, "SUPPRESSION_MODE", "DSP_MONO_ADAPTIVE_FORMANT"):
>           asyncio.run(run())

tests\test_portable_pipeline.py:329: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object PortablePipelineTests.test_legacy_streaming_keeps_asr_preparation_for_resume_and_cleans_scratch.<locals>.run at 0x000001C053CD0270>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-3864' coro=<PortablePipelineTests.test_legacy_streaming_keeps_asr_preparation_for_resume_and...t_portable_pipeline.py:320> exception=ValueError('Giọng Piper đã chọn chưa sẵn sàng. Hãy cài model và runtime Piper.')>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
>       await session.start()

tests\test_portable_pipeline.py:321: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0538B9120>

    async def start(self):
>       await self._start_guarded()

core\streaming\pipeline.py:2311: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0538B9120>
downloader = None, url = None

    async def _start_guarded(self, downloader=None, url=None):
        self.run_intent = "running"
        attempt = 0
        while True:
            try:
>               return await self._start_once(downloader, url, preserve_timings=attempt > 0)

core\streaming\pipeline.py:2322: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0538B9120>
downloader = None, url = None, preserve_timings = False

    async def _start_once(self, downloader=None, url=None, *, preserve_timings=False):
        self.start_task = asyncio.current_task()
        self._run_started_at = time.monotonic()
        self._run_finished_at = None
        self._stage_profiler.reset(
            started_at=self._run_started_at,
            initial_phase="resolve" if downloader is not None and url else "prepare",
            preserve=preserve_timings,
        )
        self._run_ready_baseline = sum(row.status in {"READY", "PLAYED"} for row in self.segments.values())
        from core.streaming.progress_eta import reset_eta
        reset_eta(self, now=self._run_started_at)
        try:
            if self.is_stopped:
                raise asyncio.CancelledError
            self.is_running = True
            if not self.start_wall_time:
                self.start_wall_time = time.time()
            self._persist_if_enabled()
            if downloader is not None and (self.video_path is None or not self.video_path.is_file()):
                loop = asyncio.get_running_loop()
    
                def publish_progress(data):
                    if self.is_stopped or self.video_path is not None:
                        return
                    phase = ("source.download" if data.get("phase") == "download" else
                             "source.download_verify" if data.get("phase") == "prepare" else
                             str(data.get("phase") or "resolve"))
                    self._stage_profiler.advance(phase, now=time.monotonic())
                    self.progress = {**data, "status": "RUNNING", "can_pause": False}
                    asyncio.create_task(self.emit("progress", self.get_progress()))
    
                def download_source():
                    self._download_info = downloader.download(
                        url, progress_callback=lambda data: loop.call_soon_threadsafe(publish_progress, dict(data)),
                        cancel_check=lambda: self.is_stopped,
                    )
                    return self._download_info
    
                await self.report_progress("resolve", "Đang nhận diện link video...")
                info = await self._run_blocking(download_source)
                raw_metrics = info.get("download_metrics")
                if isinstance(raw_metrics, dict):
                    self._download_metrics = {
                        key: raw_metrics[key] for key in (
                            "downloaded_bytes", "file_bytes", "elapsed_seconds", "end_to_end_seconds",
                            "network_read_seconds", "disk_write_seconds", "ttfb_seconds",
                            "average_speed", "peak_speed", "retry_count", "http_requests", "strategy",
                            "connections",
                        ) if key in raw_metrics
                    }
                self.video_path = Path(info["file_path"])
                self.source_video_url = f"/api/inputs/{self.video_path.name}"
                await self.emit("source_ready", {"video_url": self.source_video_url})
>           await self._start()

core\streaming\pipeline.py:2393: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0538B9120>

    async def _start(self):
        """Initializes audio extraction, segmentation, and launches worker loop."""
        if self.is_stopped:
            raise asyncio.CancelledError
        self.is_running = True
        if self.visual_translation and self.video_intelligence is None:
            from core.video_intelligence import VideoIntelligence, validate_visual_provider
            validate_visual_provider()
            self.video_intelligence = VideoIntelligence()
        if not self.start_wall_time:
            self.start_wall_time = time.time()
        await self.report_progress("prepare", "Đang tách âm thanh từ video...")
        self.asr_engine = self.faster_whisper
        if not self.visual_translation and self.asr_engine_name == "sensevoice":
            if self.sensevoice.is_available:
                self.asr_engine = self.sensevoice
            else:
                self.warnings.append("SenseVoice chưa có model; sử dụng Faster-Whisper.")
        elif not self.visual_translation and self.asr_engine_name not in ("faster-whisper", "whisper"):
            raise ValueError(f"ASR engine không được hỗ trợ: {self.asr_engine_name}")
>       self._ensure_tts_engine()

core\streaming\pipeline.py:2743: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0538B9120>

    def _ensure_tts_engine(self):
        if getattr(self, "tts_engine", None) is not None and not getattr(self, "_tts_init_failed", False):
            return
        self.tts_engine = self.edge_tts
        if self.tts_engine_name in ("vieneu", "vieneu-tts"):
            self.vieneu = VieNeuEngine()
            if not self.vieneu.is_available:
                self._tts_init_failed = True
                raise ValueError("Giọng VieNeu đã chọn chưa sẵn sàng. Hãy cài model và runtime VieNeu.")
            self.tts_engine = self.vieneu
        elif self.tts_engine_name == "piper-tts":
            from core.engines.tts.piper_engine import PiperEngine
            selected = PiperEngine()
            if not selected.is_available:
                self._tts_init_failed = True
>               raise ValueError("Giọng Piper đã chọn chưa sẵn sàng. Hãy cài model và runtime Piper.")
E               ValueError: Giọng Piper đã chọn chưa sẵn sàng. Hãy cài model và runtime Piper.

core\streaming\pipeline.py:583: ValueError
```

## `tests.test_preview_chunked_pipeline::test_explicit_review_only_rechecks_published_preview_and_preserves_waiting_lookahead`

**Failure:** `AssertionError: assert [{'regenerate...ocal': False}] == [{'regenerate...': {0, 1, 2}}]
  
  At index 0 diff: {'regenerate_audio': True, 'force_review': True, 'segment_ids': {0, 1, 2}, 'fast_local': False} != {'regenerate_audio': True, 'force_review': True, 'segment_ids': {0, 1, 2}}
  Use -v to get more diff`

```text
preview = (<core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0520C0D00>, [(0.0, 24.0)], [(0.0, 24.0, [0, 1, 2...0.0, ...}), ('segment_update', {'type': 'segment_update', 'task_id': 'preview-owned', 'id': 0, 'start': 0, ...}), ...])
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C0520C12D0>

    def test_explicit_review_only_rechecks_published_preview_and_preserves_waiting_lookahead(preview, monkeypatch):
        session, prepared, analyzed, speech, events = preview
        reviewed = []
        original_review = session._review_translations
    
        async def review(**options):
            reviewed.append(options)
            await original_review(**options)
    
        async def run():
            await session.start()
            await session.worker_task
            future = session.segments[3]
            before = deepcopy(vars(future))
            wavs = {sid: Path(session.segments[sid].audio_path).read_bytes() for sid in range(3)}
            assert future.status == "WAITING" and session.get_progress()["can_review"]
            monkeypatch.setattr(session, "_review_translations", review)
            await session.start_automatic_review()
            await session.review_task
            assert session.get_progress()["status"] == "PREVIEW_READY"
            assert session.can_translate_full and session.get_progress()["can_review"]
            assert vars(future) == before
            assert wavs == {sid: Path(session.segments[sid].audio_path).read_bytes() for sid in range(3)}
            assert not any(event == "progress" and payload["status"] == "COMPLETED" for event, payload in events)
    
        asyncio.run(run())
>       assert reviewed == [{"regenerate_audio": True, "force_review": True, "segment_ids": {0, 1, 2}}]
E       AssertionError: assert [{'regenerate...ocal': False}] == [{'regenerate...': {0, 1, 2}}]
E         
E         At index 0 diff: {'regenerate_audio': True, 'force_review': True, 'segment_ids': {0, 1, 2}, 'fast_local': False} != {'regenerate_audio': True, 'force_review': True, 'segment_ids': {0, 1, 2}}
E         Use -v to get more diff

tests\test_preview_chunked_pipeline.py:274: AssertionError
```

## `tests.test_preview_chunked_pipeline::test_semantic_review_stage_uses_indeterminate_progress_with_real_source_coverage`

**Failure:** `assert 0 == 2
 +  where 0 = sum(<generator object test_semantic_review_stage_uses_indeterminate_progress_with_real_source_coverage.<locals>.<genexpr> at 0x000001C052381230>)`

```text
preview = (<core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0537E4E20>, [], [], [], [('progress', {'type': '...', 'review_summary': {'status': 'completed', 'checked': 1, 'verified': 1, 'corrected': 0, ...}, 'warnings': [], ...})])
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C0537E58D0>

    def test_semantic_review_stage_uses_indeterminate_progress_with_real_source_coverage(preview, monkeypatch):
        session, *_ , events = preview
        session._visual_completed_seconds = 12
        monkeypatch.setattr(session, "_review_translations", StreamingPipelineSession._review_translations.__get__(session))
        row = SegmentItem(0, 0, 1, 1)
        row.text_zh = "源"
        session.segments = {0: row}
        step_seen = threading.Event()
        def event(kind, payload):
            events.append((kind, deepcopy(payload)))
            if kind == "progress" and payload.get("review_stage") == "semantic":
                step_seen.set()
        session.event_callback = event
        def review(reviewer, path, rows, screens, **options):
            options["progress_callback"](30)
            assert step_seen.wait(2)
            step_seen.clear()
            options["progress_callback"](75)
            assert step_seen.wait(2)
            return {"segments": {0: {"id": 0, "verification": {"status": "verified"}}},
                    "summary": {"checked": 1, "verified": 1}}
        monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", review)
        async def run():
            await session._review_translations(segment_ids={0})
            await asyncio.sleep(0)
        asyncio.run(run())
        snapshots = [payload for event, payload in events if event == "progress" and payload["phase"] == "review"]
>       assert sum(snapshot.get("review_stage") == "semantic" for snapshot in snapshots) == 2
E       assert 0 == 2
E        +  where 0 = sum(<generator object test_semantic_review_stage_uses_indeterminate_progress_with_real_source_coverage.<locals>.<genexpr> at 0x000001C052381230>)

tests\test_preview_chunked_pipeline.py:906: AssertionError
```

## `tests.test_preview_chunked_pipeline::test_turning_ocr_review_off_still_uses_bounded_preview_then_full`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
preview = (<core.streaming.pipeline.StreamingPipelineSession object at 0x000001C053910C70>, [(0.0, 24.0), (32.0, 56.0)], [], [0,...0.0, ...}), ('segment_update', {'type': 'segment_update', 'task_id': 'preview-owned', 'id': 1, 'start': 8, ...}), ...])
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C053910610>

    def test_turning_ocr_review_off_still_uses_bounded_preview_then_full(preview, monkeypatch):
        session, prepared, analyzed, speech, events = preview
        session.visual_translation = False
        async def process(row):
            assert row.asr_pretranscribed
            row.final_vi = f"Lời {row.id}."
            await session._synthesize_segment(row)
        monkeypatch.setattr(session, "_process_segment", process)
        monkeypatch.setattr(session.translator, "translate_single_segment", lambda **kwargs:
            {"final_vi": f"Lời {int(kwargs['text_zh'].removeprefix('源')) // 8}."})
        async def run():
            await session.start()
            await session.worker_task
            assert session.get_progress()["status"] == "PREVIEW_READY"
            assert prepared == [(0, 24)]
            await session.translate_full()
            await session.start_task
            await session.worker_task
            assert session.get_progress()["status"] == "COMPLETED"
            assert session.playable_until == 64
>       asyncio.run(run())

tests\test_preview_chunked_pipeline.py:930: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_turning_ocr_review_off_still_uses_bounded_preview_then_full.<locals>.run at 0x000001C053D1C0B0>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-4222' coro=<test_turning_ocr_review_off_still_uses_bounded_preview_then_full.<locals>.run() ...chunked_pipeline.py:920> exception=AssertionError("assert 'PREPARED' == 'COMPLETED'\n  \n  - COMPLETED\n  + PREPARED")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        await session.start()
        await session.worker_task
        assert session.get_progress()["status"] == "PREVIEW_READY"
        assert prepared == [(0, 24)]
        await session.translate_full()
        await session.start_task
        await session.worker_task
>       assert session.get_progress()["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_preview_chunked_pipeline.py:928: AssertionError
```

## `tests.test_processing_progress::test_full_scope_counts_admitted_units_but_waits_for_complete_denominator`

**Failure:** `assert 3 == 2`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C05208D600>

    def test_full_scope_counts_admitted_units_but_waits_for_complete_denominator(session):
        session.translation_mode = "full"
        row(session, 0, 0, 2, ready=True)
        row(session, 1, 8, 10)
        row(session, 2, 24, 26)
        session._source_prepared_seconds = 32
        session._chunk_jobs = [{"start": 0, "end": 24, "state": "RUNNING"}]
        snapshot = session.get_progress()
        assert snapshot["processing_scope"] == "full"
>       assert snapshot["processing_total_segments"] == 2
E       assert 3 == 2

tests\test_processing_progress.py:202: AssertionError
```

## `tests.test_source_language::test_automatic_language_policy_invalidates_old_source_but_not_background`

**Failure:** `assert 8 == 7`

```text
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C0537C5C00>

    def test_automatic_language_policy_invalidates_old_source_but_not_background(monkeypatch):
        from core.streaming import source_revision
        monkeypatch.setattr(chunked_source, "_media_identity", lambda _: {"media": "same"})
        monkeypatch.setattr(source_revision, "source_runtime_identity", lambda _: {"runtime": "same"})
        identity = chunked_source._source_identity(SimpleNamespace())
>       assert identity["language_routing_version"] == 7
E       assert 8 == 7

tests\test_source_language.py:41: AssertionError
```

## `tests.test_static_asset_versions::test_index_versions_assets_from_current_file_metadata`

**Failure:** `assert '/static/app.js?v=1793937099516187800' in '<!DOCTYPE html>\n<html lang="vi">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-wi...pt src="/static/app.js?v=f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527"></script>\n</body>\n</html>'
 +  where '<!DOCTYPE html>\n<html lang="vi">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-wi...pt src="/static/app.js?v=f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527"></script>\n</body>\n</html>' = <Response [200 OK]>.text`

```text
tmp_path = WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_index_versions_assets_fro0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C053960760>

    def test_index_versions_assets_from_current_file_metadata(tmp_path, monkeypatch):
        static = tmp_path / "static"
        static.mkdir()
        for name in ("app.js", "style.css", "studio-compact-panels.css", "studio-layout.js"):
            (static / name).write_text("first", encoding="utf-8")
        monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
        with TestClient(main.app) as client:
            first = client.get("/")
            assert first.status_code == 200
            for name in ("app.js", "style.css", "studio-compact-panels.css", "studio-layout.js"):
>               assert f"/static/{name}?v={(static / name).stat().st_mtime_ns}" in first.text
E               assert '/static/app.js?v=1793937099516187800' in '<!DOCTYPE html>\n<html lang="vi">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-wi...pt src="/static/app.js?v=f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527"></script>\n</body>\n</html>'
E                +  where '<!DOCTYPE html>\n<html lang="vi">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-wi...pt src="/static/app.js?v=f6d038deb780d892e8e8da9a88231088a97c3ea46224d7a19772997185e3c527"></script>\n</body>\n</html>' = <Response [200 OK]>.text

tests\test_static_asset_versions.py:19: AssertionError
```

## `tests.test_transcript_edit::test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting[video-ai]`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C06298E470>
source_method = 'video-ai'

    @pytest.mark.parametrize("source_method", ["video-ai", "text-ai", "audio"])
    def test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting(session, source_method):
        segment = session.segments[0]
        Path(segment.audio_path).unlink()
        segment.audio_path = segment.audio_url = None
        segment.status = "WAITING"
        segment.source_method = source_method
        segment.translation_provider = "opencode"
        segment.translation_model = "muse-spark-1.3-contributor-free"
        segment.evidence_mode = "asr-ocr-text"
        segment.needs_review = True
        segment.review_reason = "Một từ nguồn chưa rõ"
        session.visual_translation = source_method != "audio"
        session.asr_engine = Mock()
        segment.asr_pretranscribed = True
        session.translator = Mock()
        session.translator.translate_single_segment.return_value = {
            "final_vi": segment.final_vi, "needs_review": True, "review_reason": segment.review_reason,
        }
    
        async def run():
            session.is_running = True
            await session.queue.put((0, 0))
            await session._worker_loop()
            draft = segment.to_dict()
            assert draft["status"] == "READY" and draft["needs_review"] and draft["preview_is_draft"]
            assert draft["review_reason"] == "Một từ nguồn chưa rõ"
            assert draft["subtitle_cues"] and draft["speech_start"] is not None
            assert session.error is None and session.first_play_emitted
            assert session.playable_until == session.total_duration
            assert session.get_progress()["status"] == "COMPLETED"
            assert session.get_telemetry()["status"] == "finished"
            assert session.get_telemetry()["review_count"] == 1
            assert session.total_processed_duration == segment.duration
            async with client() as api:
                audio = await api.get(draft["audio_url"])
                assert audio.status_code == 200 and audio.content == (draft["final_vi"] + " aligned").encode()
                exported = await api.post("/api/streaming/export-hq", json={"task_id": session.task_id})
                assert exported.status_code == 409
                response = await api.patch(route(session), json={"final_vi": "Câu đã nghe và sửa"})
                assert response.status_code == 200, response.text
                saved = response.json()["segment"]
                assert not saved["needs_review"] and not saved["preview_is_draft"]
                assert saved["review_reason"] is None and saved["revision"] == 1
                assert (saved["start"], saved["end"]) == (draft["start"], draft["end"])
                assert saved["translation_provider"] == draft["translation_provider"]
                assert saved["translation_model"] == draft["translation_model"]
                assert saved["evidence_mode"] == draft["evidence_mode"]
                edited_audio = await api.get(saved["audio_url"])
                assert edited_audio.content == "Câu đã nghe và sửa aligned".encode()
                assert session.total_processed_duration == segment.duration
                assert session.get_telemetry()["review_count"] == 0
                assert session.get_telemetry()["review_message"] is None
    
>       asyncio.run(run())

tests\test_transcript_edit.py:306: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting.<locals>.run at 0x000001C06279AE30>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-5421' coro=<test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_doub..._transcript_edit.py:272> exception=AssertionError("assert 'PREPARED' == 'COMPLETED'\n  \n  - COMPLETED\n  + PREPARED")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        session.is_running = True
        await session.queue.put((0, 0))
        await session._worker_loop()
        draft = segment.to_dict()
        assert draft["status"] == "READY" and draft["needs_review"] and draft["preview_is_draft"]
        assert draft["review_reason"] == "Một từ nguồn chưa rõ"
        assert draft["subtitle_cues"] and draft["speech_start"] is not None
        assert session.error is None and session.first_play_emitted
        assert session.playable_until == session.total_duration
>       assert session.get_progress()["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_transcript_edit.py:282: AssertionError
```

## `tests.test_transcript_edit::test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting[text-ai]`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C0629AF730>
source_method = 'text-ai'

    @pytest.mark.parametrize("source_method", ["video-ai", "text-ai", "audio"])
    def test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting(session, source_method):
        segment = session.segments[0]
        Path(segment.audio_path).unlink()
        segment.audio_path = segment.audio_url = None
        segment.status = "WAITING"
        segment.source_method = source_method
        segment.translation_provider = "opencode"
        segment.translation_model = "muse-spark-1.3-contributor-free"
        segment.evidence_mode = "asr-ocr-text"
        segment.needs_review = True
        segment.review_reason = "Một từ nguồn chưa rõ"
        session.visual_translation = source_method != "audio"
        session.asr_engine = Mock()
        segment.asr_pretranscribed = True
        session.translator = Mock()
        session.translator.translate_single_segment.return_value = {
            "final_vi": segment.final_vi, "needs_review": True, "review_reason": segment.review_reason,
        }
    
        async def run():
            session.is_running = True
            await session.queue.put((0, 0))
            await session._worker_loop()
            draft = segment.to_dict()
            assert draft["status"] == "READY" and draft["needs_review"] and draft["preview_is_draft"]
            assert draft["review_reason"] == "Một từ nguồn chưa rõ"
            assert draft["subtitle_cues"] and draft["speech_start"] is not None
            assert session.error is None and session.first_play_emitted
            assert session.playable_until == session.total_duration
            assert session.get_progress()["status"] == "COMPLETED"
            assert session.get_telemetry()["status"] == "finished"
            assert session.get_telemetry()["review_count"] == 1
            assert session.total_processed_duration == segment.duration
            async with client() as api:
                audio = await api.get(draft["audio_url"])
                assert audio.status_code == 200 and audio.content == (draft["final_vi"] + " aligned").encode()
                exported = await api.post("/api/streaming/export-hq", json={"task_id": session.task_id})
                assert exported.status_code == 409
                response = await api.patch(route(session), json={"final_vi": "Câu đã nghe và sửa"})
                assert response.status_code == 200, response.text
                saved = response.json()["segment"]
                assert not saved["needs_review"] and not saved["preview_is_draft"]
                assert saved["review_reason"] is None and saved["revision"] == 1
                assert (saved["start"], saved["end"]) == (draft["start"], draft["end"])
                assert saved["translation_provider"] == draft["translation_provider"]
                assert saved["translation_model"] == draft["translation_model"]
                assert saved["evidence_mode"] == draft["evidence_mode"]
                edited_audio = await api.get(saved["audio_url"])
                assert edited_audio.content == "Câu đã nghe và sửa aligned".encode()
                assert session.total_processed_duration == segment.duration
                assert session.get_telemetry()["review_count"] == 0
                assert session.get_telemetry()["review_message"] is None
    
>       asyncio.run(run())

tests\test_transcript_edit.py:306: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting.<locals>.run at 0x000001C062AD1EE0>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-5426' coro=<test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_doub..._transcript_edit.py:272> exception=AssertionError("assert 'PREPARED' == 'COMPLETED'\n  \n  - COMPLETED\n  + PREPARED")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        session.is_running = True
        await session.queue.put((0, 0))
        await session._worker_loop()
        draft = segment.to_dict()
        assert draft["status"] == "READY" and draft["needs_review"] and draft["preview_is_draft"]
        assert draft["review_reason"] == "Một từ nguồn chưa rõ"
        assert draft["subtitle_cues"] and draft["speech_start"] is not None
        assert session.error is None and session.first_play_emitted
        assert session.playable_until == session.total_duration
>       assert session.get_progress()["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_transcript_edit.py:282: AssertionError
```

## `tests.test_transcript_edit::test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting[audio]`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C06283B340>
source_method = 'audio'

    @pytest.mark.parametrize("source_method", ["video-ai", "text-ai", "audio"])
    def test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting(session, source_method):
        segment = session.segments[0]
        Path(segment.audio_path).unlink()
        segment.audio_path = segment.audio_url = None
        segment.status = "WAITING"
        segment.source_method = source_method
        segment.translation_provider = "opencode"
        segment.translation_model = "muse-spark-1.3-contributor-free"
        segment.evidence_mode = "asr-ocr-text"
        segment.needs_review = True
        segment.review_reason = "Một từ nguồn chưa rõ"
        session.visual_translation = source_method != "audio"
        session.asr_engine = Mock()
        segment.asr_pretranscribed = True
        session.translator = Mock()
        session.translator.translate_single_segment.return_value = {
            "final_vi": segment.final_vi, "needs_review": True, "review_reason": segment.review_reason,
        }
    
        async def run():
            session.is_running = True
            await session.queue.put((0, 0))
            await session._worker_loop()
            draft = segment.to_dict()
            assert draft["status"] == "READY" and draft["needs_review"] and draft["preview_is_draft"]
            assert draft["review_reason"] == "Một từ nguồn chưa rõ"
            assert draft["subtitle_cues"] and draft["speech_start"] is not None
            assert session.error is None and session.first_play_emitted
            assert session.playable_until == session.total_duration
            assert session.get_progress()["status"] == "COMPLETED"
            assert session.get_telemetry()["status"] == "finished"
            assert session.get_telemetry()["review_count"] == 1
            assert session.total_processed_duration == segment.duration
            async with client() as api:
                audio = await api.get(draft["audio_url"])
                assert audio.status_code == 200 and audio.content == (draft["final_vi"] + " aligned").encode()
                exported = await api.post("/api/streaming/export-hq", json={"task_id": session.task_id})
                assert exported.status_code == 409
                response = await api.patch(route(session), json={"final_vi": "Câu đã nghe và sửa"})
                assert response.status_code == 200, response.text
                saved = response.json()["segment"]
                assert not saved["needs_review"] and not saved["preview_is_draft"]
                assert saved["review_reason"] is None and saved["revision"] == 1
                assert (saved["start"], saved["end"]) == (draft["start"], draft["end"])
                assert saved["translation_provider"] == draft["translation_provider"]
                assert saved["translation_model"] == draft["translation_model"]
                assert saved["evidence_mode"] == draft["evidence_mode"]
                edited_audio = await api.get(saved["audio_url"])
                assert edited_audio.content == "Câu đã nghe và sửa aligned".encode()
                assert session.total_processed_duration == segment.duration
                assert session.get_telemetry()["review_count"] == 0
                assert session.get_telemetry()["review_message"] is None
    
>       asyncio.run(run())

tests\test_transcript_edit.py:306: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting.<locals>.run at 0x000001C062AD2FF0>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-5431' coro=<test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_doub..._transcript_edit.py:272> exception=AssertionError("assert 'PREPARED' == 'COMPLETED'\n  \n  - COMPLETED\n  + PREPARED")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        session.is_running = True
        await session.queue.put((0, 0))
        await session._worker_loop()
        draft = segment.to_dict()
        assert draft["status"] == "READY" and draft["needs_review"] and draft["preview_is_draft"]
        assert draft["review_reason"] == "Một từ nguồn chưa rõ"
        assert draft["subtitle_cues"] and draft["speech_start"] is not None
        assert session.error is None and session.first_play_emitted
        assert session.playable_until == session.total_duration
>       assert session.get_progress()["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_transcript_edit.py:282: AssertionError
```

## `tests.test_transcript_edit::test_empty_uncertain_draft_keeps_review_without_freezing_playback_or_inventing_voice`

**Failure:** `AssertionError: assert ('FAILED' == 'READY'
  
  - READY
  + FAILED)`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C062850580>

    def test_empty_uncertain_draft_keeps_review_without_freezing_playback_or_inventing_voice(session):
        segment = session.segments[0]
        Path(segment.audio_path).unlink()
        segment.audio_path = segment.audio_url = None
        segment.status = "WAITING"
        segment.source_method = "text-ai"
        segment.final_vi = ""
        segment.needs_review = True
        segment.review_reason = "Câu này không nghe rõ"
        session.visual_translation = True
    
        async def run():
            session.is_running = True
            await session.queue.put((0, 0))
            await session._worker_loop()
            assert segment.status == "READY" and segment.needs_review
            assert segment.audio_url is None and not segment.confirmed_silence
            assert session.error is None and session.first_play_emitted
            assert session.playable_until == session.total_duration
            assert session.get_progress()["review_count"] == 1
            # READY authorizes continuous draft playback, not a full result with a
            # known source utterance silently omitted. Manual editing still works.
            assert session.get_progress()["status"] == "PREPARED"
            assert session.get_progress()["progress_pct"] is None
            assert session.get_progress()["missing_speech_ids"] == [0]
            assert session.get_telemetry()["status"] == "prepared"
            await session.edit_segment(0, "Bổ sung câu nghe được")
            assert not segment.needs_review and segment.audio_url
            assert session.total_processed_duration == segment.duration
    
>       asyncio.run(run())

tests\test_transcript_edit.py:345: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_empty_uncertain_draft_keeps_review_without_freezing_playback_or_inventing_voice.<locals>.run at 0x000001C062AD29D0>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-5436' coro=<test_empty_uncertain_draft_keeps_review_without_freezing_playback_or_inventing_v...tests\test_transcript_edit.py:326> exception=AssertionError("assert ('FAILED' == 'READY'\n  \n  - READY\n  + FAILED)")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        session.is_running = True
        await session.queue.put((0, 0))
        await session._worker_loop()
>       assert segment.status == "READY" and segment.needs_review
E       AssertionError: assert ('FAILED' == 'READY'
E         
E         - READY
E         + FAILED)

tests\test_transcript_edit.py:330: AssertionError
```

## `tests.test_transcript_edit::test_editing_one_draft_keeps_other_drafts_playable_and_does_not_fail_the_session`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C062811F30>

    def test_editing_one_draft_keeps_other_drafts_playable_and_does_not_fail_the_session(session):
        first = session.segments[0]
        first.needs_review = True
        first.review_reason = "Câu đầu cần nghe lại"
        second = SegmentItem(1, 5, 8, 3)
        second.status = "READY"
        second.needs_review = True
        second.review_reason = "Câu sau cần nghe lại"
        second.final_vi = "Bản nháp câu sau"
        second.audio_path = first.audio_path
        second.audio_url = "/api/streaming/audio/transcript-test/1"
        session.segments[1] = second
        session.total_processed_duration = first.duration + second.duration
    
        asyncio.run(session.edit_segment(0, "Câu đầu đã sửa"))
        assert first.needs_review is False and second.needs_review is True
        assert second.review_reason == "Câu sau cần nghe lại"
        assert session.total_processed_duration == 6
        assert session.error is None
>       assert session.get_progress()["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_transcript_edit.py:369: AssertionError
```

## `tests.test_transcript_edit::test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_rows[TTS]`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C06282A0B0>
failed_stage = 'TTS'

    @pytest.mark.parametrize("failed_stage", ["TTS", "ALIGNING"])
    def test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_rows(session, failed_stage):
        ready = session.segments[0]
        before = ready.to_dict()
        original_audio = Path(ready.audio_path).read_bytes()
        session.total_processed_duration = ready.duration
        session.visual_translation = True
        session.source_video_url = "/api/inputs/original.mp4"
        for index in (1, 2):
            segment = SegmentItem(index, 2 + 3 * index, 5 + 3 * index, 3)
            segment.source_method = "text-ai"
            segment.final_vi = f"Bản nháp câu {index}"
            segment.text_zh = f"原文{index}"
            segment.needs_review = True
            segment.review_reason = "Từ nguồn chưa rõ"
            segment.translation_provider = "opencode"
            segment.translation_model = "muse-spark-1.3-contributor-free"
            segment.evidence_mode = "asr-ocr-text"
            session.segments[index] = segment
        session.asr_engine = Mock()
        session.translator = Mock()
        target = session.tts_engine.synthesize if failed_stage == "TTS" else session.aligner.apply_atempo
        normal_call = target.side_effect
        attempts = 0
    
        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Lỗi mạng tạm thời")
            return normal_call(*args, **kwargs)
    
        target.side_effect = fail_once
    
        async def run():
            session.is_running = True
            for index in (1, 2):
                session.queue.put_nowait((session.segments[index].start, index))
            session.worker_task = asyncio.create_task(session._worker_loop())
            await session.worker_task
            assert session.segments[1].failed_stage == failed_stage
            assert session.segments[1].status == "FAILED" and session.segments[2].status == "WAITING"
            assert session.can_retry
            async with client() as api:
                snapshot = (await api.get(f"/api/streaming/{session.task_id}")).json()
                assert snapshot["initialized"] and snapshot["video_url"] == session.source_video_url
                assert snapshot["progress"]["can_retry"] and snapshot["telemetry"]["can_retry"]
                assert snapshot["segments"][1]["failed_stage"] == failed_stage
                tasks = (await api.get("/api/tasks")).json()["tasks"]
                assert tasks[0]["can_retry"] and tasks[0]["status"] == "FAILED"
                results = await asyncio.gather(*[
                    api.post(f"/api/streaming/{session.task_id}/retry") for _ in range(2)])
                assert sorted(response.status_code for response in results) == [200, 409]
                accepted = next(response.json() for response in results if response.status_code == 200)
                assert accepted["status"] == "retrying" and accepted["progress"]["status"] == "RUNNING"
                await session.worker_task
            assert session.error is None and not session.can_retry
            assert session.total_processed_duration == 9
            assert session.queue.empty()
            assert ready.to_dict() == before and Path(ready.audio_path).read_bytes() == original_audio
            for segment in list(session.segments.values())[1:]:
                assert segment.status == "READY" and segment.failed_stage is None
                assert segment.needs_review and segment.review_reason == "Từ nguồn chưa rõ"
                assert segment.translation_provider == "opencode"
                assert segment.translation_model == "muse-spark-1.3-contributor-free"
                assert segment.evidence_mode == "asr-ocr-text"
                assert Path(segment.audio_path).read_bytes() == f"Bản nháp câu {segment.id} aligned".encode()
            session.asr_engine.transcribe.assert_not_called()
            session.translator.translate_single_segment.assert_not_called()
            assert session.get_progress()["status"] == "COMPLETED"
    
>       asyncio.run(run())

tests\test_transcript_edit.py:446: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_rows.<locals>.run at 0x000001C062AE6110>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-5442' coro=<test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_r..._transcript_edit.py:409> exception=AssertionError("assert 'PREPARED' == 'COMPLETED'\n  \n  - COMPLETED\n  + PREPARED")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        session.is_running = True
        for index in (1, 2):
            session.queue.put_nowait((session.segments[index].start, index))
        session.worker_task = asyncio.create_task(session._worker_loop())
        await session.worker_task
        assert session.segments[1].failed_stage == failed_stage
        assert session.segments[1].status == "FAILED" and session.segments[2].status == "WAITING"
        assert session.can_retry
        async with client() as api:
            snapshot = (await api.get(f"/api/streaming/{session.task_id}")).json()
            assert snapshot["initialized"] and snapshot["video_url"] == session.source_video_url
            assert snapshot["progress"]["can_retry"] and snapshot["telemetry"]["can_retry"]
            assert snapshot["segments"][1]["failed_stage"] == failed_stage
            tasks = (await api.get("/api/tasks")).json()["tasks"]
            assert tasks[0]["can_retry"] and tasks[0]["status"] == "FAILED"
            results = await asyncio.gather(*[
                api.post(f"/api/streaming/{session.task_id}/retry") for _ in range(2)])
            assert sorted(response.status_code for response in results) == [200, 409]
            accepted = next(response.json() for response in results if response.status_code == 200)
            assert accepted["status"] == "retrying" and accepted["progress"]["status"] == "RUNNING"
            await session.worker_task
        assert session.error is None and not session.can_retry
        assert session.total_processed_duration == 9
        assert session.queue.empty()
        assert ready.to_dict() == before and Path(ready.audio_path).read_bytes() == original_audio
        for segment in list(session.segments.values())[1:]:
            assert segment.status == "READY" and segment.failed_stage is None
            assert segment.needs_review and segment.review_reason == "Từ nguồn chưa rõ"
            assert segment.translation_provider == "opencode"
            assert segment.translation_model == "muse-spark-1.3-contributor-free"
            assert segment.evidence_mode == "asr-ocr-text"
            assert Path(segment.audio_path).read_bytes() == f"Bản nháp câu {segment.id} aligned".encode()
        session.asr_engine.transcribe.assert_not_called()
        session.translator.translate_single_segment.assert_not_called()
>       assert session.get_progress()["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_transcript_edit.py:444: AssertionError
```

## `tests.test_transcript_edit::test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_rows[ALIGNING]`

**Failure:** `AssertionError: assert 'PREPARED' == 'COMPLETED'
  
  - COMPLETED
  + PREPARED`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C062811630>
failed_stage = 'ALIGNING'

    @pytest.mark.parametrize("failed_stage", ["TTS", "ALIGNING"])
    def test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_rows(session, failed_stage):
        ready = session.segments[0]
        before = ready.to_dict()
        original_audio = Path(ready.audio_path).read_bytes()
        session.total_processed_duration = ready.duration
        session.visual_translation = True
        session.source_video_url = "/api/inputs/original.mp4"
        for index in (1, 2):
            segment = SegmentItem(index, 2 + 3 * index, 5 + 3 * index, 3)
            segment.source_method = "text-ai"
            segment.final_vi = f"Bản nháp câu {index}"
            segment.text_zh = f"原文{index}"
            segment.needs_review = True
            segment.review_reason = "Từ nguồn chưa rõ"
            segment.translation_provider = "opencode"
            segment.translation_model = "muse-spark-1.3-contributor-free"
            segment.evidence_mode = "asr-ocr-text"
            session.segments[index] = segment
        session.asr_engine = Mock()
        session.translator = Mock()
        target = session.tts_engine.synthesize if failed_stage == "TTS" else session.aligner.apply_atempo
        normal_call = target.side_effect
        attempts = 0
    
        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Lỗi mạng tạm thời")
            return normal_call(*args, **kwargs)
    
        target.side_effect = fail_once
    
        async def run():
            session.is_running = True
            for index in (1, 2):
                session.queue.put_nowait((session.segments[index].start, index))
            session.worker_task = asyncio.create_task(session._worker_loop())
            await session.worker_task
            assert session.segments[1].failed_stage == failed_stage
            assert session.segments[1].status == "FAILED" and session.segments[2].status == "WAITING"
            assert session.can_retry
            async with client() as api:
                snapshot = (await api.get(f"/api/streaming/{session.task_id}")).json()
                assert snapshot["initialized"] and snapshot["video_url"] == session.source_video_url
                assert snapshot["progress"]["can_retry"] and snapshot["telemetry"]["can_retry"]
                assert snapshot["segments"][1]["failed_stage"] == failed_stage
                tasks = (await api.get("/api/tasks")).json()["tasks"]
                assert tasks[0]["can_retry"] and tasks[0]["status"] == "FAILED"
                results = await asyncio.gather(*[
                    api.post(f"/api/streaming/{session.task_id}/retry") for _ in range(2)])
                assert sorted(response.status_code for response in results) == [200, 409]
                accepted = next(response.json() for response in results if response.status_code == 200)
                assert accepted["status"] == "retrying" and accepted["progress"]["status"] == "RUNNING"
                await session.worker_task
            assert session.error is None and not session.can_retry
            assert session.total_processed_duration == 9
            assert session.queue.empty()
            assert ready.to_dict() == before and Path(ready.audio_path).read_bytes() == original_audio
            for segment in list(session.segments.values())[1:]:
                assert segment.status == "READY" and segment.failed_stage is None
                assert segment.needs_review and segment.review_reason == "Từ nguồn chưa rõ"
                assert segment.translation_provider == "opencode"
                assert segment.translation_model == "muse-spark-1.3-contributor-free"
                assert segment.evidence_mode == "asr-ocr-text"
                assert Path(segment.audio_path).read_bytes() == f"Bản nháp câu {segment.id} aligned".encode()
            session.asr_engine.transcribe.assert_not_called()
            session.translator.translate_single_segment.assert_not_called()
            assert session.get_progress()["status"] == "COMPLETED"
    
>       asyncio.run(run())

tests\test_transcript_edit.py:446: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_rows.<locals>.run at 0x000001C062AE6DC0>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-5454' coro=<test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_r..._transcript_edit.py:409> exception=AssertionError("assert 'PREPARED' == 'COMPLETED'\n  \n  - COMPLETED\n  + PREPARED")>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        session.is_running = True
        for index in (1, 2):
            session.queue.put_nowait((session.segments[index].start, index))
        session.worker_task = asyncio.create_task(session._worker_loop())
        await session.worker_task
        assert session.segments[1].failed_stage == failed_stage
        assert session.segments[1].status == "FAILED" and session.segments[2].status == "WAITING"
        assert session.can_retry
        async with client() as api:
            snapshot = (await api.get(f"/api/streaming/{session.task_id}")).json()
            assert snapshot["initialized"] and snapshot["video_url"] == session.source_video_url
            assert snapshot["progress"]["can_retry"] and snapshot["telemetry"]["can_retry"]
            assert snapshot["segments"][1]["failed_stage"] == failed_stage
            tasks = (await api.get("/api/tasks")).json()["tasks"]
            assert tasks[0]["can_retry"] and tasks[0]["status"] == "FAILED"
            results = await asyncio.gather(*[
                api.post(f"/api/streaming/{session.task_id}/retry") for _ in range(2)])
            assert sorted(response.status_code for response in results) == [200, 409]
            accepted = next(response.json() for response in results if response.status_code == 200)
            assert accepted["status"] == "retrying" and accepted["progress"]["status"] == "RUNNING"
            await session.worker_task
        assert session.error is None and not session.can_retry
        assert session.total_processed_duration == 9
        assert session.queue.empty()
        assert ready.to_dict() == before and Path(ready.audio_path).read_bytes() == original_audio
        for segment in list(session.segments.values())[1:]:
            assert segment.status == "READY" and segment.failed_stage is None
            assert segment.needs_review and segment.review_reason == "Từ nguồn chưa rõ"
            assert segment.translation_provider == "opencode"
            assert segment.translation_model == "muse-spark-1.3-contributor-free"
            assert segment.evidence_mode == "asr-ocr-text"
            assert Path(segment.audio_path).read_bytes() == f"Bản nháp câu {segment.id} aligned".encode()
        session.asr_engine.transcribe.assert_not_called()
        session.translator.translate_single_segment.assert_not_called()
>       assert session.get_progress()["status"] == "COMPLETED"
E       AssertionError: assert 'PREPARED' == 'COMPLETED'
E         
E         - COMPLETED
E         + PREPARED

tests\test_transcript_edit.py:444: AssertionError
```

## `tests.test_transcript_edit::test_automatic_review_only_regenerates_changed_audio_and_preserves_uncertainty[False-False]`

**Failure:** `assert None is not None`

```text
session = <core.streaming.pipeline.StreamingPipelineSession object at 0x000001C06299AA40>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C06299BEB0>
changed = False, unresolved = False

    @pytest.mark.parametrize("changed, unresolved", [(False, False), (True, False), (True, True)])
    def test_automatic_review_only_regenerates_changed_audio_and_preserves_uncertainty(session, monkeypatch, changed, unresolved):
        from core.translation_review import AutomaticTranslationReviewer
        monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
        segment = session.segments[0]
        segment.needs_review = True
        row = dict(segment.to_dict(), final_vi="Lời đã kiểm tra" if changed else segment.final_vi,
                   needs_review=unresolved, review_reason="Nguồn bị cắt" if unresolved else None,
                   verification={"status": "unresolved" if unresolved else "corrected" if changed else "verified"})
        monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *a, **kw: {
            "segments": {0: row}, "summary": {"checked": 1, "corrected": int(changed and not unresolved), "unresolved": int(unresolved)}})
        async def run():
            async with client() as api:
                response = await api.post(f"/api/streaming/{session.task_id}/review")
                assert response.status_code == 200 and response.json()["status"] == "reviewing"
                task = session.review_task
                assert task is not None
                await task
                assert session.get_progress()["can_review"]
                assert session.get_progress()["status"] == "PREPARED"
                assert not session.get_progress()["can_stop"]
                assert not session.output_filename and session.get_progress()["progress_pct"] is None
>       asyncio.run(run())

tests\test_transcript_edit.py:1263: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

main = <coroutine object test_automatic_review_only_regenerates_changed_audio_and_preserves_uncertainty.<locals>.run at 0x000001C06279B990>
debug = None

    def run(main, *, debug=None):
        """Execute the coroutine and return the result.
    
        This function runs the passed coroutine, taking care of
        managing the asyncio event loop and finalizing asynchronous
        generators.
    
        This function cannot be called when another asyncio event loop is
        running in the same thread.
    
        If debug is True, the event loop will be run in debug mode.
    
        This function always creates a new event loop and closes it at the end.
        It should be used as a main entry point for asyncio programs, and should
        ideally only be called once.
    
        Example:
    
            async def main():
                await asyncio.sleep(1)
                print('hello')
    
            asyncio.run(main())
        """
        if events._get_running_loop() is not None:
            raise RuntimeError(
                "asyncio.run() cannot be called from a running event loop")
    
        if not coroutines.iscoroutine(main):
            raise ValueError("a coroutine was expected, got {!r}".format(main))
    
        loop = events.new_event_loop()
        try:
            events.set_event_loop(loop)
            if debug is not None:
                loop.set_debug(debug)
>           return loop.run_until_complete(main)

E:\Train giọng\tools\python310\lib\asyncio\runners.py:44: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <ProactorEventLoop running=False closed=True debug=False>
future = <Task finished name='Task-6019' coro=<test_automatic_review_only_regenerates_changed_audio_and_preserves_uncertainty.<...done, defined at E:\Dịch video\tests\test_transcript_edit.py:1252> exception=AssertionError('assert None is not None')>

    def run_until_complete(self, future):
        """Run until the Future is done.
    
        If the argument is a coroutine, it is wrapped in a Task.
    
        WARNING: It would be disastrous to call run_until_complete()
        with the same coroutine twice -- it would wrap it in two
        different Tasks and that can't be good.
    
        Return the Future's result, or raise its exception.
        """
        self._check_closed()
        self._check_running()
    
        new_task = not futures.isfuture(future)
        future = tasks.ensure_future(future, loop=self)
        if new_task:
            # An exception is raised if the future didn't complete, so there
            # is no need to log the "destroy pending task" message
            future._log_destroy_pending = False
    
        future.add_done_callback(_run_until_complete_cb)
        try:
            self.run_forever()
        except:
            if new_task and future.done() and not future.cancelled():
                # The coroutine raised a BaseException. Consume the exception
                # to not log a warning, the caller doesn't have access to the
                # local task.
                future.exception()
            raise
        finally:
            future.remove_done_callback(_run_until_complete_cb)
        if not future.done():
            raise RuntimeError('Event loop stopped before Future completed.')
    
>       return future.result()

E:\Train giọng\tools\python310\lib\asyncio\base_events.py:649: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        async with client() as api:
            response = await api.post(f"/api/streaming/{session.task_id}/review")
            assert response.status_code == 200 and response.json()["status"] == "reviewing"
            task = session.review_task
>           assert task is not None
E           assert None is not None

tests\test_transcript_edit.py:1257: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-1]`

**Failure:** `AssertionError: assert 5 == 1
 +  where 5 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).OPENCODE_CONCURRENCY
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062DEF3A0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis0/.env'))
selected = 1, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
        assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
>       assert main.settings.OPENCODE_CONCURRENCY == selected
E       AssertionError: assert 5 == 1
E        +  where 5 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).OPENCODE_CONCURRENCY
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:127: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-3]`

**Failure:** `AssertionError: assert 1 == 3
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C06283ACB0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis1/.env'))
selected = 3, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 3
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-5]`

**Failure:** `AssertionError: assert 1 == 5
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C0627D5000>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis2/.env'))
selected = 5, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 5
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-10]`

**Failure:** `AssertionError: assert 1 == 10
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C0627D6740>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis3/.env'))
selected = 10, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 10
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-15]`

**Failure:** `AssertionError: assert 1 == 15
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062E10DC0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis4/.env'))
selected = 15, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 15
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-20]`

**Failure:** `AssertionError: assert 1 == 20
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062851990>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis5/.env'))
selected = 20, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 20
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-30]`

**Failure:** `AssertionError: assert 1 == 30
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062852B90>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis6/.env'))
selected = 30, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 30
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-40]`

**Failure:** `AssertionError: assert 1 == 40
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C06283A410>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis7/.env'))
selected = 40, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 40
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/settings-50]`

**Failure:** `AssertionError: assert 1 == 50
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062B0CD60>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis8/.env'))
selected = 50, route = '/api/settings'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 50
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-1]`

**Failure:** `AssertionError: assert 5 == 1
 +  where 5 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).OPENCODE_CONCURRENCY
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062E13220>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis9/.env'))
selected = 1, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
        assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
>       assert main.settings.OPENCODE_CONCURRENCY == selected
E       AssertionError: assert 5 == 1
E        +  where 5 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).OPENCODE_CONCURRENCY
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:127: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-3]`

**Failure:** `AssertionError: assert 1 == 3
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C0626F86A0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis10/.env'))
selected = 3, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 3
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-5]`

**Failure:** `AssertionError: assert 1 == 5
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C0627D5EA0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis11/.env'))
selected = 5, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 5
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-10]`

**Failure:** `AssertionError: assert 1 == 10
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C0626FBAF0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis12/.env'))
selected = 10, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 10
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-15]`

**Failure:** `AssertionError: assert 1 == 15
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C06299A200>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis13/.env'))
selected = 15, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 15
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-20]`

**Failure:** `AssertionError: assert 1 == 20
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062839960>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis14/.env'))
selected = 20, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 20
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-30]`

**Failure:** `AssertionError: assert 1 == 30
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C0626F9330>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis15/.env'))
selected = 30, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 30
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-40]`

**Failure:** `AssertionError: assert 1 == 40
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C06298C7C0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis16/.env'))
selected = 40, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 40
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_save_worker_preset_persists_both_limits_and_survives_reload[/api/config-50]`

**Failure:** `AssertionError: assert 1 == 50
 +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
 +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C0626FB040>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_save_worker_preset_persis17/.env'))
selected = 50, route = '/api/config'

    @pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
    @pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
    def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
        client, env_file = worker_api
        result = client.post(route, json={"translation_chunk_workers": selected})
        assert result.status_code == 200
>       assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
E       AssertionError: assert 1 == 50
E        +  where 1 = Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True).TRANSLATION_CHUNK_WORKERS
E        +    where Settings(APP_NAME='Douyin2TikTok AI Studio', HOST='127.0.0.1', PORT=58947, DEBUG=False, PREWARM_MODELS=False, DIARIZAT...TLE_FONT_SIZE=18, SUBTITLE_PRIMARY_COLOR='&H0000EBFF', SUBTITLE_OUTLINE_COLOR='&H00000000', MASK_CHINESE_SUBTITLE=True) = main.settings

tests\test_translation_worker_settings.py:126: AssertionError
```

## `tests.test_translation_worker_settings::test_unchanged_preset_can_be_saved_while_processing`

**Failure:** `assert 409 == 200
 +  where 409 = <Response [409 Conflict]>.status_code`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062DB4FA0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_unchanged_preset_can_be_s0/.env'))
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C0627D5B70>

    def test_unchanged_preset_can_be_saved_while_processing(worker_api, monkeypatch):
        client, _ = worker_api
        monkeypatch.setattr(main, "active_streaming_sessions", {"active": SimpleNamespace(is_running=True)})
        result = client.post("/api/settings", json={"translation_chunk_workers": 5})
>       assert result.status_code == 200
E       assert 409 == 200
E        +  where 409 = <Response [409 Conflict]>.status_code

tests\test_translation_worker_settings.py:171: AssertionError
```

## `tests.test_translation_worker_settings::test_failed_atomic_persistence_preserves_file_and_active_limits`

**Failure:** `AssertionError: assert 'TRANSLATION_CHUNK_WORKERS=20' in '# Existing configuration\nUSER_SETTING=preserved\nTRANSLATION_CHUNK_WORKERS=1\n'`

```text
worker_api = (<starlette.testclient.TestClient object at 0x000001C062DB7EB0>, WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_failed_atomic_persistence0/.env'))
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000001C062DB7B50>

    def test_failed_atomic_persistence_preserves_file_and_active_limits(worker_api, monkeypatch):
        client, env_file = worker_api
        original = env_file.read_bytes()
    
        def fail_publish(temporary, destination):
            staged = Path(temporary).read_text(encoding="utf-8")
            assert "TRANSLATION_CHUNK_WORKERS=20" in staged
            assert "OPENCODE_CONCURRENCY=20" in staged
            assert Path(destination) == env_file
            assert env_file.read_bytes() == original
            raise OSError("fixture disk error")
    
        monkeypatch.setattr(main.os, "replace", fail_publish)
>       result = client.post("/api/settings", json={"translation_chunk_workers": 20})

tests\test_translation_worker_settings.py:187: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient.TestClient object at 0x000001C062DB7EB0>
url = '/api/settings', content = None, data = None, files = None
json = {'translation_chunk_workers': 20}, params = None, headers = None
cookies = None
auth = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
follow_redirects = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
timeout = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
extensions = None

    def post(
        self,
        url: URL | str,
        *,
        content: RequestContent | None = None,
        data: RequestData | None = None,
        files: RequestFiles | None = None,
        json: typing.Any | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
        cookies: CookieTypes | None = None,
        auth: AuthTypes | UseClientDefault = USE_CLIENT_DEFAULT,
        follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
        timeout: TimeoutTypes | UseClientDefault = USE_CLIENT_DEFAULT,
        extensions: RequestExtensions | None = None,
    ) -> Response:
        """
        Send a `POST` request.
    
        **Parameters**: See `httpx2.request`.
        """
>       return self.request(
            "POST",
            url,
            content=content,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
            extensions=extensions,
        )

venv\lib\site-packages\httpx2\_client.py:1204: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient.TestClient object at 0x000001C062DB7EB0>
method = 'POST', url = URL('http://testserver/api/settings'), content = None
data = None, files = None, json = {'translation_chunk_workers': 20}
params = None, headers = None, cookies = None
auth = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
follow_redirects = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
timeout = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
extensions = None

    def request(  # type: ignore[override]
        self,
        method: str,
        url: httpx._types.URLTypes,
        *,
        content: httpx._types.RequestContent | None = None,
        data: _RequestData | None = None,
        files: httpx._types.RequestFiles | None = None,
        json: Any = None,
        params: httpx._types.QueryParamTypes | None = None,
        headers: httpx._types.HeaderTypes | None = None,
        cookies: httpx._types.CookieTypes | None = None,
        auth: httpx._types.AuthTypes | httpx._client.UseClientDefault = httpx._client.USE_CLIENT_DEFAULT,
        follow_redirects: bool | httpx._client.UseClientDefault = httpx._client.USE_CLIENT_DEFAULT,
        timeout: httpx._types.TimeoutTypes | httpx._client.UseClientDefault = httpx._client.USE_CLIENT_DEFAULT,
        extensions: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if timeout is not httpx.USE_CLIENT_DEFAULT:
            warnings.warn(
                "You should not use the 'timeout' argument with the TestClient. "
                "See https://github.com/Kludex/starlette/issues/1108 for more information.",
                StarletteDeprecationWarning,
                stacklevel=2,
            )
        url = self._merge_url(url)
>       return super().request(
            method,
            url,
            content=content,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
            extensions=extensions,
        )

venv\lib\site-packages\starlette\testclient.py:463: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient.TestClient object at 0x000001C062DB7EB0>
method = 'POST', url = URL('http://testserver/api/settings'), content = None
data = None, files = None, json = {'translation_chunk_workers': 20}
params = None, headers = None, cookies = None
auth = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
follow_redirects = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
timeout = <httpx2._client.UseClientDefault object at 0x000001C038E0FEB0>
extensions = None

    def request(
        self,
        method: str,
        url: URL | str,
        *,
        content: RequestContent | None = None,
        data: RequestData | None = None,
        files: RequestFiles | None = None,
        json: typing.Any | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
        cookies: CookieTypes | None = None,
        auth: AuthTypes | UseClientDefault | None = USE_CLIENT_DEFAULT,
        follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
        timeout: TimeoutTypes | UseClientDefault = USE_CLIENT_DEFAULT,
        extensions: RequestExtensions | None = None,
    ) -> Response:
        """
        Build and send a request.
    
        Equivalent to:
    
        ```python
        request = client.build_request(...)
        response = client.send(request, ...)
        ```
    
        See `Client.build_request()`, `Client.send()` and
        [Merging of configuration][0] for how the various parameters
        are merged with client-level configuration.
    
        [0]: advanced/clients.md#merging-of-configuration
        """
        if cookies is not None:
            message = (
                "Setting per-request cookies=<...> is being deprecated, because "
                "the expected behaviour on cookie persistence is ambiguous. Set "
                "cookies directly on the client instance instead."
            )
            warnings.warn(message, DeprecationWarning, stacklevel=2)
    
        request = self.build_request(
            method=method,
            url=url,
            content=content,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            extensions=extensions,
        )
>       return self.send(request, auth=auth, follow_redirects=follow_redirects)

venv\lib\site-packages\httpx2\_client.py:797: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient.TestClient object at 0x000001C062DB7EB0>
request = <Request('POST', 'http://testserver/api/settings')>, stream = False
auth = <httpx2.Auth object at 0x000001C062DB7160>, follow_redirects = True

    def send(
        self,
        request: Request,
        *,
        stream: bool = False,
        auth: AuthTypes | UseClientDefault | None = USE_CLIENT_DEFAULT,
        follow_redirects: bool | UseClientDefault = USE_CLIENT_DEFAULT,
    ) -> Response:
        """
        Send a request.
    
        The request is sent as-is, unmodified.
    
        Typically you'll want to build one with `Client.build_request()`
        so that any client-level configuration is merged into the request,
        but passing an explicit `httpx2.Request()` is supported as well.
    
        See also: [Request instances][0]
    
        [0]: advanced/clients.md#request-instances
        """
        if self._state == ClientState.CLOSED:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
    
        self._state = ClientState.OPENED
        follow_redirects = self.follow_redirects if isinstance(follow_redirects, UseClientDefault) else follow_redirects
    
        self._set_timeout(request)
    
        auth = self._build_request_auth(request, auth)
    
>       response = self._send_handling_auth(
            request,
            auth=auth,
            follow_redirects=follow_redirects,
            history=[],
        )

venv\lib\site-packages\httpx2\_client.py:980: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient.TestClient object at 0x000001C062DB7EB0>
request = <Request('POST', 'http://testserver/api/settings')>
auth = <httpx2.Auth object at 0x000001C062DB7160>, follow_redirects = True
history = []

    def _send_handling_auth(
        self,
        request: Request,
        auth: Auth,
        follow_redirects: bool,
        history: list[Response],
    ) -> Response:
        auth_flow = auth.sync_auth_flow(request)
        try:
            request = next(auth_flow)
    
            while True:
>               response = self._send_handling_redirects(
                    request,
                    follow_redirects=follow_redirects,
                    history=history,
                )

venv\lib\site-packages\httpx2\_client.py:1008: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient.TestClient object at 0x000001C062DB7EB0>
request = <Request('POST', 'http://testserver/api/settings')>
follow_redirects = True, history = []

    def _send_handling_redirects(
        self,
        request: Request,
        follow_redirects: bool,
        history: list[Response],
    ) -> Response:
        while True:
            if len(history) > self.max_redirects:
                raise TooManyRedirects("Exceeded maximum allowed redirects.", request=request)
    
            for hook in self._event_hooks["request"]:
                hook(request)
    
>           response = self._send_single_request(request)

venv\lib\site-packages\httpx2\_client.py:1043: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient.TestClient object at 0x000001C062DB7EB0>
request = <Request('POST', 'http://testserver/api/settings')>

    def _send_single_request(self, request: Request) -> Response:
        """
        Sends a single request, without handling any redirections.
        """
        transport = self._transport_for_url(request.url)
        start = time.perf_counter()
    
        if not isinstance(request.stream, SyncByteStream):
            raise RuntimeError("Attempted to send an async request with a sync Client instance.")
    
        with request_context(request=request):
>           response = transport.handle_request(request)

venv\lib\site-packages\httpx2\_client.py:1076: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient._TestClientTransport object at 0x000001C062DB7250>
request = <Request('POST', 'http://testserver/api/settings')>

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        scheme = request.url.scheme
        host = request.url.raw_host.decode(encoding="ascii")
        path = request.url.path
        raw_path = request.url.raw_path
        query = request.url.query.decode(encoding="ascii")
    
        default_port = {"http": 80, "ws": 80, "https": 443, "wss": 443}[scheme]
        port = request.url.port
        if port is None:
            port = default_port
    
        # Include the 'host' header.
        if "host" in request.headers:
            headers: list[tuple[bytes, bytes]] = []
        else:  # pragma: no cover
            headers = [(b"host", request.url.netloc)]
    
        # Include other request headers.
        headers += [(key.lower().encode(), value.encode()) for key, value in request.headers.multi_items()]
    
        scope: dict[str, Any]
    
        if scheme in {"ws", "wss"}:
            subprotocol = request.headers.get("sec-websocket-protocol", None)
            if subprotocol is None:
                subprotocols: Sequence[str] = []
            else:
                subprotocols = [value.strip() for value in subprotocol.split(",")]
            scope = {
                "type": "websocket",
                "path": unquote(path),
                "raw_path": raw_path.split(b"?", 1)[0],
                "root_path": self.root_path,
                "scheme": scheme,
                "query_string": query.encode(),
                "headers": headers,
                "client": self.client,
                "server": [host, port],
                "subprotocols": subprotocols,
                "state": self.app_state.copy(),
                "extensions": {"websocket.http.response": {}},
            }
            session = WebSocketTestSession(self.app, scope, self.portal_factory)
            raise _Upgrade(session)
    
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": request.method,
            "path": unquote(path),
            "raw_path": raw_path.split(b"?", 1)[0],
            "root_path": self.root_path,
            "scheme": scheme,
            "query_string": query.encode(),
            "headers": headers,
            "client": self.client,
            "server": [host, port],
            "extensions": {"http.response.debug": {}, "http.response.trailers": {}},
            "state": self.app_state.copy(),
        }
    
        request_complete = False
        response_started = False
        body_complete = False
        trailers_expected = False
        trailers: list[tuple[bytes, bytes]] = []
        response_complete: anyio.Event
        raw_kwargs: dict[str, Any] = {"stream": io.BytesIO()}
        debug_info: dict[str, Any] | None = None
    
        async def receive() -> Message:
            nonlocal request_complete
    
            if request_complete:
                if not response_complete.is_set():
                    await response_complete.wait()
                return {"type": "http.disconnect"}
    
            body = request.read()
            if isinstance(body, str):
                body_bytes: bytes = body.encode("utf-8")  # pragma: no cover
            elif body is None:
                body_bytes = b""  # pragma: no cover
            elif isinstance(body, GeneratorType):
                try:  # pragma: no cover
                    chunk = body.send(None)
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    return {"type": "http.request", "body": chunk, "more_body": True}
                except StopIteration:  # pragma: no cover
                    request_complete = True
                    return {"type": "http.request", "body": b""}
            else:
                body_bytes = body
    
            request_complete = True
            return {"type": "http.request", "body": body_bytes}
    
        async def send(message: Message) -> None:
            nonlocal raw_kwargs, response_started, debug_info, body_complete, trailers_expected
    
            if message["type"] == "http.response.start":
                assert not response_started, 'Received multiple "http.response.start" messages.'
                raw_kwargs["status_code"] = message["status"]
                raw_kwargs["headers"] = [(key.decode(), value.decode()) for key, value in message.get("headers", [])]
                response_started = True
                trailers_expected = message.get("trailers", False)
            elif message["type"] == "http.response.body":
                assert response_started, 'Received "http.response.body" without "http.response.start".'
                assert not response_complete.is_set(), 'Received "http.response.body" after response completed.'
                assert not body_complete, 'Received "http.response.body" after body completed.'
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                if request.method != "HEAD":
                    raw_kwargs["stream"].write(body)
                if not more_body:
                    raw_kwargs["stream"].seek(0)
                    body_complete = True
                    if not trailers_expected:
                        response_complete.set()
            elif message["type"] == "http.response.trailers":
                assert trailers_expected, 'Received "http.response.trailers" without declaring trailers.'
                assert body_complete, 'Received "http.response.trailers" before body completed.'
                assert not response_complete.is_set(), 'Received "http.response.trailers" after response completed.'
                trailers.extend(message.get("headers", []))
                if not message.get("more_trailers", False):
                    response_complete.set()
            elif message["type"] == "http.response.debug":
                debug_info = message["info"]
    
        try:
            with self.portal_factory() as portal:
                response_complete = portal.call(anyio.Event)
                portal.call(self.app, scope, receive, send)
        except BaseException as exc:
            if self.raise_server_exceptions:
>               raise exc

venv\lib\site-packages\starlette\testclient.py:360: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.testclient._TestClientTransport object at 0x000001C062DB7250>
request = <Request('POST', 'http://testserver/api/settings')>

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        scheme = request.url.scheme
        host = request.url.raw_host.decode(encoding="ascii")
        path = request.url.path
        raw_path = request.url.raw_path
        query = request.url.query.decode(encoding="ascii")
    
        default_port = {"http": 80, "ws": 80, "https": 443, "wss": 443}[scheme]
        port = request.url.port
        if port is None:
            port = default_port
    
        # Include the 'host' header.
        if "host" in request.headers:
            headers: list[tuple[bytes, bytes]] = []
        else:  # pragma: no cover
            headers = [(b"host", request.url.netloc)]
    
        # Include other request headers.
        headers += [(key.lower().encode(), value.encode()) for key, value in request.headers.multi_items()]
    
        scope: dict[str, Any]
    
        if scheme in {"ws", "wss"}:
            subprotocol = request.headers.get("sec-websocket-protocol", None)
            if subprotocol is None:
                subprotocols: Sequence[str] = []
            else:
                subprotocols = [value.strip() for value in subprotocol.split(",")]
            scope = {
                "type": "websocket",
                "path": unquote(path),
                "raw_path": raw_path.split(b"?", 1)[0],
                "root_path": self.root_path,
                "scheme": scheme,
                "query_string": query.encode(),
                "headers": headers,
                "client": self.client,
                "server": [host, port],
                "subprotocols": subprotocols,
                "state": self.app_state.copy(),
                "extensions": {"websocket.http.response": {}},
            }
            session = WebSocketTestSession(self.app, scope, self.portal_factory)
            raise _Upgrade(session)
    
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": request.method,
            "path": unquote(path),
            "raw_path": raw_path.split(b"?", 1)[0],
            "root_path": self.root_path,
            "scheme": scheme,
            "query_string": query.encode(),
            "headers": headers,
            "client": self.client,
            "server": [host, port],
            "extensions": {"http.response.debug": {}, "http.response.trailers": {}},
            "state": self.app_state.copy(),
        }
    
        request_complete = False
        response_started = False
        body_complete = False
        trailers_expected = False
        trailers: list[tuple[bytes, bytes]] = []
        response_complete: anyio.Event
        raw_kwargs: dict[str, Any] = {"stream": io.BytesIO()}
        debug_info: dict[str, Any] | None = None
    
        async def receive() -> Message:
            nonlocal request_complete
    
            if request_complete:
                if not response_complete.is_set():
                    await response_complete.wait()
                return {"type": "http.disconnect"}
    
            body = request.read()
            if isinstance(body, str):
                body_bytes: bytes = body.encode("utf-8")  # pragma: no cover
            elif body is None:
                body_bytes = b""  # pragma: no cover
            elif isinstance(body, GeneratorType):
                try:  # pragma: no cover
                    chunk = body.send(None)
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    return {"type": "http.request", "body": chunk, "more_body": True}
                except StopIteration:  # pragma: no cover
                    request_complete = True
                    return {"type": "http.request", "body": b""}
            else:
                body_bytes = body
    
            request_complete = True
            return {"type": "http.request", "body": body_bytes}
    
        async def send(message: Message) -> None:
            nonlocal raw_kwargs, response_started, debug_info, body_complete, trailers_expected
    
            if message["type"] == "http.response.start":
                assert not response_started, 'Received multiple "http.response.start" messages.'
                raw_kwargs["status_code"] = message["status"]
                raw_kwargs["headers"] = [(key.decode(), value.decode()) for key, value in message.get("headers", [])]
                response_started = True
                trailers_expected = message.get("trailers", False)
            elif message["type"] == "http.response.body":
                assert response_started, 'Received "http.response.body" without "http.response.start".'
                assert not response_complete.is_set(), 'Received "http.response.body" after response completed.'
                assert not body_complete, 'Received "http.response.body" after body completed.'
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                if request.method != "HEAD":
                    raw_kwargs["stream"].write(body)
                if not more_body:
                    raw_kwargs["stream"].seek(0)
                    body_complete = True
                    if not trailers_expected:
                        response_complete.set()
            elif message["type"] == "http.response.trailers":
                assert trailers_expected, 'Received "http.response.trailers" without declaring trailers.'
                assert body_complete, 'Received "http.response.trailers" before body completed.'
                assert not response_complete.is_set(), 'Received "http.response.trailers" after response completed.'
                trailers.extend(message.get("headers", []))
                if not message.get("more_trailers", False):
                    response_complete.set()
            elif message["type"] == "http.response.debug":
                debug_info = message["info"]
    
        try:
            with self.portal_factory() as portal:
                response_complete = portal.call(anyio.Event)
>               portal.call(self.app, scope, receive, send)

venv\lib\site-packages\starlette\testclient.py:357: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <anyio.from_thread.BlockingPortal object at 0x000001C062851D20>
func = <fastapi.applications.FastAPI object at 0x000001C02887DB70>
args = ({'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}, <function _TestClientTranspo...ls>.receive at 0x000001C062D6F010>, <function _TestClientTransport.handle_request.<locals>.send at 0x000001C062D6D2D0>)

    def call(
        self,
        func: Callable[[Unpack[PosArgsT]], Awaitable[T_Retval] | T_Retval],
        *args: Unpack[PosArgsT],
    ) -> T_Retval:
        """
        Call the given function in the event loop thread.
    
        If the callable returns a coroutine object, it is awaited on.
    
        :param func: any callable
        :raises RuntimeError: if the portal is not running or if this method is called
            from within the event loop thread
    
        """
>       return cast(T_Retval, self.start_task_soon(func, *args).result())

venv\lib\site-packages\anyio\from_thread.py:340: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = None, timeout = None

    def result(self, timeout=None):
        """Return the result of the call that the future represents.
    
        Args:
            timeout: The number of seconds to wait for the result if the future
                isn't done. If None, then there is no limit on the wait time.
    
        Returns:
            The result of the call that the future represents.
    
        Raises:
            CancelledError: If the future was cancelled.
            TimeoutError: If the future didn't finish executing before the given
                timeout.
            Exception: If the call raised then that exception will be raised.
        """
        try:
            with self._condition:
                if self._state in [CANCELLED, CANCELLED_AND_NOTIFIED]:
                    raise CancelledError()
                elif self._state == FINISHED:
                    return self.__get_result()
    
                self._condition.wait(timeout)
    
                if self._state in [CANCELLED, CANCELLED_AND_NOTIFIED]:
                    raise CancelledError()
                elif self._state == FINISHED:
>                   return self.__get_result()

E:\Train giọng\tools\python310\lib\concurrent\futures\_base.py:458: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = None

    def __get_result(self):
        if self._exception:
            try:
>               raise self._exception

E:\Train giọng\tools\python310\lib\concurrent\futures\_base.py:403: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <anyio.from_thread.BlockingPortal object at 0x000001C062851D20>
func = <fastapi.applications.FastAPI object at 0x000001C02887DB70>
args = ({'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}, <function _TestClientTranspo...ls>.receive at 0x000001C062D6F010>, <function _TestClientTransport.handle_request.<locals>.send at 0x000001C062D6D2D0>)
kwargs = {}
future = <Future at 0x1c062f5be20 state=finished raised AssertionError>

    async def _call_func(
        self,
        func: Callable[[Unpack[PosArgsT]], Awaitable[T_Retval] | T_Retval],
        args: tuple[Unpack[PosArgsT]],
        kwargs: dict[str, Any],
        future: Future[T_Retval],
    ) -> None:
        event_loop_thread_id = self._event_loop_thread_id
    
        def callback(f: Future[T_Retval]) -> None:
            if f.cancelled():
                if event_loop_thread_id == get_ident():
                    scope.cancel("the future was cancelled")
                elif event_loop_thread_id is not None:
                    run_sync(
                        scope.cancel, "the future was cancelled", token=self._token
                    )
    
        try:
            retval_or_awaitable = func(*args, **kwargs)
            if isawaitable(retval_or_awaitable):
                with CancelScope() as scope:
                    future.add_done_callback(callback)
>                   retval = await retval_or_awaitable

venv\lib\site-packages\anyio\from_thread.py:265: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <fastapi.applications.FastAPI object at 0x000001C02887DB70>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function _TestClientTransport.handle_request.<locals>.receive at 0x000001C062D6F010>
send = <function _TestClientTransport.handle_request.<locals>.send at 0x000001C062D6D2D0>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.root_path:
            scope["root_path"] = self.root_path
        if scope["type"] == "lifespan":
            from fastapi.telemetry._runtime import lifespan
    
            await lifespan(
                config=self._telemetry,
                app=super().__call__,
                scope=scope,
                receive=receive,
                send=send,
            )
            return
        if (
            scope["type"] not in ("http", "websocket")
            or "fastapi.telemetry" in scope
            or not self._native_telemetry.enabled()
        ):
>           await super().__call__(scope, receive, send)

venv\lib\site-packages\fastapi\applications.py:1215: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <fastapi.applications.FastAPI object at 0x000001C02887DB70>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function _TestClientTransport.handle_request.<locals>.receive at 0x000001C062D6F010>
send = <function _TestClientTransport.handle_request.<locals>.send at 0x000001C062D6D2D0>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope["app"] = self
        if self.middleware_stack is None:
            self.middleware_stack = self.build_middleware_stack()
>       await self.middleware_stack(scope, receive, send)

venv\lib\site-packages\starlette\applications.py:96: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.middleware.errors.ServerErrorMiddleware object at 0x000001C0484F6800>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function _TestClientTransport.handle_request.<locals>.receive at 0x000001C062D6F010>
send = <function _TestClientTransport.handle_request.<locals>.send at 0x000001C062D6D2D0>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
    
        response_started = False
    
        async def _send(message: Message) -> None:
            nonlocal response_started, send
    
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
    
        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            request = Request(scope)
            if self.debug:
                # In debug mode, return traceback responses.
                response = await run_in_threadpool(self.debug_response, request, exc)
            elif self.handler is None:
                # Use our default 500 error handler.
                response = self.error_response(request, exc)
            else:
                # Use an installed 500 error handler.
                if is_async_callable(self.handler):
                    response = await self.handler(request, exc)  # type: ignore[assignment, arg-type]
                else:
                    response = await run_in_threadpool(self.handler, request, exc)  # type: ignore[arg-type]
    
            if not response_started:
                await response(scope, receive, send)
    
            # We always continue to raise the exception.
            # This allows servers to log the error, or allows test clients
            # to optionally raise the error within the test case.
>           raise exc

venv\lib\site-packages\starlette\middleware\errors.py:186: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.middleware.errors.ServerErrorMiddleware object at 0x000001C0484F6800>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function _TestClientTransport.handle_request.<locals>.receive at 0x000001C062D6F010>
send = <function _TestClientTransport.handle_request.<locals>.send at 0x000001C062D6D2D0>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
    
        response_started = False
    
        async def _send(message: Message) -> None:
            nonlocal response_started, send
    
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
    
        try:
>           await self.app(scope, receive, _send)

venv\lib\site-packages\starlette\middleware\errors.py:164: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <fastapi.telemetry._asgi.ExceptionTelemetryMiddleware object at 0x000001C0484F6860>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function _TestClientTransport.handle_request.<locals>.receive at 0x000001C062D6F010>
send = <function ServerErrorMiddleware.__call__.<locals>._send at 0x000001C062D6DCF0>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
>           await self.app(scope, receive, send)

venv\lib\site-packages\fastapi\telemetry\_asgi.py:151: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.middleware.base.BaseHTTPMiddleware object at 0x000001C0484F6890>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function _TestClientTransport.handle_request.<locals>.receive at 0x000001C062D6F010>
send = <function ServerErrorMiddleware.__call__.<locals>._send at 0x000001C062D6DCF0>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
    
        request = _CachedRequest(scope, receive)
        wrapped_receive = request.wrapped_receive
        response_sent = anyio.Event()
        app_exc: Exception | None = None
        exception_already_raised = False
    
        async def call_next(request: Request) -> Response:
            async def receive_or_disconnect() -> Message:
                if response_sent.is_set():
                    return {"type": "http.disconnect"}
    
                async with anyio.create_task_group() as task_group:
    
                    async def wrap(func: Callable[[], Awaitable[T]]) -> T:
                        result = await func()
                        task_group.cancel_scope.cancel()
                        return result
    
                    task_group.start_soon(wrap, response_sent.wait)
                    message = await wrap(wrapped_receive)
    
                if response_sent.is_set():
                    return {"type": "http.disconnect"}
    
                return message
    
            async def send_no_error(message: Message) -> None:
                try:
                    await send_stream.send(message)
                except anyio.BrokenResourceError:
                    # recv_stream has been closed, i.e. response_sent has been set.
                    return
    
                if message["type"] == "http.response.pathsend" or (
                    message["type"] == "http.response.body" and not message.get("more_body", False)
                ):
                    await response_sent.wait()
    
            async def coro() -> None:
                nonlocal app_exc
    
                with send_stream:
                    try:
                        await self.app(scope, receive_or_disconnect, send_no_error)
                    except Exception as exc:
                        app_exc = exc
    
            task_group.start_soon(coro)
    
            try:
                message = await recv_stream.receive()
                info = message.get("info", None)
                if message["type"] == "http.response.debug" and info is not None:
                    message = await recv_stream.receive()
            except anyio.EndOfStream:
                if app_exc is not None:
                    nonlocal exception_already_raised
                    exception_already_raised = True
                    # Prevent `anyio.EndOfStream` from polluting app exception context.
                    # If both cause and context are None then the context is suppressed
                    # and `anyio.EndOfStream` is not present in the exception traceback.
                    # If exception cause is not None then it is propagated with
                    # reraising here.
                    # If exception has no cause but has context set then the context is
                    # propagated as a cause with the reraise. This is necessary in order
                    # to prevent `anyio.EndOfStream` from polluting the exception
                    # context.
                    raise app_exc from app_exc.__cause__ or app_exc.__context__
                raise RuntimeError("No response returned.")
    
            assert message["type"] == "http.response.start"
    
            async def body_stream() -> BodyStreamGenerator:
                async for message in recv_stream:
                    if message["type"] == "http.response.pathsend":
                        yield message
                        break
                    assert message["type"] == "http.response.body", f"Unexpected message: {message}"
                    body = message.get("body", b"")
                    if body:
                        yield body
                    if not message.get("more_body", False):
                        break
    
            response = _StreamingResponse(status_code=message["status"], content=body_stream(), info=info)
            response.raw_headers = message["headers"]
            return response
    
        send_stream, recv_stream = anyio.create_memory_object_stream[Message]()
        with recv_stream, send_stream:
            async with create_collapsing_task_group() as task_group:
>               response = await self.dispatch_func(request, call_next)

venv\lib\site-packages\starlette\middleware\base.py:197: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

request = <starlette.middleware.base._CachedRequest object at 0x000001C062F5B160>
call_next = <function BaseHTTPMiddleware.__call__.<locals>.call_next at 0x000001C062D6E8C0>

    @app.middleware("http")
    async def capture_http_request_start(request: Request, call_next):
        # Includes multipart receive/spooling for upload-to-MP4 measurements.
        token = _e2e_request_started_at.set(time.time())
        try:
>           return await call_next(request)

main.py:75: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

request = <starlette.middleware.base._CachedRequest object at 0x000001C062F5B160>

    async def call_next(request: Request) -> Response:
        async def receive_or_disconnect() -> Message:
            if response_sent.is_set():
                return {"type": "http.disconnect"}
    
            async with anyio.create_task_group() as task_group:
    
                async def wrap(func: Callable[[], Awaitable[T]]) -> T:
                    result = await func()
                    task_group.cancel_scope.cancel()
                    return result
    
                task_group.start_soon(wrap, response_sent.wait)
                message = await wrap(wrapped_receive)
    
            if response_sent.is_set():
                return {"type": "http.disconnect"}
    
            return message
    
        async def send_no_error(message: Message) -> None:
            try:
                await send_stream.send(message)
            except anyio.BrokenResourceError:
                # recv_stream has been closed, i.e. response_sent has been set.
                return
    
            if message["type"] == "http.response.pathsend" or (
                message["type"] == "http.response.body" and not message.get("more_body", False)
            ):
                await response_sent.wait()
    
        async def coro() -> None:
            nonlocal app_exc
    
            with send_stream:
                try:
                    await self.app(scope, receive_or_disconnect, send_no_error)
                except Exception as exc:
                    app_exc = exc
    
        task_group.start_soon(coro)
    
        try:
            message = await recv_stream.receive()
            info = message.get("info", None)
            if message["type"] == "http.response.debug" and info is not None:
                message = await recv_stream.receive()
        except anyio.EndOfStream:
            if app_exc is not None:
                nonlocal exception_already_raised
                exception_already_raised = True
                # Prevent `anyio.EndOfStream` from polluting app exception context.
                # If both cause and context are None then the context is suppressed
                # and `anyio.EndOfStream` is not present in the exception traceback.
                # If exception cause is not None then it is propagated with
                # reraising here.
                # If exception has no cause but has context set then the context is
                # propagated as a cause with the reraise. This is necessary in order
                # to prevent `anyio.EndOfStream` from polluting the exception
                # context.
>               raise app_exc from app_exc.__cause__ or app_exc.__context__

venv\lib\site-packages\starlette\middleware\base.py:173: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def coro() -> None:
        nonlocal app_exc
    
        with send_stream:
            try:
>               await self.app(scope, receive_or_disconnect, send_no_error)

venv\lib\site-packages\starlette\middleware\base.py:149: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <starlette.middleware.exceptions.ExceptionMiddleware object at 0x000001C0484F6920>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.send_no_error at 0x000001C062D6C700>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
    
        scope["starlette.exception_handlers"] = (
            self._exception_handlers,
            self._status_handlers,
        )
    
        conn: Request | WebSocket
        if scope["type"] == "http":
            conn = Request(scope, receive, send)
        else:
            conn = WebSocket(scope, receive, send)
    
>       await wrap_app_handling_exceptions(self.app, conn)(scope, receive, send)

venv\lib\site-packages\starlette\middleware\exceptions.py:63: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.send_no_error at 0x000001C062D6C700>

    async def wrapped_app(scope: Scope, receive: Receive, send: Send) -> None:
        response_started = False
    
        async def sender(message: Message) -> None:
            nonlocal response_started
    
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
    
        try:
            await app(scope, receive, sender)
        except Exception as exc:
            handler = None
    
            if isinstance(exc, HTTPException):
                handler = status_handlers.get(exc.status_code)
    
            if handler is None:
                handler = _lookup_exception_handler(exception_handlers, exc)
    
            if handler is None:
>               raise exc

venv\lib\site-packages\starlette\_exception_handler.py:53: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.send_no_error at 0x000001C062D6C700>

    async def wrapped_app(scope: Scope, receive: Receive, send: Send) -> None:
        response_started = False
    
        async def sender(message: Message) -> None:
            nonlocal response_started
    
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
    
        try:
>           await app(scope, receive, sender)

venv\lib\site-packages\starlette\_exception_handler.py:42: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <fastapi.middleware.asyncexitstack.AsyncExitStackMiddleware object at 0x000001C0484F6980>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with AsyncExitStack() as stack:
            scope[self.context_name] = stack
>           await self.app(scope, receive, send)

venv\lib\site-packages\fastapi\middleware\asyncexitstack.py:18: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <fastapi.routing.APIRouter object at 0x000001C0386BC670>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        The main entry point to the Router class.
        """
>       await self.middleware_stack(scope, receive, send)

venv\lib\site-packages\starlette\routing.py:676: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <fastapi.routing.APIRouter object at 0x000001C0386BC670>
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def app(self, scope: Scope, receive: Receive, send: Send) -> None:
        assert scope["type"] in ("http", "websocket", "lifespan")
    
        if "router" not in scope:
            scope["router"] = self
    
        if scope["type"] == "lifespan":
            await self.lifespan(scope, receive, send)
            return
    
        partial: tuple[BaseRoute, Scope] | None = None
        for route in self.routes:
            match, child_scope = route.matches(scope)
            if match == Match.FULL:
                scope.update(child_scope)
                _route_selected(
                    scope=scope,
                    path=getattr(route, "path_format", None),
                    mount=isinstance(route, routing.Mount),
                )
>               await route.handle(scope, receive, send)

venv\lib\site-packages\fastapi\routing.py:2786: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = APIRoute(path='/api/settings', name='update_settings', methods=['POST'])
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        effective_context = _get_scope_effective_route_context(scope)
        if effective_context is not None and effective_context.original_route is self:
            methods = effective_context.methods
            if methods and scope["method"] not in methods:
                headers = {"Allow": ", ".join(methods)}
                if "app" in scope:
                    raise HTTPException(status_code=405, headers=headers)
                response = PlainTextResponse(
                    "Method Not Allowed", status_code=405, headers=headers
                )
                await response(scope, receive, send)
                return
            assert effective_context.app is not None
            await effective_context.app(scope, receive, send)
            return
>       await super().handle(scope, receive, send)

venv\lib\site-packages\fastapi\routing.py:1310: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = APIRoute(path='/api/settings', name='update_settings', methods=['POST'])
scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.methods and scope["method"] not in self.methods:
            headers = {"Allow": ", ".join(self.methods)}
            if "app" in scope:
                raise HTTPException(status_code=405, headers=headers)
            else:
                response = PlainTextResponse("Method Not Allowed", status_code=405, headers=headers)
            await response(scope, receive, send)
        else:
>           await self.app(scope, receive, send)

venv\lib\site-packages\starlette\routing.py:282: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive, send)
    
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            # Starts customization
            response_awaited = False
            async with AsyncExitStack() as request_stack:
                scope["fastapi_inner_astack"] = request_stack
                async with AsyncExitStack() as function_stack:
                    scope["fastapi_function_astack"] = function_stack
                    response = await f(request)
                await response(scope, receive, send)
                # Continues customization
                response_awaited = True
            if not response_awaited:
                raise FastAPIError(
                    "Response not awaited. There's a high chance that the "
                    "application code is raising an exception and a dependency with yield "
                    "has a block with a bare except, or a block with except Exception, "
                    "and is not raising the exception again. Read more about it in the "
                    "docs: https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#dependencies-with-yield-and-except"
                )
    
        # Same as in Starlette
>       await wrap_app_handling_exceptions(app, request)(scope, receive, send)

venv\lib\site-packages\fastapi\routing.py:165: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def wrapped_app(scope: Scope, receive: Receive, send: Send) -> None:
        response_started = False
    
        async def sender(message: Message) -> None:
            nonlocal response_started
    
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
    
        try:
            await app(scope, receive, sender)
        except Exception as exc:
            handler = None
    
            if isinstance(exc, HTTPException):
                handler = status_handlers.get(exc.status_code)
    
            if handler is None:
                handler = _lookup_exception_handler(exception_handlers, exc)
    
            if handler is None:
>               raise exc

venv\lib\site-packages\starlette\_exception_handler.py:53: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6DB40>

    async def wrapped_app(scope: Scope, receive: Receive, send: Send) -> None:
        response_started = False
    
        async def sender(message: Message) -> None:
            nonlocal response_started
    
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
    
        try:
>           await app(scope, receive, sender)

venv\lib\site-packages\starlette\_exception_handler.py:42: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'path': '/api/settings', ...}
receive = <function BaseHTTPMiddleware.__call__.<locals>.call_next.<locals>.receive_or_disconnect at 0x000001C062D6F0A0>
send = <function wrap_app_handling_exceptions.<locals>.wrapped_app.<locals>.sender at 0x000001C062D6D3F0>

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        # Starts customization
        response_awaited = False
        async with AsyncExitStack() as request_stack:
            scope["fastapi_inner_astack"] = request_stack
            async with AsyncExitStack() as function_stack:
                scope["fastapi_function_astack"] = function_stack
>               response = await f(request)

venv\lib\site-packages\fastapi\routing.py:151: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

request = <starlette.requests.Request object at 0x000001C062F5A0E0>

    async def app(request: Request) -> Response:
        telemetry_data = get_telemetry_data()
        if telemetry_data is not None:
            telemetry_data.request = request
        response: Response | None = None
        file_stack = request.scope.get("fastapi_middleware_astack")
        assert isinstance(file_stack, AsyncExitStack), (
            "fastapi_middleware_astack not found in request scope"
        )
    
        # Extract endpoint context for error messages
        endpoint_ctx = (
            _extract_endpoint_context(dependant.call)
            if dependant.call
            else EndpointContext()
        )
    
        if dependant.path:
            # For mounted sub-apps, include the mount path prefix
            mount_path = request.scope.get("root_path", "").rstrip("/")
            endpoint_ctx["path"] = f"{request.method} {mount_path}{dependant.path}"
    
        # Read body and auto-close files
        try:
            body: Any = None
            if body_field:
                if is_body_form:
                    body = await request.form()
                    file_stack.push_async_callback(body.close)
                else:
                    body_bytes = await request.body()
                    if body_bytes:
                        json_body: Any = Undefined
                        content_type_value = request.headers.get("content-type")
                        if not content_type_value:
                            if not actual_strict_content_type:
                                json_body = await request.json()
                        else:
                            message = email.message.Message()
                            message["content-type"] = content_type_value
                            if message.get_content_maintype() == "application":
                                subtype = message.get_content_subtype()
                                if subtype == "json" or subtype.endswith("+json"):
                                    json_body = await request.json()
                        if json_body != Undefined:
                            body = json_body
                        else:
                            body = body_bytes
        except json.JSONDecodeError as e:
            validation_error = RequestValidationError(
                [
                    {
                        "type": "json_invalid",
                        "loc": ("body", e.pos),
                        "msg": "JSON decode error",
                        "input": {},
                        "ctx": {"error": e.msg},
                    }
                ],
                body=e.doc,
                endpoint_ctx=endpoint_ctx,
            )
            _validation_failed(validation_error)
            raise validation_error from e
        except HTTPException:
            # If a middleware raises an HTTPException, it should be raised again
            raise
        except Exception as e:
            http_error = HTTPException(
                status_code=400, detail="There was an error parsing the body"
            )
            raise http_error from e
    
        if telemetry_data is not None:
            telemetry_data.body = body
    
        # Solve dependencies and run path operation function, auto-closing dependencies
        errors: list[Any] = []
        async_exit_stack = request.scope.get("fastapi_inner_astack")
        assert isinstance(async_exit_stack, AsyncExitStack), (
            "fastapi_inner_astack not found in request scope"
        )
        with _operation(name="dependencies", function=dependant.call):
            solved_result = await solve_dependencies(
                request=request,
                dependant=dependant,
                body=cast(dict[str, Any] | FormData | bytes | None, body),
                dependency_overrides_provider=dependency_overrides_provider,
                async_exit_stack=async_exit_stack,
                embed_body_fields=embed_body_fields,
            )
            if telemetry_data is not None:
                telemetry_data.values = solved_result.values
                telemetry_data.errors = solved_result.errors
        errors = solved_result.errors
        assert dependant.call  # For types
        if not errors:
            # Shared serializer for stream items (JSONL and SSE).
            # Validates against stream_item_field when set, then
            # serializes to JSON bytes.
            def _serialize_data(data: Any) -> bytes:
                if stream_item_field:
                    value, errors_ = stream_item_field.validate(
                        data, {}, loc=("response",)
                    )
                    if errors_:
                        ctx = endpoint_ctx or EndpointContext()
                        raise ResponseValidationError(
                            errors=errors_,
                            body=data,
                            endpoint_ctx=ctx,
                        )
                    return stream_item_field.serialize_json(
                        value,
                        include=response_model_include,
                        exclude=response_model_exclude,
                        by_alias=response_model_by_alias,
                        exclude_unset=response_model_exclude_unset,
                        exclude_defaults=response_model_exclude_defaults,
                        exclude_none=response_model_exclude_none,
                    )
                else:
                    data = jsonable_encoder(data)
                    return json.dumps(data).encode("utf-8")
    
            if is_sse_stream:
                # Generator endpoint: stream as Server-Sent Events
                gen = dependant.call(**solved_result.values)
    
                def _serialize_sse_item(item: Any) -> bytes:
                    if isinstance(item, ServerSentEvent):
                        # User controls the event structure.
                        # Serialize the data payload if present.
                        # For ServerSentEvent items we skip stream_item_field
                        # validation (the user may mix types intentionally).
                        if item.raw_data is not None:
                            data_str: str | None = item.raw_data
                        elif item.data is not None:
                            if hasattr(item.data, "model_dump_json"):
                                data_str = item.data.model_dump_json()
                            else:
                                data_str = json.dumps(jsonable_encoder(item.data))
                        else:
                            data_str = None
                        return format_sse_event(
                            data_str=data_str,
                            event=item.event,
                            id=item.id,
                            retry=item.retry,
                            comment=item.comment,
                        )
                    else:
                        # Plain object: validate + serialize via
                        # stream_item_field (if set) and wrap in data field
                        return format_sse_event(
                            data_str=_serialize_data(item).decode("utf-8")
                        )
    
                if _is_async_gen_callable(dependant.call):
                    sse_aiter: AsyncIterator[Any] = gen.__aiter__()
                else:
                    sse_aiter = iterate_in_threadpool(gen)
    
                @asynccontextmanager
                async def _sse_producer_cm() -> AsyncIterator[
                    ObjectReceiveStream[bytes]
                ]:
                    # Use a memory stream to decouple generator iteration
                    # from the keepalive timer. A producer task pulls items
                    # from the generator independently, so
                    # `anyio.fail_after` never wraps the generator's
                    # `__anext__` directly - avoiding CancelledError that
                    # would finalize the generator and also working for sync
                    # generators running in a thread pool.
                    #
                    # This context manager is entered on the request-scoped
                    # AsyncExitStack so its __aexit__ (which cancels the
                    # task group) is called by the exit stack after the
                    # streaming response completes — not by async generator
                    # finalization via GeneratorExit.
                    # Ref: https://peps.python.org/pep-0789/
                    send_stream, receive_stream = anyio.create_memory_object_stream[
                        bytes
                    ](max_buffer_size=1)
    
                    async def _producer() -> None:
                        async with send_stream:
                            async for raw_item in sse_aiter:
                                await send_stream.send(_serialize_sse_item(raw_item))
    
                    send_keepalive, receive_keepalive = (
                        anyio.create_memory_object_stream[bytes](max_buffer_size=1)
                    )
    
                    async def _keepalive_inserter() -> None:
                        """Read from the producer and forward to the output,
                        inserting keepalive comments on timeout."""
                        async with send_keepalive, receive_stream:
                            try:
                                while True:
                                    try:
                                        with anyio.fail_after(_PING_INTERVAL):
                                            data = await receive_stream.receive()
                                        await send_keepalive.send(data)
                                    except TimeoutError:
                                        await send_keepalive.send(KEEPALIVE_COMMENT)
                            except anyio.EndOfStream:
                                pass
    
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(_producer)
                        tg.start_soon(_keepalive_inserter)
                        yield receive_keepalive
                        tg.cancel_scope.cancel()
    
                # Enter the SSE context manager on the request-scoped
                # exit stack. The stack outlives the streaming response,
                # so __aexit__ runs via proper structured teardown, not
                # via GeneratorExit thrown into an async generator.
                sse_receive_stream = await async_exit_stack.enter_async_context(
                    _sse_producer_cm()
                )
                # Ensure the receive stream is closed when the exit stack
                # unwinds, preventing ResourceWarning from __del__.
                async_exit_stack.push_async_callback(sse_receive_stream.aclose)
    
                async def _sse_with_checkpoints(
                    stream: ObjectReceiveStream[bytes],
                ) -> AsyncIterator[bytes]:
                    async for data in stream:
                        yield data
                        # Guarantee a checkpoint so cancellation can be
                        # delivered even when the producer is faster than
                        # the consumer and receive() never suspends.
                        await anyio.sleep(0)
    
                sse_stream_content: AsyncIterator[bytes] | Iterator[bytes] = (
                    _sse_with_checkpoints(sse_receive_stream)
                )
    
                response_args = _build_response_args(
                    status_code=status_code, solved_result=solved_result
                )
                response = StreamingResponse(
                    sse_stream_content,
                    media_type="text/event-stream",
                    **response_args,
                )
                response.headers["Cache-Control"] = "no-cache"
                # For Nginx proxies to not buffer server sent events
                response.headers["X-Accel-Buffering"] = "no"
                response.headers.raw.extend(solved_result.response.headers.raw)
            elif is_json_stream:
                # Generator endpoint: stream as JSONL
                gen = dependant.call(**solved_result.values)
    
                def _serialize_item(item: Any) -> bytes:
                    return _serialize_data(item) + b"\n"
    
                if _is_async_gen_callable(dependant.call):
    
                    async def _async_stream_jsonl() -> AsyncIterator[bytes]:
                        async for item in gen:
                            yield _serialize_item(item)
                            # To allow for cancellation to trigger
                            # Ref: https://github.com/fastapi/fastapi/issues/14680
                            await anyio.sleep(0)
    
                    jsonl_stream_content: AsyncIterator[bytes] | Iterator[bytes] = (
                        _async_stream_jsonl()
                    )
                else:
    
                    def _sync_stream_jsonl() -> Iterator[bytes]:
                        for item in gen:  # ty: ignore[not-iterable]
                            yield _serialize_item(item)
    
                    jsonl_stream_content = _sync_stream_jsonl()
    
                response_args = _build_response_args(
                    status_code=status_code, solved_result=solved_result
                )
                response = StreamingResponse(
                    jsonl_stream_content,
                    media_type="application/jsonl",
                    **response_args,
                )
                response.headers.raw.extend(solved_result.response.headers.raw)
            elif _is_async_gen_callable(dependant.call) or _is_gen_callable(
                dependant.call
            ):
                # Raw streaming with explicit response_class (e.g. StreamingResponse)
                gen = dependant.call(**solved_result.values)
                if _is_async_gen_callable(dependant.call):
    
                    async def _async_stream_raw(
                        async_gen: AsyncIterator[Any],
                    ) -> AsyncIterator[Any]:
                        async for chunk in async_gen:
                            yield chunk
                            # To allow for cancellation to trigger
                            # Ref: https://github.com/fastapi/fastapi/issues/14680
                            await anyio.sleep(0)
    
                    gen = _async_stream_raw(gen)
                response_args = _build_response_args(
                    status_code=status_code, solved_result=solved_result
                )
                response = actual_response_class(content=gen, **response_args)
                response.headers.raw.extend(solved_result.response.headers.raw)
            else:
>               raw_response = await run_endpoint_function(
                    dependant=dependant,
                    values=solved_result.values,
                    is_coroutine=is_coroutine,
                )

venv\lib\site-packages\fastapi\routing.py:727: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

dependant = Dependant(path_params=[], query_params=[], header_params=[], cookie_params=[], body_params=[ModelField(field_info=Body...ram_name=None, own_oauth_scopes=None, parent_oauth_scopes=None, use_cache=True, path='/api/settings', scope='function')
values = {'req': ConfigRequest(gemini_key=None, deepseek_key=None, llm_provider=None, gemini_model=None, opencode_model=None, o..., muse_browser_mode=None, suppression_mode=None, ducking_level=None, buffer_target=None, translation_chunk_workers=20)}
is_coroutine = True

    async def run_endpoint_function(
        *, dependant: Dependant, values: dict[str, Any], is_coroutine: bool
    ) -> Any:
        # Only called by get_request_handler. Has been split into its own function to
        # facilitate profiling endpoints, since inner functions are harder to profile.
        assert dependant.call is not None, "dependant.call must be a function"
    
        if is_coroutine:
            with _operation(name="endpoint", function=dependant.call):
>               return await dependant.call(**values)

venv\lib\site-packages\fastapi\routing.py:360: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

req = ConfigRequest(gemini_key=None, deepseek_key=None, llm_provider=None, gemini_model=None, opencode_model=None, ollama_mo...e, muse_browser_mode=None, suppression_mode=None, ducking_level=None, buffer_target=None, translation_chunk_workers=20)

    @app.post("/api/settings")
    @app.post("/api/config")
    async def update_settings(req: ConfigRequest):
        # Validate before changing any active settings or persisting the local file.
        if req.llm_provider and req.llm_provider not in {"openrouter-free", "opencode", "ollama", "free", "gemini", "deepseek", "openai", "muse"}:
            raise HTTPException(status_code=422, detail="Nhà cung cấp dịch không hợp lệ")
        if req.muse_browser_mode is not None and req.muse_browser_mode not in {"dedicated", "existing"}:
            raise HTTPException(status_code=422, detail="Chế độ trình duyệt Muse không hợp lệ")
        opencode_model = None
        if req.opencode_model is not None:
            try:
                opencode_model = OpenCodeZenClient(model=req.opencode_model).validate_model()
            except OpenCodeClientError:
                raise HTTPException(status_code=422, detail="Hãy chọn model OpenCode miễn phí trong danh sách") from None
        ollama_model = None
        if req.ollama_model is not None:
            candidate = req.ollama_model.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", candidate):
                raise HTTPException(status_code=422, detail="Tên model Ollama không hợp lệ")
            ollama_model = candidate
        if req.llm_provider == "ollama" or ollama_model is not None:
            from core.engines.translation.ollama_client import OllamaClient
            client = OllamaClient(model=ollama_model or settings.OLLAMA_MODEL,
                                  base_url=settings.OLLAMA_BASE_URL, timeout=settings.OLLAMA_TIMEOUT)
            if not await asyncio.to_thread(client.verify_model):
                raise HTTPException(status_code=503, detail="Ollama local hoặc model đã chọn chưa sẵn sàng")
        for value in (req.gemini_key, req.deepseek_key, req.gemini_model):
            if value is not None and ("\n" in value or "\r" in value):
                raise HTTPException(status_code=422, detail="Cấu hình không được chứa ký tự xuống dòng")
        env_updates = {}
        selected_provider = req.llm_provider or settings.LLM_PROVIDER
        if selected_provider == "ollama":
            # Local Ollama here has one inference runner; extra worker threads only queue requests.
            env_updates["TRANSLATION_CHUNK_WORKERS"] = "1"
        elif req.translation_chunk_workers is not None:
            selected_workers = str(req.translation_chunk_workers)
            env_updates["TRANSLATION_CHUNK_WORKERS"] = selected_workers
            env_updates["OPENCODE_CONCURRENCY"] = selected_workers
        if req.suppression_mode is not None:
            if req.suppression_mode not in {"AUTO", "DSP_MONO_ADAPTIVE_FORMANT", "DSP_STEREO_CENTER_CANCEL"}:
                raise HTTPException(status_code=422, detail="Chế độ tách thoại không hợp lệ")
            env_updates["SUPPRESSION_MODE"] = req.suppression_mode
        if req.buffer_target is not None:
            try:
                value = float(req.buffer_target)
                if not 0 < value <= 120:
                    raise ValueError
                env_updates["INITIAL_BUFFER_SECONDS"] = str(value)
            except ValueError:
                raise HTTPException(status_code=422, detail="Đệm trước phát phải lớn hơn 0 và tối đa 120 giây") from None
        if req.muse_browser_mode is not None:
            env_updates["MUSE_BROWSER_MODE"] = req.muse_browser_mode
        if req.openrouter_model is not None:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+:free", req.openrouter_model):
                raise HTTPException(status_code=422, detail="Model OpenRouter phải là bản :free")
            env_updates["OPENROUTER_MODEL"] = req.openrouter_model
        if req.gemini_key is not None:
            env_updates["GEMINI_API_KEY"] = req.gemini_key
        if req.deepseek_key is not None:
            env_updates["DEEPSEEK_API_KEY"] = req.deepseek_key
        if req.llm_provider:
            env_updates["LLM_PROVIDER"] = req.llm_provider
        if req.gemini_model:
            env_updates["GEMINI_MODEL"] = req.gemini_model
        if opencode_model:
            env_updates["OPENCODE_MODEL"] = opencode_model
        if ollama_model:
            env_updates["OLLAMA_MODEL"] = ollama_model
        if req.ducking_level:
            try:
                value = float(req.ducking_level)
                if not -60 <= value <= 0:
                    raise ValueError
                env_updates["BGM_VOLUME_DUCKED_DB"] = str(value)
            except ValueError:
                raise HTTPException(status_code=422, detail="Mức giảm BGM phải từ -60 đến 0 dB") from None
    
        def apply_settings():
            # Session engines capture some settings while later stages read globals.
            # Reject mutations until all consumers finish rather than mixing models
            # or silently skipping the final review of an existing run.
            changed = any(str(getattr(settings, name, "")) != str(value)
                          for name, value in env_updates.items())
            if changed and (any(getattr(sess, "is_running", False) or getattr(sess, "is_editing", False)
                                for sess in active_streaming_sessions.values())
                            or any(job.get("status") in {"RUNNING", "CANCELLING"}
                                   for job in active_export_tasks.values())):
                raise HTTPException(status_code=409, detail="Đang có tác vụ xử lý. Hãy chờ xong hoặc dừng tác vụ trước khi đổi cấu hình.")
            try:
                update_env_file(env_updates)
            except OSError:
                raise HTTPException(status_code=500, detail="Không thể ghi cấu hình vào .env") from None
            for name, value in env_updates.items():
                if name in {"BGM_VOLUME_DUCKED_DB", "INITIAL_BUFFER_SECONDS"}:
                    active_value = float(value)
                elif name in {"TRANSLATION_CHUNK_WORKERS", "OPENCODE_CONCURRENCY"}:
                    active_value = int(value)
                else:
                    active_value = value
                setattr(settings, name, active_value)
                if name in {"GEMINI_API_KEY", "DEEPSEEK_API_KEY"}:
                    os.environ[name] = value
    
        if env_updates:
            if req.muse_browser_mode is not None and req.muse_browser_mode != settings.MUSE_BROWSER_MODE:
                try:
                    await asyncio.to_thread(muse_service.change_browser_mode, req.muse_browser_mode, apply_settings)
                except MuseError:
                    raise HTTPException(status_code=409, detail="Muse đang đổi chế độ hoặc ứng dụng đang đóng. Hãy thử lưu lại.") from None
            else:
>               apply_settings()

main.py:575: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def apply_settings():
        # Session engines capture some settings while later stages read globals.
        # Reject mutations until all consumers finish rather than mixing models
        # or silently skipping the final review of an existing run.
        changed = any(str(getattr(settings, name, "")) != str(value)
                      for name, value in env_updates.items())
        if changed and (any(getattr(sess, "is_running", False) or getattr(sess, "is_editing", False)
                            for sess in active_streaming_sessions.values())
                        or any(job.get("status") in {"RUNNING", "CANCELLING"}
                               for job in active_export_tasks.values())):
            raise HTTPException(status_code=409, detail="Đang có tác vụ xử lý. Hãy chờ xong hoặc dừng tác vụ trước khi đổi cấu hình.")
        try:
>           update_env_file(env_updates)

main.py:554: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

updates = {'TRANSLATION_CHUNK_WORKERS': '1'}

    def update_env_file(updates: Dict[str, str]):
        """Safely updates or adds configuration keys in .env file on disk."""
        with _ENV_UPDATE_LOCK:
>           _write_env_updates(updates)

main.py:273: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

updates = {'TRANSLATION_CHUNK_WORKERS': '1'}

    def _write_env_updates(updates: Dict[str, str]):
        env_path = settings.BASE_DIR / ".env"
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    
        updated_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                k = k.strip()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}")
                    updated_keys.add(k)
                    continue
            new_lines.append(line)
    
        for k, v in updates.items():
            if k not in updated_keys:
                new_lines.append(f"{k}={v}")
    
        # Publish both limits together. A failed write must preserve the previous
        # .env and must not leave a half-written file for the next app launch.
        fd, temporary_name = tempfile.mkstemp(prefix=".env-", suffix=".tmp", dir=env_path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write("\n".join(new_lines) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
>           os.replace(temporary_path, env_path)

main.py:308: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

temporary = WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_failed_atomic_persistence0/.env-x1ugxaoa.tmp')
destination = WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_failed_atomic_persistence0/.env')

    def fail_publish(temporary, destination):
        staged = Path(temporary).read_text(encoding="utf-8")
>       assert "TRANSLATION_CHUNK_WORKERS=20" in staged
E       AssertionError: assert 'TRANSLATION_CHUNK_WORKERS=20' in '# Existing configuration\nUSER_SETTING=preserved\nTRANSLATION_CHUNK_WORKERS=1\n'

tests\test_translation_worker_settings.py:180: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[True-url]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...ing_signature='62c878cebed9bddd6d464b7b53e5ccaa1e2c57998b061d8d76382872700eaa6c'), normalize=<Mock id='1925805308272'>)
route = 'url', enabled = True

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[True-local]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...ing_signature='62c878cebed9bddd6d464b7b53e5ccaa1e2c57998b061d8d76382872700eaa6c'), normalize=<Mock id='1925804712160'>)
route = 'local', enabled = True

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[True-upload]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...7998b061d8d76382872700eaa6c', source_video_url='/api/inputs/upload_c563baac.mp4'), normalize=<Mock id='1925804659264'>)
route = 'upload', enabled = True

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[False-url]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...ing_signature='62c878cebed9bddd6d464b7b53e5ccaa1e2c57998b061d8d76382872700eaa6c'), normalize=<Mock id='1925805616976'>)
route = 'url', enabled = False

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[False-local]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...ing_signature='62c878cebed9bddd6d464b7b53e5ccaa1e2c57998b061d8d76382872700eaa6c'), normalize=<Mock id='1925810585904'>)
route = 'local', enabled = False

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[False-upload]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...7998b061d8d76382872700eaa6c', source_video_url='/api/inputs/upload_a5638fa5.mp4'), normalize=<Mock id='1925805270128'>)
route = 'upload', enabled = False

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[None-url]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...ing_signature='62c878cebed9bddd6d464b7b53e5ccaa1e2c57998b061d8d76382872700eaa6c'), normalize=<Mock id='1925808684336'>)
route = 'url', enabled = None

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[None-local]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...ing_signature='62c878cebed9bddd6d464b7b53e5ccaa1e2c57998b061d8d76382872700eaa6c'), normalize=<Mock id='1925797980160'>)
route = 'local', enabled = None

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```

## `tests.test_visual_translation_api::test_visual_choice_reaches_session_for_every_source[None-upload]`

**Failure:** `AssertionError: assert 'full' == 'preview'
  
  - preview
  + full`

```text
source_api = namespace(source=WindowsPath('C:/Users/phamc/AppData/Local/Temp/pytest-of-phamc/pytest-158/test_visual_choice_reaches_...7998b061d8d76382872700eaa6c', source_video_url='/api/inputs/upload_480ee837.mp4'), normalize=<Mock id='1925809298960'>)
route = 'upload', enabled = None

    @pytest.mark.parametrize("route", ["url", "local", "upload"])
    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
        response = post_source(source_api, route, enabled)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "started"
        source_api.factory.assert_called_once()
        args = source_api.factory.call_args.kwargs
        assert args["visual_translation"] is bool(enabled)
>       assert args["translation_mode"] == "preview"
E       AssertionError: assert 'full' == 'preview'
E         
E         - preview
E         + full

tests\test_visual_translation_api.py:100: AssertionError
```
