"""Benchmark the real upload-to-validated-MP4 pipeline without opening a UI.

This submits a local file through the same handler used by the Studio, then
records progress and host/GPU resource samples until a validated MP4 is ready.
It never removes source, cache, checkpoint, or output files.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return float(result.stdout.strip())


def _gpu_sample():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=3,
        )
        rows = []
        for line in result.stdout.splitlines():
            parts = [value.strip() for value in line.split(",")]
            if len(parts) == 3:
                rows.append({"utilization_percent": int(parts[0]),
                             "memory_used_mib": int(parts[1]),
                             "memory_total_mib": int(parts[2])})
        return rows or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _resource_sample(root_pid):
    try:
        import psutil
    except ImportError:
        psutil = None
    sample = {"at_monotonic": time.monotonic(), "gpu": _gpu_sample()}
    if psutil is None:
        return sample
    sample["system_cpu_percent"] = psutil.cpu_percent(None)
    memory = psutil.virtual_memory()
    sample["system_ram_used_percent"] = memory.percent
    sample["system_ram_available_bytes"] = memory.available
    processes = {}
    try:
        root = psutil.Process(root_pid)
        for process in [root, *root.children(recursive=True)]:
            processes[process.pid] = process
    except psutil.Error:
        pass
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = (process.info.get("name") or "").lower()
            if name.startswith("ollama") or "llama-server" in name:
                processes[process.info["pid"]] = process
        except psutil.Error:
            continue
    cpu, rss, details = 0., 0, []
    for process in processes.values():
        try:
            process_cpu = process.cpu_percent(None)
            cpu += process_cpu
            info = process.memory_info()
            rss += info.rss
            details.append({"name": process.name(), "cpu_percent": round(process_cpu, 1),
                            "rss_bytes": info.rss})
        except psutil.Error:
            continue
    sample["pipeline_process_cpu_percent"] = round(cpu, 1)
    sample["pipeline_process_rss_bytes"] = rss
    sample["processes"] = details
    return sample


def _segment_counts(session):
    counts = {}
    for row in session.segments.values():
        state = str(getattr(row, "status", "UNKNOWN"))
        counts[state] = counts.get(state, 0) + 1
    return counts


def _summarize_request_intervals(requests):
    """Separate summed request latency from the wall time requests occupy."""
    intervals = [(row.get("started_elapsed_seconds"), row.get("finished_elapsed_seconds"))
        for row in requests]
    intervals = [(start, end) for start, end in intervals
        if type(start) in (int, float) and type(end) in (int, float) and end >= start]
    if not intervals:
        return {"concurrent_wall_seconds": 0., "peak_in_flight": 0,
                "mean_in_flight": None}
    events = sorted([(start, 1) for start, _ in intervals]
                    + [(end, -1) for _, end in intervals], key=lambda event: (event[0], event[1]))
    active = peak = 0
    for _at, delta in events:
        active += delta
        peak = max(peak, active)
    ordered = sorted(intervals)
    union, left, right = 0., None, None
    for start, end in ordered:
        if left is None:
            left, right = start, end
        elif start > right:
            union += right - left
            left, right = start, end
        else:
            right = max(right, end)
    union += right - left
    summed = sum(end - start for start, end in intervals)
    return {"concurrent_wall_seconds": round(union, 3), "peak_in_flight": peak,
            "mean_in_flight": round(summed / union, 3) if union > 0 else None}


async def _run(source: Path, timeout_seconds: float, sample_seconds: float, *,
               cold_speech_cache=False, disable_media_cache_reads=False):
    from fastapi import UploadFile
    import main
    from config import settings
    from core.engines.translation.ollama_client import OllamaClient

    ollama_calls = []
    original_translate = OllamaClient.translate
    speech_cache_originals = {}
    speech_cache_directory = None
    media_cache_originals = {}

    def profiled_translate(client, *args, **kwargs):
        started = time.monotonic()
        status, error_code = "completed", None
        try:
            return original_translate(client, *args, **kwargs)
        except Exception as error:
            status = "failed"
            error_code = getattr(error, "code", type(error).__name__)
            raise
        finally:
            metrics = dict(getattr(client, "last_metrics", {}) or {})
            finished = time.monotonic()
            ollama_calls.append({
                "elapsed_seconds": round(finished - started, 3),
                "started_elapsed_seconds": round(started - started_monotonic, 3),
                "finished_elapsed_seconds": round(finished - started_monotonic, 3),
                "status": status,
                "error_code": error_code,
                "prompt_tokens": metrics.get("prompt_eval_count", 0),
                "prompt_eval_seconds": round(metrics.get("prompt_eval_duration", 0) / 1e9, 3),
                "generated_tokens": metrics.get("eval_count", 0),
                "generation_seconds": round(metrics.get("eval_duration", 0) / 1e9, 3),
                "model_load_seconds": round(metrics.get("load_duration", 0) / 1e9, 3),
                "reported_total_seconds": round(metrics.get("total_duration", 0) / 1e9, 3),
            })

    if cold_speech_cache:
        # Isolate the speech cache for a cold TTS/alignment measurement without
        # deleting or invalidating the user's shared cache. Model/runtime stays
        # warm, which is reported separately from speech-cache state.
        speech_cache_directory = (Path(settings.WORKSPACE_DIR) / "cache" / "benchmarks"
                                  / f"cold-speech-{uuid.uuid4().hex}")

    task_id = None
    started_wall = time.time()
    started_monotonic = time.monotonic()
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source),
        "input_mode": "local_file_upload",
        "end_to_end_includes_download": False,
        "source_bytes": source.stat().st_size,
        "media_duration_seconds": _duration(source),
        "cache_mode": ("fresh task/session; isolated cold speech cache; model/runtime warm"
                       if cold_speech_cache else
                       "fresh task/session; shared speech cache enabled; model/runtime warm"),
        "media_cache_reads_disabled": bool(disable_media_cache_reads),
        "speech_cache_directory": str(speech_cache_directory) if speech_cache_directory else None,
        "provider": settings.LLM_PROVIDER,
        "model": settings.OLLAMA_MODEL if settings.LLM_PROVIDER == "ollama" else None,
        "asr_engine": settings.ASR_ENGINE,
        "whisper_model": settings.WHISPER_MODEL_SIZE,
        "whisper_device": settings.DEVICE,
        "whisper_compute_type": settings.WHISPER_COMPUTE_TYPE,
        "asr_cpu_threads": settings.ASR_CPU_THREADS,
        "configured_translation_workers": settings.TRANSLATION_CHUNK_WORKERS,
        "actual_translation_workers": 1 if settings.LLM_PROVIDER == "ollama" else settings.TRANSLATION_CHUNK_WORKERS,
        "tts_engine": settings.TTS_ENGINE,
        "samples": [],
        "resource_peaks": {},
    }
    try:
        OllamaClient.translate = profiled_translate
        if cold_speech_cache:
            import core.engines.alignment.natural_speech as natural_speech
            speech_cache_directory.mkdir(parents=True, exist_ok=False)
            for name in ("load_speech_cache", "store_speech_cache",
                         "load_raw_speech_cache", "store_raw_speech_cache"):
                original = getattr(natural_speech, name)
                speech_cache_originals[name] = original
                def isolated(*args, _original=original, **kwargs):
                    kwargs["cache_dir"] = speech_cache_directory
                    return _original(*args, **kwargs)
                setattr(natural_speech, name, isolated)
        if disable_media_cache_reads:
            import core.streaming.export as export_module
            for name in ("load_audio_cache", "load_background_cache", "load_prepared_background"):
                original = getattr(export_module, name)
                media_cache_originals[name] = original
                setattr(export_module, name, lambda *args, **kwargs: None)
            report["cache_mode"] = ("fresh task/session; isolated cold speech cache; "+
                "audio-mix/background/prepared-background cache reads disabled; "+
                "model warm state measured at submission")
        source_file = source.open("rb")
        upload = UploadFile(file=source_file, filename=source.name)
        request_token = main._e2e_request_started_at.set(started_wall)
        try:
            result = await main.start_streaming_upload(
                file=upload, initial_buffer_seconds=settings.INITIAL_BUFFER_SECONDS,
                voice=None, voice_id=None, tts_engine=settings.TTS_ENGINE,
                asr_engine=settings.ASR_ENGINE, visual_translation=False,
                translation_mode="full", ref_audio=None, translation_chunk_workers=None,
            )
        finally:
            main._e2e_request_started_at.reset(request_token)
            await upload.close()
        task_id = result["task_id"]
        session = main.get_streaming_session(task_id)
        report["task_id"] = task_id
        report["submitted_at_elapsed_seconds"] = round(time.monotonic() - started_monotonic, 3)
        report["model_loaded_before_run"] = None
        try:
            with urlopen(settings.OLLAMA_BASE_URL + "/api/ps", timeout=2) as response:
                ps = json.loads(response.read(1_000_001).decode("utf-8"))
            report["model_loaded_before_run"] = any(
                item.get("name") == settings.OLLAMA_MODEL for item in ps.get("models", [])
                if isinstance(item, dict))
        except Exception:
            pass

        peak = {"pipeline_process_cpu_percent": 0., "pipeline_process_rss_bytes": 0,
                "system_cpu_percent": 0., "system_ram_used_percent": 0.,
                "gpu_utilization_percent": 0, "gpu_memory_used_mib": 0}
        first_sample = True
        deadline = started_monotonic + timeout_seconds
        terminal = {"completed", "failed", "blocked", "cancelled"}
        last_announced_stage = None
        while time.monotonic() < deadline:
            await asyncio.sleep(sample_seconds)
            if session is None:
                raise RuntimeError("Streaming session disappeared after submission")
            progress = session.get_progress()
            counts = _segment_counts(session)
            sample = _resource_sample(os.getpid())
            export = main.active_export_tasks.get(f"export_{task_id}", {})
            automatic = getattr(session, "automatic_output", {}) or {}
            sample.update({
                "elapsed_seconds": round(time.monotonic() - started_monotonic, 1),
                "phase": progress.get("phase"), "stage": progress.get("stage"),
                "source_prepared_seconds": getattr(session, "_source_prepared_seconds", 0),
                "translation_completed_segments": sum(counts.get(k, 0) for k in ("READY", "PLAYED")),
                "translation_total_segments": len(session.segments),
                "segment_states": counts,
                "completed_chunks": progress.get("completed_chunks"),
                "total_chunks": progress.get("total_chunks"),
                "automatic_output_status": automatic.get("status"),
                "export_status": export.get("status"),
                "ollama_queue": progress.get("ai_queue"),
                "download_metrics": progress.get("download_metrics"),
            })
            report["samples"].append(sample)
            if sample.get("stage") != last_announced_stage:
                last_announced_stage = sample.get("stage")
                print(json.dumps({"event": "stage", "elapsed_seconds": sample["elapsed_seconds"],
                    "phase": sample.get("phase"), "stage": sample.get("stage"),
                    "translated_segments": sample["translation_completed_segments"],
                    "total_segments": sample["translation_total_segments"],
                    "export_status": sample.get("export_status")}, ensure_ascii=False), flush=True)
            peak["pipeline_process_cpu_percent"] = max(peak["pipeline_process_cpu_percent"], sample.get("pipeline_process_cpu_percent", 0.))
            peak["pipeline_process_rss_bytes"] = max(peak["pipeline_process_rss_bytes"], sample.get("pipeline_process_rss_bytes", 0))
            peak["system_cpu_percent"] = max(peak["system_cpu_percent"], sample.get("system_cpu_percent", 0.))
            peak["system_ram_used_percent"] = max(peak["system_ram_used_percent"], sample.get("system_ram_used_percent", 0.))
            for gpu in sample.get("gpu") or []:
                peak["gpu_utilization_percent"] = max(peak["gpu_utilization_percent"], gpu["utilization_percent"])
                peak["gpu_memory_used_mib"] = max(peak["gpu_memory_used_mib"], gpu["memory_used_mib"])

            output = Path(settings.OUTPUT_DIR) / getattr(session, "output_filename", "")
            draft_ready = (automatic.get("status") == "blocked"
                           and getattr(session, "output_incomplete", False))
            if (output.is_file() and getattr(session, "output_filename", "")
                    and (automatic.get("status") == "completed" or draft_ready)):
                report["status"] = "draft" if draft_ready else "completed"
                report["output_path"] = str(output.resolve())
                report["output_bytes"] = output.stat().st_size
                report["output_duration_seconds"] = _duration(output)
                from core.streaming.export import HQExporter
                HQExporter.validate_output(output, report["media_duration_seconds"])
                report["output_validated"] = True
                report["output_complete"] = not draft_ready
                report["output_incomplete"] = bool(getattr(session, "output_incomplete", False))
                report["output_warning"] = main.session_output_details(session).get("output_warning", "")
                break
            if (automatic.get("status") in terminal and not session.is_running
                    and not any(owner is not None and not owner.done() for owner in (
                        getattr(session, "start_task", None), getattr(session, "worker_task", None),
                        getattr(session, "review_task", None), getattr(session, "auto_export_task", None)))):
                report["status"] = automatic.get("status")
                break
            if session.error and not session.is_running:
                report["status"] = "failed"
                break
        else:
            report["status"] = "timed_out"
            try:
                await session.stop()
            except Exception:
                pass

        report["elapsed_seconds"] = round(time.monotonic() - started_monotonic, 1)
        report["real_time_factor"] = round(
            report["elapsed_seconds"] / max(report["media_duration_seconds"], .001), 3)
        report["resource_peaks"] = peak
        report["phase_timings_seconds"] = session.get_progress().get("phase_timings_seconds", {})
        report["stage_work_timings"] = session.get_progress().get("stage_work_timings", {})
        generated_tokens = sum(call["generated_tokens"] for call in ollama_calls)
        generation_seconds = sum(call["generation_seconds"] for call in ollama_calls)
        report["ollama_calls"] = {
            "attempts": len(ollama_calls),
            "completed": sum(call["status"] == "completed" for call in ollama_calls),
            "failed": sum(call["status"] != "completed" for call in ollama_calls),
            "prompt_tokens": sum(call["prompt_tokens"] for call in ollama_calls),
            "generated_tokens": generated_tokens,
            "generation_seconds": round(generation_seconds, 3),
            "wall_seconds": round(sum(call["elapsed_seconds"] for call in ollama_calls), 3),
            **_summarize_request_intervals(ollama_calls),
            "model_load_seconds": round(sum(call["model_load_seconds"] for call in ollama_calls), 3),
            "generation_tokens_per_second": round(generated_tokens / generation_seconds, 3)
                if generation_seconds > 0 else None,
            "requests": ollama_calls,
        }
        report["download_metrics"] = getattr(session, "_download_metrics", {})
        report["source_prepared_seconds"] = getattr(session, "_source_prepared_seconds", 0)
        report["source_failure_code"] = getattr(session, "_source_failure_code", None)
        report["missing_source_speech_spans"] = len(getattr(session, "_source_missing_speech_spans", []))
        report["segment_states"] = _segment_counts(session)
        report["automatic_output"] = getattr(session, "automatic_output", {})
        export = main.active_export_tasks.get(f"export_{task_id}", {})
        report["export"] = {key: export.get(key) for key in (
            "status", "elapsed_seconds", "end_to_end_seconds", "phase_timings_seconds",
            "audio_cache_reused", "output_filename", "published") if key in export}
        report["review_summary"] = getattr(session, "review_summary", {})
        return report
    except Exception as error:
        report["status"] = "benchmark_error"
        report["error_type"] = type(error).__name__
        report["elapsed_seconds"] = round(time.monotonic() - started_monotonic, 1)
        if task_id:
            session = main.get_streaming_session(task_id)
            if session is not None:
                report["segment_states"] = _segment_counts(session)
                report["source_failure_code"] = getattr(session, "_source_failure_code", None)
        return report
    finally:
        OllamaClient.translate = original_translate
        if media_cache_originals:
            import core.streaming.export as export_module
            for name, original in media_cache_originals.items():
                setattr(export_module, name, original)
        if speech_cache_originals:
            import core.engines.alignment.natural_speech as natural_speech
            for name, original in speech_cache_originals.items():
                setattr(natural_speech, name, original)


def main_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="local source video to process through the Studio upload handler")
    parser.add_argument("--timeout", type=float, default=1800., help="maximum wall time in seconds (default: 1800)")
    parser.add_argument("--sample-interval", type=float, default=2., help="resource/progress polling interval")
    parser.add_argument("--cache-mode", choices=("warm", "cold-speech"), default="warm",
                        help="use the shared speech cache or isolate an empty benchmark-only speech cache")
    parser.add_argument("--disable-media-cache-reads", action="store_true",
                        help="force audio-mix, background-stem, and prepared-background cache misses without deleting cached files")
    parser.add_argument("--report", type=Path, help="JSON report path (default: reports/benchmarks/<task id>.json)")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file() or args.timeout <= 0 or args.sample_interval < .5:
        parser.error("source must exist; timeout must be positive; sample interval must be at least 0.5 seconds")
    report = asyncio.run(_run(source, args.timeout, args.sample_interval,
                              cold_speech_cache=args.cache_mode == "cold-speech",
                              disable_media_cache_reads=args.disable_media_cache_reads))
    target = args.report or PROJECT_ROOT / "reports/benchmarks" / f"streaming-{report.get('task_id', uuid.uuid4().hex[:8])}.json"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: report.get(key) for key in (
        "status", "task_id", "media_duration_seconds", "elapsed_seconds", "real_time_factor",
        "output_path", "output_validated", "output_complete", "output_incomplete", "output_warning",
        "cache_mode", "end_to_end_includes_download", "source_failure_code", "missing_source_speech_spans",
        "segment_states", "phase_timings_seconds", "ollama_calls", "download_metrics", "export", "resource_peaks")}
    print(json.dumps({"report": str(target), "summary": summary}, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in {"completed", "draft"} and report.get("output_validated") else 2


if __name__ == "__main__":
    raise SystemExit(main_cli())
