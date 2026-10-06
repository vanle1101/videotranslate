# Piper Vietnamese voices

The optional Piper provider supplies five CPU voices: Ngọc Lan, Minh Anh,
Quang Huy, Thu Hà and Yến Nhi. The model produces 22,050 Hz mono speech.
The desktop voice selector uses these presets through a shared ONNX session
with four CPU threads. It does not load Torch or require a cloud account.

## Installation

Reuse the project environment and its existing ONNX Runtime:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-piper.txt
.\venv\Scripts\python.exe -B scripts/download_piper.py --download
```

The Windows wheel is available for Python 3.9 and newer, including Python 3.12.
Launch Studio from its project shortcut: eSpeak uses a relative data path on
Windows when the project path contains Vietnamese characters. No duplicate
copy of the phonemizer data is created.
The model and configuration use 77,105,908 bytes in `workspace/models/piper_vi`.
The downloader resumes partial files and verifies both size and SHA-256.
Without `--download`, it only displays the download plan.

Source: [CakeByVPBank/piper-pgl-v4-vi_VN-version39_epoch39](https://huggingface.co/CakeByVPBank/piper-pgl-v4-vi_VN-version39_epoch39),
revision `8ea50134bea762f6a1faac671e1a33c41871289c`.
Asset filenames and checksums are pinned in `core/engines/tts/piper_engine.py`.

## Voice characteristics

| Preset | Voice | Region | Character |
| --- | --- | --- | --- |
| 0 | Ngọc Lan | North | Female, general reading |
| 1 | Minh Anh | North | Female, firm register |
| 2 | Quang Huy | North | Male, light tone |
| 3 | Thu Hà | North | Female, guidance |
| 4 | Yến Nhi | South | Female, darker timbre |

The model has five fixed speakers and does not support voice cloning. Its
training data is synthetic. Upstream notes that Yến Nhi has less high-frequency
detail than the other voices because of the reference recording.
Vietnamese numbers, dates and units are normalized with the already used
`sea-g2p` package before eSpeak phonemization. This is not the upstream banking
application's specialized brand dictionary; brand names and foreign words can
sound accented. Preview the selected voice before exporting a long video.

## Verification on this Windows installation

All five presets generated playable mono WAV files at 22,050 Hz with the CPU
runtime. A shared-model run took about 2.7 seconds for initialization plus the
first sample, then 0.26–0.45 seconds per sample of 3.6–5.6 seconds of audio.
These are short-sample measurements, not a guarantee for every input.

An independent Whisper Small read-back recovered the test sentence exactly for
Minh Anh and Quang Huy. It made one word error for Ngọc Lan and several for
Thu Hà and Yến Nhi. A second sample for the latter two remained intelligible to
the recognizer but also contained word errors. Recognition is an imperfect
quality check: listen to the preview and review names and important wording.
These five presets are additional options; they do not replace the default
voice or imply the same quality as VieNeu Turbo.

## Licenses and attribution

- The [Cake model card](https://huggingface.co/CakeByVPBank/piper-pgl-v4-vi_VN-version39_epoch39/blob/8ea50134bea762f6a1faac671e1a33c41871289c/README.md)
  distributes the model weights under MIT.
- [Piper 1.8.0](https://pypi.org/project/piper-tts/1.8.0/) is GPL-3.0-or-later.
  Its bundled runtime and eSpeak notices remain in the installed package.
  Distributing an application with this optional runtime requires complying
  with its license, independently of the model weights' MIT license.
- Model attribution: Piper TTS by Michael Hansen,
  [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices),
  [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl), and Cake by VPBank.
- Vietnamese text normalization: [sea-g2p](https://github.com/pnnbao97/sea-g2p).
