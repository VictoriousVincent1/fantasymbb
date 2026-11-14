"""Generates the synthetic demo league. All players and teams are made up."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "demo_league.json"
FIRST = ["Aiden", "Bram", "Cato", "Dario", "Emeka", "Finn", "Goran", "Hale", "Ivo", "Jonah", "Kofi",
         "Luca", "Mateo", "Niko", "Omar", "Pax", "Quinn", "Rafe", "Soren", "Theo", "Uri", "Vito",
         "Wes", "Xan", "Yuri", "Zane"]
LAST = ["Ashgrove", "Brightwater", "Calloway", "Dunmore", "Eastlake", "Fairbourne", "Glenhaven",
        "Harrowgate", "Ironwood", "Kestrel", "Larkspur", "Merrow", "Northcote", "Oakhurst",
        "Pinecrest", "Quillfeather", "Ravenscar", "Stonebridge", "Thornfield", "Underhill",
        "Vanterpool", "Westmere", "Yarrow", "Zephyrine"]
TEAMS = ["Harbor Hawks", "Copper Comets", "Lakeside Lynx", "Summit Sharks", "Riverbend Rams",
         "Foundry Foxes", "Prairie Pumas", "Glacier Geese", "Canyon Crows", "Mesa Moths",
         "Delta Drakes", "Orchard Otters"]
PRO = ["ALB", "BAY", "COL", "DUN", "ELM", "FOX", "GRV", "HIL"]

# position: (low, high) per stat at quality 0 and 1.
ARCHETYPES = {
    "PG": dict(PTS=(8, 28), REB=(2, 6), AST=(4, 11), STL=(.6, 1.8), BLK=(.1, .5), threes=(1, 3.6),
               TO=(1.3, 3.8), FGA=(7, 20), fgp=(.41, .48), FTA=(1.2, 7), ftp=(.76, .92)),
    "SG": dict(PTS=(8, 27), REB=(2.2, 6), AST=(1.5, 6), STL=(.6, 1.6), BLK=(.1, .6), threes=(1.2, 3.8),
               TO=(.9, 2.8), FGA=(7, 20), fgp=(.41, .48), FTA=(1, 6), ftp=(.76, .91)),
    "SF": dict(PTS=(7, 26), REB=(3, 8), AST=(1.2, 5.5), STL=(.6, 1.7), BLK=(.2, 1), threes=(.9, 3),
               TO=(.8, 2.8), FGA=(6, 19), fgp=(.43, .51), FTA=(1, 6), ftp=(.72, .87)),
    "PF": dict(PTS=(7, 25), REB=(4.5, 10), AST=(1, 5), STL=(.4, 1.2), BLK=(.4, 1.5), threes=(.4, 2.2),
               TO=(.8, 2.8), FGA=(5, 18), fgp=(.47, .56), FTA=(1, 7), ftp=(.66, .84)),
    "C": dict(PTS=(7, 26), REB=(6, 13.5), AST=(.8, 6), STL=(.4, 1.2), BLK=(.7, 2.8), threes=(.1, 1.6),
              TO=(.8, 3.2), FGA=(5, 17), fgp=(.52, .64), FTA=(1.2, 8), ftp=(.55, .8)),
}
SLOTS = {"PG": ["PG", "G", "UT", "BE", "IR"], "SG": ["SG", "G", "SG/SF", "G/F", "UT", "BE", "IR"],
         "SF": ["SF", "F", "SG/SF", "G/F", "F/C", "UT", "BE", "IR"],
         "PF": ["PF", "F", "PF/C", "F/C", "UT", "BE", "IR"], "C": ["C", "PF/C", "F/C", "UT", "BE", "IR"]}


def statline(rng, position, quality, games):
    a = ARCHETYPES[position]
    def draw(key):
        low, high = a[key]
        return max(0.0, (low + (high - low) * quality) * rng.uniform(.82, 1.18))
    fga, fta = draw("FGA"), draw("FTA")
    fgp = min(.70, rng.uniform(*a["fgp"]))
    ftp = min(.95, rng.uniform(*a["ftp"]))
    fgm, ftm = fga * fgp, fta * ftp
    threes = draw("threes")
    line = dict(PTS=2 * fgm + ftm + threes, REB=draw("REB"), AST=draw("AST"), STL=draw("STL"),
                BLK=draw("BLK"), TO=draw("TO"), FGM=fgm, FGA=fga, FTM=ftm, FTA=fta, GP=float(games))
    line["3PM"] = threes
    line["FG%"] = fgp
    line["FT%"] = ftp
    return {k: round(v, 3) for k, v in line.items()}


def scale(rng, line, factor, games):
    out = dict(line)
    for key in ("PTS", "REB", "AST", "STL", "BLK", "3PM", "FGM", "FGA", "FTM", "FTA"):
        out[key] = round(line[key] * factor * rng.uniform(.95, 1.05), 3)
    out["GP"] = float(games)
    return out


def main():
    rng = random.Random(20261107)
    names = [f"{a} {b}" for a in FIRST for b in LAST]
    rng.shuffle(names)
    per_team = 13
    positions = (["PG", "SG", "SF", "PF", "C"] * (12 * per_team))[: 12 * per_team]
    rng.shuffle(positions)

    players = []
    for index, position in enumerate(positions):
        quality = (1 - index / len(positions)) ** 1.5
        games = rng.randint(6, 12)
        season = statline(rng, position, quality, games)
        factor = rng.choice([1, 1, 1, 1, 1, 1.25, .78])
        extra = []
        if position in ("PG", "SG") and rng.random() < .3:
            extra = ["SG" if position == "PG" else "PG"]
        if position in ("SF", "PF") and rng.random() < .3:
            extra = ["PF" if position == "SF" else "SF"]
        players.append({
            "id": 1000 + index, "name": names[index], "pro_team": rng.choice(PRO), "position": position,
            "eligible_slots": sorted(set(SLOTS[position] + extra)), "lineup_slot": "BE",
            "injury_status": "ACTIVE",
            "stats": {"season": season, "last_30": scale(rng, season, factor, games)},
        })

    # Weighted draft so teams have different strengths, which gives trades a reason to exist.
    teams = [{"id": i + 1, "name": n, "abbrev": n[:4].upper(), "wins": 0, "losses": 0, "ties": 0, "players": []}
             for i, n in enumerate(TEAMS)]
    tilts = [rng.choice(["PG", "SG", "SF", "PF", "C"]) for _ in teams]
    remaining = list(players)
    for round_index in range(per_team):
        order = teams if round_index % 2 == 0 else list(reversed(teams))
        for team in order:
            tilt = tilts[team["id"] - 1]
            window = remaining[:6]
            pick = next((p for p in window if p["position"] == tilt and rng.random() < .6), window[0])
            remaining.remove(pick)
            team["players"].append(pick)

    for team in teams:
        team["wins"] = rng.randint(1, 3)
        team["losses"] = 4 - team["wins"]
    teams[2]["players"][4]["injury_status"] = "OUT"
    teams[5]["players"][7]["injury_status"] = "DAY_TO_DAY"
    teams[8]["players"][-1]["lineup_slot"] = "IR"
    teams[8]["players"][-1]["injury_status"] = "OUT"
    del teams[10]["players"][5]["stats"]["last_30"]

    def move(player, frm, to):
        return {"player_id": player["id"], "from_team": frm, "to_team": to}

    t1, t3, t5 = teams[0]["players"], teams[2]["players"], teams[4]["players"]
    offers = [
        {"id": "demo-offer-1", "proposer_id": 3, "proposed_ms": 1794500000000,
         "expires_ms": 4102444800000, "includes_budget": False,
         "moves": [move(t3[1], 3, 1), move(t1[0], 1, 3)]},
        {"id": "demo-offer-2", "proposer_id": 5, "proposed_ms": 1794400000000,
         "expires_ms": 4102444800000, "includes_budget": False,
         "moves": [move(t5[3], 5, 1), move(t5[9], 5, 1), move(t1[2], 1, 5)]},
        {"id": "demo-offer-3", "proposer_id": 1, "proposed_ms": 1794300000000,
         "expires_ms": 4102444800000, "includes_budget": False,
         "moves": [move(t1[6], 1, 5), move(t5[6], 5, 1)]},
    ]

    league = {
        "league_id": 0, "year": 2027, "name": "Demo League (made-up players)", "scoring_type": "H2H_CATEGORY",
        "categories": [{"name": n, "reverse": n == "TO"} for n in
                       ["FG%", "FT%", "3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"]],
        "lineup_slots": {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "G": 1, "F": 1, "UT": 3, "BE": 3, "IR": 1},
        "roster_limit": 13, "trade_deadline_ms": 0, "teams": teams,
        "fetched_at": "2026-11-07T12:00:00+00:00", "source": "demo",
        "note": "Made-up players and teams. Add your ESPN league details to use your own league.",
        "offers": offers,
    }
    OUT.write_text(json.dumps(league, indent=1))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
