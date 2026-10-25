"""Simulates whole matchdays in memory (no Supabase, Telegram, LAE or ESPN).

    python -m tests.test_simulation [output_dir]

Every message and image the bot would send is written to output_dir so it can be
reviewed. The scenario covers: announcement, 48 h reminder, published picks, a
daily report, an overlapping midweek matchday, a schedule change, a skipped cup
matchday, the final result, who pays and the prize message. Two shorter scenarios cover
a matchday published early and live scores from ESPN while LAE has no results yet.
"""
from __future__ import annotations

import random
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

from bot import espn
from bot.main import Bot
from bot.rules import MADRID, days_pending_report, pick_pleno_player, who_pays
from bot.teams import tokens
from bot.telegram import Telegram

FIXTURES = [("Athletic Club", "Valencia"), ("Real Madrid", "Sevilla"), ("Betis", "Barcelona"),
            ("Osasuna", "Getafe"), ("Villarreal", "Alavés"), ("Celta", "Mallorca"),
            ("Girona", "Rayo Vallecano"), ("At. Madrid", "Real Sociedad"), ("Espanyol", "Levante"),
            ("Elche", "Oviedo"), ("Racing", "Deportivo"), ("Eibar", "Zaragoza"), ("Sporting", "Málaga"),
            ("Almería", "Granada"), ("Real Sociedad", "Athletic")]
# Saturday 24 Oct: matches 1-7 · Sunday 25 Oct: matches 8-15
SAT = datetime(2026, 10, 24, 14, 0, tzinfo=MADRID)
SUN = datetime(2026, 10, 25, 14, 0, tzinfo=MADRID)
SCORES = ["2-1", "1-1", "0-2", "3-0", "1-0", "1-1", "2-2", "1-0", "0-1", "1-1", "2-0", "0-0", "1-2", "3-1", "2-1"]


class FakeSource:
    """Stands in for bot.lae."""
    with_midweek = False
    midweek_moved_earlier = timedelta(0)

    def __init__(self):
        self.played_until: datetime | None = None

    def _weekend_matches(self):
        out = []
        for i, (home, away) in enumerate(FIXTURES):
            kickoff = (SAT if i < 7 else SUN) + timedelta(hours=2 * (i % 4))
            hg = ag = sign = None
            if self.played_until and kickoff + timedelta(hours=2) <= self.played_until:
                hg, ag = map(int, SCORES[i].split("-"))
                sign = "1" if hg > ag else "2" if hg < ag else "X"
            out.append({"position": i + 1, "home": home, "away": away, "kickoff": kickoff,
                        "home_goals": hg, "away_goals": ag, "sign": sign})
        return out

    def upcoming_matchdays(self):
        out = [{"draw_id": "LAQU-14", "number": 14, "draw_date": date(2026, 10, 25),
                "first_kickoff": SAT, "matches": self._weekend_matches()}]
        if self.with_midweek:     # LaLiga midweek matchday, published while 14 is being played
            tuesday = datetime(2026, 10, 27, 21, 0, tzinfo=MADRID) - self.midweek_moved_earlier
            out.append({"draw_id": "LAQU-15", "number": 15, "draw_date": date(2026, 10, 28),
                        "first_kickoff": tuesday,
                        "matches": [{"position": i + 1, "home": FIXTURES[i][1], "away": FIXTURES[i][0],
                                     "kickoff": tuesday + timedelta(hours=24 * (i % 2)), "home_goals": None,
                                     "away_goals": None, "sign": None} for i in range(15)]})
            out.append({"draw_id": "LAQU-16", "number": 16, "draw_date": date(2026, 10, 29),
                        "first_kickoff": tuesday,    # cup matchday: must be ignored
                        "matches": [{"position": i + 1, "home": f"Cd Pueblo {i}", "away": FIXTURES[i][0],
                                     "kickoff": tuesday, "home_goals": None, "away_goals": None,
                                     "sign": None} for i in range(15)]})
        if self.with_next_weekend:
            out.append(self.next_weekend())
        return out

    with_next_weekend = False

    def next_weekend(self):
        """A LaLiga matchday for the following weekend, published early (real case: matchday 18)."""
        sat = SAT + timedelta(days=7)
        return {"draw_id": "LAQU-18", "number": 18, "draw_date": date(2026, 11, 1), "first_kickoff": sat,
                "matches": [{"position": i + 1, "home": FIXTURES[i][0], "away": FIXTURES[i][1],
                             "kickoff": sat + timedelta(hours=i % 5), "home_goals": None,
                             "away_goals": None, "sign": None} for i in range(15)]}

    def payouts(self, draw_id, draw_date):
        if draw_id != "LAQU-14" or not self.played_until or self.played_until < datetime(2026, 10, 26, tzinfo=MADRID):
            return None
        return {"pleno": Decimal("80778.13"), 14: Decimal("11162.13"), 13: Decimal("165.26"),
                12: Decimal("12.76"), 11: Decimal("2.22"), 10: Decimal("0"), 9: Decimal("0")}

    def matches(self, draw_id, draw_date):
        if draw_id == "LAQU-18":
            return self.next_weekend()["matches"]
        return next((m["matches"] for m in self.upcoming_matchdays() if m["draw_id"] == draw_id), None)

    lae_settles_late = False      # real LAE: no results until the whole draw is settled
    lae_settled = False

    def results(self, draw_id, draw_date):
        if draw_id != "LAQU-14":
            return None
        matches = self._weekend_matches()
        if self.lae_settles_late and not self.lae_settled:
            matches = [{**m, "home_goals": None, "away_goals": None, "sign": None} for m in matches]
        return matches


class NoLiveScores:
    """Stands in for bot.espn when the scenario relies on LAE only."""
    @staticmethod
    def fill_results(matches, now): return matches


# How ESPN spells the teams (different from LAE on purpose, to exercise the matching)
ESPN_NAMES = {"At. Madrid": "Atlético Madrid", "Betis": "Real Betis", "Celta": "Celta Vigo",
              "Racing": "Racing Santander", "Deportivo": "Deportivo La Coruña", "Sporting": "Sporting Gijón",
              "Almería": "UD Almería", "Oviedo": "Real Oviedo", "Málaga": "Málaga CF"}


class FakeESPN:
    """Stands in for bot.espn: finished games, spelled the ESPN way, run through the real matching."""
    def __init__(self, source):
        self.source = source

    def fill_results(self, matches, now):
        games = [{"home": tokens(ESPN_NAMES.get(m["home"], m["home"])),
                  "away": tokens(ESPN_NAMES.get(m["away"], m["away"])),
                  "kickoff": m["kickoff"], "home_goals": m["home_goals"], "away_goals": m["away_goals"]}
                 for m in self.source._weekend_matches() if m["sign"]]
        return espn.fill_results(matches, now, games=games)


class FakeDB:
    """Stands in for bot.db.DB, keeping everything in memory."""
    now = None

    def __init__(self):
        self._players = [{"id": i + 1, "name": n, "telegram_user_id": None}
                         for i, n in enumerate(["Luis Angel", "Rosana", "Joseba", "Lara", "Borja"])]
        self.matchdays, self.fixtures, self.signs, self.plenos, self.reports, self.pay = [], {}, {}, {}, set(), {}
        # Matchday 13, already finished: Joseba lost; Lara and Borja tied at the top
        self.matchdays.append({"id": 13, "number": 13, "finished": True, "prizes_published": True,
                               "first_kickoff": SAT - timedelta(days=7)})
        self.fixtures[13] = [{"position": i + 1, "sign": "1"} for i in range(14)]
        self.signs[13] = {1: "1111111111XXXX", 2: "111111111XXXXX", 3: "11111XXXXXXXXX",
                          4: "1111111111111X", 5: "1111111111111X"}
        self.pay[13] = [{"player_id": 3, "amount": Decimal("3.75")}]

    def players(self): return deepcopy(self._players)

    def open_matchdays(self):
        return deepcopy(sorted((m for m in self.matchdays if not m["finished"]), key=lambda m: m["first_kickoff"]))

    def previous_matchday(self, first_kickoff):
        earlier = [m for m in self.matchdays if m["first_kickoff"] < first_kickoff]
        return deepcopy(max(earlier, key=lambda m: m["first_kickoff"])) if earlier else None

    def last_finished(self):
        done = [m for m in self.matchdays if m["finished"]]
        return deepcopy(done[-1]) if done else None

    def matchdays_without_prizes(self):
        return deepcopy([m for m in self.matchdays if m["finished"] and not m.get("prizes_published")])

    def matchday_exists(self, season, number): return any(m["number"] == number for m in self.matchdays)
    def matches(self, md_id): return deepcopy(self.fixtures[md_id])

    def picks(self, md_id):
        return {p: {"signs": s, "pleno": self.plenos.get((md_id, p))} for p, s in self.signs.get(md_id, {}).items()}

    def reported_days(self, md_id): return {d for (m, d) in self.reports if m == md_id}
    def payments(self, md_id): return deepcopy(self.pay.get(md_id, []))

    def create_matchday(self, md, fixtures):
        md = {**md, "id": md["number"], "created_at": self.now, "announced": False, "reminder_sent": False,
              "picks_published": False, "finished": False, "prizes_published": False}
        self.matchdays.append(md)
        self.fixtures[md["id"]] = deepcopy(fixtures)
        return deepcopy(md)

    def update_matchday(self, md_id, **fields): next(m for m in self.matchdays if m["id"] == md_id).update(fields)
    def save_matches(self, md_id, fixtures): self.fixtures[md_id] = deepcopy(fixtures)
    def mark_reported(self, md_id, day): self.reports.add((md_id, day))

    def save_payments(self, md_id, amounts):
        self.pay[md_id] = [{"player_id": p, "amount": a} for p, a in sorted(amounts.items())]


def main(output_dir: str):
    import bot.teams
    bot.teams.from_espn = lambda: []      # offline: use the fallback team list
    db, source = FakeDB(), FakeSource()
    tg = Telegram(dry_run=output_dir)
    rng = random.Random(7)

    def tick(at: datetime, label: str):
        before = tg.sent
        db.now = at
        Bot(db, tg, source, now=at, rng=rng, live=NoLiveScores).tick()
        print(f"{at:%a %d %H:%M}  {label:<44} -> {tg.sent - before} sent")

    tick(datetime(2026, 10, 20, 11, 0, tzinfo=MADRID), "matchday 14 published")
    tick(datetime(2026, 10, 20, 12, 0, tzinfo=MADRID), "next run (nothing repeated)")
    db.signs[14] = {1: "11221X111X11X1", 2: "1X2111X11112X1", 4: "112X1X1X1X1121"}     # 3 of 5 in
    md14 = db.open_matchdays()[0]
    tick(datetime(2026, 10, 22, 14, 30, tzinfo=MADRID), "48 h before: reminder to the missing ones")
    tick(datetime(2026, 10, 22, 16, 0, tzinfo=MADRID), "next run (reminder not repeated)")
    db.signs[14][3] = "1X1X1X1X1X1X1X"
    db.signs[14][5] = "11211X1X1X1121"
    db.signs[14][2] = "1X2111X1121X21"   # Rosana: 11 hits -> 4th-category prize
    db.plenos[(14, md14["pleno_player_id"])] = "2-1"
    tick(datetime(2026, 10, 22, 20, 0, tzinfo=MADRID), "all 5 in: picks published")
    source.played_until = datetime(2026, 10, 24, 19, 0, tzinfo=MADRID)
    tick(datetime(2026, 10, 24, 19, 0, tzinfo=MADRID), "Saturday halfway (nothing sent)")
    source.played_until = datetime(2026, 10, 25, 0, 30, tzinfo=MADRID)
    tick(datetime(2026, 10, 25, 0, 30, tzinfo=MADRID), "Saturday over: daily report")
    source.with_midweek = True
    tick(datetime(2026, 10, 25, 10, 0, tzinfo=MADRID), "midweek 15 published while 14 in play")
    source.midweek_moved_earlier = timedelta(hours=2)
    tick(datetime(2026, 10, 25, 12, 0, tzinfo=MADRID), "15 moves its first kickoff earlier")
    source.played_until = datetime(2026, 10, 26, 2, 0, tzinfo=MADRID)
    tick(datetime(2026, 10, 26, 2, 0, tzinfo=MADRID), "Sunday over: final, payments, prizes")
    tick(datetime(2026, 10, 26, 3, 0, tzinfo=MADRID), "next run (final not repeated)")
    tick(datetime(2026, 10, 26, 9, 0, tzinfo=MADRID), "Monday: 48 h window of 15")

    # ---- rule checks ----
    assert who_pays({1: 5, 2: 3, 3: 3, 4: 9, 5: 7}) == {2: Decimal("1.87"), 3: Decimal("1.88")}
    assert sum(who_pays({1: 2, 2: 2, 3: 2, 4: 9, 5: 7}).values()) == Decimal("3.75")
    assert pick_pleno_player({1: 9, 2: 12, 3: 4}, [1, 2, 3]) == (2, False)
    assert pick_pleno_player(None, [1, 2, 3, 4, 5], random.Random(1))[1] is True
    md14 = next(m for m in db.matchdays if m["id"] == 14)
    md15 = next(m for m in db.matchdays if m["id"] == 15)
    assert md14["finished"] and not md15["finished"] and md15["announced"]
    assert not any(m["id"] == 16 for m in db.matchdays), "the cup matchday must not be created"
    assert md15["first_kickoff"] == datetime(2026, 10, 27, 19, 0, tzinfo=MADRID), "the deadline must move"

    # Postponed match: the day's report goes out after 8 h, but not if no result exists at all
    fixtures = [{"position": i + 1, "kickoff": (SAT if i < 7 else SUN) + timedelta(hours=i % 3),
                 "sign": "1" if i < 6 else None} for i in range(15)]
    assert days_pending_report(fixtures, set(), SAT + timedelta(hours=4)) == []
    assert days_pending_report(fixtures, set(), SAT + timedelta(hours=11)) == [SAT.date()]
    assert days_pending_report([{**f, "sign": None} for f in fixtures], set(), SAT + timedelta(hours=11)) == []

    print("Pleno matchday 14:", md14["pleno_player_id"], "(drawn)" if md14["pleno_by_draw"] else "")
    print("Pleno matchday 15:", md15["pleno_player_id"], "(drawn)" if md15["pleno_by_draw"] else "")
    print("Pays the next ticket:", db.pay[14])
    print("OK")



def early_next_matchday(output_dir: str):
    """Next weekend's matchday is published while this one is still open (real case:
    matchday 18 published on Wednesday). It must wait until this one has finished, so that
    its Pleno goes to this matchday's best player and the announcement says who pays."""
    db, source = FakeDB(), FakeSource()
    source.with_next_weekend = True
    tg = Telegram(dry_run=output_dir)
    rng = random.Random(7)

    def tick(at: datetime):
        db.now = at
        Bot(db, tg, source, now=at, rng=rng, live=NoLiveScores).tick()

    tick(datetime(2026, 10, 20, 11, 0, tzinfo=MADRID))
    db.signs[14] = {1: "11221X111X11X1", 2: "1X2111X1121X21", 3: "1X1X1X1X1X1X1X",
                    4: "112X1X1X1X1121", 5: "11211X1X1X1121"}
    tick(datetime(2026, 10, 21, 12, 0, tzinfo=MADRID))             # 18 published, 14 open
    assert not any(m["number"] == 18 for m in db.matchdays), "must wait while 14 is open"
    source.played_until = datetime(2026, 10, 25, 0, 30, tzinfo=MADRID)
    tick(datetime(2026, 10, 25, 0, 30, tzinfo=MADRID))             # 14 in play
    assert not any(m["number"] == 18 for m in db.matchdays), "must wait for 14 to finish"
    source.played_until = datetime(2026, 10, 26, 2, 0, tzinfo=MADRID)
    tick(datetime(2026, 10, 26, 2, 0, tzinfo=MADRID))              # 14 finishes
    tick(datetime(2026, 10, 26, 3, 0, tzinfo=MADRID))              # next run announces 18
    md18 = next(m for m in db.matchdays if m["number"] == 18)
    assert md18["announced"]
    assert md18["pleno_player_id"] == 2 and not md18["pleno_by_draw"], "Pleno: 14's best player (Rosana)"
    announcement = sorted(f for f in __import__("os").listdir(output_dir) if f.endswith("_photo.txt"))[-1]
    text = open(f"{output_dir}/{announcement}", encoding="utf-8").read()
    assert "jornada 18" in text and "Joseba</b> debe abonarla" in text and "Ojo" not in text, text
    print("Early matchday 18: waited for 14, Pleno to its winner, payer announced. OK")


def live_scores(output_dir: str):
    """Real case, matchday 16: LAE published no results during the weekend, so Saturday's
    report was never sent. ESPN's live scores must fill in the gap."""
    import bot.teams
    bot.teams.from_espn = lambda: []
    db, source = FakeDB(), FakeSource()
    source.lae_settles_late = True
    tg = Telegram(dry_run=output_dir)
    rng = random.Random(7)

    def tick(at: datetime):
        db.now = at
        Bot(db, tg, source, now=at, rng=rng, live=FakeESPN(source)).tick()

    tick(datetime(2026, 10, 20, 11, 0, tzinfo=MADRID))
    db.signs[14] = {1: "11221X111X11X1", 2: "1X2111X1121X21", 3: "1X1X1X1X1X1X1X",
                    4: "112X1X1X1X1121", 5: "11211X1X1X1121"}
    db.plenos[(14, db.open_matchdays()[0]["pleno_player_id"])] = "2-1"
    source.played_until = datetime(2026, 10, 25, 0, 30, tzinfo=MADRID)
    tick(datetime(2026, 10, 25, 0, 30, tzinfo=MADRID))
    assert db.reported_days(14) == {SAT.date()}, "Saturday's report must go out with ESPN's scores"
    assert all(m["sign"] for m in db.fixtures[14] if m["position"] <= 7)
    source.played_until = datetime(2026, 10, 26, 2, 0, tzinfo=MADRID)
    tick(datetime(2026, 10, 26, 2, 0, tzinfo=MADRID))
    md14 = next(m for m in db.matchdays if m["id"] == 14)
    assert md14["finished"] and db.pay[14], "the matchday must close with live scores too"
    print("Live scores: Saturday report and final result sent before LAE settled. OK")

    # Matching rules: the kickoff and at least one team must agree; women's and reserve teams never match
    sat = SAT
    game = {"home": tokens("Atlético Madrid"), "away": tokens("Real Sociedad"), "kickoff": sat,
            "home_goals": 2, "away_goals": 0}
    match = {"position": 1, "home": "At. Madrid", "away": "Real Sociedad", "kickoff": sat, "sign": None}
    assert espn.fill_results([match], sat + timedelta(hours=3), [game])[0]["sign"] == "1"
    assert espn.fill_results([{**match, "home": "Atl. Madrid"}], sat + timedelta(hours=3), [game])[0]["sign"] == "1"
    assert espn.fill_results([{**match, "home": "Betis", "away": "Celta"}], sat + timedelta(hours=3),
                             [game])[0]["sign"] is None
    assert espn.fill_results([{**match, "kickoff": sat + timedelta(days=1)}], sat + timedelta(days=2),
                             [game])[0]["sign"] is None
    assert espn.fill_results([{**match, "home": "At. Madrid (F)", "away": "Real Sociedad (F)"}], sat + timedelta(hours=3),
                             [game])[0]["sign"] is None
    assert espn.fill_results([{**match, "home": "Sevilla At.", "away": "Real Sociedad B"}], sat + timedelta(hours=3),
                             [{**game, "home": tokens("Sevilla")}])[0]["sign"] is None


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "simulation_output"
    main(out)
    early_next_matchday(out + "-early-matchday")
    live_scores(out + "-live-scores")
