# Video-to-Video Ad Creative Pipeline

Reference ad + product assets → finished 20–35s multi-shot creative, assembled to
the reference's own cut rhythm.

**Start with [`TONIGHT.md`](TONIGHT.md)** for the fastest runnable path, and
[`RECOMMENDATION.md`](RECOMMENDATION.md) for the three ranked options and what I
would not promise a client.

> **Status:** the deterministic half (decomposition → prompt synthesis →
> assembly → review) is built, measured and tested. The generative half is
> **untested** — no platform was funded in the session that produced this. See
> [`docs/test-results.md`](docs/test-results.md).

## Why this exists

Handing a reference video straight to an LLM and asking for a prompt fails
because the model samples roughly one frame per ten seconds — it never sees the
cuts, shot boundaries, transitions or editing rhythm, which is exactly the
information that makes an ad work. So the reference is **decomposed
programmatically first**, into an EDL that drives everything downstream.

Measured on real footage: **cut precision 1.000, timing error 28ms**, and **4.1
frames sampled per shot** instead of one per ten seconds.

## Install

```bash
apt-get install -y ffmpeg tesseract-ocr
pip3 install scenedetect opencv-python-headless librosa soundfile jsonschema pytesseract pytest
```

## Use

```bash
# 1. reference -> validated EDL (review any flagged cuts by eye)
PYTHONPATH=. python3 -m adpipe.cli decompose work/refs/YOUR_AD.mp4 --out work/out/ref

# 2. what will this cost, on which platform
PYTHONPATH=. python3 -m adpipe.cli budget work/out/ref/edl.json --attempts 3

# 3. see the prompts and cost without spending anything
PYTHONPATH=. python3 -m adpipe.cli run work/refs/YOUR_AD.mp4 \
    --edl work/out/ref/edl.json --product-image p.png \
    --product-name "the X" --dry-run

# 4. run it (mock = local placeholder footage, zero spend)
PYTHONPATH=. python3 -m adpipe.cli run work/refs/YOUR_AD.mp4 \
    --edl work/out/ref/edl.json --product-image p.png \
    --product-name "the X" --adapter mock --out work/out/run

# 5. review
PYTHONPATH=. python3 -m adpipe.cli review list --root work/out/run/review
```

## Layout

```
adpipe/
  decompose/     reference -> EDL
    edl.py         schema + cross-field invariants (refuses to emit a bad EDL)
    shots.py       shot boundaries, threshold sweep, accuracy scoring
    motion.py      optical flow: camera-move classification + cut discontinuity
    fuse.py        appearance/motion cut fusion, precision-first
    frames.py      density-scaled frame extraction (the original bug's fix)
    audio.py       tempo, beat grid, cut/beat alignment, VO heuristic
    appearance.py  palette, lighting, shot-scale heuristic
    transitions.py hard cut / dissolve / whip / fade at each boundary
    ostext.py      OCR inventory of burned-in text
    pipeline.py    orchestrator
  synth/prompts.py EDL shot -> generation prompt (versioned, pure, unit-tested)
  adapters/        schema-validated request envelopes (base, kling, mock)
  assemble/        ffmpeg cut assembly, colour normalisation, glow/bloom
  cost.py          budget ledger w/ hard stop + generation packing planner
  review.py        approve / reject / requeue-with-one-change
  run.py           end-to-end orchestration
  cli.py           single-command entry point
tools/             ground-truth reference builders (synthetic, real, camera moves)
docs/              capability matrix, prompt patterns, validation, test results
```

## Design decisions worth knowing

**Strict where it spends, free where it creates.** `GenerationRequest.prompt` is
unconstrained prose; durations, resolutions and reference counts are validated
against a per-platform JSON Schema *before* submission. Verified vendor behaviour
includes accepting an over-limit request and charging for it, and silently
dropping a reference input with no error — client-side validation is the only
place those are catchable.

**Precision over recall on cut detection.** Every generated shot costs money, so
inventing a cut buys a wasted generation and corrupts the rhythm. Ambiguous
boundaries are surfaced as review flags instead (`fuse.py`).

**Pack generations, then reframe the slices.** Ad grammar's sub-second cuts
against a 3–5s billing floor wastes 2–3×. Packing consecutive shots into one
generation and slicing per the EDL halves cost — but slices of one take have no
*visible* cut between them (measured: rhythm F1 0.929 → 0.571). Reframing each
slice restores it to 0.889 at half the generations.

**The EDL slot is authoritative, not the model's output length.** Models emit
whole seconds; `conform_clip` retimes every clip to its exact slot, so the
reference's rhythm survives.

## Tests

```bash
python3 -m pytest tests/ -q     # 44 tests
```

Covering EDL invariants, cut-accuracy scoring (including that duplicate
detections are not forgiven), prompt synthesis and its lint rules, the verified
Kling contract traps, budget guards, and packing economics.
