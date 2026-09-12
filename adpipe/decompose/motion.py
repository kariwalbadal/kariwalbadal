"""Optical-flow analysis: camera-move classification and cut detection.

Two jobs share one flow pass.

1. `classify_camera_move` -- decomposes inter-frame motion into translation,
   scale and rotation to name the camera move for each shot.

2. `flow_cut_scores` -- detects cuts from motion *discontinuity* rather than
   appearance. This exists because appearance-delta detectors provably miss a
   cut between two moments of the same clip (same palette, grain and subject);
   measured at recall 0.867 on real footage, see docs/EDL-VALIDATION.md.
   Correspondence does not survive a cut even when appearance does, so feature
   tracking collapses at the boundary and that collapse is the signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import math

import cv2
import numpy as np

TRACK_W = 384          # flow is computed downscaled; motion is a low-freq signal
MAX_CORNERS = 400


@dataclass
class PairMotion:
    """Motion between one consecutive frame pair."""
    t: float
    dx: float = 0.0            # px translation, normalised to TRACK_W
    dy: float = 0.0
    dscale: float = 1.0        # >1 pushing in
    drot: float = 0.0          # radians
    tracked_frac: float = 0.0  # fraction of corners tracked forward+back
    inlier_frac: float = 0.0   # fraction consistent with one affine model
    fb_error: float = float("nan")   # median forward-backward reprojection error
    discontinuity: float = 0.0 # 0..1, high = correspondence broke


def _prep(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = TRACK_W / float(w)
    small = cv2.resize(frame, (TRACK_W, max(1, int(round(h * scale)))),
                       interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def _pair_motion(g0: np.ndarray, g1: np.ndarray, t: float) -> PairMotion:
    """Motion between two prepped grey frames.

    Translation is reported as the displacement of the frame CENTRE. The affine
    matrix from estimateAffinePartial2D carries translation relative to the
    origin, so a pure rotation about the centre shows up there as a large bogus
    translation -- which made orbits classify as tilts until this was fixed.
    """
    pm = PairMotion(t=t)
    p0 = cv2.goodFeaturesToTrack(g0, maxCorners=MAX_CORNERS, qualityLevel=0.01,
                                 minDistance=7, blockSize=7)
    if p0 is None or len(p0) < 12:
        # A featureless frame cannot be tracked; that is not evidence of a cut.
        pm.discontinuity = 0.0
        return pm

    lk = dict(winSize=(21, 21), maxLevel=3,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    p1, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, p0, None, **lk)
    if p1 is None:
        pm.discontinuity = 1.0
        return pm
    # Forward-backward check: re-track to the origin frame and measure drift.
    p0r, st2, _ = cv2.calcOpticalFlowPyrLK(g1, g0, p1, None, **lk)
    if p0r is None:
        pm.discontinuity = 1.0
        return pm

    good = (st.reshape(-1) == 1) & (st2.reshape(-1) == 1)
    n0 = len(p0)
    if good.sum() < 8:
        pm.tracked_frac = float(good.sum()) / n0
        pm.discontinuity = 1.0
        return pm

    a = p0.reshape(-1, 2)[good]
    b = p1.reshape(-1, 2)[good]
    back = p0r.reshape(-1, 2)[good]
    fb = np.linalg.norm(a - back, axis=1)
    keep = fb < 1.5                        # drifted points are not correspondences
    pm.fb_error = float(np.median(fb))
    pm.tracked_frac = float(keep.sum()) / n0

    if keep.sum() < 8:
        pm.discontinuity = 1.0
        return pm
    a, b = a[keep], b[keep]

    M, inliers = cv2.estimateAffinePartial2D(
        a, b, method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=2000)
    if M is None or inliers is None:
        pm.discontinuity = 1.0
        return pm
    pm.inlier_frac = float(inliers.sum()) / len(a)
    h, w = g0.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    mapped_x = M[0, 0] * cx + M[0, 1] * cy + M[0, 2]
    mapped_y = M[1, 0] * cx + M[1, 1] * cy + M[1, 2]
    pm.dx = float(mapped_x - cx); pm.dy = float(mapped_y - cy)
    pm.dscale = float(math.hypot(M[0, 0], M[1, 0])) or 1.0
    pm.drot = float(math.atan2(M[1, 0], M[0, 0]))

    # Correspondence quality, not appearance, drives the discontinuity score.
    pm.discontinuity = float(np.clip(
        0.55 * (1.0 - pm.tracked_frac) + 0.45 * (1.0 - pm.inlier_frac), 0.0, 1.0))
    return pm


def analyse_motion(video_path: str, start: float = 0.0,
                   end: Optional[float] = None,
                   stride: int = 1) -> list[PairMotion]:
    """Per-frame-pair motion across [start, end)."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if start > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(start * fps)))
    out: list[PairMotion] = []
    ok, prev = cap.read()
    if not ok:
        cap.release(); return out
    gprev = _prep(prev)
    idx = int(round(start * fps))
    while True:
        for _ in range(stride):
            ok, cur = cap.read()
            idx += 1
            if not ok:
                break
        if not ok:
            break
        t = idx / fps
        if end is not None and t > end:
            break
        g = _prep(cur)
        out.append(_pair_motion(gprev, g, t))
        gprev = g
    cap.release()
    return out


def flow_cut_scores(video_path: str) -> list[PairMotion]:
    return analyse_motion(video_path)


def detect_cuts_from_flow(video_path: str, motions: Optional[list[PairMotion]] = None,
                          abs_floor: float = 0.42, z_thresh: float = 3.0,
                          min_gap: float = 0.20) -> tuple[list[float], list[PairMotion]]:
    """Cut times from motion discontinuity peaks.

    A peak must clear both an absolute floor and a local-median z-score, because
    handheld and whip-pan shots raise the floor inside a shot; only a cut makes
    correspondence fail relative to its own neighbourhood.
    """
    ms = motions if motions is not None else analyse_motion(video_path)
    if not ms:
        return [], ms
    d = np.array([m.discontinuity for m in ms], dtype=float)
    ts = np.array([m.t for m in ms], dtype=float)

    win = 25
    cuts: list[float] = []
    for i in range(len(d)):
        lo, hi = max(0, i - win), min(len(d), i + win + 1)
        nb = np.concatenate([d[lo:i], d[i + 1:hi]])
        if nb.size < 4:
            continue
        med = float(np.median(nb))
        mad = float(np.median(np.abs(nb - med))) or 1e-6
        z = (d[i] - med) / (1.4826 * mad)
        is_local_max = d[i] >= d[max(0, i - 1):min(len(d), i + 2)].max()
        if d[i] >= abs_floor and z >= z_thresh and is_local_max:
            cuts.append(float(ts[i]))

    deduped: list[float] = []
    for c in cuts:
        if not deduped or c - deduped[-1] >= min_gap:
            deduped.append(c)
        elif d[int(np.argmin(np.abs(ts - c)))] > d[int(np.argmin(np.abs(ts - deduped[-1])))]:
            deduped[-1] = c
    return deduped, ms


def classify_camera_move(motions: list[PairMotion],
                         duration: float) -> tuple[str, float, dict[str, Any]]:
    """Name the camera move for one shot from its aggregated motion.

    Two things make this work where a naive version fails.

    Units are normalised to be resolution- and framerate-independent:
    translation as fractions of frame width per second, scale as proportional
    change per second, rotation as degrees per second. Raw px/frame at the
    tracking width is meaningless across different footage.

    Selection is by score, not by priority. A fixed if-chain that tests scale
    before translation labels every pan with mild parallax a push-in, because
    panning across depth does change apparent scale. Each hypothesis is instead
    scored as evidence over its own threshold and the strongest wins, with
    confidence taken from its margin over the runner-up.
    """
    usable = [m for m in motions if m.inlier_frac > 0.25]
    if len(usable) < 2:
        return "unknown", 0.0, {"n_pairs": len(motions), "n_usable": len(usable)}

    # Sampling interval from the pair timestamps, so stride/fps do not matter.
    ts = np.array([m.t for m in usable])
    dt = float(np.median(np.diff(ts))) if len(ts) > 1 else (duration / len(usable))
    dt = dt if dt > 1e-6 else 1.0 / 24.0

    dx = np.array([m.dx for m in usable]); dy = np.array([m.dy for m in usable])
    sc = np.array([m.dscale for m in usable]); rot = np.array([m.drot for m in usable])
    span = max(1e-6, float(ts[-1] - ts[0]) + dt)

    # --- normalise -------------------------------------------------------
    # fraction of frame width per second
    vx = float(np.mean(dx)) / TRACK_W / dt
    vy = float(np.mean(dy)) / TRACK_W / dt
    peak_vx = float(np.percentile(np.abs(dx), 90)) / TRACK_W / dt
    # proportional scale change per second
    log_scale = float(np.sum(np.log(np.clip(sc, 1e-6, None))))
    scale_rate = log_scale / span
    # degrees per second
    rot_rate = math.degrees(float(np.sum(rot))) / span
    # jitter: per-second reversal energy not explained by net drift
    jitter = float((np.std(dx) + np.std(dy)) / 2.0) / TRACK_W / dt
    drift = math.hypot(vx, vy)

    metrics = {
        "n_pairs": len(motions), "n_usable": len(usable), "dt": round(dt, 5),
        "cum_scale": round(float(np.prod(sc)), 4),
        "scale_rate_per_s": round(scale_rate, 4),
        "rot_rate_deg_per_s": round(rot_rate, 3),
        "vx_frac_per_s": round(vx, 4), "vy_frac_per_s": round(vy, 4),
        "peak_vx_frac_per_s": round(peak_vx, 4),
        "jitter_frac_per_s": round(jitter, 4), "drift_frac_per_s": round(drift, 4),
        "mean_tracked_frac": round(float(np.mean([m.tracked_frac for m in usable])), 3),
    }

    # --- thresholds, in the normalised units above ------------------------
    T_SCALE, T_PAN, T_WHIP, T_ROT, T_JIT = 0.035, 0.020, 0.180, 4.0, 0.030

    scores: dict[str, float] = {}
    # A whip IS a pan, just violent. Letting both compete lets the plain pan
    # win on a large |vx| and hide the whip, so the specialisation supersedes
    # its own family rather than racing it.
    is_whip = abs(peak_vx) >= T_WHIP
    if is_whip:
        scores["whip_pan"] = abs(peak_vx) / T_WHIP
    if scale_rate > 0:
        scores["push_in"] = scale_rate / T_SCALE
    else:
        scores["pull_out"] = -scale_rate / T_SCALE
    scores["orbit"] = abs(rot_rate) / T_ROT
    if not is_whip:
        if abs(vx) >= abs(vy):
            scores["pan_left" if vx > 0 else "pan_right"] = abs(vx) / T_PAN
        else:
            scores["tilt_up" if vy > 0 else "tilt_down"] = abs(vy) / T_PAN
    # Handheld only counts when reversal energy dominates net travel.
    if drift < 1e-9 or jitter > drift * 1.3:
        scores["handheld"] = jitter / T_JIT

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    metrics["scores"] = {k: round(v, 3) for k, v in ranked}

    if best_score < 1.0:
        return "static", float(min(0.9, 0.55 + (1.0 - best_score) * 0.4)), metrics
    margin = best_score / max(runner, 1e-6)
    conf = float(np.clip(0.40 + 0.22 * math.log(max(best_score, 1.0)) + 0.18 * math.log(max(margin, 1.0)),
                         0.35, 0.95))
    return best, round(conf, 3), metrics


def detect_speed_ramp(motions: list[PairMotion]) -> Optional[str]:
    """Flag a within-shot velocity change (speed ramp / slow motion)."""
    usable = [m for m in motions if m.inlier_frac > 0.25]
    if len(usable) < 8:
        return None
    mag = np.array([math.hypot(m.dx, m.dy) for m in usable])
    half = len(mag) // 2
    a, b = float(np.mean(mag[:half])), float(np.mean(mag[half:]))
    if a < 1e-6 and b < 1e-6:
        return None
    ratio = (b + 1e-6) / (a + 1e-6)
    if ratio > 2.2:
        return "ramp_up"
    if ratio < 0.45:
        return "ramp_down"
    if float(np.mean(mag)) < 0.5 and float(np.percentile(mag, 95)) < 1.0:
        return "slow_motion_or_locked"
    return None
