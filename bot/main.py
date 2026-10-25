"""Family Quiniela bot.

Usage:
  python -m bot.main tick                 # what GitHub Actions runs on schedule
  python -m bot.main tick --dry-run DIR   # same, but saves images and texts to DIR instead of sending
  python -m bot.main diagnose             # shows how LAE and ESPN data are being interpreted
  python -m bot.main telegram-ids         # prints the group's chat_id and members' user_ids

`tick` is idempotent: every message is flagged in the database once sent, so it
never repeats no matter how often it runs.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta

from . import espn, lae, messages as msg, teams
from .db import DB
from .rules import (MADRID, MATCH_DURATION, column_prizes, days_pending_report, hits_by_player, is_league_matchday,
                    matchday_complete, pick_pleno_player, pleno_hit, season_of, to_date, to_datetime,
                    who_pays)
from .telegram import Telegram
from .ticket import render

# Link behind the "Hacer mi quiniela" button. The family key travels in the URL
# fragment, so only people in the Telegram group can use the web app.
_FAMILY_KEY = os.environ.get("FAMILY_KEY", "")
WEB_URL = os.environ.get("WEB_URL", "")
WEB_LINK = f"{WEB_URL}#k={_FAMILY_KEY}" if WEB_URL and _FAMILY_KEY else WEB_URL
BETTING_CLOSES_BEFORE = timedelta(hours=24)   # picks can be edited until 24 h before the first kickoff
REMINDER_BEFORE = timedelta(hours=48)         # reminder to whoever is missing, 48 h before
PRIZE_WAIT = timedelta(days=10)               # stop waiting for the official prize breakdown after this
MIN_BETTING_WINDOW = timedelta(hours=48)      # never wait for the previous matchday if less is left


class Bot:
    def __init__(self, db: DB, tg: Telegram, source=lae, now: datetime | None = None,
                 rng: random.Random | None = None, live=espn):
        self.db, self.tg, self.source, self.live = db, tg, source, live
        self.now = now or datetime.now(MADRID)
        self.rng = rng or random.Random()
        self.players = db.players()
        self.by_id = {p["id"]: p for p in self.players}

    # ------------------------------------------------------------------
    def tick(self):
        self.find_new_matchdays()
        open_matchdays = self.db.open_matchdays()
        if not open_matchdays:
            print("No open matchdays.")
        for md in open_matchdays:
            self.process(md)
        for md in self.db.matchdays_without_prizes():
            self.publish_prizes(md)

    def find_new_matchdays(self):
        top_flight = None
        for new in self.source.upcoming_matchdays():
            if not new["number"] or not new["first_kickoff"]:
                continue
            season = season_of(new["draw_date"] or new["first_kickoff"])
            if self.db.matchday_exists(season, new["number"]):
                continue
            if top_flight is None:      # only downloaded when there is a new matchday to check
                top_flight = self.top_flight_teams()
            if not is_league_matchday(new["matches"], top_flight):
                print(f"Matchday {new['number']}: not a LaLiga Quiniela (cup, Champions League…); skipped.")
                continue
            if new["first_kickoff"] - BETTING_CLOSES_BEFORE <= self.now:
                print(f"Matchday {new['number']}: published too late to bet on; skipped.")
                continue
            if reason := self.must_wait(new):
                print(f"Matchday {new['number']}: waiting, {reason}.")
                continue
            self.create_matchday(new, season)

    def must_wait(self, new: dict) -> str | None:
        """Decides whether a new matchday should be announced later. Players bet on one
        matchday at a time, and the Pleno and who pays come from the previous matchday,
        so the bot waits for it when there is time:
          1. while an earlier matchday is still open for betting, always wait;
          2. if an earlier matchday will finish before the new one closes, wait until it
             finishes, unless less than MIN_BETTING_WINDOW would be left to bet.
        Returns the reason to wait, or None to announce now."""
        new_close = new["first_kickoff"] - BETTING_CLOSES_BEFORE
        for md in self.db.open_matchdays():
            if to_datetime(md["first_kickoff"]) >= new["first_kickoff"]:
                continue
            if self.now < to_datetime(md["first_kickoff"]) - BETTING_CLOSES_BEFORE:
                return f"matchday {md['number']} is still open for betting"
            kickoffs = [to_datetime(m["kickoff"]) for m in self.db.matches(md["id"]) if m.get("kickoff")]
            expected_end = max(kickoffs) + MATCH_DURATION if kickoffs else None
            if expected_end and expected_end < new_close and self.now < new_close - MIN_BETTING_WINDOW:
                return f"matchday {md['number']} finishes first"
        return None

    def top_flight_teams(self) -> list[str]:
        names = teams.from_espn()
        print(f"Top-flight teams: {'ESPN' if names else 'fallback list'}")
        return names

    def create_matchday(self, new: dict, season: str) -> dict:
        # Pleno al 15: best player of the previous matchday. If that one is still being
        # played (overlapping matchdays), the last finished matchday is used instead.
        previous = self.db.previous_matchday(new["first_kickoff"])
        if previous and not previous["finished"]:
            previous = self.db.last_finished()
        previous_hits = self.hits_of(previous) if previous else None
        pleno_player, by_draw = pick_pleno_player(previous_hits, list(self.by_id), self.rng)

        fixtures = [{k: m[k] for k in ("position", "home", "away", "kickoff")} for m in new["matches"]]
        for m in fixtures:          # results are loaded later, as matches are played
            m.update(home_goals=None, away_goals=None, sign=None)
        return self.db.create_matchday({
            "season": season, "number": new["number"], "draw_date": new["draw_date"],
            "lae_draw_id": new["draw_id"], "first_kickoff": new["first_kickoff"],
            "pleno_player_id": pleno_player, "pleno_by_draw": by_draw,
        }, fixtures)

    def payments_due(self, md: dict) -> list[dict]:
        """Who pays this matchday's ticket: the losers of the previous one, once it has finished."""
        previous = self.db.previous_matchday(md["first_kickoff"])
        if previous and previous["finished"]:
            return self.db.payments(previous["id"])
        return []

    # ------------------------------------------------------------------
    def process(self, md: dict):
        if self.now < to_datetime(md["first_kickoff"]):
            md = self.refresh_fixtures(md)
        first_kickoff = to_datetime(md["first_kickoff"])
        close = first_kickoff - BETTING_CLOSES_BEFORE
        remind_at = first_kickoff - REMINDER_BEFORE

        if not md["announced"]:
            self.announce(md, close)
            self.db.update_matchday(md["id"], announced=True)

        picks = self.db.picks(md["id"])
        missing = [p for p in self.players if p["id"] not in picks]

        if not md["reminder_sent"] and remind_at <= self.now < close:
            # If the matchday was only announced shortly before, a reminder would be noise
            announced_late = md.get("created_at") and to_datetime(md["created_at"]) > remind_at - timedelta(hours=6)
            if missing and not announced_late:
                self.tg.send_message(msg.reminder(md["number"], missing, close), msg.PICK_BUTTON, WEB_LINK)
            self.db.update_matchday(md["id"], reminder_sent=True)

        if not md["picks_published"] and (not missing or self.now >= close):
            self.publish_picks(md, picks, missing)
            self.db.update_matchday(md["id"], picks_published=True)

        if self.now >= first_kickoff:
            self.track_results(md, picks)

    def refresh_fixtures(self, md: dict) -> dict:
        """Before kickoff, re-reads names and kickoff times in case LAE changes them
        (postponements, rescheduling). If the first kickoff moves while betting is open,
        the deadline moves with it and the group is told. Returns the updated matchday."""
        try:
            fixtures = self.source.matches(md["lae_draw_id"], to_date(md.get("draw_date")))
        except Exception as e:  # noqa: BLE001
            print(f"Could not refresh the fixtures of matchday {md['number']}: {e}")
            return md
        if not fixtures:
            return md
        self.db.save_matches(md["id"], [{**m, "home_goals": None, "away_goals": None, "sign": None}
                                        for m in fixtures])
        kickoffs = [to_datetime(m["kickoff"]) for m in fixtures if m.get("kickoff")]
        old = to_datetime(md["first_kickoff"])
        if not kickoffs or min(kickoffs) == old or self.now >= old - BETTING_CLOSES_BEFORE:
            return md          # unchanged, or betting already closed
        new = min(kickoffs)
        self.db.update_matchday(md["id"], first_kickoff=new)
        print(f"Matchday {md['number']}: first kickoff moved from {old} to {new}")
        if md["announced"]:
            self.tg.send_message(msg.schedule_change(md["number"], new), msg.PICK_BUTTON, WEB_LINK)
        return {**md, "first_kickoff": new}

    def announce(self, md: dict, close: datetime):
        pleno_player = self.by_id.get(md["pleno_player_id"])
        if not md["pleno_by_draw"]:
            reason = "best"
        elif self.db.last_finished():
            reason = "tie"
        else:
            reason = "draw"
        caption = msg.announcement(md["number"], close, pleno_player, reason, self.payments_due(md),
                                   self.by_id, self.overlap_notice(md))
        png = render(title=msg.ticket_title(md["number"]), subtitle=msg.ticket_subtitle_deadline(close),
                     matches=self.db.matches(md["id"]), players=[], picks={}, pleno_player_id=None,
                     show_results=False)
        self.tg.send_photo(png, caption, msg.PICK_BUTTON, WEB_LINK)

    def overlap_notice(self, md: dict) -> str | None:
        """If an earlier matchday is still being played, warn that these are separate Quinielas."""
        in_play = [o for o in self.db.open_matchdays()
                   if o["id"] != md["id"] and to_datetime(o["first_kickoff"]) < to_datetime(md["first_kickoff"])]
        if not in_play:
            return None
        midweek = to_datetime(md["first_kickoff"]).astimezone(MADRID).weekday() <= 3   # Monday to Thursday
        return msg.overlap_notice([o["number"] for o in in_play], midweek)

    def publish_picks(self, md: dict, picks: dict, missing: list[dict]):
        caption = msg.picks_published(md["number"], self.payments_due(md), self.by_id, missing)
        png = render(title=msg.ticket_title(md["number"]), subtitle=msg.TICKET_SUBTITLE_PICKS,
                     matches=self.db.matches(md["id"]), players=self.players, picks=picks,
                     pleno_player_id=md["pleno_player_id"], show_results=False)
        self.tg.send_photo(png, caption)

    # ------------------------------------------------------------------
    def track_results(self, md: dict, picks: dict):
        latest = self.source.results(md["lae_draw_id"], to_date(md.get("draw_date")))
        official = sum(1 for m in latest or [] if m["sign"])
        stored = [{**m, "kickoff": to_datetime(m["kickoff"]) if m.get("kickoff") else None}
                  for m in self.db.matches(md["id"])]
        if not latest:      # LAE unreachable: complete what is stored
            latest = stored
        # Once a match is played LAE may drop its kickoff time; the stored one is kept, as the
        # daily reports group matches by the day they are played
        kickoff_of = {m["position"]: m["kickoff"] for m in stored}
        latest = [{**m, "kickoff": m.get("kickoff") or kickoff_of.get(m["position"])} for m in latest]
        # LAE only publishes results once the draw is settled; live scores fill the gaps
        latest = self.live.fill_results(latest, self.now)
        known = sum(1 for m in latest if m["sign"])
        print(f"Matchday {md['number']}: {official} of 15 results at LAE, {known} with live scores")
        if latest:
            self.db.save_matches(md["id"], latest)
        fixtures = self.db.matches(md["id"])

        for day in days_pending_report(fixtures, self.db.reported_days(md["id"]), self.now):
            up_to_day = [m for m in fixtures if m["position"] <= 14 and m.get("kickoff")
                         and to_datetime(m["kickoff"]).astimezone(MADRID).date() <= day]
            played = [m for m in up_to_day if m.get("sign")]
            unresolved = [m for m in up_to_day if not m.get("sign")]
            hits = self.hits_for_all(picks, {m["position"]: m["sign"] for m in played})
            best = max(hits.values())
            leaders = [self.by_id[p]["name"] for p, h in hits.items() if h == best]
            visible = [{**m, "sign": m["sign"] if m in played else None} for m in fixtures]
            png = render(title=msg.ticket_title(md["number"]),
                         subtitle=msg.ticket_subtitle_progress(day, len(played)),
                         matches=visible, players=self.players, picks=picks,
                         pleno_player_id=md["pleno_player_id"], show_results=True)
            self.tg.send_photo(png, msg.daily_report(md["number"], day, len(played), leaders, best, unresolved))
            self.db.mark_reported(md["id"], day)

        if matchday_complete(fixtures):
            self.close_matchday(md, fixtures, picks)

    def close_matchday(self, md: dict, fixtures: list[dict], picks: dict):
        hits = self.hits_for_all(picks, {m["position"]: m["sign"] for m in fixtures if m["position"] <= 14})
        amounts = who_pays(hits)
        self.db.save_payments(md["id"], amounts)
        payments = [{"player_id": p, "amount": a} for p, a in sorted(amounts.items())]

        ranking = sorted(hits.items(), key=lambda x: -x[1])
        caption = msg.final_result(
            md["number"], [(self.by_id[p]["name"], h) for p, h in ranking],
            [self.by_id[p]["name"] for p, h in ranking if h == 14],
            self.pleno_correct(md, fixtures, picks), payments, self.by_id)
        png = render(title=msg.ticket_title(md["number"], final=True), subtitle=msg.TICKET_SUBTITLE_FINAL,
                     matches=fixtures, players=self.players, picks=picks,
                     pleno_player_id=md["pleno_player_id"], show_results=True)
        self.tg.send_photo(png, caption)
        self.db.update_matchday(md["id"], finished=True)

    def pleno_correct(self, md: dict, fixtures: list[dict], picks: dict) -> bool:
        """There is one Pleno al 15 per ticket (filled in by the designated player) and it
        counts for every column."""
        match15 = next((m for m in fixtures if m["position"] == 15), None)
        pleno = (picks.get(md["pleno_player_id"]) or {}).get("pleno")
        return bool(match15 and pleno_hit(pleno, match15.get("home_goals"), match15.get("away_goals")))

    def publish_prizes(self, md: dict):
        """Once LAE publishes the official prize breakdown, report which columns won and how much."""
        payouts = self.source.payouts(md["lae_draw_id"], to_date(md.get("draw_date")))
        if not payouts:
            if self.now - to_datetime(md["first_kickoff"]) > PRIZE_WAIT:
                self.db.update_matchday(md["id"], prizes_published=True)   # not coming any more
            return
        fixtures = self.db.matches(md["id"])
        picks = self.db.picks(md["id"])
        hits = self.hits_for_all(picks, {m["position"]: m["sign"] for m in fixtures if m["position"] <= 14})
        won = column_prizes(hits, self.pleno_correct(md, fixtures, picks), payouts)
        if won:
            winners = [(self.by_id[p], category, amount)
                       for p, prizes in sorted(won.items(), key=lambda x: -hits[x[0]])
                       for category, amount in prizes]
            self.tg.send_message(msg.prizes(md["number"], winners))
        self.db.update_matchday(md["id"], prizes_published=True)

    # ------------------------------------------------------------------
    def hits_for_all(self, picks: dict, results: dict) -> dict[int, int]:
        """Hits per player; a player without a column counts as 0 hits."""
        base = hits_by_player({p: pick["signs"] for p, pick in picks.items()}, results)
        return {p: base.get(p, 0) for p in self.by_id}

    def hits_of(self, md: dict) -> dict[int, int]:
        fixtures = self.db.matches(md["id"])
        results = {m["position"]: m["sign"] for m in fixtures if m.get("sign") and m["position"] <= 14}
        return self.hits_for_all(self.db.picks(md["id"]), results)


def diagnose():
    """Prints how the bot currently interprets ESPN and LAE data."""
    names = teams.from_espn()
    print(f"=== Top-flight teams according to ESPN ({len(names)} names) ===\n{names}\n")
    print("=== Upcoming draws (proximosv3) ===")
    print(json.dumps(lae.upcoming_raw(), ensure_ascii=False))
    upcoming = lae.upcoming_matchdays()
    for md in upcoming:
        print(f"\n=== Matchday {md['number']} · draw {md['draw_id']} · {md['draw_date']} ===")
        print(f"First kickoff: {md['first_kickoff']}  ·  official close: {md['official_close']}")
        print(f"Family deadline (24 h before): {md['first_kickoff'] - BETTING_CLOSES_BEFORE}")
        print(f"LaLiga Quiniela?: {'yes' if is_league_matchday(md['matches'], names or None) else 'NO, skipped'}")
        official = lae.results(md["draw_id"], md["draw_date"]) or md["matches"]
        with_live = espn.fill_results(official, datetime.now(MADRID))
        for m, live in zip(official, with_live):
            result = (f"{m['home_goals']}-{m['away_goals']} ({m['sign']}) LAE" if m["sign"] else
                      f"{live['home_goals']}-{live['away_goals']} ({live['sign']}) ESPN" if live["sign"] else "")
            print(f"  {m['position']:>2}. {m['kickoff']:%a %d %H:%M}  {m['home']} - {m['away']}  {result}")
    now = datetime.now(MADRID)
    games = espn.finished_games(now - timedelta(days=3), now)
    print(f"\n=== ESPN finished games in the last 3 days (Primera and Segunda): {len(games)} ===")
    print("\n=== Prize breakdown of draws settled in the last 10 days ===")
    today = datetime.now(MADRID).date()
    for d in lae.as_list(lae.settled_raw(today - timedelta(days=10), today)):
        print(f"Matchday {d.get('numero')} ({d.get('id_sorteo')}):",
              lae.payouts(d["id_sorteo"], lae.parse_datetime(d.get("fecha_sorteo")).date()))
    print("\n=== Raw draw sheet of the first upcoming matchday (fechav3) ===")
    if upcoming:
        print(json.dumps(lae.draw_raw(upcoming[0]["draw_date"]), ensure_ascii=False)[:4000])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    tick = sub.add_parser("tick")
    tick.add_argument("--dry-run", help="folder to save images and texts to instead of sending them")
    sub.add_parser("diagnose")
    sub.add_parser("telegram-ids")
    args = parser.parse_args(argv)

    if args.cmd == "tick":
        Bot(DB(), Telegram(dry_run=args.dry_run)).tick()
    elif args.cmd == "diagnose":
        diagnose()
    elif args.cmd == "telegram-ids":
        for update in Telegram().get_updates():
            m = update.get("message") or update.get("my_chat_member") or {}
            chat, sender = m.get("chat", {}), m.get("from", {})
            print(f"chat_id={chat.get('id')} ({chat.get('title') or chat.get('type')})  "
                  f"user_id={sender.get('id')} ({sender.get('first_name')})")


if __name__ == "__main__":
    sys.exit(main())
