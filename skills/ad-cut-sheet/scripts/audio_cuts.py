#!/usr/bin/env python3
"""Audio structure, and how the picture cuts relate to it.

Beat alignment alone is too coarse for ad grammar. An edit can be locked to
beats, to onsets (transients that are not beats), to bar lines, or to the
music's section changes — and it can sit deliberately ahead of or behind the
beat. Each implies a different instruction when recreating the edit, so each is
measured separately.

Sign convention: delta = cut_time - reference_time. Negative means the cut
LEADS (lands early, the common editorial choice); positive means it LAGS.
"""
from __future__ import annotations
from typing import Any, Optional
import numpy as np

BEAT_TOL = 0.080          # ~2 frames at 24fps: tighter than a viewer notices
ONSET_TOL = 0.060
MUSIC_DRIVEN_FRAC = 0.60  # majority-on-beat before quantising is safe


def analyse_audio_cuts(wav_path: str, cut_times: list[float],
                       duration: float) -> dict[str, Any]:
    import librosa

    y, sr = librosa.load(wav_path, sr=None, mono=True)
    hop = 512

    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time", hop_length=hop)
    bpm = float(np.atleast_1d(tempo)[0]) if tempo is not None else None
    beats = [float(b) for b in np.atleast_1d(beats)]

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onsets = [float(t) for t in librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=hop, units="time")]

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)

    # Section changes: peaks in spectral novelty mark drops, builds, breakdowns.
    try:
        sections = [float(t) for t in librosa.frames_to_time(
            librosa.segment.agglomerative(
                librosa.feature.mfcc(y=y, sr=sr, hop_length=hop), 6),
            sr=sr, hop_length=hop)]
    except Exception:
        sections = []

    def nearest(t: float, grid: list[float]):
        if not grid:
            return None, None
        i = int(np.argmin([abs(t - g) for g in grid]))
        return grid[i], round(t - grid[i], 4)      # signed: -ve = cut leads

    per_cut, on_beat, on_onset, leads, lags = [], 0, 0, 0, 0
    for c in cut_times:
        b, db = nearest(c, beats)
        o, do = nearest(c, onsets)
        hit_b = db is not None and abs(db) <= BEAT_TOL
        hit_o = do is not None and abs(do) <= ONSET_TOL
        on_beat += hit_b; on_onset += hit_o
        if hit_b:
            leads += db < 0; lags += db > 0
        # energy at the cut, relative to the track: do cuts land on transients?
        e = float(rms[int(np.argmin(np.abs(rms_t - c)))]) if len(rms) else 0.0
        per_cut.append({"cut": round(c, 4),
                        "beat_delta": db, "on_beat": bool(hit_b),
                        "onset_delta": do, "on_onset": bool(hit_o),
                        "rms_pct": round(float((rms < e).mean()), 3)})

    n = max(1, len(cut_times))
    beat_frac = round(on_beat / n, 4)
    # Bar grid: 4 beats to a bar, so every 4th beat is a downbeat candidate.
    downbeats = beats[::4]
    on_downbeat = sum(1 for c in cut_times
                      if downbeats and min(abs(c - d) for d in downbeats) <= BEAT_TOL)
    near_section = sum(1 for c in cut_times
                       if sections and min(abs(c - s) for s in sections) <= 0.25)

    return {
        "bpm": None if bpm is None else round(bpm, 2),
        "beat_seconds": None if not bpm else round(60.0 / bpm, 4),
        "n_beats": len(beats), "n_onsets": len(onsets),
        "beat_times": [round(b, 4) for b in beats],
        "onset_times": [round(o, 4) for o in onsets],
        "section_times": [round(s, 4) for s in sections],
        "cuts_on_beat": on_beat, "cuts_on_beat_frac": beat_frac,
        "cuts_on_onset": on_onset,
        "cuts_on_onset_frac": round(on_onset / n, 4),
        "cuts_on_downbeat": on_downbeat,
        "cuts_near_section_change": near_section,
        # Which grid the editor actually cut to.
        "edit_locked_to": ("beat" if beat_frac >= MUSIC_DRIVEN_FRAC else
                           "onset" if on_onset / n >= MUSIC_DRIVEN_FRAC else
                           "picture"),
        "music_driven_edit": bool(beat_frac >= MUSIC_DRIVEN_FRAC),
        # Editorial feel: cutting early is a different instruction from cutting late.
        "beat_phase": ("leads" if leads > lags * 1.5 else
                       "lags" if lags > leads * 1.5 else "on"),
        "mean_abs_beat_delta": (round(float(np.mean([abs(p["beat_delta"])
                                for p in per_cut if p["beat_delta"] is not None])), 4)
                                if per_cut else None),
        "per_cut": per_cut,
        "beat_tolerance": BEAT_TOL,
    }


def audio_cuts_md(a: dict[str, Any]) -> str:
    if not a or a.get("bpm") is None:
        return "## Audio\n\nNo usable audio track.\n"
    L = ["## Audio and cut sync", "",
         f"- **{a['bpm']} bpm** (beat {a['beat_seconds']}s) · {a['n_beats']} beats · "
         f"{a['n_onsets']} onsets · {len(a['section_times'])} section changes",
         f"- cuts on beat **{a['cuts_on_beat']}/{len(a['per_cut'])}** "
         f"({a['cuts_on_beat_frac']:.0%}) · on onset {a['cuts_on_onset']} · "
         f"on downbeat {a['cuts_on_downbeat']} · at section changes {a['cuts_near_section_change']}",
         f"- **edit locked to: {a['edit_locked_to']}** · phase: cuts **{a['beat_phase']}** "
         f"the beat (mean |delta| {a['mean_abs_beat_delta']}s)"]
    if a["edit_locked_to"] == "picture":
        L.append("- → picture-led. Do NOT quantise the recreation to a beat grid; "
                 "that would impose a rhythm the reference does not have.")
    else:
        L.append(f"- → recreate on the {a['edit_locked_to']} grid, cutting "
                 f"{a['beat_phase']} the beat.")
    L += ["", "| cut | beat delta | on beat | onset delta | energy pct |",
          "|---|---|---|---|---|"]
    for p in a["per_cut"]:
        bd = "-" if p["beat_delta"] is None else f"{p['beat_delta']:+.3f}s"
        od = "-" if p["onset_delta"] is None else f"{p['onset_delta']:+.3f}s"
        L.append(f"| {p['cut']:.3f} | {bd} | {'Y' if p['on_beat'] else ''} | {od} | "
                 f"{p['rms_pct']:.0%} |")
    L.append("")
    L.append("_delta = cut − reference; negative means the cut lands early._")
    return "\n".join(L)
