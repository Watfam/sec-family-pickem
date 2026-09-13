#!/usr/bin/env python3
"""
Pulls SEC football data from CollegeFootballData.com and writes it into
index.html's embedded SEASON data, in place: final scores, plus kickoff
time, venue, TV network, betting line, and excitement index for every game
(not just decided ones, so upcoming games get real detail too).

Runs unattended inside a GitHub Actions workflow (see
.github/workflows/update-scores.yml) \u2014 this is not something you run by
hand week to week; the whole point is that you don't have to.

Requires one environment variable:
  CFBD_API_KEY  \u2014 a free key from https://collegefootballdata.com/key
                  (stored as a GitHub Actions secret, never in this file)

Exits 0 whether or not anything changed. Prints "CHANGES_MADE" as the last
line of output if index.html was modified, so the workflow step that follows
this one can decide whether there's anything worth committing.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error

INDEX_HTML = "index.html"
SEASON_YEAR = 2026

# Your schedule uses a few shorthand team names that don't match CFBD's
# official naming. Add to this if a game silently fails to match \u2014 that's
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

# Preference order for whose betting line to show when a game has several
# (CFBD aggregates multiple sportsbooks) \u2014 falls back to whichever the API
# lists first if none of these are present.
PREFERRED_LINE_PROVIDERS = ["consensus", "DraftKings", "Bovada", "ESPN Bet"]


def cfbd_name(name):
    return NAME_TO_CFBD.get(name, name)


def our_name(cfbd_team_name):
    return CFBD_TO_NAME.get(cfbd_team_name, cfbd_team_name)


def cfbd_get(path, params, api_key):
    url = f"https://api.collegefootballdata.com/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  ! CFBD request failed for {path} ({params}): HTTP {e.code}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ! CFBD request failed for {path} ({params}): {e}", file=sys.stderr)
        return []


def fetch_games_for_team(team, api_key):
    return cfbd_get("games", {"year": SEASON_YEAR, "team": cfbd_name(team)}, api_key)


def fetch_media_for_team(team, api_key):
    return cfbd_get("games/media", {"year": SEASON_YEAR, "team": cfbd_name(team)}, api_key)


def fetch_lines_for_team(team, api_key):
    return cfbd_get("lines", {"year": SEASON_YEAR, "team": cfbd_name(team)}, api_key)


def pick_line(lines_list):
    """A GameLines entry holds one row per sportsbook in its own 'lines'
    array. Prefer a consensus/major-book line; fall back to the first one
    present rather than showing nothing."""
    if not lines_list:
        return None
    by_provider = {l.get("provider"): l for l in lines_list}
    for name in PREFERRED_LINE_PROVIDERS:
        if name in by_provider:
            return by_provider[name]
    return lines_list[0]


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
    by_key = {}
    for i, g in enumerate(games):
        by_key[frozenset([g["a"], g["b"]])] = i

    changed = False
    sample_logged = {"games": False, "media": False, "lines": False}

    # ---- scores, kickoff time, venue, excitement (one endpoint) ----
    games_by_key = {}
    for team in SEC_TEAMS:
        team_games = fetch_games_for_team(team, api_key)
        if not sample_logged["games"] and team_games:
            g0 = team_games[0]
            print(f"  (sample CFBD game for {team}: {g0.get('awayTeam')} @ "
                  f"{g0.get('homeTeam')}, completed={g0.get('completed')}, "
                  f"date={g0.get('startDate')})", file=sys.stderr)
            sample_logged["games"] = True
        for g in team_games:
            key = frozenset([our_name(g["homeTeam"]), our_name(g["awayTeam"])])
            games_by_key[key] = g

    for key, i in by_key.items():
        cg = games_by_key.get(key)
        if not cg:
            continue
        g = games[i]
        home = our_name(cg["homeTeam"])
        away = our_name(cg["awayTeam"])

        if cg.get("startDate") and g.get("kickoff") != cg["startDate"]:
            g["kickoff"] = cg["startDate"]
            g["kickoffTBD"] = bool(cg.get("startTimeTBD"))
            changed = True
        if cg.get("venue") and g.get("venue") != cg["venue"]:
            g["venue"] = cg["venue"]
            changed = True
        if cg.get("neutralSite"):
            g["neutralSite"] = True
        if cg.get("excitementIndex") is not None:
            rounded = round(cg["excitementIndex"], 1)
            if g.get("excitement") != rounded:
                g["excitement"] = rounded
                changed = True
        if cg.get("attendance"):
            g["attendance"] = cg["attendance"]

        if not g.get("winner") and cg.get("completed"):
            home_pts, away_pts = cg.get("homePoints"), cg.get("awayPoints")
            if home_pts is not None and away_pts is not None:
                winner = home if home_pts > away_pts else away
                hi, lo = max(home_pts, away_pts), min(home_pts, away_pts)
                g["winner"] = winner
                g["score"] = f"{hi}-{lo}"
                changed = True
                print(f"  \u2713 score: {g['a']} vs {g['b']} ({g['when']}) -> {winner} ({hi}-{lo})")

    # ---- TV network (separate endpoint) ----
    media_by_key = {}
    for team in SEC_TEAMS:
        team_media = fetch_media_for_team(team, api_key)
        if not sample_logged["media"] and team_media:
            print(f"  (sample CFBD media for {team}: {team_media[0]})", file=sys.stderr)
            sample_logged["media"] = True
        for m_ in team_media:
            if m_.get("mediaType") and m_["mediaType"] != "tv":
                continue
            key = frozenset([our_name(m_["homeTeam"]), our_name(m_["awayTeam"])])
            media_by_key[key] = m_

    for key, i in by_key.items():
        m_ = media_by_key.get(key)
        if m_ and m_.get("outlet") and games[i].get("tv") != m_["outlet"]:
            games[i]["tv"] = m_["outlet"]
            changed = True

    # ---- betting line (separate endpoint) ----
    lines_by_key = {}
    for team in SEC_TEAMS:
        team_lines = fetch_lines_for_team(team, api_key)
        if not sample_logged["lines"] and team_lines:
            print(f"  (sample CFBD lines entry for {team}: {team_lines[0]})", file=sys.stderr)
            sample_logged["lines"] = True
        for gl in team_lines:
            key = frozenset([our_name(gl.get("homeTeam", "")), our_name(gl.get("awayTeam", ""))])
            lines_by_key[key] = gl

    for key, i in by_key.items():
        gl = lines_by_key.get(key)
        if not gl:
            continue
        line = pick_line(gl.get("lines", []))
        if not line:
            continue
        spread_text = line.get("formattedSpread")
        if not spread_text and line.get("spread") is not None:
            fav = games[i]["a"] if line["spread"] < 0 else games[i]["b"]
            spread_text = f"{fav} {line['spread']}"
        if spread_text and games[i].get("spread") != spread_text:
            games[i]["spread"] = spread_text
            changed = True
        if line.get("overUnder") is not None and games[i].get("overUnder") != line["overUnder"]:
            games[i]["overUnder"] = line["overUnder"]
            changed = True

    if not changed:
        print("Checked CFBD \u2014 nothing new to update (scores, times, odds all already current).")
        return

    html = save_season(html, season, match)
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print("index.html updated.")
    print("CHANGES_MADE")


if __name__ == "__main__":
    main()
