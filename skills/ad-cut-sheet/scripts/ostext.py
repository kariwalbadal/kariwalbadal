"""On-screen text and graphics inventory via OCR.

Ads carry burned-in copy -- claims, price, CTA -- that has to be reproduced or
deliberately replaced, so its timecode, content and position are inventoried.
OCR on video frames is noisy, so a detection must persist across frames before
it is reported.
"""
from __future__ import annotations

from typing import Any, Optional
import re


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def ocr_frame(path: str, min_conf: int = 55) -> list[dict[str, Any]]:
    """Text boxes in one frame with normalised positions."""
    import cv2
    import pytesseract
    from pytesseract import Output

    im = cv2.imread(path)
    if im is None:
        return []
    h, w = im.shape[:2]
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    # Ad titling is usually high-contrast over busy footage; CLAHE lifts it.
    g = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(g)
    try:
        data = pytesseract.image_to_data(g, output_type=Output.DICT)
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for i, txt in enumerate(data.get("text", [])):
        t = _clean(txt)
        if len(t) < 2:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, KeyError, IndexError):
            continue
        if conf < min_conf:
            continue
        x, y = data["left"][i], data["top"][i]
        bw, bh = data["width"][i], data["height"][i]
        cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
        pos = ("top" if cy < 0.33 else "bottom" if cy > 0.66 else "middle")
        pos += "-" + ("left" if cx < 0.33 else "right" if cx > 0.66 else "centre")
        out.append({"text": t, "confidence": round(conf, 1), "position": pos,
                    "bbox_norm": [round(x / w, 4), round(y / h, 4),
                                  round(bw / w, 4), round(bh / h, 4)],
                    "height_frac": round(bh / h, 4)})
    return out


def inventory_onscreen_text(shots: list[dict[str, Any]],
                            min_persist: int = 2,
                            max_frames_per_shot: int = 6) -> list[dict[str, Any]]:
    """Text inventory across shots; a string must appear in >= min_persist frames.

    Requiring persistence is what separates real titling from OCR noise on
    textured footage.
    """
    inv: list[dict[str, Any]] = []
    for s in shots:
        frames = (s.get("frames") or [])[:max_frames_per_shot]
        seen: dict[str, dict[str, Any]] = {}
        for fr in frames:
            path = fr["path"] if isinstance(fr, dict) else fr.path
            t = fr["t"] if isinstance(fr, dict) else fr.t
            for box in ocr_frame(path):
                key = box["text"].lower()
                rec = seen.setdefault(key, {"text": box["text"], "count": 0,
                                            "first_t": t, "last_t": t,
                                            "positions": [], "heights": [],
                                            "confidences": []})
                rec["count"] += 1
                rec["last_t"] = max(rec["last_t"], t)
                rec["first_t"] = min(rec["first_t"], t)
                rec["positions"].append(box["position"])
                rec["heights"].append(box["height_frac"])
                rec["confidences"].append(box["confidence"])
        for rec in seen.values():
            if rec["count"] < min_persist:
                continue
            pos = max(set(rec["positions"]), key=rec["positions"].count)
            inv.append({
                "shot_index": s["index"],
                "text": rec["text"],
                "start": round(rec["first_t"], 3),
                "end": round(rec["last_t"], 3),
                "position": pos,
                "mean_height_frac": round(sum(rec["heights"]) / len(rec["heights"]), 4),
                "mean_confidence": round(sum(rec["confidences"]) / len(rec["confidences"]), 1),
                "n_frames_seen": rec["count"],
                "animation_style": "unknown_static_ocr",
            })
    return inv
