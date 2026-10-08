"""Pure regressions for complete speech borrowing bounded preceding silence."""
from copy import deepcopy

import pytest

from core.engines.alignment.dub_timing import (
    available_dub_duration, plan_backshift, available_reflow_duration, plan_reflow,
)


def row(identity, start, end, audio_duration=None, **extra):
    return {"id": identity, "start": start, "end": end,
            "status": "READY" if audio_duration is not None else "WAITING",
            "audio_duration": audio_duration, **extra}


def test_real_number_shape_borrows_gap_by_moving_complete_previous_wav():
    rows = [row(4, 4.98, 6.44, 1.4355), row(5, 6.84, 7.72, .877833),
            row(6, 7.72, 8.34), row(7, 8.34, 9.0)]
    before = deepcopy(rows)
    duration = .8563 / 1.15
    plan = plan_backshift(rows, 6, duration)
    assert set(plan) == {5, 6}
    assert plan[6]["dub_end"] == pytest.approx(8.34)
    assert plan[6]["dub_start"] == pytest.approx(8.34 - duration)
    assert plan[5]["dub_end"] == pytest.approx(plan[6]["dub_start"])
    assert plan[5]["dub_end"] - plan[5]["dub_start"] == pytest.approx(.877833)
    assert plan[5]["dub_start"] > 6.44
    assert rows == before


def test_zero_gap_cannot_invent_capacity_at_video_start():
    rows = [row(0, 0, 1, 1), row(1, 1, 2, 1), row(2, 2, 2.62)]
    assert plan_backshift(rows, 2, .75) is None
    assert available_dub_duration(rows, 2) == pytest.approx(.62)


def test_minimum_reflow_moves_only_the_needed_contiguous_block():
    rows = [row(0, 0, .5, .5), row(1, 1, 2, 1), row(2, 2, 3, 1), row(3, 3, 3.5),
            row(4, 3.5, 4)]
    plan = plan_backshift(rows, 3, .7)
    assert set(plan) == {1, 2, 3}
    assert plan[1] == pytest.approx({"dub_start": .8, "dub_end": 1.8})
    assert plan[2] == pytest.approx({"dub_start": 1.8, "dub_end": 2.8})
    assert plan[3] == pytest.approx({"dub_start": 2.8, "dub_end": 3.5})


def test_shorter_previous_audio_releases_padding_without_moving_speech():
    rows = [row(0, 0, 1, .7), row(1, 1, 1.5)]
    plan = plan_backshift(rows, 1, .7)
    assert plan[0] == pytest.approx({"dub_start": 0, "dub_end": .7})
    assert plan[1] == pytest.approx({"dub_start": .8, "dub_end": 1.5})


def test_fitting_focus_does_not_move_or_shrink_previous_rows():
    rows = [row(0, 0, 1, .7), row(1, 1, 2), row(2, 2, 3)]
    assert plan_backshift(rows, 1, .6) == {1: {"dub_start": 1, "dub_end": 1.65}}


@pytest.mark.parametrize("predecessor", [
    row(0, 1, 2),
    row(0, 1, 2, .5, status="FAILED"),
    row(0, 1, 2, 0),
    row(0, 1, 2, float("nan")),
])
def test_unavailable_previous_audio_reserves_original_slot(predecessor):
    rows = [predecessor, row(1, 2, 2.5)]
    assert plan_backshift(rows, 1, .7) is None
    assert available_dub_duration(rows, 1) == pytest.approx(.5)


def test_existing_shift_budget_is_relative_to_original_source_start():
    rows = [row(0, 1, 2, 1, dub_start=.8, dub_end=1.8), row(1, 2, 2.5)]
    plan = plan_backshift(rows, 1, .8)
    assert plan[0] == pytest.approx({"dub_start": .7, "dub_end": 1.7})
    assert plan[1] == pytest.approx({"dub_start": 1.7, "dub_end": 2.5})
    assert plan_backshift(rows, 1, .851) is None
    assert available_dub_duration(rows, 1) == pytest.approx(.85)


def test_focus_already_shifted_never_moves_later():
    rows = [row(0, 1, 2, dub_start=.8, dub_end=1.8)]
    assert plan_backshift(rows, 0, .5) == {0: {"dub_start": .8, "dub_end": 1.65}}


def test_following_shifted_row_is_unchanged_and_limits_focus_end():
    rows = [row(0, 1, 2), row(1, 2, 3, .9, dub_start=1.9, dub_end=2.8)]
    before = deepcopy(rows)
    plan = plan_backshift(rows, 0, 1)
    assert plan == {0: pytest.approx({"dub_start": .9, "dub_end": 1.9})}
    assert available_dub_duration(rows, 0) == pytest.approx(1.25)
    assert rows == before


def test_zero_shift_limit_only_uses_existing_slot_and_padding():
    rows = [row(0, 1, 2, .7), row(1, 2, 2.5)]
    assert available_dub_duration(rows, 1, max_shift=0) == .5
    assert plan_backshift(rows, 1, .6, max_shift=0) is None


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), True, "1"])
def test_invalid_required_duration_is_rejected(duration):
    assert plan_backshift([row(0, 1, 2)], 0, duration) is None


@pytest.mark.parametrize("rows,focus,shift", [
    ([row(0, 1, 2)], 9, .35),
    ([row(0, 1, 2)], 0, -1),
    ([row(0, 1, 2)], 0, float("nan")),
    ([row(0, 1, 2)], 0, True),
    ([row(0, 1, 2), row(0, 2, 3)], 0, .35),
    ([row(0, 1, 2), row(1, 1.9, 3)], 1, .35),
    ([row(0, -1, 2)], 0, .35),
    ([row(0, 1, float("inf"))], 0, .35),
    ([row(0, 1, 2, dub_start=.8)], 0, .35),
    ([row(0, 1, 2, dub_start=.5, dub_end=1.5)], 0, .35),
    ([row(0, 1, 2, dub_start=1.1, dub_end=1.9)], 0, .35),
    ([row(0, 1, 2, 1.1)], 0, .35),
])
def test_invalid_source_or_existing_timing_cannot_produce_plan(rows, focus, shift):
    assert plan_backshift(rows, focus, .5, shift) is None
    assert available_dub_duration(rows, focus, shift) == 0


def test_capacity_is_feasible_and_any_material_excess_is_not():
    rows = [row(0, 0, .4), row(1, 1, 2, .9), row(2, 2, 2.5, .45), row(3, 2.5, 3)]
    capacity = available_dub_duration(rows, 3)
    assert capacity == pytest.approx(.85)
    assert plan_backshift(rows, 3, capacity) is not None
    assert plan_backshift(rows, 3, capacity + .00001) is None


def test_repeated_edits_preserve_all_complete_audio_without_cumulative_drift():
    rows = [row(0, 1, 2, .9), row(1, 2, 3, .9), row(2, 3, 3.5)]
    first = plan_backshift(rows, 2, .8)
    for item in rows:
        item.update(first.get(item["id"], {}))
    rows[2].update(status="READY", audio_duration=.8)
    before = deepcopy(rows)
    second = plan_backshift(rows, 2, .84)
    assert second is not None
    assert plan_backshift(rows, 2, .86) is None
    assert rows == before
    updated = [{**item, **second.get(item["id"], {})} for item in rows]
    for left, right in zip(updated, updated[1:]):
        assert left["dub_end"] <= right["dub_start"] + 1e-9
    for item in updated:
        assert item["start"] - .35 <= item["dub_start"] + 1e-9
        assert item["dub_end"] <= item["end"] + 1e-9
        expected = .84 if item["id"] == 2 else item["audio_duration"]
        assert item["dub_end"] - item["dub_start"] == pytest.approx(expected)


def test_real_command_shape_uses_next_gap_without_changing_source_or_semantics():
    rows = [row(4, 4.98, 6.44, 1.449, dub_start=4.98, dub_end=6.429),
            row(5, 6.84, 7.72, .877833333333333, dub_start=6.566375, dub_end=7.444208333333333),
            row(6, 7.72, 8.34, .752125, dub_start=7.444208333333333, dub_end=8.196333333333333),
            row(7, 8.34, 9, .763958333333334, dub_start=8.196333333333333, dub_end=8.960291666666667),
            row(8, 9, 9.84, .879708333333333, dub_start=8.960291666666667, dub_end=9.84, needs_review=True),
            row(9, 9.84, 10.36), row(10, 10.36, 11.22), row(11, 11.72, 12.42)]
    before = deepcopy(rows)
    duration = .959 / 1.15
    plan = plan_reflow(rows, 9, duration, total_duration=15.018688)
    assert set(plan) == {9, 10}
    assert plan[9] == pytest.approx({"dub_start": 9.84, "dub_end": 9.84 + duration})
    assert plan[10] == pytest.approx({"dub_start": 9.84 + duration,
                                     "dub_end": 9.84 + duration + .86})
    assert plan[10]["dub_end"] < 11.72
    assert available_reflow_duration(rows, 9, total_duration=15.018688) == pytest.approx(.87)
    assert rows == before


def test_forward_without_future_gap_fails_at_video_end():
    rows = [row(0, 0, 1, 1), row(1, 1, 1.5), row(2, 1.5, 2.5)]
    assert plan_reflow(rows, 1, .7, total_duration=2.5) is None
    assert available_reflow_duration(rows, 1, total_duration=2.5) == pytest.approx(.5)


def test_forward_reflow_preserves_all_unready_reserved_intervals():
    rows = [row(0, 0, 1, 1), row(1, 1, 1.5), row(2, 1.5, 2.5), row(3, 2.5, 3),
            row(4, 3.4, 4)]
    plan = plan_reflow(rows, 1, .8, total_duration=4)
    assert set(plan) == {1, 2, 3}
    assert plan[2] == pytest.approx({"dub_start": 1.8, "dub_end": 2.8})
    assert plan[3] == pytest.approx({"dub_start": 2.8, "dub_end": 3.3})


def test_forward_reuses_measured_audio_padding_even_with_semantic_uncertainty():
    rows = [row(0, 0, 1, 1), row(1, 1, 1.5),
            row(2, 1.5, 2.5, .6, needs_review=True), row(3, 2.5, 3)]
    plan = plan_reflow(rows, 1, .8, total_duration=3)
    assert set(plan) == {1, 2}
    assert plan[2] == pytest.approx({"dub_start": 1.8, "dub_end": 2.5})
    assert rows[2]["needs_review"] is True


def test_backward_can_shift_known_audio_without_approving_its_translation():
    rows = [row(0, 1, 2, 1, needs_review=True), row(1, 2, 2.5)]
    plan = plan_backshift(rows, 1, .7)
    assert plan[0] == pytest.approx({"dub_start": .8, "dub_end": 1.8})
    assert rows[0]["needs_review"] is True


def test_backward_plan_is_preferred_when_both_directions_are_available():
    rows = [row(0, 1, 2, 1), row(1, 2, 2.5), row(2, 2.5, 3.5)]
    assert plan_reflow(rows, 1, .7, total_duration=4) == plan_backshift(rows, 1, .7)


def test_forward_shift_limit_is_relative_to_original_even_after_restore():
    rows = [row(0, 0, 1, 1), row(1, 1, 1.5), row(2, 1.5, 2.5), row(3, 3, 4)]
    first = plan_reflow(rows, 1, .8, total_duration=4)
    for item in rows:
        item.update(first.get(item["id"], {}))
    rows[1].update(status="READY", audio_duration=.8)
    before = deepcopy(rows)
    second = plan_reflow(rows, 1, .84, total_duration=4)
    assert second[2] == pytest.approx({"dub_start": 1.84, "dub_end": 2.84})
    assert plan_reflow(rows, 1, .851, total_duration=4) is None
    assert available_reflow_duration(rows, 1, total_duration=4) == pytest.approx(.85)
    assert rows == before


def test_later_shifted_sentence_can_resume_with_its_reserved_full_duration():
    rows = [row(0, 0, 1, 1), row(1, 1, 1.5, .8, dub_start=1, dub_end=1.8),
            row(2, 1.5, 2.5, dub_start=1.8, dub_end=2.8), row(3, 3, 4)]
    plan = plan_reflow(rows, 2, 1, total_duration=4)
    assert plan == {2: pytest.approx({"dub_start": 1.8, "dub_end": 2.8})}
    assert available_reflow_duration(rows, 2, total_duration=4) == pytest.approx(1.05)


def test_short_focus_preserves_end_offset_limit_using_silent_slot_tail():
    rows = [row(0, 1, 3)]
    plan = plan_reflow(rows, 0, .1, total_duration=3)
    assert plan == {0: {"dub_start": 1, "dub_end": 2.65}}


@pytest.mark.parametrize("duration", [None, 0, -1, float("nan"), True, "2"])
def test_reflow_requires_valid_actual_video_duration(duration):
    rows = [row(0, 0, 1)]
    assert plan_reflow(rows, 0, .8, total_duration=duration) is None
    assert available_reflow_duration(rows, 0, total_duration=duration) == 0


def test_reflow_rejects_existing_overlap_and_out_of_video_rows():
    rows = [row(0, 0, 1, 1), row(1, 1, 2, dub_start=.8, dub_end=1.8)]
    assert plan_reflow(rows, 1, .8, total_duration=2) is None
    assert available_reflow_duration(rows, 1, total_duration=2) == 0
    assert plan_reflow([row(0, 0, 2)], 0, 1, total_duration=1.9) is None
