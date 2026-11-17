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
  const { src, type, name, size, ml } = el.dataset;

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
  info.replaceChildren(pos, ` · ${name} · ${size}${ml ? " · " + ml : ""}`);
  const dl = document.createElement("a");
  dl.href = src + "&download=1";
  dl.textContent = "⬇ download";
  dl.setAttribute("download", "");
  info.appendChild(dl);

  showRating(el.dataset.rating);
  setRateMsg("", "");
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
  if (RATING_KEYS[e.key]) rate(RATING_KEYS[e.key]);
});

document.getElementById("lb").addEventListener("click", (e) => {
  if (e.target.id === "lb") closeLb();
});

// ── Swipe: left = next, right = previous ─────────────────────
const SWIPE_MIN = 50;
let touchStart = null;

document.getElementById("lb").addEventListener("touchstart", (e) => {
  touchStart = null;
  if (e.touches.length !== 1 || e.target.closest("button, audio")) return;
  const t = e.touches[0];
  // Leave the bottom of a video alone: that is where its controls are.
  const video = e.target.closest("video");
  if (video && t.clientY > video.getBoundingClientRect().bottom - 60) return;
  touchStart = { x: t.clientX, y: t.clientY };
}, { passive: true });

document.getElementById("lb").addEventListener("touchend", (e) => {
  if (!touchStart || currentIdx < 0) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - touchStart.x;
  const dy = t.clientY - touchStart.y;
  touchStart = null;
  if (Math.abs(dx) >= SWIPE_MIN && Math.abs(dx) > Math.abs(dy) * 1.5) navLb(dx < 0 ? 1 : -1);
}, { passive: true });

// ── Rating: good / bad / delete, saved in the database ───────
const RATING_KEYS = { "1": "good", "2": "bad", "3": "delete" };
const RATING_LABELS = { good: "Good", bad: "Not good", delete: "To delete" };

function showRating(rating) {
  document.querySelectorAll("#lb .rate").forEach((b) => {
    b.classList.toggle("active", b.dataset.rating === rating);
  });
}

function setRateMsg(text, cls) {
  const m = document.getElementById("lb-rate-msg");
  m.textContent = text;
  m.className = "rate-msg " + cls;
}

async function rate(rating) {
  const tile = allTiles()[currentIdx];
  if (!tile) return;
  // Pressing the current rating again clears it.
  const next = tile.dataset.rating === rating ? null : rating;
  setRateMsg("Saving…", "");
  try {
    const resp = await fetch("/api/rate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: tile.dataset.rel, rating: next }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
    tile.dataset.rating = next || "";
    if (allTiles()[currentIdx] === tile) showRating(tile.dataset.rating);
    const text = next ? `Saved: ${RATING_LABELS[next]}` : "Rating removed";
    setRateMsg(`${text} · ${data.marked} file(s) marked to delete`, "ok");
  } catch (err) {
    setRateMsg("Not saved: " + err.message, "error");
  }
}

document.querySelector("#lb .rate-bar").addEventListener("click", (e) => {
  const b = e.target.closest(".rate");
  if (b) rate(b.dataset.rating);
});
