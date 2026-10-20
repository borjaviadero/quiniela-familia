"""Lectura de la Quiniela oficial en loteriasyapuestas.es.

Loterías no documenta una API pública; estos son los servicios JSON que usa su
propia web (comprobados el 20/10/2026):

  proximosv3?game_id=LAQU&num=3
      Próximas jornadas: id_sorteo, jornada, fecha (del sorteo), cierre, estado.
  fechav3?game_id=LAQU&fecha_sorteo=AAAAMMDD
      Una jornada con sus 15 partidos en orden: local, visitante, fecha
      ("2026\\/10\\/24 14:00:00"). Cuando hay resultados, también marcador/signo.
  buscadorSorteos?game_id=LAQU&celebrados=true&fechaInicioInclusiva=..&fechaFinInclusiva=..
      Jornadas ya escrutadas: partidos con posicion, signo ("2 "), marcador ("0 - 1"),
      fecha ("17-10-2026") y hora ("14:00").

Loterías rechaza (403) a los clientes que no parecen un navegador, así que las
peticiones se hacen con curl_cffi imitando a Chrome.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

try:
    from curl_cffi import requests as http
    _IMPERSONAR = {"impersonate": "chrome"}
except ImportError:  # pragma: no cover
    import requests as http
    _IMPERSONAR = {}

from .logica import MADRID, signo_de_marcador

BASE = "https://www.loteriasyapuestas.es/servicios"
CABECERAS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.loteriasyapuestas.es/es/resultados/quiniela",
}


def _get(ruta: str, params: dict) -> Any:
    r = http.get(f"{BASE}/{ruta}", params=params, headers=CABECERAS, timeout=30, **_IMPERSONAR)
    r.raise_for_status()
    return r.json()


def probar(url: str) -> tuple[int | str, str]:
    """Para el diagnóstico: devuelve (código HTTP, principio de la respuesta)."""
    try:
        r = http.get(url, headers=CABECERAS, timeout=30, **_IMPERSONAR)
        return r.status_code, r.text[:3000]
    except Exception as e:  # noqa: BLE001
        return "ERROR", str(e)[:300]


def proximos_crudo(num: int = 3) -> Any:
    return _get("proximosv3", {"game_id": "LAQU", "num": num})


def fecha_crudo(fecha_sorteo: date) -> Any:
    return _get("fechav3", {"game_id": "LAQU", "fecha_sorteo": fecha_sorteo.strftime("%Y%m%d")})


def celebrados_crudo(desde: date, hasta: date) -> Any:
    return _get("buscadorSorteos", {
        "game_id": "LAQU", "celebrados": "true",
        "fechaInicioInclusiva": desde.strftime("%Y%m%d"),
        "fechaFinInclusiva": hasta.strftime("%Y%m%d"),
    })


# --------------------------------------------------------------------------
#  Normalización
# --------------------------------------------------------------------------

_FORMATOS = ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
             "%Y/%m/%d %H:%M", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d", "%Y/%m/%d",
             "%d-%m-%Y", "%d/%m/%Y")


def _fecha(valor, hora=None) -> datetime | None:
    if valor in (None, ""):
        return None
    s = str(valor).replace("\\/", "/").strip()
    if hora:
        s = f"{s[:10]} {str(hora).strip()}"
    for fmt in _FORMATOS:
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=MADRID)
        except ValueError:
            continue
    return None


def _nombre(valor) -> str:
    """'Athletic Club (m)' -> 'Athletic Club'."""
    return re.sub(r"\s*\((m|f)\)\s*$", "", str(valor or "")).strip()


def _goles(txt: str) -> int | None:
    txt = txt.strip().upper()
    if txt == "M":
        return 3
    return int(txt) if txt.isdigit() else None


def _marcador(valor) -> tuple[int | None, int | None]:
    """'0 - 1', '2-1', 'M - 0', y para el pleno también '11' o '1M'."""
    s = str(valor or "").strip().upper()
    m = re.match(r"^([0-9]+|M)\s*[-:]\s*([0-9]+|M)$", s) or re.match(r"^([0-2M])([0-2M])$", s)
    if not m:
        return None, None
    return _goles(m.group(1)), _goles(m.group(2))


def _partido(i: int, p: dict) -> dict:
    posicion = int(p.get("posicion") or i + 1)
    gl, gv = _marcador(p.get("marcador"))
    signo = str(p.get("signo") or "").strip().upper()
    if posicion == 15 and gl is None:
        gl, gv = _marcador(signo)          # el pleno a veces viene como "11" o "M-1"
    if signo not in ("1", "X", "2"):
        signo = signo_de_marcador(gl, gv) if gl is not None else None
    return {
        "posicion": posicion,
        "local": _nombre(p.get("local")) or f"Partido {posicion}",
        "visitante": _nombre(p.get("visitante")),
        "inicio": _fecha(p.get("fecha"), p.get("hora")),
        "goles_local": gl,
        "goles_visitante": gv,
        "signo": signo,
    }


def _lista(crudo: Any) -> list[dict]:
    if isinstance(crudo, list):
        return [x for x in crudo if isinstance(x, dict)]
    if isinstance(crudo, dict):
        return [crudo]
    return []          # p. ej. "No se ha encontrado ningún registro…"


def _partidos_de(sorteo: dict) -> list[dict]:
    ps = [p for p in (sorteo.get("partidos") or []) if isinstance(p, dict)]
    return sorted((_partido(i, p) for i, p in enumerate(ps)), key=lambda x: x["posicion"])[:15]


def partidos(id_sorteo: str, fecha_sorteo: date) -> list[dict] | None:
    for s in _lista(fecha_crudo(fecha_sorteo)):
        if str(s.get("id_sorteo")) == str(id_sorteo):
            ps = _partidos_de(s)
            return ps if len(ps) >= 14 else None
    return None


def proximas_jornadas() -> list[dict]:
    """Jornadas próximas que ya tienen los partidos publicados, de la más cercana a la más lejana."""
    out = []
    for s in _lista(proximos_crudo()):
        fs = _fecha(s.get("fecha"))
        if not fs or not s.get("id_sorteo") or not s.get("jornada"):
            continue
        try:
            ps = partidos(s["id_sorteo"], fs.date())
        except Exception:  # noqa: BLE001
            ps = None
        if not ps:
            continue
        inicios = [p["inicio"] for p in ps if p["inicio"]]
        out.append({
            "id_sorteo": str(s["id_sorteo"]),
            "numero": int(s["jornada"]),
            "fecha_sorteo": fs.date(),
            "primer_partido": min(inicios) if inicios else None,
            "cierre_oficial": _fecha(s.get("cierre")),
            "partidos": ps,
        })
    return sorted(out, key=lambda j: j["fecha_sorteo"])


def resultados(id_sorteo: str, fecha_sorteo: date | None) -> list[dict] | None:
    """Partidos de una jornada con los resultados que haya. Usa la ficha de la jornada
    (resultados según se van jugando) y el escrutinio oficial; se queda con la más completa."""
    if not fecha_sorteo:
        return None
    candidatos = []
    try:
        candidatos.append(partidos(id_sorteo, fecha_sorteo))
    except Exception:  # noqa: BLE001
        pass
    try:
        for s in _lista(celebrados_crudo(fecha_sorteo - timedelta(days=7), fecha_sorteo + timedelta(days=3))):
            if str(s.get("id_sorteo")) == str(id_sorteo):
                candidatos.append(_partidos_de(s))
    except Exception:  # noqa: BLE001
        pass
    candidatos = [c for c in candidatos if c and len(c) >= 14]
    if not candidatos:
        return None
    return max(candidatos, key=lambda ps: sum(1 for p in ps if p["signo"]))
