"""Optional Telegram push notifications, so the phone is the control surface."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"


class Notifier:
    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """Best effort: a failed notification must never stop the bot."""
        if not self.enabled:
            log.info("notify (disabled): %s", text)
            return False
        payload = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode()
        request = urllib.request.Request(API.format(token=self.token), data=payload)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return bool(json.loads(response.read().decode()).get("ok"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("telegram send failed: %s", exc)
            return False
