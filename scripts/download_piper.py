"""Download and verify the optional five-voice Piper Vietnamese model (73.6 MiB)."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engines.tts.piper_engine import (
    PIPER_ASSETS, PIPER_REVISION, PIPER_SOURCE_URL, piper_model_dir,
)
from scripts.download_models import download


def build_plan():
    return [
        {"name": name, "size": size, "sha256": digest,
         "url": f"{PIPER_SOURCE_URL}/resolve/{PIPER_REVISION}/{name}",
         "path": str(piper_model_dir() / name)}
        for name, size, digest in PIPER_ASSETS
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    for item in build_plan():
        print(f"{item['name']}: {item['size'] / 1024 ** 2:.1f} MiB", flush=True)
        if args.download:
            download(item)
            print(f"VERIFIED {item['name']}", flush=True)


if __name__ == "__main__":
    main()
