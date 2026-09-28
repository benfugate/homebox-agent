"""End-to-end check of every tool against a real Homebox.

Everything happens under a throwaway top-level location that is deleted at the
end, even if a check fails. Needs HOMEBOX_URL and HOMEBOX_API_KEY.

    uv run --with-requirements requirements.txt tests/test_live.py
"""

import asyncio
import re
import secrets
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import homebox_mcp  # noqa: E402
from mcp import Client  # noqa: E402

ID = re.compile(r"\[id ([0-9a-f-]{36})\]")
failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures.append(label)
        if detail:
            print("      " + detail.replace("\n", "\n      "))


def tiny_png(path: Path) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"\x00\xff\x00\x00"  # one red pixel
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


async def main() -> None:
    root_name = f"zz-agent-test-{secrets.token_hex(3)}"
    created: list[str] = []  # entity ids, deleted in reverse at the end

    async with Client(homebox_mcp.mcp) as c:

        async def call(tool: str, **args) -> tuple[str, bool]:
            r = await c.call_tool(tool, args)
            text = "\n".join(getattr(b, "text", "") for b in r.content)
            return text, bool(getattr(r, "is_error", False))

        try:
            text, err = await call("list_locations")
            check(not err and "Attic" in text, "list_locations shows existing rooms", text)

            text, err = await call("create_location", name=root_name)
            root = ID.search(text).group(1)
            created.append(root)
            check(not err and "Created location" in text, "create top-level location", text)

            text, err = await call("create_location", name=root_name.upper())
            check("Already exists" in text and root in text, "same name is reused, not duplicated", text)

            text, err = await call("create_location", name="Blue bin (left)", parent_id=root)
            bin_id = ID.search(text).group(1)
            created.append(bin_id)
            check(f"{root_name} > Blue bin (left)" in text, "create nested location", text)

            text, err = await call(
                "add_items",
                location_id=bin_id,
                items=[
                    {"name": "Christmas lights"},
                    {"name": "Tree stand"},
                    {"name": "Wreath", "quantity": 2, "description": "pine"},
                ],
            )
            ids = ID.findall(text)
            created.extend(ids)
            check(not err and len(ids) == 3 and "Wreath x2" in text, "add three items", text)
            lights, stand, wreath = ids

            text, err = await call("get_details", entity_id=stand)
            check("(item)" in text, "new entries are items, not locations", text)

            text, err = await call("add_items", location_id=bin_id, items=[{"name": "tree stand"}])
            check("Skipped" in text and "Nothing new" in text, "duplicate item name is skipped", text)

            text, err = await call("find_items", query="wreath")
            check(f"Wreath x2 (pine) in {root_name} > Blue bin (left)" in text, "find item with path", text)

            text, err = await call("find_items", query=root_name)
            check("Locations:" in text and root in text, "find matches location names", text)

            text, err = await call("location_contents", location_id=root)
            check("[Blue bin (left)]" in text and "Tree stand" in text, "nested contents listing", text)

            text, err = await call("list_locations")
            check(f"Blue bin (left) (3 items) [id {bin_id}]" in text, "tree shows item counts", text)

            await call("update_item", item_id=wreath, notes="top of the bin")
            await call("update_item", item_id=wreath, quantity=3)
            text, err = await call("update_item", item_id=wreath, description="plastic, red bow")
            check(
                "quantity: 3" in text
                and "notes: top of the bin" in text
                and "description: plastic, red bow" in text
                and f"{root_name} > Blue bin (left)" in text,
                "updates keep the other fields and location",
                text,
            )

            # Fields only the Homebox UI sets must survive the agent's full-replace update.
            api = homebox_mcp._api
            cur = api("GET", f"/entities/{lights}")
            tag = next(iter(api("GET", "/tags") or []), None)
            api(
                "PUT",
                f"/entities/{lights}",
                json={
                    "id": lights,
                    "name": cur["name"],
                    "quantity": 1,
                    "parentId": bin_id,
                    "manufacturer": "GE",
                    "purchasePrice": 19.99,
                    "tagIds": [tag["id"]] if tag else [],
                    "fields": [{"name": "Bulbs", "type": "text", "textValue": "LED warm white"}],
                },
            )
            await call("update_item", item_id=lights, name="Christmas lights (outdoor)")
            after = api("GET", f"/entities/{lights}")
            check(
                after["name"] == "Christmas lights (outdoor)"
                and after.get("manufacturer") == "GE"
                and after.get("purchasePrice") == 19.99
                and [t["id"] for t in after.get("tags") or []] == ([tag["id"]] if tag else [])
                and [(f["name"], f.get("textValue")) for f in after.get("fields") or []] == [("Bulbs", "LED warm white")]
                and (after.get("parent") or {}).get("id") == bin_id,
                "rename keeps tags, price, manufacturer and custom fields",
                str({k: after.get(k) for k in ("name", "manufacturer", "purchasePrice", "tags", "fields")}),
            )

            text, err = await call("move", entity_ids=[stand], new_location_id=root)
            text, err = await call("get_details", entity_id=stand)
            check(text.startswith(f"Tree stand (item) at {root_name} > Tree stand"), "move item up a level", text)

            text, err = await call("move", entity_ids=[root], new_location_id=bin_id)
            check(err and "inside itself" in text, "refuse moving a location into its own child", text)

            with tempfile.TemporaryDirectory() as tmp:
                png = Path(tmp) / "bin.png"
                tiny_png(png)
                text, err = await call("attach_photo", entity_id=bin_id, file_path=str(png))
                check(not err and "main photo" in text, "attach photo", text)
            text, err = await call("attach_photo", entity_id=bin_id, file_path="/etc/passwd")
            check(err and "not a supported image" in text, "refuse non-image files", text)
            text, err = await call("get_details", entity_id=bin_id)
            check("attachments: 1" in text, "photo is on the location", text)

            text, err = await call("delete", entity_id=bin_id)
            check(err and "not empty" in text, "refuse deleting a non-empty location", text)

            text, err = await call("get_details", entity_id="../users/self")
            check(err and "not a valid Homebox ID" in text, "reject malformed ids", text)

        finally:
            for eid in reversed(created):
                text, err = await call("delete", entity_id=eid)
                if err and "No item or location" not in text:
                    print(f"cleanup: {text}")
            text, _ = await call("find_items", query=root_name)
            check("Nothing matches" in text, "cleanup removed every test entity", text)

    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
