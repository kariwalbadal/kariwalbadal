"""Cut-list fusion across appearance and motion detectors.

Measured behaviour on real footage (docs/EDL-VALIDATION.md):

  histogram (appearance)  P=1.000 R=0.867  -- never invents a cut, misses
                                              jump cuts inside a take
  flow (motion)           P=0.867 R=0.867  -- finds some jump cuts, but its
                                              false positives score d=1.000,
                                              *higher* than the true cut it
                                              rescues (0.609)

Because flow's errors outrank its successes, its extra candidates cannot be
auto-accepted on strength. The default policy is therefore precision-first:
trust appearance, and surface motion-only candidates as review flags. Every
generated shot costs money, so inventing a cut is more expensive than flagging
an ambiguous one for ten seconds of operator attention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Policy = Literal["appearance_only", "union", "strong_union", "review_flags"]


@dataclass
class FusedCuts:
    cuts: list[float]
    policy: str
    review_flags: list[dict[str, Any]] = field(default_factory=list)
    agreed: list[float] = field(default_factory=list)
    appearance_only: list[float] = field(default_factory=list)
    motion_only: list[float] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_flags)


def fuse_cuts(appearance_cuts: list[float], motion_cuts: list[float],
              motion_strength: Optional[dict[float, float]] = None,
              policy: Policy = "review_flags",
              agree_window: float = 0.12,
              strong_threshold: float = 0.55) -> FusedCuts:
    """Combine an appearance cut list with a motion cut list.

    `agree_window` is ~3 frames at 24fps; measured timing error between the two
    detectors on real footage was under 50ms.
    """
    app = sorted(float(x) for x in appearance_cuts)
    mot = sorted(float(x) for x in motion_cuts)
    strength = motion_strength or {}

    agreed = [a for a in app if any(abs(a - m) <= agree_window for m in mot)]
    app_only = [a for a in app if not any(abs(a - m) <= agree_window for m in mot)]
    mot_only = [m for m in mot if not any(abs(a - m) <= agree_window for a in app)]

    if policy == "appearance_only":
        cuts, flags = list(app), []
    elif policy == "union":
        cuts, flags = sorted(app + mot_only), []
    elif policy == "strong_union":
        strong = [m for m in mot_only if strength.get(m, 1.0) >= strong_threshold]
        cuts, flags = sorted(app + strong), []
    elif policy == "review_flags":
        cuts = list(app)
        flags = [{"t": round(m, 3), "reason": "motion_discontinuity_without_appearance_change",
                  "strength": round(strength.get(m, float("nan")), 3),
                  "suggest": "possible jump cut inside a continuous take; confirm by eye"}
                 for m in mot_only]
    else:
        raise ValueError(f"unknown policy {policy!r}")

    return FusedCuts(cuts=cuts, policy=policy, review_flags=flags, agreed=agreed,
                     appearance_only=app_only, motion_only=mot_only)
