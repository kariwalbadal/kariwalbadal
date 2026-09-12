"""Review queue: finished outputs land somewhere the operator judges them.

The operator's stated requirement is to review finished outputs only, never to
touch the pipeline. So a rejection must be re-runnable by changing one
parameter: every queue item stores the full request that produced it, and
`requeue` clones that request with a single override applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional
import json
import shutil
import time
import uuid


@dataclass
class ReviewItem:
    item_id: str
    video_path: str
    edl_path: str
    status: str = "pending"          # pending | approved | rejected
    score: dict[str, Any] = field(default_factory=dict)
    request_manifest: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    created: float = field(default_factory=time.time)
    reviewed: Optional[float] = None
    reject_reason: str = ""
    lineage: list[str] = field(default_factory=list)


class ReviewQueue:
    """Filesystem-backed queue. Plain files so the operator can just look."""

    def __init__(self, root: str):
        self.root = Path(root)
        for sub in ("pending", "approved", "rejected"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.jsonl"

    def _append(self, item: ReviewItem) -> None:
        with self.index_path.open("a") as fh:
            fh.write(json.dumps(asdict(item)) + "\n")

    def all_items(self) -> list[ReviewItem]:
        """Latest state per item_id (the index is append-only)."""
        if not self.index_path.exists():
            return []
        latest: dict[str, ReviewItem] = {}
        for line in self.index_path.read_text().splitlines():
            if line.strip():
                it = ReviewItem(**json.loads(line))
                latest[it.item_id] = it
        return sorted(latest.values(), key=lambda i: i.created)

    def submit(self, video_path: str, edl_path: str,
               request_manifest: Optional[dict[str, Any]] = None,
               cost_usd: float = 0.0, score: Optional[dict[str, Any]] = None,
               lineage: Optional[list[str]] = None) -> ReviewItem:
        item_id = uuid.uuid4().hex[:10]
        dst = self.root / "pending" / f"{item_id}_{Path(video_path).name}"
        shutil.copy2(video_path, dst)
        item = ReviewItem(item_id=item_id, video_path=str(dst), edl_path=edl_path,
                          request_manifest=request_manifest or {},
                          cost_usd=cost_usd, score=score or {},
                          lineage=lineage or [])
        (self.root / "pending" / f"{item_id}.manifest.json").write_text(
            json.dumps(asdict(item), indent=2))
        self._append(item)
        return item

    def _move(self, item: ReviewItem, status: str) -> ReviewItem:
        src = Path(item.video_path)
        dst = self.root / status / src.name
        if src.exists():
            shutil.move(str(src), str(dst))
            item.video_path = str(dst)
        item.status = status
        item.reviewed = time.time()
        self._append(item)
        return item

    def approve(self, item_id: str) -> ReviewItem:
        item = self.get(item_id)
        return self._move(item, "approved")

    def reject(self, item_id: str, reason: str = "") -> ReviewItem:
        item = self.get(item_id)
        item.reject_reason = reason
        return self._move(item, "rejected")

    def get(self, item_id: str) -> ReviewItem:
        for it in self.all_items():
            if it.item_id == item_id:
                return it
        raise KeyError(item_id)

    def pending(self) -> list[ReviewItem]:
        return [i for i in self.all_items() if i.status == "pending"]

    def requeue(self, item_id: str, **overrides: Any) -> dict[str, Any]:
        """Build the re-run manifest for a rejected item with one change.

        Returns the manifest rather than running it, so the caller stays in
        control of spend.
        """
        item = self.get(item_id)
        manifest = json.loads(json.dumps(item.request_manifest))
        applied: dict[str, Any] = {}
        for k, v in overrides.items():
            manifest[k] = v
            applied[k] = v
        manifest["_requeue"] = {"from_item": item_id,
                                "previous_reject_reason": item.reject_reason,
                                "overrides": applied,
                                "lineage": item.lineage + [item_id]}
        return manifest

    def stats(self) -> dict[str, Any]:
        items = self.all_items()
        n = len(items)
        appr = sum(1 for i in items if i.status == "approved")
        rej = sum(1 for i in items if i.status == "rejected")
        reviewed = appr + rej
        return {"total": n, "pending": n - reviewed, "approved": appr,
                "rejected": rej,
                "accept_rate": round(appr / reviewed, 3) if reviewed else None,
                "total_cost_usd": round(sum(i.cost_usd for i in items), 2),
                "cost_per_approved_usd": (round(sum(i.cost_usd for i in items) / appr, 2)
                                          if appr else None)}
