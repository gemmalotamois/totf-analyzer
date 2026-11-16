"""Readers for TotF2 T2SR session layers.

Layouts below were measured on schema-15 recordings (120 Hz tick base).
Fields that are not exact game-struct names are documented at the call site.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

MAGIC = b"T2SR"

# punches.bin: fixed 140-byte records after a 40-byte header.
PUNCH_HEADER = 40
PUNCH_RECORD = 140
# events.bin: fixed 69-byte records after a 40-byte header.
EVENT_HEADER = 40
EVENT_RECORD = 69

# Type-1 events with this flag match official scored hits received by that track.
SCORED_HIT_TYPE = 1
SCORED_HIT_FLAG = 1
SCORED_HIT_FLAG_OFFSET = 8


@dataclass(frozen=True)
class PunchRecord:
    tick: int
    hand_index: int
    kinematics: bool
    direction: tuple[float, float, float] | None
    speed: float | None


@dataclass(frozen=True)
class EventRecord:
    tick: int
    type_code: int
    subtype: int
    scored_hit: bool


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def assert_t2sr(data: bytes, label: str) -> None:
    if len(data) < 8 or data[:4] != MAGIC:
        raise ValueError(f"{label} is not a T2SR layer")


def find_record_start(
    data: bytes,
    stride: int,
    tick_step: int,
    origin_tick: int,
    search: int = 500,
) -> int:
    """Locate the first packed record whose tick advances by ``tick_step``."""
    limit = min(len(data) - 2 * stride - 4, search)
    if limit < 0:
        raise ValueError(f"layer shorter than one record (stride {stride})")
    for off in range(0, limit):
        t0 = _u32(data, off)
        if not origin_tick <= t0 <= origin_tick + tick_step * 2:
            continue
        t1 = _u32(data, off + stride)
        t2 = _u32(data, off + 2 * stride)
        if t1 == t0 + tick_step and t2 == t0 + 2 * tick_step:
            return off
    raise ValueError(f"could not find tick records (stride {stride}, step {tick_step})")


def iter_records(data: bytes, start: int, stride: int) -> int:
    if stride <= 0 or start < 0 or start > len(data):
        raise ValueError("invalid record framing")
    return (len(data) - start) // stride


def read_tick_stream(
    data: bytes,
    start: int,
    stride: int,
    payload_dtype: str,
    payload_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    count = iter_records(data, start, stride)
    if count <= 0:
        raise ValueError("tick stream contained no records")
    raw = np.frombuffer(data, dtype=np.uint8, offset=start, count=count * stride)
    buf = raw.reshape(count, stride)
    ticks = buf[:, 0:4].copy().view("<u4").reshape(count)
    width = np.dtype(payload_dtype).itemsize * payload_count
    payload = buf[:, 4 : 4 + width].copy().view(payload_dtype).reshape(count, payload_count)
    return ticks, payload


def read_punches(data: bytes) -> list[PunchRecord]:
    assert_t2sr(data, "punches.bin")
    if (len(data) - PUNCH_HEADER) % PUNCH_RECORD != 0:
        raise ValueError("punches.bin size is not a whole number of 140-byte records")
    count = (len(data) - PUNCH_HEADER) // PUNCH_RECORD
    punches: list[PunchRecord] = []
    for index in range(count):
        rec = data[PUNCH_HEADER + index * PUNCH_RECORD : PUNCH_HEADER + (index + 1) * PUNCH_RECORD]
        tick = _u32(rec, 0)
        hand_index = rec[10]
        kinematics = rec[9] == 0
        direction: tuple[float, float, float] | None = None
        speed: float | None = None
        if kinematics:
            x, y, z = struct.unpack_from("<fff", rec, 36)
            mag = (x * x + y * y + z * z) ** 0.5
            if mag >= 0.25:
                direction = (x, y, z)
            raw_speed = struct.unpack_from("<f", rec, 80)[0]
            if raw_speed == raw_speed and 0.5 < raw_speed < 30.0:
                speed = float(raw_speed)
        punches.append(
            PunchRecord(
                tick=tick,
                hand_index=int(hand_index),
                kinematics=kinematics,
                direction=direction,
                speed=speed,
            )
        )
    return punches


def read_events(data: bytes) -> list[EventRecord]:
    assert_t2sr(data, "events.bin")
    if (len(data) - EVENT_HEADER) % EVENT_RECORD != 0:
        raise ValueError("events.bin size is not a whole number of 69-byte records")
    count = (len(data) - EVENT_HEADER) // EVENT_RECORD
    events: list[EventRecord] = []
    for index in range(count):
        rec = data[EVENT_HEADER + index * EVENT_RECORD : EVENT_HEADER + (index + 1) * EVENT_RECORD]
        type_code = int(rec[4])
        events.append(
            EventRecord(
                tick=_u32(rec, 0),
                type_code=type_code,
                subtype=int(rec[5]),
                scored_hit=type_code == SCORED_HIT_TYPE and rec[SCORED_HIT_FLAG_OFFSET] == SCORED_HIT_FLAG,
            )
        )
    return events


def read_punishment(data: bytes, origin_tick: int) -> tuple[np.ndarray, np.ndarray]:
    assert_t2sr(data, "punishment.bin")
    start = find_record_start(data, stride=16, tick_step=4, origin_tick=origin_tick)
    ticks, payload = read_tick_stream(data, start, 16, "<f4", 3)
    return ticks, payload


def read_stiffness(data: bytes, origin_tick: int) -> tuple[np.ndarray, np.ndarray]:
    assert_t2sr(data, "stiffness.bin")
    start = find_record_start(data, stride=17, tick_step=1, origin_tick=origin_tick)
    ticks, payload = read_tick_stream(data, start, 17, "<f4", 3)
    return ticks, payload


def read_guard_channels(data: bytes, origin_tick: int) -> tuple[np.ndarray, np.ndarray]:
    """Return ticks and the first quantized sample of brace, friction, mass, ladder."""
    assert_t2sr(data, "guard.bin")
    if b"brace" not in data[:160] or b"friction" not in data[:160]:
        raise ValueError("guard.bin is missing brace/friction channel names")
    start = find_record_start(data, stride=52, tick_step=1, origin_tick=origin_tick)
    ticks, samples = read_tick_stream(data, start, 52, "<i2", 24)
    # Four named channels, six int16 samples each. The first sample is the
    # channel's primary level; the rest of the group is not independently named.
    primary = samples.reshape(len(ticks), 4, 6)[:, :, 0].astype(np.float32)
    return ticks, primary


def read_skeleton_positions(data: bytes, origin_tick: int) -> tuple[np.ndarray, np.ndarray]:
    """Dequantize 21 joints × xyz.

    Each record is a tick plus 126 uint16 values. Joint ``j`` occupies six
    samples; the first three dequantize as position over the header range
    [-10, 10] meters. That range is the ``position`` channel min/max in the
    layer header.
    """
    assert_t2sr(data, "skeleton.bin")
    start = find_record_start(data, stride=256, tick_step=1, origin_tick=origin_tick)
    ticks, samples = read_tick_stream(data, start, 256, "<u2", 126)
    index = np.array([joint * 6 + axis for joint in range(21) for axis in range(3)], dtype=np.int64)
    xyz = samples[:, index].reshape(len(ticks), 21, 3).astype(np.float32)
    positions = xyz / np.float32(65535.0) * np.float32(20.0) - np.float32(10.0)
    return ticks, positions
