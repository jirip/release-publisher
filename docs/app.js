// Renders docs/releases.json into the page. Untrusted strings — anything that came from a
// dispatch payload — must always go through escapeHtml before being interpolated into HTML.

const OPEN_KEY_PREFIX = "app-open:";

const ASSET_LABELS = {
  exe: "Windows (.exe)",
  apk: "Android (.apk)",
  aab: "Android (.aab)",
  dmg: "macOS (.dmg)",
  zip: "ZIP archive",
  tar: "tarball",
};

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

const fmtDate = (iso) => new Date(iso).toISOString().slice(0, 10);

const assetLabel = (name) => {
  const ext = name.split(".").pop().toLowerCase();
  return ASSET_LABELS[ext] || name;
};

// Only allow https links — release URLs and webUrls are produced by the workflow and
// are always https. Anything else is treated as untrusted and dropped.
const isSafeHref = (url) => {
  try {
    return new URL(url, window.location.href).protocol === "https:";
  } catch {
    return false;
  }
};

const isAppOpen = (name) => {
  try {
    return localStorage.getItem(OPEN_KEY_PREFIX + name) === "1";
  } catch {
    return false;
  }
};

const setAppOpen = (name, open) => {
  try {
    if (open) localStorage.setItem(OPEN_KEY_PREFIX + name, "1");
    else localStorage.removeItem(OPEN_KEY_PREFIX + name);
  } catch {
    // storage disabled (private mode, locked-down browsers); persistence is a nicety.
  }
};

// Assets with `hidden: true` are still in the manifest (so the Worker / OTA / API consumers
// can resolve URLs) but aren't drawn on the page.
const visibleAssets = (assets) => (assets || []).filter((a) => a.hidden !== true);

const renderAssets = (assets) =>
  visibleAssets(assets)
    .filter((a) => isSafeHref(a.url))
    .map((a) => {
      const text = a.label && a.label.length > 0 ? a.label : assetLabel(a.name);
      return `<a href="${escapeHtml(a.url)}">${escapeHtml(text)}</a>`;
    })
    .join("");

const renderHistory = (history) => {
  if (history.length === 0) return "";
  const items = history
    .map(
      (r) => `<li>
      <span class="history-meta">${escapeHtml(r.version)} &middot; ${fmtDate(r.date)}</span>
      <span class="history-assets">${renderAssets(r.assets)}</span>
    </li>`,
    )
    .join("");
  return `
    <details class="history">
      <summary>Previous versions (${history.length})</summary>
      <ul>${items}</ul>
    </details>`;
};

const renderApp = (name, app) => {
  const releases = [...(app.releases || [])].sort((a, b) => (a.date < b.date ? 1 : -1));
  const latest = releases[0];
  const history = releases.slice(1);

  const webButton =
    app.webUrl && isSafeHref(app.webUrl)
      ? `<a class="web-link" href="${escapeHtml(app.webUrl)}" target="_blank" rel="noopener">Open Web</a>`
      : "";
  const notes = latest.notes ? `<p class="app-notes">${escapeHtml(latest.notes)}</p>` : "";
  const openAttr = isAppOpen(name) ? " open" : "";

  return `
    <details class="app" data-app="${escapeHtml(name)}"${openAttr}>
      <summary class="app-header">
        <h2 class="app-name">${escapeHtml(name)}</h2>
        ${webButton}
        <span class="app-version">${escapeHtml(latest.version)} &middot; ${fmtDate(latest.date)}</span>
      </summary>
      <div class="app-body">
        ${notes}
        <div class="downloads">${renderAssets(latest.assets)}</div>
        ${renderHistory(history)}
      </div>
    </details>`;
};

// An app whose latest release has no visible assets is intentionally invisible —
// e.g. fridgeye-firmware, where every artifact is published purely so the device
// can OTA from a known URL, not so a human ever clicks it.
const hasVisibleLatest = (app) => {
  const releases = app.releases || [];
  if (releases.length === 0) return false;
  const latest = [...releases].sort((a, b) => (a.date < b.date ? 1 : -1))[0];
  return visibleAssets(latest.assets).length > 0;
};

const render = (data) => {
  const root = document.getElementById("apps");
  const apps = data.apps || {};
  const names = Object.keys(apps)
    .filter((n) => hasVisibleLatest(apps[n]))
    .sort();

  if (names.length === 0) {
    root.innerHTML = '<p class="empty">No releases yet.</p>';
    return;
  }

  root.innerHTML = names.map((name) => renderApp(name, apps[name])).join("");

  for (const el of root.querySelectorAll("details.app")) {
    el.addEventListener("toggle", () => setAppOpen(el.dataset.app, el.open));
    // Clicking the "Open Web" button shouldn't toggle the surrounding <details>.
    for (const a of el.querySelectorAll("summary.app-header a.web-link")) {
      a.addEventListener("click", (e) => e.stopPropagation());
    }
  }
};

const showError = () => {
  document.getElementById("apps").innerHTML = '<p class="empty">Failed to load releases.</p>';
};

fetch("releases.json", { cache: "no-store" })
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then(render)
  .catch((e) => {
    console.error(e);
    showError();
  });
