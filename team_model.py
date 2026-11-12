"""Team-level category strength, and how a trade would change it.

"Expected category wins" is the main measure. For each opponent and category it
estimates the chance you win that category, from the gap between the teams scaled
by how spread out the league is in that category. Gains in a close category
therefore count for more than gains in one you already win or have given up on.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from models import League, Player, Team
from valuation import PERCENT_PARTS, Valuation, all_players, line_for, mean_std, value_players

PUNT_MIN_SIGMA = 1.0  # at least this far below the league average...
PUNT_BOTTOM_RANKS = 2  # ...and among this many lowest-ranked teams
STRENGTH_SIGMA = 0.5
STREAK_GAP = 1.5  # z-score difference between the last 30 days and the season


def can_fill_lineup(players: List[Player], lineup_slots: Dict[str, int], open_spots: int = 0) -> bool:
    """Whether the starting slots can all be filled, using bipartite matching.

    Open spots count as a free-agent pickup who can fill any slot.
    """
    slots = [
        slot for slot, count in lineup_slots.items() if slot not in ("BE", "IR") for _ in range(count)
    ]
    matched: Dict[int, int] = {}  # slot index -> player index

    def assign(player_index: int, seen: set) -> bool:
        eligible = players[player_index].eligible_slots
        for index, slot in enumerate(slots):
            if slot in eligible and index not in seen:
                seen.add(index)
                if index not in matched or assign(matched[index], seen):
                    matched[index] = player_index
                    return True
        return False

    filled = sum(assign(i, set()) for i, p in enumerate(players) if not p.is_out)
    return filled + open_spots >= len(slots)


@dataclass
class RosterChange:
    players: List[Player]
    dropped: List[Player] = field(default_factory=list)
    open_spots: int = 0
    value_gain: float = 0.0
    lineup_check: Optional[Callable[[], bool]] = field(default=None, repr=False)
    _lineup_ok: Optional[bool] = field(default=None, repr=False)

    @property
    def lineup_ok(self) -> bool:
        """Checked only when asked, since the matching is the slowest step of a search."""
        if self._lineup_ok is None:
            self._lineup_ok = self.lineup_check() if self.lineup_check else True
        return self._lineup_ok


@dataclass
class TradeEval:
    team_id: int
    opponent_id: int
    give: List[Player]
    get: List[Player]
    my_change: RosterChange
    their_change: RosterChange
    my_before: Dict[str, float]
    my_after: Dict[str, float]
    their_before: Dict[str, float]
    their_after: Dict[str, float]
    my_ranks_before: Dict[str, int]
    my_ranks_after: Dict[str, int]
    their_ranks_before: Dict[str, int]
    their_ranks_after: Dict[str, int]
    my_wins_before: float
    my_wins_after: float
    their_wins_before: float
    their_wins_after: float

    @property
    def my_delta(self) -> float:
        return self.my_wins_after - self.my_wins_before

    @property
    def their_delta(self) -> float:
        return self.their_wins_after - self.their_wins_before


class LeagueModel:
    def __init__(self, league: League, window: str = "season"):
        self.league = league
        self.window = window
        self.categories = league.categories
        players = all_players(league)
        self.valuation: Valuation = value_players(players, league.categories, window)
        self.season_valuation = (
            self.valuation if window == "season" else value_players(players, league.categories, "season")
        )
        self.last30_valuation = (
            self.valuation if window == "last_30" else value_players(players, league.categories, "last_30")
        )
        self.lines = {p.id: self._line(p) for p in players}
        self.replacement_line, self.replacement_value = self._replacement(players)
        self.totals = {t.id: self.team_totals(self.active(t)) for t in league.teams}
        self.sigma = {
            c.name: mean_std([totals[c.name] for totals in self.totals.values()])[1]
            for c in self.categories
        }
        self.mean = {
            c.name: mean_std([totals[c.name] for totals in self.totals.values()])[0]
            for c in self.categories
        }

    def _line(self, player: Player) -> Dict[str, float]:
        """The player's stats in this window, falling back to the season."""
        return line_for(player, self.window) or line_for(player, "season") or {}

    def _replacement(self, players: List[Player]) -> Tuple[Dict[str, float], float]:
        """A stand-in for a free-agent pickup: the average of the lowest-valued rostered players."""
        with_data = [p for p in players if self.valuation.has_data[p.id]]
        with_data.sort(key=lambda p: self.valuation.total[p.id])
        bottom = with_data[: max(1, len(self.league.teams))]
        keys = {key for p in bottom for key in self.lines[p.id]}
        line = {key: sum(self.lines[p.id].get(key, 0.0) for p in bottom) / len(bottom) for key in keys}
        value = sum(self.valuation.total[p.id] for p in bottom) / len(bottom) if bottom else 0.0
        return line, value

    def active(self, team: Team) -> List[Player]:
        return [p for p in team.players if not p.on_ir]

    def value(self, player: Player) -> float:
        return self.valuation.total[player.id]

    def has_data(self, player: Player) -> bool:
        return self.valuation.has_data[player.id]

    def streak(self, player: Player) -> Optional[str]:
        """"hot" or "cold" when the last 30 days differ clearly from the season."""
        if not (line_for(player, "season") and line_for(player, "last_30")):
            return None
        gap = self.last30_valuation.total[player.id] - self.season_valuation.total[player.id]
        return "hot" if gap >= STREAK_GAP else "cold" if gap <= -STREAK_GAP else None

    def team_totals(self, players: List[Player], open_spots: int = 0) -> Dict[str, float]:
        lines = [self.lines[p.id] for p in players] + [self.replacement_line] * open_spots
        totals: Dict[str, float] = {}
        for category in self.categories:
            parts = PERCENT_PARTS.get(category.name)
            if parts:
                makes = sum(line.get(parts[0], 0.0) for line in lines)
                attempts = sum(line.get(parts[1], 0.0) for line in lines)
                totals[category.name] = makes / attempts if attempts else 0.0
            else:
                totals[category.name] = sum(line.get(category.name, 0.0) for line in lines)
        return totals

    def ranks(self, team_id: int, overrides: Optional[Dict[int, Dict[str, float]]] = None) -> Dict[str, int]:
        """Rank in each category, where 1 is best. Tied teams share a rank."""
        totals = {**self.totals, **(overrides or {})}
        mine = totals[team_id]
        result = {}
        for category in self.categories:
            sign = -1 if category.reverse else 1
            value = sign * mine[category.name]
            result[category.name] = 1 + sum(
                sign * other[category.name] > value + 1e-12 for other in totals.values()
            )
        return result

    def win_probability(self, mine: float, theirs: float, category) -> float:
        sigma = self.sigma[category.name]
        gap = (theirs - mine) if category.reverse else (mine - theirs)
        if sigma <= 0:
            return 0.5 if gap == 0 else (1.0 if gap > 0 else 0.0)
        return 0.5 * (1 + math.erf(gap / (2 * sigma)))  # normal CDF of gap / (sqrt(2) * sigma)

    def expected_wins(self, team_id: int, overrides: Optional[Dict[int, Dict[str, float]]] = None) -> float:
        """Expected categories won in a matchup against the average opponent."""
        totals = {**self.totals, **(overrides or {})}
        mine = totals[team_id]
        opponents = [t for other_id, t in totals.items() if other_id != team_id]
        if not opponents:
            return 0.0
        wins = sum(
            self.win_probability(mine[c.name], other[c.name], c)
            for other in opponents
            for c in self.categories
        )
        return wins / len(opponents)

    def apply_trade(self, team: Team, give: List[Player], get: List[Player]) -> RosterChange:
        """The team's active roster after a trade, including forced drops and open spots."""
        before = self.active(team)
        give_ids = {p.id for p in give}
        arriving = [p for p in get if not p.on_ir]
        after = [p for p in before if p.id not in give_ids] + arriving
        arriving_ids = {p.id for p in arriving}

        dropped: List[Player] = []
        while len(after) > self.league.roster_limit:
            candidates = [p for p in after if p.id not in arriving_ids]
            # Don't drop players we can't value yet, such as an injured star with no games.
            candidates = [p for p in candidates if self.has_data(p)] or candidates
            if not candidates:
                break
            lowest = min(candidates, key=self.value)
            dropped.append(lowest)
            after = [p for p in after if p.id != lowest.id]

        open_spots = max(0, len(before) - len(after))
        gain = (
            sum(self.value(p) for p in arriving)
            - sum(self.value(p) for p in give if not p.on_ir)
            - sum(self.value(p) for p in dropped)
            + open_spots * self.replacement_value
        )
        slots = self.league.lineup_slots
        check = lambda: can_fill_lineup(after, slots, open_spots) or not can_fill_lineup(before, slots)
        return RosterChange(after, dropped, open_spots, gain, check)

    def evaluate(self, team_id: int, opponent_id: int, give: List[Player], get: List[Player],
                 my_change: Optional[RosterChange] = None,
                 their_change: Optional[RosterChange] = None) -> TradeEval:
        me, them = self.league.team(team_id), self.league.team(opponent_id)
        my_change = my_change or self.apply_trade(me, give, get)
        their_change = their_change or self.apply_trade(them, get, give)
        overrides = {
            team_id: self.team_totals(my_change.players, my_change.open_spots),
            opponent_id: self.team_totals(their_change.players, their_change.open_spots),
        }
        return TradeEval(
            team_id=team_id,
            opponent_id=opponent_id,
            give=give,
            get=get,
            my_change=my_change,
            their_change=their_change,
            my_before=self.totals[team_id],
            my_after=overrides[team_id],
            their_before=self.totals[opponent_id],
            their_after=overrides[opponent_id],
            my_ranks_before=self.ranks(team_id),
            my_ranks_after=self.ranks(team_id, overrides),
            their_ranks_before=self.ranks(opponent_id),
            their_ranks_after=self.ranks(opponent_id, overrides),
            my_wins_before=self.expected_wins(team_id),
            my_wins_after=self.expected_wins(team_id, overrides),
            their_wins_before=self.expected_wins(opponent_id),
            their_wins_after=self.expected_wins(opponent_id, overrides),
        )

    def gap_in_sigma(self, team_id: int, category) -> float:
        """How far above (+) or below (-) the league average, in standard deviations."""
        sigma = self.sigma[category.name]
        if sigma <= 0:
            return 0.0
        gap = (self.totals[team_id][category.name] - self.mean[category.name]) / sigma
        return -gap if category.reverse else gap

    def category_label(self, team_id: int, category) -> str:
        gap = self.gap_in_sigma(team_id, category)
        rank = self.ranks(team_id)[category.name]
        if gap <= -PUNT_MIN_SIGMA and rank > len(self.league.teams) - PUNT_BOTTOM_RANKS:
            return "likely punt"
        if gap >= STRENGTH_SIGMA:
            return "strength"
        if gap <= -STRENGTH_SIGMA:
            return "weakness"
        return "average"

    def punts(self, team_id: int) -> List[str]:
        return [c.name for c in self.categories if self.category_label(team_id, c) == "likely punt"]
