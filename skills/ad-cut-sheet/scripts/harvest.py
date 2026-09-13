"""Intra-clip harvesting: find which SECONDS of a generation are usable.

The real yield problem with generative video is not that a clip is good or bad
-- it is that a 10s clip contains a few genuinely good seconds surrounded by
junk. Deciding which seconds those are, and assembling an ad from the
survivors, is the manual work that does not scale.

This scores quality per frame, builds a quality timeline, and returns the
maximal contiguous spans that clear a threshold. Three signals, chosen because
they fail on different artefacts:

  sharpness        Laplacian variance. Catches soft/mushy stretches.
  affine_residual  How far tracked points sit from ONE global affine model.
                   A rigid camera move fits that model; a non-rigid morph
                   cannot, so this is the signal for warping and melting.
  inlier_frac      Fraction of points any single model explains. Collapses
                   when correspondence breaks down.

Scores are normalised per clip, not against absolute constants: what counts as
sharp depends on the footage, and the question is always "which parts of THIS
clip are the good parts".
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Optional
import math

import cv2
import numpy as np

TRACK_W = 480
MAX_CORNERS = 500
ORB_FEATURES = 1200


@dataclass
class FrameScore:
    t: float
    sharpness: float = 0.0
    affine_residual: float = float("nan")
    inlier_frac: float = 0.0
    tracked_frac: float = 0.0
    product_inliers: float = 0.0   # matches to the product's own reference photo
    quality: float = 0.0           # 0..1, populated after normalisation


@dataclass
class Span:
    start: float
    end: float
    mean_quality: float
    min_quality: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration"] = self.duration
        return d


def _prep(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    s = TRACK_W / float(w)
    small = cv2.resize(frame, (TRACK_W, max(1, int(round(h * s)))),
                       interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def _product_matcher(reference_paths: list[str]):
    """Prepare ORB descriptors for each product reference image.

    Measured against injected artefacts, this is the ONLY signal that catches
    every class. Geometric signals miss non-rigid morphs and can invert on them:
    a morph adds high-frequency detail (so it reads as sharp) and a structured
    ripple is temporally MORE stable than real footage (so motion-coherence
    improves). Matching the product against its own photograph asks the right
    question -- "is this still the client's product" -- instead of a proxy.
    """
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    refs = []
    for path in reference_paths:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        kp, des = orb.detectAndCompute(img, None)
        if des is not None and len(kp) >= 8:
            refs.append((kp, des))
    return orb, refs, cv2.BFMatcher(cv2.NORM_HAMMING)


def _product_score(frame_gray, orb, refs, bf) -> float:
    """Best geometrically-consistent match count across the reference set.

    Best-of rather than mean: with 10-20 references only the ones matching this
    shot's angle should contribute, and a reference shot from behind should not
    drag the score down on a front-on shot.
    """
    if not refs:
        return 0.0
    kf, df = orb.detectAndCompute(frame_gray, None)
    if df is None or len(kf) < 8:
        return 0.0
    best = 0.0
    for kr, dr in refs:
        pairs = bf.knnMatch(dr, df, k=2)
        good = [a for a, b in (p for p in pairs if len(p) == 2)
                if a.distance < 0.75 * b.distance]
        if len(good) < 8:
            continue
        src = np.float32([kr[a.queryIdx].pt for a in good]).reshape(-1, 1, 2)
        dst = np.float32([kf[a.trainIdx].pt for a in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
        if mask is not None:
            best = max(best, float(mask.sum()))
    return best


def score_frames(video_path: str,
                 product_references: Optional[list[str]] = None) -> list[FrameScore]:
    """Raw per-frame signals, before normalisation."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    out: list[FrameScore] = []
    ok, prev = cap.read()
    if not ok:
        cap.release(); return out
    gprev = _prep(prev)
    idx = 0
    lk = dict(winSize=(21, 21), maxLevel=3,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    orb, refs, bf = _product_matcher(product_references or [])

    while True:
        ok, cur = cap.read()
        if not ok:
            break
        idx += 1
        g = _prep(cur)
        fs = FrameScore(t=idx / fps)

        # Sharpness on the full-res luma: mush shows here before anywhere else.
        lum = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
        fs.sharpness = float(cv2.Laplacian(lum, cv2.CV_64F).var())
        fs.product_inliers = _product_score(lum, orb, refs, bf)

        p0 = cv2.goodFeaturesToTrack(gprev, maxCorners=MAX_CORNERS,
                                     qualityLevel=0.01, minDistance=6, blockSize=7)
        if p0 is not None and len(p0) >= 20:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(gprev, g, p0, None, **lk)
            if p1 is not None:
                good = st.reshape(-1) == 1
                fs.tracked_frac = float(good.sum()) / len(p0)
                a = p0.reshape(-1, 2)[good]
                b = p1.reshape(-1, 2)[good]
                if len(a) >= 12:
                    M, inl = cv2.estimateAffinePartial2D(
                        a, b, method=cv2.RANSAC, ransacReprojThreshold=2.0,
                        maxIters=2000)
                    if M is not None and inl is not None:
                        fs.inlier_frac = float(inl.sum()) / len(a)
                        # Residual of EVERY point against the single global model.
                        # Rigid motion -> small. Non-rigid warp -> large.
                        pred = (M[:, :2] @ a.T).T + M[:, 2]
                        err = np.linalg.norm(pred - b, axis=1)
                        fs.affine_residual = float(np.median(err))
        out.append(fs)
        gprev = g
        idx_prev = idx
    cap.release()
    return out


def _robust_norm(x: np.ndarray, invert: bool = False) -> np.ndarray:
    """Map to 0..1 using percentiles, so one bad frame cannot set the scale."""
    finite = x[np.isfinite(x)]
    if finite.size == 0:
        return np.zeros_like(x)
    lo, hi = np.percentile(finite, 5), np.percentile(finite, 95)
    if hi - lo < 1e-9:
        return np.ones_like(x) * 0.5
    y = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    y = np.where(np.isfinite(x), y, 0.0)
    return 1.0 - y if invert else y


def build_quality_timeline(scores: list[FrameScore],
                           smooth_frames: int = 5,
                           w_product: float = 0.80,
                           w_sharp: float = 0.20) -> list[FrameScore]:
    """Normalise, weight and smooth into a single 0..1 quality curve.

    Product-reference matching dominates, because it is the only signal that
    survived testing on all three artefact classes. Sharpness is kept as a
    ONE-SIDED penalty: low sharpness means mush, but high sharpness does not
    mean good -- a morph measured +256% sharpness while destroying the frame.
    Rewarding it is how the first version of this function ranked the worst
    second of a clip as its best.
    """
    if not scores:
        return scores
    prod = np.array([s.product_inliers for s in scores], dtype=float)
    sharp_raw = np.array([s.sharpness for s in scores], dtype=float)

    if prod.max() <= 0:
        # No usable product references: fall back, and say so in the report.
        resid = _robust_norm(np.array([s.affine_residual for s in scores],
                                      dtype=float), invert=True)
        q = 0.5 * _robust_norm(sharp_raw) + 0.5 * resid
    else:
        prod_n = _robust_norm(prod)
        med = float(np.median(sharp_raw[np.isfinite(sharp_raw)]))
        # one-sided: only a drop below the clip's own median counts against it
        penalty = np.clip(sharp_raw / max(med, 1e-9), 0.0, 1.0)
        q = w_product * prod_n + w_sharp * penalty

    q_raw = q.copy()
    if smooth_frames > 1:
        k = np.ones(smooth_frames) / smooth_frames
        q = np.convolve(q_raw, k, mode="same")
        # convolve tapers the ends; rebuild them from the shortest valid window
        half = smooth_frames // 2
        for i in list(range(half)) + list(range(len(q) - half, len(q))):
            lo, hi = max(0, i - half), min(len(q), i + half + 1)
            q[i] = float(q_raw[lo:hi].mean())
    for s, v in zip(scores, q):
        s.quality = float(np.clip(v, 0.0, 1.0))
    return scores


def otsu_threshold(q: np.ndarray, bins: int = 64) -> float:
    """Split a quality curve into bad/good by maximising between-class variance.

    A percentile threshold cannot be used here. Real clips are often mostly
    unusable -- a 10s generation commonly yields 2-3 good seconds -- so a
    mid-percentile lands ON the bad mode and every frame passes it. Otsu finds
    the valley between the two modes instead, so it holds whether 20% or 80% of
    the clip is good.
    """
    q = q[np.isfinite(q)]
    if q.size == 0:
        return 0.0
    lo, hi = float(q.min()), float(q.max())
    if hi - lo < 1e-6:
        return hi + 1.0        # degenerate: nothing stands out, select nothing
    hist, edges = np.histogram(q, bins=bins, range=(lo, hi))
    hist = hist.astype(float)
    centres = (edges[:-1] + edges[1:]) / 2.0
    total = hist.sum()
    w0 = np.cumsum(hist) / total
    w1 = 1.0 - w0
    m0 = np.cumsum(hist * centres) / np.maximum(np.cumsum(hist), 1e-9)
    total_mean = float((hist * centres).sum() / total)
    m1 = (total_mean - w0 * m0) / np.maximum(w1, 1e-9)
    between = w0 * w1 * (m0 - m1) ** 2
    between[~np.isfinite(between)] = -1.0
    return float(centres[int(np.argmax(between))])


def find_spans(scores: list[FrameScore], min_duration: float = 1.0,
               threshold: Optional[float] = None,
               keep_ratio: float = 0.80,
               max_dip_frames: int = 2) -> list[Span]:
    """Maximal contiguous spans clearing `threshold`, at least min_duration long.

    The threshold is the stricter of two per-clip rules, because either alone
    fails on real footage:

      Otsu       finds the valley between the bad and good modes, and so works
                 when most of the clip is unusable.
      keep_ratio keeps only what is within `keep_ratio` of this clip's best
                 quality, which stops a uniformly mediocre clip from passing
                 wholesale just because Otsu found some split in the noise.

    `max_dip_frames` tolerates a one- or two-frame stumble so a usable span is
    not fractured by noise.
    """
    if not scores:
        return []
    q = np.array([s.quality for s in scores])
    t = np.array([s.t for s in scores])
    if threshold is None:
        if float(q.max() - q.min()) < 0.02:
            # Flat curve: nothing in this clip stands out from anything else.
            # Scores are normalised PER CLIP, so a flat curve cannot tell a
            # uniformly good clip from a uniformly bad one -- it only ever ranks
            # within a clip. Offer the whole thing and let the caller flag it
            # for a human look, rather than silently returning nothing.
            thr = float(q.min()) - 1.0
        else:
            high = float(np.percentile(q, 90))
            thr = max(otsu_threshold(q), high * keep_ratio)
    else:
        thr = threshold

    above = q >= thr
    # bridge short dips
    i = 0
    while i < len(above):
        if not above[i]:
            j = i
            while j < len(above) and not above[j]:
                j += 1
            if i > 0 and j < len(above) and (j - i) <= max_dip_frames:
                above[i:j] = True
            i = j
        else:
            i += 1

    spans: list[Span] = []
    i = 0
    while i < len(above):
        if above[i]:
            j = i
            while j < len(above) and above[j]:
                j += 1
            start, end = float(t[i]), float(t[j - 1])
            if end - start >= min_duration:
                seg = q[i:j]
                spans.append(Span(start=round(start, 3), end=round(end, 3),
                                  mean_quality=round(float(seg.mean()), 4),
                                  min_quality=round(float(seg.min()), 4)))
            i = j
        else:
            i += 1
    spans.sort(key=lambda s: -s.mean_quality)
    return spans


def harvest_clip(video_path: str, min_duration: float = 1.0,
                 product_references: Optional[list[str]] = None,
                 keep_ratio: float = 0.80) -> dict[str, Any]:
    """Score one clip and return its usable spans, best first.

    `discriminated` is False when the quality curve was flat. Because scoring is
    normalised per clip, that means the whole clip is equally good OR equally
    bad and this function cannot tell which -- so the spans it returns then need
    a human glance before anything is spent on them.
    """
    scores = build_quality_timeline(
        score_frames(video_path, product_references=product_references))
    spans = find_spans(scores, min_duration=min_duration, keep_ratio=keep_ratio)
    usable = round(sum(s.duration for s in spans), 3)
    total = round(scores[-1].t, 3) if scores else 0.0
    q = [s.quality for s in scores]
    anchored = any(s.product_inliers > 0 for s in scores)
    return {"video": video_path, "duration": total,
            "usable_seconds": usable,
            "yield": round(usable / total, 3) if total else 0.0,
            "n_spans": len(spans),
            "spans": [s.to_dict() for s in spans],
            "product_anchored": anchored,
            "discriminated": bool(q and (max(q) - min(q)) >= 0.02),
            "timeline": [(round(s.t, 3), round(s.quality, 4)) for s in scores]}
