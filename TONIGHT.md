# TONIGHT — fastest path to one watchable 20–35s multi-shot output

**There is a runnable output already in the repo.** Then two paths depending on
whether you can fund a platform in the next ten minutes.

---

## Already built — look at this first

```
work/out/TONIGHT/final/ad.mp4
```

**22.54s · 16 shots · 7 generations · 1280×720 @24fps · audio bed · glow pass**

Reproduced the reference ad's cut rhythm at **F1 = 0.889, timing error 0ms** —
every rendered cut lands on the reference's exact frame.

**Be clear about what you are looking at.** The *structure* is real: real cuts at
real ad pacing (0.42s hook cuts through 3.0s payoff holds), varied framings,
speed ramps, cross-shot colour normalisation, highlight bloom, muxed audio. The
*footage* is procedurally generated placeholder — flat colour fields with boxes,
because **no generation credits were available** (Kling account: Free tier, 0
credits; no other vendor keys in the environment).

So it is a mechanism proof, not a creative proof. Do not show it to the client as
a creative sample. Do show it to yourself as evidence that the hard deterministic
half works, because swapping the generator in is a one-line change.

Regenerate it:

```bash
cd /home/user/kariwalbadal
PYTHONPATH=. python3 -c "
from adpipe.run import run_pipeline
from adpipe.synth.prompts import ProductSpec, StyleSpec
from adpipe.adapters.mock import MockAdapter
print(run_pipeline('work/refs/real_composite_ad.mp4',
    ProductSpec(name='the Aurora can', colour='cobalt blue', logo_text='AURORA',
                image_paths=['work/products/can_front.png']),
    StyleSpec(), out_root='work/out/TONIGHT', adapter=MockAdapter(),
    edl_path='work/out/real_ad/edl.json', pack=True, reframe_slices=True)['status'])"
```

---

## Path A — you can fund Kling in the next 10 minutes (recommended)

Cheapest route to a *real* watchable multi-shot ad, and the adapter is already
written and contract-tested against the live API.

1. **Top up.** Kling Standard is $6.99 (660 credits) — enough for several full
   ads at 720p. Ultra ($127.99 / 26,000cr) is the throughput tier.
   `https://kling.ai/h5-app/membership-agent?r=agent_Claude&type=credit`
2. **Confirm credits landed:** call `query_membership_and_credits` — it must
   report non-zero `availableRemainCredits`.
3. **Put 1–3 flat-lit product stills** in `work/products/` (plain background,
   neutral light, no scene — per the reference division of labour in
   `docs/prompt-patterns.md`).
4. **Decompose your reference ad** (any 20–35s ad you have rights to):

```bash
PYTHONPATH=. python3 -c "
from adpipe.decompose.pipeline import decompose
e=decompose('work/refs/YOUR_AD.mp4','work/out/yours')
print(e['timing']['n_shots'],'shots', e['timing']['rhythm_profile'])
print('review these by eye:', e['provenance']['review_flags'])"
```

5. **Check the cut list before spending.** If `n_shots` is obviously wrong, fix
   the EDL first — a wrong EDL means paying for the wrong shots.
6. **Dry-run to see prompts and cost, with no spend:**

```bash
PYTHONPATH=. python3 -c "
from adpipe.run import run_pipeline
from adpipe.synth.prompts import ProductSpec, StyleSpec
from adpipe.adapters.kling import KlingAdapter
r=run_pipeline('work/refs/YOUR_AD.mp4',
  ProductSpec(name='YOUR PRODUCT', colour='...', logo_text='...',
              image_paths=['work/products/p1.png']),
  StyleSpec(), out_root='work/out/live', adapter=KlingAdapter(model='kling-video-v3_0_omni'),
  edl_path='work/out/yours/edl.json', platform='kling_elements', dry_run=True)
print(r['budget_report'])
print(r['prompts'][0]['prompt'][:600])"
```

7. **Submit.** Kling here is reached over **MCP, which is an agent-side
   transport** — a Python process cannot call it. So `KlingAdapter.submit` builds
   and validates the exact MCP payload and hands it back; have Claude submit each
   payload with `mcp__Kling__image_to_video`, poll `query_tasks`, drop the
   returned MP4s into `work/out/live/gen/`, then run the pipeline with
   `MockAdapter` replaced by a trivial adapter that returns those paths. The
   validation, costing and ledger stay in one place either way.

**Expect:** 720p (1080p is membership-gated and only on `motion_control`, which
wants a person or animal subject — not a product). ~$1.65/attempt packed,
≈$4.94 per accepted ad at 3 attempts.

## Path B — you can fund `fal` instead

Seedance 2.0 reference-to-video is the only platform with a documented, genuine
**video-reference + separate product-reference** contract (9 images + 3 videos +
3 audio, 12 files total, `@Image1`/`@Video1` addressing, 1080p available) — and
its v2v path is **40% cheaper per second than its t2v path** ($0.1814 vs $0.3024
at 720p).

It is the best creative bet and the most expensive per accepted ad (≈$17.96).
`platform='seedance'` is already wired in prompt synthesis; the HTTP adapter is
the one piece not written (the base class does the validation, ~40 lines).

---

## What to tell the client on Monday

Truthfully:

- The reference-decomposition problem that broke the last attempt **is solved and
  measured**: precision 1.000, 28ms timing error on real footage, 4.1 frames
  sampled per shot instead of one per ten seconds.
- Multi-shot assembly at 20–35s with frame-exact reference rhythm **works today**.
- **Per-shot creative quality is untested** because no platform was funded. That
  is a half-day of testing once one account has credit — not a rebuild.
- Do not promise 20–30 shipped creatives in 3 days yet. The compute comfortably
  supports it; the unmeasured acceptance rate and ~3 hours of human review are
  what decide it. See `RECOMMENDATION.md` §"What I would not promise".
