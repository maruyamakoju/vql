"""Synthetic surveillance demo data generator.

Generates a realistic 2-hour entrance-camera VIR:
  - 347 person tracks distributed across 4 zones
  - Zone layout: A区域, B区域, 受付, エレベーター前
  - Hard-coded demo targets: 4 people enter A区域 after 14:00 and stay < 5 min
    (exactly the result set for the flagship VQL demo query)

Usage
-----
    from vql.demo_data import generate_surveillance_vir
    vir = generate_surveillance_vir()
    vir.to_json("vql_demo_vir.json")
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from vql.vir import BBox, Entity, StayFact, Track, TrackPosition, VIR, Zone, ZoneEvent


# ── Zone definitions ─────────────────────────────────────────────────────────

SURVEILLANCE_ZONES = [
    Zone("A区域",          [[0.28, 0.08], [0.72, 0.08], [0.72, 0.48], [0.28, 0.48]]),
    Zone("B区域",          [[0.02, 0.52], [0.45, 0.52], [0.45, 0.98], [0.02, 0.98]]),
    Zone("受付",           [[0.55, 0.52], [0.98, 0.52], [0.98, 0.98], [0.55, 0.98]]),
    Zone("エレベーター前", [[0.30, 0.52], [0.70, 0.52], [0.70, 0.98], [0.30, 0.98]]),
]

# Zone → typical visit durations (seconds): (min, max)
_ZONE_DWELL = {
    "A区域":          (90, 1800),
    "B区域":          (300, 5400),
    "受付":           (120, 2400),
    "エレベーター前": (20,  300),
}

# ── Hard-coded demo targets ──────────────────────────────────────────────────
# These tracks MUST appear in the result of:
#   SELECT person FROM VIR(...) WHERE ENTERS(person, zone("A区域"),
#     time_range(from="14:00:00", to="15:00:00")) AND DURATION(...) < 5min

_DEMO_TARGETS = [
    {"track_id": "track_047", "entity_id": "entity_047",
     "zone": "A区域", "enter_t": 14*3600 + 2*60 + 11,   "exit_t": 14*3600 + 4*60 + 33},   # 2m 22s
    {"track_id": "track_083", "entity_id": "entity_083",
     "zone": "A区域", "enter_t": 14*3600 + 8*60 + 45,   "exit_t": 14*3600 + 11*60 + 20},  # 2m 35s
    {"track_id": "track_126", "entity_id": "entity_126",
     "zone": "A区域", "enter_t": 14*3600 + 23*60 + 7,   "exit_t": 14*3600 + 26*60 + 52},  # 3m 45s
    {"track_id": "track_194", "entity_id": "entity_194",
     "zone": "A区域", "enter_t": 14*3600 + 51*60 + 33,  "exit_t": 14*3600 + 54*60 + 18},  # 2m 45s
]
_DEMO_TARGET_IDS = {t["track_id"] for t in _DEMO_TARGETS}

# Sequence targets: visit 受付 first (< 3 min), then A区域
_SEQ_TARGETS = [
    {"track_id": "track_021", "entity_id": "entity_021",
     "zone_visits": [
         {"zone": "受付",  "enter_t": 9*3600+14*60+5,  "exit_t": 9*3600+16*60+8},
         {"zone": "A区域", "enter_t": 9*3600+18*60+30, "exit_t": 9*3600+47*60+22},
     ]},
    {"track_id": "track_155", "entity_id": "entity_155",
     "zone_visits": [
         {"zone": "受付",  "enter_t": 10*3600+42*60+8,  "exit_t": 10*3600+44*60+0},
         {"zone": "A区域", "enter_t": 10*3600+46*60+30, "exit_t": 10*3600+59*60+44},
     ]},
    {"track_id": "track_267", "entity_id": "entity_267",
     "zone_visits": [
         {"zone": "受付",  "enter_t": 11*3600+31*60+30, "exit_t": 11*3600+33*60+0},
         {"zone": "A区域", "enter_t": 11*3600+35*60+0,  "exit_t": 11*3600+47*60+15},
     ]},
]
_SEQ_TARGET_IDS = {t["track_id"] for t in _SEQ_TARGETS}

# Long-stay targets: > 30 min in B区域, between 13:00-18:00
_LONG_STAY_TARGETS = [
    {"track_id": "track_231", "entity_id": "entity_231",
     "zone": "B区域", "enter_t": 13*3600+5*60+22,  "exit_t": 14*3600+47*60+38},  # 1h 42m
    {"track_id": "track_312", "entity_id": "entity_312",
     "zone": "B区域", "enter_t": 15*3600+22*60+4,  "exit_t": 16*3600+1*60+44},   # 39m
]
_LONG_STAY_IDS = {t["track_id"] for t in _LONG_STAY_TARGETS}
_ALL_SPECIAL_IDS = _DEMO_TARGET_IDS | _SEQ_TARGET_IDS | _LONG_STAY_IDS


# ── Helper ───────────────────────────────────────────────────────────────────

def _zone_centre(zone: Zone) -> tuple[float, float]:
    xs = [p[0] for p in zone.polygon]
    ys = [p[1] for p in zone.polygon]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _gen_positions(
    track_id: str, appear_t: float, disappear_t: float,
    fps: float, cx: float, cy: float,
    rng: random.Random,
) -> list[TrackPosition]:
    """Generate a simple linear-ish trajectory."""
    n_frames = max(2, int((disappear_t - appear_t) * fps))
    # Limit to 30 stored positions to keep VIR compact
    step = max(1, n_frames // 30)
    positions = []
    dx = rng.uniform(-0.005, 0.005)
    dy = rng.uniform(-0.002, 0.002)
    x, y = cx + rng.uniform(-0.05, 0.05), cy + rng.uniform(-0.05, 0.05)
    w = rng.uniform(0.04, 0.08)
    h = rng.uniform(0.10, 0.18)
    for fi in range(0, n_frames, step):
        t = appear_t + fi / fps
        x = max(0.01, min(0.99 - w, x + dx + rng.gauss(0, 0.002)))
        y = max(0.01, min(0.99 - h, y + dy + rng.gauss(0, 0.001)))
        positions.append(TrackPosition(
            frame=int(appear_t * fps) + fi,
            t_sec=round(t, 3),
            bbox=BBox(x=round(x, 4), y=round(y, 4), w=round(w, 4), h=round(h, 4)),
            conf=round(rng.uniform(0.82, 0.99), 2),
        ))
    return positions


# ── Main generator ───────────────────────────────────────────────────────────

def generate_surveillance_vir(
    source: str = "entrance_cam_2h.mp4",
    n_persons: int = 347,
    fps: float = 15.0,
    duration_hours: float = 2.0,
    seed: int = 42,
) -> VIR:
    """Generate a 2-hour entrance-camera VIR (surveillance domain).

    Parameters
    ----------
    source:         video filename label (not a real file — demo only)
    n_persons:      total number of person tracks to generate
    fps:            frames per second
    duration_hours: total video length in hours
    seed:           random seed (deterministic output)
    """
    rng = random.Random(seed)
    duration_sec = duration_hours * 3600.0

    entities: list[Entity]     = []
    tracks:   list[Track]      = []
    zone_events: list[ZoneEvent]  = []
    stay_facts:  list[StayFact]   = []

    zone_map = {z.id: z for z in SURVEILLANCE_ZONES}

    # Zone distribution (rough): 受付 most visited, then A/B/エレベーター
    zone_weights = ["受付"] * 5 + ["A区域"] * 3 + ["B区域"] * 3 + ["エレベーター前"] * 2

    # Build lookup maps for special tracks
    target_lookup   = {t["track_id"]: t for t in _DEMO_TARGETS}
    seq_lookup       = {t["track_id"]: t for t in _SEQ_TARGETS}
    long_stay_lookup = {t["track_id"]: t for t in _LONG_STAY_TARGETS}

    for i in range(1, n_persons + 1):
        tid  = f"track_{i:03d}"
        eid  = f"entity_{i:03d}"

        entities.append(Entity(id=eid, type="person"))

        if tid in seq_lookup:
            # ── Sequence target: visits multiple zones ────────────────────
            tgt = seq_lookup[tid]
            all_enter = tgt["zone_visits"][0]["enter_t"]
            all_exit  = tgt["zone_visits"][-1]["exit_t"]
            zone_0    = zone_map[tgt["zone_visits"][0]["zone"]]
            cx, cy    = _zone_centre(zone_0)
            positions = _gen_positions(tid, all_enter - 5, all_exit + 5, fps, cx, cy, rng)
            tracks.append(Track(id=tid, entity_id=eid, positions=positions))
            for visit in tgt["zone_visits"]:
                z_id, enter, exit_ = visit["zone"], visit["enter_t"], visit["exit_t"]
                zone_events.append(ZoneEvent(
                    track_id=tid, zone_id=z_id, event_type="ENTER",
                    t_sec=round(enter, 2), frame=int(enter * fps),
                ))
                zone_events.append(ZoneEvent(
                    track_id=tid, zone_id=z_id, event_type="EXIT",
                    t_sec=round(exit_, 2), frame=int(exit_ * fps),
                ))
                stay_facts.append(StayFact(
                    track_id=tid, zone_id=z_id,
                    enter_t=round(enter, 2), exit_t=round(exit_, 2),
                    duration_sec=round(exit_ - enter, 2),
                ))
            continue

        if tid in long_stay_lookup:
            # ── Long-stay target ──────────────────────────────────────────
            tgt = long_stay_lookup[tid]
            z_id, enter, exit_ = tgt["zone"], tgt["enter_t"], tgt["exit_t"]
            zone = zone_map[z_id]; cx, cy = _zone_centre(zone)
            positions = _gen_positions(tid, enter - 5, exit_ + 5, fps, cx, cy, rng)
            tracks.append(Track(id=tid, entity_id=eid, positions=positions))
            for et, ext, ev in [(enter, exit_, "ENTER"), (exit_, None, "EXIT")]:
                if ev == "ENTER":
                    zone_events.append(ZoneEvent(track_id=tid, zone_id=z_id,
                        event_type="ENTER", t_sec=round(enter, 2), frame=int(enter*fps)))
                    zone_events.append(ZoneEvent(track_id=tid, zone_id=z_id,
                        event_type="EXIT", t_sec=round(exit_, 2), frame=int(exit_*fps)))
                    stay_facts.append(StayFact(
                        track_id=tid, zone_id=z_id,
                        enter_t=round(enter, 2), exit_t=round(exit_, 2),
                        duration_sec=round(exit_ - enter, 2),
                    ))
                    break
            continue

        if tid in target_lookup:
            # ── Hard-coded demo target ────────────────────────────────────
            tgt   = target_lookup[tid]
            z_id  = tgt["zone"]
            enter = tgt["enter_t"]
            exit_ = tgt["exit_t"]
            dur   = exit_ - enter
            zone  = zone_map[z_id]
            cx, cy = _zone_centre(zone)

            positions = _gen_positions(tid, enter - 10, exit_ + 10, fps, cx, cy, rng)
            tracks.append(Track(id=tid, entity_id=eid, positions=positions))

            zone_events.append(ZoneEvent(
                track_id=tid, zone_id=z_id, event_type="ENTER",
                t_sec=round(enter, 2), frame=int(enter * fps),
            ))
            zone_events.append(ZoneEvent(
                track_id=tid, zone_id=z_id, event_type="EXIT",
                t_sec=round(exit_, 2), frame=int(exit_ * fps),
            ))
            stay_facts.append(StayFact(
                track_id=tid, zone_id=z_id,
                enter_t=round(enter, 2), exit_t=round(exit_, 2),
                duration_sec=round(dur, 2),
            ))

        else:
            # ── Background person ────────────────────────────────────────
            # Appear at a random time, spend time in a randomly chosen zone
            appear_t  = rng.uniform(0, duration_sec - 120)
            z_id      = rng.choice(zone_weights)
            zone      = zone_map[z_id]
            dmin, dmax = _ZONE_DWELL[z_id]
            dwell     = rng.uniform(dmin, dmax)

            # Arrive a bit before entering the zone, leave a bit after
            enter_t = appear_t + rng.uniform(15, 60)
            exit_t  = min(duration_sec - 5, enter_t + dwell)
            disappear_t = min(duration_sec, exit_t + rng.uniform(10, 60))

            cx, cy = _zone_centre(zone)
            positions = _gen_positions(tid, appear_t, disappear_t, fps, cx, cy, rng)
            tracks.append(Track(id=tid, entity_id=eid, positions=positions))

            zone_events.append(ZoneEvent(
                track_id=tid, zone_id=z_id, event_type="ENTER",
                t_sec=round(enter_t, 2), frame=int(enter_t * fps),
            ))
            zone_events.append(ZoneEvent(
                track_id=tid, zone_id=z_id, event_type="EXIT",
                t_sec=round(exit_t, 2), frame=int(exit_t * fps),
            ))
            stay_facts.append(StayFact(
                track_id=tid, zone_id=z_id,
                enter_t=round(enter_t, 2), exit_t=round(exit_t, 2),
                duration_sec=round(exit_t - enter_t, 2),
            ))

    # Sort events chronologically
    zone_events.sort(key=lambda e: e.t_sec)

    return VIR(
        source=source,
        fps=fps,
        duration_sec=duration_sec,
        width=1920,
        height=1080,
        entities=entities,
        tracks=tracks,
        zones=SURVEILLANCE_ZONES,
        zone_events=zone_events,
        stay_facts=stay_facts,
    )


# ── Synthetic video stub (for demo_vql.py compatibility) ────────────────────

def generate_synthetic_video(output_path: str, width: int = 640, height: int = 480,
                              fps: int = 15, duration_sec: int = 30) -> None:
    """Write a minimal surveillance-look MP4 using OpenCV (if available)."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        # Create a placeholder file if OpenCV is unavailable
        Path(output_path).write_bytes(b"")
        return

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    rng = random.Random(0)

    for frame_idx in range(fps * duration_sec):
        t = frame_idx / fps
        img = np.zeros((height, width, 3), dtype=np.uint8)
        img[:] = (12, 14, 22)   # dark surveillance look

        # Draw faint grid
        for gx in range(0, width, 40):
            cv2.line(img, (gx, 0), (gx, height), (25, 28, 45), 1)
        for gy in range(0, height, 30):
            cv2.line(img, (0, gy), (width, gy), (25, 28, 45), 1)

        # Draw zone outlines
        zone_colours = [(0, 182, 212), (99, 99, 241), (34, 197, 94), (249, 115, 22)]
        for z, col in zip(SURVEILLANCE_ZONES, zone_colours):
            pts = [(int(p[0]*width), int(p[1]*height)) for p in z.polygon]
            cv2.polylines(img, [np.array(pts, np.int32)], True, col, 1)
            cx = int(sum(p[0] for p in pts) / len(pts))
            cy = int(sum(p[1] for p in pts) / len(pts))
            cv2.putText(img, z.id, (cx - 20, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.35, col, 1)

        # Overlay timestamp
        hms = f"{int(t//3600):02d}:{int((t%3600)//60):02d}:{t%60:05.2f}"
        cv2.putText(img, hms, (8, height - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        out.write(img)

    out.release()


def generate_synthetic_vir(video_path: str) -> VIR:
    """Convenience wrapper for demo_vql.py compatibility."""
    return generate_surveillance_vir(source=video_path)
