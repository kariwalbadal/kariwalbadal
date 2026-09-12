"""EDL-conformant assembly with ffmpeg.

This is the deterministic half of the pipeline and it is what actually delivers
the brief's creative spec: real cuts, real editing rhythm, speed ramps and
finishing. A generative model asked for "a 30-second multi-shot ad" invents its
own pacing; assembling per-shot clips against the EDL reproduces the
reference's pacing exactly, to the frame.

Every clip is conformed to one codec/fps/resolution envelope before concat --
mismatched inputs are the usual cause of concat desync and audio drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import json
import math
import shutil
import subprocess


@dataclass
class AssemblySpec:
    width: int = 1280
    height: int = 720
    fps: int = 30
    crf: int = 18
    preset: str = "medium"
    pix_fmt: str = "yuv420p"
    audio_bitrate: str = "192k"
    sample_rate: int = 48000


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:8])}...\n{proc.stderr[-2500:]}")


def probe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def conform_clip(src: str, dst: str, target_duration: float, spec: AssemblySpec,
                 speed_ramp: Optional[str] = None,
                 pad_mode: str = "hold_last") -> dict[str, Any]:
    """Conform one generated clip to exactly `target_duration` at spec.

    A generated clip almost never matches the EDL slot: models emit whole
    seconds. Retiming to fit is what preserves the reference's rhythm, and the
    EDL slot -- not the model's output length -- is authoritative.
    """
    src_dur = probe_duration(src)
    if src_dur <= 0:
        raise RuntimeError(f"cannot read duration of {src}")

    n_frames = max(1, int(round(target_duration * spec.fps)))
    # setpts retimes without dropping content; PTS factor < 1 speeds up.
    factor = target_duration / src_dur
    filters = [f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease",
               f"pad={spec.width}:{spec.height}:(ow-iw)/2:(oh-ih)/2:color=black",
               "setsar=1"]

    if speed_ramp in ("ramp_up", "ramp_down") and src_dur > 0.4:
        # A real ramp changes velocity ACROSS the shot rather than uniformly.
        #
        # setpts remaps each input frame at time T to a new time f(T), so
        # playback speed is 1/f'(T). Acceleration therefore needs a DECREASING
        # derivative: f(T)=sqrt(D*T) speeds up, f(T)=T^2/D slows down. Both
        # satisfy f(D)=D, so the ramp preserves the source span and the uniform
        # `factor` below is still what fits the clip to its EDL slot.
        #
        # Variables are ffmpeg's: T is seconds, TB the timebase. (There is no
        # TS constant -- using one silently fails the whole filtergraph.)
        d = max(src_dur, 1e-6)
        if speed_ramp == "ramp_up":
            ramp = f"sqrt({d:.6f}*T)"
        else:
            ramp = f"(T*T/{d:.6f})"
        filters.append(f"setpts=({factor:.6f}*{ramp})/TB")
        filters.append(f"fps={spec.fps}")
    else:
        filters.append(f"setpts={factor:.6f}*PTS")
        filters.append(f"fps={spec.fps}")

    vf = ",".join(filters)
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-an",
          "-vf", vf, "-frames:v", str(n_frames),
          "-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf),
          "-pix_fmt", spec.pix_fmt, "-video_track_timescale", str(spec.fps * 1000), dst])

    got = probe_duration(dst)
    if got < target_duration - 1.5 / spec.fps and pad_mode == "hold_last":
        # Short by more than a frame: hold the final frame rather than let the
        # concat slip, which would shift every later cut off its beat.
        held = str(Path(dst).with_name(Path(dst).stem + "_held.mp4"))
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", dst,
              "-vf", f"tpad=stop_mode=clone:stop_duration={target_duration-got:.4f},fps={spec.fps}",
              "-frames:v", str(n_frames), "-c:v", "libx264", "-preset", spec.preset,
              "-crf", str(spec.crf), "-pix_fmt", spec.pix_fmt, held])
        shutil.move(held, dst)
        got = probe_duration(dst)

    return {"src": src, "dst": dst, "src_duration": round(src_dur, 4),
            "target_duration": round(target_duration, 4),
            "final_duration": round(got, 4), "frames": n_frames,
            "speed_ramp": speed_ramp, "retime_factor": round(factor, 4)}


def build_transition(clip_a: str, clip_b: str, dst: str, kind: str,
                     spec: AssemblySpec, duration: float = 0.25) -> Optional[str]:
    """Render a transition between two clips. Returns dst, or None for a hard cut.

    A hard cut needs no render -- concatenation IS the cut. Only overlapping
    transitions consume frames from both sides.
    """
    if kind in ("hard_cut", "end", "unknown", "match_cut"):
        return None      # match cuts are composed, not filtered; see docs
    xf = {"dissolve": "fade", "fade": "fadeblack", "whip": "hlslice",
          "morph": "fadegrays"}.get(kind)
    if xf is None:
        return None
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip_a, "-i", clip_b,
          "-filter_complex",
          f"[0:v][1:v]xfade=transition={xf}:duration={duration:.3f}:offset="
          f"{max(0.0, probe_duration(clip_a)-duration):.4f},fps={spec.fps},"
          f"format={spec.pix_fmt}[v]",
          "-map", "[v]", "-c:v", "libx264", "-preset", spec.preset,
          "-crf", str(spec.crf), "-pix_fmt", spec.pix_fmt, dst])
    return dst


def concat_clips(clips: list[str], dst: str, spec: AssemblySpec,
                 work_dir: Optional[str] = None) -> str:
    """Concat conformed clips. Uses the demuxer since all inputs share a format."""
    wd = Path(work_dir or Path(dst).parent); wd.mkdir(parents=True, exist_ok=True)
    lst = wd / "concat_list.txt"
    lst.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips))
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
          "-i", str(lst), "-c", "copy", dst])
    return dst


def mux_audio(video: str, audio: str, dst: str, spec: AssemblySpec,
              duck_to_video: bool = True) -> str:
    """Lay the reference audio bed under the assembled cut.

    The bed is trimmed to the video, never the reverse: the video length is the
    EDL's length and must not move.
    """
    vdur = probe_duration(video)
    args = ["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-i", audio,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", spec.audio_bitrate,
            "-ar", str(spec.sample_rate), "-t", f"{vdur:.4f}", "-shortest", dst]
    _run(args)
    return dst
