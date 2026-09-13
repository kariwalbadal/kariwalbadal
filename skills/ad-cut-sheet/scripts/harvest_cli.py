#!/usr/bin/env python3
"""Find the usable spans inside a generated clip.

Anchor the score to the product's own reference photo: it was the only signal
that caught every artefact class tested. See ../SKILL.md.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harvest import harvest_clip


def main() -> int:
    ap = argparse.ArgumentParser(description="Find usable spans in a generated clip")
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--product-ref", action="append", default=[],
                    help="product reference image; repeatable. Strongly recommended.")
    ap.add_argument("--min-duration", type=float, default=1.0)
    ap.add_argument("--keep-ratio", type=float, default=0.80)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    results, total_usable, total_dur = [], 0.0, 0.0
    for c in a.clips:
        r = harvest_clip(c, min_duration=a.min_duration,
                         product_references=a.product_ref or None,
                         keep_ratio=a.keep_ratio)
        results.append(r)
        total_usable += r["usable_seconds"]; total_dur += r["duration"]
        warn = "" if r["product_anchored"] else "  [NO PRODUCT REF - weaker signal]"
        if not r["discriminated"]:
            warn += "  [FLAT CURVE - look at this one yourself]"
        print(f"\n{Path(c).name}  {r['duration']:.2f}s -> {r['usable_seconds']:.2f}s usable "
              f"({r['yield']:.0%}) in {r['n_spans']} span(s){warn}")
        for s in r["spans"]:
            print(f"    {s['start']:6.2f}-{s['end']:6.2f}s  ({s['duration']:4.2f}s)  q={s['mean_quality']:.3f}")
    print(f"\nTOTAL: {total_usable:.2f}s usable of {total_dur:.2f}s "
          f"({total_usable/max(total_dur,1e-9):.0%}) across {len(a.clips)} clip(s)")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(results, indent=2))
        print(f"wrote {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
