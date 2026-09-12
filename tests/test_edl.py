"""EDL schema and cross-field invariants."""
import copy
import json
import pytest

from adpipe.decompose.edl import validate_edl, EDL_SCHEMA_VERSION


def base_edl():
    return {
        "schema_version": EDL_SCHEMA_VERSION,
        "source": {"path": "x.mp4", "duration": 3.0, "fps": 24.0,
                   "width": 1280, "height": 720},
        "timing": {"n_shots": 2, "cut_times": [1.0],
                   "shot_durations": [1.0, 2.0]},
        "shots": [
            {"index": 0, "start": 0.0, "end": 1.0, "duration": 1.0},
            {"index": 1, "start": 1.0, "end": 3.0, "duration": 2.0},
        ],
        "audio": {"has_audio": False},
    }


def test_valid_edl_passes():
    validate_edl(base_edl())


def test_shot_count_mismatch_rejected():
    e = base_edl(); e["timing"]["n_shots"] = 3
    with pytest.raises(ValueError, match="n_shots"):
        validate_edl(e)


def test_cut_count_must_be_shots_minus_one():
    e = base_edl(); e["timing"]["cut_times"] = [1.0, 2.0]
    with pytest.raises(ValueError, match="cut_times"):
        validate_edl(e)


def test_gap_between_shots_rejected():
    """A gap means assembled output would drift off the reference timing.

    `duration` is adjusted to match the new span so this isolates the gap
    check rather than tripping the duration invariant first.
    """
    e = base_edl()
    e["shots"][1]["start"] = 1.5
    e["shots"][1]["duration"] = 1.5
    e["timing"]["shot_durations"] = [1.0, 1.5]
    with pytest.raises(ValueError, match="gap/overlap"):
        validate_edl(e)


def test_duration_must_match_span():
    e = base_edl(); e["shots"][0]["duration"] = 0.5
    with pytest.raises(ValueError, match="duration"):
        validate_edl(e)


def test_out_of_order_index_rejected():
    e = base_edl(); e["shots"][0]["index"] = 5
    with pytest.raises(ValueError, match="out of order"):
        validate_edl(e)


def test_shot_past_source_duration_rejected():
    e = base_edl(); e["shots"][1]["end"] = 9.0
    e["shots"][1]["duration"] = 8.0
    e["timing"]["shot_durations"] = [1.0, 8.0]
    with pytest.raises(ValueError, match="beyond source duration"):
        validate_edl(e)
