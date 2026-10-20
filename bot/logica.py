"""Reglas de la quiniela familiar. Funciones puras, sin red ni base de datos."""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
PRECIO_BOLETO = Decimal("3.75")
# Margen tras el inicio del último partido del día para dar por terminado
# el día si Loterías no publica la hora final (90' + descanso + añadido).
DURACION_PARTIDO = timedelta(hours=2)


# Equipos de Primera División 2026-27 tal como los escribe Loterías (sin el "(m)").
# Revisar al empezar cada temporada (ascensos y descensos). No hace falta que esté
# completa: basta con que la mayoría de partidos de Liga se reconozcan.
EQUIPOS_PRIMERA = {
    "Athletic Club", "At. Madrid", "Barcelona", "Betis", "Celta", "Deportivo", "Elche",
    "Espanyol", "Getafe", "Levante", "Osasuna", "Rayo Vallecano", "Real Madrid",
    "Real Sociedad", "Sevilla", "Valencia", "Villarreal",
}
MIN_PARTIDOS_PRIMERA = 5


def _norm(nombre: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", ". ").split())


def es_jornada_de_primera(partidos: list[dict]) -> bool:
    """La quiniela de Liga lleva varios partidos de Primera entre sí. Las de Champions,
    Copa, etc. tienen como mucho un equipo de Primera por partido."""
    primera = {_norm(e) for e in EQUIPOS_PRIMERA}
    n = sum(1 for p in partidos if p["posicion"] <= 14
            and _norm(p["local"]) in primera and _norm(p["visitante"]) in primera)
    return n >= MIN_PARTIDOS_PRIMERA


def signo_de_marcador(goles_local: int, goles_visitante: int) -> str:
    if goles_local > goles_visitante:
        return "1"
    if goles_local < goles_visitante:
        return "2"
    return "X"


def goles_pleno(goles: int) -> str:
    return "M" if goles >= 3 else str(goles)


def contar_aciertos(signos: str, resultados: dict[int, str]) -> int:
    """resultados: {posicion (1-14): '1'|'X'|'2'} solo de los partidos ya jugados."""
    return sum(1 for pos, s in resultados.items() if pos <= 14 and signos[pos - 1] == s)


def pleno_acertado(pleno: str | None, goles_local: int | None, goles_visitante: int | None) -> bool | None:
    if pleno is None or goles_local is None or goles_visitante is None:
        return None
    return pleno == f"{goles_pleno(goles_local)}-{goles_pleno(goles_visitante)}"


def ranking(apuestas: dict[int, str], resultados: dict[int, str]) -> dict[int, int]:
    """{jugador_id: aciertos}"""
    return {j: contar_aciertos(s, resultados) for j, s in apuestas.items()}


def quienes_pagan(aciertos: dict[int, int]) -> dict[int, Decimal]:
    """El que menos aciertos saca paga el boleto siguiente; si hay empate, se reparte.
    Los céntimos que no cuadran se los queda el primero de la lista (redondeo a 0,01)."""
    minimo = min(aciertos.values())
    perdedores = sorted(j for j, a in aciertos.items() if a == minimo)
    parte = (PRECIO_BOLETO / len(perdedores)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    importes = {j: parte for j in perdedores}
    importes[perdedores[0]] += PRECIO_BOLETO - parte * len(perdedores)
    return importes


def elegir_pleno(aciertos_anterior: dict[int, int] | None, jugadores: list[int],
                 rng: random.Random | None = None) -> tuple[int, bool]:
    """Devuelve (jugador_id, por_sorteo). Le toca al que más acertó en la jornada anterior;
    si hay empate o no hay jornada anterior, se sortea."""
    rng = rng or random.Random()
    if not aciertos_anterior:
        return rng.choice(sorted(jugadores)), True
    maximo = max(aciertos_anterior.values())
    candidatos = sorted(j for j, a in aciertos_anterior.items() if a == maximo)
    if len(candidatos) == 1:
        return candidatos[0], False
    return rng.choice(candidatos), True


def dias_de_partido(partidos: list[dict]) -> dict[date, list[int]]:
    """Agrupa las posiciones 1-15 por día (hora de Madrid)."""
    dias: dict[date, list[int]] = defaultdict(list)
    for p in partidos:
        if p.get("inicio"):
            dias[_dt(p["inicio"]).astimezone(MADRID).date()].append(p["posicion"])
    return dict(sorted(dias.items()))


def dias_con_parte_pendiente(partidos: list[dict], ya_enviados: set[date], ahora: datetime) -> list[date]:
    """Días cuyos partidos tienen ya todos resultado oficial y aún no se ha mandado el parte.
    El último día de la jornada no lleva parte diario: lo cubre el resultado final."""
    por_pos = {p["posicion"]: p for p in partidos}
    dias = dias_de_partido(partidos)
    if not dias:
        return []
    ultimo_dia = max(dias)
    pendientes = []
    for dia, posiciones in dias.items():
        if dia == ultimo_dia or dia in ya_enviados:
            continue
        todos_jugados = all(por_pos[pos].get("signo") for pos in posiciones if pos <= 14)
        ultimo_inicio = max(_dt(por_pos[pos]["inicio"]) for pos in posiciones)
        if todos_jugados and ahora >= ultimo_inicio + DURACION_PARTIDO:
            pendientes.append(dia)
    return pendientes


def jornada_completa(partidos: list[dict]) -> bool:
    return all(p.get("signo") for p in partidos if p["posicion"] <= 14)


def eur(importe: Decimal) -> str:
    return f"{importe:.2f}".replace(".", ",") + " €"


def nombre_dia(d: date) -> str:
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return dias[d.weekday()]


def _dt(valor) -> datetime:
    if isinstance(valor, datetime):
        return valor
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
