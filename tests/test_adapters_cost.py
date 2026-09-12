"""Adapter validation, budget guards and packing economics.

These cover the paths that spend money, where a silent failure is expensive
rather than merely wrong.
"""
import pytest

from adpipe.adapters.base import (GenerationRequest, ReferenceAsset,
                                  ValidationFailed, quantise_duration)
from adpipe.adapters.kling import KlingAdapter
from adpipe.adapters.mock import MockAdapter
from adpipe.cost import (CostLedger, BudgetExceeded, plan_generation_budget,
                         budget_report)


def req(**kw):
    base = dict(shot_index=0, prompt="a can rotates", duration_s=5.0,
                resolution="720p")
    base.update(kw)
    return GenerationRequest(**base)


# --- Kling contract, verified from who_am_i 2026-09-12 ---------------------

def test_i2v_rejects_1080p():
    """Every image_to_video model lists 720p only; 1080p is membership-gated."""
    a = KlingAdapter(model="kling-video-v3_0_omni")
    with pytest.raises(ValidationFailed, match="1080p"):
        a.build_mcp_payload(req(resolution="1080p"))


def test_duration_floor_enforced():
    a = KlingAdapter(model="kling-video-v3_0_omni")
    with pytest.raises(ValidationFailed):
        a.build_mcp_payload(req(duration_s=2.0))


def test_v2_6_allows_only_5_or_10s():
    a = KlingAdapter(model="kling-video-v2_6")
    with pytest.raises(ValidationFailed):
        a.build_mcp_payload(req(duration_s=7.0))
    a.build_mcp_payload(req(duration_s=10.0))


def test_v2_6_tail_image_requires_1080p():
    """Documented trap: tail_image at 720p is silently unavailable."""
    a = KlingAdapter(model="kling-video-v2_6")
    with pytest.raises(ValidationFailed, match="tail_image requires"):
        a.build_mcp_payload(req(duration_s=5.0, extra={"tail_image": "u"}))


def test_motion_control_needs_person_or_animal():
    """Its subject input expects a person/animal, so a bare product is invalid."""
    a = KlingAdapter(model="kling-video-v3_0-motion")
    with pytest.raises(ValidationFailed, match="person or animal"):
        a.build_mcp_payload(req(resolution="1080p"))
    a.build_mcp_payload(req(resolution="1080p",
                            extra={"subject_is_person_or_animal": True}))


def test_multi_shot_rejected_on_model_without_it():
    a = KlingAdapter(model="kling-video-v2_6")
    with pytest.raises(ValidationFailed, match="prefer_multi_shots"):
        a.build_mcp_payload(req(duration_s=5.0, extra={"prefer_multi_shots": True}))


def test_reference_images_addressed_as_image_n():
    a = KlingAdapter(model="kling-video-v3_0_omni")
    p = a.build_mcp_payload(req(references=[
        ReferenceAsset("image", "u1"), ReferenceAsset("image", "u2")]))
    assert [i["name"] for i in p["inputs"]] == ["image_1", "image_2"]


def test_audio_cannot_travel_alone():
    a = MockAdapter()
    with pytest.raises(ValidationFailed, match="audio reference must travel"):
        a.validate(req(references=[ReferenceAsset("audio", "a.mp3")]))


def test_total_file_cap():
    a = MockAdapter()
    a.max_total_files = 3
    with pytest.raises(ValidationFailed):
        a.validate(req(references=[ReferenceAsset("image", f"u{i}") for i in range(4)]))


# --- duration quantisation -------------------------------------------------

def test_quantise_snaps_up_not_down():
    """Snapping down would force a hold-frame that reads as a freeze."""
    assert quantise_duration(1.12, [3.0, 4.0, 5.0], 3.0, 15.0) == 3.0
    assert quantise_duration(4.2, [3.0, 4.0, 5.0], 3.0, 15.0) == 5.0


def test_quantise_respects_ceiling():
    assert quantise_duration(99.0, [3.0, 10.0], 3.0, 10.0) == 10.0


# --- ledger ----------------------------------------------------------------

def test_ledger_blocks_overspend_before_submission():
    led = CostLedger(monthly_cap_usd=10.0)
    led.record("generation", "x", 0, 9.5, "succeeded")
    with pytest.raises(BudgetExceeded):
        led.authorise(1.0)


def test_failed_attempts_still_count_as_spend():
    """Retries are where per-video cost actually goes."""
    led = CostLedger(monthly_cap_usd=10.0)
    led.record("generation", "x", 0, 3.0, "failed")
    led.record("retry", "x", 0, 3.0, "succeeded")
    assert led.spent == 6.0
    assert led.summary()["spend_on_failed_attempts_usd"] == 3.0


def test_ledger_persists_across_instances(tmp_path):
    p = str(tmp_path / "l.jsonl")
    CostLedger(500.0, path=p).record("generation", "x", 0, 4.0, "succeeded")
    assert CostLedger(500.0, path=p).spent == 4.0


# --- packing economics -----------------------------------------------------

def _edl(durations):
    shots, t = [], 0.0
    for i, d in enumerate(durations):
        shots.append({"index": i, "start": t, "end": t + d, "duration": d,
                      "camera_move": "static", "transition_out": "hard_cut"})
        t += d
    return {"shots": shots, "timing": {"n_shots": len(shots)},
            "source": {"duration": t}}


def test_packing_reduces_billed_seconds_on_fast_cuts():
    """Sub-second ad cuts against a 4s floor is the core cost problem."""
    edl = _edl([0.5] * 8)
    un = budget_report(plan_generation_budget(edl, 4.0, 15.0,
                                              [float(d) for d in range(4, 16)],
                                              pack=False), 0.18)
    pk = budget_report(plan_generation_budget(edl, 4.0, 15.0,
                                              [float(d) for d in range(4, 16)],
                                              pack=True), 0.18)
    assert un["n_generations"] == 8 and pk["n_generations"] == 1
    assert pk["billed_seconds"] < un["billed_seconds"]
    assert un["waste_multiplier"] > pk["waste_multiplier"]


def test_packing_respects_max_duration():
    edl = _edl([5.0] * 6)
    groups = plan_generation_budget(edl, 3.0, 15.0, None, pack=True)
    assert all(g.edl_seconds <= 15.0 for g in groups)


def test_non_hard_cut_breaks_a_pack():
    """A dissolve at the seam needs two real clips to render across."""
    edl = _edl([1.0, 1.0, 1.0])
    edl["shots"][0]["transition_out"] = "dissolve"
    groups = plan_generation_budget(edl, 3.0, 15.0, None, pack=True)
    assert len(groups) >= 2


def test_whip_shot_is_not_packed():
    edl = _edl([1.0, 1.0, 1.0])
    edl["shots"][1]["camera_move"] = "whip_pan"
    groups = plan_generation_budget(edl, 3.0, 15.0, None, pack=True)
    assert any(g.shot_indices == [1] for g in groups)
