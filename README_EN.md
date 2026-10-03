# Douyin2TikTok AI Studio

<div align="center">

![Douyin2TikTok AI Studio Banner](docs/images/banner.png)

[![Windows Desktop](https://img.shields.io/badge/Platform-Windows%2010%2B-blue?logo=windows&style=flat-square)](https://github.com/vanle1101/videotranslate)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-green?logo=python&style=flat-square)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6%20%7C%20QtWebEngine-purple?logo=qt&style=flat-square)](https://wiki.qt.io/Qt_for_Python)
[![PyTorch CUDA](https://img.shields.io/badge/CUDA-12.4%20%7C%20Torch%202.6-red?logo=pytorch&style=flat-square)](https://pytorch.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

[Tiếng Việt](README.md) | **English**

</div>

**Douyin2TikTok AI Studio** is a standalone Windows desktop application designed for **real-time / near-realtime** Chinese-to-Vietnamese video translation, Chinese vocal suppression, background music/SFX retention, emotional Vietnamese dubbing, and 9:16 vertical video rendering for TikTok, Reels, and Shorts.

Simply paste a video URL or drag-and-drop an MP4 file. After buffering for just a few seconds, playback begins immediately with synchronized Vietnamese subtitles and voiceover, while the backend continuously processes upcoming segments ahead of playback.

---

## ⚡ Key Highlights

| Realtime Streaming Buffer | Chinese Vocal Removal & BGM Retention | Vietnamese AI Dubbing (VieNeu-TTS) |
|---|---|---|
| **Zero waiting for full renders:** Watch and listen while upcoming segments process ahead (+45s buffer). | **Up to -26dB suppression:** Realtime DSP center-cancellation + BS-RoFormer HQ export, preserving 100% of BGM & SFX. | **Natural human prosody:** 8B-parameter VieNeu-TTS v3 Turbo with automated atempo time-stretching. |

| Ultra-fast Chinese ASR (SenseVoice) | 3-Tier Semantic Translation (Gemini) | 9:16 Vertical Video & Hardcoded Sub Masking |
|---|---|---|
| **FunAudioLLM SenseVoice:** High-accuracy ASR in <0.3s with speaker emotion classification. | **VideoLingo Duration Budgeting:** 3-tier translation (*Literal ➔ Natural ➔ Time-Budget*) tailored for TikTok pacing. | **Frosted glass blur:** Elegantly masks Chinese burned subtitles and overlays stylized high-contrast ASS captions. |

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
