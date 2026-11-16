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
- `get_key_moments` — optional `type` filter (`stun`, `knockdown`, `knockout`, `exchange`, `punishment_spike`, `high_velocity_landed`, `head_drop`, `hurt_event`)
- `get_series` — `punishment_0`, `punishment_1`, `punishment_2`, `guard_up`, `guard_brace`, `guard_friction`, `guard_mass`, `guard_ladder`, `head_speed`, `head_height`, `punch_density`, `stiffness_max`. Optional `round_index` returns that round with time starting at the bell.

## What the numbers mean

| Measure | Definition |
| --- | --- |
| Thrown | Punch records in `punches.bin` |
| Landed | Type-1 events on the defender's track with flag byte 8 set. That count is the defender's official hits taken, and the attacker's landed punches. |
| Accuracy | Landed / thrown |
| Hand | Record byte 10 (`hand_0` / `hand_1`). Left/right is applied only when skeleton motion agrees. |
| Trajectory | `straight`, `hook`, `uppercut`, `overhand`, or `unknown`, from the direction vector when the kinematics block is present |
| Speed | Float at punch-record offset 80, treated as m/s |
| Guard up | Both lateral hand joints within 0.38 m below the head and 0.40 m horizontally |
| Under fire | 0.05 s before through 0.30 s after each opponent punch record |
| Head | Skeleton joint with the highest median height |
| Punishment | Three float meters in `punishment.bin`, separate from official received damage |
| Exchange | A run of 1-second bins where each fighter has at least 2 punch records |
| Stun / knockdown / knockout | Event type codes whose counts match that corner's scorecard totals |

`decode_status` on every report marks each field `confirmed`, `inferred`, or `unavailable`.

## CLI

```bash
totf-analyzer analyze Recordings/session.zip
totf-analyzer analyze Recordings/session.zip --json
```
