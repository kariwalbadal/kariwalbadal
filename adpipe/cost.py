"""Cost ledger with a hard stop, and generation-budget planning.

Two jobs.

`CostLedger` records every billable attempt against a monthly cap and refuses
to authorise work that would breach it. Retries are where per-video cost
actually goes, so failures are recorded as spend, not forgotten.

`plan_generation_budget` addresses a structural cost problem that only shows up
once real numbers are in. Ad grammar runs sub-second cuts; generation platforms
bill per second with a 3-4s floor. Generating one clip per EDL shot therefore
pays for 3-4s to use 0.5s -- a 2-3x multiplier on every fast-cut ad. Packing
consecutive shots that share a visual world into ONE longer generation and
slicing it per the EDL recovers most of that, because the slices come from a
single continuous take (which also holds continuity for free).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
import json
import time


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class LedgerEntry:
    ts: float
    kind: str                 # generation | retry | upload | other
    platform: str
    shot_index: int
    cost_usd: float
    status: str
    duration_s: float = 0.0
    request_id: str = ""
    note: str = ""


class CostLedger:
    """Append-only spend log with a hard cap.

    `authorise` is called BEFORE submission and raises rather than returning a
    flag, so a caller cannot accidentally ignore it and spend anyway.
    """

    def __init__(self, monthly_cap_usd: float = 500.0, path: Optional[str] = None,
                 reserve_fraction: float = 0.0):
        self.cap = float(monthly_cap_usd)
        self.path = Path(path) if path else None
        self.reserve_fraction = reserve_fraction
        self.entries: list[LedgerEntry] = []
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        for line in self.path.read_text().splitlines():
            if line.strip():
                self.entries.append(LedgerEntry(**json.loads(line)))

    @property
    def spent(self) -> float:
        return round(sum(e.cost_usd for e in self.entries), 4)

    @property
    def remaining(self) -> float:
        usable = self.cap * (1.0 - self.reserve_fraction)
        return round(usable - self.spent, 4)

    def authorise(self, estimated_usd: float, what: str = "generation") -> None:
        if estimated_usd > self.remaining:
            raise BudgetExceeded(
                f"{what} would cost ${estimated_usd:.2f} but only "
                f"${self.remaining:.2f} of the ${self.cap:.2f} cap remains "
                f"(spent ${self.spent:.2f})")

    def record(self, kind: str, platform: str, shot_index: int, cost_usd: float,
               status: str, duration_s: float = 0.0, request_id: str = "",
               note: str = "") -> LedgerEntry:
        e = LedgerEntry(ts=time.time(), kind=kind, platform=platform,
                        shot_index=shot_index, cost_usd=round(float(cost_usd), 6),
                        status=status, duration_s=duration_s,
                        request_id=request_id, note=note)
        self.entries.append(e)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(asdict(e)) + "\n")
        return e

    def summary(self) -> dict[str, Any]:
        by_status: dict[str, float] = {}
        by_platform: dict[str, float] = {}
        for e in self.entries:
            by_status[e.status] = round(by_status.get(e.status, 0.0) + e.cost_usd, 4)
            by_platform[e.platform] = round(by_platform.get(e.platform, 0.0) + e.cost_usd, 4)
        wasted = round(sum(e.cost_usd for e in self.entries if e.status != "succeeded"), 4)
        return {"cap_usd": self.cap, "spent_usd": self.spent,
                "remaining_usd": self.remaining, "n_entries": len(self.entries),
                "by_status": by_status, "by_platform": by_platform,
                "spend_on_failed_attempts_usd": wasted,
                "billed_seconds": round(sum(e.duration_s for e in self.entries), 2)}


@dataclass
class GenerationGroup:
    """One billable generation covering one or more EDL shots."""
    shot_indices: list[int]
    edl_seconds: float           # what the EDL actually needs
    billed_seconds: float        # what the platform will charge for
    camera_moves: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def waste_seconds(self) -> float:
        return round(max(0.0, self.billed_seconds - self.edl_seconds), 3)


def plan_generation_budget(edl: dict[str, Any], min_duration: float,
                           max_duration: float,
                           allowed_durations: Optional[list[float]] = None,
                           pack: bool = True,
                           packable_moves: Optional[set[str]] = None
                           ) -> list[GenerationGroup]:
    """Group EDL shots into billable generations.

    Packing only merges consecutive shots whose transition is a hard cut and
    whose camera moves are compatible. A dissolve or a whip at the seam has to
    be rendered across two real clips, and packing across a genuine change of
    visual world would produce one continuous take where the reference had a
    scene change.
    """
    from .adapters.base import quantise_duration

    shots = edl["shots"]
    groups: list[GenerationGroup] = []
    if not pack:
        for s in shots:
            billed = quantise_duration(float(s["duration"]), allowed_durations,
                                       min_duration, max_duration)
            groups.append(GenerationGroup(
                shot_indices=[s["index"]], edl_seconds=round(float(s["duration"]), 3),
                billed_seconds=billed, camera_moves=[s.get("camera_move", "unknown")],
                reason="one_generation_per_shot"))
        return groups

    ok_moves = packable_moves or {"static", "push_in", "pull_out", "orbit",
                                  "pan_left", "pan_right", "tilt_up", "tilt_down",
                                  "dolly", "handheld"}
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        if not cur:
            return
        need = sum(float(s["duration"]) for s in cur)
        billed = quantise_duration(need, allowed_durations, min_duration, max_duration)
        groups.append(GenerationGroup(
            shot_indices=[s["index"] for s in cur],
            edl_seconds=round(need, 3), billed_seconds=billed,
            camera_moves=[s.get("camera_move", "unknown") for s in cur],
            reason=("packed_continuous_take" if len(cur) > 1 else "single_shot")))
        cur.clear()

    for s in shots:
        prospective = sum(float(x["duration"]) for x in cur) + float(s["duration"])
        move_ok = s.get("camera_move", "unknown") in ok_moves
        prev_hard = (not cur) or cur[-1].get("transition_out") == "hard_cut"
        if cur and (prospective > max_duration or not move_ok or not prev_hard):
            flush()
        cur.append(s)
        if not move_ok:                 # a whip/dissolve shot stays on its own
            flush()
    flush()
    return groups


def budget_report(groups: list[GenerationGroup], usd_per_second: float,
                  attempts_per_accept: float = 1.0) -> dict[str, Any]:
    edl_s = round(sum(g.edl_seconds for g in groups), 2)
    billed_s = round(sum(g.billed_seconds for g in groups), 2)
    cost = round(billed_s * usd_per_second, 4)
    return {"n_generations": len(groups),
            "edl_seconds_needed": edl_s,
            "billed_seconds": billed_s,
            "waste_seconds": round(billed_s - edl_s, 2),
            "waste_multiplier": round(billed_s / edl_s, 3) if edl_s else None,
            "cost_per_attempt_usd": cost,
            "attempts_per_accept": attempts_per_accept,
            "cost_per_accepted_ad_usd": round(cost * attempts_per_accept, 2)}
