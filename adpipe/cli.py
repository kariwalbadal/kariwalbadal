"""Single-command entry point.

    python3 -m adpipe.cli decompose REF.mp4 --out work/out/ref
    python3 -m adpipe.cli run REF.mp4 --product-image p.png --product-name "X" --dry-run
    python3 -m adpipe.cli review list --root work/out/run/review
    python3 -m adpipe.cli budget REF_EDL.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_decompose(a: argparse.Namespace) -> int:
    from .decompose.pipeline import decompose
    edl = decompose(a.reference, a.out, dense_every=a.dense_every, do_ocr=not a.no_ocr)
    t = edl["timing"]
    print(f"{t['n_shots']} shots | median {t['median_shot_duration']}s | "
          f"{t['rhythm_profile']} | {t['cuts_per_second']} cuts/s")
    au = edl["audio"]
    print(f"audio: bpm={au.get('bpm')} beat_align={au.get('cut_beat_alignment')} "
          f"music_driven={au.get('music_driven_edit')} vo={au.get('voiceover_present')}")
    flags = edl["provenance"]["review_flags"]
    if flags:
        print(f"\n{len(flags)} cut(s) need review by eye (possible jump cut in a take):")
        for f in flags:
            print(f"  t={f['t']}s strength={f['strength']}")
    print(f"\nEDL -> {Path(a.out) / 'edl.json'}")
    return 0


def cmd_budget(a: argparse.Namespace) -> int:
    from .decompose.edl import load_edl
    from .cost import plan_generation_budget, budget_report
    edl = load_edl(a.edl)
    plats = [("kling-v3.0", [float(d) for d in range(3, 16)], 3.0, 15.0, 0.0588),
             ("seedance-2.0-r2v", [float(d) for d in range(4, 16)], 4.0, 15.0, 0.1814),
             ("minimax-h3", [float(d) for d in range(5, 16)], 5.0, 15.0, 0.10),
             ("runway-aleph", None, 1.0, 10.0, 0.15)]
    print(f"EDL: {edl['timing']['n_shots']} shots, {edl['source']['duration']:.2f}s, "
          f"median {edl['timing']['median_shot_duration']}s\n")
    print(f"{'platform':20} {'mode':10} {'gens':>5} {'billed':>8} {'waste':>7} "
          f"{'$/att':>8} {'$/accept':>9}")
    print("-" * 72)
    for name, allowed, lo, hi, rate in plats:
        for pack, label in ((False, "per-shot"), (True, "packed")):
            g = plan_generation_budget(edl, lo, hi, allowed, pack=pack)
            r = budget_report(g, rate, attempts_per_accept=a.attempts)
            print(f"{name:20} {label:10} {r['n_generations']:5} "
                  f"{r['billed_seconds']:8.1f} {r['waste_multiplier']:6.2f}x "
                  f"${r['cost_per_attempt_usd']:7.2f} ${r['cost_per_accepted_ad_usd']:8.2f}")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from .run import run_pipeline
    from .synth.prompts import ProductSpec, StyleSpec
    if a.adapter == "mock":
        from .adapters.mock import MockAdapter
        adapter = MockAdapter()
    elif a.adapter == "kling":
        from .adapters.kling import KlingAdapter
        adapter = KlingAdapter(model=a.model, tier=a.tier)
    else:
        print(f"unknown adapter {a.adapter}", file=sys.stderr)
        return 2

    product = ProductSpec(name=a.product_name, description=a.product_description,
                          colour=a.product_colour, logo_text=a.product_logo,
                          image_paths=list(a.product_image or []))
    style = StyleSpec(visual_direction=a.visual_direction,
                      environments=list(a.environment or []), glow=not a.no_glow)
    res = run_pipeline(a.reference, product, style, out_root=a.out, adapter=adapter,
                       edl_path=a.edl, pack=not a.no_pack,
                       reframe_slices=not a.no_reframe, architecture=a.architecture,
                       monthly_cap_usd=a.cap, glow=a.glow_strength,
                       platform=a.platform, max_attempts=a.attempts,
                       dry_run=a.dry_run)
    status = res.get("status")
    if status == "dry_run":
        print(f"DRY RUN: {res['n_generations_planned']} generations planned")
        print(json.dumps(res["budget_report"], indent=2))
        for p in res["prompts"][:a.show_prompts]:
            print(f"\n--- group {p['group']} shots {p['shots']} "
                  f"({p['edl_s']}s needed, {p['billed_s']}s billed) ---")
            print(p["prompt"])
            if p["lint"]:
                print(f"  LINT: {p['lint']}")
        return 0
    if status != "ok":
        print(f"status={status}", file=sys.stderr)
        print(json.dumps({k: v for k, v in res.items()
                          if k not in ("edl", "prompts")}, indent=2, default=str)[:3000],
              file=sys.stderr)
        return 1
    print(f"OK  {res['final_video']}  {res['duration_s']:.2f}s  "
          f"{res['n_shots']} shots from {res['n_generations']} generations")
    print(f"spent ${res['ledger']['spent_usd']} / cap ${res['ledger']['cap_usd']}")
    print(f"review item {res['review_item']}  ({res['wall_clock_s']}s wall clock)")
    return 0


def cmd_review(a: argparse.Namespace) -> int:
    from .review import ReviewQueue
    rq = ReviewQueue(a.root)
    if a.action == "list":
        for it in rq.all_items():
            print(f"{it.item_id}  {it.status:9} ${it.cost_usd:7.2f}  {it.video_path}")
        print(json.dumps(rq.stats(), indent=2))
    elif a.action == "approve":
        print(rq.approve(a.item_id).status)
    elif a.action == "reject":
        print(rq.reject(a.item_id, a.reason).status)
    elif a.action == "requeue":
        overrides = dict(kv.split("=", 1) for kv in (a.set or []))
        print(json.dumps(rq.requeue(a.item_id, **overrides), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adpipe",
                                description="Video-to-video ad creative pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decompose", help="reference video -> validated EDL")
    d.add_argument("reference")
    d.add_argument("--out", default="work/out/ref")
    d.add_argument("--dense-every", type=float, default=0.5)
    d.add_argument("--no-ocr", action="store_true")
    d.set_defaults(func=cmd_decompose)

    b = sub.add_parser("budget", help="cost projection across platforms")
    b.add_argument("edl")
    b.add_argument("--attempts", type=float, default=3.0)
    b.set_defaults(func=cmd_budget)

    r = sub.add_parser("run", help="reference + product -> finished ad")
    r.add_argument("reference")
    r.add_argument("--out", default="work/out/run")
    r.add_argument("--edl", default=None, help="reuse an existing EDL")
    r.add_argument("--adapter", default="mock", choices=("mock", "kling"))
    r.add_argument("--model", default="kling-video-v3_0_omni")
    r.add_argument("--tier", default="ultra")
    r.add_argument("--platform", default="seedance",
                   help="prompt addressing dialect")
    r.add_argument("--architecture", default="A", choices=("A", "B"))
    r.add_argument("--product-name", default="the product")
    r.add_argument("--product-description", default="")
    r.add_argument("--product-colour", default="")
    r.add_argument("--product-logo", default="")
    r.add_argument("--product-image", action="append")
    r.add_argument("--environment", action="append")
    r.add_argument("--visual-direction", default="clean, premium, contemporary")
    r.add_argument("--glow-strength", type=float, default=0.35)
    r.add_argument("--no-glow", action="store_true")
    r.add_argument("--no-pack", action="store_true",
                   help="one generation per shot (costlier, max rhythm fidelity)")
    r.add_argument("--no-reframe", action="store_true",
                   help="disable slice reframing (WARNING: packed cuts go invisible)")
    r.add_argument("--cap", type=float, default=500.0, help="monthly USD hard stop")
    r.add_argument("--attempts", type=int, default=3)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--show-prompts", type=int, default=2)
    r.set_defaults(func=cmd_run)

    q = sub.add_parser("review", help="approve / reject / requeue outputs")
    q.add_argument("action", choices=("list", "approve", "reject", "requeue"))
    q.add_argument("--root", default="work/out/run/review")
    q.add_argument("--item-id", default=None)
    q.add_argument("--reason", default="")
    q.add_argument("--set", action="append", help="key=value override for requeue")
    q.set_defaults(func=cmd_review)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
