"""Decrypt the recipient blob from a `publish-release` dispatch and send notifications.

Reads:
  NOTIFY_KEY            base64-encoded AES-256 key (32 bytes)
  RECIPIENTS_ENC        base64(nonce || ciphertext) of the encrypted recipient JSON
  WINDMILL_URL          WhatsApp webhook URL (required iff any whatsapp recipients)
  WINDMILL_TOKEN        WhatsApp webhook bearer token (required iff any whatsapp recipients)
  FALLBACK_TG_TOKEN     Telegram bot token used when payload omits its own
  APP, VERSION          App identifier and version string
  HEADLINE              Free-form release notes; first non-empty line is shown
  RELEASE_URL           Public release URL
  REPO                  owner/name of the publisher repo (for the WhatsApp webhook)

Decrypted payload shape:
  {"recipients": [{"channel": "whatsapp"|"telegram", "address": "<digits>"}, ...],
   "telegram_bot_token"?: "<token>"}
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


WHATSAPP_ADDRESS_RE = re.compile(r"\A\d+\Z")
TELEGRAM_ADDRESS_RE = re.compile(r"\A-?\d+\Z")
HTTP_TIMEOUT_SECONDS = 15


def env(name: str, *, required: bool = True) -> str:
    """Fetch an env var. Required by default — workflow inputs should never be silently missing."""
    value = os.environ.get(name, "")
    if required and not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def mask(value: str) -> None:
    """Hide `value` from subsequent workflow logs via the `::add-mask::` workflow command."""
    print(f"::add-mask::{value}", file=sys.stderr, flush=True)


def decrypt_payload(key_b64: str, blob_b64: str) -> dict[str, Any]:
    key = base64.b64decode(key_b64)
    blob = base64.b64decode(blob_b64)
    if len(blob) < 13:
        raise ValueError("encrypted blob is too short to contain nonce + ciphertext")
    nonce, ct = blob[:12], blob[12:]
    plaintext = AESGCM(key).decrypt(nonce, ct, None)
    payload = json.loads(plaintext.decode())
    if not isinstance(payload, dict):
        raise ValueError("decrypted payload must be a JSON object")
    return payload


def parse_recipients(raw: Any) -> list[dict[str, str]]:
    """Return a list of {channel, address} dicts. Reject anything malformed."""
    if not isinstance(raw, list):
        raise ValueError("recipients must be a list")
    parsed: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"recipient entry must be an object: {item!r}")
        channel = item.get("channel")
        address = item.get("address")
        if not isinstance(address, str) or not address:
            raise ValueError(f"recipient address must be a non-empty string: {item!r}")
        if channel == "whatsapp":
            if not WHATSAPP_ADDRESS_RE.fullmatch(address):
                raise ValueError(f"whatsapp address must be digits only: {address!r}")
        elif channel == "telegram":
            if not TELEGRAM_ADDRESS_RE.fullmatch(address):
                raise ValueError(f"telegram chat id must be an integer: {address!r}")
        else:
            raise ValueError(f"unknown channel: {channel!r}")
        parsed.append({"channel": channel, "address": address})
    return parsed


def first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def build_telegram_text(app: str, version: str, headline: str, release_url: str) -> str:
    parts = [f"{app} {version}", headline, release_url]
    return "\n".join(p for p in parts if p)


def post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> None:
    data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        response.read()


def send_whatsapp(recipients: list[str], context: dict[str, str]) -> None:
    url = env("WINDMILL_URL")
    token = env("WINDMILL_TOKEN")
    post_json(
        url,
        body={
            "repo": context["repo"],
            "version_tag": context["version_tag"],
            "url": context["release_url"],
            "headline": context["headline"],
            "recipients": recipients,
        },
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    print(f"Windmill: dispatched {len(recipients)} whatsapp recipient(s)")


def send_telegram(recipients: list[str], token: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in recipients:
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode())
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {data.get('description')!r}")
    print(f"Telegram: sent to {len(recipients)} recipient(s)")


def main() -> None:
    payload = decrypt_payload(env("NOTIFY_KEY"), env("RECIPIENTS_ENC"))
    recipients = parse_recipients(payload.get("recipients", []))

    tg_token_raw = payload.get("telegram_bot_token")
    if tg_token_raw is not None and not isinstance(tg_token_raw, str):
        raise ValueError("telegram_bot_token must be a string when present")
    tg_token = tg_token_raw or env("FALLBACK_TG_TOKEN", required=False)

    for r in recipients:
        mask(r["address"])
    if tg_token:
        mask(tg_token)

    whatsapp = [r["address"] for r in recipients if r["channel"] == "whatsapp"]
    telegram = [r["address"] for r in recipients if r["channel"] == "telegram"]

    app = env("APP")
    version = env("VERSION")
    headline = first_nonempty_line(env("HEADLINE", required=False))
    context = {
        "repo": env("REPO"),
        "version_tag": f"{app}-v{version}",
        "release_url": env("RELEASE_URL"),
        "headline": headline,
    }

    if whatsapp:
        send_whatsapp(whatsapp, context)

    if telegram:
        if not tg_token:
            raise RuntimeError(
                "telegram recipients present but no bot token "
                "(set TELEGRAM_BOT_TOKEN on the source repo or RELEASE_PUBLISHER_BOT_TOKEN on the publisher)"
            )
        send_telegram(telegram, tg_token, build_telegram_text(app, version, headline, context["release_url"]))


if __name__ == "__main__":
    main()
