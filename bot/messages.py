"""User-facing texts, in Spanish (the family's language).

Everything the players read in Telegram lives here, so the rest of the code base
stays in English and the copy can be reviewed or translated in one place.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .rules import MADRID, to_datetime
from .telegram import mention

PICK_BUTTON = "Hacer mi quiniela"

_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"]


# ---- formatting helpers ----------------------------------------------------
def weekday(d: date) -> str:
    return _WEEKDAYS[d.weekday()]


def when(dt) -> str:
    """'viernes 23 de octubre a las 14:00'"""
    dt = to_datetime(dt).astimezone(MADRID)
    return f"{weekday(dt.date())} {dt.day} de {_MONTHS[dt.month - 1]} a las {dt:%H:%M}"


def eur(amount: Decimal) -> str:
    """Decimal('1.87') -> '1,87 €'"""
    return f"{amount:.2f}".replace(".", ",") + " €"


def join_names(names: list[str]) -> str:
    """['A', 'B', 'C'] -> 'A, B y C'"""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def payment(payments: list[dict], players: dict[int, dict], verb: str) -> str:
    """'Joseba debe abonarla con 3,75 €' / 'Joseba (1,87 €) y Lara (1,88 €) deben abonarla'."""
    amounts = [Decimal(str(p["amount"])) for p in payments]
    names = [mention(players[p["player_id"]]) for p in payments]
    if len(payments) == 1:
        return f"{names[0]} debe {verb} con {eur(amounts[0])}"
    if len(set(amounts)) == 1:
        return f"{join_names(names)} deben {verb} con {eur(amounts[0])} cada uno"
    parts = [f"{n} ({eur(a)})" for n, a in zip(names, amounts)]
    return f"{join_names(parts)} deben {verb}"


# ---- messages ---------------------------------------------------------------
def announcement(number: int, close: datetime, pleno_player: dict | None, pleno_reason: str | None,
                 payments: list[dict], players: dict[int, dict], overlap_notice: str | None) -> str:
    lines = [f"⚽ <b>Ya está la quiniela de la jornada {number}</b>",
             f"Tenéis hasta el {when(close)} para registrar vuestra columna."]
    if pleno_player:
        reason = {"best": "por ser quien más acertó",
                  "tie": "sorteado entre los que más acertaron",
                  "draw": "por sorteo"}[pleno_reason]
        lines.append(f"El pleno al 15 lo rellena {mention(pleno_player)} ({reason}).")
    if payments:
        lines.append(payment(payments, players, "abonarla") + ".")
    if overlap_notice:
        lines.append(overlap_notice)
    return "\n".join(lines)


def overlap_notice(numbers: list[int], midweek: bool) -> str:
    others = (f"la jornada {numbers[0]} sigue" if len(numbers) == 1
              else f"las jornadas {join_names([str(n) for n in numbers])} siguen")
    kind = "una jornada entre semana" if midweek else "otra jornada"
    return (f"⚠️ Ojo: es {kind} y {others} en juego. Son quinielas distintas: "
            f"esta tiene su propio plazo, sus propias columnas y su propio boleto.")


def reminder(number: int, missing: list[dict], close: datetime) -> str:
    if len(missing) > 1:
        return (f"⏰ {join_names([mention(p) for p in missing])}, todavía no habéis registrado "
                f"la quiniela de la jornada {number}. El plazo cierra el {when(close)}.")
    return (f"⏰ {mention(missing[0])}, todavía no has registrado la quiniela de la jornada "
            f"{number}. El plazo cierra el {when(close)}.")


def schedule_change(number: int, new_first_kickoff: datetime) -> str:
    return (f"⚠️ <b>Cambio de horario en la jornada {number}</b>\n"
            f"El primer partido pasa a ser el {when(new_first_kickoff)}, así que el plazo para "
            f"registrar la columna cierra ahora el {when(new_first_kickoff - timedelta(hours=24))}.")


def picks_published(number: int, payments: list[dict], players: dict[int, dict], missing: list[dict]) -> str:
    lines = [f"📋 <b>Aquí están las apuestas de la jornada {number}</b>"]
    if payments:
        lines.append(payment(payments, players, "abonarla") + ".")
    if missing:
        lines.append(f"Sin columna: {join_names([p['name'] for p in missing])} (cuenta como 0 aciertos).")
    return "\n".join(lines)


def daily_report(number: int, day: date, played: int, leaders: list[str], best: int,
                 unresolved: list[dict]) -> str:
    text = (f"📊 <b>Así va la jornada {number} tras el {weekday(day)}</b>\n"
            f"{played} de 14 partidos jugados. "
            f"Va en cabeza {join_names(leaders)} con {best} aciertos.")
    if unresolved:
        text += ("\n⚠️ Sin resultado todavía (aplazado o pendiente): "
                 + ", ".join(f"{m['home']} - {m['away']}" for m in unresolved) + ".")
    return text


def final_result(number: int, ranking: list[tuple[str, int]], fourteen: list[str], pleno_correct: bool,
                 payments: list[dict], players: dict[int, dict]) -> str:
    lines = [f"🏁 <b>Jornada {number} terminada</b>"]
    lines += [f"{name}: {hits} aciertos" for name, hits in ranking]
    for name in fourteen:
        extra = " ¡y con el pleno al 15!" if pleno_correct else ""
        lines.append(f"🎉 ¡{name} ha acertado los 14{extra}")
    lines.append("")
    lines.append(payment(payments, players, "pagar la próxima quiniela") + ".")
    return "\n".join(lines)


def prizes(number: int, winners: list[tuple[dict, object, Decimal]]) -> str:
    """winners: [(player, category, amount)] where category is a hit count or 'pleno'."""
    lines = [f"💰 <b>Premios de la jornada {number}</b>"]
    for player, category, amount in winners:
        label = "pleno al 15" if category == "pleno" else f"{category} aciertos"
        lines.append(f"{mention(player)} ha tenido {label} por lo que corresponde {eur(amount)} según el sorteo.")
    if len(winners) > 1:
        lines.append(f"\nEn total, {eur(sum((a for _, _, a in winners), Decimal(0)))} para el boleto.")
    return "\n".join(lines)


# ---- image captions ----------------------------------------------------------
def ticket_title(number: int, final: bool = False) -> str:
    return f"Jornada {number}" + (" · final" if final else "")


def ticket_subtitle_deadline(close: datetime) -> str:
    return f"Plazo: {when(close)}"


TICKET_SUBTITLE_PICKS = "Columnas para sellar en la administración"
TICKET_SUBTITLE_FINAL = "Resultado oficial"


def ticket_subtitle_progress(day: date, played: int) -> str:
    return f"Así va tras el {weekday(day)} · {played} de 14 jugados"
