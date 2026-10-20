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
from . import equipos as equipos_mod
from .imagen import boleto
from .logica import (MADRID, dias_con_parte_pendiente, es_jornada_de_primera, eur, elegir_pleno,
                     jornada_completa,
                     nombre_dia, pleno_acertado, premios_columnas, quienes_pagan, ranking, _dt)
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
        self.buscar_jornadas_nuevas()
        abiertas = self.db.jornadas_abiertas()
        if not abiertas:
            print("No hay jornadas abiertas.")
        for jo in abiertas:
            self.procesar(jo)
        for jo in self.db.jornadas_sin_premios():
            self.publicar_premios(jo)

    def buscar_jornadas_nuevas(self):
        equipos = None
        for nueva in self.fuente.proximas_jornadas():
            if not nueva["numero"] or not nueva["primer_partido"]:
                continue
            temporada = temporada_de(nueva["fecha_sorteo"] or nueva["primer_partido"])
            if self.db.jornada_existe(temporada, nueva["numero"]):
                continue
            if equipos is None:     # se descarga solo si hay alguna jornada nueva que mirar
                equipos = self.equipos_primera()
            if not es_jornada_de_primera(nueva["partidos"], equipos):
                print(f"Jornada {nueva['numero']}: no es de Liga (Copa, Champions…); se omite.")
                continue
            if nueva["primer_partido"] - timedelta(hours=24) <= self.ahora:
                print(f"Jornada {nueva['numero']}: publicada demasiado tarde para apostar; se omite.")
                continue
            self.crear_jornada(nueva, temporada)

    def equipos_primera(self) -> list[str]:
        lista = equipos_mod.de_espn()
        print(f"Equipos de Primera: {'ESPN' if lista else 'lista de respaldo'}")
        return lista

    def crear_jornada(self, nueva: dict, temporada: str) -> dict:
        # Pleno: el que más acertó en la jornada anterior. Si esa aún se está jugando
        # (jornadas solapadas), se usa la última terminada.
        anterior = self.db.jornada_anterior(nueva["primer_partido"])
        if anterior and not anterior["finalizada"]:
            anterior = self.db.ultima_finalizada()
        aciertos_ant = self.aciertos_de(anterior) if anterior else None
        pleno_id, por_sorteo = elegir_pleno(aciertos_ant, list(self.jug), self.rng)

        partidos = [{k: p[k] for k in ("posicion", "local", "visitante", "inicio")}
                    for p in nueva["partidos"]]
        for p in partidos:          # los resultados se cargan después, al jugarse
            p.update(goles_local=None, goles_visitante=None, signo=None)
        return self.db.crear_jornada({
            "temporada": temporada, "numero": nueva["numero"], "fecha_sorteo": nueva["fecha_sorteo"],
            "id_sorteo_lae": nueva["id_sorteo"], "primer_partido": nueva["primer_partido"],
            "pleno_jugador_id": pleno_id, "pleno_por_sorteo": por_sorteo,
        }, partidos)

    def pagos_anterior(self, jo: dict) -> list[dict]:
        """Quién paga esta jornada: los perdedores de la inmediatamente anterior, si ya acabó."""
        anterior = self.db.jornada_anterior(jo["primer_partido"])
        if anterior and anterior["finalizada"]:
            return self.db.pagos(anterior["id"])
        return []

    # ------------------------------------------------------------------
    def procesar(self, jo: dict):
        primer = _dt(jo["primer_partido"])
        cierre = primer - timedelta(hours=24)
        aviso = primer - timedelta(hours=48)

        if self.ahora < primer:
            self.refrescar_partidos(jo)

        if not jo["anunciada"]:
            self.anunciar(jo, cierre)
            self.db.actualizar_jornada(jo["id"], anunciada=True)

        apuestas = self.db.apuestas(jo["id"])
        faltan = [j for j in self.jugadores if j["id"] not in apuestas]

        if not jo["recordatorio_enviado"] and aviso <= self.ahora < cierre:
            anunciada_tarde = jo.get("created_at") and _dt(jo["created_at"]) > aviso - timedelta(hours=6)
            if faltan and not anunciada_tarde:
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

    def refrescar_partidos(self, jo: dict):
        """Antes de empezar, actualiza nombres y horarios por si Loterías los cambia."""
        try:
            ps = self.fuente.partidos(jo["id_sorteo_lae"], _fecha_iso(jo.get("fecha_sorteo")))
        except Exception as e:  # noqa: BLE001
            print(f"No se pudieron refrescar los partidos de la jornada {jo['numero']}: {e}")
            return
        if ps:
            self.db.guardar_resultados(jo["id"], [{**p, "goles_local": None, "goles_visitante": None,
                                                   "signo": None} for p in ps])

    def anunciar(self, jo: dict, cierre):
        partidos = self.db.partidos(jo["id"])
        lineas = [f"⚽ <b>Ya está la quiniela de la jornada {jo['numero']}</b>",
                  f"Tenéis hasta el {fmt_fecha(cierre)} para registrar vuestra columna."]
        if jo["pleno_jugador_id"]:
            if not jo["pleno_por_sorteo"]:
                motivo = "por ser quien más acertó"
            elif self.db.ultima_finalizada():
                motivo = "sorteado entre los que más acertaron"
            else:
                motivo = "por sorteo"
            lineas.append(f"El pleno al 15 lo rellena {mencion(self.jug[jo['pleno_jugador_id']])} ({motivo}).")
        if pagos := self.pagos_anterior(jo):
            lineas.append(texto_pago(pagos, self.jug, "abonarla") + ".")
        png = boleto(titulo=f"Jornada {jo['numero']}", subtitulo=f"Plazo: {fmt_fecha(cierre)}",
                     partidos=partidos, jugadores=[], apuestas={}, pleno_jugador_id=None,
                     mostrar_resultados=False)
        self.tg.foto(png, "\n".join(lineas), BOTON, WEB_URL)

    def publicar_apuestas(self, jo: dict, apuestas: dict, faltan: list[dict]):
        partidos = self.db.partidos(jo["id"])
        lineas = [f"📋 <b>Aquí están las apuestas de la jornada {jo['numero']}</b>"]
        if pagos := self.pagos_anterior(jo):
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
        print(f"Jornada {jo['numero']}: {sum(1 for p in nuevos or [] if p['signo'])} de 15 resultados en Loterías")
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
        pj = jo["pleno_jugador_id"]
        pleno_ok = self.pleno_ok(jo, partidos, apuestas)
        for j, a in orden:
            if a == 14:
                extra = " ¡y con el pleno al 15!" if pleno_ok else ""
                lineas.append(f"🎉 ¡{self.jug[j]['nombre']} ha acertado los 14{extra}")
        lineas.append("")
        lineas.append(texto_pago(pagos, self.jug, "pagar la próxima quiniela") + ".")

        png = boleto(titulo=f"Jornada {jo['numero']} · final", subtitulo="Resultado oficial",
                     partidos=partidos, jugadores=self.jugadores, apuestas=apuestas,
                     pleno_jugador_id=pj, mostrar_resultados=True)
        self.tg.foto(png, "\n".join(lineas))
        self.db.actualizar_jornada(jo["id"], finalizada=True)

    def pleno_ok(self, jo: dict, partidos: list[dict], apuestas: dict) -> bool:
        """El pleno al 15 es uno por boleto (lo rellena quien toca) y vale para todas las columnas."""
        p15 = next((p for p in partidos if p["posicion"] == 15), None)
        pleno = (apuestas.get(jo["pleno_jugador_id"]) or {}).get("pleno")
        return bool(p15 and pleno_acertado(pleno, p15.get("goles_local"), p15.get("goles_visitante")))

    def publicar_premios(self, jo: dict):
        """Cuando Loterías publica el escrutinio, dice qué columnas han cobrado y cuánto."""
        escr = self.fuente.escrutinio(jo["id_sorteo_lae"], _fecha_iso(jo.get("fecha_sorteo")))
        if not escr:
            if self.ahora - _dt(jo["primer_partido"]) > timedelta(days=10):
                self.db.actualizar_jornada(jo["id"], premios_publicados=True)   # no va a llegar
            return
        partidos = self.db.partidos(jo["id"])
        apuestas = self.db.apuestas(jo["id"])
        res = {p["posicion"]: p["signo"] for p in partidos if p["posicion"] <= 14}
        aciertos = self.aciertos_completos(apuestas, res)
        premios = premios_columnas(aciertos, self.pleno_ok(jo, partidos, apuestas), escr)
        if premios:
            lineas = [f"💰 <b>Premios de la jornada {jo['numero']}</b>"]
            total = Decimal(0)
            for j, conceptos in sorted(premios.items(), key=lambda x: -aciertos[x[0]]):
                for concepto, imp in conceptos:
                    lineas.append(f"{mencion(self.jug[j])} ha tenido {concepto} por lo que corresponde "
                                  f"{eur(imp)} según el sorteo.")
                    total += imp
            if len(lineas) > 2:
                lineas.append(f"\nEn total, {eur(total)} para el boleto.")
            self.tg.mensaje("\n".join(lineas))
        self.db.actualizar_jornada(jo["id"], premios_publicados=True)

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
    """Muestra cómo interpreta el bot las próximas jornadas y los resultados de Loterías."""
    eq = equipos_mod.de_espn()
    print(f"=== Equipos de Primera según ESPN ({len(eq)} nombres) ===\n{eq}\n")
    print("=== Próximas jornadas (proximosv3) ===")
    print(json.dumps(lae_mod.proximos_crudo(), ensure_ascii=False))
    for j in lae_mod.proximas_jornadas():
        print(f"\n=== Jornada {j['numero']} · sorteo {j['id_sorteo']} · {j['fecha_sorteo']} ===")
        print(f"Primer partido: {j['primer_partido']}  ·  cierre oficial: {j['cierre_oficial']}")
        print(f"Plazo familiar (24 h antes): {j['primer_partido'] - timedelta(hours=24)}")
        print(f"¿Es de Liga (Primera)?: {'sí' if es_jornada_de_primera(j['partidos'], eq or None) else 'NO, se omite'}")
        for p in lae_mod.resultados(j["id_sorteo"], j["fecha_sorteo"]) or j["partidos"]:
            res = f"{p['goles_local']}-{p['goles_visitante']} ({p['signo']})" if p["signo"] else ""
            print(f"  {p['posicion']:>2}. {p['inicio']:%a %d %H:%M}  {p['local']} - {p['visitante']}  {res}")
    print("\n=== Escrutinio de las jornadas terminadas en los últimos 10 días ===")
    hoy = datetime.now(MADRID).date()
    for s_ in lae_mod._lista(lae_mod.celebrados_crudo(hoy - timedelta(days=10), hoy)):
        print(f"Jornada {s_.get('numero')} ({s_.get('id_sorteo')}):",
              lae_mod.escrutinio(s_["id_sorteo"], lae_mod._fecha(s_.get("fecha_sorteo")).date()))
    print("\n=== Ficha cruda de la primera jornada (fechav3) ===")
    pj = lae_mod.proximas_jornadas()
    if pj:
        print(json.dumps(lae_mod.fecha_crudo(pj[0]["fecha_sorteo"]), ensure_ascii=False)[:4000])


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
