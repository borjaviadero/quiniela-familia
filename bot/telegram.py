"""Sending to the Telegram group. With dry_run, images and texts are saved to a folder instead."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

import requests


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None, dry_run: str | None = None):
        self.dry_run = Path(dry_run) if dry_run else None
        self.sent = 0
        if self.dry_run:
            self.dry_run.mkdir(parents=True, exist_ok=True)
            return
        self.token = token or os.environ["TELEGRAM_TOKEN"]
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    def _api(self, method: str, data: dict, files=None):
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", data=data, files=files, timeout=60)
        if not r.ok or not r.json().get("ok"):
            raise RuntimeError(f"Telegram {method}: {r.status_code} {r.text}")
        return r.json()["result"]

    @staticmethod
    def _button(text: str | None, url: str | None) -> str | None:
        if not text or not url:
            return None
        return json.dumps({"inline_keyboard": [[{"text": text, "url": url}]]})

    def send_message(self, text: str, button: str | None = None, url: str | None = None):
        self.sent += 1
        if self.dry_run:
            (self.dry_run / f"{self.sent:02d}_message.txt").write_text(
                text + (f"\n[{button}] {url}" if button else ""), encoding="utf-8")
            return
        data = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if markup := self._button(button, url):
            data["reply_markup"] = markup
        self._api("sendMessage", data)

    def send_photo(self, png: bytes, caption: str, button: str | None = None, url: str | None = None):
        self.sent += 1
        if self.dry_run:
            (self.dry_run / f"{self.sent:02d}_photo.png").write_bytes(png)
            (self.dry_run / f"{self.sent:02d}_photo.txt").write_text(
                caption + (f"\n[{button}] {url}" if button else ""), encoding="utf-8")
            return
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        if markup := self._button(button, url):
            data["reply_markup"] = markup
        self._api("sendPhoto", data, files={"photo": ("quiniela.png", png, "image/png")})

    def get_updates(self) -> list[dict]:
        """Used to find out the group's chat_id and each member's user_id."""
        return self._api("getUpdates", {})


def mention(player: dict) -> str:
    """Clickable mention (notifies the player) when their Telegram id is known."""
    name = html.escape(player["name"])
    if player.get("telegram_user_id"):
        return f'<a href="tg://user?id={player["telegram_user_id"]}">{name}</a>'
    return f"<b>{name}</b>"
