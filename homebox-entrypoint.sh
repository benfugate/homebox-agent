#!/bin/bash
# Prepares the /root/.claude volume, then hands off to the claude-hermes entrypoint.
set -e

AGENT=/opt/homebox-agent
STATE=/root/.claude

mkdir -p "$STATE/hermes"

# /root/.claude is a bind mount, so anything the image put there would be hidden.
# Copy the instructions in on every start so an image update always takes effect.
install -m 644 "$AGENT/CLAUDE.md" "$STATE/CLAUDE.md"

# Approve the homebox MCP server. Merged, because hermes adds its own statusLine key.
settings="$STATE/settings.json"
[ -s "$settings" ] || echo '{}' > "$settings"
jq -s '.[0] * .[1]' "$settings" "$AGENT/claude-settings.json" > "$settings.new"
mv "$settings.new" "$settings"

# Hermes settings hold the Discord token and IDs, so they are only seeded, never
# overwritten. Seeding here also keeps the base entrypoint from writing its own
# default (Opus, no restrictions).
if [ ! -f "$STATE/hermes/settings.json" ]; then
    install -m 600 "$AGENT/hermes-settings.json" "$STATE/hermes/settings.json"
fi

# hermes refuses to start while daemon.pid names a live process, and inside a
# container the recorded PID is always 1: this very process. After an unclean
# stop the file is left behind and the container crash-loops, so remove it.
# Nothing else can be running in a container that is only now starting.
rm -f "$STATE/hermes/daemon.pid"

exec /entrypoint.sh "$@"
