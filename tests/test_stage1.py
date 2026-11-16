"""Schema-15 joint, punch, and guard decoding."""

from pathlib import Path

import numpy as np
import pytest

from totf_analyzer.analytics.metrics import _round_windows, build_analytics
from totf_analyzer.parser.session import load_session
from totf_analyzer.parser.t2sr import (
    BONE_LEFT_WRIST,
    BONE_RIGHT_WRIST,
    HAND_LEFT,
    HAND_RIGHT,
    L_ELBOW,
    L_SHOULDER,
    guard_channel_scales,
)

ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / "Recordings"


def _recording(stamp: str) -> Path:
    matches = sorted(RECORDINGS.glob(f"*_{stamp}.zip"))
    if matches:
        return matches[0]
    return RECORDINGS / f"session_{stamp}.zip"


SAMPLE = _recording("20261115_054837")
THREE_ROUNDS = _recording("20261115_061911")

GUARD_BOUNDS = {
    "brace": (0.0, 8.0),
    "friction": (0.0, 4.0),
    "mass": (0.0, 8.0),
    "ladder": (0.0, 5.0),
}


def _scaled_upper_arm(session, corner: int) -> float:
    for track in session.metadata["m_tracks"]:
        if int(track["m_corner"]) == corner:
            return float(track["m_snapshot"]["m_scaledUpperArmLength"])
    raise AssertionError(f"no snapshot for corner {corner}")


def test_guard_header_scales():
    import zipfile

    for path in (SAMPLE, THREE_ROUNDS):
        assert path.is_file(), path
        with zipfile.ZipFile(path) as archive:
            for folder in ("p1", "p2"):
                scales = guard_channel_scales(archive.read(f"{folder}/guard.bin"))
                assert scales.keys() == GUARD_BOUNDS.keys()
                for name, bounds in GUARD_BOUNDS.items():
                    assert scales[name] == pytest.approx(bounds)


def test_upper_arm_matches_snapshot():
    session = load_session(SAMPLE)
    for track in session.tracks.values():
        segment = track.skeleton_pos[:, L_SHOULDER] - track.skeleton_pos[:, L_ELBOW]
        length = np.linalg.norm(segment, axis=1)
        assert np.median(length) == pytest.approx(_scaled_upper_arm(session, track.corner), abs=0.005)


def test_punch_hand_bone_and_launch():
    session = load_session(SAMPLE)
    for track in session.tracks.values():
        assert track.punches
        for punch in track.punches:
            assert punch.launch_tick <= punch.tick
            if punch.hand_index == HAND_LEFT:
                assert punch.bone_index == BONE_LEFT_WRIST
            elif punch.hand_index == HAND_RIGHT:
                assert punch.bone_index == BONE_RIGHT_WRIST
            else:
                raise AssertionError(punch.hand_index)


def test_three_round_guard_excludes_breaks():
    assert THREE_ROUNDS.is_file(), THREE_ROUNDS
    session = load_session(THREE_ROUNDS)
    report = build_analytics(session)
    windows = {window.index: window for window in _round_windows(session)}
    limits = {f"guard_{name}": upper for name, (_lower, upper) in GUARD_BOUNDS.items()}
    for series in report.series:
        upper = limits.get(series.name)
        if upper is None:
            continue
        assert series.y
        assert min(series.y) >= -1.0e-4
        assert max(series.y) <= upper + 1.0e-4

    fight = {item.corner: item for item in report.defense}
    for corner, track in session.tracks.items():
        assert fight[corner].guard_up_percent is not None
        assert fight[corner].guard_up_percent > 0
        weighted = 0.0
        frames = 0
        for rnd in report.rounds:
            if not rnd.completed or not rnd.defense:
                continue
            window = windows[rnd.index]
            defense = next(item for item in rnd.defense if item.corner == corner)
            assert defense.guard_up_percent is not None
            assert defense.guard_up_percent > 0
            selected = (track.skeleton_ticks >= window.warning_tick) & (track.skeleton_ticks <= window.stats_end)
            count = int(np.sum(selected))
            assert count > 0
            weighted += defense.guard_up_percent * count
            frames += count
        assert frames > 0
        assert fight[corner].guard_up_percent == pytest.approx(weighted / frames, abs=1.0e-4)

    hands = {row.hand for item in report.offense for row in item.hands}
    assert {"left", "right"} <= hands
    assert "hand_0" not in hands and "hand_1" not in hands
