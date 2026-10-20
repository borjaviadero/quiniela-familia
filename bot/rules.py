"""Game rules. Pure functions: no network, no database."""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
TICKET_PRICE = Decimal("3.75")

# Time after the last kickoff of a day before the day is considered over
# (90' + half-time + stoppage time).
MATCH_DURATION = timedelta(hours=2)
# If a match still has no result this long after the last kickoff of its day
# (postponed, suspended…), that day's report is sent without it.
MAX_RESULT_WAIT = timedelta(hours=8)

# FALLBACK list of 2026-27 LaLiga (Primera División) teams, used only if ESPN is
# unreachable. The live list is downloaded on every new matchday (see teams.py),
# so promotions and relegations need no manual changes.
FALLBACK_TOP_FLIGHT_TEAMS = {
    "Athletic Club", "At. Madrid", "Barcelona", "Betis", "Celta", "Deportivo", "Elche",
    "Espanyol", "Getafe", "Levante", "Osasuna", "Rayo Vallecano", "Real Madrid",
    "Real Sociedad", "Sevilla", "Valencia", "Villarreal",
}
MIN_TOP_FLIGHT_MATCHES = 5


def is_league_matchday(matches: list[dict], teams: list[str] | set[str] | None = None) -> bool:
    """A LaLiga Quiniela has several matches between two top-flight teams. Champions League,
    Copa del Rey, etc. have at most one top-flight team per match."""
    from .teams import matches_team, tokens
    team_tokens = [tokens(t) for t in (teams or FALLBACK_TOP_FLIGHT_TEAMS)]

    def top_flight(name: str) -> bool:
        return any(matches_team(name, t) for t in team_tokens)

    n = sum(1 for m in matches if m["position"] <= 14 and top_flight(m["home"]) and top_flight(m["away"]))
    return n >= MIN_TOP_FLIGHT_MATCHES


def sign_from_score(home_goals: int, away_goals: int) -> str:
    """1 = home win, X = draw, 2 = away win."""
    if home_goals > away_goals:
        return "1"
    if home_goals < away_goals:
        return "2"
    return "X"


def pleno_goals(goals: int) -> str:
    """Pleno al 15 is predicted per team as 0, 1, 2 or M (3 or more)."""
    return "M" if goals >= 3 else str(goals)


def count_hits(signs: str, results: dict[int, str]) -> int:
    """results: {position (1-14): '1'|'X'|'2'} for the matches already played."""
    return sum(1 for pos, s in results.items() if pos <= 14 and signs[pos - 1] == s)


def pleno_hit(pleno: str | None, home_goals: int | None, away_goals: int | None) -> bool | None:
    if pleno is None or home_goals is None or away_goals is None:
        return None
    return pleno == f"{pleno_goals(home_goals)}-{pleno_goals(away_goals)}"


def hits_by_player(picks: dict[int, str], results: dict[int, str]) -> dict[int, int]:
    """{player_id: hits}"""
    return {p: count_hits(s, results) for p, s in picks.items()}


def who_pays(hits: dict[int, int]) -> dict[int, Decimal]:
    """The player with the fewest hits pays the next ticket; ties split the price.
    Leftover cents go to the first player of the list (rounded to 0.01)."""
    lowest = min(hits.values())
    losers = sorted(p for p, h in hits.items() if h == lowest)
    share = (TICKET_PRICE / len(losers)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amounts = {p: share for p in losers}
    amounts[losers[0]] += TICKET_PRICE - share * len(losers)
    return amounts


def pick_pleno_player(previous_hits: dict[int, int] | None, players: list[int],
                      rng: random.Random | None = None) -> tuple[int, bool]:
    """Returns (player_id, by_draw). The best player of the previous matchday fills in
    the Pleno al 15; on a tie, or with no previous matchday, it is drawn at random."""
    rng = rng or random.Random()
    if not previous_hits:
        return rng.choice(sorted(players)), True
    best = max(previous_hits.values())
    candidates = sorted(p for p, h in previous_hits.items() if h == best)
    if len(candidates) == 1:
        return candidates[0], False
    return rng.choice(candidates), True


def match_days(matches: list[dict]) -> dict[date, list[int]]:
    """Groups positions 1-15 by calendar day (Madrid time)."""
    days: dict[date, list[int]] = defaultdict(list)
    for m in matches:
        if m.get("kickoff"):
            days[to_datetime(m["kickoff"]).astimezone(MADRID).date()].append(m["position"])
    return dict(sorted(days.items()))


def days_pending_report(matches: list[dict], already_sent: set[date], now: datetime) -> list[date]:
    """Days whose matches all have an official result and whose report has not been sent.
    If one is still missing MAX_RESULT_WAIT after the day's last kickoff (postponed), the
    report is sent anyway, but only if at least one result of that day has been published.
    The last day of the matchday gets no daily report: the final result covers it."""
    by_pos = {m["position"]: m for m in matches}
    days = match_days(matches)
    if not days:
        return []
    last_day = max(days)
    pending = []
    for day, positions in days.items():
        if day == last_day or day in already_sent:
            continue
        all_played = all(by_pos[p].get("sign") for p in positions if p <= 14)
        any_played = any(by_pos[p].get("sign") for p in positions if p <= 14)
        last_kickoff = max(to_datetime(by_pos[p]["kickoff"]) for p in positions)
        if (all_played and now >= last_kickoff + MATCH_DURATION) or \
                (any_played and now >= last_kickoff + MAX_RESULT_WAIT):
            pending.append(day)
    return pending


def matchday_complete(matches: list[dict]) -> bool:
    return all(m.get("sign") for m in matches if m["position"] <= 14)


def column_prizes(hits: dict[int, int], pleno_correct: bool, payouts: dict) -> dict[int, list]:
    """{player_id: [(category, amount), ...]} for prize-winning columns only.
    There is one Pleno al 15 per ticket: it counts for any column with 14 hits.
    payouts: {"pleno": Decimal, 14: Decimal, 13: ..., 10: ...} from the official results."""
    out: dict[int, list] = {}
    for player, h in hits.items():
        prizes = []
        if (amount := payouts.get(h)) and amount > 0:
            prizes.append((h, amount))
        if h == 14 and pleno_correct and (amount := payouts.get("pleno")) and amount > 0:
            prizes.append(("pleno", amount))
        if prizes:
            out[player] = prizes
    return out


def season_of(day: date | datetime) -> str:
    """'2026-2027' for any date between July 2026 and June 2027."""
    return f"{day.year}-{day.year + 1}" if day.month >= 7 else f"{day.year - 1}-{day.year}"


def to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def to_date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
