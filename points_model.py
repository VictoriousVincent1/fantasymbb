"""Points leagues: player value is fantasy points per game under the league's own rules.

Team strength is projected fantasy points per week from the players who would
actually start. Each day, every player has a chance of having a game, and only
the starting slots count. On a light night everyone who plays starts; on a busy
night the lowest players on a deep roster sit on the bench. So the 15th player
on a roster is worth less than the 5th, which is why consolidation trades can help.

In a points league trades are otherwise close to zero-sum, so a deal helps both
teams mainly through roster spots and depth, or because the two managers value
players differently (for example, recent form versus the full season).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb
from typing import Dict, List, Optional

from models import League, Player
from team_model import LeagueModel, RosterChange
from valuation import all_players, line_for

# (chance a given player has a game that day, share of days). Light, normal and busy
# NBA nights, averaging 3.5 games per player per week.
DAY_MIX = ((0.25, 1 / 3), (0.5, 1 / 3), (0.75, 1 / 3))
DAYS_PER_WEEK = 7
STREAK_SHARE = 0.15  # last 30 days at least 15% above or below the season...
STREAK_MIN_POINTS = 3.0  # ...and at least this many points per game
MIN_MY_GAIN = 5.0  # projected fantasy points per week
MAX_THEIR_LOSS = 10.0
THEY_GAIN = 1.0  # their weekly gain needed to say the deal helps them too
SHAPES = ((1, 1), (2, 1), (1, 2), (2, 2))


def fantasy_points(line: Dict[str, float], weights: Dict[str, float]) -> float:
    return sum(line.get(stat, 0.0) * points for stat, points in weights.items())


def start_weights(count: int, slots: int, day_mix=DAY_MIX) -> List[float]:
    """Expected starts per week for the k-th best player on a roster (k from 0).

    The k-th best player starts on a day when they play and fewer than `slots` of the
    k better players also play: q * P(Binomial(k, q) <= slots - 1), averaged over the day mix.
    """
    weights = []
    for k in range(count):
        starts = 0.0
        for q, share in day_mix:
            fits = sum(comb(k, j) * q ** j * (1 - q) ** (k - j) for j in range(min(k, slots - 1) + 1))
            starts += share * q * fits
        weights.append(DAYS_PER_WEEK * starts)
    return weights


def stat_points(line: Dict[str, float], weights: Dict[str, float]) -> Dict[str, float]:
    """Fantasy points per game that come from each scoring stat."""
    return {stat: line.get(stat, 0.0) * points for stat, points in weights.items()}


@dataclass
class PointsTradeEval:
    team_id: int
    opponent_id: int
    give: List[Player]
    get: List[Player]
    my_change: RosterChange
    their_change: RosterChange
    my_before: float
    my_after: float
    their_before: float
    their_after: float
    my_rank_before: int
    my_rank_after: int
    their_rank_before: int
    their_rank_after: int

    @property
    def my_delta(self) -> float:
        return self.my_after - self.my_before

    @property
    def their_delta(self) -> float:
        return self.their_after - self.their_before


class PointsModel:
    mode = "points"

    # Roster handling is shared with the category model.
    active = LeagueModel.active
    apply_trade = LeagueModel.apply_trade

    def __init__(self, league: League, window: str = "season"):
        self.league = league
        self.window = window
        self.weights = league.points_per_stat
        players = all_players(league)
        self.lines = {p.id: line_for(p, window) or line_for(p, "season") or {} for p in players}
        self._has_data = {p.id: line_for(p, window) is not None for p in players}
        self.fppg = {p.id: fantasy_points(self.lines[p.id], self.weights) for p in players}
        self.season_fppg = {p.id: self._window_fppg(p, "season") for p in players}
        self.last30_fppg = {p.id: self._window_fppg(p, "last_30") for p in players}

        ranked = sorted((p for p in players if self._has_data[p.id]), key=self.value)
        bottom = ranked[: max(1, len(league.teams))]
        self.replacement_value = sum(map(self.value, bottom)) / len(bottom) if bottom else 0.0
        starters = sum(n for slot, n in league.lineup_slots.items() if slot not in ("BE", "IR"))
        self.starting_slots = starters or league.roster_limit
        self.weights_by_rank = start_weights(league.roster_limit + 8, self.starting_slots)
        self.strength = {t.id: self.team_strength(self.active(t)) for t in league.teams}
        self.league_average = sum(self.strength.values()) / len(self.strength)

    def _window_fppg(self, player: Player, window: str) -> Optional[float]:
        line = line_for(player, window)
        return fantasy_points(line, self.weights) if line else None

    def value(self, player: Player) -> float:
        return self.fppg[player.id]

    def has_data(self, player: Player) -> bool:
        return self._has_data[player.id]

    def streak(self, player: Player) -> Optional[str]:
        season, recent = self.season_fppg[player.id], self.last30_fppg[player.id]
        if season is None or recent is None:
            return None
        gap = recent - season
        if abs(gap) < max(STREAK_MIN_POINTS, STREAK_SHARE * abs(season)):
            return None
        return "hot" if gap > 0 else "cold"

    def team_strength(self, players: List[Player], open_spots: int = 0) -> float:
        """Projected fantasy points per week from the players who would start."""
        values = sorted([self.value(p) for p in players] + [self.replacement_value] * open_spots,
                        reverse=True)
        weights = self.weights_by_rank
        if len(values) > len(weights):
            weights = start_weights(len(values), self.starting_slots)
        return sum(v * w for v, w in zip(values, weights))

    def rank(self, team_id: int, overrides: Optional[Dict[int, float]] = None) -> int:
        strength = {**self.strength, **(overrides or {})}
        return 1 + sum(other > strength[team_id] + 1e-9 for other in strength.values())

    def evaluate(self, team_id: int, opponent_id: int, give: List[Player], get: List[Player],
                 my_change: Optional[RosterChange] = None,
                 their_change: Optional[RosterChange] = None) -> PointsTradeEval:
        me, them = self.league.team(team_id), self.league.team(opponent_id)
        my_change = my_change or self.apply_trade(me, give, get)
        their_change = their_change or self.apply_trade(them, get, give)
        overrides = {
            team_id: self.team_strength(my_change.players, my_change.open_spots),
            opponent_id: self.team_strength(their_change.players, their_change.open_spots),
        }
        return PointsTradeEval(
            team_id=team_id, opponent_id=opponent_id, give=give, get=get,
            my_change=my_change, their_change=their_change,
            my_before=self.strength[team_id], my_after=overrides[team_id],
            their_before=self.strength[opponent_id], their_after=overrides[opponent_id],
            my_rank_before=self.rank(team_id), my_rank_after=self.rank(team_id, overrides),
            their_rank_before=self.rank(opponent_id), their_rank_after=self.rank(opponent_id, overrides),
        )

    def acceptance_reasons(self, result: PointsTradeEval) -> List[str]:
        """Plain reasons the other manager might say yes. Empty means none found."""
        reasons = []
        them = self.league.team(result.opponent_id).name
        if result.their_delta >= THEY_GAIN:
            reasons.append(
                f"{them} also gains: their projected weekly points go from {result.their_before:.0f} "
                f"to {result.their_after:.0f}."
            )
        best = max(result.give + result.get, key=self.value)
        if best in result.give:
            reasons.append(
                f"They get {best.name}, the best player in the deal at {self.value(best):.1f} "
                "points per game. Managers often pay extra for a star."
            )
        hot = [p for p in result.give if self.streak(p) == "hot"]
        if hot:
            reasons.append(
                f"{hot[0].name} has averaged {self.last30_fppg[hot[0].id]:.1f} over the last 30 days, "
                f"above their season average of {self.season_fppg[hot[0].id]:.1f}, which can look appealing."
            )
        season_give = [self.season_fppg[p.id] for p in result.give]
        season_get = [self.season_fppg[p.id] for p in result.get]
        if (self.window == "last_30" and None not in season_give + season_get
                and sum(season_give) > sum(season_get)):
            reasons.append(
                f"On full-season numbers they receive {sum(season_give):.1f} points per game and "
                f"send {sum(season_get):.1f}, so the deal looks good to them."
            )
        return reasons


def search_points_trades(model: PointsModel, team_id: int, opponent_id: Optional[int] = None,
                         limit: int = 200) -> List[PointsTradeEval]:
    me = model.league.team(team_id)
    mine = [p for p in me.players if not p.is_out and model.has_data(p)]
    results: List[PointsTradeEval] = []
    replacement = model.replacement_value
    my_before = model.strength[team_id]

    for them in model.league.teams:
        if them.id == team_id or (opponent_id is not None and them.id != opponent_id):
            continue
        theirs = [p for p in them.players if not p.is_out and model.has_data(p)]
        their_before = model.strength[them.id]
        for give_count, get_count in SHAPES:
            spots_back = max(0, give_count - get_count)
            for give in combinations(mine, give_count):
                give_value = sum(map(model.value, give))
                for get in combinations(theirs, get_count):
                    raw = sum(map(model.value, get)) - give_value
                    # Quick bound: no player starts more than every day, so a per-game gain
                    # this negative can't become a weekly gain.
                    if (raw + spots_back * replacement) * DAYS_PER_WEEK < MIN_MY_GAIN:
                        continue
                    my_change = model.apply_trade(me, list(give), list(get))
                    my_after = model.team_strength(my_change.players, my_change.open_spots)
                    if my_after - my_before < MIN_MY_GAIN:
                        continue
                    their_change = model.apply_trade(them, list(get), list(give))
                    their_after = model.team_strength(their_change.players, their_change.open_spots)
                    if their_after - their_before < -MAX_THEIR_LOSS:
                        continue
                    if not (my_change.lineup_ok and their_change.lineup_ok):
                        continue
                    result = model.evaluate(team_id, them.id, list(give), list(get),
                                            my_change, their_change)
                    if model.acceptance_reasons(result):
                        results.append(result)

    results.sort(key=deal_score, reverse=True)
    return results[:limit]


def deal_score(result: PointsTradeEval) -> float:
    """Your gain, minus a penalty for what the other team loses: lopsided deals rarely happen."""
    return result.my_delta + 2 * min(result.their_delta, 0.0)
