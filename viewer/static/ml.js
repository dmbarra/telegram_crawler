// ── ML analysis box on the channel page ──────────────────────
(() => {
  const box = document.getElementById("ml-box");
  if (!box) return;
  const channel = Number(box.dataset.channel);
  const study = document.getElementById("study-src");
  const startBtn = document.getElementById("ml-start");
  const stopBtn = document.getElementById("ml-stop");
  const msg = document.getElementById("ml-msg");
  let timer = null;

  const setMsg = (text, cls) => { msg.textContent = text; msg.className = "msg " + (cls || ""); };
  const post = async (url, body) => {
    const resp = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || "HTTP " + resp.status);
    return data;
  };
  const dur = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);

  function summary(c) {
    const left = c.images - c.done;
    return `${c.done} of ${c.images} images/videos analyzed · ${left} new · ${c.errors} error(s) · ` +
           `${c.with_faces} image(s) with faces (${c.faces} faces)`;
  }

  function lastRun(s) {
    if (s.channel_id !== channel || !s.finished_at) return "";
    const took = dur(s.finished_at - s.started_at);
    if (s.result === "failed") return `Stopped after ${s.done} of ${s.total}: ${s.message}`;
    if (s.result === "stopped") return `Stopped by you after ${s.done} of ${s.total} images (${took}).`;
    return `Done: analyzed ${s.done} image(s) in ${took} · ${s.errors} error(s) · ${s.faces} face(s).`;
  }

  async function refresh() {
    clearTimeout(timer);
    let s;
    try {
      s = await (await fetch(`/api/ml/status?channel=${channel}`)).json();
    } catch (err) {
      setMsg("Could not read the analysis status: " + err.message, "error");
      timer = setTimeout(refresh, 5000);
      return;
    }
    study.checked = s.study_source;
    const mine = s.running && s.channel_id === channel;
    stopBtn.hidden = !mine;
    startBtn.disabled = s.running || !s.study_source || s.channel.done >= s.channel.images;

    if (mine) {
      const elapsed = Date.now() / 1000 - s.started_at;
      const per = s.done ? elapsed / s.done : 0;
      const eta = per ? ` · about ${dur(per * (s.total - s.done))} left` : "";
      setMsg(`Analyzing ${s.done} of ${s.total} · ${s.errors} error(s) · ${s.faces} face(s)` +
             (per ? ` · ${per.toFixed(1)} s/image` : "") + eta);
      timer = setTimeout(refresh, 1500);
      return;
    }
    if (s.running) {
      setMsg(`Busy: another channel is being analyzed (${s.done} of ${s.total}). ${summary(s.channel)}`);
      timer = setTimeout(refresh, 3000);
      return;
    }
    const last = lastRun(s);
    const base = s.study_source ? summary(s.channel) : "Turn on Study source to analyze this channel.";
    setMsg(last ? `${last} ${base}` : base,
           s.channel_id === channel && s.result === "failed" ? "error" : last ? "ok" : "");
  }

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    setMsg("Checking the ML service…");
    try {
      await post("/api/ml/start", { channel });
      refresh();
    } catch (err) {
      setMsg("Not started: " + err.message, "error");
      startBtn.disabled = false;
    }
  });

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try { await post("/api/ml/stop", {}); } catch (err) { setMsg("Stop failed: " + err.message, "error"); }
    stopBtn.disabled = false;
    refresh();
  });

  study.addEventListener("change", async () => {
    const on = study.checked;
    if (on && !confirm("Turn on Study source?\n\nOnly do this if every image in this channel comes from " +
                       "consenting patients or a public medical dataset.")) {
      study.checked = false;
      return;
    }
    try {
      const d = await post("/api/study-source", { channel, on });
      study.checked = d.on;
      await refresh();
      setMsg(`Saved: Study source ${d.on ? "on" : "off"}. ` + msg.textContent, "ok");
    } catch (err) {
      study.checked = !on;
      setMsg("Not saved: " + err.message, "error");
    }
  });

  refresh();
})();
