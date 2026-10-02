document.addEventListener("DOMContentLoaded", () => {
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("video-file");
  const fileNameDisplay = document.getElementById("file-name-display");
  const videoUrlInput = document.getElementById("video-url");
  const btnStart = document.getElementById("btn-start");
  const voiceSelect = document.getElementById("voice-select");
  const llmSelect = document.getElementById("llm-select");
  const maskSubCheckbox = document.getElementById("mask-sub-checkbox");

  // Progress UI
  const progressCard = document.getElementById("progress-card");
  const progressBarFill = document.getElementById("progress-bar-fill");
  const progressPercent = document.getElementById("progress-percent");
  const progressStatusText = document.getElementById("progress-status-text");
  const logConsole = document.getElementById("log-console");

  // Results UI
  const resultCard = document.getElementById("result-card");
  const videoPlayer = document.getElementById("output-video-player");
  const downloadVideoBtn = document.getElementById("download-video-btn");
  const downloadSrtBtn = document.getElementById("download-srt-btn");
  const segmentsContainer = document.getElementById("segments-container");
  const btnReRender = document.getElementById("btn-re-render");

  // Modal UI
  const settingsModal = document.getElementById("settings-modal");
  const btnSettingsModal = document.getElementById("btn-settings-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const btnSaveKeys = document.getElementById("btn-save-keys");
  const modalGeminiKey = document.getElementById("modal-gemini-key");
  const modalDeepseekKey = document.getElementById("modal-deepseek-key");

  let selectedFile = null;
  let currentTaskId = null;
  let currentSegments = [];

  // Drag & drop handlers
  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      selectedFile = e.target.files[0];
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
    }
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("border-pink-500");
  });
  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("border-pink-500");
  });
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("border-pink-500");
    if (e.dataTransfer.files.length > 0) {
      selectedFile = e.dataTransfer.files[0];
      fileNameDisplay.textContent = `Đã chọn: ${selectedFile.name} (${(selectedFile.size / 1024 / 1024).toFixed(1)}MB)`;
      videoUrlInput.value = "";
    }
  });

  // Modal open/close
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

  function addLog(msg) {
    const time = new Date().toLocaleTimeString();
    const div = document.createElement("div");
    div.textContent = `[${time}] ${msg}`;
    logConsole.appendChild(div);
    logConsole.scrollTop = logConsole.scrollHeight;
  }

  function updateProgress(percent, msg) {
    progressCard.classList.remove("hidden");
    progressBarFill.style.width = `${percent}%`;
    progressPercent.textContent = `${percent}%`;
    progressStatusText.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${msg}`;
    addLog(msg);
  }

  // Start Pipeline
  btnStart.addEventListener("click", async () => {
    const url = videoUrlInput.value.trim();
    if (!url && !selectedFile) {
      alert("Vui lòng dán link video Douyin/TikTok hoặc chọn một file video!");
      return;
    }

    btnStart.disabled = true;
    btnStart.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> ĐANG XỬ LÝ...`;
    progressCard.classList.remove("hidden");
    resultCard.classList.add("hidden");
    logConsole.innerHTML = "";
    updateProgress(5, "Đang chuẩn bị tiến trình...");

    try {
      let response;
      if (selectedFile) {
        // Upload File
        updateProgress(8, "Đang tải video lên server...");
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("voice", voiceSelect.value);
        formData.append("llm_provider", llmSelect.value);
        formData.append("mask_chinese", maskSubCheckbox.checked);

        response = await fetch("/api/process-upload", {
          method: "POST",
          body: formData
        });
      } else {
        // Submit URL
        updateProgress(8, "Đang gửi yêu cầu tải video từ link...");
        response = await fetch("/api/process-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: url,
            voice: voiceSelect.value,
            llm_provider: llmSelect.value,
            mask_chinese: maskSubCheckbox.checked
          })
        });
      }

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Xử lý thất bại");
      }

      const data = await response.json();
      currentTaskId = data.task_id;

      // Connect WebSocket for live updates
      setupWebSocket(currentTaskId);

    } catch (err) {
      alert("Lỗi: " + err.message);
      btnStart.disabled = false;
      btnStart.innerHTML = `<i class="fa-solid fa-play"></i> BẮT ĐẦU CHUYỂN NGỮ & XUẤT VIDEO`;
      progressStatusText.innerHTML = `<span class="text-rose-400">Lỗi tiến trình</span>`;
    }
  });

  function setupWebSocket(taskId) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/progress/${taskId}`);

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "progress") {
        updateProgress(data.percent, data.message);
      } else if (data.type === "done") {
        updateProgress(100, "Hoàn tất xuất sắc!");
        btnStart.disabled = false;
        btnStart.innerHTML = `<i class="fa-solid fa-play"></i> BẮT ĐẦU CHUYỂN NGỮ & XUẤT VIDEO`;
        renderResults(data.result);
        ws.close();
      } else if (data.type === "error") {
        alert("Lỗi xử lý: " + data.message);
        btnStart.disabled = false;
        btnStart.innerHTML = `<i class="fa-solid fa-play"></i> BẮT ĐẦU CHUYỂN NGỮ & XUẤT VIDEO`;
        ws.close();
      }
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
    };
  }

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

  function renderSegmentsEditor(segments) {
    segmentsContainer.innerHTML = "";
    btnReRender.classList.remove("hidden");

    if (!segments || segments.length === 0) {
      segmentsContainer.innerHTML = `<p class="text-xs text-gray-500 py-6 text-center">Không có câu thoại nào</p>`;
      return;
    }

    segments.forEach((seg, index) => {
      const item = document.createElement("div");
      item.className = "segment-item bg-[#0d0f14] border border-gray-800 rounded-xl p-3.5 space-y-2";
      item.innerHTML = `
        <div class="flex items-center justify-between text-[11px] text-gray-400">
          <span class="font-mono text-pink-400 font-semibold flex items-center gap-1.5">
            <i class="fa-regular fa-clock"></i> ${seg.start.toFixed(1)}s - ${seg.end.toFixed(1)}s (${seg.duration ? seg.duration.toFixed(1) : (seg.end - seg.start).toFixed(1)}s)
          </span>
          <span class="bg-gray-800 text-gray-400 px-2 py-0.5 rounded text-[10px]">#${index + 1}</span>
        </div>
        <div class="text-xs text-gray-400 italic bg-gray-900/60 p-2 rounded-lg border border-gray-800/60">
          ${seg.text || "—"}
        </div>
        <div>
          <label class="text-[10px] text-gray-500 block mb-1">Dịch tiếng Việt (chỉnh sửa nếu muốn):</label>
          <input type="text" data-seg-id="${seg.id}" value="${(seg.vi_text || "").replace(/"/g, '&quot;')}"
            class="seg-vi-input w-full bg-[#161922] border border-gray-700 focus:border-pink-500 rounded-lg px-2.5 py-1.5 text-xs text-gray-100 focus:outline-none transition">
        </div>
      `;
      segmentsContainer.appendChild(item);
    });
  }

  // Re-render when edits made
  btnReRender.addEventListener("click", async () => {
    const inputs = document.querySelectorAll(".seg-vi-input");
    const updatedSegments = currentSegments.map(seg => {
      const copy = { ...seg };
      inputs.forEach(inp => {
        if (parseInt(inp.dataset.segId) === seg.id) {
          copy.vi_text = inp.value.trim();
        }
      });
      return copy;
    });

    btnReRender.disabled = true;
    btnReRender.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Đang xuất lại...`;
    progressCard.classList.remove("hidden");
    updateProgress(70, "Đang tạo lại lồng tiếng từ bản chỉnh sửa...");

    try {
      const res = await fetch("/api/re-render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: currentTaskId,
          segments: updatedSegments,
          voice: voiceSelect.value,
          mask_chinese: maskSubCheckbox.checked
        })
      });

      if (!res.ok) throw new Error("Xuất lại thất bại");
      const data = await res.json();
      setupWebSocket(data.task_id);
    } catch (err) {
      alert("Lỗi: " + err.message);
    } finally {
      btnReRender.disabled = false;
      btnReRender.innerHTML = `<i class="fa-solid fa-rotate"></i> Xuất lại với bản sửa`;
    }
  });
});
