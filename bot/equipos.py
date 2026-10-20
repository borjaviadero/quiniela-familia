"""Equipos de Primera División, actualizados solos cada temporada.

La lista sale de ESPN (sin clave ni registro). Si ESPN no responde, se usa la
lista de respaldo de logica.EQUIPOS_PRIMERA. Como ESPN y Loterías escriben los
nombres distinto ("Atlético Madrid" / "At. Madrid", "Real Betis" / "Betis"),
se comparan por palabras significativas.
"""
from __future__ import annotations

import re
import unicodedata

import requests

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/teams"

_ABREV = {"at": "atletico", "atl": "atletico", "r": "real", "dep": "deportivo", "rc": "racing"}
_RELLENO = {"real", "club", "cf", "fc", "cd", "sd", "ud", "rcd", "sad", "de", "la", "el", "balompie",
            "futbol", "sociedad_anonima"}


def palabras(nombre: str) -> frozenset[str]:
    """'At. Madrid' -> {atletico, madrid} · 'Real Betis Balompié' -> {betis} · 'Real Sociedad' -> {sociedad}."""
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    toks = [_ABREV.get(t, t) for t in re.findall(r"[a-z0-9]+", s)]
    utiles = [t for t in toks if t not in _RELLENO]
    return frozenset(utiles or toks)


def es_femenino(nombre: str) -> bool:
    return nombre.strip().endswith("(F)")


def coincide(nombre_lae: str, equipo: frozenset[str]) -> bool:
    """El nombre de Loterías coincide si todas sus palabras están en el del equipo
    ('Celta' ⊆ 'Celta Vigo', 'At. Madrid' ⊆ 'Atlético Madrid'). Al revés no vale:
    así los filiales ('Sevilla At.', 'Real Madrid Castilla') no cuentan."""
    if es_femenino(nombre_lae):
        return False
    p = palabras(nombre_lae)
    return bool(p) and p <= equipo


def de_espn() -> list[str]:
    """Nombres de los equipos de Primera esta temporada según ESPN ([] si falla)."""
    try:
        r = requests.get(ESPN, timeout=20)
        r.raise_for_status()
        ligas = r.json()["sports"][0]["leagues"][0]["teams"]
        nombres = []
        for t in ligas:
            t = t.get("team", t)
            nombres += [t[c] for c in ("displayName", "shortDisplayName", "name") if t.get(c)]
        equipos = {n for n in nombres}
        return sorted(equipos) if len(ligas) >= 18 else []
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo leer la lista de equipos de ESPN: {e}")
        return []
