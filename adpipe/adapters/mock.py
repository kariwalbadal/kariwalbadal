"""Local mock generator: runs the whole pipeline with zero spend.

Not a stub for tests only. It is the reason the assembly half can be proven
end-to-end -- cut rhythm, transitions, ramps, grade, audio -- while the
generative half is unfunded or unavailable. It renders a real, distinct clip
per shot honouring the requested duration and the EDL's camera move, so the
assembled output has genuinely different shots to cut between.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import subprocess
import time

from .base import BaseAdapter, GenerationRequest, GenerationResult

PALETTE = ["0x1b3a5c", "0x8c2f1f", "0x2f6b4f", "0xb0862a", "0x4a2f6b",
           "0x1f5f6b", "0x7a2f4f", "0x3f4a2f", "0x5c2b1b", "0x2b2b3c"]


class MockAdapter(BaseAdapter):
    name = "mock"
    allowed_durations = None
    min_duration = 0.1
    max_duration = 30.0
    allowed_resolutions = ("720p", "1080p")
    usd_per_second = 0.0

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30):
        self.width, self.height, self.fps = width, height, fps

    def submit(self, req: GenerationRequest, out_dir: str) -> GenerationResult:
        self.validate(req)
        t0 = time.time()
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        dst = out / f"mock_shot{req.shot_index:03d}_{req.request_id}.mp4"

        # Deterministic per-shot look so repeat runs are comparable.
        seed = int(hashlib.sha256(
            f"{req.shot_index}:{req.prompt[:80]}".encode()).hexdigest()[:8], 16)
        colour = PALETTE[seed % len(PALETTE)]
        move = (req.extra or {}).get("camera_move", "static")
        n = max(1, int(round(req.duration_s * self.fps)))

        base = (f"color=c={colour}:size={self.width}x{self.height}:"
                f"rate={self.fps}:duration={req.duration_s:.3f}")
        # Texture, so optical-flow scoring of the OUTPUT is meaningful and the
        # shots are visually distinguishable at a cut.
        chain = [f"noise=alls=14:allf=t+u",
                 f"drawbox=x={80+(seed%400)}:y={60+(seed%240)}:w=340:h=240:"
                 f"color=white@0.30:t=fill",
                 f"drawbox=x={140+(seed%360)}:y={120+(seed%200)}:w=170:h=170:"
                 f"color=black@0.35:t=fill"]
        if move == "push_in":
            chain.append(f"zoompan=z='min(1+0.35*on/{n},1.35)':d=1:"
                         f"s={self.width}x{self.height}:fps={self.fps}")
        elif move == "pull_out":
            chain.append(f"zoompan=z='max(1.35-0.35*on/{n},1.0)':d=1:"
                         f"s={self.width}x{self.height}:fps={self.fps}")
        elif move in ("whip_pan", "pan_left", "pan_right"):
            chain.append(f"zoompan=z=1.35:x='(iw-iw/1.35)*on/{n}':"
                         f"y='(ih-ih/1.35)/2':d=1:s={self.width}x{self.height}:fps={self.fps}")
        elif move == "orbit":
            chain.append("rotate=a='0.35*t':fillcolor=black")
        elif move == "handheld":
            chain.append(f"crop=w={self.width-60}:h={self.height-60}:"
                         f"x='30+16*sin(11*t)':y='30+13*cos(8*t)',"
                         f"scale={self.width}:{self.height}")
        chain.append(f"drawtext=text='SHOT {req.shot_index+1}':fontcolor=white@0.85:"
                     f"fontsize=44:x=40:y=h-90:box=1:boxcolor=black@0.45:boxborderw=10")
        chain.append("format=yuv420p")

        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", f"{base},{','.join(chain)}", "-frames:v", str(n),
               "-r", str(self.fps), "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "20", "-pix_fmt", "yuv420p", str(dst)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return GenerationResult(request_id=req.request_id, shot_index=req.shot_index,
                                    status="failed", error=proc.stderr[-800:],
                                    latency_s=round(time.time()-t0, 3))
        return GenerationResult(request_id=req.request_id, shot_index=req.shot_index,
                                status="succeeded", output_path=str(dst),
                                cost_usd=0.0, latency_s=round(time.time()-t0, 3),
                                raw={"adapter": "mock", "camera_move": move,
                                     "colour": colour})
