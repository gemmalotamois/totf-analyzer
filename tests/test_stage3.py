"""Footwork, angle cuts, range, and stance on bell-to-end-bell frames."""

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
    return load_session(path), build_analytics(load_session(path))


def test_local_player_matches_061911_footwork_prototype():
    """Bell-to-end-bell hips steps for the local orthodox fighter.

    Issue #1 measured 164.5 m radial and 143.2 m tangential, which is a 46.5%
    lateral share when total is the sum of those two absolute paths.
    """
    _session, report = _report(THREE_ROUNDS)
    local = next(fighter for fighter in report.fighters if fighter.is_local_player)
    foot = next(item.footwork for item in report.defense if item.corner == local.corner)
    assert foot.radial_distance_m == pytest.approx(164.5, abs=0.1)
    assert foot.tangential_distance_m == pytest.approx(143.2, abs=0.1)
    assert foot.total_distance_m == pytest.approx(foot.radial_distance_m + foot.tangential_distance_m)
    assert foot.tangential_share == pytest.approx(0.465, abs=0.001)
    assert foot.clockwise_distance_m + foot.counterclockwise_distance_m == pytest.approx(foot.tangential_distance_m)


def test_rounds_sum_to_the_fight_and_ranges_match():
    _session, report = _report(THREE_ROUNDS)
    feet = [item.footwork for item in report.defense]
    assert len(feet) == 2
    for name in ("clinch_range_percent", "pocket_range_percent", "outside_range_percent"):
        assert getattr(feet[0], name) == pytest.approx(getattr(feet[1], name))
    for item in report.defense:
        foot = item.footwork
        radial = 0.0
        tangential = 0.0
        cuts = 0
        for rnd in report.rounds:
            if not rnd.completed or not rnd.defense:
                continue
            block = next(row.footwork for row in rnd.defense if row.corner == item.corner)
            radial += block.radial_distance_m
            tangential += block.tangential_distance_m
            cuts += block.angle_cut_count
            round_cuts = [moment for moment in rnd.key_moments if moment.type == "angle_cut" and item.corner in moment.corners]
            assert block.angle_cut_count == len(round_cuts)
            assert block.clinch_range_percent + block.pocket_range_percent + block.outside_range_percent == pytest.approx(100.0)
        assert foot.radial_distance_m == pytest.approx(radial, abs=1.0e-3)
        assert foot.tangential_distance_m == pytest.approx(tangential, abs=1.0e-3)
        assert foot.angle_cut_count == cuts
        fight_cuts = [moment for moment in report.key_moments if moment.type == "angle_cut" and item.corner in moment.corners]
        assert foot.angle_cut_count == len(fight_cuts)
        assert foot.clinch_range_percent + foot.pocket_range_percent + foot.outside_range_percent == pytest.approx(100.0)


def test_stance_angles_and_combo_sweeps_are_finite():
    _session, report = _report(THREE_ROUNDS)
    local = next(fighter for fighter in report.fighters if fighter.is_local_player)
    for item in report.defense:
        foot = item.footwork
        assert foot.angle_cut_count > 0
        assert 0.0 <= foot.shoulder_blade_angle_deg <= 90.0
        assert 0.0 <= foot.foot_blade_angle_deg <= 90.0
        for value in (
            foot.square_stance_percent,
            foot.lead_left_percent,
            foot.square_while_punching_percent,
            foot.clinch_range_percent,
            foot.pocket_range_percent,
            foot.outside_range_percent,
        ):
            assert 0.0 <= value <= 100.0
        for moment in report.key_moments:
            if moment.type != "angle_cut":
                continue
            assert moment.detail["direction"] in {"clockwise", "counterclockwise"}
            assert math.isfinite(moment.detail["delta_deg"])
    local_foot = next(item.footwork for item in report.defense if item.corner == local.corner)
    assert local_foot.attack_entry_angle_deg is not None and math.isfinite(local_foot.attack_entry_angle_deg)
    assert local_foot.post_combo_exit_angle_deg is not None and math.isfinite(local_foot.post_combo_exit_angle_deg)


def test_footwork_decode_notes():
    _session, report = _report(THREE_ROUNDS)
    foot = next(item for item in report.decode_status if item.field == "defense.footwork")
    assert foot.confidence == "inferred"
    assert "0.65" in foot.note and "end bell" in foot.note
    blade = next(item for item in report.decode_status if item.field == "defense.footwork.foot_blade_angle_deg")
    assert blade.confidence == "inferred"
    assert "IK" in blade.note
    cut = next(item for item in report.decode_status if item.field == "timeline.angle_cut")
    assert cut.confidence == "inferred"
