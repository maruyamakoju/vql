"""Evidence frame extraction and annotation.

Draws annotated frames that visually prove a VQL match:
  - bounding box around the matched entity
  - zone overlay
  - timestamp and track ID
  - event label (ENTER / EXIT)

Uses OpenCV if available; falls back to a PIL-based renderer.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from vql.vir import VIR
from vql.executor import MatchedTrack


def extract_evidence_frames(
    video_path: str,
    vir: VIR,
    match: MatchedTrack,
    n_frames: int = 2,
) -> list:
    """Return up to *n_frames* annotated images for a MatchedTrack.

    Returns a list of numpy arrays (BGR) if OpenCV is available,
    or a list of PIL Images otherwise.
    Falls back to synthetic rendered frames if the video file is missing.
    """
    times = match.evidence_frame_times[:n_frames]
    track_map = {t.id: t for t in vir.tracks}
    track = track_map.get(match.track_id)

    if Path(video_path).exists() and Path(video_path).stat().st_size > 100:
        try:
            return _cv2_frames(video_path, vir, match, track, times)
        except Exception:
            pass

    # Fallback: synthetic rendered frame
    return _synthetic_frames(vir, match, track, times)


def _cv2_frames(
    video_path: str, vir: VIR, match: MatchedTrack,
    track, times: list[float],
) -> list:
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    frames = []
    for t_sec in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000)
        ok, frame = cap.read()
        if not ok:
            frame = np.zeros((108, 192, 3), dtype=np.uint8)
        frame = _annotate_frame_cv2(frame, vir, match, track, t_sec)
        frames.append(frame)
    cap.release()
    return frames


def _annotate_frame_cv2(frame, vir: VIR, match: MatchedTrack, track, t_sec: float):
    import cv2
    import numpy as np

    H, W = frame.shape[:2]
    is_enter = abs(t_sec - match.evidence_frame_times[0]) < 1.0
    colour = (0, 200, 100) if is_enter else (60, 60, 220)

    # Draw zone overlay
    for zone in vir.zones:
        pts = [(int(p[0]*W), int(p[1]*H)) for p in zone.polygon]
        overlay = frame.copy()
        cv2.fillPoly(overlay, [np.array(pts, np.int32)], (20, 100, 80))
        cv2.addWeighted(overlay, 0.2, frame, 0.8, 0, frame)
        cv2.polylines(frame, [np.array(pts, np.int32)], True, (0, 180, 180), 1)

    # Find closest position in track
    if track and track.positions:
        pos = min(track.positions, key=lambda p: abs(p.t_sec - t_sec))
        bx = int(pos.bbox.x * W)
        by = int(pos.bbox.y * H)
        bw = int(pos.bbox.w * W)
        bh = int(pos.bbox.h * H)
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), colour, 2)
        cv2.putText(frame, match.track_id, (bx, by - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, colour, 1)

    # Timestamp bar
    event_label = "ENTER" if is_enter else "EXIT"
    hms = _sec_to_hms(t_sec)
    cv2.rectangle(frame, (0, H - 20), (W, H), (0, 0, 0), -1)
    cv2.putText(frame, f"  {hms}  [{event_label}]", (4, H - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
    return frame


def _synthetic_frames(
    vir: VIR, match: MatchedTrack,
    track, times: list[float],
) -> list:
    """Generate synthetic annotated frames using PIL (no video needed)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        return [_pil_frame(vir, match, track, t, i == 0) for i, t in enumerate(times)]
    except ImportError:
        pass
    # Absolute fallback: empty list
    return []


def _pil_frame(vir: VIR, match: MatchedTrack, track, t_sec: float, is_enter: bool):
    from PIL import Image, ImageDraw
    W, H = 320, 180
    img = Image.new("RGB", (W, H), color=(12, 14, 22))
    draw = ImageDraw.Draw(img)

    # Scanlines
    for y in range(0, H, 3):
        draw.line([(0, y), (W, y)], fill=(18, 20, 32), width=1)

    # Grid
    for x in range(0, W, 32):
        draw.line([(x, 0), (x, H)], fill=(22, 26, 44), width=1)
    for y in range(0, H, 24):
        draw.line([(0, y), (W, y)], fill=(22, 26, 44), width=1)

    zone_colours = [
        (0, 182, 212), (99, 99, 241), (34, 197, 94), (249, 115, 22)
    ]
    # Draw zone fills
    for zone, col in zip(vir.zones, zone_colours):
        pts = [(int(p[0]*W), int(p[1]*H)) for p in zone.polygon]
        alpha_col = tuple(int(c * 0.12) for c in col)
        draw.polygon(pts, fill=alpha_col + (255,) if len(alpha_col) == 3 else alpha_col)
        draw.polygon(pts, outline=col, width=1)
        cx = sum(p[0] for p in pts) // len(pts)
        cy = sum(p[1] for p in pts) // len(pts)
        draw.text((cx - 12, cy - 6), zone.id, fill=col)

    # Person silhouette in zone
    col = (0, 220, 120) if is_enter else (220, 60, 60)
    px, py = W // 2, H // 2 - 20
    draw.ellipse((px - 8, py - 8, px + 8, py + 8), fill=col)   # head
    draw.rectangle((px - 10, py + 8, px + 10, py + 36), fill=col)  # body
    draw.rectangle((px - 10, py + 36, px - 2, py + 52), fill=col)  # left leg
    draw.rectangle((px + 2,  py + 36, px + 10, py + 52), fill=col)  # right leg
    draw.rectangle((px - 15, py - 12, px + 15, py + 56), outline=col, width=2)  # bbox
    draw.text((px - 14, py - 22), match.track_id, fill=col)

    # Event badge
    badge_col = (34, 197, 94) if is_enter else (239, 68, 68)
    label = "ENTER" if is_enter else "EXIT"
    draw.rectangle((W - 48, 4, W - 4, 18), fill=badge_col)
    draw.text((W - 45, 5), label, fill=(255, 255, 255))

    # Timestamp bar
    draw.rectangle((0, H - 20, W, H), fill=(0, 0, 0))
    hms = _sec_to_hms(t_sec)
    draw.text((4, H - 15), f"⏱ {hms}", fill=(180, 180, 180))

    return img


def _sec_to_hms(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"
