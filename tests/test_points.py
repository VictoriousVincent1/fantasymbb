import re

import pytest
from fastapi.testclient import TestClient

from app import Coach, create_app
from config import Settings
from conftest import POINTS_RULES, make_league, make_player
from espn_client import is_points_scoring, points_from_settings
from points_explain import explain_points_trade, points_team_report, rules_note
from points_model import (MAX_THEIR_LOSS, MIN_MY_GAIN, PointsModel, fantasy_points,
                          deal_score, search_points_trades, start_weights)
from store import Store


def test_fantasy_points_by_hand():
    line = {"PTS": 20, "REB": 10, "AST": 5, "STL": 1, "BLK": 2, "3PM": 2, "TO": 3,
            "FGM": 8, "FGA": 16, "FTM": 2, "FTA": 3}
    # 20 + 10 + 10 + 4 + 8 + 2 - 6 + 16 - 16 + 2 - 3 = 47
    assert fantasy_points(line, POINTS_RULES) == pytest.approx(47.0)


def test_start_weights_by_hand():
    # One starting slot, every day a coin flip: the best player starts 7 * 0.5 = 3.5 times a
    # week; the second only when the best doesn't play, 7 * 0.5 * 0.5 = 1.75.
    assert start_weights(3, 1, day_mix=((0.5, 1.0),)) == pytest.approx([3.5, 1.75, 0.875])
    # With plenty of slots, everyone who plays starts.
    assert start_weights(3, 10, day_mix=((0.5, 1.0),)) == pytest.approx([3.5, 3.5, 3.5])
    # Deep benches lose starts: the 16th player with 10 slots starts less than the 1st.
    weights = start_weights(16, 10)
    assert weights[0] == pytest.approx(3.5)
    assert weights[9] == pytest.approx(3.5)  # the top 10 always start when they play
    assert weights[15] < weights[10] < weights[9]


def test_strength_rank_and_replacement():
    a = [make_player(1, PTS=30.0), make_player(2, PTS=10.0)]
    b = [make_player(3, PTS=25.0), make_player(4, PTS=5.0, lineup_slot="IR", injury="OUT")]
    league = make_league([a, b])  # one UT starting slot
    league.scoring_mode, league.points_per_stat = "points", {"PTS": 1.0}
    model = PointsModel(league)
    w = start_weights(4, 1)
    assert model.strength[1] == pytest.approx(30 * w[0] + 10 * w[1])
    assert model.strength[2] == pytest.approx(25 * w[0])  # IR players don't count
    assert model.rank(1) == 1 and model.rank(2) == 2
    assert model.replacement_value == pytest.approx(7.5)  # bottom two with data: 10 and 5


def test_streak_needs_a_clear_gap():
    hot = make_player(1, PTS=20.0)
    hot.stats["last_30"] = {"PTS": 26.0, "GP": 8.0}
    flat = make_player(2, PTS=20.0)
    flat.stats["last_30"] = {"PTS": 21.0, "GP": 8.0}
    league = make_league([[hot], [flat]])
    league.scoring_mode, league.points_per_stat = "points", {"PTS": 1.0}
    model = PointsModel(league)
    assert model.streak(hot) == "hot"
    assert model.streak(flat) is None


@pytest.fixture
def points_results(points_league):
    model = PointsModel(points_league)
    return model, search_points_trades(model, 1)


def test_points_search_rules(points_results):
    model, results = points_results
    assert results
    league = model.league
    mine = {p.id for p in league.team(1).players}
    for r in results:
        assert r.my_delta >= MIN_MY_GAIN
        assert r.their_delta >= -MAX_THEIR_LOSS
        assert model.acceptance_reasons(r)
        assert {p.id for p in r.give} <= mine
        assert {p.id for p in r.get} <= {p.id for p in league.team(r.opponent_id).players}
        assert len(r.my_change.players) <= league.roster_limit
        assert r.my_change.lineup_ok and r.their_change.lineup_ok
        assert not any(p.is_out for p in r.give + r.get)
        rebuilt = model.team_strength(r.my_change.players, r.my_change.open_spots)
        assert r.my_after == pytest.approx(rebuilt)
    scores = [deal_score(r) for r in results]
    assert scores == sorted(scores, reverse=True)


def test_points_explanations_match_numbers(points_results):
    model, results = points_results
    for r in results[:25]:
        e = explain_points_trade(model, r)
        assert f"from {r.my_before:.0f} to {r.my_after:.0f}" in e["summary"]
        assert f"rank {r.my_rank_before} to {r.my_rank_after}" in e["summary"]
        assert e["headline"]["text"].startswith(f"{r.my_delta:+.0f}")
        assert e["lesson"]["title"] and e["lesson"]["detail"] and e["why_them"]
        # The stat breakdown adds up to the players' combined points change.
        players_change = sum(model.value(p) for p in r.get) - sum(model.value(p) for p in r.give)
        assert sum(row["change"] for row in e["breakdown"]) == pytest.approx(players_change, abs=0.1 * len(e["breakdown"]))
        for number in re.findall(r"averaged ([\d.]+) over the last 30 days", e["lesson"]["detail"]):
            assert any(f"{v:.1f}" == number for v in model.last30_fppg.values() if v is not None)


def test_points_team_report(points_league):
    model = PointsModel(points_league)
    report = points_team_report(model, 1)
    assert report["mode"] == "points"
    assert report["weekly"] == round(model.strength[1])
    assert "+4 per steal" in report["rules"]
    assert "Steals and blocks are worth the most" in report["rules_note"]
    assert "cost points" in report["rules_note"]


def test_rules_note_without_attempt_penalties():
    note = rules_note({"PTS": 1.0, "REB": 1.2})
    assert note.startswith("Rebounds are worth the most")
    assert "cost points" not in note


def test_players_without_stats_are_not_dropped():
    healthy = [make_player(i, PTS=float(10 + i)) for i in range(1, 4)]
    unknown = make_player(9)
    unknown.stats = {}
    giver = [make_player(20, PTS=40.0), make_player(21, PTS=30.0)]
    league = make_league([healthy + [unknown], giver], roster_limit=4)
    league.scoring_mode, league.points_per_stat = "points", {"PTS": 1.0}
    model = PointsModel(league)
    change = model.apply_trade(league.team(1), [healthy[2]], giver)
    assert [p.id for p in change.dropped] == [1]  # lowest player with stats, not the unknown one


def test_points_settings_parsing():
    scoring = {"scoringItems": [{"statId": 1, "points": 4.0}, {"statId": 14, "points": -1.0},
                                {"statId": 7, "points": 0}]}
    assert points_from_settings(scoring) == {"BLK": 4.0, "FGA": -1.0}
    assert is_points_scoring("H2H_POINTS") and not is_points_scoring("H2H_CATEGORY")


def test_points_api(tmp_path, points_league):
    coach = Coach(Store(tmp_path / "s.sqlite"), Settings(None, 2027, None, None, None))
    coach.set_league(points_league)
    client = TestClient(create_app(coach))
    assert client.get("/api/league").json()["scoring_mode"] == "points"
    assert client.get("/api/team/1").json()["mode"] == "points"
    trades = client.get("/api/trades", params={"team_id": 1, "limit": 3}).json()["trades"]
    assert trades and all(t["mode"] == "points" for t in trades)
    assert client.get("/api/trades", params={"team_id": 1, "need": "BLK"}).status_code == 400
    league = client.get("/api/league").json()
    body = {"team_id": 1, "opponent_id": 2, "give": [league["teams"][0]["players"][0]["id"]],
            "get": [league["teams"][1]["players"][0]["id"]]}
    checked = client.post("/api/trade-check", json=body).json()
    assert checked["mode"] == "points" and checked["verdict"]
