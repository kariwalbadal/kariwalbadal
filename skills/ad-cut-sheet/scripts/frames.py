"""Dense per-shot frame extraction.

One frame per shot is what broke the operator's earlier attempt: a single
sample cannot show camera movement *within* a shot, so the model never saw the
motion it was supposed to reproduce. Sampling here is density-scaled -- short
hook shots get first/middle/last, long payoff shots get a frame roughly every
`dense_every` seconds -- so within-shot movement is always visible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import subprocess

from edl import FrameRef


def plan_sample_times(start: float, end: float, dense_every: float = 0.5,
                      min_samples: int = 3, max_samples: int = 12) -> list[tuple[float, str]]:
    """Sample times for one shot, with position labels.

    Samples are pulled inward from the boundaries by a small epsilon: a frame
    taken exactly at a cut can decode from the wrong side of it.
    """
    dur = max(1e-3, end - start)
    eps = min(0.04, dur / 8.0)
    a, b = start + eps, end - eps
    n = int(round(dur / dense_every)) + 1
    n = max(min_samples, min(max_samples, n))
    if n <= 1:
        return [((a + b) / 2.0, "middle")]
    times = [a + (b - a) * i / (n - 1) for i in range(n)]
    labels: list[str] = []
    for i in range(n):
        if i == 0:
            labels.append("first")
        elif i == n - 1:
            labels.append("last")
        elif n >= 3 and i == n // 2:
            labels.append("middle")
        else:
            labels.append(f"dense_{i}")
    return list(zip(times, labels))


def extract_shot_frames(video_path: str, shot_index: int, start: float, end: float,
                        out_dir: str, dense_every: float = 0.5,
                        width: Optional[int] = 512) -> list[FrameRef]:
    """Extract frames for one shot; returns FrameRefs for the EDL."""
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    refs: list[FrameRef] = []
    for t, pos in plan_sample_times(start, end, dense_every=dense_every):
        path = out / f"shot{shot_index:03d}_{pos}_{t:.3f}.jpg"
        vf = f"scale={width}:-2" if width else "null"
        # -ss before -i seeks fast; accurate enough since we re-decode one frame.
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.4f}",
               "-i", video_path, "-frames:v", "1", "-vf", vf, "-q:v", "3", str(path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            continue
        if path.exists() and path.stat().st_size > 0:
            refs.append(FrameRef(t=round(t, 4), path=str(path), position=pos))
    return refs
