---
name: ad-cut-sheet
description: >
  Tear a reference ad down into a frame-accurate cut sheet — exact cut list, per-shot
  camera move, shot scale, transition type, speed ramps, audio/beat sync and on-screen
  text — and emit an EDL plus a time-cued prompt block for MiniMax H3, Seedance or Veo.
  Use when asked to analyse, decompose, break down, tear down, reverse engineer, clone or
  recreate an ad or reference video; to produce a cut sheet, shot list, shot table, EDL or
  edit-rhythm breakdown; to work out how a video was cut, what its transitions are, or how
  its music lines up with its cuts; or to find the usable seconds inside generated clips.
  Use before writing any prompt meant to reproduce an existing video's structure.
license: Apache-2.0
metadata:
  version: "1.1.0"
  requires:
    bins: ["ffmpeg", "ffprobe", "python3"]
    pip: ["scenedetect", "opencv-python-headless", "librosa", "soundfile", "numpy", "jsonschema", "pytesseract"]
---

# Ad cut sheet

## Rule zero: decode every frame, never sample

"Watch this video and describe it" fails because the video arrives as a few sampled
frames — often one per ten seconds. The model never sees the cuts, transitions or rhythm,
i.e. exactly what was asked for, and answers confidently about a video it did not watch.
Re-prompting cannot fix it.

Fast ads make it worse: at 24fps a whip transition is two frames. Extract all frames to
disk, detect over the files, and read the frames either side of every boundary.

## Run

```bash
python3 scripts/cut_sheet.py REF.mp4 --out work/teardown --product "the X"
python3 scripts/cut_sheet.py REF.mp4 --out work/teardown --truth-cuts 0.5,0.92,1.42  # tune
python3 scripts/harvest_cli.py clip*.mp4 --product-ref product.png  # usable spans
```

Writes `edl.json`, `cut-sheet.md`, `prompt.txt`, `frames/`.

## Pipeline

| # | Step | Non-obvious part |
|---|---|---|
| 1 | Probe | State duration, fps, frame count |
| 2 | Detect cuts | Two detectors: `histogram` 0.08 (appearance) + optical-flow (motion) |
| 3 | **Reconcile** | shots = cuts+1, durations sum to duration. Print both. Mismatch ⇒ cut list wrong ⇒ everything downstream wrong |
| 4 | Per shot | camera move, scale, subject action, lighting, palette, speed ramp, transition *out* |
| 5 | Transitions | Classified from frames **either side** of the boundary, never from shot content. Hard cut concentrates change in one frame pair; dissolve spreads it |
| 6 | Audio↔cuts | See below |
| 7 | OCR | String must persist ≥2 frames — separates titling from OCR noise on texture |
| 8 | Emit | EDL + cut sheet + time-cued prompt |

## Audio↔cut sync

Beat alignment alone is too coarse. An edit locks to **beats**, **onsets** (transients
that aren't beats), **downbeats**, **section changes**, or to **picture** — and it sits
ahead of or behind the grid. Each is a different recreation instruction, so each is
measured. `scripts/audio_cuts.py` reports per cut: signed beat delta, onset delta, and
the energy percentile at the cut.

- **Sign convention**: `delta = cut − reference`. Negative = cut **leads** (early; the
  common editorial choice). Positive = **lags**.
- Tolerances: beat 80ms (~2 frames @24fps), onset 60ms.
- `edit_locked_to` = beat / onset / picture at a 0.60 majority.
- **If `picture`: do not quantise the recreation to a beat grid.** Imposing a rhythm the
  reference lacks damages it.
- Energy percentile catches edits that cut on loud moments without being beat-locked —
  measured on one reference: only 31% on beat, but cuts at the 91–97th energy percentile.

## Measured failure modes

All from validation against exact ground truth on real footage.

| Trap | Evidence | Consequence |
|---|---|---|
| Default `ContentDetector` misses desaturated cuts | Weighted mean of H/S/luma; between desaturated shots H and S deltas are **0**, so a luma delta of 10.35 dilutes to ~3.45. Found the cut at **0/9** thresholds; histogram **5/5** | Histogram is primary |
| Argmax-F1 picks a fragile setting | Several settings tie at F1 1.000; tie-break turns on noise | Pick the **widest passing band**, mid-band |
| Synthetic validation lies | `content`+`luma_only` was perfect on flat-colour fixtures, **F1 0.645 with 6 false positives** on real footage (real shots have luma changes *within* a shot) | Validate on real pixels or claim no number |
| Jump cuts inside one take are undetectable | A ~200ms skip gave appearance discontinuity **0.015**, *below* the shot-interior median 0.031. Appearance 0/2, motion 1/2 | Flag for human review; never claim completeness |
| Motion's errors outrank its successes | Flow rescued a cut appearance missed (strength 0.609) but its false positives scored **1.000** | Never auto-accept motion-only cuts — surface as review flags. Holds precision **1.000**, recall 0.867, ~28ms error |
| Camera move needs normalised, centre-relative units | Priority ordering made parallax pans read as push-ins; px/frame isn't resolution/fps invariant; translation from the affine **origin** made rotation read as tilt — measuring the **frame centre** collapsed orbit's spurious translation from (0.084, −0.148) to (−0.0002, 0.0004) | 44% → **100%** on known moves |
| Sharpness is one-sided | A morph *adds* high-frequency detail: **+256% sharpness** while destroying the frame; motion coherence *improved* 24% (a structured ripple is more stable than real footage) | Low sharpness = mush. High sharpness = nothing. Never reward it |
| Percentile thresholds invert when most of a clip is bad | Generated clips hold 2–3 good seconds in 10; a mid-percentile lands **on the bad mode** and passes every frame | Otsu (valley between modes) + keep-ratio vs the clip's own best |

## Harvesting usable spans

Anchor scoring to the product's **own reference photo**. ORB matching + geometrically
consistent inliers was the only signal catching all three artefact classes: clean 376
inliers vs 118 (warp), 25 (mush), 22 (heavy morph). Geometric signals caught two and
inverted on the third.

Scoring is normalised **per clip** — it ranks within a clip and cannot judge one that is
uniformly bad. `discriminated: false` means look yourself, not that the clip is fine.

## Writing the prompt

Bracketed windows covering the full duration, one line per shot: `[0-2s] … [2-3.5s] …`.
Models read a shooting script, not prose.

- **Give every input a job**: "@Video1 — camera movement, cut rhythm, transition timing
  ONLY. @Image1..N — product identity; never from @Video1." Models conflate them otherwise.
- **Ask each reference only for what it carries**: images → identity, product, style, *not*
  lighting. Video → motion, camera, grade, grain, *not* identity. The commonest cause of a
  drifting product is asking a reference for what it cannot supply.
- **Name the move and motivate it**: "slow push in *as the product settles*" > "slow push in".
- **State the physical arrangement.** These models don't infer affordances — which way a
  subject faces, what's gripped in which hand, that a ring sits in its slot, that time runs
  forward, that a limb's speed and a struck object's speed agree. Every unstated assumption
  is a failure and the set is unbounded, which is why staging is better *authored* (in a
  still or in 3D) than described.
- **State stillness once, positively** ("stays fixed in place"). Negative phrasing trips
  some safety filters.
- **Never substitute keyframes for the reference video** — the reused information is
  dynamic. The cut sheet supplements the video reference; it never replaces it.

## Scoring a recreation

Run cut detection on the **output**, score its cut list against the reference's at 100ms
tolerance. Edit-rhythm fidelity is measurable, not opinion.

Use the *validated* detector config. Scoring with an arbitrary one nearly buried a real
result: under `content/27` a change looked inert (0.571 both ways); under the validated
`histogram/0.08` the same change scored **0.889 vs 0.571**.
