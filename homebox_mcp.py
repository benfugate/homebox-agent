"""Homebox tools for the inventory agent, served over MCP stdio.

Targets the Homebox 0.26 entities API, where items and locations are both
"entities" and a location is an entity whose type has isLocation=true.
Configured by HOMEBOX_URL (e.g. http://homebox:7745) and HOMEBOX_API_KEY.
"""

import logging
import mimetypes
import os
import uuid
from pathlib import Path

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

SEP = " > "
PAGE_SIZE = 500
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".avif"}

INSTRUCTIONS = """\
Tools for a household inventory in Homebox. Locations nest (a room holds shelves,
a shelf holds bins) and items live in a location. Look locations up with
list_locations before creating new ones so you reuse what exists. IDs are UUIDs
taken from earlier tool output; never invent them."""

mcp = MCPServer("homebox", instructions=INSTRUCTIONS)

# httpx logs every request at INFO, which buries real errors in the agent logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

_http: httpx.Client | None = None


def _client() -> httpx.Client:
    global _http
    if _http is None:
        base = os.environ.get("HOMEBOX_URL", "").rstrip("/")
        key = os.environ.get("HOMEBOX_API_KEY", "")
        if not base or not key:
            raise ToolError("HOMEBOX_URL and HOMEBOX_API_KEY must be set")
        _http = httpx.Client(
            base_url=f"{base}/api/v1",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
    return _http


def _api(method: str, path: str, **kwargs):
    try:
        r = _client().request(method, path, **kwargs)
    except httpx.HTTPError as e:
        raise ToolError(f"Could not reach Homebox: {e}") from e
    if r.status_code >= 400:
        raise ToolError(f"Homebox {method} {path} returned {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else None


def _check_id(value: str, what: str = "id") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise ToolError(f"{what} {value!r} is not a valid Homebox ID") from None


# --- inventory snapshot -------------------------------------------------------


class _Snapshot:
    """Locations and items fetched once per tool call, with full paths."""

    def __init__(self) -> None:
        self.loc_name: dict[str, str] = {}
        self.loc_parent: dict[str, str | None] = {}
        self.loc_children: dict[str | None, list[str]] = {}
        self._walk(_api("GET", "/entities/tree") or [], None)
        self.items = _all_items()
        self.items_in: dict[str, list[dict]] = {}
        for it in self.items:
            pid = (it.get("parent") or {}).get("id")
            self.items_in.setdefault(pid, []).append(it)

    def _walk(self, nodes: list[dict], parent: str | None) -> None:
        for n in sorted(nodes, key=lambda n: n["name"].lower()):
            if n.get("type") != "location":
                continue
            self.loc_name[n["id"]] = n["name"]
            self.loc_parent[n["id"]] = parent
            self.loc_children.setdefault(parent, []).append(n["id"])
            self._walk(n.get("children") or [], n["id"])

    def path(self, loc_id: str | None) -> str:
        parts = []
        while loc_id is not None and loc_id in self.loc_name:
            parts.append(self.loc_name[loc_id])
            loc_id = self.loc_parent[loc_id]
        return SEP.join(reversed(parts)) if parts else "(no location)"

    def descendants(self, loc_id: str) -> list[str]:
        out, stack = [], list(self.loc_children.get(loc_id, []))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(self.loc_children.get(c, []))
        return out

    def require_location(self, loc_id: str) -> str:
        loc_id = _check_id(loc_id, "location_id")
        if loc_id not in self.loc_name:
            raise ToolError(f"No location with id {loc_id}. Use list_locations to find it.")
        return loc_id


def _all_items() -> list[dict]:
    items, page = [], 1
    while True:
        res = _api("GET", "/entities", params={"page": page, "pageSize": PAGE_SIZE})
        batch = res.get("items") or []
        items.extend(i for i in batch if not (i.get("entityType") or {}).get("isLocation"))
        if len(batch) < PAGE_SIZE or len(items) >= (res.get("total") or 0):
            return items
        page += 1


def _location_type_id() -> str:
    types = [t for t in _api("GET", "/entity-types") or [] if t.get("isLocation")]
    if not types:
        raise ToolError("Homebox has no location entity type")
    named = [t for t in types if t["name"].lower() == "location"]
    return (named or types)[0]["id"]


def _qty(q) -> str:
    if q in (None, 0, 1):
        return ""
    return f" x{int(q) if float(q).is_integer() else q}"


def _item_line(snap: _Snapshot, it: dict) -> str:
    desc = f" ({it['description']})" if it.get("description") else ""
    where = snap.path((it.get("parent") or {}).get("id"))
    return f"- {it['name']}{_qty(it.get('quantity'))}{desc} in {where} [id {it['id']}]"


# --- tools --------------------------------------------------------------------


@mcp.tool()
def list_locations() -> str:
    """Show every location as an indented tree with item counts and IDs.

    Call this before adding things so you can reuse an existing location
    (e.g. "the blue bin in the attic") instead of creating a duplicate."""
    snap = _Snapshot()
    lines: list[str] = []

    def walk(parent: str | None, depth: int) -> None:
        for lid in snap.loc_children.get(parent, []):
            n = len(snap.items_in.get(lid, []))
            count = f" ({n} item{'s' if n != 1 else ''})" if n else ""
            lines.append(f"{'  ' * depth}- {snap.loc_name[lid]}{count} [id {lid}]")
            walk(lid, depth + 1)

    walk(None, 0)
    return "\n".join(lines) or "No locations yet."


@mcp.tool()
def find_items(query: str, limit: int = 25) -> str:
    """Search items (name, description, notes, serial...) and location names.

    Returns each match with its full location path, e.g.
    "Tree stand in Attic > Blue bin (left)"."""
    if not query.strip():
        raise ToolError("query is empty")
    snap = _Snapshot()
    res = _api("GET", "/entities", params={"q": query, "pageSize": max(1, min(limit, 100))})
    items = [i for i in res.get("items") or [] if not (i.get("entityType") or {}).get("isLocation")]
    q = query.lower()
    locs = [lid for lid, name in snap.loc_name.items() if q in name.lower()]

    out = []
    if items:
        out.append(f"Items ({len(items)}{' shown' if res.get('total', 0) > len(items) else ''}):")
        out += [_item_line(snap, i) for i in items]
    if locs:
        out.append("Locations:")
        out += [f"- {snap.path(lid)} [id {lid}]" for lid in locs]
    return "\n".join(out) or f"Nothing matches {query!r}."


@mcp.tool()
def location_contents(location_id: str, include_nested: bool = True) -> str:
    """List what is inside a location: its items and, by default, everything in
    the locations nested under it."""
    snap = _Snapshot()
    root = snap.require_location(location_id)
    lines = [f"{snap.path(root)}:"]

    def walk(lid: str, depth: int) -> None:
        pad = "  " * depth
        for it in sorted(snap.items_in.get(lid, []), key=lambda i: i["name"].lower()):
            desc = f" ({it['description']})" if it.get("description") else ""
            lines.append(f"{pad}- {it['name']}{_qty(it.get('quantity'))}{desc} [id {it['id']}]")
        for child in snap.loc_children.get(lid, []):
            lines.append(f"{pad}- [{snap.loc_name[child]}] [id {child}]")
            if include_nested:
                walk(child, depth + 1)

    walk(root, 1)
    return "\n".join(lines) if len(lines) > 1 else f"{snap.path(root)} is empty."


@mcp.tool()
def get_details(entity_id: str) -> str:
    """Full details for one item or location: path, quantity, description,
    notes, tags and attachment count."""
    eid = _check_id(entity_id, "entity_id")
    e = _api("GET", f"/entities/{eid}")
    path = SEP.join(p["name"] for p in _api("GET", f"/entities/{eid}/path") or [])
    kind = "location" if (e.get("entityType") or {}).get("isLocation") else "item"
    lines = [f"{e['name']} ({kind}) at {path or '(no location)'}", f"id: {eid}"]
    if kind == "item":
        lines.append(f"quantity: {_qty(e.get('quantity')).strip(' x') or 1}")
    for key in ("description", "notes", "manufacturer", "modelNumber", "serialNumber"):
        if e.get(key):
            lines.append(f"{key}: {e[key]}")
    if e.get("tags"):
        lines.append("tags: " + ", ".join(t["name"] for t in e["tags"]))
    if e.get("attachments"):
        lines.append(f"attachments: {len(e['attachments'])}")
    return "\n".join(lines)


@mcp.tool()
def create_location(name: str, parent_id: str | None = None, description: str = "") -> str:
    """Create a location, optionally inside another one (a bin inside the attic,
    a shelf inside the garage). Leave parent_id empty for a top-level room.

    If a location with the same name already exists in that spot, it is
    returned instead of making a duplicate."""
    name = name.strip()
    if not name:
        raise ToolError("name is empty")
    snap = _Snapshot()
    parent = snap.require_location(parent_id) if parent_id else None
    for lid in snap.loc_children.get(parent, []):
        if snap.loc_name[lid].lower() == name.lower():
            return f"Already exists: {snap.path(lid)} [id {lid}]"
    body = {"name": name, "description": description, "entityTypeId": _location_type_id()}
    if parent:
        body["parentId"] = parent
    new = _api("POST", "/entities", json=body)
    where = f"{snap.path(parent)}{SEP}{name}" if parent else name
    return f"Created location {where} [id {new['id']}]"


class NewItem(BaseModel):
    name: str = Field(description="Short item name, e.g. 'Christmas lights'")
    quantity: float = Field(default=1, gt=0, description="How many")
    description: str = Field(default="", description="Optional details: color, size, brand")


@mcp.tool()
def add_items(location_id: str, items: list[NewItem]) -> str:
    """Add one or more items to a location.

    Items whose name already exists in that location are skipped and reported,
    so you can decide whether to change the existing item's quantity instead."""
    if not items:
        raise ToolError("items is empty")
    snap = _Snapshot()
    loc = snap.require_location(location_id)
    existing = {i["name"].lower(): i for i in snap.items_in.get(loc, [])}
    added, skipped = [], []
    for it in items:
        name = it.name.strip()
        if not name:
            continue
        if name.lower() in existing:
            ex = existing[name.lower()]
            skipped.append(f"- {ex['name']}{_qty(ex.get('quantity'))} already there [id {ex['id']}]")
            continue
        new = _api(
            "POST",
            "/entities",
            json={"name": name, "quantity": it.quantity, "description": it.description, "parentId": loc},
        )
        existing[name.lower()] = new
        added.append(f"- {name}{_qty(it.quantity)} [id {new['id']}]")
    out = [f"Added to {snap.path(loc)}:"] + added if added else [f"Nothing new added to {snap.path(loc)}."]
    if skipped:
        out += ["Skipped (already in this location):"] + skipped
    return "\n".join(out)


# EntityUpdate is a full replace, so every field is copied from the current
# entity before applying changes; anything left out would be cleared.
_UPDATE_FIELDS = (
    "archived", "assetId", "description", "insured", "lifetimeWarranty", "manufacturer",
    "modelNumber", "name", "notes", "purchaseDate", "purchaseFrom", "purchasePrice",
    "quantity", "serialNumber", "soldDate", "soldNotes", "soldPrice", "soldTo",
    "warrantyDetails", "warrantyExpires",
)


@mcp.tool()
def update_item(
    item_id: str,
    name: str | None = None,
    quantity: float | None = None,
    description: str | None = None,
    notes: str | None = None,
) -> str:
    """Rename an item or change its quantity, description or notes. Only the
    fields you pass are changed. Use move to change where it is."""
    iid = _check_id(item_id, "item_id")
    if quantity is not None and quantity <= 0:
        raise ToolError("quantity must be positive; use delete to remove an item")
    if name is None and description is None and notes is None:
        if quantity is None:
            raise ToolError("nothing to change")
        _api("PATCH", f"/entities/{iid}", json={"id": iid, "quantity": quantity})
    else:
        cur = _api("GET", f"/entities/{iid}")
        body = {k: cur.get(k) for k in _UPDATE_FIELDS}
        body.update(
            id=iid,
            parentId=(cur.get("parent") or {}).get("id"),
            entityTypeId=(cur.get("entityType") or {}).get("id"),
            tagIds=[t["id"] for t in cur.get("tags") or []],
            fields=cur.get("fields") or [],
            syncChildEntityLocations=False,
        )
        for key, val in (("name", name), ("quantity", quantity), ("description", description), ("notes", notes)):
            if val is not None:
                body[key] = val.strip() if isinstance(val, str) else val
        _api("PUT", f"/entities/{iid}", json={k: v for k, v in body.items() if v is not None})
    return "Updated:\n" + get_details(iid)


@mcp.tool()
def move(entity_ids: list[str], new_location_id: str) -> str:
    """Move items or whole locations (e.g. a bin with everything in it) into
    another location."""
    snap = _Snapshot()
    dest = snap.require_location(new_location_id)
    item_ids = {i["id"] for i in snap.items}
    moved = []
    for raw in entity_ids:
        eid = _check_id(raw, "entity_id")
        if eid in snap.loc_name:
            if eid == dest or dest in snap.descendants(eid):
                raise ToolError(f"Can't move {snap.path(eid)} inside itself")
            label = snap.path(eid)
        elif eid in item_ids:
            label = next(i["name"] for i in snap.items if i["id"] == eid)
        else:
            raise ToolError(f"No item or location with id {eid}")
        _api("PATCH", f"/entities/{eid}", json={"id": eid, "parentId": dest})
        moved.append(f"- {label}")
    return f"Moved to {snap.path(dest)}:\n" + "\n".join(moved)


@mcp.tool()
def delete(entity_id: str) -> str:
    """Delete an item, or a location that is already empty. Locations that
    still hold items or other locations are refused; move or delete their
    contents first."""
    snap = _Snapshot()
    eid = _check_id(entity_id, "entity_id")
    if eid in snap.loc_name:
        inside = len(snap.items_in.get(eid, [])) + len(snap.loc_children.get(eid, []))
        if inside:
            raise ToolError(f"{snap.path(eid)} is not empty ({inside} things inside)")
        label = f"location {snap.path(eid)}"
    else:
        it = next((i for i in snap.items if i["id"] == eid), None)
        if it is None:
            raise ToolError(f"No item or location with id {eid}")
        label = f"{it['name']} from {snap.path((it.get('parent') or {}).get('id'))}"
    _api("DELETE", f"/entities/{eid}")
    return f"Deleted {label}"


@mcp.tool()
def attach_photo(entity_id: str, file_path: str, primary: bool = True) -> str:
    """Attach an image file (e.g. a photo sent in chat) to an item or location.
    primary=True makes it the thumbnail shown in Homebox."""
    eid = _check_id(entity_id, "entity_id")
    p = Path(file_path)
    if p.suffix.lower() not in PHOTO_TYPES:
        raise ToolError(f"{p.name} is not a supported image type")
    if not p.is_file():
        raise ToolError(f"{file_path} does not exist")
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    with p.open("rb") as fh:
        _api(
            "POST",
            f"/entities/{eid}/attachments",
            files={"file": (p.name, fh, mime)},
            data={"type": "photo", "primary": "true" if primary else "false", "name": p.name},
        )
    return f"Attached {p.name}" + (" as the main photo" if primary else "")


if __name__ == "__main__":
    mcp.run()
