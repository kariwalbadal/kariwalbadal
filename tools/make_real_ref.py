#!/usr/bin/env python3
"""Build a real-footage reference ad with EXACT known cut timecodes.

Synthetic flat-colour shots prove nothing about real footage: no grain, no
motion blur, no compression artefacts, no complex content. This composites real
source clips at ad pacing so ground truth stays exact while the pixels are real.

The hard cases are deliberate. Segments marked same_source_adjacent cut between
two moments of the SAME clip -- identical palette, grain and subject -- which is
the real production case (an ad that stays inside one visual world) and the one
a content-delta detector is most likely to miss.
"""
import json, subprocess, sys
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "work/refs/real_composite_ad.mp4")
OUT.parent.mkdir(parents=True, exist_ok=True)
FPS, W, H = 24, 1280, 720
R = Path("work/refs")
JELLY = R / "src_Jellyfish_720_10s_5MB.mp4"   # real camera footage, real grain
BBB   = R / "src_Big_Buck_Bunny_720_10s_5MB.mp4"
SINTEL= R / "src_Sintel_1080_10s_5MB.mp4"

# (src, in_point, duration, role)  -- ad grammar: fast hook, build, long payoff
SEGS = [
    (JELLY,  1.0, 0.5, "hook"),
    (BBB,    2.0, 0.4, "hook"),
    (JELLY,  4.5, 0.5, "hook"),     # same source as seg0, different moment
    (SINTEL, 1.5, 0.6, "hook"),
    (JELLY,  5.2, 0.5, "hook"),     # same_source_adjacent with seg2 in look
    (BBB,    5.0, 1.0, "build"),
    (BBB,    6.2, 1.2, "build"),    # SAME SOURCE, ADJACENT -> hard case
    (SINTEL, 3.0, 1.1, "build"),
    (SINTEL, 4.3, 1.4, "build"),    # SAME SOURCE, ADJACENT -> hard case
    (JELLY,  7.0, 1.8, "mid"),
    (BBB,    0.5, 2.0, "mid"),
    (SINTEL, 6.0, 2.2, "mid"),
    (JELLY,  2.5, 3.0, "payoff"),
    (BBB,    7.5, 2.4, "payoff"),
    (SINTEL, 8.0, 1.9, "payoff"),
    (JELLY,  8.0, 2.0, "payoff"),
]

def cut(i, src, ss, dur, tmp):
    out = tmp / f"seg_{i:02d}.mp4"
    n = int(round(dur * FPS))
    subprocess.run([
        "ffmpeg","-y","-loglevel","error","-ss",f"{ss}","-i",str(src),
        "-frames:v",str(n),
        "-vf",f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},setsar=1",
        "-an","-c:v","libx264","-preset","veryfast","-crf","20",
        "-pix_fmt","yuv420p","-x264-params","keyint=12:scenecut=0",
        str(out)],check=True)
    got = subprocess.run(["ffprobe","-v","error","-select_streams","v",
                          "-count_frames","-show_entries","stream=nb_read_frames",
                          "-of","csv=p=0",str(out)],capture_output=True,text=True).stdout.strip()
    return out, int(got), n

def main():
    for p in (JELLY,BBB,SINTEL):
        if not p.exists(): sys.exit(f"missing source {p}")
    tmp = OUT.parent/"_real_tmp"; tmp.mkdir(exist_ok=True)
    paths, truth, frames_acc = [], [], 0
    for i,(src,ss,dur,role) in enumerate(SEGS):
        p,got,want = cut(i,src,ss,dur,tmp)
        if got != want:
            print(f"  warn seg{i}: {got} frames, wanted {want} -> using actual")
        paths.append(p)
        start_f, end_f = frames_acc, frames_acc+got
        truth.append({"index":i,"source":src.name,"in_point":ss,
                      "start":round(start_f/FPS,6),"end":round(end_f/FPS,6),
                      "duration":round(got/FPS,6),"role":role,
                      "start_frame":start_f,"end_frame":end_f})
        frames_acc = end_f
    lst = tmp/"list.txt"; lst.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    silent = tmp/"silent.mp4"
    subprocess.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0",
                    "-i",str(lst),"-c","copy",str(silent)],check=True)
    total = round(frames_acc/FPS,6)
    click="sine=frequency=1600:duration=%.3f:sample_rate=44100"%total
    bed="sine=frequency=98:duration=%.3f:sample_rate=44100"%total
    subprocess.run(["ffmpeg","-y","-loglevel","error",
        "-f","lavfi","-i",click,"-f","lavfi","-i",bed,"-i",str(silent),
        "-filter_complex",
        "[0:a]volume='if(lt(mod(t,0.5),0.04),1.0,0)':eval=frame[c];"
        "[1:a]volume=0.15[b];[c][b]amix=inputs=2:duration=first:normalize=0[a]",
        "-map","2:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","128k",
        "-shortest",str(OUT)],check=True)
    # adjacency hard cases: consecutive segments from the same source clip
    same=[i for i in range(1,len(truth)) if truth[i]["source"]==truth[i-1]["source"]]
    gt={"video":str(OUT),"fps":FPS,"width":W,"height":H,"total_duration":total,
        "n_shots":len(truth),"bpm":120,"beat_seconds":0.5,
        "cut_times":[round(s["end"],6) for s in truth[:-1]],
        "shots":truth,
        "hard_cases":{"same_source_adjacent_cut_indices":same,
                      "same_source_adjacent_cut_times":[round(truth[i-1]["end"],6) for i in same],
                      "note":"cuts between two moments of the same clip: identical "
                             "palette, grain and subject world"}}
    OUT.with_suffix(".truth.json").write_text(json.dumps(gt,indent=2))
    print(f"wrote {OUT} ({total}s, {len(truth)} real-footage shots)")
    print(f"hard cases (same-source adjacent cuts): {same} at {gt['hard_cases']['same_source_adjacent_cut_times']}")

if __name__=="__main__": main()
