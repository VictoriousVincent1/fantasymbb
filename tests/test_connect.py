"""Visitors connecting their own leagues on a public site."""
import dataclasses
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import Coach, RateLimit, Sessions, create_app
from config import Settings
from connect import BadInput, clean_cookies, clean_swid, parse_league_id
from espn_client import LeagueNotFound, PrivateLeague, viewer_team_id
from models import League
from store import Store

SWID = "{ABCDEF12-3456-7890-ABCD-EF1234567890}"
S2 = "AEB%2Bexample%2Fcookie%3Dvalue1234567890"


def test_league_id_from_number_or_url():
    assert parse_league_id(" 12345678 ") == 12345678
    assert parse_league_id("https://fantasy.espn.com/basketball/league?leagueId=987654&seasonId=2027") == 987654
    assert parse_league_id("fantasy.espn.com/basketball/team?leagueId=42&teamId=3") == 42
    for bad in ("", "my league", "https://fantasy.espn.com/basketball/", "1234567890123"):
        with pytest.raises(BadInput):
            parse_league_id(bad)


def test_cookies_are_checked_and_normalized():
    assert clean_swid("abcdef12-3456-7890-abcd-ef1234567890") == SWID
    assert clean_swid(f'"{SWID}"') == SWID
    assert clean_cookies(None, "") == (None, None)
    assert clean_cookies(f" {S2} ", SWID) == (S2, SWID)
    with pytest.raises(BadInput):
        clean_cookies(S2, None)  # both or neither
    with pytest.raises(BadInput):
        clean_swid("not-a-swid")
    with pytest.raises(BadInput) as error:
        clean_cookies("has spaces in it which is wrong", SWID)
    assert "spaces in it" not in str(error.value)  # messages never repeat the input


class Owner:
    def __init__(self, team_id, owners):
        self.team_id, self.owners = team_id, owners


def test_viewer_team_comes_from_swid():
    teams = [Owner(1, [{"id": "{OTHER}"}]), Owner(2, [{"id": SWID.lower()}])]
    assert viewer_team_id(teams, SWID) == 2
    assert viewer_team_id(teams, None) is None
    assert viewer_team_id([Owner(3, None)], SWID) is None


def test_sessions_expire_and_drop_the_oldest():
    now = [0.0]
    sessions = Sessions(limit=2, ttl=100, clock=lambda: now[0])
    a, b = sessions.add("A"), sessions.add("B")
    assert sessions.get(a) == "A"  # also marks A as recently used
    c = sessions.add("C")
    assert sessions.get(b) is None and sessions.get(a) == "A" and sessions.get(c) == "C"
    now[0] = 150
    assert sessions.get(a) is None
    assert len({a, b, c}) == 3


def test_rate_limit_window():
    now = [0.0]
    limit = RateLimit(2, 10, clock=lambda: now[0])
    assert limit.allow("ip") and limit.allow("ip") and not limit.allow("ip")
    assert limit.allow("other")
    now[0] = 10
    assert limit.allow("ip")


@pytest.fixture
def site(tmp_path, monkeypatch, points_league):
    """A public site whose ESPN fetches return fixtures and record what they were given."""
    calls = []
    demo_league = League.from_dict(json.loads(app_module.DEMO_PATH.read_text()))

    def fake_fetch(settings):
        calls.append(settings)
        if settings.league_id == 404:
            raise LeagueNotFound("No such league.")
        if settings.league_id == 403 and not settings.swid:
            raise PrivateLeague("ESPN says this league is private.")
        base = points_league if settings.league_id == 2 else demo_league
        return dataclasses.replace(
            League.from_dict(base.to_dict()), league_id=settings.league_id, source="espn",
            viewer_team_id=3 if settings.swid else None,
            fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))

    monkeypatch.setattr(app_module, "fetch_league", fake_fetch)
    home = Coach(Store(tmp_path / "s.sqlite"), Settings(None, 2027, None, None, None))
    client = TestClient(create_app(home, password="", public=True,
                                   connect_limit=RateLimit(50, 600)))
    return client, calls, home


def connect(client, **body):
    return client.post("/api/connect", json=body)


def test_visitors_start_on_the_demo(site):
    client, _, _ = site
    league = client.get("/api/league").json()
    assert league["source"] == "demo" and league["public"] and not league["connected"]
    assert not league["can_refresh"]


def test_connect_a_public_league(site):
    client, calls, home = site
    response = connect(client, league="https://fantasy.espn.com/basketball/league?leagueId=7")
    assert response.status_code == 200
    token = response.json()["session"]
    assert calls[-1].league_id == 7 and calls[-1].espn_s2 is None
    league = client.get("/api/league", headers={"X-League-Session": token}).json()
    assert league["connected"] and league["can_refresh"] and league["source"] == "espn"
    assert client.get("/api/team/1", headers={"X-League-Session": token}).status_code == 200
    assert home.load().source == "demo"  # other visitors are unaffected


def test_connected_leagues_are_kept_apart(site):
    client, _, _ = site
    categories = connect(client, league="7").json()["session"]
    points = connect(client, league="2", espn_s2=S2, swid=SWID).json()["session"]
    assert client.get("/api/league", headers={"X-League-Session": categories}).json()["scoring_mode"] == "categories"
    second = client.get("/api/league", headers={"X-League-Session": points}).json()
    assert second["scoring_mode"] == "points" and second["default_team_id"] == 3


def test_private_league_errors(site):
    client, calls, _ = site
    response = connect(client, league="403")
    assert response.status_code == 403 and "private" in response.json()["detail"]
    assert connect(client, league="403", espn_s2=S2, swid=SWID).status_code == 200
    assert calls[-1].espn_s2 == S2 and calls[-1].swid == SWID
    assert connect(client, league="404").status_code == 400
    assert connect(client, league="not a league").status_code == 400
    assert connect(client, league="7", espn_s2=S2).status_code == 400


def test_cookies_are_never_echoed_or_saved(site, tmp_path):
    client, _, _ = site
    response = client.post("/api/connect", json={"league": "7", "espn_s2": S2, "swid": "x" * 200})
    assert response.status_code == 422 and S2 not in response.text and "x" * 50 not in response.text
    token = connect(client, league="7", espn_s2=S2, swid=SWID).json()["session"]
    league = client.get("/api/league", headers={"X-League-Session": token})
    assert S2 not in league.text and SWID not in league.text
    store = Store(tmp_path / "s.sqlite")
    assert store.latest(7, 2027) is None  # connected leagues stay in memory only


def test_expired_session_asks_the_browser_to_reconnect(site):
    client, _, _ = site
    assert client.get("/api/league", headers={"X-League-Session": "gone"}).status_code == 410
    token = connect(client, league="7").json()["session"]
    assert client.post("/api/disconnect", headers={"X-League-Session": token}).status_code == 200
    assert client.get("/api/league", headers={"X-League-Session": token}).status_code == 410


def test_refresh_uses_cookies_from_the_request(site, monkeypatch):
    client, calls, _ = site
    token = connect(client, league="403", espn_s2=S2, swid=SWID).json()["session"]
    headers = {"X-League-Session": token}
    # Fresh data can't be refreshed for 10 minutes.
    assert client.post("/api/refresh", headers=headers, json={"espn_s2": S2, "swid": SWID}).status_code == 429
    monkeypatch.setattr(app_module, "refresh_wait_seconds", lambda league: 0)
    assert client.post("/api/refresh", headers=headers, json={"espn_s2": S2, "swid": SWID}).status_code == 200
    assert calls[-1].swid == SWID
    assert client.post("/api/refresh", headers=headers).status_code == 403  # no cookies sent
    assert client.post("/api/refresh").status_code == 400  # the demo can't refresh


def test_connect_is_rate_limited(tmp_path, monkeypatch, demo_league):
    monkeypatch.setattr(app_module, "fetch_league", lambda settings: demo_league)
    home = Coach(Store(tmp_path / "s.sqlite"), Settings(None, 2027, None, None, None))
    client = TestClient(create_app(home, password="", public=True, connect_limit=RateLimit(2, 600)))
    assert [connect(client, league="7").status_code for _ in range(3)] == [200, 200, 429]


def test_public_site_ignores_the_hosts_league(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("ESPN_LEAGUE_ID", "123")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    client = TestClient(create_app())
    league = client.get("/api/league").json()
    assert league["source"] == "demo" and league["public"]


def test_security_headers(site):
    client, _, _ = site
    response = client.get("/")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
