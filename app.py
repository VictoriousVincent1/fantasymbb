"""Web app for the trade coach. Read-only: it never changes anything on ESPN.

Visitors can connect their own league. Their ESPN cookies stay in their browser and arrive
only with a connect or update request; the server uses them for that one ESPN request and
never stores or logs them. Connected leagues live in memory only, never on disk.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import replace
from typing import Callable, Deque, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import ROOT, Settings, default_season_year, load_settings
from connect import BadInput, clean_cookies, parse_league_id
from espn_client import EspnError, PrivateLeague, fetch_league
from explain import explain_trade, team_report
from points_explain import explain_points_trade, points_team_report
from points_model import PointsModel, search_points_trades
from models import League, WINDOWS
from offers import offers_for_team
from store import Store, refresh_wait_seconds
from team_model import LeagueModel
from trade_search import diversify, search_trades

DEMO_PATH = ROOT / "tests" / "fixtures" / "demo_league.json"
SESSION_HEADER = "x-league-session"
MAX_SESSIONS = 25           # connected leagues kept in memory; the oldest is dropped first
SESSION_TTL_SECONDS = 12 * 3600
CONNECTS_PER_WINDOW = 8     # ESPN fetches one visitor can start in CONNECT_WINDOW_SECONDS
CONNECT_WINDOW_SECONDS = 600
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


class Cookies(BaseModel):
    espn_s2: Optional[str] = Field(None, max_length=2000)
    swid: Optional[str] = Field(None, max_length=100)


class ConnectRequest(Cookies):
    league: str = Field(..., max_length=500)  # a league ID or an ESPN league page URL
    year: Optional[int] = Field(None, ge=2010, le=2100)


class TradeCheck(BaseModel):
    team_id: int
    opponent_id: int
    give: List[int]
    get: List[int]
    window: str = "season"


class Coach:
    """Holds the current league and caches the slower calculations."""

    def __init__(self, store: Optional[Store] = None, settings=None,
                 league: Optional[League] = None, connected: bool = False):
        self.settings = settings or load_settings()
        # A visitor's connected league is kept in memory only, never saved to disk.
        self.connected = connected
        self.store = None if connected else (store or Store(ROOT / "data" / "coach.sqlite"))
        self.league: Optional[League] = league
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

    def fetch(self, cookies: Optional[Tuple[Optional[str], Optional[str]]] = None) -> League:
        settings = self.settings
        if cookies is not None:
            settings = replace(settings, espn_s2=cookies[0], swid=cookies[1])
        league = fetch_league(settings)
        if self.store:
            self.store.save(league)
        self.warning = None
        self.set_league(league)
        return league

    @property
    def can_refresh(self) -> bool:
        return self.connected or self.settings.has_league

    @property
    def default_team_id(self) -> Optional[int]:
        league = self.load()
        return self.settings.team_id or league.viewer_team_id

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


class Sessions:
    """Connected leagues by random token, in memory. Lost on restart; the browser reconnects."""

    def __init__(self, limit: int = MAX_SESSIONS, ttl: float = SESSION_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.limit, self.ttl, self.clock = limit, ttl, clock
        self.items: "OrderedDict[str, Tuple[float, Coach]]" = OrderedDict()
        self.lock = threading.Lock()

    def add(self, coach: Coach) -> str:
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.items[token] = (self.clock(), coach)
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        return token

    def get(self, token: str) -> Optional[Coach]:
        with self.lock:
            entry = self.items.get(token)
            if entry is None:
                return None
            if self.clock() - entry[0] > self.ttl:
                del self.items[token]
                return None
            self.items[token] = (self.clock(), entry[1])
            self.items.move_to_end(token)
            return entry[1]

    def remove(self, token: str) -> None:
        with self.lock:
            self.items.pop(token, None)


class RateLimit:
    def __init__(self, count: int, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.count, self.seconds, self.clock = count, seconds, clock
        self.hits: Dict[str, Deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self.lock:
            if len(self.hits) > 10000:  # forget idle visitors
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.seconds}
            hits = self.hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.seconds:
                hits.popleft()
            if len(hits) >= self.count:
                return False
            hits.append(now)
            return True


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def is_public() -> bool:
    """A hosted site for anyone. Render sets RENDER; elsewhere set PUBLIC_SITE=1."""
    return bool(os.environ.get("RENDER") or os.environ.get("PUBLIC_SITE"))


def password_from_header(header: str) -> Optional[str]:
    """The password from an HTTP Basic auth header. Any username is accepted."""
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return decoded.split(":", 1)[1] if ":" in decoded else None


def create_app(coach: Optional[Coach] = None, password: Optional[str] = None,
               public: Optional[bool] = None, sessions: Optional[Sessions] = None,
               connect_limit: Optional[RateLimit] = None) -> FastAPI:
    public = is_public() if public is None else public
    if coach is None:
        # A public site never shows the host's own league: everyone starts on the demo.
        coach = Coach(settings=Settings(None, default_season_year(), None, None, None)) if public else Coach()
    home = coach
    password = password if password is not None else os.environ.get("APP_PASSWORD") or None
    sessions = sessions or Sessions()
    connect_limit = connect_limit or RateLimit(CONNECTS_PER_WINDOW, CONNECT_WINDOW_SECONDS)
    app = FastAPI(title="Trade Coach", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # FastAPI's default reply repeats the input, which could include cookies.
        fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
        return JSONResponse({"detail": f"Check these fields: {', '.join(fields)}."}, status_code=422)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.middleware("http")
    async def check_password(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        if password:
            given = password_from_header(request.headers.get("authorization", ""))
            if given is None or not secrets.compare_digest(given.encode(), password.encode()):
                return PlainTextResponse(
                    "Password required.", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Trade Coach", charset="UTF-8"'},
                )
        return await call_next(request)

    @app.get("/healthz")
    def health():
        return {"ok": True}

    def current(request: Request) -> Coach:
        token = request.headers.get(SESSION_HEADER)
        if not token:
            return home
        found = sessions.get(token)
        if found is None:
            # The browser still has the visitor's details and reconnects on its own.
            raise HTTPException(410, "Your league connection expired. Reconnecting...")
        return found

    def require_team(coach: Coach, team_id: int) -> None:
        if team_id not in {t.id for t in coach.load().teams}:
            raise HTTPException(404, f"No team with ID {team_id} in this league.")

    def espn_failure(error: EspnError) -> HTTPException:
        return HTTPException(403 if isinstance(error, PrivateLeague) else 400, str(error))

    @app.post("/api/connect")
    def connect(body: ConnectRequest, request: Request):
        try:
            league_id = parse_league_id(body.league)
            cookies = clean_cookies(body.espn_s2, body.swid)
        except BadInput as error:
            raise HTTPException(400, str(error))
        if not connect_limit.allow(client_ip(request)):
            raise HTTPException(429, "Too many connection attempts. Wait a few minutes and try again.")
        settings = Settings(league_id, body.year or default_season_year(), None, None, None)
        coach = Coach(settings=settings, connected=True)
        try:
            coach.fetch(cookies)
        except EspnError as error:
            raise espn_failure(error)
        return {"session": sessions.add(coach)}

    @app.post("/api/disconnect")
    def disconnect(request: Request):
        token = request.headers.get(SESSION_HEADER)
        if token:
            sessions.remove(token)
        return {"ok": True}

    @app.get("/api/league")
    def league_info(coach: Coach = Depends(current)):
        league = coach.load()
        return {
            "name": league.name,
            "year": league.year,
            "source": league.source,
            "scoring_mode": league.scoring_mode,
            "note": league.note,
            "warning": coach.warning,
            "fetched_at": league.fetched_at,
            "default_team_id": coach.default_team_id,
            "can_refresh": coach.can_refresh,
            "connected": coach.connected,
            "public": public,
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
    def team(team_id: int, window: str = "season", coach: Coach = Depends(current)):
        model = coach.model(window)
        require_team(coach, team_id)
        return points_team_report(model, team_id) if coach.points else team_report(model, team_id)

    @app.get("/api/trades")
    def trades(team_id: int, window: str = "season", opponent_id: Optional[int] = None,
               need: Optional[str] = None, limit: int = Query(20, ge=1, le=50),
               coach: Coach = Depends(current)):
        model = coach.model(window)
        require_team(coach, team_id)
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

    @app.get("/api/offers")
    def offers(team_id: int, window: str = "season", coach: Coach = Depends(current)):
        model = coach.model(window)
        require_team(coach, team_id)
        return offers_for_team(coach.load(), team_id, model, coach.explain)

    @app.post("/api/trade-check")
    def trade_check(body: TradeCheck, coach: Coach = Depends(current)):
        model = coach.model(body.window)
        require_team(coach, body.team_id)
        require_team(coach, body.opponent_id)
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
    def refresh(body: Optional[Cookies] = None, coach: Coach = Depends(current)):
        if not coach.can_refresh:
            raise HTTPException(400, "This is the demo league. Connect your ESPN league to update it.")
        league = coach.load()
        wait = refresh_wait_seconds(league if league.source == "espn" else None)
        if wait:
            raise HTTPException(429, f"Last updated recently. Try again in {wait // 60 + 1} min.")
        cookies = None
        if coach.connected:
            try:
                cookies = clean_cookies(body.espn_s2 if body else None, body.swid if body else None)
            except BadInput as error:
                raise HTTPException(400, str(error))
        try:
            coach.fetch(cookies)
        except EspnError as error:
            failure = espn_failure(error)
            raise HTTPException(failure.status_code, f"{error} Still showing your last data.")
        return {"fetched_at": coach.league.fetched_at}

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()
