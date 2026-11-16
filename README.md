# TOTF Analyzer

Factual analytics for Thrill of the Fight 2 session recordings. Drop a session zip in `Recordings/`, then read punch counts, accuracy, speed, guard, head movement, and timestamped moments in the local dashboard or over MCP.

The tool does not rate fighters or recommend what to fix. Measurement definitions are listed below so a coach can interpret the numbers.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
./totf.sh
```

`./totf.sh` opens a menu. It can run the dashboard and MCP server in the foreground, install them as systemd user services, or uninstall those services. Session zips live in `Recordings/` (override with `TOTF_RECORDINGS`).

## Web dashboard

```bash
systemctl --user enable --now totf-analyzer-web.service
```

With lingering enabled the unit starts at boot as the installing user, binds `127.0.0.1:8000`, and reads this project's `Recordings/` directory. `totf-analyzer serve` starts the same server in the foreground.

For the React UI during development:

```bash
cd web
npm install
npm run dev
```

Vite serves `http://127.0.0.1:5173` and proxies `/api` to the Python server. `npm run build` writes `web/dist`; `totf-analyzer serve` then serves that UI at `/`.

## MCP

The MCP server listens on Streamable HTTP and reads session zips itself. Cursor connects to that URL, so the agent does not need permission to open `Recordings/`.

```bash
systemctl --user enable --now totf-analyzer-mcp.service
```

The unit runs as the installing user, binds `127.0.0.1:8010`, and sets `TOTF_RECORDINGS` to this project's `Recordings/` directory.

```json
{
  "mcpServers": {
    "totf-analyzer": {
      "url": "http://127.0.0.1:8010/mcp"
    }
  }
}
```

Tools:

- `list_recordings`
- `analyze_recording` — full analytics JSON
- `get_fighter_stats` — one corner (`1` blue, `2` red)
- `get_key_moments` — optional `type` filter (`stun`, `knockdown`, `knockout`, `exchange`, `punishment_spike`, `high_velocity_landed`, `head_drop`, `hurt_event`, `angle_cut`)
- `get_series` — `punishment_0`, `punishment_1`, `punishment_2`, `guard_up`, `guard_brace`, `guard_friction`, `guard_mass`, `guard_ladder`, `head_speed`, `head_height`, `punch_density`, `stiffness_max`. Optional `round_index` returns that round with time starting at the bell.

## What the numbers mean

| Measure | Definition |
| --- | --- |
| Thrown | Punch records in `punches.bin` |
| Landed | Type-1 events on the defender's track with flag byte 8 set. That count is the defender's official hits taken, and the attacker's landed punches. |
| Accuracy | Landed / thrown |
| Hand | Record byte 10: `0` right, `1` left. Confirmed when that wrist moves more than the other from launch to end. |
| Trajectory | Byte 32: `1` jab, `2` cross, `3` left_hook, `4` right_hook, `6` uppercut. Class `0` is the wrist's launch-to-peak displacement in the opponent frame (forward, up, right). |
| Speed | Float at punch-record offset 80, treated as m/s |
| Guard up | Both glove centers within 0.38 m below head joint 4 and 0.40 m horizontally. A glove center is the wrist extended along the forearm by half of `m_scaledHandLength` (0.095 m if the snapshot omits it). Fight-level percent uses bell-to-bell ticks only. `left_guard_up_percent`, `right_guard_up_percent`, `both_guard_up_percent`, and `either_guard_up_percent` use that same box per hand. `both_guard_up_percent` is `guard_up_percent`. Each is also reported under fire. |
| Off-hand kept | At a kinematic punch's peak glove extension (byte 9 is 0), the other glove's drop below the head is under 0.28 m and its horizontal distance is under 0.38 m. A glove above the head has drop 0. Reported overall and when throwing left or right. Drop and horizontal flare (median, p90) are split by lead and rear: orthodox lead is left, southpaw lead is right. |
| Retraction | From that peak until the punching glove re-enters the same 0.28 / 0.38 m box, the next same-hand launch, or 800 ms. A glove already in that box at the peak, or a same-hand launch before re-entry, is excluded. `retraction_returned_percent` is returns divided by returns plus misses, kinematic punches only. Time is median, mean, and p90 of returns. Droop is the largest drop of head-relative glove height below the straight chord from peak to re-entry; `sag_over_10cm_percent` is the share of returns whose droop exceeds 0.10 m. Directness is chord length divided by path length, in [0, 1], as median and mean. |
| Footwork | Hips joint 0 from the bell through the end bell. The warning and the time after the end bell are left out. Radial and tangential distances are absolute step lengths on the opponent frame (forward toward the opponent's hips, right is the fighter's right). `total_distance_m` is their sum, and `tangential_share` is tangential divided by that sum. Positive tangential motion is counterclockwise around the opponent when viewed from above. Clinch is under 0.85 m, pocket is 0.85 m through 1.35 m, and outside is beyond 1.35 m. Shoulder blade (joints 6 and 10) and foot blade (joints 15 and 19) are the median absolute angle of that horizontal line against the fighter's right: 0 is square to the opponent and 90 points at the opponent. Square stance is a shoulder angle under 15 degrees. Lead left is a signed shoulder angle of at least 15 degrees. Square while punching uses the shoulder angle at each punch's launch tick. An angle cut merges 0.75 s windows in which this fighter's own hips sweep at least 20 degrees around the opponent while the hips stay under 1.8 m apart. Entry and exit angles use that same sweep. A new punch group starts when launch ticks are more than 0.65 s apart. Entry runs from the first launch to the last punch's end. Exit is the next 0.5 s, stopping at the end bell. Foot blade is inferred because Quest ankle joints are IK-estimated. |
| Under fire | 0.05 s before through 0.30 s after each opponent punch record |
| Head | Skeleton joint 4. `head_lateral_range` is the horizontal extent, hypot(max x − min x, max z − min z). |
| Guard channels | `guard_brace`, `guard_friction`, `guard_mass`, and `guard_ladder` are uint16 samples scaled by the header min/max. Schema 15 ranges are brace [0, 8], friction [0, 4], mass [0, 8], ladder [0, 5]. |
| Punishment | Three float meters in `punishment.bin`, separate from official received damage |
| Exchange | A run of 1-second bins where each fighter has at least 2 punch records |
| Stun / knockdown / knockout | Event type codes whose counts match that corner's scorecard totals |

`decode_status` on every report marks each field `confirmed`, `inferred`, or `unavailable`.

## CLI

```bash
totf-analyzer analyze Recordings/session.zip
totf-analyzer analyze Recordings/session.zip --json
```
