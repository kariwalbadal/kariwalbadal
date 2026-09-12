> **SUPERSEDED — see [`CORRECTED-FINDINGS.md`](CORRECTED-FINDINGS.md).**
> This document assumes one reference video per finished ad, which is not the
> operator's situation, and its diagnosis of the prior MiniMax failures was
> wrong on all three counts. The measured decomposition results it cites still
> stand; the premises and conclusions do not.

# Test Results

**Read this first.** Section 7 of the brief specifies a scored matrix of 3
references × 2 products × top-3 platforms × 3 attempts — up to 54 paid
generations. **I ran zero of them, because there was nothing to pay with.**

- Kling MCP (the only generation connector present): `membershipType: NORMAL`
  (Free), `availableRemainCredits: **0.0**`.
- No `fal`, Replicate, Higgsfield, Seedance/Volcano, MiniMax, Runway or Vertex
  credentials existed anywhere in the environment.

So **every per-video creative score in Section 7 is unmeasured**: product
fidelity, shot-to-shot continuity, motion quality, finish quality,
hand–product interaction, on-screen text legibility, and ship/no-ship. I am not
going to estimate them. They are the scores that decide the deal and they need
funded accounts.

What *was* measured is the deterministic half of the system — and it is the half
that produces the multi-shot structure, cut rhythm, speed ramps and finishing the
client is actually buying. Those results follow.

---

## 1. Reference decomposition (the Section 3 gate) — PASSED with caveats

Full detail and method in [EDL-VALIDATION.md](EDL-VALIDATION.md).

| Metric | Synthetic | **Real footage** |
|---|---|---|
| Cut detection F1 | **1.000** | **0.929** |
| Precision | 1.000 | **1.000** |
| Recall | 1.000 | 0.867 |
| Timing error (MAE) | 0ms | **28ms** (0.7 frame @24fps) |
| Beat alignment | 0.80 (`music_driven=True`) ✓ | 0.267 (`music_driven=False`) ✓ |
| OCR labels recovered | 13/16 | n/a (no burned-in text) |
| Camera-move accuracy | — | **9/9 = 100%** (known moves, real texture) |
| Frames per shot | — | 4.1 (vs 1 naive; was ~1 per 10s) |

**Named failure class:** ~200ms jump cuts *inside a continuous take* are not
reliably detectable — appearance found 0/2, motion 1/2, one invisible to both.
Ads built on micro jump cuts need the cut list reviewed by eye before generation.

## 2. Edit-rhythm fidelity — measurable, and measured

The brief is right that this "can be measured, not eyeballed." Method: run shot
detection on the **pipeline's own output** and score its cut list against the
reference EDL's cut list.

Full pipeline, `MockAdapter` (local placeholder footage, zero spend), scored with
the **validated** `histogram/0.08` detector at 100ms tolerance:

| mode | generations | cuts found | P | R | **F1** |
|---|---|---|---|---|---|
| unpacked (1 gen per shot) | 16 | 12/15 | 1.000 | 0.800 | **0.929** |
| packed, no reframe | 7 | 6/15 | 1.000 | 0.400 | 0.571 |
| **packed + slice reframing** | **7** | 12/15 | 1.000 | 0.800 | **0.889** |

Timing error of reproduced cuts: **MAE = 0ms**. Where a cut is rendered, it lands
exactly on the reference's frame.

### The finding, and a methodology correction I had to make

Packing consecutive shots into one generation halves cost (§3) but initially
**halved rhythm fidelity too** — slices of one continuous take are continuous
footage, so *there is no visible cut between them*. That is the cut rhythm the ad
is being bought for, destroyed to save money.

Giving each slice its own framing off a ladder (`REFRAME_LADDER`) makes
consecutive slices read as separate camera setups — how an editor cuts within one
take. That recovers fidelity to 0.889 while keeping 7 generations.

**My first attempt concluded reframing did not work.** It measured identical
(0.571) — because I scored the output with `content/27.0`, a setting I had never
validated and which is specifically blind to reframe-induced cuts. Re-measured
with the validated detector, reframing recovers rhythm across 4 of 5 detector
configurations. Scoring with an unvalidated detector nearly buried the result.

| detector | unpacked | packed | packed+reframe |
|---|---|---|---|
| `histogram/0.08` (validated) | 0.929 | 0.571 | **0.889** |
| `histogram/0.03` | 0.848 | 0.583 | 0.828 |
| `content/12` | 0.966 | 0.710 | 0.857 |
| `content/8` luma | **1.000** | 0.571 | 0.889 |
| `content/27` (unvalidated) | 0.889 | 0.571 | 0.571 |

**Caveat, and it is a real one:** this is measured on placeholder footage that is
flatter and less textured than real generations. Reframing should separate *more*
strongly on real footage, but that is an expectation, not a measurement.

## 3. Cost per finished ad — arithmetic on verified prices

Computed by `adpipe/cost.py` against the validated 16-shot / 22.54s EDL
(median shot 1.46s). Prices per `capability-matrix.md`.

| Platform | floor | billed (1/shot) | billed (packed) | $/attempt | **$/accept @3** |
|---|---|---|---|---|---|
| Kling v3.0 720p (Ultra tier) | 3s | 48.0s (2.13×) | 28.0s (1.24×) | $1.65 | **$4.94** |
| Seedance 2.0 r2v 720p | 4s | 64.0s (2.84×) | 33.0s (1.46×) | $5.99 | **$17.96** |
| MiniMax H3 | 5s | 80.0s (3.55×) | 38.0s (1.69×) | $3.80 | **$11.40** |
| Runway Aleph | none | 25.6s (1.14×) | 23.0s (1.02×) | $3.44 | $10.33 |

**No platform breaches the brief's $50/accepted-video kill threshold.** What
$500/month actually buys, at 3 attempts per accept:

- Kling packed: **~100 finished 30s ads**
- MiniMax H3 packed: **~43**
- Runway Aleph: ~48
- Seedance 2.0 r2v packed: **~27** (30 ads = $539, marginally over)

`attempts_per_accept = 3` is the brief's cap, **not a measured acceptance rate.**
If real acceptance needs 6 attempts on hard shot grammars, halve every number.
That single unknown dominates the economics and is exactly what the unfunded
tests would have established.

## 4. Wall-clock and throughput — arithmetic, not measurement

Measured locally (16-core container, no network):

| Stage | Time |
|---|---|
| Decomposition (22.5s ref, incl. flow + OCR + audio) | **~110s** |
| Assembly (7 gens → conform → grade+glow → concat → mux) | **~35s** |
| Full pipeline, mock generation | **~110s** |
| Test suite (44 tests) | 0.3s |

Deterministic cost is ~2.5 min/ad and parallelises freely. **Generation latency
and queue times are unmeasured** — no funded account. Published figures suggest
1–4 min per clip, which at 7 generations/ad is 7–30 min of wall clock, mostly
parallelisable across shots.

**Is 20–30 creatives in 3 days achievable?** On arithmetic, yes: 30 ads × 7
generations = 210 calls; at even 4 concurrent slots and 3 min/call that is ~2.6
hours of compute, plus ~1.3 hours of deterministic processing. The binding
constraints are **not** compute:

1. **Human review.** At 3 attempts/accept, 30 accepted ads means reviewing ~90
   candidates. At 2 min each (watch 30s, judge, log) that is **~3 hours of
   operator attention** — the real bottleneck, and the reason the review queue
   exists.
2. **Acceptance rate**, unmeasured (above).
3. **Undocumented concurrency/rate limits**, unmeasured.

So: 20–30 in 3 days is *plausible and not compute-bound*, but I cannot certify it
without one funded platform and a measured acceptance rate. The honest version is
that the pipeline can *submit and assemble* far more than 30 ads in 3 days; whether
30 come out *acceptable* is the open question.

## 5. What the delivered output proves, and what it does not

`work/out/TONIGHT/final/ad.mp4` — 22.54s, **16 shots from 7 generations**,
1280×720 @24fps, 16 clips colour-normalised (gain span 0.740–1.257, clamp active),
glow pass applied, reference audio bed muxed, rhythm F1 = 0.889 at MAE = 0ms.

**Proves:** EDL-conformant multi-shot assembly, frame-exact cut rhythm from a real
reference, speed ramps, cross-shot colour normalisation, highlight bloom, audio
bed, budget ledger, review queue — end to end, one command.

**Does not prove anything about creative quality.** The shots are procedurally
generated placeholders. Every claim about product fidelity, continuity of a real
SKU, hand–product interaction or ship-worthiness remains **untested**.

## 6. Unverified items that block a client commitment

1. **Rights.** Commercial-use terms, and whether inputs/outputs train vendor
   models, verified for *no* platform. The operator handles confidential client
   work. Settle before uploading a single client asset.
2. **Acceptance rate** on real generations, per shot grammar.
3. **Concurrency, rate limits, queue times** under real load.
4. **Higgsfield browser-automation durability** — no account to test.
5. Whether Seedance **Extend** builds 30s without drift, and whether *any* native
   multi-shot model respects a **specified** cut rhythm rather than inventing one.
   This last is the pivotal Architecture A vs B question and it is **unresolved**.
