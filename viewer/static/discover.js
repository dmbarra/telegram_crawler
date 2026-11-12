// ── Discover page: public search, invite links, safe joining ──
const $ = (id) => document.getElementById(id);

let tgReady = false;
let results = [];              // last public search / suggestions
let resultsTitle = "";
let lastInvitesJson = "";
let invites = [];
let scanListLoaded = false;
let toastTimer = null;

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
  return data;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function button(label, cls, onClick) {
  const b = el("button", "btn small " + (cls || ""), label);
  b.type = "button";
  b.addEventListener("click", async () => {
    b.disabled = true;
    try { await onClick(); } finally { b.disabled = false; }
  });
  return b;
}

function toast(text, isError) {
  const t = $("toast");
  t.textContent = text;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, isError ? 7000 : 3500);
}

function fmtCount(n) {
  if (n === null || n === undefined) return "";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function fmtWait(s) {
  s = Math.ceil(s);
  if (s < 120) return `${s} s`;
  const m = Math.ceil(s / 60);
  return m < 120 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function tag(text, cls) { return el("span", "tag " + (cls || ""), text); }

// ── Public search ────────────────────────────────────────────
function renderResults() {
  const box = $("results");
  box.replaceChildren();
  $("search-info").textContent = resultsTitle;
  for (const r of results) {
    const card = el("div", "result");
    const top = el("div", "result-top");
    top.appendChild(el("span", "result-title", r.title));
    top.appendChild(tag(r.kind));
    if (r.members) top.appendChild(tag(`${fmtCount(r.members)} members`));
    if (r.is_member) top.appendChild(tag("member", "ok"));
    if (r.in_list) top.appendChild(tag("in download list", "ok"));
    if (r.needs_approval) top.appendChild(tag("needs approval", "warn"));
    if (r.scam) top.appendChild(tag("marked SCAM / FAKE by Telegram", "error"));
    card.appendChild(top);

    if (r.username) {
      const a = el("a", "result-link", "@" + r.username);
      a.href = "https://t.me/" + encodeURIComponent(r.username);
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      card.appendChild(a);
    }

    const details = el("div", "result-details");
    const actions = el("div", "row actions");
    actions.appendChild(button("Details", "", async () => {
      try {
        const d = await api("/api/discover/details", { id: r.id });
        details.replaceChildren();
        if (d.about) details.appendChild(el("p", "about", d.about));
        const last = d.last_post ? new Date(d.last_post * 1000).toLocaleDateString() : "never";
        details.appendChild(el("p", "muted",
          `${fmtCount(d.members)} members · last post ${last} · ${d.photos} photos · ${d.videos} videos`));
      } catch (err) { toast(err.message, true); }
    }));
    actions.appendChild(button("Similar", "", async () => {
      try {
        const data = await api("/api/discover/similar", { id: r.id });
        results = data.results;
        resultsTitle = `${results.length} suggestions similar to "${r.title}"`;
        renderResults();
      } catch (err) { toast(err.message, true); }
    }));
    if (!r.in_list && r.username) {
      actions.appendChild(button("Add to download list", "", async () => {
        try {
          const res = await api("/api/discover/add", { id: r.id });
          r.in_list = true;
          toast(res.note ? `${r.title}: ${res.note.toLowerCase()}` : `Added "${r.title}" to the download list.`);
          renderResults();
        } catch (err) { toast(err.message, true); }
      }));
    }
    if (!r.is_member) {
      const label = r.needs_approval ? "Request to join" : "Join";
      actions.appendChild(button(label, "primary", async () => {
        if (r.scam && !confirm("Telegram marked this as scam or fake. Join anyway?")) return;
        try {
          const res = await api("/api/discover/join", { id: r.id });
          r.is_member = res.status === "joined" || res.status === "member";
          toast(statusText(res.status, r.title));
          renderResults();
          refreshState();
        } catch (err) { toast(err.message, true); }
      }));
    }
    card.append(actions, details);
    box.appendChild(card);
  }
}

$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  $("search-btn").disabled = true;
  $("search-info").textContent = "Searching…";
  try {
    const data = await api("/api/discover/search", { q });
    results = data.results;
    resultsTitle = results.length ? `${results.length} results for "${q}"` : `Nothing found for "${q}".`;
    renderResults();
  } catch (err) {
    $("search-info").textContent = "";
    toast(err.message, true);
  } finally {
    $("search-btn").disabled = false;
  }
});

function statusText(status, title) {
  return {
    joined: `Joined "${title}". It is now in your download list.`,
    requested: `Join request sent to "${title}". An admin must approve it.`,
    member: `You are already a member of "${title}".`,
    expired: "This invite link has expired.",
  }[status] || status;
}

// ── Invite links ─────────────────────────────────────────────
const STATUS_TAG = {
  new: ["not joined", ""],
  member: ["member", "ok"],
  joined: ["joined", "ok"],
  requested: ["request pending", "warn"],
  expired: ["expired", "error"],
};

function renderInvites() {
  const q = $("inv-filter").value.trim().toLowerCase();
  const showIgnored = $("inv-ignored").checked;
  const box = $("invites");
  box.replaceChildren();
  let shown = 0;
  for (const it of invites) {
    if (it.ignored && !showIgnored) continue;
    const p = it.preview || {};
    const haystack = [p.title, p.about, it.context, it.sources.join(" ")].join(" ").toLowerCase();
    if (q && !haystack.includes(q)) continue;
    shown++;

    const card = el("div", "result" + (it.ignored ? " ignored" : ""));
    const top = el("div", "result-top");
    top.appendChild(el("span", "result-title", p.title || "Not previewed yet"));
    const [label, cls] = STATUS_TAG[it.status] || [it.status, ""];
    top.appendChild(tag(label, cls));
    if (p.kind) top.appendChild(tag(p.kind));
    if (p.members) top.appendChild(tag(`${fmtCount(p.members)} members`));
    if (p.needs_approval) top.appendChild(tag("needs approval", "warn"));
    if (p.scam) top.appendChild(tag("marked SCAM / FAKE by Telegram", "error"));
    if (it.ignored) top.appendChild(tag("ignored"));
    card.appendChild(top);

    const link = el("a", "result-link", it.link);
    link.href = it.link;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.appendChild(link);
    if (p.about) card.appendChild(el("p", "about", p.about));
    card.appendChild(el("div", "muted small-text", "Found in: " + it.sources.join(", ")));
    if (it.context) card.appendChild(el("div", "context", it.context));

    const actions = el("div", "row actions");
    if (it.status !== "expired") {
      actions.appendChild(button(it.preview ? "Preview again" : "Preview", "", async () => {
        try {
          await api("/api/discover/invites/preview", { hash: it.hash });
          await refreshState();
        } catch (err) { toast(err.message, true); }
      }));
    }
    if (it.status === "new") {
      const joinLabel = p.needs_approval ? "Request to join" : "Join";
      actions.appendChild(button(joinLabel, "primary", async () => {
        if (!it.preview && !confirm("You have not previewed this group yet. Join anyway?")) return;
        if (p.scam && !confirm("Telegram marked this as scam or fake. Join anyway?")) return;
        try {
          const res = await api("/api/discover/invites/join", { hash: it.hash });
          toast(statusText(res.status, p.title || "the group"));
          await refreshState();
        } catch (err) { toast(err.message, true); }
      }));
    }
    actions.appendChild(button(it.ignored ? "Unignore" : "Ignore", "", async () => {
      try {
        await api("/api/discover/invites/ignore", { hash: it.hash, ignored: !it.ignored });
        await refreshState();
      } catch (err) { toast(err.message, true); }
    }));
    card.appendChild(actions);
    box.appendChild(card);
  }
  $("inv-count").textContent = `${shown} of ${invites.length} shown`;
  if (!invites.length) box.appendChild(el("div", "empty", "No invite links yet. Paste one, or scan your groups."));
}

$("inv-filter").addEventListener("input", renderInvites);
$("inv-ignored").addEventListener("change", renderInvites);

$("paste-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("paste").value.trim();
  if (!text) return;
  $("paste-btn").disabled = true;
  try {
    const res = await api("/api/discover/invites/add", { text });
    $("paste").value = "";
    toast(res.new ? `Added ${res.new} new link(s). Press Preview to see the group.` : "Link was already in the list.");
    await refreshState();
  } catch (err) {
    toast(err.message, true);
  } finally {
    $("paste-btn").disabled = !tgReady;
  }
});

// ── Scan your groups for invite links ────────────────────────
async function loadScanList() {
  const { channels } = await api("/api/tg/channels");
  const box = $("scan-list");
  box.replaceChildren();
  for (const c of channels) {
    const row = el("label", "ch");
    row.dataset.search = (c.title + " " + (c.username || "")).toLowerCase();
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = c.id;
    row.append(cb, el("span", "t", c.title), el("span", "k", c.kind));
    box.appendChild(row);
  }
  if (!channels.length) box.appendChild(el("div", "empty", "No groups."));
  scanListLoaded = true;
  updateScanCount();
}

const scanIds = () =>
  Array.from(document.querySelectorAll("#scan-list input:checked")).map((cb) => Number(cb.value));

function updateScanCount() {
  $("scan-count").textContent = `${scanIds().length} selected`;
}

$("scan-list").addEventListener("change", updateScanCount);
$("scan-filter").addEventListener("input", () => {
  const q = $("scan-filter").value.trim().toLowerCase();
  document.querySelectorAll("#scan-list .ch").forEach((row) => {
    row.hidden = q !== "" && !row.dataset.search.includes(q);
  });
});
$("scan-all").addEventListener("change", (e) => {
  document.querySelectorAll("#scan-list .ch:not([hidden]) input").forEach((cb) => {
    cb.checked = e.target.checked;
  });
  updateScanCount();
});

$("scan-start").addEventListener("click", async () => {
  try {
    await api("/api/discover/scan", {
      channel_ids: scanIds(),
      limit: Number($("scan-limit").value),
      keywords: $("scan-kw").value,
    });
    await refreshState();
  } catch (err) { toast(err.message, true); }
});

$("scan-stop").addEventListener("click", async () => {
  try { await api("/api/discover/scan/stop", {}); } catch (err) { toast(err.message, true); }
});

function renderScan(s) {
  $("scan-start").disabled = !tgReady || s.running;
  $("scan-stop").disabled = !s.running;
  let text = "";
  if (s.running) {
    text = `Scanning ${s.done + 1}/${s.total}: ${s.current} · ${s.new} new link(s) so far`;
  } else if (s.finished_at) {
    text = `${s.stopped ? "Stopped" : "Done"}: ${s.done}/${s.total} groups, ` +
           `${s.seen} link(s) seen, ${s.new} new.`;
    if (s.errors.length) text += ` Problems: ${s.errors.join("; ")}`;
  }
  $("scan-info").textContent = text;
}

// ── Join limits + polling ────────────────────────────────────
function renderJoins(j) {
  $("j-count").textContent = j.joins_today;
  $("j-next").textContent = j.wait > 0 ? `next join in ${fmtWait(j.wait)}` : "next join: now";
  $("join-bar").classList.toggle("blocked", j.wait > 0);
  if (j.reason && j.wait > 60) $("j-next").textContent = j.reason;
}

async function refreshState() {
  const s = await api("/api/discover/state");
  renderScan(s.scan);
  renderJoins(s.joins);
  const json = JSON.stringify(s.invites);
  if (json !== lastInvitesJson) {
    lastInvitesJson = json;
    invites = s.invites;
    renderInvites();
  }
}

async function poll() {
  try {
    const t = (await api("/api/tg/status?since=999999999")).telegram;
    tgReady = t.status === "ready";
    $("tg-status").textContent =
      tgReady ? `Telegram: connected as ${t.account}` :
      t.status === "error" ? `Telegram: error — ${t.error}` : "Telegram: connecting…";
    $("search-btn").disabled = !tgReady;
    $("paste-btn").disabled = !tgReady;
    if (tgReady && !scanListLoaded) await loadScanList();
    await refreshState();
  } catch (_) {
    $("tg-status").textContent = "Server unreachable";
  }
  setTimeout(poll, 2000);
}

poll();
