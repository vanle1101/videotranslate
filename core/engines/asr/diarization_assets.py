"""Pinned real ONNX graphs fetched once from official sherpa-onnx releases.

Release assets had no upstream digest. These hashes pin the exact TLS-fetched
bytes inspected for this integration; they are not claimed upstream signatures.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

SEGMENTATION_NAME = "pyannote-segmentation-3-0.onnx"
SEGMENTATION_SIZE = 5992913
SEGMENTATION_SHA256 = "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
EMBEDDING_NAME = "wespeaker_zh_cnceleb_resnet34_LM.onnx"
EMBEDDING_SIZE = 26530548
EMBEDDING_SHA256 = "87d1d5068397f3792c730570b53d66cd8be1da7ea22dd04f5b6706d96a3cd168"
SEGMENTATION_ARCHIVE_NAME = "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEGMENTATION_ARCHIVE_SIZE = 6958444
SEGMENTATION_ARCHIVE_SHA256 = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"


def validate_graph(path, size, sha256):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ValueError(f"Missing/incomplete diarization graph: {path}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != sha256:
        raise ValueError(f"Diarization graph checksum mismatch; existing file preserved: {path}")
    return path.resolve()


def installed_assets(directory):
    directory = Path(directory)
    segmentation = validate_graph(directory / SEGMENTATION_NAME, SEGMENTATION_SIZE, SEGMENTATION_SHA256)
    embedding = validate_graph(directory / EMBEDDING_NAME, EMBEDDING_SIZE, EMBEDDING_SHA256)
    return segmentation, embedding, {"segmentation_sha256": SEGMENTATION_SHA256,
        "embedding_sha256": EMBEDDING_SHA256, "segmentation": "pyannote-segmentation-3.0",
        "embedding": "wespeaker-zh-cnceleb-resnet34-LM", "runtime": "sherpa_onnx"}


__all__ = ["installed_assets", "validate_graph"]
