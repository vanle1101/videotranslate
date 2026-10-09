# Douyin2TikTok AI Studio

> **Windows / 16 GB profile:** run `setup.bat` once, then `start.bat`. Defaults use Whisper Small on CPU int8, Edge-TTS and DSP separation. Premium engines described below require separate installation. See the [Windows installation and operating guide](docs/windows-guide.md).

> **Translation:** the default is **OpenCode Zen · Muse Spark Free** (`muse-spark-1.3-contributor-free`) through the OpenCode session connected on the machine. OpenRouter Free, Gemini, DeepSeek, and Google/MyMemory remain selectable alternatives; the app does not silently switch to a paid model when a provider fails. Keys are never committed or returned to the settings UI. Test the connection in Settings.

<div align="center">

![Douyin2TikTok AI Studio Banner](docs/images/banner.png)

[![Windows Desktop](https://img.shields.io/badge/Platform-Windows%2010%2B-blue?logo=windows&style=flat-square)](https://github.com/vanle1101/videotranslate)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-green?logo=python&style=flat-square)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6%20%7C%20QtWebEngine-purple?logo=qt&style=flat-square)](https://wiki.qt.io/Qt_for_Python)
[![PyTorch CUDA](https://img.shields.io/badge/CUDA-12.4%20%7C%20Torch%202.6-red?logo=pytorch&style=flat-square)](https://pytorch.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

[Tiếng Việt](README.md) | **English**

</div>

**Douyin2TikTok AI Studio** is a Windows application for Chinese-to-Vietnamese video translation, dubbing and subtitles. The configured defaults use Whisper Small, OpenCode Muse, Edge-TTS and DSP vocal suppression. Recognition, translation and separation quality depend on the source and provider.

Paste shared text containing a video URL or select an MP4 to prepare a short preview. Choose full translation to process subsequent chunks; earlier completed rows remain editable. Recognition and independent Muse checks can take minutes. Failed or uncertain rows remain visible for retry, and full export requires valid data. The [current runtime QA report](docs/RUNTIME_QA_REPORT.md) records verified scenarios and remaining failures.

---

## ⚡ Key Highlights

| Incremental preview | Original voice suppression and BGM | Vietnamese dubbing |
|---|---|---|
| Play completed speech and subtitle chunks; saved checkpoints allow retry after failure. | DSP reduces the original voice. Optional BS-RoFormer requires separate installation; neither guarantees complete voice removal or unchanged SFX. | Edge-TTS is the default. Local engines are optional. Actual WAV measurements govern fitting, with a 1.15× speed ceiling and no speech truncation. |

| Chinese speech recognition | Muse translation and review | MP4 export and subtitle controls |
|---|---|---|
| Whisper Small runs on CPU; optional SenseVoice requires its model. Source ASR timing remains available for evidence review. | Muse translates with surrounding dialogue and independently checks meaning. Pacing rewrites also require a separate Vietnamese fluency check. | Preserve source dimensions; edit subtitle colors and placement, and optionally blur recognized original subtitle regions. |

---

## 📸 Desktop Studio Screenshots

### 1. Realtime Streaming Player & Telemetry Ribbon
![Studio Dashboard](docs/images/studio_dashboard.png)

### 2. AI Model Management Panel
![Models Management Tab](docs/images/models_tab.png)

### 3. Engine Settings & Audio Ducking Configuration
![Settings Tab](docs/images/settings_tab.png)

---

## 🚀 Quick Start (1-Click)

Double click the Windows launcher:
👉 **`Start Douyin2TikTok AI Studio.bat`**

No command lines, no black terminal windows, no localhost in a browser. Launches directly as a native Windows desktop program with system tray controls and drag-and-drop support.

---

## 📄 License
Distributed under the **MIT License**. Free for personal and commercial applications.
