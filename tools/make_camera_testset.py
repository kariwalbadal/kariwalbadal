#!/usr/bin/env python3
"""Camera-move validation set: KNOWN moves applied to REAL textured footage.

Flat synthetic colour fields starve goodFeaturesToTrack, so measuring the move
classifier on them measures the fixture rather than the classifier. Applying
known ffmpeg transforms to real footage gives both real texture and exact
ground truth.
"""
import json, subprocess, sys
from pathlib import Path

OUT = Path("work/refs/camera_moves"); OUT.mkdir(parents=True, exist_ok=True)
SRC = Path("work/refs/src_Jellyfish_720_10s_5MB.mp4")   # real camera, real grain
FROZEN = OUT / "_frozen.mp4"   # one real frame held still: real texture, zero
                               # intrinsic motion, so "static" is achievable
FPS, W, H, DUR = 24, 1280, 720, 2.0
N = int(DUR * FPS)

def vf_for(move: str) -> str:
    z = f"scale={W}:{H},fps={FPS}"
    if move == "static":     return z
    if move == "push_in":    return f"{z},zoompan=z='min(1+0.40*on/{N},1.40)':d=1:s={W}x{H}:fps={FPS}"
    if move == "pull_out":   return f"{z},zoompan=z='max(1.40-0.40*on/{N},1.0)':d=1:s={W}x{H}:fps={FPS}"
    if move == "pan_right":  return f"{z},zoompan=z=1.3:x='(iw-iw/1.3)*on/{N}':y='(ih-ih/1.3)/2':d=1:s={W}x{H}:fps={FPS}"
    if move == "pan_left":   return f"{z},zoompan=z=1.3:x='(iw-iw/1.3)*(1-on/{N})':y='(ih-ih/1.3)/2':d=1:s={W}x{H}:fps={FPS}"
    if move == "whip_pan":   return f"{z},zoompan=z=1.5:x='(iw-iw/1.5)*min(1,2.5*on/{N})':y='(ih-ih/1.5)/2':d=1:s={W}x{H}:fps={FPS}"
    if move == "orbit":      return f"{z},rotate=a='0.30*t':fillcolor=black"
    if move == "handheld":   return (f"{z},crop=w={W-60}:h={H-60}:x='30+14*sin(11*t)':y='30+12*cos(8.5*t)',"
                                    f"scale={W}:{H}")
    if move == "tilt_up":    return f"{z},zoompan=z=1.3:y='(ih-ih/1.3)*(1-on/{N})':x='(iw-iw/1.3)/2':d=1:s={W}x{H}:fps={FPS}"
    raise ValueError(move)

MOVES = ["static","push_in","pull_out","pan_right","pan_left","whip_pan","orbit","handheld","tilt_up"]

def build_frozen():
    """Hold one real frame for DUR seconds -- the only honest 'static' baseline.

    The Jellyfish source drifts about -0.8 px/frame on its own, so applying a
    move to live footage contaminates every measurement with that baseline.
    """
    still = OUT / "_still.png"
    subprocess.run(["ffmpeg","-y","-loglevel","error","-ss","3.0","-i",str(SRC),
                    "-frames:v","1","-vf",f"scale={W}:{H}",str(still)],check=True)
    subprocess.run(["ffmpeg","-y","-loglevel","error","-loop","1","-i",str(still),
                    "-frames:v",str(N),"-r",str(FPS),"-c:v","libx264","-preset","veryfast",
                    "-crf","16","-pix_fmt","yuv420p",str(FROZEN)],check=True)
    return FROZEN

def main():
    if not SRC.exists(): sys.exit(f"missing {SRC}")
    base = build_frozen()
    truth=[]
    for mv in MOVES:
        out = OUT / f"{mv}.mp4"
        subprocess.run(["ffmpeg","-y","-loglevel","error","-i",str(base),
                        "-frames:v",str(N),"-vf",vf_for(mv),"-an",
                        "-c:v","libx264","-preset","veryfast","-crf","18",
                        "-pix_fmt","yuv420p",str(out)],check=True)
        truth.append({"move":mv,"path":str(out),"duration":DUR,"fps":FPS})
        print(f"  built {mv}")
    (OUT/"truth.json").write_text(json.dumps({"clips":truth,"source":str(SRC)},indent=2))
    print(f"wrote {len(truth)} clips + truth.json")

if __name__=="__main__": main()
