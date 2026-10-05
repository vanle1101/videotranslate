document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const videoPlayer = document.getElementById("video-player");
  const playerPlaceholder = document.getElementById("player-placeholder");
  const subtitleOverlay = document.getElementById("subtitle-overlay");
  const subtitleText = document.getElementById("subtitle-text");
  const chineseSubMask = document.getElementById("chinese-sub-mask");
  const bufferingAlert = document.getElementById("buffering-alert");
  const bufferingText = document.getElementById("buffering-text");

  // Controls
  const videoUrlInput = document.getElementById("video-url");
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("video-file");
  const fileNameDisplay = document.getElementById("file-name-display");
  const bufferSelect = document.getElementById("buffer-select");
  const voiceSelect = document.getElementById("voice-select");
  const refAudioFile = document.getElementById("ref-audio-file");
  const btnStart = document.getElementById("btn-start");
  const btnPauseWorker = document.getElementById("btn-pause-worker");
  const btnResumeWorker = document.getElementById("btn-resume-worker");
  const btnStopWorker = document.getElementById("btn-stop-worker");
  const btnExportHQ = document.getElementById("btn-export-hq");

  // Toggles & Volumes
  const toggleMaskChinese = document.getElementById("toggle-mask-chinese");
  const toggleSubtitles = document.getElementById("toggle-subtitles");
  const volDubSlider = document.getElementById("vol-dub");
  const volDubVal = document.getElementById("vol-dub-val");
  const volBgmSlider = document.getElementById("vol-bgm");
  const volBgmVal = document.getElementById("vol-bgm-val");

  // Telemetry Ribbon
  const telPlaying = document.getElementById("tel-playing");
  const telPlayable = document.getElementById("tel-playable");
  const telBuffer = document.getElementById("tel-buffer");
  const telTtfp = document.getElementById("tel-ttfp");
  const telRtf = document.getElementById("tel-rtf");
  const telSuppression = document.getElementById("tel-suppression");
  const playerSuppressionBadge = document.getElementById("player-suppression-badge");
  const workerAsrBadge = document.getElementById("worker-asr-badge");
  const workerTransBadge = document.getElementById("worker-trans-badge");
  const workerTtsBadge = document.getElementById("worker-tts-badge");

  // Timeline & Segments
  const timelineTrack = document.getElementById("segments-timeline-track");
  let playbackHeadMarker = document.getElementById("playback-head-marker");
  const barCurrentTime = document.getElementById("bar-current-time");
  const barTotalTime = document.getElementById("bar-total-time");
  const barBufferInfo = document.getElementById("bar-buffer-info");
  const segmentsList = document.getElementById("segments-list");
  const segmentsCountBadge = document.getElementById("segments-count-badge");

  // Hardware Pill
  const hardwarePill = document.getElementById("hardware-pill");
  const gpuInfoText = document.getElementById("gpu-info-text");

  // Export Modal
  const exportModal = document.getElementById("export-modal");
  const btnCloseExportModal = document.getElementById("btn-close-export-modal");
  const btnConfirmExport = document.getElementById("btn-confirm-export");
  const exportStatusBox = document.getElementById("export-status-box");
  const exportStatusText = document.getElementById("export-status-text");
  const exportProgressBar = document.getElementById("export-progress-bar");
  const exportProgressPct = document.getElementById("export-progress-pct");
  const btnCancelExport = document.getElementById("btn-cancel-export");
  const exportResultBox = document.getElementById("export-result-box");
  const btnSaveAsNative = document.getElementById("btn-save-as-native");

  // Settings Elements
  const settingsLlmProvider = document.getElementById("settings-llm-provider");
  const settingsGeminiKey = document.getElementById("settings-gemini-key");
  const settingsDeepseekKey = document.getElementById("settings-deepseek-key");
  const settingsGeminiModel = document.getElementById("settings-gemini-model");
  const settingsOpenCodeModel = document.getElementById("settings-opencode-model");
  const openCodeAuthStatus = document.getElementById("opencode-auth-status");
  const btnTestOpenCode = document.getElementById("btn-test-opencode");
  const openCodeTestResult = document.getElementById("opencode-test-result");
  const settingsOpenRouterModel = document.getElementById("settings-openrouter-model");
  const openRouterAuthStatus = document.getElementById("openrouter-auth-status");
  const btnTestOpenRouter = document.getElementById("btn-test-openrouter");
  const openRouterTestResult = document.getElementById("openrouter-test-result");
  const settingsSuppressionMode = document.getElementById("settings-suppression-mode");
  const settingsDuckingLevel = document.getElementById("settings-ducking-level");
  const settingsBufferTarget = document.getElementById("settings-buffer-target");
  const btnToggleGeminiKey = document.getElementById("btn-toggle-gemini-key");
  const btnTestGemini = document.getElementById("btn-test-gemini");
  const geminiTestResult = document.getElementById("gemini-test-result");
  const btnSaveSettings = document.getElementById("btn-save-settings");

  // Connect to Desktop Bridge via QWebChannel
  if (typeof QWebChannel !== "undefined" && window.qt && window.qt.webChannelTransport) {
    new QWebChannel(window.qt.webChannelTransport, function(channel) {
      window.desktopBridge = channel.objects.desktopBridge;
      console.log("[Bridge] Native desktop bridge connected successfully.");
    });
  }

  // Session State
  let currentTaskId = null;
  let currentWs = null;
  let selectedFile = null;
  window.currentLocalFilePath = null;
  let totalVideoDuration = 0;
  let segments = {}; // segId -> segment data
  let activeAudio = null;
  let activePlayingSegId = null;
  let bgmAudio = null;
  let bgmUrl = null;
  let isBufferingUnderrun = false;
  let lastWsReportTime = 0;
  let lastExportedFileUrl = "";
  let previewObjectUrl = null;
  let exportingTaskId = null;
  let exportCancelled = false;
  let duckingLevel = -14;
  let previewGeneration = 0;
  let previewDescriptor = null;
  let previewFallbackTried = false;
  let previewPending = false;
  let previewResumeTime = null;
  let playWhenPreviewReady = false;
  let translationReady = false;
  let audioPermissionNeeded = false;
  const mediaPlayButton = document.createElement("button");
  mediaPlayButton.type = "button";
  mediaPlayButton.className = "hidden rounded-lg bg-pink-600 px-4 py-2 text-xs font-semibold text-white";
  mediaPlayButton.textContent = "Bấm để phát video và âm thanh";
  bufferingAlert.appendChild(mediaPlayButton);

  function reportPlayFailure(error) {
    if (error.name === "AbortError") return;
    if (error.name === "NotAllowedError") {
      audioPermissionNeeded = true;
      videoPlayer.pause();
      bufferingText.textContent = "Trình duyệt cần một thao tác để bật âm thanh.";
      mediaPlayButton.classList.remove("hidden");
      bufferingAlert.classList.remove("hidden");
    } else {
      showMediaError("Không thể phát âm thanh hoặc video. Xem Diagnostics rồi thử lại.");
    }
  }
  mediaPlayButton.addEventListener("click", () => {
    audioPermissionNeeded = false;
    mediaPlayButton.classList.add("hidden");
    bufferingAlert.classList.add("hidden");
    if (bgmAudio) bgmAudio.play().catch(reportPlayFailure);
    if (activeAudio) activeAudio.play().catch(reportPlayFailure);
    videoPlayer.play().catch(reportPlayFailure);
  });

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch]));
  }

  function stopPreviewAudio() {
    isBufferingUnderrun = false;
    videoPlayer.pause();
    if (activeAudio) activeAudio.pause();
    if (bgmAudio) bgmAudio.pause();
    activeAudio = null;
    activePlayingSegId = null;
    bgmAudio = null;
    videoPlayer.muted = false;
  }

  function setPreviewSource(source, descriptor = null) {
    const canReuse = previewFallbackTried && !previewPending && videoPlayer.readyState >= 2 &&
      videoPlayer.currentSrc?.includes("/api/preview/") && descriptor?.task_id &&
      ((previewDescriptor?.file_path && previewDescriptor.file_path === window.currentLocalFilePath) ||
       (previewDescriptor?.file && previewDescriptor.file === selectedFile));
    stopPreviewAudio();
    previewGeneration++;
    previewDescriptor = descriptor || (typeof source === "string" ? { file_path: window.currentLocalFilePath } : { file: source });
    previewFallbackTried = !!canReuse;
    previewPending = false;
    audioPermissionNeeded = false;
    mediaPlayButton.classList.add("hidden");
    previewResumeTime = null;
    playWhenPreviewReady = false;
    if (canReuse) {
      videoPlayer.currentTime = 0;
      playerPlaceholder.classList.add("hidden");
      return;
    }
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = typeof source === "string" ? null : URL.createObjectURL(source);
    videoPlayer.src = previewObjectUrl || source;
    videoPlayer.load();
    playerPlaceholder.classList.add("hidden");
  }

  function showMediaError(message) {
    previewPending = false;
    videoPlayer.pause();
    bufferingText.textContent = message;
    bufferingAlert.classList.remove("hidden");
  }

  async function loadCompatiblePreview() {
    if (previewFallbackTried) {
      showMediaError("Không thể phát bản xem trước. Hãy kiểm tra FFmpeg trong Diagnostics rồi chọn lại video.");
      return;
    }
    previewFallbackTried = true;
    previewPending = true;
    const generation = previewGeneration;
    const resumeTime = videoPlayer.currentTime || 0;
    const shouldPlay = !videoPlayer.paused || translationReady;
    videoPlayer.pause();
    bufferingText.textContent = "Đang chuẩn bị bản xem trước tương thích… Video gốc vẫn được giữ nguyên.";
    bufferingAlert.classList.remove("hidden");
    try {
      let response;
      if (previewDescriptor?.file) {
        const body = new FormData();
        body.append("file", previewDescriptor.file);
        response = await fetch("/api/preview/upload", { method: "POST", body });
      } else {
        response = await fetch("/api/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(previewDescriptor || {}) });
      }
      let data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Không thể chuẩn bị bản xem trước");
      while (generation === previewGeneration && data.status === "PROCESSING") {
        await new Promise(resolve => setTimeout(resolve, 400));
        if (generation !== previewGeneration) return;
        response = await fetch(`/api/preview/${data.preview_id}`);
        data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Không đọc được trạng thái bản xem trước");
      }
      if (generation !== previewGeneration) return;
      if (data.status !== "READY") throw new Error(data.error || "Không thể tạo bản xem trước");
      previewResumeTime = resumeTime;
      playWhenPreviewReady = shouldPlay || translationReady;
      videoPlayer.src = data.video_url;
      videoPlayer.load();
    } catch (error) {
      if (generation === previewGeneration) showMediaError(`Lỗi xem trước: ${error.message}`);
    }
  }

  videoPlayer.addEventListener("error", () => {
    if (videoPlayer.error && [3, 4].includes(videoPlayer.error.code)) loadCompatiblePreview();
    else showMediaError("Không tải được video. Hãy kiểm tra đường dẫn hoặc chọn lại file.");
  });
  videoPlayer.addEventListener("loadedmetadata", () => {
    if (previewResumeTime !== null) videoPlayer.currentTime = Math.min(previewResumeTime, videoPlayer.duration || 0);
  });
  videoPlayer.addEventListener("canplay", () => {
    if (previewResumeTime === null) return;
    previewResumeTime = null;
    previewPending = false;
    if (!currentTaskId || translationReady) bufferingAlert.classList.add("hidden");
    else bufferingText.textContent = "Đang chờ các câu dịch đầu tiên...";
    if (playWhenPreviewReady) videoPlayer.play().catch(reportPlayFailure);
  });

  function selectPreviewSource(source) {
    segments = {};
    translationReady = false;
    totalVideoDuration = 0;
    currentTaskId = null;
    if (currentWs) currentWs.close();
    currentWs = null;
    subtitleText.textContent = "";
    document.getElementById("pipeline-warning")?.classList.add("hidden");
    bufferingAlert.classList.add("hidden");
    renderSegmentsDrawer();
    renderTimelineSlices();
    setPreviewSource(source);
  }

  function setSelectValue(select, value, label = value) {
    if (!select || value == null) return;
    if (![...select.options].some(option => option.value === String(value))) {
      select.add(new Option(String(label), String(value)));
    }
    select.value = String(value);
  }

  // =========================================================
  // 1. NAVIGATION TABS (STUDIO / TASKS / MODELS / DIAGNOSTICS / SETTINGS)
  // =========================================================
  const navTabs = [
    { btnId: "tab-studio", viewId: "view-studio" },
    { btnId: "tab-tasks", viewId: "view-tasks" },
    { btnId: "tab-models", viewId: "view-models" },
    { btnId: "tab-diagnostics", viewId: "view-diagnostics" },
    { btnId: "tab-settings", viewId: "view-settings" }
  ];

  function switchTab(targetViewId) {
    navTabs.forEach(({ btnId, viewId }) => {
      const btn = document.getElementById(btnId);
      const view = document.getElementById(viewId);
      if (viewId === targetViewId) {
        view.classList.remove("hidden");
        btn.className = "nav-btn px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-pink-600/80 shadow transition flex items-center gap-1.5";
      } else {
        view.classList.add("hidden");
        btn.className = "nav-btn px-3 py-1.5 rounded-lg text-xs font-semibold text-gray-400 hover:text-gray-200 transition flex items-center gap-1.5";
      }
    });

    if (targetViewId === "view-models") loadModelsTable();
    if (targetViewId === "view-diagnostics") loadDiagnosticsLog(currentLogCategory);
    if (targetViewId === "view-tasks") updateTasksTable();
    if (targetViewId === "view-settings") loadSettingsForm();
  }

  navTabs.forEach(({ btnId, viewId }) => {
    const btn = document.getElementById(btnId);
    if (btn) btn.addEventListener("click", () => switchTab(viewId));
  });

  // =========================================================
  // 2. HARDWARE DETECTION
  // =========================================================
  async function loadHardware() {
    try {
      const res = await fetch("/api/hardware");
      if (res.ok) {
        const data = await res.json();
        if (data.gpu_name) {
          const vram = data.vram_total_mb ? ` (${Math.round(data.vram_total_mb)}MB VRAM)` : "";
          gpuInfoText.textContent = `${data.gpu_name}${vram}`;
        } else {
          gpuInfoText.textContent = `CPU Mode (${data.cpu_count || 4} Cores)`;
        }
      }
    } catch (e) {
      gpuInfoText.textContent = "Hardware Ready";
    }
  }
  loadHardware();

  // =========================================================
  // 3. FILE SELECTION & DESKTOP DRAG & DROP
  // =========================================================
  window.loadDroppedLocalVideo = function(filePath) {
    if (!filePath) return;
    if (btnStart.classList.contains("hidden") || exportingTaskId) {
      alert("Hãy dừng phiên dịch hiện tại trước khi chọn video khác.");
      return;
    }
    window.currentLocalFilePath = filePath;
    selectedFile = null;
    videoUrlInput.value = "";
    const fileName = filePath.split(/[\\/]/).pop();
    fileNameDisplay.textContent = `[LOCAL FILE] ${fileName}`;
    
    // Preview in video player immediately
    selectPreviewSource(`/api/local-file?path=${encodeURIComponent(filePath)}`);
  };

  dropZone.addEventListener("click", () => {
    if (btnStart.classList.contains("hidden") || exportingTaskId) return;
    if (window.desktopBridge && typeof window.desktopBridge.pickVideoFile === "function") {
      window.desktopBridge.pickVideoFile();
    } else {
      fileInput.click();
    }
  });
  dropZone.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      dropZone.click();
    }
  });
  fileInput.addEventListener("click", event => event.stopPropagation());
  videoUrlInput.addEventListener("input", () => {
    if (!videoUrlInput.value.trim()) return;
    selectedFile = null;
    window.currentLocalFilePath = null;
    fileInput.value = "";
    fileNameDisplay.textContent = "Chọn file từ máy (Native Dialog)";
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      selectedFile = e.target.files[0];
      window.currentLocalFilePath = null;
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
      selectPreviewSource(selectedFile);
    }
  });

  // HTML5 Drag & Drop visual feedback
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("border-pink-500"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("border-pink-500"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("border-pink-500");
    if (btnStart.classList.contains("hidden") || exportingTaskId) return;
    if (e.dataTransfer.files.length > 0) {
      selectedFile = e.dataTransfer.files[0];
      window.currentLocalFilePath = null;
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
      selectPreviewSource(selectedFile);
    }
  });

  // =========================================================
  // 4. PLAYER CONTROLS & SIDECHAIN DUCKING
  // =========================================================
  toggleMaskChinese.addEventListener("change", () => {
    chineseSubMask.style.display = toggleMaskChinese.checked ? "block" : "none";
  });
  toggleSubtitles.addEventListener("change", () => {
    subtitleOverlay.style.display = toggleSubtitles.checked ? "block" : "none";
  });

  volDubSlider.addEventListener("input", () => {
    volDubVal.textContent = `${Math.round(volDubSlider.value * 100)}%`;
    if (activeAudio) activeAudio.volume = parseFloat(volDubSlider.value);
  });
  volBgmSlider.addEventListener("input", () => {
    volBgmVal.textContent = `${Math.round(volBgmSlider.value * 100)}%`;
    syncPlayback();
  });

  videoPlayer.addEventListener("play", () => {
    if (bgmAudio) {
      bgmAudio.currentTime = videoPlayer.currentTime;
      bgmAudio.play().catch(reportPlayFailure);
    }
    syncPlayback();
  });

  videoPlayer.addEventListener("pause", () => {
    if (bgmAudio) bgmAudio.pause();
    if (activeAudio) activeAudio.pause();
  });

  videoPlayer.addEventListener("seeking", () => {
    if (bgmAudio) bgmAudio.currentTime = videoPlayer.currentTime;
    if (activeAudio) {
      activeAudio.pause();
      activeAudio = null;
      activePlayingSegId = null;
    }
    if (currentWs?.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: "seek", time: videoPlayer.currentTime }));
    }
  });
  videoPlayer.addEventListener("seeked", syncPlayback);
  videoPlayer.addEventListener("ratechange", () => {
    if (bgmAudio) bgmAudio.playbackRate = videoPlayer.playbackRate;
    if (activeAudio) activeAudio.playbackRate = videoPlayer.playbackRate;
  });

  function formatTime(secs) {
    if (isNaN(secs) || secs < 0) return "00:00";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }

  // =========================================================
  // 5. START TRANSLATE & PLAY
  // =========================================================
  btnStart.addEventListener("click", async () => {
    const url = videoUrlInput.value.trim();
    if (!url && !selectedFile && !window.currentLocalFilePath) {
      alert("Vui lòng dán link Douyin hoặc chọn video từ máy!");
      return;
    }

    if (exportingTaskId) {
      alert("Hãy chờ xuất video hoặc hủy tác vụ xuất trước khi bắt đầu phiên mới.");
      return;
    }
    stopPreviewAudio();
    if (currentWs) currentWs.close();
    currentWs = null;
    currentTaskId = null;
    totalVideoDuration = 0;
    translationReady = false;
    lastExportedFileUrl = "";
    btnStart.classList.add("hidden");
    videoUrlInput.disabled = true;
    fileInput.disabled = true;
    dropZone.setAttribute("aria-disabled", "true");
    btnPauseWorker.classList.remove("hidden");
    btnResumeWorker.classList.add("hidden");
    btnStopWorker.classList.remove("hidden");

    segments = {};
    segmentsList.innerHTML = "";
    timelineTrack.innerHTML = '<div id="playback-head-marker" class="absolute top-0 bottom-0 w-1 bg-white z-10 shadow-glow" style="left: 0%;"></div>';
    playbackHeadMarker = document.getElementById("playback-head-marker");

    telTtfp.textContent = "--";
    telBuffer.textContent = "+0.0s";
    bufferingAlert.classList.remove("hidden");
    bufferingText.textContent = "Đang nạp video và bóc tách câu thoại...";

    try {
      let res;
      if (window.currentLocalFilePath) {
        // Fast local file start without HTTP upload
        res = await fetch("/api/streaming/start-local-file", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            file_path: window.currentLocalFilePath,
            initial_buffer_seconds: parseFloat(bufferSelect.value) || 10.0,
            voice: voiceSelect.value || "vi-VN-HoaiMyNeural",
            tts_engine: document.body.dataset.ttsEngine,
            asr_engine: document.body.dataset.asrEngine
          })
        });
      } else if (selectedFile) {
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("initial_buffer_seconds", bufferSelect.value);
        formData.append("voice", voiceSelect.value);
        formData.append("tts_engine", document.body.dataset.ttsEngine);
        formData.append("asr_engine", document.body.dataset.asrEngine);
        if (refAudioFile?.files.length > 0) {
          formData.append("ref_audio", refAudioFile.files[0]);
        }
        res = await fetch("/api/streaming/start-upload", { method: "POST", body: formData });
      } else {
        res = await fetch("/api/streaming/start-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: url,
            initial_buffer_seconds: parseFloat(bufferSelect.value),
            voice: voiceSelect.value,
            tts_engine: document.body.dataset.ttsEngine,
            asr_engine: document.body.dataset.asrEngine
          })
        });
      }

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Không thể khởi tạo phiên dịch video");
      }

      const data = await res.json();
      currentTaskId = data.task_id;

      setPreviewSource(data.video_url, { task_id: currentTaskId });
      videoPlayer.volume = parseFloat(volBgmSlider.value);
      playerPlaceholder.classList.add("hidden");

      setupStreamingWebSocket(currentTaskId);
      updateTasksTable();

    } catch (e) {
      alert("Lỗi: " + e.message);
      resetWorkerControls();
      bufferingAlert.classList.add("hidden");
    }
  });

  // =========================================================
  // 6. STREAMING WEBSOCKET & DUBBING SYNC
  // =========================================================
  function setupStreamingWebSocket(taskId) {
    if (currentWs) currentWs.close();

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    currentWs = new WebSocket(`${proto}//${window.location.host}/ws/stream/${taskId}`);

    const socket = currentWs;
    currentWs.onmessage = (event) => {
      if (socket !== currentWs) return;
      const msg = JSON.parse(event.data);

      if (msg.type === "init") {
        totalVideoDuration = msg.duration;
        barTotalTime.textContent = formatTime(totalVideoDuration);
        segmentsCountBadge.textContent = `${msg.segments_count} câu`;

        if (msg.bgm_url) {
          bgmUrl = msg.bgm_url;
          if (bgmAudio) {
            bgmAudio.pause();
            bgmAudio = null;
          }
          bgmAudio = new Audio(bgmUrl);
          bgmAudio.addEventListener("error", () => showMediaError("Không phát được nhạc nền. Xem Diagnostics và thử lại phiên dịch."));
          bgmAudio.volume = parseFloat(volBgmSlider.value);
          videoPlayer.muted = true; // Raw audio muted; BGM + Foley plays via bgmAudio!
        }

        if (msg.suppression_level) {
          telSuppression.textContent = `≈ ${msg.suppression_level}`;
          playerSuppressionBadge.textContent = `≈ ${msg.suppression_level}`;
        }

        msg.segments.forEach(s => {
          segments[s.id] = s;
        });
        renderTimelineSlices();
        renderSegmentsDrawer();
      }
      else if (msg.type === "segment_update") {
        segments[msg.id] = msg;
        updateSegmentSlice(msg);
        updateSegmentDrawerItem(msg);
        if (isBufferingUnderrun && ["READY", "PLAYED"].includes(msg.status) &&
            videoPlayer.currentTime >= msg.start && videoPlayer.currentTime < msg.end) {
          isBufferingUnderrun = false;
          bufferingAlert.classList.add("hidden");
          videoPlayer.play().catch(reportPlayFailure);
        }

        // Worker status badges
        if (msg.status === "ASR") {
          workerAsrBadge.className = "px-2 py-0.5 rounded bg-pink-900/60 text-pink-300 font-bold animate-pulse";
          workerAsrBadge.textContent = `ASR: #${msg.id}`;
        } else if (msg.status === "TRANSLATING") {
          workerTransBadge.className = "px-2 py-0.5 rounded bg-amber-900/60 text-amber-300 font-bold animate-pulse";
          workerTransBadge.textContent = `Dịch: #${msg.id}`;
        } else if (msg.status === "TTS") {
          workerTtsBadge.className = "px-2 py-0.5 rounded bg-violet-900/60 text-violet-300 font-bold animate-pulse";
          workerTtsBadge.textContent = `TTS: #${msg.id}`;
        } else if (msg.status === "READY") {
          workerAsrBadge.className = "px-2 py-0.5 rounded bg-gray-800 text-gray-300";
          workerTransBadge.className = "px-2 py-0.5 rounded bg-gray-800 text-gray-300";
          workerTtsBadge.className = "px-2 py-0.5 rounded bg-gray-800 text-gray-300";
        }
      }
      else if (msg.type === "telemetry") {
        const warning = document.getElementById("pipeline-warning");
        if (warning) {
          warning.textContent = (msg.warnings || []).join(" · ");
          warning.classList.toggle("hidden", !warning.textContent);
        }
        telPlayable.textContent = formatTime(msg.playable_until);
        const buf = msg.buffer_ahead || 0;
        telBuffer.textContent = `+${buf.toFixed(1)}s`;
        barBufferInfo.textContent = `Buffer: +${buf.toFixed(1)}s`;

        if (buf > 10) {
          telBuffer.className = "px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-950/80 text-emerald-400 border border-emerald-800/80";
        } else if (buf > 3) {
          telBuffer.className = "px-2 py-0.5 rounded text-[11px] font-bold bg-amber-950/80 text-amber-400 border border-amber-800/80";
        } else {
          telBuffer.className = "px-2 py-0.5 rounded text-[11px] font-bold bg-rose-950/80 text-rose-400 border border-rose-800/80 animate-pulse";
        }

        if (msg.realtime_factor) {
          telRtf.textContent = `${msg.realtime_factor.toFixed(1)}x`;
        }
        if (msg.time_to_first_play) {
          telTtfp.textContent = `${msg.time_to_first_play.toFixed(1)}s`;
        }
      }
      else if (msg.type === "ready_to_play") {
        translationReady = true;
        if (!previewPending) {
          bufferingAlert.classList.add("hidden");
          videoPlayer.play().catch(error => {
            if (error.name === "NotSupportedError") loadCompatiblePreview();
            else reportPlayFailure(error);
          });
        }
      }
      else if (msg.type === "export_progress") {
        if (exportProgressBar) exportProgressBar.style.width = `${msg.progress}%`;
        if (exportProgressPct) exportProgressPct.textContent = `${msg.progress}%`;
        if (exportStatusText) exportStatusText.textContent = msg.stage;
      }
      else if (msg.type === "error") {
        isBufferingUnderrun = false;
        videoPlayer.pause();
        bufferingText.textContent = msg.message || msg.error || "Xử lý thất bại. Xem Diagnostics rồi thử lại.";
        bufferingAlert.classList.remove("hidden");
        resetWorkerControls();
        updateTasksTable();
      }
      else if (msg.type === "finished") {
        resetWorkerControls();
        updateTasksTable();
      }
    };
  }

  // Realtime Dubbing Frame Loop
  function syncPlayback() {
    const curTime = videoPlayer.currentTime;
    telPlaying.textContent = formatTime(curTime);
    barCurrentTime.textContent = formatTime(curTime);

    if (totalVideoDuration > 0) {
      const pct = (curTime / totalVideoDuration) * 100;
      if (playbackHeadMarker) playbackHeadMarker.style.left = `${Math.min(100, Math.max(0, pct))}%`;
      timelineTrack.setAttribute("aria-valuenow", String(Math.round(curTime)));
    }

    // Sync BGM
    if (bgmAudio && !videoPlayer.paused) {
      bgmAudio.playbackRate = videoPlayer.playbackRate;
      if (Math.abs(bgmAudio.currentTime - curTime) > 0.25) {
        bgmAudio.currentTime = curTime;
      }
      if (bgmAudio.paused && !audioPermissionNeeded) bgmAudio.play().catch(reportPlayFailure);
    }

    // Active Vietnamese Dub Segment
    let matchedSeg = null;
    for (const id in segments) {
      const s = segments[id];
      if (curTime >= s.start && curTime < s.end) {
        matchedSeg = s;
        break;
      }
    }

    if (matchedSeg) {
      subtitleText.textContent = matchedSeg.final_vi || matchedSeg.natural_vi || matchedSeg.literal_vi || "";
      subtitleOverlay.classList.remove("opacity-0");
      if (!["READY", "PLAYED"].includes(matchedSeg.status) && !videoPlayer.paused) {
        isBufferingUnderrun = true;
        videoPlayer.pause();
        bufferingText.textContent = "Đang chờ dịch và tạo giọng cho câu tiếp theo...";
        bufferingAlert.classList.remove("hidden");
      }

      if (["READY", "PLAYED"].includes(matchedSeg.status) && matchedSeg.audio_url) {
        if (activePlayingSegId !== matchedSeg.id) {
          if (activeAudio) {
            activeAudio.pause();
            activeAudio = null;
          }
          activePlayingSegId = matchedSeg.id;
          activeAudio = new Audio(matchedSeg.audio_url);
          activeAudio.addEventListener("error", () => showMediaError(`Không phát được giọng đọc câu #${matchedSeg.id}. Xem Diagnostics rồi thử lại.`));
          activeAudio.volume = parseFloat(volDubSlider.value);
          const offset = Math.max(0, curTime - matchedSeg.start);
          activeAudio.currentTime = offset;
        }
        activeAudio.playbackRate = videoPlayer.playbackRate;
        const offset = Math.max(0, curTime - matchedSeg.start);
        if (Math.abs(activeAudio.currentTime - offset) > 0.25) activeAudio.currentTime = offset;
        if (!videoPlayer.paused && activeAudio.paused && !activeAudio.ended && !audioPermissionNeeded) activeAudio.play().catch(reportPlayFailure);
      }
    } else {
      subtitleText.textContent = "";
      subtitleOverlay.classList.add("opacity-0");
      if (activeAudio) {
        activeAudio.pause();
        activeAudio = null;
        activePlayingSegId = null;
      }
    }

    if (bgmAudio) {
      const speaking = matchedSeg && ["READY", "PLAYED"].includes(matchedSeg.status) && matchedSeg.audio_url;
      bgmAudio.volume = parseFloat(volBgmSlider.value) * (speaking ? Math.pow(10, duckingLevel / 20) : 1);
    }

    // Report playback head over WebSocket
    if (currentWs && currentWs.readyState === WebSocket.OPEN) {
      if (Date.now() - lastWsReportTime > 500) {
        lastWsReportTime = Date.now();
        currentWs.send(JSON.stringify({ type: "playback_position", time: curTime }));
      }
    }
  }
  videoPlayer.addEventListener("timeupdate", syncPlayback);

  // Timeline Slices
  function renderTimelineSlices() {
    timelineTrack.innerHTML = '<div id="playback-head-marker" class="absolute top-0 bottom-0 w-1 bg-white z-10 shadow-glow" style="left: 0%;"></div>';
    playbackHeadMarker = document.getElementById("playback-head-marker");
    timelineTrack.setAttribute("aria-valuemax", String(Math.ceil(totalVideoDuration)));
    if (!totalVideoDuration) return;
    for (const id in segments) {
      const s = segments[id];
      const slice = document.createElement("div");
      slice.id = `slice-seg-${s.id}`;
      slice.className = "absolute top-0 bottom-0 transition-colors duration-200 bg-gray-800";
      slice.style.left = `${(s.start / totalVideoDuration) * 100}%`;
      slice.style.width = `${Math.max(0.5, (s.duration / totalVideoDuration) * 100)}%`;
      timelineTrack.appendChild(slice);
      updateSegmentSlice(s);
    }
    syncPlayback();
  }

  function updateSegmentSlice(seg) {
    const el = document.getElementById(`slice-seg-${seg.id}`);
    if (!el) return;
    if (seg.status === "READY" || seg.status === "PLAYED") {
      el.className = "absolute top-0 bottom-0 transition-colors duration-200 bg-emerald-500/80";
    } else if (seg.status === "ASR" || seg.status === "TRANSLATING" || seg.status === "TTS") {
      el.className = "absolute top-0 bottom-0 transition-colors duration-200 bg-amber-500/80 animate-pulse";
    }
  }

  // Segments Drawer
  function renderSegmentsDrawer() {
    segmentsList.innerHTML = "";
    for (const id in segments) {
      const s = segments[id];
      const row = document.createElement("div");
      row.id = `seg-row-${s.id}`;
      row.className = "p-2 rounded-xl bg-gray-900/60 border border-gray-800/80 flex items-start justify-between gap-2 text-[11px]";
      row.innerHTML = `
        <div class="space-y-0.5 flex-1 min-w-0">
          <div class="flex items-center gap-2 text-gray-400 font-mono text-[10px]">
            <span>#${s.id}</span>
            <span>[${formatTime(s.start)} - ${formatTime(s.end)}]</span>
            <span id="seg-badge-${s.id}" class="px-1.5 py-0.2 rounded text-[9px] bg-gray-800 text-gray-300">${s.status}</span>
          </div>
          <p class="text-gray-300 font-sans text-xs truncate" id="seg-zh-${s.id}">${escapeHtml(s.text_zh || "...")}</p>
          <p class="text-pink-300 font-sans text-xs font-semibold" id="seg-vi-${s.id}">${escapeHtml(s.final_vi || s.natural_vi || "")}</p>
        </div>
      `;
      segmentsList.appendChild(row);
    }
  }

  function updateSegmentDrawerItem(seg) {
    let row = document.getElementById(`seg-row-${seg.id}`);
    if (!row) {
      renderSegmentsDrawer();
      row = document.getElementById(`seg-row-${seg.id}`);
      if (!row) return;
    }
    const badge = document.getElementById(`seg-badge-${seg.id}`);
    const zh = document.getElementById(`seg-zh-${seg.id}`);
    const vi = document.getElementById(`seg-vi-${seg.id}`);

    if (badge) {
      badge.textContent = seg.status;
      if (seg.status === "READY") {
        badge.className = "px-1.5 py-0.2 rounded text-[9px] bg-emerald-950 text-emerald-400 border border-emerald-800";
      } else if (seg.status === "ASR" || seg.status === "TRANSLATING" || seg.status === "TTS") {
        badge.className = "px-1.5 py-0.2 rounded text-[9px] bg-amber-950 text-amber-400 border border-amber-800 animate-pulse";
      }
    }
    if (zh && seg.text_zh) zh.textContent = seg.text_zh;
    if (vi && (seg.final_vi || seg.natural_vi)) vi.textContent = seg.final_vi || seg.natural_vi;
  }

  // Seeking on timeline
  timelineTrack.addEventListener("click", async (e) => {
    if (!totalVideoDuration || !currentTaskId) return;
    const rect = timelineTrack.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const pct = Math.max(0, Math.min(1, clickX / rect.width));
    const targetTime = pct * totalVideoDuration;

    videoPlayer.currentTime = targetTime;
    if (bgmAudio) bgmAudio.currentTime = targetTime;

  });
  timelineTrack.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || !totalVideoDuration) return;
    event.preventDefault();
    const target = event.key === "Home" ? 0 : event.key === "End" ? totalVideoDuration : videoPlayer.currentTime + (event.key === "ArrowLeft" ? -5 : 5);
    videoPlayer.currentTime = Math.min(totalVideoDuration, Math.max(0, target));
  });

  // Worker controls
  function resetWorkerControls() {
    videoUrlInput.disabled = false;
    fileInput.disabled = false;
    dropZone.setAttribute("aria-disabled", "false");
    btnStart.classList.remove("hidden");
    btnPauseWorker.classList.add("hidden");
    btnResumeWorker.classList.add("hidden");
    btnStopWorker.classList.add("hidden");
  }

  async function controlTask(id, action) {
    if (!id) return;
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(id)}/${action}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Không thể điều khiển tác vụ");
      if (id === currentTaskId) {
        if (action === "stop") {
          stopPreviewAudio();
          segments = {};
          translationReady = false;
          playWhenPreviewReady = false;
          previewGeneration++;
          previewPending = false;
          previewResumeTime = null;
          subtitleText.textContent = "";
          subtitleOverlay.classList.add("opacity-0");
          renderTimelineSlices();
          renderSegmentsDrawer();
          segmentsCountBadge.textContent = "0 câu";
          resetWorkerControls();
          bufferingAlert.classList.add("hidden");
          if (currentWs) {
            currentWs.onmessage = null;
            currentWs.close();
          }
          currentWs = null;
          currentTaskId = null;
        } else {
          btnPauseWorker.classList.toggle("hidden", action === "pause");
          btnResumeWorker.classList.toggle("hidden", action === "resume");
        }
      }
      await updateTasksTable();
    } catch (error) {
      alert("Lỗi điều khiển tác vụ: " + error.message);
    }
  }
  window.studioPause = () => controlTask(currentTaskId, "pause");
  window.studioResume = () => controlTask(currentTaskId, "resume");
  window.studioStop = () => controlTask(currentTaskId, "stop");
  btnPauseWorker.addEventListener("click", window.studioPause);
  btnResumeWorker.addEventListener("click", window.studioResume);
  btnStopWorker.addEventListener("click", window.studioStop);

  // =========================================================
  // 7. TASK MANAGER VIEW
  // =========================================================
  window.pauseTask = id => controlTask(id, "pause");
  window.resumeTask = id => controlTask(id, "resume");
  window.stopTask = id => controlTask(id, "stop");

  async function updateTasksTable() {
    const tbody = document.getElementById("tasks-table-body");
    if (!tbody) return;

    try {
      const res = await fetch("/api/tasks");
      const data = await res.json();
      const tasks = data.tasks || [];

      if (tasks.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-gray-500 font-sans">Không có tác vụ nào đang hoạt động.</td></tr>`;
        return;
      }

      tbody.innerHTML = "";
      tasks.forEach(t => {
        const tr = document.createElement("tr");
        tr.className = "hover:bg-gray-800/40 transition";

        let statusBadge = "";
        if (t.status === "RUNNING") {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] animate-pulse">ĐANG CHẠY</span>`;
        } else if (t.status === "PAUSED" || t.status === "CANCELLING") {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800 text-[10px]">${t.status === "CANCELLING" ? "ĐANG DỪNG" : "TẠM DỪNG"}</span>`;
        } else if (t.status === "COMPLETED") {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-blue-950 text-blue-400 border border-blue-800 text-[10px]">HOÀN THÀNH</span>`;
        } else if (t.status === "CANCELLED" || t.status === "STOPPED") {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700 text-[10px]">ĐÃ DỪNG</span>`;
        } else {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-rose-950 text-rose-400 border border-rose-800 text-[10px]">LỖI</span>`;
        }

        let actionButtons = "";
        if (t.can_pause) {
          actionButtons += `<button onclick="window.pauseTask('${t.task_id}')" class="px-2 py-1 bg-amber-600/80 hover:bg-amber-600 text-white rounded text-[10px]"><i class="fa-solid fa-pause mr-1"></i>Tạm dừng</button> `;
        }
        if (t.can_resume) {
          actionButtons += `<button onclick="window.resumeTask('${t.task_id}')" class="px-2 py-1 bg-emerald-600/80 hover:bg-emerald-600 text-white rounded text-[10px]"><i class="fa-solid fa-play mr-1"></i>Tiếp tục</button> `;
        }
        if (t.can_stop) {
          actionButtons += `<button onclick="window.stopTask('${t.task_id}')" class="px-2 py-1 bg-rose-700/80 hover:bg-rose-700 text-white rounded text-[10px]"><i class="fa-solid fa-stop mr-1"></i>Hủy</button> `;
        }
        if (t.status === "COMPLETED" && t.video_url) {
          actionButtons += `<a href="${t.video_url}" target="_blank" class="px-2 py-1 bg-pink-600 hover:bg-pink-500 text-white rounded text-[10px] inline-flex items-center gap-1"><i class="fa-solid fa-circle-play"></i>Xem kết quả</a> `;
        }

        tr.innerHTML = `
          <td class="p-3.5 font-bold text-white">${t.task_id}</td>
          <td class="p-3.5 text-pink-400 font-semibold">${t.task_type}</td>
          <td class="p-3.5">
            <div class="flex items-center gap-2">
              <div class="w-24 bg-gray-800 h-2 rounded-full overflow-hidden">
                <div class="bg-gradient-to-r from-pink-500 to-violet-500 h-full transition-all duration-300" style="width: ${t.progress_pct}%"></div>
              </div>
              <span class="text-[10px] text-gray-300 font-sans">${t.progress_pct}% (${escapeHtml(t.stage || '')})</span>
            </div>
          </td>
          <td class="p-3.5 text-gray-300">${formatTime(t.duration)}</td>
          <td class="p-3.5">${statusBadge}</td>
          <td class="p-3.5 text-right space-x-1">${actionButtons || '<span class="text-gray-500 text-[10px]">-</span>'}</td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {
      console.error("Error loading tasks:", e);
    }
  }
  document.getElementById("btn-refresh-tasks")?.addEventListener("click", updateTasksTable);
  setInterval(() => {
    if (!document.getElementById("view-tasks").classList.contains("hidden")) updateTasksTable();
  }, 2000);

  // =========================================================
  // 8. MODEL MANAGER VIEW
  // =========================================================
  window.verifyModel = async (query, btnElement) => {
    const origHtml = btnElement.innerHTML;
    btnElement.disabled = true;
    btnElement.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Đang test...';

    try {
      const res = await fetch("/api/models/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query })
      });
      const data = await res.json();
      if (data.ok) {
        btnElement.className = "px-2.5 py-1 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 text-[11px] font-semibold";
        btnElement.innerHTML = '<i class="fa-solid fa-circle-check mr-1"></i> Đã xác thực';
        alert(`[Xác Thực Checkpoint Toàn Vẹn]\nModel: ${data.model_name}\nDung lượng: ${data.size_mb} MB\nThiết bị: ${data.device}\nTrạng thái: ${data.status}\n\n${data.message}`);
      } else {
        btnElement.className = "px-2.5 py-1 rounded bg-rose-950 text-rose-300 border border-rose-800 text-[11px] font-semibold";
        btnElement.innerHTML = '<i class="fa-solid fa-circle-xmark mr-1"></i> Chưa có file';
        alert(`[Xác Thực Thất Bại]\n${data.message}`);
      }
    } catch (e) {
      btnElement.innerHTML = origHtml;
      btnElement.disabled = false;
      alert("Lỗi kiểm tra model: " + e.message);
    }
  };

  async function loadModelsTable() {
    const tbody = document.getElementById("models-full-table-body");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="6" class="p-6 text-center text-gray-500"><i class="fa-solid fa-spinner fa-spin"></i> Đang kiểm tra checkpoints...</td></tr>`;

    try {
      const res = await fetch("/api/models");
      const data = await res.json();
      tbody.innerHTML = "";

      data.models.forEach(m => {
        const tr = document.createElement("tr");
        tr.className = "hover:bg-gray-800/40 transition";
        const badge = m.downloaded
          ? `<span class="bg-emerald-950 text-emerald-400 border border-emerald-800 px-2.5 py-1 rounded text-[10px] font-bold"><i class="fa-solid fa-circle-check mr-1"></i> SẴN SÀNG</span>`
          : `<span class="bg-amber-950 text-amber-400 border border-amber-800 px-2.5 py-1 rounded text-[10px] font-bold">CHƯA TẢI</span>`;

        const safeName = m.model_name.replace(/'/g, "\\'");
        tr.innerHTML = `
          <td class="p-3.5 font-bold text-gray-100">${m.engine}</td>
          <td class="p-3.5 font-mono text-[11px] text-pink-300">${m.model_name}</td>
          <td class="p-3.5 font-mono text-gray-400">${m.size_mb} MB</td>
          <td class="p-3.5 font-mono text-cyan-400">${m.device}</td>
          <td class="p-3.5">${badge}</td>
          <td class="p-3.5 text-right">
            <button class="px-2.5 py-1 rounded bg-gray-800 hover:bg-gray-700 text-[11px] text-gray-200 border border-gray-700 transition" onclick="window.verifyModel('${safeName}', this)">
              <i class="fa-solid fa-shield-halved mr-1"></i> Verify Checkpoint
            </button>
          </td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6" class="p-6 text-center text-rose-400">Không thể tải thông tin models: ${e.message}</td></tr>`;
    }
  }
  document.getElementById("btn-reload-models")?.addEventListener("click", loadModelsTable);

  // =========================================================
  // 9. DIAGNOSTICS & LOG VIEWER
  // =========================================================
  let currentLogCategory = "app";
  const logConsole = document.getElementById("log-console-output");
  const chkAutoScroll = document.getElementById("chk-auto-scroll-log");

  async function loadDiagnosticsLog(cat) {
    currentLogCategory = cat;
    if (!logConsole) return;
    try {
      const res = await fetch(`/api/diagnostics/logs?category=${cat}&lines=150`);
      const data = await res.json();
      logConsole.textContent = data.logs || "(Nhật ký rỗng)";
      if (chkAutoScroll && chkAutoScroll.checked) {
        logConsole.scrollTop = logConsole.scrollHeight;
      }
    } catch (e) {
      logConsole.textContent = `Lỗi đọc log: ${e.message}`;
    }
  }

  document.querySelectorAll(".diag-cat-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".diag-cat-btn").forEach(b => {
        b.className = "diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono text-gray-400 hover:text-white";
      });
      btn.className = "diag-cat-btn px-3 py-1 rounded-lg text-xs font-mono font-bold bg-pink-600/80 text-white";
      loadDiagnosticsLog(btn.dataset.cat);
    });
  });

  document.getElementById("btn-refresh-log")?.addEventListener("click", () => loadDiagnosticsLog(currentLogCategory));

  document.getElementById("btn-copy-log")?.addEventListener("click", () => {
    if (!logConsole) return;
    navigator.clipboard.writeText(logConsole.textContent).then(() => {
      alert("Đã sao chép nội dung log vào Clipboard!");
    });
  });

  document.getElementById("btn-open-log-folder")?.addEventListener("click", () => {
    if (window.desktopBridge && typeof window.desktopBridge.openLogsFolder === "function") {
      window.desktopBridge.openLogsFolder();
    } else {
      fetch("/api/diagnostics/open-folder", { method: "POST" });
    }
  });

  document.getElementById("btn-clear-log")?.addEventListener("click", async () => {
    if (confirm(`Bạn có chắc muốn xóa sạch log danh mục '${currentLogCategory}'?`)) {
      await fetch(`/api/diagnostics/logs/clear?category=${currentLogCategory}`, { method: "POST" });
      loadDiagnosticsLog(currentLogCategory);
    }
  });

  // =========================================================
  // 10. SETTINGS & API CONNECTION CHECKS
  // =========================================================
  async function loadSettingsForm() {
    refreshMuseStatus();
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const cfg = await res.json();
        if (settingsLlmProvider && cfg.llm_provider) settingsLlmProvider.value = cfg.llm_provider;
        if (settingsGeminiKey) {
          settingsGeminiKey.value = "";
          settingsGeminiKey.placeholder = cfg.gemini_configured ? "Đã có key — để trống để giữ nguyên" : "Nhập Gemini API key";
        }
        if (settingsGeminiModel && cfg.gemini_model != null) settingsGeminiModel.value = cfg.gemini_model;
        const museMode = document.getElementById("settings-muse-browser-mode");
        if (museMode) museMode.value = cfg.muse_browser_mode || "dedicated";
        if (settingsOpenRouterModel) settingsOpenRouterModel.value = cfg.openrouter_model;
        if (openRouterAuthStatus) openRouterAuthStatus.textContent = cfg.openrouter_configured
          ? "Đã nhận key OpenRouter Free từ máy. Không cần nhập lại."
          : "Chưa có key OpenRouter. Kết nối OpenRouter trong OpenCode hoặc đặt OPENROUTER_API_KEY trong .env.";
        if (settingsDeepseekKey) {
          settingsDeepseekKey.value = "";
          settingsDeepseekKey.placeholder = cfg.deepseek_configured ? "Đã có key — để trống để giữ nguyên" : "Nhập DeepSeek API key";
        }
        if (settingsOpenCodeModel) {
          settingsOpenCodeModel.replaceChildren(...(cfg.opencode_free_models || []).map(model => new Option(model, model)));
          settingsOpenCodeModel.value = cfg.opencode_model;
        }
        if (openCodeAuthStatus) {
          openCodeAuthStatus.textContent = cfg.opencode_configured
            ? (cfg.opencode_cli_available
              ? "Đã nhận OpenCode và key trên máy. Không cần nhập lại."
              : "Đã nhận key, nhưng chưa tìm thấy OpenCode CLI. Hãy cài OpenCode trước.")
            : "Chưa tìm thấy key. Mở OpenCode → /connect → OpenCode Zen rồi tải lại tab Cài đặt.";
        }
        if (settingsSuppressionMode && cfg.suppression_mode) settingsSuppressionMode.value = cfg.suppression_mode;
        setSelectValue(settingsDuckingLevel, cfg.ducking_level, `${cfg.ducking_level} dB`);
        setSelectValue(settingsBufferTarget, cfg.buffer_target, `${cfg.buffer_target}s`);
        setSelectValue(bufferSelect, cfg.buffer_target, `${cfg.buffer_target} giây`);
        duckingLevel = Number(cfg.ducking_level ?? -14);
      }
    } catch (e) {
      console.warn("Could not load backend settings:", e);
      if (openCodeAuthStatus) openCodeAuthStatus.textContent = "Không thể tải cấu hình. Hãy mở lại tab Cài đặt.";
      if (openRouterAuthStatus) openRouterAuthStatus.textContent = "Không thể tải cấu hình. Hãy mở lại tab Cài đặt.";
    }
  }

  const museStatus = document.getElementById("muse-status");
  const museButtons = ["btn-muse-login", "btn-muse-status", "btn-test-muse"]
    .map(id => document.getElementById(id)).filter(Boolean);

  function showMuseStatus(state) {
    if (!museStatus) return;
    museStatus.textContent = !state.installed
      ? "Chưa cài Muse. Chạy setup_muse.bat một lần rồi kiểm tra lại."
      : !state.running
        ? (state.browser_mode === "existing"
          ? "Đã cài Muse. Bấm Kết nối Muse để dùng phiên Chrome đã đăng nhập."
          : "Đã cài Muse. Bấm Kết nối Muse để đăng nhập trong Chrome riêng của tool.")
        : state.logged_in && state.composer_ready
          ? "Muse đã đăng nhập và sẵn sàng. Bạn có thể thử dịch."
          : "Cửa sổ Muse đã mở. Hoàn tất đăng nhập hoặc yêu cầu truy cập trên trang, rồi kiểm tra lại.";
  }

  async function refreshMuseStatus() {
    try {
      const response = await fetch("/api/muse/status");
      if (!response.ok) throw new Error("status");
      const data = await response.json();
      if (data.ok === false) throw new Error("status");
      showMuseStatus(data);
    } catch {
      if (museStatus) museStatus.textContent = "Không thể kiểm tra Muse. Hãy thử lại.";
    }
  }

  document.getElementById("btn-muse-status")?.addEventListener("click", refreshMuseStatus);
  for (const [id, endpoint, waiting] of [
    ["btn-muse-login", "/api/muse/login", "Đang mở cửa sổ Muse…"],
    ["btn-test-muse", "/api/test-muse", "Đang thử dịch qua Muse…"]
  ]) {
    document.getElementById(id)?.addEventListener("click", async () => {
      museButtons.forEach(button => { button.disabled = true; });
      museStatus.textContent = waiting;
      try {
        const response = await fetch(endpoint, { method: "POST" });
        const data = await response.json();
        if (!response.ok || data.ok === false) {
          museStatus.textContent = data.error || "Không thể kết nối Muse. Hãy thử lại.";
        } else if (id === "btn-muse-login") {
          showMuseStatus(data.status || data);
        } else {
          museStatus.textContent = `Muse đã trả lời (${data.latency_ms} ms). Bạn có thể chọn Muse và lưu cấu hình.`;
        }
      } catch {
        museStatus.textContent = "Không thể kết nối với ứng dụng. Hãy thử lại.";
      } finally {
        museButtons.forEach(button => { button.disabled = false; });
      }
    });
  }

  btnToggleGeminiKey?.addEventListener("click", () => {
    if (settingsGeminiKey.type === "password") {
      settingsGeminiKey.type = "text";
      btnToggleGeminiKey.innerHTML = '<i class="fa-regular fa-eye-slash text-xs"></i>';
    } else {
      settingsGeminiKey.type = "password";
      btnToggleGeminiKey.innerHTML = '<i class="fa-regular fa-eye text-xs"></i>';
    }
  });

  btnTestGemini?.addEventListener("click", async () => {
    btnTestGemini.disabled = true;
    geminiTestResult.textContent = "Đang kiểm tra model đã lưu…";
    try {
      const res = await fetch("/api/test-gemini", { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        geminiTestResult.textContent = `Kết nối OK: ${data.model} (${data.latency_ms} ms)`;
      } else {
        geminiTestResult.textContent = data.error || "Lỗi kết nối Gemini";
      }
    } catch (e) {
      geminiTestResult.textContent = "Không thể kết nối với ứng dụng. Hãy thử lại.";
    } finally {
      btnTestGemini.disabled = false;
    }
  });

  btnTestOpenCode?.addEventListener("click", async () => {
    btnTestOpenCode.disabled = true;
    openCodeTestResult.textContent = "Đang kiểm tra model đã lưu…";
    try {
      const res = await fetch("/api/test-opencode", { method: "POST" });
      const data = await res.json();
      openCodeTestResult.textContent = data.ok
        ? `Kết nối OK: ${data.model} (${data.latency_ms} ms)`
        : data.error || "Lỗi kết nối OpenCode";
    } catch (e) {
      openCodeTestResult.textContent = "Không thể kết nối với ứng dụng. Hãy thử lại.";
    } finally {
      btnTestOpenCode.disabled = false;
    }
  });

  btnTestOpenRouter?.addEventListener("click", async () => {
    btnTestOpenRouter.disabled = true;
    openRouterTestResult.textContent = "Đang kiểm tra model đã lưu…";
    try {
      const res = await fetch("/api/test-openrouter", { method: "POST" });
      const data = await res.json();
      openRouterTestResult.textContent = data.ok
        ? `Kết nối OK: ${data.model} (${data.latency_ms} ms)`
        : data.error || "Lỗi kết nối OpenRouter Free";
    } catch (e) {
      openRouterTestResult.textContent = "Không thể kết nối với ứng dụng. Hãy thử lại.";
    } finally {
      btnTestOpenRouter.disabled = false;
    }
  });

  btnSaveSettings?.addEventListener("click", async () => {
    const payload = {
      llm_provider: settingsLlmProvider?.value || "openrouter-free",
      openrouter_model: settingsOpenRouterModel?.value.trim() || "inclusionai/ling-3.0-flash-sante:free",
      opencode_model: settingsOpenCodeModel?.value || "big-pickle",
      gemini_key: settingsGeminiKey?.value.trim() || undefined,
      gemini_model: settingsGeminiModel?.value || "gemini-2.5-flash",
      muse_browser_mode: document.getElementById("settings-muse-browser-mode")?.value || "dedicated",
      deepseek_key: settingsDeepseekKey?.value.trim() || undefined,
      suppression_mode: settingsSuppressionMode?.value || "AUTO",
      ducking_level: settingsDuckingLevel?.value || "-14",
      buffer_target: settingsBufferTarget?.value || "10"
    };

    localStorage.removeItem("gemini_key");
    localStorage.removeItem("deepseek_key");

    const origText = btnSaveSettings.innerHTML;
    btnSaveSettings.disabled = true;
    btnSaveSettings.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1.5"></i> Đang lưu cấu hình...';

    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Không thể lưu cấu hình");
      await loadSettingsForm();
      btnSaveSettings.innerHTML = '<i class="fa-solid fa-check mr-1.5"></i> Đã lưu thành công!';
      setTimeout(() => {
        btnSaveSettings.innerHTML = origText;
        btnSaveSettings.disabled = false;
      }, 2000);
      alert(data.message || "Đã lưu cấu hình hệ thống thành công vào .env!");
    } catch (e) {
      btnSaveSettings.innerHTML = origText;
      btnSaveSettings.disabled = false;
      alert("Lỗi lưu cấu hình: " + e.message);
    }
  });

  // =========================================================
  // 11. HQ EXPORT & NATIVE SAVE
  // =========================================================
  btnExportHQ.addEventListener("click", () => {
    if (!currentTaskId) {
      alert("Chưa có session video nào đang chạy!");
      return;
    }
    exportModal.classList.remove("hidden");
    exportStatusBox.classList.toggle("hidden", !exportingTaskId);
    exportResultBox.classList.toggle("hidden", !lastExportedFileUrl || !!exportingTaskId);
    btnConfirmExport.classList.toggle("hidden", !!exportingTaskId);
    btnCloseExportModal.focus();
  });

  function closeExportModal() {
    exportModal.classList.add("hidden");
    btnExportHQ.focus();
  }
  btnCloseExportModal.addEventListener("click", closeExportModal);
  exportModal.addEventListener("keydown", event => {
    if (event.key === "Escape") closeExportModal();
    if (event.key !== "Tab") return;
    const buttons = [...exportModal.querySelectorAll("button, a, input, select")].filter(el => !el.disabled && el.getClientRects().length);
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });

  btnCancelExport?.addEventListener("click", async () => {
    if (!exportingTaskId) return;
    try {
      const response = await fetch(`/api/streaming/export-hq/cancel/${exportingTaskId}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Không thể hủy tác vụ xuất");
      exportCancelled = true;
      btnCancelExport.disabled = true;
      if (exportStatusText) exportStatusText.textContent = "Đang hủy tác vụ xuất video...";
      updateTasksTable();
    } catch (e) {
      alert("Lỗi hủy xuất: " + e.message);
    }
  });

  btnConfirmExport.addEventListener("click", async () => {
    if (!currentTaskId || exportingTaskId) return;
    const taskId = currentTaskId;
    exportingTaskId = taskId;
    exportCancelled = false;
    btnCancelExport.disabled = false;
    btnConfirmExport.classList.add("hidden");
    exportStatusBox.classList.remove("hidden");
    exportResultBox.classList.add("hidden");
    if (exportProgressBar) exportProgressBar.style.width = "5%";
    if (exportProgressPct) exportProgressPct.textContent = "5%";
    exportStatusText.textContent = "Đang chuẩn bị âm thanh, phụ đề và xuất video...";

    // Poll for status updates
    const pollInterval = setInterval(async () => {
      try {
        const sRes = await fetch(`/api/streaming/export-hq/status/${taskId}`);
        if (sRes.ok) {
          const sData = await sRes.json();
          if (exportProgressBar) exportProgressBar.style.width = `${sData.progress}%`;
          if (exportProgressPct) exportProgressPct.textContent = `${sData.progress}%`;
          if (exportStatusText) exportStatusText.textContent = sData.stage;
          if (sData.status === "COMPLETED" || sData.status === "FAILED" || sData.status === "CANCELLED") {
            clearInterval(pollInterval);
          }
        }
      } catch (_) {}
    }, 1000);

    try {
      const res = await fetch("/api/streaming/export-hq", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId, mask_chinese: toggleMaskChinese.checked })
      });
      clearInterval(pollInterval);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Lỗi xuất video");

      if (exportProgressBar) exportProgressBar.style.width = "100%";
      if (exportProgressPct) exportProgressPct.textContent = "100%";
      exportStatusBox.classList.add("hidden");
      exportResultBox.classList.remove("hidden");
      lastExportedFileUrl = data.video_url;
      updateTasksTable();

      // Native desktop notification if available
      if (window.desktopBridge && typeof window.desktopBridge.showNotification === "function") {
        window.desktopBridge.showNotification(
          "Xuất Video Hoàn Tất",
          `Video TikTok 9:16 '${data.output_filename}' đã sẵn sàng!`
        );
      }

    } catch (e) {
      clearInterval(pollInterval);
      exportStatusBox.classList.remove("hidden");
      exportStatusText.textContent = exportCancelled ? "Đã hủy xuất video." : `Lỗi xuất video: ${e.message}`;
      btnConfirmExport.classList.remove("hidden");
    } finally {
      exportingTaskId = null;
      btnCancelExport.disabled = true;
      updateTasksTable();
    }
  });

  btnSaveAsNative?.addEventListener("click", async () => {
    if (!lastExportedFileUrl) return;
    const defaultName = lastExportedFileUrl.split("/").pop() || "vietnamese_dub.mp4";
    if (window.desktopBridge && typeof window.desktopBridge.saveVideoAs === "function") {
      const targetPath = await new Promise(resolve => window.desktopBridge.saveVideoAs(defaultName, resolve));
      if (targetPath) {
        alert(`Đã lưu file thành công tại:\n${targetPath}`);
      }
    } else {
      const a = document.createElement("a");
      a.href = lastExportedFileUrl;
      a.download = defaultName;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
  });

  loadSettingsForm();
});
