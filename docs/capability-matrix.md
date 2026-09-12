# Capability Matrix — Video-to-Video Ad Creative

All checks performed **2026-09-12**. Two evidence classes, kept separate:

- **[LIVE]** — read off a live API contract in this session. Strongest evidence.
- **[DOC]** — vendor or third-party documentation, URL + date given. A claim, not a test.

Nothing here is from model training data. Where I could not verify, the row says
so rather than guessing.

> **Funding blocker.** The only generation connector available in this session was
> the Kling MCP server, on a `NORMAL` (Free) account with `availableRemainCredits:
> 0.0`. No `fal`, Replicate, Higgsfield, Seedance/Volcano, MiniMax, Runway or
> Vertex credentials existed in the environment. **Zero paid generations were run.**
> Every per-video quality claim below is therefore unverified by me.

---

## 1. Kling — [LIVE], highest-confidence rows in this document

Source: `mcp__Kling__who_am_i` response, MCP server v1.3.2, userId 47806679,
2026-09-12. 108KB of argument specs parsed directly.

### The resolution ceiling

| Tool | Models | `resolution` allowedValues |
|---|---|---|
| `image_to_video` | v2_5, v2_6, o1, v3_0_omni, v3_0, v3_0_turbo | **`['720p']` only** |
| `text_to_video` | same set | **`['720p']` only** |
| `motion_control` | v2_6, v3_0 | **`['720p','1080p']`** |

**Confirmed:** on this account tier every generation model caps at 720p, and
`motion_control` is the *only* 1080p route. Model descriptions state it plainly —
v3_0: *"Quality tier (non-member: 720p = std)"*; v3_0_omni: *"720p=std,
1080p=pro. 4k for members."* A client needing 1080p+ must hold a paid membership.

### Per-model contract

| Model | Tool | Duration | Multi-shot | Elements | Audio | Refs |
|---|---|---|---|---|---|---|
| `kling-video-v3_0_omni` | i2v | **3–15s** | `prefer_multi_shots` (def false) | yes | `enable_audio` | `image_1..image_7` |
| `kling-video-v3_0` | i2v | **3–15s** | `prefer_multi_shots` (def **true**) | yes | `enable_audio` | `first_image` + **`tail_image`** |
| `kling-video-v3_0_turbo` | i2v | 3–15s | no | no | no | `first_image` |
| `kling-video-o1` | i2v | 3–10s | no | yes | no | `image_1..image_7` |
| `kling-video-v2_6` | i2v | **5 or 10s only** | no | no | 1080p-only | `first_image` + `tail_image` |
| `kling-video-v3_0` | `motion_control` | n/a | no | yes | `keepOriginalSound` | subject image + motion video |

### Findings the brief did not have

1. **`tail_image` (last-frame chaining) exists on the multi-shot model `v3_0`.**
   This is the continuity mechanism for Architecture A and the route to >15s.
2. **Reference images are addressed in-prompt as `图片N` (Chinese), not `@ImageN`.**
   A prompt written with `@Image1` against Kling addresses nothing at all.
3. **On `v2_6`, `tail_image` REQUIRES `resolution=1080p`**, and `enable_audio` is
   *"Only at resolution=1080p; not supported with a tail image."* So on a free/720p
   account, last-frame chaining and audio are **both unavailable**, and they are
   mutually exclusive even when paid. This materially constrains Architecture A.
4. **`motion_control` subject input expects "a clear person or animal."** As the
   brief suspected, this rules it out for bare product swaps — and it is the only
   1080p path. That is a direct conflict for product-led ads.
5. Elements bind as `[{"id","bindName"}]` with `<<<id>>>` in the prompt. Video
   subjects (`resource.video`) work **only** on `image_to_video` with `v3_0` or
   `v3_0_omni` — not on `o1`, not on `image_to_image`, not on t2v.

### Video-subject (Element) constraints — [LIVE], from `element_create`

Duration 3–60s; fps > 23.8; short edge ≥ 700px; ≤ 3840×2160; SAR empty/1:1/0:1;
**HDR not supported**. Matches the brief's figures exactly.

### Pricing — [DOC]

Standard 660cr/$6.99 · Pro 3000/$25.99 · Premier 8000/$64.99 · Ultra 26000/$127.99;
4K = 30 cr/s. ([kling.ai credit guide](https://kling.ai/blog/kling-video-3-0-credit-cost-guide), 2026-09-12)
Third-party reports 6 cr/s (720p no audio) → 12 cr/s (1080p + audio)
([eesel](https://www.eesel.ai/blog/kling-ai-pricing)). Ultra ≈ **$0.0049/credit**.

**No true video-to-video tool exists in the Kling MCP surface.** The two v2v-ish
routes are `motion_control` (video as motion source) and video Elements bound into
`image_to_video`.

---

## 2. Seedance 2.0 — [DOC], strongest non-live contract

Source: [fal reference-to-video](https://fal.ai/models/bytedance/seedance-2.0/reference-to-video), 2026-09-12.

| Property | Value |
|---|---|
| Reference images | up to **9**, ≤30MB each |
| Reference videos | up to **3**, combined **2–15s**, <50MB total |
| Reference audio | up to **3**, ≤15s combined |
| **Total files** | **≤ 12** across all modalities |
| Prompt syntax | **`@Image1` / `@Video1` / `@Audio1`** |
| Duration | `auto` or **4–15s** |
| Resolution | 480p / 720p / **1080p** |
| Aspect | auto, 21:9, 16:9, 4:3, 1:1, 3:4, 9:16 |
| **Price (720p, with video input)** | **$0.1814/sec** — a 0.6× discount |
| Price (720p, no video input) | $0.3024/sec |

The video-input discount is unusual and favourable: the v2v path is **40% cheaper
per second** than t2v. Genuine v2v (structural reference + separate product
reference) with the widest documented reference envelope of any platform here.

**Unverified:** whether Extend actually builds 30s without drift, and whether
reference-driven output respects a *specified* cut rhythm. Both need paid runs.

---

## 3. MiniMax H3 (Hailuo 3.0) — [DOC]

Sources: [morphic specs](https://morphic.com/resources/models/minimax-h3) ·
[atlascloud r2v rules](https://www.atlascloud.ai/blog/tips/minimax-h3-reference-to-video), 2026-09-12.

Same reference envelope as Seedance: **9 images + 3 videos + 3 audio, 12 total**;
reference video 2–15s each / 15s total. Output **5–15s**, 24fps, 1440px short edge,
native stereo. References passed in a `refers` array.

**The operator's earlier H3 failure was almost certainly the prompt pipeline, not
the model.** H3 does accept video references. The documented division of labour is
exactly the architecture needed:

- **Image refs lock** identity, garment, product, style — **but not lighting**
- **Video refs lock** motion, camera, grade, grain — **but not identity**

Verified failure modes to guard client-side (all encoded in `adpipe/adapters/`):

- Audio-only reference sets are rejected: *"reference-to-video requires at least
  one reference image or video"*
- **First-frame image + references silently drops one input, at full price, with
  no error**
- **Validation is late**: submit returns HTTP 200, real validation lands 2–3 min
  later at generation time
- Over-limit requests may complete and charge anyway (16.1s of audio against a
  15s ceiling went through)

The 5s duration floor is the worst fit here for fast-cut ads — see §7.

---

## 4. Higgsfield — the operator's stated preference. **v2v is not API-exposed.**

The product feature is real: Marketing Studio's *UGC Videos* accepts a reference ad
and *"reproduces its structure and format with your product in place of the
original"*; Ad Reference *"recreates its concept, scene composition, pacing, and
hook using your own product"* — **up to 15 seconds per generation**
([higgsfield.ai](https://higgsfield.ai/marketing-studio-intro), 2026-09-12).

**But the API does not expose it.** The authoritative machine-readable doc index
([docs.higgsfield.ai/docs/llms.txt](https://docs.higgsfield.ai/docs/llms.txt),
fetched 2026-09-12) lists 19 pages — Quickstart, Auth, Polling, Webhooks, File
uploads, Rate limits, API reference, etc. — and **no video-to-video,
video-reference, ad-reference, product-swap, or Marketing Studio endpoint.**
Documented modes are Text-to-Video, Image-to-Video, and Soul.

Auth is `Authorization: Key ${HF_API_KEY_ID}:${HF_API_KEY_SECRET}`. API access is
gated to $15–$99/mo tiers, with reported undocumented rate limits causing silent
failures, credits expiring after 90 days, and no batch support
([wireflow](https://www.wireflow.ai/blog/best-higgsfield-api-alternatives-in-2026), dated 2026-04-26).

**Consequence:** automating the exact feature the operator wants means driving the
web UI. See the ToS/durability warning at the top of `RECOMMENDATION.md`. I could
not assess the community MCP/CLI/Playwright packs — no account, and no credentials
to test against.

---

## 5. Runway Aleph — [DOC]

Gen-4 Aleph **15 credits/sec** at $0.01/credit = **$0.15/sec**; Aleph 2.0 at 28
cr/sec = $0.28/sec ([creatify](https://creatify.ai/blog/runway-pricing-(2026)-plans-credits-and-what-you-ll-actually-pay), 2026-09-12).
Official API, genuine v2v — but it is a **restyling/editing** model: it transforms a
source video rather than substituting a new product into a reference's *structure*.
No per-second duration floor, so packing barely helps it (§7).

---

## 6. TopView — [DOC]

A real REST API exists: URL-to-Video, Video Avatar, Product Avatar, Anyshoot;
async submit + polling + signed output URLs ([topview.ai/openapi](https://www.topview.ai/openapi), 2026-09-12).
It also markets itself as *"One API for Every AI Video & Image Model"*, so it may be
an aggregator route to Kling/Seedance. **No structural video-to-video endpoint
found.** The brief's Sept-2026 observation that `mcp.topview.ai` exposed no video
tools is consistent with what I can see.

Not investigated for lack of credentials: Veo/Vertex, Pika, Luma, Wan, LTX. I am
not padding the matrix with image-to-video tools wearing a v2v label.

---

## 7. The duration-floor economics — the finding that changes the design

Measured on the validated 16-shot reference EDL (22.54s, **median shot 1.46s**),
computed by `adpipe/cost.py`:

| Platform | floor | 1 gen/shot | packed | $/accept @3 attempts |
|---|---|---|---|---|
| Kling v3.0 | 3s | 48.0s billed (**2.13× waste**) | 28.0s (1.24×) | $8.47 → **$4.94** |
| Seedance 2.0 r2v | 4s | 64.0s (**2.84×**) | 33.0s (1.46×) | $34.83 → **$17.96** |
| MiniMax H3 | 5s | 80.0s (**3.55×**) | 38.0s (1.69×) | $24.00 → **$11.40** |
| Runway Aleph | none | 25.6s (1.14×) | 23.0s (1.02×) | $11.53 → $10.33 |

**Ad grammar is economically hostile to per-second billing with a 3–5s floor.**
Generating one clip per EDL shot pays for 3–5s to use 0.5s. Packing consecutive
compatible shots into one longer generation and slicing it per the EDL cuts
generations 16 → 7 and roughly halves cost.

Nothing here breaches the brief's $50/accepted-video kill threshold. **Cost is not
the binding constraint** — quality and control are, and in this session, funding.

---

## 8. Rights and throughput — NOT VERIFIED

I did not verify commercial-use terms, or whether inputs/outputs train vendor
models, for any platform. The operator handles confidential client work, so this
must be settled from each vendor's current ToS/DPA before any client asset is
uploaded. Treat it as an open blocker, not a detail.

Concurrency limits, rate limits and real queue times were likewise untestable
without funded accounts. The 3-day throughput projection in `test-results.md` is
therefore arithmetic, not measurement.
