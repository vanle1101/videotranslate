from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

import core.model_manager as manager
import download_models as downloader
from download_models import validate


def test_checkpoint_does_not_imply_runtime_ready():
    with TemporaryDirectory() as tmp:
        checkpoint = Path(tmp) / "model.bin"
        checkpoint.write_bytes(b"weights")
        with patch.object(manager, "_module_present", return_value=False):
            record = manager._model_record("test", "model", checkpoint.parent, [checkpoint], "CPU", ("missing",))
        assert record["downloaded"]
        assert record["status"] == "RUNTIME_MISSING"
        assert not record["runtime_available"]
        assert not record["runtime_verified"]


def test_partial_multifile_checkpoint_is_not_downloaded():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "graph.onnx").write_bytes(b"graph")
        record = manager._model_record("test", "model", root,
                                       [root / "graph.onnx", root / "weights.data"], "CPU")
        assert not record["downloaded"]
        assert record["status"] == "NOT_DOWNLOADED"
        assert record["missing_files"] == [str(root / "weights.data")]


def test_runtime_integration_is_required_even_when_files_exist():
    with TemporaryDirectory() as tmp:
        checkpoint = Path(tmp) / "ProPainter.pth"
        checkpoint.write_bytes(b"weights")
        record = manager._model_record("test", "model", checkpoint.parent, [checkpoint], "CPU",
                                       runtime_note="Inference integration is not implemented")
        assert record["downloaded"]
        assert not record["runtime_available"]
        assert record["status"] == "RUNTIME_MISSING"


def test_vieneu_single_graph_does_not_count_as_complete_model():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        graph = root / "hf" / "hub" / "models--pnnbao-ump--VieNeu-TTS-v3-Turbo" / "snapshots" / "rev" / "model.onnx"
        graph.parent.mkdir(parents=True)
        graph.write_bytes(b"graph")
        settings = SimpleNamespace(WORKSPACE_DIR=root, BASE_DIR=root, DEVICE="cpu",
                                   ROFORMER_MODEL="roformer.ckpt", WHISPER_MODEL_SIZE="small")
        with patch.object(manager, "settings", settings), patch.dict("os.environ", {"HF_HUB_CACHE": str(root / "hf" / "hub")}):
            record = next(item for item in manager.ModelManager.get_all_models() if item["engine"] == "VieNeu-TTS")
        assert not record["downloaded"]
        assert not record["runtime_available"]


def test_download_validation_rejects_wrong_size_and_hash():
    import hashlib
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.bin"
        path.write_bytes(b"complete")
        assert validate(path, {"size": 8, "sha256": hashlib.sha256(b"complete").hexdigest()})
        assert not validate(path, {"size": 9})
        assert not validate(path, {"size": 8, "sha256": "0" * 64})


class _Response:
    def __init__(self, status, body, content_range=None):
        self.status_code = status
        self.body = body
        self.headers = {"Content-Range": content_range} if content_range else {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self.body


def _download_item(root, data):
    import hashlib
    return {"path": str(root / "asset.bin"), "name": "asset.bin", "url": "https://example.com/asset",
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_download_resumes_exact_prefix_with_bounded_ranges():
    data = b"abcdefghijklmnop"
    seen = []

    def get(url, *, headers, **kwargs):
        start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
        seen.append((start, end))
        return _Response(206, data[start:end + 1], f"bytes {start}-{end}/{len(data)}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        partial = root / "asset.bin.download"
        partial.write_bytes(data[:6])
        with patch.object(downloader, "_RANGE_CHUNK_SIZE", 4), patch.object(downloader.requests, "get", side_effect=get):
            result = downloader.download(_download_item(root, data))
        assert result.read_bytes() == data
        assert sorted(seen) == [(6, 9), (10, 13), (14, 15)]
        assert not partial.exists()


def test_wrong_content_range_keeps_existing_partial_untouched():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        partial = root / "asset.bin.download"
        partial.write_bytes(b"abc")
        with patch.object(downloader.requests, "get", return_value=_Response(206, b"def", "bytes 0-2/6")):
            with pytest.raises(RuntimeError, match="Content-Range"):
                downloader.download(_download_item(root, b"abcdef"))
        assert partial.read_bytes() == b"abc"
        assert not (root / "asset.bin").exists()


def test_no_range_server_never_overwrites_partial():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        partial = root / "asset.bin.download"
        partial.write_bytes(b"abc")
        with patch.object(downloader.requests, "get", return_value=_Response(200, b"abcdef")) as request:
            with pytest.raises(downloader.RangeUnsupportedError, match="Cannot resume safely"):
                downloader.download(_download_item(root, b"abcdef"))
        assert request.call_count == 1
        assert partial.read_bytes() == b"abc"


def test_no_range_fallback_only_when_starting_from_zero():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch.object(downloader.requests, "get", return_value=_Response(200, b"abcdef")) as request:
            result = downloader.download(_download_item(root, b"abcdef"))
        assert request.call_count == 2
        assert result.read_bytes() == b"abcdef"


def test_failed_range_keeps_only_contiguous_validated_prefix():
    def get(url, *, headers, **kwargs):
        if headers["Range"] == "bytes=0-3":
            return _Response(206, b"abcd", "bytes 0-3/8")
        raise requests.ConnectionError("network down")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch.object(downloader, "_RANGE_CHUNK_SIZE", 4), patch.object(downloader.requests, "get", side_effect=get), patch.object(downloader.time, "sleep"):
            with pytest.raises(requests.ConnectionError):
                downloader.download(_download_item(root, b"abcdefgh"))
        assert (root / "asset.bin.download").read_bytes() == b"abcd"
        assert not (root / "asset.bin").exists()
