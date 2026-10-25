"""Telegram Bot API client. With dry_run, images and texts are saved to a folder instead,
and incoming updates are taken from the `incoming` list (used by the simulation)."""
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
        self.incoming: list[dict] = []        # dry run: updates to deliver on the next get_updates
        self.edits: list[tuple] = []          # dry run: edited messages and answered button presses
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

    def send_message(self, text: str, button: str | None = None, url: str | None = None,
                     choices: list[tuple[str, str]] | None = None) -> int | None:
        """Returns the message id. choices: [(label, callback_data), ...] shown as buttons in a row."""
        self.sent += 1
        if self.dry_run:
            extra = f"\n[{button}] {url}" if button else ""
            extra += "".join(f"\n[{label}] -> {data}" for label, data in choices or [])
            (self.dry_run / f"{self.sent:02d}_message.txt").write_text(text + extra, encoding="utf-8")
            return self.sent
        data = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if choices:
            data["reply_markup"] = json.dumps(
                {"inline_keyboard": [[{"text": label, "callback_data": cb} for label, cb in choices]]})
        elif markup := self._button(button, url):
            data["reply_markup"] = markup
        return self._api("sendMessage", data)["message_id"]

    def edit_message(self, message_id: int, text: str):
        """Replaces a message's text and removes its buttons."""
        if self.dry_run:
            self.edits.append(("edit", message_id, text))
            return
        self._api("editMessageText", {"chat_id": self.chat_id, "message_id": message_id, "text": text,
                                      "parse_mode": "HTML", "disable_web_page_preview": "true"})

    def answer_button(self, callback_id: str, text: str | None = None, alert: bool = False):
        """Acknowledges a button press, optionally with a pop-up. Telegram only accepts this
        for a short while after the press, so a late answer is silently ignored."""
        if self.dry_run:
            self.edits.append(("answer", callback_id, text))
            return
        try:
            self._api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text or "",
                                              "show_alert": "true" if alert else "false"})
        except RuntimeError:
            pass

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

    def get_updates(self, offset: int | None = None) -> list[dict]:
        """New messages and button presses. Passing offset confirms everything before it."""
        if self.dry_run:
            updates, self.incoming = self.incoming, []
            return updates
        data = {"timeout": 0}
        if offset is not None:
            data["offset"] = offset
        return self._api("getUpdates", data)


def mention(player: dict) -> str:
    """Clickable mention (notifies the player) when their Telegram id is known."""
    name = html.escape(player["name"])
    if player.get("telegram_user_id"):
        return f'<a href="tg://user?id={player["telegram_user_id"]}">{name}</a>'
    return f"<b>{name}</b>"
