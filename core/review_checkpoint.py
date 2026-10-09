"""Bounded, atomic records for exact-input translation review resume.

Only review/OCR data is accepted; callers never pass provider credentials.
Source content, adapter code, model assets and runtime versions form the namespace.
"""
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from collections import OrderedDict

from config import settings

SCHEMA = 1
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
_NAME = re.compile(r"[0-9a-f]{64}\.review\Z")
_LOCK = threading.RLock()
_DIGEST_LOCK = threading.RLock()
_FILE_DIGESTS = OrderedDict()
_SECRETS = {"api_key", "api-key", "apikey", "authorization", "password", "secret", "token",
            "access_token", "refresh_token", "credentials"}


def encoded(value):
    def check(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() in _SECRETS:
                    raise ValueError("Credentials are not review data.")
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_digest(path, check=lambda: None):
    path = Path(path).resolve(strict=True)
    check()
    before = path.stat()
    identity = (str(path), before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns)
    with _DIGEST_LOCK:
        cached = _FILE_DIGESTS.get(identity)
        if cached is not None:
            _FILE_DIGESTS.move_to_end(identity)
    if cached is not None:
        check()
        return cached
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            result.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise OSError("Source changed while hashing review inputs.")
    value = result.hexdigest()
    check()
    with _DIGEST_LOCK:
        _FILE_DIGESTS[identity] = value
        _FILE_DIGESTS.move_to_end(identity)
        while len(_FILE_DIGESTS) > 256:
            _FILE_DIGESTS.popitem(last=False)
    return value


def implementation_revision():
    root = Path(__file__).parent
    paths = [root / name for name in ("review_checkpoint.py", "translation_review.py", "translation_context.py",
             "video_intelligence.py", "screen_ocr.py", "media_process.py", "chinese_text.py", "structured_response.py", "semantic_segments.py", "ai_execution.py",
             "engines/translation/opencode_client.py", "engines/asr/sensevoice_engine.py",
             "engines/asr/native_process.py", "engines/asr/whisper_process.py",
             "engines/asr/whisper_worker.py", "engines/asr/review_evidence_worker.py")]
    return {str(path.relative_to(root)): file_digest(path) for path in paths}


try:
    _PROCESS_IMPLEMENTATION_REVISION = implementation_revision()
except OSError:
    _PROCESS_IMPLEMENTATION_REVISION = None


def loaded_implementation_revision():
    # A long-running Studio may still execute old imported code after an edit.
    # Never let that process stamp its results with the newer files on disk.
    if _PROCESS_IMPLEMENTATION_REVISION is None:
        raise OSError("Review cache unavailable: no complete runtime revision.")
    return dict(_PROCESS_IMPLEMENTATION_REVISION)


def evidence_revision(check):
    versions, assets = {}, {}
    for package in ("rapidocr-onnxruntime", "onnxruntime", "faster-whisper", "sherpa-onnx"):
        try:
            dist = metadata.distribution(package)
            versions[package] = dist.version
            if package == "rapidocr-onnxruntime":
                for item in dist.files or []:
                    if str(item).endswith(".onnx"):
                        assets[str(item)] = file_digest(Path(dist.locate_file(item)), check)
        except metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    model_root = settings.BASE_DIR / "workspace" / "models"
    for relative in ("sensevoice_onnx/model.int8.onnx", "sensevoice_onnx/tokens.txt",
                     "faster-whisper-small/model.bin", "faster-whisper-small/config.json",
                     "faster-whisper-small/tokenizer.json", "faster-whisper-small/vocabulary.json"):
        path = model_root / relative
        assets[relative] = file_digest(path, check) if path.is_file() else "unavailable"
    return {"versions": versions, "assets": assets}


class ReviewCheckpoint:
    def __init__(self, video_path, model, *, directory=None, check=lambda: None,
                 force=False, runtime_revision=None):
        self.check, self.force = check, force
        self.directory = Path(directory) if directory is not None else settings.WORKSPACE_DIR / "cache" / "translation_review"
        self.source = Path(video_path).resolve(strict=True)
        info = self.source.stat()
        self.source_stat = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        self.namespace = {"schema": SCHEMA, "source": file_digest(self.source, check),
            "source_bytes": info.st_size, "provider": "opencode", "model": model,
            "code": loaded_implementation_revision(),
            "evidence": evidence_revision(check) if runtime_revision is None else runtime_revision}

    def _path(self, stage):
        return self.directory / (digest({"namespace": self.namespace, "stage": stage}) + ".review")

    def _source_unchanged(self):
        info = self.source.stat()
        return (info.st_size, info.st_mtime_ns, info.st_ctime_ns) == self.source_stat

    def load(self, stage):
        self.check()
        if self.force:
            return None
        try:
            path = self._path(stage)
            with _LOCK:
                if (not self._source_unchanged() or path.is_symlink() or not path.is_file()
                        or path.stat().st_size > MAX_RECORD_BYTES):
                    return None
                record = json.loads(path.read_text(encoding="utf-8"))
                payload = record["payload"]
                if (record.get("key") != path.stem or record.get("schema") != SCHEMA
                        or record.get("payload_sha256") != digest(payload)):
                    return None
                self.check()
                return payload
        except (OSError, TypeError, ValueError, KeyError):
            return None

    def store(self, stage, payload):
        self.check()
        temporary = None
        try:
            path = self._path(stage)
            data = encoded({"schema": SCHEMA, "key": path.stem, "payload": payload,
                            "payload_sha256": digest(payload)})
            if len(data) > MAX_RECORD_BYTES:
                return False
            with _LOCK:
                if not self._source_unchanged():
                    return False
                self.directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(prefix="review-", suffix=".tmp", dir=self.directory, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                entries = []
                for candidate in self.directory.glob("*.review"):
                    if _NAME.fullmatch(candidate.name) and not candidate.is_symlink() and candidate != path:
                        try:
                            info = candidate.stat()
                            entries.append((info.st_mtime_ns, candidate, info.st_size))
                        except OSError:
                            continue
                total = sum(size for _, _, size in entries)
                for _, candidate, size in sorted(entries):
                    if total + len(data) <= MAX_CACHE_BYTES:
                        break
                    try:
                        candidate.unlink()
                        total -= size
                    except OSError:
                        continue
                if total + len(data) > MAX_CACHE_BYTES:
                    return False
                self.check()
                temporary.replace(path)
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
