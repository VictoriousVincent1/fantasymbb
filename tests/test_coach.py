import datetime as dt
import math
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import Coach, create_app
from conftest import make_league, make_player
from config import Settings, default_season_year, parse_env
from espn_client import categories_from_settings, is_category_scoring, lineup_from_settings, player_from_espn
from explain import explain_trade
from models import Category, League
from store import MIN_REFRESH_SECONDS, Store, refresh_wait_seconds
from team_model import LeagueModel, can_fill_lineup
from trade_search import MIN_MY_GAIN, MIN_THEIR_GAIN, diversify, search_trades
from valuation import value_players


# Valuation, checked against hand calculations
def test_counting_z_scores():
    # Values 10, 20, 30: mean 20, population std sqrt(200/3) = 8.165
    players = [make_player(i, PTS=v) for i, v in enumerate([10.0, 20.0, 30.0])]
    result = value_players(players, [Category("PTS")], "season")
    assert result.z[0]["PTS"] == pytest.approx(-1.2247, abs=1e-3)
    assert result.z[1]["PTS"] == pytest.approx(0.0)
    assert result.z[2]["PTS"] == pytest.approx(1.2247, abs=1e-3)


def test_turnovers_are_inverted():
    players = [make_player(0, TO=1.0), make_player(1, TO=3.0)]
    result = value_players(players, [Category("TO", reverse=True)], "season")
    assert result.z[0]["TO"] == pytest.approx(1.0)
    assert result.z[1]["TO"] == pytest.approx(-1.0)


def test_percentages_are_weighted_by_volume():
    # Both shoot 90%, but one takes ten times the attempts.
    players = [
        make_player(0, FTM=9.0, FTA=10.0),
        make_player(1, FTM=0.9, FTA=1.0),
        make_player(2, FTM=3.5, FTA=7.0),
    ]
    z = value_players(players, [Category("FT%")], "season").z
    assert z[0]["FT%"] > z[1]["FT%"] > z[2]["FT%"]
    # Pool: 13.4/18 = 0.7444. Impacts: 1.5556, 0.1556, -1.7111.
    impacts = [1.5556, 0.1556, -1.7111]
    mean = sum(impacts) / 3
    std = math.sqrt(sum((v - mean) ** 2 for v in impacts) / 3)
    assert z[0]["FT%"] == pytest.approx((1.5556 - mean) / std, abs=1e-3)


def test_player_without_stats_is_neutral():
    empty = make_player(9)
    empty.stats = {}
    result = value_players([empty, make_player(1, PTS=5.0), make_player(2, PTS=9.0)], [Category("PTS")], "season")
    assert result.total[9] == 0.0
    assert result.has_data[9] is False


# Team model
def test_ranks_totals_and_reverse_categories():
    categories = [Category("PTS"), Category("TO", reverse=True), Category("FG%")]
    a = [make_player(1, PTS=10.0, TO=1.0, FGM=5.0, FGA=10.0), make_player(2, PTS=10.0, TO=1.0, FGM=1.0, FGA=10.0)]
    b = [make_player(3, PTS=30.0, TO=4.0, FGM=4.0, FGA=5.0)]
    c = [make_player(4, PTS=25.0, TO=3.0, FGM=3.0, FGA=10.0)]
    model = LeagueModel(make_league([a, b, c], categories))
    assert model.totals[1]["PTS"] == 20.0
    assert model.totals[1]["FG%"] == pytest.approx(0.3)  # 6/20, not the average of 50% and 10%
    assert model.ranks(1) == {"PTS": 3, "TO": 1, "FG%": 2}
    assert model.ranks(2) == {"PTS": 1, "TO": 3, "FG%": 1}


def test_expected_wins_by_hand():
    a, b = [make_player(1, PTS=10.0)], [make_player(2, PTS=20.0)]
    model = LeagueModel(make_league([a, b]))
    # Gap 10, sigma 5: P(win) = normal CDF(10 / (sqrt(2) * 5)) = CDF(1.4142) = 0.9214
    assert model.expected_wins(2) == pytest.approx(0.9214, abs=1e-3)
    assert model.expected_wins(1) == pytest.approx(1 - 0.9214, abs=1e-3)


def test_ir_players_do_not_count():
    a = [make_player(1, PTS=10.0), make_player(2, PTS=50.0, lineup_slot="IR", injury="OUT")]
    model = LeagueModel(make_league([a, [make_player(3, PTS=5.0)]]))
    assert model.totals[1]["PTS"] == 10.0


def test_lineup_matching_needs_assignment_not_counts():
    both = make_player(1, slots=("PG", "SG"))
    pg_only = make_player(2, slots=("PG",))
    assert can_fill_lineup([both, pg_only], {"PG": 1, "SG": 1})
    assert not can_fill_lineup([pg_only, make_player(3, slots=("PG",))], {"PG": 1, "SG": 1})
    assert can_fill_lineup([pg_only], {"PG": 1, "SG": 1}, open_spots=1)


def test_two_for_one_forces_drop_and_leaves_open_spot():
    a = [make_player(i, PTS=float(i)) for i in range(1, 4)]
    b = [make_player(i, PTS=float(i)) for i in range(11, 14)]
    model = LeagueModel(make_league([a, b], roster_limit=3))
    me, them = model.league.team(1), model.league.team(2)
    # I give one and get two: over the limit, so my lowest player who isn't arriving is dropped.
    change = model.apply_trade(me, [a[2]], [b[0], b[1]])
    assert [p.id for p in change.dropped] == [1]
    assert len(change.players) == 3
    # They give two and get one: an open spot.
    change = model.apply_trade(them, [b[0], b[1]], [a[2]])
    assert change.open_spots == 1
    assert not change.dropped


# Trade search on the demo league
@pytest.fixture
def demo_results(demo_league):
    model = LeagueModel(demo_league)
    return model, search_trades(model, 1)


def test_search_finds_trades_that_help_both_teams(demo_results):
    model, results = demo_results
    assert results
    for r in results:
        assert r.my_delta >= MIN_MY_GAIN
        assert r.their_delta >= MIN_THEIR_GAIN


def test_search_respects_rosters_and_limits(demo_results):
    model, results = demo_results
    league = model.league
    mine = {p.id for p in league.team(1).players}
    for r in results:
        theirs = {p.id for p in league.team(r.opponent_id).players}
        assert {p.id for p in r.give} <= mine
        assert {p.id for p in r.get} <= theirs
        assert r.opponent_id != 1
        assert len(r.my_change.players) <= league.roster_limit
        assert len(r.their_change.players) <= league.roster_limit
        assert r.my_change.lineup_ok and r.their_change.lineup_ok
        assert not any(p.is_out for p in r.give + r.get)
        # Everyone traded must be on the right roster afterward.
        after = {p.id for p in r.my_change.players}
        assert not {p.id for p in r.give} & after


def test_after_totals_match_a_rebuilt_roster(demo_results):
    model, results = demo_results
    r = results[0]
    expected = model.team_totals(r.my_change.players, r.my_change.open_spots)
    assert r.my_after == expected


def test_results_are_sorted_best_first(demo_results):
    _, results = demo_results
    deltas = [r.my_delta for r in results]
    assert deltas == sorted(deltas, reverse=True)


def test_diversify_limits_repeats_and_keeps_order(demo_results):
    _, results = demo_results
    kept = diversify(results, per_player=2)
    assert kept[0] is results[0]
    counts = {}
    for r in kept:
        for p in r.give:
            counts[p.id] = counts.get(p.id, 0) + 1
    assert max(counts.values()) <= 2


# Explanations
def test_explanation_numbers_match_computed_values(demo_results):
    model, results = demo_results
    for r in results[:25]:
        e = explain_trade(model, r)
        assert f"{r.my_wins_before:.2f} to {r.my_wins_after:.2f}" in e["summary"]
        assert f"{r.their_wins_before:.2f} to {r.their_wins_after:.2f}" in e["why_them"]
        rows = {row["category"]: row for row in e["my_categories"]}
        for name, before, after, rank_a, rank_b in re.findall(
            r"([\w%]+) \(([\d.]+) to ([\d.]+), rank (\d+) to (\d+)\)", e["why_you"] + e["cost"]
        ):
            row = rows[name]
            assert (before, after) == (row["before"], row["after"])
            assert (int(rank_a), int(rank_b)) == (row["rank_before"], row["rank_after"])
            digits = 3 if name.endswith("%") else 1
            assert before == f"{r.my_before[name]:.{digits}f}"
            assert after == f"{r.my_after[name]:.{digits}f}"


def test_every_explanation_has_a_lesson_and_named_players(demo_results):
    model, results = demo_results
    for r in results:
        e = explain_trade(model, r)
        assert e["lesson"]["title"] and e["lesson"]["body"] and e["lesson"]["detail"]
        for player in r.give + r.get:
            assert player.name in e["summary"]


def test_forced_drop_appears_in_risks(demo_results):
    model, results = demo_results
    with_drop = next(r for r in results if r.my_change.dropped)
    text = " ".join(explain_trade(model, with_drop)["risks"])
    assert with_drop.my_change.dropped[0].name in text


def test_selling_a_star_for_depth_is_flagged(demo_results):
    model, results = demo_results
    deal = next(
        r for r in results
        if len(r.get) > len(r.give)
        and max(map(model.value, r.give)) > max(map(model.value, r.get))
    )
    assert any("best player in this deal" in note for note in explain_trade(model, deal)["risks"])


# ESPN adapter, using the wrapper's own Player class on ESPN-shaped data
def test_player_from_real_wrapper_shape():
    from espn_api.basketball.player import Player as EspnPlayer

    raw = {
        "fullName": "Test Player", "id": 7, "defaultPositionId": 1, "lineupSlotId": 13,
        "eligibleSlots": [0, 5, 11, 12, 13], "acquisitionType": "DRAFT", "proTeamId": 13,
        "injuryStatus": "OUT", "positionalRanking": 1,
        "playerPoolEntry": {"player": {
            "injuryStatus": "OUT", "injured": True,
            "stats": [
                {"id": "002027", "seasonId": 2027, "scoringPeriodId": 0, "appliedTotal": 0,
                 "stats": {"0": 200.0, "42": 10.0, "19": 0.5},
                 "averageStats": {"0": 20.0, "42": 10.0, "19": 0.5}},
                {"id": "032027", "seasonId": 2027, "scoringPeriodId": 0, "appliedTotal": 0,
                 "stats": {"0": 50.0, "42": 2.0}},
            ],
        }},
    }
    player = player_from_espn(EspnPlayer(raw, 2027), 2027)
    assert player.on_ir and player.is_out
    assert player.eligible_slots == ["PG", "G", "UT", "BE", "IR"]
    assert player.stats["season"]["PTS"] == 20.0
    assert player.stats["season"]["GP"] == 10.0
    # With no averages, per-game numbers come from totals.
    assert player.stats["last_30"]["PTS"] == 25.0


def test_settings_parsing():
    scoring = {"scoringItems": [{"statId": 0}, {"statId": 11, "isReverseItem": True}, {"statId": 19}]}
    assert [(c.name, c.reverse) for c in categories_from_settings(scoring)] == [
        ("PTS", False), ("TO", True), ("FG%", False)]
    lineup = {"rosterSettings": {"lineupSlotCounts": {"0": 1, "11": 3, "12": 3, "13": 1, "1": 0}}}
    assert lineup_from_settings(lineup) == {"PG": 1, "UT": 3, "BE": 3, "IR": 1}
    assert is_category_scoring("H2H_CATEGORY") and is_category_scoring("ROTO")
    assert is_category_scoring("H2H_MOST_CATEGORIES")
    assert not is_category_scoring("H2H_POINTS") and not is_category_scoring(None)


def test_config():
    assert parse_env("# note\nESPN_S2='abc=='\nESPN_YEAR = 2027\n") == {"ESPN_S2": "abc==", "ESPN_YEAR": "2027"}
    assert default_season_year(dt.date(2026, 11, 7)) == 2027
    assert default_season_year(dt.date(2027, 3, 1)) == 2027


# Snapshots
def test_store_round_trip_and_refresh_limit(tmp_path, demo_league):
    store = Store(tmp_path / "s.sqlite")
    assert store.latest(0, 2027) is None
    store.save(demo_league)
    loaded = store.latest(0, 2027)
    assert loaded.to_dict() == demo_league.to_dict()
    fetched = dt.datetime.fromisoformat(demo_league.fetched_at)
    assert refresh_wait_seconds(None) == 0
    assert refresh_wait_seconds(demo_league, fetched) == MIN_REFRESH_SECONDS
    assert refresh_wait_seconds(demo_league, fetched + dt.timedelta(seconds=MIN_REFRESH_SECONDS)) == 0


# API
@pytest.fixture
def client(tmp_path):
    settings = Settings(None, 2027, None, None, None)
    return TestClient(create_app(Coach(Store(tmp_path / "s.sqlite"), settings)))


def test_api_flow(client):
    league = client.get("/api/league").json()
    assert league["source"] == "demo"
    assert client.get("/api/team/1").json()["team"]["id"] == 1
    trades = client.get("/api/trades", params={"team_id": 1, "limit": 5}).json()
    assert 0 < len(trades["trades"]) <= 5
    needed = client.get("/api/trades", params={"team_id": 1, "need": "BLK"}).json()
    for t in needed["trades"]:
        row = next(r for r in t["my_categories"] if r["category"] == "BLK")
        assert row["effect"] > 0


def test_trade_check_validates_input(client):
    league = client.get("/api/league").json()
    mine = league["teams"][0]["players"][0]["id"]
    theirs = league["teams"][1]["players"][0]["id"]
    ok = client.post("/api/trade-check", json={"team_id": 1, "opponent_id": 2, "give": [mine], "get": [theirs]})
    assert ok.status_code == 200 and ok.json()["verdict"]
    assert client.post("/api/trade-check", json={"team_id": 1, "opponent_id": 2, "give": [theirs], "get": [mine]}).status_code == 400
    assert client.post("/api/trade-check", json={"team_id": 1, "opponent_id": 2, "give": [], "get": [theirs]}).status_code == 400
    assert client.post("/api/trade-check", json={"team_id": 1, "opponent_id": 1, "give": [mine], "get": [mine]}).status_code == 400
    assert client.get("/api/team/999").status_code == 404
    assert client.get("/api/team/1", params={"window": "nope"}).status_code == 400
    assert client.post("/api/refresh").status_code == 400
