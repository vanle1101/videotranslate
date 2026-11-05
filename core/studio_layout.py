"""Persist Studio split sizes independently of the desktop server's random port."""
import json
import logging
import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import settings


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studio", tags=["studio"])
_layout_lock = threading.Lock()
_group_sizes = {"columns": 3, "left": 2, "middle": 3, "right": 2}
_legacy_group_sizes = {"middle": 2, "right": 3}


class StudioLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    groups: dict[str, list[float]] = Field(default_factory=dict)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported layout version")
        return value

    @field_validator("groups", mode="before")
    @classmethod
    def validate_groups(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Layout groups must be an object")
        for name, ratios in value.items():
            if name not in _group_sizes:
                raise ValueError("Unknown layout group")
            if not isinstance(ratios, list) or len(ratios) != _group_sizes[name]:
                raise ValueError("Incorrect number of panel sizes")
            for ratio in ratios:
                try:
                    valid = type(ratio) in (int, float) and math.isfinite(ratio) and ratio > 0
                except OverflowError:
                    valid = False
                if not valid:
                    raise ValueError("Panel sizes must be positive finite numbers")
        return value


def _layout_path() -> Path:
    return Path(settings.WORKSPACE_DIR) / "studio_layout.json"


def read_layout(path: Path | None = None) -> StudioLayout:
    target = path if path is not None else _layout_path()
    with _layout_lock:
        try:
            saved = json.loads(target.read_text(encoding="utf-8"))
            # These panels moved between columns. Reuse unchanged dimensions,
            # but let the client choose defaults for the former panel groups.
            # Migration applies only to disk reads; PUT still requires new sizes.
            if isinstance(saved, dict) and isinstance(saved.get("groups"), dict):
                groups = saved["groups"]
                for name, old_size in _legacy_group_sizes.items():
                    ratios = groups.get(name)
                    if isinstance(ratios, list) and len(ratios) == old_size:
                        del groups[name]
            return StudioLayout.model_validate(saved)
        except FileNotFoundError:
            return StudioLayout()
        except (OSError, ValueError):
            logger.warning("Could not read saved Studio layout; using defaults")
            return StudioLayout()


def write_layout(layout: StudioLayout, path: Path | None = None) -> StudioLayout:
    target = path if path is not None else _layout_path()
    payload = json.dumps(layout.model_dump(), ensure_ascii=False, allow_nan=False)
    with _layout_lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=".studio-layout-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return layout


@router.get("/layout", response_model=StudioLayout)
def get_studio_layout():
    return read_layout()


@router.put("/layout", response_model=StudioLayout)
def put_studio_layout(layout: StudioLayout):
    try:
        return write_layout(layout)
    except OSError:
        logger.exception("Could not save Studio layout")
        raise HTTPException(status_code=500, detail="Không lưu được bố cục Studio.") from None
