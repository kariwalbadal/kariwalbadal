"""Prompt synthesis: EDL shot object -> generation prompt.

Versioned and dependency-free on purpose. This is where the creative judgment
lives, so it must be testable in isolation and diffable between versions --
every other stage is plumbing that can be re-run, but a prompt regression
silently degrades every ad produced after it.

The structure encodes patterns verified against vendor guidance (see
docs/prompt-patterns.md for sources and dates). Three rules do most of the work:

1. Assign every input an explicit job. The reference video supplies camera
   movement and editing rhythm; the product image supplies identity. Models
   conflate them unless told which is which.
2. Respect the division of labour. Image references carry identity, product and
   style but NOT lighting; video references carry motion, camera, grade and
   grain but NOT identity. Asking a reference for what it cannot supply is the
   most common cause of a drifting product.
3. Name the camera move, and motivate it. Named moves ("slow push in", "whip
   pan") are read directly; a move justified by something happening in the
   scene renders better than an arbitrary instruction.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

PROMPT_SYNTH_VERSION = "1.2.0"

Platform = Literal["seedance", "minimax_h3", "kling_elements", "kling_motion",
                   "runway_aleph", "generic"]

# --- vocabulary -------------------------------------------------------------
# Phrasings chosen because vendor guidance lists them as directly-read tokens.
CAMERA_PHRASE: dict[str, str] = {
    "static": "locked-off static camera",
    "push_in": "slow push in toward the product",
    "pull_out": "steady pull out revealing the wider scene",
    "orbit": "smooth orbit around the product",
    "whip_pan": "fast whip pan",
    "pan_left": "lateral pan to the left",
    "pan_right": "lateral pan to the right",
    "tilt_up": "tilt up across the product",
    "tilt_down": "tilt down onto the product",
    "handheld": "loose handheld camera with natural sway",
    "crane": "rising crane move",
    "top_down": "top-down overhead angle",
    "dolly": "dolly move alongside the product",
    "roll": "rolling camera",
    "unknown": "controlled camera move",
}

SCALE_PHRASE: dict[str, str] = {
    "extreme_close_up": "extreme close-up filling the frame",
    "close_up": "tight close-up",
    "medium": "medium shot",
    "wide": "wide shot",
    "extreme_wide": "extreme wide establishing shot",
    "unknown": "",
}

# A move reads better when the scene motivates it, per vendor prompt guidance.
MOTIVATION: dict[str, str] = {
    "push_in": "as the product settles into place",
    "pull_out": "as the surrounding scene opens up",
    "orbit": "as light travels across its surface",
    "whip_pan": "snapping to the next beat",
    "handheld": "as if filmed by hand on a phone",
    "tilt_up": "following the product upward",
    "tilt_down": "settling onto the product",
}

# Only genuine velocity CHANGES become instructions. `slow_motion_or_locked`
# is deliberately absent: the detector cannot tell a slow-motion shot from a
# locked-off one, and emitting "slow motion" on a static shot instructs the
# model to do something the reference never did.
RAMP_PHRASE: dict[str, str] = {
    "ramp_up": "speed ramping up into the cut",
    "ramp_down": "easing into slow motion",
}

ROLE_INTENT: dict[str, str] = {
    "hook": "an arresting opening beat that stops the scroll",
    "build": "building momentum",
    "mid": "sustaining interest",
    "payoff": "the hero product payoff",
    "endcard": "a final product hold",
}

# Verified failure modes, encoded as a lint rather than prose in a doc nobody
# reads at 2am.
LINT_RULES = [
    ("lighting_and_grade_conflict",
     "Do not request a lighting change and a reference-grade match in the same "
     "prompt; the model will satisfy neither."),
    ("audio_alone",
     "An audio reference must travel with at least one image or video reference."),
    ("too_many_refs",
     "Seedance 2.0 / MiniMax H3 cap total reference files at 12."),
    ("keyframe_cropping",
     "Do not substitute extracted keyframes for the reference video; the reused "
     "information is dynamic."),
]


@dataclass
class ProductSpec:
    """The SKU being advertised."""
    name: str
    description: str = ""
    material: str = ""
    colour: str = ""
    logo_text: str = ""
    image_paths: list[str] = field(default_factory=list)
    must_preserve: list[str] = field(default_factory=lambda: [
        "colour", "overall structure", "silhouette", "label legibility",
        "material finish"])


@dataclass
class StyleSpec:
    """Creative direction that is not derivable from the reference."""
    visual_direction: str = "clean, premium, contemporary"
    environments: list[str] = field(default_factory=list)
    glow: bool = True
    negative: list[str] = field(default_factory=lambda: [
        "warped product label", "extra fingers", "deformed hands",
        "illegible text", "melting geometry", "duplicated product",
        "watermark", "subtitles"])


@dataclass
class ShotPrompt:
    """One generation request's creative payload, platform-addressed."""
    shot_index: int
    text: str
    negative: str
    duration_s: float
    reference_map: dict[str, str]
    params: dict[str, Any] = field(default_factory=dict)
    synth_version: str = PROMPT_SYNTH_VERSION
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ref_token(platform: Platform, kind: str, idx: int,
               element_id: Optional[str] = None) -> str:
    """Per-platform reference addressing.

    These differ materially and are a common silent failure: a prompt written
    with @Image1 against Kling addresses nothing at all.
    """
    if platform in ("seedance", "minimax_h3"):
        return f"@{'Image' if kind == 'image' else 'Video'}{idx}"
    if platform == "kling_elements":
        # Kling binds Elements by id wrapped in triple angle brackets; plain
        # reference images are addressed in Chinese as 图片N.
        return f"<<<{element_id}>>>" if element_id else f"图片{idx}"
    if platform == "kling_motion":
        return "the subject image"
    if platform == "runway_aleph":
        return "the source video" if kind == "video" else "the reference image"
    return f"[{kind}{idx}]"


def describe_shot(shot: dict[str, Any]) -> str:
    """Plain-language description of one EDL shot's craft."""
    bits: list[str] = []
    scale = SCALE_PHRASE.get(shot.get("shot_scale", "unknown"), "")
    if scale and (shot.get("shot_scale_confidence") or 0) >= 0.30:
        bits.append(scale)
    move = shot.get("camera_move", "unknown")
    bits.append(CAMERA_PHRASE.get(move, CAMERA_PHRASE["unknown"]))
    if (mot := MOTIVATION.get(move)):
        bits.append(mot)
    if (ramp := shot.get("speed_ramp")) and ramp in RAMP_PHRASE:
        bits.append(RAMP_PHRASE[ramp])
    light = (shot.get("lighting") or {}).get("description")
    if light:
        bits.append(light.replace("_", " "))
    return ", ".join(b for b in bits if b)


def synthesize_shot_prompt(shot: dict[str, Any], product: ProductSpec,
                           style: StyleSpec, platform: Platform = "seedance",
                           reference_video_role: bool = True,
                           element_id: Optional[str] = None,
                           n_product_images: int = 1) -> ShotPrompt:
    """EDL shot -> one generation prompt. Pure function; no I/O.

    `reference_video_role` controls whether the reference clip is cited for its
    motion. Turn it off when the platform cannot take a video reference, so the
    prompt does not instruct against an input that is not there.
    """
    warnings: list[str] = []
    refs: dict[str, str] = {}

    img_tok = _ref_token(platform, "image", 1, element_id)
    refs["product_image"] = img_tok
    vid_tok = None
    if reference_video_role and platform in ("seedance", "minimax_h3", "runway_aleph"):
        vid_tok = _ref_token(platform, "video", 1)
        refs["reference_video"] = vid_tok

    role = shot.get("role") or "mid"
    intent = ROLE_INTENT.get(role, "")
    craft = describe_shot(shot)
    dur = float(shot.get("duration") or 0.0)

    parts: list[str] = []

    # 1. Job assignment first -- what each input is FOR.
    if vid_tok:
        parts.append(
            f"Reference {vid_tok} for its camera movement, motion energy and "
            f"editing rhythm only. Take the product identity from {img_tok}, not "
            f"from {vid_tok}.")
    else:
        parts.append(f"Create the shot around the product shown in {img_tok}.")

    # 2. The shot itself.
    shot_line = (f"Single continuous shot, {dur:.1f} seconds: {craft}." if craft
                 else f"Single continuous shot, {dur:.1f} seconds.")
    parts.append(shot_line)
    if intent:
        parts.append(f"This shot is {intent}.")

    # 3. Product and what the product does -- kinetics, per the brief's spec.
    subject_action = shot.get("subject_action") or (
        "the product moving with real weight and physicality -- rotating, "
        "landing or settling rather than floating")
    parts.append(f"Feature {product.name}: {subject_action}.")
    if product.description:
        parts.append(product.description)

    # 4. Identity lock. Repetition here is deliberate; it measurably holds the
    #    SKU across independently generated shots.
    preserve = ", ".join(product.must_preserve)
    ident = f"Keep {product.name} identical to {img_tok} across every shot: preserve {preserve}"
    if product.colour:
        ident += f"; the colour is {product.colour}"
    if product.material:
        ident += f"; the material reads as {product.material}"
    if product.logo_text:
        ident += f"; the label reads '{product.logo_text}' and must stay legible"
    parts.append(ident + ".")

    # 5. Environment and direction.
    if style.environments:
        parts.append(f"Setting: {style.environments[min(shot.get('index', 0), len(style.environments)-1)]}.")
    parts.append(f"Overall visual direction: {style.visual_direction}.")

    # 6. Finishing. Only requested when no grade-match is also being asked for,
    #    because the two together is a documented failure.
    if style.glow:
        if vid_tok:
            # Phrased as a highlight treatment rather than a relight, because
            # asking for a lighting change AND a reference-grade match in one
            # prompt is a documented way to get neither.
            parts.append("Add crisp specular highlights and a subtle light bloom on the product.")
        else:
            parts.append("Add a glossy highlight pass and light bloom on the product.")

    text = " ".join(parts)
    negative = ", ".join(style.negative)

    params: dict[str, Any] = {"duration_s": round(dur, 3),
                              "shot_role": role,
                              "camera_move": shot.get("camera_move"),
                              "transition_out": shot.get("transition_out")}

    if n_product_images > 9:
        warnings.append("more than 9 product images exceeds the documented cap")

    return ShotPrompt(shot_index=int(shot.get("index", 0)), text=text,
                      negative=negative, duration_s=round(dur, 3),
                      reference_map=refs, params=params, warnings=warnings)


def synthesize_multishot_prompt(edl: dict[str, Any], product: ProductSpec,
                                style: StyleSpec,
                                platform: Platform = "seedance",
                                max_shots: Optional[int] = None) -> ShotPrompt:
    """Architecture B: one prompt for a whole multi-shot sequence.

    The cut rhythm is stated as an explicit shot-by-shot timing list. Without
    it a native multi-shot model invents its own pacing, which is precisely
    what makes it unable to reproduce a reference.
    """
    shots = edl["shots"][:max_shots] if max_shots else edl["shots"]
    total = sum(float(s["duration"]) for s in shots)
    img_tok = _ref_token(platform, "image", 1)
    vid_tok = _ref_token(platform, "video", 1)

    lines = [
        f"Reference {vid_tok} for its camera movement, cutting rhythm and "
        f"transition timing. Take product identity from {img_tok}.",
        f"Produce a {total:.1f}-second advertisement in {len(shots)} distinct shots "
        f"with hard cuts between them. Hold each shot for exactly the duration given:",
    ]
    for s in shots:
        craft = describe_shot(s)
        lines.append(f"- Shot {s['index']+1} ({float(s['duration']):.2f}s, "
                     f"{s.get('role','mid')}): {craft}. "
                     f"Cut out with a {s.get('transition_out','hard_cut').replace('_',' ')}.")
    preserve = ", ".join(product.must_preserve)
    lines.append(f"Across all shots keep {product.name} identical to {img_tok}: "
                 f"preserve {preserve}.")
    lines.append(f"Overall visual direction: {style.visual_direction}.")
    if (edl.get("audio") or {}).get("music_driven_edit"):
        bpm = (edl.get("audio") or {}).get("bpm")
        lines.append(f"The edit is music-driven at about {bpm} BPM; land cuts on the beat.")

    return ShotPrompt(shot_index=-1, text="\n".join(lines),
                      negative=", ".join(style.negative),
                      duration_s=round(total, 3),
                      reference_map={"product_image": img_tok,
                                     "reference_video": vid_tok},
                      params={"n_shots": len(shots), "mode": "native_multishot"})


def lint_prompt(prompt: ShotPrompt, n_images: int = 1, n_videos: int = 0,
                n_audio: int = 0) -> list[str]:
    """Check a synthesized prompt against verified vendor failure modes."""
    problems: list[str] = []
    text = prompt.text.lower()
    if n_audio > 0 and n_images == 0 and n_videos == 0:
        problems.append("audio_alone: " + LINT_RULES[1][1])
    if n_images + n_videos + n_audio > 12:
        problems.append("too_many_refs: " + LINT_RULES[2][1])
    if n_images > 9:
        problems.append("too_many_refs: more than 9 image references")
    if n_videos > 3:
        problems.append("too_many_refs: more than 3 video references")
    if "relight" in text and "grade" in text:
        problems.append("lighting_and_grade_conflict: " + LINT_RULES[0][1])
    if not prompt.text.strip():
        problems.append("empty prompt")
    if prompt.duration_s <= 0:
        problems.append("non-positive duration")
    return problems
