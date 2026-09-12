import json, time, sys
from adpipe.decompose.pipeline import decompose
for name, vid in (("real_ad","work/refs/real_composite_ad.mp4"),
                  ("synth_ad","work/refs/synthetic_ad.mp4")):
    truth=json.load(open(vid.replace(".mp4",".truth.json")))
    t0=time.time()
    edl=decompose(vid, f"work/out/{name}", truth_cuts=truth["cut_times"])
    a=edl["provenance"]["accuracy_vs_truth"]
    print(f"[{name}] {time.time()-t0:.0f}s shots={edl['timing']['n_shots']} "
          f"F1={a['f1']:.3f} P={a['precision']:.3f} R={a['recall']:.3f} "
          f"rhythm={edl['timing']['rhythm_profile']} "
          f"beat_align={edl['audio']['cut_beat_alignment']} "
          f"ocr={len(edl['onscreen_text'])}", flush=True)
    moves={}
    for s in edl["shots"]: moves[s["camera_move"]]=moves.get(s["camera_move"],0)+1
    print(f"[{name}] camera moves: {moves}", flush=True)
print("DONE")
