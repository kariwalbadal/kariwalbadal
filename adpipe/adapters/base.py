"""Adapter contract: schema-validated generation requests.

The split the brief asks for: free-form where the creative judgment lives,
strictly typed where the execution lives. `GenerationRequest.prompt` is
unconstrained prose; everything that costs money or can silently truncate --
durations, resolutions, reference counts -- is validated against a per-platform
JSON Schema BEFORE submission.

This matters more than it looks. Verified vendor behaviour includes accepting
an over-limit request and charging for it, and silently dropping a reference
when a first-frame image is combined with reference inputs. Neither returns an
error, so client-side validation is the only place those are catchable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional, Protocol
import json
import time
import uuid

import jsonschema


class AdapterError(RuntimeError):
    pass


class ValidationFailed(AdapterError):
    pass


@dataclass
class ReferenceAsset:
    kind: str                 # image | video | audio
    path_or_url: str
    token: Optional[str] = None   # how the prompt addresses it (@Image1, <<<id>>>)
    duration_s: Optional[float] = None
    bytes_size: Optional[int] = None


@dataclass
class GenerationRequest:
    """One unit of billable work."""
    shot_index: int
    prompt: str                       # free-form: the creative payload
    duration_s: float
    references: list[ReferenceAsset] = field(default_factory=list)
    negative_prompt: str = ""
    resolution: str = "720p"
    aspect_ratio: str = "16:9"
    model: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def counts(self) -> dict[str, int]:
        c = {"image": 0, "video": 0, "audio": 0}
        for r in self.references:
            c[r.kind] = c.get(r.kind, 0) + 1
        return c

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GenerationResult:
    request_id: str
    shot_index: int
    status: str                       # succeeded | failed | pending
    output_path: Optional[str] = None
    output_url: Optional[str] = None
    cost_usd: float = 0.0
    provider_job_id: Optional[str] = None
    error: Optional[str] = None
    latency_s: float = 0.0
    attempt: int = 1
    raw: dict[str, Any] = field(default_factory=dict)


class Adapter(Protocol):
    name: str

    def request_schema(self) -> dict[str, Any]: ...
    def estimate_cost(self, req: GenerationRequest) -> float: ...
    def submit(self, req: GenerationRequest, out_dir: str) -> GenerationResult: ...


class BaseAdapter:
    """Shared validation, cost estimation and retry behaviour."""
    name = "base"
    max_images = 9
    max_videos = 3
    max_audio = 3
    max_total_files = 12
    allowed_durations: Optional[list[float]] = None
    min_duration = 1.0
    max_duration = 15.0
    allowed_resolutions = ("720p",)
    usd_per_second = 0.0

    def request_schema(self) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["shot_index", "prompt", "duration_s", "resolution"],
            "properties": {
                "shot_index": {"type": "integer"},
                "prompt": {"type": "string", "minLength": 1},   # free-form
                "duration_s": {"type": "number",
                               "minimum": self.min_duration,
                               "maximum": self.max_duration},
                "resolution": {"enum": list(self.allowed_resolutions)},
                "aspect_ratio": {"type": "string"},
                "negative_prompt": {"type": "string"},
                "model": {"type": "string"},
                "references": {"type": "array", "maxItems": self.max_total_files},
            },
        }

    def validate(self, req: GenerationRequest) -> None:
        """Raise ValidationFailed before anything billable happens."""
        try:
            jsonschema.validate(req.to_dict(), self.request_schema())
        except jsonschema.ValidationError as exc:
            raise ValidationFailed(f"{self.name}: {exc.message}") from exc

        c = req.counts()
        if c["image"] > self.max_images:
            raise ValidationFailed(f"{self.name}: {c['image']} images > max {self.max_images}")
        if c["video"] > self.max_videos:
            raise ValidationFailed(f"{self.name}: {c['video']} videos > max {self.max_videos}")
        if c["audio"] > self.max_audio:
            raise ValidationFailed(f"{self.name}: {c['audio']} audio > max {self.max_audio}")
        if sum(c.values()) > self.max_total_files:
            raise ValidationFailed(
                f"{self.name}: {sum(c.values())} reference files > max {self.max_total_files}")
        # Verified behaviour: audio-only reference sets are rejected server-side.
        if c["audio"] > 0 and c["image"] == 0 and c["video"] == 0:
            raise ValidationFailed(
                f"{self.name}: audio reference must travel with an image or video")
        if self.allowed_durations is not None:
            if not any(abs(req.duration_s - d) < 1e-6 for d in self.allowed_durations):
                raise ValidationFailed(
                    f"{self.name}: duration {req.duration_s}s not in "
                    f"{self.allowed_durations}")
        if req.resolution not in self.allowed_resolutions:
            raise ValidationFailed(
                f"{self.name}: resolution {req.resolution} not in {self.allowed_resolutions}")

    def estimate_cost(self, req: GenerationRequest) -> float:
        return round(self.usd_per_second * float(req.duration_s), 4)

    def submit(self, req: GenerationRequest, out_dir: str) -> GenerationResult:
        raise NotImplementedError


def quantise_duration(target: float, allowed: Optional[list[float]],
                      lo: float, hi: float) -> float:
    """Snap an EDL slot to a duration the platform will actually accept.

    Models emit whole seconds; EDL slots are arbitrary. Snapping UP is
    deliberate -- a clip longer than its slot is trimmed losslessly at
    assembly, while a short clip forces a hold-frame that reads as a freeze.
    """
    t = max(lo, min(hi, float(target)))
    if not allowed:
        return round(t, 3)
    longer = [d for d in sorted(allowed) if d >= t - 1e-9]
    return float(longer[0]) if longer else float(max(allowed))
