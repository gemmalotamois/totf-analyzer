"""Bell-to-bell head slip, level changes, ducks, and bob-and-weave."""

import math
from pathlib import Path

import pytest

from totf_analyzer.analytics.metrics import build_analytics
from totf_analyzer.parser.session import load_session

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / "Recordings"


def _recording(stamp: str) -> Path:
    matches = sorted(RECORDINGS.glob(f"*_{stamp}.zip"))
    if matches:
        return matches[0]
    return RECORDINGS / f"session_{stamp}.zip"


THREE_ROUNDS = _recording("20261115_061911")


def _report(path: Path):
    assert path.is_file(), path
    return build_analytics(load_session(path))


def test_local_player_matches_061911_centerline_prototype():
    """Issue #1: the local fighter is off the hip centerline for 4.6% of bell time."""
    report = _report(THREE_ROUNDS)
    local = next(fighter for fighter in report.fighters if fighter.is_local_player)
    movement = next(item.head_movement for item in report.defense if item.corner == local.corner)
    assert movement.off_centerline_percent == pytest.approx(4.6, abs=0.2)


def test_slip_spreads_and_level_changes_are_finite():
    report = _report(THREE_ROUNDS)
    for item in report.defense:
        movement = item.head_movement
        slip = movement.head_lateral_slip_m
        assert slip.median is not None and slip.p90 is not None and slip.under_fire_p90 is not None
        assert math.isfinite(slip.median) and math.isfinite(slip.p90) and math.isfinite(slip.under_fire_p90)
        assert slip.p90 >= slip.median
        for value in (
            movement.off_centerline_percent,
            movement.off_centerline_under_fire_percent,
            movement.off_centerline_while_punching_percent,
        ):
            assert value is not None
            assert 0.0 <= value <= 100.0
        assert movement.head_height_iqr_m is not None and movement.head_height_iqr_m >= 0.0
        assert movement.p05_p95_span_m is not None and movement.p05_p95_span_m >= movement.head_height_iqr_m
        assert movement.pelvis_drop_iqr_m is not None and movement.pelvis_drop_iqr_m >= 0.0
        assert movement.trunk_bend_iqr_m is not None and movement.trunk_bend_iqr_m >= 0.0


def test_round_event_counts_match_moments():
    report = _report(THREE_ROUNDS)
    for item in report.defense:
        movement = item.head_movement
        ducks = 0
        weaves = 0
        for rnd in report.rounds:
            if not rnd.completed or not rnd.defense:
                continue
            block = next(row.head_movement for row in rnd.defense if row.corner == item.corner)
            ducks += block.duck_count
            weaves += block.bob_and_weave_count
            round_ducks = [moment for moment in rnd.key_moments if moment.type == "duck" and item.corner in moment.corners]
            round_weaves = [
                moment for moment in rnd.key_moments if moment.type == "bob_and_weave" and item.corner in moment.corners
            ]
            assert block.duck_count == len(round_ducks)
            assert block.bob_and_weave_count == len(round_weaves)
            for moment in round_ducks:
                assert moment.detail["drop_m"] >= 0.15 - 1.0e-4
                assert moment.detail["duration_sec"] > 0.0
            for moment in round_weaves:
                assert moment.detail["drop_m"] >= 0.12 - 1.0e-4
                assert moment.detail["slip_delta_m"] >= 0.12 - 1.0e-4
        assert movement.duck_count == ducks
        assert movement.bob_and_weave_count == weaves
        fight_ducks = [moment for moment in report.key_moments if moment.type == "duck" and item.corner in moment.corners]
        fight_weaves = [
            moment for moment in report.key_moments if moment.type == "bob_and_weave" and item.corner in moment.corners
        ]
        assert movement.duck_count == len(fight_ducks)
        assert movement.bob_and_weave_count == len(fight_weaves)


def test_head_movement_decode_notes():
    report = _report(THREE_ROUNDS)
    note = next(item for item in report.decode_status if item.field == "defense.head_movement")
    assert note.confidence == "inferred"
    assert "0.10" in note.note and "end bell" in note.note
    duck = next(item for item in report.decode_status if item.field == "timeline.duck")
    weave = next(item for item in report.decode_status if item.field == "timeline.bob_and_weave")
    assert duck.confidence == "inferred"
    assert weave.confidence == "inferred"


def test_fighter_stats_include_head_movement():
    from totf_analyzer.mcp_server import server

    report = _report(THREE_ROUNDS)
    local = next(fighter for fighter in report.fighters if fighter.is_local_player)
    server.configure(RECORDINGS)
    stats = server.get_fighter_stats(THREE_ROUNDS.name, local.corner)
    assert stats["defense"]["head_movement"]["off_centerline_percent"] == pytest.approx(4.6, abs=0.2)
    completed = next(row for row in stats["rounds"] if row["completed"] and "defense" in row)
    assert "head_movement" in completed["defense"]
    assert "off_centerline_percent" in completed["defense"]["head_movement"]
