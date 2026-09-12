# Prompt Patterns for Reference-Driven Ad Recreation

Five documented patterns, each with its source and the date checked
(**2026-09-12**), followed by the shared structural rules they imply and the spec
for the prompt-synthesis function that implements them.

Generic prompting advice is excluded. Every pattern below comes from vendor or
platform guidance for *reference-driven* generation specifically.

---

## Pattern 1 — Explicit job assignment per input (Seedance 2.5)

Source: [WeShop AI — Recreate Viral Product Ads with Seedance 2.5](https://www.weshop.ai/solutions/models/seedance-2-5-recreate-a-viral-product-video-with-the-same-camera-moves-and-editing-rhythm)

Verbatim published prompt:

> "Reference @Video1 for its camera movement and editing rhythm, and create an
> advertisement for the headphones in @Image1. Adapt the environments to fit the
> product, with a modern, minimal, technology-focused visual direction. Keep the
> timing of the transitions between product close-ups and wide environmental shots
> aligned with the reference video. Follow a similar movement speed and transition
> style. Replace the original featured subject with the silver over-ear headphones.
> Emphasize the metallic finish, ear cushion details, product silhouette, and
> premium wearing experience. Use settings such as a modern city, a minimal
> interior lifestyle space, and an open outdoor environment where appropriate,
> while keeping the overall visual direction clean, restrained, and
> technology-focused. Keep the headphone appearance consistent across shots.
> Preserve its silver color, overall structure, silhouette, and key product
> details."

The move: each input is *assigned a job*. `@Video1` supplies motion and rhythm;
`@Image1` supplies identity. Then an explicit identity-preservation clause.

**Its warning, verbatim:** *"Do not crop it into individual screenshots or extract
isolated keyframes for this workflow. The model needs the video itself because the
information you want to reuse is dynamic."*

## Pattern 2 — Reference division of labour (MiniMax H3)

Source: [AtlasCloud — MiniMax H3 Reference to Video](https://www.atlascloud.ai/blog/tips/minimax-h3-reference-to-video)

- **Image references** lock *"identity, garment, product, style"* — **but not lighting**
- **Video references** lock *"motion, camera, grade, grain"* — **but not identity**
- **Audio references** are *"a steer on the mix, not a music slot"*

Corollaries the guide states: shoot product/character references *"neutral light,
plain background, no scene, no mood"*, because the model learns lighting alongside
identity. And **do not request a lighting change and a reference-grade match in the
same prompt** — you get neither.

Naming convention: *"Image 1 is the character, Image 2 is the product."*

## Pattern 3 — Name the camera move, and motivate it (Seedance 2.5)

Source: WeShop (above) + [Kapwing Seedance 2.5 prompt guide](https://www.kapwing.com/resources/how-to-prompt-seedance-2-5-a-guide-for-ai-video-creators/)

Directly-read tokens: *tracking shot, slow push in, wide establishing shot,
handheld, aerial, dolly forward, orbit, rack focus, low angle, top down.*

The refinement that matters: *camera prompts work better when the movement is
caused by something happening in the scene, rather than being an arbitrary
instruction.* "Slow push in **as the product settles into place**" beats "slow push
in."

## Pattern 4 — Rhythm is the product (Seedance 2.5)

Source: WeShop (above)

> "What usually makes a viral product video work is the rhythm behind the edit:
> when the camera pushes forward, when the product gets a close-up, when the scene
> opens into a wide shot, and how each transition carries momentum into the next."

Implication: pacing must be *stated*, not hoped for. A native multi-shot model with
no timing instruction invents its own rhythm — and then cannot reproduce a
reference by definition.

## Pattern 5 — Element/subject binding by identifier (Kling) — [LIVE]

Source: Kling MCP `who_am_i`, 2026-09-12.

> "Optional reusable subjects (Elements). JSON array string of `{id, bindName}` …
> Write `<<<id>>>` in the prompt where each subject should appear (an id may be
> referenced multiple times)."

Plain reference images on `v3_0_omni` / `o1` are addressed as **`图片N`** (Chinese).
`@Image1` addresses nothing on Kling — the syntax is not portable, which is why
addressing is a per-platform concern in the code.

---

## Shared structural rules

Every pattern above reduces to the same five moves:

1. **Assign each input a job** before describing anything.
2. **Ask each reference only for what it can supply** (identity from images, motion
   from video). Never both from one.
3. **Name the craft explicitly** — move, scale, lighting — using the platform's
   read tokens, and motivate the move from the scene.
4. **State timing numerically.** Durations and transition types, per shot.
5. **Repeat the identity lock.** Verbosely, every shot. This is what holds a SKU
   across independently generated clips.

### Conflict to resolve consciously

Pattern 1 says *never* substitute keyframes for the reference video. This pipeline
decomposes the reference into an EDL — which is **not** a violation, because the EDL
is used for *programmatic control* (timing, assembly, QC), while the **whole
reference video is still passed** as `@Video1`. The EDL supplements the video
reference; it must not replace it. Feeding extracted keyframes *instead of* the
clip is the failure the guide warns about, and is also the operator's original bug
in a different costume.

---

## Prompt-synthesis function spec

Implemented in `adpipe/synth/prompts.py`, version **`1.2.0`**, exported as
`PROMPT_SYNTH_VERSION`. Pure functions, no I/O, unit-tested in isolation
(`tests/test_prompts.py`) so a prompt regression is caught rather than silently
degrading every later ad.

### Signature

```python
synthesize_shot_prompt(shot, product, style, platform, element_id=None) -> ShotPrompt
synthesize_multishot_prompt(edl, product, style, platform) -> ShotPrompt   # Architecture B
lint_prompt(prompt, n_images, n_videos, n_audio) -> list[str]
```

### Emission order (Architecture A, per shot)

| # | Section | Source | Rule |
|---|---|---|---|
| 1 | Job assignment | — | P1, P2 |
| 2 | Shot craft | `camera_move`, `shot_scale`, `speed_ramp`, `lighting` | P3 |
| 3 | Role intent | `role` (hook/build/mid/payoff) | P4 |
| 4 | Product kinetics | `subject_action` + default physicality clause | brief §2 |
| 5 | Identity lock | `ProductSpec` | P1, P5 |
| 6 | Environment + direction | `StyleSpec` | P1 |
| 7 | Finishing | `style.glow` | brief §2 |

Platform addressing is resolved by `_ref_token()`: `@Image1`/`@Video1` for
Seedance and MiniMax; `<<<id>>>` or `图片N` for Kling; prose for Runway.

### Two deliberate suppressions

- **`slow_motion_or_locked` never becomes an instruction.** The motion detector
  cannot distinguish a genuine slow-motion shot from a locked-off one, so emitting
  "slow motion" on a static shot instructs a treatment the reference never had.
  Only real velocity *changes* (`ramp_up`/`ramp_down`) are emitted. Regression-tested.
- **Glow is phrased as a highlight treatment, not a relight**, whenever a video
  grade reference is present — per Pattern 2's lighting/grade conflict.

### Lint rules (enforced pre-submission)

`audio_alone` · `too_many_refs` (>9 images, >3 videos, >12 total) ·
`lighting_and_grade_conflict` · empty prompt · non-positive duration.

These are also enforced a second time in `adpipe/adapters/base.py` against a JSON
Schema, because the documented vendor behaviour is to *accept and charge for*
some out-of-contract requests rather than reject them.

### Versioning

Bump `PROMPT_SYNTH_VERSION` on any wording change. Every generation is logged with
the version that produced it (`run.py` → `prompts` in the manifest), so an
acceptance-rate change can be attributed to a prompt change rather than guessed at.
