"""Genera la imagen del boleto con las 5 columnas, al estilo del boleto oficial."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .logica import goles_pleno

# Misma paleta que la web: papel verde pálido y tinta roja
PAPEL = "#E7EFE6"
BLANCO = "#FBFDFB"
TINTA = "#17352A"
TINTA_SUAVE = "#4F6A5E"
ROJO = "#B3262F"
ROJO_SUAVE = "#F2D9DA"
VERDE = "#1E7A46"
VERDE_SUAVE = "#D3EBDC"

FUENTES = Path(__file__).parent / "fuentes"
_RESPALDO = {
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "semi": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "normal": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf", "DejaVuSans.ttf"],
}
_PROPIAS = {"bold": "BarlowCondensed-Bold.ttf", "semi": "BarlowCondensed-SemiBold.ttf",
            "normal": "BarlowCondensed-Medium.ttf"}


def fuente(peso: str, tam: int) -> ImageFont.FreeTypeFont:
    for ruta in [FUENTES / _PROPIAS[peso], *_RESPALDO[peso]]:
        try:
            return ImageFont.truetype(str(ruta), tam)
        except OSError:
            continue
    return ImageFont.load_default(tam)


def _centrado(d: ImageDraw.ImageDraw, caja, texto, f, color):
    x0, y0, x1, y1 = caja
    w = d.textlength(texto, font=f)
    asc, desc = f.getmetrics()
    d.text(((x0 + x1 - w) / 2, (y0 + y1 - asc - desc) / 2 + 1), texto, font=f, fill=color)


def _recortar(d, texto, f, ancho):
    if d.textlength(texto, font=f) <= ancho:
        return texto
    while texto and d.textlength(texto + "…", font=f) > ancho:
        texto = texto[:-1]
    return texto + "…"


def boleto(*, titulo: str, subtitulo: str, partidos: list[dict], jugadores: list[dict],
           apuestas: dict[int, dict], pleno_jugador_id: int | None,
           mostrar_resultados: bool, pie: list[str] | None = None) -> bytes:
    """
    partidos:  [{posicion, local, visitante, signo, goles_local, goles_visitante}] (15 con el pleno)
    jugadores: [{id, nombre}]
    apuestas:  {jugador_id: {"signos": "11X...", "pleno": "2-1"|None}}
    """
    E = 2  # escala para que se lea bien en el móvil
    M, FILA, COL, EQ, RES, NUM = 28 * E, 44 * E, 86 * E, 300 * E, 70 * E, 40 * E
    n = len(jugadores)
    ancho = M * 2 + NUM + EQ + COL * n + (RES if mostrar_resultados else 0)
    cab_h = 120 * E
    filas = 15 + (1 if mostrar_resultados else 0)
    pie = pie or []
    pie_h = (len(pie) * 30 + 30) * E if pie else 16 * E
    alto = cab_h + FILA * (filas + 1) + pie_h + M

    f_tit, f_sub = fuente("bold", 52 * E), fuente("normal", 20 * E)
    medir = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    textos = [subtitulo, *pie]
    ancho = int(max(ancho, *(medir.textlength(t, font=f_sub) + 2 * M for t in textos)))
    img = Image.new("RGB", (ancho, alto), PAPEL)
    d = ImageDraw.Draw(img)
    f_cel, f_eq, f_cab = fuente("bold", 22 * E), fuente("normal", 19 * E), fuente("semi", 18 * E)

    d.text((M, 22 * E), titulo, font=f_tit, fill=TINTA)
    d.text((M, 82 * E), subtitulo, font=f_sub, fill=TINTA_SUAVE)
    d.line([(M, cab_h - 8 * E), (ancho - M, cab_h - 8 * E)], fill=ROJO, width=2 * E)

    x_eq = M + NUM
    x_cols = x_eq + EQ
    x_res = x_cols + COL * n
    y = cab_h

    # Fondo blanco del boleto
    d.rectangle([M, y, ancho - M, y + FILA * (filas + 1)], fill=BLANCO, outline=ROJO, width=E + 1)

    # Cabecera de columnas
    for i, j in enumerate(jugadores):
        nombre = j["nombre"].split(" ")[0]
        _centrado(d, (x_cols + COL * i, y, x_cols + COL * (i + 1), y + FILA),
                  _recortar(d, nombre, f_cab, COL - 6 * E), f_cab, ROJO)
    if mostrar_resultados:
        _centrado(d, (x_res, y, x_res + RES, y + FILA), "Res.", f_cab, ROJO)
    d.text((x_eq, y + 12 * E), "Partido", font=f_cab, fill=ROJO)
    y += FILA

    aciertos = {j["id"]: 0 for j in jugadores}
    for p in partidos:
        pos = p["posicion"]
        es_pleno = pos == 15
        if es_pleno:
            d.rectangle([M + 1, y, ancho - M - 1, y + FILA], fill=ROJO_SUAVE)
        linea = ROJO if pos in (5, 10, 14) else ROJO_SUAVE
        d.line([(M, y + FILA), (ancho - M, y + FILA)], fill=linea, width=E if linea == ROJO_SUAVE else 2 * E)
        _centrado(d, (M, y, M + NUM, y + FILA), str(pos), f_cel, ROJO)
        txt = f"{p['local']} - {p['visitante']}"
        d.text((x_eq, y + 11 * E), _recortar(d, txt, f_eq, EQ - 10 * E), font=f_eq, fill=TINTA)

        real = None
        if es_pleno and p.get("goles_local") is not None:
            real = f"{goles_pleno(p['goles_local'])}-{goles_pleno(p['goles_visitante'])}"
        elif not es_pleno:
            real = p.get("signo")

        for i, j in enumerate(jugadores):
            a = apuestas.get(j["id"])
            if es_pleno:
                s = (a or {}).get("pleno") if j["id"] == pleno_jugador_id else ""
            else:
                s = a["signos"][pos - 1] if a else "–"
            caja = (x_cols + COL * i + 6 * E, y + 5 * E, x_cols + COL * (i + 1) - 6 * E, y + FILA - 5 * E)
            if s and mostrar_resultados and real:
                ok = s == real
                if ok and not es_pleno:
                    aciertos[j["id"]] += 1
                d.rounded_rectangle(caja, radius=4 * E, fill=VERDE_SUAVE if ok else ROJO_SUAVE)
                _centrado(d, caja, s, f_cel, VERDE if ok else ROJO)
            elif s:
                d.rounded_rectangle(caja, radius=4 * E, outline=ROJO, width=E)
                _centrado(d, caja, s, f_cel, ROJO)
        if mostrar_resultados and real:
            _centrado(d, (x_res, y, x_res + RES, y + FILA), real, f_cel, TINTA)
        y += FILA

    if mostrar_resultados:
        d.text((x_eq, y + 11 * E), "Aciertos", font=f_cab, fill=TINTA)
        maximo = max(aciertos.values())
        minimo = min(aciertos.values())
        for i, j in enumerate(jugadores):
            v = aciertos[j["id"]]
            color = VERDE if v == maximo else ROJO if v == minimo else TINTA
            _centrado(d, (x_cols + COL * i, y, x_cols + COL * (i + 1), y + FILA), str(v), f_cel, color)
        y += FILA

    # Volver a pintar el marco (el fondo del pleno lo tapa)
    d.rectangle([M, cab_h, ancho - M, cab_h + FILA * (filas + 1)], outline=ROJO, width=E + 1)
    y += 18 * E
    for linea in pie:
        d.text((M, y), linea, font=f_sub, fill=TINTA)
        y += 30 * E

    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
