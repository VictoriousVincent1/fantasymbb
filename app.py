"""Local web app for the trade coach. Read-only: it never changes anything on ESPN."""
from __future__ import annotations

import json
import threading
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import ROOT, load_settings
from espn_client import EspnError, fetch_league
from explain import explain_trade, team_report
from points_explain import explain_points_trade, points_team_report
from points_model import PointsModel, search_points_trades
from models import League, WINDOWS
from store import Store, refresh_wait_seconds
from team_model import LeagueModel
from trade_search import diversify, search_trades

DEMO_PATH = ROOT / "tests" / "fixtures" / "demo_league.json"


class TradeCheck(BaseModel):
    team_id: int
    opponent_id: int
    give: List[int]
    get: List[int]
    window: str = "season"


class Coach:
    """Holds the current league and caches the slower calculations."""

    def __init__(self, store: Optional[Store] = None, settings=None):
        self.settings = settings or load_settings()
        self.store = store or Store(ROOT / "data" / "coach.sqlite")
        self.league: Optional[League] = None
        self.warning: Optional[str] = None
        self.models: Dict[str, object] = {}
        self.searches: Dict[Tuple[int, str], list] = {}
        self.search_lock = threading.Lock()

    def set_league(self, league: League) -> None:
        self.league = league
        self.models, self.searches = {}, {}

    def load(self) -> League:
        if self.league:
            return self.league
        if not self.settings.has_league:
            self.set_league(League.from_dict(json.loads(DEMO_PATH.read_text())))
            return self.league
        saved = self.store.latest(self.settings.league_id, self.settings.year)
        if saved:
            self.set_league(saved)
            return self.league
        try:
            self.fetch()
        except EspnError as error:
            self.warning = f"{error} Showing the demo league instead."
            self.set_league(League.from_dict(json.loads(DEMO_PATH.read_text())))
        return self.league

    def fetch(self) -> League:
        league = fetch_league(self.settings)
        self.store.save(league)
        self.warning = None
        self.set_league(league)
        return league

    @property
    def points(self) -> bool:
        return self.load().scoring_mode == "points"

    def model(self, window: str):
        if window not in WINDOWS:
            raise HTTPException(400, f"Unknown stats window: {window}")
        league = self.load()
        if window not in self.models:
            model_class = PointsModel if league.scoring_mode == "points" else LeagueModel
            self.models[window] = model_class(league, window)
        return self.models[window]

    def explain(self, model, result) -> dict:
        return explain_points_trade(model, result) if self.points else explain_trade(model, result)

    def trades(self, team_id: int, window: str) -> list:
        key = (team_id, window)
        # One search at a time, so quick filter changes reuse a result instead of repeating it.
        with self.search_lock:
            if key not in self.searches:
                search = search_points_trades if self.points else search_trades
                self.searches[key] = search(self.model(window), team_id)
            return self.searches[key]


def create_app(coach: Optional[Coach] = None) -> FastAPI:
    coach = coach or Coach()
    app = FastAPI(title="Trade Coach")

    def require_team(team_id: int) -> None:
        if team_id not in {t.id for t in coach.load().teams}:
            raise HTTPException(404, f"No team with ID {team_id} in this league.")

    @app.get("/api/league")
    def league_info():
        league = coach.load()
        return {
            "name": league.name,
            "year": league.year,
            "source": league.source,
            "scoring_mode": league.scoring_mode,
            "note": league.note,
            "warning": coach.warning,
            "fetched_at": league.fetched_at,
            "default_team_id": coach.settings.team_id,
            "can_refresh": coach.settings.has_league,
            "trade_deadline_ms": league.trade_deadline_ms,
            "categories": [c.name for c in league.categories],
            "teams": [
                {
                    "id": t.id,
                    "name": t.name,
                    "players": [
                        {"id": p.id, "name": p.name, "position": p.position, "pro_team": p.pro_team}
                        for p in t.players
                    ],
                }
                for t in league.teams
            ],
        }

    @app.get("/api/team/{team_id}")
    def team(team_id: int, window: str = "season"):
        model = coach.model(window)
        require_team(team_id)
        return points_team_report(model, team_id) if coach.points else team_report(model, team_id)

    @app.get("/api/trades")
    def trades(team_id: int, window: str = "season", opponent_id: Optional[int] = None,
               need: Optional[str] = None, limit: int = Query(20, ge=1, le=50)):
        model = coach.model(window)
        require_team(team_id)
        found = coach.trades(team_id, window)
        total = len(found)
        if opponent_id is not None:
            found = [r for r in found if r.opponent_id == opponent_id]
        if need and coach.points:
            raise HTTPException(400, "Category filters only apply to category leagues.")
        if need:
            category = next((c for c in model.categories if c.name == need), None)
            if category is None:
                raise HTTPException(400, f"Unknown category: {need}")
            sign = -1 if category.reverse else 1
            found = [r for r in found if sign * (r.my_after[need] - r.my_before[need]) > 0]
        found = diversify(found)
        return {
            "total_found": total,
            "matching": len(found),
            "trades": [coach.explain(model, r) for r in found[:limit]],
        }

    @app.post("/api/trade-check")
    def trade_check(body: TradeCheck):
        model = coach.model(body.window)
        require_team(body.team_id)
        require_team(body.opponent_id)
        if body.team_id == body.opponent_id:
            raise HTTPException(400, "Choose two different teams.")
        if not body.give or not body.get:
            raise HTTPException(400, "Choose at least one player on each side.")
        league = coach.load()
        mine = {p.id: p for p in league.team(body.team_id).players}
        theirs = {p.id: p for p in league.team(body.opponent_id).players}
        if not set(body.give) <= set(mine):
            raise HTTPException(400, "A player you're giving isn't on your team.")
        if not set(body.get) <= set(theirs):
            raise HTTPException(400, "A player you're getting isn't on that team.")
        result = model.evaluate(
            body.team_id, body.opponent_id,
            [mine[i] for i in body.give], [theirs[i] for i in body.get],
        )
        return coach.explain(model, result)

    @app.post("/api/refresh")
    def refresh():
        if not coach.settings.has_league:
            raise HTTPException(400, "No ESPN league is configured, so the app is showing demo data.")
        league = coach.load()
        wait = refresh_wait_seconds(league if league.source == "espn" else None)
        if wait:
            raise HTTPException(429, f"Last updated recently. Try again in {wait // 60 + 1} min.")
        try:
            coach.fetch()
        except EspnError as error:
            raise HTTPException(502, f"{error} Still showing your last saved data.")
        return {"fetched_at": coach.league.fetched_at}

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()
