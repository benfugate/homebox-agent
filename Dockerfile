FROM ghcr.io/benfugate/claude-hermes-container:latest@sha256:8fdabaccae4aad4fcbaa7eb6999f96fb82104dac60c8d6ec92dccc1985227303

# uv's cache and managed Pythons point into the /root/.claude volume, which build steps can't write to.
COPY requirements.txt /opt/homebox-agent/requirements.txt
RUN UV_NO_CACHE=1 /root/.local/bin/uv venv --python /usr/bin/python3 /opt/homebox-agent/.venv \
 && UV_NO_CACHE=1 /root/.local/bin/uv pip install \
        --python /opt/homebox-agent/.venv/bin/python \
        -r /opt/homebox-agent/requirements.txt

COPY homebox_mcp.py session_reset.py CLAUDE.md config/claude-settings.json config/hermes-settings.json /opt/homebox-agent/
COPY config/mcp.json /root/.mcp.json
COPY homebox-entrypoint.sh /homebox-entrypoint.sh
RUN chmod +x /homebox-entrypoint.sh

ENTRYPOINT ["/homebox-entrypoint.sh"]
CMD ["start"]
