#!/usr/bin/env bash
# Deploy the agent to an Unraid host over SSH.
#
# Installs the Unraid template with your values filled in, pulls the latest image
# from ghcr.io and recreates the container. The image's entrypoint prepares the
# appdata volume (instructions, MCP approval, first-run hermes settings), so
# nothing else is copied. Safe to re-run.
#
# Settings come from deploy/.env; see deploy/.env.example.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source deploy/.env
: "${DEPLOY_HOST:?set DEPLOY_HOST in deploy/.env}"
: "${HOMEBOX_URL:?set HOMEBOX_URL in deploy/.env}"
: "${HOMEBOX_API_KEY:?set HOMEBOX_API_KEY in deploy/.env}"
NAME=${CONTAINER_NAME:-homebox-agent}
DATA=${APPDATA:-/mnt/user/appdata/homebox-agent}
TZ=${TZ:-UTC}
IMAGE=${IMAGE:-ghcr.io/benfugate/homebox-agent:latest}
TEMPLATE=/boot/config/plugins/dockerMan/templates-user/my-$NAME.xml
ICON=https://cdn.jsdelivr.net/gh/selfhst/icons/png/homebox.png

remote() { ssh -o BatchMode=yes "$DEPLOY_HOST" "$@" 2> >(grep -v -e post-quantum -e 'store now' -e 'upgraded. See' >&2); }

echo "==> Unraid template"
sed -E \
    -e "s#<Name>homebox-agent</Name>#<Name>$NAME</Name>#" \
    -e "s#(Target=\"HOMEBOX_URL\"[^>]*>)[^<]*<#\1$HOMEBOX_URL<#" \
    -e "s#(Target=\"HOMEBOX_API_KEY\"[^>]*>)[^<]*<#\1$HOMEBOX_API_KEY<#" \
    -e "s#(Target=\"/root/.claude\"[^>]*>)[^<]*<#\1$DATA<#" \
    deploy/my-homebox-agent.xml | remote "cat > $TEMPLATE"

echo "==> pulling $IMAGE"
remote "docker pull -q $IMAGE >/dev/null"

# Recreate rather than restart so a new image or API key takes effect. Everything
# that has to persist lives in the $DATA volume.
echo "==> (re)creating $NAME"
remote "docker stop -t 20 $NAME >/dev/null 2>&1; docker rm $NAME >/dev/null 2>&1; true"
remote "docker run -d --name $NAME --net bridge --restart unless-stopped \
    -e TZ=$TZ -e IS_SANDBOX=1 \
    -e HOMEBOX_URL=$HOMEBOX_URL -e HOMEBOX_API_KEY=$HOMEBOX_API_KEY \
    -l net.unraid.docker.managed=dockerman -l net.unraid.docker.icon=$ICON \
    -v $DATA:/root/.claude:rw \
    $IMAGE >/dev/null"

echo "==> checking it started"
sleep 8
remote "docker inspect $NAME --format '{{.Name}}: {{.State.Status}}, restarts {{.RestartCount}}'; \
    docker logs $NAME 2>&1 | grep -E 'daemon started|Aborted|Discord gateway|Bootstrap complete' | tail -4"
