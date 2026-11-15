# Trade Coach

A read-only coach for ESPN fantasy basketball points, category and roto leagues. It finds trades that could help both teams and explains the reasoning behind each one. It never proposes or submits anything on ESPN.

Without a league configured, it runs on a demo league of made-up players.

## Screens

- **My Team:** your team's strengths and weaknesses, and how your scoring works.
- **Trade Ideas:** trades that could help both teams, with an explanation and a lesson for each.
- **Offers:** pending trade offers to and from your team on ESPN, each analyzed with advice to accept, treat as a close call, or decline. Offers from the last 7 days are read with each update; expired ones are hidden. You still accept or decline on ESPN.
- **Trade Check:** any trade you enter, analyzed the same way.

## Setup

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

Open http://127.0.0.1:8765. Running locally needs no password unless you set `APP_PASSWORD`.

## Connect your ESPN league

Click **Connect your league** and enter your league ID, or paste your league page's address.

- **Public leagues:** that's all.
- **Private leagues** (most leagues): also add the `espn_s2` and `SWID` cookies from a computer signed in to ESPN. The connect window shows where to find them in Chrome, Safari and Firefox.
- **Your team** is picked automatically from your SWID when you add cookies.

### Where your cookies go

The cookies give access to your ESPN account, so the site handles them carefully:

- They stay in your browser (localStorage, or only for this tab if you untick **Remember on this device**).
- They reach the server only with a connect or update request, over HTTPS. The server uses them for that one ESPN request and never saves or logs them, and error messages never repeat them.
- Connected leagues are kept in the server's memory only, never on disk, and are dropped after 12 hours unused or when the server restarts. Your browser then reconnects on its own.
- **Disconnect** removes them from your browser and the league from the server.

### Running it on your own computer

Instead of connecting in the browser, you can set a league for your own copy:

1. Copy `.env.example` to `~/.config/fantasy-trade-coach/.env`. Use this location rather than the project folder if the folder syncs to iCloud.
2. Add your league ID and, for a private league, the two cookies.
3. Test the connection:

```bash
.venv/bin/python scripts/check_espn.py
```

This prints what it read and saves an anonymized snapshot to `tests/fixtures/espn_snapshot.json`. ESPN access uses the unofficial `espn-api` package, so it can break when ESPN changes its internal endpoints. The app keeps working from your last saved snapshot.

## Put it on the web (Render)

The hosted site is public: anyone with the link can try the demo league and connect their own. It never shows a league from the host's settings, so `ESPN_*` values set on Render are ignored.

1. Sign in at [render.com](https://render.com) with GitHub.
2. Choose **New > Blueprint**, pick this repository, and confirm. Render reads `render.yaml`.
3. Open the `onrender.com` address Render gives you.

Notes:

- To limit the site to people you choose, add `APP_PASSWORD` under **Environment**. Browsers then ask for it once; any username works.
- The free plan sleeps after about 15 minutes without visits, so the first visit afterward takes up to a minute, and connected visitors reconnect automatically.
- Each visitor can start 8 ESPN connections per 10 minutes, and each league can update from ESPN once every 10 minutes.
- Up to 25 connected leagues are kept at once; the least recently used is dropped first and reconnects when its visitor returns.
- ESPN's data access is unofficial. ESPN could change it or limit requests from hosting providers at any time.

## How it values trades

### Points leagues

- **Player value:** fantasy points per game under your league's own scoring rules, read from ESPN.
- **Team strength:** projected points per week from the players who would actually start. Each day a player has a chance of having a game (light, normal and busy NBA nights), and only your starting slots score, so deep-bench players count for less.
- **Suggestions:** trades that add at least 5 projected points a week for you and cost the other team no more than 10. They're ranked to favor deals the other manager is more likely to accept, and each shows a reason they might say yes.

### Category leagues

- **Player value:** z-scores against all rostered players, with turnovers inverted and FG%/FT% weighted by attempts.
- **Expected category wins:** the estimated number of categories you'd win against an average opponent. Gains in close categories count for more than gains in ones you already win or have given up on.
- **Roster effects:** going over the roster limit drops your lowest-valued player who isn't arriving in the trade. An open spot is filled by a stand-in based on the league's lowest-valued rostered players.
- **Limits:** the model ignores schedules, games played per week, and rest-of-season projections. Treat its results as a starting point for your own judgment.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The demo league comes from `scripts/make_demo_fixture.py`.
