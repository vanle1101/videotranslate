"""A video-centric history view, without changing execution task identities.

Grouping is a read-only projection. Saved revisions remain separate projects;
exports are operations of their owning project rather than another video.
"""
import hashlib
import math
import ntpath
import os
from pathlib import Path
from urllib.parse import urlsplit

from core.douyin_resolver import _video_id
from core.douyin_cookies import is_douyin_url


ACTIVE_STATES = frozenset({"RUNNING", "PAUSED", "CANCELLING", "QUEUED"})


def source_identity(source_url):
    """Only discard URL differences when they identify a known Douyin post."""
    if not isinstance(source_url, str) or not source_url.strip():
        return None
    value = source_url.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if is_douyin_url(value):
            post_id = _video_id(value)
            if post_id:
                return "douyin:" + post_id
    except ValueError:
        return None
    # Do not guess that two short links or two arbitrary signed URLs are equal.
    return "url:" + value


def local_identity(video_path):
    """Normalize full paths lexically; never compare basename or access disk."""
    if not isinstance(video_path, str) or not video_path.strip():
        return None
    value = video_path.strip()
    if ntpath.isabs(value) and (ntpath.splitdrive(value)[0] or "\\" in value):
        return "file:" + ntpath.normcase(ntpath.normpath(value))
    return "file:" + os.path.normcase(os.path.abspath(os.path.normpath(value)))


def source_metadata(session):
    """Cheap live metadata; source existence was validated by the normal runtime."""
    path = getattr(session, "video_path", None)
    source_url = getattr(session, "source_url", None) or ""
    value = str(path) if path is not None else ""
    title = getattr(session, "source_title", None) or (Path(value).name if value else source_url)
    return {
        "title": title or getattr(session, "task_id", "Video"),
        "video_path": value,
        "source_url": source_url,
        "source_video_url": getattr(session, "source_video_url", None) or "",
        "updated_at": getattr(session, "_history_updated_at", None)
            or getattr(session, "start_wall_time", None) or 0,
        "can_open": bool(path or source_url),
    }


def _timestamp(row):
    value = row.get("updated_at", row.get("start_time", 0))
    return float(value) if type(value) in {int, float} and math.isfinite(value) else 0


def _owner(row):
    task_id = str(row.get("task_id") or "")
    return str(row.get("parent_session_id") or (task_id[7:] if task_id.startswith("export_") else task_id))


def group_video_history(tasks):
    """Join exact source evidence and owning export IDs in O(n log n) time."""
    rows = [dict(row) for row in tasks]
    parent = list(range(len(rows)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(left, right):
        parent[find(right)] = find(left)

    seen = {}
    keys = []
    # A reused local path is weak evidence when records identify distinct
    # known posts. Do not let it bridge those posts (or attach URL-less records
    # arbitrarily); exact URLs and explicit session/export ownership remain.
    path_posts = {}
    for row in rows:
        source = source_identity(row.get("source_url"))
        path = local_identity(row.get("video_path"))
        if path and source and source.startswith("douyin:"):
            path_posts.setdefault(path, set()).add(source)
    ambiguous_paths = {path for path, posts in path_posts.items() if len(posts) > 1}
    for index, row in enumerate(rows):
        source = source_identity(row.get("source_url"))
        path = local_identity(row.get("video_path"))
        if path in ambiguous_paths:
            path = None if source else "unassigned:" + path
        identities = [key for key in (source, path) if key]
        identities.append("session:" + _owner(row))
        keys.append(identities)
        for key in identities:
            if key in seen:
                join(index, seen[key])
            else:
                seen[key] = index

    groups = {}
    for index, row in enumerate(rows):
        groups.setdefault(find(index), []).append((index, row))
    result = []
    for members in groups.values():
        projects = [row for _, row in members if not row.get("is_export") and not str(row.get("task_id", "")).startswith("export_")]
        active = [row for _, row in members if row.get("status") in ACTIVE_STATES]
        # A newer failure is the current state even when its source is missing.
        # Older editable projects remain available in versions; do not mask the
        # latest error by selecting an older successful/openable project.
        active_export_owners = {_owner(row) for row in active if row.get("is_export")}
        active_candidates = [row for row in active if row.get("is_export") or _owner(row) not in active_export_owners]
        candidates = active_candidates or projects or [row for _, row in members]
        # When a live session and its export coexist, the actual export stage
        # owns the progress. Its Stop action still uses the export task ID.
        representative = max(candidates, key=lambda row: (
            _timestamp(row), bool(active and row.get("is_export")), str(row.get("task_id", ""))))
        if not active:
            terminal_exports = [row for _, row in members if row.get("is_export")
                and row.get("status") in {"FAILED", "CANCELLED", "STOPPED", "TIMED_OUT"}
                and _owner(row) == _owner(representative)
                and _timestamp(row) >= _timestamp(representative)]
            if terminal_exports:
                representative = max(terminal_exports, key=_timestamp)
        ordered_projects = sorted(projects, key=lambda row: (_timestamp(row), str(row.get("task_id", ""))), reverse=True)
        source = next((row for row in [representative, *ordered_projects] if row.get("video_path") or row.get("source_url")), representative)
        identities = sorted({key for index, _ in members for key in keys[index] if not key.startswith("session:")})
        identity = next((key for key in identities if key.startswith("douyin:")), identities[0] if identities else "session:" + _owner(representative))
        versions = [{key: row.get(key) for key in (
            "task_id", "title", "updated_at", "status", "can_open", "translation_mode", "output_video_url", "stage", "saved")}
            for row in ordered_projects]
        view = {**representative,
            "video_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            "session_id": _owner(representative),
            "title": source.get("title") or representative.get("title") or _owner(representative),
            "source_url": source.get("source_url") or "",
            "source_video_url": source.get("source_video_url") or "",
            "versions": versions, "version_count": len(versions),
            "updated_at": max((_timestamp(row) for _, row in members), default=0),
            "can_open": bool(any(row.get("can_open") and row.get("task_id") == _owner(representative) for row in projects)),
            "is_export": bool(representative.get("is_export")),
        }
        # Source metadata may originate from another version, but actions and
        # state always belong to the representative. No averaged percentage.
        if not view.get("duration"):
            view["duration"] = source.get("duration", 0)
        result.append(view)
    return sorted(result, key=lambda row: (row.get("status") in ACTIVE_STATES, row["updated_at"], row["video_id"]), reverse=True)
