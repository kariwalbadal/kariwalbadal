"""Intra-clip harvesting: span selection and the scoring rules that matter.

The regression guarded hardest here is the one-sided sharpness rule. An earlier
version weighted sharpness as a reward, and a non-rigid morph measured +256%
sharpness while destroying the frame -- so the scorer ranked the worst second of
a clip as its best (23.5% of selected footage landed in known-bad ranges).
Sharpness may only ever count against a frame, never for it.
"""
from adpipe.harvest import FrameScore, build_quality_timeline, find_spans


def timeline(vals, product=None, sharp=None, fps=24.0):
    """FrameScores with explicit signals; product defaults to tracking `vals`."""
    out = []
    for i, v in enumerate(vals):
        out.append(FrameScore(
            t=(i + 1) / fps,
            product_inliers=(v if product is None else product[i]),
            sharpness=(100.0 if sharp is None else sharp[i]),
            affine_residual=1.0,
            inlier_frac=0.8,
        ))
    return out


# --- span finding ---------------------------------------------------------

def test_finds_single_contiguous_span():
    scores = build_quality_timeline(timeline([10] * 24 + [400] * 48 + [10] * 24),
                                    smooth_frames=1)
    spans = find_spans(scores, min_duration=1.0)
    assert len(spans) == 1
    assert spans[0].duration >= 1.0


def test_rejects_spans_below_min_duration():
    # a good stretch of only ~0.2s must not be offered as a shot
    scores = build_quality_timeline(timeline([10] * 20 + [400] * 5 + [10] * 20),
                                    smooth_frames=1)
    assert find_spans(scores, min_duration=1.0) == []


def test_finds_two_spans_separated_by_a_bad_stretch():
    vals = [400] * 36 + [5] * 36 + [400] * 36
    spans = find_spans(build_quality_timeline(timeline(vals), smooth_frames=1),
                       min_duration=1.0)
    assert len(spans) == 2


def test_spans_avoid_the_bad_stretch():
    vals = [400] * 36 + [5] * 36 + [400] * 36
    spans = find_spans(build_quality_timeline(timeline(vals), smooth_frames=1),
                       min_duration=1.0)
    bad_start, bad_end = 37 / 24.0, 72 / 24.0
    overlap = sum(max(0.0, min(s.end, bad_end) - max(s.start, bad_start))
                  for s in spans)
    assert overlap < 0.15


def test_short_dips_are_bridged_not_split():
    """A one-frame stumble must not fracture an otherwise usable span."""
    vals = [400] * 30 + [5] + [400] * 30
    spans = find_spans(build_quality_timeline(timeline(vals), smooth_frames=1),
                       min_duration=1.0, max_dip_frames=2)
    assert len(spans) == 1


def test_spans_ranked_best_first():
    vals = [400] * 36 + [5] * 24 + [250] * 36
    spans = find_spans(build_quality_timeline(timeline(vals), smooth_frames=1),
                       min_duration=1.0, keep_ratio=0.55)
    assert len(spans) >= 2
    assert spans[0].mean_quality >= spans[1].mean_quality


# --- the scoring rules ----------------------------------------------------

def test_high_sharpness_cannot_rescue_a_failed_product_match():
    """The exact bug: a morph reads as very sharp while the product is ruined.

    Frames 24-48 have 4x the sharpness of everything else but a collapsed
    product match. They must not end up the highest-quality stretch.
    """
    n = 72
    product = [400] * 24 + [20] * 24 + [400] * 24
    sharp = [100.0] * 24 + [400.0] * 24 + [100.0] * 24
    scores = build_quality_timeline(timeline([0] * n, product=product, sharp=sharp),
                                    smooth_frames=1)
    morph = [s.quality for s in scores[24:48]]
    clean = [s.quality for s in scores[:24]] + [s.quality for s in scores[48:]]
    assert max(morph) < min(clean)


def test_low_sharpness_still_penalises():
    """Mush must be caught even when the product somehow still matches."""
    n = 48
    product = [400] * n
    sharp = [100.0] * 24 + [2.0] * 24
    scores = build_quality_timeline(timeline([0] * n, product=product, sharp=sharp),
                                    smooth_frames=1)
    assert max(s.quality for s in scores[24:]) < min(s.quality for s in scores[:24])


def test_product_signal_dominates():
    """Product fidelity outranks sharpness; a wrong product is not shippable."""
    n = 48
    product = [400] * 24 + [10] * 24
    sharp = [80.0] * 24 + [120.0] * 24     # second half nominally "sharper"
    scores = build_quality_timeline(timeline([0] * n, product=product, sharp=sharp),
                                    smooth_frames=1)
    assert max(s.quality for s in scores[24:]) < min(s.quality for s in scores[:24])


def test_falls_back_when_no_product_references():
    """With no product refs the scorer must still produce a usable curve."""
    n = 48
    scores = build_quality_timeline(
        timeline([0] * n, product=[0.0] * n,
                 sharp=[200.0] * 24 + [3.0] * 24), smooth_frames=1)
    assert all(0.0 <= s.quality <= 1.0 for s in scores)
    assert max(s.quality for s in scores[24:]) < max(s.quality for s in scores[:24])


def test_empty_input():
    assert build_quality_timeline([]) == []
    assert find_spans([]) == []
