"""Install only the pinned optional speaker graphs; reuse existing downloader.

No dependency install, engine switch, archive extraction tree or backup copy.
Existing mismatching files are preserved and reported instead of overwritten.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tarfile
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from scripts.download_models import download
from core.engines.asr import diarization_assets as assets


def install(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    target = root / assets.SEGMENTATION_NAME
    if target.exists():
        assets.validate_graph(target, assets.SEGMENTATION_SIZE, assets.SEGMENTATION_SHA256)
    else:
        archive_created = not (root / assets.SEGMENTATION_ARCHIVE_NAME).exists()
        archive = download({"name": assets.SEGMENTATION_ARCHIVE_NAME,
            "size": assets.SEGMENTATION_ARCHIVE_SIZE, "sha256": assets.SEGMENTATION_ARCHIVE_SHA256,
            "url": assets.RELEASE_BASE + "speaker-segmentation-models/" + assets.SEGMENTATION_ARCHIVE_NAME,
            "path": str(root / assets.SEGMENTATION_ARCHIVE_NAME)})
        temporary = None
        try:
            with tarfile.open(archive, "r:bz2") as tar:
                matches = [item for item in tar.getmembers()
                           if item.name == "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"]
                if len(matches) != 1 or not matches[0].isfile() or matches[0].size != assets.SEGMENTATION_SIZE:
                    raise ValueError("Segmentation archive does not contain the pinned graph")
                with tar.extractfile(matches[0]) as graph, tempfile.NamedTemporaryFile(dir=root, suffix=".download", delete=False) as output:
                    temporary = Path(output.name)
                    count = 0
                    for block in iter(lambda: graph.read(1024 * 1024), b""):
                        count += len(block)
                        if count > assets.SEGMENTATION_SIZE:
                            raise ValueError("Segmentation archive graph exceeds its pinned size")
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
            assets.validate_graph(temporary, assets.SEGMENTATION_SIZE, assets.SEGMENTATION_SHA256)
            # Hard linking creates a final file atomically without overwriting
            # a concurrently installed or pre-existing user asset.
            try:
                os.link(temporary, target)
            except FileExistsError:
                assets.validate_graph(target, assets.SEGMENTATION_SIZE, assets.SEGMENTATION_SHA256)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            # The archive is an installer-owned disposable artifact, not a
            # required resume checkpoint or another permanent model copy.
            if archive_created:
                archive.unlink(missing_ok=True)
    download({"name": assets.EMBEDDING_NAME, "size": assets.EMBEDDING_SIZE,
              "sha256": assets.EMBEDDING_SHA256,
              "url": assets.RELEASE_BASE + "speaker-recongition-models/" + assets.EMBEDDING_NAME,
              "path": str(root / assets.EMBEDDING_NAME)})
    return assets.installed_assets(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    print("Speaker assets: 31.0 MiB installed; 31.9 MiB downloaded once from official releases.")
    if args.download:
        paths = install(settings.WORKSPACE_DIR / "models" / "speaker_diarization")
        print("Verified pinned graph sizes and SHA256:", *paths[:2], sep="\n")


if __name__ == "__main__":
    main()
