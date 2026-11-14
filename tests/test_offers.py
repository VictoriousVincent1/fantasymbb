import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import Coach, create_app
from config import Settings
from espn_client import offers_from_transactions
from explain import explain_trade
from models import League, Offer
from offers import advice, offers_for_team
from store import Store
from team_model import LeagueModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_espn import anonymize

NOW = 1_800_000_000_000


def tx(status="PENDING", type_="TRADE_PROPOSAL", items=None, **extra):
    return {"id": extra.pop("id", "t1"), "type": type_, "status": status, "teamId": 3,
            "proposedDate": 100, "expirationDate": NOW + 1, "items": items or [], **extra}


def item(player, frm, to, type_="TRADE"):
    return {"playerId": player, "fromTeamId": frm, "toTeamId": to, "type": type_,
            "fromLineupSlotId": 12, "toLineupSlotId": -1}


# Reading ESPN's transaction records (same shape as ESPN's mTransactions2 view)
def test_only_pending_proposals_become_offers():
    records = [
        tx(items=[item(10, 3, 1), item(20, 1, 3), item(0, 1, 3, "ACQUISITION_BUDGET_TRADE")]),
        tx(id="t2", status="CANCELED", items=[item(10, 3, 1)]),
        tx(id="t3", type_="TRADE_DECLINE", items=[item(10, 3, 1)]),
        tx(id="t4", type_="FREEAGENT", status="EXECUTED", items=[item(10, 0, 1, "ADD")]),
        tx(items=[item(10, 3, 1), item(20, 1, 3)]),  # the same offer seen in two periods
    ]
    offers = offers_from_transactions(records)
    assert len(offers) == 1
    offer = offers[0]
    assert offer.proposer_id == 3 and offer.expires_ms == NOW + 1
    assert offer.moves == [{"player_id": 10, "from_team": 3, "to_team": 1},
                           {"player_id": 20, "from_team": 1, "to_team": 3}]
    assert offer.team_ids == [1, 3]


def test_budget_is_flagged():
    records = [tx(items=[item(10, 3, 1), item(0, 1, 3, "ACQUISITION_BUDGET_TRADE")])]
    assert offers_from_transactions(records)[0].includes_budget


def test_snapshots_never_keep_offers(demo_league):
    data = anonymize(demo_league.to_dict())
    assert data["offers"] == []


# Analyzing offers for a team
def make_offer(league, offer_id, proposer, give, get, them, expires=NOW + 1, budget=False):
    moves = ([{"player_id": p.id, "from_team": 1, "to_team": them} for p in give] +
             [{"player_id": p.id, "from_team": them, "to_team": 1} for p in get])
    return Offer(offer_id, proposer, 1, expires, moves, budget)


def analyze(league, team_id=1):
    model = LeagueModel(league)
    return offers_for_team(league, team_id, model, explain_trade, now_ms=NOW)


def test_incoming_and_sent_offers(demo_league):
    me, them = demo_league.team(1).players, demo_league.team(3).players
    demo_league.offers = [
        make_offer(demo_league, "in", 3, [me[0]], [them[0]], 3, budget=True),
        make_offer(demo_league, "out", 1, [me[1]], [them[1]], 3),
        make_offer(demo_league, "old", 3, [me[2]], [them[2]], 3, expires=NOW - 1),
    ]
    result = analyze(demo_league)
    assert [o["offer_id"] for o in result["incoming"]] == ["in"]
    assert [o["offer_id"] for o in result["sent"]] == ["out"]  # the expired one is hidden
    incoming = result["incoming"][0]
    assert [p["id"] for p in incoming["analysis"]["give"]] == [me[0].id]
    assert [p["id"] for p in incoming["analysis"]["get"]] == [them[0].id]
    assert incoming["advice"]["key"] in ("accept", "close", "decline")
    assert "FAAB" in incoming["notes"][0]
    # The other team sees the same offer from their side.
    theirs = analyze(demo_league, team_id=3)
    assert [o["offer_id"] for o in theirs["sent"]] == ["in"]
    assert [o["offer_id"] for o in theirs["incoming"]] == ["out"]


def test_offer_analysis_matches_trade_check(demo_league):
    me, them = demo_league.team(1).players, demo_league.team(3).players
    demo_league.offers = [make_offer(demo_league, "in", 3, [me[0]], [them[0]], 3)]
    model = LeagueModel(demo_league)
    checked = explain_trade(model, model.evaluate(1, 3, [me[0]], [them[0]]))
    assert analyze(demo_league)["incoming"][0]["analysis"] == checked


def test_offers_that_cant_be_analyzed(demo_league):
    me, t3, t4 = (demo_league.team(i).players for i in (1, 3, 4))
    three_way = Offer("three", 3, 1, NOW + 1, [
        {"player_id": me[0].id, "from_team": 1, "to_team": 3},
        {"player_id": t3[0].id, "from_team": 3, "to_team": 4},
        {"player_id": t4[0].id, "from_team": 4, "to_team": 1},
    ])
    stale = Offer("stale", 3, 1, NOW + 1, [
        {"player_id": me[0].id, "from_team": 1, "to_team": 3},
        {"player_id": 999999, "from_team": 3, "to_team": 1},
    ])
    moved = Offer("moved", 3, 1, NOW + 1, [
        {"player_id": me[0].id, "from_team": 1, "to_team": 3},
        {"player_id": t4[0].id, "from_team": 3, "to_team": 1},  # actually on team 4
    ])
    demo_league.offers = [three_way, stale, moved]
    problems = {o["offer_id"]: o["problem"] for o in analyze(demo_league)["incoming"]}
    assert "more than two teams" in problems["three"]
    assert "changed teams" in problems["stale"]
    assert "changed teams" in problems["moved"]


def test_offers_for_other_teams_are_ignored(demo_league):
    t2, t3 = demo_league.team(2).players, demo_league.team(3).players
    demo_league.offers = [Offer("x", 2, 1, NOW + 1, [
        {"player_id": t2[0].id, "from_team": 2, "to_team": 3},
        {"player_id": t3[0].id, "from_team": 3, "to_team": 2},
    ])]
    result = analyze(demo_league)
    assert result["incoming"] == [] and result["sent"] == []


@pytest.mark.parametrize("mode, delta, key", [
    ("categories", 0.10, "accept"), ("categories", 0.0, "close"), ("categories", -0.2, "decline"),
    ("points", 6.0, "accept"), ("points", -4.0, "close"), ("points", -6.0, "decline"),
])
def test_advice_thresholds(mode, delta, key):
    assert advice(mode, delta, incoming=True)["key"] == key


def test_offers_api_on_demo_league(tmp_path):
    coach = Coach(Store(tmp_path / "s.sqlite"), Settings(None, 2027, None, None, None))
    client = TestClient(create_app(coach, password="", require_password=False))
    result = client.get("/api/offers", params={"team_id": 1}).json()
    assert result["source"] == "demo"
    assert len(result["incoming"]) == 2 and len(result["sent"]) == 1
    assert all("analysis" in o and "advice" in o for o in result["incoming"] + result["sent"])
    assert client.get("/api/offers", params={"team_id": 999}).status_code == 404
