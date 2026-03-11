"""VQL Compiler — real video → VIR using YOLO detection.

This is the "perception layer": runs YOLO on each sampled frame,
applies centroid tracking, detects zone ENTER/EXIT events, and
writes a VIR JSON that the VQL query engine can query.

Usage
-----
    from vql.compiler import VIRCompiler
    vir = VIRCompiler(model_path="yolov8n.pt").compile("video.mp4", zones=ZONES)
    vir.to_json("out.json")
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from vql.vir import BBox, Entity, StayFact, Track, TrackPosition, VIR, Zone, ZoneEvent


# ── Tracker ───────────────────────────────────────────────────────────────────

@dataclass
class _ActiveTrack:
    tid: str
    eid: str
    positions: list[TrackPosition] = field(default_factory=list)
    last_cx: float = 0.0
    last_cy: float = 0.0
    in_zones: set[str] = field(default_factory=set)
    first_t: float = 0.0
    last_t: float = 0.0
    lost_frames: int = 0


def _iou(a: tuple, b: tuple) -> float:
    """Intersection over Union for (x,y,w,h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx); iy = max(ay, by)
    ix2 = min(ax+aw, bx+bw); iy2 = min(ay+ah, by+bh)
    if ix2 <= ix or iy2 <= iy:
        return 0.0
    inter = (ix2 - ix) * (iy2 - iy)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _dist(a: tuple, b: tuple) -> float:
    return math.hypot(a[0]-b[0], a[1]-b[1])


def _point_in_polygon(px: float, py: float, poly: list) -> bool:
    """Ray-casting test."""
    n = len(poly); inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi:
            inside = not inside
        j = i
    return inside


# ── Compiler ──────────────────────────────────────────────────────────────────

class VIRCompiler:
    """Compile a video into a VIR using YOLO object detection.

    Parameters
    ----------
    model_path:   path to YOLO weights (e.g. "yolov8n.pt")
    sample_every: process every N-th frame (default 3 → ~10 fps for 30fps video)
    classes:      YOLO class IDs to track (default [0] = person)
    conf_thresh:  detection confidence threshold
    iou_thresh:   IOU threshold for track association
    max_lost:     frames before a track is removed
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        sample_every: int = 3,
        classes: list[int] | None = None,
        conf_thresh: float = 0.35,
        iou_thresh: float = 0.30,
        max_lost: int = 10,
    ):
        from ultralytics import YOLO
        self._model = YOLO(model_path)
        self._sample_every = sample_every
        self._classes = classes if classes is not None else [0]
        self._conf = conf_thresh
        self._iou  = iou_thresh
        self._max_lost = max_lost

    def compile(self, video_path: str, zones: list[Zone], source_label: str | None = None) -> VIR:
        """Run perception pipeline and return a VIR.

        Parameters
        ----------
        video_path:   path to the input video file
        zones:        list of Zone objects (polygon in normalised [0,1] coords)
        source_label: label stored in VIR.source (defaults to basename of video_path)
        """
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
        W     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        eff_fps = fps / self._sample_every

        active: dict[str, _ActiveTrack] = {}
        retired: list[_ActiveTrack]     = []
        next_id = 0
        zone_events: list[ZoneEvent] = []

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % self._sample_every != 0:
                frame_idx += 1
                continue

            t_sec = frame_idx / fps

            # ── YOLO inference ─────────────────────────────────────────────
            results = self._model.predict(
                frame, classes=self._classes, conf=self._conf,
                verbose=False, device="cpu",
            )
            dets: list[tuple] = []   # (cx_norm, cy_norm, x_norm, y_norm, w_norm, h_norm, conf)
            if results and results[0].boxes is not None:
                for box in results[0].boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    cf = float(box.conf[0])
                    bx = x1/W; by = y1/H; bw = (x2-x1)/W; bh = (y2-y1)/H
                    cx = (x1+x2)/2/W; cy = (y1+y2)/2/H
                    dets.append((cx, cy, bx, by, bw, bh, cf))

            # ── Track association (greedy IoU) ─────────────────────────────
            matched_track_ids: set[str] = set()
            matched_det_idxs:  set[int] = set()

            for tid, tr in list(active.items()):
                if not tr.positions:
                    continue
                last_pos = tr.positions[-1].bbox
                best_iou, best_di = 0.0, -1
                for di, det in enumerate(dets):
                    if di in matched_det_idxs:
                        continue
                    det_box = (det[2], det[3], det[4], det[5])
                    last_box = (last_pos.x, last_pos.y, last_pos.w, last_pos.h)
                    iou_val = _iou(last_box, det_box)
                    if iou_val > best_iou:
                        best_iou, best_di = iou_val, di
                if best_iou >= self._iou and best_di >= 0:
                    matched_track_ids.add(tid)
                    matched_det_idxs.add(best_di)
                    det = dets[best_di]
                    tr.positions.append(TrackPosition(
                        frame=frame_idx, t_sec=round(t_sec, 3),
                        bbox=BBox(round(det[2],4), round(det[3],4),
                                  round(det[4],4), round(det[5],4)),
                        conf=round(det[6], 2),
                    ))
                    tr.last_cx = det[0]; tr.last_cy = det[1]
                    tr.last_t  = t_sec
                    tr.lost_frames = 0

            # New detections → new tracks
            for di, det in enumerate(dets):
                if di in matched_det_idxs:
                    continue
                tid = f"track_{next_id:04d}"; eid = f"entity_{next_id:04d}"; next_id += 1
                tr = _ActiveTrack(tid=tid, eid=eid, first_t=t_sec, last_t=t_sec,
                                  last_cx=det[0], last_cy=det[1])
                tr.positions.append(TrackPosition(
                    frame=frame_idx, t_sec=round(t_sec, 3),
                    bbox=BBox(round(det[2],4), round(det[3],4), round(det[4],4), round(det[5],4)),
                    conf=round(det[6], 2),
                ))
                active[tid] = tr

            # ── Zone ENTER / EXIT detection ───────────────────────────────
            for tid, tr in list(active.items()):
                cx, cy = tr.last_cx, tr.last_cy
                for zone in zones:
                    poly = zone.polygon  # normalised [0,1] coords
                    inside = _point_in_polygon(cx, cy, poly)
                    if inside and zone.id not in tr.in_zones:
                        tr.in_zones.add(zone.id)
                        zone_events.append(ZoneEvent(
                            track_id=tid, zone_id=zone.id, event_type="ENTER",
                            t_sec=round(t_sec, 3), frame=frame_idx,
                        ))
                    elif not inside and zone.id in tr.in_zones:
                        tr.in_zones.discard(zone.id)
                        zone_events.append(ZoneEvent(
                            track_id=tid, zone_id=zone.id, event_type="EXIT",
                            t_sec=round(t_sec, 3), frame=frame_idx,
                        ))

            # ── Age out lost tracks ────────────────────────────────────────
            for tid in list(active.keys()):
                if tid not in matched_track_ids:
                    active[tid].lost_frames += 1
                    if active[tid].lost_frames > self._max_lost:
                        retired.append(active.pop(tid))

            frame_idx += 1

        cap.release()

        # Retire remaining active tracks
        for tr in active.values():
            retired.append(tr)

        # ── Build stay_facts from paired ENTER/EXIT events ─────────────────
        stay_facts: list[StayFact] = []
        by_track: dict[str, dict[str, float]] = {}
        for ev in sorted(zone_events, key=lambda e: e.t_sec):
            tid = ev.track_id; zid = ev.zone_id
            if ev.event_type == "ENTER":
                by_track.setdefault(tid, {})[zid] = ev.t_sec
            else:
                enter_t = by_track.get(tid, {}).pop(zid, None)
                if enter_t is not None:
                    stay_facts.append(StayFact(
                        track_id=tid, zone_id=zid,
                        enter_t=round(enter_t, 3), exit_t=round(ev.t_sec, 3),
                        duration_sec=round(ev.t_sec - enter_t, 3),
                    ))
        # Flush still-open stays
        for tid, opens in by_track.items():
            tr_obj = next((t for t in retired if t.tid == tid), None)
            last_t = tr_obj.last_t if tr_obj else total / fps
            for zid, enter_t in opens.items():
                stay_facts.append(StayFact(
                    track_id=tid, zone_id=zid,
                    enter_t=round(enter_t, 3), exit_t=round(last_t, 3),
                    duration_sec=round(last_t - enter_t, 3),
                ))

        entities = [Entity(id=tr.eid, type="person") for tr in retired]
        tracks   = [
            Track(id=tr.tid, entity_id=tr.eid, positions=tr.positions[:50])
            for tr in retired if tr.positions
        ]
        duration_sec = total / fps

        return VIR(
            source=source_label or video_path,
            fps=round(eff_fps, 2),
            duration_sec=round(duration_sec, 2),
            width=W, height=H,
            entities=entities,
            tracks=tracks,
            zones=zones,
            zone_events=sorted(zone_events, key=lambda e: e.t_sec),
            stay_facts=stay_facts,
        )
