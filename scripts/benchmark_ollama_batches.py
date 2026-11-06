"""Benchmark Ollama subtitle batches against a saved source transcript.

This calls the same structured translator used by the pipeline. It does not
open the Studio UI or send transcript data anywhere outside local Ollama.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import re
from pathlib import Path
import statistics
import threading
import time
import urllib.request
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _gpu_sample():
    import shutil
    import subprocess

    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"], check=True, capture_output=True,
            text=True, timeout=3,
        )
        samples = []
        for line in result.stdout.splitlines():
            values = [part.strip() for part in line.split(",")]
            if len(values) == 3:
                samples.append({"utilization_percent": int(values[0]),
                                "memory_used_mib": int(values[1]),
                                "memory_total_mib": int(values[2])})
        return samples or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class ResourceSampler:
    def __init__(self, interval=1.0):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def _sample(self):
        import psutil
        while not self._stop.is_set():
            memory = psutil.virtual_memory()
            self.samples.append({"at_monotonic": time.monotonic(),
                "system_cpu_percent": psutil.cpu_percent(None),
                "system_ram_used_percent": memory.percent,
                "system_ram_available_bytes": memory.available,
                "gpu": _gpu_sample()})
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(3.0, self.interval * 2))


def _summarize_requests(samples):
    prompt_tokens = sum(row.get("prompt_eval_count", 0) for row in samples)
    output_tokens = sum(row.get("eval_count", 0) for row in samples)
    prompt_seconds = sum(row.get("prompt_eval_duration", 0) for row in samples) / 1e9
    output_seconds = sum(row.get("eval_duration", 0) for row in samples) / 1e9
    wall = [row["wall_elapsed_seconds"] for row in samples]
    return {
        "request_count": len(samples),
        "successful_requests": sum(row["success"] for row in samples),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "prompt_eval_seconds": round(prompt_seconds, 3),
        "generation_seconds": round(output_seconds, 3),
        "model_load_seconds": round(sum(row.get("load_duration", 0) for row in samples) / 1e9, 3),
        "ollama_total_seconds": round(sum(row.get("total_duration", 0) for row in samples) / 1e9, 3),
        "prompt_tokens_per_second": round(prompt_tokens / prompt_seconds, 2) if prompt_seconds else None,
        "output_tokens_per_second": round(output_tokens / output_seconds, 2) if output_seconds else None,
        "mean_request_wall_seconds": round(statistics.mean(wall), 3) if wall else None,
        "p95_request_wall_seconds": round(sorted(wall)[min(len(wall) - 1,
            math.ceil((len(wall) - 1) * .95))], 3) if wall else None,
        "requests": samples,
    }


def _load_rows(path: Path, limit: int, offset: int):
    if path.is_symlink() or not (path.is_file() or path.is_dir()):
        raise ValueError("Transcript must be a regular JSON file or checkpoint directory")
    if path.is_dir():
        files = sorted(file for file in path.glob("*.json")
            if not file.is_symlink() and not file.name.endswith(
                ("_language.json", "_native_error.json", "_vocal_evidence.json")))
        if len(files) > 1000 or sum(file.stat().st_size for file in files) > 100_000_000:
            raise ValueError("Transcript directory exceeds the 100 MB / 1000 checkpoint limit")
    else:
        if path.stat().st_size > 10_000_000:
            raise ValueError("Transcript checkpoint exceeds the 10 MB limit")
        files = [path]
    all_rows = []
    for file in files:
        value = json.loads(file.read_text(encoding="utf-8"))
        rows = value.get("rows") if isinstance(value, dict) else value
        if not isinstance(rows, list):
            continue
        for row in rows:
            if (isinstance(row, dict) and isinstance(row.get("text_zh"), str)
                    and row["text_zh"].strip() and type(row.get("start")) in (int, float)
                    and type(row.get("end")) in (int, float) and row["start"] < row["end"]):
                all_rows.append(dict(row))
    all_rows.sort(key=lambda row: (row["start"], row["end"]))
    for identity, row in enumerate(all_rows):
        row["id"] = identity
    selected = all_rows[offset:offset + limit]
    if len(selected) < min(limit, 2):
        raise ValueError("Transcript needs at least two timed source sentences")
    return all_rows, selected


def _model_loaded(base_url, model):
    try:
        request = urllib.request.Request(base_url.rstrip("/") + "/api/ps",
            headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=2) as response:
            data = json.loads(response.read(1_000_001).decode("utf-8"))
        return any(item.get("name") == model for item in data.get("models", [])
                   if isinstance(item, dict))
    except Exception:
        return None


def benchmark(transcript: Path, *, limit=16, offset=0, batch_sizes=(8, 12, 16), model=None):
    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from config import settings
    from core.engines.translation.ollama_client import OllamaClient
    from core.engines.translation.semantic_translator import SemanticTranslator

    if settings.LLM_PROVIDER != "ollama":
        raise ValueError("Benchmark requires the project's configured local Ollama provider")
    if not re.fullmatch(r"http://(127\.0\.0\.1|localhost|\[::1\])(?::11434)?/?",
                        settings.OLLAMA_BASE_URL):
        raise ValueError("Benchmark only allows the configured local Ollama endpoint")
    if model is not None:
        from urllib.error import URLError
        from urllib.request import Request, urlopen
        request = Request(settings.OLLAMA_BASE_URL.rstrip("/") + "/api/tags",
            headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=5) as response:
                tags = json.loads(response.read(1_000_001).decode("utf-8"))
        except (OSError, URLError, TimeoutError, ValueError) as error:
            raise ValueError("Cannot verify model against the local Ollama registry") from error
        available = {item.get("name") for item in tags.get("models", []) if isinstance(item, dict)}
        if model not in available:
            raise ValueError(f"Model {model!r} is not already installed in local Ollama")
        settings.OLLAMA_MODEL = model
    all_rows, rows = _load_rows(transcript.resolve(), limit, offset)
    translator = SemanticTranslator("ollama")
    context_info = translator._extract_context_and_glossary(
        json.dumps(all_rows, ensure_ascii=False))
    original_translate = OllamaClient.translate
    requests = []
    request_lock = threading.Lock()

    def tracked_translate(client, *args, **kwargs):
        started = time.monotonic()
        success = False
        try:
            result = original_translate(client, *args, **kwargs)
            success = True
            return result
        finally:
            sample = {**client.last_metrics,
                "wall_elapsed_seconds": round(time.monotonic() - started, 3),
                "success": success}
            with request_lock:
                requests.append(sample)

    OllamaClient.translate = tracked_translate
    sampler = ResourceSampler()
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_transcript": str(transcript.resolve()), "model": settings.OLLAMA_MODEL,
        "base_url": settings.OLLAMA_BASE_URL, "source_sentence_count": len(rows),
        "source_row_offset": offset,
        "model_loaded_before_run": _model_loaded(settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL),
        "batch_sizes": list(batch_sizes), "warmup": None, "runs": [],
        "environment": {"provider": settings.LLM_PROVIDER,
            "configured_cpu_threads": 2, "gpu_backend": "see Ollama server log and resource samples"}}
    try:
        sampler.start()
        translator.MAX_OLLAMA_TRANSLATION_TARGETS = max(batch_sizes)
        before = len(requests)
        warmup_started = time.monotonic()
        warmup = translator._execute_3tier_translation(rows[:1], context_info,
                                                        source_context=all_rows)
        warmup_samples = requests[before:]
        report["warmup"] = {"elapsed_seconds": round(time.monotonic() - warmup_started, 3),
            "complete": len(warmup) == 1 and not getattr(warmup, "errors", {}),
            "metrics": _summarize_requests(warmup_samples)}
        for batch_size in batch_sizes:
            translator.MAX_OLLAMA_TRANSLATION_TARGETS = batch_size
            before = len(requests)
            started = time.monotonic()
            result = translator._execute_3tier_translation(rows, context_info,
                                                            source_context=all_rows)
            elapsed = time.monotonic() - started
            samples = requests[before:]
            texts = [result.get(int(row["id"]), {}).get("final_vi", "") for row in rows]
            errors = getattr(result, "errors", {})
            report["runs"].append({"batch_size": batch_size,
                "elapsed_seconds": round(elapsed, 3),
                "sentences_per_second": round(len(texts) / elapsed, 3) if elapsed else None,
                "complete": len(texts) == len(rows) and all(texts) and not errors,
                "error_ids": sorted(map(int, errors)),
                "residual_han_ids": [int(row["id"]) for row, text in zip(rows, texts)
                    if re.search(r"[\u3400-\u9fff]", text)],
                "request_metrics": _summarize_requests(samples),
                "translations": [{"id": int(row["id"]), "source_zh": row["text_zh"],
                                  "final_vi": text} for row, text in zip(rows, texts)]})
    finally:
        sampler.stop()
        OllamaClient.translate = original_translate
    report["elapsed_seconds"] = round(sum(row["elapsed_seconds"] for row in report["runs"])
                                     + report["warmup"]["elapsed_seconds"], 3)
    report["resource_samples"] = sampler.samples
    report["resource_peaks"] = {
        "system_cpu_percent": max((row.get("system_cpu_percent", 0) for row in sampler.samples), default=None),
        "system_ram_used_percent": max((row.get("system_ram_used_percent", 0) for row in sampler.samples), default=None),
        "gpu_utilization_percent": max((gpu["utilization_percent"] for row in sampler.samples
            for gpu in row.get("gpu") or []), default=None),
        "gpu_memory_used_mib": max((gpu["memory_used_mib"] for row in sampler.samples
            for gpu in row.get("gpu") or []), default=None)}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path, help="source-preparation JSON checkpoint with timed rows")
    parser.add_argument("--limit", type=int, default=16, help="number of source rows, default: 16")
    parser.add_argument("--offset", type=int, default=0, help="skip this many valid transcript rows")
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[8, 12, 16])
    parser.add_argument("--model", help="benchmark an already-installed local model")
    parser.add_argument("--report", type=Path, help="JSON report path")
    args = parser.parse_args()
    if not 2 <= args.limit <= 128 or args.offset < 0 or any(size < 1 or size > 16 for size in args.batch_sizes):
        parser.error("--limit must be 2..128, --offset nonnegative and every batch size 1..16")
    output = args.report or PROJECT_ROOT / "reports" / "benchmarks" / f"ollama-batches-{uuid.uuid4().hex[:8]}.json"
    output = output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / "reports" / "benchmarks").resolve()):
        parser.error("--report must stay inside reports/benchmarks")
    report = benchmark(args.transcript, limit=args.limit, offset=args.offset,
                       batch_sizes=tuple(args.batch_sizes), model=args.model)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {output}")
    for run in report["runs"]:
        metrics = run["request_metrics"]
        print(f"batch={run['batch_size']} complete={run['complete']} "
              f"elapsed={run['elapsed_seconds']:.2f}s sentences/s={run['sentences_per_second']} "
              f"requests={metrics['request_count']} prompt_tps={metrics['prompt_tokens_per_second']} "
              f"generation_tps={metrics['output_tokens_per_second']} "
              f"tokens={metrics['prompt_tokens']}+{metrics['output_tokens']}")


if __name__ == "__main__":
    main()
