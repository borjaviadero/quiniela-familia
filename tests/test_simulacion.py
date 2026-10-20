"""Simula una jornada entera en memoria (sin Supabase, Telegram ni Loterías).

python -m tests.test_simulacion [carpeta_salida]
"""
from __future__ import annotations

import random
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal

from bot.logica import MADRID, quienes_pagan, elegir_pleno
from bot.main import Bot
from bot.telegram import Telegram

EQUIPOS = [("Athletic Club", "Valencia"), ("Real Madrid", "Sevilla"), ("Betis", "Barcelona"), ("Osasuna", "Getafe"),
           ("Villarreal", "Alavés"), ("Celta", "Mallorca"), ("Girona", "Rayo Vallecano"), ("At. Madrid", "Real Sociedad"),
           ("Espanyol", "Levante"), ("Elche", "Oviedo"), ("Racing", "Deportivo"), ("Eibar", "Zaragoza"),
           ("Sporting", "Málaga"), ("Almería", "Granada"), ("Real Sociedad", "Athletic")]
# Sábado 24 oct: 1-7 · Domingo 25 oct: 8-15
SAB = datetime(2026, 10, 24, 14, 0, tzinfo=MADRID)
DOM = datetime(2026, 10, 25, 14, 0, tzinfo=MADRID)
MARCADORES = ["2-1", "1-1", "0-2", "3-0", "1-0", "1-1", "2-2", "1-0", "0-1", "1-1", "2-0", "0-0", "1-2", "3-1", "2-1"]


class FakeFuente:
    def __init__(self):
        self.jugados_hasta: datetime | None = None

    def _partidos(self):
        out = []
        for i, (l, v) in enumerate(EQUIPOS):
            inicio = (SAB if i < 7 else DOM) + timedelta(hours=2 * (i % 4))
            gl = gv = signo = None
            if self.jugados_hasta and inicio + timedelta(hours=2) <= self.jugados_hasta:
                gl, gv = map(int, MARCADORES[i].split("-"))
                signo = "1" if gl > gv else "2" if gl < gv else "X"
            out.append({"posicion": i + 1, "local": l, "visitante": v, "inicio": inicio,
                        "goles_local": gl, "goles_visitante": gv, "signo": signo})
        return out

    def proximas_jornadas(self):
        out = [{"id_sorteo": "LAQU-14", "numero": 14, "fecha_sorteo": date(2026, 10, 25),
                "primer_partido": SAB, "partidos": self._partidos()}]
        if self.con_jornada_15:    # jornada de entre semana, publicada mientras se juega la 14
            martes = datetime(2026, 10, 27, 21, 0, tzinfo=MADRID)
            out.append({"id_sorteo": "LAQU-15", "numero": 15, "fecha_sorteo": date(2026, 10, 28),
                        "primer_partido": martes,
                        "partidos": [{"posicion": i + 1, "local": EQUIPOS[i][1], "visitante": EQUIPOS[i][0],
                                      "inicio": martes + timedelta(hours=24 * (i % 2)), "goles_local": None,
                                      "goles_visitante": None, "signo": None} for i in range(15)]})
            out.append({"id_sorteo": "LAQU-16", "numero": 16, "fecha_sorteo": date(2026, 10, 29),
                        "primer_partido": martes,   # Copa: no es de Liga, el bot la ignora
                        "partidos": [{"posicion": i + 1, "local": f"Cd Pueblo {i}", "visitante": EQUIPOS[i][0],
                                      "inicio": martes, "goles_local": None, "goles_visitante": None,
                                      "signo": None} for i in range(15)]})
        return out

    con_jornada_15 = False

    def escrutinio(self, id_sorteo, fecha):
        if id_sorteo != "LAQU-14" or not self.jugados_hasta or self.jugados_hasta < datetime(2026, 10, 26, tzinfo=MADRID):
            return None
        return {"pleno": Decimal("80778.13"), 14: Decimal("11162.13"), 13: Decimal("165.26"),
                12: Decimal("12.76"), 11: Decimal("2.22"), 10: Decimal("0"), 9: Decimal("0")}

    def partidos(self, id_sorteo, fecha):
        return next((j["partidos"] for j in self.proximas_jornadas() if j["id_sorteo"] == id_sorteo), None)

    def resultados(self, id_sorteo, fecha):
        return self._partidos() if id_sorteo == "LAQU-14" else None


class FakeDB:
    def __init__(self):
        self.js = [{"id": i + 1, "nombre": n, "telegram_user_id": None}
                   for i, n in enumerate(["Luis Angel", "Rosana", "Joseba", "Lara", "Borja"])]
        self.jornadas, self.parts, self.aps, self.partes, self.pag = [], {}, {}, set(), {}
        # Jornada 13 ya cerrada con el sistema antiguo: perdió Joseba; Lara y Borja empataron arriba
        self.jornadas.append({"id": 13, "numero": 13, "finalizada": True, "premios_publicados": True,
                              "primer_partido": SAB - timedelta(days=7)})
        self.parts[13] = [{"posicion": i + 1, "signo": "1"} for i in range(14)]
        self.aps[13] = {1: "1111111111XXXX", 2: "111111111XXXXX", 3: "11111XXXXXXXXX",
                        4: "1111111111111X", 5: "1111111111111X"}
        self.pag[13] = [{"jugador_id": 3, "importe": Decimal("3.75")}]

    def jugadores(self): return deepcopy(self.js)
    def jornadas_sin_premios(self):
        return deepcopy([j for j in self.jornadas if j["finalizada"] and not j.get("premios_publicados")])
    def jornadas_abiertas(self):
        return deepcopy(sorted((j for j in self.jornadas if not j["finalizada"]),
                               key=lambda j: j["primer_partido"]))
    def jornada_anterior(self, primer):
        a = [j for j in self.jornadas if j["primer_partido"] < primer]
        return deepcopy(max(a, key=lambda j: j["primer_partido"])) if a else None
    ahora = None
    def ultima_finalizada(self):
        a = [j for j in self.jornadas if j["finalizada"]]
        return deepcopy(a[-1]) if a else None
    def jornada_existe(self, t, n): return any(j["numero"] == n for j in self.jornadas)
    def partidos(self, jid): return deepcopy(self.parts[jid])
    def apuestas(self, jid): return {j: {"signos": s, "pleno": self.plenos.get((jid, j))}
                                     for j, s in self.aps.get(jid, {}).items()}
    plenos: dict = {}
    def partes_enviados(self, jid): return {d for (j, d) in self.partes if j == jid}
    def pagos(self, jid): return deepcopy(self.pag.get(jid, []))
    def crear_jornada(self, jo, partidos):
        jo = {**jo, "id": jo["numero"], "created_at": self.ahora, "anunciada": False, "recordatorio_enviado": False,
              "apuestas_publicadas": False, "finalizada": False,
              "premios_publicados": False}
        self.jornadas.append(jo); self.parts[jo["id"]] = deepcopy(partidos)
        return deepcopy(jo)
    def actualizar_jornada(self, jid, **c):
        next(j for j in self.jornadas if j["id"] == jid).update(c)
    def guardar_resultados(self, jid, ps): self.parts[jid] = deepcopy(ps)
    def registrar_parte(self, jid, dia): self.partes.add((jid, dia))
    def guardar_pagos(self, jid, imp):
        self.pag[jid] = [{"jugador_id": j, "importe": v} for j, v in sorted(imp.items())]


def main(salida: str):
    import bot.equipos
    bot.equipos.de_espn = lambda: []      # sin red: usa la lista de respaldo
    db, fuente = FakeDB(), FakeFuente()
    tg = Telegram(dry_run=salida)
    rng = random.Random(7)

    def tick(cuando: datetime, etiqueta: str):
        antes = tg.n
        db.ahora = cuando
        Bot(db, tg, fuente, ahora=cuando, rng=rng).tick()
        print(f"{cuando:%a %d %H:%M}  {etiqueta:<38} -> {tg.n - antes} envío(s)")

    tick(datetime(2026, 10, 20, 11, 0, tzinfo=MADRID), "se publica la jornada")
    tick(datetime(2026, 10, 20, 12, 0, tzinfo=MADRID), "otra pasada (no repite)")
    # Apuestan 3 de 5
    db.aps[14] = {1: "11221X111X11X1", 2: "1X2111X11112X1", 4: "112X1X1X1X1121"}
    jo = db.jornadas_abiertas()[0]
    tick(datetime(2026, 10, 22, 14, 30, tzinfo=MADRID), "48 h antes: aviso a quien falta")
    tick(datetime(2026, 10, 22, 16, 0, tzinfo=MADRID), "otra pasada (no repite aviso)")
    db.aps[14][3] = "1X1X1X1X1X1X1X"
    db.aps[14][5] = "11211X1X1X1121"
    db.aps[14][2] = "1X2111X1121X21"   # Rosana: 11 aciertos -> premio de 4ª categoría
    db.plenos[(14, jo["pleno_jugador_id"])] = "2-1"
    tick(datetime(2026, 10, 22, 20, 0, tzinfo=MADRID), "ya están las 5: se publican")
    fuente.jugados_hasta = datetime(2026, 10, 24, 19, 0, tzinfo=MADRID)
    tick(datetime(2026, 10, 24, 19, 0, tzinfo=MADRID), "sábado a medias (no envía)")
    fuente.jugados_hasta = datetime(2026, 10, 25, 0, 30, tzinfo=MADRID)
    tick(datetime(2026, 10, 25, 0, 30, tzinfo=MADRID), "acaba el sábado: parte diario")
    fuente.con_jornada_15 = True
    tick(datetime(2026, 10, 25, 10, 0, tzinfo=MADRID), "sale la 15 (martes) con la 14 en juego")
    fuente.jugados_hasta = datetime(2026, 10, 26, 2, 0, tzinfo=MADRID)
    tick(datetime(2026, 10, 26, 2, 0, tzinfo=MADRID), "acaba el domingo: cierre y pagos")
    tick(datetime(2026, 10, 26, 3, 0, tzinfo=MADRID), "otra pasada (no repite cierre)")
    tick(datetime(2026, 10, 26, 9, 0, tzinfo=MADRID), "lunes: aviso 48 h de la 15")

    # Comprobaciones de reglas
    assert quienes_pagan({1: 5, 2: 3, 3: 3, 4: 9, 5: 7}) == {2: Decimal("1.87"), 3: Decimal("1.88")}
    assert sum(quienes_pagan({1: 2, 2: 2, 3: 2, 4: 9, 5: 7}).values()) == Decimal("3.75")
    assert elegir_pleno({1: 9, 2: 12, 3: 4}, [1, 2, 3]) == (2, False)
    assert elegir_pleno(None, [1, 2, 3, 4, 5], random.Random(1))[1] is True
    j14 = next(j for j in db.jornadas if j["id"] == 14)
    j15 = next(j for j in db.jornadas if j["id"] == 15)
    assert j14["finalizada"] and not j15["finalizada"] and j15["anunciada"]
    assert not any(j["id"] == 16 for j in db.jornadas), "la jornada de Copa no debe crearse"
    print("Pleno jornada 14:", j14["pleno_jugador_id"], "(por sorteo)" if j14["pleno_por_sorteo"] else "")
    print("Pleno jornada 15:", j15["pleno_jugador_id"], "(por sorteo)" if j15["pleno_por_sorteo"] else "")
    print("Pagan la siguiente:", db.pag[14])
    print("OK")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "salida_simulacion")
