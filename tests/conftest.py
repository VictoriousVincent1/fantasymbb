import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import Category, League, Player, Team


@pytest.fixture
def demo_league():
    return League.from_dict(json.loads((ROOT / "tests" / "fixtures" / "demo_league.json").read_text()))


def make_player(player_id, slots=("UT", "BE", "IR"), lineup_slot="BE", injury="ACTIVE", **stats):
    stats.setdefault("GP", 20.0)
    return Player(player_id, f"P{player_id}", "AAA", "PG", list(slots), lineup_slot, injury, {"season": stats})


def make_league(teams, categories=None, roster_limit=13, lineup=None):
    categories = categories or [Category("PTS")]
    return League(
        league_id=1, year=2027, name="Test", scoring_type="H2H_CATEGORY", categories=categories,
        lineup_slots=lineup or {"UT": 1, "BE": 1}, roster_limit=roster_limit, trade_deadline_ms=0,
        teams=[Team(i + 1, f"T{i + 1}", f"T{i + 1}", 0, 0, 0, players) for i, players in enumerate(teams)],
        fetched_at="2026-11-07T00:00:00+00:00", source="demo",
    )


# The scoring rules of a typical ESPN points league.
POINTS_RULES = {"PTS": 1.0, "REB": 1.0, "AST": 2.0, "STL": 4.0, "BLK": 4.0, "3PM": 1.0,
                "TO": -2.0, "FGM": 2.0, "FGA": -1.0, "FTM": 1.0, "FTA": -1.0}


@pytest.fixture
def points_league(demo_league):
    demo_league.scoring_mode = "points"
    demo_league.points_per_stat = dict(POINTS_RULES)
    demo_league.categories = []
    return demo_league
