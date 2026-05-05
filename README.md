# release-publisher

Public mirror for release artifacts (`.exe`, `.apk`, …) built from private repos.

Live page: <https://jirip.github.io/release-publisher/>

## How it works

1. A private repo finishes a release build and POSTs a `repository_dispatch` event of type `publish-release` to this repo, carrying the app name, version, source tag, asset download URLs, and (optionally) an encrypted recipient list for WhatsApp notification.
2. The `republish` workflow downloads the assets from the private repo (using `SOURCE_REPO_TOKEN`), creates a public release here tagged `<app>-v<version>`, appends an entry to `docs/releases.json`, and commits.
3. If recipients are included, the workflow decrypts them (using the shared `NOTIFY_KEY`), masks each number in the log, and POSTs to Windmill to send a WhatsApp notification linking to the **public** release.
4. GitHub Pages serves `docs/`, which renders cards per app with direct download links to the latest version.

## Notifications

The private repos' `.github/notify.txt` lists who to notify when a release is published. Two channels are supported, one recipient per line:

```
whatsapp: 420728814716   # Jirka
telegram: 7521184714     # Jirka
# whole-line comments are ignored
```

The trailing `# name` is purely a hint for the file's author — it's stripped before parsing.

### Recipient privacy

The whole recipient list (and any per-app Telegram bot token, see below) is JSON-encoded and encrypted with AES-256-GCM using a shared `NOTIFY_KEY` before being placed in the `repository_dispatch` payload. The public event payload and this repo's source only ever see ciphertext. Decryption happens inside the publisher's workflow, which calls `::add-mask::` on every address and the bot token before any further logging.

If `NOTIFY_KEY` is ever leaked: rotate it on all repos that use it (regenerate with `openssl rand -base64 32`, `gh secret set NOTIFY_KEY` on each). Historical dispatch payloads remain decryptable with the old key, so treat rotation as defence-in-depth, not retroactive.

### Channel routing

- **WhatsApp** recipients are forwarded to a Windmill webhook (one batched POST). Requires `WINDMILL_URL` and `WINDMILL_TOKEN` secrets on `release-publisher`.
- **Telegram** recipients receive one direct `sendMessage` call per chat ID. The bot used is, in order of preference:
  1. A per-app bot token sent (encrypted) inside the dispatch payload — set `TELEGRAM_BOT_TOKEN` on the private source repo to enable.
  2. The fallback `RELEASE_PUBLISHER_BOT_TOKEN` secret on `release-publisher` — used when the source repo doesn't supply its own.

Per-app bots give each app a distinct sender name in the user's Telegram chat list. Apps that don't care can omit `TELEGRAM_BOT_TOKEN`; the fallback bot covers them.

Bot tokens are full-impersonation secrets — never put them in `notify.txt` or any committed file. They live only in GitHub Secrets and travel through dispatch payloads inside the encrypted blob.

## Wiring up a new private repo

**1. Create two fine-grained PATs:**

- **`release-publisher-read`** — `contents: read` on every private source repo.
  Stored as `SOURCE_REPO_TOKEN` in `jirip/release-publisher` → used by the republish workflow to download assets from the private release.

- **`release-publisher-write`** — `contents: write` + `metadata: read` on `jirip/release-publisher`.
  Stored as `PUBLISH_TOKEN` in *each* private source repo → used by the private repo's release workflow to trigger `repository_dispatch` here and to read its own release (`gh release view`).

Naming the PATs `release-publisher-read` / `release-publisher-write` in the GitHub token UI makes their purpose obvious when you come back months later.

**2. Append a dispatch step** to the private repo's release workflow, after the release is created.

The step encrypts the recipient list (and optionally a per-app Telegram bot token) using `NOTIFY_KEY`, then dispatches to `release-publisher`:

```yaml
- name: Dispatch to release-publisher
  env:
    GH_TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    NOTIFY_KEY: ${{ secrets.NOTIFY_KEY }}
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}  # optional; falls back to publisher's bot
    APP: <app-name>                                 # e.g. pdf2jpg
    VERSION: ${{ steps.version.outputs.name }}     # e.g. 0.1.8 (no leading v)
    SOURCE_TAG: v${{ steps.version.outputs.name }} # matches the tag on this repo
    NOTES: ${{ steps.notes.outputs.body }}
    WEB_URL: ""                                    # optional, see "Web-wrapped apps" below
  run: |
    pip install --quiet 'cryptography>=42'

    RECIPIENTS_ENC=$(python3 <<'PY'
    import base64, json, os, re
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    recipients = []
    try:
        with open(".github/notify.txt") as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                channel, _, address = line.partition(":")
                channel, address = channel.strip().lower(), address.strip()
                assert channel in ("whatsapp", "telegram"), f"unknown channel: {channel}"
                assert address, f"empty address on line: {raw!r}"
                if channel == "whatsapp":
                    assert re.fullmatch(r"\d+", address), f"whatsapp address must be digits: {address}"
                else:
                    assert re.fullmatch(r"-?\d+", address), f"telegram chat id must be integer: {address}"
                recipients.append({"channel": channel, "address": address})
    except FileNotFoundError:
        pass  # no notify.txt -> no recipients, no notification step

    payload = {"recipients": recipients}
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    if tg_token:
        payload["telegram_bot_token"] = tg_token

    if not recipients:
        print("")  # empty -> publisher skips the notify step
    else:
        key = base64.b64decode(os.environ["NOTIFY_KEY"])
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), None)
        print(base64.b64encode(nonce + ct).decode())
    PY
    )

    ASSETS=$(gh release view "$SOURCE_TAG" \
      --repo "$GITHUB_REPOSITORY" \
      --json assets \
      --jq '[.assets[] | {name: .name, url: .apiUrl}]')

    jq -n \
      --arg app "$APP" \
      --arg version "$VERSION" \
      --arg source_repo "$GITHUB_REPOSITORY" \
      --arg source_tag "$SOURCE_TAG" \
      --arg notes "$NOTES" \
      --arg web_url "$WEB_URL" \
      --arg recipients_enc "$RECIPIENTS_ENC" \
      --argjson assets "$ASSETS" \
      '{event_type: "publish-release", client_payload: ({app: $app, version: $version, source_repo: $source_repo, source_tag: $source_tag, notes: $notes, assets: $assets, recipients_enc: $recipients_enc} + (if $web_url == "" then {} else {web_url: $web_url} end))}' \
    | curl -fsS -X POST \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GH_TOKEN" \
        "https://api.github.com/repos/jirip/release-publisher/dispatches" \
        --data-binary @-
```

Asset URLs must be API URLs (`.apiUrl`), not browser URLs — the republisher downloads them with a `Bearer` token and `Accept: application/octet-stream`.

## Web-wrapped apps (optional `web_url`)

Apps that ship both downloadable artifacts and a hosted web version (e.g. a Capacitor-wrapped PWA) can pass a `web_url` in the dispatch. The public page then renders an **Open Web** button on the app's card alongside the download links.

Semantics of `web_url` in the dispatch payload:

- **Omitted** → whatever `apps[app].webUrl` is currently stored in `docs/releases.json` is preserved unchanged. This is the default for apps with no web version.
- **Empty string (`""`)** → clears any previously stored `webUrl` for that app.
- **`"https://..."`** → sets/overwrites `apps[app].webUrl`.

The URL lives at the app level in the manifest (not per-release) because one URL per app is enough — a hosted PWA only has one live version at a time.

## Adding a new app

No code changes needed — the page renders whatever apps appear in `docs/releases.json`. The first dispatch for a new `app` name creates its entry automatically.

## Manifest shape

```json
{
  "apps": {
    "pdf2jpg": {
      "releases": [
        {
          "version": "0.1.8",
          "date": "2026-04-17T02:18:35Z",
          "source_repo": "jirip/pdf2jpg",
          "source_tag": "v0.1.8",
          "notes": "...",
          "release_url": "https://github.com/jirip/release-publisher/releases/tag/pdf2jpg-v0.1.8",
          "assets": [
            { "name": "pdf2jpg.exe", "url": "https://github.com/jirip/release-publisher/releases/download/pdf2jpg-v0.1.8/pdf2jpg.exe" }
          ]
        }
      ]
    },
    "pexesongy": {
      "webUrl": "https://pexesongy.pages.dev/",
      "releases": [ ... ]
    }
  }
}
```

`webUrl` is optional. If present, the public page renders an **Open Web** button on the app's card.

Releases are kept in reverse-chronological order within each app (newest first).
