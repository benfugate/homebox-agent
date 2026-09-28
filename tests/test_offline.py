"""Checks that need no Homebox: the server loads with the pinned SDK, every tool is
registered, and bad input is rejected before any request is made.

Run inside the built image (see .github/workflows), or locally with
    uv run --with-requirements requirements.txt tests/test_offline.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Nothing listens here; any tool that reached the network would fail loudly.
os.environ.setdefault("HOMEBOX_URL", "http://127.0.0.1:9")
os.environ.setdefault("HOMEBOX_API_KEY", "offline-test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import homebox_mcp  # noqa: E402
from mcp import Client  # noqa: E402

EXPECTED_TOOLS = {
    "list_locations", "find_items", "location_contents", "get_details", "create_location",
    "add_items", "update_item", "move", "delete", "attach_photo",
}


async def main() -> int:
    failures = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"\n      {detail}" if not ok and detail else ""))
        failures += not ok

    async with Client(homebox_mcp.mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        check(tools == EXPECTED_TOOLS, "all tools registered", f"got {sorted(tools)}")

        async def error_text(tool: str, **args) -> str:
            r = await c.call_tool(tool, args)
            return "\n".join(getattr(b, "text", "") for b in r.content) if getattr(r, "is_error", False) else ""

        check("not a valid Homebox ID" in await error_text("get_details", entity_id="../users/self"),
              "reject path-like ids")
        check("not a valid Homebox ID" in await error_text("update_item", item_id="nope", quantity=2),
              "reject malformed item ids")
        check("not a supported image type" in await error_text(
                  "attach_photo", entity_id="00000000-0000-0000-0000-000000000001", file_path="/etc/passwd"),
              "refuse non-image uploads")
        check("quantity must be positive" in await error_text(
                  "update_item", item_id="00000000-0000-0000-0000-000000000001", quantity=0),
              "refuse zero quantity")
        check("Could not reach Homebox" in await error_text("list_locations"),
              "network errors surface as tool errors")

    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
