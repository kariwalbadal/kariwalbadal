"""Reference decomposition orchestrator: video in, validated EDL out.

Order matters. Shot boundaries come first because every later stage is scoped
to a shot; get the cut list wrong and everything after it is wrong, which is
why `decompose` refuses to emit an EDL that fails schema validation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import hashlib
import json
import math
import subprocess

import numpy as np

from .edl import EDL_SCHEMA_VERSION, Shot, validate_edl
from . import shots as shotmod
from . import motion as motionmod
from . import frames as framemod
from . import appearance as appmod
from . import transitions as transmod
from . import audio as audiomod
from . import ostext
from .fuse import fuse_cuts


def probe(video_path: str) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,nb_frames,codec_name",
         "-show_entries", "format=duration", "-of", "json", video_path],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    num, _, den = (st.get("r_frame_rate") or "25/1").partition("/")
    fps = float(num) / float(den or 1)
    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    g = math.gcd(w, h) or 1
    return {"path": video_path, "width": w, "height": h, "fps": round(fps, 6),
            "codec": st.get("codec_name"),
            "duration": float(d.get("format", {}).get("duration") or 0.0),
            "aspect_ratio": f"{w//g}:{h//g}" if w and h else "unknown"}


def _sha256(path: str, limit: int = 32 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
            limit -= len(chunk)
            if limit <= 0:
                break
    return h.hexdigest()


def assign_roles(durations: list[float]) -> list[str]:
    """Label each shot by its function in ad grammar.

    Ads open fast and end slow, so a shot's role is read from where it sits in
    the film and how its length compares to the film's own median rather than
    from absolute seconds.
    """
    n = len(durations)
    if n == 0:
        return []
    med = float(np.median(durations))
    roles: list[str] = []
    for i, d in enumerate(durations):
        frac = i / max(1, n - 1)
        if frac <= 0.30 and d <= med:
            roles.append("hook")
        elif frac >= 0.80 and d >= med:
            roles.append("payoff")
        elif frac >= 0.92:
            roles.append("endcard")
        elif frac < 0.60:
            roles.append("build")
        else:
            roles.append("mid")
    return roles


def rhythm_profile(durations: list[float]) -> str:
    med = float(np.median(durations))
    if med <= 0.7:
        return "fast_cut_hook_heavy"
    if med <= 1.5:
        return "brisk_commercial"
    if med <= 3.0:
        return "moderate"
    return "slow_hero"


def decompose(video_path: str, out_dir: str,
              truth_cuts: Optional[list[float]] = None,
              dense_every: float = 0.5,
              do_ocr: bool = True,
              detector_cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Full decomposition. Writes frames under out_dir and returns the EDL."""
    out = Path(out_dir); (out / "frames").mkdir(parents=True, exist_ok=True)
    src = probe(video_path)
    src["sha256"] = _sha256(video_path)

    # --- 1. shot boundaries (appearance) + motion discontinuity, then fuse ----
    if truth_cuts:
        spans, app_cuts, report = shotmod.detect_shots_auto(video_path, truth_cuts=truth_cuts)
        cfg_used = report["config"]
    else:
        cfg_used = dict(detector_cfg or shotmod.VALIDATED_DEFAULT)
        spans, app_cuts = shotmod.detect_shots(video_path, **cfg_used)
        report = {"tuned": False, "config": cfg_used, "accuracy": None}

    all_motion = motionmod.analyse_motion(video_path)
    mot_cuts, _ = motionmod.detect_cuts_from_flow(video_path, motions=all_motion)
    dsig = {m.t: m.discontinuity for m in all_motion}
    strength = {c: dsig.get(min(dsig, key=lambda t: abs(t - c)), 0.0) for c in mot_cuts}
    fused = fuse_cuts(app_cuts, mot_cuts, strength, policy="review_flags")

    cuts = fused.cuts
    bounds = [0.0] + list(cuts) + [src["duration"]]
    spans = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
             if bounds[i + 1] - bounds[i] > 1e-3]

    durations = [round(b - a, 6) for a, b in spans]
    roles = assign_roles(durations)

    # --- 2. per-shot analysis ------------------------------------------------
    shot_objs: list[Shot] = []
    for i, (a, b) in enumerate(spans):
        sh = Shot(index=i, start=round(a, 6), end=round(b, 6), duration=round(b - a, 6),
                  role=roles[i])
        seg = [m for m in all_motion if a <= m.t < b]
        move, conf, metrics = motionmod.classify_camera_move(seg, sh.duration)
        sh.camera_move, sh.camera_move_confidence, sh.motion_metrics = move, round(conf, 3), metrics
        sh.speed_ramp = motionmod.detect_speed_ramp(seg)

        sh.frames = framemod.extract_shot_frames(
            video_path, i, a, b, str(out / "frames"), dense_every=dense_every)
        paths = [f.path for f in sh.frames]
        sh.palette = appmod.palette(paths)
        sh.lighting = appmod.lighting(paths)
        sh.shot_scale, sh.shot_scale_confidence = appmod.shot_scale_heuristic(paths)
        shot_objs.append(sh)

    # --- 3. transitions at each boundary ------------------------------------
    def flow_mag(t: float, side: str) -> float:
        w = [m for m in all_motion
             if (t - 0.25 <= m.t < t if side == "before" else t < m.t <= t + 0.25)]
        return float(np.mean([math.hypot(m.dx, m.dy) for m in w])) if w else 0.0

    for i, sh in enumerate(shot_objs):
        if i == len(shot_objs) - 1:
            sh.transition_out, sh.transition_out_confidence = "end", 1.0
            continue
        lab, conf, _ = transmod.classify_transition(
            video_path, sh.end, src["fps"],
            flow_before=flow_mag(sh.end, "before"), flow_after=flow_mag(sh.end, "after"))
        sh.transition_out, sh.transition_out_confidence = lab, round(conf, 3)

    shot_dicts = [s.to_dict() for s in shot_objs]

    # --- 4. audio -----------------------------------------------------------
    aud = audiomod.analyse_audio(video_path, cuts, out_wav=str(out / "audio.wav"))
    beats = aud.get("beat_times") or []
    tol = aud.get("beat_tolerance", 0.08)
    for sh in shot_dicts:
        sh["on_beat"] = bool(beats and min((abs(sh["start"] - b) for b in beats),
                                           default=9e9) <= tol)

    # --- 5. on-screen text --------------------------------------------------
    os_text = ostext.inventory_onscreen_text(shot_dicts) if do_ocr else []

    edl: dict[str, Any] = {
        "schema_version": EDL_SCHEMA_VERSION,
        "source": src,
        "timing": {
            "n_shots": len(shot_dicts),
            "cut_times": [round(c, 6) for c in cuts],
            "shot_durations": durations,
            "median_shot_duration": round(float(np.median(durations)), 4),
            "p10_shot_duration": round(float(np.percentile(durations, 10)), 4),
            "p90_shot_duration": round(float(np.percentile(durations, 90)), 4),
            "cuts_per_second": round(len(cuts) / max(1e-6, src["duration"]), 4),
            "rhythm_profile": rhythm_profile(durations),
        },
        "shots": shot_dicts,
        "audio": aud,
        "onscreen_text": os_text,
        "provenance": {
            "detector": cfg_used,
            "detector_tuned_against_truth": bool(truth_cuts),
            "fusion_policy": fused.policy,
            "review_flags": fused.review_flags,
            "motion_only_candidates": [round(x, 3) for x in fused.motion_only],
            "appearance_only_cuts": [round(x, 3) for x in fused.appearance_only],
            "n_motion_pairs": len(all_motion),
            "accuracy_vs_truth": (report["accuracy"].__dict__ if report.get("accuracy") else None),
        },
    }
    validate_edl(edl)
    (out / "edl.json").write_text(json.dumps(edl, indent=2, default=str))
    return edl
