"""Settings, read from the environment and an optional .env file."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent
ENV_PATHS = [Path.home() / ".config" / "fantasy-trade-coach" / ".env", ROOT / ".env"]


def parse_env(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def default_season_year(today: Optional[dt.date] = None) -> int:
    """ESPN names a season by the year it ends; a new season appears in October."""
    today = today or dt.date.today()
    return today.year + 1 if today.month >= 10 else today.year


@dataclass
class Settings:
    league_id: Optional[int]
    year: int
    espn_s2: Optional[str]
    swid: Optional[str]
    team_id: Optional[int]

    @property
    def has_league(self) -> bool:
        return self.league_id is not None


def load_settings() -> Settings:
    values: Dict[str, str] = {}
    for path in ENV_PATHS:
        if path.exists():
            values = parse_env(path.read_text())
            break
    values.update({k: v for k, v in os.environ.items() if k.startswith("ESPN_")})

    def as_int(key: str) -> Optional[int]:
        raw = values.get(key, "")
        return int(raw) if raw.isdigit() else None

    return Settings(
        league_id=as_int("ESPN_LEAGUE_ID"),
        year=as_int("ESPN_YEAR") or default_season_year(),
        espn_s2=values.get("ESPN_S2") or None,
        swid=values.get("ESPN_SWID") or None,
        team_id=as_int("ESPN_TEAM_ID"),
    )
