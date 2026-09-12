#!/usr/bin/env python3
"""
Pulls final SEC football scores from CollegeFootballData.com and writes them
into index.html's embedded SEASON data, in place.

Runs unattended inside a GitHub Actions workflow (see
.github/workflows/update-scores.yml) — this is not something you run by hand
week to week; the whole point is that you don't have to.

Requires one environment variable:
  CFBD_API_KEY  — a free key from https://collegefootballdata.com/key
                  (stored as a GitHub Actions secret, never in this file)

Exits 0 whether or not anything changed. Prints "CHANGES_MADE" as the last
line of output if index.html was modified, so the workflow step that follows
this one can decide whether there's anything worth committing.
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error

INDEX_HTML = "index.html"
SEASON_YEAR = 2026

# Your schedule uses a few shorthand team names that don't match CFBD's
# official naming. Add to this if a game silently fails to match — that's
# almost always a naming mismatch, not a real problem with the approach.
NAME_TO_CFBD = {
    "Miss State": "Mississippi State",
}
CFBD_TO_NAME = {v: k for k, v in NAME_TO_CFBD.items()}

SEC_TEAMS = [
    "Alabama", "Arkansas", "Auburn", "Florida", "Georgia", "Kentucky", "LSU",
    "Miss State", "Missouri", "Oklahoma", "Ole Miss", "South Carolina",
    "Tennessee", "Texas", "Texas A&M", "Vanderbilt",
]


def cfbd_name(name):
    return NAME_TO_CFBD.get(name, name)


def our_name(cfbd_team_name):
    return CFBD_TO_NAME.get(cfbd_team_name, cfbd_team_name)


def fetch_games_for_team(team, api_key):
    """One CFBD call per SEC team, covering that team's whole season."""
    url = (
        "https://api.collegefootballdata.com/games"
        f"?year={SEASON_YEAR}&team={urllib.parse.quote(cfbd_name(team))}"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  ! CFBD request failed for {team}: HTTP {e.code}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ! CFBD request failed for {team}: {e}", file=sys.stderr)
        return []


def load_season(html):
    m = re.search(r"const SEASON = (\{.*?\});\n", html, re.S)
    if not m:
        raise RuntimeError("Could not find 'const SEASON = {...};' in index.html")
    return json.loads(m.group(1)), m


def save_season(html, season, match):
    new_block = "const SEASON = " + json.dumps(season, separators=(",", ":")) + ";\n"
    return html[: match.start()] + new_block + html[match.end():]


def main():
    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        print("CFBD_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    season, match = load_season(html)
    games = season["games"]

    undecided = [(i, g) for i, g in enumerate(games) if not g.get("winner")]
    if not undecided:
        print("No undecided games in the schedule. Nothing to check.")
        return

    print(f"{len(undecided)} undecided game(s) in the schedule. Checking CFBD...")

    # Pull each SEC team's full-season game list once, build one lookup keyed
    # by the pair of teams playing (not the date). Matching on date was the
    # original approach, but CFBD reports kickoff time in UTC, and a normal
    # evening kickoff in the US rolls over into the next UTC calendar day \u2014
    # that silently broke the match for almost every game. Each SEC team only
    # plays every other team once a season, so the team pair alone is a safe,
    # unambiguous key on its own.
    cfbd_by_key = {}
    sample_logged = False
    for team in SEC_TEAMS:
        team_games = fetch_games_for_team(team, api_key)
        if not sample_logged and team_games:
            g0 = team_games[0]
            print(f"  (sample CFBD game for {team}: {g0.get('awayTeam')} @ "
                  f"{g0.get('homeTeam')}, completed={g0.get('completed')}, "
                  f"date={g0.get('startDate')})", file=sys.stderr)
            sample_logged = True
        for g in team_games:
            if not g.get("completed"):
                continue
            home = our_name(g["homeTeam"])
            away = our_name(g["awayTeam"])
            key = frozenset([home, away])
            cfbd_by_key[key] = g

    changed = False
    for i, g in undecided:
        key = frozenset([g["a"], g["b"]])
        match_game = cfbd_by_key.get(key)
        if not match_game:
            continue  # not final yet, or a genuine mismatch to look into

        home_pts = match_game.get("homePoints")
        away_pts = match_game.get("awayPoints")
        if home_pts is None or away_pts is None:
            continue

        home = our_name(match_game["homeTeam"])
        away = our_name(match_game["awayTeam"])
        winner = home if home_pts > away_pts else away
        hi, lo = max(home_pts, away_pts), min(home_pts, away_pts)

        games[i]["winner"] = winner
        games[i]["score"] = f"{hi}-{lo}"
        changed = True
        print(f"  \u2713 {g['a']} vs {g['b']} ({g['when']}) -> {winner} ({hi}-{lo})")

    if not changed:
        print("Checked CFBD, but nothing new is final yet.")
        return

    html = save_season(html, season, match)
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print("index.html updated.")
    print("CHANGES_MADE")


if __name__ == "__main__":
    import urllib.parse
    main()
