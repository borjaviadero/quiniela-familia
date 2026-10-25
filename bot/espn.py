"""Live results from ESPN's public scoreboard API (no key or sign-up).

Loterías y Apuestas only publishes results once the whole draw is settled, so the daily
progress reports would never be sent while a matchday is being played. ESPN has every
final score of LaLiga (Primera and Segunda) within minutes, so it fills in the matches
LAE has not published yet. LAE's official results always take precedence.

Names are matched by their meaningful words, as in bot.teams. A game matches when the
kickoff agrees and at least one of the two teams is recognised (spellings differ, e.g.
"Dep. Alavés" / "Alavés"); a team plays only once in that window, so it is unambiguous.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

from .rules import MADRID, sign_from_score
from .teams import is_womens_team, tokens

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
LEAGUES = ("esp.1", "esp.2")              # Primera and Segunda División
KICKOFF_TOLERANCE = timedelta(hours=3)    # same match even if the kickoff time was moved a little


def _team_tokens(team: dict) -> frozenset[str]:
    words: set[str] = set()
    for key in ("displayName", "shortDisplayName", "name", "location"):
        if team.get(key):
            words |= tokens(team[key])
    return frozenset(words)


def finished_games(start: datetime, end: datetime) -> list[dict]:
    """Finished games between two dates: [{home, away (token sets), kickoff, home_goals, away_goals}]."""
    dates = f"{start.astimezone(MADRID):%Y%m%d}-{end.astimezone(MADRID):%Y%m%d}"
    games = []
    for league in LEAGUES:
        try:
            r = requests.get(SCOREBOARD_URL.format(league=league),
                             params={"dates": dates, "limit": 200}, timeout=20)
            r.raise_for_status()
            events = r.json().get("events", [])
        except Exception as e:  # noqa: BLE001
            print(f"Could not read the ESPN scoreboard ({league}): {e}")
            continue
        for event in events:
            try:
                competition = event["competitions"][0]
                status = (competition.get("status") or event.get("status") or {}).get("type", {})
                if not status.get("completed") or status.get("state") != "post":
                    continue
                sides = {c["homeAway"]: c for c in competition["competitors"]}
                games.append({
                    "home": _team_tokens(sides["home"]["team"]),
                    "away": _team_tokens(sides["away"]["team"]),
                    "kickoff": datetime.fromisoformat(event["date"].replace("Z", "+00:00")),
                    "home_goals": int(sides["home"]["score"]),
                    "away_goals": int(sides["away"]["score"]),
                })
            except (KeyError, IndexError, TypeError, ValueError):
                continue
    return games


def _same_match(match: dict, game: dict) -> bool:
    if is_womens_team(match["home"]) or is_womens_team(match["away"]):
        return False
    home, away = tokens(match["home"]), tokens(match["away"])
    same_team = (home and home <= game["home"]) or (away and away <= game["away"])
    return bool(same_team) and abs(match["kickoff"] - game["kickoff"]) <= KICKOFF_TOLERANCE


def fill_results(matches: list[dict], now: datetime, games: list[dict] | None = None) -> list[dict]:
    """Returns the matches with the missing results completed from ESPN, where possible.
    Only matches that have kicked off and have no sign yet are looked up."""
    missing = [m for m in matches if not m.get("sign") and m.get("kickoff") and m["kickoff"] <= now]
    if not missing:
        return matches
    if games is None:
        games = finished_games(min(m["kickoff"] for m in missing) - timedelta(days=1),
                               max(m["kickoff"] for m in missing) + timedelta(days=1))
    out = []
    for m in matches:
        if m in missing:
            found = [g for g in games if _same_match(m, g)]
            if len(found) == 1:
                g = found[0]
                m = {**m, "home_goals": g["home_goals"], "away_goals": g["away_goals"],
                     "sign": sign_from_score(g["home_goals"], g["away_goals"])}
        out.append(m)
    return out
