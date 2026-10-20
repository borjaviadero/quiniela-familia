"""Bot de la quiniela familiar.

Uso:
  python -m bot.main tick              # lo que ejecuta GitHub Actions cada rato
  python -m bot.main tick --dry-run X  # igual, pero guarda fotos y textos en la carpeta X
  python -m bot.main diagnostico-lae   # vuelca lo que devuelve Loterías
  python -m bot.main telegram-ids      # muestra el chat_id del grupo y los user_id

`tick` es idempotente: cada mensaje lleva una bandera en la base de datos y
solo se envía una vez, así que da igual cuántas veces se ejecute.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from . import lae as lae_mod
from .db import DB
from .imagen import boleto
from .logica import (MADRID, dias_con_parte_pendiente, eur, elegir_pleno, jornada_completa,
                     nombre_dia, pleno_acertado, quienes_pagan, ranking, _dt)
from .telegram import Telegram, mencion

WEB_URL = os.environ.get("WEB_URL", "")
BOTON = "Hacer mi quiniela"


def temporada_de(fecha) -> str:
    return f"{fecha.year}-{fecha.year + 1}" if fecha.month >= 7 else f"{fecha.year - 1}-{fecha.year}"


def fmt_fecha(dt) -> str:
    dt = _dt(dt).astimezone(MADRID)
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{nombre_dia(dt.date())} {dt.day} de {meses[dt.month - 1]} a las {dt:%H:%M}"


def lista_nombres(nombres: list[str]) -> str:
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]


def texto_pago(pagos: list[dict], jug: dict[int, dict], verbo: str) -> str:
    """'Joseba debe abonarla con 3,75 €' / 'Joseba (1,87 €) y Lara (1,88 €) deben abonarla'."""
    importes = [Decimal(str(p["importe"])) for p in pagos]
    nombres = [mencion(jug[p["jugador_id"]]) for p in pagos]
    if len(pagos) == 1:
        return f"{nombres[0]} debe {verbo} con {eur(importes[0])}"
    if len(set(importes)) == 1:
        return f"{lista_nombres(nombres)} deben {verbo} con {eur(importes[0])} cada uno"
    partes = [f"{n} ({eur(i)})" for n, i in zip(nombres, importes)]
    return f"{lista_nombres(partes)} deben {verbo}"


class Bot:
    def __init__(self, db: DB, tg: Telegram, fuente=lae_mod, ahora: datetime | None = None,
                 rng: random.Random | None = None):
        self.db, self.tg, self.fuente = db, tg, fuente
        self.ahora = ahora or datetime.now(MADRID)
        self.rng = rng or random.Random()
        self.jugadores = db.jugadores()
        self.jug = {j["id"]: j for j in self.jugadores}

    # ------------------------------------------------------------------
    def tick(self):
        jornada = self.db.jornada_abierta()
        if jornada is None:
            jornada = self.buscar_jornada_nueva()
            if jornada is None:
                print("No hay jornada abierta ni nueva publicada.")
                return
        self.procesar(jornada)

    def buscar_jornada_nueva(self) -> dict | None:
        nueva = self.fuente.proxima_jornada()
        if not nueva or not nueva["numero"] or not nueva["primer_partido"]:
            return None
        temporada = temporada_de(nueva["fecha_sorteo"] or nueva["primer_partido"])
        if self.db.jornada_existe(temporada, nueva["numero"]):
            return None
        if nueva["primer_partido"] - timedelta(hours=24) <= self.ahora:
            print(f"Jornada {nueva['numero']} publicada demasiado tarde para apostar; se omite.")
            return None

        anterior = self.db.ultima_finalizada()
        aciertos_ant = self.aciertos_de(anterior) if anterior else None
        pleno_id, por_sorteo = elegir_pleno(aciertos_ant, list(self.jug), self.rng)

        partidos = [{k: p[k] for k in ("posicion", "local", "visitante", "inicio",
                                       "goles_local", "goles_visitante", "signo")}
                    for p in nueva["partidos"]]
        for p in partidos:          # resultados que pudieran venir de antes no cuentan
            p.update(goles_local=None, goles_visitante=None, signo=None)
        return self.db.crear_jornada({
            "temporada": temporada, "numero": nueva["numero"], "fecha_sorteo": nueva["fecha_sorteo"],
            "id_sorteo_lae": nueva["id_sorteo"], "primer_partido": nueva["primer_partido"],
            "pleno_jugador_id": pleno_id, "pleno_por_sorteo": por_sorteo,
        }, partidos)

    # ------------------------------------------------------------------
    def procesar(self, jo: dict):
        primer = _dt(jo["primer_partido"])
        cierre = primer - timedelta(hours=24)
        aviso = primer - timedelta(hours=48)

        if not jo["anunciada"]:
            self.anunciar(jo, cierre)
            self.db.actualizar_jornada(jo["id"], anunciada=True)

        apuestas = self.db.apuestas(jo["id"])
        faltan = [j for j in self.jugadores if j["id"] not in apuestas]

        if not jo["recordatorio_enviado"] and aviso <= self.ahora < cierre:
            if faltan:
                self.tg.mensaje(
                    f"⏰ {lista_nombres([mencion(j) for j in faltan])}, todavía no habéis registrado "
                    f"la quiniela de la jornada {jo['numero']}. El plazo cierra el {fmt_fecha(cierre)}."
                    if len(faltan) > 1 else
                    f"⏰ {mencion(faltan[0])}, todavía no has registrado la quiniela de la jornada "
                    f"{jo['numero']}. El plazo cierra el {fmt_fecha(cierre)}.",
                    BOTON, WEB_URL)
            self.db.actualizar_jornada(jo["id"], recordatorio_enviado=True)

        if not jo["apuestas_publicadas"] and (not faltan or self.ahora >= cierre):
            self.publicar_apuestas(jo, apuestas, faltan)
            self.db.actualizar_jornada(jo["id"], apuestas_publicadas=True)

        if self.ahora >= primer:
            self.seguir_resultados(jo, apuestas)

    def anunciar(self, jo: dict, cierre):
        partidos = self.db.partidos(jo["id"])
        lineas = [f"⚽ <b>Ya está la quiniela de la jornada {jo['numero']}</b>",
                  f"Tenéis hasta el {fmt_fecha(cierre)} para registrar vuestra columna."]
        if jo["pleno_jugador_id"]:
            if not jo["pleno_por_sorteo"]:
                motivo = "por ser quien más acertó la jornada pasada"
            elif self.db.ultima_finalizada():
                motivo = "sorteado entre los que más acertaron la jornada pasada"
            else:
                motivo = "por sorteo"
            lineas.append(f"El pleno al 15 lo rellena {mencion(self.jug[jo['pleno_jugador_id']])} ({motivo}).")
        anterior = self.db.ultima_finalizada()
        if anterior and (pagos := self.db.pagos(anterior["id"])):
            lineas.append(texto_pago(pagos, self.jug, "abonarla") + ".")
        png = boleto(titulo=f"Jornada {jo['numero']}", subtitulo=f"Plazo: {fmt_fecha(cierre)}",
                     partidos=partidos, jugadores=[], apuestas={}, pleno_jugador_id=None,
                     mostrar_resultados=False)
        self.tg.foto(png, "\n".join(lineas), BOTON, WEB_URL)

    def publicar_apuestas(self, jo: dict, apuestas: dict, faltan: list[dict]):
        partidos = self.db.partidos(jo["id"])
        lineas = [f"📋 <b>Aquí están las apuestas de la jornada {jo['numero']}</b>"]
        anterior = self.db.ultima_finalizada()
        if anterior and (pagos := self.db.pagos(anterior["id"])):
            lineas.append(texto_pago(pagos, self.jug, "abonarla") + ".")
        if faltan:
            lineas.append(f"Sin columna: {lista_nombres([j['nombre'] for j in faltan])} (cuenta como 0 aciertos).")
        png = boleto(titulo=f"Jornada {jo['numero']}", subtitulo="Columnas para sellar en la administración",
                     partidos=partidos, jugadores=self.jugadores, apuestas=apuestas,
                     pleno_jugador_id=jo["pleno_jugador_id"], mostrar_resultados=False)
        self.tg.foto(png, "\n".join(lineas))

    # ------------------------------------------------------------------
    def seguir_resultados(self, jo: dict, apuestas: dict):
        nuevos = self.fuente.resultados(jo["id_sorteo_lae"], _fecha_iso(jo.get("fecha_sorteo")))
        if nuevos:
            self.db.guardar_resultados(jo["id"], nuevos)
        partidos = self.db.partidos(jo["id"])

        enviados = self.db.partes_enviados(jo["id"])
        for dia in dias_con_parte_pendiente(partidos, enviados, self.ahora):
            jugados = [p for p in partidos if p.get("signo") and _dt(p["inicio"]).astimezone(MADRID).date() <= dia]
            res = {p["posicion"]: p["signo"] for p in jugados}
            aciertos = self.aciertos_completos(apuestas, res)
            pie = [f"{self.jug[j]['nombre']}: {a}" for j, a in sorted(aciertos.items(), key=lambda x: -x[1])]
            visibles = [{**p, "signo": p["signo"] if p in jugados else None} for p in partidos]
            png = boleto(titulo=f"Jornada {jo['numero']}",
                         subtitulo=f"Así va tras el {nombre_dia(dia)} · {len(jugados)} de 14 jugados",
                         partidos=visibles, jugadores=self.jugadores, apuestas=apuestas,
                         pleno_jugador_id=jo["pleno_jugador_id"], mostrar_resultados=True)
            lider = max(aciertos.values())
            lideres = [self.jug[j]["nombre"] for j, a in aciertos.items() if a == lider]
            self.tg.foto(png, f"📊 <b>Así va la jornada {jo['numero']} tras el {nombre_dia(dia)}</b>\n"
                              f"{len(jugados)} de 14 partidos jugados. "
                              f"Va en cabeza {lista_nombres(lideres)} con {lider} aciertos.")
            self.db.registrar_parte(jo["id"], dia)

        if jornada_completa(partidos):
            self.cerrar_jornada(jo, partidos, apuestas)

    def cerrar_jornada(self, jo: dict, partidos: list[dict], apuestas: dict):
        res = {p["posicion"]: p["signo"] for p in partidos if p["posicion"] <= 14}
        aciertos = self.aciertos_completos(apuestas, res)
        importes = quienes_pagan(aciertos)
        self.db.guardar_pagos(jo["id"], importes)
        pagos = [{"jugador_id": j, "importe": v} for j, v in sorted(importes.items())]

        orden = sorted(aciertos.items(), key=lambda x: -x[1])
        lineas = [f"🏁 <b>Jornada {jo['numero']} terminada</b>"]
        lineas += [f"{self.jug[j]['nombre']}: {a} aciertos" for j, a in orden]
        p15 = next((p for p in partidos if p["posicion"] == 15), None)
        pj = jo["pleno_jugador_id"]
        if pj and aciertos.get(pj) == 14 and p15 and \
                pleno_acertado((apuestas.get(pj) or {}).get("pleno"), p15.get("goles_local"), p15.get("goles_visitante")):
            lineas.append(f"🎉 ¡{self.jug[pj]['nombre']} ha hecho pleno al 15!")
        lineas.append("")
        lineas.append(texto_pago(pagos, self.jug, "pagar la próxima quiniela") + ".")

        png = boleto(titulo=f"Jornada {jo['numero']} · final", subtitulo="Resultado oficial",
                     partidos=partidos, jugadores=self.jugadores, apuestas=apuestas,
                     pleno_jugador_id=pj, mostrar_resultados=True)
        self.tg.foto(png, "\n".join(lineas))
        self.db.actualizar_jornada(jo["id"], finalizada=True)

    # ------------------------------------------------------------------
    def aciertos_completos(self, apuestas: dict, res: dict) -> dict[int, int]:
        """Quien no tiene columna cuenta con 0 aciertos."""
        base = ranking({j: a["signos"] for j, a in apuestas.items()}, res)
        return {j: base.get(j, 0) for j in self.jug}

    def aciertos_de(self, jornada: dict) -> dict[int, int]:
        partidos = self.db.partidos(jornada["id"])
        res = {p["posicion"]: p["signo"] for p in partidos if p.get("signo") and p["posicion"] <= 14}
        return self.aciertos_completos(self.db.apuestas(jornada["id"]), res)


def _fecha_iso(v):
    from datetime import date
    return date.fromisoformat(v) if isinstance(v, str) else v


def diagnostico_lae():
    """Prueba varias direcciones de Loterías y vuelca lo que devuelven."""
    from datetime import date as _date
    base = "https://www.loteriasyapuestas.es"
    hoy = datetime.now(MADRID).date()
    f = lambda d: d.strftime("%Y%m%d")  # noqa: E731
    urls = [f"{base}/servicios/proximosv3?game_id=LAQU&num=2"]
    codigo, texto = lae_mod.probar(urls[0])
    print(f"### {codigo}  {urls[0]}\n{texto}\n")
    sorteos = []
    try:
        sorteos = json.loads(texto)
    except Exception:  # noqa: BLE001
        pass
    for s in sorteos if isinstance(sorteos, list) else []:
        ids, fecha = s.get("id_sorteo"), str(s.get("fecha", ""))[:10].replace("-", "")
        urls += [
            f"{base}/servicios/fechav3?game_id=LAQU&fecha_sorteo={fecha}",
            f"{base}/servicios/buscadorSorteos?game_id=LAQU&celebrados=false&fechaInicioInclusiva={fecha}&fechaFinInclusiva={fecha}",
            f"{base}/servicios/infoSorteo?id_sorteo={ids}",
            f"{base}/servicios/sorteo?id_sorteo={ids}",
            f"{base}/servicios/partidos?id_sorteo={ids}",
            f"{base}/servicios/partidosQuiniela?id_sorteo={ids}",
        ]
    urls += [
        f"{base}/servicios/buscadorSorteos?game_id=LAQU&celebrados=true"
        f"&fechaInicioInclusiva={f(hoy - timedelta(days=10))}&fechaFinInclusiva={f(hoy)}",
        f"{base}/es/la-quiniela",
    ]
    for u in urls[1:]:
        codigo, texto = lae_mod.probar(u)
        if u.endswith("/es/la-quiniela"):   # página HTML: solo enseñamos si menciona partidos
            texto = f"(HTML, {len(texto)} car.) contiene 'partido': {'partido' in texto.lower()}"
        print(f"### {codigo}  {u}\n{texto}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--dry-run", help="carpeta donde guardar fotos y textos en vez de enviarlos")
    sub.add_parser("diagnostico-lae")
    sub.add_parser("telegram-ids")
    a = ap.parse_args(argv)

    if a.cmd == "tick":
        Bot(DB(), Telegram(dry_run=a.dry_run)).tick()
    elif a.cmd == "diagnostico-lae":
        diagnostico_lae()
    elif a.cmd == "telegram-ids":
        for u in Telegram().actualizaciones():
            m = u.get("message") or u.get("my_chat_member") or {}
            chat, de = m.get("chat", {}), m.get("from", {})
            print(f"chat_id={chat.get('id')} ({chat.get('title') or chat.get('type')})  "
                  f"user_id={de.get('id')} ({de.get('first_name')})")


if __name__ == "__main__":
    sys.exit(main())
