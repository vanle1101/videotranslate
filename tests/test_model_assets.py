from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import core.model_manager as manager
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
