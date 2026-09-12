# Corrected Findings

**This supersedes `RECOMMENDATION.md` and `docs/test-results.md`.** Both were
built on premises the operator has since corrected. Read this first.

---

## 1. What I got wrong

### Wrong premise: one reference video per output

The decomposition pipeline in `adpipe/decompose/` assumes a reference ad per
finished ad. The operator will not have that for most jobs. A reference-driven
pipeline is the wrong shape for the business; what is needed is a reusable
**shot-grammar library** that is product-agnostic.

The decomposition code is still valid and still measured (see
`docs/EDL-VALIDATION.md`) — it is useful for mining a reference ad *into* that
library. It is not the spine of the system.

### Wrong diagnosis of the operator's prior failures

I asserted three causes. All three were inference presented as finding, and all
three were false:

| I claimed | Actually |
|---|---|
| Prompts weren't timecoded | Timecoded beats are used on every model, always |
| Wrong endpoint chosen | The reference API matching the available input is used deliberately |
| The model was left to draw the product | Full reference sets are supplied, as many as each call accepts |

Product *identity* is not the operator's main problem, and product LoRAs,
ComfyUI and RunPod have already been tried.

---

## 2. The actual problem: unbounded affordance specification

The operator's own example. Reference images supplied: a ring box open, the
same box closed, and the ring. Instruction: the ring is in the box, the box is
open, someone picks the ring out. Result: the ring laid flat, not seated in its
slot.

> "Then I would also have to say the box is facing me. The box is not opened
> upside down. Gravity is downwards, the camera is not upside down, record it
> front to last, not last to front. There is no end."

That is the finding. **The set of physical assumptions that would have to be
stated is unbounded**, and every clause added to cover one of them dilutes the
instruction that mattered. Rings sit in slots, laptops open toward the viewer,
water falls down — these are affordances, not descriptions, and a diffusion
model has no physics to consult.

Two of the operator's other observations share this single cause:

- **1–4 hours in CapCut per ad** (trim, reorder, speed, mask, colour, audio) is
  time spent *salvaging* output that was wrong by construction. At 30 ads that
  is 30–120 hours, which is why the current workflow cannot scale.
- **A bad second "follows the problematic part or leads to it."** The model is
  resolving an impossible arrangement across time, so the error is not local and
  trimming around it does not recover the neighbouring seconds.

In a 3D scene none of this arises: the ring is in the slot because it was placed
there. Nothing is inferred, so nothing can be inferred wrongly.

---

## 3. How the target tier actually produces these ads

The quality bar named was Zara / Puma / Adidas / Van Heusen, and the operator's
client states Puma's ads are AI-generated. Both are true, and the mechanism is
not prompt-driven video generation. Checked 2026-09-12:

- Puma's H-Street 2026 campaign was **"executed with 3D render and CGI
  technique."**
- adidas built **SRP (Sneaker Rendering Pipeline)**: an Unreal Engine 5
  environment with spawn targets where, once set up, it **"can be re-used
  repeatedly for different products automatically by dropping 3D models in a
  folder."**
- The 2026 industry pattern: **"traditional 3D geometry anchors generative AI
  diffusion models — AI is a tool inside the pipeline, not a replacement for the
  studio."**

AI does environments and backplates, variant and localisation generation,
rotoscoping, grading, conform and upscale (compressing post from ~18 days to
~9–10). **The hero product shot is rendered from 3D geometry, never prompted.**

The adidas drop-in-a-folder detail is the throughput answer as much as the
quality answer: one scene setup services many products, which matches "ten
products for one client, or ten different products".

---

## 4. What was actually built and measured here

`adpipe/harvest.py` — intra-clip harvesting. The operator reports 2–3 usable
seconds per 10s generation, and finding them is manual. That part is
automatable, but **not the obvious way.**

### Negative result: cheap geometric metrics fail, and can invert

Three artefact classes were injected into known time ranges of real footage.
On the heaviest non-rigid morph, every geometric signal pointed the wrong way:

| signal | warp | mush | heavy morph |
|---|---|---|---|
| sharpness (Laplacian var) | flags | flags | **+256% — misleads** |
| affine residual | flat | flat | **−24% — misleads** |
| inlier fraction | flat | flat | flat |

A morph *adds* high-frequency detail, so it reads as sharp; and a structured
ripple is temporally *more* stable than real moving footage, so
motion-coherence improves. The first scorer ranked the worst second of the clip
as its best — **23.5% of selected footage fell in known-bad ranges.**

Sharpness is therefore now a **one-sided** signal: it may count against a frame,
never for it. Regression-tested in `tests/test_harvest.py`.

### Positive result: anchor the score to the product's own reference photo

ORB feature matching against the product reference, counting geometrically
consistent inliers:

| range | inliers | vs clean |
|---|---|---|
| clean baseline | 376.3 | — |
| warp | 117.8 | −68.7% |
| mush | 25.2 | −93.3% |
| heavy morph | 22.2 | **−94.1%** |

All three classes detected, including the one that fooled everything else.
Transitions land on the injected boundaries.

### Threshold selection: percentile is wrong here

A mid-percentile threshold breaks on exactly the operator's case. When 70–80%
of a clip is unusable, a `percentile=55` threshold lands **on the bad mode** and
every frame passes. Replaced with the stricter of **Otsu** (the valley between
the bad and good modes, so it holds at any good/bad ratio) and a **keep_ratio**
relative to the clip's own best quality.

Result on the real test clip: **all four clean ranges recovered, 5.88s of 10s
(59% yield, 84% of the available clean footage), 0.0% contamination** — up from
2 of 4 ranges under the percentile rule.

### Known limits of the harvester

- Scores are normalised **per clip**, so it ranks within a clip and cannot judge
  a clip that is uniformly bad. `discriminated: False` flags a flat curve for a
  human look rather than returning a silent answer.
- It replaces the **trim** portion of the CapCut pass only. Reorder, mask,
  colour and audio are untouched.
- It cannot make a wrongly-staged shot usable. It shortens salvage; it does not
  remove the need for it.

---

## 5. Recommendation

Full CGI animation is a larger studio than exists today, so the bridge is:

1. **Ask each client what 3D they already hold** — jewellery is *designed* in
   CAD (Rhino, Matrix); apparel brands increasingly hold CLO3D or Browzwear
   digital twins. Highest leverage available, and costs an email.
2. **Where there is no CAD, build geometry from the reference photos already
   being shot** — photogrammetry, NeRF or generative image-to-3D. Note the
   published ceiling: current guidance rates AI-generated 3D as good for
   prototyping, concept visualisation and product viewers, *not* hero-ad
   fidelity.
3. **Author staging in 3D and render stills.** Blender geometry nodes plus
   Python make scene templates reusable and parameterised.
4. **Use AI for environments and finish, not staging** — the division the large
   pipelines use, and where AI has no physical constraint to violate.
5. **Animate only between two correct frames** (first/last frame, 3–5s), so the
   model interpolates motion between states that were authored.
6. **Automate the trim** with `adpipe/harvest.py`.

### Honest costs

- This is a different discipline (3D staging and lookdev); buying it in may beat
  learning it against a deadline.
- Jewellery and apparel are the hard end — refractive stones and cloth drape are
  what CGI is worst at.
- The first ad is slower than the current 1–4 hours; scene templates only pay
  back across products.
- **This does not deliver 30 ads in 3 days from a standing start at
  Zara/Puma quality.** It makes ad eleven cheap.
