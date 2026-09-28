"""Checks that need no Homebox. Run: uv run --with-requirements requirements.txt tests/test_offline.py"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HOMEBOX_URL", "http://127.0.0.1:9")
os.environ.setdefault("HOMEBOX_API_KEY", "offline-test")
os.environ["AGENT_LOG_DIR"] = LOG_DIR = tempfile.mkdtemp(prefix="homebox-agent-logs-")
os.environ.pop("AGENT_LOG_MODE", None)
os.environ.pop("AGENT_FEEDBACK", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import homebox_mcp  # noqa: E402
from mcp import Client  # noqa: E402

EXPECTED_TOOLS = {
    "list_locations", "find_items", "location_contents", "get_details", "create_location",
    "add_items", "update_item", "move", "delete", "attach_photo", "record_feedback",
}


def read_log(name: str) -> list[dict]:
    path = Path(LOG_DIR) / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


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

        audit = read_log("audit.jsonl")
        check(homebox_mcp.LOG_MODE == "changes", "default log mode is changes")
        check([a["tool"] for a in audit] == ["get_details", "update_item", "attach_photo", "update_item", "list_locations"]
              and not any(a["ok"] for a in audit),
              "changes mode logs every failed call", json.dumps(audit)[:400])
        check(audit and audit[1]["args"] == {"item_id": "nope", "quantity": 2} and "not a valid" in audit[1]["result"],
              "audit entries carry arguments and the error", json.dumps(audit[1:2]))

        r = await c.call_tool("record_feedback", {
            "what_was_asked": "where is the drill", "what_went_wrong": "said attic, it is in the garage",
            "correction": "Garage > Cabinet"})
        check(not getattr(r, "is_error", False), "record_feedback succeeds")
        fb = read_log("feedback.jsonl")
        check(len(fb) == 1 and fb[0]["asked"] == "where is the drill" and fb[0]["correction"] == "Garage > Cabinet",
              "feedback is written to feedback.jsonl", json.dumps(fb))
        check(len(read_log("audit.jsonl")) == 5, "successful non-changing calls stay out of the audit in changes mode")
        check(bool(await error_text("record_feedback", what_was_asked=" ", what_went_wrong="x")),
              "feedback needs what was asked")

        homebox_mcp.LOG_MODE = "verbose"
        await c.call_tool("record_feedback", {"what_was_asked": "a", "what_went_wrong": "b"})
        last = read_log("audit.jsonl")[-1]
        check(last["tool"] == "record_feedback" and last["ok"] and last["result"] == "Noted.",
              "verbose mode logs successful calls too", json.dumps(last))

        homebox_mcp.LOG_MODE = "off"
        before = len(read_log("audit.jsonl"))
        await error_text("list_locations")
        check(len(read_log("audit.jsonl")) == before, "off mode logs nothing")

        homebox_mcp.LOG_MODE, homebox_mcp.LOG_MAX_BYTES = "changes", 200
        await error_text("list_locations")
        await error_text("list_locations")
        check((Path(LOG_DIR) / "audit.jsonl.1").exists(), "audit log rotates at the size cap")

    code = ("import asyncio, homebox_mcp; from mcp import Client\n"
            "async def m():\n"
            "    async with Client(homebox_mcp.mcp) as c:\n"
            "        print(sorted(t.name for t in (await c.list_tools()).tools))\n"
            "asyncio.run(m())")
    env = {**os.environ, "AGENT_FEEDBACK": "off", "PYTHONPATH": str(Path(homebox_mcp.__file__).resolve().parent)}
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    check("record_feedback" not in proc.stdout and "list_locations" in proc.stdout,
          "AGENT_FEEDBACK=off removes record_feedback", (proc.stdout + proc.stderr)[-500:])

    print("ALL PASSED" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
