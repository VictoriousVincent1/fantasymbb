"""Plain-English explanations for points-league trades. Every number comes from the model."""
from __future__ import annotations

from typing import Dict, List

from explain import risks
from points_model import MAX_THEIR_LOSS, PointsModel, PointsTradeEval, stat_points
from valuation import line_for

STAT_NAMES = {
    "PTS": "points", "REB": "rebounds", "AST": "assists", "STL": "steals", "BLK": "blocks",
    "3PM": "3-pointers made", "TO": "turnovers", "FGM": "field goals made",
    "FGA": "field goal attempts", "FTM": "free throws made", "FTA": "free throw attempts",
    "OREB": "offensive rebounds", "DREB": "defensive rebounds", "DD": "double-doubles",
    "TD": "triple-doubles", "3PA": "3-point attempts",
}
SINGULAR = {
    "PTS": "point", "REB": "rebound", "AST": "assist", "STL": "steal", "BLK": "block",
    "3PM": "3-pointer made", "TO": "turnover", "FGM": "field goal made",
    "FGA": "field goal attempt", "FTM": "free throw made", "FTA": "free throw attempt",
    "OREB": "offensive rebound", "DREB": "defensive rebound", "DD": "double-double",
    "TD": "triple-double", "3PA": "3-point attempt",
}
SHOOTING = {"FGM", "FGA", "FTM", "FTA", "3PA"}

LESSONS = {
    "consolidation": (
        "Consolidation",
        "Two good players for one better one frees a roster spot for a free agent. Because "
        "your lineup has limited slots, one high scorer is often worth more than two average ones.",
    ),
    "roster_spots": (
        "Roster spots have value",
        "In a points league, trades are close to an even swap of points. Most of the extra value "
        "comes from roster spots: who has to drop someone, and who gets to add a free agent.",
    ),
    "depth": (
        "Depth versus starts",
        "Only your starting slots score. On busy nights the bottom of a deep roster sits on the "
        "bench, so your 15th player is worth less than their per-game average suggests.",
    ),
    "efficiency": (
        "Shooting efficiency",
        "Your league takes a point off for every shot and free throw attempted, then pays for "
        "makes. A player who scores on fewer attempts earns more fantasy points than raw scoring suggests.",
    ),
    "scoring_rules": (
        "Know your scoring rules",
        "Some stats are worth far more than others in your league. Players who rack up the "
        "high-value stats are worth more here than in a standard ranking.",
    ),
    "selling_high": (
        "Selling high",
        "A hot month can make a player look better than their longer record. Trading them "
        "then captures that value, though the streak may also be real.",
    ),
    "buying_low": (
        "Buying low",
        "A cold month can lower a player's price below their longer record. Buying then can "
        "pay off if they return to form, though the slump may have a real cause.",
    ),
    "per_game_value": (
        "Points per game",
        "In a points league, a player's worth is mostly their fantasy points per game, adjusted "
        "for how often they play. Upgrading per-game production is the most direct way to win.",
    ),
}


def names(players) -> str:
    return " and ".join(p.name for p in players)


def rules_text(weights: Dict[str, float]) -> List[str]:
    lines = []
    for stat, points in sorted(weights.items(), key=lambda kv: -kv[1]):
        lines.append(f"{points:+g} per {SINGULAR.get(stat, stat)}")
    return lines


def rules_note(weights: Dict[str, float]) -> str:
    """One sentence on what these particular rules reward."""
    positive = sorted((kv for kv in weights.items() if kv[1] > 0), key=lambda kv: -kv[1])
    if not positive:
        return ""
    top_value = positive[0][1]
    top = [STAT_NAMES.get(s, s) for s, v in positive if v == top_value]
    note = f"{' and '.join(top).capitalize()} are worth the most, at {top_value:+g} each."
    penalized = [STAT_NAMES.get(s, s) for s in ("FGA", "FTA") if weights.get(s, 0) < 0]
    if penalized:
        note += (f" {' and '.join(penalized).capitalize()} cost points, so efficient scorers are "
                 "worth more here than their raw scoring suggests.")
    if weights.get("TO", 0) < 0:
        note += f" Each turnover costs {abs(weights['TO']):g}."
    return note


def breakdown(model: PointsModel, result: PointsTradeEval) -> List[dict]:
    """Change in fantasy points per game from each stat, for the players in the deal."""
    def total(players):
        sums: Dict[str, float] = {stat: 0.0 for stat in model.weights}
        for p in players:
            for stat, value in stat_points(model.lines[p.id], model.weights).items():
                sums[stat] += value
        return sums

    gets = total([p for p in result.get if not p.on_ir])
    gives = total([p for p in result.give if not p.on_ir])
    rows = [
        {
            "stat": stat,
            "points_each": f"{points:+g}",
            "change": round(gets[stat] - gives[stat], 1),
        }
        for stat, points in model.weights.items()
    ]
    return sorted(rows, key=lambda r: -abs(r["change"]))


def choose_lesson(model: PointsModel, result: PointsTradeEval, rows: List[dict]) -> dict:
    sold = [p for p in result.give if model.streak(p) == "hot"]
    bought = [p for p in result.get if model.streak(p) == "cold"]
    gains = [r for r in rows if r["change"] > 0]
    top = gains[0] if gains else None
    shooting = sum(r["change"] for r in rows if r["stat"] in SHOOTING)
    has_attempt_penalty = any(model.weights.get(s, 0) < 0 for s in ("FGA", "FTA"))

    if len(result.give) > len(result.get) and result.my_change.open_spots:
        key = "consolidation"
        detail = (f"You send {names(result.give)} for {names(result.get)}, which opens "
                  f"{result.my_change.open_spots} roster spot for a free agent.")
    elif len(result.get) > len(result.give) and result.my_change.dropped:
        key = "depth"
        detail = (f"You'd drop {names(result.my_change.dropped)} to make room, but you add more "
                  "per-game production to the players who start most often.")
    elif result.their_change.dropped and result.their_delta < 0 <= result.my_delta:
        key = "roster_spots"
        detail = (f"They'd have to drop {names(result.their_change.dropped)} to fit both players, "
                  "which is part of what makes the deal work for you.")
    elif sold:
        key = "selling_high"
        p = sold[0]
        detail = (f"{p.name} has averaged {model.last30_fppg[p.id]:.1f} over the last 30 days, "
                  f"against {model.season_fppg[p.id]:.1f} for the season.")
    elif bought:
        key = "buying_low"
        p = bought[0]
        detail = (f"{p.name} has averaged {model.last30_fppg[p.id]:.1f} over the last 30 days, "
                  f"against {model.season_fppg[p.id]:.1f} for the season.")
    elif has_attempt_penalty and shooting >= 2.0:
        key = "efficiency"
        detail = (f"Shooting stats alone (makes minus attempts) add {shooting:.1f} points per game "
                  "for you in this deal.")
    elif top and abs(model.weights.get(top["stat"], 0)) >= 3:
        key = "scoring_rules"
        detail = (f"The biggest gain is {STAT_NAMES.get(top['stat'], top['stat'])}, worth "
                  f"{top['points_each']} each in your league: {top['change']:+.1f} points per game.")
    else:
        key = "per_game_value"
        detail = (f"The players you get average {sum(model.value(p) for p in result.get):.1f} points "
                  f"per game combined, against {sum(model.value(p) for p in result.give):.1f} for the "
                  "players you give.")
    title, body = LESSONS[key]
    return {"key": key, "title": title, "body": body, "detail": detail}


def player_summary(model: PointsModel, player) -> dict:
    line = line_for(player, model.window) or line_for(player, "season") or {}
    return {
        "id": player.id, "name": player.name, "pro_team": player.pro_team,
        "position": player.position, "injury_status": player.injury_status,
        "on_ir": player.on_ir, "games": int(line.get("GP", 0)),
        "value": round(model.value(player), 2),
    }


def verdict(result: PointsTradeEval) -> str:
    mine, theirs = result.my_delta, result.their_delta
    if mine >= 2 and theirs >= 1:
        return "Helps both teams"
    if mine >= 2 and theirs > -MAX_THEIR_LOSS:
        return "Slightly favors you"
    if mine >= 2:
        return "Favors you clearly, so it may be hard to sell"
    if theirs >= 1:
        return "Helps them more than you"
    return "Doesn't clearly help either team"


def explain_points_trade(model: PointsModel, result: PointsTradeEval) -> dict:
    them = model.league.team(result.opponent_id)
    rows = breakdown(model, result)
    gains = [r for r in rows if r["change"] > 0][:3]
    losses = [r for r in rows if r["change"] < 0][:3]
    def fmt_row(r):
        # For penalized stats, gaining points means fewer of them.
        name = STAT_NAMES.get(r["stat"], r["stat"])
        if model.weights.get(r["stat"], 0) < 0:
            name = ("fewer " if r["change"] > 0 else "more ") + name
        return f'{name} ({r["change"]:+.1f})'
    reasons = model.acceptance_reasons(result)
    if not reasons:
        reasons = [
            f"For {them.name}, projected weekly points go from {result.their_before:.0f} to "
            f"{result.their_after:.0f}, so they have little reason to accept."
        ]
    return {
        "mode": "points",
        "id": "-".join(map(str, sorted(p.id for p in result.give + result.get))),
        "opponent": {"id": them.id, "name": them.name},
        "give": [player_summary(model, p) for p in result.give],
        "get": [player_summary(model, p) for p in result.get],
        "verdict": verdict(result),
        "headline": {
            "change": round(result.my_delta, 1),
            "text": f"{result.my_delta:+.0f} projected fantasy points per week.",
        },
        "my_points": {"before": round(result.my_before), "after": round(result.my_after),
                      "rank_before": result.my_rank_before, "rank_after": result.my_rank_after},
        "their_points": {"before": round(result.their_before), "after": round(result.their_after),
                         "rank_before": result.their_rank_before, "rank_after": result.their_rank_after},
        "summary": (
            f"You give {names(result.give)} and get {names(result.get)}. Your projected weekly "
            f"points go from {result.my_before:.0f} to {result.my_after:.0f}, moving you from "
            f"rank {result.my_rank_before} to {result.my_rank_after} of {len(model.league.teams)}."
        ),
        "why_you": ("Per game, the players you get add the most in " + ", ".join(map(fmt_row, gains)) + ".")
                   if gains else "No stat clearly improves.",
        "cost": ("You lose ground in " + ", ".join(map(fmt_row, losses)) + ".")
                if losses else "No stat clearly gets worse.",
        "why_them": " ".join(reasons),
        "tables": [{
            "title": "Where the points change, per game",
            "columns": ["Stat", "Points each", "Change"],
            "rows": [[STAT_NAMES.get(r["stat"], r["stat"]), r["points_each"], f'{r["change"]:+.1f}']
                     for r in rows],
        }],
        "breakdown": rows,
        "lesson": choose_lesson(model, result, rows),
        "risks": risks(model, result),
    }


def points_team_report(model: PointsModel, team_id: int) -> dict:
    team = model.league.team(team_id)
    active = model.active(team)
    teams = model.league.teams

    def by_stat(players):
        sums = {stat: 0.0 for stat in model.weights}
        for p in players:
            for stat, value in stat_points(model.lines[p.id], model.weights).items():
                sums[stat] += value
        return sums

    mine = by_stat(active)
    everyone = {t.id: by_stat(model.active(t)) for t in teams}
    sources = []
    for stat, points in model.weights.items():
        league_avg = sum(e[stat] for e in everyone.values()) / len(teams)
        rank = 1 + sum(e[stat] > mine[stat] + 1e-9 for e in everyone.values())
        sources.append({
            "stat": STAT_NAMES.get(stat, stat), "points_each": f"{points:+g}",
            "yours": f"{mine[stat]:+.1f}", "league_average": f"{league_avg:+.1f}", "rank": rank,
        })
    roster = []
    for p in sorted(team.players, key=model.value, reverse=True):
        season, recent = model.season_fppg[p.id], model.last30_fppg[p.id]
        roster.append({
            **player_summary(model, p),
            "season_fppg": None if season is None else round(season, 1),
            "last30_fppg": None if recent is None else round(recent, 1),
            "trend": model.streak(p) or "",
            "above_replacement": round(model.value(p) - model.replacement_value, 1),
        })
    strength = model.strength[team_id]
    return {
        "mode": "points",
        "team": {"id": team.id, "name": team.name, "record": f"{team.wins}-{team.losses}-{team.ties}"},
        "weekly": round(strength),
        "starting_slots": model.starting_slots,
        "league_average": round(model.league_average),
        "rank": model.rank(team_id),
        "team_count": len(teams),
        "replacement": round(model.replacement_value, 1),
        "rules": rules_text(model.weights),
        "rules_note": rules_note(model.weights),
        "sources": sources,
        "roster": roster,
    }
