"""Client for the official Quiniela data at loteriasyapuestas.es (LAE).

LAE has no documented public API. These are the JSON endpoints its own website
uses (verified on 2026-10-20):

  proximosv3?game_id=LAQU&num=3
      Upcoming draws: id_sorteo, jornada (matchday number), fecha (draw date), cierre.
  fechav3?game_id=LAQU&fecha_sorteo=YYYYMMDD
      One draw with its 15 matches in ticket order: local, visitante, fecha
      ("2026\\/10\\/24 14:00:00"), plus marcador/signo once results exist.
  buscadorSorteos?game_id=LAQU&celebrados=true&fechaInicioInclusiva=..&fechaFinInclusiva=..
      Settled draws: matches with posicion, signo ("2 "), marcador ("0 - 1"),
      fecha ("17-10-2026"), hora ("14:00"), and the prize breakdown (escrutinio).

LAE answers 403 to clients that do not look like a browser, so requests go
through curl_cffi impersonating Chrome. The external JSON keys are Spanish;
everything returned by this module uses the project's English keys.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from curl_cffi import requests as http
    _IMPERSONATE = {"impersonate": "chrome"}
except ImportError:  # pragma: no cover
    import requests as http
    _IMPERSONATE = {}

from .rules import MADRID, sign_from_score

BASE_URL = "https://www.loteriasyapuestas.es/servicios"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.loteriasyapuestas.es/es/resultados/quiniela",
}
GAME_ID = "LAQU"   # La Quiniela


def _get(endpoint: str, params: dict) -> Any:
    r = http.get(f"{BASE_URL}/{endpoint}", params=params, headers=HEADERS, timeout=30, **_IMPERSONATE)
    r.raise_for_status()
    return r.json()


def upcoming_raw(count: int = 3) -> Any:
    return _get("proximosv3", {"game_id": GAME_ID, "num": count})


def draw_raw(draw_date: date) -> Any:
    return _get("fechav3", {"game_id": GAME_ID, "fecha_sorteo": draw_date.strftime("%Y%m%d")})


def settled_raw(start: date, end: date) -> Any:
    return _get("buscadorSorteos", {
        "game_id": GAME_ID, "celebrados": "true",
        "fechaInicioInclusiva": start.strftime("%Y%m%d"),
        "fechaFinInclusiva": end.strftime("%Y%m%d"),
    })


# --------------------------------------------------------------------------
#  Normalisation
# --------------------------------------------------------------------------

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                 "%Y/%m/%d %H:%M", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d", "%Y/%m/%d",
                 "%d-%m-%Y", "%d/%m/%Y")


def parse_datetime(value, time=None) -> datetime | None:
    """Parses the several date formats LAE uses, in Madrid time."""
    if value in (None, ""):
        return None
    s = str(value).replace("\\/", "/").strip()
    candidates = [s]
    if time:      # "hora" is not always a time (once a match is played it can hold a status)
        candidates.insert(0, f"{s[:10]} {str(time).strip()}")
    for text in candidates:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text[:19], fmt).replace(tzinfo=MADRID)
            except ValueError:
                continue
    return None


def _team_name(value) -> str:
    """'Athletic Club (m)' -> 'Athletic Club' · 'Real Madrid (f)' -> 'Real Madrid (F)' (women's team)."""
    s = str(value or "").strip()
    if re.search(r"\(f\)\s*$", s, re.I):
        return re.sub(r"\s*\(f\)\s*$", "", s, flags=re.I) + " (F)"
    return re.sub(r"\s*\(m\)\s*$", "", s, flags=re.I)


def _goals(text: str) -> int | None:
    text = text.strip().upper()
    if text == "M":
        return 3
    return int(text) if text.isdigit() else None


def _score(value) -> tuple[int | None, int | None]:
    """'0 - 1', '2-1', 'M - 0', and for the Pleno al 15 also '11' or '1M'."""
    s = str(value or "").strip().upper()
    m = re.match(r"^([0-9]+|M)\s*[-:]\s*([0-9]+|M)$", s) or re.match(r"^([0-2M])([0-2M])$", s)
    if not m:
        return None, None
    return _goals(m.group(1)), _goals(m.group(2))


def _match(index: int, raw: dict) -> dict:
    position = int(raw.get("posicion") or index + 1)
    home_goals, away_goals = _score(raw.get("marcador"))
    sign = str(raw.get("signo") or "").strip().upper()
    if position == 15 and home_goals is None:
        home_goals, away_goals = _score(sign)       # the pleno sometimes comes as "11" or "M-1"
    if sign not in ("1", "X", "2"):
        sign = sign_from_score(home_goals, away_goals) if home_goals is not None else None
    return {
        "position": position,
        "home": _team_name(raw.get("local")) or f"Partido {position}",
        "away": _team_name(raw.get("visitante")),
        "kickoff": parse_datetime(raw.get("fecha"), raw.get("hora")),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "sign": sign,
    }


def as_list(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []          # e.g. "No se ha encontrado ningún registro…"


def _matches_of(draw: dict) -> list[dict]:
    raw = [m for m in (draw.get("partidos") or []) if isinstance(m, dict)]
    return sorted((_match(i, m) for i, m in enumerate(raw)), key=lambda m: m["position"])[:15]


# --------------------------------------------------------------------------
#  Public API
# --------------------------------------------------------------------------

def matches(draw_id: str, draw_date: date) -> list[dict] | None:
    """The 15 matches of a draw, with any results already published."""
    for d in as_list(draw_raw(draw_date)):
        if str(d.get("id_sorteo")) == str(draw_id):
            ms = _matches_of(d)
            return ms if len(ms) >= 14 else None
    return None


def upcoming_matchdays() -> list[dict]:
    """Upcoming matchdays whose matches are already published, nearest first."""
    out = []
    for d in as_list(upcoming_raw()):
        draw_date = parse_datetime(d.get("fecha"))
        if not draw_date or not d.get("id_sorteo") or not d.get("jornada"):
            continue
        try:
            ms = matches(d["id_sorteo"], draw_date.date())
        except Exception:  # noqa: BLE001
            ms = None
        if not ms:
            continue
        kickoffs = [m["kickoff"] for m in ms if m["kickoff"]]
        out.append({
            "draw_id": str(d["id_sorteo"]),
            "number": int(d["jornada"]),
            "draw_date": draw_date.date(),
            "first_kickoff": min(kickoffs) if kickoffs else None,
            "official_close": parse_datetime(d.get("cierre")),
            "matches": ms,
        })
    return sorted(out, key=lambda m: m["draw_date"])


def results(draw_id: str, draw_date: date | None) -> list[dict] | None:
    """Matches of a draw with whatever results exist. Combines the draw sheet (results as
    they are played) and the settled draw (official), keeping the most complete one."""
    if not draw_date:
        return None
    candidates = []
    try:
        candidates.append(matches(draw_id, draw_date))
    except Exception:  # noqa: BLE001
        pass
    try:
        for d in as_list(settled_raw(draw_date - timedelta(days=7), draw_date + timedelta(days=3))):
            if str(d.get("id_sorteo")) == str(draw_id):
                candidates.append(_matches_of(d))
    except Exception:  # noqa: BLE001
        pass
    candidates = [c for c in candidates if c and len(c) >= 14]
    if not candidates:
        return None
    return max(candidates, key=lambda ms: sum(1 for m in ms if m["sign"]))


def _amount(text) -> Decimal:
    """'80.778,13' -> Decimal('80778.13')"""
    s = str(text or "0").strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal(0)


def payouts(draw_id: str, draw_date: date | None) -> dict | None:
    """Prize per column for each category, from the official breakdown:
    {"pleno": Decimal, 14: Decimal, 13: ..., 10: ...}. None until it is published."""
    if not draw_date:
        return None
    for d in as_list(settled_raw(draw_date - timedelta(days=7), draw_date + timedelta(days=3))):
        if str(d.get("id_sorteo")) != str(draw_id) or not d.get("escrutinio"):
            continue
        prizes = {}
        for category in d["escrutinio"]:
            label = str(category.get("tipo", "")).lower()
            if "pleno" in label:
                prizes["pleno"] = _amount(category.get("premio"))
            elif (m := re.search(r"(\d+)\s*acierto", label)):
                prizes[int(m.group(1))] = _amount(category.get("premio"))
        return prizes or None
    return None
