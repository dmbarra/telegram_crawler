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
  const pos = document.createElement("b");
  pos.textContent = `${i + 1}/${all.length}`;
  info.replaceChildren(pos, ` · ${name} · ${size}`);
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
