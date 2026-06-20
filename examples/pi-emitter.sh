#!/usr/bin/env sh
set -eu

STATUS_DIR="${AGENT_STATUS_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-status}"
AGENT_ID="pi-$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex)
PY
)"
WORKSPACE="$(pwd)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 -m agent_status emit \
  --status-dir "$STATUS_DIR" \
  --agent-id "$AGENT_ID" \
  --agent-name "pi" \
  --lifecycle running \
  --workspace "$WORKSPACE" \
  --pid "$$" \
  --last-activity-at "$NOW" \
  --task-id "task-demo" \
  --context-id "ctx-demo" \
  --task-state input-required \
  --task-summary "waiting for user input" \
  --task-status-timestamp "$NOW" \
  --meta branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf unknown)"
