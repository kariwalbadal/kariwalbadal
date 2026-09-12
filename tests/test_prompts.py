"""Prompt synthesis: platform addressing, identity lock, and lint rules."""
from adpipe.synth.prompts import (ProductSpec, StyleSpec, synthesize_shot_prompt,
                                  synthesize_multishot_prompt, lint_prompt,
                                  PROMPT_SYNTH_VERSION)

SHOT = {"index": 0, "start": 0.0, "end": 1.0, "duration": 1.0,
        "camera_move": "push_in", "camera_move_confidence": 0.9,
        "shot_scale": "close_up", "shot_scale_confidence": 0.35,
        "role": "hook", "speed_ramp": None, "transition_out": "hard_cut",
        "lighting": {"description": "low_key, high_contrast, saturated"}}


def product():
    return ProductSpec(name="the test can", colour="cobalt", logo_text="AURORA")


def test_seedance_addressing():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec(), platform="seedance")
    assert "@Video1" in p.text and "@Image1" in p.text


def test_kling_elements_addressing_differs():
    """A prompt written with @Image1 addresses nothing on Kling."""
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec(),
                               platform="kling_elements", element_id="el_9")
    assert "<<<el_9>>>" in p.text
    assert "@Image1" not in p.text


def test_job_assignment_is_explicit():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec(), platform="seedance")
    assert "camera movement" in p.text and "editing rhythm" in p.text
    assert "not from @Video1" in p.text


def test_identity_lock_present():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec(), platform="seedance")
    assert "identical to" in p.text
    assert "AURORA" in p.text


def test_named_camera_move_and_motivation():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec(), platform="seedance")
    assert "push in" in p.text
    assert "settles into place" in p.text       # motivated, not bare


def test_slow_motion_metadata_does_not_become_an_instruction():
    """`slow_motion_or_locked` is ambiguous; it must not instruct slow motion."""
    s = dict(SHOT, speed_ramp="slow_motion_or_locked", camera_move="static")
    p = synthesize_shot_prompt(s, product(), StyleSpec(), platform="seedance")
    assert "slow motion" not in p.text.lower()


def test_genuine_ramp_does_become_an_instruction():
    s = dict(SHOT, speed_ramp="ramp_up")
    p = synthesize_shot_prompt(s, product(), StyleSpec(), platform="seedance")
    assert "speed ramping" in p.text


def test_multishot_states_explicit_durations():
    edl = {"shots": [dict(SHOT, index=i, duration=0.5 + i * 0.25) for i in range(3)],
           "audio": {"music_driven_edit": False}}
    p = synthesize_multishot_prompt(edl, product(), StyleSpec())
    assert "3 distinct shots" in p.text
    for d in ("0.50s", "0.75s", "1.00s"):
        assert d in p.text


def test_lint_audio_alone():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec())
    assert any("audio_alone" in x for x in lint_prompt(p, n_images=0, n_videos=0, n_audio=1))


def test_lint_total_file_cap():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec())
    assert any("too_many_refs" in x for x in lint_prompt(p, n_images=9, n_videos=3, n_audio=1))


def test_lint_clean_for_normal_case():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec())
    assert lint_prompt(p, n_images=1, n_videos=1) == []


def test_version_is_exposed():
    p = synthesize_shot_prompt(SHOT, product(), StyleSpec())
    assert p.synth_version == PROMPT_SYNTH_VERSION
