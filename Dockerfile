# Household inventory agent: claude-hermes restricted to one MCP server, for Homebox.
#
# The base is pinned by digest; Dependabot bumps it and ci.yml auto-merges the bump,
# so this image follows every claude-hermes-container release.
FROM ghcr.io/benfugate/claude-hermes-container:latest@sha256:1046123e79c16efac7ff0a8e5a9640084f9674cfd911eaa750bca676cc025d3f

# The MCP server gets its own venv on the image's system Python, so nothing is
# downloaded at runtime. uv's cache and managed Pythons point into the
# /root/.claude volume in the base image, and build-time writes to a volume are
# thrown away, hence --no-cache and an explicit interpreter.
COPY requirements.txt /opt/homebox-agent/requirements.txt
RUN UV_NO_CACHE=1 /root/.local/bin/uv venv --python /usr/bin/python3 /opt/homebox-agent/.venv \
 && UV_NO_CACHE=1 /root/.local/bin/uv pip install \
        --python /opt/homebox-agent/.venv/bin/python \
        -r /opt/homebox-agent/requirements.txt

COPY homebox_mcp.py CLAUDE.md config/claude-settings.json config/hermes-settings.json /opt/homebox-agent/
# Claude Code reads project MCP servers from the working directory, which is /root,
# outside the volume, so this one can live in the image.
COPY config/mcp.json /root/.mcp.json
COPY homebox-entrypoint.sh /homebox-entrypoint.sh
RUN chmod +x /homebox-entrypoint.sh

ENTRYPOINT ["/homebox-entrypoint.sh"]
CMD ["start"]
