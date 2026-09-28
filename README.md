# homebox-agent

[![Build and Push Docker Image](https://github.com/benfugate/homebox-agent/actions/workflows/publish.yml/badge.svg)](https://github.com/benfugate/homebox-agent/actions/workflows/publish.yml)

A Discord bot for a household inventory in [Homebox](https://github.com/sysadminsmedia/homebox).
Tell it where you put things ("attic, blue bin on the left: Christmas lights, tree
stand, two wreaths"), send it a photo of a bin, or ask where something is.

The image, `ghcr.io/benfugate/homebox-agent:latest`, is
[claude-hermes-container](https://github.com/benfugate/claude-hermes-container)
restricted to one MCP server:

| File | What it is |
|---|---|
| `homebox_mcp.py` | MCP server (stdio) with the Homebox tools: list/find/contents/details, create location, add/update/move/delete, attach photo. Runs from a venv built into the image. |
| `CLAUDE.md` | The agent's instructions, copied into `/root/.claude/` on every start. |
| `config/` | The MCP registration (`/root/.mcp.json`), the Claude Code setting that approves it, and the hermes settings seeded on first start. |
| `homebox-entrypoint.sh` | Prepares the `/root/.claude` volume, then runs the claude-hermes entrypoint. |
| `deploy/` | Unraid template and `deploy.sh`. |
| `tests/` | `test_offline.py` runs in CI inside the built image; `test_live.py` exercises every tool against a real Homebox. |

## Locked down

- hermes security level `strict` (no Bash, WebSearch, WebFetch) plus Write, Edit
  and NotebookEdit blocked, so the only things it can change are through the
  Homebox tools.
- No SSH keys or other credentials beyond the Homebox API key and the Discord token.
- `attach_photo` only uploads image files, and every ID is validated as a UUID.

## Requirements

- Homebox **0.26.x**, which has the entities API (items and locations are both
  `/v1/entities`). Pin Homebox's image to `0.26` and run `test_live.py` before
  moving it to a new minor version.
- A Claude subscription for Claude Code.
- A Discord bot with **Message Content Intent** enabled.

## Running it

The container needs a persistent `/root/.claude` volume and two variables:

| Variable | |
|---|---|
| `HOMEBOX_URL` | Homebox base URL as reachable from the container, e.g. `http://192.0.2.10:3100` |
| `HOMEBOX_API_KEY` | An API key from Homebox's Profile page |

On Unraid, `deploy/my-homebox-agent.xml` is a template, and `deploy/deploy.sh`
installs it and (re)creates the container over SSH using the values in
`deploy/.env` (copy `deploy/.env.example`).

After the first start:

1. Log Claude in: `docker exec -it homebox-agent claude`, then `/login`.
2. In `<volume>/hermes/settings.json`, set `discord.token`, `discord.allowedUserIds`
   (the Discord user IDs allowed to use it) and `discord.listenChannels` (the
   channel it answers in without being mentioned), then restart the container.

Model, security level and Discord settings live in that file and are never
overwritten by image updates. `CLAUDE.md` is replaced on every start.

## Pipeline

- `publish.yml` builds on every push to `main`, runs the offline tests inside the
  image, then pushes `latest` and the short commit SHA to ghcr.io.
- Dependabot bumps the base-image digest when claude-hermes-container publishes,
  and `ci.yml` auto-merges those bumps once the PR build passes, which triggers a
  publish. `requirements.txt` and GitHub Actions bumps are merged by hand; run
  `test_live.py` before merging an MCP SDK bump.

Auto-merge needs a `MERGE_GITHUB_TOKEN` **Dependabot** secret (a fine-grained PAT
with Contents and Pull requests read/write on this repo), "Allow auto-merge" in
the repo settings, and Actions allowed to approve pull requests.

## Tests

```bash
# no Homebox needed
uv run --with-requirements requirements.txt tests/test_offline.py

# every tool against a real Homebox, inside a throwaway location it deletes afterwards
HOMEBOX_URL=http://... HOMEBOX_API_KEY=hb_... \
  uv run --with-requirements requirements.txt tests/test_live.py
```
