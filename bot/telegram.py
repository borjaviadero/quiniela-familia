"""Envío al grupo de Telegram. Con dry_run guarda las imágenes y textos en una carpeta."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

import requests


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None, dry_run: str | None = None):
        self.dry_run = Path(dry_run) if dry_run else None
        if self.dry_run:
            self.dry_run.mkdir(parents=True, exist_ok=True)
            self.n = 0
            return
        self.token = token or os.environ["TELEGRAM_TOKEN"]
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    def _api(self, metodo: str, data: dict, files=None):
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{metodo}", data=data, files=files, timeout=60)
        if not r.ok or not r.json().get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {r.status_code} {r.text}")
        return r.json()["result"]

    @staticmethod
    def _boton(texto: str | None, url: str | None) -> str | None:
        if not texto or not url:
            return None
        return json.dumps({"inline_keyboard": [[{"text": texto, "url": url}]]})

    def mensaje(self, texto: str, boton: str | None = None, url: str | None = None):
        if self.dry_run:
            self.n += 1
            (self.dry_run / f"{self.n:02d}_mensaje.txt").write_text(texto + (f"\n[{boton}] {url}" if boton else ""))
            return
        data = {"chat_id": self.chat_id, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if (rm := self._boton(boton, url)):
            data["reply_markup"] = rm
        self._api("sendMessage", data)

    def foto(self, png: bytes, pie: str, boton: str | None = None, url: str | None = None):
        if self.dry_run:
            self.n += 1
            (self.dry_run / f"{self.n:02d}_foto.png").write_bytes(png)
            (self.dry_run / f"{self.n:02d}_foto.txt").write_text(pie + (f"\n[{boton}] {url}" if boton else ""))
            return
        data = {"chat_id": self.chat_id, "caption": pie[:1024], "parse_mode": "HTML"}
        if (rm := self._boton(boton, url)):
            data["reply_markup"] = rm
        self._api("sendPhoto", data, files={"photo": ("quiniela.png", png, "image/png")})

    def actualizaciones(self) -> list[dict]:
        """Para descubrir el chat_id del grupo y los user_id de cada uno."""
        return self._api("getUpdates", {})


def mencion(jugador: dict) -> str:
    nombre = html.escape(jugador["nombre"])
    if jugador.get("telegram_user_id"):
        return f'<a href="tg://user?id={jugador["telegram_user_id"]}">{nombre}</a>'
    return f"<b>{nombre}</b>"
