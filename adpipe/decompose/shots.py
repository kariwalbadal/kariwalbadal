"""Shot boundary detection.

Ad edits run fast and default thresholds miss hard cuts between frames of
similar luma, so the threshold is not left at its default: `sweep_thresholds`
scores candidate settings against a ground-truth cut list and
`detect_shots_auto` picks the best by F1.

Detector choice: PySceneDetect ContentDetector works on HSV content deltas.
AdaptiveDetector normalises that delta against a rolling window, which is what
actually rescues same-luma cuts, so both are swept.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional
import math


@dataclass
class CutAccuracy:
    """Precision/recall of a detected cut list against ground truth."""
    tolerance: float
    n_truth: int
    n_detected: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    mean_abs_error: Optional[float]
    matched: list[tuple[float, float]]
    missed: list[float]
    spurious: list[float]

    def summary(self) -> str:
        mae = "n/a" if self.mean_abs_error is None else f"{self.mean_abs_error*1000:.0f}ms"
        return (f"P={self.precision:.3f} R={self.recall:.3f} F1={self.f1:.3f} "
                f"TP={self.true_positives} FP={self.false_positives} FN={self.false_negatives} "
                f"MAE={mae} (tol={self.tolerance*1000:.0f}ms)")


def score_cuts(detected: Iterable[float], truth: Iterable[float],
               tolerance: float = 0.20) -> CutAccuracy:
    """Greedy nearest-match scoring within `tolerance` seconds.

    Each truth cut consumes at most one detection and vice versa, so duplicate
    detections around one real cut are counted as false positives rather than
    silently forgiven.
    """
    det = sorted(float(d) for d in detected)
    tru = sorted(float(t) for t in truth)
    pairs: list[tuple[float, float, float]] = []
    for t in tru:
        for d in det:
            if abs(d - t) <= tolerance:
                pairs.append((abs(d - t), t, d))
    pairs.sort()
    used_t: set[float] = set()
    used_d: set[float] = set()
    matched: list[tuple[float, float]] = []
    for err, t, d in pairs:
        if t in used_t or d in used_d:
            continue
        used_t.add(t); used_d.add(d)
        matched.append((t, d))
    tp = len(matched)
    fp = len(det) - tp
    fn = len(tru) - tp
    precision = tp / len(det) if det else 0.0
    recall = tp / len(tru) if tru else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    mae = (sum(abs(d - t) for t, d in matched) / tp) if tp else None
    return CutAccuracy(
        tolerance=tolerance, n_truth=len(tru), n_detected=len(det),
        true_positives=tp, false_positives=fp, false_negatives=fn,
        precision=precision, recall=recall, f1=f1, mean_abs_error=mae,
        matched=sorted(matched),
        missed=sorted(t for t in tru if t not in used_t),
        spurious=sorted(d for d in det if d not in used_d),
    )


def _scene_list_to_cuts(scene_list) -> list[float]:
    """Interior boundaries only -- t=0 and t=end are not cuts."""
    if not scene_list:
        return []
    return [float(s[1].get_seconds()) for s in scene_list[:-1]]


def detect_shots(video_path: str, detector: str = "content", threshold: float = 27.0,
                 min_scene_len_s: float = 0.20, downscale: Optional[int] = None,
                 luma_only: bool = False, edge_weight: float = 0.0,
                 bins: int = 256) -> tuple[list[tuple[float, float]], list[float]]:
    """Return (shot spans, interior cut times) in seconds.

    `edge_weight` > 0 turns on ContentDetector's edge component, which is the
    only channel that separates two desaturated shots of equal luma. See
    docs/EDL-VALIDATION.md for why that case needs it.
    """
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import (ContentDetector, AdaptiveDetector,
                                       ThresholdDetector, HistogramDetector,
                                       HashDetector)

    video = open_video(video_path)
    fps = video.frame_rate
    min_len_frames = max(1, int(round(min_scene_len_s * fps)))

    sm = SceneManager()
    if detector == "content":
        kw: dict[str, Any] = {}
        if edge_weight > 0:
            kw["weights"] = ContentDetector.Components(
                delta_hue=1.0, delta_sat=1.0, delta_lum=1.0, delta_edges=edge_weight)
        sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_len_frames,
                                        luma_only=luma_only, **kw))
    elif detector == "adaptive":
        kw = {}
        if edge_weight > 0:
            kw["weights"] = ContentDetector.Components(
                delta_hue=1.0, delta_sat=1.0, delta_lum=1.0, delta_edges=edge_weight)
        sm.add_detector(AdaptiveDetector(adaptive_threshold=threshold,
                                         min_scene_len=min_len_frames,
                                         luma_only=luma_only, **kw))
    elif detector == "threshold":
        sm.add_detector(ThresholdDetector(threshold=threshold, min_scene_len=min_len_frames))
    elif detector == "histogram":
        sm.add_detector(HistogramDetector(threshold=threshold, bins=bins,
                                          min_scene_len=min_len_frames))
    elif detector == "hash":
        sm.add_detector(HashDetector(threshold=threshold, min_scene_len=min_len_frames))
    else:
        raise ValueError(f"unknown detector {detector!r}")

    if downscale:
        sm.auto_downscale = False
        sm.downscale = downscale

    sm.detect_scenes(video, show_progress=False)
    scenes = sm.get_scene_list()
    if not scenes:
        dur = float(video.duration.get_seconds()) if video.duration else 0.0
        return [(0.0, dur)], []
    spans = [(float(a.get_seconds()), float(b.get_seconds())) for a, b in scenes]
    return spans, _scene_list_to_cuts(scenes)


# Swept grid. Content thresholds go well below the 27.0 default because ad cuts
# between similar frames produce small HSV deltas.
DEFAULT_GRID: list[dict[str, Any]] = (
    [{"detector": "content", "threshold": t, "luma_only": False}
     for t in (8.0, 12.0, 15.0, 18.0, 21.0, 24.0, 27.0, 32.0, 40.0)]
    # luma_only drops the two dead HSV channels, so a desaturated-pair cut is
    # scored on its full luma delta instead of one third of it.
    + [{"detector": "content", "threshold": t, "luma_only": True}
       for t in (4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 27.0)]
    # edges are the one channel that still differs when luma and chroma match.
    + [{"detector": "content", "threshold": t, "luma_only": False, "edge_weight": w}
       for t in (8.0, 12.0, 15.0, 20.0) for w in (0.5, 1.0, 2.0)]
    + [{"detector": "adaptive", "threshold": t, "luma_only": False}
       for t in (1.5, 2.0, 2.5, 3.0, 4.0, 6.0)]
    + [{"detector": "adaptive", "threshold": t, "luma_only": True}
       for t in (1.5, 2.0, 3.0, 4.0)]
    + [{"detector": "histogram", "threshold": t}
       for t in (0.02, 0.05, 0.08, 0.12, 0.20)]
    + [{"detector": "hash", "threshold": t}
       for t in (0.10, 0.15, 0.20, 0.30, 0.40)]
)


def sweep_thresholds(video_path: str, truth_cuts: list[float],
                     grid: Optional[list[dict[str, Any]]] = None,
                     tolerance: float = 0.20,
                     min_scene_len_s: float = 0.20) -> list[dict[str, Any]]:
    """Score every setting in `grid`; returns rows sorted best-F1 first.

    Ties on F1 break toward lower timing error, then fewer false positives --
    a detector that finds every cut but jitters is worse than one that lands
    them tightly.
    """
    rows: list[dict[str, Any]] = []
    for cfg in (grid or DEFAULT_GRID):
        try:
            _, cuts = detect_shots(video_path, min_scene_len_s=min_scene_len_s, **cfg)
            acc = score_cuts(cuts, truth_cuts, tolerance=tolerance)
            rows.append({**cfg, "n_detected": len(cuts), "accuracy": acc,
                         "f1": acc.f1, "precision": acc.precision, "recall": acc.recall,
                         "mae": acc.mean_abs_error, "error": None})
        except Exception as exc:  # a bad grid point must not kill the sweep
            rows.append({**cfg, "n_detected": 0, "accuracy": None, "f1": -1.0,
                         "precision": 0.0, "recall": 0.0, "mae": None, "error": str(exc)})
    rows.sort(key=lambda r: (-r["f1"], r["mae"] if r["mae"] is not None else math.inf,
                             r["accuracy"].false_positives if r["accuracy"] else math.inf))
    return rows


CFG_KEYS = ("detector", "threshold", "luma_only", "edge_weight", "bins")

# Validated on the synthetic ground-truth reference at F1=1.000 across its whole
# swept threshold band -- see docs/EDL-VALIDATION.md. Used when no ground truth
# is supplied for the footage at hand.
VALIDATED_DEFAULT: dict[str, Any] = {"detector": "histogram", "threshold": 0.08}
VALIDATED_CROSSCHECK: dict[str, Any] = {"detector": "content", "threshold": 8.0,
                                        "luma_only": True}


def select_robust_config(rows: list[dict[str, Any]],
                         min_f1: float = 0.999) -> dict[str, Any]:
    """Pick the centre of the widest contiguous run of passing thresholds.

    Argmax-F1 is the wrong rule here: several settings tie at F1=1.000 and the
    tie-break then turns on noise. A setting sitting mid-band stays correct when
    unseen footage shifts the deltas, so band width is the real signal of
    robustness. Families are keyed on everything but `threshold`.
    """
    families: dict[tuple, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("error"):
            continue
        key = tuple((k, r.get(k)) for k in CFG_KEYS if k != "threshold")
        families.setdefault(key, []).append(r)

    best_run: tuple[int, float, list[dict[str, Any]]] | None = None
    for key, members in families.items():
        members.sort(key=lambda r: r["threshold"])
        run: list[dict[str, Any]] = []
        runs: list[list[dict[str, Any]]] = []
        for r in members:
            if r["f1"] >= min_f1:
                run.append(r)
            else:
                if run:
                    runs.append(run)
                run = []
        if run:
            runs.append(run)
        for rn in runs:
            mean_f1 = sum(x["f1"] for x in rn) / len(rn)
            cand = (len(rn), mean_f1, rn)
            if best_run is None or (cand[0], cand[1]) > (best_run[0], best_run[1]):
                best_run = cand

    if best_run is None:                      # nothing passed -- fall back to argmax
        top = rows[0]
        return {k: top[k] for k in CFG_KEYS if k in top}
    run = best_run[2]
    mid = run[len(run) // 2]
    return {k: mid[k] for k in CFG_KEYS if k in mid}


def detect_shots_auto(video_path: str, truth_cuts: Optional[list[float]] = None,
                      min_scene_len_s: float = 0.20, tolerance: float = 0.20):
    """Detect shots, tuning against `truth_cuts` when ground truth is available.

    Returns (spans, cuts, report). Without ground truth, uses VALIDATED_DEFAULT.
    """
    if truth_cuts:
        rows = sweep_thresholds(video_path, truth_cuts, tolerance=tolerance,
                                min_scene_len_s=min_scene_len_s)
        cfg = select_robust_config(rows)
        spans, cuts = detect_shots(video_path, min_scene_len_s=min_scene_len_s, **cfg)
        chosen = next((r for r in rows
                       if all(r.get(k) == v for k, v in cfg.items())), rows[0])
        return spans, cuts, {"tuned": True, "config": cfg,
                             "accuracy": chosen["accuracy"], "sweep": rows}
    cfg = dict(VALIDATED_DEFAULT)
    spans, cuts = detect_shots(video_path, min_scene_len_s=min_scene_len_s, **cfg)
    return spans, cuts, {"tuned": False, "config": cfg, "accuracy": None, "sweep": []}


def detect_shots_consensus(video_path: str, min_scene_len_s: float = 0.20,
                           agree_window: float = 0.10):
    """Run the validated detector and its cross-check; report where they differ.

    Disagreement is the honest signal that footage needs manual review, which is
    the only defence available when there is no ground truth for it.
    """
    _, cuts_a = detect_shots(video_path, min_scene_len_s=min_scene_len_s,
                             **VALIDATED_DEFAULT)
    _, cuts_b = detect_shots(video_path, min_scene_len_s=min_scene_len_s,
                             **VALIDATED_CROSSCHECK)
    agreed, only_a = [], []
    for a in cuts_a:
        if any(abs(a - b) <= agree_window for b in cuts_b):
            agreed.append(a)
        else:
            only_a.append(a)
    only_b = [b for b in cuts_b
              if not any(abs(a - b) <= agree_window for a in cuts_a)]
    return {"agreed": agreed, "only_primary": only_a, "only_crosscheck": only_b,
            "primary": VALIDATED_DEFAULT, "crosscheck": VALIDATED_CROSSCHECK,
            "needs_review": bool(only_a or only_b)}
