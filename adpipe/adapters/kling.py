"""Kling adapter, built against the live MCP contract.

Limits here are not from marketing copy -- they were read off the Kling MCP
`who_am_i` response on 2026-09-12 (see docs/capability-matrix.md). The ones
that shape the pipeline:

  * every image_to_video / text_to_video model allows resolution '720p' ONLY;
    1080p appears only on motion_control. 1080p elsewhere is membership-gated.
  * kling-video-v3_0 and v3_0_omni take duration 3-15s and expose
    `prefer_multi_shots`; v2_5/v2_6 allow only 5s or 10s.
  * on v2_6, `tail_image` REQUIRES 1080p, and `enable_audio` is 1080p-only and
    mutually exclusive with a tail image -- so last-frame chaining and audio
    are both unavailable on a free/720p account.
  * Elements bind by id as <<<id>>> in the prompt; plain reference images are
    addressed as 图片N. Video subjects (resource.video) work only on
    image_to_video with v3_0 or v3_0_omni.

Submission note: this session reaches Kling through an MCP server, which is an
agent-side transport rather than an HTTP client this module can call. So
`submit` builds and validates the exact MCP argument payload and hands it back;
an injected `submit_fn` performs the call. That keeps validation, costing and
the ledger in one place regardless of transport.
"""
from __future__ import annotations

from typing import Any, Callable, Optional
import time

from .base import (BaseAdapter, GenerationRequest, GenerationResult,
                   ValidationFailed)

# Verified from who_am_i, 2026-09-12.
KLING_MODELS: dict[str, dict[str, Any]] = {
    "kling-video-v3_0_omni": {"tool": "image_to_video",
                              "durations": [float(d) for d in range(3, 16)],
                              "resolutions": ["720p"], "multi_shot": True,
                              "elements": True, "audio": True, "max_ref_images": 7},
    "kling-video-v3_0": {"tool": "image_to_video",
                         "durations": [float(d) for d in range(3, 16)],
                         "resolutions": ["720p"], "multi_shot": True,
                         "elements": True, "audio": True, "tail_image": True},
    "kling-video-v3_0_turbo": {"tool": "image_to_video",
                               "durations": [float(d) for d in range(3, 16)],
                               "resolutions": ["720p"], "multi_shot": False,
                               "elements": False},
    "kling-video-o1": {"tool": "image_to_video",
                       "durations": [float(d) for d in range(3, 11)],
                       "resolutions": ["720p"], "multi_shot": False,
                       "elements": True, "max_ref_images": 7},
    "kling-video-v2_6": {"tool": "image_to_video", "durations": [5.0, 10.0],
                         "resolutions": ["720p"], "multi_shot": False,
                         "elements": False, "tail_image_requires_1080p": True},
    # The only 1080p route on this account tier.
    "kling-video-v3_0-motion": {"tool": "motion_control", "durations": [],
                                "resolutions": ["720p", "1080p"],
                                "requires_person_or_animal_subject": True,
                                "elements": True},
}

# credits/second x USD/credit. 12 cr/s is 1080p+audio; 6 cr/s is 720p no audio.
KLING_CREDIT_USD = {"standard": 6.99 / 660, "pro": 25.99 / 3000,
                    "premier": 64.99 / 8000, "ultra": 127.99 / 26000}


class KlingAdapter(BaseAdapter):
    name = "kling"
    max_images = 7           # image_1..image_7 on o1 / v3_0_omni
    max_videos = 1           # one video subject via an Element
    max_audio = 0
    max_total_files = 8

    def __init__(self, model: str = "kling-video-v3_0_omni",
                 tier: str = "ultra", credits_per_second: float = 6.0,
                 submit_fn: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None):
        if model not in KLING_MODELS:
            raise ValueError(f"unknown Kling model {model!r}; "
                             f"known: {sorted(KLING_MODELS)}")
        self.model = model
        self.spec = KLING_MODELS[model]
        self.tier = tier
        self.credits_per_second = credits_per_second
        self.submit_fn = submit_fn
        self.allowed_durations = self.spec["durations"] or None
        self.allowed_resolutions = tuple(self.spec["resolutions"])
        self.min_duration = min(self.allowed_durations) if self.allowed_durations else 1.0
        self.max_duration = max(self.allowed_durations) if self.allowed_durations else 15.0
        self.usd_per_second = self.credits_per_second * KLING_CREDIT_USD[tier]

    def validate(self, req: GenerationRequest) -> None:
        super().validate(req)
        # Documented-but-silent traps, caught before spending.
        if req.extra.get("tail_image") and self.spec.get("tail_image_requires_1080p") \
                and req.resolution != "1080p":
            raise ValidationFailed(
                f"{self.name}: {self.model} tail_image requires resolution=1080p "
                f"(got {req.resolution}); on a non-member tier this is unavailable")
        if req.extra.get("enable_audio") and req.extra.get("tail_image") \
                and self.model == "kling-video-v2_6":
            raise ValidationFailed(
                f"{self.name}: v2_6 enable_audio is not supported with a tail image")
        if req.extra.get("prefer_multi_shots") and not self.spec.get("multi_shot"):
            raise ValidationFailed(
                f"{self.name}: {self.model} does not support prefer_multi_shots")
        if req.extra.get("elements") and not self.spec.get("elements"):
            raise ValidationFailed(
                f"{self.name}: {self.model} does not accept Elements")
        if self.spec.get("requires_person_or_animal_subject") and \
                not req.extra.get("subject_is_person_or_animal"):
            raise ValidationFailed(
                f"{self.name}: motion_control expects a subject image containing a "
                f"clear person or animal; a bare product shot is out of contract")

    def build_mcp_payload(self, req: GenerationRequest) -> dict[str, Any]:
        """The exact MCP call for this request. Validated first."""
        self.validate(req)
        args: list[dict[str, str]] = [
            {"name": "prompt", "value": req.prompt},
            {"name": "duration", "value": str(int(req.duration_s))},
            {"name": "resolution", "value": req.resolution},
        ]
        if self.spec.get("multi_shot"):
            args.append({"name": "prefer_multi_shots",
                         "value": "true" if req.extra.get("prefer_multi_shots") else "false"})
        if self.spec.get("audio"):
            args.append({"name": "enable_audio",
                         "value": "true" if req.extra.get("enable_audio") else "false"})
        if req.extra.get("elements"):
            args.append({"name": "elements", "value": req.extra["elements"]})
        if req.aspect_ratio and self.model in ("kling-video-v3_0_omni", "kling-video-o1"):
            args.append({"name": "aspect_ratio", "value": req.aspect_ratio})

        inputs: list[dict[str, str]] = []
        imgs = [r for r in req.references if r.kind == "image"]
        if self.model in ("kling-video-v3_0_omni", "kling-video-o1"):
            for i, r in enumerate(imgs[: self.spec.get("max_ref_images", 7)], start=1):
                inputs.append({"name": f"image_{i}", "inputType": "URL",
                               "url": r.path_or_url})
        else:
            if imgs:
                inputs.append({"name": "first_image", "inputType": "URL",
                               "url": imgs[0].path_or_url})
            if req.extra.get("tail_image"):
                inputs.append({"name": "tail_image", "inputType": "URL",
                               "url": req.extra["tail_image"]})

        return {"tool": f"mcp__Kling__{self.spec['tool']}",
                "model": self.model, "arguments": args, "inputs": inputs,
                "rationale": req.extra.get("rationale",
                                           "Automated ad-creative shot generation "
                                           "driven by a reference EDL."),
                "_estimated_cost_usd": self.estimate_cost(req),
                "_request_id": req.request_id, "_shot_index": req.shot_index}

    def submit(self, req: GenerationRequest, out_dir: str) -> GenerationResult:
        payload = self.build_mcp_payload(req)
        if self.submit_fn is None:
            return GenerationResult(
                request_id=req.request_id, shot_index=req.shot_index,
                status="pending", cost_usd=0.0,
                error="no submit_fn injected: Kling is reached over MCP (agent-side "
                      "transport). Payload validated and returned for submission.",
                raw=payload)
        t0 = time.time()
        try:
            resp = self.submit_fn(payload)
        except Exception as exc:
            return GenerationResult(request_id=req.request_id, shot_index=req.shot_index,
                                    status="failed", error=str(exc),
                                    cost_usd=self.estimate_cost(req),
                                    latency_s=round(time.time() - t0, 2), raw=payload)
        return GenerationResult(
            request_id=req.request_id, shot_index=req.shot_index,
            status=resp.get("status", "pending"),
            output_url=resp.get("url"), output_path=resp.get("path"),
            provider_job_id=resp.get("generationId"),
            cost_usd=self.estimate_cost(req),
            latency_s=round(time.time() - t0, 2), raw={"payload": payload, "resp": resp})
