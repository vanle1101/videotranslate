document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("video-file");
  const fileNameDisplay = document.getElementById("file-name-display");
  const videoUrlInput = document.getElementById("video-url");
  const btnStart = document.getElementById("btn-start");
  const btnPause = document.getElementById("btn-pause");
  const btnResume = document.getElementById("btn-resume");
  const btnStop = document.getElementById("btn-stop");

  // Selectors
  const asrSelect = document.getElementById("asr-select");
  const ttsEngineSelect = document.getElementById("tts-engine-select");
  const voiceSelect = document.getElementById("voice-select");
  const refAudioFile = document.getElementById("ref-audio-file");
  const separatorSelect = document.getElementById("separator-select");

  // Monitor UI
  const stagesList = document.getElementById("stages-list");
  const overallPercent = document.getElementById("overall-percent");
  const liveLogBox = document.getElementById("live-log-box");
  const gpuInfoText = document.getElementById("gpu-info-text");
  const hardwarePill = document.getElementById("hardware-pill");

  // Results UI
  const resultCard = document.getElementById("result-card");
  const videoPlayer = document.getElementById("output-video-player");
  const downloadVideoBtn = document.getElementById("download-video-btn");
  const downloadSrtBtn = document.getElementById("download-srt-btn");
  const segmentsContainer = document.getElementById("segments-container");
  const btnReRender = document.getElementById("btn-re-render");

  // Models Modal
  const btnModelsModal = document.getElementById("btn-models-modal");
  const modelsModal = document.getElementById("models-modal");
  const btnCloseModelsModal = document.getElementById("btn-close-models-modal");
  const modelsTableBody = document.getElementById("models-table-body");

  // Settings Modal
  const settingsModal = document.getElementById("settings-modal");
  const btnSettingsModal = document.getElementById("btn-settings-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const btnSaveKeys = document.getElementById("btn-save-keys");
  const modalGeminiKey = document.getElementById("modal-gemini-key");
  const modalDeepseekKey = document.getElementById("modal-deepseek-key");

  let selectedFile = null;
  let currentTaskId = null;
  let currentWs = null;
  let currentSegments = [];

  const STAGE_ORDER = [
    "stage-download",
    "stage-extract",
    "stage-roformer",
    "stage-asr",
    "stage-glossary",
    "stage-translate",
    "stage-tts",
    "stage-timing",
    "stage-timeline",
    "stage-ducking",
    "stage-subtitles",
    "stage-render"
  ];

  // 1. Detect Hardware on Startup
  async function loadHardware() {
    try {
      const res = await fetch("/api/hardware");
      if (res.ok) {
        const data = await res.json();
        if (data.gpu_name) {
          const vram = data.vram_total_mb ? ` (${Math.round(data.vram_total_mb)}MB VRAM)` : "";
          const cuda = data.cuda_version ? ` · CUDA ${data.cuda_version}` : "";
          gpuInfoText.textContent = `${data.gpu_name}${vram}${cuda}`;
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

  // 2. Models Manager Modal
  btnModelsModal.addEventListener("click", async () => {
    modelsModal.classList.remove("hidden");
    modelsTableBody.innerHTML = `<tr><td colspan="5" class="p-4 text-center text-gray-500"><i class="fa-solid fa-spinner fa-spin"></i> Đang tải thông tin checkpoints...</td></tr>`;
    try {
      const res = await fetch("/api/models");
      const data = await res.json();
      modelsTableBody.innerHTML = "";
      data.models.forEach(m => {
        const tr = document.createElement("tr");
        tr.className = "hover:bg-gray-800/40 transition";
        const statusBadge = m.downloaded
          ? `<span class="bg-emerald-950/80 text-emerald-400 border border-emerald-800/80 px-2 py-0.5 rounded text-[10px] font-mono"><i class="fa-solid fa-check"></i> SẴN SÀNG</span>`
          : `<span class="bg-amber-950/80 text-amber-400 border border-amber-800/80 px-2 py-0.5 rounded text-[10px] font-mono">CHƯA TẢI</span>`;

        tr.innerHTML = `
          <td class="p-2.5 font-semibold text-gray-200">${m.engine}</td>
          <td class="p-2.5 font-mono text-[11px] text-pink-300">${m.model_name}</td>
          <td class="p-2.5 font-mono text-gray-400">${m.size_mb} MB</td>
          <td class="p-2.5 text-gray-400 font-mono text-[11px]">${m.device}</td>
          <td class="p-2.5">${statusBadge}</td>
        `;
        modelsTableBody.appendChild(tr);
      });
    } catch (err) {
      modelsTableBody.innerHTML = `<tr><td colspan="5" class="p-4 text-center text-rose-400">Lỗi nạp danh sách model</td></tr>`;
    }
  });
  btnCloseModelsModal.addEventListener("click", () => modelsModal.classList.add("hidden"));

  // 3. Settings Modal
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
    alert("Đã lưu API Keys thành công!");
  });

  // 4. Drag & Drop File Handling
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

  function addLog(msg) {
    const time = new Date().toLocaleTimeString();
    const div = document.createElement("div");
    div.textContent = `[${time}] ${msg}`;
    liveLogBox.appendChild(div);
    liveLogBox.scrollTop = liveLogBox.scrollHeight;
  }

  function resetStages() {
    STAGE_ORDER.forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.className = "stage-item flex items-center justify-between p-1.5 rounded-lg bg-gray-900/40";
        const badge = el.querySelector(".stage-badge");
        if (badge) {
          badge.className = "stage-badge text-gray-500";
          badge.textContent = "Queued";
        }
      }
    });
    overallPercent.textContent = "0%";
    liveLogBox.innerHTML = "";
  }

  function updateStageUI(currentStageId, percent, msg) {
    overallPercent.textContent = `${percent}%`;
    addLog(msg);

    const currentIdx = STAGE_ORDER.indexOf(currentStageId);

    STAGE_ORDER.forEach((id, idx) => {
      const el = document.getElementById(id);
      if (!el) return;
      const badge = el.querySelector(".stage-badge");

      if (currentIdx !== -1) {
        if (idx < currentIdx) {
          // Completed
          el.className = "stage-item flex items-center justify-between p-1.5 rounded-lg bg-emerald-950/20 border border-emerald-900/40";
          badge.className = "stage-badge text-emerald-400 font-medium";
          badge.innerHTML = `<i class="fa-solid fa-circle-check"></i> Xong`;
        } else if (idx === currentIdx) {
          // Running
          el.className = "stage-item flex items-center justify-between p-1.5 rounded-lg bg-pink-950/30 border border-pink-700/60 shadow-sm";
          badge.className = "stage-badge text-pink-400 font-semibold animate-pulse";
          badge.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Đang chạy`;
        } else {
          // Queued
          el.className = "stage-item flex items-center justify-between p-1.5 rounded-lg bg-gray-900/40";
          badge.className = "stage-badge text-gray-500";
          badge.textContent = "Queued";
        }
      }
    });

    if (percent === 100) {
      STAGE_ORDER.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
          el.className = "stage-item flex items-center justify-between p-1.5 rounded-lg bg-emerald-950/20 border border-emerald-900/40";
          const badge = el.querySelector(".stage-badge");
          badge.className = "stage-badge text-emerald-400 font-medium";
          badge.innerHTML = `<i class="fa-solid fa-circle-check"></i> Xong`;
        }
      });
    }
  }

  // 5. Start Pipeline Execution
  btnStart.addEventListener("click", async () => {
    const url = videoUrlInput.value.trim();
    if (!url && !selectedFile) {
      alert("Vui lòng dán link video Douyin/TikTok hoặc chọn một file video!");
      return;
    }

    btnStart.classList.add("hidden");
    btnPause.classList.remove("hidden");
    btnResume.classList.add("hidden");
    btnStop.classList.remove("hidden");

    resultCard.classList.add("hidden");
    resetStages();
    updateStageUI("stage-download", 5, "Khởi động pipeline cao cấp...");

    try {
      let response;
      if (selectedFile) {
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("asr_engine", asrSelect.value);
        formData.append("tts_engine", ttsEngineSelect.value);
        formData.append("voice", voiceSelect.value);
        formData.append("separator_engine", separatorSelect.value);
        if (refAudioFile.files.length > 0) {
          formData.append("ref_audio", refAudioFile.files[0]);
        }
        formData.append("mask_chinese", true);

        response = await fetch("/api/process-upload", {
          method: "POST",
          body: formData
        });
      } else {
        response = await fetch("/api/process-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: url,
            asr_engine: asrSelect.value,
            tts_engine: ttsEngineSelect.value,
            voice: voiceSelect.value,
            separator_engine: separatorSelect.value,
            mask_chinese: true
          })
        });
      }

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Xử lý thất bại");
      }

      const data = await response.json();
      currentTaskId = data.task_id;
      setupWebSocket(currentTaskId);

    } catch (err) {
      alert("Lỗi: " + err.message);
      resetControls();
    }
  });

  // 6. WebSocket Setup & Handlers
  function setupWebSocket(taskId) {
    if (currentWs) {
      currentWs.close();
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    currentWs = new WebSocket(`${protocol}//${window.location.host}/ws/progress/${taskId}`);

    currentWs.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "progress") {
        updateStageUI(data.stage, data.percent, data.message);
      } else if (data.type === "done") {
        updateStageUI("stage-render", 100, "Hoàn tất xuất sắc toàn bộ quy trình!");
        resetControls();
        renderResults(data.result);
        currentWs.close();
      } else if (data.type === "error") {
        alert("Lỗi xử lý: " + data.message);
        addLog("LỖI: " + data.message);
        resetControls();
        currentWs.close();
      }
    };

    currentWs.onerror = (err) => {
      console.error("WebSocket error:", err);
    };
  }

  function resetControls() {
    btnStart.classList.remove("hidden");
    btnPause.classList.add("hidden");
    btnResume.classList.add("hidden");
    btnStop.classList.add("hidden");
  }

  // 7. Pause, Resume, Stop Buttons
  btnPause.addEventListener("click", async () => {
    if (!currentTaskId) return;
    try {
      await fetch(`/api/task/${currentTaskId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "pause" })
      });
      btnPause.classList.add("hidden");
      btnResume.classList.remove("hidden");
      addLog("[Tạm dừng] Đã gửi lệnh tạm dừng tới pipeline.");
    } catch (e) {
      console.error(e);
    }
  });

  btnResume.addEventListener("click", async () => {
    if (!currentTaskId) return;
    try {
      await fetch(`/api/task/${currentTaskId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "resume" })
      });
      btnResume.classList.add("hidden");
      btnPause.classList.remove("hidden");
      addLog("[Tiếp tục] Đã gửi lệnh tiếp tục tới pipeline.");
    } catch (e) {
      console.error(e);
    }
  });

  btnStop.addEventListener("click", async () => {
    if (!currentTaskId) return;
    if (!confirm("Bạn có chắc chắn muốn hủy bỏ tiến trình đang chạy?")) return;
    try {
      await fetch(`/api/task/${currentTaskId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "stop" })
      });
      addLog("[Hủy bỏ] Đã gửi lệnh hủy tiến trình.");
      resetControls();
    } catch (e) {
      console.error(e);
    }
  });

  // 8. Render Results
  function renderResults(result) {
    resultCard.classList.remove("hidden");
    const videoUrl = `/api/outputs/${result.output_filename}`;
    videoPlayer.src = videoUrl;
    videoPlayer.load();

    downloadVideoBtn.href = videoUrl;
    downloadVideoBtn.setAttribute("download", result.output_filename);
    downloadSrtBtn.href = `/api/download-srt/${result.task_id}`;

    currentSegments = result.segments || [];
    renderSegmentsEditor(currentSegments);
  }

  // 9. VideoLingo Multi-Tier Subtitle Editor
  function renderSegmentsEditor(segments) {
    segmentsContainer.innerHTML = "";
    btnReRender.classList.remove("hidden");

    if (!segments || segments.length === 0) {
      segmentsContainer.innerHTML = `<p class="text-xs text-gray-500 py-6 text-center">Không có câu thoại nào</p>`;
      return;
    }

    segments.forEach((seg, index) => {
      const item = document.createElement("div");
      item.className = "segment-item bg-[#0d0f14] border border-gray-800 rounded-xl p-3.5 space-y-2.5 hover:border-gray-700 transition";
      
      const emotionTag = seg.emotion && seg.emotion !== "<|NEUTRAL|>"
        ? `<span class="bg-violet-900/60 text-violet-300 border border-violet-700/60 px-2 py-0.5 rounded text-[10px] font-mono">${seg.emotion}</span>`
        : "";

      item.innerHTML = `
        <div class="flex items-center justify-between text-[11px] text-gray-400">
          <span class="font-mono text-pink-400 font-semibold flex items-center gap-1.5">
            <i class="fa-regular fa-clock"></i> ${seg.start.toFixed(2)}s - ${seg.end.toFixed(2)}s (${(seg.duration || (seg.end - seg.start)).toFixed(2)}s)
          </span>
          <div class="flex items-center gap-2">
            ${emotionTag}
            <span class="bg-gray-800 text-gray-400 px-2 py-0.5 rounded text-[10px] font-mono">#${index + 1}</span>
          </div>
        </div>

        <!-- Chinese Raw Text -->
        <div class="text-xs text-gray-300 bg-gray-900/70 p-2.5 rounded-lg border border-gray-800/80 font-sans">
          <span class="text-[10px] text-gray-500 block mb-0.5 font-semibold">GỐC (TIẾNG TRUNG):</span>
          ${seg.text_zh || seg.text || "—"}
        </div>

        <!-- Literal Translation -->
        <div class="text-[11px] text-gray-400 bg-gray-900/40 p-2 rounded-lg border border-gray-800/40">
          <span class="text-[10px] text-gray-500 block mb-0.5">Dịch sát nghĩa (Literal):</span>
          ${seg.literal_vi || "—"}
        </div>

        <!-- Final Vietnamese Translation (Editable) -->
        <div>
          <label class="text-[10px] text-pink-400 font-semibold block mb-1">
            Bản dịch TikTok & Lồng tiếng (Final Vietnamese):
          </label>
          <input type="text" data-seg-id="${seg.id}" value="${(seg.final_vi || seg.vi_text || "").replace(/"/g, '&quot;')}"
            class="seg-vi-input w-full bg-[#161922] border border-gray-700 focus:border-pink-500 rounded-lg px-2.5 py-1.5 text-xs text-gray-100 focus:outline-none transition">
        </div>
      `;
      segmentsContainer.appendChild(item);
    });
  }

  // 10. Re-render with User Edits
  btnReRender.addEventListener("click", async () => {
    const inputs = document.querySelectorAll(".seg-vi-input");
    const updatedSegments = currentSegments.map(seg => {
      const copy = { ...seg };
      inputs.forEach(inp => {
        if (parseInt(inp.dataset.segId) === seg.id) {
          copy.final_vi = inp.value.trim();
          copy.vi_text = inp.value.trim();
        }
      });
      return copy;
    });

    btnReRender.disabled = true;
    btnReRender.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Đang render lại...`;
    resetStages();
    updateStageUI("stage-tts", 68, "Bắt đầu tổng hợp lại giọng nói và render video...");

    try {
      const res = await fetch("/api/re-render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: currentTaskId,
          segments: updatedSegments,
          voice: voiceSelect.value,
          tts_engine: ttsEngineSelect.value,
          mask_chinese: true
        })
      });

      if (!res.ok) throw new Error("Xuất lại thất bại");
      const data = await res.json();
      currentTaskId = data.task_id;
      setupWebSocket(data.task_id);
    } catch (err) {
      alert("Lỗi: " + err.message);
    } finally {
      btnReRender.disabled = false;
      btnReRender.innerHTML = `<i class="fa-solid fa-rotate"></i> Render lại với bản sửa`;
    }
  });
});
