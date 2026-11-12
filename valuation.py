"""Player value as z-scores: how far above or below the rostered pool, per category."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from models import Category, League, Player

# Percentage categories and the makes and attempts behind them.
PERCENT_PARTS = {"FG%": ("FGM", "FGA"), "FT%": ("FTM", "FTA"), "3PT%": ("3PM", "3PA")}


def line_for(player: Player, window: str) -> Optional[Dict[str, float]]:
    line = player.stats.get(window)
    return line if line and line.get("GP") else None


def mean_std(values: List[float]):
    """Mean and population standard deviation."""
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def percent_impact(line: Dict[str, float], parts, pool_pct: float) -> float:
    """Extra makes per game compared with an average shooter on the same attempts.

    This is what weights percentages by volume: 90% on 10 FTA moves a team far
    more than 90% on 1 FTA.
    """
    makes, attempts = parts
    return line.get(makes, 0.0) - line.get(attempts, 0.0) * pool_pct


@dataclass
class Valuation:
    window: str
    z: Dict[int, Dict[str, float]]  # player ID -> category -> z-score
    total: Dict[int, float]  # player ID -> sum of z-scores
    has_data: Dict[int, bool]


def value_players(players: List[Player], categories: List[Category], window: str) -> Valuation:
    lines = {p.id: line_for(p, window) for p in players}
    pool = [line for line in lines.values() if line]
    z: Dict[int, Dict[str, float]] = {p.id: {} for p in players}

    for category in categories:
        parts = PERCENT_PARTS.get(category.name)
        if parts:
            makes = sum(line.get(parts[0], 0.0) for line in pool)
            attempts = sum(line.get(parts[1], 0.0) for line in pool)
            pct = makes / attempts if attempts else 0.0
            read = lambda line, parts=parts, pct=pct: percent_impact(line, parts, pct)
        else:
            read = lambda line, name=category.name: line.get(name, 0.0)

        mean, std = mean_std([read(line) for line in pool])
        sign = -1.0 if category.reverse else 1.0
        for player_id, line in lines.items():
            z[player_id][category.name] = (
                sign * (read(line) - mean) / std if line and std > 0 else 0.0
            )

    return Valuation(
        window=window,
        z=z,
        total={player_id: sum(values.values()) for player_id, values in z.items()},
        has_data={player_id: line is not None for player_id, line in lines.items()},
    )


def all_players(league: League) -> List[Player]:
    return [player for team in league.teams for player in team.players]
