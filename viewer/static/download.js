// ── Downloader page: pick channels, start/stop, live progress ──
const $ = (id) => document.getElementById(id);

let logSince = 0;
let channelsLoaded = false;
let jobStartedAt = null;
let tgReady = false;
let jobRunning = false;

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

function fmtBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  return (n / 1024 ** i).toFixed(i ? 1 : 0) + " " + units[i];
}

function pct(done, total) {
  return total ? Math.min(100, (done / total) * 100) : 0;
}

function showMsg(text, isError) {
  const el = $("msg");
  el.textContent = text;
  el.className = "msg" + (isError ? " error" : "");
}

// ── Remembered choices (browser only) ────────────────────────
const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem("tg." + key)) ?? fallback; }
    catch (_) { return fallback; }
  },
  set(key, value) { localStorage.setItem("tg." + key, JSON.stringify(value)); },
};

function restoreOptions() {
  $("media").value = store.get("media", $("media").value);
  $("batch").value = store.get("batch", $("batch").value);
  $("max-msgs").value = store.get("maxMsgs", $("max-msgs").value);
}

function saveOptions() {
  store.set("media", $("media").value);
  store.set("batch", $("batch").value);
  store.set("maxMsgs", $("max-msgs").value);
}

["media", "batch", "max-msgs"].forEach((id) => $(id).addEventListener("change", saveOptions));
restoreOptions();

// ── Channel list ─────────────────────────────────────────────
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function removeButton(ident) {
  const btn = el("button", "rm", "×");
  btn.type = "button";
  btn.title = "Remove from list";
  btn.dataset.ident = ident;
  return btn;
}

async function loadChannels() {
  const { channels, failed } = await api("/api/tg/channels");
  const saved = new Set(store.get("selected", []));
  const box = $("ch-list");
  box.replaceChildren();
  if (!channels.length && !failed.length) box.appendChild(el("div", "empty", "No channels found."));

  for (const c of channels) {
    const row = el("label", "ch");
    row.dataset.search = (c.title + " " + (c.username || "") + " " + (c.extra || "")).toLowerCase();

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = c.id;
    cb.checked = saved.has(c.id);

    row.append(cb, el("span", "t", c.title));
    if (c.extra) row.appendChild(el("span", "tag", c.extra_source === "env" ? "extra · .env" : "extra"));
    row.appendChild(el("span", "k", c.kind + (c.username ? " · @" + c.username : "")));
    if (c.extra_source === "file") row.appendChild(removeButton(c.extra));
    box.appendChild(row);
  }

  for (const f of failed) {
    const row = el("div", "ch failed");
    row.dataset.search = f.ident.toLowerCase();
    row.append(el("span", "t", `Not found: ${f.ident}`), el("span", "k", f.error));
    if (f.source === "file") row.appendChild(removeButton(f.ident));
    box.appendChild(row);
  }

  channelsLoaded = true;
  $("ch-all").checked = false;
  applyFilter();
}

const checkedIds = () =>
  Array.from(document.querySelectorAll("#ch-list input:checked")).map((cb) => Number(cb.value));

function updateCount() {
  const n = checkedIds().length;
  $("ch-count").textContent = `${n} selected`;
  $("start").disabled = !tgReady || jobRunning || n === 0;
}

function saveSelection() {
  store.set("selected", checkedIds());
  updateCount();
}

function applyFilter() {
  const q = $("ch-filter").value.trim().toLowerCase();
  document.querySelectorAll("#ch-list .ch").forEach((row) => {
    row.hidden = q !== "" && !row.dataset.search.includes(q);
  });
  updateCount();
}

$("ch-filter").addEventListener("input", applyFilter);
$("ch-list").addEventListener("change", saveSelection);

$("ch-all").addEventListener("change", (e) => {
  document.querySelectorAll("#ch-list .ch:not([hidden]) input").forEach((cb) => {
    cb.checked = e.target.checked;
  });
  saveSelection();
});

$("ch-list").addEventListener("click", async (e) => {
  const btn = e.target.closest(".rm");
  if (!btn) return;
  e.preventDefault();
  btn.disabled = true;
  try {
    await api("/api/tg/channels/remove", { ident: btn.dataset.ident });
    await loadChannels();
    showMsg(`Removed ${btn.dataset.ident}.`);
  } catch (err) {
    btn.disabled = false;
    showMsg(err.message, true);
  }
});

$("add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ident = $("add-ident").value.trim();
  if (!ident) return;
  $("add-btn").disabled = true;
  try {
    const res = await api("/api/tg/channels/add", { ident });
    store.set("selected", [...new Set([...store.get("selected", []), res.id])]);
    await loadChannels();
    $("add-ident").value = "";
    showMsg(res.note ? `${ident}: ${res.note.toLowerCase()} (ticked).` : `Added ${ident}.`);
  } catch (err) {
    showMsg(err.message, true);
  } finally {
    $("add-btn").disabled = false;
  }
});

$("ch-refresh").addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true;
  btn.textContent = "Refreshing…";
  try {
    await api("/api/tg/channels/refresh", {});
    await loadChannels();
    showMsg("Channel list updated.");
  } catch (err) {
    showMsg(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Refresh list";
  }
});

// ── Start / stop ─────────────────────────────────────────────
$("start").addEventListener("click", async () => {
  try {
    await api("/api/tg/start", {
      channel_ids: checkedIds(),
      media: $("media").value,
      batch_size: Number($("batch").value),
      max_messages: Number($("max-msgs").value),
    });
    showMsg("Download started.");
  } catch (err) {
    showMsg(err.message, true);
  }
});

$("stop").addEventListener("click", async () => {
  try {
    await api("/api/tg/stop", {});
    showMsg("Stopping…");
  } catch (err) {
    showMsg(err.message, true);
  }
});

// ── Progress rendering ───────────────────────────────────────
function renderTelegram(t) {
  tgReady = t.status === "ready";
  $("tg-status").textContent =
    tgReady ? `Telegram: connected as ${t.account}` :
    t.status === "error" ? `Telegram: error — ${t.error}` :
    "Telegram: connecting… (first login? check the terminal)";
  $("ch-refresh").disabled = !tgReady;
  $("add-btn").disabled = !tgReady;
}

function renderActive(files) {
  const box = $("active");
  box.replaceChildren();
  for (const f of files) {
    const row = document.createElement("div");
    row.className = "file";

    const label = document.createElement("div");
    label.className = "bar-label";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = f.name;
    const size = document.createElement("span");
    size.textContent = `${fmtBytes(f.done)} / ${fmtBytes(f.total)}`;
    label.append(name, size);

    const bar = document.createElement("div");
    bar.className = "bar small";
    const fill = document.createElement("div");
    fill.style.width = pct(f.done, f.total) + "%";
    bar.appendChild(fill);

    row.append(label, bar);
    box.appendChild(row);
  }
}

function appendLog(entries) {
  const box = $("log");
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
  for (const e of entries) {
    const line = document.createElement("div");
    line.className = "line " + e.level;
    const time = new Date(e.t * 1000).toLocaleTimeString();
    line.textContent = `${time}  ${e.msg}`;
    box.appendChild(line);
    logSince = Math.max(logSince, e.id);
  }
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderJob(j) {
  if (j.started_at !== jobStartedAt) {
    jobStartedAt = j.started_at;
    $("log").replaceChildren();
  }
  jobRunning = j.running;

  $("job-state").textContent =
    j.running ? (j.stopping ? "Stopping…" : `Running · ${j.media}`) :
    j.finished_at ? `${j.stopped ? "Stopped" : "Finished"} at ${new Date(j.finished_at * 1000).toLocaleTimeString()}` :
    "Idle";
  $("job-state").className = "job-state" + (j.running ? " running" : "");

  $("ch-prog").textContent = `${j.channel_index} / ${j.channel_count}`;
  $("ch-bar").style.width = pct(j.channel_index, j.channel_count) + "%";
  $("cur-channel").textContent = j.channel_title || "—";
  $("file-prog").textContent = `${j.files_done} / ${j.files_total} files`;
  $("file-bar").style.width = pct(j.files_done, j.files_total) + "%";

  $("t-d").textContent = j.downloaded;
  $("t-s").textContent = j.skipped;
  $("t-e").textContent = j.errors;
  $("t-dup").textContent = j.duplicates;

  $("stop").disabled = !j.running || j.stopping;
  renderActive(j.active);
  appendLog(j.log);
  updateCount();
}

async function poll() {
  try {
    const s = await api("/api/tg/status?since=" + logSince);
    renderTelegram(s.telegram);
    if (tgReady && !channelsLoaded) await loadChannels();
    renderJob(s.job);
  } catch (_) {
    $("tg-status").textContent = "Server unreachable";
  }
  setTimeout(poll, 1000);
}

poll();
