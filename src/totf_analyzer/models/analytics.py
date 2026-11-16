"""JSON schema for a fight's factual analytics. No coaching narrative."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["confirmed", "inferred", "unavailable"]


class DecodeNote(BaseModel):
    field: str
    confidence: Confidence
    note: str


class Fighter(BaseModel):
    corner: int
    color: str
    name: str
    track_folder: str
    is_local_player: bool
    stance: str | None = None
    height_m: float | None = None
    ai_archetype: str | None = None
    ai_difficulty: str | None = None


class CornerRound(BaseModel):
    score: int
    hits: int
    received_damage: float
    stuns: int
    punishment_stuns: int
    knockdowns: int
    punishment_knockdowns: int
    all_knockdowns: int
    knocked_out: bool
    technically_knocked_out: bool
    fouls: int


class RoundScore(BaseModel):
    index: int
    completed: bool
    red: CornerRound
    blue: CornerRound
    # Fight-clock seconds of the bell and the end of the round. Series, moments,
    # and punch times inside the round are seconds after the bell.
    bell_sec: float | None = None
    end_sec: float | None = None
    duration_sec: float | None = None
    offense: list[OffenseStats] = Field(default_factory=list)
    defense: list[DefenseStats] = Field(default_factory=list)
    key_moments: list[KeyMoment] = Field(default_factory=list)
    series: list[Series] = Field(default_factory=list)


class SessionFacts(BaseModel):
    name: str
    source: str
    fight_id: str | None = None
    created_utc: str | None = None
    app_version: str | None = None
    venue: str | None = None
    rounds_scheduled: int
    round_duration_sec: float
    break_duration_sec: float
    duration_sec: float
    fixed_timestep: float
    start_tick: int
    end_tick: int
    method: str
    winner_corner: int | None = None
    winner_color: str | None = None
    red_total_score: int | None = None
    blue_total_score: int | None = None


class HandStats(BaseModel):
    hand: str
    hand_index: int | None = None
    thrown: int
    landed: int
    accuracy: float | None = None
    mean_speed: float | None = None


class TrajectoryStats(BaseModel):
    trajectory: str
    thrown: int
    landed: int
    accuracy: float | None = None
    mean_speed: float | None = None


class HistogramBin(BaseModel):
    lo: float
    hi: float
    count: int


class VelocityStats(BaseModel):
    samples: int
    mean_thrown: float | None = None
    mean_landed: float | None = None
    max_speed: float | None = None
    mean_punishment_delta_landed: float | None = None
    speed_punishment_correlation: float | None = None
    histogram: list[HistogramBin] = Field(default_factory=list)


class PunchRow(BaseModel):
    t_sec: float
    tick: int
    hand: str
    hand_index: int
    trajectory: str
    speed: float | None = None
    landed: bool
    punishment_delta: float | None = None


class OffenseStats(BaseModel):
    corner: int
    thrown: int
    kinematic_thrown: int
    landed: int
    official_hits: int
    accuracy: float | None = None
    punches_per_minute: float
    hands: list[HandStats]
    trajectories: list[TrajectoryStats]
    velocity: VelocityStats
    punches: list[PunchRow]


class DefenseStats(BaseModel):
    corner: int
    guard_up_percent: float | None = None
    guard_up_percent_under_fire: float | None = None
    under_fire_seconds: float
    head_mean_speed: float | None = None
    head_mean_speed_under_fire: float | None = None
    head_mean_speed_not_under_fire: float | None = None
    head_vertical_range: float | None = None
    head_lateral_range: float | None = None
    head_joint_index: int | None = None
    punishment_peak: list[float] = Field(default_factory=list)
    received_damage_official: float


class KeyMoment(BaseModel):
    t_sec: float
    tick: int
    type: str
    corners: list[int]
    detail: dict


class Series(BaseModel):
    name: str
    corner: int | None = None
    t: list[float]
    y: list[float]


class FightAnalytics(BaseModel):
    session: SessionFacts
    fighters: list[Fighter]
    rounds: list[RoundScore]
    offense: list[OffenseStats]
    defense: list[DefenseStats]
    key_moments: list[KeyMoment]
    series: list[Series]
    decode_status: list[DecodeNote]


RoundScore.model_rebuild()
