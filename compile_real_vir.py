#!/usr/bin/env python3
"""Compile a real video into a VIR using YOLO detection.

Usage
-----
    python compile_real_vir.py [VIDEO_PATH] [--out OUT.json] [--model MODEL.pt]

Example
-------
    python compile_real_vir.py test_video.mp4 --out vql_real_vir.json

This is the perception layer of VQL:
  video.mp4  →  YOLO detection + centroid tracking + zone analysis  →  VIR.json
  ※ VIR.json は VQL クエリエンジンに渡す — クエリ実行時にモデルは不使用
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from vql.vir import Zone
from vql.compiler import VIRCompiler

# ── Zone definitions (normalised [0,1] coords) ────────────────────────────────
# Adjust these to match your camera layout.
DEFAULT_ZONES = [
    Zone("左エリア",   [[0.00, 0.10], [0.45, 0.10], [0.45, 0.90], [0.00, 0.90]]),
    Zone("中央エリア", [[0.30, 0.10], [0.70, 0.10], [0.70, 0.90], [0.30, 0.90]]),
    Zone("右エリア",   [[0.55, 0.10], [1.00, 0.10], [1.00, 0.90], [0.55, 0.90]]),
]


def main():
    parser = argparse.ArgumentParser(description="VQL Compiler: video → VIR")
    parser.add_argument("video", nargs="?", default="test_video.mp4",
                        help="Input video file (default: test_video.mp4)")
    parser.add_argument("--out", default="vql_real_vir.json",
                        help="Output VIR JSON path")
    parser.add_argument("--model", default="yolov8n.pt",
                        help="YOLO model weights path")
    parser.add_argument("--sample-every", type=int, default=3,
                        help="Process every N-th frame (default 3)")
    args = parser.parse_args()

    if not Path(args.video).exists():
        print(f"[ERROR] Video not found: {args.video}")
        sys.exit(1)

    print(f"VQL Compiler — real video → VIR")
    print(f"  input  : {args.video}")
    print(f"  model  : {args.model}")
    print(f"  zones  : {[z.id for z in DEFAULT_ZONES]}")
    print()

    compiler = VIRCompiler(model_path=args.model, sample_every=args.sample_every)

    print("  Running YOLO perception pipeline ...", flush=True)
    t0 = time.perf_counter()
    vir = compiler.compile(args.video, zones=DEFAULT_ZONES,
                           source_label=Path(args.video).name)
    elapsed = time.perf_counter() - t0

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(vir.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"  Done ({elapsed:.1f}s) → {args.out}")
    print()
    print(f"  VIR summary:")
    print(f"    source      : {vir.source}")
    print(f"    duration    : {vir.duration_sec:.1f}s  ({vir.fps:.1f} eff.fps after sampling)")
    print(f"    resolution  : {vir.width}×{vir.height}")
    print(f"    entities    : {len(vir.entities)}")
    print(f"    tracks      : {len(vir.tracks)}")
    print(f"    zone_events : {len(vir.zone_events)}")
    print(f"    stay_facts  : {len(vir.stay_facts)}")
    print()

    if vir.zone_events:
        print("  Zone events (first 10):")
        for ev in vir.zone_events[:10]:
            print(f"    {ev.event_type:5s}  {ev.track_id}  zone={ev.zone_id}  t={ev.t_sec:.2f}s")
        if len(vir.zone_events) > 10:
            print(f"    ... ({len(vir.zone_events) - 10} more)")
    else:
        print("  (no zone events detected — try adjusting zone polygons or conf_thresh)")

    print()
    print("  Next: run VQL queries against this VIR")
    print(f"    from vql.executor import VQLExecutor")
    print(f"    from vql.parser import parse_vql")
    print(f"    from vql.vir import VIR")
    print(f"    vir = VIR.from_json(\"{args.out}\")")
    print(f"    result = VQLExecutor().execute(")
    print("      parse_vql('SELECT p FROM VIR(...)'")
    print("      vir)")
    print("    print(result)")
    print(f"    print(result)")


if __name__ == "__main__":
    main()
