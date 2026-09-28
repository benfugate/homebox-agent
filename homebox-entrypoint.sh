#!/bin/bash
set -e

AGENT=/opt/homebox-agent
STATE=/root/.claude

mkdir -p "$STATE/hermes"
install -m 644 "$AGENT/CLAUDE.md" "$STATE/CLAUDE.md"

days=${TRANSCRIPT_RETENTION_DAYS:-365}
# 0 would stop Claude Code saving sessions, which hermes needs to resume conversations.
if ! [[ "$days" =~ ^[0-9]+$ ]] || [ "$days" -lt 1 ]; then
    echo "[homebox-agent] TRANSCRIPT_RETENTION_DAYS=$days is not a whole number of days >= 1; using 365"
    days=365
fi
settings="$STATE/settings.json"
[ -s "$settings" ] || echo '{}' > "$settings"
jq -s --argjson days "$days" '.[0] * .[1] * {cleanupPeriodDays: $days}' \
    "$settings" "$AGENT/claude-settings.json" > "$settings.new"
mv "$settings.new" "$settings"

if [ ! -f "$STATE/hermes/settings.json" ]; then
    install -m 600 "$AGENT/hermes-settings.json" "$STATE/hermes/settings.json"
fi

# A pid file left by an unclean stop always says 1, which is this process, so hermes would refuse to start.
rm -f "$STATE/hermes/daemon.pid"

exec /entrypoint.sh "$@"
