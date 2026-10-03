# Douyin2TikTok AI Studio — UI Function Inventory

Tài liệu kiểm kê toàn bộ thành phần giao diện (UI Inventory), các thao tác người dùng (Actions), API / Handlers liên quan, hành vi kỳ vọng (Expected Behavior), và trạng thái kiểm tra (Status).

---

## 1. App Header & Global Navigation

| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 1 | Global | Tab Studio | `#tab-studio` | Click | `switchTab('view-studio')` | Chuyển sang màn hình Studio, hiển thị trình phát và điều khiển realtime | Verified |
| 2 | Global | Tab Tác Vụ | `#tab-tasks` | Click | `switchTab('view-tasks')` -> `updateTasksTable()` | Chuyển sang Quản lý tác vụ, nạp danh sách task đang chạy/hoàn tất | Verified |
| 3 | Global | Tab Models | `#tab-models` | Click | `switchTab('view-models')` -> `loadModelsTable()` | Chuyển sang Quản lý mô hình, kiểm tra trạng thái checkpoints trên đĩa | Verified |
| 4 | Global | Tab Diagnostics | `#tab-diagnostics` | Click | `switchTab('view-diagnostics')` -> `loadDiagnosticsLog('app')` | Chuyển sang màn hình Nhật ký & Chẩn đoán, nạp nội dung log mới nhất | Verified |
| 5 | Global | Tab Cài Đặt | `#tab-settings` | Click | `switchTab('view-settings')` -> `loadSettingsForm()` | Chuyển sang Cài đặt hệ thống, nạp các giá trị cấu hình từ backend | Verified |
| 6 | Global | Hardware Pill | `#hardware-pill` / `#gpu-info-text` | Page Load | `GET /api/hardware` -> `loadHardware()` | Nhận diện card đồ họa (NVIDIA GPU VRAM / CPU Mode), hiển thị thông số phần cứng | Verified |

---

## 2. Studio View (`#view-studio`)

### 2.1 Telemetry Ribbon & Workers Status
| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 7 | Studio | Playing Time | `#tel-playing` | Timeupdate | `videoPlayer.currentTime` | Cập nhật mm:ss vị trí video đang phát tức thời | Verified |
| 8 | Studio | Playable Time | `#tel-playable` | WS Event | `msg.playable_until` | Hiển thị mốc thời gian an toàn video đã sẵn sàng giọng lồng tiếng | Verified |
| 9 | Studio | Buffer Ahead | `#tel-buffer` | WS Event | `msg.buffer_ahead` | Hiển thị giây đệm trước (+X.Xs), đổi màu xanh/vàng/đỏ theo mức an toàn | Verified |
| 10 | Studio | TTFP | `#tel-ttfp` | WS Event | `msg.time_to_first_play` | Hiển thị số giây từ lúc bấm Start đến khi bắt đầu nghe thấy tiếng Việt | Verified |
| 11 | Studio | RTF Gauge | `#tel-rtf` | WS Event | `msg.realtime_factor` | Hiển thị tốc độ xử lý thực tế (vd: 75.0x / 2.3x) | Verified |
| 12 | Studio | Vocal Suppression | `#tel-suppression` | WS Event | `msg.suppression_level` | Hiển thị mức khử giọng thoại gốc (dB) | Verified |
| 13 | Studio | Worker ASR | `#worker-asr-badge` | WS Event | `msg.status == 'ASR'` | Chớp sáng hiển thị câu thoại SenseVoice đang nhận dạng | Verified |
| 14 | Studio | Worker Gemini | `#worker-trans-badge` | WS Event | `msg.status == 'TRANSLATING'` | Chớp sáng hiển thị câu thoại Gemini đang dịch thuật | Verified |
| 15 | Studio | Worker VieNeu | `#worker-tts-badge` | WS Event | `msg.status == 'TTS'` | Chớp sáng hiển thị câu thoại VieNeu đang tổng hợp giọng nói | Verified |

### 2.2 Video Player & Overlays
| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 16 | Studio | Video Player | `#video-player` | Play/Pause/Seek | HTML5 Video & Audio Sync loop | Phát video mượt mà, đồng bộ âm thanh BGM và giọng lồng tiếng Việt | Verified |
| 17 | Studio | Che Sub Trung | `#toggle-mask-chinese` | Change (Checkbox) | `chineseSubMask.style.display` | Bật/tắt thanh làm mờ mờ đục che cứng phụ đề tiếng Trung trên video | Verified |
| 18 | Studio | Hiện Sub Việt | `#toggle-subtitles` | Change (Checkbox) | `subtitleOverlay.style.display` | Bật/tắt hiển thị phụ đề tiếng Việt chữ vàng viền đen | Verified |
| 19 | Studio | Subtitle Banner | `#subtitle-overlay` / `#subtitle-text` | Timeupdate | Match segment timestamp | Render chữ phụ đề Việt tương ứng với đoạn thoại đang phát | Verified |
| 20 | Studio | Buffering Spinner | `#buffering-alert` / `#buffering-text` | State | Pipeline buffering event | Hiện overlay thông báo khi đang đệm âm thanh ban đầu | Verified |
| 21 | Studio | Player Placeholder | `#player-placeholder` | State | Video load event | Hiện biểu tượng hướng dẫn kéo thả khi chưa có video nào được nạp | Verified |

### 2.3 Timeline & Volume Controls
| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 22 | Studio | Timeline Current Time | `#bar-current-time` | Timeupdate | `formatTime(videoPlayer.currentTime)` | Hiển thị thời gian hiện tại mm:ss bên trái thanh timeline | Verified |
| 23 | Studio | Timeline Buffer Info | `#bar-buffer-info` | WS Event | `msg.buffer_ahead` | Hiển thị thông số Buffer: +X.Xs ở giữa thanh timeline | Verified |
| 24 | Studio | Timeline Total Time | `#bar-total-time` | WS Event / Video | `formatTime(totalVideoDuration)` | Hiển thị tổng thời lượng video mm:ss bên phải timeline | Verified |
| 25 | Studio | Timeline Slices Track | `#segments-timeline-track` | Click (Seek) | WS `seek` & Smart Seek | Nhảy video đến vị trí bấm, ưu tiên worker xử lý ngay vùng vừa seek | Verified |
| 26 | Studio | Playback Marker | `#playback-head-marker` | Timeupdate | CSS `left: %` | Con trỏ màu trắng di chuyển tương ứng theo tiến trình phát | Verified |
| 27 | Studio | Vol Dub Slider | `#vol-dub` | Input (Range) | `activeAudio.volume = val` | Tăng giảm âm lượng giọng đọc tiếng Việt tức thì (0 - 100%) | Verified |
| 28 | Studio | Vol BGM Slider | `#vol-bgm` | Input (Range) | `bgmAudio.volume = val` | Tăng giảm âm lượng nhạc nền BGM & Foley tức thì (0 - 100%) | Verified |
| 29 | Studio | Vocal Suppression Tag | `#player-suppression-badge` | WS Init | `msg.suppression_level` | Hiển thị cấu hình bộ lọc giọng Trung (-26dB) và giữ BGM | Verified |

### 2.4 Video Input & Controls
| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 30 | Studio | URL Input | `#video-url` | Type / Paste | Value binding | Nhập link Douyin hoặc direct URL video | Verified |
| 31 | Studio | Drop Zone | `#drop-zone` | Click | `desktopBridge.pickVideoFile()` or File input | Mở hộp thoại chọn file Windows Explorer native hoặc nạp file | Verified |
| 32 | Studio | Drop Zone Drag-Drop | `#drop-zone` | Drag & Drop | `loadDroppedLocalVideo()` | Kéo thả trực tiếp file MP4/MOV từ Windows Explorer vào nạp video ngay | Verified |
| 33 | Studio | File Input (Hidden) | `#video-file` | File select | Change event | Nạp file video vào trình phát preview | Verified |
| 34 | Studio | File Name Display | `#file-name-display` | Dynamic text | Update filename text | Hiển thị tên file và dung lượng MB video đã chọn | Verified |
| 35 | Studio | Buffer Ahead Select | `#buffer-select` | Select dropdown | Start request payload | Chọn đệm trước phát: 5s, 10s, 15s, 30s | Verified |
| 36 | Studio | Voice Select | `#voice-select` | Select dropdown | Start request payload | Chọn giọng VieNeu: Trúc Ly, Thiện Minh, Mai Anh, Hải Đăng, Adam bựa | Verified |
| 37 | Studio | Start Button | `#btn-start` | Click | `POST /api/streaming/start-*` | Bắt đầu tải/đọc video, kích hoạt pipeline ASR-Gemini-TTS realtime | Verified |
| 38 | Studio | Pause Button | `#btn-pause-worker` | Click | WS `pause` & `/api/tasks/{id}/pause` | Tạm dừng pipeline AI worker phía sau | Verified |
| 39 | Studio | Resume Button | `#btn-resume-worker` | Click | WS `resume` & `/api/tasks/{id}/resume` | Tiếp tục chạy pipeline AI worker | Verified |
| 40 | Studio | Stop Button | `#btn-stop-worker` | Click | WS `stop` & `/api/tasks/{id}/stop` | Dừng hẳn session, hủy giải phóng tài nguyên GPU/RAM | Verified |
| 41 | Studio | HQ Export Button | `#btn-export-hq` | Click | Open `#export-modal` | Mở hộp thoại xuất video TikTok 9:16 chất lượng cao BS-RoFormer | Verified |

### 2.5 Segments Drawer
| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 42 | Studio | Segments Count | `#segments-count-badge` | WS Event | `msg.segments_count` | Hiển thị tổng số câu thoại đã bóc tách được | Verified |
| 43 | Studio | Segments List | `#segments-list` | WS Updates | `renderSegmentsDrawer()` | Danh sách cuộn chứa tất cả các câu thoại với timestamp, text ZH và bản dịch VI | Verified |
| 44 | Studio | Segment Item | `#seg-row-{id}` | Dynamic Item | `updateSegmentDrawerItem()` | Cập nhật trạng thái câu (ASR -> TRANSLATING -> TTS -> READY) theo thời gian thực | Verified |

---

## 3. Tasks View (`#view-tasks`)

| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 45 | Tasks | Refresh Button | `#btn-refresh-tasks` | Click | `GET /api/tasks` -> `updateTasksTable()` | Tải lại danh sách tác vụ đang chạy từ backend | Verified |
| 46 | Tasks | Tasks Table | `#tasks-table-body` | Dynamic Table | Render tasks array | Hiển thị bảng tác vụ: ID, Loại tác vụ, Tiến độ (%), Thời lượng, Trạng thái | Verified |
| 47 | Tasks | Task Pause Button | `.btn-task-pause` | Click | `POST /api/tasks/{id}/pause` | Tạm dừng tác vụ tương ứng | Verified |
| 48 | Tasks | Task Resume Button | `.btn-task-resume` | Click | `POST /api/tasks/{id}/resume` | Tiếp tục tác vụ tương ứng | Verified |
| 49 | Tasks | Task Cancel Button | `.btn-task-cancel` | Click | `POST /api/tasks/{id}/stop` | Hủy hoàn toàn tác vụ và cập nhật trạng thái bảng | Verified |
| 50 | Tasks | Task Open Result Button | `.btn-task-open` | Click | Native open file / download | Mở video kết quả khi tác vụ đã HOÀN THÀNH | Verified |

---

## 4. Models View (`#view-models`)

| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 51 | Models | Reload Button | `#btn-reload-models` | Click | `GET /api/models` -> `loadModelsTable()` | Quét lại toàn bộ thư mục models và cập nhật bảng | Verified |
| 52 | Models | Models Table Body | `#models-full-table-body` | Dynamic Table | Render models array | Hiển thị: Engine, Model name, Size (MB), Device (CUDA/CPU), Status | Verified |
| 53 | Models | Verify Button | `.btn-model-verify` | Click | `POST /api/models/verify` | Kiểm tra tính toàn vẹn checkpoint trên đĩa, phản hồi kết quả trực quan | Verified |
| 54 | Models | Download Button | `.btn-model-download` | Click | `POST /api/models/download` | Tải về checkpoint nếu chưa có trên máy | Verified |

---

## 5. Diagnostics View (`#view-diagnostics`)

| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 55 | Diag | App Log Tab | `[data-cat="app"]` | Click | `GET /api/diagnostics/logs?category=app` | Nạp và hiển thị nhật ký ứng dụng `app.log` | Verified |
| 56 | Diag | AI Log Tab | `[data-cat="ai"]` | Click | `GET /api/diagnostics/logs?category=ai` | Nạp và hiển thị nhật ký engine AI `ai.log` | Verified |
| 57 | Diag | Pipeline Log Tab | `[data-cat="pipeline"]` | Click | `GET /api/diagnostics/logs?category=pipeline` | Nạp và hiển thị nhật ký pipeline streaming `pipeline.log` | Verified |
| 58 | Diag | Errors Log Tab | `[data-cat="errors"]` | Click | `GET /api/diagnostics/logs?category=errors` | Nạp và hiển thị nhật ký lỗi `errors.log` | Verified |
| 59 | Diag | Auto Scroll Checkbox | `#chk-auto-scroll-log` | Toggle | `logConsole.scrollTop` | Tự động cuộn xuống cuối khi nạp log mới | Verified |
| 60 | Diag | Refresh Log Button | `#btn-refresh-log` | Click | `loadDiagnosticsLog(currentCategory)` | Nạp lại log của danh mục hiện tại | Verified |
| 61 | Diag | Copy Log Button | `#btn-copy-log` | Click | `navigator.clipboard.writeText()` | Sao chép toàn bộ text log vào Clipboard và báo thông báo | Verified |
| 62 | Diag | Open Log Folder Button | `#btn-open-log-folder` | Click | `POST /api/diagnostics/open-folder` or bridge | Mở trực tiếp thư mục `workspace/logs/` trong Windows Explorer | Verified |
| 63 | Diag | Clear Log Button | `#btn-clear-log` | Click | `POST /api/diagnostics/logs/clear` | Xóa sạch nội dung log của danh mục đã chọn sau khi xác nhận | Verified |
| 64 | Diag | Log Console Box | `#log-console-output` | View | Display text | Khung hiển thị nội dung log dạng terminal monospaced | Verified |

---

## 6. Settings View (`#view-settings`)

| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 65 | Settings | LLM Provider Select | `#settings-llm-provider` | Select dropdown | Config save payload | Chọn: Gemini (Khuyên dùng), DeepSeek AI, OpenAI API, Ollama | Verified |
| 66 | Settings | Gemini Key Input | `#settings-gemini-key` | Password Input | Config save payload | Nhập Google Gemini API Key | Verified |
| 67 | Settings | Toggle Key Visibility | `#btn-toggle-gemini-key` | Click | `input.type = text / password` | Ẩn / hiện ký tự API Key an toàn | Verified |
| 68 | Settings | Gemini Model Select | `#settings-gemini-model` | Select dropdown | Config save payload | Chọn: gemini-2.0-flash, gemini-1.5-flash, gemini-1.5-pro | Verified |
| 69 | Settings | Test Gemini Button | `#btn-test-gemini` | Click | `POST /api/test-gemini` | Gửi request test thực tế đến Google Gemini API và đo độ trễ (latency ms) | Verified |
| 70 | Settings | Gemini Test Result | `#gemini-test-result` | Dynamic Text | Display latency & status | Hiển thị Kết nối OK (latency ms) hoặc báo lỗi chi tiết | Verified |
| 71 | Settings | DeepSeek Key Input | `#settings-deepseek-key` | Password Input | Config save payload | Nhập DeepSeek API Key tùy chọn | Verified |
| 72 | Settings | Suppression Mode Select | `#settings-suppression-mode` | Select dropdown | Config save payload | Chọn chế độ lọc realtime: AUTO, DSP Mono Formant, DSP Stereo Center Cancel | Verified |
| 73 | Settings | Ducking Level Select | `#settings-ducking-level` | Select dropdown | Config save payload | Chọn mức giảm BGM khi có thoại: -12dB, -14dB, -18dB, -24dB | Verified |
| 74 | Settings | Buffer Target Select | `#settings-buffer-target` | Select dropdown | Config save payload | Chọn độ trễ đệm tối thiểu: 5s, 10s, 15s | Verified |
| 75 | Settings | Save Settings Button | `#btn-save-settings` | Click | `POST /api/config` & save `.env` | Ghi đè cấu hình vào `.env` và cập nhật runtime settings của app | Verified |

---

## 7. HQ Export Modal (`#export-modal`)

| # | Page / Scope | Component | Element ID / Selector | Action | Backend / API / Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 76 | Modal | Close Modal Button | `#btn-close-export-modal` | Click | Hide `#export-modal` | Đóng modal xuất video HQ | Verified |
| 77 | Modal | Confirm Export Button | `#btn-confirm-export` | Click | `POST /api/streaming/export-hq` | Bắt đầu chạy pipeline xuất video HQ (BS-RoFormer + Ducking + Subtitle + Render) | Verified |
| 78 | Modal | Export Status Box | `#export-status-box` | Progress View | Dynamic stage & progress | Hiển thị tiến độ xuất video (%) và tên bước đang thực hiện | Verified |
| 79 | Modal | Export Progress Bar | `#export-progress-bar` | Progress Indicator | CSS `width: %` | Thanh tiến độ 0-100% kèm thời gian ước tính (ETA) | Verified |
| 80 | Modal | Cancel Export Button | `#btn-cancel-export` | Click | `POST /api/export-hq/cancel` | Cho phép hủy tác vụ xuất video bất kỳ lúc nào | Verified |
| 81 | Modal | Export Result Box | `#export-result-box` | State Box | Export completed event | Hiển thị thông báo hoàn thành kèm đường dẫn video | Verified |
| 82 | Modal | Save As Native Button | `#btn-save-as-native` | Click | `desktopBridge.saveVideoAs()` or browser download | Mở hộp thoại Windows "Save As..." lưu video về thư mục tùy chọn | Verified |

---

## 8. Desktop App & System Tray

| # | Page / Scope | Component | Selector / Method | Action | Backend / Qt Handler | Expected Behavior | Status |
|---|---|---|---|---|---|---|---|
| 83 | Desktop | Tray Open Action | `act_open` | Click | `main_win.restore_window()` | Hiện cửa sổ Studio lên trên màn hình | Verified |
| 84 | Desktop | Tray Pause Action | `act_pause` | Click | `studioPause()` | Tạm dừng pipeline xử lý từ tray menu | Verified |
| 85 | Desktop | Tray Resume Action | `act_resume` | Click | `studioResume()` | Tiếp tục pipeline xử lý từ tray menu | Verified |
| 86 | Desktop | Tray Exit Action | `act_exit` | Click | `main_win.close()` | Thoát sạch ứng dụng và tắt toàn bộ service ngầm | Verified |
| 87 | Desktop | Window Close Event | `closeEvent` | Click 'X' | Prompt & `service_manager.shutdown_all()` | Hỏi xác nhận nếu đang có tác vụ xử lý dở, sau đó tắt sạch tiến trình | Verified |

---
**Tổng cộng: 87 thành phần UI & thao tác điều khiển được kiểm kê đầy đủ.**
