#!/usr/bin/env bash
# Write user systemd units for this checkout. Paths come from the repo
# location and $HOME, so the units are not committed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/.venv/bin/totf-analyzer"
RECORDINGS="$ROOT/Recordings"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
GAME="$HOME/.local/share/Steam/steamapps/compatdata/3279600/pfx/drive_c/users/steamuser/AppData/LocalLow/Halfbrick Studios/The Thrill of the Fight 2/SessionRecordings"

if [[ ! -x "$BIN" ]]; then
  echo "totf-analyzer is not installed at $BIN" >&2
  echo "Create the venv and run: pip install -e \".[dev]\"" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR" "$RECORDINGS"

cat >"$UNIT_DIR/totf-analyzer-web.service" <<EOF
[Unit]
Description=TotF Analyzer web dashboard (port 8000)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment="TOTF_RECORDINGS=$RECORDINGS"
Environment="TOTF_GAME_RECORDINGS=$GAME"
ExecStart="$BIN" serve --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=default.target
EOF

cat >"$UNIT_DIR/totf-analyzer-mcp.service" <<EOF
[Unit]
Description=TotF Analyzer MCP (Streamable HTTP on port 8010)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment="TOTF_RECORDINGS=$RECORDINGS"
ExecStart="$BIN" mcp --host 127.0.0.1 --port 8010
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload

echo "Wrote $UNIT_DIR/totf-analyzer-web.service"
echo "Wrote $UNIT_DIR/totf-analyzer-mcp.service"
echo "Enable them with:"
echo "  systemctl --user enable --now totf-analyzer-web.service"
echo "  systemctl --user enable --now totf-analyzer-mcp.service"
echo "If they are already running, restart them to load the new units."
