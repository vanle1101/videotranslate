"""Download the optional CPU model assets from pinned upstream sources.

Run without arguments to show the download plan; pass --download to execute it.
Existing Hugging Face cache entries and verified local files are reused.
This does not change the active pipeline engines or install Python packages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import requests

from config import settings

SENSE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSE_REV = "2365baeacb507f821a0c8120fcee3d484dba7a07"
VIENEU_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"
VIENEU_REV = "61b85e3d937fbbacb387714180e8182823512523"
CODEC_REPO = "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX"
CODEC_REV = "ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae"
VIENEU_FILES = [
    "config.json", "denoiser.onnx", "speaker_encoder.onnx",
    *[f"onnx_update/{name}" for name in (
        "config.json", "tokenizer.json", "vieneu_prefill.onnx",
        "vieneu_decode_step.onnx", "vieneu_acoustic_cached.onnx",
        "vieneu_backbone_shared.data", "vieneu_v3_heads.npz",
    )],
]
CODEC_FILES = [
    "moss_audio_tokenizer_decode_full.onnx", "moss_audio_tokenizer_decode_shared.data",
    "moss_audio_tokenizer_decode_step.onnx", "codec_browser_onnx_meta.json",
    "moss_audio_tokenizer_encode.onnx", "moss_audio_tokenizer_encode.data",
]


def _json(url):
    response = requests.get(url, timeout=40)
    response.raise_for_status()
    return response.json()


def build_plan():
    root = settings.WORKSPACE_DIR / "models"
    plan = []
    for repo, rev, files, dest in (
        (SENSE_REPO, SENSE_REV, ["model.int8.onnx", "tokens.txt"], root / "sensevoice_onnx"),
        (VIENEU_REPO, VIENEU_REV, VIENEU_FILES, None),
        (CODEC_REPO, CODEC_REV, CODEC_FILES, None),
    ):
        metadata = _json(f"https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true")
        by_name = {entry["rfilename"]: entry for entry in metadata["siblings"]}
        for name in files:
            item = by_name[name]
            plan.append({
                "name": name, "repo": repo, "revision": rev, "size": item["size"],
                "sha256": item.get("lfs", {}).get("sha256"),
                "url": f"https://huggingface.co/{repo}/resolve/{rev}/{name}",
                "path": str(dest / name) if dest else None,
            })
    for repo, tag, names, dest in (
        ("TRvlvr/model_repo", "all_public_uvr_models", [settings.ROFORMER_MODEL], root / "audio_separator"),
        ("sczhou/ProPainter", "v0.1.0", ["ProPainter.pth", "raft-things.pth", "recurrent_flow_completion.pth"], root / "propainter"),
    ):
        release = _json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
        assets = {asset["name"]: asset for asset in release["assets"]}
        for name in names:
            asset = assets[name]
            digest = asset.get("digest") or ""
            plan.append({
                "name": name, "size": asset["size"], "url": asset["browser_download_url"],
                "path": str(dest / name), "sha256": digest.removeprefix("sha256:") or None,
            })
    config_name = "model_bs_roformer_ep_317_sdr_12.9755.yaml"
    config_url = ("https://raw.githubusercontent.com/TRvlvr/application_data/"
                  "3826b05b570dbd4fbedbc807758803b35348ba1b/mdx_model_data/mdx_c_configs/" + config_name)
    response = requests.get(config_url, timeout=40)
    response.raise_for_status()
    plan.append({"name": config_name, "size": len(response.content), "url": config_url,
                 "path": str(root / "audio_separator" / config_name),
                 "sha256": hashlib.sha256(response.content).hexdigest()})
    return plan


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(path, item):
    return (path.is_file() and path.stat().st_size == item["size"]
            and (not item.get("sha256") or _digest(path) == item["sha256"]))


def download(item):
    if item["path"] is None:
        from huggingface_hub import hf_hub_download
        # The SDK uses this same cache. Do not create another copy in workspace.
        path = Path(hf_hub_download(item["repo"], item["name"], revision=item["revision"]))
        if not validate(path, item):
            raise RuntimeError(f"Size/checksum mismatch: {item['name']}")
        return path
    path = Path(item["path"])
    if validate(path, item):
        print(f"REUSED {item['name']}", flush=True)
        return path
    if path.exists():
        raise RuntimeError(f"Existing file does not match upstream; preserving it: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".download")
    try:
        with requests.get(item["url"], stream=True, timeout=(30, 90)) as response:
            response.raise_for_status()
            count, last_report = 0, time.monotonic()
            with partial.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    count += len(chunk)
                    if count > item["size"]:
                        raise RuntimeError(f"Unexpected download size: {item['name']}")
                    stream.write(chunk)
                    if time.monotonic() - last_report >= 10:
                        print(f"{item['name']}: {count / item['size']:.0%}", flush=True)
                        last_report = time.monotonic()
        if not validate(partial, item):
            raise RuntimeError(f"Size/checksum mismatch: {item['name']}")
        partial.replace(path)
        return path
    finally:
        partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    plan = build_plan()
    print(f"Total model assets: {sum(item['size'] for item in plan) / 1024**3:.2f} GiB", flush=True)
    for item in plan:
        print(f"{item['name']}: {item['size'] / 1024**2:.1f} MiB", flush=True)
    if not args.download:
        return
    records = []
    for item in plan:
        print(f"Downloading {item['name']} ...", flush=True)
        path = download(item)
        records.append({**item, "path": str(path), "sha256": _digest(path)})
        print(f"VERIFIED {item['name']}", flush=True)
    manifest = settings.WORKSPACE_DIR / "models" / "download_manifest.json"
    manifest.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print("All requested model assets downloaded and size/checksum verified.", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
