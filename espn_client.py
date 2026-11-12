"""The only module that talks to ESPN. Read-only: it never writes to a league."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Dict, List, Optional

from config import Settings
from models import Category, League, Player, Team

# ESPN's numeric stat IDs for scoring categories.
from espn_api.basketball.constant import POSITION_MAP, STATS_MAP


class EspnError(Exception):
    """A problem reaching or reading ESPN, with a message safe to show the user."""


class NotCategoryLeague(EspnError):
    pass


def is_category_scoring(scoring_type: Optional[str]) -> bool:
    text = (scoring_type or "").upper()
    return "CATEGOR" in text or "ROTO" in text


def is_points_scoring(scoring_type: Optional[str]) -> bool:
    return "POINTS" in (scoring_type or "").upper()


def points_from_settings(scoring_settings: dict) -> Dict[str, float]:
    """Fantasy points per unit of each stat, such as {"STL": 4.0, "TO": -2.0}."""
    weights: Dict[str, float] = {}
    for item in scoring_settings.get("scoringItems", []):
        name = STATS_MAP.get(str(item.get("statId")))
        if name and item.get("points"):
            weights[name] = float(item["points"])
    return weights


def categories_from_settings(scoring_settings: dict) -> List[Category]:
    categories = []
    for item in scoring_settings.get("scoringItems", []):
        name = STATS_MAP.get(str(item.get("statId")))
        if name:
            categories.append(Category(name=name, reverse=bool(item.get("isReverseItem"))))
    return categories


def lineup_from_settings(raw_settings: dict) -> Dict[str, int]:
    counts = raw_settings.get("rosterSettings", {}).get("lineupSlotCounts", {})
    slots: Dict[str, int] = {}
    for slot_id, count in counts.items():
        name = POSITION_MAP.get(int(slot_id))
        if name and count:
            slots[name] = count
    return slots


def _window_stats(split: Optional[dict]) -> Optional[Dict[str, float]]:
    """Per-game stats for one split, derived from totals if ESPN omits averages."""
    if not split:
        return None
    avg, total = split.get("avg"), split.get("total")
    if avg:
        stats = {str(k): float(v) for k, v in avg.items()}
    elif total and total.get("GP"):
        games = float(total["GP"])
        stats = {str(k): float(v) / games for k, v in total.items()}
    else:
        return None
    games = (total or {}).get("GP", stats.get("GP", 0))
    if not games:
        return None
    stats["GP"] = float(games)
    return stats


def player_from_espn(raw, year: int) -> Player:
    stats = {}
    for window, key in (("season", f"{year}_total"), ("last_30", f"{year}_last_30")):
        line = _window_stats(raw.stats.get(key))
        if line:
            stats[window] = line
    return Player(
        id=raw.playerId,
        name=raw.name,
        pro_team=raw.proTeam,
        position=raw.position,
        eligible_slots=[s for s in raw.eligibleSlots if s],
        lineup_slot=raw.lineupSlot or "",
        injury_status=raw.injuryStatus or "ACTIVE",
        stats=stats,
    )


def fetch_league(settings: Settings) -> League:
    if not settings.has_league:
        raise EspnError("No ESPN league is configured. Add ESPN_LEAGUE_ID to your .env file.")

    # Imported here so demo mode and tests never need the network stack.
    from espn_api.basketball import League as EspnLeague

    logging.getLogger("espn_api").setLevel(logging.ERROR)
    try:
        espn = EspnLeague(
            league_id=settings.league_id,
            year=settings.year,
            espn_s2=settings.espn_s2,
            swid=settings.swid,
        )
        raw_settings = espn.espn_request.get_league().get("settings", {})
    except Exception as exc:  # the wrapper raises several types, some bare
        # Never include the exception's request details, which could hold cookies.
        raise EspnError(
            f"ESPN request failed ({type(exc).__name__}). Check the league ID and season "
            "year, and for a private league refresh your espn_s2 and SWID cookies."
        ) from None

    scoring_type = espn.settings.scoring_type or ""
    scoring_settings = raw_settings.get("scoringSettings", {})
    categories: List[Category] = []
    weights: Dict[str, float] = {}
    if is_points_scoring(scoring_type):
        mode = "points"
        weights = points_from_settings(scoring_settings)
        if not weights:
            raise EspnError("ESPN returned no point values for this league's stats.")
    elif is_category_scoring(scoring_type):
        mode = "categories"
        categories = categories_from_settings(scoring_settings)
        if not categories:
            raise EspnError("ESPN returned no scoring categories for this league.")
    else:
        raise NotCategoryLeague(
            f"This league uses {scoring_type or 'an unknown scoring type'}, "
            "which the coach doesn't support."
        )

    slots = lineup_from_settings(raw_settings)
    teams = [
        Team(
            id=t.team_id,
            name=t.team_name,
            abbrev=t.team_abbrev,
            wins=t.wins,
            losses=t.losses,
            ties=t.ties,
            players=[player_from_espn(p, settings.year) for p in t.roster],
        )
        for t in espn.teams
    ]
    largest = max((sum(not p.on_ir for p in t.players) for t in teams), default=0)
    limit = sum(n for slot, n in slots.items() if slot != "IR") or largest
    return League(
        league_id=settings.league_id,
        year=settings.year,
        name=espn.settings.name,
        scoring_type=scoring_type,
        categories=categories,
        lineup_slots=slots,
        roster_limit=limit,
        trade_deadline_ms=espn.settings.trade_deadline or 0,
        teams=teams,
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        source="espn",
        scoring_mode=mode,
        points_per_stat=weights,
    )
