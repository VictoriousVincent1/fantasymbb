"""Turns a computed trade into a plain-English lesson. Every number comes from the evaluation."""
from __future__ import annotations

from typing import Dict, List, Optional

from team_model import LeagueModel, TradeEval
from valuation import PERCENT_PARTS, line_for

SMALL_SAMPLE_GAMES = 10

CATEGORY_HELP = {
    "PTS": "Points per game. Common among starters, so it's rarely the scarcest category.",
    "REB": "Rebounds per game. Mostly from big men, so it often moves with blocks and FG%.",
    "AST": "Assists per game. Concentrated among lead guards, who often also bring turnovers.",
    "STL": "Steals per game. Low totals make it volatile from week to week.",
    "BLK": "Blocks per game. A few players produce most of them, which makes them scarce.",
    "3PM": "Three-pointers made per game. Usually comes from guards and wings.",
    "TO": "Turnovers per game. Lower is better, and high-usage stars hurt here.",
    "FG%": "Made shots divided by attempts. High-volume shooters move it most.",
    "FT%": "Free throws made divided by attempts. High-volume shooters move it most.",
    "3PT%": "Threes made divided by attempts. High-volume shooters move it most.",
}

LESSONS = {
    "consolidation": (
        "Consolidation",
        "Trading two good players for one better one concentrates value in one roster spot. "
        "The open spot can then go to a useful free agent, so the total can rise.",
    ),
    "punting": (
        "Punting",
        "Punting means giving up on a category you're unlikely to win, then using that "
        "production to buy categories you can win. You need only a majority of categories.",
    ),
    "percentage_volume": (
        "Percentage volume",
        "FG% and FT% depend on attempts as well as accuracy. A 90% free-throw shooter on 8 "
        "attempts moves your team much further than one on 2 attempts.",
    ),
    "scarcity": (
        "Category scarcity",
        "When a few players produce most of a category, those players cost more to replace. "
        "Scarce production is hard to find on waivers, so it's worth paying for.",
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
    "category_fit": (
        "Trading surplus for need",
        "A category lead beyond what you need to win it adds little. Moving that surplus "
        "into a close category can win you more matchups, even in a trade of equal value.",
    ),
}

SCARCE_TOP_SHARE = 0.25  # top tenth of players produce at least this share


def fmt(category: str, value: float) -> str:
    return f"{value:.3f}" if category in PERCENT_PARTS else f"{value:.1f}"


def names(players) -> str:
    return " and ".join(p.name for p in players)


def category_rows(model: LeagueModel, result: TradeEval, side: str) -> List[dict]:
    before = getattr(result, f"{side}_before")
    after = getattr(result, f"{side}_after")
    ranks_before = getattr(result, f"{side}_ranks_before")
    ranks_after = getattr(result, f"{side}_ranks_after")
    rows = []
    for category in model.categories:
        name = category.name
        sigma = model.sigma[name]
        change = after[name] - before[name]
        signed = -change if category.reverse else change
        rows.append({
            "category": name,
            "before": fmt(name, before[name]),
            "after": fmt(name, after[name]),
            "rank_before": ranks_before[name],
            "rank_after": ranks_after[name],
            "effect": round(signed / sigma, 2) if sigma > 0 else 0.0,
        })
    return rows


def describe_moves(rows: List[dict], improving: bool, limit: int = 3) -> str:
    ordered = sorted(rows, key=lambda r: r["effect"], reverse=improving)
    picked = [r for r in ordered if (r["effect"] > 0 if improving else r["effect"] < 0)][:limit]
    return ", ".join(
        f'{r["category"]} ({r["before"]} to {r["after"]}, rank {r["rank_before"]} to {r["rank_after"]})'
        for r in picked
    )


def scarce_categories(model: LeagueModel) -> List[str]:
    players = [p for t in model.league.teams for p in t.players if model.valuation.has_data[p.id]]
    scarce = []
    for category in model.categories:
        if category.name in PERCENT_PARTS or category.reverse:
            continue
        values = sorted((model.lines[p.id].get(category.name, 0.0) for p in players), reverse=True)
        total = sum(values)
        top = values[: max(1, len(values) // 10)]
        if total > 0 and sum(top) / total >= SCARCE_TOP_SHARE:
            scarce.append(category.name)
    return scarce


def choose_lesson(model: LeagueModel, result: TradeEval, my_rows: List[dict]) -> dict:
    by_category = {r["category"]: r for r in my_rows}
    best = max(my_rows, key=lambda r: r["effect"])
    key: Optional[str] = None
    detail = ""

    sold = [p for p in result.give if model.streak(p) == "hot"]
    bought = [p for p in result.get if model.streak(p) == "cold"]
    punted = [name for name in model.punts(result.team_id) if by_category[name]["effect"] < 0]

    if len(result.give) > len(result.get) and result.my_change.open_spots:
        key = "consolidation"
        detail = (
            f"You send {names(result.give)} for {names(result.get)}, "
            f"which opens {result.my_change.open_spots} roster spot."
        )
    elif punted and best["effect"] > 0:
        key = "punting"
        detail = (
            f"You're already far behind in {', '.join(punted)}, so losing some there costs little. "
            f"The biggest gain is {best['category']}."
        )
    elif sold:
        key = "selling_high"
        detail = f"{names(sold)} has been well above their season level over the last 30 days."
    elif bought:
        key = "buying_low"
        detail = f"{names(bought)} has been well below their season level over the last 30 days."
    elif best["category"] in PERCENT_PARTS and best["effect"] > 0:
        key = "percentage_volume"
        detail = f"{best['category']} is the biggest gain here, driven by shot volume as well as accuracy."
    elif best["category"] in scarce_categories(model) and best["effect"] > 0:
        key = "scarcity"
        detail = f"{best['category']} is the biggest gain, and a small group of players produces much of it."
    else:
        key = "category_fit"
        detail = f"The biggest gain is {best['category']}, where the deal moves production to where it counts more."

    title, body = LESSONS[key]
    return {"key": key, "title": title, "body": body, "detail": detail}


def risks(model, result) -> List[str]:
    """Risks shared by category and points trades."""
    notes: List[str] = []
    for player in result.get + result.give:
        if player.on_ir or player.injury_status not in ("ACTIVE", "NORMAL", ""):
            label = "on IR" if player.on_ir else player.injury_status.replace("_", " ").lower()
            notes.append(f"{player.name} is listed as {label}.")
        line = line_for(player, "season")
        if not line:
            notes.append(f"{player.name} has no season stats yet, so their value is unknown.")
        elif line["GP"] < SMALL_SAMPLE_GAMES:
            notes.append(
                f"{player.name} has played only {int(line['GP'])} games, so these numbers may not last."
            )
        if model.window == "last_30" and not line_for(player, "last_30"):
            notes.append(f"{player.name} has no last-30-day stats, so season numbers are used.")
    for player in result.get:
        if model.streak(player) == "hot":
            notes.append(f"{player.name} is on a hot streak, so you may be buying at their peak.")
    for player in result.my_change.dropped:
        notes.append(f"You would have to drop {player.name} to stay within the roster limit.")
    if result.my_change.open_spots:
        notes.append(
            f"This leaves you {result.my_change.open_spots} open roster spot. The estimate assumes "
            "you fill it with a free agent similar to the league's lowest-valued rostered players."
        )
    if len(result.get) > len(result.give):
        top_give = max(result.give, key=model.value)
        if model.value(top_give) > max(model.value(p) for p in result.get):
            notes.append(
                f"{top_give.name} is the best player in this deal. Stars are harder to replace than "
                "depth, and this estimate can underrate that, so be careful selling one for two."
            )
    if not result.my_change.lineup_ok:
        notes.append("You could not fill every starting lineup slot after this trade.")
    if not result.their_change.lineup_ok:
        notes.append("The other team could not fill every starting lineup slot after this trade.")
    return notes


def verdict(result: TradeEval, threshold: float = 0.02) -> str:
    mine, theirs = result.my_delta >= threshold, result.their_delta >= threshold
    if mine and theirs:
        return "Helps both teams"
    if mine:
        return "Helps you, but it's hard to see why they'd accept"
    if theirs:
        return "Helps them more than you"
    return "Doesn't clearly help either team"


def player_summary(model: LeagueModel, player) -> dict:
    line = line_for(player, model.window) or line_for(player, "season") or {}
    return {
        "id": player.id,
        "name": player.name,
        "pro_team": player.pro_team,
        "position": player.position,
        "injury_status": player.injury_status,
        "on_ir": player.on_ir,
        "games": int(line.get("GP", 0)),
        "value": round(model.value(player), 2),
    }


def explain_trade(model: LeagueModel, result: TradeEval) -> dict:
    my_rows = category_rows(model, result, "my")
    their_rows = category_rows(model, result, "their")
    them = model.league.team(result.opponent_id)

    mine_up = describe_moves(my_rows, True)
    mine_down = describe_moves(my_rows, False)
    theirs_up = describe_moves(their_rows, True)
    their_gain = round(result.their_delta, 2)
    if their_gain > 0:
        why_them = (
            f"For {them.name}, expected category wins per matchup go from "
            f"{result.their_wins_before:.2f} to {result.their_wins_after:.2f}."
        )
        if theirs_up:
            why_them += f" They gain most in {theirs_up}."
    else:
        why_them = (
            f"For {them.name}, expected category wins per matchup go from "
            f"{result.their_wins_before:.2f} to {result.their_wins_after:.2f}, "
            "so they have little reason to accept."
        )

    table = lambda title, rows: {
        "title": title,
        "columns": ["Category", "Before", "After", "Rank before", "Rank after", "Effect"],
        "rows": [[r["category"], r["before"], r["after"], r["rank_before"], r["rank_after"],
                  f'{r["effect"]:+.2f}'] for r in rows],
    }
    return {
        "mode": "categories",
        "headline": {
            "change": round(result.my_delta, 2),
            "text": f"{result.my_delta:+.2f} expected category wins per matchup.",
        },
        "tables": [table("Your categories, before and after", my_rows),
                   table(f"{them.name} categories, before and after", their_rows)],
        "id": "-".join(map(str, sorted(p.id for p in result.give + result.get))),
        "opponent": {"id": them.id, "name": them.name},
        "give": [player_summary(model, p) for p in result.give],
        "get": [player_summary(model, p) for p in result.get],
        "verdict": verdict(result),
        "my_wins": {
            "before": round(result.my_wins_before, 2),
            "after": round(result.my_wins_after, 2),
            "change": round(result.my_delta, 2),
        },
        "their_wins": {
            "before": round(result.their_wins_before, 2),
            "after": round(result.their_wins_after, 2),
            "change": their_gain,
        },
        "my_value_change": round(result.my_change.value_gain, 2),
        "their_value_change": round(result.their_change.value_gain, 2),
        "summary": (
            f"You give {names(result.give)} and get {names(result.get)}. Your expected category "
            f"wins per matchup go from {result.my_wins_before:.2f} to {result.my_wins_after:.2f}."
        ),
        "why_you": f"You gain most in {mine_up}." if mine_up else "No category clearly improves.",
        "cost": f"You give up ground in {mine_down}." if mine_down else "No category clearly gets worse.",
        "why_them": why_them,
        "my_categories": my_rows,
        "their_categories": their_rows,
        "lesson": choose_lesson(model, result, my_rows),
        "risks": risks(model, result),
        "dropped": [p.name for p in result.my_change.dropped],
        "their_dropped": [p.name for p in result.their_change.dropped],
    }


def team_report(model: LeagueModel, team_id: int) -> dict:
    team = model.league.team(team_id)
    ranks = model.ranks(team_id)
    categories = [
        {
            "category": c.name,
            "value": fmt(c.name, model.totals[team_id][c.name]),
            "league_average": fmt(c.name, model.mean[c.name]),
            "rank": ranks[c.name],
            "label": model.category_label(team_id, c),
            "lower_is_better": c.reverse,
            "help": CATEGORY_HELP.get(c.name, ""),
        }
        for c in model.categories
    ]
    roster = sorted(team.players, key=model.value, reverse=True)
    return {
        "mode": "categories",
        "team": {"id": team.id, "name": team.name, "record": f"{team.wins}-{team.losses}-{team.ties}"},
        "expected_wins": round(model.expected_wins(team_id), 2),
        "category_count": len(model.categories),
        "team_count": len(model.league.teams),
        "categories": categories,
        "punts": model.punts(team_id),
        "roster": [
            {
                **player_summary(model, p),
                "z": {k: round(v, 2) for k, v in model.valuation.z[p.id].items()},
            }
            for p in roster
        ],
    }
