# EDL Validation — the gate, and what it actually measured

The brief's gate: *"manually spot-check the EDL against the source video. If the
cut list is wrong, everything downstream is wrong."* This is the record of that
gate, with numbers.

Two references were built because one was not enough.

| Reference | What it is | Why |
|---|---|---|
| `synthetic_ad.mp4` | 27.5s, 16 shots, flat-colour lavfi sources, exact cut list | Exact ground truth |
| `real_composite_ad.mp4` | 22.54s, 16 shots composited from **real footage** (Jellyfish = real camera + real grain, Sintel, BBB) at ad pacing | Real pixels, still exact ground truth |

Both use ad grammar: sub-second hook cuts (0.4–0.6s), a build, long payoff holds
(up to 4.4s), over a 120 BPM click so beat alignment is ground-truthable too.

---

## 1. Synthetic: F1 = 1.000 — and misleading

A 49-point sweep over detector, threshold, `luma_only`, edge weighting, histogram
and hash found **12 settings at F1 = 1.000, MAE = 0ms**.

The planted hard case was two adjacent desaturated greys (`0x808080` vs `0x828282`).
Diagnosis of why default settings missed it, from measured frame deltas:

```
d_HSV across the boundary = [0.00, 0.00, 10.35]
```

Hue and saturation carry **zero** signal on desaturated footage. `ContentDetector`
scores a weighted *mean* of its three components, so a real luma delta of 10.35 is
diluted to ≈3.45 — under every threshold swept.

| detector | finds the desaturated cut? | perfect thresholds |
|---|---|---|
| `content` (default HSV) | **never** | 0/9 |
| `content` + `edge_weight` | **never** | 0/12 |
| `adaptive` | **never** | 0/10 |
| `content` + `luma_only` | yes | 4/8 |
| `histogram` | yes | **5/5** |
| `hash` | yes | 2/5 |

Selection therefore uses **widest contiguous passing band**, not argmax-F1
(`select_robust_config`): several settings tie at 1.000 and the tie-break then
turns on noise, whereas a mid-band setting survives threshold drift on unseen
footage. That picked `histogram/0.08`.

## 2. Real footage: **0 of 49 settings reach F1 = 1.000**

This is the finding that matters, and it only appeared because the test moved to
real pixels.

| setting | P | R | F1 | MAE |
|---|---|---|---|---|
| `histogram/0.08` (synthetic-validated) | **1.000** | 0.867 | 0.929 | 28ms |
| `content/27` (library default) | 0.933 | 0.933 | 0.933 | 29ms |
| `content/8` + `luma_only` (synthetic-perfect) | 0.625 | 0.667 | **0.645** | 29ms |

**Synthetic validation was actively misleading.** `content+luma_only` was perfect
on synthetic and collapses to F1 = 0.645 with 6 false positives on real footage,
because real shots contain luma changes *within* a shot.

### What the two real misses are

Both planted same-source cuts are **~200ms jump cuts inside a single continuous
take**:

```
cut @3.500s : BBB    out at src t=6.00 -> in at 6.20  => 200ms skip in one take
cut @5.792s : Sintel out at src t=4.08 -> in at 4.30  => 217ms skip in one take
```

At 3.5s the appearance discontinuity is **0.015** — *below* the shot-interior
median of 0.031. That cut is physically near-undetectable.

## 3. Motion-discontinuity detection — complementary, not a fix

Appearance-delta detectors cannot see a cut between two moments of the same clip,
so a second detector works on a different physical signal: optical-flow
correspondence (`adpipe/decompose/motion.py`). Correspondence does not survive a
cut even when appearance does.

Flow-only: **P=0.867 R=0.867 F1=0.867** — and it **recovered the 5.792s cut that
appearance missed entirely**.

But it cannot be fused naively. Its two false positives score discontinuity
**1.000**, *higher* than the true cut it rescues (**0.609**) — tracking legitimately
fails inside shots on fast or featureless content. So strength-thresholding imports
the errors along with the win.

| policy | ncuts | P | R | F1 | flags |
|---|---|---|---|---|---|
| `appearance_only` | 13 | 1.000 | 0.867 | 0.929 | 0 |
| `union` | 16 | 0.875 | 0.933 | 0.903 | 0 |
| `strong_union` | 16 | 0.875 | 0.933 | 0.903 | 0 |
| **`review_flags`** (default) | 13 | **1.000** | 0.867 | 0.929 | 3 |

**`review_flags` is the default.** Identical automatic accuracy to
`appearance_only`, but it *surfaces* motion-only candidates — including the real
5.792s cut — for ten seconds of operator attention instead of dropping them
silently. Precision-first is the right bias when every generated shot costs money:
inventing a cut buys a wasted generation and corrupts the rhythm; flagging an
ambiguous one costs a glance.

## 4. Named failure class — state it plainly

**Jump cuts inside a continuous take are not reliably detectable.** Appearance
found 0 of 2; motion found 1 of 2. One (a 200ms skip at 3.5s) is invisible to both.
Any ad built heavily on micro jump cuts within one take will have an EDL that
under-counts shots, and needs human review of the cut list before generation.

Everything else — cuts between genuinely different shots — is detected at
**precision 1.000 and ~28ms timing error (0.7 frame at 24fps)**.

## 5. Camera-move classification: 100% after three real bug fixes

First measured on the synthetic flat-colour reference: **62.5%** — but two shots
returned `unknown` with no metrics at all, because `goodFeaturesToTrack` starves on
a flat colour field with one box. That measures the fixture, not the classifier.

Correct experiment: **known ffmpeg transforms applied to real textured footage**,
9 clips, one per move. First run **44.4%**, and the failures were my bugs:

| bug | symptom | fix |
|---|---|---|
| Fixed priority ordering (scale tested before translation) | `pan_right` → `push_in`; panning across depth genuinely changes apparent scale | score every hypothesis against its own threshold, take argmax |
| Thresholds in raw px/frame at the 384px tracking width | not resolution- or framerate-invariant | normalise to frame-fractions/sec, scale-rate/sec, deg/sec |
| `whip_pan` gated on `duration <= 1.0` | a 2s whip could never fire the branch | whip **supersedes** its own pan family rather than racing it |
| **Translation measured from the affine origin** | pure rotation about the centre shows as large bogus translation → `orbit` → `tilt_down` | measure displacement of the **frame centre** |
| Contaminated baseline (live source drifts ~-0.8px/frame) | "static" unachievable | hold one real frame still: real texture, zero intrinsic motion |

The origin-vs-centre fix was decisive — orbit's spurious translation collapsed from
`(0.084, -0.148)` to `(-0.0002, 0.0004)`:

```
     TRUTH ->  PREDICTED   scale/s    rot/s     vx/s     vy/s
    static ->     static       0.0     -0.0      0.0     -0.0
   push_in ->    push_in    0.1686    0.013   0.0849    0.048
  pull_out ->   pull_out   -0.1661    0.013  -0.0836  -0.0473
 pan_right ->  pan_right     0.001    0.008  -0.1502  -0.0001
  pan_left ->   pan_left    0.0009   -0.023   0.1505  -0.0001
  whip_pan ->   whip_pan    0.0007   -0.017  -0.2547      0.0
     orbit ->      orbit    0.0006   17.093  -0.0002   0.0004
  handheld ->   handheld    0.0007    0.042  -0.0024   0.0087
   tilt_up ->    tilt_up    0.0004    0.071    -0.0     0.0843

accuracy: 9/9 = 100.0%   (was 44.4%)
```

**Caveat:** 9 clips, one source, synthetic transforms. 100% here means the
classifier reads clean camera motion correctly — not that it is right on
hand-held product footage with motion blur and rolling shutter.

## 6. Beat alignment and OCR — validated against ground truth

On the synthetic reference (cuts deliberately on a 0.5s grid at 120 BPM):

- **Beat alignment 0.80, `music_driven_edit=True`** — correct. Detected BPM 117.45
  vs true 120 (librosa estimate, beat grid still aligned).
- **OCR recovered 13/16** burned-in shot labels, with correct positions
  (`top-left`) and correct shot indices.

On the real composite (frame-accurate durations, *not* beat-locked): alignment
0.267, `music_driven_edit=False` — also correct, and the right answer to act on:
quantising a non-beat-locked edit to a beat grid would damage its rhythm.

## 7. Frame density — the original bug, fixed

The diagnosed cause of the operator's failure was ~1 frame per 10 seconds.
Sampling is now density-scaled: **66 frames across 16 shots (4.1/shot)**, from 3 on
a 0.5s hook shot to 10 on a 4.4s payoff, so within-shot camera movement is always
visible.

## 8. Reproducing

```bash
python3 tools/make_synthetic_ref.py        # exact ground truth
python3 tools/make_real_ref.py             # real pixels, exact ground truth
python3 tools/make_camera_testset.py       # 9 known moves on real texture
PYTHONPATH=. python3 tools/run_decomp.py   # full decomposition + accuracy
python3 -m pytest tests/ -q                # 44 tests
```
