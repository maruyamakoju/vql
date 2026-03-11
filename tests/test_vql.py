"""VQL unit tests — parser, executor, VIR serialisation.

Run:
    python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from vql.parser import (
    DurationPredicate, EntersPredicate, SequencePredicate,
    StaysPredicate, TimeOfDayPredicate, TypePredicate,
    VQLSyntaxError, parse_vql,
)
from vql.vir import BBox, Entity, StayFact, Track, TrackPosition, VIR, Zone, ZoneEvent
from vql.executor import VQLExecutor


# ── test fixture ──────────────────────────────────────────────────────────────

def _vir():
    """Minimal 2-zone VIR: t1/person in ZoneA, t2/person in ZoneB, t3/vehicle in ZoneA."""
    zones = [
        Zone("ZoneA", [[0, 0], [1, 0], [1, .5], [0, .5]]),
        Zone("ZoneB", [[0, .5], [1, .5], [1, 1], [0, 1]]),
    ]
    ents = [Entity("e1", "person"), Entity("e2", "person"), Entity("e3", "vehicle")]
    def _p(t):
        return TrackPosition(int(t * 10), t, BBox(.1, .1, .1, .1), .9)
    tracks = [
        Track("t1", "e1", [_p(100), _p(200)]),
        Track("t2", "e2", [_p(300), _p(400)]),
        Track("t3", "e3", [_p(50),  _p(80)]),
    ]
    evts = [
        ZoneEvent("t1", "ZoneA", "ENTER", 100, 1000),
        ZoneEvent("t1", "ZoneA", "EXIT",  200, 2000),
        ZoneEvent("t2", "ZoneB", "ENTER", 300, 3000),
        ZoneEvent("t2", "ZoneB", "EXIT",  400, 4000),
        ZoneEvent("t3", "ZoneA", "ENTER",  50,  500),
        ZoneEvent("t3", "ZoneA", "EXIT",   80,  800),
    ]
    facts = [
        StayFact("t1", "ZoneA", 100, 200, 100),
        StayFact("t2", "ZoneB", 300, 400, 100),
        StayFact("t3", "ZoneA",  50,  80,  30),
    ]
    return VIR("test.mp4", 10., 7200., 1920, 1080, ents, tracks, zones, evts, facts)


# ═══════════════════════════════════════════════════════════════════════════════
# Parser tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestParser:

    def test_enters_keyword_timerange(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") '
            'WHERE ENTERS(p, zone("A"), time_range(from="14:00:00", to="15:00:00")) '
            'RETURN track_id'
        )
        p = q.predicates[0]
        assert isinstance(p, EntersPredicate)
        assert p.time_range.from_t == "14:00:00"
        assert p.time_range.to_t   == "15:00:00"

    def test_enters_positional_timerange(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") '
            'WHERE ENTERS(p, zone("A"), time_range("13:00:00", "18:00:00")) '
            'RETURN track_id'
        )
        assert q.predicates[0].time_range.from_t == "13:00:00"

    def test_duration_lt_5min(self):
        q = parse_vql('SELECT p FROM VIR("c.mp4") WHERE DURATION(p, zone("Z")) < 5min RETURN track_id')
        p = q.predicates[0]
        assert isinstance(p, DurationPredicate) and p.op == "<" and p.duration.seconds == 300.

    def test_stays_gt_30min(self):
        q = parse_vql('SELECT p FROM VIR("c.mp4") WHERE STAYS(p, zone("Z")) > 30min RETURN track_id')
        p = q.predicates[0]
        assert isinstance(p, StaysPredicate) and p.op == ">" and p.duration.seconds == 1800.

    def test_sequence_two_zones(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") '
            'WHERE SEQUENCE(ENTERS(p, zone("受付")), ENTERS(p, zone("A"))) '
            'RETURN track_id'
        )
        p = q.predicates[0]
        assert isinstance(p, SequencePredicate) and len(p.predicates) == 2
        assert p.predicates[0].zone.zone_id == "受付"
        assert p.predicates[1].zone.zone_id == "A"

    def test_time_of_day_keyword(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") '
            'WHERE TIME_OF_DAY(p) IN time_range(from="13:00:00", to="18:00:00") '
            'RETURN track_id'
        )
        assert isinstance(q.predicates[0], TimeOfDayPredicate)
        assert q.predicates[0].time_range.from_t == "13:00:00"

    def test_time_of_day_positional(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") '
            'WHERE TIME_OF_DAY(p) IN time_range("13:00:00", "18:00:00") '
            'RETURN track_id'
        )
        assert q.predicates[0].time_range.to_t == "18:00:00"

    def test_type_predicate(self):
        q = parse_vql('SELECT v FROM VIR("c.mp4") WHERE TYPE(v, "vehicle") RETURN track_id')
        assert isinstance(q.predicates[0], TypePredicate)
        assert q.predicates[0].entity_type == "vehicle"

    def test_multiple_and(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") '
            'WHERE ENTERS(p, zone("A")) AND DURATION(p, zone("A")) < 5min '
            'RETURN track_id'
        )
        assert len(q.predicates) == 2

    def test_source_extracted(self):
        q = parse_vql('SELECT p FROM VIR("myvid.mp4") WHERE TYPE(p, "person") RETURN track_id')
        assert q.source == "myvid.mp4"

    def test_japanese_zone_name(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") WHERE ENTERS(p, zone("エレベーター前")) RETURN track_id'
        )
        assert q.predicates[0].zone.zone_id == "エレベーター前"

    def test_duration_unit_seconds(self):
        q = parse_vql('SELECT p FROM VIR("c.mp4") WHERE DURATION(p, zone("Z")) < 30s RETURN track_id')
        assert q.predicates[0].duration.seconds == 30.

    def test_duration_unit_hours(self):
        q = parse_vql('SELECT p FROM VIR("c.mp4") WHERE STAYS(p, zone("Z")) > 1h RETURN track_id')
        assert q.predicates[0].duration.seconds == 3600.

    def test_syntax_error_missing_from_keyword(self):
        with pytest.raises(VQLSyntaxError):
            parse_vql('SELECT p VIR("c.mp4") WHERE TYPE(p, "person") RETURN track_id')

    def test_syntax_error_unclosed_paren(self):
        with pytest.raises(VQLSyntaxError):
            parse_vql('SELECT p FROM VIR("c.mp4" WHERE TYPE(p, "person") RETURN track_id')

    def test_return_items_count(self):
        q = parse_vql(
            'SELECT p FROM VIR("c.mp4") WHERE TYPE(p, "person") '
            'RETURN track_id, enter_t, exit_t, evidence_frames(n=2)'
        )
        assert len(q.returns) == 4


# ═══════════════════════════════════════════════════════════════════════════════
# Executor tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecutor:

    def setup_method(self):
        self.vir = _vir()
        self.ex  = VQLExecutor()

    def _run(self, vql):
        return self.ex.execute(parse_vql(vql), self.vir)

    def test_enters_zone_a_returns_t1_and_t3(self):
        ids = {m.track_id for m in self._run(
            'SELECT p FROM VIR("t.mp4") WHERE ENTERS(p, zone("ZoneA")) RETURN track_id'
        ).matched_tracks}
        assert "t1" in ids and "t3" in ids and "t2" not in ids

    def test_enters_zone_b_returns_t2(self):
        assert {m.track_id for m in self._run(
            'SELECT p FROM VIR("t.mp4") WHERE ENTERS(p, zone("ZoneB")) RETURN track_id'
        ).matched_tracks} == {"t2"}

    def test_time_range_filters_early_track(self):
        # t1 enters at t=100s (00:01:40), t3 at t=50s (00:00:50)
        # window 00:01:00 - 00:02:00 → only t1
        ids = {m.track_id for m in self._run(
            'SELECT p FROM VIR("t.mp4") '
            'WHERE ENTERS(p, zone("ZoneA"), time_range(from="00:01:00", to="00:02:00")) '
            'RETURN track_id'
        ).matched_tracks}
        assert "t1" in ids and "t3" not in ids

    def test_duration_lt_60s_returns_t3_only(self):
        # t3: 30s in ZoneA  |  t1: 100s in ZoneA
        assert {m.track_id for m in self._run(
            'SELECT p FROM VIR("t.mp4") WHERE DURATION(p, zone("ZoneA")) < 60s RETURN track_id'
        ).matched_tracks} == {"t3"}

    def test_stays_gt_60s_returns_t1(self):
        ids = {m.track_id for m in self._run(
            'SELECT p FROM VIR("t.mp4") WHERE STAYS(p, zone("ZoneA")) > 60s RETURN track_id'
        ).matched_tracks}
        assert "t1" in ids and "t3" not in ids

    def test_type_person_excludes_vehicle(self):
        ids = {m.track_id for m in self._run(
            'SELECT p FROM VIR("t.mp4") '
            'WHERE ENTERS(p, zone("ZoneA")) AND TYPE(p, "person") '
            'RETURN track_id'
        ).matched_tracks}
        assert "t1" in ids and "t3" not in ids

    def test_type_vehicle_returns_t3(self):
        assert {m.track_id for m in self._run(
            'SELECT v FROM VIR("t.mp4") WHERE TYPE(v, "vehicle") RETURN track_id'
        ).matched_tracks} == {"t3"}

    def test_deterministic_two_runs(self):
        q  = parse_vql('SELECT p FROM VIR("t.mp4") WHERE ENTERS(p, zone("ZoneA")) RETURN track_id')
        r1 = self.ex.execute(q, self.vir)
        r2 = self.ex.execute(q, self.vir)
        assert [m.track_id for m in r1.matched_tracks] == [m.track_id for m in r2.matched_tracks]

    def test_deterministic_100_runs(self):
        q     = parse_vql('SELECT p FROM VIR("t.mp4") WHERE ENTERS(p, zone("ZoneA")) RETURN track_id')
        first = [m.track_id for m in self.ex.execute(q, self.vir).matched_tracks]
        for _ in range(99):
            assert [m.track_id for m in self.ex.execute(q, self.vir).matched_tracks] == first

    def test_execution_under_10ms(self):
        q = parse_vql(
            'SELECT p FROM VIR("t.mp4") '
            'WHERE ENTERS(p, zone("ZoneA")) AND DURATION(p, zone("ZoneA")) < 5min '
            'RETURN track_id'
        )
        assert self.ex.execute(q, self.vir).execution_time_ms < 10.

    def test_plan_steps_present(self):
        r = self._run('SELECT p FROM VIR("t.mp4") WHERE ENTERS(p, zone("ZoneA")) RETURN track_id')
        assert len(r.plan_steps) >= 2

    def test_matched_track_has_evidence_times(self):
        for m in self._run(
            'SELECT p FROM VIR("t.mp4") WHERE ENTERS(p, zone("ZoneA")) RETURN track_id'
        ).matched_tracks:
            assert len(m.evidence_frame_times) >= 1

    def test_no_match_returns_empty(self):
        r = self._run('SELECT p FROM VIR("t.mp4") WHERE ENTERS(p, zone("NoSuchZone")) RETURN track_id')
        assert r.total_matches == 0 and r.matched_tracks == []


# ═══════════════════════════════════════════════════════════════════════════════
# VIR serialisation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestVIRSerialisation:

    def test_roundtrip_dict(self):
        v = _vir(); v2 = VIR.from_dict(v.to_dict())
        assert v2.source == v.source
        assert len(v2.tracks) == len(v.tracks)
        assert len(v2.zone_events) == len(v.zone_events)
        assert len(v2.stay_facts) == len(v.stay_facts)

    def test_roundtrip_json(self):
        import json
        v = _vir(); v2 = VIR.from_dict(json.loads(json.dumps(v.to_dict())))
        assert v2.duration_sec == v.duration_sec
        assert len(v2.entities) == len(v.entities)

    def test_zone_polygon_preserved(self):
        v = _vir(); v2 = VIR.from_dict(v.to_dict())
        for z1, z2 in zip(v.zones, v2.zones):
            assert z1.id == z2.id and z1.polygon == z2.polygon
