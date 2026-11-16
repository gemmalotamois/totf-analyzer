"""Cross-stream fight metrics. Numbers and timestamps only."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from totf_analyzer.models.analytics import (
    CornerRound,
    DecodeNote,
    DefenseStats,
    FightAnalytics,
    Fighter,
    HandStats,
    HistogramBin,
    KeyMoment,
    OffenseStats,
    PunchRow,
    RoundScore,
    Series,
    SessionFacts,
    TrajectoryStats,
    VelocityStats,
)
from totf_analyzer.parser.session import COLOR_CORNER, CORNER_COLOR, ParsedSession, TrackData
from totf_analyzer.parser.t2sr import EventRecord, PunchRecord

# Hands count as "up" when both are within these distances of the head joint.
GUARD_DY_MAX = 0.38
GUARD_HORIZ_MAX = 0.40
# Opponent punch records mark the surrounding ticks as under fire.
UNDER_FIRE_BEFORE = 6
UNDER_FIRE_AFTER = 36
# A scored-hit event links to the nearest unused punch inside this window.
HIT_MATCH_TICKS = 36
PUNISHMENT_LINK_TICKS = 48
SPEED_BINS = (0.0, 6.0, 7.5, 8.5, 9.5, 12.0, 30.0)
SERIES_STEP_SEC = 0.25
HEAD_DROP_METERS = 0.28
HEAD_DROP_TICKS = 48
HEAD_SPEED_CAP = 5.0
EXCHANGE_MIN_EACH = 2
# A round start is two type-5 events this many ticks apart: the warning, then the bell.
ROUND_START_GAP = 735
# The end bell lands a few ticks after round_duration / fixed_timestep.
END_BELL_TOLERANCE = 12
# Punches that land just after the bell still belong to the round.
BELL_GRACE_TICKS = 240

# Event type codes whose counts line up with scorecard totals on schema 15.
COUNT_MATCHED_EVENTS = (
    (7, "stun", "stuns"),
    (2, "knockdown", "all_knockdowns"),
)
HURT_EVENT_TYPE = 5
KNOCKOUT_EVENT_TYPE = 6


def build_analytics(session: ParsedSession) -> FightAnalytics:
    notes: list[DecodeNote] = []
    rounds = _rounds(session)
    totals = _score_totals(rounds)
    fighters = [_fighter(track, notes) for track in _ordered(session)]
    labels = {
        corner: _hand_labels(track, _body(track), notes) for corner, track in session.tracks.items()
    }
    windows = _round_windows(session)
    offense, defense, moments, series = _measure(session, labels, totals, notes, clock_ticks=_clock_ticks(windows))
    _attach_rounds(session, rounds, labels, windows)
    notes.extend(_static_notes())
    return FightAnalytics(
        session=_session_facts(session, totals),
        fighters=fighters,
        rounds=rounds,
        offense=offense,
        defense=defense,
        key_moments=moments,
        series=series,
        decode_status=notes,
    )


def _measure(
    session: ParsedSession,
    labels: dict[int, dict[int, str]],
    totals: dict[int, dict],
    notes: list[DecodeNote],
    pace_sec: float | None = None,
    clock_ticks: set[int] | None = None,
) -> tuple[list[OffenseStats], list[DefenseStats], list[KeyMoment], list[Series]]:
    bodies = {corner: _body(track) for corner, track in session.tracks.items()}
    landed_events = {
        corner: [event for event in track.events if event.scored_hit]
        for corner, track in session.tracks.items()
    }
    _note_scored_hits(landed_events, totals, notes)
    matches: dict[int, dict[int, EventRecord]] = {}
    for corner, track in session.tracks.items():
        defender = _opponent(session, corner)
        matches[corner] = _match_punches(track.punches, landed_events[defender])
    offense = [
        _offense(
            session,
            track,
            labels[track.corner],
            matches[track.corner],
            landed_events,
            totals,
            notes,
            pace_sec=pace_sec,
        )
        for track in _ordered(session)
    ]
    defense = [
        _defense(session, track, bodies[track.corner], totals, notes) for track in _ordered(session)
    ]
    return (
        offense,
        defense,
        _key_moments(session, matches, bodies, totals, notes, clock_ticks),
        _series(session, bodies),
    )


def _ordered(session: ParsedSession) -> list[TrackData]:
    return [session.tracks[corner] for corner in sorted(session.tracks)]


def _opponent(session: ParsedSession, corner: int) -> int:
    others = [item for item in session.tracks if item != corner]
    if len(others) != 1:
        raise ValueError("analytics expects exactly two fighters")
    return others[0]


def _sec(session: ParsedSession, tick: int) -> float:
    return round((tick - session.start_tick) * session.fixed_timestep, 4)


def _finite(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _ratio(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def _fighter(track: TrackData, notes: list[DecodeNote]) -> Fighter:
    if track.stance and not track.stance_confirmed:
        notes.append(
            DecodeNote(
                field=f"fighter.{track.corner}.stance",
                confidence="inferred",
                note="Body-stance code 1 is orthodox on the AI track that publishes the stance name.",
            )
        )
    return Fighter(
        corner=track.corner,
        color=track.color,
        name=track.name,
        track_folder=track.folder,
        is_local_player=track.is_local_player,
        stance=track.stance,
        height_m=track.height_m,
        ai_archetype=track.ai_archetype,
        ai_difficulty=track.ai_difficulty,
    )


def _session_facts(session: ParsedSession, totals: dict[int, dict]) -> SessionFacts:
    meta = session.metadata
    venue = meta.get("m_venue") or {}
    scoring = meta.get("m_scoring") or {}
    method, winner = _result(totals)
    return SessionFacts(
        name=str(meta.get("m_name") or session.path.stem),
        source=session.path.name,
        fight_id=meta.get("m_fightId"),
        created_utc=meta.get("m_createdUtc"),
        app_version=meta.get("m_appVersion"),
        venue=venue.get("m_venueName"),
        rounds_scheduled=int(venue.get("m_rounds") or len(session.metadata["m_scoring"]["m_rounds"])),
        round_duration_sec=float(venue.get("m_roundDurationSeconds") or 0),
        break_duration_sec=float(venue.get("m_roundBreakDurationSeconds") or 0),
        duration_sec=round(session.duration_sec, 4),
        fixed_timestep=session.fixed_timestep,
        start_tick=session.start_tick,
        end_tick=session.end_tick,
        method=method,
        winner_corner=winner,
        winner_color=CORNER_COLOR.get(winner) if winner is not None else None,
        red_total_score=scoring.get("m_redTotalScore"),
        blue_total_score=scoring.get("m_blueTotalScore"),
    )


def _result(totals: dict[int, dict]) -> tuple[str, int | None]:
    knocked = [corner for corner, row in totals.items() if row["knocked_out"]]
    technical = [corner for corner, row in totals.items() if row["technically_knocked_out"]]
    if len(knocked) == 1:
        return "knockout", _other_corner(knocked[0], totals)
    if len(technical) == 1:
        return "technical_knockout", _other_corner(technical[0], totals)
    red = totals.get(COLOR_CORNER["red"], {}).get("score", 0)
    blue = totals.get(COLOR_CORNER["blue"], {}).get("score", 0)
    if red == blue:
        return "decision", None
    return "decision", COLOR_CORNER["red"] if red > blue else COLOR_CORNER["blue"]


def _other_corner(corner: int, totals: dict[int, dict]) -> int | None:
    others = [item for item in totals if item != corner]
    return others[0] if len(others) == 1 else None


def _foul_count(fouls: object) -> int:
    if isinstance(fouls, dict):
        return int(sum(value for value in fouls.values() if isinstance(value, (int, float))))
    return 0


def _corner_round(raw: dict) -> CornerRound:
    return CornerRound(
        score=int(raw.get("m_score") or 0),
        hits=int(raw.get("m_hits") or 0),
        received_damage=float(raw.get("m_receivedDamage") or 0),
        stuns=int(raw.get("m_stuns") or 0),
        punishment_stuns=int(raw.get("m_punishmentStuns") or 0),
        knockdowns=int(raw.get("m_knockedDowns") or 0),
        punishment_knockdowns=int(raw.get("m_punishmentKnockdowns") or 0),
        all_knockdowns=int(raw.get("m_allKnockedDown") or 0),
        knocked_out=bool(raw.get("m_knockedOut")),
        technically_knocked_out=bool(raw.get("m_technicallyKnockedOut")),
        fouls=_foul_count(raw.get("m_fouls")),
    )


def _rounds(session: ParsedSession) -> list[RoundScore]:
    scoring = session.metadata.get("m_scoring") or {}
    completed = int(scoring.get("m_completedRoundIndex") or 0)
    rows = []
    for index, raw in enumerate(scoring.get("m_rounds") or []):
        rows.append(
            RoundScore(
                index=index,
                completed=index <= completed,
                red=_corner_round(raw.get("m_red") or {}),
                blue=_corner_round(raw.get("m_blue") or {}),
            )
        )
    return rows


@dataclass(frozen=True)
class _RoundWindow:
    index: int
    warning_tick: int
    bell_tick: int
    end_bell_tick: int | None
    stats_end: int
    bell_sec: float
    end_sec: float
    duration_sec: float


def _round_windows(session: ParsedSession) -> list[_RoundWindow]:
    """Bell-to-bell windows. Breaks between rounds are left out."""
    venue = session.metadata.get("m_venue") or {}
    round_sec = float(venue.get("m_roundDurationSeconds") or 0)
    if round_sec <= 0 or session.fixed_timestep <= 0:
        return []
    duration_ticks = int(round(round_sec / session.fixed_timestep))
    ticks = sorted(
        {
            event.tick
            for track in session.tracks.values()
            for event in track.events
            if event.type_code == 5
        }
    )
    bells = [tick for prev, tick in zip(ticks, ticks[1:]) if tick - prev == ROUND_START_GAP]
    windows: list[_RoundWindow] = []
    for bell in bells:
        expected = bell + duration_ticks
        near = [tick for tick in ticks if abs(tick - expected) <= END_BELL_TOLERANCE]
        end_bell = min(near, key=lambda tick: abs(tick - expected)) if near else None
        if end_bell is not None:
            official_end = end_bell
            stats_end = min(session.end_tick, end_bell + BELL_GRACE_TICKS)
        elif session.end_tick <= expected + BELL_GRACE_TICKS:
            official_end = session.end_tick
            stats_end = session.end_tick
            end_bell = None
        else:
            official_end = expected
            stats_end = min(session.end_tick, expected + BELL_GRACE_TICKS)
            end_bell = None
        if windows and bell <= windows[-1].stats_end:
            continue
        bell_sec = _sec(session, bell)
        end_sec = _sec(session, official_end)
        windows.append(
            _RoundWindow(
                index=len(windows),
                warning_tick=bell - ROUND_START_GAP,
                bell_tick=bell,
                end_bell_tick=end_bell,
                stats_end=stats_end,
                bell_sec=bell_sec,
                end_sec=end_sec,
                duration_sec=round(end_sec - bell_sec, 4),
            )
        )
    return windows


def _clip_track(track: TrackData, start: int, end: int) -> TrackData:
    def take(ticks: np.ndarray) -> np.ndarray:
        return (ticks >= start) & (ticks <= end)

    skeleton = take(track.skeleton_ticks)
    punishment = take(track.punishment_ticks)
    stiffness = take(track.stiffness_ticks)
    guard = take(track.guard_ticks)
    return TrackData(
        folder=track.folder,
        corner=track.corner,
        color=track.color,
        name=track.name,
        is_local_player=track.is_local_player,
        stance=track.stance,
        stance_confirmed=track.stance_confirmed,
        height_m=track.height_m,
        ai_archetype=track.ai_archetype,
        ai_difficulty=track.ai_difficulty,
        start_tick=start,
        end_tick=end,
        punches=[punch for punch in track.punches if start <= punch.tick <= end],
        events=[event for event in track.events if start <= event.tick <= end],
        punishment_ticks=track.punishment_ticks[punishment],
        punishment=track.punishment[punishment],
        stiffness_ticks=track.stiffness_ticks[stiffness],
        stiffness=track.stiffness[stiffness],
        guard_ticks=track.guard_ticks[guard],
        guard_primary=track.guard_primary[guard],
        skeleton_ticks=track.skeleton_ticks[skeleton],
        skeleton_pos=track.skeleton_pos[skeleton],
    )


def _clock_ticks(windows: list[_RoundWindow]) -> set[int]:
    """Type-5 ticks that mark the warning, the bell, and the end bell."""
    ticks: set[int] = set()
    for window in windows:
        ticks.add(window.warning_tick)
        ticks.add(window.bell_tick)
        if window.end_bell_tick is not None:
            ticks.add(window.end_bell_tick)
    return ticks


def _drop_clock_events(track: TrackData, clock: set[int]) -> TrackData:
    if not clock:
        return track
    return TrackData(
        folder=track.folder,
        corner=track.corner,
        color=track.color,
        name=track.name,
        is_local_player=track.is_local_player,
        stance=track.stance,
        stance_confirmed=track.stance_confirmed,
        height_m=track.height_m,
        ai_archetype=track.ai_archetype,
        ai_difficulty=track.ai_difficulty,
        start_tick=track.start_tick,
        end_tick=track.end_tick,
        punches=track.punches,
        events=[event for event in track.events if not (event.type_code == 5 and event.tick in clock)],
        punishment_ticks=track.punishment_ticks,
        punishment=track.punishment,
        stiffness_ticks=track.stiffness_ticks,
        stiffness=track.stiffness,
        guard_ticks=track.guard_ticks,
        guard_primary=track.guard_primary,
        skeleton_ticks=track.skeleton_ticks,
        skeleton_pos=track.skeleton_pos,
    )


def _clip_session(session: ParsedSession, window: _RoundWindow) -> ParsedSession:
    clock = _clock_ticks([window])
    return ParsedSession(
        path=session.path,
        metadata=session.metadata,
        fixed_timestep=session.fixed_timestep,
        start_tick=window.bell_tick,
        end_tick=window.stats_end,
        tracks={
            corner: _drop_clock_events(_clip_track(track, window.warning_tick, window.stats_end), clock)
            for corner, track in session.tracks.items()
        },
    )


def _attach_rounds(
    session: ParsedSession,
    rounds: list[RoundScore],
    labels: dict[int, dict[int, str]],
    windows: list[_RoundWindow],
) -> None:
    windows = {window.index: window for window in windows}
    for rnd in rounds:
        if not rnd.completed:
            continue
        window = windows.get(rnd.index)
        if window is None:
            continue
        offense, defense, moments, series = _measure(
            _clip_session(session, window),
            labels,
            _score_totals([rnd]),
            [],
            pace_sec=window.duration_sec,
        )
        rnd.bell_sec = window.bell_sec
        rnd.end_sec = window.end_sec
        rnd.duration_sec = window.duration_sec
        rnd.offense = offense
        rnd.defense = defense
        rnd.key_moments = moments
        rnd.series = series


def _score_totals(rounds: list[RoundScore]) -> dict[int, dict]:
    totals = {
        corner: defaultdict(float)
        for corner in (COLOR_CORNER["red"], COLOR_CORNER["blue"])
    }
    flags = ("knocked_out", "technically_knocked_out")
    numbers = (
        "score",
        "hits",
        "received_damage",
        "stuns",
        "punishment_stuns",
        "knockdowns",
        "punishment_knockdowns",
        "all_knockdowns",
        "fouls",
    )
    for rnd in rounds:
        if not rnd.completed:
            continue
        for corner, block in ((COLOR_CORNER["red"], rnd.red), (COLOR_CORNER["blue"], rnd.blue)):
            for name in numbers:
                totals[corner][name] += getattr(block, name)
            for name in flags:
                totals[corner][name] = bool(totals[corner][name] or getattr(block, name))
    for row in totals.values():
        for name in flags:
            row[name] = bool(row[name])
        row["score"] = int(row["score"])
        row["hits"] = int(row["hits"])
        row["stuns"] = int(row["stuns"])
        row["punishment_stuns"] = int(row["punishment_stuns"])
        row["all_knockdowns"] = int(row["all_knockdowns"])
    return totals


def _trajectory(direction: tuple[float, float, float] | None) -> str:
    if direction is None:
        return "unknown"
    x, y, z = direction
    mag = math.sqrt(x * x + y * y + z * z)
    if mag < 0.25:
        return "unknown"
    x, y, z = x / mag, y / mag, z / mag
    if y >= 0.45:
        return "uppercut"
    if y <= -0.35 and abs(x) >= abs(z):
        return "overhand"
    if abs(z) >= 0.40 and abs(z) >= abs(x):
        return "hook"
    return "straight"


def _body(track: TrackData) -> dict:
    pos = track.skeleton_pos
    if len(pos) == 0:
        return {
            "head": 0,
            "left": None,
            "right": None,
            "ticks": track.skeleton_ticks.astype(np.int64),
            "guard": np.zeros(0, dtype=bool),
            "head_xyz": np.zeros((0, 3), dtype=np.float64),
        }
    med = np.median(pos, axis=0)
    speed = np.linalg.norm(np.diff(pos, axis=0), axis=2).mean(axis=0)
    head = int(np.argmax(med[:, 1]))
    head_y = float(med[head, 1])
    head_z = float(med[head, 2])
    candidates = []
    for joint in range(pos.shape[1]):
        if joint == head:
            continue
        height = float(med[joint, 1])
        if not head_y - 0.75 < height < head_y - 0.05:
            continue
        if abs(float(med[joint, 2]) - head_z) < 0.03:
            continue
        candidates.append(joint)
    left = [joint for joint in candidates if float(med[joint, 2]) < head_z]
    right = [joint for joint in candidates if float(med[joint, 2]) > head_z]
    left_joint = max(left, key=lambda joint: float(speed[joint])) if left else None
    right_joint = max(right, key=lambda joint: float(speed[joint])) if right else None
    ticks = track.skeleton_ticks.astype(np.int64)
    guard = np.zeros(len(ticks), dtype=bool)
    if left_joint is not None and right_joint is not None:
        dy_l = pos[:, head, 1] - pos[:, left_joint, 1]
        dy_r = pos[:, head, 1] - pos[:, right_joint, 1]
        horiz_l = np.linalg.norm(pos[:, left_joint][:, [0, 2]] - pos[:, head][:, [0, 2]], axis=1)
        horiz_r = np.linalg.norm(pos[:, right_joint][:, [0, 2]] - pos[:, head][:, [0, 2]], axis=1)
        guard = (dy_l < GUARD_DY_MAX) & (dy_r < GUARD_DY_MAX) & (horiz_l < GUARD_HORIZ_MAX) & (
            horiz_r < GUARD_HORIZ_MAX
        )
    head_xyz = pos[:, head, :]
    return {
        "head": head,
        "left": left_joint,
        "right": right_joint,
        "ticks": ticks,
        "guard": guard,
        "head_xyz": head_xyz,
    }


def _majority_side(ballots: list[str]) -> str | None:
    if len(ballots) < 8:
        return None
    left_share = ballots.count("left") / len(ballots)
    if left_share >= 0.65:
        return "left"
    if left_share <= 0.35:
        return "right"
    return None


def _hand_labels(track: TrackData, body: dict, notes: list[DecodeNote]) -> dict[int, str]:
    default = {0: "hand_0", 1: "hand_1"}
    if body["left"] is None or body["right"] is None or not track.punches:
        notes.append(
            DecodeNote(
                field=f"offense.{track.corner}.hand",
                confidence="unavailable",
                note="Hand index is stored, but it could not be tied to left/right from the skeleton.",
            )
        )
        return default
    index_of = {int(tick): i for i, tick in enumerate(body["ticks"])}
    votes: dict[int, list[str]] = defaultdict(list)
    pos = track.skeleton_pos
    for punch in track.punches:
        frame = index_of.get(punch.tick)
        if frame is None or frame + 12 >= len(pos):
            continue
        window = pos[frame : frame + 12]
        left_motion = float(np.linalg.norm(np.diff(window[:, body["left"]], axis=0), axis=1).sum())
        right_motion = float(np.linalg.norm(np.diff(window[:, body["right"]], axis=0), axis=1).sum())
        if left_motion == right_motion:
            continue
        votes[punch.hand_index].append("left" if left_motion > right_motion else "right")
    side_0 = _majority_side(votes[0])
    side_1 = _majority_side(votes[1])
    if side_0 and side_1 and side_0 != side_1:
        notes.append(
            DecodeNote(
                field=f"offense.{track.corner}.hand",
                confidence="inferred",
                note="Hand 0/1 was labeled left/right by which skeleton hand moved more at the punch tick.",
            )
        )
        return {0: side_0, 1: side_1}
    notes.append(
        DecodeNote(
            field=f"offense.{track.corner}.hand",
            confidence="unavailable",
            note="Punch records distinguish hand 0 and hand 1, but the skeleton vote was not consistent enough to call them left and right.",
        )
    )
    return default


def _note_scored_hits(landed_events: dict[int, list[EventRecord]], totals: dict[int, dict], notes: list[DecodeNote]) -> None:
    matched = True
    for corner, events in landed_events.items():
        taken = int(totals.get(corner, {}).get("hits", -1))
        if len(events) != taken:
            matched = False
    notes.append(
        DecodeNote(
            field="offense.landed",
            confidence="confirmed" if matched else "inferred",
            note=(
                "Type-1 events with flag byte 8 set sit on the fighter who was hit and match that fighter's official hit total. "
                "Those hits are the opponent's landed punches."
                if matched
                else "Type-1 flag-1 events did not match the official hit total, so landed counts are an estimate."
            ),
        )
    )


def _match_punches(punches: list[PunchRecord], received: list[EventRecord]) -> dict[int, EventRecord]:
    pairs: list[tuple[int, int, int]] = []
    for event_index, event in enumerate(received):
        for punch_index, punch in enumerate(punches):
            delta = abs(punch.tick - event.tick)
            if delta <= HIT_MATCH_TICKS:
                pairs.append((delta, event_index, punch_index))
    pairs.sort()
    used_events: set[int] = set()
    used_punches: set[int] = set()
    matched: dict[int, EventRecord] = {}
    for _delta, event_index, punch_index in pairs:
        if event_index in used_events or punch_index in used_punches:
            continue
        used_events.add(event_index)
        used_punches.add(punch_index)
        matched[punch_index] = received[event_index]
    return matched


def _punishment_delta(track: TrackData, tick: int) -> float | None:
    ticks = track.punishment_ticks
    if len(ticks) < 2:
        return None
    score = track.punishment[:, 0] + track.punishment[:, 1]
    best = 0.0
    seen = False
    for index in range(1, len(ticks)):
        if int(ticks[index - 1]) < tick:
            continue
        if int(ticks[index]) > tick + PUNISHMENT_LINK_TICKS:
            break
        step = float(score[index] - score[index - 1])
        seen = True
        if step > best:
            best = step
    if not seen:
        return None
    return best


def _offense(
    session: ParsedSession,
    track: TrackData,
    labels: dict[int, str],
    matched: dict[int, EventRecord],
    landed_events: dict[int, list[EventRecord]],
    totals: dict[int, dict],
    notes: list[DecodeNote],
    pace_sec: float | None = None,
) -> OffenseStats:
    defender = session.tracks[_opponent(session, track.corner)]
    received = landed_events[defender.corner]
    # m_hits on a corner is hits that fighter took. The punch that landed is on the defender's card.
    official = int(totals.get(defender.corner, {}).get("hits", 0))
    rows: list[PunchRow] = []
    hand_buckets: dict[str, list[PunchRow]] = defaultdict(list)
    traj_buckets: dict[str, list[PunchRow]] = defaultdict(list)
    speeds: list[float] = []
    landed_speeds: list[float] = []
    landed_deltas: list[float] = []
    corr_speed: list[float] = []
    corr_delta: list[float] = []
    for index, punch in enumerate(track.punches):
        landed = index in matched
        delta = _punishment_delta(defender, punch.tick) if landed else None
        hand = labels.get(punch.hand_index, f"hand_{punch.hand_index}")
        trajectory = _trajectory(punch.direction)
        row = PunchRow(
            t_sec=_sec(session, punch.tick),
            tick=punch.tick,
            hand=hand,
            hand_index=punch.hand_index,
            trajectory=trajectory,
            speed=_finite(punch.speed),
            landed=landed,
            punishment_delta=_finite(delta),
        )
        rows.append(row)
        hand_buckets[hand].append(row)
        traj_buckets[trajectory].append(row)
        if row.speed is not None:
            speeds.append(row.speed)
            if landed:
                landed_speeds.append(row.speed)
                if row.punishment_delta is not None:
                    landed_deltas.append(row.punishment_delta)
                    corr_speed.append(row.speed)
                    corr_delta.append(row.punishment_delta)
    unmatched = len(received) - len(matched)
    if unmatched > 0:
        hand_buckets["unmatched"].extend(
            PunchRow(
                t_sec=0,
                tick=0,
                hand="unmatched",
                hand_index=-1,
                trajectory="unknown",
                speed=None,
                landed=True,
                punishment_delta=None,
            )
            for _ in range(unmatched)
        )
    if not any(punch.speed is not None for punch in track.punches):
        notes.append(
            DecodeNote(
                field=f"offense.{track.corner}.speed",
                confidence="unavailable",
                note="This track's punch records do not carry the kinematics block (speed and direction are zero).",
            )
        )
    pace = session.duration_sec if pace_sec is None else pace_sec
    minutes = pace / 60.0 if pace else 0.0
    landed_total = len(received)
    return OffenseStats(
        corner=track.corner,
        thrown=len(track.punches),
        kinematic_thrown=sum(1 for punch in track.punches if punch.kinematics),
        landed=landed_total,
        official_hits=official,
        accuracy=_ratio(landed_total, len(track.punches)),
        punches_per_minute=(len(track.punches) / minutes) if minutes else 0.0,
        hands=[_hand_stats(name, bucket) for name, bucket in _sorted_hands(hand_buckets)],
        trajectories=[_traj_stats(name, bucket) for name, bucket in sorted(traj_buckets.items())],
        velocity=_velocity(speeds, landed_speeds, landed_deltas, corr_speed, corr_delta),
        punches=rows,
    )


def _sorted_hands(buckets: dict[str, list[PunchRow]]) -> list[tuple[str, list[PunchRow]]]:
    order = {"left": 0, "right": 1, "hand_0": 2, "hand_1": 3, "unmatched": 9}
    return sorted(buckets.items(), key=lambda item: (order.get(item[0], 5), item[0]))


def _hand_stats(name: str, rows: list[PunchRow]) -> HandStats:
    thrown = sum(1 for row in rows if row.hand != "unmatched")
    landed = sum(1 for row in rows if row.landed)
    speeds = [row.speed for row in rows if row.speed is not None]
    index = next((row.hand_index for row in rows if row.hand_index >= 0), None)
    return HandStats(
        hand=name,
        hand_index=index,
        thrown=thrown,
        landed=landed,
        accuracy=_ratio(landed, thrown) if name != "unmatched" else None,
        mean_speed=_mean(speeds),
    )


def _traj_stats(name: str, rows: list[PunchRow]) -> TrajectoryStats:
    thrown = len(rows)
    landed = sum(1 for row in rows if row.landed)
    speeds = [row.speed for row in rows if row.speed is not None]
    return TrajectoryStats(
        trajectory=name,
        thrown=thrown,
        landed=landed,
        accuracy=_ratio(landed, thrown),
        mean_speed=_mean(speeds),
    )


def _velocity(
    speeds: list[float],
    landed_speeds: list[float],
    landed_deltas: list[float],
    corr_speed: list[float],
    corr_delta: list[float],
) -> VelocityStats:
    histogram: list[HistogramBin] = []
    for lo, hi in zip(SPEED_BINS[:-1], SPEED_BINS[1:], strict=True):
        histogram.append(HistogramBin(lo=lo, hi=hi, count=sum(1 for speed in speeds if lo <= speed < hi)))
    return VelocityStats(
        samples=len(speeds),
        mean_thrown=_mean(speeds),
        mean_landed=_mean(landed_speeds),
        max_speed=max(speeds) if speeds else None,
        mean_punishment_delta_landed=_mean(landed_deltas),
        speed_punishment_correlation=_pearson(corr_speed, corr_delta),
        histogram=histogram,
    )


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 8 or len(xs) != len(ys):
        return None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return None
    value = float(np.corrcoef(x, y)[0, 1])
    return value if math.isfinite(value) else None


def _under_fire_mask(session: ParsedSession, track: TrackData, body: dict) -> np.ndarray:
    attacker = session.tracks[_opponent(session, track.corner)]
    ticks = body["ticks"]
    mask = np.zeros(len(ticks), dtype=bool)
    if len(ticks) == 0:
        return mask
    for punch in attacker.punches:
        center = int(np.searchsorted(ticks, punch.tick))
        lo = max(0, center - UNDER_FIRE_BEFORE)
        hi = min(len(mask), center + UNDER_FIRE_AFTER + 1)
        if lo < hi:
            mask[lo:hi] = True
    return mask


def _defense(
    session: ParsedSession,
    track: TrackData,
    body: dict,
    totals: dict[int, dict],
    notes: list[DecodeNote],
) -> DefenseStats:
    mask = _under_fire_mask(session, track, body)
    guard = body["guard"]
    guard_pct = float(guard.mean() * 100.0) if len(guard) else None
    under = guard[mask]
    under_pct = float(under.mean() * 100.0) if mask.any() else None
    head = body["head_xyz"]
    step = _head_speed(head, session.fixed_timestep)
    speed_mask = mask[1:] if len(mask) == len(step) + 1 else mask[: len(step)]
    mean_speed = float(step.mean()) if len(step) else None
    under_speed = float(step[speed_mask].mean()) if speed_mask.any() else None
    quiet = ~speed_mask
    quiet_speed = float(step[quiet].mean()) if quiet.any() else None
    vertical = float(head[:, 1].max() - head[:, 1].min()) if len(head) else None
    lateral = float(head[:, 2].max() - head[:, 2].min()) if len(head) else None
    if body["left"] is None or body["right"] is None:
        notes.append(
            DecodeNote(
                field=f"defense.{track.corner}.guard_up_percent",
                confidence="unavailable",
                note="Guard-up needs two skeleton hands on opposite sides of the head, and that split was not found.",
            )
        )
        guard_pct = None
        under_pct = None
    if track.punishment.size:
        peaks = [float(np.max(track.punishment[:, channel])) for channel in range(track.punishment.shape[1])]
    else:
        peaks = []
    dt = session.fixed_timestep
    return DefenseStats(
        corner=track.corner,
        guard_up_percent=_finite(guard_pct),
        guard_up_percent_under_fire=_finite(under_pct),
        under_fire_seconds=round(float(mask.sum()) * dt, 4),
        head_mean_speed=_finite(mean_speed),
        head_mean_speed_under_fire=_finite(under_speed),
        head_mean_speed_not_under_fire=_finite(quiet_speed),
        head_vertical_range=_finite(vertical),
        head_lateral_range=_finite(lateral),
        head_joint_index=int(body["head"]),
        punishment_peak=peaks,
        received_damage_official=float(totals.get(track.corner, {}).get("received_damage", 0.0)),
    )


def _key_moments(
    session: ParsedSession,
    matches: dict[int, dict[int, EventRecord]],
    bodies: dict[int, dict],
    totals: dict[int, dict],
    notes: list[DecodeNote],
    clock_ticks: set[int] | None = None,
) -> list[KeyMoment]:
    moments: list[KeyMoment] = []
    moments.extend(_outcome_moments(session, totals, notes, clock_ticks or set()))
    moments.extend(_exchange_moments(session))
    moments.extend(_punishment_spikes(session))
    moments.extend(_fast_lands(session, matches))
    moments.extend(_head_drops(session, bodies))
    moments.sort(key=lambda item: (item.t_sec, item.type))
    return moments


def _deduped_events(
    session: ParsedSession,
    type_code: int,
    skip_ticks: set[int] | None = None,
) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    seen: set[int] = set()
    skipped = skip_ticks or set()
    for track in session.tracks.values():
        for event in track.events:
            if event.type_code != type_code or event.tick in seen or event.tick in skipped:
                continue
            seen.add(event.tick)
            found.append((event.tick, event.subtype))
    found.sort()
    return found


def _unique_corner(totals: dict[int, dict], value: int, field: str) -> int | None:
    owners = [corner for corner, row in totals.items() if int(row.get(field, 0)) == value and value > 0]
    if len(owners) == 1:
        return owners[0]
    return None


def _outcome_moments(
    session: ParsedSession,
    totals: dict[int, dict],
    notes: list[DecodeNote],
    clock_ticks: set[int],
) -> list[KeyMoment]:
    moments: list[KeyMoment] = []
    for type_code, label, field in COUNT_MATCHED_EVENTS:
        events = _deduped_events(session, type_code)
        corner = _unique_corner(totals, len(events), field)
        if not events:
            continue
        if corner is None:
            notes.append(
                DecodeNote(
                    field=f"timeline.{label}",
                    confidence="unavailable",
                    note=f"Event type {type_code} count ({len(events)}) did not match a single corner's {field}.",
                )
            )
            continue
        notes.append(
            DecodeNote(
                field=f"timeline.{label}",
                confidence="inferred",
                note=f"Event type {type_code} occurs {len(events)} time(s), matching corner {corner} {field}.",
            )
        )
        for tick, subtype in events:
            moments.append(
                KeyMoment(
                    t_sec=_sec(session, tick),
                    tick=tick,
                    type=label,
                    corners=[corner],
                    detail={"event_type": type_code, "subtype": subtype, "label_confidence": "inferred"},
                )
            )
    hurt = _deduped_events(session, HURT_EVENT_TYPE, clock_ticks)
    hurt_owners = [
        corner
        for corner, row in totals.items()
        if int(row.get("stuns", 0)) + int(row.get("punishment_stuns", 0)) == len(hurt) and hurt
    ]
    if len(hurt) and len(hurt_owners) == 1:
        corner = hurt_owners[0]
        notes.append(
            DecodeNote(
                field="timeline.hurt_event",
                confidence="inferred",
                note="Event type 5 count matches that corner's stuns plus punishment stuns.",
            )
        )
        for tick, subtype in hurt:
            moments.append(
                KeyMoment(
                    t_sec=_sec(session, tick),
                    tick=tick,
                    type="hurt_event",
                    corners=[corner],
                    detail={"event_type": HURT_EVENT_TYPE, "subtype": subtype, "label_confidence": "inferred"},
                )
            )
    knockouts = _deduped_events(session, KNOCKOUT_EVENT_TYPE)
    ko_corners = [corner for corner, row in totals.items() if row.get("knocked_out")]
    if len(knockouts) == 1 and len(ko_corners) == 1:
        tick, subtype = knockouts[0]
        notes.append(
            DecodeNote(
                field="timeline.knockout",
                confidence="inferred",
                note="The single type-6 event is timestamped as the knockout on the corner flagged knocked out.",
            )
        )
        moments.append(
            KeyMoment(
                t_sec=_sec(session, tick),
                tick=tick,
                type="knockout",
                corners=[ko_corners[0]],
                detail={"event_type": KNOCKOUT_EVENT_TYPE, "subtype": subtype, "label_confidence": "inferred"},
            )
        )
    return moments


def _exchange_moments(session: ParsedSession) -> list[KeyMoment]:
    duration = session.duration_sec
    if duration <= 0:
        return []
    edges = np.arange(0.0, duration + 1.0, 1.0)
    counts = {}
    for corner, track in session.tracks.items():
        times = np.array([_sec(session, punch.tick) for punch in track.punches], dtype=np.float64)
        counts[corner] = np.histogram(times, bins=edges)[0]
    moments: list[KeyMoment] = []
    active: list[int] = []
    corners = sorted(session.tracks)
    for index in range(len(edges) - 1):
        hot = all(int(counts[corner][index]) >= EXCHANGE_MIN_EACH for corner in corners)
        if hot:
            active.append(index)
            continue
        if active:
            moments.append(_exchange(session, edges, counts, corners, active))
            active = []
    if active:
        moments.append(_exchange(session, edges, counts, corners, active))
    return moments


def _exchange(session, edges, counts, corners, indexes: list[int]) -> KeyMoment:
    start = float(edges[indexes[0]])
    end = float(edges[indexes[-1] + 1])
    detail = {"window_sec": round(end - start, 3)}
    for corner in corners:
        thrown = int(sum(int(counts[corner][index]) for index in indexes))
        detail[f"corner_{corner}_punches"] = thrown
    mid = (start + end) / 2.0
    tick = session.start_tick + int(round(mid / session.fixed_timestep))
    return KeyMoment(
        t_sec=round(mid, 4),
        tick=tick,
        type="exchange",
        corners=list(corners),
        detail=detail,
    )


def _punishment_spikes(session: ParsedSession) -> list[KeyMoment]:
    moments: list[KeyMoment] = []
    for track in session.tracks.values():
        score = track.punishment[:, 0] + track.punishment[:, 1]
        last_tick = -10_000
        for index in range(1, len(score)):
            step = float(score[index] - score[index - 1])
            tick = int(track.punishment_ticks[index])
            if step < 8.0 or tick - last_tick < 48:
                if step >= 8.0:
                    last_tick = tick
                continue
            last_tick = tick
            moments.append(
                KeyMoment(
                    t_sec=_sec(session, tick),
                    tick=tick,
                    type="punishment_spike",
                    corners=[track.corner],
                    detail={"punishment_step": round(step, 4)},
                )
            )
    return moments


def _fast_lands(session: ParsedSession, matches: dict[int, dict[int, EventRecord]]) -> list[KeyMoment]:
    moments: list[KeyMoment] = []
    for track in session.tracks.values():
        landed = [
            punch
            for index, punch in enumerate(track.punches)
            if index in matches.get(track.corner, {}) and punch.speed is not None
        ]
        if not landed:
            continue
        cutoff = float(np.percentile([punch.speed for punch in landed], 75))
        chosen = sorted(
            (punch for punch in landed if punch.speed is not None and punch.speed >= cutoff),
            key=lambda punch: punch.speed or 0.0,
            reverse=True,
        )[:8]
        for punch in chosen:
            moments.append(
                KeyMoment(
                    t_sec=_sec(session, punch.tick),
                    tick=punch.tick,
                    type="high_velocity_landed",
                    corners=[track.corner],
                    detail={"speed": round(float(punch.speed or 0.0), 4), "percentile_cutoff": round(cutoff, 4)},
                )
            )
    return moments


def _head_drops(session: ParsedSession, bodies: dict[int, dict]) -> list[KeyMoment]:
    moments: list[KeyMoment] = []
    for corner, body in bodies.items():
        height = body["head_xyz"][:, 1]
        ticks = body["ticks"]
        last = -10_000
        span = HEAD_DROP_TICKS
        for index in range(0, len(height) - span, 6):
            drop = float(height[index] - height[index + span])
            tick = int(ticks[index + span])
            if drop < HEAD_DROP_METERS or tick - last < 120:
                continue
            last = tick
            moments.append(
                KeyMoment(
                    t_sec=_sec(session, tick),
                    tick=tick,
                    type="head_drop",
                    corners=[corner],
                    detail={"drop_m": round(drop, 4), "window_sec": round(span * session.fixed_timestep, 4)},
                )
            )
    return moments


def _head_speed(head: np.ndarray, dt: float) -> np.ndarray:
    step = np.linalg.norm(np.diff(head, axis=0), axis=1) / dt
    # Single-frame jumps above this are skeleton glitches, not head movement.
    return np.minimum(step, HEAD_SPEED_CAP)


def _bucket(times: np.ndarray, values: np.ndarray, step: float) -> tuple[list[float], list[float]]:
    if len(times) == 0:
        return [], []
    start = float(times[0])
    end = float(times[-1])
    edges = np.arange(start, end + step, step)
    if len(edges) < 2:
        return [round(start, 4)], [float(values.mean())]
    out_t: list[float] = []
    out_y: list[float] = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        selected = (times >= lo) & (times < hi)
        if not np.any(selected):
            continue
        out_t.append(round(float((lo + hi) / 2.0), 4))
        out_y.append(round(float(values[selected].mean()), 4))
    return out_t, out_y


def _series(session: ParsedSession, bodies: dict[int, dict]) -> list[Series]:
    series: list[Series] = []
    for track in _ordered(session):
        t_pun = np.array([_sec(session, int(tick)) for tick in track.punishment_ticks])
        for channel in range(track.punishment.shape[1]):
            ts, ys = _bucket(t_pun, track.punishment[:, channel], SERIES_STEP_SEC)
            series.append(Series(name=f"punishment_{channel}", corner=track.corner, t=ts, y=ys))
        t_guard = np.array([_sec(session, int(tick)) for tick in track.guard_ticks])
        names = ("guard_brace", "guard_friction", "guard_mass", "guard_ladder")
        for channel, name in enumerate(names):
            ts, ys = _bucket(t_guard, track.guard_primary[:, channel], SERIES_STEP_SEC)
            series.append(Series(name=name, corner=track.corner, t=ts, y=ys))
        body = bodies[track.corner]
        t_body = np.array([_sec(session, int(tick)) for tick in body["ticks"]])
        ts, ys = _bucket(t_body, body["guard"].astype(np.float32), 1.0)
        series.append(Series(name="guard_up", corner=track.corner, t=ts, y=ys))
        head = body["head_xyz"]
        if len(head) > 1:
            speed = _head_speed(head, session.fixed_timestep)
            ts, ys = _bucket(t_body[1:], speed.astype(np.float32), SERIES_STEP_SEC)
            series.append(Series(name="head_speed", corner=track.corner, t=ts, y=ys))
            ts, ys = _bucket(t_body, head[:, 1], SERIES_STEP_SEC)
            series.append(Series(name="head_height", corner=track.corner, t=ts, y=ys))
        t_stiff = np.array([_sec(session, int(tick)) for tick in track.stiffness_ticks])
        stiff = np.max(track.stiffness, axis=1)
        ts, ys = _bucket(t_stiff, stiff, SERIES_STEP_SEC)
        series.append(Series(name="stiffness_max", corner=track.corner, t=ts, y=ys))
        duration = session.duration_sec
        times = np.array([_sec(session, punch.tick) for punch in track.punches], dtype=np.float64)
        origin = min(0.0, float(np.floor(times.min()))) if len(times) else 0.0
        edges = np.arange(origin, max(duration, origin + 0.01) + 1.0, 1.0)
        hist, _edges = np.histogram(times, bins=edges)
        centers = [round(float((lo + hi) / 2.0), 4) for lo, hi in zip(edges[:-1], edges[1:], strict=True)]
        series.append(
            Series(name="punch_density", corner=track.corner, t=centers, y=[int(value) for value in hist])
        )
    return series


def _static_notes() -> list[DecodeNote]:
    return [
        DecodeNote(
            field="time",
            confidence="confirmed",
            note="Seconds are (tick - startTick) × fixedTimestep from metadata.",
        ),
        DecodeNote(
            field="rounds",
            confidence="inferred",
            note=(
                "A completed round runs from the warning to just after the end bell. "
                "The warning and the bell are type-5 events 735 ticks apart, and the end bell is one round-duration later. "
                "Round stats, charts, and punch times are seconds after that round's bell. Breaks are not included."
            ),
        ),
        DecodeNote(
            field="corner_color",
            confidence="confirmed",
            note="Corner 1 is blue and corner 2 is red. Round points, hits taken, stuns, knockdowns, and knockouts belong to that corner.",
        ),
        DecodeNote(
            field="offense.thrown",
            confidence="confirmed",
            note="Thrown is the number of 140-byte punch records. kinematic_thrown counts records that include a direction and speed block.",
        ),
        DecodeNote(
            field="offense.accuracy",
            confidence="confirmed",
            note="Accuracy is landed divided by thrown. Landed is the defender's scored-hit count, which matches the hits taken on that defender's card.",
        ),
        DecodeNote(
            field="offense.trajectory",
            confidence="inferred",
            note="straight, hook, uppercut, and overhand are classified from the punch direction vector, not from a game enum.",
        ),
        DecodeNote(
            field="offense.speed",
            confidence="inferred",
            note="Speed is the float at punch-record offset 80. Magnitudes fall in a few meters per second and are treated as m/s.",
        ),
        DecodeNote(
            field="defense.guard_up_percent",
            confidence="inferred",
            note=(
                "A frame is guard-up when both lateral hand joints sit at most "
                f"{GUARD_DY_MAX:.2f} m below the head and within {GUARD_HORIZ_MAX:.2f} m horizontally. "
                "Under fire is the 0.05 s before through 0.30 s after each opponent punch record."
            ),
        ),
        DecodeNote(
            field="defense.head",
            confidence="inferred",
            note=(
                "The head joint is the skeleton point with the highest median height. "
                f"Position samples are uint16 values scaled across [-10, 10] m. "
                f"Instantaneous speed above {HEAD_SPEED_CAP:.0f} m/s is clipped as a tracking spike."
            ),
        ),
        DecodeNote(
            field="defense.punishment",
            confidence="inferred",
            note="punishment_0/1/2 are the three float meters in punishment.bin. They are not the official received-damage total.",
        ),
        DecodeNote(
            field="series.guard_brace",
            confidence="inferred",
            note="guard_brace, guard_friction, guard_mass, and guard_ladder are the first quantized sample of each named guard channel.",
        ),
        DecodeNote(
            field="timeline.exchange",
            confidence="inferred",
            note=f"An exchange is a run of 1-second bins in which each fighter has at least {EXCHANGE_MIN_EACH} punch records.",
        ),
        DecodeNote(
            field="timeline.punishment_spike",
            confidence="inferred",
            note="A punishment spike is a step of at least 8 on punishment channels 0+1, with repeats inside 0.4 s collapsed.",
        ),
        DecodeNote(
            field="timeline.head_drop",
            confidence="inferred",
            note=f"A head drop is a loss of at least {HEAD_DROP_METERS:.2f} m of head height inside {HEAD_DROP_TICKS / 120:.2f} s.",
        ),
    ]
