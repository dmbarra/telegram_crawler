// ── Config from body data-* ──────────────────────────────────
const PAGE_SIZE = parseInt(document.body.dataset.pageSize || "20", 10);
const CURRENT_TYPE = document.body.dataset.currentType || "";
const CURRENT_QUERY = document.body.dataset.currentQuery || "";
const CURRENT_SORT = document.body.dataset.currentSort || "newest";

// ── Tile accessors ───────────────────────────────────────────
const allTiles = () => Array.from(document.querySelectorAll(".tile[data-src]"));
let currentIdx = -1;

// ── Force-abort any in-flight media in the lightbox ──────────
// Without this, navigating fast leaves orphan network requests that
// keep the server busy for minutes.
function cleanupMedia() {
  const media = document.getElementById("lb-media");
  media.querySelectorAll("video, audio").forEach((el) => {
    try {
      el.pause();
      el.removeAttribute("src");
      el.load();          // ← tells browser to abort network request
    } catch (_) { /* ignore */ }
  });
  media.innerHTML = "";
}

// ── Delegated click handler ──────────────────────────────────
document.getElementById("main").addEventListener("click", (e) => {
  const loadBtn = e.target.closest(".load-more");
  if (loadBtn) { handleLoadMore(loadBtn); return; }

  const tile = e.target.closest(".tile[data-src]");
  if (tile) openLbByElement(tile);
});

// ── Lightbox ─────────────────────────────────────────────────
function openLbByElement(el) {
  const idx = allTiles().indexOf(el);
  if (idx >= 0) openLb(idx);
}

function openLb(i) {
  const all = allTiles();
  if (i < 0 || i >= all.length) return;
  currentIdx = i;

  const el = all[i];
  const { src, type, name, size } = el.dataset;

  // CRITICAL: abort the previous video BEFORE creating the new one
  cleanupMedia();

  const media = document.getElementById("lb-media");

  if (type === "image") {
    const img = document.createElement("img");
    img.src = src; img.decoding = "async";
    media.appendChild(img);
  } else if (type === "video") {
    const v = document.createElement("video");
    v.preload = "metadata";          // ← only fetch headers, not the whole file
    v.src = src;
    v.controls = true;
    v.autoplay = true;
    media.appendChild(v);
  } else {
    const a = document.createElement("audio");
    a.preload = "metadata";
    a.src = src;
    a.controls = true;
    a.autoplay = true;
    media.appendChild(a);
  }

  const info = document.getElementById("lb-info");
  info.innerHTML = `<b>${i + 1}/${all.length}</b> · ${name} · ${size}`;
  const dl = document.createElement("a");
  dl.href = src + "&download=1";
  dl.textContent = "⬇ download";
  dl.setAttribute("download", "");
  info.appendChild(dl);

  document.getElementById("lb").classList.add("open");
}

function closeLb() {
  cleanupMedia();                       // ← abort before hiding
  document.getElementById("lb").classList.remove("open");
  currentIdx = -1;
}

function navLb(delta) {
  const next = currentIdx + delta;
  if (next >= 0 && next < allTiles().length) openLb(next);
}

document.addEventListener("keydown", (e) => {
  if (currentIdx < 0) return;
  if (e.key === "Escape") closeLb();
  if (e.key === "ArrowLeft") navLb(-1);
  if (e.key === "ArrowRight") navLb(1);
});

document.getElementById("lb").addEventListener("click", (e) => {
  if (e.target.id === "lb") closeLb();
});

// ── Load More ────────────────────────────────────────────────
function buildTile(f) {
  const src = "/media?p=" + encodeURIComponent(f.rel);
  const div = document.createElement("div");
  div.className = "tile" + (f.type !== "image" ? " " + f.type : "");
  div.dataset.src = src;
  div.dataset.type = f.type;
  div.dataset.name = f.name;
  div.dataset.size = f.size_str;

  if (f.type === "image") {
    const img = document.createElement("img");
    img.src = src; img.loading = "lazy"; img.decoding = "async"; img.alt = "";
    div.appendChild(img);
  } else if (f.type === "video") {
    const img = document.createElement("img");
    img.className = "thumb";
    img.src = "/thumb?p=" + encodeURIComponent(f.rel);
    img.loading = "lazy"; img.decoding = "async"; img.alt = "";
    img.onerror = () => img.remove();
    div.appendChild(img);

    const badge = document.createElement("div");
    badge.className = "play-badge";
    badge.textContent = "▶";
    div.appendChild(badge);
  }

  const lbl = document.createElement("div");
  lbl.className = "label";
  lbl.textContent = f.name;
  div.appendChild(lbl);
  return div;
}

async function handleLoadMore(btn) {
  if (btn.disabled) return;
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "Loading…";

  const params = new URLSearchParams({
    channel: btn.dataset.channel,
    offset: btn.dataset.offset,
    limit: PAGE_SIZE,
    type: CURRENT_TYPE,
    q: CURRENT_QUERY,
    sort: CURRENT_SORT,
  });

  try {
    const resp = await fetch("/api/files?" + params.toString());
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();

    const section = btn.closest(".channel");
    const grid = section.querySelector(".grid");
    const frag = document.createDocumentFragment();
    for (const f of data.files) frag.appendChild(buildTile(f));
    grid.appendChild(frag);

    const countEl = section.querySelector("h2 .count");
    if (countEl) countEl.textContent = `(${data.next_offset} / ${data.total})`;

    if (data.has_more) {
      btn.dataset.offset = data.next_offset;
      btn.textContent = `Load more (${data.total - data.next_offset} remaining)`;
      btn.disabled = false;
    } else {
      btn.remove();
    }
  } catch (err) {
    console.error(err);
    btn.textContent = "Error — retry";
    btn.disabled = false;
    setTimeout(() => { btn.textContent = oldText; }, 2000);
  }
}