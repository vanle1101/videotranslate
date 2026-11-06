"""Deployment fingerprint for matching acceptance work to the started backend.

Only application source is hashed; no configuration secrets or user media.
The server captures it at import, never relabels an old process from new files.
"""
import hashlib
from pathlib import Path


def source_revision(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    paths = [root / "main.py", root / "config.py", root / "desktop_app.py",
             *sorted((root / "core").rglob("*.py"))]
    for folder, suffixes in (("templates", {".html"}), ("static", {".js", ".css"})):
        paths.extend(path for path in sorted((root / folder).rglob("*"))
                     if path.is_file() and path.suffix.lower() in suffixes)
    paths = [path for path in paths if path.is_file()]
    value = hashlib.sha256()
    for path in paths:
        value.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        value.update(path.read_bytes() + b"\0")
    return value.hexdigest()


STARTED_SOURCE_REVISION = source_revision()
