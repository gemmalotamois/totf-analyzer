#!/usr/bin/env bash
# Choose a foreground run, a systemd user-service install, or uninstall.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$ROOT/.venv/bin/totf-analyzer"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(totf-analyzer-web.service totf-analyzer-mcp.service)
cd "$ROOT"

require_app() {
  if [[ ! -x "$BIN" ]]; then
    echo "totf-analyzer is not installed at $BIN" >&2
    echo "Create the venv and run: pip install -e \".[dev]\"" >&2
    return 1
  fi
}

services_active() {
  systemctl --user is-active --quiet totf-analyzer-web.service && return 0
  systemctl --user is-active --quiet totf-analyzer-mcp.service
}

run_foreground() {
  require_app || return 1
  if services_active; then
    echo "The systemd services are already running."
    echo "Dashboard  http://127.0.0.1:8000"
    echo "MCP        http://127.0.0.1:8010/mcp"
    echo "Stop them before a foreground run:"
    echo "  systemctl --user stop totf-analyzer-web.service totf-analyzer-mcp.service"
    return 1
  fi
  if [[ ! -f "$ROOT/web/dist/index.html" ]]; then
    echo "The dashboard UI is not built. The API will still run."
    echo "Build it with: cd web && npm install && npm run build"
  fi

  export TOTF_RECORDINGS="${TOTF_RECORDINGS:-$ROOT/Recordings}"
  mkdir -p "$TOTF_RECORDINGS"

  local web_pid="" mcp_pid=""
  cleanup() {
    trap - RETURN INT TERM
    if [[ -n "$web_pid" ]]; then
      kill "$web_pid" 2>/dev/null || true
    fi
    if [[ -n "$mcp_pid" ]]; then
      kill "$mcp_pid" 2>/dev/null || true
    fi
    wait "$web_pid" "$mcp_pid" 2>/dev/null || true
    web_pid=""
    mcp_pid=""
  }

  echo "Web dashboard  http://127.0.0.1:8000"
  echo "MCP            http://127.0.0.1:8010/mcp"
  echo "Press Ctrl+C to stop."

  "$BIN" serve --host 127.0.0.1 --port 8000 &
  web_pid=$!
  "$BIN" mcp --host 127.0.0.1 --port 8010 &
  mcp_pid=$!
  trap cleanup RETURN INT TERM
  set +e
  wait -n "$web_pid" "$mcp_pid"
  set -e
  cleanup
}

install_service() {
  require_app || return 1
  "$ROOT/deploy/setup-services.sh"
  systemctl --user enable --now totf-analyzer-web.service totf-analyzer-mcp.service
  systemctl --user restart totf-analyzer-web.service totf-analyzer-mcp.service
  echo "Installed and started the user services."
  echo "Dashboard  http://127.0.0.1:8000"
  echo "MCP        http://127.0.0.1:8010/mcp"
  if ! loginctl show-user "$USER" -p Linger --value 2>/dev/null | grep -qx yes; then
    echo "These services stop at logout. To start them at boot:"
    echo "  sudo loginctl enable-linger $USER"
  fi
}

uninstall_service() {
  local unit
  for unit in "${UNITS[@]}"; do
    systemctl --user disable --now "$unit" || true
    rm -f "$UNIT_DIR/$unit" "$UNIT_DIR/default.target.wants/$unit"
  done
  systemctl --user daemon-reload
  systemctl --user reset-failed "${UNITS[@]}" 2>/dev/null || true
  echo "Removed the user services."
}

if [[ $# -ne 0 ]]; then
  echo "Usage: ./totf.sh" >&2
  echo "Run it with no arguments to open the menu." >&2
  exit 2
fi

echo "TotF Analyzer"
PS3="Select: "
options=(
  "Run now, without installing a service"
  "Install as a systemd service"
  "Uninstall the systemd service"
  "Quit"
)
select _choice in "${options[@]}"; do
  case "$REPLY" in
    1) run_foreground || true ;;
    2) install_service || true ;;
    3) uninstall_service || true ;;
    4) break ;;
    *) echo "Enter 1, 2, 3, or 4." ;;
  esac
done
