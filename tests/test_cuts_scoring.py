"""Cut-accuracy scoring: the metric the whole validation gate rests on."""
from adpipe.decompose.shots import score_cuts


def test_perfect_match():
    a = score_cuts([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], tolerance=0.1)
    assert (a.precision, a.recall, a.f1) == (1.0, 1.0, 1.0)
    assert a.mean_abs_error == 0.0


def test_within_tolerance_counts_and_reports_error():
    a = score_cuts([1.05], [1.0], tolerance=0.1)
    assert a.true_positives == 1
    assert abs(a.mean_abs_error - 0.05) < 1e-9


def test_outside_tolerance_is_fp_and_fn():
    a = score_cuts([1.5], [1.0], tolerance=0.1)
    assert (a.true_positives, a.false_positives, a.false_negatives) == (0, 1, 1)


def test_duplicate_detections_are_not_forgiven():
    """Two detections around one true cut must cost a false positive.

    Otherwise a jittery detector scores as well as a precise one.
    """
    a = score_cuts([0.98, 1.02], [1.0], tolerance=0.1)
    assert a.true_positives == 1
    assert a.false_positives == 1


def test_greedy_matching_prefers_closest():
    a = score_cuts([1.09, 1.01], [1.0], tolerance=0.1)
    assert a.matched[0][1] == 1.01


def test_empty_detection():
    a = score_cuts([], [1.0, 2.0], tolerance=0.1)
    assert (a.recall, a.false_negatives) == (0.0, 2)


def test_empty_truth():
    a = score_cuts([1.0], [], tolerance=0.1)
    assert a.false_positives == 1
