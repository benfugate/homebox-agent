#!/usr/bin/env bash
# Usage: deploy/logs.sh [audit|feedback] [count] [raw]
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source deploy/.env
: "${DEPLOY_HOST:?set DEPLOY_HOST in deploy/.env}"
DATA=${APPDATA:-/mnt/user/appdata/homebox-agent}

which=${1:-audit}
count=${2:-20}
case "$which" in audit|feedback) ;; *) echo "usage: $0 [audit|feedback] [count] [raw]" >&2; exit 2 ;; esac
[[ "$count" =~ ^[0-9]+$ ]] || { echo "count must be a number" >&2; exit 2; }

lines=$(ssh -o BatchMode=yes "$DEPLOY_HOST" "tail -n $count $DATA/homebox-agent/$which.jsonl 2>/dev/null" \
    2> >(grep -v -e post-quantum -e 'store now' -e 'upgraded. See' >&2) || true)
[ -n "$lines" ] || { echo "no $which entries yet"; exit 0; }

if [ "${3:-}" = raw ]; then
    printf '%s\n' "$lines"
elif [ "$which" = audit ]; then
    printf '%s\n' "$lines" | jq -r '
        "\(.ts[0:19] | sub("T"; " "))  \(if .ok then "ok " else "ERR" end)  \(.tool)  \(.ms)ms  \(.args | tojson)",
        "    \(.result | split("\n") | .[0:4] | join("\n    "))"'
else
    printf '%s\n' "$lines" | jq -r '
        "\(.ts[0:19] | sub("T"; " "))",
        "    asked:      \(.asked)",
        "    wrong:      \(.wrong)",
        (if .correction != "" then "    correction: \(.correction)" else empty end)'
fi
