"""Supabase access through its REST API (PostgREST), using the service_role key."""
from __future__ import annotations

import json
import os
from datetime import date, datetime

import requests


class DB:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/") + "/rest/v1"
        key = key or os.environ["SUPABASE_SERVICE_KEY"]
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json", "Prefer": "return=representation"}

    def request(self, method: str, table: str, params: dict | None = None, body=None, prefer: str | None = None):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        r = requests.request(method, f"{self.url}/{table}", params=params, headers=headers, timeout=30,
                             data=json.dumps(body, default=_json) if body is not None else None)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {method} {table}: {r.status_code} {r.text}")
        return r.json() if r.text else None

    # ---- reads -------------------------------------------------------
    def players(self) -> list[dict]:
        return self.request("GET", "players", {"select": "id,name,telegram_user_id", "order": "id"})

    def open_matchdays(self) -> list[dict]:
        return self.request("GET", "matchdays", {"finished": "eq.false", "order": "first_kickoff.asc"})

    def previous_matchday(self, first_kickoff) -> dict | None:
        """The matchday right before the given kickoff, finished or not."""
        r = self.request("GET", "matchdays", {"first_kickoff": f"lt.{_json(first_kickoff)}",
                                              "order": "first_kickoff.desc", "limit": 1})
        return r[0] if r else None

    def last_finished(self) -> dict | None:
        r = self.request("GET", "matchdays", {"finished": "eq.true", "order": "first_kickoff.desc", "limit": 1})
        return r[0] if r else None

    def matchdays_without_prizes(self) -> list[dict]:
        return self.request("GET", "matchdays", {"finished": "eq.true", "prizes_published": "eq.false",
                                                 "order": "first_kickoff.asc"})

    def matchday_exists(self, season: str, number: int) -> bool:
        return bool(self.request("GET", "matchdays", {"season": f"eq.{season}", "number": f"eq.{number}",
                                                      "select": "id"}))

    def matches(self, matchday_id: int) -> list[dict]:
        return self.request("GET", "matches", {"matchday_id": f"eq.{matchday_id}", "order": "position"})

    def picks(self, matchday_id: int) -> dict[int, dict]:
        rows = self.request("GET", "picks", {"matchday_id": f"eq.{matchday_id}"})
        return {r["player_id"]: r for r in rows}

    def reported_days(self, matchday_id: int) -> set[date]:
        rows = self.request("GET", "daily_reports", {"matchday_id": f"eq.{matchday_id}", "select": "day"})
        return {date.fromisoformat(r["day"]) for r in rows}

    def payments(self, matchday_id: int) -> list[dict]:
        return self.request("GET", "payments", {"matchday_id": f"eq.{matchday_id}", "order": "player_id"})

    # ---- writes ------------------------------------------------------
    def create_matchday(self, matchday: dict, matches: list[dict]) -> dict:
        md = self.request("POST", "matchdays", body=matchday)[0]
        self.request("POST", "matches", body=[{**m, "matchday_id": md["id"]} for m in matches])
        return md

    def update_matchday(self, matchday_id: int, **fields):
        self.request("PATCH", "matchdays", {"id": f"eq.{matchday_id}"}, fields)

    def save_matches(self, matchday_id: int, matches: list[dict]):
        rows = [{"matchday_id": matchday_id, **{k: m[k] for k in
                 ("position", "home", "away", "kickoff", "home_goals", "away_goals", "sign")}}
                for m in matches]
        self.request("POST", "matches", {"on_conflict": "matchday_id,position"}, rows,
                     prefer="resolution=merge-duplicates,return=minimal")

    def mark_reported(self, matchday_id: int, day: date):
        self.request("POST", "daily_reports", body={"matchday_id": matchday_id, "day": day.isoformat()},
                     prefer="return=minimal")

    def save_payments(self, matchday_id: int, amounts: dict[int, object]):
        self.request("POST", "payments", {"on_conflict": "matchday_id,player_id"},
                     [{"matchday_id": matchday_id, "player_id": p, "amount": str(a)} for p, a in amounts.items()],
                     prefer="resolution=merge-duplicates,return=minimal")


def _json(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)
