"""EDL schema: the machine-readable reconstruction of a reference ad.

This artifact is the contract between decomposition and generation. Everything
downstream (prompt synthesis, job submission, assembly) reads only this, so a
wrong EDL is a wrong ad -- hence `validate_edl` and the accuracy gate in
`adpipe.decompose.validate`.

Fields carry an explicit `confidence` and `method` wherever they are inferred
rather than measured, so a later vision pass can override low-confidence
heuristics without guessing what was solid.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional
import json

EDL_SCHEMA_VERSION = "1.1.0"

CameraMove = Literal[
    "static", "push_in", "pull_out", "orbit", "whip_pan", "pan_left", "pan_right",
    "tilt_up", "tilt_down", "handheld", "crane", "top_down", "dolly", "roll", "unknown",
]
ShotScale = Literal["extreme_close_up", "close_up", "medium", "wide", "extreme_wide", "unknown"]
Transition = Literal["hard_cut", "whip", "match_cut", "morph", "dissolve", "fade", "unknown"]

# JSON Schema -- enforced on every emit. Keeps the generative half honest: the
# creative text is free-form, the execution envelope is strictly typed.
EDL_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AdPipe Reference EDL",
    "type": "object",
    "required": ["schema_version", "source", "timing", "shots", "audio"],
    "additionalProperties": True,
    "properties": {
        "schema_version": {"type": "string"},
        "source": {
            "type": "object",
            "required": ["path", "duration", "fps", "width", "height"],
            "properties": {
                "path": {"type": "string"},
                "duration": {"type": "number", "exclusiveMinimum": 0},
                "fps": {"type": "number", "exclusiveMinimum": 0},
                "width": {"type": "integer", "minimum": 1},
                "height": {"type": "integer", "minimum": 1},
                "aspect_ratio": {"type": "string"},
                "sha256": {"type": "string"},
            },
        },
        "timing": {
            "type": "object",
            "required": ["n_shots", "cut_times", "shot_durations"],
            "properties": {
                "n_shots": {"type": "integer", "minimum": 1},
                "cut_times": {"type": "array", "items": {"type": "number", "minimum": 0}},
                "shot_durations": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0}},
                "median_shot_duration": {"type": "number"},
                "p10_shot_duration": {"type": "number"},
                "p90_shot_duration": {"type": "number"},
                "cuts_per_second": {"type": "number"},
                "rhythm_profile": {"type": "string"},
            },
        },
        "shots": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["index", "start", "end", "duration"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "start": {"type": "number", "minimum": 0},
                    "end": {"type": "number", "exclusiveMinimum": 0},
                    "duration": {"type": "number", "exclusiveMinimum": 0},
                    "camera_move": {"type": "string"},
                    "camera_move_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "motion_metrics": {"type": "object"},
                    "shot_scale": {"type": "string"},
                    "shot_scale_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "subject": {"type": ["string", "null"]},
                    "subject_action": {"type": ["string", "null"]},
                    "lighting": {"type": "object"},
                    "palette": {"type": "array", "items": {"type": "string"}},
                    "speed_ramp": {"type": ["string", "null"]},
                    "transition_out": {"type": "string"},
                    "transition_out_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "frames": {"type": "array", "items": {"type": "object"}},
                    "role": {"type": ["string", "null"]},
                    "on_beat": {"type": "boolean"},
                },
            },
        },
        "audio": {
            "type": "object",
            "properties": {
                "has_audio": {"type": "boolean"},
                "bpm": {"type": ["number", "null"]},
                "beat_times": {"type": "array", "items": {"type": "number"}},
                "cut_beat_alignment": {"type": ["number", "null"]},
                "music_driven_edit": {"type": ["boolean", "null"]},
                "voiceover_present": {"type": ["boolean", "null"]},
                "speech_segments": {"type": "array", "items": {"type": "object"}},
                "rms_envelope_path": {"type": ["string", "null"]},
            },
        },
        "onscreen_text": {"type": "array", "items": {"type": "object"}},
        "provenance": {"type": "object"},
    },
}


@dataclass
class FrameRef:
    """One extracted frame. `position` is where in the shot it was sampled."""
    t: float
    path: str
    position: str  # first | early | middle | late | last | dense_<n>


@dataclass
class Shot:
    index: int
    start: float
    end: float
    duration: float
    camera_move: str = "unknown"
    camera_move_confidence: float = 0.0
    motion_metrics: dict[str, Any] = field(default_factory=dict)
    shot_scale: str = "unknown"
    shot_scale_confidence: float = 0.0
    subject: Optional[str] = None
    subject_action: Optional[str] = None
    lighting: dict[str, Any] = field(default_factory=dict)
    palette: list[str] = field(default_factory=list)
    speed_ramp: Optional[str] = None
    transition_out: str = "unknown"
    transition_out_confidence: float = 0.0
    frames: list[FrameRef] = field(default_factory=list)
    role: Optional[str] = None          # hook | build | mid | payoff | endcard
    on_beat: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["frames"] = [asdict(f) if not isinstance(f, dict) else f for f in self.frames]
        return d


def validate_edl(edl: dict[str, Any]) -> None:
    """Raise jsonschema.ValidationError if the EDL is malformed.

    Also enforces cross-field invariants the JSON Schema cannot express.
    """
    import jsonschema

    jsonschema.validate(edl, EDL_JSON_SCHEMA)

    shots = edl["shots"]
    timing = edl["timing"]
    if timing["n_shots"] != len(shots):
        raise ValueError(f"timing.n_shots={timing['n_shots']} but {len(shots)} shots present")
    if len(timing["cut_times"]) != max(0, len(shots) - 1):
        raise ValueError(
            f"{len(timing['cut_times'])} cut_times for {len(shots)} shots "
            f"(expected {max(0, len(shots) - 1)})"
        )
    for i, s in enumerate(shots):
        if s["index"] != i:
            raise ValueError(f"shots[{i}].index={s['index']} out of order")
        if s["end"] <= s["start"]:
            raise ValueError(f"shot {i}: end {s['end']} <= start {s['start']}")
        if abs((s["end"] - s["start"]) - s["duration"]) > 1e-3:
            raise ValueError(f"shot {i}: duration {s['duration']} != end-start")
        if i and abs(shots[i - 1]["end"] - s["start"]) > 1e-3:
            raise ValueError(f"gap/overlap between shot {i-1} and {i}")
    dur = edl["source"]["duration"]
    if shots[-1]["end"] - dur > 0.25:
        raise ValueError(f"last shot ends {shots[-1]['end']} beyond source duration {dur}")


def write_edl(edl: dict[str, Any], path: str) -> None:
    validate_edl(edl)
    with open(path, "w") as fh:
        json.dump(edl, fh, indent=2)


def load_edl(path: str) -> dict[str, Any]:
    with open(path) as fh:
        edl = json.load(fh)
    validate_edl(edl)
    return edl
