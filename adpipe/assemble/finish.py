"""Finishing passes: colour normalisation, glow/bloom, and grain.

Independently generated shots do not match. Even with the same product
reference, exposure and white balance drift between generations, and a cut
between two mismatched shots reads as an error rather than an edit. The grade
pass is therefore not a stylistic flourish -- it is what makes Architecture A's
shot-by-shot output look like one film.

Glow/bloom is the brief's requested product finishing treatment, applied as a
highlight-isolated screen composite rather than a global blur, so the product
highlights bloom while midtones stay sharp.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import json
import subprocess


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:8])}...\n{p.stderr[-2000:]}")


def measure_clip(path: str) -> dict[str, float]:
    """Mean luma/saturation of a clip, for cross-shot normalisation."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(path)
    lumas, sats = [], []
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, n // 12) if n else 1
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
            lumas.append(float(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).mean()))
            sats.append(float(hsv[:, :, 1].mean()))
        i += 1
    cap.release()
    if not lumas:
        return {"mean_luma": 0.0, "mean_sat": 0.0}
    return {"mean_luma": sum(lumas) / len(lumas), "mean_sat": sum(sats) / len(sats)}


def plan_normalisation(clip_paths: list[str]) -> dict[str, dict[str, float]]:
    """Per-clip gain/saturation corrections toward the set's median.

    Normalising to the median rather than to the first shot stops one oddly
    exposed generation from dragging the whole ad with it.
    """
    stats = {p: measure_clip(p) for p in clip_paths}
    lum = sorted(s["mean_luma"] for s in stats.values() if s["mean_luma"] > 0)
    sat = sorted(s["mean_sat"] for s in stats.values() if s["mean_sat"] > 0)
    if not lum:
        return {p: {"gain": 1.0, "sat": 1.0} for p in clip_paths}
    med_l = lum[len(lum) // 2]
    med_s = sat[len(sat) // 2] if sat else 1.0
    plan: dict[str, dict[str, float]] = {}
    for p, s in stats.items():
        gain = med_l / s["mean_luma"] if s["mean_luma"] > 1e-6 else 1.0
        sgain = med_s / s["mean_sat"] if s["mean_sat"] > 1e-6 else 1.0
        # Clamped: a large correction means the generation was wrong, and
        # pushing it into range would only produce a differently wrong shot.
        plan[p] = {"gain": round(min(1.35, max(0.74, gain)), 4),
                   "sat": round(min(1.30, max(0.78, sgain)), 4),
                   "measured_luma": round(s["mean_luma"], 2),
                   "measured_sat": round(s["mean_sat"], 2)}
    return plan


def apply_finish(src: str, dst: str, gain: float = 1.0, sat: float = 1.0,
                 glow: float = 0.0, glow_threshold: float = 0.72,
                 grain: float = 0.0, contrast: float = 1.0,
                 crf: int = 18, preset: str = "medium") -> str:
    """Apply grade + optional glow/bloom + grain in one pass.

    `glow` is a 0..1 strength. Highlights are isolated, blurred, and screened
    back over the picture, which blooms speculars on the product without
    softening the whole frame the way a global blur would.
    """
    chain = [f"eq=brightness=0:contrast={contrast:.4f}:saturation={sat:.4f}:gamma=1.0",
             f"colorlevels=rimin=0:gimin=0:bimin=0"]
    # Gain via a multiplier on all channels.
    if abs(gain - 1.0) > 1e-3:
        chain.append(f"colorchannelmixer=rr={gain:.4f}:gg={gain:.4f}:bb={gain:.4f}")

    if glow > 1e-3:
        thr = max(0.0, min(0.99, glow_threshold))
        fc = (
            f"[0:v]{','.join(chain)},split=2[base][hi];"
            # isolate highlights, blur them, screen back over the base
            f"[hi]lutyuv=y='if(gt(val,{int(thr*255)}),val,0)',"
            f"gblur=sigma=18:steps=2[bloom];"
            f"[base][bloom]blend=all_mode=screen:all_opacity={min(1.0, glow):.3f}[g]"
        )
        last = "[g]"
        if grain > 1e-3:
            fc += f";{last}noise=alls={int(max(1, grain*22))}:allf=t+u[out]"
            last = "[out]"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
              "-filter_complex", fc, "-map", last, "-an",
              "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
              "-pix_fmt", "yuv420p", dst])
        return dst

    if grain > 1e-3:
        chain.append(f"noise=alls={int(max(1, grain*22))}:allf=t+u")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
          "-vf", ",".join(chain), "-an",
          "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
          "-pix_fmt", "yuv420p", dst])
    return dst
