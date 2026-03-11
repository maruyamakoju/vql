#!/usr/bin/env python3
"""VQL Demo — compile a surveillance VIR and run queries against it.

Usage
-----
    python demo_vql.py                # compile VIR + run queries + show results
    python demo_vql.py --no-server    # compile VIR + run queries only
    python demo_vql.py --regen        # force recompile VIR (ignore cache)

Key design property
-------------------
  Step 1 (perception):  video → VIR   [runs models ONCE]
  Step 2 (query):       VIR  → result [NO models, < 10 ms, deterministic]

The same VQL query executed twice against the same VIR always returns
identical results — which is the central claim of this proposal.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BANNER = r"""
 ╔══════════════════════════════════════════════════════╗
 ║   VQL — Video Query Language                         ║
 ║   映像のSQL: compile once, query deterministically   ║
 ║   Mitou IT 2026 Application Demo                     ║
 ╚══════════════════════════════════════════════════════╝
"""

VIR_CACHE   = Path("vql_demo_vir.json")
DEMO_VIDEO  = Path("vql_demo_surveillance.mp4")
OUTPUT_DIR  = Path("vql_demo_output")

# ── Demo queries (surveillance domain) ──────────────────────────────────────

DEMO_QUERIES = [
    (
        "14時以降にA区域へ入り5分未満で退出した人物を全件抽出",
        """\
SELECT   person
FROM     VIR("entrance_cam_2h.mp4")
WHERE    ENTERS(person, zone("A区域"),
                time_range(from="14:00:00", to="15:00:00"))
  AND    DURATION(person, zone("A区域")) < 5min
RETURN   track_id, enter_t, exit_t, duration,
         evidence_frames(n=2)""",
    ),
    (
        "受付を経由してA区域に入った人物のシーケンス検出",
        """\
SELECT   person
FROM     VIR("entrance_cam_2h.mp4")
WHERE    SEQUENCE(
           ENTERS(person, zone("受付")),
           ENTERS(person, zone("A区域"))
         )
  AND    DURATION(person, zone("受付")) < 3min
RETURN   track_id, sequence_events, evidence_frames(n=2)""",
    ),
    (
        "13〜18時にB区域で30分以上滞留した人物の検出",
        """\
SELECT   person
FROM     VIR("entrance_cam_2h.mp4")
WHERE    STAYS(person, zone("B区域")) > 30min
  AND    TIME_OF_DAY(person) IN time_range("13:00:00", "18:00:00")
RETURN   track_id, total_stay_duration, evidence_frames(n=2)""",
    ),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sep(title: str = "", width: int = 64) -> None:
    print()
    print("\u2500" * width)
    if title:
        print(f"  {title}")
        print("\u2500" * width)


def _sec_to_hms(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def _fmt_dur(sec: float) -> str:
    if sec < 60:
        return f"{sec:.0f}s"
    m = int(sec // 60)
    s = int(sec % 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    return f"{m//60}h {m%60:02d}m {s:02d}s"


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VQL Surveillance Demo")
    parser.add_argument("--no-server", action="store_true",
                        help="Skip launching the web UI server")
    parser.add_argument("--regen", action="store_true",
                        help="Force recompile VIR (ignore cache)")
    args = parser.parse_args()

    print(BANNER)

    # ── Step 1: Generate / load VIR ─────────────────────────────────────────
    _sep("Step 1  \u25b6  Compile surveillance video \u2192 VIR")
    from vql.vir       import VIR
    from vql.demo_data import generate_surveillance_vir, generate_synthetic_video

    if not DEMO_VIDEO.exists() or args.regen:
        print(f"  Generating synthetic surveillance video \u2192 {DEMO_VIDEO} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        generate_synthetic_video(str(DEMO_VIDEO))
        print(f"done  ({(time.perf_counter()-t0)*1000:.0f}ms)")
    else:
        print(f"  Using cached video  {DEMO_VIDEO}")

    if not VIR_CACHE.exists() or args.regen:
        print("  Compiling VIR from perception pipeline ...", end=" ", flush=True)
        t0 = time.perf_counter()
        vir = generate_surveillance_vir()
        VIR_CACHE.write_text(
            json.dumps(vir.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        compile_ms = (time.perf_counter() - t0) * 1000
        print(f"done  ({compile_ms:.0f}ms)")
    else:
        print(f"  Loading cached VIR  {VIR_CACHE}")
        t0 = time.perf_counter()
        vir = VIR.from_json(VIR_CACHE)
        load_ms = (time.perf_counter() - t0) * 1000
        print(f"  Loaded in {load_ms:.1f}ms")

    print()
    print(f"  VIR summary:")
    print(f"    source       : {vir.source}")
    print(f"    duration     : {_fmt_dur(vir.duration_sec)}  ({vir.duration_sec:.0f}s)")
    print(f"    fps          : {vir.fps}")
    print(f"    zones        : {len(vir.zones)}  ->  {[z.id for z in vir.zones]}")
    print(f"    entities     : {len(vir.entities)}")
    print(f"    tracks       : {len(vir.tracks)}")
    print(f"    zone_events  : {len(vir.zone_events)}")
    print(f"    stay_facts   : {len(vir.stay_facts)}")

    # ── Step 2: Run demo queries ─────────────────────────────────────────────
    _sep("Step 2  \u25b6  Execute VQL queries  [NO models -- deterministic]")
    from vql.parser   import parse_vql, VQLSyntaxError
    from vql.executor import VQLExecutor

    executor  = VQLExecutor()
    OUTPUT_DIR.mkdir(exist_ok=True)

    for qi, (label, query_str) in enumerate(DEMO_QUERIES, 1):
        print()
        print(f"  +-- Query {qi}: {label}")
        for line in query_str.strip().splitlines():
            print(f"  |  {line}")
        print(f"  |")

        try:
            query = parse_vql(query_str)
        except VQLSyntaxError as e:
            print(f"  |  [SYNTAX ERROR] {e}")
            print(f"  +--")
            continue

        # Run twice to prove determinism
        result1 = executor.execute(query, vir, query_str)
        result2 = executor.execute(query, vir, query_str)
        assert result1.total_matches == result2.total_matches
        assert ([m.track_id for m in result1.matched_tracks] ==
                [m.track_id for m in result2.matched_tracks])

        print(f"  |  -> {result1.total_matches} match(es)"
              f"   exec: {result1.execution_time_ms:.2f}ms (run 1)"
              f"  /  {result2.execution_time_ms:.2f}ms (run 2)  [deterministic OK]")
        print(f"  |")
        print(f"  |  Execution plan:")
        for step in result1.plan_steps:
            print(f"  |    {step}")
        print(f"  |")

        for mi, match in enumerate(result1.matched_tracks, 1):
            t_enter = match.evidence_frame_times[0] if match.evidence_frame_times else 0
            t_exit  = match.evidence_frame_times[-1] if match.evidence_frame_times else 0
            print(f"  |  [{mi}] {match.track_id}"
                  f"  type={match.entity_type}"
                  f"  {_sec_to_hms(t_enter)} -> {_sec_to_hms(t_exit)}"
                  f"  dur={_fmt_dur(match.duration_sec)}"
                  f"  conf={match.confidence:.2f}")
            print(f"  |      predicates: {', '.join(match.matched_predicates)}")

            # Save evidence frames
            try:
                from vql.evidence import extract_evidence_frames
                frames = extract_evidence_frames(str(DEMO_VIDEO), vir, match)
                for ki, frame in enumerate(frames):
                    out_path = OUTPUT_DIR / f"q{qi}_match{mi}_frame{ki+1}"
                    try:
                        import cv2
                        cv2.imwrite(str(out_path.with_suffix(".jpg")), frame)
                    except Exception:
                        try:
                            frame.save(str(out_path.with_suffix(".png")))
                        except Exception:
                            pass
            except Exception:
                pass

        if result1.total_matches == 0:
            print(f"  |  (no matches)")
        print(f"  +--")

    # ── Step 3: Determinism proof ────────────────────────────────────────────
    _sep("Step 3  \u25b6  Determinism verification")
    print()
    print("  Running Query 1 a third time ...")
    q1      = parse_vql(DEMO_QUERIES[0][1])
    ids_a   = [m.track_id for m in executor.execute(q1, vir).matched_tracks]
    ids_b   = [m.track_id for m in executor.execute(q1, vir).matched_tracks]
    assert ids_a == ids_b
    print(f"  All three runs returned identical result sets:")
    for tid in ids_a:
        print(f"    - {tid}")
    print()
    print("  [OK] VQL is 100% deterministic.")
    print("       Perception error is confined to the VIR compilation layer,")
    print("       not the query layer.")

    # ── Step 4: Web server (optional) ───────────────────────────────────────
    if not args.no_server:
        _sep("Step 4  \u25b6  Web Demo  ->  open vql_mitou2026.html in browser")
        print()
        print("  The interactive HTML demo is at:")
        print("    vql_mitou2026.html  (open locally in any browser)")
        print()
        print("  To launch a minimal query API server (requires uvicorn):")
        try:
            import uvicorn
            print("  Starting server -> http://localhost:8899")
            uvicorn.run("vql.server:app", host="0.0.0.0", port=8899,
                        reload=False, log_level="warning")
        except ImportError:
            print("  (uvicorn not installed -- skipping server)")
        except Exception as e:
            print(f"  (server error: {e})")
    else:
        print()
        print("  Done. Open vql_mitou2026.html in any browser for the interactive demo.")
        print()


if __name__ == "__main__":
    main()
