"""Plan bounded dub shifts without changing the source dialogue timeline.

This module only plans timing. Callers must publish the complete plan together
with audio/caption metadata, and use the same dub times in preview and export.
"""
from dataclasses import dataclass
import math
from typing import Mapping


_EPSILON = 1e-9


@dataclass(frozen=True)
class _Row:
    id: int | str
    start: float
    end: float
    dub_start: float
    dub_end: float
    audio_duration: float | None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _prepare(rows, focus_id, max_shift, *, allow_forward=False):
    max_shift = _number(max_shift)
    if max_shift is None or max_shift < 0 or isinstance(focus_id, bool):
        return None
    try:
        records = list(rows)
    except TypeError:
        return None
    parsed, ids, focus_index = [], set(), None
    previous_end = 0.0
    for record in records:
        if not isinstance(record, Mapping):
            return None
        identity = record.get("id")
        if (isinstance(identity, bool) or not isinstance(identity, (int, str))
                or identity in ids):
            return None
        start, end = _number(record.get("start")), _number(record.get("end"))
        if (start is None or end is None or start < 0 or end <= start
                or start < previous_end - _EPSILON):
            return None
        # Missing fields mean an unchanged source slot. A partly persisted
        # timing pair is not enough evidence to infer a usable dub interval.
        has_start = record.get("dub_start") is not None
        has_end = record.get("dub_end") is not None
        if has_start != has_end:
            return None
        dub_start = _number(record["dub_start"]) if has_start else start
        dub_end = _number(record["dub_end"]) if has_end else end
        if (dub_start is None or dub_end is None or dub_start < 0
                or dub_start < start - max_shift - _EPSILON
                or dub_start > start + (max_shift if allow_forward else 0) + _EPSILON
                or dub_end <= dub_start
                or dub_end > end + (max_shift if allow_forward else 0) + _EPSILON
                or dub_end < end - max_shift - _EPSILON):
            return None
        duration = _number(record.get("audio_duration"))
        if (record.get("status") not in ("READY", "PLAYED")
                or duration is None or duration <= 0):
            duration = None
        if duration is not None and duration > dub_end - dub_start + _EPSILON:
            return None
        if identity == focus_id:
            focus_index = len(parsed)
        parsed.append(_Row(identity, start, end, dub_start, dub_end, duration))
        ids.add(identity)
        previous_end = end
    if focus_index is None:
        return None
    return parsed, focus_index, max_shift


def _end_limit(rows, focus_index):
    end = rows[focus_index].end
    if focus_index + 1 < len(rows):
        end = min(end, rows[focus_index + 1].dub_start)
    return end


def plan_backshift(rows, focus_id, required_duration, max_shift=0.35):
    """Return an atomic ``id -> {dub_start, dub_end}`` plan, or ``None``.

    Rows are in source order and contain immutable ``id/start/end``. Optional
    ``dub_start/dub_end`` default to the source slot. Movable predecessors must
    be READY/PLAYED and provide their complete measured WAV length as
    ``audio_duration``. Semantic uncertainty does not invalidate measured audio.
    Other predecessors keep occupying their source
    slots. Every moved WAV retains its exact duration and starts no more than
    ``max_shift`` seconds before its ORIGINAL source start.

    The focus may expand up to its original source end, subject to the following
    row's existing dub start. Only the necessary contiguous predecessors move;
    no row moves later. The returned plan always includes the focus and includes
    previous rows only when their timing must change. Input rows are untouched.
    """
    required_duration = _number(required_duration)
    prepared = _prepare(rows, focus_id, max_shift)
    if prepared is None or required_duration is None or required_duration <= 0:
        return None
    records, focus_index, max_shift = prepared
    focus = records[focus_index]
    start = min(focus.dub_start, _end_limit(records, focus_index) - required_duration)
    if start < max(0.0, focus.start - max_shift) - _EPSILON:
        return None
    start = max(0.0, focus.start - max_shift, start)
    end = max(start + required_duration, focus.end - max_shift)
    if end > _end_limit(records, focus_index) + _EPSILON:
        return None
    plan = {focus.id: {"dub_start": start, "dub_end": end}}
    cursor = start
    for row in reversed(records[:focus_index]):
        if row.audio_duration is None:
            # Missing audio cannot justify borrowing any portion
            # of this source slot, even if a previous dub timing was shorter.
            if row.end > cursor + _EPSILON:
                return None
            break
        new_start = min(row.dub_start, cursor - row.audio_duration)
        if new_start < max(0.0, row.start - max_shift) - _EPSILON:
            return None
        new_start = max(0.0, row.start - max_shift, new_start)
        new_end = max(new_start + row.audio_duration, row.end - max_shift)
        if new_end > min(row.end, cursor) + _EPSILON:
            return None
        if row.dub_end <= cursor + _EPSILON and new_start == row.dub_start:
            break
        # Shrinking only unused slot padding is sometimes enough; that change
        # still belongs in the plan so the preview selects the next row on time.
        plan[row.id] = {"dub_start": new_start, "dub_end": new_end}
        cursor = new_start
    return plan


def available_dub_duration(rows, focus_id, max_shift=0.35):
    """Maximum complete WAV duration the planner can fit, or zero if invalid.

    This is capacity only: it does not shift rows. A successful synthesis must
    subsequently pass ``plan_backshift`` with its actual measured duration.
    """
    prepared = _prepare(rows, focus_id, max_shift)
    if prepared is None:
        return 0.0
    records, focus_index, max_shift = prepared
    cursor = 0.0
    for row in records[:focus_index]:
        if row.audio_duration is None:
            if cursor > row.dub_start + _EPSILON:
                return 0.0
            cursor = row.end
        else:
            start = max(cursor, 0.0, row.start - max_shift)
            if start > row.dub_start + _EPSILON or start + row.audio_duration > row.end + _EPSILON:
                return 0.0
            cursor = max(start + row.audio_duration, row.end - max_shift)
    focus = records[focus_index]
    earliest = max(cursor, 0.0, focus.start - max_shift)
    capacity = _end_limit(records, focus_index) - earliest
    if capacity <= 0 or earliest > focus.dub_start + _EPSILON:
        return 0.0
    return capacity


def _prepare_reflow(rows, focus_id, max_shift, total_duration):
    total_duration = _number(total_duration)
    prepared = _prepare(rows, focus_id, max_shift, allow_forward=True)
    if prepared is None or total_duration is None or total_duration <= 0:
        return None
    records, _, _ = prepared
    cursor = 0.0
    for row in records:
        if (row.end > total_duration + _EPSILON or row.dub_end > total_duration + _EPSILON
                or row.dub_start < cursor - _EPSILON):
            return None
        cursor = row.dub_end
    return prepared


def _limits(row, duration, max_shift, total_duration, *, reserved=False):
    start_min = max(0.0, row.start - max_shift)
    end_min = row.end - max_shift
    start_max = row.start + max_shift
    end_max = min(total_duration, row.end + max_shift)
    if reserved:
        # Unknown audio has an exact occupied interval, not a minimum. Moving
        # it must preserve every second of its existing reservation.
        start_min = max(start_min, end_min - duration)
        start_max = min(start_max, end_max - duration)
    return start_min, start_max, end_min, end_max


def _reflow_bounds(prepared, total_duration):
    """Earliest focus start and latest focus end from chain constraints.

    Predecessors push only to the right in the earliest schedule; successors
    push only to the left in the latest schedule. Since the graph is a chain,
    one pass in each direction gives exact capacity without duration guessing.
    """
    records, focus_index, max_shift = prepared
    focus = records[focus_index]
    cursor = 0.0
    for row in records[:focus_index]:
        duration = row.audio_duration or row.dub_end - row.dub_start
        low, high, end_low, end_high = _limits(row, duration, max_shift, total_duration,
                                               reserved=row.audio_duration is None)
        start = max(cursor, low)
        end = max(start + duration, end_low)
        if start > high + _EPSILON or end > end_high + _EPSILON:
            return None
        cursor = end
    earliest = max(cursor, 0.0, focus.start - max_shift)
    cursor = total_duration
    for row in reversed(records[focus_index + 1:]):
        duration = row.audio_duration or row.dub_end - row.dub_start
        low, high, end_low, end_high = _limits(row, duration, max_shift, total_duration,
                                               reserved=row.audio_duration is None)
        end = min(cursor, end_high)
        start = min(high, end - duration)
        if start < low - _EPSILON or end < end_low - _EPSILON:
            return None
        cursor = start
    latest = min(cursor, total_duration, focus.end + max_shift)
    if earliest > focus.start + max_shift + _EPSILON or latest < focus.end - max_shift - _EPSILON:
        return None
    return earliest, latest


def _plan_general(prepared, required_duration, total_duration):
    bounds = _reflow_bounds(prepared, total_duration)
    if bounds is None:
        return None
    earliest, latest = bounds
    records, focus_index, max_shift = prepared
    focus = records[focus_index]
    latest_start = min(focus.start + max_shift, latest - required_duration)
    if earliest > latest_start + _EPSILON:
        return None
    # Clamp the current start into the feasible range, changing it by the
    # smallest amount. Then propagate only collisions outward from the focus.
    start = max(earliest, min(focus.dub_start, latest_start))
    end = max(start + required_duration, focus.end - max_shift)
    if end > latest + _EPSILON:
        return None
    plan = {focus.id: {"dub_start": start, "dub_end": end}}
    cursor = start
    for row in reversed(records[:focus_index]):
        if row.dub_end <= cursor + _EPSILON:
            break
        duration = row.audio_duration or row.dub_end - row.dub_start
        new_start = min(row.dub_start, cursor - duration)
        new_end = (new_start + duration if row.audio_duration is None
                   else max(new_start + duration, min(row.dub_end, cursor), row.end - max_shift))
        low, high, end_low, end_high = _limits(row, duration, max_shift, total_duration,
                                               reserved=row.audio_duration is None)
        if (new_start < low - _EPSILON or new_start > high + _EPSILON
                or new_end < end_low - _EPSILON or new_end > min(cursor, end_high) + _EPSILON):
            return None
        plan[row.id] = {"dub_start": new_start, "dub_end": new_end}
        cursor = new_start
    cursor = end
    for row in records[focus_index + 1:]:
        if cursor <= row.dub_start + _EPSILON:
            break
        duration = row.audio_duration or row.dub_end - row.dub_start
        new_start = max(row.dub_start, cursor)
        new_end = (new_start + duration if row.audio_duration is None
                   else max(row.dub_end, new_start + duration, row.end - max_shift))
        low, high, end_low, end_high = _limits(row, duration, max_shift, total_duration,
                                               reserved=row.audio_duration is None)
        if (new_start < low - _EPSILON or new_start > high + _EPSILON
                or new_end < end_low - _EPSILON or new_end > end_high + _EPSILON):
            return None
        plan[row.id] = {"dub_start": new_start, "dub_end": new_end}
        cursor = new_end
    return plan


def plan_reflow(rows, focus_id, required_duration, max_shift=0.35, *, total_duration):
    """Prefer backward borrowing, then solve bounded bidirectional reflow.

    Start AND end stay within ``max_shift`` of their original source timestamps.
    The fallback keeps the focus nearest its current start and propagates only
    collisions to preceding/following rows. Unknown audio keeps its complete
    reserved duration; measured READY
    or PLAYED audio always retains every sample regardless of semantic flags.
    The source timeline and all input rows remain unchanged.
    """
    required_duration = _number(required_duration)
    try:
        rows = list(rows)
    except TypeError:
        return None
    prepared = _prepare_reflow(rows, focus_id, max_shift, total_duration)
    if prepared is None or required_duration is None or required_duration <= 0:
        return None
    focus = prepared[0][prepared[1]]
    if required_duration <= focus.dub_end - focus.dub_start + _EPSILON:
        return {focus.id: {"dub_start": focus.dub_start, "dub_end": focus.dub_end}}
    backward = plan_backshift(rows, focus_id, required_duration, max_shift)
    if backward is not None:
        return backward
    return _plan_general(prepared, required_duration, total_duration)


def available_reflow_duration(rows, focus_id, max_shift=0.35, *, total_duration):
    """Maximum budget usable by ``plan_reflow`` without publishing any shifts."""
    try:
        rows = list(rows)
    except TypeError:
        return 0.0
    prepared = _prepare_reflow(rows, focus_id, max_shift, total_duration)
    if prepared is None:
        return 0.0
    bounds = _reflow_bounds(prepared, total_duration)
    return max(0.0, bounds[1] - bounds[0]) if bounds is not None else 0.0
