"""Lectura de la Quiniela oficial en loteriasyapuestas.es.

Loterías no documenta una API pública: estos son los servicios JSON que usa su
propia web. Su formato puede cambiar, así que el parseo es tolerante (prueba
varios nombres de campo) y `python -m bot.main diagnostico-lae` vuelca la
respuesta cruda para revisarla.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

import requests

from .logica import MADRID, signo_de_marcador

BASE = "https://www.loteriasyapuestas.es/servicios"
CABECERAS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.loteriasyapuestas.es/es/resultados/quiniela",
}


def _get(ruta: str, params: dict) -> Any:
    r = requests.get(f"{BASE}/{ruta}", params=params, headers=CABECERAS, timeout=30)
    r.raise_for_status()
    return r.json()


def proximos_crudo(num: int = 2) -> Any:
    return _get("proximosv3", {"game_id": "LAQU", "num": num})


def celebrados_crudo(desde: date, hasta: date) -> Any:
    return _get("buscadorSorteos", {
        "game_id": "LAQU", "celebrados": "true",
        "fechaInicioInclusiva": desde.strftime("%Y%m%d"),
        "fechaFinInclusiva": hasta.strftime("%Y%m%d"),
    })


# --------------------------------------------------------------------------
#  Normalización
# --------------------------------------------------------------------------

def _primero(d: dict, *claves, defecto=None):
    for c in claves:
        if c in d and d[c] not in (None, ""):
            return d[c]
    return defecto


def _fecha(valor) -> datetime | None:
    """Acepta epoch en ms/s, 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD', 'DD/MM/YYYY HH:MM'."""
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
        n = int(valor)
        return datetime.fromtimestamp(n / 1000 if n > 10**11 else n, MADRID)
    s = str(valor).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=MADRID)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=MADRID)
    except ValueError:
        return None


def _marcador(valor) -> tuple[int | None, int | None]:
    m = re.match(r"\s*(\d+)\s*[-:]\s*(\d+)\s*$", str(valor or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _nombre(valor) -> str:
    """'ATH. CLUB' -> 'Ath. Club' (Loterías suele dar los nombres en mayúsculas)."""
    s = str(valor or "").strip()
    return s.title() if s.isupper() else s


def _partido(i: int, p: dict) -> dict:
    local = _primero(p, "local", "equipo_local", "equipoLocal", "eq_local")
    visitante = _primero(p, "visitante", "equipo_visitante", "equipoVisitante", "eq_visitante")
    if (not local or not visitante) and isinstance(p.get("partido"), str) and "-" in p["partido"]:
        local, visitante = (x.strip() for x in p["partido"].split("-", 1))
    gl, gv = _marcador(_primero(p, "marcador", "resultado_marcador", "goles"))
    if gl is None:
        gl, gv = _primero(p, "goles_local", "golesLocal"), _primero(p, "goles_visitante", "golesVisitante")
        gl = int(gl) if gl is not None else None
        gv = int(gv) if gv is not None else None
    signo = str(_primero(p, "signo", "resultado", defecto="") or "").upper().strip()
    if signo not in ("1", "X", "2"):
        signo = signo_de_marcador(gl, gv) if gl is not None else None
    posicion = int(_primero(p, "posicion", "orden", "numero", "num", defecto=i + 1))
    return {
        "posicion": posicion,
        "local": _nombre(local) or f"Partido {posicion}",
        "visitante": _nombre(visitante),
        "inicio": _fecha(_primero(p, "fecha", "fecha_partido", "fechaPartido", "hora", "inicio")),
        "goles_local": gl,
        "goles_visitante": gv,
        "signo": signo,
    }


def _sorteo(s: dict) -> dict | None:
    partidos = _primero(s, "partidos", "partidosQuiniela", "matches")
    if not isinstance(partidos, list) or len(partidos) < 14:
        return None
    norm = [_partido(i, p) for i, p in enumerate(partidos) if isinstance(p, dict)]
    norm = sorted(norm, key=lambda x: x["posicion"])[:15]
    fecha_sorteo = _fecha(_primero(s, "fecha_sorteo", "fecha", "fechaSorteo"))
    inicios = [p["inicio"] for p in norm if p["inicio"]]
    primer = min(inicios) if inicios else (
        fecha_sorteo.replace(hour=0, minute=0) - timedelta(days=1) if fecha_sorteo else None)
    return {
        "id_sorteo": str(_primero(s, "id_sorteo", "idSorteo", "id", defecto="")),
        "numero": int(_primero(s, "jornada", "num_jornada", "numJornada", defecto=0)),
        "fecha_sorteo": fecha_sorteo.date() if fecha_sorteo else None,
        "primer_partido": primer,
        "partidos": norm,
    }


def _lista(crudo: Any) -> list[dict]:
    if isinstance(crudo, list):
        return [x for x in crudo if isinstance(x, dict)]
    if isinstance(crudo, dict):
        for c in ("sorteos", "proximos", "resultados", "data"):
            if isinstance(crudo.get(c), list):
                return crudo[c]
        return [crudo]
    return []


def proxima_jornada() -> dict | None:
    """La próxima Quiniela con partidos ya publicados (o None si aún no está)."""
    sorteos = [s for s in (_sorteo(x) for x in _lista(proximos_crudo())) if s]
    sorteos.sort(key=lambda s: s["fecha_sorteo"] or date.max)
    return sorteos[0] if sorteos else None


def resultados(id_sorteo: str, fecha_sorteo: date | None) -> list[dict] | None:
    """Partidos de un sorteo con los resultados que ya haya. Mira primero en
    'próximos' (resultados parciales del fin de semana) y luego en 'celebrados'."""
    candidatos = []
    try:
        candidatos += [_sorteo(x) for x in _lista(proximos_crudo())]
    except requests.RequestException:
        pass
    if fecha_sorteo:
        try:
            candidatos += [_sorteo(x) for x in _lista(
                celebrados_crudo(fecha_sorteo - timedelta(days=7), fecha_sorteo + timedelta(days=2)))]
        except requests.RequestException:
            pass
    mejores = [s for s in candidatos if s and s["id_sorteo"] == str(id_sorteo)]
    if not mejores:
        return None
    # Nos quedamos con la versión que más resultados tenga
    mejores.sort(key=lambda s: sum(1 for p in s["partidos"] if p["signo"]), reverse=True)
    return mejores[0]["partidos"]
