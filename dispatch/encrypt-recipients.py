"""Read .github/notify.txt, validate, and emit base64(nonce || AES-256-GCM ciphertext) on stdout.

Outputs an empty line when there are no recipients (the publisher then skips the notify step).

Reads:
  NOTIFY_KEY              base64-encoded AES-256 key
  TELEGRAM_BOT_TOKEN      optional per-app bot token, embedded inside the encrypted blob
  NOTIFY_FILE             path to notify.txt (default: .github/notify.txt)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


WHATSAPP_RE = re.compile(r"\A\d+\Z")
TELEGRAM_RE = re.compile(r"\A-?\d+\Z")
VALID_CHANNELS = {"whatsapp": WHATSAPP_RE, "telegram": TELEGRAM_RE}


def parse_notify_file(path: str) -> list[dict[str, str]]:
    """Parse a notify.txt of `channel: address  # comment` lines into a list of recipients."""
    recipients: list[dict[str, str]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                channel, sep, address = line.partition(":")
                if not sep:
                    raise ValueError(f"{path}:{lineno}: expected 'channel: address', got {raw!r}")
                channel = channel.strip().lower()
                address = address.strip()
                pattern = VALID_CHANNELS.get(channel)
                if pattern is None:
                    raise ValueError(f"{path}:{lineno}: unknown channel {channel!r}")
                if not pattern.fullmatch(address):
                    raise ValueError(f"{path}:{lineno}: invalid {channel} address {address!r}")
                recipients.append({"channel": channel, "address": address})
    except FileNotFoundError:
        return []
    return recipients


def main() -> None:
    notify_file = os.environ.get("NOTIFY_FILE", ".github/notify.txt")
    recipients = parse_notify_file(notify_file)

    if not recipients:
        print("")
        return

    payload: dict[str, object] = {"recipients": recipients}
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    if tg_token:
        payload["telegram_bot_token"] = tg_token

    key_b64 = os.environ.get("NOTIFY_KEY")
    if not key_b64:
        sys.exit("NOTIFY_KEY is required when notify.txt has recipients")

    key = base64.b64decode(key_b64)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), None)
    print(base64.b64encode(nonce + ciphertext).decode())


if __name__ == "__main__":
    main()
