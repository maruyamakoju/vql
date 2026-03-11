"""VIR — Video Intermediate Representation.

A VIR is the output of the *indexing* phase: a structured, queryable
summary of everything a perception pipeline could extract from a video.
Once a VIR is compiled, no model is needed to answer queries about it.

Schema
------
  Entity       — a unique object identity across the whole video
  Track        — a contiguous sequence of detections for one entity
  Zone         — a named polygon region in normalised [0, 1] image coords
  ZoneEvent    — a single ENTER or EXIT event (track × zone × timestamp)
  StayFact     — a derived record: how long did a track stay in a zone?

All timestamps are *seconds from the start of the video* (float).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# ── Primitive geometry ──────────────────────────────────────────────────────

@dataclass
class BBox:
    """Bounding box in normalised image coords [0, 1]."""
    x: float   # left
    y: float   # top
    w: float   # width
    h: float   # height

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


# ── Core VIR records ────────────────────────────────────────────────────────

@dataclass
class Entity:
    """A unique physical object (person, vehicle, …) in the video."""
    id: str
    type: str          # "person" | "vehicle" | "bicycle" | …
    attributes: dict = field(default_factory=dict)


@dataclass
class TrackPosition:
    """One detection within a track."""
    frame: int
    t_sec: float
    bbox: BBox
    conf: float = 1.0


@dataclass
class Track:
    """Contiguous trajectory of an entity.

    A single entity may produce multiple tracks if it leaves and
    re-enters the frame (each re-entry starts a new track).
    """
    id: str
    entity_id: str
    positions: list[TrackPosition] = field(default_factory=list)

    @property
    def first_t(self) -> float:
        return self.positions[0].t_sec if self.positions else 0.0

    @property
    def last_t(self) -> float:
        return self.positions[-1].t_sec if self.positions else 0.0

    @property
    def duration_sec(self) -> float:
        return self.last_t - self.first_t


@dataclass
class Zone:
    """A named polygon region, e.g. "A区域" or "paint_area"."""
    id: str
    polygon: list[list[float]]   # [[x0,y0],[x1,y1],…] normalised

    def contains_point(self, x: float, y: float) -> bool:
        """Ray-casting point-in-polygon test."""
        poly = self.polygon
        n = len(poly)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
                inside = not inside
            j = i
        return inside


@dataclass
class ZoneEvent:
    """A single ENTER or EXIT crossing event."""
    track_id: str
    zone_id: str
    event_type: str    # "ENTER" | "EXIT"
    t_sec: float
    frame: int


@dataclass
class StayFact:
    """How long a track stayed inside a zone between one ENTER and EXIT."""
    track_id: str
    zone_id: str
    enter_t: float
    exit_t: Optional[float]      # None if track ended without explicit exit
    duration_sec: float


# ── VIR container ───────────────────────────────────────────────────────────

@dataclass
class VIR:
    """Video Intermediate Representation — the queryable compiled form of a video."""

    source: str            # original video path / identifier
    fps: float
    duration_sec: float
    width: int  = 1920
    height: int = 1080

    entities:     list[Entity]       = field(default_factory=list)
    tracks:       list[Track]        = field(default_factory=list)
    zones:        list[Zone]         = field(default_factory=list)
    zone_events:  list[ZoneEvent]    = field(default_factory=list)
    stay_facts:   list[StayFact]     = field(default_factory=list)

    # ── convenience indices (built lazily) ──────────────────────────────────

    def _entity_map(self) -> dict[str, Entity]:
        return {e.id: e for e in self.entities}

    def _track_map(self) -> dict[str, Track]:
        return {t.id: t for t in self.tracks}

    def entity_for_track(self, track_id: str) -> Optional[Entity]:
        tm = self._track_map()
        em = self._entity_map()
        tr = tm.get(track_id)
        if tr is None:
            return None
        return em.get(tr.entity_id)

    def stay_facts_for(self, track_id: str, zone_id: Optional[str] = None) -> list[StayFact]:
        return [
            sf for sf in self.stay_facts
            if sf.track_id == track_id and (zone_id is None or sf.zone_id == zone_id)
        ]

    def zone_events_for(self, track_id: str, zone_id: Optional[str] = None) -> list[ZoneEvent]:
        return [
            ev for ev in self.zone_events
            if ev.track_id == track_id and (zone_id is None or ev.zone_id == zone_id)
        ]

    # ── serialisation ───────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        def _convert(obj):
            if isinstance(obj, list):
                return [_convert(x) for x in obj]
            if hasattr(obj, "__dataclass_fields__"):
                return {k: _convert(v) for k, v in asdict(obj).items()}
            return obj
        return _convert(self)

    def to_json(self, path: str | Path, indent: int = 2) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=indent, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, d: dict) -> VIR:
        entities = [Entity(**e) for e in d.get("entities", [])]

        tracks = []
        for t in d.get("tracks", []):
            positions = [
                TrackPosition(
                    frame=p["frame"],
                    t_sec=p["t_sec"],
                    bbox=BBox(**p["bbox"]),
                    conf=p.get("conf", 1.0),
                )
                for p in t.get("positions", [])
            ]
            tracks.append(Track(id=t["id"], entity_id=t["entity_id"], positions=positions))

        zones = [Zone(id=z["id"], polygon=z["polygon"]) for z in d.get("zones", [])]
        zone_events = [ZoneEvent(**e) for e in d.get("zone_events", [])]
        stay_facts = [
            StayFact(
                track_id=s["track_id"],
                zone_id=s["zone_id"],
                enter_t=s["enter_t"],
                exit_t=s.get("exit_t"),
                duration_sec=s["duration_sec"],
            )
            for s in d.get("stay_facts", [])
        ]

        return cls(
            source=d.get("source", ""),
            fps=d.get("fps", 15.0),
            duration_sec=d.get("duration_sec", 0.0),
            width=d.get("width", 1920),
            height=d.get("height", 1080),
            entities=entities,
            tracks=tracks,
            zones=zones,
            zone_events=zone_events,
            stay_facts=stay_facts,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> VIR:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ── summary ─────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"VIR(source={self.source!r}, "
            f"duration={self.duration_sec:.1f}s, "
            f"entities={len(self.entities)}, "
            f"tracks={len(self.tracks)}, "
            f"zone_events={len(self.zone_events)}, "
            f"stay_facts={len(self.stay_facts)})"
        )
