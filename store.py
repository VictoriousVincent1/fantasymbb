"""Timestamped league snapshots in SQLite, so the app works when ESPN doesn't."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Optional

from models import League

MIN_REFRESH_SECONDS = 600
KEEP_SNAPSHOTS = 30


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS snapshots (
                       id INTEGER PRIMARY KEY,
                       league_id INTEGER NOT NULL,
                       year INTEGER NOT NULL,
                       fetched_at TEXT NOT NULL,
                       body TEXT NOT NULL)"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, league: League) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO snapshots (league_id, year, fetched_at, body) VALUES (?, ?, ?, ?)",
                (league.league_id, league.year, league.fetched_at, json.dumps(league.to_dict())),
            )
            db.execute(
                """DELETE FROM snapshots WHERE league_id = ? AND year = ? AND id NOT IN (
                       SELECT id FROM snapshots WHERE league_id = ? AND year = ?
                       ORDER BY id DESC LIMIT ?)""",
                (league.league_id, league.year, league.league_id, league.year, KEEP_SNAPSHOTS),
            )

    def latest(self, league_id: int, year: int) -> Optional[League]:
        with self._connect() as db:
            row = db.execute(
                "SELECT body FROM snapshots WHERE league_id = ? AND year = ? "
                "ORDER BY id DESC LIMIT 1",
                (league_id, year),
            ).fetchone()
        return League.from_dict(json.loads(row[0])) if row else None


def refresh_wait_seconds(league: Optional[League], now: Optional[dt.datetime] = None) -> int:
    """Seconds until another ESPN fetch is allowed; 0 when one is allowed now."""
    if league is None:
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    age = (now - dt.datetime.fromisoformat(league.fetched_at)).total_seconds()
    return max(0, int(MIN_REFRESH_SECONDS - age))
