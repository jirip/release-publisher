# release-publisher

Public mirror for release artifacts (`.exe`, `.apk`, …) built from private repos.

Live page: <https://jirip.github.io/release-publisher/>

## How it works

1. A private repo finishes a release build and POSTs a `repository_dispatch` event of type `publish-release` to this repo, carrying the app name, version, source tag, and asset download URLs.
2. The `republish` workflow downloads the assets from the private repo (using `SOURCE_REPO_TOKEN`), creates a public release here tagged `<app>-v<version>`, appends an entry to `docs/releases.json`, and commits.
3. GitHub Pages serves `docs/`, which renders cards per app with direct download links to the latest version.

## Wiring up a new private repo

**1. Create two fine-grained PATs:**

- **`release-publisher-read`** — `contents: read` on every private source repo.
  Stored as `SOURCE_REPO_TOKEN` in `jirip/release-publisher` → used by the republish workflow to download assets from the private release.

- **`release-publisher-write`** — `contents: write` + `metadata: read` on `jirip/release-publisher`.
  Stored as `PUBLISH_TOKEN` in *each* private source repo → used by the private repo's release workflow to trigger `repository_dispatch` here and to read its own release (`gh release view`).

Naming the PATs `release-publisher-read` / `release-publisher-write` in the GitHub token UI makes their purpose obvious when you come back months later.

**2. Append a dispatch step** to the private repo's release workflow, after the release is created:

```yaml
- name: Dispatch to release-publisher
  env:
    GH_TOKEN: ${{ secrets.PUBLISH_TOKEN }}
    APP: <app-name>                                 # e.g. pdf2jpg
    VERSION: ${{ steps.version.outputs.name }}     # e.g. 0.1.8 (no leading v)
    SOURCE_TAG: v${{ steps.version.outputs.name }} # matches the tag on this repo
    NOTES: ${{ steps.notes.outputs.body }}
  run: |
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
      --argjson assets "$ASSETS" \
      '{event_type: "publish-release", client_payload: {app: $app, version: $version, source_repo: $source_repo, source_tag: $source_tag, notes: $notes, assets: $assets}}' \
    | curl -fsS -X POST \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $GH_TOKEN" \
        "https://api.github.com/repos/jirip/release-publisher/dispatches" \
        --data-binary @-
```

Asset URLs must be API URLs (`.apiUrl`), not browser URLs — the republisher downloads them with a `Bearer` token and `Accept: application/octet-stream`.

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
    }
  }
}
```

Releases are kept in reverse-chronological order within each app (newest first).
