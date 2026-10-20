"""LaLiga top-flight teams, refreshed automatically every season.

The list comes from ESPN's public API (no key or sign-up). If ESPN is unreachable,
rules.FALLBACK_TOP_FLIGHT_TEAMS is used instead. ESPN and Loterías spell names
differently ("Atlético Madrid" / "At. Madrid", "Real Betis" / "Betis"), so names are
compared by their meaningful words.
"""
from __future__ import annotations

import re
import unicodedata

import requests

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/teams"

_ABBREVIATIONS = {"at": "atletico", "atl": "atletico", "r": "real", "dep": "deportivo", "rc": "racing"}
_FILLER_WORDS = {"real", "club", "cf", "fc", "cd", "sd", "ud", "rcd", "sad", "de", "la", "el",
                 "balompie", "futbol"}


def tokens(name: str) -> frozenset[str]:
    """'At. Madrid' -> {atletico, madrid} · 'Real Betis Balompié' -> {betis} · 'Real Sociedad' -> {sociedad}."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [_ABBREVIATIONS.get(w, w) for w in re.findall(r"[a-z0-9]+", ascii_name)]
    meaningful = [w for w in words if w not in _FILLER_WORDS]
    return frozenset(meaningful or words)


def is_womens_team(name: str) -> bool:
    return name.strip().endswith("(F)")


def matches_team(lae_name: str, team: frozenset[str]) -> bool:
    """A Loterías name matches when all its words are in the team's name
    ('Celta' ⊆ 'Celta Vigo', 'At. Madrid' ⊆ 'Atlético Madrid'). Not the other way round,
    so reserve teams ('Sevilla At.', 'Real Madrid Castilla') never match."""
    if is_womens_team(lae_name):
        return False
    words = tokens(lae_name)
    return bool(words) and words <= team


def from_espn() -> list[str]:
    """This season's top-flight team names according to ESPN ([] on failure)."""
    try:
        r = requests.get(ESPN_TEAMS_URL, timeout=20)
        r.raise_for_status()
        teams = r.json()["sports"][0]["leagues"][0]["teams"]
        names = set()
        for t in teams:
            t = t.get("team", t)
            names.update(t[k] for k in ("displayName", "shortDisplayName", "name") if t.get(k))
        return sorted(names) if len(teams) >= 18 else []
    except Exception as e:  # noqa: BLE001
        print(f"Could not read the team list from ESPN: {e}")
        return []
