"""Our own league data shape, independent of ESPN's."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

WINDOWS = ("season", "last_30")


@dataclass
class Category:
    name: str
    reverse: bool = False  # True when lower is better, such as turnovers


@dataclass
class Player:
    id: int
    name: str
    pro_team: str
    position: str
    eligible_slots: List[str]
    lineup_slot: str
    injury_status: str
    # window -> per-game stats, plus GP (games played)
    stats: Dict[str, Dict[str, float]] = field(default_factory=dict)

    @property
    def on_ir(self) -> bool:
        return self.lineup_slot == "IR"

    @property
    def is_out(self) -> bool:
        return self.on_ir or self.injury_status in ("OUT", "INJURY_RESERVE", "SUSPENSION")


@dataclass
class Team:
    id: int
    name: str
    abbrev: str
    wins: int
    losses: int
    ties: int
    players: List[Player]


@dataclass
class Offer:
    """A pending trade proposal on ESPN."""
    id: str
    proposer_id: int
    proposed_ms: int
    expires_ms: int
    # Each move: {"player_id": int, "from_team": int, "to_team": int}
    moves: List[Dict[str, int]]
    includes_budget: bool = False

    @property
    def team_ids(self) -> List[int]:
        return sorted({m["from_team"] for m in self.moves} | {m["to_team"] for m in self.moves})


@dataclass
class League:
    league_id: int
    year: int
    name: str
    scoring_type: str
    categories: List[Category]
    lineup_slots: Dict[str, int]
    roster_limit: int
    trade_deadline_ms: int
    teams: List[Team]
    fetched_at: str
    source: str  # "espn" or "demo"
    note: Optional[str] = None
    scoring_mode: str = "categories"  # or "points"
    points_per_stat: Dict[str, float] = field(default_factory=dict)
    offers: List[Offer] = field(default_factory=list)
    offers_error: Optional[str] = None

    def team(self, team_id: int) -> Team:
        for team in self.teams:
            if team.id == team_id:
                return team
        raise KeyError(team_id)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "League":
        data = dict(data)
        data["categories"] = [Category(**c) for c in data["categories"]]
        data["teams"] = [
            Team(**{**t, "players": [Player(**p) for p in t["players"]]}) for t in data["teams"]
        ]
        data["offers"] = [Offer(**o) for o in data.get("offers", [])]
        return cls(**data)
