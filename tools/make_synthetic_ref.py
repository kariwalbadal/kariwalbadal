#!/usr/bin/env python3
"""Build a synthetic multi-shot 'ad' with EXACT known cut timecodes.

Purpose: ground truth for validating the shot-boundary detector. Ad grammar is
deliberately reproduced: sub-second hook cuts, a build, then long payoff holds.
Two adjacent shots (idx 7,8) are near-identical luma to exercise the
similar-luma hard-cut failure mode called out in the brief.

Audio: 120 BPM click (beat = 0.500s) + tone bed, so beat-alignment of cuts is
also ground-truthable.
"""
import json, subprocess, sys, os
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "work/refs/synthetic_ad.mp4")
OUT.parent.mkdir(parents=True, exist_ok=True)
FPS, W, H = 30, 1280, 720

# (duration, lavfi source, label, camera_move) -- ad pacing: hook -> build -> payoff
SHOTS = [
    (0.5, "color=c=0xE8402A",                      "HOOK-1",  "whip_pan"),
    (0.4, "color=c=0x121212",                      "HOOK-2",  "static"),
    (0.6, "testsrc2",                              "HOOK-3",  "push_in"),
    (0.5, "color=c=0xF5C518",                      "HOOK-4",  "whip_pan"),
    (0.8, "color=c=0x0B6E4F",                      "HOOK-5",  "pull_out"),
    (1.2, "smptebars",                             "BUILD-1", "static"),
    (1.0, "color=c=0x1F3A93",                      "BUILD-2", "orbit"),
    (1.5, "color=c=0x808080",                      "BUILD-3", "push_in"),   # similar luma pair
    (1.1, "color=c=0x828282",                      "BUILD-4", "static"),    # similar luma pair
    (2.0, "rgbtestsrc",                            "MID-1",   "orbit"),
    (1.8, "color=c=0x6A2C70",                      "MID-2",   "handheld"),
    (2.2, "color=c=0xEDEDED",                      "MID-3",   "top_down"),
    (3.0, "testsrc2",                              "PAY-1",   "push_in"),
    (2.5, "color=c=0x0A1A2F",                      "PAY-2",   "orbit"),
    (4.0, "color=c=0xD94F2B",                      "PAY-3",   "static"),
    (4.4, "color=c=0x101820",                      "PAY-4",   "pull_out"),
]

def build_shot(i, dur, src, label, move, tmp):
    path = tmp / f"shot_{i:02d}.mp4"
    base = f"{src}=size={W}x{H}:rate={FPS}:duration={dur}" if src in ("testsrc2","smptebars","rgbtestsrc") \
           else f"{src}:size={W}x{H}:rate={FPS}:duration={dur}"
    # In-shot camera motion so per-shot motion classification has real signal.
    if move == "push_in":   zoom = f",zoompan=z='min(1+0.35*on/{max(1,int(dur*FPS))},1.35)':d=1:s={W}x{H}:fps={FPS}"
    elif move == "pull_out":zoom = f",zoompan=z='max(1.35-0.35*on/{max(1,int(dur*FPS))},1.0)':d=1:s={W}x{H}:fps={FPS}"
    elif move == "whip_pan":zoom = f",zoompan=z=1.25:x='iw*0.6*on/{max(1,int(dur*FPS))}':d=1:s={W}x{H}:fps={FPS}"
    elif move == "orbit":   zoom = f",rotate=a='0.25*t':fillcolor=black"
    elif move == "handheld":zoom = f",crop=w={W-40}:h={H-40}:x='20+8*sin(12*t)':y='20+8*cos(9*t)',scale={W}:{H}"
    elif move == "top_down":zoom = f",rotate=a='PI/2':fillcolor=black"
    else:                   zoom = ""
    # Distinct geometry per shot so even same-colour shots differ structurally.
    geo = f",drawbox=x={80+i*55}:y={120+(i%4)*90}:w=260:h=180:color=white@0.55:t=fill"
    txt = (f",drawtext=text='{label}':fontcolor=white:fontsize=54:x=40:y=40"
           f":box=1:boxcolor=black@0.5:boxborderw=12")
    vf = f"{base}{geo}{zoom}{txt},format=yuv420p"
    subprocess.run(["ffmpeg","-y","-loglevel","error","-f","lavfi","-i",vf,
                    "-t",f"{dur}","-r",str(FPS),"-c:v","libx264","-preset","veryfast",
                    "-pix_fmt","yuv420p",str(path)], check=True)
    return path

def main():
    tmp = OUT.parent / "_synth_tmp"; tmp.mkdir(exist_ok=True)
    paths, truth, t = [], [], 0.0
    for i,(dur,src,label,move) in enumerate(SHOTS):
        paths.append(build_shot(i,dur,src,label,move,tmp))
        truth.append({"index":i,"label":label,"start":round(t,3),"end":round(t+dur,3),
                      "duration":dur,"camera_move":move,"source":src})
        t += dur
    total = round(t,3)
    lst = tmp/"list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    silent = tmp/"silent.mp4"
    subprocess.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0","-i",str(lst),
                    "-c","copy",str(silent)],check=True)
    # 120 BPM click (0.5s beat) + tone bed -> cuts on multiples of 0.5 are beat-locked
    click = "sine=frequency=1800:duration=%.3f:sample_rate=44100" % total
    bed   = "sine=frequency=110:duration=%.3f:sample_rate=44100" % total
    subprocess.run(["ffmpeg","-y","-loglevel","error",
        "-f","lavfi","-i",click,"-f","lavfi","-i",bed,"-i",str(silent),
        "-filter_complex",
        f"[0:a]volume='if(lt(mod(t,0.5),0.045),1.0,0)':eval=frame[clk];"
        f"[1:a]volume=0.18[bd];[clk][bd]amix=inputs=2:duration=first:normalize=0[a]",
        "-map","2:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","128k",
        "-shortest",str(OUT)],check=True)
    gt = OUT.with_suffix(".truth.json")
    gt.write_text(json.dumps({
        "video":str(OUT),"fps":FPS,"width":W,"height":H,"total_duration":total,
        "n_shots":len(SHOTS),"bpm":120,"beat_seconds":0.5,
        "cut_times":[round(s["end"],3) for s in truth[:-1]],
        "shots":truth,
        "hard_cases":{"similar_luma_pair":[7,8],
                      "note":"shots 7,8 are 0x808080 vs 0x828282 - near-identical luma"}
    },indent=2))
    print(f"wrote {OUT} ({total}s, {len(SHOTS)} shots)")
    print(f"wrote {gt}")

if __name__=="__main__": main()
