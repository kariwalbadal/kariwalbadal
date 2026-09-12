"""End-to-end orchestration: reference video + product assets in, ad out.

Stage order is load-bearing:

    decompose -> plan budget -> synthesize -> generate -> conform
              -> normalise -> assemble -> mux audio -> review queue

Budget planning sits before generation so spend is authorised against the
ledger BEFORE any billable call, and normalisation sits after generation
because it needs to measure what actually came back.

Packed groups are generated as one clip and then SLICED per EDL shot. That is
what buys both the cost saving and continuity: slices of one take share
lighting and product identity for free.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
import json
import shutil
import subprocess
import time

from .decompose.pipeline import decompose
from .decompose.edl import load_edl
from .synth.prompts import (ProductSpec, StyleSpec, synthesize_shot_prompt,
                            synthesize_multishot_prompt, lint_prompt,
                            PROMPT_SYNTH_VERSION)
from .adapters.base import GenerationRequest, ReferenceAsset, GenerationResult
from .cost import CostLedger, plan_generation_budget, budget_report, BudgetExceeded
from .assemble.cuts import (AssemblySpec, conform_clip, concat_clips, mux_audio,
                            probe_duration)
from .assemble.finish import plan_normalisation, apply_finish
from .review import ReviewQueue


# Distinct framings applied to consecutive slices of one packed take.
# (zoom, x_anchor, y_anchor) with anchors in 0..1 of the available margin.
REFRAME_LADDER: list[tuple[float, float, float]] = [
    (1.00, 0.5, 0.5),    # full frame
    (1.34, 0.28, 0.42),  # tighter, pushed left
    (1.18, 0.72, 0.58),  # medium, pushed right
    (1.52, 0.5, 0.30),   # close, high
    (1.10, 0.35, 0.70),  # loose, low left
    (1.42, 0.66, 0.44),  # close, right
]


def slice_group_clip(clip: str, group_shots: list[dict[str, Any]], out_dir: str,
                     spec: AssemblySpec, reframe: bool = True
                     ) -> list[tuple[int, str]]:
    """Cut one packed generation into its constituent EDL shots.

    Offsets are cumulative within the group, so the slices land on the
    reference's cut points inside what the model rendered as one take.

    `reframe` exists because of a measured problem: slices of one continuous
    take are continuous footage, so the cut between them is INVISIBLE. Packing
    without reframing measured 0.571 edit-rhythm F1 against the reference,
    versus 0.889 unpacked -- it halves cost by destroying the cut rhythm the
    ad is being bought for. Giving each slice its own framing off the ladder
    above makes consecutive slices read as separate camera setups, which is the
    standard way an editor cuts within a single take.
    """
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    avail = probe_duration(clip)
    results: list[tuple[int, str]] = []
    t = 0.0
    for k, s in enumerate(group_shots):
        want = float(s["duration"])
        dst = out / f"slice_shot{s['index']:03d}.mp4"
        take = min(want, max(0.0, avail - t))
        if take <= 1e-3:
            break
        vf = None
        if reframe and len(group_shots) > 1:
            z, ax, ay = REFRAME_LADDER[k % len(REFRAME_LADDER)]
            if z > 1.001:
                cw, ch = f"iw/{z:.4f}", f"ih/{z:.4f}"
                vf = (f"crop=w={cw}:h={ch}:x='(iw-{cw})*{ax:.3f}':y='(ih-{ch})*{ay:.3f}',"
                      f"scale={spec.width}:{spec.height},setsar=1")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.4f}",
               "-i", clip, "-t", f"{take:.4f}", "-an"]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf),
                "-pix_fmt", spec.pix_fmt, str(dst)]
        subprocess.run(cmd, check=True)
        results.append((s["index"], str(dst)))
        t += want
    return results


def run_pipeline(reference_video: str, product: ProductSpec, style: StyleSpec,
                 out_root: str, adapter, *,
                 edl_path: Optional[str] = None,
                 truth_cuts: Optional[list[float]] = None,
                 pack: bool = True,
                 architecture: str = "A",
                 monthly_cap_usd: float = 500.0,
                 glow: float = 0.35,
                 normalise: bool = True,
                 use_reference_audio: bool = True,
                 reframe_slices: bool = True,
                 max_attempts: int = 3,
                 platform: str = "seedance",
                 dry_run: bool = False) -> dict[str, Any]:
    """Run the full pipeline. Returns a manifest of everything that happened."""
    t_start = time.time()
    root = Path(out_root); root.mkdir(parents=True, exist_ok=True)
    for sub in ("decompose", "gen", "conformed", "graded", "slices", "final"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    ledger = CostLedger(monthly_cap_usd=monthly_cap_usd, path=str(root / "ledger.jsonl"))
    spec = AssemblySpec()

    # --- 1. decompose ----------------------------------------------------
    if edl_path:
        edl = load_edl(edl_path)
    else:
        edl = decompose(reference_video, str(root / "decompose"), truth_cuts=truth_cuts)
    spec.fps = int(round(edl["source"]["fps"])) or 30
    spec.width, spec.height = edl["source"]["width"], edl["source"]["height"]

    # --- 2. plan budget --------------------------------------------------
    groups = plan_generation_budget(
        edl, adapter.min_duration, adapter.max_duration,
        adapter.allowed_durations, pack=pack)
    report = budget_report(groups, adapter.usd_per_second)
    try:
        ledger.authorise(report["cost_per_attempt_usd"], "full ad generation")
    except BudgetExceeded as exc:
        return {"status": "aborted_budget", "error": str(exc),
                "budget_report": report}

    by_index = {s["index"]: s for s in edl["shots"]}

    # --- 3. synthesize + 4. generate ------------------------------------
    prompts_log: list[dict[str, Any]] = []
    gen_results: list[GenerationResult] = []
    shot_clips: dict[int, str] = {}

    for gi, group in enumerate(groups):
        shots = [by_index[i] for i in group.shot_indices]
        lead = shots[0]
        if architecture == "B" and len(shots) > 1:
            sub_edl = {**edl, "shots": shots}
            sp = synthesize_multishot_prompt(sub_edl, product, style, platform=platform)
        else:
            # A packed group is ONE continuous take spanning several EDL slots,
            # so the prompt must state the GROUP's duration. Using the lead
            # shot's duration here produced a self-contradicting prompt ("a 0.5
            # second shot ... sustain for 5.8 seconds").
            lead_for_prompt = (dict(lead, duration=group.edl_seconds)
                               if len(shots) > 1 else lead)
            sp = synthesize_shot_prompt(lead_for_prompt, product, style,
                                        platform=platform)
            if len(shots) > 1:
                beats = ", ".join(f"{float(x['duration']):.2f}s" for x in shots)
                sp.text += (f" Sustain one continuous take with steady, "
                            f"uninterrupted motion suitable for cutting into "
                            f"{len(shots)} consecutive beats of {beats}.")

        refs = [ReferenceAsset("image", p, sp.reference_map.get("product_image"))
                for p in product.image_paths]
        if platform in ("seedance", "minimax_h3") and reference_video:
            refs.append(ReferenceAsset("video", reference_video,
                                       sp.reference_map.get("reference_video")))
        problems = lint_prompt(sp, n_images=sum(1 for r in refs if r.kind == "image"),
                               n_videos=sum(1 for r in refs if r.kind == "video"))
        req = GenerationRequest(
            shot_index=lead["index"], prompt=sp.text, negative_prompt=sp.negative,
            duration_s=group.billed_seconds, references=refs,
            resolution=adapter.allowed_resolutions[0], model=getattr(adapter, "model", ""),
            extra={"camera_move": lead.get("camera_move"),
                   "group_shots": group.shot_indices,
                   "prefer_multi_shots": architecture == "B" and len(shots) > 1})
        prompts_log.append({"group": gi, "shots": group.shot_indices,
                            "billed_s": group.billed_seconds,
                            "edl_s": group.edl_seconds, "lint": problems,
                            "prompt": sp.text, "synth_version": PROMPT_SYNTH_VERSION})
        if dry_run:
            continue

        res: Optional[GenerationResult] = None
        for attempt in range(1, max_attempts + 1):
            est = adapter.estimate_cost(req)
            try:
                ledger.authorise(est, f"group {gi} attempt {attempt}")
            except BudgetExceeded as exc:
                res = GenerationResult(request_id=req.request_id,
                                       shot_index=req.shot_index, status="failed",
                                       error=str(exc), attempt=attempt)
                break
            r = adapter.submit(req, str(root / "gen"))
            r.attempt = attempt
            ledger.record("generation" if attempt == 1 else "retry", adapter.name,
                          req.shot_index, r.cost_usd, r.status,
                          duration_s=req.duration_s, request_id=req.request_id,
                          note=f"group{gi} attempt{attempt}")
            res = r
            if r.status == "succeeded":
                break
        if res:
            gen_results.append(res)
        if res and res.status == "succeeded" and res.output_path:
            if len(shots) > 1:
                for idx, path in slice_group_clip(res.output_path, shots,
                                                  str(root / "slices"), spec,
                                                  reframe=reframe_slices):
                    shot_clips[idx] = path
            else:
                shot_clips[lead["index"]] = res.output_path

    if dry_run:
        return {"status": "dry_run", "edl": edl, "groups": [asdict(g) for g in groups],
                "budget_report": report, "prompts": prompts_log,
                "n_generations_planned": len(groups)}

    missing = [s["index"] for s in edl["shots"] if s["index"] not in shot_clips]
    if missing:
        return {"status": "incomplete", "missing_shots": missing,
                "budget_report": report, "ledger": ledger.summary(),
                "generations": [asdict(r) for r in gen_results]}

    # --- 5. conform to EDL timing ---------------------------------------
    conformed: list[str] = []
    conform_log: list[dict[str, Any]] = []
    for s in edl["shots"]:
        dst = str(root / "conformed" / f"shot{s['index']:03d}.mp4")
        info = conform_clip(shot_clips[s["index"]], dst, float(s["duration"]), spec,
                            speed_ramp=s.get("speed_ramp"))
        conform_log.append(info)
        conformed.append(dst)

    # --- 6. normalise + finish ------------------------------------------
    finished: list[str] = []
    norm_plan = plan_normalisation(conformed) if normalise else {}
    for p in conformed:
        corr = norm_plan.get(p, {"gain": 1.0, "sat": 1.0})
        dst = str(root / "graded" / Path(p).name)
        apply_finish(p, dst, gain=corr["gain"], sat=corr["sat"], glow=glow,
                     crf=spec.crf, preset=spec.preset)
        finished.append(dst)

    # --- 7. assemble + 8. audio -----------------------------------------
    silent = str(root / "final" / "assembled_silent.mp4")
    concat_clips(finished, silent, spec, work_dir=str(root / "final"))
    final = str(root / "final" / "ad.mp4")
    ref_audio = root / "decompose" / "audio.wav"
    if use_reference_audio and ref_audio.exists():
        mux_audio(silent, str(ref_audio), final, spec)
    else:
        shutil.copy2(silent, final)

    # --- 9. review queue -------------------------------------------------
    rq = ReviewQueue(str(root / "review"))
    manifest = {"reference_video": reference_video, "product": asdict(product),
                "style": asdict(style), "architecture": architecture,
                "platform": platform, "pack": pack, "glow": glow,
                "adapter": adapter.name, "synth_version": PROMPT_SYNTH_VERSION}
    item = rq.submit(final, str(root / "decompose" / "edl.json"),
                     request_manifest=manifest, cost_usd=ledger.spent)

    return {"status": "ok", "final_video": final, "duration_s": probe_duration(final),
            "n_shots": len(edl["shots"]), "n_generations": len(groups),
            "budget_report": report, "ledger": ledger.summary(),
            "conform": conform_log, "normalisation": norm_plan,
            "review_item": item.item_id, "prompts": prompts_log,
            "wall_clock_s": round(time.time() - t_start, 1),
            "edl_timing": edl["timing"]}
