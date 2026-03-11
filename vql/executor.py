"""VQL Executor — runs a parsed VQL AST against a VIR.

Key property: once the VIR has been compiled, executing the same query
against it always returns *identical* results (deterministic / reproducible).
No model is invoked during execution.

Time complexity: O(|stay_facts| + |zone_events|) per query.
Typical wall-clock time: < 10 ms for a 2-hour surveillance VIR.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from vql.parser import (
    DurationPredicate, EntersPredicate, ExitsPredicate,
    SequencePredicate, StaysPredicate, TimeOfDayPredicate,
    TypePredicate, VQLQuery, _hms_to_sec,
)
from vql.vir import VIR, StayFact, ZoneEvent


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class MatchedTrack:
    """A single result row from a VQL query."""
    track_id: str
    entity_type: str
    timerange_start: str           # "HH:MM:SS" relative to video start
    timerange_end: str
    duration_sec: float
    matched_predicates: list[str]
    confidence: float = 1.0
    evidence_frame_times: list[float] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def timerange_start_abs(self) -> Optional[str]:
        """Absolute time if base_time is set in extra."""
        return self.extra.get("abs_enter")

    @property
    def timerange_end_abs(self) -> Optional[str]:
        return self.extra.get("abs_exit")


@dataclass
class QueryResult:
    """Complete result of executing a VQLQuery against a VIR."""
    query_id: str
    source: str
    total_matches: int
    matched_tracks: list[MatchedTrack]
    execution_time_ms: float
    plan_steps: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"QueryResult(matches={self.total_matches}, "
            f"time={self.execution_time_ms:.2f}ms)"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sec_to_hms(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def _cmp(a: float, op: str, b: float) -> bool:
    return {
        "<": a < b, "<=": a <= b,
        ">": a > b, ">=": a >= b,
        "==": a == b, "!=": a != b,
    }[op]


# ── Executor ──────────────────────────────────────────────────────────────────

class VQLExecutor:
    """Executes VQL queries against a compiled VIR.

    Thread-safe; holds no mutable state after construction.
    """

    def execute(self, query: VQLQuery, vir: VIR, query_str: str = "") -> QueryResult:
        t0 = time.perf_counter()
        plan: list[str] = []

        # Build fast lookup structures ─────────────────────────────────────
        stay_by_track: dict[str, list[StayFact]] = {}
        for sf in vir.stay_facts:
            stay_by_track.setdefault(sf.track_id, []).append(sf)

        events_by_track: dict[str, list[ZoneEvent]] = {}
        for ev in vir.zone_events:
            events_by_track.setdefault(ev.track_id, []).append(ev)

        entity_map = {e.id: e for e in vir.entities}
        track_map  = {t.id: t for t in vir.tracks}

        # Candidate set: all track IDs ─────────────────────────────────────
        candidate_ids: set[str] = set(track_map.keys())
        plan.append(f"VIR_SCAN  {len(candidate_ids)} tracks loaded from {vir.source!r}")

        # Apply each predicate ─────────────────────────────────────────────
        matched: dict[str, dict[str, Any]] = {}

        for pred in query.predicates:
            candidate_ids, step_matches, step_name = self._apply_predicate(
                pred, candidate_ids, stay_by_track, events_by_track,
                entity_map, track_map, vir,
            )
            plan.append(f"{step_name}  → {len(candidate_ids)} candidates")
            for tid, info in step_matches.items():
                if tid not in matched:
                    matched[tid] = {}
                matched[tid].update(info)

        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Build result objects ─────────────────────────────────────────────
        results: list[MatchedTrack] = []
        for tid in sorted(candidate_ids):
            info = matched.get(tid, {})
            tr = track_map.get(tid)
            if tr is None:
                continue
            ent = entity_map.get(tr.entity_id)
            etype = ent.type if ent else "unknown"

            enter_t = info.get("enter_t", tr.first_t)
            exit_t  = info.get("exit_t",  tr.last_t)
            dur     = exit_t - enter_t

            # Choose 2 evidence frame timestamps: at enter and at exit
            ev_times = [enter_t, exit_t]

            results.append(MatchedTrack(
                track_id=tid,
                entity_type=etype,
                timerange_start=_sec_to_hms(enter_t),
                timerange_end=_sec_to_hms(exit_t),
                duration_sec=dur,
                matched_predicates=info.get("matched_predicates", []),
                confidence=info.get("confidence", 1.0),
                evidence_frame_times=ev_times,
                extra=info.get("extra", {}),
            ))

        plan.append(f"RETURN  {len(results)} result(s)  total_time={elapsed_ms:.2f}ms")

        return QueryResult(
            query_id=hash(query.raw or query_str) & 0xFFFFFF,
            source=query.source,
            total_matches=len(results),
            matched_tracks=results,
            execution_time_ms=elapsed_ms,
            plan_steps=plan,
        )

    # ── predicate dispatchers ─────────────────────────────────────────────────

    def _apply_predicate(
        self, pred: Any,
        candidates: set[str],
        stay_by_track: dict[str, list[StayFact]],
        events_by_track: dict[str, list[ZoneEvent]],
        entity_map: dict, track_map: dict, vir: VIR,
    ) -> tuple[set[str], dict[str, dict], str]:

        if isinstance(pred, EntersPredicate):
            return self._enters(pred, candidates, events_by_track, entity_map, track_map)
        if isinstance(pred, ExitsPredicate):
            return self._exits(pred, candidates, events_by_track, entity_map, track_map)
        if isinstance(pred, (StaysPredicate, DurationPredicate)):
            return self._duration(pred, candidates, stay_by_track, entity_map, track_map)
        if isinstance(pred, TypePredicate):
            return self._type_filter(pred, candidates, entity_map, track_map)
        if isinstance(pred, SequencePredicate):
            return self._sequence(
                pred, candidates, stay_by_track, events_by_track,
                entity_map, track_map, vir,
            )
        if isinstance(pred, TimeOfDayPredicate):
            return self._time_of_day(pred, candidates, stay_by_track, entity_map, track_map)

        # Unknown predicate — pass through unchanged
        return candidates, {}, "UNKNOWN_PRED"

    # ── ENTERS ────────────────────────────────────────────────────────────────

    def _enters(
        self, pred: EntersPredicate,
        candidates: set[str],
        events_by_track: dict[str, list[ZoneEvent]],
        entity_map: dict, track_map: dict,
    ) -> tuple[set[str], dict, str]:
        zone_id = pred.zone.zone_id
        tr_from = pred.time_range.from_sec() if pred.time_range else None
        tr_to   = pred.time_range.to_sec()   if pred.time_range else None
        count_gte = pred.count_gte

        matched_ids: set[str] = set()
        info: dict[str, dict] = {}

        for tid in candidates:
            events = [
                ev for ev in events_by_track.get(tid, [])
                if ev.zone_id == zone_id and ev.event_type == "ENTER"
            ]
            if tr_from is not None:
                events = [e for e in events if e.t_sec >= tr_from]
            if tr_to is not None:
                events = [e for e in events if e.t_sec <= tr_to]
            if count_gte is not None and len(events) < count_gte:
                continue
            if not events:
                continue

            matched_ids.add(tid)
            info[tid] = {
                "enter_t": events[0].t_sec,
                "matched_predicates": [f"ENTERS zone({zone_id!r})"],
                "confidence": 0.95,
            }

        label = f"ENTERS zone={zone_id!r}"
        if tr_from:
            label += f" from={_sec_to_hms(tr_from)}"
        if tr_to:
            label += f" to={_sec_to_hms(tr_to)}"
        return matched_ids, info, label

    # ── EXITS ─────────────────────────────────────────────────────────────────

    def _exits(
        self, pred: ExitsPredicate,
        candidates: set[str],
        events_by_track: dict[str, list[ZoneEvent]],
        entity_map: dict, track_map: dict,
    ) -> tuple[set[str], dict, str]:
        zone_id = pred.zone.zone_id
        matched_ids: set[str] = set()
        info: dict[str, dict] = {}

        for tid in candidates:
            events = [
                ev for ev in events_by_track.get(tid, [])
                if ev.zone_id == zone_id and ev.event_type == "EXIT"
            ]
            if not events:
                continue
            matched_ids.add(tid)
            info[tid] = {
                "exit_t": events[-1].t_sec,
                "matched_predicates": [f"EXITS zone({zone_id!r})"],
            }

        return matched_ids, info, f"EXITS zone={zone_id!r}"

    # ── DURATION / STAYS ──────────────────────────────────────────────────────

    def _duration(
        self, pred: StaysPredicate | DurationPredicate,
        candidates: set[str],
        stay_by_track: dict[str, list[StayFact]],
        entity_map: dict, track_map: dict,
    ) -> tuple[set[str], dict, str]:
        zone_id = pred.zone.zone_id
        threshold = pred.duration.seconds
        op = pred.op

        matched_ids: set[str] = set()
        info: dict[str, dict] = {}

        for tid in candidates:
            facts = [sf for sf in stay_by_track.get(tid, []) if sf.zone_id == zone_id]
            if not facts:
                continue
            # Use the largest stay duration for this zone
            best = max(facts, key=lambda sf: sf.duration_sec)
            if not _cmp(best.duration_sec, op, threshold):
                continue

            matched_ids.add(tid)
            pname = ("STAYS" if isinstance(pred, StaysPredicate) else "DURATION")
            info[tid] = {
                "enter_t": best.enter_t,
                "exit_t": best.exit_t or (best.enter_t + best.duration_sec),
                "matched_predicates": [f"{pname}(zone({zone_id!r})) {op} {pred.duration.raw}"],
                "confidence": 0.98,
            }

        label = f"DURATION zone={zone_id!r} {op} {pred.duration.raw}"
        return matched_ids, info, label

    # ── TYPE ──────────────────────────────────────────────────────────────────

    def _type_filter(
        self, pred: TypePredicate,
        candidates: set[str],
        entity_map: dict, track_map: dict,
    ) -> tuple[set[str], dict, str]:
        matched_ids: set[str] = set()
        for tid in candidates:
            tr = track_map.get(tid)
            if tr is None:
                continue
            ent = entity_map.get(tr.entity_id)
            if ent and ent.type == pred.entity_type:
                matched_ids.add(tid)
        return matched_ids, {}, f"TYPE={pred.entity_type!r}"

    # ── SEQUENCE ─────────────────────────────────────────────────────────────

    def _sequence(
        self, pred: SequencePredicate,
        candidates: set[str],
        stay_by_track: dict, events_by_track: dict,
        entity_map: dict, track_map: dict, vir: VIR,
    ) -> tuple[set[str], dict, str]:
        """Require all sub-predicates to match in temporal order."""
        matched_ids: set[str] = set()
        info: dict[str, dict] = {}

        for tid in candidates:
            last_t = 0.0
            ok = True
            seq_preds = []
            for sub in pred.predicates:
                sub_ids, sub_info, _ = self._apply_predicate(
                    sub, {tid}, stay_by_track, events_by_track,
                    entity_map, track_map, vir,
                )
                if tid not in sub_ids:
                    ok = False; break
                enter = sub_info.get(tid, {}).get("enter_t", last_t)
                if enter < last_t:
                    ok = False; break
                last_t = enter
                seq_preds += sub_info.get(tid, {}).get("matched_predicates", [])
            if ok:
                matched_ids.add(tid)
                info[tid] = {
                    "matched_predicates": seq_preds,
                    "confidence": 0.92,
                }

        return matched_ids, info, f"SEQUENCE({len(pred.predicates)} steps)"

    # ── TIME_OF_DAY ───────────────────────────────────────────────────────────

    def _time_of_day(
        self, pred: TimeOfDayPredicate,
        candidates: set[str],
        stay_by_track: dict, entity_map: dict, track_map: dict,
    ) -> tuple[set[str], dict, str]:
        tr_from = pred.time_range.from_sec() or 0.0
        tr_to   = pred.time_range.to_sec()   or float("inf")
        matched_ids: set[str] = set()

        for tid in candidates:
            facts = stay_by_track.get(tid, [])
            if any(tr_from <= sf.enter_t <= tr_to for sf in facts):
                matched_ids.add(tid)

        label = f"TIME_OF_DAY in [{_sec_to_hms(tr_from)}, {_sec_to_hms(tr_to)}]"
        return matched_ids, {}, label
