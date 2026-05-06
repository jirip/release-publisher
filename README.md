# release-publisher

Public mirror for release artifacts (`.exe`, `.apk`, …) built from private repos.

Live page: <https://jirip.github.io/release-publisher/>

## How it works

1. A private repo finishes a release build and triggers `repository_dispatch` here with event type `publish-release`. The payload carries the app name, version, source tag, asset metadata, and (optionally) an encrypted recipient list for notifications.
2. The `republish` workflow validates the payload, downloads the assets from the private repo (using `SOURCE_REPO_TOKEN`), creates a public release tagged `<app>-v<version>`, prepends an entry to `docs/releases.json` (capped at the most recent `MAX_RELEASES_PER_APP` per app, currently 10), and commits.
3. If recipients are present, the workflow decrypts them (using the shared `NOTIFY_KEY`), masks each address and any bot token in the workflow log, and dispatches notifications.
4. GitHub Pages serves `docs/`, which renders one card per app with download links to the latest version and a collapsible history.

## Repo layout

| Path | Purpose |
| --- | --- |
| `.github/workflows/republish.yml` | The `publish-release` event handler. |
| `.github/scripts/notify.py` | Decrypts recipients and fans out to WhatsApp/Telegram. Imported by the workflow. |
| `dispatch/action.yml` | Composite action used by source repos to trigger a publish (see below). |
| `dispatch/encrypt-recipients.py` | Helper used by the composite action to encrypt the recipient list. |
| `docs/` | The static GitHub Pages site (`index.html`, `app.js`, `style.css`, `releases.json`). |

## Notifications

Each source repo's `.github/notify.txt` lists who to notify when a release is published. Two channels are supported, one recipient per line:

```
whatsapp: 420728814716   # Jirka
telegram: 7521184714     # Jirka
# whole-line comments are ignored
```

The trailing `# name` is a hint for the file's author — it's stripped before parsing.

### Channel routing

- **WhatsApp** recipients are forwarded to a [Windmill](https://www.windmill.dev/) webhook (one batched POST). Windmill is the scripting platform that fans the message out to the WhatsApp Business API. Requires `WINDMILL_URL` and `WINDMILL_TOKEN` secrets on `release-publisher`.
- **Telegram** recipients receive one direct `sendMessage` call per chat ID. The bot used is, in order of preference:
  1. A per-app bot token sent (encrypted) inside the dispatch payload — set `TELEGRAM_BOT_TOKEN` on the source repo to enable.
  2. The fallback `RELEASE_PUBLISHER_BOT_TOKEN` secret on `release-publisher` — used when the source repo doesn't supply its own.

Per-app bots give each app a distinct sender name in the user's Telegram chat list. Apps that don't care can omit `TELEGRAM_BOT_TOKEN`; the fallback bot covers them.

Bot tokens are full-impersonation secrets — never put them in `notify.txt` or any committed file. They live only in GitHub Secrets and travel through dispatch payloads inside the encrypted blob.

### Recipient privacy

The whole recipient list (and any per-app Telegram bot token) is JSON-encoded and encrypted with AES-256-GCM using a shared `NOTIFY_KEY` before being placed in the `repository_dispatch` payload. The public event payload and this repo's source only ever see ciphertext. Decryption happens inside the publisher's workflow, which calls `::add-mask::` on every address and the bot token before any further logging.

If `NOTIFY_KEY` is ever leaked: rotate it on all repos that use it (regenerate with `openssl rand -base64 32`, then `gh secret set NOTIFY_KEY` on each). Historical dispatch payloads remain decryptable with the old key, so treat rotation as defence-in-depth, not retroactive.

## Wiring up a new private repo

**1. Create two fine-grained PATs:**

- **`release-publisher-read`** — `contents: read` on every private source repo.
  Stored as `SOURCE_REPO_TOKEN` in `jirip/release-publisher` → used by the republish workflow to download assets from the private release.

- **`release-publisher-write`** — `contents: write` + `metadata: read` on `jirip/release-publisher`.
  Stored as `PUBLISH_TOKEN` in *each* private source repo → used by the source repo's release workflow to dispatch here and to read its own release (`gh release view`).

Naming the PATs `release-publisher-read` / `release-publisher-write` in the GitHub token UI makes their purpose obvious months later.

**2. Add a step to the source repo's release workflow** using the composite action:

```yaml
- name: Publish to release-publisher
  uses: jirip/release-publisher/dispatch@master
  with:
    app: pdf2jpg
    version: ${{ steps.version.outputs.name }}     # e.g. 0.1.8 (no leading v)
    source-tag: v${{ steps.version.outputs.name }} # tag on this repo
    notes: ${{ steps.notes.outputs.body }}
    github-token: ${{ secrets.PUBLISH_TOKEN }}
    notify-key: ${{ secrets.NOTIFY_KEY }}
    telegram-bot-token: ${{ secrets.TELEGRAM_BOT_TOKEN }}  # optional; falls back to publisher's bot
```

That's it. The action reads `.github/notify.txt`, encrypts the recipient list with `NOTIFY_KEY`, fetches the source release's asset metadata via `gh release view`, and POSTs the dispatch.

Pin to a tag (`@v1`) once tags exist; for now the working branch reference (`@master`) is fine.

### Migrating an existing source repo

The publisher's wire format is unchanged, so any source repo still using the older inline-shell-and-Python snippet keeps working — migration is per-repo and can happen at any time. For repos that previously set `WEB_URL` (e.g. `pexesongy`), pass `set-web-url: true` along with `web-url:` so the publisher updates the manifest entry.

Migration checklist (tick off as each repo is moved to the composite action; once the list is empty, the inline-snippet path can be deleted from the publisher's history docs):

- [ ] `jirip/pdf2jpg`
- [ ] `jirip/pexesongy` (set `set-web-url: true` + `web-url: https://pexesongy.pages.dev/`)
- [ ] `jirip/prague-mhd-dashboard`
- [ ] `jirip/fridgeye`

### Web-wrapped apps (optional `web_url`)

Apps that ship both downloadable artifacts and a hosted web version (e.g. a Capacitor-wrapped PWA) can pass a `web_url` so the public page renders an **Open Web** button on the app's card.

Pass `set-web-url: true` along with `web-url:` to update it. The dispatch then sets the value at the app level in the manifest:

| `web-url:` | Effect |
| --- | --- |
| (omitted, `set-web-url: false`) | Preserve any previously stored value. **Default.** |
| `""` | Clear any previously stored value. |
| `https://...` | Set/overwrite. |

The URL lives at the app level (not per-release) because a hosted PWA only has one live version at a time.

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

`webUrl` is optional. Releases are kept in reverse-chronological order within each app (newest first), capped at `MAX_RELEASES_PER_APP` (currently 10) by the workflow.

## Trust model

`PUBLISH_TOKEN` is shared across every private source repo that wants to publish here. Anyone with the token can publish anything to any `app` namespace they like — including overwriting an existing app's manifest entry (subject to the value of `web_url`) or pushing a release whose body contains arbitrary Markdown. Treat `PUBLISH_TOKEN` as a privileged secret accordingly.

The workflow restricts what the dispatch payload can contain (regex on `app`/`version`, https-only asset URLs, allowed-character set on filenames, https-only `web_url`), but it does **not** authenticate which source repo dispatched the event — GitHub doesn't surface that on `repository_dispatch`.
