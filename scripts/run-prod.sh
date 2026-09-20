#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9>"${TMPDIR:-/tmp}/beaver-${UID}-production.lock"
flock -n 9 || { echo 'Production is already running. Use the existing game window.' >&2; exit 1; }
relay_pid=''
game_pid=''
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$game_pid" ]]; then kill -TERM "$game_pid" 2>/dev/null || true; fi
  if [[ -n "$relay_pid" ]]; then kill "$relay_pid" 2>/dev/null || true; fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
/usr/bin/python -u -m beaver_battle.relay --port "${BEAVER_RECEIVER:-/dev/ttyUSB0}" &
relay_pid=$!
sleep 0.3
kill -0 "$relay_pid" 2>/dev/null || exit 1
.venv/bin/python -u -m beaver_battle --display "${BEAVER_DISPLAY:-1}" --fullscreen --no-names --mute --calibrate "$@" &
game_pid=$!
wait -n "$relay_pid" "$game_pid"
