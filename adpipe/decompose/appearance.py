"""Per-shot appearance: colour palette, lighting, and a shot-scale heuristic.

Palette and lighting are measured. Shot scale is *inferred* and reported with
low confidence: naming it properly needs subject detection, so the EDL keeps
the field overridable by a later vision pass rather than pretending the
heuristic is ground truth.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def _hex(bgr) -> str:
    b, g, r = (int(round(float(x))) for x in bgr[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def palette(frame_paths: list[str], k: int = 5) -> list[str]:
    """Dominant colours across a shot's sampled frames, most frequent first."""
    px: list[np.ndarray] = []
    for p in frame_paths:
        im = cv2.imread(p)
        if im is None:
            continue
        small = cv2.resize(im, (64, 36), interpolation=cv2.INTER_AREA)
        px.append(small.reshape(-1, 3))
    if not px:
        return []
    data = np.vstack(px).astype(np.float32)
    k = int(max(1, min(k, len(np.unique(data, axis=0)))))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(data, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.reshape(-1), minlength=k)
    return [_hex(centers[i]) for i in np.argsort(-counts)]


def lighting(frame_paths: list[str]) -> dict[str, Any]:
    """Luma statistics -> a coarse lighting description."""
    lumas, contrasts, sats = [], [], []
    for p in frame_paths:
        im = cv2.imread(p)
        if im is None:
            continue
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        lumas.append(float(g.mean()))
        contrasts.append(float(g.std()))
        sats.append(float(hsv[:, :, 1].mean()))
    if not lumas:
        return {}
    L, C, S = float(np.mean(lumas)), float(np.mean(contrasts)), float(np.mean(sats))
    key = "low_key" if L < 70 else ("high_key" if L > 175 else "mid_key")
    contrast = "high_contrast" if C > 62 else ("flat" if C < 28 else "normal_contrast")
    sat_desc = "desaturated" if S < 40 else ("saturated" if S > 120 else "natural_saturation")
    return {"mean_luma": round(L, 2), "contrast": round(C, 2),
            "mean_saturation": round(S, 2), "key": key,
            "contrast_class": contrast, "saturation_class": sat_desc,
            "description": f"{key}, {contrast}, {sat_desc}"}


def shot_scale_heuristic(frame_paths: list[str]) -> tuple[str, float]:
    """Guess shot scale from the size of the dominant foreground region.

    Deliberately low confidence. Edge density and blob size correlate with
    framing only loosely; a close-up of a busy label looks like a wide shot to
    this measure. A vision pass should overwrite it.
    """
    ratios: list[float] = []
    for p in frame_paths:
        im = cv2.imread(p)
        if im is None:
            continue
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (5, 5), 0)
        edges = cv2.Canny(g, 60, 160)
        edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        biggest = max(cv2.contourArea(c) for c in cnts)
        ratios.append(biggest / float(g.shape[0] * g.shape[1]))
    if not ratios:
        return "unknown", 0.0
    r = float(np.mean(ratios))
    if r > 0.55:
        return "extreme_close_up", 0.35
    if r > 0.34:
        return "close_up", 0.35
    if r > 0.16:
        return "medium", 0.30
    return "wide", 0.30
