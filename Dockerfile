FROM ghcr.io/benfugate/claude-hermes-container:latest@sha256:f50f301e50f598f177e7d630a41f6f8d9a6657da4c2679bf79e9cfac9a296e5d

# uv's cache and managed Pythons point into the /root/.claude volume, which build steps can't write to.
COPY requirements.txt /opt/homebox-agent/requirements.txt
RUN UV_NO_CACHE=1 /root/.local/bin/uv venv --python /usr/bin/python3 /opt/homebox-agent/.venv \
 && UV_NO_CACHE=1 /root/.local/bin/uv pip install \
        --python /opt/homebox-agent/.venv/bin/python \
        -r /opt/homebox-agent/requirements.txt

COPY homebox_mcp.py CLAUDE.md config/claude-settings.json config/hermes-settings.json /opt/homebox-agent/
COPY config/mcp.json /root/.mcp.json
COPY homebox-entrypoint.sh /homebox-entrypoint.sh
RUN chmod +x /homebox-entrypoint.sh

ENTRYPOINT ["/homebox-entrypoint.sh"]
CMD ["start"]
