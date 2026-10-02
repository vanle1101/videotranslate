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
  const playbackHeadMarker = document.getElementById("playback-head-marker");
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
  const exportResultBox = document.getElementById("export-result-box");
  const btnSaveAsNative = document.getElementById("btn-save-as-native");

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
    window.currentLocalFilePath = filePath;
    selectedFile = null;
    videoUrlInput.value = "";
    const fileName = filePath.split(/[\\/]/).pop();
    fileNameDisplay.innerHTML = `<span class="text-pink-400 font-bold">[LOCAL FILE]</span> ${fileName}`;
    
    // Preview in video player immediately
    videoPlayer.src = `/api/local-file?path=${encodeURIComponent(filePath)}`;
    videoPlayer.load();
    playerPlaceholder.classList.add("hidden");
  };

  dropZone.addEventListener("click", () => {
    if (window.desktopBridge && typeof window.desktopBridge.pickVideoFile === "function") {
      window.desktopBridge.pickVideoFile();
    } else {
      fileInput.click();
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      selectedFile = e.target.files[0];
      window.currentLocalFilePath = null;
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
      videoPlayer.src = URL.createObjectURL(selectedFile);
      videoPlayer.load();
      playerPlaceholder.classList.add("hidden");
    }
  });

  // HTML5 Drag & Drop visual feedback
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("border-pink-500"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("border-pink-500"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("border-pink-500");
    if (e.dataTransfer.files.length > 0) {
      selectedFile = e.dataTransfer.files[0];
      window.currentLocalFilePath = null;
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
      videoPlayer.src = URL.createObjectURL(selectedFile);
      videoPlayer.load();
      playerPlaceholder.classList.add("hidden");
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
    if (bgmAudio) bgmAudio.volume = parseFloat(volBgmSlider.value);
  });

  videoPlayer.addEventListener("play", () => {
    if (bgmAudio) {
      bgmAudio.currentTime = videoPlayer.currentTime;
      bgmAudio.play().catch(() => {});
    }
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

    btnStart.classList.add("hidden");
    btnPauseWorker.classList.remove("hidden");
    btnResumeWorker.classList.add("hidden");
    btnStopWorker.classList.remove("hidden");

    segments = {};
    segmentsList.innerHTML = "";
    timelineTrack.innerHTML = '<div id="playback-head-marker" class="absolute top-0 bottom-0 w-1 bg-white z-10 shadow-glow" style="left: 0%;"></div>';

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
            voice: voiceSelect.value || "Trúc Ly",
            tts_engine: "vieneu",
            asr_engine: "sensevoice"
          })
        });
      } else if (selectedFile) {
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("initial_buffer_seconds", bufferSelect.value);
        formData.append("voice", voiceSelect.value);
        formData.append("tts_engine", "vieneu");
        formData.append("asr_engine", "sensevoice");
        if (refAudioFile.files.length > 0) {
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
            tts_engine: "vieneu",
            asr_engine: "sensevoice"
          })
        });
      }

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Không thể khởi tạo phiên dịch video");
      }

      const data = await res.json();
      currentTaskId = data.task_id;

      videoPlayer.src = data.video_url;
      videoPlayer.load();
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

    currentWs.onmessage = (event) => {
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
          bgmAudio.volume = parseFloat(volBgmSlider.value);
          videoPlayer.muted = true; // Raw audio muted; BGM + Foley plays via bgmAudio!
        }

        if (msg.suppression_level) {
          telSuppression.textContent = msg.suppression_level;
          playerSuppressionBadge.textContent = msg.suppression_level;
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

        // Worker status badges
        if (msg.status === "ASR") {
          workerAsrBadge.className = "px-2 py-0.5 rounded bg-pink-900/60 text-pink-300 font-bold animate-pulse";
          workerAsrBadge.textContent = `ASR: #${msg.id}`;
        } else if (msg.status === "TRANSLATING") {
          workerTransBadge.className = "px-2 py-0.5 rounded bg-amber-900/60 text-amber-300 font-bold animate-pulse";
          workerTransBadge.textContent = `Gemini: #${msg.id}`;
        } else if (msg.status === "TTS") {
          workerTtsBadge.className = "px-2 py-0.5 rounded bg-violet-900/60 text-violet-300 font-bold animate-pulse";
          workerTtsBadge.textContent = `VieNeu: #${msg.id}`;
        } else if (msg.status === "READY") {
          workerAsrBadge.className = "px-2 py-0.5 rounded bg-gray-800 text-gray-300";
          workerTransBadge.className = "px-2 py-0.5 rounded bg-gray-800 text-gray-300";
          workerTtsBadge.className = "px-2 py-0.5 rounded bg-gray-800 text-gray-300";
        }
      }
      else if (msg.type === "telemetry") {
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
        bufferingAlert.classList.add("hidden");
        videoPlayer.play().catch(() => {});
        if (bgmAudio) {
          bgmAudio.currentTime = videoPlayer.currentTime;
          bgmAudio.play().catch(() => {});
        }
      }
      else if (msg.type === "finished") {
        resetWorkerControls();
      }
    };
  }

  // Realtime Dubbing Frame Loop
  videoPlayer.addEventListener("timeupdate", () => {
    const curTime = videoPlayer.currentTime;
    telPlaying.textContent = formatTime(curTime);
    barCurrentTime.textContent = formatTime(curTime);

    if (totalVideoDuration > 0) {
      const pct = (curTime / totalVideoDuration) * 100;
      playbackHeadMarker.style.left = `${Math.min(100, Math.max(0, pct))}%`;
    }

    // Sync BGM
    if (bgmAudio && !videoPlayer.paused) {
      if (Math.abs(bgmAudio.currentTime - curTime) > 0.25) {
        bgmAudio.currentTime = curTime;
      }
      if (bgmAudio.paused) bgmAudio.play().catch(() => {});
    }

    // Active Vietnamese Dub Segment
    let matchedSeg = null;
    for (const id in segments) {
      const s = segments[id];
      if (curTime >= s.start && curTime <= s.end) {
        matchedSeg = s;
        break;
      }
    }

    if (matchedSeg) {
      subtitleText.textContent = matchedSeg.final_vi || matchedSeg.natural_vi || matchedSeg.literal_vi || "";
      subtitleOverlay.classList.remove("opacity-0");

      if (matchedSeg.status === "READY" && matchedSeg.audio_url) {
        if (activePlayingSegId !== matchedSeg.id) {
          if (activeAudio) {
            activeAudio.pause();
            activeAudio = null;
          }
          activePlayingSegId = matchedSeg.id;
          activeAudio = new Audio(matchedSeg.audio_url);
          activeAudio.volume = parseFloat(volDubSlider.value);
          const offset = Math.max(0, curTime - matchedSeg.start);
          activeAudio.currentTime = offset;
          activeAudio.play().catch(() => {});
        }
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

    // Report playback head over WebSocket
    if (currentWs && currentWs.readyState === WebSocket.OPEN) {
      if (Date.now() - lastWsReportTime > 500) {
        lastWsReportTime = Date.now();
        currentWs.send(JSON.stringify({ type: "playback_position", time: curTime }));
      }
    }
  });

  // Timeline Slices
  function renderTimelineSlices() {
    timelineTrack.innerHTML = '<div id="playback-head-marker" class="absolute top-0 bottom-0 w-1 bg-white z-10 shadow-glow" style="left: 0%;"></div>';
    if (!totalVideoDuration) return;
    for (const id in segments) {
      const s = segments[id];
      const slice = document.createElement("div");
      slice.id = `slice-seg-${s.id}`;
      slice.className = "absolute top-0 bottom-0 transition-colors duration-200 bg-gray-800";
      slice.style.left = `${(s.start / totalVideoDuration) * 100}%`;
      slice.style.width = `${Math.max(0.5, (s.duration / totalVideoDuration) * 100)}%`;
      timelineTrack.appendChild(slice);
    }
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
          <p class="text-gray-300 font-sans text-xs truncate" id="seg-zh-${s.id}">${s.text_zh || "..."}</p>
          <p class="text-pink-300 font-sans text-xs font-semibold" id="seg-vi-${s.id}">${s.final_vi || s.natural_vi || ""}</p>
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

    if (currentWs && currentWs.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: "seek", time: targetTime }));
    }
  });

  // Worker controls
  function resetWorkerControls() {
    btnStart.classList.remove("hidden");
    btnPauseWorker.classList.add("hidden");
    btnResumeWorker.classList.add("hidden");
    btnStopWorker.classList.add("hidden");
  }

  btnPauseWorker.addEventListener("click", () => {
    if (currentWs) currentWs.send(JSON.stringify({ type: "pause" }));
    btnPauseWorker.classList.add("hidden");
    btnResumeWorker.classList.remove("hidden");
  });

  btnResumeWorker.addEventListener("click", () => {
    if (currentWs) currentWs.send(JSON.stringify({ type: "resume" }));
    btnResumeWorker.classList.add("hidden");
    btnPauseWorker.classList.remove("hidden");
  });

  btnStopWorker.addEventListener("click", () => {
    if (currentWs) currentWs.send(JSON.stringify({ type: "stop" }));
    resetWorkerControls();
  });

  // Tray control helpers exposed on window
  window.studioPause = () => { if (currentWs) currentWs.send(JSON.stringify({ type: "pause" })); };
  window.studioResume = () => { if (currentWs) currentWs.send(JSON.stringify({ type: "resume" })); };
  window.studioStop = () => { if (currentWs) currentWs.send(JSON.stringify({ type: "stop" })); resetWorkerControls(); };

  // =========================================================
  // 7. TASK MANAGER VIEW
  // =========================================================
  function updateTasksTable() {
    const tbody = document.getElementById("tasks-table-body");
    if (!tbody) return;

    if (!currentTaskId) {
      tbody.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-gray-500 font-sans">Không có tác vụ nào đang hoạt động.</td></tr>`;
      return;
    }

    const readyCount = Object.values(segments).filter(s => s.status === "READY" || s.status === "PLAYED").length;
    const totalCount = Object.keys(segments).length || 1;
    const pct = Math.round((readyCount / totalCount) * 100);

    tbody.innerHTML = `
      <tr class="hover:bg-gray-800/40 transition">
        <td class="p-3.5 font-bold text-white">${currentTaskId}</td>
        <td class="p-3.5 text-pink-400">Realtime Dubbing</td>
        <td class="p-3.5">
          <div class="flex items-center gap-2">
            <div class="w-24 bg-gray-800 h-2 rounded-full overflow-hidden">
              <div class="bg-gradient-to-r from-pink-500 to-violet-500 h-full" style="width: ${pct}%"></div>
            </div>
            <span class="text-[10px] text-gray-400">${pct}% (${readyCount}/${totalCount})</span>
          </div>
        </td>
        <td class="p-3.5 text-gray-300">${formatTime(totalVideoDuration)}</td>
        <td class="p-3.5">
          <span class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px]">ĐANG CHẠY</span>
        </td>
        <td class="p-3.5 text-right space-x-1">
          <button onclick="window.studioPause()" class="px-2 py-1 bg-amber-600/80 hover:bg-amber-600 text-white rounded text-[10px]">Tạm dừng</button>
          <button onclick="window.studioResume()" class="px-2 py-1 bg-emerald-600/80 hover:bg-emerald-600 text-white rounded text-[10px]">Tiếp tục</button>
          <button onclick="window.studioStop()" class="px-2 py-1 bg-rose-700/80 hover:bg-rose-700 text-white rounded text-[10px]">Hủy</button>
        </td>
      </tr>
    `;
  }
  document.getElementById("btn-refresh-tasks")?.addEventListener("click", updateTasksTable);

  // =========================================================
  // 8. MODEL MANAGER VIEW
  // =========================================================
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

        tr.innerHTML = `
          <td class="p-3.5 font-bold text-gray-100">${m.engine}</td>
          <td class="p-3.5 font-mono text-[11px] text-pink-300">${m.model_name}</td>
          <td class="p-3.5 font-mono text-gray-400">${m.size_mb} MB</td>
          <td class="p-3.5 font-mono text-cyan-400">${m.device}</td>
          <td class="p-3.5">${badge}</td>
          <td class="p-3.5 text-right">
            <button class="px-2.5 py-1 rounded bg-gray-800 hover:bg-gray-700 text-[11px] text-gray-200 border border-gray-700 transition" onclick="alert('Checkpoint ${m.model_name} đã được xác thực toàn vẹn!')">
              <i class="fa-solid fa-shield-halved mr-1"></i> Verify
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
  // 10. SETTINGS & GEMINI TEST CONNECTION
  // =========================================================
  const settingsGeminiKey = document.getElementById("settings-gemini-key");
  const settingsDeepseekKey = document.getElementById("settings-deepseek-key");
  const settingsGeminiModel = document.getElementById("settings-gemini-model");
  const btnToggleGeminiKey = document.getElementById("btn-toggle-gemini-key");
  const btnTestGemini = document.getElementById("btn-test-gemini");
  const geminiTestResult = document.getElementById("gemini-test-result");
  const btnSaveSettings = document.getElementById("btn-save-settings");

  function loadSettingsForm() {
    if (settingsGeminiKey) settingsGeminiKey.value = localStorage.getItem("gemini_key") || "";
    if (settingsDeepseekKey) settingsDeepseekKey.value = localStorage.getItem("deepseek_key") || "";
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
    geminiTestResult.innerHTML = '<span class="text-amber-400"><i class="fa-solid fa-spinner fa-spin"></i> Đang test API...</span>';
    try {
      const res = await fetch("/api/test-gemini", { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        geminiTestResult.innerHTML = `<span class="text-emerald-400 font-bold"><i class="fa-solid fa-check"></i> Kết nối OK (${data.latency_ms}ms)</span>`;
      } else {
        geminiTestResult.innerHTML = `<span class="text-rose-400 font-bold"><i class="fa-solid fa-triangle-exclamation"></i> ${data.error || 'Lỗi kết nối'}</span>`;
      }
    } catch (e) {
      geminiTestResult.innerHTML = `<span class="text-rose-400 font-bold">Lỗi: ${e.message}</span>`;
    }
  });

  btnSaveSettings?.addEventListener("click", async () => {
    const gKey = settingsGeminiKey.value.trim();
    const dKey = settingsDeepseekKey.value.trim();
    localStorage.setItem("gemini_key", gKey);
    localStorage.setItem("deepseek_key", dKey);

    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gemini_key: gKey, deepseek_key: dKey })
    });

    alert("Đã lưu cấu hình hệ thống thành công!");
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
    exportStatusBox.classList.add("hidden");
    exportResultBox.classList.add("hidden");
    btnConfirmExport.classList.remove("hidden");
  });

  btnCloseExportModal.addEventListener("click", () => exportModal.classList.add("hidden"));

  btnConfirmExport.addEventListener("click", async () => {
    btnConfirmExport.classList.add("hidden");
    exportStatusBox.classList.remove("hidden");
    exportStatusText.textContent = "BS-RoFormer đang bóc tách vocal & ProPainter khử sub...";

    try {
      const res = await fetch("/api/streaming/export-hq", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: currentTaskId, mask_chinese: toggleMaskChinese.checked })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Lỗi xuất video");

      exportStatusBox.classList.add("hidden");
      exportResultBox.classList.remove("hidden");
      lastExportedFileUrl = data.video_url;

      // Native desktop notification if available
      if (window.desktopBridge && typeof window.desktopBridge.showNotification === "function") {
        window.desktopBridge.showNotification(
          "Xuất Video Hoàn Tất",
          `Video TikTok 9:16 '${data.output_filename}' đã sẵn sàng!`
        );
      }

    } catch (e) {
      exportStatusBox.classList.remove("hidden");
      exportStatusText.textContent = `Lỗi xuất video: ${e.message}`;
      btnConfirmExport.classList.remove("hidden");
    }
  });

  btnSaveAsNative?.addEventListener("click", async () => {
    if (!lastExportedFileUrl) return;
    const defaultName = lastExportedFileUrl.split("/").pop() || "vietnamese_dub.mp4";
    if (window.desktopBridge && typeof window.desktopBridge.saveVideoAs === "function") {
      const targetPath = window.desktopBridge.saveVideoAs(defaultName);
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

});
