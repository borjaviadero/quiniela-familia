"""Copia de seguridad de la base de datos.

  python -m bot.backup exportar copia.json     # descarga todas las tablas a un JSON
  python -m bot.backup restaurar copia.json    # vuelve a cargarlas en Supabase

El workflow .github/workflows/backup.yml lo ejecuta cada semana, cifra el JSON
con el secreto BACKUP_KEY y lo guarda en backups/quiniela.json.enc. El repositorio
es público: sin la contraseña, la copia no se puede leer.

Para recuperar una copia (en una terminal con openssl):
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in backups/quiniela.json.enc -out copia.json
  python -m bot.backup restaurar copia.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from .db import DB

# En este orden, para que existan las tablas de las que dependen las siguientes
TABLAS = ["jugadores", "jornadas", "partidos", "apuestas", "partes_diarios", "pagos"]
CLAVES = {"jugadores": "id", "jornadas": "id", "partidos": "jornada_id,posicion",
          "apuestas": "jornada_id,jugador_id", "partes_diarios": "jornada_id,dia",
          "pagos": "jornada_id,jugador_id"}
LOTE = 1000   # Supabase devuelve como mucho 1000 filas por petición


def exportar(db: DB) -> dict:
    copia = {"creada": datetime.now(timezone.utc).isoformat(), "tablas": {}}
    for t in TABLAS:
        filas, desde = [], 0
        while True:
            lote = db._req("GET", t, {"select": "*", "order": CLAVES[t].replace(",", ".asc,") + ".asc",
                                      "limit": LOTE, "offset": desde})
            filas += lote
            if len(lote) < LOTE:
                break
            desde += LOTE
        copia["tablas"][t] = filas
    return copia


def restaurar(db: DB, copia: dict):
    for t in TABLAS:
        filas = copia["tablas"].get(t, [])
        for i in range(0, len(filas), 500):
            db._req("POST", t, {"on_conflict": CLAVES[t]}, filas[i:i + 500],
                    prefer="resolution=merge-duplicates,return=minimal")
        print(f"{t}: {len(filas)} filas")
    print("Hecho. Ejecuta en Supabase > SQL Editor para que las jornadas nuevas sigan numerándose bien:\n"
          "  select setval('jornadas_id_seq', (select coalesce(max(id), 1) from jornadas));")


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) != 2 or argv[0] not in ("exportar", "restaurar"):
        print(__doc__)
        return 2
    db = DB()
    if argv[0] == "exportar":
        copia = exportar(db)
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(copia, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("Copia: " + ", ".join(f"{t} {len(v)}" for t, v in copia["tablas"].items()))
    else:
        with open(argv[1], encoding="utf-8") as f:
            restaurar(db, json.load(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
