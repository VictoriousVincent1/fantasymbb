# Trade Coach

A local, read-only coach for ESPN fantasy basketball points, category and roto leagues. It finds trades that could help both teams and explains the reasoning behind each one. It never proposes or submits anything on ESPN.

Without a league configured, it runs on a demo league of made-up players.

## Setup

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

Open http://127.0.0.1:8765. Running locally needs no password unless you set `APP_PASSWORD`.

## Connect your ESPN league

1. Copy `.env.example` to `~/.config/fantasy-trade-coach/.env`. Use this location rather than the project folder if the folder syncs to iCloud, because the cookies give access to your ESPN account.
2. Add your league ID, which is in your league page's URL as `leagueId=`.
3. For a private league, add the `espn_s2` and `SWID` cookies from a browser signed in to ESPN (DevTools > Application > Cookies).
4. Test the connection:

```bash
.venv/bin/python scripts/check_espn.py
```

This prints what it read and saves an anonymized snapshot to `tests/fixtures/espn_snapshot.json`. ESPN access uses the unofficial `espn-api` package, so it can break when ESPN changes its internal endpoints. The app keeps working from your last saved snapshot.

## Put it on the web (Render)

The site is locked behind a password. Your browser asks for it once; any username works. On Render it refuses to show anything until `APP_PASSWORD` is set.

1. Sign in at [render.com](https://render.com) with GitHub.
2. Choose **New > Blueprint**, pick this repository, and confirm. Render reads `render.yaml`.
3. When asked, fill in `APP_PASSWORD`, `ESPN_LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID` and optionally `ESPN_TEAM_ID`. These stay in Render's settings, not in the repository.
4. Open the `onrender.com` address Render gives you.

Notes:

- The free plan sleeps after about 15 minutes without visits, so the first visit afterward takes up to a minute.
- Saved snapshots don't survive a restart on the free plan, so the site fetches fresh data from ESPN when it wakes up.
- Anyone with the password can see your league's rosters. Share it only with people you'd show that to.
- If your ESPN cookies expire, update `ESPN_S2` and `ESPN_SWID` under **Environment** in Render.

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
