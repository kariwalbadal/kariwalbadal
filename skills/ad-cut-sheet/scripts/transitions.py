"""Transition-type classification at shot boundaries.

The transition *into* the next shot is part of ad grammar -- a whip into a
match cut reads completely differently from two hard cuts -- so each boundary is
classified rather than assumed to be a hard cut.

Method: appearance change at a hard cut is confined to one frame pair, while a
dissolve spreads it over many. Comparing the change at the boundary to the
change in the frames around it separates the two, and flow magnitude on either
side separates a whip from a static cut.
"""
from __future__ import annotations

from typing import Any, Optional
import math

import cv2
import numpy as np


def _frames_around(video_path: str, t: float, n: int = 4,
                   fps: float = 24.0, width: int = 256) -> list[np.ndarray]:
    """2n frames straddling t, small and grey."""
    cap = cv2.VideoCapture(video_path)
    start = max(0, int(round(t * fps)) - n)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    out: list[np.ndarray] = []
    for _ in range(2 * n):
        ok, fr = cap.read()
        if not ok:
            break
        h, w = fr.shape[:2]
        small = cv2.resize(fr, (width, max(1, int(round(h * width / w)))),
                           interpolation=cv2.INTER_AREA)
        out.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32))
    cap.release()
    return out


def classify_transition(video_path: str, t: float, fps: float,
                        flow_before: Optional[float] = None,
                        flow_after: Optional[float] = None,
                        n: int = 4) -> tuple[str, float, dict[str, Any]]:
    """Classify the boundary at time `t`. Returns (label, confidence, metrics)."""
    fr = _frames_around(video_path, t, n=n, fps=fps)
    if len(fr) < 4:
        return "hard_cut", 0.2, {"reason": "insufficient_frames"}

    deltas = [float(np.abs(fr[i + 1] - fr[i]).mean()) for i in range(len(fr) - 1)]
    mid = len(deltas) // 2
    boundary = max(deltas[max(0, mid - 1):mid + 2]) if deltas else 0.0
    neighbours = [d for i, d in enumerate(deltas) if abs(i - mid) > 1]
    nb_mean = float(np.mean(neighbours)) if neighbours else 0.0
    nb_max = float(np.max(neighbours)) if neighbours else 0.0

    # Concentration: how much of the total change sits at the boundary.
    total = sum(deltas) or 1e-6
    concentration = boundary / total
    ratio = boundary / (nb_mean + 1e-6)

    # Luma trajectory separates a fade (to/from black) from a cross-dissolve.
    means = [float(f.mean()) for f in fr]
    fade_like = min(means) < 12.0 and (means[0] > 30 or means[-1] > 30)

    metrics = {"boundary_delta": round(boundary, 3), "neighbour_mean": round(nb_mean, 3),
               "neighbour_max": round(nb_max, 3), "concentration": round(concentration, 3),
               "ratio": round(ratio, 3), "min_luma": round(min(means), 1),
               "flow_before": None if flow_before is None else round(flow_before, 2),
               "flow_after": None if flow_after is None else round(flow_after, 2)}

    if fade_like:
        return "fade", 0.6, metrics
    fast = 9.0    # px/frame at the 384px tracking width -- a whip, not a drift
    if (flow_before or 0) > fast and (flow_after or 0) > fast:
        return "whip", 0.6, metrics
    # A gradual boundary spread over many frames is a dissolve/morph.
    if ratio < 2.0 and nb_mean > 2.0 and concentration < 0.35:
        return "dissolve", 0.5, metrics
    if ratio >= 3.0 or concentration >= 0.45:
        return "hard_cut", 0.8, metrics
    return "hard_cut", 0.45, metrics
