import os
import sys
import time
import json
import subprocess
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from core.engines.separator.roformer_engine import BSRoFormerSeparator

COMPARISON_DIR = Path("workspace/temp/comparisons")
COMPARISON_DIR.mkdir(parents=True, exist_ok=True)

VIDEOS = [
    {
        "id": "video_1_family_dinner",
        "file": Path("workspace/inputs/real_chinese_1.mp4.webm"),
        "desc": "Family Dinner (Female/Child dialogue, dining cutlery Foley, kitchen ambience)"
    },
    {
        "id": "video_2_street_interview",
        "file": Path("workspace/inputs/real_chinese_street.mp4.webm"),
        "desc": "Street Interview (Male voice, outdoor street traffic, city ambience, cars)"
    },
    {
        "id": "video_3_dialogue_music",
        "file": Path("workspace/inputs/real_chinese_3.mp4.webm"),
        "desc": "Casual Street Dialogue (Male & female dialogue, background music & footsteps)"
    }
]

def extract_10s_slice(video_path: Path, output_wav: Path):
    """Extracts first 10 seconds of audio as 44.1kHz 16-bit WAV stereo."""
    cmd = [
        "ffmpeg", "-y", "-ss", "0", "-t", "10",
        "-i", str(video_path),
        "-vn", "-ac", "2", "-ar", "44100",
        "-c:a", "pcm_s16le", str(output_wav)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def bandpass_energy(audio: np.ndarray, sr: int, low_freq: float, high_freq: float) -> float:
    """Computes RMS energy within a specific frequency band using Butterworth filter."""
    nyq = 0.5 * sr
    low = max(0.001, low_freq / nyq)
    high = min(0.999, high_freq / nyq)
    sos = butter(4, [low, high], btype='bandpass', output='sos')
    
    # Process each channel and sum energy
    if audio.ndim == 1:
        filtered = sosfilt(sos, audio)
        return float(np.mean(filtered**2))
    else:
        filtered_0 = sosfilt(sos, audio[:, 0])
        filtered_1 = sosfilt(sos, audio[:, 1])
        return float((np.mean(filtered_0**2) + np.mean(filtered_1**2)) / 2.0)

def compute_metrics(orig_wav: Path, proc_wav: Path) -> dict:
    """
    Computes objective acoustic metrics:
    - Speech band leakage (300Hz - 3400Hz): ratio & dB reduction vs original
    - BGM retention (Bass foundation: 40Hz - 250Hz): percentage of original retained
    - SFX retention (High transients/Foley: 3800Hz - 14000Hz): percentage of original retained
    """
    orig_data, sr_o = sf.read(str(orig_wav), dtype='float32')
    proc_data, sr_p = sf.read(str(proc_wav), dtype='float32')
    
    # Match length
    min_len = min(len(orig_data), len(proc_data))
    orig_data = orig_data[:min_len]
    proc_data = proc_data[:min_len]
    
    # 1. Speech band energy (300Hz - 3400Hz)
    e_speech_orig = bandpass_energy(orig_data, sr_o, 300, 3400)
    e_speech_proc = bandpass_energy(proc_data, sr_p, 300, 3400)
    speech_leakage_ratio = e_speech_proc / max(e_speech_orig, 1e-9)
    speech_reduction_db = 10 * np.log10(max(speech_leakage_ratio, 1e-9))
    
    # 2. BGM Bass foundation (40Hz - 250Hz)
    e_bass_orig = bandpass_energy(orig_data, sr_o, 40, 250)
    e_bass_proc = bandpass_energy(proc_data, sr_p, 40, 250)
    bgm_retention = min(100.0, (e_bass_proc / max(e_bass_orig, 1e-9)) * 100.0)
    
    # 3. SFX / Foley Highs (3800Hz - 14000Hz)
    e_sfx_orig = bandpass_energy(orig_data, sr_o, 3800, 14000)
    e_sfx_proc = bandpass_energy(proc_data, sr_p, 3800, 14000)
    sfx_retention = min(100.0, (e_sfx_proc / max(e_sfx_orig, 1e-9)) * 100.0)
    
    return {
        "speech_reduction_db": round(float(speech_reduction_db), 1),
        "speech_leakage_pct": round(float(speech_leakage_ratio * 100.0), 1),
        "bgm_retention_pct": round(float(bgm_retention), 1),
        "sfx_retention_pct": round(float(sfx_retention), 1)
    }

def main():
    print("=" * 80)
    print("BENCHMARK: REAL DOUYIN CHINESE AUDIO VOCAL SUPPRESSION")
    print("Engines evaluated: DSP Stereo Cancel vs DSP Mono Formant vs Auto DSP vs BS-RoFormer HQ")
    print("=" * 80)
    
    suppressor = RealtimeVocalSuppressor()
    roformer = BSRoFormerSeparator()
    
    all_results = []
    
    for vid_info in VIDEOS:
        vid_id = vid_info["id"]
        vid_path = vid_info["file"]
        desc = vid_info["desc"]
        
        print(f"\n[{vid_id.upper()}] Processing: {desc}")
        print(f"File: {vid_path}")
        
        target_dir = COMPARISON_DIR / vid_id
        target_dir.mkdir(parents=True, exist_ok=True)
        
        orig_10s = target_dir / "original_10s.wav"
        dsp_stereo_10s = target_dir / "dsp_stereo_cancel_10s.wav"
        dsp_mono_10s = target_dir / "dsp_mono_formant_10s.wav"
        auto_10s = target_dir / "auto_realtime_10s.wav"
        roformer_10s = target_dir / "roformer_hq_10s.wav"
        
        # Step 1: Extract 10-second reference
        extract_10s_slice(vid_path, orig_10s)
        print(f"  -> Extracted 10s slice: {orig_10s.name}")
        
        # Step 2: Analyze audio properties
        audio_props = suppressor.analyze_audio_properties(orig_10s)
        corr = audio_props["correlation"]
        side_ratio = audio_props["side_ratio"]
        auto_mode = audio_props["mode"]
        print(f"  -> Audio Profile: Correlation r_LR={corr:.4f}, Side Energy={side_ratio:.2%}")
        print(f"  -> Auto Selected Mode: {auto_mode} ({audio_props['reason']})")
        
        # Step 3: Run DSP Stereo Center Cancel (Forced)
        t0 = time.time()
        suppressor.process_file(orig_10s, dsp_stereo_10s, forced_mode="DSP_STEREO_CENTER_CANCEL")
        t_stereo = time.time() - t0
        rtf_stereo = round(10.0 / max(t_stereo, 0.001), 1)
        m_stereo = compute_metrics(orig_10s, dsp_stereo_10s)
        
        # Step 4: Run DSP Mono Adaptive Formant (Forced)
        t0 = time.time()
        suppressor.process_file(orig_10s, dsp_mono_10s, forced_mode="DSP_MONO_ADAPTIVE_FORMANT")
        t_mono = time.time() - t0
        rtf_mono = round(10.0 / max(t_mono, 0.001), 1)
        m_mono = compute_metrics(orig_10s, dsp_mono_10s)
        
        # Step 5: Run Auto Mode
        t0 = time.time()
        auto_res = suppressor.process_file(orig_10s, auto_10s)
        t_auto = time.time() - t0
        rtf_auto = round(10.0 / max(t_auto, 0.001), 1)
        m_auto = compute_metrics(orig_10s, auto_10s)
        
        # Step 6: Run BS-RoFormer HQ
        t0 = time.time()
        voc_p, inst_p = roformer.separate(orig_10s, target_dir)
        # Rename separated instrumental to roformer_hq_10s.wav
        if inst_p.exists() and inst_p != roformer_10s:
            import shutil
            shutil.copyfile(inst_p, roformer_10s)
        t_roformer = time.time() - t0
        rtf_roformer = round(10.0 / max(t_roformer, 0.001), 2)
        m_roformer = compute_metrics(orig_10s, roformer_10s)
        
        vid_result = {
            "id": vid_id,
            "description": desc,
            "correlation": corr,
            "side_ratio": side_ratio,
            "auto_mode": auto_mode,
            "engines": {
                "DSP_Stereo_Cancel": {
                    "rtf": f"{rtf_stereo}x",
                    "latency_sec": round(t_stereo, 3),
                    "speech_reduction_db": m_stereo["speech_reduction_db"],
                    "speech_leakage_pct": m_stereo["speech_leakage_pct"],
                    "bgm_retention_pct": m_stereo["bgm_retention_pct"],
                    "sfx_retention_pct": m_stereo["sfx_retention_pct"],
                    "file": str(dsp_stereo_10s)
                },
                "DSP_Mono_Formant": {
                    "rtf": f"{rtf_mono}x",
                    "latency_sec": round(t_mono, 3),
                    "speech_reduction_db": m_mono["speech_reduction_db"],
                    "speech_leakage_pct": m_mono["speech_leakage_pct"],
                    "bgm_retention_pct": m_mono["bgm_retention_pct"],
                    "sfx_retention_pct": m_mono["sfx_retention_pct"],
                    "file": str(dsp_mono_10s)
                },
                "Auto_DSP_Realtime": {
                    "rtf": f"{rtf_auto}x",
                    "latency_sec": round(t_auto, 3),
                    "speech_reduction_db": m_auto["speech_reduction_db"],
                    "speech_leakage_pct": m_auto["speech_leakage_pct"],
                    "bgm_retention_pct": m_auto["bgm_retention_pct"],
                    "sfx_retention_pct": m_auto["sfx_retention_pct"],
                    "file": str(auto_10s)
                },
                "BS_RoFormer_HQ": {
                    "rtf": f"{rtf_roformer}x",
                    "latency_sec": round(t_roformer, 2),
                    "speech_reduction_db": m_roformer["speech_reduction_db"],
                    "speech_leakage_pct": m_roformer["speech_leakage_pct"],
                    "bgm_retention_pct": m_roformer["bgm_retention_pct"],
                    "sfx_retention_pct": m_roformer["sfx_retention_pct"],
                    "file": str(roformer_10s)
                }
            }
        }
        all_results.append(vid_result)
        
        print(f"  [+] DSP Stereo: Speech Red={m_stereo['speech_reduction_db']}dB | BGM Ret={m_stereo['bgm_retention_pct']}% | SFX Ret={m_stereo['sfx_retention_pct']}% | RTF={rtf_stereo}x")
        print(f"  [+] DSP Mono:   Speech Red={m_mono['speech_reduction_db']}dB | BGM Ret={m_mono['bgm_retention_pct']}% | SFX Ret={m_mono['sfx_retention_pct']}% | RTF={rtf_mono}x")
        print(f"  [+] Auto Mode:  Speech Red={m_auto['speech_reduction_db']}dB | BGM Ret={m_auto['bgm_retention_pct']}% | SFX Ret={m_auto['sfx_retention_pct']}% | RTF={rtf_auto}x")
        print(f"  [+] BS-RoFormer: Speech Red={m_roformer['speech_reduction_db']}dB | BGM Ret={m_roformer['bgm_retention_pct']}% | SFX Ret={m_roformer['sfx_retention_pct']}% | RTF={rtf_roformer}x")

    summary_file = COMPARISON_DIR / "real_audio_benchmark_results.json"
    summary_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"\n[+] Benchmark completed! Full JSON results saved to: {summary_file}")

if __name__ == "__main__":
    main()
