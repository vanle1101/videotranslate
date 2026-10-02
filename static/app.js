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
  const workerAsrBadge = document.getElementById("worker-asr-badge");
  const workerTransBadge = document.getElementById("worker-trans-badge");
  const workerTtsBadge = document.getElementById("worker-tts-badge");

  // Timeline
  const timelineTrack = document.getElementById("segments-timeline-track");
  const playbackHeadMarker = document.getElementById("playback-head-marker");
  const barCurrentTime = document.getElementById("bar-current-time");
  const barTotalTime = document.getElementById("bar-total-time");
  const barBufferInfo = document.getElementById("bar-buffer-info");

  // Segments Drawer
  const segmentsList = document.getElementById("segments-list");
  const segmentsCountBadge = document.getElementById("segments-count-badge");

  // Modals
  const hardwarePill = document.getElementById("hardware-pill");
  const gpuInfoText = document.getElementById("gpu-info-text");
  const btnModelsModal = document.getElementById("btn-models-modal");
  const modelsModal = document.getElementById("models-modal");
  const btnCloseModelsModal = document.getElementById("btn-close-models-modal");
  const modelsTableBody = document.getElementById("models-table-body");
  const btnSettingsModal = document.getElementById("btn-settings-modal");
  const settingsModal = document.getElementById("settings-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const btnSaveKeys = document.getElementById("btn-save-keys");
  const modalGeminiKey = document.getElementById("modal-gemini-key");
  const modalDeepseekKey = document.getElementById("modal-deepseek-key");

  // Export Modal
  const exportModal = document.getElementById("export-modal");
  const btnCloseExportModal = document.getElementById("btn-close-export-modal");
  const btnConfirmExport = document.getElementById("btn-confirm-export");
  const exportStatusBox = document.getElementById("export-status-box");
  const exportStatusText = document.getElementById("export-status-text");
  const exportResultBox = document.getElementById("export-result-box");
  const exportActions = document.getElementById("export-actions");
  const btnDownloadHQ = document.getElementById("btn-download-hq");

  // Session State
  let currentTaskId = null;
  let currentWs = null;
  let selectedFile = null;
  let totalVideoDuration = 0;
  let segments = {}; // segId -> segment data
  let activeAudio = null;
  let activePlayingSegId = null;
  let isBufferingUnderrun = false;
  let lastWsReportTime = 0;

  // 1. Hardware Detection
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
        hardwarePill.classList.remove("hidden");
      }
    } catch (e) {
      gpuInfoText.textContent = "Hardware detected";
    }
  }
  loadHardware();

  // 2. Modals Handling
  btnModelsModal.addEventListener("click", async () => {
    modelsModal.classList.remove("hidden");
    modelsTableBody.innerHTML = `<tr><td colspan="5" class="p-4 text-center text-gray-500"><i class="fa-solid fa-spinner fa-spin"></i> Đang tải...</td></tr>`;
    try {
      const res = await fetch("/api/models");
      const data = await res.json();
      modelsTableBody.innerHTML = "";
      data.models.forEach(m => {
        const tr = document.createElement("tr");
        tr.className = "hover:bg-gray-800/40 transition";
        const badge = m.downloaded
          ? `<span class="bg-emerald-950 text-emerald-400 border border-emerald-800 px-2 py-0.5 rounded text-[10px] font-mono"><i class="fa-solid fa-check"></i> SẴN SÀNG</span>`
          : `<span class="bg-amber-950 text-amber-400 border border-amber-800 px-2 py-0.5 rounded text-[10px] font-mono">CHƯA TẢI</span>`;
        tr.innerHTML = `
          <td class="p-2.5 font-semibold text-gray-200">${m.engine}</td>
          <td class="p-2.5 font-mono text-[11px] text-pink-300">${m.model_name}</td>
          <td class="p-2.5 font-mono text-gray-400">${m.size_mb} MB</td>
          <td class="p-2.5 text-gray-400 font-mono text-[11px]">${m.device}</td>
          <td class="p-2.5">${badge}</td>
        `;
        modelsTableBody.appendChild(tr);
      });
    } catch (e) {
      modelsTableBody.innerHTML = `<tr><td colspan="5" class="p-4 text-center text-rose-400">Lỗi tải danh sách</td></tr>`;
    }
  });
  btnCloseModelsModal.addEventListener("click", () => modelsModal.classList.add("hidden"));

  btnSettingsModal.addEventListener("click", () => {
    modalGeminiKey.value = localStorage.getItem("gemini_key") || "";
    modalDeepseekKey.value = localStorage.getItem("deepseek_key") || "";
    settingsModal.classList.remove("hidden");
  });
  btnCloseModal.addEventListener("click", () => settingsModal.classList.add("hidden"));
  btnSaveKeys.addEventListener("click", async () => {
    localStorage.setItem("gemini_key", modalGeminiKey.value.trim());
    localStorage.setItem("deepseek_key", modalDeepseekKey.value.trim());
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gemini_key: modalGeminiKey.value.trim(),
        deepseek_key: modalDeepseekKey.value.trim()
      })
    });
    settingsModal.classList.add("hidden");
    alert("Đã lưu API Keys!");
  });

  // 3. Drag & Drop File Upload
  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      selectedFile = e.target.files[0];
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
    }
  });
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("border-pink-500"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("border-pink-500"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("border-pink-500");
    if (e.dataTransfer.files.length > 0) {
      selectedFile = e.dataTransfer.files[0];
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
    }
  });

  // Toggles
  toggleMaskChinese.addEventListener("change", () => {
    chineseSubMask.style.display = toggleMaskChinese.checked ? "block" : "none";
  });
  toggleSubtitles.addEventListener("change", () => {
    subtitleOverlay.style.display = toggleSubtitles.checked ? "block" : "none";
  });

  // Volume Sliders
  volDubSlider.addEventListener("input", () => {
    volDubVal.textContent = `${Math.round(volDubSlider.value * 100)}%`;
    if (activeAudio) {
      activeAudio.volume = parseFloat(volDubSlider.value);
    }
  });
  volBgmSlider.addEventListener("input", () => {
    volBgmVal.textContent = `${Math.round(volBgmSlider.value * 100)}%`;
    if (!activePlayingSegId) {
      videoPlayer.volume = parseFloat(volBgmSlider.value);
    }
  });

  function formatTime(secs) {
    if (isNaN(secs) || secs < 0) return "00:00";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }

  // 4. Start Streaming Pipeline
  btnStart.addEventListener("click", async () => {
    const url = videoUrlInput.value.trim();
    if (!url && !selectedFile) {
      alert("Vui lòng dán link video Douyin/TikTok hoặc chọn file video!");
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
    bufferingText.textContent = "Đang nạp video và phân tích câu thoại...";

    try {
      let res;
      if (selectedFile) {
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
        throw new Error(err.detail || "Không thể khởi động session");
      }

      const data = await res.json();
      currentTaskId = data.task_id;

      // Attach video source to player
      videoPlayer.src = data.video_url;
      videoPlayer.load();
      videoPlayer.volume = parseFloat(volBgmSlider.value);
      playerPlaceholder.classList.add("hidden");

      // Connect WebSocket
      setupStreamingWebSocket(currentTaskId);

    } catch (e) {
      alert("Lỗi: " + e.message);
      resetWorkerControls();
      bufferingAlert.classList.add("hidden");
    }
  });

  // 5. Streaming WebSocket
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

        // Update worker badges
        if (msg.status === "ASR") {
          workerAsrBadge.className = "px-2 py-0.5 rounded bg-pink-900/60 text-pink-300 font-bold animate-pulse";
          workerAsrBadge.textContent = `ASR: #${msg.id}`;
        } else if (msg.status === "TRANSLATING") {
          workerTransBadge.className = "px-2 py-0.5 rounded bg-amber-900/60 text-amber-300 font-bold animate-pulse";
          workerTransBadge.textContent = `Trans: #${msg.id}`;
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

        // Resume if was buffering underrun
        if (isBufferingUnderrun && buf >= 3.0) {
          isBufferingUnderrun = false;
          bufferingAlert.classList.add("hidden");
          videoPlayer.play();
        }
      }
      else if (msg.type === "ready_to_play") {
        bufferingAlert.classList.add("hidden");
        telTtfp.textContent = `${msg.time_to_first_play}s`;
        videoPlayer.play().catch(e => console.log("Autoplay policy:", e));
      }
      else if (msg.type === "finished") {
        workerAsrBadge.textContent = "ASR: Xong";
        workerTransBadge.textContent = "Trans: Xong";
        workerTtsBadge.textContent = "TTS: Xong";
      }
    };
  }

  // 6. Video Time Update & Realtime Audio Sync
  videoPlayer.addEventListener("timeupdate", () => {
    const cur = videoPlayer.currentTime;
    telPlaying.textContent = formatTime(cur);
    barCurrentTime.textContent = formatTime(cur);

    // Update marker on timeline
    if (totalVideoDuration > 0) {
      const pct = (cur / totalVideoDuration) * 100;
      document.getElementById("playback-head-marker").style.left = `${pct}%`;
    }

    // Send playback position to WebSocket (throttled every 400ms)
    const now = Date.now();
    if (now - lastWsReportTime > 400 && currentWs && currentWs.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: "playback_position", time: cur }));
      lastWsReportTime = now;
    }

    // Subtitle & Dubbing Sync
    let matchedSeg = null;
    for (const id in segments) {
      const s = segments[id];
      if (cur >= s.start && cur <= s.end) {
        matchedSeg = s;
        break;
      }
    }

    // Subtitle Overlay
    if (matchedSeg && toggleSubtitles.checked && (matchedSeg.final_vi || matchedSeg.text_vi)) {
      subtitleText.textContent = matchedSeg.final_vi || matchedSeg.text_vi;
      subtitleOverlay.classList.remove("opacity-0");
    } else {
      subtitleOverlay.classList.add("opacity-0");
    }

    // Real-Time Audio Ducking & Dub Playback
    if (matchedSeg && matchedSeg.status === "READY" && matchedSeg.audio_url) {
      if (activePlayingSegId !== matchedSeg.id) {
        activePlayingSegId = matchedSeg.id;

        if (activeAudio) {
          activeAudio.pause();
          activeAudio = null;
        }

        activeAudio = new Audio(matchedSeg.audio_url);
        activeAudio.volume = parseFloat(volDubSlider.value);

        // Sidechain: duck video volume to 20%
        const originalVol = parseFloat(volBgmSlider.value);
        videoPlayer.volume = originalVol * 0.2;

        activeAudio.play().catch(e => console.log("Dub play error:", e));

        activeAudio.onended = () => {
          // Restore video volume
          videoPlayer.volume = parseFloat(volBgmSlider.value);
          activePlayingSegId = null;
        };
      }
    } else {
      if (!activePlayingSegId) {
        videoPlayer.volume = parseFloat(volBgmSlider.value);
      }
    }
  });

  // 7. Timeline Slices Rendering
  function renderTimelineSlices() {
    timelineTrack.innerHTML = '<div id="playback-head-marker" class="absolute top-0 bottom-0 w-1 bg-white z-10 shadow-glow" style="left: 0%;"></div>';
    if (totalVideoDuration <= 0) return;

    for (const id in segments) {
      const s = segments[id];
      const slice = document.createElement("div");
      slice.id = `slice-seg-${s.id}`;
      const leftPct = (s.start / totalVideoDuration) * 100;
      const widthPct = Math.max(0.5, (s.duration / totalVideoDuration) * 100);
      slice.style.left = `${leftPct}%`;
      slice.style.width = `${widthPct}%`;
      slice.className = "absolute top-0 bottom-0 bg-gray-700/60 hover:brightness-125 transition-colors";
      slice.title = `#${s.id} (${s.start}s - ${s.end}s)`;
      timelineTrack.appendChild(slice);
    }
  }

  function updateSegmentSlice(seg) {
    const el = document.getElementById(`slice-seg-${seg.id}`);
    if (!el) return;
    if (seg.status === "READY" || seg.status === "PLAYED") {
      el.className = "absolute top-0 bottom-0 bg-emerald-500/80 transition-colors shadow-sm";
    } else if (seg.status === "TTS") {
      el.className = "absolute top-0 bottom-0 bg-violet-500/80 animate-pulse transition-colors";
    } else if (seg.status === "TRANSLATING") {
      el.className = "absolute top-0 bottom-0 bg-amber-500/80 animate-pulse transition-colors";
    } else if (seg.status === "ASR") {
      el.className = "absolute top-0 bottom-0 bg-pink-500/80 animate-pulse transition-colors";
    }
  }

  // Seek clicking on Timeline
  timelineTrack.addEventListener("click", (e) => {
    if (totalVideoDuration <= 0) return;
    const rect = timelineTrack.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const pct = Math.max(0, Math.min(1, clickX / rect.width));
    const targetTime = pct * totalVideoDuration;

    videoPlayer.currentTime = targetTime;
    if (currentWs && currentWs.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: "seek", time: targetTime }));
    }
  });

  // 8. Segments Drawer
  function renderSegmentsDrawer() {
    segmentsList.innerHTML = "";
    for (const id in segments) {
      const s = segments[id];
      const item = document.createElement("div");
      item.id = `drawer-seg-${s.id}`;
      item.className = "p-2.5 rounded-xl bg-[#090b10] border border-gray-800 space-y-1.5 hover:border-gray-700 transition";
      item.innerHTML = getDrawerItemHtml(s);
      segmentsList.appendChild(item);
    }
  }

  function updateSegmentDrawerItem(seg) {
    const el = document.getElementById(`drawer-seg-${seg.id}`);
    if (el) {
      el.innerHTML = getDrawerItemHtml(seg);
    }
  }

  function getDrawerItemHtml(s) {
    let badgeClass = "bg-gray-800 text-gray-400";
    if (s.status === "READY") badgeClass = "bg-emerald-950 text-emerald-400 border border-emerald-800/80";
    else if (s.status === "TTS") badgeClass = "bg-violet-950 text-violet-400 animate-pulse";
    else if (s.status === "TRANSLATING") badgeClass = "bg-amber-950 text-amber-400 animate-pulse";
    else if (s.status === "ASR") badgeClass = "bg-pink-950 text-pink-400 animate-pulse";

    return `
      <div class="flex items-center justify-between text-[10px] text-gray-400">
        <span class="font-mono text-pink-400 font-semibold">${s.start.toFixed(1)}s - ${s.end.toFixed(1)}s (${s.duration.toFixed(1)}s)</span>
        <span class="px-2 py-0.5 rounded text-[9px] font-mono ${badgeClass}">${s.status}</span>
      </div>
      <div class="text-[11px] text-gray-300 italic font-sans">${s.text_zh || "..."}</div>
      <div class="text-xs text-yellow-300 font-medium">${s.final_vi || s.text_vi || "..."}</div>
    `;
  }

  // 9. Worker Controls
  btnPauseWorker.addEventListener("click", () => {
    if (currentWs && currentWs.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: "pause" }));
      btnPauseWorker.classList.add("hidden");
      btnResumeWorker.classList.remove("hidden");
    }
  });

  btnResumeWorker.addEventListener("click", () => {
    if (currentWs && currentWs.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: "resume" }));
      btnResumeWorker.classList.add("hidden");
      btnPauseWorker.classList.remove("hidden");
    }
  });

  btnStopWorker.addEventListener("click", () => {
    if (confirm("Bạn có chắc muốn dừng pipeline?")) {
      if (currentWs && currentWs.readyState === WebSocket.OPEN) {
        currentWs.send(JSON.stringify({ type: "stop" }));
      }
      resetWorkerControls();
    }
  });

  function resetWorkerControls() {
    btnStart.classList.remove("hidden");
    btnPauseWorker.classList.add("hidden");
    btnResumeWorker.classList.add("hidden");
    btnStopWorker.classList.add("hidden");
  }

  // 10. HQ Export Handling
  btnExportHQ.addEventListener("click", () => {
    if (!currentTaskId) {
      alert("Chưa có video nào đang chạy để xuất HQ!");
      return;
    }
    exportModal.classList.remove("hidden");
    exportStatusBox.classList.add("hidden");
    exportResultBox.classList.add("hidden");
    exportActions.classList.remove("hidden");
  });
  btnCloseExportModal.addEventListener("click", () => exportModal.classList.add("hidden"));

  btnConfirmExport.addEventListener("click", async () => {
    exportActions.classList.add("hidden");
    exportStatusBox.classList.remove("hidden");
    exportStatusText.textContent = "Đang chạy BS-RoFormer bóc tách BGM & render TikTok 9:16...";

    try {
      const res = await fetch("/api/streaming/export-hq", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: currentTaskId,
          mask_chinese: toggleMaskChinese.checked
        })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Xuất thất bại");
      }

      const data = await res.json();
      exportStatusBox.classList.add("hidden");
      exportResultBox.classList.remove("hidden");
      btnDownloadHQ.href = data.video_url;
      btnDownloadHQ.setAttribute("download", data.output_filename);

    } catch (e) {
      alert("Lỗi xuất HQ: " + e.message);
      exportActions.classList.remove("hidden");
      exportStatusBox.classList.add("hidden");
    }
  });
});
