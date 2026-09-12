> **SUPERSEDED — see [`docs/CORRECTED-FINDINGS.md`](docs/CORRECTED-FINDINGS.md).**
> This document assumes one reference video per finished ad, which is not the
> operator's situation, and its diagnosis of the prior MiniMax failures was
> wrong on all three counts. The measured decomposition results it cites still
> stand; the premises and conclusions do not.

# Recommendation

## Read these three things before anything else

**1. Nothing was generated. Nothing creative was tested.**
The only generation connector available was the Kling MCP server, on a Free
account with **0 credits**. No `fal`, Replicate, Higgsfield, Seedance, MiniMax,
Runway or Vertex credentials existed in the environment. **Zero paid generations
ran.** Product fidelity, shot-to-shot continuity, hand–product interaction,
on-screen text legibility and ship/no-ship are therefore **unmeasured**. What is
measured is reference decomposition and multi-shot assembly, both to numbers.

**2. The platform you wanted most cannot be automated through an API.**
Higgsfield Marketing Studio's UGC/Ad-Reference feature does exactly what the brief
describes — drop in a reference ad, get its structure with your product — but its
**authoritative API doc index lists no video-to-video, ad-reference, product-swap
or Marketing Studio endpoint** (`docs.higgsfield.ai/docs/llms.txt`, 2026-09-12).
Documented modes are Text-to-Video, Image-to-Video and Soul.

So automating that feature means **driving the web UI with browser automation**,
and that carries risks I will not bury:

- It is **against the grain of any normal ToS**. Higgsfield's terms cover
  API/MCP/CLI access *"when offered"* — the offering does not include this
  feature. Automating the UI instead is not a supported path, and account
  termination is a live risk to a client deliverable.
- It is **structurally fragile**. Any front-end change breaks it, with no
  deprecation notice and no version guarantee, mid-engagement.
- The community MCP/CLI/Playwright packs named in the brief are
  **unassessed** — I had no account and no credentials to test them against.
- **15 seconds per generation** is the documented ceiling, so it cannot produce
  20–35s natively regardless. You still need assembly.

My advice: do not build the client's delivery path on it. Use it manually as a
creative benchmark if it produces the best shots.

**3. One acceptance rate decides the whole economics, and it is unmeasured.**
Every cost and throughput figure below assumes the brief's cap of 3 attempts per
accepted ad. If hard shot grammars really need 6, halve every throughput number
and double every cost. Establishing that number is the first thing to do with a
funded account; it is roughly half a day.

---

## The one architectural finding that shapes all three options

Ad grammar is **economically hostile** to how these platforms bill.

The validated reference has a **median shot of 1.46s**. Generation platforms bill
per second with a floor of 3s (Kling), 4s (Seedance) or 5s (MiniMax H3).
Generating one clip per shot pays for 3–5s to use 0.5s — **2.1× to 3.6× waste**.

Packing consecutive compatible shots into one longer generation and slicing it per
the EDL cuts 16 generations to **7** and roughly halves cost. But packing alone
**destroyed the cut rhythm** — slices of one continuous take are continuous
footage, so the cut between them is invisible (measured: rhythm F1 fell 0.929 →
0.571). Giving each slice its own framing restores it to **0.889 at 7
generations**. That hybrid is what the recommended pipeline does by default.

Corollary worth saying plainly: **for sub-second cuts, generate fewer and longer,
then cut in post.** Asking a model for a 0.5s clip is paying 6× for 0.5s of
footage and getting worse continuity than slicing one take.

---

## Option 1 — Kling v3.0, shot-packed, programmatic assembly  ← **default pick**

**What it costs.** ~$1.65 per attempt packed; **≈$4.94 per accepted 30s ad** at 3
attempts. $500/mo buys **~100 finished ads** — the only option with real headroom.
Needs a membership: Standard $6.99 to start, Ultra $127.99/mo (26,000cr) for
throughput.

**What it produces.** 3–15s clips, `prefer_multi_shots` for native shot splitting,
`tail_image` last-frame chaining on the multi-shot model, Elements for reusable
product identity, native audio. Assembled to any length at exact reference rhythm.

**What it cannot do.**
- **720p only.** Every `image_to_video`/`text_to_video` model lists
  `resolution: ['720p']`. 1080p exists **only** on `motion_control` — which
  expects "a clear person or animal" subject and so is unusable for product
  swaps. **If the client demands 1080p+ for a product ad, this option fails**, and
  that is a live risk.
- On `v2_6`, `tail_image` requires 1080p and `enable_audio` is 1080p-only and
  mutually exclusive with a tail image — so chaining and audio fight each other.
- No true video-to-video. Structure comes from *your* EDL, not from Kling reading
  the reference. Rhythm fidelity is bought by assembly, not by the model.

**How fragile.** Least fragile option. Official MCP server, contract verified live,
adapter guard-rails unit-tested. Caveat: reached over **MCP, an agent-side
transport** — a headless cron job cannot call it without an HTTP key.

**Scaling to 30.** 7 gens × 30 = 210 calls. Comfortable on Ultra with credits to
spare.

**Why it is the default:** the only option verified against a live contract, the
cheapest by 2–4×, the most headroom, and the one whose failure modes I can
enumerate precisely rather than guess at.

## Option 2 — Seedance 2.0 reference-to-video (via `fal`)

**What it costs.** **$0.1814/sec at 720p with video input** — 40% cheaper than its
own t2v path. ~$5.99/attempt packed; **≈$17.96 per accepted ad**. $500/mo buys
**~27 ads** — 30 would be ~$539, marginally over budget.

**What it produces.** The only documented genuine v2v contract here: **9 images + 3
videos + 3 audio, 12 files total**, addressed `@Image1`/`@Video1`, 4–15s, up to
**1080p**, native audio included. This is the platform that can actually take
"reference this ad's rhythm, use my product" as a single instruction — the WeShop
pattern is published for exactly this.

**What it cannot do.** 15s max per generation, so 20–35s still needs chaining or
assembly. Whether **Extend** builds 30s without drift is **unverified**. Whether it
respects a *specified* cut rhythm rather than inventing its own is **unverified** —
and that is the pivotal question for Architecture B.

**How fragile.** Moderate. Official API via a mature aggregator, webhooks, SDKs.
Main risk is model-version churn.

**Scaling to 30.** Feasible but at the top of budget. Best creative bet, worst unit
economics.

## Option 3 — MiniMax H3 reference-to-video

**What it costs.** ~$3.80/attempt packed; **≈$11.40 per accepted ad**; ~43 ads at
$500/mo.

**What it produces.** Same reference envelope as Seedance (9+3+3, 12 total), 5–15s
output, 24fps, 1440px short edge, native stereo.

**Worth a second look despite the history.** The earlier abysmal results were
almost certainly **the prompt pipeline, not the model** — H3 does accept video
references, and the documented division of labour (image refs lock identity and
product but not lighting; video refs lock motion, camera, grade and grain but not
identity) is exactly the right architecture. With the decomposition stage in front
of it, it deserves a fair retest.

**What it cannot do.** **5s duration floor** — the worst fit for fast-cut ads
(3.55× waste unpacked). Documented traps: a first-frame image plus references
**silently drops an input at full price with no error**; validation lands 2–3
minutes *after* a 200 OK; over-limit requests may complete and charge anyway. All
three are guarded client-side in `adpipe/adapters/base.py`, which is mandatory here
rather than nice-to-have.

**How fragile.** Moderate, with the worst silent-failure profile of the three.

---

## What I would actually do

1. **Fund Kling Standard ($6.99) tonight.** Run 3 references × 1 product, 3
   attempts. That buys the one number everything hinges on: **acceptance rate per
   shot grammar**. Half a day.
2. **In parallel, fund `fal` (~$50)** and run the same matrix on Seedance 2.0
   r2v. This is the Architecture A vs B decision, settled by evidence: does
   native multi-shot respect a *specified* rhythm, or invent its own? If it
   invents its own, **Architecture A wins by default** and Kling is the answer.
3. **Settle rights before any client asset is uploaded.** Commercial use and
   training-on-inputs are verified for *no* platform. You handle confidential
   client work; this is a blocker, not a detail.
4. **Use Higgsfield manually** as a creative benchmark. Do not put browser
   automation in the delivery path.

## What I would not promise the client on Monday

**Do not commit to 20–30 shipped creatives in 3 days yet.** The arithmetic
supports it — 210 calls is a few hours of mostly-parallel compute, plus ~1.3 hours
of deterministic processing — but two unmeasured things decide it:

- **Acceptance rate.** At 3 attempts/accept, 30 ads needs ~90 candidates. At 6,
  it needs 180 and Seedance goes over budget.
- **Human review.** ~90 candidates at 2 minutes each is **~3 hours of your
  attention** — the actual bottleneck. Compute is not the constraint; you are.

**What is safely promisable now:** the decomposition problem that broke the last
attempt is fixed and measured; 20–35s multi-shot assembly at frame-exact reference
rhythm works today; and a funded half-day converts that into a real quality number.

## And the legitimate finding the brief asked me to be willing to state

If the acceptance rate on real generations turns out poor, **the honest answer is
shot-by-shot generation with heavy human QC, not end-to-end automation** — and the
pipeline is already built for that. It generates and assembles automatically,
prices every attempt against a hard budget stop, and lands every candidate in a
review queue where a rejection is re-runnable with one changed parameter. That is
the shape that survives a bad acceptance rate. Full end-to-end automation is an
optimisation to earn once the numbers justify it, not the thing to sell on Monday.
