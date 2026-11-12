"""Finds trades that help you and give the other manager a reason to say yes."""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import List, Optional

from models import Player
from team_model import LeagueModel, TradeEval

MIN_MY_GAIN = 0.03  # expected category wins per matchup
MIN_THEIR_GAIN = 0.02
MAX_VALUE_LOSS = 1.5  # most raw z-score value either side may give up
SHAPES = ((1, 1), (2, 1), (1, 2), (2, 2))


def tradable(model: LeagueModel, players: List[Player]) -> List[Player]:
    """Players worth suggesting: healthy, not on IR, and with stats in this window."""
    return [p for p in players if not p.is_out and model.has_data(p)]


def search_trades(model: LeagueModel, team_id: int, opponent_id: Optional[int] = None,
                  limit: int = 200) -> List[TradeEval]:
    me = model.league.team(team_id)
    mine = tradable(model, me.players)
    results: List[TradeEval] = []
    baseline = {team.id: model.expected_wins(team.id) for team in model.league.teams}
    my_wins = baseline[team_id]

    for them in model.league.teams:
        if them.id == team_id or (opponent_id is not None and them.id != opponent_id):
            continue
        theirs = tradable(model, them.players)
        for give_count, get_count in SHAPES:
            for give in combinations(mine, give_count):
                give_value = sum(model.value(p) for p in give)
                for get in combinations(theirs, get_count):
                    # Quick value check first, since most combinations fail it.
                    get_value = sum(model.value(p) for p in get)
                    if abs(get_value - give_value) > MAX_VALUE_LOSS + 6:
                        continue
                    my_change = model.apply_trade(me, list(give), list(get))
                    if my_change.value_gain < -MAX_VALUE_LOSS or not my_change.lineup_ok:
                        continue
                    their_change = model.apply_trade(them, list(get), list(give))
                    if their_change.value_gain < -MAX_VALUE_LOSS or not their_change.lineup_ok:
                        continue
                    # Check the gains alone first; the full evaluation also calculates ranks.
                    overrides = {
                        team_id: model.team_totals(my_change.players, my_change.open_spots),
                        them.id: model.team_totals(their_change.players, their_change.open_spots),
                    }
                    if model.expected_wins(team_id, overrides) - my_wins < MIN_MY_GAIN:
                        continue
                    if model.expected_wins(them.id, overrides) - baseline[them.id] < MIN_THEIR_GAIN:
                        continue
                    results.append(model.evaluate(team_id, them.id, list(give), list(get),
                                                  my_change, their_change))

    results.sort(key=lambda r: r.my_delta, reverse=True)
    return results[:limit]


def diversify(results: List[TradeEval], per_player: int = 4) -> List[TradeEval]:
    """Keeps the best trades while limiting how often one player of yours repeats."""
    used: Counter = Counter()
    kept = []
    for result in results:
        if all(used[p.id] < per_player for p in result.give):
            kept.append(result)
            used.update(p.id for p in result.give)
    return kept
