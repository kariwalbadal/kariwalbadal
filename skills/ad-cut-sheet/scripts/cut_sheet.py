#!/usr/bin/env python3
"""Reference ad -> frame-accurate cut sheet, EDL, and a time-cued prompt block.

Decodes every frame. Never samples. See ../SKILL.md for why that is the whole
point and for the measured failure modes this guards against.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import decompose
from audio_cuts import analyse_audio_cuts, audio_cuts_md


def fmt_tc(t: float, fps: float) -> str:
    """Timecode with the frame number, because cuts live on frames."""
    return f"{t:7.3f}s (f{int(round(t * fps)):>4})"


def cut_sheet_md(edl: dict) -> str:
    src, T, A = edl["source"], edl["timing"], edl["audio"]
    fps = src["fps"]
    L = [f"# Cut sheet — {Path(src['path']).name}", ""]
    L += [f"- **{src['width']}x{src['height']}** @ {fps}fps · **{src['duration']:.3f}s** · {src['aspect_ratio']}",
          f"- **{T['n_shots']} shots**, {len(T['cut_times'])} cuts · median **{T['median_shot_duration']}s** "
          f"(p10 {T['p10_shot_duration']} / p90 {T['p90_shot_duration']}) · {T['cuts_per_second']} cuts/s",
          f"- rhythm: **{T['rhythm_profile']}**"]
    AC = edl.get("audio_cuts") or {}
    if A.get("has_audio"):
        lock = AC.get("edit_locked_to", "?")
        L.append(f"- audio: **{AC.get('bpm', A.get('bpm'))} bpm** · edit locked to "
                 f"**{lock}** · cuts {AC.get('beat_phase','?')} the beat · "
                 f"VO: {A.get('voiceover_present')}")
    # arithmetic reconciliation -- if this fails the cut list is wrong
    total = sum(s["duration"] for s in edl["shots"])
    ok = abs(total - src["duration"]) < 0.05 and T["n_shots"] == len(T["cut_times"]) + 1
    L += ["", f"- reconciliation: shots {T['n_shots']} = cuts {len(T['cut_times'])} + 1, "
              f"durations sum {total:.3f}s vs source {src['duration']:.3f}s → "
              f"**{'OK' if ok else 'MISMATCH — cut list is wrong'}**", ""]

    L += ["## Cut list", "", "```"]
    L += [f"{i+1:>3}. {fmt_tc(c, fps)}" for i, c in enumerate(T["cut_times"])]
    L += ["```", "", "## Shots", "",
          "| # | in | out | dur | role | camera | scale | ramp | out-transition | beat | palette |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in edl["shots"]:
        L.append(f"| {s['index']} | {s['start']:.3f} | {s['end']:.3f} | {s['duration']:.3f} | "
                 f"{s.get('role')} | {s.get('camera_move')} ({s.get('camera_move_confidence')}) | "
                 f"{s.get('shot_scale')} | {s.get('speed_ramp') or '-'} | "
                 f"{s.get('transition_out')} | {'Y' if s.get('on_beat') else ''} | "
                 f"{' '.join(s.get('palette', [])[:3])} |")

    if edl.get("audio_cuts"):
        L += ["", audio_cuts_md(edl["audio_cuts"])]

    if edl.get("onscreen_text"):
        L += ["", "## On-screen text", "", "| shot | in | out | position | text |", "|---|---|---|---|---|"]
        for t in edl["onscreen_text"]:
            L.append(f"| {t['shot_index']} | {t['start']} | {t['end']} | {t['position']} | {t['text']} |")

    flags = edl["provenance"].get("review_flags") or []
    if flags:
        L += ["", "## Needs your eyes", "",
              "Motion discontinuity with no appearance change — possibly a jump cut inside "
              "one take. Not auto-accepted (measured: motion false positives outrank its "
              "true positives).", ""]
        L += [f"- t={f['t']}s (strength {f['strength']})" for f in flags]
    return "\n".join(L)


def prompt_block(edl: dict, product: str = "the product") -> str:
    L = [f"Reference @Video1 for camera movement, cut rhythm and transition timing ONLY.",
         f"Take {product} identity from @Image1..N, never from @Video1.",
         f"Produce a {edl['source']['duration']:.2f}s advert in "
         f"{edl['timing']['n_shots']} shots, holding each shot exactly as timed:", ""]
    for s in edl["shots"]:
        bits = [s.get("camera_move", "").replace("_", " ")]
        if s.get("shot_scale") not in (None, "unknown"):
            bits.insert(0, s["shot_scale"].replace("_", " "))
        if s.get("speed_ramp") in ("ramp_up", "ramp_down"):
            bits.append(s["speed_ramp"].replace("_", " "))
        light = (s.get("lighting") or {}).get("description")
        if light:
            bits.append(light.replace("_", " "))
        L.append(f"[{s['start']:.2f}-{s['end']:.2f}s] {', '.join(b for b in bits if b)}. "
                 f"Cut out with a {s.get('transition_out', 'hard_cut').replace('_', ' ')}.")
    AC = edl.get("audio_cuts") or {}
    if AC.get("edit_locked_to") in ("beat", "onset"):
        phase = {"leads": "landing each cut a touch ahead of", "lags":
                 "landing each cut a touch behind", "on": "landing each cut on"}[AC["beat_phase"]]
        L += ["", f"The edit is locked to the {AC['edit_locked_to']} grid at about "
                  f"{AC['bpm']} BPM, {phase} the beat."]
    L += ["", f"Hold {product} identical across every shot: preserve colour, structure, "
              "silhouette, material and logo legibility."]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reference ad -> cut sheet + EDL + prompt")
    ap.add_argument("video")
    ap.add_argument("--out", default="work/teardown")
    ap.add_argument("--product", default="the product")
    ap.add_argument("--dense-every", type=float, default=0.4,
                    help="seconds between per-shot frame samples (lower = denser)")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--truth-cuts", default=None,
                    help="comma-separated known cut times, to tune the detector")
    a = ap.parse_args()

    truth = [float(x) for x in a.truth_cuts.split(",")] if a.truth_cuts else None
    edl = decompose(a.video, a.out, truth_cuts=truth,
                    dense_every=a.dense_every, do_ocr=not a.no_ocr)

    wav = Path(a.out) / "audio.wav"
    if wav.exists():
        try:
            edl["audio_cuts"] = analyse_audio_cuts(
                str(wav), edl["timing"]["cut_times"], edl["source"]["duration"])
            (Path(a.out) / "edl.json").write_text(json.dumps(edl, indent=2, default=str))
        except Exception as exc:
            print(f"[warn] audio-cut analysis failed: {exc}", file=sys.stderr)

    out = Path(a.out)
    (out / "cut-sheet.md").write_text(cut_sheet_md(edl))
    (out / "prompt.txt").write_text(prompt_block(edl, a.product))
    print(cut_sheet_md(edl))
    print(f"\n--- wrote {out/'edl.json'}, {out/'cut-sheet.md'}, {out/'prompt.txt'}, {out/'frames'}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
