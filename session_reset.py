"""Clears the agent's Discord conversations on a schedule, so every day starts fresh.

Homebox is the real memory; an old conversation only adds stale answers and resends history on
every message. A reset is the same UPDATE hermes runs for /reset (src/runtime/session-target.ts):
hermes reads the row on every message, so the next one starts a new Claude session.

SESSION_RESET_CRON is a 5-field cron expression in local time (TZ), or "off". Default 0 4 * * *.
  session_reset.py              run the schedule (started by homebox-entrypoint.sh)
  session_reset.py --now        reset once and exit
"""

import os
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

DEFAULT_CRON = "0 4 * * *"
DB_PATH = os.environ.get("HERMES_STATE_DB", "/root/.claude/hermes/state.db")


def log(msg: str) -> None:
    print(f"[homebox-agent] {msg}", flush=True)


def local_zone() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def valid(expr: str) -> bool:
    return expr == "off" or (len(expr.split()) == 5 and croniter.is_valid(expr))


def next_fire(expr: str, after: datetime) -> datetime:
    return croniter(expr, after).get_next(datetime)


def reset_sessions(db_path: str = DB_PATH) -> int:
    """Clear every conversation's Claude session; returns how many were active."""
    # mode=rw: never create the file; hermes owns it.
    with closing(sqlite3.connect(f"file:{db_path}?mode=rw", uri=True, timeout=30)) as db, db:
        return db.execute(
            "UPDATE sessions SET claude_session_id = NULL, turn_count = 0, compact_warned = 0 "
            "WHERE claude_session_id IS NOT NULL"
        ).rowcount


def run_reset() -> None:
    try:
        log(f"session reset: {reset_sessions()} conversation(s) cleared")
    except sqlite3.Error as e:
        log(f"session reset failed: {e}")


def schedule(expr: str) -> None:
    zone = local_zone()
    while True:
        due = next_fire(expr, datetime.now(zone))
        log(f"next session reset {due:%Y-%m-%d %H:%M %Z} ({expr})")
        # Short sleeps, so a clock change or a suspended host can't skip a reset by hours.
        while (left := (due - datetime.now(zone)).total_seconds()) > 0:
            time.sleep(min(left, 60))
        run_reset()


def main(argv: list[str]) -> int:
    if argv[:1] == ["--now"]:
        run_reset()
        return 0
    expr = (os.environ.get("SESSION_RESET_CRON") or DEFAULT_CRON).strip()
    if expr == "off":
        return 0
    if not valid(expr):
        log(f"SESSION_RESET_CRON={expr!r} is not a 5-field cron expression; sessions won't be reset")
        return 1
    schedule(expr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
