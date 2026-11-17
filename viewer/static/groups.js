// ── Automatic groups: Group new / Rebuild / tightness ────────
(() => {
  const box = document.getElementById("grp-box");
  if (!box) return;
  const kind = box.dataset.kind;
  const newBtn = document.getElementById("grp-new");
  const rebuildBtn = document.getElementById("grp-rebuild");
  const level = document.getElementById("grp-level");
  const msg = document.getElementById("grp-msg");
  const KIND = { look: "By look", face: "By face" };
  let wasRunning = false;

  const setMsg = (text, cls) => { msg.textContent = text; msg.className = "msg " + (cls || ""); };
  const post = async (url, body) => {
    const resp = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
    return data;
  };
  const what = (k) => (k === "face" ? "faces" : "files");
  const result = (k, r) => r && r.items
    ? `${KIND[k]}: ${r.items} ${what(k)} → ${r.joined} joined a group, ${r.created} new group(s), ${r.merged} merge(s)`
    : `${KIND[k]}: nothing new`;

  async function refresh() {
    let s;
    try {
      s = await (await fetch("/api/groups/status")).json();
    } catch (err) {
      setMsg("Could not read the grouping status: " + err.message, "error");
      setTimeout(refresh, 5000);
      return;
    }
    newBtn.disabled = rebuildBtn.disabled = s.running;
    const sum = s.summary[kind];
    const base = `${sum.groups} group(s) · ${sum.items} ${what(kind)} grouped · ${s.new[kind]} new ${what(kind)} waiting`;

    if (s.running) {
      wasRunning = true;
      const step = s.kind ? `${s.mode === "rebuild" ? "Rebuilding" : "Grouping"} by ${s.kind}: ${s.done} of ${s.total}` : "Starting…";
      setMsg(step);
      setTimeout(refresh, 1000);
      return;
    }
    if (s.error) {
      setMsg(`Grouping failed: ${s.error}. ${base}`, "error");
    } else if (wasRunning) {
      const took = Math.max(1, Math.round(s.finished_at - s.started_at));
      setMsg(`Done in ${took}s. ${result("look", s.results.look)}; ${result("face", s.results.face)}. Reloading…`, "ok");
      setTimeout(() => location.reload(), 2500);
    } else {
      setMsg(base);
      newBtn.disabled = s.new.look + s.new.face === 0;
    }
  }

  newBtn.addEventListener("click", async () => {
    newBtn.disabled = rebuildBtn.disabled = true;
    try { await post("/api/groups/start", { rebuild: false }); refresh(); }
    catch (err) { setMsg("Not started: " + err.message, "error"); refresh(); }
  });

  rebuildBtn.addEventListener("click", async () => {
    if (!confirm("Rebuild all groups, by look and by face, with the current tightness?\n\n" +
                 "Every group is made again from the saved vectors. No ML run is needed.")) return;
    newBtn.disabled = rebuildBtn.disabled = true;
    try { await post("/api/groups/start", { rebuild: true }); refresh(); }
    catch (err) { setMsg("Not started: " + err.message, "error"); refresh(); }
  });

  level.addEventListener("change", async () => {
    try {
      const d = await post("/api/groups/tightness", { kind, level: Number(level.value) });
      level.value = d.level;
      setMsg(`Saved tightness ${d.level}. New ${what(kind)} use it now. Press Rebuild all to apply it to every group.`, "ok");
    } catch (err) {
      setMsg("Not saved: " + err.message, "error");
    }
  });

  refresh();
})();
