"""Versioned explicit terminology, independent of speculative character roles.

No extraction or auto-promotion of model drafts occurs here. Provenance records
where a supplied mapping came from, not whether its semantic meaning is proven.
Callers include the complete snapshot/version in prompts and stage cache keys.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unicodedata

SCHEMA_VERSION = 1
MAX_TERMS = 500
PROVENANCE_KINDS = frozenset({"user_supplied", "imported_glossary"})


def _text(value, limit, *, optional=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Nội dung bảng thuật ngữ không hợp lệ.")
    value = unicodedata.normalize("NFC", value.strip())
    if (not optional and not value) or any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Thuật ngữ trống hoặc chứa ký tự điều khiển.")
    return value


def _terms(terms):
    if not isinstance(terms, list) or len(terms) > MAX_TERMS:
        raise ValueError("Bảng thuật ngữ phải có tối đa 500 mục.")
    checked, seen = [], set()
    for term in terms:
        if not isinstance(term, dict) or set(term) != {"src", "tgt", "note", "provenance"}:
            raise ValueError("Thuật ngữ thiếu nguồn, đích, ghi chú hoặc xuất xứ.")
        source = _text(term["src"], 200)
        target = _text(term["tgt"], 200)
        note = _text(term["note"], 1000, optional=True)
        provenance = term["provenance"]
        if (not isinstance(provenance, dict) or set(provenance) != {"kind", "reference"}
                or not isinstance(provenance["kind"], str) or provenance["kind"] not in PROVENANCE_KINDS):
            raise ValueError("Xuất xứ thuật ngữ phải là dữ liệu người dùng cung cấp hoặc bảng được nhập.")
        reference = _text(provenance["reference"], 200)
        if source in seen:
            raise ValueError("Bảng thuật ngữ có nguồn trùng; cần chọn một cách dịch rõ ràng.")
        seen.add(source)
        checked.append(dict(src=source, tgt=target, note=note,
                            provenance={"kind": provenance["kind"], "reference": reference}))
    return sorted(checked, key=lambda row: row["src"])


def build_glossary(terms=None):
    """Canonical snapshot, versioned by mappings plus provenance, not wall time."""
    checked = _terms([] if terms is None else terms)
    encoded = json.dumps([SCHEMA_VERSION, checked], ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return {"schema_version": SCHEMA_VERSION,
            "version": hashlib.sha256(encoded.encode("utf-8")).hexdigest(), "terms": checked}


def validate_glossary(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "version", "terms"}
            or type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION):
        raise ValueError("Định dạng bảng thuật ngữ không hợp lệ.")
    expected = build_glossary(value["terms"])
    if value["version"] != expected["version"]:
        raise ValueError("Phiên bản bảng thuật ngữ không khớp nội dung đã lưu.")
    return expected


def glossary_snapshot(session):
    """Old sessions honestly have no supplied glossary; never mine their drafts."""
    value = getattr(session, "shared_glossary", None)
    return build_glossary() if value is None else validate_glossary(value)


def set_shared_glossary(session, terms, *, expected_version=None):
    """Optimistic update. Caller persists before launching dependent work."""
    current = glossary_snapshot(session)
    if expected_version is not None and current["version"] != expected_version:
        raise ValueError("Bảng thuật ngữ đã thay đổi; tải lại trước khi cập nhật.")
    updated = build_glossary(terms)
    session.shared_glossary = deepcopy(updated)
    translator = getattr(session, "translator", None)
    if translator is not None:
        translator.glossary = deepcopy(updated)
    return updated
