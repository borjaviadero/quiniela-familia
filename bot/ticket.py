"""Renders the ticket image (all players' columns), styled after the official Quiniela ticket."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .rules import pleno_goals

# Same palette as the website: pale green paper and red ink
PAPER = "#E7EFE6"
WHITE = "#FBFDFB"
INK = "#17352A"
INK_SOFT = "#4F6A5E"
RED = "#B3262F"
RED_SOFT = "#F2D9DA"
GREEN = "#1E7A46"
GREEN_SOFT = "#D3EBDC"

# Labels drawn on the image (user-facing, Spanish)
LABEL_MATCH = "Partido"
LABEL_RESULT = "Res."
LABEL_HITS = "Aciertos"

# Barlow Condensed is downloaded by the workflow; DejaVu is the fallback
FONTS_DIR = Path(__file__).parent / "fonts"
_FALLBACK_FONTS = {
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "semi": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "normal": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf", "DejaVuSans.ttf"],
}
_BRAND_FONTS = {"bold": "BarlowCondensed-Bold.ttf", "semi": "BarlowCondensed-SemiBold.ttf",
                "normal": "BarlowCondensed-Medium.ttf"}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for path in [FONTS_DIR / _BRAND_FONTS[weight], *_FALLBACK_FONTS[weight]]:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _centered(d: ImageDraw.ImageDraw, box, text, f, color):
    x0, y0, x1, y1 = box
    w = d.textlength(text, font=f)
    ascent, descent = f.getmetrics()
    d.text(((x0 + x1 - w) / 2, (y0 + y1 - ascent - descent) / 2 + 1), text, font=f, fill=color)


def _truncate(d, text, f, width):
    if d.textlength(text, font=f) <= width:
        return text
    while text and d.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text + "…"


def render(*, title: str, subtitle: str, matches: list[dict], players: list[dict],
           picks: dict[int, dict], pleno_player_id: int | None,
           show_results: bool, footer: list[str] | None = None) -> bytes:
    """
    matches: [{position, home, away, sign, home_goals, away_goals}] (15 including the pleno)
    players: [{id, name}]   (empty list = just the fixtures, e.g. for the announcement)
    picks:   {player_id: {"signs": "11X...", "pleno": "2-1" | None}}
    Returns the PNG bytes.
    """
    S = 2  # scale factor, so it reads well on phones
    M, ROW, COL, TEAMS, RES, NUM = 28 * S, 44 * S, 86 * S, 300 * S, 70 * S, 40 * S
    n = len(players)
    width = M * 2 + NUM + TEAMS + COL * n + (RES if show_results else 0)
    header_h = 120 * S
    rows = 15 + (1 if show_results else 0)
    footer = footer or []
    footer_h = (len(footer) * 30 + 30) * S if footer else 16 * S
    height = header_h + ROW * (rows + 1) + footer_h + M

    f_title, f_sub = font("bold", 52 * S), font("normal", 20 * S)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    width = int(max(width, *(measure.textlength(t, font=f_sub) + 2 * M for t in [subtitle, *footer])))
    img = Image.new("RGB", (width, height), PAPER)
    d = ImageDraw.Draw(img)
    f_cell, f_team, f_head = font("bold", 22 * S), font("normal", 19 * S), font("semi", 18 * S)

    d.text((M, 22 * S), title, font=f_title, fill=INK)
    d.text((M, 82 * S), subtitle, font=f_sub, fill=INK_SOFT)
    d.line([(M, header_h - 8 * S), (width - M, header_h - 8 * S)], fill=RED, width=2 * S)

    x_teams = M + NUM
    x_cols = x_teams + TEAMS
    x_res = x_cols + COL * n
    y = header_h

    d.rectangle([M, y, width - M, y + ROW * (rows + 1)], fill=WHITE, outline=RED, width=S + 1)

    # Column headers: first name of each player
    for i, p in enumerate(players):
        first_name = p["name"].split(" ")[0]
        _centered(d, (x_cols + COL * i, y, x_cols + COL * (i + 1), y + ROW),
                  _truncate(d, first_name, f_head, COL - 6 * S), f_head, RED)
    if show_results:
        _centered(d, (x_res, y, x_res + RES, y + ROW), LABEL_RESULT, f_head, RED)
    d.text((x_teams, y + 12 * S), LABEL_MATCH, font=f_head, fill=RED)
    y += ROW

    hits = {p["id"]: 0 for p in players}
    for m in matches:
        pos = m["position"]
        is_pleno = pos == 15
        if is_pleno:
            d.rectangle([M + 1, y, width - M - 1, y + ROW], fill=RED_SOFT)
        rule = RED if pos in (5, 10, 14) else RED_SOFT     # thick rules every 5 rows, like the paper ticket
        d.line([(M, y + ROW), (width - M, y + ROW)], fill=rule, width=S if rule == RED_SOFT else 2 * S)
        _centered(d, (M, y, M + NUM, y + ROW), str(pos), f_cell, RED)
        d.text((x_teams, y + 11 * S), _truncate(d, f"{m['home']} - {m['away']}", f_team, TEAMS - 10 * S),
               font=f_team, fill=INK)

        actual = None
        if is_pleno and m.get("home_goals") is not None:
            actual = f"{pleno_goals(m['home_goals'])}-{pleno_goals(m['away_goals'])}"
        elif not is_pleno:
            actual = m.get("sign")

        for i, p in enumerate(players):
            pick = picks.get(p["id"])
            if is_pleno:
                value = (pick or {}).get("pleno") if p["id"] == pleno_player_id else ""
            else:
                value = pick["signs"][pos - 1] if pick else "–"
            box = (x_cols + COL * i + 6 * S, y + 5 * S, x_cols + COL * (i + 1) - 6 * S, y + ROW - 5 * S)
            if value and show_results and actual:
                ok = value == actual
                if ok and not is_pleno:
                    hits[p["id"]] += 1
                d.rounded_rectangle(box, radius=4 * S, fill=GREEN_SOFT if ok else RED_SOFT)
                _centered(d, box, value, f_cell, GREEN if ok else RED)
            elif value:
                d.rounded_rectangle(box, radius=4 * S, outline=RED, width=S)
                _centered(d, box, value, f_cell, RED)
        if show_results and actual:
            _centered(d, (x_res, y, x_res + RES, y + ROW), actual, f_cell, INK)
        y += ROW

    if show_results:
        d.text((x_teams, y + 11 * S), LABEL_HITS, font=f_head, fill=INK)
        best, worst = max(hits.values()), min(hits.values())
        for i, p in enumerate(players):
            v = hits[p["id"]]
            color = GREEN if v == best else RED if v == worst else INK
            _centered(d, (x_cols + COL * i, y, x_cols + COL * (i + 1), y + ROW), str(v), f_cell, color)
        y += ROW

    # Redraw the frame (the pleno row background covers it)
    d.rectangle([M, header_h, width - M, header_h + ROW * (rows + 1)], outline=RED, width=S + 1)
    y += 18 * S
    for line in footer:
        d.text((M, y), line, font=f_sub, fill=INK)
        y += 30 * S

    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
