document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const videoPlayer = document.getElementById("video-player");
  const playerContainer = document.getElementById("player-container");
  const playerPlaceholder = document.getElementById("player-placeholder");
  const subtitleOverlay = document.getElementById("subtitle-overlay");
  const subtitleText = document.getElementById("subtitle-text");
  const screenTextOverlay = document.getElementById("screen-text-overlay");
  const visualTranslation = document.getElementById("visual-translation");
  const chineseSubMask = document.getElementById("chinese-sub-mask");
  const bufferingAlert = document.getElementById("buffering-alert");
  const bufferingText = document.getElementById("buffering-text");
  const playerDownloadProgress = document.getElementById("player-download-progress");
  const playerProgressValue = document.getElementById("player-progress-value");
  const playerProgressBar = document.getElementById("player-progress-bar");
  const playerProgressDetail = document.getElementById("player-progress-detail");
  const playerTaskStatus = document.getElementById("player-task-status");
  const playerProgressTrack = document.getElementById("player-progress-track");
  const playerPlayToggle = document.getElementById("player-play-toggle");
  const playerMuteToggle = document.getElementById("player-mute-toggle");
  const playerVolume = document.getElementById("player-volume");
  const playerFullscreen = document.getElementById("player-fullscreen");

  // Controls
  const videoUrlInput = document.getElementById("video-url");
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("video-file");
  const fileNameDisplay = document.getElementById("file-name-display");
  const bufferSelect = document.getElementById("buffer-select");
  const voiceSelect = document.getElementById("voice-select");
  const voiceSource = document.getElementById("voice-source");
  const voiceList = document.getElementById("voice-list");
  const voiceSearch = document.getElementById("voice-search");
  const voiceFilter = document.getElementById("voice-filter");
  let voiceSourceFilter = "";
  const voiceCount = document.getElementById("voice-count");
  const voiceEmpty = document.getElementById("voice-empty");
  const voiceCatalogStatus = document.getElementById("voice-catalog-status");
  const voicePreviewAudio = document.getElementById("voice-preview-audio");
  const voicePreviewStatus = document.getElementById("voice-preview-status");
  const refAudioFile = document.getElementById("ref-audio-file");
  const btnStart = document.getElementById("btn-start");
  const btnPauseWorker = document.getElementById("btn-pause-worker");
  const btnResumeWorker = document.getElementById("btn-resume-worker");
  const btnStopWorker = document.getElementById("btn-stop-worker");
  const btnRetryWorker = document.getElementById("btn-retry-worker");
  const btnReviewWorker = document.getElementById("btn-review-worker");
  const btnExportHQ = document.getElementById("btn-export-hq");
  const videoUrlStatus = document.getElementById("video-url-status");
  const taskProgress = document.getElementById("task-progress");
  const taskProgressStage = document.getElementById("task-progress-stage");
  const taskProgressValue = document.getElementById("task-progress-value");
  const taskProgressTrack = document.getElementById("task-progress-track");
  const taskProgressBar = document.getElementById("task-progress-bar");
  const taskProgressDetail = document.getElementById("task-progress-detail");
  const taskConnectionStatus = document.getElementById("task-connection-status");
  const taskResult = document.getElementById("task-result");
  const taskResultStatus = document.getElementById("task-result-status");
  const taskResultLink = document.getElementById("task-result-link");
  const btnSaveResult = document.getElementById("btn-save-result");
  const resultPreviewModal = document.getElementById("result-preview-modal");
  const resultPreviewVideo = document.getElementById("result-preview-video");
  const resultPreviewStatus = document.getElementById("result-preview-status");
  const btnCloseResultPreview = document.getElementById("btn-close-result-preview");

  // Toggles & Volumes
  const toggleMaskChinese = document.getElementById("toggle-mask-chinese");
  const toggleSubtitles = document.getElementById("toggle-subtitles");
  const toggleScreenText = document.getElementById("toggle-screen-text");
  const captionStylePanel = document.getElementById("caption-style-panel");
  const captionStyleControls = document.getElementById("caption-style-controls");
  const captionTextColor = document.getElementById("caption-text-color");
  const captionBackgroundColor = document.getElementById("caption-background-color");
  const captionAutoBackground = document.getElementById("caption-auto-background");
  const captionPosition = document.getElementById("caption-position");
  const captionBlurOriginal = document.getElementById("caption-blur-original");
  const captionStyleReset = document.getElementById("caption-style-reset");
  const captionStyleStatus = document.getElementById("caption-style-status");
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
  const playerBgmStatus = document.getElementById("player-bgm-status");
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
  const transcriptFollow = document.getElementById("transcript-follow");
  const transcriptStatus = document.getElementById("transcript-status");

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
  const douyinCookiesPanel = document.getElementById("douyin-cookies-panel");
  const douyinCookiesFile = document.getElementById("douyin-cookies-file");
  const douyinCookiesState = document.getElementById("douyin-cookies-state");
  const douyinCookiesStatus = document.getElementById("douyin-cookies-status");
  const btnImportDouyinCookies = document.getElementById("btn-import-douyin-cookies");
  const btnRefreshDouyinCookies = document.getElementById("btn-refresh-douyin-cookies");
  const btnDeleteDouyinCookies = document.getElementById("btn-delete-douyin-cookies");
  let douyinCookiesConfigured = false;
  let douyinCookiesBusy = false;
  let douyinCookiesVersion = 0;

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
  let screenTexts = [];
  let screenTextSignature = "";
  let visualSession = false;
  const defaultCaptionStyle = {background_color: null, text_color: "#000000", position: "auto", blur_original: false};
  let captionStyle = {...defaultCaptionStyle};
  let captionStyleTaskId = null, captionStyleTimer = null, captionStyleRequest = null;
  let captionStyleLastResponse = null;
  let captionStyleRevision = 0, captionStyleEditVersion = 0;
  let captionStyleDirty = false, captionOutputOutdated = false, hasSubtitleRegions = false;
  let containedVideoWidth = 0;
  let containedVideoHeight = 0;
  const transcriptRows = new Map();
  const transcriptDrafts = new Set();
  let activeTranscriptId = null;
  let transcriptTaskId = null;
  let pendingTranscriptSaves = 0;
  let activeAudio = null;
  let activePlayingSegId = null;
  const dubAudioCache = new Map();
  let dubPlaybackWait = null;
  let internalDubPauseEvents = 0;
  let playbackFrame = null;
  let playbackFrameGeneration = 0;
  let bgmAudio = null;
  let sourceAudition = null;
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
  let previewSource = null;
  let previewFallbackTried = false;
  let previewPending = false;
  let previewResumeTime = null;
  let playWhenPreviewReady = false;
  let autoPlayTaskId = null;
  let internalPreviewPauseEvents = 0;
  let translationReady = false;
  let audioPermissionNeeded = false;
  let pendingStart = null;
  let pendingTaskAction = null;
  let currentProgress = null;
  let automaticExportActive = false;
  let progressRevision = 0;
  let taskPollInFlight = false;
  let taskPollWarning = false;
  let streamDisconnected = false;
  let streamEventRevision = 0;
  let reconnectTimer = null;
  let recoveryRequest = null;
  let masterVolume = 1;
  let masterMuted = false;
  let voiceCatalog = [];
  let voicePreviewGeneration = 0;
  let voicePreviewId = null;
  let voicePreviewBusy = false;
  let previewedVoiceId = null;
  let voiceCatalogLoaded = false;
  let savedSessionVoice = null;
  const voiceRows = new Map();
  const voiceGroups = [];
  const voicePreferenceKey = "studio.voice-id";
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
    if (!sourceAudition && bgmAudio) bgmAudio.play().catch(reportPlayFailure);
    videoPlayer.play().catch(reportPlayFailure);
  });

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch]));
  }

  function selectedCatalogVoice() {
    return voiceCatalog.find(voice => voice.id === voiceSelect.value && voice.available);
  }

  function selectedVoicePayload() {
    const voice = selectedCatalogVoice();
    return voice && voiceCatalogLoaded ? { voice_id: voice.id } : {
      voice: voiceSelect.value || "vi-VN-HoaiMyNeural",
      tts_engine: voice?.engine || document.body.dataset.ttsEngine
    };
  }

  function applySavedSessionVoice() {
    if (!savedSessionVoice || savedSessionVoice.taskId !== currentTaskId) return false;
    const { voice, engine, id } = savedSessionVoice;
    const match = voiceCatalog.find(item => item.engine === engine && (item.id === id || item.id === voice));
    voiceSelect.value = match?.id || id;
    describeSelectedVoice();
    return true;
  }

  function restoreSessionVoice(snapshot, taskId) {
    savedSessionVoice = null;
    const voice = typeof snapshot.voice === "string" ? snapshot.voice.trim() : "";
    const engine = typeof snapshot.tts_engine === "string" ? snapshot.tts_engine.trim() : "";
    const prefix = { "edge-tts": "edge", "vieneu-tts": "vieneu", "piper-tts": "piper" }[engine];
    if (!voice || !prefix) return;
    // Restoring history must neither change the user's default nor let a late
    // catalog response replace the voice that generated this project's audio.
    savedSessionVoice = { taskId, voice, engine, id: `${prefix}:${voice}` };
    applySavedSessionVoice();
  }

  function updateVoiceControls() {
    const sessionBusy = btnStart.classList.contains("hidden");
    voiceSelect.disabled = sessionBusy;
    for (const [id, { voice, row, choose, preview }] of voiceRows) {
      const selected = id === voiceSelect.value;
      const previewing = id === previewedVoiceId;
      row.dataset.selected = String(selected);
      row.dataset.previewing = String(previewing);
      choose.disabled = sessionBusy || !voice.available;
      choose.textContent = selected ? "Đã chọn" : "Chọn";
      choose.setAttribute("aria-pressed", String(selected));
      preview.disabled = sessionBusy || voicePreviewBusy || !voice.available || !voiceCatalogLoaded;
      preview.setAttribute("aria-busy", String(previewing && voicePreviewBusy));
      const playing = previewing && !voicePreviewAudio.paused && !voicePreviewAudio.ended;
      const wave = row.querySelector?.('.voice-wave');
      if (wave) wave.dataset.playing = String(playing);
      preview.innerHTML = `<i aria-hidden="true" class="fa-solid ${previewing && voicePreviewBusy ? "fa-spinner fa-spin" : playing ? "fa-stop" : "fa-play"}"></i>`;
      preview.title = previewing && voicePreviewBusy ? "Đang tạo mẫu giọng…" : playing ? "Dừng mẫu" : "Nghe thử";
      preview.setAttribute("aria-label", `${playing ? "Dừng mẫu" : "Nghe thử"} giọng ${voice.name}`);
    }
  }

  function deleteVoicePreview(id) {
    if (id) fetch(`/api/voices/preview/${encodeURIComponent(id)}`, { method: "DELETE", keepalive: true }).catch(() => {});
  }

  function stopVoicePreview() {
    voicePreviewGeneration++;
    // Soft cancellation cannot stop inference; wait for its response before
    // enabling another request, then delete the superseded sample.
    previewedVoiceId = null;
    voicePreviewAudio.pause();
    voicePreviewAudio.removeAttribute("src");
    voicePreviewAudio.load();
    voicePreviewAudio.classList.add("hidden");
    deleteVoicePreview(voicePreviewId);
    voicePreviewId = null;
    voicePreviewStatus.textContent = "";
    voicePreviewStatus.dataset.error = "false";
    updateVoiceControls();
  }

  function describeSelectedVoice() {
    const voice = voiceCatalog.find(item => item.id === voiceSelect.value);
    if (voice) {
      voiceSource.textContent = `Đang chọn: ${voice.name} · ${voice.source} · ${voice.offline ? "Chạy trên máy" : "Cần Internet"}${voice.available ? "" : " · Chưa sẵn sàng"}`;
    } else if (savedSessionVoice?.taskId === currentTaskId && savedSessionVoice?.id === voiceSelect.value) {
      voiceSource.textContent = `Giọng dự án: ${savedSessionVoice.voice} · ${savedSessionVoice.engine}${voiceCatalogLoaded ? " · Chưa có trong danh sách giọng hiện tại" : ""}`;
    }
    updateVoiceControls();
  }

  function searchableVoice(text) {
    return String(text).normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
  }

  function filterVoices() {
    const terms = searchableVoice(voiceSearch.value).trim().split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const { voice, row } of voiceRows.values()) {
      const text = searchableVoice(`${voice.name} ${voice.description || ""} ${voice.source}`);
      row.hidden = !terms.every(term => text.includes(term)) || !!(voiceSourceFilter && voice.source !== voiceSourceFilter);
      if (!row.hidden) visible++;
    }
    for (const { element, ids } of voiceGroups) element.hidden = ids.every(id => voiceRows.get(id).row.hidden);
    voiceCount.textContent = terms.length || voiceSourceFilter ? `${visible} / ${voiceCatalog.length} giọng` : `${voiceCatalog.length} giọng`;
    voiceEmpty.classList.toggle("hidden", visible > 0);
  }

  function renderVoiceList() {
    voiceRows.clear();
    voiceGroups.length = 0;
    voiceList.replaceChildren();
    const groups = new Map();
    for (const voice of voiceCatalog) {
      if (!groups.has(voice.source)) {
        const group = document.createElement("section");
        group.className = "voice-group";
        group.dataset.source = voice.source;
        const heading = document.createElement("h4");
        heading.className = "voice-group-title";
        heading.textContent = voice.source;
        group.appendChild(heading);
        const record = { element: group, ids: [] };
        groups.set(voice.source, record);
        voiceGroups.push(record);
        voiceList.appendChild(group);
      }
      const row = document.createElement("div");
      row.className = "voice-row";
      row.dataset.voiceId = voice.id;
      const details = document.createElement("div");
      details.className = "voice-info";
      const name = document.createElement("p");
      name.className = "voice-name";
      name.textContent = voice.name;
      const description = document.createElement("p");
      description.className = "voice-description";
      for (const text of [...(voice.description || "Tiếng Việt").split(" · "), voice.offline ? "Chạy trên máy" : "Cần Internet", ...(!voice.available ? ["Chưa sẵn sàng"] : [])]) {
        const tag = document.createElement("span");
        tag.className = "voice-tag";
        tag.textContent = text;
        description.appendChild(tag);
      }
      details.appendChild(name);
      details.appendChild(description);
      const preview = document.createElement("button");
      preview.type = "button";
      preview.className = "voice-preview-button";
      preview.addEventListener("click", () => previewVoice(voice));
      const choose = document.createElement("button");
      choose.type = "button";
      choose.className = "voice-choose-button";
      choose.setAttribute("aria-label", `Chọn giọng ${voice.name}`);
      choose.addEventListener("click", () => {
        if (!voice.available || btnStart.classList.contains("hidden")) return;
        voiceSelect.value = voice.id;
        selectVoice();
      });
      const wave = document.createElement("span");
      wave.className = "voice-wave";
      wave.setAttribute("aria-hidden", "true");
      for (const [i, height] of [5, 9, 14, 20, 27, 18, 12, 22, 29, 20, 14, 9, 5].entries()) {
        const bar = document.createElement("i");
        bar.style.setProperty("--bar", `${height}px`);
        bar.style.setProperty("--delay", `${i * -0.08}s`);
        wave.appendChild(bar);
      }
      row.append(preview, details, wave, choose);
      const group = groups.get(voice.source);
      group.ids.push(voice.id);
      group.element.appendChild(row);
      voiceRows.set(voice.id, { voice, row, choose, preview });
    }
    filterVoices();
    describeSelectedVoice();
  }

  async function loadVoiceCatalog() {
    const initialVoice = voiceSelect.value;
    try {
      const response = await fetch("/api/voices");
      const data = await response.json();
      const voices = Array.isArray(data.voices) ? data.voices.filter(voice =>
        typeof voice.id === "string" && typeof voice.name === "string" && typeof voice.source === "string"
      ) : [];
      if (!response.ok || !voices.some(voice => voice.available)) throw new Error("catalog unavailable");
      voiceCatalog = voices;
      voiceCatalogLoaded = true;
      let savedId;
      try { savedId = localStorage.getItem(voicePreferenceKey); } catch (_) {}
      const available = id => voices.some(voice => voice.id === id && voice.available);
      const legacy = voices.find(voice => voice.available && voice.id.endsWith(`:${initialVoice}`) &&
        voice.engine === document.body.dataset.ttsEngine);
      const selectedId = available(savedId) ? savedId : available(data.default_voice_id)
        ? data.default_voice_id : legacy?.id || voices.find(voice => voice.available).id;
      if (!applySavedSessionVoice()) voiceSelect.value = selectedId;
      renderVoiceList();
    } catch (_) {
      voiceCatalogLoaded = false;
      const edge = ["edge", "edge-tts"].includes(document.body.dataset.ttsEngine);
      voiceCatalog = (edge ? [
        ["vi-VN-HoaiMyNeural", "Hoài My", "Nữ"], ["vi-VN-NamMinhNeural", "Nam Minh", "Nam"]
      ] : [["Trúc Ly", "Trúc Ly", "Nữ"], ["Thiện Minh", "Thiện Minh", "Nam"]]).map(([id, name, description]) => ({
        id, name, description, engine: edge ? "edge-tts" : "vieneu-tts",
        source: edge ? "Microsoft Edge" : "VieNeu", available: true, offline: !edge
      }));
      if (!applySavedSessionVoice()) {
        voiceSelect.value = voiceCatalog.some(voice => voice.id === initialVoice) ? initialVoice : voiceCatalog[0].id;
      }
      voiceCatalogStatus.textContent = "Chưa tải được danh sách mở rộng. Vẫn dùng được các giọng hiện tại; mở lại Studio để nghe thử.";
      renderVoiceList();
    }
  }

  function selectVoice() {
    savedSessionVoice = null;
    stopVoicePreview();
    describeSelectedVoice();
    const voice = selectedCatalogVoice();
    if (voice && voiceCatalogLoaded) { try { localStorage.setItem(voicePreferenceKey, voice.id); } catch (_) {} }
  }
  voiceSelect.addEventListener("change", selectVoice);
  voiceSearch.addEventListener("input", filterVoices);
  voiceFilter?.addEventListener("click", () => {
    const sources = ["", ...new Set(voiceCatalog.map(voice => voice.source))];
    voiceSourceFilter = sources[(sources.indexOf(voiceSourceFilter) + 1) % sources.length];
    const icon = document.createElement("i");
    icon.className = "fa-solid fa-filter";
    icon.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = voiceSourceFilter || "Tất cả";
    voiceFilter.replaceChildren(icon, label);
    voiceFilter.title = "Đổi bộ lọc nguồn giọng";
    filterVoices();
  });

  async function previewVoice(voice) {
    if (!voice.available || !voiceCatalogLoaded || voicePreviewBusy || btnStart.classList.contains("hidden")) return;
    if (previewedVoiceId === voice.id && !voicePreviewAudio.paused && !voicePreviewAudio.ended) {
      stopVoicePreview();
      return;
    }
    stopVoicePreview();
    videoPlayer.pause();
    const generation = voicePreviewGeneration;
    voicePreviewBusy = true;
    previewedVoiceId = voice.id;
    voicePreviewStatus.textContent = `Đang chuẩn bị mẫu giọng ${voice.name}…`;
    updateVoiceControls();
    try {
      const response = await fetch("/api/voices/preview", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ voice_id: voice.id })
      });
      const data = await response.json();
      if (generation !== voicePreviewGeneration) { deleteVoicePreview(data.preview_id); return; }
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không tạo được mẫu giọng. Hãy thử lại.");
      voicePreviewId = data.preview_id;
      const origin = `${window.location.protocol}//${window.location.host}`;
      const audioUrl = new URL(data.audio_url, origin);
      if (!data.preview_id || !data.audio_url || audioUrl.origin !== origin) throw new Error("Đường dẫn mẫu giọng không hợp lệ.");
      voicePreviewAudio.src = audioUrl.href;
      voicePreviewAudio.setAttribute("aria-label", `Mẫu giọng ${voice.name}`);
      voicePreviewAudio.classList.remove("hidden");
      voicePreviewStatus.textContent = `Đang nghe mẫu: ${voice.name}.${data.cached ? " Dùng mẫu đã lưu." : " Mẫu đã được lưu cho lần sau."} Bấm Chọn ở dòng giọng bạn muốn dùng.`;
      try { await voicePreviewAudio.play(); }
      catch (_) {
        if (generation === voicePreviewGeneration) voicePreviewStatus.textContent = "Mẫu giọng đã sẵn sàng. Bấm nút phát trên thanh âm thanh để nghe.";
      }
    } catch (error) {
      if (generation === voicePreviewGeneration) {
        deleteVoicePreview(voicePreviewId);
        voicePreviewId = null;
        voicePreviewStatus.textContent = error instanceof TypeError ? "Không kết nối được dịch vụ giọng đọc. Hãy thử lại." : error.message;
        voicePreviewStatus.dataset.error = "true";
      }
    } finally {
      voicePreviewBusy = false;
      updateVoiceControls();
    }
  }
  for (const event of ["play", "pause", "ended"]) voicePreviewAudio.addEventListener(event, updateVoiceControls);
  voicePreviewAudio.addEventListener("error", () => {
    if (!voicePreviewId) return;
    stopVoicePreview();
    voicePreviewStatus.textContent = "Không phát được mẫu giọng. Bấm Nghe thử để tạo lại.";
    voicePreviewStatus.dataset.error = "true";
  });
  window.addEventListener?.("pagehide", stopVoicePreview);
  loadVoiceCatalog();

  function measuredProgress(value) {
    return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
  }

  function formatProgressPercent(value) {
    const pct = measuredProgress(value);
    if (pct === null) return "Chưa có %";
    return `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`;
  }

  function visualProgressDetail(progress) {
    const end = Number(progress.chunk_end);
    const start = Number(progress.chunk_start);
    const done = Number(progress.processed_seconds ?? progress.processed_duration ?? progress.chunk_start);
    const total = Number(progress.total_duration ?? progress.total_seconds);
    const step = progress.visual_step || progress.visual_stage;
    if (!step) return "Tiến độ thời lượng video đã đối chiếu lời nói và chữ trên hình / tổng thời lượng. Các đoạn xong sẽ xuất hiện trong Transcript.";
    const duration = Number.isFinite(total) && total > 0 && Number.isFinite(done)
      ? `Đã xử lý ${formatTime(Math.max(0, done))} / ${formatTime(total)}. ` : "";
    const chunk = Number.isFinite(start) && Number.isFinite(end) && end > start
      ? `Đoạn ${formatTime(start)}–${formatTime(end)}. ` : "";
    if (step === "ocr") {
      const frames = progress.frames_done, count = progress.frames_total;
      const measured = Number.isInteger(frames) && Number.isInteger(count) && count > 0 && frames >= 0 && frames <= count;
      return `${duration}${chunk}${measured ? `Đã đọc ${frames}/${count} khung hình (${Math.round(frames * 100 / count)}%).` : "Đang đọc chữ trong khung hình."}`;
    }
    const actions = {decode:"Đang trích khung hình để đọc chữ.", source:"Đang chờ Muse đối chiếu lời gốc.",
      translate:"Đang chờ Muse dịch đoạn này.", verify:"Đang chờ Muse kiểm tra bản dịch."};
    const batch = Number.isInteger(progress.batch_index) && Number.isInteger(progress.batch_total)
      ? ` Nhóm ${progress.batch_index}/${progress.batch_total}.` : "";
    return `${duration}${chunk}${actions[step] || "Đang xử lý đoạn này."}${batch}`;
  }

  function formatBytes(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let amount = value;
    let unit = 0;
    while (amount >= 1000 && unit < units.length - 1) {
      amount /= 1000;
      unit += 1;
    }
    return `${Number(amount.toFixed(unit === 0 ? 0 : 2))} ${units[unit]}`;
  }

  function formatEta(seconds) {
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
    const total = Math.round(seconds);
    if (total < 60) return `${total} giây`;
    const minutes = Math.round(total / 60);
    if (minutes < 60) return `${minutes} phút`;
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return remainder ? `${hours} giờ ${remainder} phút` : `${hours} giờ`;
  }

  function updateExportAvailability() {
    syncCaptionStyleTask();
    const items = Object.values(segments);
    btnExportHQ.disabled = !currentTaskId || !items.length ||
      captionStyleDirty || !!captionStyleRequest ||
      reviewInProgress() || pendingTaskAction?.kind === "review" || automaticExportActive ||
      ["failed", "incomplete"].includes(currentProgress?.review_summary?.status) || (currentProgress?.phase === "export" && ["RUNNING", "CANCELLING"].includes(currentProgress?.status)) ||
      transcriptDrafts.size > 0 || pendingTranscriptSaves > 0 ||
      items.some(segment => (segment.needs_review && !(currentProgress?.review_summary?.status === "completed" && segment.verification?.status === "unresolved")) || !["READY", "PLAYED"].includes(segment.status)) ||
      (["FAILED", "STOPPED", "CANCELLED", "CANCELLING"].includes(currentProgress?.status)
        && currentProgress?.phase !== "export_error");
    if (btnReviewWorker) btnReviewWorker.disabled = !!pendingTaskAction || reviewInProgress() || automaticExportActive ||
      !!exportingTaskId || captionStyleDirty || !!captionStyleRequest || transcriptDrafts.size > 0 || pendingTranscriptSaves > 0;
    updateCaptionStyleAvailability();
  }

  function writeCaptionStyleControls(value) {
    captionTextColor.value = value.text_color || "#000000";
    captionAutoBackground.checked = value.background_color === null;
    captionBackgroundColor.value = value.background_color || "#FFE500";
    captionPosition.value = value.position || "auto";
    if (toggleScreenText) toggleScreenText.checked = captionPosition.value === "auto";
    captionBlurOriginal.checked = value.blur_original === true;
  }

  function syncCaptionStyleTask() {
    if (captionStyleTaskId === currentTaskId) return;
    clearTimeout(captionStyleTimer); captionStyleTimer = null;
    captionStyleTaskId = currentTaskId;
    captionStyle = {...defaultCaptionStyle};
    captionStyleRevision = 0; captionStyleEditVersion++;
    captionStyleDirty = captionOutputOutdated = hasSubtitleRegions = false;
    // An older POST may finish on the server; its response cannot alter this task.
    captionStyleRequest = null;
    captionStyleLastResponse = null;
    writeCaptionStyleControls(captionStyle);
    captionStyleStatus.textContent = "Xem trước thay đổi tại đây, rồi bấm Xuất video MP4 để lưu bản mới.";
    captionStyleStatus.dataset.error = "false";
  }

  function captionSourceReady() {
    const items = Object.values(segments);
    return !!currentTaskId && (currentProgress?.status === "COMPLETED" ||
      (currentProgress?.phase === "export_error" && items.length > 0 &&
        items.every(segment => ["READY", "PLAYED"].includes(segment.status))));
  }

  function captionStyleLocked() {
    return !captionSourceReady() ||
      reviewInProgress() || !!pendingTaskAction || automaticExportActive || !!exportingTaskId ||
      transcriptDrafts.size > 0 || pendingTranscriptSaves > 0 ||
      [...transcriptRows.values()].some(item => !item.editor.hidden);
  }

  function updateCaptionStyleAvailability() {
    const complete = !!currentTaskId && (captionSourceReady() || !!lastExportedFileUrl || captionOutputOutdated);
    captionStylePanel?.classList.toggle("hidden", !complete);
    const locked = captionStyleLocked();
    captionStyleControls.disabled = locked;
    captionBackgroundColor.disabled = locked || captionAutoBackground.checked;
    captionBlurOriginal.disabled = locked || (!hasSubtitleRegions && !captionBlurOriginal.checked);
    captionBlurOriginal.title = hasSubtitleRegions ? "Làm mờ đúng vùng sub gốc đã nhận diện" : "Video chưa có vùng phụ đề gốc đủ tin cậy để làm mờ";
    if (toggleScreenText) toggleScreenText.disabled = captionStyleDirty || !!captionStyleRequest || !!exportingTaskId || automaticExportActive;
  }

  function invalidateCaptionOutput() {
    captionOutputOutdated = true;
    resetTaskResult();
    exportResultBox.classList.add("hidden");
    taskResult.classList.remove("hidden");
    taskResultStatus.textContent = "Phụ đề đã thay đổi. Bấm Xuất video MP4 để tạo bản mới.";
    if (currentProgress) { currentProgress.output_video_url = ""; currentProgress.output_filename = ""; }
  }

  function applyCaptionStyleSnapshot(data, {force = false} = {}) {
    syncCaptionStyleTask();
    const revision = Number(data.caption_style_revision) || 0;
    if (revision < captionStyleRevision || (!force && (captionStyleDirty || captionStyleRequest))) return;
    if (data.caption_style) {
      captionStyle = {...defaultCaptionStyle, ...data.caption_style};
      captionStyleRevision = revision;
      writeCaptionStyleControls(captionStyle);
    }
    if (typeof data.has_subtitle_regions === "boolean") hasSubtitleRegions = data.has_subtitle_regions;
    if (data.output_outdated) invalidateCaptionOutput();
    else if (data.output_outdated === false) captionOutputOutdated = false;
    updateCaptionStyleAvailability();
  }

  function readCaptionStyleControls() {
    return {text_color: captionTextColor.value,
      background_color: captionAutoBackground.checked ? null : captionBackgroundColor.value,
      position: captionPosition.value, blur_original: captionBlurOriginal.checked};
  }

  function scheduleCaptionStyle() {
    if (captionStyleLocked()) return;
    captionStyleEditVersion++; captionStyleDirty = true;
    clearTimeout(captionStyleTimer);
    captionStyleStatus.dataset.error = "false";
    captionStyleStatus.textContent = "Đang cập nhật phụ đề…";
    updateExportAvailability();
    captionStyleTimer = setTimeout(saveCaptionStyle, 250);
  }

  async function saveCaptionStyle() {
    captionStyleTimer = null;
    if (!captionStyleDirty || captionStyleRequest || captionStyleLocked()) return;
    const request = {taskId: currentTaskId, version: captionStyleEditVersion, style: readCaptionStyleControls()};
    captionStyleRequest = request;
    updateExportAvailability(); updateTranscriptReviewAvailability();
    try {
      const response = await fetch(`/api/streaming/${encodeURIComponent(request.taskId)}/caption-style`, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(request.style),
      });
      const data = await response.json();
      if (request.taskId !== currentTaskId || captionStyleRequest !== request) return;
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không cập nhật được phụ đề. Hãy thử lại.");
      if (!data.caption_style || !Array.isArray(data.segments)) throw new Error("Máy chủ chưa xác nhận phụ đề mới. Hãy thử lại.");
      captionStyleLastResponse = data;
      if (data.output_outdated) invalidateCaptionOutput();
      if (request.version !== captionStyleEditVersion) return;
      captionStyleDirty = false;
      applyCaptionStyleSnapshot(data, {force: true});
      data.segments.forEach(applySegmentUpdate);
      positionVideoOverlays();
      captionStyleStatus.textContent = "Đã cập nhật xem trước. Bấm Xuất video MP4 để lưu bản mới.";
      captionStyleStatus.dataset.error = "false";
    } catch (error) {
      if (request.taskId !== currentTaskId || captionStyleRequest !== request || request.version !== captionStyleEditVersion) return;
      captionStyleDirty = false;
      if (captionStyleLastResponse) {
        applyCaptionStyleSnapshot(captionStyleLastResponse, {force: true});
        captionStyleLastResponse.segments.forEach(applySegmentUpdate);
        positionVideoOverlays();
      }
      writeCaptionStyleControls(captionStyle);
      captionStyleStatus.textContent = error.message || "Không cập nhật được phụ đề. Hãy thử lại.";
      captionStyleStatus.dataset.error = "true";
    } finally {
      if (captionStyleRequest === request) {
        captionStyleRequest = null;
        updateExportAvailability(); updateTranscriptReviewAvailability();
        // Serialize writes: aborting fetch alone cannot cancel a server mutation.
        if (captionStyleDirty && request.taskId === currentTaskId) saveCaptionStyle();
      }
    }
  }

  for (const control of [captionTextColor, captionBackgroundColor, captionAutoBackground, captionPosition, captionBlurOriginal]) {
    control.addEventListener("input", scheduleCaptionStyle);
    control.addEventListener("change", scheduleCaptionStyle);
  }
  captionStyleReset.addEventListener("click", () => {
    if (captionStyleLocked()) return;
    writeCaptionStyleControls(defaultCaptionStyle); scheduleCaptionStyle();
  });
  writeCaptionStyleControls(captionStyle);

  function reviewInProgress() {
    return (currentProgress?.review_summary?.status === "running" || (currentProgress?.phase === "review" && currentProgress?.status === "RUNNING")) &&
      !["FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status);
  }

  function reviewSummaryText(summary = currentProgress?.review_summary) {
    if (!summary) return "";
    const count = key => Math.max(0, Number(summary[key]) || 0);
    if (summary.status === "running") return "AI đang đối chiếu và sửa bản dịch" +
      (summary.checked !== undefined ? ` · Đã kiểm tra ${count("checked")} câu.` : ". Các câu đã có giọng vẫn phát được trong lúc kiểm tra.");
    if (["failed", "incomplete"].includes(summary.status)) return "AI chưa kiểm tra đủ nguồn. Bản nháp và giọng đã tạo vẫn được giữ; bấm AI kiểm tra lại để thử tiếp.";
    if (summary.status !== "completed") return "";
    return `AI đã kiểm tra ${count("checked")} câu · Giữ nguyên ${count("verified")} · Đã sửa ${count("corrected")}.` +
      (count("manual") ? ` Có ${count("manual")} câu do bạn sửa sau kiểm tra.` : "") +
      (count("unresolved") ? ` Còn ${count("unresolved")} câu nguồn chưa rõ; giữ bản dịch có căn cứ và ghi rõ lý do tại từng câu. Không cần xác nhận thủ công để xuất.` : count("manual") ? "Có thể xuất bản đã sửa hoặc bấm AI kiểm tra lại." : " Bản dịch đã qua kiểm tra tự động.");
  }

  function updateTranscriptReviewAvailability() {
    const busy = reviewInProgress() || pendingTaskAction?.kind === "review" || automaticExportActive || !!exportingTaskId || captionStyleDirty || !!captionStyleRequest;
    for (const [id, item] of transcriptRows) {
      const segment = segments[id];
      item.translation.disabled = busy || !["READY", "PLAYED", "NEEDS_REVIEW"].includes(segment?.status);
      item.silence.disabled = busy || item.saving;
      item.save.disabled = item.input.disabled = busy || item.saving;
      // A lifecycle event can finish review without another segment event.
      // Refresh its hint too so an idle/failed task never claims AI is working.
      if (segment) updateSegmentDrawerItem(segment);
    }
  }

  function resetTaskResult() {
    lastExportedFileUrl = "";
    automaticExportActive = false;
    taskResult?.classList.add("hidden");
    taskResultLink?.classList.add("hidden");
    if (btnSaveResult) btnSaveResult.disabled = true;
    taskResultLink?.removeAttribute("href");
  }

  let resultPreviewTrigger = null;
  let resultPreviewActive = false;
  let resultPreviewGeneration = 0;
  let resultPreviewState = null;
  const resultPreviewFailure = "Chưa phát được video kết quả. Bạn có thể lưu MP4 rồi mở bằng trình phát trên máy.";

  function currentResultPreview(state) {
    return resultPreviewActive && resultPreviewState === state && state.generation === resultPreviewGeneration;
  }

  function releaseResultPreviewRequest(state) {
    if (!state) return;
    clearTimeout(state.pollTimer);
    clearTimeout(state.requestTimer);
    state.controller?.abort();
    state.controller = null;
  }

  function closeResultPreview(restoreFocus = true) {
    resultPreviewActive = false;
    resultPreviewGeneration++;
    releaseResultPreviewRequest(resultPreviewState);
    resultPreviewState = null;
    resultPreviewVideo?.pause();
    resultPreviewVideo?.removeAttribute("src");
    resultPreviewVideo?.load();
    resultPreviewModal?.classList.add("hidden");
    if (restoreFocus) resultPreviewTrigger?.focus();
    resultPreviewTrigger = null;
  }

  function failResultPreview(state, message = resultPreviewFailure) {
    if (!currentResultPreview(state)) return;
    state.phase = "failed";
    releaseResultPreviewRequest(state);
    resultPreviewVideo.pause();
    resultPreviewStatus.textContent = message;
  }

  function playResultPreview(state) {
    const mediaUrl = state.mediaUrl;
    resultPreviewVideo.play().catch(error => {
      if (!currentResultPreview(state) || state.mediaUrl !== mediaUrl || state.phase === "failed" || state.phase === "converting") return;
      if (error?.name === "AbortError") return;
      if (error?.name === "NotSupportedError" || [3, 4].includes(resultPreviewVideo.error?.code)) {
        startResultCompatibility(state);
      } else if (error?.name === "NotAllowedError" && !resultPreviewVideo.error) {
        resultPreviewStatus.textContent = "Bấm Phát để xem video kết quả.";
      } else {
        failResultPreview(state);
      }
    });
  }

  async function requestResultCompatibility(state, previewId = null) {
    if (!currentResultPreview(state) || state.phase !== "converting" || state.controller) return;
    const controller = new AbortController();
    state.controller = controller;
    state.requestTimer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(previewId ? `/api/preview/${previewId}` : "/api/preview", {
        signal: controller.signal,
        ...(previewId ? {} : { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ output_filename: state.filename }) }),
      });
      if (!response.ok) throw Error("preview unavailable");
      const data = await response.json();
      if (!currentResultPreview(state)) return;
      if (controller.signal.aborted) throw Error("preview timed out");
      if (!/^[a-zA-Z0-9_-]+$/.test(data.preview_id || "")) throw Error("invalid preview");
      if (data.status === "READY") {
        const origin = `${window.location.protocol}//${window.location.host}`;
        const mediaUrl = new URL(data.video_url, origin);
        if (mediaUrl.origin !== origin || mediaUrl.pathname !== `/api/preview/${data.preview_id}/media` || mediaUrl.search || mediaUrl.hash) throw Error("invalid preview media");
        state.phase = "compatible";
        state.mediaUrl = mediaUrl.href;
        resultPreviewStatus.textContent = "Bản xem trước tương thích. Tệp MP4 giữ nguyên chất lượng xuất.";
        resultPreviewVideo.src = state.mediaUrl;
        resultPreviewVideo.load();
        playResultPreview(state);
      } else if (data.status === "PROCESSING" && Date.now() - state.startedAt < 600000) {
        state.pollTimer = setTimeout(() => requestResultCompatibility(state, data.preview_id), 1000);
      } else {
        failResultPreview(state);
      }
    } catch (_) {
      failResultPreview(state);
    } finally {
      clearTimeout(state.requestTimer);
      if (state.controller === controller) state.controller = null;
    }
  }

  function startResultCompatibility(state) {
    if (!currentResultPreview(state) || state.phase === "converting" || state.phase === "failed") return;
    if (state.phase === "compatible") { failResultPreview(state); return; }
    state.phase = "converting";
    state.startedAt = Date.now();
    resultPreviewVideo.pause();
    resultPreviewVideo.removeAttribute("src");
    resultPreviewVideo.load();
    resultPreviewStatus.textContent = "Đang tạo bản xem trước tương thích với trình phát...";
    requestResultCompatibility(state);
  }

  function openResultPreview(rawUrl, trigger = taskResultLink) {
    const origin = `${window.location.protocol}//${window.location.host}`;
    let url;
    try { url = new URL(rawUrl, origin); } catch (_) { return; }
    if (url.origin !== origin || !/^\/api\/outputs\/[^/]+\.mp4$/i.test(url.pathname) || url.search || url.hash || !resultPreviewModal) return;
    let filename;
    try { filename = decodeURIComponent(url.pathname.split("/").pop()); } catch (_) { return; }
    if (/[\\/:\x00-\x1f]/.test(filename)) return;
    closeResultPreview(false);
    stopVoicePreview();
    stopSourceAudition();
    cancelDubPlaybackWait();
    resultPreviewActive = true;
    isBufferingUnderrun = false;
    playWhenPreviewReady = false;
    videoPlayer.pause();
    resultPreviewTrigger = trigger;
    resultPreviewStatus.textContent = "";
    resultPreviewModal.classList.remove("hidden");
    const state = resultPreviewState = { generation: resultPreviewGeneration, filename, outputUrl: url.pathname, phase: "direct", mediaUrl: url.href };
    resultPreviewVideo.volume = playbackVolume();
    if (resultPreviewVideo.canPlayType?.('video/mp4; codecs="avc1.42E01E, mp4a.40.2"') === "") {
      startResultCompatibility(state);
    } else {
      resultPreviewVideo.src = state.mediaUrl;
      resultPreviewVideo.load();
      playResultPreview(state);
    }
    btnCloseResultPreview.focus();
  }

  taskResultLink?.addEventListener("click", event => {
    event.preventDefault();
    openResultPreview(taskResultLink.href);
  });
  document.getElementById("tasks-table-body")?.addEventListener("click", event => {
    const link = event.target.closest?.("a[data-result-preview]");
    if (!link) return;
    event.preventDefault();
    openResultPreview(link.href, link);
  });
  btnCloseResultPreview?.addEventListener("click", () => closeResultPreview());
  resultPreviewModal?.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); closeResultPreview(); }
    if (event.key === "Tab") {
      const first = document.getElementById("btn-save-preview-result") || btnCloseResultPreview, last = resultPreviewVideo;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  resultPreviewVideo?.addEventListener("error", () => {
    const state = resultPreviewState;
    if (!state || !currentResultPreview(state) || !resultPreviewVideo.error || state.phase === "converting") return;
    if ([3, 4].includes(resultPreviewVideo.error.code)) startResultCompatibility(state);
    else failResultPreview(state);
  });
  window.addEventListener("pagehide", () => closeResultPreview(false));

  function showPipelineWarnings(data) {
    if (!Array.isArray(data.warnings) && !data.metadata_warning) return;
    const warning = document.getElementById("pipeline-warning");
    if (!warning) return;
    const reviewCompleted = (data.review_summary || data.progress?.review_summary || currentProgress?.review_summary)?.status === "completed";
    const resolvedReviewWarnings = new Set([
      "AI kiểm tra lại chưa hoàn tất. Các câu đã lưu và âm thanh sẵn có được giữ; có thể thử lại.",
      "AI kiểm tra lại chưa hoàn tất; giữ bản nháp và cho phép thử lại.",
      "AI kiểm tra lại chưa hoàn tất; mở dự án và bấm AI kiểm tra lại để tiếp tục.",
    ]);
    const messages = (Array.isArray(data.warnings) ? data.warnings : []).filter(item => typeof item === "string" && item && !(reviewCompleted && resolvedReviewWarnings.has(item)));
    if (typeof data.metadata_warning === "string" && data.metadata_warning && !messages.includes(data.metadata_warning)) messages.push(data.metadata_warning);
    warning.textContent = messages.join(" · ");
    warning.classList.toggle("hidden", !warning.textContent);
  }

  function showTaskResult(data) {
    showPipelineWarnings(data);
    if (captionStyleDirty || captionStyleRequest || captionOutputOutdated) return;
    if (["failed", "incomplete", "running"].includes((data.review_summary || currentProgress?.review_summary)?.status)) return;
    if (!data.output_video_url || !taskResult) return;
    const origin = `${window.location.protocol}//${window.location.host}`;
    let url;
    try { url = new URL(data.output_video_url, origin); } catch (_) { return; }
    if (url.origin !== origin || !/^\/api\/outputs\/[^/]+$/.test(url.pathname)) return;
    lastExportedFileUrl = url.pathname;
    taskResultStatus.textContent = `Video kết quả đã sẵn sàng: ${data.output_filename || url.pathname.split("/").pop()}`;
    taskResultLink.href = url.href;
    taskResult.classList.remove("hidden");
    taskResultLink.classList.remove("hidden");
    btnSaveResult.classList.remove("hidden");
    btnSaveResult.disabled = false;
    btnSaveResult.title = "Lưu toàn bộ MP4 đã dịch, giữ nguyên chất lượng xuất";
    updateCaptionStyleAvailability();
  }

  function resetWorkerBadges() {
    for (const [badge, label] of [[workerAsrBadge, "ASR"], [workerTransBadge, "Dịch"], [workerTtsBadge, "TTS"]]) {
      badge.className = "px-2 py-0.5 rounded bg-gray-800 text-gray-300";
      badge.textContent = `${label}: Idle`;
    }
  }

  function showExportFailure(message, status = "FAILED") {
    automaticExportActive = false;
    if (taskResult) {
      taskResult.classList.remove("hidden");
      taskResultLink.classList.add("hidden");
      btnSaveResult.disabled = true;
      taskResultStatus.textContent = message || (status === "CANCELLED" ? "Đã hủy xuất video. Bản dịch vẫn được giữ." : "Chưa xuất được video. Bấm Xuất video MP4 để thử lại.");
    }
  }

  function renderExportProgress(progress, stage) {
    const pct = measuredProgress(progress);
    if (exportProgressBar) {
      exportProgressBar.style.width = pct === null ? "100%" : `${pct}%`;
      exportProgressBar.style.opacity = pct === null ? "0.3" : "1";
    }
    if (exportProgressPct) exportProgressPct.textContent = pct === null ? "" : formatProgressPercent(pct);
    if (exportStatusText && stage) exportStatusText.textContent = stage;
  }

  function pauseStoppedSession(progress = currentProgress, pausePlayback = true) {
    // Stopping execution must not detach the saved project: Retry needs its ID
    // and users still need the transcript and already generated audio. In
    // particular, history restore receives STOPPED again over the live socket.
    if (pausePlayback) {
      cancelDubPlaybackWait();
      stopPlaybackFrames();
      stopSourceAudition();
      videoPlayer.pause();
      activeAudio?.pause();
      bgmAudio?.pause();
      activeAudio = null;
      activePlayingSegId = null;
      releaseDubAudio();
    }
    autoPlayTaskId = null;
    isBufferingUnderrun = false;
    playWhenPreviewReady = false;
    const socket = currentWs;
    currentWs = null;
    if (socket) socket.close();
    streamDisconnected = false;
    bufferingText.textContent = "";
    bufferingAlert.classList.add("hidden");
    bufferingAlert.dataset.state = "stopped";
    playerDownloadProgress?.classList.add("hidden");
    if (playerTaskStatus) {
      playerTaskStatus.textContent = progressMessage(progress, "Đã dừng tác vụ");
      playerTaskStatus.dataset.state = "stopped";
      playerTaskStatus.classList.remove("hidden");
    }
  }

  function progressMessage(progress, fallback = "") {
    return progress?.stage || progress?.message || fallback;
  }

  function updatePlayerDownloadProgress(progress, pct, terminal = false) {
    const downloading = progress?.phase === "download" && !terminal;
    playerDownloadProgress?.classList.toggle("hidden", !downloading);
    if (!downloading) return;
    if (playerProgressValue) playerProgressValue.textContent = formatProgressPercent(pct);
    if (playerProgressBar) {
      playerProgressBar.style.width = pct === null ? "100%" : `${pct}%`;
      playerProgressTrack?.setAttribute("data-indeterminate", String(pct === null));
      if (pct === null) playerProgressTrack?.removeAttribute("aria-valuenow");
      else playerProgressTrack?.setAttribute("aria-valuenow", String(pct));
    }
    if (playerProgressDetail) {
      const details = [];
      const downloaded = formatBytes(progress.downloaded_bytes);
      const total = formatBytes(progress.total_bytes);
      if (downloaded && total) details.push(`${downloaded} / ${total}`);
      else if (downloaded) details.push(`đã tải ${downloaded}`);
      const speed = formatBytes(progress.speed);
      if (speed) details.push(`${speed}/s`);
      const eta = formatEta(progress.eta);
      if (eta) details.push(`ước tính còn ${eta}`);
      playerProgressDetail.textContent = details.join(" · ") || "Đang chờ số liệu từ máy chủ…";
    }
  }

  function updatePlayerTaskStatus(progress, terminal = false) {
    if (!playerTaskStatus) return;
    if (!terminal) {
      playerTaskStatus.classList.add("hidden");
      playerTaskStatus.textContent = "";
      playerTaskStatus.dataset.state = "";
      return;
    }
    const status = String(progress?.status || "").toUpperCase();
    playerTaskStatus.textContent = progressMessage(progress, status === "FAILED" ? "Tác vụ thất bại" : "Tác vụ đã kết thúc");
    playerTaskStatus.dataset.state = status.toLowerCase();
    playerTaskStatus.classList.remove("hidden");
  }

  function showTaskProgress(progress) {
    progressRevision++;
    taskPollWarning = false;
    const previousStatus = currentProgress?.status;
    currentProgress = { ...currentProgress, ...progress };
    const incompleteReview = ["failed", "incomplete"].includes(currentProgress.review_summary?.status);
    if (incompleteReview && (currentProgress.status === "COMPLETED" || (currentProgress.status === "FAILED" && currentProgress.phase === "review"))) {
      currentProgress = {...currentProgress, status: "FAILED", phase: "review",
        stage: reviewSummaryText(), progress_pct: null};
      resetTaskResult();
    }
    const status = currentProgress.status || "RUNNING";
    if (currentProgress.phase === "export") automaticExportActive = ["RUNNING", "CANCELLING"].includes(status);
    else if (["FAILED", "STOPPED", "CANCELLED"].includes(status)) automaticExportActive = false;
    if (progress.output_video_url) { automaticExportActive = false; showTaskResult(progress); }
    else if (automaticExportActive) taskResult?.classList.add("hidden");
    if (pendingTaskAction?.kind === "retry" && status !== "FAILED") pendingTaskAction.progressSeen = true;
    if (pendingTaskAction?.kind === "review" && (reviewInProgress() || currentProgress.phase === "review")) pendingTaskAction.progressSeen = true;
    const terminal = ["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(status);
    setWorkerSourceBusy(!terminal || !!pendingTaskAction || reviewInProgress() || automaticExportActive);
    const pct = measuredProgress(currentProgress.progress_pct);
    updatePlayerDownloadProgress(currentProgress, pct, terminal);
    updatePlayerTaskStatus(currentProgress, terminal);
    if (!streamDisconnected) taskConnectionStatus.classList.add("hidden");
    taskProgress.classList.remove("hidden");
    taskProgress.dataset.status = status;
    taskProgressStage.textContent = `${status === "PAUSED" ? "Đã tạm dừng · " : ""}${currentProgress.stage || (reviewInProgress() ? "AI đang kiểm tra lại bản dịch…" : "Đang xử lý video…")}`;
    bufferingAlert.dataset.state = status === "FAILED" ? "error" : status === "PAUSED" ? "paused" : "loading";
    taskProgressValue.textContent = terminal && pct === null ? "" : formatProgressPercent(pct);
    taskProgressTrack.classList.toggle("hidden", terminal && pct === null);
    taskProgressTrack.dataset.indeterminate = String(pct === null);
    taskProgressTrack.setAttribute("aria-valuetext", `${taskProgressStage.textContent}${pct === null ? ": chưa có số liệu phần trăm" : `: ${formatProgressPercent(pct)}`}`);
    if (pct === null) taskProgressTrack.removeAttribute("aria-valuenow");
    else taskProgressTrack.setAttribute("aria-valuenow", String(pct));
    taskProgressBar.style.width = pct === null ? "100%" : `${pct}%`;
    taskProgressDetail.textContent = terminal
      ? (currentProgress.phase === "export_error" ? "Video kết quả chưa được tạo. Bản dịch và giọng đọc vẫn được giữ; bạn có thể xuất lại." : incompleteReview ? reviewSummaryText() : status === "FAILED" ? "Mở Diagnostics để xem lỗi. Bấm Tiếp tục tác vụ để dùng lại phần đã lưu." : status === "COMPLETED" ? (reviewSummaryText() || (currentProgress.review_count ? "Bản dịch còn câu chưa chắc chắn. Bấm AI kiểm tra lại để tự đối chiếu và sửa." : "Các câu dịch đã xử lý xong. Có thể xuất video.")) : "Tác vụ đã dừng.")
      : reviewInProgress() || currentProgress.phase === "review" ? (reviewSummaryText() || "AI đang đối chiếu từng câu với lời gốc và chữ trên hình, tự sửa lỗi trước khi tạo giọng.")
      : currentProgress.phase === "export" ? "Đang tạo video kết quả từ bản dịch đã được AI kiểm tra. Tiến độ xuất được cập nhật riêng."
      : currentProgress.phase === "download" ? (pct === null ? "Đang tải video; máy chủ chưa cung cấp tổng dung lượng." : "Tiến độ tải video nguồn. Bước xử lý câu thoại sẽ có tiến độ riêng.")
      : currentProgress.phase === "visual" ? visualProgressDetail(currentProgress)
      : pct !== null ? "Tiến độ xử lý câu thoại: số câu hoàn tất / tổng số câu."
      : "Bước này chưa có số liệu phần trăm. Trạng thái sẽ cập nhật khi có kết quả.";
    btnPauseWorker.classList.toggle("hidden", !currentProgress.can_pause || terminal);
    btnResumeWorker.classList.toggle("hidden", !currentProgress.can_resume || terminal);
    btnRetryWorker?.classList.toggle("hidden", !currentProgress.can_retry);
    btnReviewWorker?.classList.toggle("hidden", !currentProgress.can_review || reviewInProgress());
    btnStopWorker.classList.toggle("hidden", terminal || currentProgress.can_stop === false);
    btnStopWorker.disabled = status === "CANCELLING";
    if (!terminal && !translationReady && !previewPending) {
      bufferingText.textContent = taskProgressStage.textContent;
      if (["download", "resolve"].includes(currentProgress.phase)) bufferingAlert.classList.remove("hidden");
    }
    updateExportAvailability();
    updateTranscriptReviewAvailability();
    const reviewText = reviewSummaryText();
    if (reviewText) transcriptStatus.textContent = reviewText;
    if (terminal) {
      resetWorkerBadges();
      if (["STOPPED", "CANCELLED"].includes(status)) pauseStoppedSession(currentProgress, previousStatus !== status);
      else if (status === "FAILED" && currentProgress.phase === "review" && Object.values(segments).some(s => ["READY", "PLAYED"].includes(s.status))) {
        // A review failure is independent of draft playback. Repeated status
        // polls must not pause a draft the user explicitly chose to listen to.
        isBufferingUnderrun = false;
        bufferingAlert.classList.add("hidden");
      } else if (status === "FAILED" && Object.values(segments).some(s => ["READY", "PLAYED"].includes(s.status))) {
        // A later provider timeout must not cover or pause an existing playable
        // prefix. The error/retry remains visible in the task card.
        isBufferingUnderrun = false;
        bufferingAlert.classList.add("hidden");
      } else if (status === "FAILED") {
        cancelDubPlaybackWait();
        videoPlayer.pause();
        isBufferingUnderrun = false;
        bufferingText.textContent = taskProgressStage.textContent;
        bufferingAlert.classList.remove("hidden");
      } else if (!previewPending && !dubPlaybackWait) {
        isBufferingUnderrun = false;
        bufferingAlert.classList.add("hidden");
      }
      resetWorkerControls();
      taskConnectionStatus.classList.add("hidden");
    }
  }

  function describeVideoUrl() {
    const input = videoUrlInput.value.trim();
    // A copied Markdown label may itself contain a URL; use its actual target.
    // Keep query strings intact and only remove formatting around whole links.
    const shareText = input
      .replace(/\[[^\]\r\n]*\]\(\s*(https?:\/\/[^\s<>()]+)\s*\)/gi, (_, target) => ` ${target} `)
      .replace(/(^|[\s(\[<{])(\*\*|__|`)(https?:\/\/[^\s<>]+?)\2/gi, (_, prefix, marker, target) => `${prefix}${target} `)
      .trim();
    const match = shareText.match(/https?:\/\/[^\s<>"'，。！？、；（）【】]+/i);
    const bareDomain = /^(?:www\.)?(?:v\.)?(?:douyin\.com|tiktok\.com|youtu\.be|youtube\.com|bilibili\.com)\//i.test(shareText);
    const candidate = (match?.[0] || (bareDomain ? `https://${shareText.split(/\s/)[0]}` : ""))
      .replace(/[.,;!?)\\\]}»”’]+$/, "").replace(/\\_/g, "_");
    let parsed;
    try { if (candidate) parsed = new URL(candidate); } catch (_) {}
    const valid = !!parsed?.hostname && !parsed.username && !parsed.password && ["http:", "https:"].includes(parsed.protocol);
    videoUrlStatus.classList.toggle("hidden", !input);
    videoUrlInput.setAttribute("aria-invalid", String(!!input && !valid));
    videoUrlStatus.textContent = !input ? "" : valid
      ? `${/(^|\.)(?:douyin|iesdouyin)\.com$/i.test(parsed.hostname) ? "Đã nhận link Douyin" : `Đã nhận link từ ${parsed.hostname}`}: ${parsed.origin}${parsed.pathname}`
      : "Chưa nhận được link hợp lệ. Dán đường dẫn bắt đầu bằng https:// hoặc nội dung chia sẻ có link.";
    btnStart.disabled = !!input && !valid;
    if (valid) parsed.hash = "";
    return valid ? parsed.href : null;
  }

  function stopPreviewAudio() {
    autoPlayTaskId = null;
    playWhenPreviewReady = false;
    cancelDubPlaybackWait();
    stopPlaybackFrames();
    stopSourceAudition();
    isBufferingUnderrun = false;
    videoPlayer.pause();
    if (activeAudio) activeAudio.pause();
    if (bgmAudio) bgmAudio.pause();
    activeAudio = null;
    activePlayingSegId = null;
    releaseDubAudio();
    bgmAudio = null;
    if (playerBgmStatus) playerBgmStatus.textContent = "Chưa có";
    playerSuppressionBadge.textContent = "--";
    telSuppression.textContent = "--";
    videoPlayer.muted = false;
    videoPlayer.volume = playbackVolume();
  }

  function setPreviewSource(source, descriptor = null) {
    // A reconnect sends the source again. Keep the current playhead and any
    // compatible preview already prepared for this exact task and source.
    if (descriptor?.task_id && descriptor.task_id === previewDescriptor?.task_id && source === previewSource) return;
    const canReuse = previewFallbackTried && !previewPending && videoPlayer.readyState >= 2 &&
      videoPlayer.currentSrc?.includes("/api/preview/") && descriptor?.task_id &&
      ((previewDescriptor?.file_path && previewDescriptor.file_path === window.currentLocalFilePath) ||
       (previewDescriptor?.file && previewDescriptor.file === selectedFile));
    const freshStartIntent = descriptor?.task_id === currentTaskId ? autoPlayTaskId : null;
    stopPreviewAudio();
    autoPlayTaskId = freshStartIntent;
    previewGeneration++;
    previewDescriptor = descriptor || (typeof source === "string" ? { file_path: window.currentLocalFilePath } : { file: source });
    previewSource = source;
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
    cancelDubPlaybackWait();
    previewPending = false;
    autoPlayTaskId = null;
    playWhenPreviewReady = false;
    videoPlayer.pause();
    bufferingText.textContent = message;
    bufferingAlert.dataset.state = "error";
    bufferingAlert.classList.remove("hidden");
  }

  async function loadCompatiblePreview() {
    if (previewFallbackTried) {
      showMediaError("Không thể phát bản xem trước. Hãy kiểm tra FFmpeg trong Diagnostics rồi chọn lại video.");
      return;
    }
    previewFallbackTried = true;
    previewPending = true;
    bufferingAlert.dataset.state = "loading";
    const generation = previewGeneration;
    const resumeTime = videoPlayer.currentTime || 0;
    // Readiness means media exists; only explicit Start/Play or interrupted
    // playback grants permission to resume after codec conversion.
    playWhenPreviewReady = !videoPlayer.paused || playWhenPreviewReady;
    if (!videoPlayer.paused) {
      internalPreviewPauseEvents++;
      videoPlayer.pause();
    }
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
  // Match core.subtitle_cues: display timing is estimated within each sentence.
  function makeSubtitleCues(text, start, end, maxChars = 60, lineChars = 32) {
    text = String(text || "").replace(/\r\n?/g, "\n").trim();
    start = Number(start); end = Number(end);
    if (!text || !Number.isFinite(start) || !Number.isFinite(end) || end <= Math.max(0, start)) return [];
    start = Math.max(0, start);
    if (text.includes("\n")) {
      const lines = text.split("\n"), pages = [];
      for (let i = 0; i < lines.length; i += 2) pages.push(lines.slice(i, i + 2).join("\n"));
      const weight = pages.reduce((sum, page) => sum + Math.max(1, page.length), 0);
      let elapsed = 0;
      return pages.map((page, i) => {
        const cueStart = start + (end - start) * elapsed / weight;
        elapsed += Math.max(1, page.length);
        return { start: cueStart, end: i === pages.length - 1 ? end : start + (end - start) * elapsed / weight, text: page };
      });
    }
    const words = text.split(/\s+/), pages = [];
    while (words.length) {
      let count = 0, size = 0;
      for (const word of words) {
        const proposed = size + (count ? 1 : 0) + word.length;
        if (count && proposed > maxChars) break;
        size = proposed; count++;
      }
      if (count < words.length) {
        let prefix = 0, boundary = 0;
        words.slice(0, count).forEach((word, i) => {
          prefix += word.length + (i ? 1 : 0);
          if (prefix >= maxChars * .5 && /[.!?;:,。！？；，]["'”’)]?$/.test(word)) boundary = i + 1;
        });
        if (boundary) count = boundary;
      }
      pages.push(words.splice(0, count).join(" "));
    }
    // Match Python tail balancing: avoid flashing a final word by itself.
    if (pages.length > 1 && (pages.at(-1).length < Math.min(20, maxChars / 2) || pages.at(-1).split(" ").length === 1)) {
      const tailWords = (pages.at(-2) + " " + pages.at(-1)).split(" ");
      const joined = tailWords.join(" ");
      if (joined.length <= maxChars) pages.splice(-2, 2, joined);
      else {
        let best = null, difference = Infinity;
        for (let i = 1; i < tailWords.length; i++) {
          const left = tailWords.slice(0, i).join(" "), right = tailWords.slice(i).join(" ");
          const gap = Math.abs(left.length - right.length);
          if (Math.max(left.length, right.length) <= maxChars && gap < difference) {
            best = [left, right]; difference = gap;
          }
        }
        if (best) pages.splice(-2, 2, ...best);
      }
    }
    const weight = pages.reduce((sum, page) => sum + page.length, 0);
    let elapsed = 0;
    return pages.map((page, i) => {
      const cueStart = start + (end - start) * elapsed / weight;
      elapsed += page.length;
      const cueEnd = i === pages.length - 1 ? end : start + (end - start) * elapsed / weight;
      const parts = page.split(" ");
      if (page.length > lineChars && parts.length > 1) {
        let best = 1, difference = Infinity;
        for (let j = 1; j < parts.length; j++) {
          const gap = Math.abs(parts.slice(0, j).join(" ").length - parts.slice(j).join(" ").length);
          if (gap < difference) { best = j; difference = gap; }
        }
        page = parts.slice(0, best).join(" ") + "\n" + parts.slice(best).join(" ");
      }
      return { start: cueStart, end: cueEnd, text: page };
    });
  }

  function resolveDubTiming(segment) {
    if (!segment) return null;
    const valid = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
    const { start, end, dub_start: dubStart, dub_end: dubEnd } = segment;
    if (!valid(start) || !valid(end) || end <= start) return null;
    if (dubStart == null && dubEnd == null) return { start, end };
    if (!valid(dubStart) || !valid(dubEnd) || dubEnd <= dubStart ||
        Math.abs(dubStart - start) > .35 + 1e-9 || Math.abs(dubEnd - end) > .35 + 1e-9) return null;
    return { start: dubStart, end: dubEnd };
  }

  function dubContainsTime(segment, time) {
    const bounds = resolveDubTiming(segment);
    return bounds && time >= bounds.start && time < bounds.end;
  }

  function sameDubTiming(left, right) {
    const a = resolveDubTiming(left), b = resolveDubTiming(right);
    return a && b && a.start === b.start && a.end === b.end;
  }

  function subtitleAtTime(segment, time) {
    const bounds = resolveDubTiming(segment);
    if (!bounds) return "";
    const text = segmentTranslation(segment);
    const timingKey = `${bounds.start}:${bounds.end}`;
    if (!segment._displayCues || segment._displayText !== text || segment._displayTiming !== timingKey) {
      segment._displayText = text;
      segment._displayTiming = timingKey;
      segment._displayCues = Array.isArray(segment.subtitle_cues)
        ? segment.subtitle_cues
        : makeSubtitleCues(text, bounds.start, bounds.end);
    }
    if (!dubContainsTime(segment, time)) return "";
    return segment._displayCues.find(cue => time >= cue.start && time < cue.end)?.text || "";
  }

  function fitTitleText(text, boxWidth, boxHeight, baseFont) {
    text = String(text || "").trim().split(/\s+/).join(" ");
    if (!text) return "";
    const words = text.split(" "), candidates = [text];
    if (words.length > 1) {
      let best = 1, difference = Infinity;
      for (let i = 1; i < words.length; i++) {
        const gap = Math.abs(words.slice(0, i).join(" ").length - words.slice(i).join(" ").length);
        if (gap < difference) { best = i; difference = gap; }
      }
      candidates.push(words.slice(0, best).join(" ") + "\n" + words.slice(best).join(" "));
    }
    const fitSize = candidate => {
      const lines = candidate.split("\n"), longest = Math.max(...lines.map(line => line.length));
      return Math.min(baseFont, boxWidth * .92 / Math.max(1, longest * .62), boxHeight * .9 / (lines.length * 1.25));
    };
    return candidates.length > 1 && fitSize(candidates[1]) > fitSize(candidates[0]) ? candidates[1] : candidates[0];
  }

  function normalizeScreenTexts(items) {
    return (Array.isArray(items) ? items : []).flatMap(item => {
      const maskOnly = item?.mask_only === true;
      if (!item || item.needs_review || (!maskOnly && !String(item.text_vi || "").trim()) ||
          !Array.isArray(item.bbox) || item.bbox.length !== 4) return [];
      let start = Number(item.start), end = Number(item.end);
      const [x, y, w, h] = item.bbox.map(Number);
      if (![start, end, x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return [];
      if (maskOnly && (h > .35 || w * h > .30 || x < 0 || y < 0 || x + w > 1 || y + h > 1)) return [];
      const left = Math.max(0, x), top = Math.max(0, y);
      const right = Math.min(1, x + w), bottom = Math.min(1, y + h);
      start = Math.max(0, start);
      if (totalVideoDuration > 0) end = Math.min(end, totalVideoDuration);
      if (right <= left || bottom <= top || end <= start) return [];
      return [{ ...item, start, end, text_vi: maskOnly ? "" : String(item.text_vi).trim(), mask_only: maskOnly,
        kind: item.kind === "title" ? "title" : "subtitle", bbox: [left, top, right - left, bottom - top] }];
    });
  }

  function setScreenTexts(items) {
    screenTexts = normalizeScreenTexts(items);
    screenTextSignature = "";
  }

  function subtitleFontSize(width, height) {
    // Match the ASS font in source pixels, then scale with the contained image.
    const sourceWidth = videoPlayer.videoWidth || width;
    const sourceHeight = videoPlayer.videoHeight || height;
    return Math.max(8, Math.round(Math.min(sourceWidth * .035, sourceHeight * .028))) * width / sourceWidth;
  }

  function resetScreenTexts() {
    screenTexts = []; screenTextSignature = ""; visualSession = false;
    screenTextOverlay?.replaceChildren();
    chineseSubMask.style.display = "none";
  }

  function renderScreenTexts(time) {
    chineseSubMask.style.display = "none";
    if (!screenTextOverlay) return false;
    const masks = !sourceAudition && captionStyle.blur_original ? Object.values(segments).flatMap(segment => {
      const plan = toggleScreenText.checked ? segment.caption_layout : segment.caption_bottom_layout;
      return (plan?.source_masks || []).filter(mask => time >= mask.start && time < mask.end &&
        Array.isArray(mask.bbox) && mask.bbox.length === 4 && mask.bbox.every(Number.isFinite) &&
        mask.bbox[0] >= 0 && mask.bbox[1] >= 0 && mask.bbox[2] > 0 && mask.bbox[3] > 0 &&
        mask.bbox[0] + mask.bbox[2] <= 1.001 && mask.bbox[1] + mask.bbox[3] <= 1.001);
    }) : [];
    const unique = [...new Map(masks.map(mask => [JSON.stringify(mask.bbox), mask])).values()];
    const signature = JSON.stringify(unique.map(mask => mask.bbox));
    if (screenTextSignature !== signature) {
      screenTextSignature = signature;
      screenTextOverlay.replaceChildren(...unique.map(mask => {
        const item = document.createElement("div"), [x,y,w,h] = mask.bbox;
        item.className = "source-subtitle-blur";
        Object.assign(item.style, {left: `${x*100}%`, top: `${y*100}%`, width: `${w*100}%`, height: `${h*100}%`});
        return item;
      }));
    }
    return unique.length > 0;
  }

  function renderSpeechCaption(segment, time) {
    if (dubPlaybackWait) return;
    const plan = toggleScreenText.checked ? segment?.caption_layout : segment?.caption_bottom_layout;
    const cue = plan?.cues?.find(item => time >= item.start && time < item.end);
    const ready = segment && ["READY", "PLAYED"].includes(segment.status);
    const text = ready && toggleSubtitles.checked && !sourceAudition
      ? (plan ? cue?.text || "" : subtitleAtTime(segment, time)) : "";
    subtitleText.textContent = text;
    subtitleOverlay.classList.toggle("opacity-0", !text);
    if (!text) return;
    const frame = playerContainer.getBoundingClientRect();
    const width = containedVideoWidth || frame.width, height = containedVideoHeight || frame.height;
    const side = (frame.width - width) / 2, top = (frame.height - height) / 2;
    if (cue) {
      const [x,y,w,h] = cue.bbox;
      Object.assign(subtitleOverlay.style, { left: `${side+x*width}px`, top: `${top+y*height}px`,
        width: `${w*width}px`, height: `${h*height}px`, right: "auto", bottom: "auto" });
      Object.assign(subtitleText.style, { width: "100%", height: "100%", padding: "0",
        display: "flex", alignItems: "center", justifyContent: "center", boxSizing: "border-box",
        fontSize: `${cue.font_size * width / cue.video_size[0]}px`,
        background: /^#[0-9a-f]{6}$/i.test(cue.background_color || "") ? cue.background_color : cue.background === "yellow" ? "#FFE500" : "#FFFFFF",
        color: /^#[0-9a-f]{6}$/i.test(cue.color || "") ? cue.color : "#000000",
        borderRadius: `${cue.border_radius * width / cue.video_size[0]}px` });
    } else {
      Object.assign(subtitleOverlay.style, {left: `${side+width*.04}px`, right: `${side+width*.04}px`,
        top: "auto", bottom: `${top+height*.10}px`, width: "auto", height: "auto" });
      Object.assign(subtitleText.style, { width: "auto", height: "auto", padding: ".2em .55em",
        display: "inline-block", background: "#FFFFFF", color: "#000000", fontSize: `${subtitleFontSize(width,height)}px` });
    }
  }

  function positionVideoOverlays() {
    const box = playerContainer.getBoundingClientRect();
    const ratio = videoPlayer.videoWidth / videoPlayer.videoHeight;
    if (!Number.isFinite(ratio) || ratio <= 0 || !box.width || !box.height) return;
    const width = Math.min(box.width, box.height * ratio);
    const height = width / ratio;
    const side = (box.width - width) / 2;
    const bottom = (box.height - height) / 2;
    containedVideoWidth = width;
    containedVideoHeight = height;
    const fontSize = subtitleFontSize(width, height);
    playerContainer.style.setProperty("--subtitle-font-size", `${fontSize}px`);
    // CSS strokes straddle the glyph edge; paint-order keeps the yellow fill
    // whole while leaving the same outer outline as the ASS export.
    playerContainer.style.setProperty("--subtitle-stroke-width", `${fontSize * .20}px`);
    playerContainer.style.setProperty("--subtitle-shadow-offset", `${fontSize * .055}px`);
    if (screenTextOverlay) {
      screenTextOverlay.style.left = `${side}px`;
      screenTextOverlay.style.top = `${bottom}px`;
      screenTextOverlay.style.width = `${width}px`;
      screenTextOverlay.style.height = `${height}px`;
      screenTextSignature = "";
      renderScreenTexts(videoPlayer.currentTime);
    }
    chineseSubMask.style.display = "none";
    const current = Object.values(segments).find(s => dubContainsTime(s, videoPlayer.currentTime));
    renderSpeechCaption(current, videoPlayer.currentTime);
  }
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(positionVideoOverlays).observe(playerContainer);
  videoPlayer.addEventListener("loadedmetadata", () => {
    if (videoPlayer.videoWidth > 0 && videoPlayer.videoHeight > 0) {
      playerContainer.style.aspectRatio = `${videoPlayer.videoWidth} / ${videoPlayer.videoHeight}`;
      playerContainer.style.setProperty("--source-ratio", String(videoPlayer.videoWidth / videoPlayer.videoHeight));
    }
    positionVideoOverlays();
    if (previewResumeTime !== null) videoPlayer.currentTime = Math.min(previewResumeTime, videoPlayer.duration || 0);
  });
  videoPlayer.addEventListener("emptied", () => {
    cancelDubPlaybackWait();
    stopSourceAudition();
    playerContainer.style.aspectRatio = "9 / 16";
    playerContainer.style.setProperty("--source-ratio", "0.5625");
  });
  videoPlayer.addEventListener("canplay", () => {
    if (previewResumeTime === null) return;
    previewResumeTime = null;
    previewPending = false;
    if (!currentTaskId || translationReady) bufferingAlert.classList.add("hidden");
    else bufferingText.textContent = "Đang chờ các câu dịch đầu tiên...";
    const shouldPlay = playWhenPreviewReady;
    playWhenPreviewReady = false;
    if (shouldPlay && !resultPreviewActive) videoPlayer.play().catch(reportPlayFailure);
  });

  function selectPreviewSource(source) {
    stopVoicePreview();
    resetTaskResult();
    taskProgress.classList.add("hidden");
    currentProgress = null;
    progressRevision++;
    taskPollWarning = false;
    playerDownloadProgress?.classList.add("hidden");
    playerTaskStatus?.classList.add("hidden");
    taskConnectionStatus.classList.add("hidden");
    describeVideoUrl();
    segments = {};
    resetScreenTexts();
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
    updateExportAvailability();
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
    setDiagnosticsVisible(targetViewId === "view-diagnostics");
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
  const sourceTabs = ["url", "file", "library"].map(name => [name, document.getElementById(`source-tab-${name}`)]);
  let libraryLoading = false;
  function selectSourceTab(name) {
    for (const [key, tab] of sourceTabs) {
      tab?.classList.toggle("is-active", key === name);
      tab?.setAttribute("aria-pressed", String(key === name));
    }
    document.getElementById("source-url-panel")?.classList.toggle("hidden", name === "library");
    document.getElementById("source-library-panel")?.classList.toggle("hidden", name !== "library");
  }
  async function loadLibrary() {
    if (libraryLoading) return;
    libraryLoading = true;
    const list = document.getElementById("library-list");
    const status = document.getElementById("library-status");
    status.textContent = "Đang đọc video đã có trên máy…";
    list.replaceChildren();
    try {
      const response = await fetch("/api/library", { cache: "no-store" });
      if (!response.ok) throw new Error("library unavailable");
      const data = await response.json();
      if (!Array.isArray(data.items)) throw new Error("invalid library");
      for (const item of data.items) {
        if (typeof item.name !== "string" || typeof item.file_path !== "string") continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "source-library-item";
        const name = document.createElement("span");
        name.textContent = item.name;
        const size = document.createElement("small");
        size.textContent = formatBytes(item.size);
        button.append(name, size);
        button.addEventListener("click", () => {
          if (videoUrlInput.disabled || exportingTaskId) return;
          window.loadDroppedLocalVideo(item.file_path);
          selectSourceTab("file");
        });
        list.appendChild(button);
      }
      status.textContent = list.children.length ? `${list.children.length} video đã lưu trên máy. Bấm để chọn.` : "Chưa có video trong thư viện. Tải video hoặc chọn file từ máy.";
    } catch (_) {
      status.textContent = "Chưa đọc được thư viện. Bấm Thư viện để thử lại; vẫn có thể chọn file từ máy.";
    } finally { libraryLoading = false; }
  }
  for (const [name, tab] of sourceTabs) tab?.addEventListener("click", () => {
    if (videoUrlInput.disabled || exportingTaskId) return;
    selectSourceTab(name);
    if (name === "file") dropZone.click();
    else if (name === "library") loadLibrary();
    else videoUrlInput.focus();
  });
  document.getElementById("clear-video-url")?.addEventListener("click", () => {
    if (videoUrlInput.disabled || exportingTaskId) return;
    videoUrlInput.value = "";
    handleSourceTextChange();
    videoUrlInput.focus();
  });
  fileInput.addEventListener("click", event => event.stopPropagation());
  function handleSourceTextChange() {
    describeVideoUrl();
    if (!videoUrlInput.value.trim()) return;
    selectedFile = null;
    window.currentLocalFilePath = null;
    fileInput.value = "";
    fileNameDisplay.textContent = "Chọn video từ máy";
    if (!currentTaskId && !pendingStart) {
      playerDownloadProgress?.classList.add("hidden");
      playerTaskStatus?.classList.add("hidden");
      stopPreviewAudio();
      previewGeneration++;
      previewPending = false;
      previewDescriptor = null;
      videoPlayer.removeAttribute("src");
      videoPlayer.load();
      playerPlaceholder.classList.remove("hidden");
      bufferingAlert.classList.add("hidden");
    }
  }
  videoUrlInput.addEventListener("input", handleSourceTextChange);
  videoUrlInput.addEventListener("paste", event => {
    if (videoUrlInput.disabled) return;
    const pasted = event.clipboardData?.getData("text/plain");
    if (!pasted) return;
    const previous = videoUrlInput.value;
    videoUrlInput.value = pasted;
    const cleanUrl = describeVideoUrl();
    if (!cleanUrl) { videoUrlInput.value = previous; describeVideoUrl(); return; }
    event.preventDefault();
    videoUrlInput.value = cleanUrl;
    handleSourceTextChange();
  });

  fileInput.addEventListener("change", (e) => {
    if (videoUrlInput.disabled || exportingTaskId) return;
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
  function playbackVolume() { return masterMuted ? 0 : masterVolume; }
  function applyMasterVolume() {
    videoPlayer.volume = playbackVolume();
    if (activeAudio) activeAudio.volume = parseFloat(volDubSlider.value) * playbackVolume();
    syncPlayback();
    if (playerVolume) playerVolume.value = String(masterMuted ? 0 : masterVolume);
    if (playerMuteToggle) {
      playerMuteToggle.innerHTML = `<i class="fa-solid ${!playbackVolume() ? "fa-volume-xmark" : "fa-volume-high"}" aria-hidden="true"></i>`;
      playerMuteToggle.setAttribute("aria-label", !playbackVolume() ? "Bật tiếng" : "Tắt tiếng");
      playerMuteToggle.setAttribute("aria-pressed", String(!playbackVolume()));
    }
  }
  function updatePlayerControls() {
    if (playerPlayToggle) {
      const playing = !videoPlayer.paused || !!dubPlaybackWait || (previewPending && playWhenPreviewReady);
      playerPlayToggle.innerHTML = `<i class="fa-solid ${playing ? "fa-pause" : "fa-play"}" aria-hidden="true"></i>`;
      playerPlayToggle.setAttribute("aria-label", playing ? "Tạm dừng video" : "Phát video");
    }
  }
  playerPlayToggle?.addEventListener("click", () => {
    if (!videoPlayer.currentSrc && !videoPlayer.getAttribute("src")) return;
    if (previewPending) {
      autoPlayTaskId = null;
      playWhenPreviewReady = !playWhenPreviewReady;
      updatePlayerControls();
      return;
    }
    if (dubPlaybackWait) {
      cancelDubPlaybackWait();
      bufferingAlert.classList.add("hidden");
      videoPlayer.pause();
      return;
    }
    if (videoPlayer.paused) videoPlayer.play().catch(reportPlayFailure);
    else videoPlayer.pause();
  });
  playerMuteToggle?.addEventListener("click", () => {
    masterMuted = !masterMuted;
    if (!masterMuted && !masterVolume) masterVolume = 1;
    applyMasterVolume();
  });
  playerVolume?.addEventListener("input", () => {
    masterVolume = Math.max(0, Math.min(1, Number(playerVolume.value) || 0));
    masterMuted = false;
    applyMasterVolume();
  });
  playerFullscreen?.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await playerContainer.closest(".player-surface").requestFullscreen();
    } catch (_) {
      playerTaskStatus.textContent = "Chưa mở được toàn màn hình. Hãy phóng to cửa sổ Studio.";
      playerTaskStatus.classList.remove("hidden");
    }
  });
  document.getElementById("player-settings")?.addEventListener("click", () => switchTab("view-settings"));
  for (const event of ["play", "pause", "ended", "emptied"]) videoPlayer.addEventListener(event, updatePlayerControls);
  videoPlayer.addEventListener("loadedmetadata", () => {
    if (!currentTaskId && Number.isFinite(videoPlayer.duration)) {
      totalVideoDuration = videoPlayer.duration;
      barTotalTime.textContent = formatTime(totalVideoDuration);
      renderTimelineSlices();
    }
  });
  toggleMaskChinese.addEventListener("change", () => {
    chineseSubMask.style.display = "none";
    screenTextSignature = "";
    renderScreenTexts(videoPlayer.currentTime);
  });
  toggleSubtitles.addEventListener("change", () => {
    subtitleOverlay.style.display = toggleSubtitles.checked ? "block" : "none";
    screenTextSignature = "";
    syncPlayback();
  });
  toggleScreenText.addEventListener("change", () => {
    if (toggleScreenText.disabled) return;
    if (currentTaskId && currentProgress?.status === "COMPLETED") {
      captionPosition.value = toggleScreenText.checked ? "auto" : "bottom";
      scheduleCaptionStyle();
      return;
    }
    screenTextSignature = "";
    chineseSubMask.style.display = "none";
    syncPlayback();
    updateExportScreenTextSummary();
  });
  toggleMaskChinese.addEventListener("change", updateExportScreenTextSummary);

  volDubSlider.addEventListener("input", () => {
    volDubVal.textContent = `${Math.round(volDubSlider.value * 100)}%`;
    if (activeAudio) activeAudio.volume = parseFloat(volDubSlider.value) * playbackVolume();
  });
  volBgmSlider.addEventListener("input", () => {
    volBgmVal.textContent = `${Math.round(volBgmSlider.value * 100)}%`;
    syncPlayback();
  });

  videoPlayer.addEventListener("play", () => {
    if (resultPreviewActive) { videoPlayer.pause(); return; }
    if (!sourceAudition && bgmAudio) {
      bgmAudio.currentTime = videoPlayer.currentTime;
      bgmAudio.play().catch(reportPlayFailure);
    }
    syncPlayback();
    startPlaybackFrames();
  });

  videoPlayer.addEventListener("pause", () => {
    // pause() dispatches asynchronously in a browser. A buffering pause must
    // not cancel its own intent, nor pause audio resumed before this event.
    if (internalDubPauseEvents) { internalDubPauseEvents--; return; }
    if (internalPreviewPauseEvents) { internalPreviewPauseEvents--; return; }
    autoPlayTaskId = null;
    playWhenPreviewReady = false;
    cancelDubPlaybackWait();
    stopPlaybackFrames();
    if (bgmAudio) bgmAudio.pause();
    if (activeAudio) activeAudio.pause();
    releaseDubAudio(activeAudio);
    if (videoPlayer.paused) stopSourceAudition();
  });
  videoPlayer.addEventListener("ended", () => {
    cancelDubPlaybackWait();
    stopPlaybackFrames();
    stopSourceAudition();
    bgmAudio?.pause();
    activeAudio?.pause();
    activeAudio = null;
    activePlayingSegId = null;
    releaseDubAudio();
  });
  window.addEventListener?.("pagehide", stopPreviewAudio);

  videoPlayer.addEventListener("seeking", () => {
    cancelDubPlaybackWait();
    if (sourceAudition) {
      if (videoPlayer.currentTime < sourceAudition.start || videoPlayer.currentTime >= sourceAudition.end) stopSourceAudition();
      else return;
    }
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
    if (pendingStart || btnStart.classList.contains("hidden")) return;
    const url = describeVideoUrl();
    if (videoUrlInput.value.trim() && !url) return;
    if (!url && !selectedFile && !window.currentLocalFilePath) {
      alert("Vui lòng dán link Douyin hoặc chọn video từ máy!");
      return;
    }

    if (exportingTaskId) {
      alert("Hãy chờ xuất video hoặc hủy tác vụ xuất trước khi bắt đầu phiên mới.");
      return;
    }
    if (voiceCatalog.length && !selectedCatalogVoice()) {
      voicePreviewStatus.textContent = "Hãy chọn một giọng đang có sẵn trước khi bắt đầu.";
      voicePreviewStatus.dataset.error = "true";
      voiceList.focus();
      return;
    }
    stopVoicePreview();
    const voicePayload = selectedVoicePayload();
    stopPreviewAudio();
    if (currentWs) currentWs.close();
    currentWs = null;
    currentTaskId = null;
    const request = { cancelled: false };
    pendingStart = request;
    currentProgress = null;
    streamDisconnected = false;
    taskConnectionStatus.classList.add("hidden");
    totalVideoDuration = 0;
    translationReady = false;
    previewGeneration++;
    previewPending = false;
    previewResumeTime = null;
    resetTaskResult();
    btnStart.classList.add("hidden");
    updateVoiceControls();
    videoUrlInput.disabled = true;
    fileInput.disabled = true;
    dropZone.setAttribute("aria-disabled", "true");
    btnPauseWorker.classList.add("hidden");
    btnResumeWorker.classList.add("hidden");
    btnStopWorker.classList.remove("hidden");
    btnStopWorker.disabled = false;

    segments = {};
    resetScreenTexts();
    const useVisualTranslation = visualTranslation?.checked === true;
    if (visualTranslation) visualTranslation.disabled = true;
    renderSegmentsDrawer();
    timelineTrack.innerHTML = '<div id="playback-head-marker" class="absolute top-0 bottom-0 w-1 bg-white z-10 shadow-glow" style="left: 0%;"></div>';
    playbackHeadMarker = document.getElementById("playback-head-marker");

    telTtfp.textContent = "--";
    telBuffer.textContent = "+0.0s";
    telPlayable.textContent = "00:00";
    telPlaying.textContent = "00:00";
    telRtf.textContent = "0.0x";
    barCurrentTime.textContent = "00:00";
    barTotalTime.textContent = "00:00";
    barBufferInfo.textContent = "Buffer: 0.0s";
    document.getElementById("pipeline-warning")?.classList.add("hidden");
    bufferingAlert.classList.remove("hidden");
    showTaskProgress({ phase: "request", stage: "Đang gửi yêu cầu…", progress_pct: null, status: "RUNNING", can_pause: false });

    try {
      let res;
      if (!url && window.currentLocalFilePath) {
        // Fast local file start without HTTP upload
        res = await fetch("/api/streaming/start-local-file", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            file_path: window.currentLocalFilePath,
            initial_buffer_seconds: parseFloat(bufferSelect.value) || 10.0,
            ...voicePayload,
            asr_engine: document.body.dataset.asrEngine,
            visual_translation: useVisualTranslation
          })
        });
      } else if (!url && selectedFile) {
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("initial_buffer_seconds", bufferSelect.value);
        for (const [name, value] of Object.entries(voicePayload)) formData.append(name, value);
        formData.append("asr_engine", document.body.dataset.asrEngine);
        formData.append("visual_translation", String(useVisualTranslation));
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
            ...voicePayload,
            asr_engine: document.body.dataset.asrEngine,
            visual_translation: useVisualTranslation
          })
        });
      }

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Không thể khởi tạo phiên dịch video");
      }

      const data = await res.json();
      if (!data.task_id) throw new Error("Ứng dụng chưa trả mã tác vụ. Hãy mở Diagnostics để kiểm tra.");
      if (request.cancelled) {
        const stopResponse = await fetch(`/api/tasks/${encodeURIComponent(data.task_id)}/stop`, { method: "POST" });
        if (!stopResponse.ok) throw new Error("Không thể dừng yêu cầu vừa tạo. Mở Tác vụ để dừng lại.");
        showTaskProgress({ phase: "stopped", status: "STOPPED", stage: "Đã hủy yêu cầu", progress_pct: null });
        bufferingAlert.classList.add("hidden");
        updateTasksTable();
        return;
      }
      currentTaskId = data.task_id;

      if (data.video_url) setPreviewSource(data.video_url, { task_id: currentTaskId });
      else {
        previewGeneration++;
        previewDescriptor = { task_id: currentTaskId };
        videoPlayer.removeAttribute("src");
        videoPlayer.load();
      }
      videoPlayer.volume = playbackVolume();
      showTaskProgress(data.progress || { phase: url ? "resolve" : "prepare", stage: url ? "Đang kiểm tra và lấy video từ link…" : "Đang chuẩn bị video…", progress_pct: null, status: "RUNNING", can_pause: false });

      setupStreamingWebSocket(currentTaskId, { allowAutoPlay: true });
      updateTasksTable();

    } catch (e) {
      showTaskProgress({ phase: "failed", status: "FAILED", stage: e.message, progress_pct: null });
      showMediaError(e.message);
    } finally {
      if (pendingStart === request) pendingStart = null;
    }
  });

  // =========================================================
  // 6. STREAMING WEBSOCKET & DUBBING SYNC
  // =========================================================
  function applyStreamingSnapshot(msg) {
    applyCaptionStyleSnapshot(msg);
    visualSession = msg.visual_translation === true || (msg.screen_texts || []).length > 0;
    totalVideoDuration = msg.duration;
    setScreenTexts(msg.screen_texts);
    chineseSubMask.style.display = "none";
    positionVideoOverlays();
    barTotalTime.textContent = formatTime(totalVideoDuration);
    segmentsCountBadge.textContent = `${msg.segments_count} câu`;

    if (msg.bgm_url && (!bgmAudio || bgmUrl !== msg.bgm_url)) {
      bgmUrl = msg.bgm_url;
      if (bgmAudio) {
        bgmAudio.pause();
        bgmAudio = null;
      }
      bgmAudio = new Audio(bgmUrl);
      if (playerBgmStatus) playerBgmStatus.textContent = "Đã lọc";
      bgmAudio.addEventListener("error", () => showMediaError("Không phát được nhạc nền. Xem Diagnostics và thử lại phiên dịch."));
      bgmAudio.volume = parseFloat(volBgmSlider.value) * playbackVolume();
      if (sourceAudition) sourceAudition.muted = true;
      else videoPlayer.muted = true; // Raw audio muted; BGM + Foley plays via bgmAudio!
    }

    if (msg.suppression_level) {
      telSuppression.textContent = `≈ ${msg.suppression_level}`;
      playerSuppressionBadge.textContent = `≈ ${msg.suppression_level}`;
    }

    const incomingSegments = Array.isArray(msg.segments) ? msg.segments : [];
    const activeSnapshot = incomingSegments.find(s => s.id === activePlayingSegId);
    if (activeAudio && (!activeSnapshot ||
        activeSnapshot.audio_url !== segments[activePlayingSegId]?.audio_url ||
        !sameDubTiming(activeSnapshot, segments[activePlayingSegId]))) {
      activeAudio.pause();
      activeAudio = null;
      activePlayingSegId = null;
    }
    const previousSegments = segments;
    segments = {};
    incomingSegments.forEach(s => {
      const previous = previousSegments[s.id];
      segments[s.id] = previous && (previous.revision || 0) > (s.revision || 0) ? previous : s;
    });
    renderTimelineSlices();
    renderSegmentsDrawer();
    updateExportAvailability();
    syncPlayback();
  }

  function queueStreamRecovery(taskId, socket) {
    if (reconnectTimer || recoveryRequest || taskId !== currentTaskId || socket !== currentWs) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      recoverStreamingSession(taskId, socket);
    }, 1500);
  }

  async function recoverStreamingSession(taskId, socket) {
    if (recoveryRequest || taskId !== currentTaskId || socket !== currentWs || !streamDisconnected) return;
    const controller = new AbortController();
    recoveryRequest = controller;
    const revision = streamEventRevision;
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(`/api/streaming/${encodeURIComponent(taskId)}`, {signal: controller.signal, cache: "no-store"});
      if (!response.ok) throw new Error("Không đọc được phiên dịch");
      const data = await response.json();
      if (taskId !== currentTaskId || socket !== currentWs) return;
      if (revision === streamEventRevision) {
        if (data.video_url) setPreviewSource(data.video_url, {task_id: taskId});
        showTaskProgress(data.progress || {});
        if (taskId !== currentTaskId) return;
        if (data.initialized && Array.isArray(data.segments)) applyStreamingSnapshot(data);
        translationReady = data.telemetry?.ready_to_play === true || translationReady;
        showTaskResult(data);
      }
      setupStreamingWebSocket(taskId);
      updateTasksTable();
    } catch (_) {
      // Task polling retains the last measured progress while this retries.
    } finally {
      clearTimeout(timeout);
      if (recoveryRequest === controller) recoveryRequest = null;
      if (taskId === currentTaskId && socket === currentWs && streamDisconnected) queueStreamRecovery(taskId, socket);
    }
  }

  window.addEventListener("pagehide", () => {
    clearTimeout(captionStyleTimer); captionStyleTimer = null;
    captionStyleRequest = null; captionStyleDirty = false; captionStyleEditVersion++;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    recoveryRequest?.abort();
    const socket = currentWs;
    currentWs = null;
    if (socket) socket.close();
  });

  function setupStreamingWebSocket(taskId, { allowAutoPlay = false } = {}) {
    autoPlayTaskId = allowAutoPlay ? taskId : null;
    if (!allowAutoPlay) playWhenPreviewReady = false;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    const previousSocket = currentWs;
    currentWs = null;
    if (previousSocket) previousSocket.close();

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    currentWs = new WebSocket(`${proto}//${window.location.host}/ws/stream/${taskId}`);

    const socket = currentWs;
    socket.onopen = () => {
      if (socket !== currentWs) return;
      streamDisconnected = false;
      taskConnectionStatus.classList.add("hidden");
    };
    const reportDisconnect = () => {
      if (socket !== currentWs || taskId !== currentTaskId) return;
      streamDisconnected = true;
      taskConnectionStatus.textContent = "Mất kết nối realtime. Đang kiểm tra trạng thái tác vụ qua máy chủ…";
      taskConnectionStatus.classList.remove("hidden");
      updateTasksTable();
      queueStreamRecovery(taskId, socket);
    };
    socket.onclose = reportDisconnect;
    socket.onerror = reportDisconnect;
    currentWs.onmessage = (event) => {
      if (socket !== currentWs) return;
      const msg = JSON.parse(event.data);
      streamEventRevision++;

      if (msg.type === "progress") {
        showTaskProgress(msg);
      }
      else if (msg.type === "source_ready") {
        if (msg.video_url) setPreviewSource(msg.video_url, { task_id: taskId });
      }
      else if (msg.type === "init") {
        applyStreamingSnapshot(msg);
      }
      else if (msg.type === "segment_update") {
        applySegmentUpdate(msg);
        if (isBufferingUnderrun && ["READY", "PLAYED"].includes(msg.status) &&
            dubContainsTime(msg, videoPlayer.currentTime)) {
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
      else if (msg.type === "screen_update") {
        // Visual chunks are committed independently. Refresh OCR overlays even
        // when a chunk contains no speech row, so the UI never waits for the
        // full prepass before showing the completed on-screen text work.
        if (Array.isArray(msg.screen_texts)) {
          visualSession = true;
          setScreenTexts(msg.screen_texts);
          (msg.segments || []).forEach(applySegmentUpdate);
          renderScreenTexts(videoPlayer.currentTime || 0);
          positionVideoOverlays();
        }
        if (msg.processed_seconds != null && msg.total_seconds != null) {
          showTaskProgress({phase: "visual", processed_seconds: msg.processed_seconds,
            total_seconds: msg.total_seconds, chunk_start: msg.chunk_start,
            chunk_end: msg.chunk_end});
        }
      }
      else if (msg.type === "caption_style") {
        if (!captionStyleDirty && !captionStyleRequest && (Number(msg.caption_style_revision) || 0) >= captionStyleRevision) {
          applyCaptionStyleSnapshot(msg);
          (msg.segments || []).forEach(applySegmentUpdate);
          positionVideoOverlays();
        }
      }
      else if (msg.type === "telemetry") {
        if (msg.review_summary) showTaskProgress({review_summary: msg.review_summary});
        showPipelineWarnings(msg);
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
        if (!previewPending) bufferingAlert.classList.add("hidden");
        if (autoPlayTaskId === taskId) {
          autoPlayTaskId = null;
          if (!resultPreviewActive) {
            playWhenPreviewReady = true;
            if (!previewPending) {
              const generation = previewGeneration;
              videoPlayer.play().then(() => {
                if (generation === previewGeneration && !previewPending) playWhenPreviewReady = false;
              }).catch(error => {
                if (generation !== previewGeneration) return;
                if (error.name === "NotSupportedError" && playWhenPreviewReady) loadCompatiblePreview();
                else { playWhenPreviewReady = false; reportPlayFailure(error); }
              });
            }
          }
        }
      }
      else if (msg.type === "review_complete") {
        showPipelineWarnings(msg);
        if (pendingTaskAction?.kind === "review") pendingTaskAction.progressSeen = true;
        const summary = msg.review_summary || msg.summary;
        if (summary) showTaskProgress({...msg.progress, review_summary: summary});
      }
      else if (msg.type === "export_progress") {
        if (!exportingTaskId) {
          automaticExportActive = true;
          showTaskProgress({status: "RUNNING", phase: "export", stage: msg.stage || "Đang xuất video kết quả…", progress_pct: msg.progress, can_review: false, can_pause: false});
        }
        renderExportProgress(msg.progress, msg.stage);
      }
      else if (msg.type === "result_ready") {
        automaticExportActive = false;
        if (!captionStyleDirty && !captionStyleRequest) captionOutputOutdated = false;
        showTaskResult(msg);
        showTaskProgress({status: "COMPLETED", phase: "complete", stage: "Video kết quả đã sẵn sàng", progress_pct: 100,
          ...(msg.review_summary ? {review_summary: msg.review_summary} : {})});
      }
      else if (msg.type === "result_invalidated") {
        resetTaskResult();
        showTaskProgress({output_video_url: "", output_filename: "",
          ...(msg.review_summary ? {review_summary: msg.review_summary} : {})});
      }
      else if (msg.type === "result_error") {
        showExportFailure(msg.message);
        showTaskProgress({status: "FAILED", phase: "export_error", stage: msg.message || "Chưa xuất được video kết quả", progress_pct: null});
      }
      else if (msg.type === "error") {
        const message = msg.message || msg.error || "Xử lý thất bại. Xem Diagnostics rồi thử lại.";
        showTaskProgress({ phase: "failed", status: "FAILED", stage: message, progress_pct: null });
        updateTasksTable();
      }
      else if (msg.type === "finished") {
        if (pendingTaskAction?.kind === "review") pendingTaskAction.progressSeen = true;
        const finishedStatus = String(msg.status || "").toUpperCase();
        const reviewFailed = ["failed", "incomplete"].includes((msg.review_summary || currentProgress?.review_summary)?.status)
          && !msg.error && !(currentProgress?.status === "FAILED" && currentProgress?.phase === "failed");
        const failed = finishedStatus === "FAILED" || currentProgress?.status === "FAILED" || reviewFailed;
        const stopped = ["CANCELLED", "STOPPED"].includes(finishedStatus);
        showTaskProgress({ status: failed ? "FAILED" : stopped ? "STOPPED" : "COMPLETED",
          ...(msg.review_summary ? {review_summary: msg.review_summary} : {}),
          phase: failed ? (reviewFailed ? "review" : "failed") : stopped ? "stopped" : "complete",
          stage: failed ? (currentProgress?.status === "FAILED" ? currentProgress.stage : msg.message || "Xử lý video thất bại") : stopped ? "Đã dừng tác vụ" : "Hoàn tất xử lý câu thoại",
          progress_pct: failed || stopped ? null : 100 });
        updateTasksTable();
      }
    };
  }

  function releaseDubAudio(keep = null) {
    for (const [id, entry] of dubAudioCache) {
      if (entry.audio === keep) continue;
      // Remove before load(): aborted preloads must not report stale errors.
      dubAudioCache.delete(id);
      entry.audio.pause();
      entry.audio.removeAttribute("src");
      entry.audio.load();
    }
  }

  function cancelDubPlaybackWait() {
    const wait = dubPlaybackWait;
    if (!wait) return;
    dubPlaybackWait = null;
    clearTimeout(wait.timer);
    wait.audio.pause();
    updatePlayerControls();
  }

  function currentDubWait(wait) {
    return dubPlaybackWait === wait && currentTaskId === wait.taskId && !sourceAudition && !resultPreviewActive &&
      activePlayingSegId === wait.segmentId && activeAudio === wait.audio &&
      segments[wait.segmentId]?.audio_url === wait.url &&
      sameDubTiming(segments[wait.segmentId], wait.bounds) &&
      Math.abs(videoPlayer.currentTime - wait.time) < 0.05;
  }

  function failDubPlayback(wait, error = null) {
    if (!currentDubWait(wait)) return;
    cancelDubPlaybackWait();
    const entry = dubAudioCache.get(wait.segmentId);
    if (entry?.audio === wait.audio) dubAudioCache.delete(wait.segmentId);
    wait.audio.removeAttribute("src");
    wait.audio.load();
    activeAudio = null;
    activePlayingSegId = null;
    if (error?.name === "NotAllowedError") reportPlayFailure(error);
    else showMediaError("Chưa tải/phát được giọng đọc. Bấm Phát để tải lại câu này; nếu vẫn lỗi, mở Diagnostics.");
  }

  function resumeDubPlayback(wait) {
    if (!currentDubWait(wait) || wait.starting) return;
    const entry = dubAudioCache.get(wait.segmentId);
    // Fitted speech can end before its segment. play() at the media endpoint
    // rewinds to zero, so a cold seek into trailing silence must skip it.
    if (entry && dubSpeechFinished(segments[wait.segmentId], wait.audio, wait.time)) {
      wait.audio.pause();
      clearTimeout(wait.timer);
      dubPlaybackWait = null;
      bufferingAlert.classList.add("hidden");
      renderSpeechCaption(segments[wait.segmentId], wait.time);
      videoPlayer.play().catch(reportPlayFailure);
      return;
    }
    if (!entry || entry.waiting || !(wait.audio.readyState >= 3) || wait.audio.seeking) return;
    wait.starting = true;
    Promise.resolve(wait.audio.play()).then(() => {
      if (!currentDubWait(wait)) {
        if (dubPlaybackWait?.audio !== wait.audio && (activeAudio !== wait.audio || videoPlayer.paused)) wait.audio.pause();
        return;
      }
      wait.starting = false;
      if (!(wait.audio.readyState >= 3) || wait.audio.seeking || entry.waiting || wait.audio.paused) return;
      clearTimeout(wait.timer);
      dubPlaybackWait = null;
      bufferingAlert.classList.add("hidden");
      renderSpeechCaption(segments[wait.segmentId], wait.time);
      videoPlayer.play().catch(reportPlayFailure);
    }).catch(error => failDubPlayback(wait, error));
  }

  function dubSpeechFinished(segment, audio, time) {
    const bounds = resolveDubTiming(segment);
    return bounds && audio.readyState >= 1 && Number.isFinite(audio.duration) && audio.duration > 0 &&
      time - bounds.start >= audio.duration;
  }

  function waitForDubPlayback(segment, audio) {
    if (dubPlaybackWait && !currentDubWait(dubPlaybackWait)) cancelDubPlaybackWait();
    if (!dubPlaybackWait) {
      const wait = {taskId: currentTaskId, segmentId: segment.id, url: segment.audio_url,
        bounds: resolveDubTiming(segment), audio, time: videoPlayer.currentTime, starting: false};
      dubPlaybackWait = wait;
      wait.timer = setTimeout(() => failDubPlayback(wait), 15000);
      // Pause the master clock before asking the browser to decode/play audio.
      // Keep the already rendered cue frozen until actual playback starts.
      stopPlaybackFrames();
      bgmAudio?.pause();
      if (!videoPlayer.paused) { internalDubPauseEvents++; videoPlayer.pause(); }
      audio.pause();
      bufferingText.textContent = "Đang chờ âm thanh câu hiện tại…";
      bufferingAlert.classList.remove("hidden");
      updatePlayerControls();
    }
    resumeDubPlayback(dubPlaybackWait);
  }

  function prepareDubAudio(curTime) {
    // Only the current and next two ready utterances are retained. Never
    // preload an entire long transcript or hold old revisions after an edit.
    const upcoming = Object.values(segments)
      .filter(segment => {
        const bounds = resolveDubTiming(segment);
        return bounds && bounds.end > curTime && bounds.start < curTime + 12 &&
          ["READY", "PLAYED"].includes(segment.status) && segment.audio_url;
      })
      .sort((a, b) => resolveDubTiming(a).start - resolveDubTiming(b).start).slice(0, 3);
    const wanted = new Map(upcoming.map(segment => [segment.id, segment]));
    for (const [id, entry] of dubAudioCache) {
      const segment = wanted.get(id);
      if (segment && entry.url === segment.audio_url && entry.revision === (segment.revision || 0) &&
          sameDubTiming(segment, entry.bounds)) continue;
      dubAudioCache.delete(id);
      entry.audio.pause();
      entry.audio.removeAttribute("src");
      entry.audio.load();
    }
    for (const segment of upcoming) {
      if (dubAudioCache.has(segment.id)) continue;
      const audio = new Audio(segment.audio_url);
      const entry = { audio, url: segment.audio_url, revision: segment.revision || 0,
        bounds: resolveDubTiming(segment) };
      dubAudioCache.set(segment.id, entry);
      audio.preload = "auto";
      audio.addEventListener("error", () => {
        if (dubAudioCache.get(segment.id) !== entry || activeAudio !== audio) return;
        if (!dubPlaybackWait && !videoPlayer.paused) waitForDubPlayback(segment, audio);
        if (dubPlaybackWait?.audio === audio) failDubPlayback(dubPlaybackWait);
      });
      for (const event of ["waiting", "stalled"]) audio.addEventListener(event, () => {
        if (dubAudioCache.get(segment.id) !== entry || activeAudio !== audio) return;
        // stalled describes the network request, not necessarily playback.
        // Buffered audio may continue without another canplay event.
        if (event === "stalled" && audio.readyState >= 3 && !audio.seeking) return;
        entry.waiting = true;
        if (!videoPlayer.paused || dubPlaybackWait?.audio === audio) waitForDubPlayback(segment, audio);
      });
      for (const event of ["canplay", "seeked", "playing"]) audio.addEventListener(event, () => {
        if (dubAudioCache.get(segment.id) !== entry || activeAudio !== audio) return;
        entry.waiting = false;
        if (dubPlaybackWait?.audio === audio) resumeDubPlayback(dubPlaybackWait);
        else if (event === "playing" && videoPlayer.paused) audio.pause();
      });
      audio.addEventListener("loadedmetadata", () => {
        if (dubAudioCache.get(segment.id) === entry && dubPlaybackWait?.audio === audio)
          resumeDubPlayback(dubPlaybackWait);
      });
      audio.load();
    }
  }

  function stopPlaybackFrames() {
    playbackFrameGeneration++;
    if (playbackFrame !== null) window.cancelAnimationFrame?.(playbackFrame);
    playbackFrame = null;
  }

  function startPlaybackFrames() {
    if (playbackFrame !== null || videoPlayer.paused || videoPlayer.ended || !window.requestAnimationFrame) return;
    const generation = playbackFrameGeneration;
    const frame = () => {
      if (generation !== playbackFrameGeneration) return;
      playbackFrame = null;
      if (videoPlayer.paused || videoPlayer.ended) return;
      syncPlayback();
      if (generation === playbackFrameGeneration && !videoPlayer.paused && !videoPlayer.ended)
        playbackFrame = window.requestAnimationFrame(frame);
    };
    playbackFrame = window.requestAnimationFrame(frame);
  }

  // timeupdate may arrive only four times a second. Preload the next utterance
  // and synchronize on animation frames so short lines retain their opening.
  function syncPlayback() {
    const curTime = videoPlayer.currentTime;
    if (dubPlaybackWait) {
      if (currentDubWait(dubPlaybackWait)) { resumeDubPlayback(dubPlaybackWait); return; }
      cancelDubPlaybackWait();
    }
    telPlaying.textContent = formatTime(curTime);
    barCurrentTime.textContent = formatTime(curTime);
    const hasScreenSubtitle = renderScreenTexts(curTime);

    if (totalVideoDuration > 0) {
      const pct = (curTime / totalVideoDuration) * 100;
      if (playbackHeadMarker) playbackHeadMarker.style.left = `${Math.min(100, Math.max(0, pct))}%`;
      timelineTrack.setAttribute("aria-valuenow", String(Math.round(curTime)));
    }

    if (sourceAudition) {
      bgmAudio?.pause();
      activeAudio?.pause();
      if (curTime < sourceAudition.start || curTime >= sourceAudition.end) {
        stopSourceAudition(true);
      } else {
        highlightTranscript(sourceAudition.id);
        subtitleText.textContent = "";
        subtitleOverlay.classList.add("opacity-0");
      }
      return;
    }
    prepareDubAudio(curTime);

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
      if (dubContainsTime(s, curTime)) {
        matchedSeg = s;
        break;
      }
    }

    highlightTranscript(matchedSeg?.id ?? null);

    if (matchedSeg) {
      const dubBounds = resolveDubTiming(matchedSeg);
      if (!["READY", "PLAYED"].includes(matchedSeg.status) && !videoPlayer.paused) {
        isBufferingUnderrun = true;
        videoPlayer.pause();
        bufferingText.textContent = matchedSeg.status === "FAILED"
          ? "Không tạo được giọng cho câu này. Xem Diagnostics rồi thử lại."
          : "Đang chờ dịch và tạo giọng cho câu tiếp theo…";
        bufferingAlert.classList.remove("hidden");
      }

      if (["READY", "PLAYED"].includes(matchedSeg.status) && matchedSeg.audio_url) {
        const preparedAudio = dubAudioCache.get(matchedSeg.id)?.audio;
        if (activePlayingSegId !== matchedSeg.id || activeAudio !== preparedAudio) {
          if (activeAudio) {
            activeAudio.pause();
            activeAudio = null;
          }
          activePlayingSegId = matchedSeg.id;
          activeAudio = preparedAudio;
          activeAudio.volume = parseFloat(volDubSlider.value) * playbackVolume();
          const offset = Math.max(0, curTime - dubBounds.start);
          activeAudio.currentTime = offset;
        }
        activeAudio.playbackRate = videoPlayer.playbackRate;
        const offset = Math.max(0, curTime - dubBounds.start);
        if (Math.abs(activeAudio.currentTime - offset) > 0.25) activeAudio.currentTime = offset;
        const entry = dubAudioCache.get(matchedSeg.id);
        if (!videoPlayer.paused && !audioPermissionNeeded && !activeAudio.ended &&
            !dubSpeechFinished(matchedSeg, activeAudio, curTime) &&
            (activeAudio.paused || !(activeAudio.readyState >= 3) || activeAudio.seeking || entry.waiting)) {
          waitForDubPlayback(matchedSeg, activeAudio);
          return;
        }
      } else if (activeAudio) {
        // A confirmed silent row is still a matched segment. Stop the prior
        // dub when crossing into it, just as we do for a gap between rows.
        activeAudio.pause();
        activeAudio = null;
        activePlayingSegId = null;
      }
      renderSpeechCaption(matchedSeg, curTime);
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
      bgmAudio.volume = parseFloat(volBgmSlider.value) * playbackVolume() * (speaking ? Math.pow(10, duckingLevel / 20) : 1);
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
      const bounds = resolveDubTiming(s);
      if (!bounds) continue;
      const slice = document.createElement("div");
      slice.id = `slice-seg-${s.id}`;
      slice.className = "absolute top-0 bottom-0 transition-colors duration-200 bg-gray-800";
      slice.style.left = `${(bounds.start / totalVideoDuration) * 100}%`;
      slice.style.width = `${Math.max(0.5, ((bounds.end - bounds.start) / totalVideoDuration) * 100)}%`;
      timelineTrack.appendChild(slice);
      updateSegmentSlice(s);
    }
    syncPlayback();
  }

  function updateSegmentSlice(seg) {
    const el = document.getElementById(`slice-seg-${seg.id}`);
    if (!el) return;
    const bounds = resolveDubTiming(seg);
    el.hidden = !bounds;
    if (bounds && totalVideoDuration > 0) {
      el.style.left = `${(bounds.start / totalVideoDuration) * 100}%`;
      el.style.width = `${Math.max(0.5, ((bounds.end - bounds.start) / totalVideoDuration) * 100)}%`;
    }
    if (seg.status === "READY" || seg.status === "PLAYED") {
      el.className = "absolute top-0 bottom-0 transition-colors duration-200 bg-emerald-500/80";
    } else if (seg.status === "ASR" || seg.status === "TRANSLATING" || seg.status === "TTS") {
      el.className = "absolute top-0 bottom-0 transition-colors duration-200 bg-amber-500/80 animate-pulse";
    }
  }

  // Transcript rows retain their editors while background segment updates arrive.
  function segmentTranslation(segment) {
    return typeof segment.final_vi === "string" ? segment.final_vi : segment.natural_vi || segment.literal_vi || "";
  }

  function segmentStatusLabel(status) {
    if (["STOPPED", "CANCELLED"].includes(currentProgress?.status) &&
        ["WAITING", "ASR", "TRANSLATING", "TTS", "ALIGNING", "FAILED"].includes(status)) return "Chưa xong · Đã dừng";
    return { WAITING: "Đang chờ", ASR: "Nhận dạng", TRANSLATING: "Đang dịch", TTS: "Tạo giọng",
      READY: "Sẵn sàng", PLAYED: "Sẵn sàng", FAILED: "Có lỗi", NEEDS_REVIEW: "Cần kiểm tra" }[status] || "Đang xử lý";
  }

  function seekTranscript(segment) {
    if (!segment || !currentTaskId) return;
    const bounds = resolveDubTiming(segment);
    if (!bounds) { showMediaError("Mốc thời gian lồng tiếng không hợp lệ. Hãy xử lý lại câu này."); return; }
    stopSourceAudition();
    videoPlayer.currentTime = Math.max(0, Math.min(totalVideoDuration || bounds.end, bounds.start));
    if (bgmAudio) bgmAudio.currentTime = videoPlayer.currentTime;
    syncPlayback();
  }

  function stopSourceAudition(atEnd = false) {
    if (!sourceAudition) return;
    const audition = sourceAudition;
    sourceAudition = null;
    clearInterval(audition.timer);
    videoPlayer.pause();
    if (atEnd) videoPlayer.currentTime = audition.end;
    videoPlayer.muted = audition.muted;
    videoPlayer.volume = playbackVolume();
    chineseSubMask.style.display = "none";
    screenTextSignature = "";
    renderScreenTexts(videoPlayer.currentTime);
    const currentSegment = Object.values(segments).find(segment => dubContainsTime(segment, videoPlayer.currentTime));
    renderSpeechCaption(currentSegment, videoPlayer.currentTime);
    const button = transcriptRows.get(audition.id)?.listen;
    if (button) {
      button.textContent = "Nghe gốc";
      button.setAttribute("aria-pressed", "false");
    }
  }

  async function auditionOriginal(id) {
    const segment = segments[id];
    if (!currentTaskId || !segment || !Number.isFinite(segment.start) || !Number.isFinite(segment.end) || segment.end <= segment.start) return;
    if (sourceAudition?.id === id) { stopSourceAudition(); return; }
    stopSourceAudition();
    stopVoicePreview();
    cancelDubPlaybackWait();
    videoPlayer.pause();
    activeAudio?.pause();
    bgmAudio?.pause();
    isBufferingUnderrun = false;
    const audition = { id, start: Math.max(0, segment.start), end: segment.end, muted: videoPlayer.muted };
    sourceAudition = audition;
    videoPlayer.currentTime = audition.start;
    videoPlayer.muted = false;
    // An explicit audition temporarily enables sound without changing master mute.
    videoPlayer.volume = masterVolume || 1;
    bufferingAlert.classList.add("hidden");
    const button = transcriptRows.get(id)?.listen;
    if (button) {
      button.textContent = "Dừng nghe gốc";
      button.setAttribute("aria-pressed", "true");
    }
    audition.timer = setInterval(() => {
      if (sourceAudition === audition && videoPlayer.currentTime >= audition.end) stopSourceAudition(true);
    }, 50);
    try {
      await videoPlayer.play();
    } catch (_) {
      if (sourceAudition !== audition) return;
      stopSourceAudition();
      transcriptStatus.textContent = "Không phát được âm thanh gốc. Kiểm tra video nguồn rồi bấm Nghe gốc để thử lại.";
    }
  }

  function highlightTranscript(id, force = false) {
    if (!force && activeTranscriptId === id) return;
    if (activeTranscriptId !== null) transcriptRows.get(activeTranscriptId)?.row.setAttribute("data-active", "false");
    activeTranscriptId = id;
    const item = transcriptRows.get(id);
    if (!item) return;
    item.row.setAttribute("data-active", "true");
    const editing = [...transcriptRows.values()].some(row => !row.editor.hidden);
    if (transcriptFollow?.checked && !editing) {
      // Only scroll the list, so playback never pulls the entire page away from controls.
      const top = item.row.offsetTop - segmentsList.offsetTop;
      if (top < segmentsList.scrollTop || top + item.row.offsetHeight > segmentsList.scrollTop + segmentsList.clientHeight) {
        segmentsList.scrollTop = Math.max(0, top - segmentsList.clientHeight / 3);
      }
    }
  }

  function applySegmentUpdate(segment) {
    const previous = segments[segment.id];
    if (previous?.revision > (segment.revision || 0)) return;
    if (Array.isArray(segment.screen_texts)) setScreenTexts(segment.screen_texts);
    if (activePlayingSegId === segment.id && (previous?.audio_url !== segment.audio_url ||
        !sameDubTiming(previous, segment))) {
      activeAudio?.pause();
      activeAudio = null;
      activePlayingSegId = null;
    }
    segments[segment.id] = segment;
    updateSegmentSlice(segment);
    updateSegmentDrawerItem(segment);
    updateExportAvailability();
    syncPlayback();
  }

  function startTranscriptEdit(id, event) {
    const segment = segments[id], item = transcriptRows.get(id);
    if (reviewInProgress() || pendingTaskAction?.kind === "review" || automaticExportActive || exportingTaskId || captionStyleDirty || captionStyleRequest) return;
    if (!item || !["READY", "PLAYED", "NEEDS_REVIEW"].includes(segment?.status)) return;
    let caretOffset = 0;
    // The text button has one text node: preserve the word the user clicked when opening its editor.
    if (event && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
      const caret = document.caretPositionFromPoint?.(event.clientX, event.clientY);
      const range = !caret && document.caretRangeFromPoint?.(event.clientX, event.clientY);
      if (caret?.offsetNode === item.translation.firstChild) caretOffset = caret.offset;
      else if (range?.startContainer === item.translation.firstChild) caretOffset = range.startOffset;
    }
    videoPlayer.pause();
    seekTranscript(segment);
    if (item.editor.hidden) item.input.value = segmentTranslation(segment);
    item.editor.hidden = false;
    item.translation.hidden = true;
    item.row.dataset.editing = "true";
    updateCaptionStyleAvailability();
    item.input.focus();
    item.input.setSelectionRange?.(caretOffset, caretOffset);
  }

  function closeTranscriptEdit(id, restoreFocus = true) {
    const item = transcriptRows.get(id);
    if (!item || item.saving) return;
    item.editor.hidden = true;
    item.translation.hidden = false;
    item.row.dataset.editing = "false";
    item.message.textContent = "";
    item.message.dataset.error = "false";
    transcriptDrafts.delete(id);
    updateExportAvailability();
    if (restoreFocus) item.translation.focus();
  }

  async function saveTranscriptEdit(id, confirmSilence = false) {
    const item = transcriptRows.get(id), segment = segments[id];
    if (reviewInProgress() || pendingTaskAction?.kind === "review" || automaticExportActive || exportingTaskId) return;
    if (!item || item.saving || !segment || !currentTaskId) return;
    if (confirmSilence && !(segment.needs_review || segment.status === "NEEDS_REVIEW")) return;
    const text = confirmSilence ? "" : item.input.value.trim();
    if ((!text && !confirmSilence) || text.length > 2000) {
      item.message.textContent = !text ? "Nhập bản dịch trước khi lưu." : "Mỗi câu tối đa 2.000 ký tự.";
      item.message.dataset.error = "true";
      item.input.focus();
      return;
    }
    if (!confirmSilence && text === segmentTranslation(segment) && !segment.needs_review) { closeTranscriptEdit(id); return; }
    const taskId = currentTaskId;
    item.saving = true;
    item.save.disabled = item.cancel.disabled = item.input.disabled = item.silence.disabled = true;
    pendingTranscriptSaves++;
    updateExportAvailability();
    item.message.dataset.error = "false";
    item.message.textContent = confirmSilence ? "Đang xác nhận đoạn không có lời thoại…" : "Đang tạo lại giọng đọc và đồng bộ với câu…";
    try {
      const response = await fetch(`/api/streaming/${encodeURIComponent(taskId)}/segments/${encodeURIComponent(id)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ final_vi: text, ...(confirmSilence ? { confirm_silence: true } : {}) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không lưu được câu. Giữ bản sửa và thử lại.");
      if (!data.segment || data.segment.id !== id) throw new Error("Máy chủ chưa xác nhận câu đã lưu. Hãy thử lại.");
      if (taskId !== currentTaskId || transcriptRows.get(id) !== item) return;
      applySegmentUpdate({ ...data.segment, ...(Array.isArray(data.screen_texts) ? { screen_texts: data.screen_texts } : {}) });
      item.saving = false;
      closeTranscriptEdit(id);
      transcriptStatus.textContent = confirmSilence ? `Đã xác nhận đoạn #${id + 1} không có lời thoại; giữ nguyên thời gian.`
        : `Đã lưu câu #${id + 1}. Phụ đề và giọng đọc đã cập nhật, mốc thời gian giữ nguyên.`;
    } catch (error) {
      if (taskId !== currentTaskId || transcriptRows.get(id) !== item) return;
      item.message.textContent = error.message || "Không lưu được câu. Bản sửa vẫn được giữ để thử lại.";
      item.message.dataset.error = "true";
    } finally {
      item.saving = false;
      item.save.disabled = item.cancel.disabled = item.input.disabled = item.silence.disabled = false;
      pendingTranscriptSaves--;
      updateExportAvailability();
    }
  }

  function createTranscriptRow(segment) {
    const element = (tag, className, text) => {
      const node = document.createElement(tag); node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const row = element("article", "transcript-row"); row.id = `seg-row-${segment.id}`;
    row.dataset.segmentId = String(segment.id);
    const heading = element("div", "transcript-row-heading");
    const time = element("button", "transcript-time", `#${segment.id + 1} · ${formatTime(segment.start)} – ${formatTime(segment.end)}`);
    time.type = "button";
    time.setAttribute("aria-label", `Tua đến câu ${segment.id + 1}, ${formatTime(segment.start)}`);
    time.addEventListener("click", () => seekTranscript(segments[segment.id]));
    const badge = element("span", "transcript-state"); badge.id = `seg-badge-${segment.id}`;
    const listen = element("button", "transcript-time transcript-listen-original", "Nghe gốc");
    listen.type = "button";
    listen.setAttribute("aria-label", `Nghe âm thanh gốc câu ${segment.id + 1}`);
    listen.setAttribute("aria-pressed", "false");
    listen.addEventListener("click", () => auditionOriginal(segment.id));
    heading.append(time, listen, badge);
    const original = element("button", "transcript-original"); original.type = "button"; original.id = `seg-zh-${segment.id}`;
    original.title = "Tua đến câu này";
    original.addEventListener("click", () => seekTranscript(segments[segment.id]));
    const translation = element("button", "transcript-translation"); translation.type = "button"; translation.id = `seg-vi-${segment.id}`;
    translation.title = "Bấm vào chữ cần sửa";
    translation.addEventListener("click", event => startTranscriptEdit(segment.id, event));
    const editor = element("div", "transcript-editor"); editor.hidden = true;
    const label = element("label", "", "Sửa bản dịch tiếng Việt"); label.setAttribute("for", `seg-input-${segment.id}`);
    const input = element("textarea", ""); input.id = `seg-input-${segment.id}`; input.rows = 3; input.maxLength = 2000;
    input.addEventListener("input", () => {
      if (input.value.trim() !== segmentTranslation(segments[segment.id])) transcriptDrafts.add(segment.id);
      else transcriptDrafts.delete(segment.id);
      updateExportAvailability();
    });
    input.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); saveTranscriptEdit(segment.id); }
      if (event.key === "Escape") { event.preventDefault(); closeTranscriptEdit(segment.id); }
    });
    const actions = element("div", "transcript-editor-actions");
    const save = element("button", "", "Lưu và tạo lại giọng"); save.type = "button";
    save.addEventListener("click", () => saveTranscriptEdit(segment.id));
    const cancel = element("button", "", "Hủy sửa"); cancel.type = "button";
    cancel.addEventListener("click", () => closeTranscriptEdit(segment.id));
    actions.append(save, cancel);
    const message = element("p", "transcript-edit-message"); message.setAttribute("role", "status");
    editor.append(label, input, actions, message);
    const review = element("p", "transcript-review"); review.hidden = true;
    review.setAttribute("role", "status");
    const silence = element("button", "transcript-time transcript-confirm-silence", "Không có lời thoại");
    silence.type = "button"; silence.hidden = true;
    silence.title = "Xác nhận đã nghe lại: đoạn này không có lời thoại, giữ nguyên âm thanh nền và thời gian.";
    silence.addEventListener("click", () => {
      if (silence.disabled) return;
      startTranscriptEdit(segment.id);
      return saveTranscriptEdit(segment.id, true);
    });
    row.append(heading, element("p", "transcript-label", "GỐC"), original, element("p", "transcript-label", "TIẾNG VIỆT · BẤM ĐỂ SỬA"), translation, review, silence, editor);
    transcriptRows.set(segment.id, { row, badge, original, listen, translation, review, silence, editor, input, save, cancel, message, saving: false });
    segmentsList.appendChild(row);
    updateSegmentDrawerItem(segment);
  }

  function renderSegmentsDrawer() {
    segmentsList.innerHTML = "";
    const sorted = Object.values(segments).sort((a, b) => a.start - b.start);
    if (transcriptTaskId !== currentTaskId || !sorted.length) {
      stopSourceAudition();
      transcriptRows.clear();
      transcriptDrafts.clear();
      activeTranscriptId = null;
      transcriptTaskId = currentTaskId;
    }
    segmentsCountBadge.textContent = `${sorted.length} câu`;
    if (!sorted.length) {
      segmentsList.innerHTML = '<div class="transcript-empty"><i class="fa-regular fa-file-lines" aria-hidden="true"></i><p>Câu thoại sẽ xuất hiện tại đây</p><span>Nạp video và bắt đầu dịch để xem, sửa và nghe lại cạnh khung hình.</span></div>';
      transcriptStatus.textContent = "Bản sửa sẽ cập nhật phụ đề và tạo lại giọng đọc, giữ nguyên mốc thời gian.";
      return;
    }
    sorted.forEach(segment => {
      const existing = transcriptRows.get(segment.id);
      if (existing) { segmentsList.appendChild(existing.row); updateSegmentDrawerItem(segment); }
      else createTranscriptRow(segment);
    });
    highlightTranscript(activeTranscriptId, true);
  }

  function updateSegmentDrawerItem(segment) {
    if (!transcriptRows.has(segment.id)) { createTranscriptRow(segment); return; }
    const item = transcriptRows.get(segment.id);
    const ready = ["READY", "PLAYED"].includes(segment.status);
    item.badge.textContent = ready && segment.needs_review
      ? "Bản nháp · Cần kiểm tra" : segmentStatusLabel(segment.status);
    if (ready && !segment.needs_review) {
      const label = {verified: "AI đã kiểm tra", corrected: "AI đã sửa", manual: "Đã sửa thủ công"}[segment.verification?.status];
      if (label) item.badge.textContent = label;
    } else if (segment.verification?.status === "unresolved") item.badge.textContent = "AI chưa xác minh được";
    if (segment.source_method === "video-ai" && !segment.needs_review) item.badge.textContent += " · AI hình + tiếng";
    if (segment.source_method === "text-ai" && !segment.needs_review) {
      const provider = {opencode: "OpenCode", "openrouter-free": "OpenRouter"}[segment.translation_provider] || "AI";
      item.badge.textContent += ` · ${provider} · bản chép + OCR`;
    }
    item.badge.dataset.ready = String(ready);
    item.row.dataset.needsReview = String(Boolean(segment.needs_review));
    item.original.textContent = segment.text_zh || (segment.confirmed_silence ? "Không có lời thoại" : "Đang nhận dạng lời thoại…");
    item.translation.textContent = segment.confirmed_silence ? "Đã xác nhận không có lời thoại" : segmentTranslation(segment)
      || (ready && segment.needs_review ? "Chưa đủ căn cứ để dịch câu này." : "Bản dịch sẽ xuất hiện sau khi xử lý.");
    item.translation.disabled = reviewInProgress() || pendingTaskAction?.kind === "review" || automaticExportActive || !!exportingTaskId || !["READY", "PLAYED", "NEEDS_REVIEW"].includes(segment.status);
    item.review.hidden = !segment.needs_review;
    item.silence.hidden = !(segment.needs_review || segment.status === "NEEDS_REVIEW");
    const reviewReason = String(segment.verification?.reason || segment.review_reason || "AI chưa đủ căn cứ xác minh nội dung câu này.")
      .replace(/;?\s*nghe lại trước khi tạo giọng\.?/gi, ".")
      .replace(/\s*Kiểm tra video rồi sửa bản dịch trước khi tạo giọng\.?/gi, "");
    const reviewHint = reviewInProgress() ? "AI đang đối chiếu lại câu này." : segment.verification?.status === "unresolved"
      ? "AI đã kiểm tra nhưng nguồn chưa đủ rõ, nên giữ bản dịch có căn cứ và ghi lại điểm chưa chắc. Không cần xác nhận thủ công."
      : "AI sẽ đối chiếu lại lời gốc và bản dịch. Có thể bấm AI kiểm tra lại sau khi xử lý xong.";
    const emptyHint = ready && !segment.audio_url && !segmentTranslation(segment) ? " Chưa có giọng Việt cho câu này; video vẫn phát tiếp." : "";
    item.review.textContent = segment.needs_review ? `${reviewReason} ${reviewHint}${emptyHint}` : "";
    // Never replace text in an open editor: another worker update must not erase a draft.
    if (item.editor.hidden) item.input.value = segmentTranslation(segment);
  }

  transcriptFollow?.addEventListener("change", () => highlightTranscript(activeTranscriptId, true));
  document.getElementById("transcript-show-time")?.addEventListener("change", event => {
    segmentsList.dataset.hideTime = String(!event.target.checked);
  });

  // Seeking on timeline
  timelineTrack.addEventListener("click", async (e) => {
    if (!totalVideoDuration) return;
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
  function setWorkerSourceBusy(busy) {
    if (visualTranslation) visualTranslation.disabled = busy;
    videoUrlInput.disabled = busy;
    fileInput.disabled = busy;
    dropZone.setAttribute("aria-disabled", String(busy));
    btnStart.classList.toggle("hidden", busy);
    updateVoiceControls();
  }

  function resetWorkerControls() {
    setWorkerSourceBusy(!!pendingTaskAction || reviewInProgress() || automaticExportActive);
    btnPauseWorker.classList.add("hidden");
    btnResumeWorker.classList.add("hidden");
    btnStopWorker.classList.add("hidden");
    btnStopWorker.disabled = false;
    updateExportAvailability();
  }

  async function controlTask(id, action) {
    if (!id) return;
    if (id === currentTaskId && ["pause", "stop"].includes(action)) {
      autoPlayTaskId = null;
      playWhenPreviewReady = false;
    }
    const revision = progressRevision;
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(id)}/${action}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Không thể điều khiển tác vụ");
      if (id === currentTaskId) {
        if (action === "stop") {
          showTaskProgress({ phase: "stopped", status: "STOPPED", stage: "Đã dừng tác vụ", progress_pct: null });
        } else if (revision === progressRevision && !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status)) {
          showTaskProgress({ status: action === "pause" ? "PAUSED" : "RUNNING", can_pause: action === "resume", can_resume: action === "pause" });
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
  window.studioStop = () => {
    if (!currentTaskId && pendingStart) {
      pendingStart.cancelled = true;
      showTaskProgress({ phase: "request", status: "CANCELLING", stage: "Đang hủy yêu cầu…", progress_pct: null, can_pause: false });
      return;
    }
    return controlTask(currentTaskId, "stop");
  };
  btnPauseWorker.addEventListener("click", window.studioPause);
  btnResumeWorker.addEventListener("click", window.studioResume);
  btnStopWorker.addEventListener("click", window.studioStop);
  window.studioAttachTask = async taskId => {
    if (!/^[a-zA-Z0-9_-]{1,80}$/.test(taskId) || pendingStart || pendingTaskAction || exportingTaskId || captionStyleDirty || captionStyleRequest || transcriptDrafts.size || pendingTranscriptSaves) return false;
    pendingTaskAction = {kind: "attach"};
    stopVoicePreview();
    setWorkerSourceBusy(true);
    try {
      const response = await fetch(`/api/streaming/${encodeURIComponent(taskId)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Phiên dịch không còn tồn tại.");
      stopPreviewAudio();
      if (currentWs) currentWs.close();
      currentWs = null; currentTaskId = taskId; currentProgress = null;
      restoreSessionVoice(data, taskId);
      syncCaptionStyleTask();
      resetTaskResult();
      segments = {}; resetScreenTexts(); renderSegmentsDrawer();
      translationReady = false; isBufferingUnderrun = false;
      if (data.video_url) setPreviewSource(data.video_url, {task_id: taskId});
      else {
        previewGeneration++;
        previewDescriptor = {task_id: taskId};
        previewPending = false;
        previewResumeTime = null;
        videoPlayer.removeAttribute("src");
        videoPlayer.load();
      }
      showTaskProgress(data.progress || {});
      if (data.initialized && Array.isArray(data.segments)) applyStreamingSnapshot(data);
      translationReady = data.telemetry?.ready_to_play === true;
      applyCaptionStyleSnapshot(data);
      showTaskResult(data);
      setupStreamingWebSocket(taskId);
      updateTasksTable();
      return true;
    } finally {
      pendingTaskAction = null;
      setWorkerSourceBusy(!!currentTaskId && !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status));
      updateExportAvailability();
    }
  };
  window.openTaskInStudio = async taskId => {
    try {
      if (await window.studioAttachTask(taskId)) switchTab("view-studio");
      else alert("Hãy chờ thao tác hiện tại hoặc lưu lời thoại trước khi mở tác vụ.");
    } catch (error) { alert(error.message); }
  };
  window.studioRetry = async () => {
    if (!currentTaskId || !currentProgress?.can_retry || pendingTaskAction || btnRetryWorker.disabled) return;
    const taskId = currentTaskId;
    const request = {kind: "retry", progressSeen: false};
    pendingTaskAction = request;
    btnRetryWorker.disabled = true;
    stopVoicePreview();
    setWorkerSourceBusy(true);
    try {
      const response = await fetch(`/api/streaming/${encodeURIComponent(taskId)}/retry`, {method: "POST"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Chưa thử lại được câu lỗi.");
      if (currentTaskId !== taskId) return;
      // The live socket may already have delivered newer worker progress.
      if (!request.progressSeen) {
        currentProgress = null;
        showTaskProgress(data.progress);
      }
      if (!["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status)) {
        isBufferingUnderrun = true;
        bufferingText.textContent = currentProgress?.stage || "Đang tiếp tục từ phần đã lưu…";
        bufferingAlert.classList.remove("hidden");
        if (translationReady && !previewPending) videoPlayer.play().catch(reportPlayFailure);
      }
      if (!currentWs || currentWs.readyState !== WebSocket.OPEN) setupStreamingWebSocket(taskId);
    } catch (error) { alert(error.message); }
    finally {
      pendingTaskAction = null;
      btnRetryWorker.disabled = false;
      setWorkerSourceBusy(!!currentTaskId && !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status));
    }
  };
  btnRetryWorker?.addEventListener("click", window.studioRetry);

  window.studioReview = async () => {
    if (!currentTaskId || !currentProgress?.can_review || pendingTaskAction || pendingStart ||
        exportingTaskId || automaticExportActive || transcriptDrafts.size || pendingTranscriptSaves || reviewInProgress() || btnReviewWorker?.disabled) return;
    const taskId = currentTaskId;
    const request = {kind: "review", progressSeen: false};
    pendingTaskAction = request;
    stopVoicePreview();
    setWorkerSourceBusy(true);
    updateExportAvailability();
    updateTranscriptReviewAvailability();
    try {
      const response = await fetch(`/api/streaming/${encodeURIComponent(taskId)}/review`, {method: "POST"});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Chưa bắt đầu được bước AI kiểm tra lại.");
      if (currentTaskId !== taskId) return;
      if (!request.progressSeen) {
        currentProgress = null;
        showTaskProgress(data.progress || {status: "RUNNING", phase: "review", can_review: false, review_summary: {status: "running", checked: 0}});
      }
      if (!currentWs || currentWs.readyState !== WebSocket.OPEN) setupStreamingWebSocket(taskId);
    } catch (error) { alert(error.message); }
    finally {
      pendingTaskAction = null;
      setWorkerSourceBusy(!!currentTaskId && (reviewInProgress() || !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status)));
      updateExportAvailability();
      updateTranscriptReviewAvailability();
    }
  };
  btnReviewWorker?.addEventListener("click", window.studioReview);


  // =========================================================
  // 7. TASK MANAGER VIEW
  // =========================================================
  window.pauseTask = id => controlTask(id, "pause");
  window.resumeTask = id => controlTask(id, "resume");
  window.stopTask = id => controlTask(id, "stop");

  async function updateTasksTable() {
    const tbody = document.getElementById("tasks-table-body");
    if (!tbody || taskPollInFlight) return;
    taskPollInFlight = true;
    const taskIdAtRequest = currentTaskId;
    const revisionAtRequest = progressRevision;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);

    try {
      const res = await fetch("/api/tasks", { signal: controller.signal, cache: "no-store" });
      if (!res.ok) throw new Error("Không đọc được danh sách tác vụ");
      const data = await res.json();
      if (taskIdAtRequest !== currentTaskId || revisionAtRequest !== progressRevision) return;
      if (!Array.isArray(data.tasks)) throw new Error("Danh sách tác vụ không hợp lệ");
      const tasks = data.tasks;

      let activeTask = tasks.find(task => task.task_id === currentTaskId);
      const automaticExport = !exportingTaskId && tasks.find(task => task.task_id === `export_${currentTaskId}`);
      if (activeTask && automaticExport && ["RUNNING", "CANCELLING"].includes(automaticExport.status)) {
        automaticExportActive = true;
        activeTask = {...activeTask, phase: "export", status: automaticExport.status,
          progress_pct: automaticExport.progress_pct, stage: automaticExport.stage,
          output_video_url: "", output_filename: "", can_pause: false, can_resume: false, can_review: false, can_stop: false};
      } else if (automaticExportActive && automaticExport && ["COMPLETED", "FAILED", "CANCELLED"].includes(automaticExport.status)) {
        automaticExportActive = false;
        if (activeTask && automaticExport.status === "COMPLETED") activeTask = {...activeTask,
          output_video_url: automaticExport.output_video_url || automaticExport.video_url, output_filename: automaticExport.output_filename};
      }
      if (automaticExportActive && !automaticExport && activeTask && ["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(activeTask.status)) automaticExportActive = false;
      if (activeTask?.status === "COMPLETED" && automaticExport && ["FAILED", "CANCELLED"].includes(automaticExport.status)) {
        showExportFailure(automaticExport.stage, automaticExport.status);
        activeTask = {...activeTask, status: "FAILED", phase: "export_error", stage: automaticExport.stage || "Chưa xuất được video kết quả", progress_pct: null};
      }
      if (activeTask) {
        const statusChanged = activeTask.status !== currentProgress?.status;
        const progressChanged = ["phase", "stage", "progress_pct", "downloaded_bytes", "total_bytes", "speed", "eta", "can_retry", "can_pause", "can_resume", "can_stop", "can_review", "review_count", "output_video_url", "output_filename"]
          .some(key => activeTask[key] !== undefined && activeTask[key] !== currentProgress?.[key]) ||
          (activeTask.review_summary !== undefined && JSON.stringify(activeTask.review_summary) !== JSON.stringify(currentProgress?.review_summary));
        if (taskPollWarning || streamDisconnected || statusChanged || progressChanged) {
          showTaskProgress(activeTask);
          if (streamDisconnected && !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(activeTask.status)) {
            taskConnectionStatus.textContent = "Kết nối realtime bị gián đoạn. Đang lấy tiến độ mới mỗi 2 giây.";
            taskConnectionStatus.classList.remove("hidden");
          } else if (!streamDisconnected) {
            taskConnectionStatus.classList.add("hidden");
          }
        }
      } else if (currentTaskId && !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status)) {
        taskPollWarning = true;
        bufferingAlert.classList.add("hidden");
        playerDownloadProgress?.classList.add("hidden");
        taskConnectionStatus.textContent = "Không tìm thấy tác vụ trên máy chủ. Đang kiểm tra lại…";
        taskConnectionStatus.classList.remove("hidden");
        if (playerTaskStatus) {
          playerTaskStatus.textContent = "Không tìm thấy tác vụ trên máy chủ; chưa xác nhận tải xong.";
          playerTaskStatus.dataset.state = "disconnected";
          playerTaskStatus.classList.remove("hidden");
        }
      }

      if (tasks.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-gray-500 font-sans">${pendingStart ? "Đang gửi yêu cầu tạo tác vụ…" : currentTaskId && !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status) ? "Chưa nhận được trạng thái tác vụ từ máy chủ. Đang kiểm tra lại…" : "Không có tác vụ nào đang hoạt động."}</td></tr>`;
        return;
      }

      tbody.innerHTML = "";
      tasks.forEach(t => {
        const tr = document.createElement("tr");
        tr.className = "hover:bg-gray-800/40 transition";
        const pct = measuredProgress(t.progress_pct);
        const terminalWithoutProgress = pct === null && ["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(t.status);
        const progressLabel = pct === null ? "Chưa có số liệu %" : formatProgressPercent(pct);

        let statusBadge = "";
        if (t.status === "RUNNING") {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 text-[10px] animate-pulse">ĐANG CHẠY</span>`;
        } else if (t.status === "PAUSED" || t.status === "CANCELLING") {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800 text-[10px]">${t.status === "CANCELLING" ? "ĐANG DỪNG" : "TẠM DỪNG"}</span>`;
        } else if (t.status === "COMPLETED") {
          const resultAvailable = !!(t.output_video_url || t.video_url);
          statusBadge = `<span class="px-2 py-0.5 rounded bg-blue-950 text-blue-400 border border-blue-800 text-[10px]">${resultAvailable ? "ĐÃ XUẤT VIDEO" : "ĐÃ DỊCH · CHƯA XUẤT"}</span>`;
        } else if (t.status === "CANCELLED" || t.status === "STOPPED") {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700 text-[10px]">ĐÃ DỪNG</span>`;
        } else {
          statusBadge = `<span class="px-2 py-0.5 rounded bg-rose-950 text-rose-400 border border-rose-800 text-[10px]">LỖI</span>`;
        }

        let actionButtons = "";
        if (/^[a-zA-Z0-9_-]{1,80}$/.test(t.task_id) && !t.task_id.startsWith("export_") && t.can_open !== false && (t.saved || !["STOPPED", "CANCELLED"].includes(t.status))) {
          actionButtons += `<button onclick="window.openTaskInStudio('${t.task_id}')" class="px-2 py-1 bg-pink-600/80 hover:bg-pink-600 text-white rounded text-[10px]">${t.saved ? 'Mở lại để chỉnh' : 'Mở trong Studio'}</button> `;
        }
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
          actionButtons += `<a href="${t.video_url}" data-result-preview class="px-2 py-1 bg-pink-600 hover:bg-pink-500 text-white rounded text-[10px] inline-flex items-center gap-1"><i class="fa-solid fa-circle-play"></i>Xem kết quả</a> `;
        }

        tr.innerHTML = `
          <td class="p-3.5 font-bold text-white">${escapeHtml(t.title || t.task_id)}${t.title ? `<div class="text-[10px] text-gray-400 font-normal">${escapeHtml(t.task_id)}</div>` : ''}${t.updated_at ? `<div class="text-[10px] text-gray-400 font-normal">${escapeHtml(new Date(t.updated_at * 1000).toLocaleString('vi-VN'))}</div>` : ''}</td>
          <td class="p-3.5 text-pink-400 font-semibold">${escapeHtml(t.saved ? 'Phiên đã lưu' : t.task_type)}</td>
          <td class="p-3.5">
            <div class="flex items-center gap-2">
              ${terminalWithoutProgress ? "" : `<div class="w-24 bg-gray-800 h-2 rounded-full overflow-hidden">
                <div class="bg-gradient-to-r from-pink-500 to-violet-500 h-full transition-all duration-300" style="width: ${pct === null ? 100 : pct}%; opacity: ${pct === null ? 0.3 : 1}"></div>
              </div>`}
              <span class="text-[10px] text-gray-300 font-sans">${terminalWithoutProgress ? "" : `${progressLabel} · `}${escapeHtml(t.stage || '')}</span>
            </div>
          </td>
          <td class="p-3.5 text-gray-300">${formatTime(t.duration)}</td>
          <td class="p-3.5">${statusBadge}</td>
          <td class="p-3.5 text-right space-x-1">${actionButtons || '<span class="text-gray-500 text-[10px]">-</span>'}</td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {
      if (taskIdAtRequest !== currentTaskId || revisionAtRequest !== progressRevision) return;
      console.error("Error loading tasks:", e);
      tbody.innerHTML = '<tr><td colspan="6" class="p-6 text-center text-rose-300">Không thể tải danh sách tác vụ. Bấm Làm mới để thử lại.</td></tr>';
      if (currentTaskId && !["COMPLETED", "FAILED", "STOPPED", "CANCELLED"].includes(currentProgress?.status)) {
        taskPollWarning = true;
        bufferingAlert.dataset.state = "disconnected";
        taskConnectionStatus.textContent = "Không kết nối được máy chủ. Tiến độ hiển thị là trạng thái cuối đã nhận.";
        taskConnectionStatus.classList.remove("hidden");
        if (playerTaskStatus) {
          playerTaskStatus.textContent = "Không kết nối được máy chủ; đang giữ số liệu cuối đã nhận.";
          playerTaskStatus.dataset.state = "disconnected";
          playerTaskStatus.classList.remove("hidden");
        }
      }
    } finally {
      clearTimeout(timeout);
      taskPollInFlight = false;
    }
  }
  document.getElementById("btn-refresh-tasks")?.addEventListener("click", updateTasksTable);
  setInterval(() => {
    if (currentTaskId || !document.getElementById("view-tasks").classList.contains("hidden") || streamDisconnected) updateTasksTable();
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
          btnElement.innerHTML = '<i class="fa-solid fa-circle-check mr-1"></i> Đã có tệp';
        alert(`[Kiểm Tra Tệp Checkpoint]\nModel: ${data.model_name}\nDung lượng: ${data.size_mb} MB\nThiết bị: ${data.device}\nTrạng thái: ${data.status}\n\n${data.message}`);
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
        const badge = m.status === "RUNTIME_MISSING"
          ? `<span class="bg-amber-950 text-amber-400 border border-amber-800 px-2.5 py-1 rounded text-[10px] font-bold">ĐÃ TẢI · THIẾU RUNTIME</span>`
          : m.downloaded
          ? `<span class="bg-emerald-950 text-emerald-400 border border-emerald-800 px-2.5 py-1 rounded text-[10px] font-bold"><i class="fa-solid fa-circle-check mr-1"></i> ĐÃ TẢI</span>`
          : `<span class="bg-amber-950 text-amber-400 border border-amber-800 px-2.5 py-1 rounded text-[10px] font-bold">CHƯA TẢI</span>`;

        const safeName = m.model_name.replace(/'/g, "\\'");
        tr.innerHTML = `
          <td class="p-3.5 font-bold text-gray-100">${m.engine}</td>
          <td class="p-3.5 font-mono text-[11px] text-pink-300">${m.model_name}</td>
          <td class="p-3.5 font-mono text-gray-400">${m.size_mb} MB</td>
          <td class="p-3.5 font-mono text-cyan-400">${m.device}</td>
          <td class="p-3.5">${badge}<p class="mt-2 text-[10px] text-gray-400 max-w-xs">${escapeHtml(m.runtime_note || "Kiểm tra tệp không thay thế kiểm thử chạy mô hình.")}</p></td>
          <td class="p-3.5 text-right">
            <button class="px-2.5 py-1 rounded bg-gray-800 hover:bg-gray-700 text-[11px] text-gray-200 border border-gray-700 transition" onclick="window.verifyModel('${safeName}', this)">
              <i class="fa-solid fa-shield-halved mr-1"></i> Kiểm tra tệp
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
  let currentLogCategory = "pipeline";
  const logCategoryLabels = { pipeline: "Tác vụ", app: "Ứng dụng", ai: "AI", errors: "Lỗi" };
  const logConsole = document.getElementById("log-console-output");
  const chkAutoScroll = document.getElementById("chk-auto-scroll-log");
  const logSummary = document.getElementById("log-summary");
  const logCopyStatus = document.getElementById("log-copy-status");
  let rawLogText = "";
  let logLoadSequence = 0;
  let logRequestRunning = false;
  let logRefreshQueued = false;
  let logRequestController = null;
  let logPollTimer = null;
  let diagnosticsVisible = false;
  let renderedLogText = null;

  function setDiagnosticsVisible(visible) {
    diagnosticsVisible = visible;
    clearInterval(logPollTimer);
    logPollTimer = null;
    if (visible && !document.hidden) {
      loadDiagnosticsLog(currentLogCategory);
      logPollTimer = setInterval(() => loadDiagnosticsLog(currentLogCategory, { background: true }), 3000);
    } else {
      logLoadSequence++;
      logRefreshQueued = false;
      logRequestController?.abort();
    }
  }

  document.addEventListener("visibilitychange", () => setDiagnosticsVisible(diagnosticsVisible));
  window.addEventListener("pagehide", () => setDiagnosticsVisible(false));

  async function loadDiagnosticsLog(cat, { background = false } = {}) {
    if (background && (!diagnosticsVisible || document.hidden)) return;
    const categoryChanged = cat !== currentLogCategory;
    currentLogCategory = cat;
    if (!logConsole) return;
    if (categoryChanged) {
      logLoadSequence++;
      rawLogText = "";
      renderedLogText = null;
      logConsole.textContent = "Đang tải nhật ký…";
      logSummary.textContent = `Đang tải ${logCategoryLabels[cat]}…`;
      logCopyStatus.textContent = "";
      logRequestController?.abort();
    }
    // A slow local server must not accumulate polling requests. A category
    // change queues only the newest choice and invalidates the older response.
    if (logRequestRunning) {
      if (!background) logRefreshQueued = true;
      return;
    }
    logRequestRunning = true;
    const sequence = ++logLoadSequence;
    const controller = new AbortController();
    logRequestController = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    if (renderedLogText === null) logSummary.textContent = `Đang tải ${logCategoryLabels[cat]}…`;
    try {
      const res = await fetch(`/api/diagnostics/logs?category=${cat}&lines=150`, { signal: controller.signal });
      if (!res.ok) throw new Error("Máy chủ chưa trả nhật ký");
      const data = await res.json();
      if (sequence !== logLoadSequence) return;
      const nextLogText = String(data.logs || "");
      const selection = window.getSelection?.();
      // Keep selected DOM nodes intact while the user reads or copies a line.
      // The next poll applies the latest text after the selection is released.
      if (!chkAutoScroll?.checked && selection && !selection.isCollapsed
          && (logConsole.contains(selection.anchorNode) || logConsole.contains(selection.focusNode))) return;
      rawLogText = nextLogText;
      const lines = rawLogText.split(/\r?\n/);
      if (renderedLogText !== rawLogText) {
        const scrollTop = logConsole.scrollTop;
        const scrollLeft = logConsole.scrollLeft;
        logConsole.innerHTML = rawLogText ? lines.map(line => {
          const level = /\b(ERROR|CRITICAL|Exception|Traceback)\b/.test(line) ? "log-line-error" : /\bWARNING\b/.test(line) ? "log-line-warning" : "";
          return `<span class="${level}">${escapeHtml(line)}</span>`;
        }).join("\n") : (cat === "pipeline" ? "Chưa có nhật ký tác vụ. Nội dung sẽ cập nhật khi tải hoặc dịch video." : "Chưa có nhật ký trong mục này.");
        renderedLogText = rawLogText;
        if (!chkAutoScroll?.checked) {
          logConsole.scrollTop = scrollTop;
          logConsole.scrollLeft = scrollLeft;
        }
      }
      const errors = lines.filter(line => /\b(ERROR|CRITICAL)\b/.test(line)).length;
      const warnings = lines.filter(line => /\bWARNING\b/.test(line)).length;
      logSummary.textContent = `${logCategoryLabels[cat]} · ${rawLogText ? lines.filter(Boolean).length : 0} dòng gần nhất · ${errors} lỗi · ${warnings} cảnh báo`;
      if (chkAutoScroll && chkAutoScroll.checked) {
        logConsole.scrollTop = logConsole.scrollHeight;
      }
    } catch (e) {
      if (sequence !== logLoadSequence) return;
      if (renderedLogText === null) logConsole.textContent = "Chưa đọc được nhật ký.";
      logSummary.textContent = "Chưa cập nhật được nhật ký · đang thử lại.";
    } finally {
      clearTimeout(timeout);
      logRequestRunning = false;
      logRequestController = null;
      if (logRefreshQueued) {
        logRefreshQueued = false;
        loadDiagnosticsLog(currentLogCategory);
      }
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

  document.getElementById("btn-copy-log")?.addEventListener("click", async () => {
    if (!rawLogText) {
      logCopyStatus.textContent = "Chưa có nội dung log để sao chép.";
      return;
    }
    const copy = rawLogText;
    const category = currentLogCategory;
    try {
      let copied = false;
      if (typeof window.desktopBridge?.copyText === "function") {
        copied = await new Promise(resolve => window.desktopBridge.copyText(copy, result => resolve(result === true)));
      }
      if (!copied) {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard không sẵn sàng");
        await navigator.clipboard.writeText(copy);
      }
      logCopyStatus.textContent = `Đã sao chép log ${logCategoryLabels[category]}.`;
    } catch (_) {
      logCopyStatus.textContent = "Không truy cập được clipboard. Chọn nội dung trong khung nhật ký rồi nhấn Ctrl+C.";
      logConsole.focus();
    }
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
  function setDouyinCookiesBusy(busy) {
    douyinCookiesBusy = busy;
    douyinCookiesPanel?.setAttribute("aria-busy", String(busy));
    if (douyinCookiesFile) douyinCookiesFile.disabled = busy;
    if (btnImportDouyinCookies) btnImportDouyinCookies.disabled = busy;
    if (btnRefreshDouyinCookies) btnRefreshDouyinCookies.disabled = busy;
    if (btnDeleteDouyinCookies) btnDeleteDouyinCookies.disabled = busy || !douyinCookiesConfigured;
  }

  function showDouyinCookiesMessage(message, isError = false) {
    if (!douyinCookiesStatus) return;
    douyinCookiesStatus.textContent = message;
    douyinCookiesStatus.classList.toggle("text-rose-300", isError);
    douyinCookiesStatus.classList.toggle("text-gray-400", !isError);
  }

  function applyDouyinCookiesStatus(data) {
    if (typeof data?.configured !== "boolean" || !Number.isInteger(data.count) || data.count < 0) {
      throw new Error("invalid cookie status");
    }
    douyinCookiesConfigured = data.stored === true || data.configured;
    douyinCookiesState.textContent = data.configured
      ? `Đã nhập ${data.count} cookie Douyin trên máy này.`
      : data.stored === true
        ? "Cookie đã lưu hết hạn hoặc không còn hợp lệ. Nhập lại hoặc xóa bản cũ."
        : "Chưa có cookie Douyin đã nhập.";
    btnDeleteDouyinCookies.disabled = douyinCookiesBusy || !douyinCookiesConfigured;
  }

  async function refreshDouyinCookies() {
    if (!douyinCookiesStatus || douyinCookiesBusy) return;
    const version = ++douyinCookiesVersion;
    btnRefreshDouyinCookies.disabled = true;
    showDouyinCookiesMessage("Đang kiểm tra cookie đã lưu trên máy…");
    try {
      const response = await fetch("/api/douyin/cookies", { cache: "no-store" });
      if (!response.ok) throw new Error("cookie status unavailable");
      const data = await response.json();
      if (version !== douyinCookiesVersion) return;
      applyDouyinCookiesStatus(data);
      showDouyinCookiesMessage(data.configured
        ? "Đã có cookie để thử tải. Chạy lại link Douyin để kiểm tra phiên còn dùng được."
        : data.stored === true
          ? "Nhập tệp mới hoặc xóa phiên cũ trước khi thử lại."
          : "Chọn tệp cookie Douyin để nhập. Đăng nhập Chrome không tự cập nhật mục này.");
    } catch (_) {
      if (version === douyinCookiesVersion) {
        showDouyinCookiesMessage("Không kiểm tra được trạng thái cookie. Bấm Kiểm tra trạng thái để thử lại.", true);
      }
    } finally {
      if (version === douyinCookiesVersion) btnRefreshDouyinCookies.disabled = false;
    }
  }

  async function mutateDouyinCookies(method, file) {
    if (douyinCookiesBusy) return;
    const version = ++douyinCookiesVersion;
    setDouyinCookiesBusy(true);
    const importing = method === "POST";
    showDouyinCookiesMessage(importing ? "Đang nhập cookie Douyin…" : "Đang xóa bản cookie đã nhập…");
    try {
      const options = { method, headers: { "X-Studio-Request": "douyin-cookies" } };
      if (importing) {
        options.body = new FormData();
        options.body.append("file", file, "douyin-cookies.txt");
      }
      const response = await fetch("/api/douyin/cookies", options);
      if (!response.ok) {
        showDouyinCookiesMessage(importing && [400, 413, 415, 422].includes(response.status)
          ? "Không nhập được tệp. Chọn bản Netscape cookies .txt hợp lệ, chỉ xuất từ Douyin và không quá 1 MiB. Cookie đã lưu trước đó được giữ nguyên."
          : importing
            ? "Không nhập được cookie. Cookie đã lưu trước đó được giữ nguyên; hãy thử lại."
            : "Chưa xác nhận xóa được cookie. Bấm Kiểm tra trạng thái trước khi thử lại.", true);
        return;
      }
      const data = await response.json();
      if (version !== douyinCookiesVersion) return;
      applyDouyinCookiesStatus(data);
      showDouyinCookiesMessage(importing
        ? "Đã nhập cookie. Chạy lại link Douyin để kiểm tra tải video; phiên đăng nhập chưa được xác minh."
        : "Đã xóa bản cookie khỏi Studio. Phiên đăng nhập trong Chrome vẫn giữ nguyên.");
    } catch (_) {
      showDouyinCookiesMessage(importing
        ? "Chưa xác nhận nhập được cookie. Bấm Kiểm tra trạng thái rồi thử lại."
        : "Chưa xác nhận xóa được cookie. Bấm Kiểm tra trạng thái rồi thử lại.", true);
    } finally {
      if (douyinCookiesFile) douyinCookiesFile.value = "";
      if (version === douyinCookiesVersion) setDouyinCookiesBusy(false);
    }
  }

  btnImportDouyinCookies?.addEventListener("click", () => {
    if (!douyinCookiesBusy) douyinCookiesFile.click();
  });
  douyinCookiesFile?.addEventListener("change", async () => {
    const file = douyinCookiesFile.files?.[0];
    if (!file || douyinCookiesBusy) {
      douyinCookiesFile.value = "";
      return;
    }
    if (!/\.txt$/i.test(file.name) || file.size <= 0 || file.size > 1024 * 1024) {
      ++douyinCookiesVersion;
      btnRefreshDouyinCookies.disabled = false;
      showDouyinCookiesMessage("Chọn tệp Netscape cookies .txt có nội dung, tối đa 1 MiB. Cookie đã lưu trước đó được giữ nguyên.", true);
      douyinCookiesFile.value = "";
      return;
    }
    await mutateDouyinCookies("POST", file);
  });
  btnRefreshDouyinCookies?.addEventListener("click", refreshDouyinCookies);
  btnDeleteDouyinCookies?.addEventListener("click", async () => {
    if (douyinCookiesConfigured) await mutateDouyinCookies("DELETE");
  });

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
      llm_provider: settingsLlmProvider?.value || "opencode",
      openrouter_model: settingsOpenRouterModel?.value.trim() || "inclusionai/ling-3.0-flash-sante:free",
      opencode_model: settingsOpenCodeModel?.value || "muse-spark-1.3-contributor-free",
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
  function updateExportScreenTextSummary() {
    const summary = document.getElementById("export-screen-text-summary");
    if (!summary) return;
    if (captionStyle.position !== "auto" || captionStyle.background_color || captionStyle.text_color !== "#000000" || captionStyle.blur_original) {
      const positions = {auto: "tự động", top: "phía trên", middle: "ở giữa", bottom: "phía dưới"};
      summary.textContent = `Phụ đề ${positions[captionStyle.position] || "tự động"}, màu chữ ${captionStyle.text_color}, nền ${captionStyle.background_color || "tự động"}. ${captionStyle.blur_original ? "Làm mờ vùng sub gốc đã nhận diện." : "Giữ nguyên sub gốc."} Xuất đúng kiểu đang xem trước, giữ nguyên giọng đọc.`;
      return;
    }
    summary.textContent = toggleScreenText.checked
      ? "Tự đặt vị trí: giữ sub Trung, lời Việt nền vàng ở ngay dưới; không có sub gốc thì dùng ô trắng nhỏ phía dưới. Mỗi lượt thoại chỉ hiện khi bắt đầu đọc."
      : "Sub Việt nhỏ ở phía dưới. Giữ nguyên chữ và hình nguồn; mỗi lượt thoại hiện cùng giọng đọc.";
  }
  btnExportHQ.addEventListener("click", () => {
    if (btnExportHQ.disabled) return;
    if (!currentTaskId) {
      alert("Chưa có session video nào đang chạy!");
      return;
    }
    exportModal.classList.remove("hidden");
    updateExportScreenTextSummary();
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
    if (!currentTaskId || exportingTaskId || btnExportHQ.disabled) return;
    const taskId = currentTaskId;
    exportingTaskId = taskId;
    updateExportAvailability();
    updateTranscriptReviewAvailability();
    exportCancelled = false;
    btnCancelExport.disabled = false;
    btnConfirmExport.classList.add("hidden");
    exportStatusBox.classList.remove("hidden");
    exportResultBox.classList.add("hidden");
    renderExportProgress(null, "Đang chuẩn bị âm thanh, phụ đề và xuất video...");

    // Ignore reads from a completed run, including responses already in flight.
    let pollActive = true;
    let pollController = null;
    const stopExportPolling = () => {
      pollActive = false;
      clearInterval(pollInterval);
      pollController?.abort();
    };
    const pollInterval = setInterval(async () => {
      if (!pollActive || pollController) return;
      const controller = new AbortController();
      pollController = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const sRes = await fetch(`/api/streaming/export-hq/status/${taskId}`, {signal: controller.signal, cache: "no-store"});
        if (sRes.ok) {
          const sData = await sRes.json();
          if (!pollActive || taskId !== currentTaskId || exportingTaskId !== taskId) return;
          renderExportProgress(sData.progress, sData.stage);
          if (["COMPLETED", "FAILED", "CANCELLED"].includes(sData.status)) stopExportPolling();
        }
      } catch (_) {} finally {
        clearTimeout(timeout);
        if (pollController === controller) pollController = null;
      }
    }, 1000);

    try {
      const res = await fetch("/api/streaming/export-hq", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId, mask_chinese: false,
          translate_screen_text: toggleScreenText.checked })
      });
      stopExportPolling();
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Lỗi xuất video");

      renderExportProgress(100, "Xuất video hoàn tất");
      exportStatusBox.classList.add("hidden");
      exportResultBox.classList.remove("hidden");
      lastExportedFileUrl = data.video_url;
      captionOutputOutdated = false;
      showTaskResult({output_video_url: data.video_url, output_filename: data.output_filename});
      updateTasksTable();

      // Native desktop notification if available
      if (window.desktopBridge && typeof window.desktopBridge.showNotification === "function") {
        window.desktopBridge.showNotification(
          "Xuất Video Hoàn Tất",
          `Video TikTok 9:16 '${data.output_filename}' đã sẵn sàng!`
        );
      }

    } catch (e) {
      stopExportPolling();
      exportStatusBox.classList.remove("hidden");
      exportStatusText.textContent = exportCancelled ? "Đã hủy xuất video." : `Lỗi xuất video: ${e.message}`;
      btnConfirmExport.classList.remove("hidden");
    } finally {
      exportingTaskId = null;
      updateExportAvailability();
      updateTranscriptReviewAvailability();
      btnCancelExport.disabled = true;
      updateTasksTable();
    }
  });

  async function saveResultVideo(rawUrl = lastExportedFileUrl) {
    const origin = `${window.location.protocol}//${window.location.host}`;
    let url, defaultName;
    try {
      url = new URL(rawUrl, origin);
      defaultName = decodeURIComponent(url.pathname.split("/").pop());
    } catch (_) { return; }
    if (url.origin !== origin || !/^\/api\/outputs\/[^/]+\.mp4$/i.test(url.pathname)
        || url.search || url.hash || /[\\/:\x00-\x1f]/.test(defaultName)) return;
    if (window.desktopBridge && typeof window.desktopBridge.saveVideoAs === "function") {
      const targetPath = await new Promise(resolve => window.desktopBridge.saveVideoAs(defaultName, resolve));
      if (targetPath) {
        alert(`Đã lưu file thành công tại:\n${targetPath}`);
      }
    } else {
      const a = document.createElement("a");
      a.href = url.href;
      a.download = defaultName;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
  }
  btnSaveAsNative?.addEventListener("click", () => saveResultVideo());
  btnSaveResult?.addEventListener("click", () => saveResultVideo());
  document.getElementById("btn-save-preview-result")?.addEventListener("click", () => {
    if (resultPreviewActive && resultPreviewState) saveResultVideo(resultPreviewState.outputUrl);
  });

  loadSettingsForm();
  refreshDouyinCookies();
});
