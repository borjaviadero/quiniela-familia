"""Acceso a Supabase vía su API REST (PostgREST) con la service_role key."""
from __future__ import annotations

import json
import os
from datetime import date, datetime

import requests


class DB:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/") + "/rest/v1"
        key = key or os.environ["SUPABASE_SERVICE_KEY"]
        self.h = {"apikey": key, "Authorization": f"Bearer {key}",
                  "Content-Type": "application/json", "Prefer": "return=representation"}

    def _req(self, metodo: str, tabla: str, params: dict | None = None, cuerpo=None, prefer: str | None = None):
        h = dict(self.h)
        if prefer:
            h["Prefer"] = prefer
        r = requests.request(metodo, f"{self.url}/{tabla}", params=params, headers=h, timeout=30,
                             data=json.dumps(cuerpo, default=_json) if cuerpo is not None else None)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {metodo} {tabla}: {r.status_code} {r.text}")
        return r.json() if r.text else None

    # ---- lecturas ----------------------------------------------------
    def jugadores(self) -> list[dict]:
        return self._req("GET", "jugadores", {"select": "id,nombre,telegram_user_id", "order": "id"})

    def jornadas_abiertas(self) -> list[dict]:
        return self._req("GET", "jornadas", {"finalizada": "eq.false", "order": "primer_partido.asc"})

    def jornada_anterior(self, primer_partido) -> dict | None:
        """La jornada inmediatamente anterior (por fecha del primer partido), acabada o no."""
        r = self._req("GET", "jornadas", {"primer_partido": f"lt.{_json(primer_partido)}",
                                          "order": "primer_partido.desc", "limit": 1})
        return r[0] if r else None

    def ultima_finalizada(self) -> dict | None:
        r = self._req("GET", "jornadas", {"finalizada": "eq.true", "order": "primer_partido.desc", "limit": 1})
        return r[0] if r else None

    def jornada_existe(self, temporada: str, numero: int) -> bool:
        return bool(self._req("GET", "jornadas", {"temporada": f"eq.{temporada}", "numero": f"eq.{numero}",
                                                  "select": "id"}))

    def partidos(self, jornada_id: int) -> list[dict]:
        return self._req("GET", "partidos", {"jornada_id": f"eq.{jornada_id}", "order": "posicion"})

    def apuestas(self, jornada_id: int) -> dict[int, dict]:
        filas = self._req("GET", "apuestas", {"jornada_id": f"eq.{jornada_id}"})
        return {f["jugador_id"]: f for f in filas}

    def partes_enviados(self, jornada_id: int) -> set[date]:
        filas = self._req("GET", "partes_diarios", {"jornada_id": f"eq.{jornada_id}", "select": "dia"})
        return {date.fromisoformat(f["dia"]) for f in filas}

    def pagos(self, jornada_id: int) -> list[dict]:
        return self._req("GET", "pagos", {"jornada_id": f"eq.{jornada_id}", "order": "jugador_id"})

    # ---- escrituras --------------------------------------------------
    def crear_jornada(self, jornada: dict, partidos: list[dict]) -> dict:
        j = self._req("POST", "jornadas", cuerpo=jornada)[0]
        self._req("POST", "partidos", cuerpo=[{**p, "jornada_id": j["id"]} for p in partidos])
        return j

    def actualizar_jornada(self, jornada_id: int, **campos):
        self._req("PATCH", "jornadas", {"id": f"eq.{jornada_id}"}, campos)

    def guardar_resultados(self, jornada_id: int, partidos: list[dict]):
        filas = [{"jornada_id": jornada_id, **{k: p[k] for k in
                  ("posicion", "local", "visitante", "inicio", "goles_local", "goles_visitante", "signo")}}
                 for p in partidos]
        self._req("POST", "partidos", {"on_conflict": "jornada_id,posicion"}, filas,
                  prefer="resolution=merge-duplicates,return=minimal")

    def registrar_parte(self, jornada_id: int, dia: date):
        self._req("POST", "partes_diarios", cuerpo={"jornada_id": jornada_id, "dia": dia.isoformat()},
                  prefer="return=minimal")

    def guardar_pagos(self, jornada_id: int, importes: dict[int, object]):
        self._req("POST", "pagos", {"on_conflict": "jornada_id,jugador_id"},
                  [{"jornada_id": jornada_id, "jugador_id": j, "importe": str(v)} for j, v in importes.items()],
                  prefer="resolution=merge-duplicates,return=minimal")


def _json(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)
