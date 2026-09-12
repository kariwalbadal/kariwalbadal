"""Audio analysis: tempo, beat grid, cut/beat alignment, voiceover presence.

Whether the edit is music-driven decides how the generated ad must be cut. If
cuts land on beats, assembly has to quantise to the beat grid; if they do not,
quantising would actively damage the rhythm. That is measured here, not assumed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import subprocess
import tempfile

import numpy as np


def extract_wav(video_path: str, out_wav: Optional[str] = None,
                sr: int = 22050) -> Optional[str]:
    """Demux audio to mono wav. Returns None when the file has no audio track."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", video_path],
        capture_output=True, text=True)
    if not probe.stdout.strip():
        return None
    path = out_wav or str(Path(tempfile.mkdtemp()) / "audio.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_path,
                    "-vn", "-ac", "1", "-ar", str(sr), path], check=True)
    return path


def analyse_audio(video_path: str, cut_times: list[float],
                  out_wav: Optional[str] = None,
                  beat_tolerance: float = 0.08) -> dict[str, Any]:
    """Tempo, beat grid, fraction of cuts landing on beats, VO heuristic.

    `beat_tolerance` of 80ms is about two frames at 24fps -- tighter than a
    viewer notices, loose enough to survive detector timing error.
    """
    import librosa

    wav = extract_wav(video_path, out_wav)
    if wav is None:
        return {"has_audio": False, "bpm": None, "beat_times": [],
                "cut_beat_alignment": None, "music_driven_edit": None,
                "voiceover_present": None, "speech_segments": [],
                "rms_envelope_path": None}

    y, sr = librosa.load(wav, sr=None, mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    bpm = float(np.atleast_1d(tempo)[0]) if tempo is not None else None
    beat_times = [round(float(b), 4) for b in np.atleast_1d(beats)]

    aligned = 0
    per_cut: list[dict[str, Any]] = []
    for c in cut_times:
        if beat_times:
            d = min(abs(c - b) for b in beat_times)
        else:
            d = float("inf")
        on = d <= beat_tolerance
        aligned += int(on)
        per_cut.append({"cut": round(c, 4),
                        "nearest_beat_delta": (None if d == float("inf") else round(d, 4)),
                        "on_beat": on})
    frac = (aligned / len(cut_times)) if cut_times else None

    # Voiceover heuristic. Speech sits in a narrow band with strong temporal
    # modulation and low spectral flatness; a music bed is flatter and steadier.
    # This is a heuristic, reported with its own confidence, not a transcript.
    # Both features are derived from ONE STFT so their frame grids line up;
    # librosa's defaults differ per feature and would otherwise not broadcast.
    n_fft, hop = 1024, 256
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
    flat = librosa.feature.spectral_flatness(S=S)[0]
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    band = (freqs >= 300) & (freqs <= 3400)          # telephony speech band
    band_ratio = S[band].sum(axis=0) / (S.sum(axis=0) + 1e-9)
    times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr,
                                   hop_length=hop, n_fft=n_fft)
    speechy = (band_ratio > 0.55) & (flat < 0.12)

    segs: list[dict[str, Any]] = []
    if speechy.any():
        run_start = None
        for i, v in enumerate(speechy):
            if v and run_start is None:
                run_start = times[i]
            elif not v and run_start is not None:
                if times[i] - run_start >= 0.35:
                    segs.append({"start": round(float(run_start), 3),
                                 "end": round(float(times[i]), 3)})
                run_start = None
        if run_start is not None and times[-1] - run_start >= 0.35:
            segs.append({"start": round(float(run_start), 3),
                         "end": round(float(times[-1]), 3)})
    vo_cov = sum(s["end"] - s["start"] for s in segs) / max(1e-6, len(y) / sr)

    rms = librosa.feature.rms(S=S, frame_length=n_fft, hop_length=hop)[0]
    env_path = None
    if out_wav:
        env_path = str(Path(out_wav).with_suffix(".rms.npy"))
        np.save(env_path, rms)

    return {
        "has_audio": True,
        "bpm": None if bpm is None else round(bpm, 2),
        "beat_times": beat_times,
        "n_beats": len(beat_times),
        "cut_beat_alignment": None if frac is None else round(frac, 4),
        "cut_beat_detail": per_cut,
        "beat_tolerance": beat_tolerance,
        # A majority of cuts on beats is the threshold for calling the edit
        # music-driven; below that, assembly must not quantise.
        "music_driven_edit": None if frac is None else bool(frac >= 0.6),
        "voiceover_present": bool(vo_cov > 0.15),
        "voiceover_coverage": round(float(vo_cov), 3),
        "voiceover_confidence": "heuristic_spectral_no_asr",
        "speech_segments": segs,
        "rms_envelope_path": env_path,
    }
