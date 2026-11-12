"""Step 1 check: can we read your league? Saves an anonymized fixture. Reads only."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_settings
from espn_client import EspnError, fetch_league

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "espn_snapshot.json"


def anonymize(data: dict) -> dict:
    """Removes team names and the league's identity. NBA player names are public."""
    data = dict(data, league_id=0, name="Anonymized league")
    data["teams"] = [
        dict(team, name=f"Team {i}", abbrev=f"T{i}") for i, team in enumerate(data["teams"], 1)
    ]
    return data


def main() -> int:
    settings = load_settings()
    try:
        league = fetch_league(settings)
    except EspnError as error:
        print(f"Could not read the league: {error}")
        return 1

    print(f"League: {league.name} ({league.year}), scoring: {league.scoring_type}")
    if league.scoring_mode == "points":
        print("Points per stat:", ", ".join(f"{k} {v:+g}" for k, v in league.points_per_stat.items()))
    else:
        print("Categories:", ", ".join(("-" if c.reverse else "") + c.name for c in league.categories))
    print("Lineup:", league.lineup_slots, "| roster limit:", league.roster_limit)
    for team in league.teams:
        with_stats = sum(bool(p.stats.get("season")) for p in team.players)
        recent = sum(bool(p.stats.get("last_30")) for p in team.players)
        print(f"  {team.id:>3} {team.name}: {len(team.players)} players, "
              f"{with_stats} with season stats, {recent} with last-30 stats")
    OUT.write_text(json.dumps(anonymize(league.to_dict()), indent=1))
    print(f"Saved anonymized snapshot to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
