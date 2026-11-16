"""Streamable HTTP MCP server. Tools return measurements, not coaching opinions."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from totf_analyzer.analytics.metrics import build_analytics
from totf_analyzer.models.analytics import FightAnalytics
from totf_analyzer.parser.session import COLOR_CORNER, load_session

mcp = MCPServer("totf-analyzer")
_recordings = Path(os.environ.get("TOTF_RECORDINGS", "Recordings"))
_cache: dict[tuple[str, int, int], FightAnalytics] = {}


def configure(recordings: Path) -> None:
    global _recordings
    _recordings = recordings


def _resolve(path: str) -> Path:
    root = _recordings.resolve()
    raw = Path(path)
    candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if candidate.suffix.lower() != ".zip" or not candidate.is_file() or not candidate.is_relative_to(root):
        raise FileNotFoundError(f"session zip not found: {path}")
    return candidate


def _load(path: str) -> FightAnalytics:
    resolved = _resolve(path)
    stat = resolved.stat()
    key = (str(resolved), stat.st_mtime_ns, stat.st_size)
    if key not in _cache:
        _cache[key] = build_analytics(load_session(resolved))
    return _cache[key]


@mcp.tool()
def list_recordings() -> list[dict]:
    """List TotF2 session zip files in the recordings directory."""
    root = _recordings
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.glob("*.zip")):
        stat = path.stat()
        rows.append({"name": path.name, "path": str(path.resolve()), "size": stat.st_size})
    return rows


@mcp.tool()
def analyze_recording(path: str) -> dict:
    """Parse a session zip and return the full FightAnalytics object.

    `path` is a file name or an absolute path inside the recordings directory.
    """
    return _load(path).model_dump(mode="json")


@mcp.tool()
def get_fighter_stats(path: str, corner: int) -> dict:
    """Offense and defense stats for one corner (1 = blue, 2 = red)."""
    report = _load(path)
    fighter = next((item for item in report.fighters if item.corner == corner), None)
    offense = next((item for item in report.offense if item.corner == corner), None)
    defense = next((item for item in report.defense if item.corner == corner), None)
    if fighter is None or offense is None or defense is None:
        raise ValueError(f"corner {corner} is not in this session")
    rounds = []
    for rnd in report.rounds:
        block = rnd.red if corner == COLOR_CORNER["red"] else rnd.blue
        round_offense = next((item for item in rnd.offense if item.corner == corner), None)
        round_defense = next((item for item in rnd.defense if item.corner == corner), None)
        row = {
            "index": rnd.index,
            "completed": rnd.completed,
            "bell_sec": rnd.bell_sec,
            "duration_sec": rnd.duration_sec,
            **block.model_dump(),
        }
        if round_offense is not None:
            row["offense"] = {
                "thrown": round_offense.thrown,
                "landed": round_offense.landed,
                "accuracy": round_offense.accuracy,
                "punches_per_minute": round_offense.punches_per_minute,
                "mean_speed": round_offense.velocity.mean_thrown,
            }
        if round_defense is not None:
            row["defense"] = {
                "guard_up_percent": round_defense.guard_up_percent,
                "guard_up_percent_under_fire": round_defense.guard_up_percent_under_fire,
                "under_fire_seconds": round_defense.under_fire_seconds,
                "head_mean_speed": round_defense.head_mean_speed,
                "received_damage_official": round_defense.received_damage_official,
                "guard_discipline": round_defense.guard_discipline.model_dump(),
            }
        rounds.append(row)
    return {
        "fighter": fighter.model_dump(),
        "offense": offense.model_dump(),
        "defense": defense.model_dump(),
        "rounds": rounds,
    }


@mcp.tool()
def get_key_moments(path: str, type: str | None = None) -> list[dict]:
    """Timestamped moments. Optional `type` filters stun, knockdown, knockout, exchange, and the other moment types."""
    moments = _load(path).key_moments
    if type:
        moments = [moment for moment in moments if moment.type == type]
    return [moment.model_dump(mode="json") for moment in moments]


@mcp.tool()
def get_series(path: str, name: str, corner: int | None = None, round_index: int | None = None) -> list[dict]:
    """Downsampled series by name, optionally for one corner and one round.

    Omit round_index for the whole recording. Pass a completed round index (0-based)
    for that round only; those timestamps are seconds after the bell.

    Names include punishment_0, punishment_1, punishment_2, guard_up, guard_brace,
    head_speed, head_height, punch_density, and stiffness_max.
    """
    report = _load(path)
    if round_index is None:
        source = report.series
    else:
        rnd = next((item for item in report.rounds if item.index == round_index), None)
        if rnd is None:
            raise ValueError(f"round {round_index} is not in this session")
        source = rnd.series
    rows = [series for series in source if series.name == name]
    if corner is not None:
        rows = [series for series in rows if series.corner == corner]
    return [series.model_dump(mode="json") for series in rows]


def main(host: str = "127.0.0.1", port: int = 8010) -> None:
    mcp.run(transport="streamable-http", host=host, port=port)
