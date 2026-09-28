#!/usr/bin/env bash
# Install the Unraid template and (re)create the container over SSH, using deploy/.env.
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
AGENT_LOG_MODE=${AGENT_LOG_MODE:-changes}
AGENT_FEEDBACK=${AGENT_FEEDBACK:-on}
TRANSCRIPT_RETENTION_DAYS=${TRANSCRIPT_RETENTION_DAYS:-365}
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
    -e "s#(Target=\"AGENT_LOG_MODE\"[^>]*>)[^<]*<#\1$AGENT_LOG_MODE<#" \
    -e "s#(Target=\"AGENT_FEEDBACK\"[^>]*>)[^<]*<#\1$AGENT_FEEDBACK<#" \
    -e "s#(Target=\"TRANSCRIPT_RETENTION_DAYS\"[^>]*>)[^<]*<#\1$TRANSCRIPT_RETENTION_DAYS<#" \
    deploy/my-homebox-agent.xml | remote "cat > $TEMPLATE"

echo "==> pulling $IMAGE"
remote "docker pull -q $IMAGE >/dev/null"

echo "==> (re)creating $NAME"
remote "docker stop -t 20 $NAME >/dev/null 2>&1; docker rm $NAME >/dev/null 2>&1; true"
remote "docker run -d --name $NAME --net bridge --restart unless-stopped \
    -e TZ=$TZ -e IS_SANDBOX=1 \
    -e HOMEBOX_URL=$HOMEBOX_URL -e HOMEBOX_API_KEY=$HOMEBOX_API_KEY \
    -e AGENT_LOG_MODE=$AGENT_LOG_MODE -e AGENT_FEEDBACK=$AGENT_FEEDBACK \
    -e TRANSCRIPT_RETENTION_DAYS=$TRANSCRIPT_RETENTION_DAYS \
    -l net.unraid.docker.managed=dockerman -l net.unraid.docker.icon=$ICON \
    -v $DATA:/root/.claude:rw \
    $IMAGE >/dev/null"

echo "==> checking it started"
sleep 8
remote "docker inspect $NAME --format '{{.Name}}: {{.State.Status}}, restarts {{.RestartCount}}'; \
    docker logs $NAME 2>&1 | grep -E 'daemon started|Aborted|Discord gateway|Bootstrap complete' | tail -4"
