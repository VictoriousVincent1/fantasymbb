"""Analyzes pending trade offers with the same model as Trade Check. Never acts on ESPN."""
from __future__ import annotations

import datetime as dt
from typing import Callable, List, Optional

from models import League, Offer

# Gain thresholds for advice: (worth accepting, close call). Below the second, decline.
ADVICE_THRESHOLDS = {"categories": (0.05, -0.05), "points": (5.0, -5.0)}


def advice(mode: str, my_delta: float, incoming: bool) -> dict:
    accept, close = ADVICE_THRESHOLDS[mode]
    if my_delta >= accept:
        key = "accept"
        text = "Worth accepting" if incoming else "Good for you if they accept"
    elif my_delta >= close:
        key = "close"
        text = "Close call: about even for you"
    else:
        key = "decline"
        text = "Better to decline or counter" if incoming else "This offer would hurt your team"
    return {"key": key, "text": text}


def offers_for_team(league: League, team_id: int, model, explain: Callable,
                    now_ms: Optional[int] = None) -> dict:
    now_ms = now_ms or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    players = {p.id: p for t in league.teams for p in t.players}
    roster_of = {p.id: t.id for t in league.teams for p in t.players}
    team_names = {t.id: t.name for t in league.teams}
    incoming: List[dict] = []
    sent: List[dict] = []

    for offer in league.offers:
        if team_id not in offer.team_ids:
            continue
        if offer.expires_ms and offer.expires_ms < now_ms:
            continue
        is_incoming = offer.proposer_id != team_id
        entry = {
            "offer_id": offer.id,
            "incoming": is_incoming,
            "from_team": team_names.get(offer.proposer_id, "Another team"),
            "proposed_ms": offer.proposed_ms,
            "expires_ms": offer.expires_ms,
            "notes": [],
        }
        if offer.includes_budget:
            entry["notes"].append("This offer also moves FAAB budget, which the coach doesn't value.")
        others = [t for t in offer.team_ids if t != team_id]
        missing = [m for m in offer.moves if m["player_id"] not in players]
        moved = [m for m in offer.moves
                 if m["player_id"] in players and roster_of[m["player_id"]] != m["from_team"]]

        if len(others) != 1:
            entry["problem"] = "This offer involves more than two teams, which the coach can't analyze yet."
        elif missing or moved:
            entry["problem"] = ("Some players in this offer have changed teams since the last update. "
                                "Click Update from ESPN, then check again.")
        else:
            give = [players[m["player_id"]] for m in offer.moves if m["from_team"] == team_id]
            get = [players[m["player_id"]] for m in offer.moves if m["to_team"] == team_id]
            if not give or not get:
                entry["problem"] = "One side of this offer has no players, so there's nothing to compare."
            else:
                result = model.evaluate(team_id, others[0], give, get)
                entry["analysis"] = explain(model, result)
                entry["advice"] = advice(league.scoring_mode, result.my_delta, is_incoming)
        (incoming if is_incoming else sent).append(entry)

    return {"incoming": incoming, "sent": sent, "error": league.offers_error,
            "fetched_at": league.fetched_at, "source": league.source}
