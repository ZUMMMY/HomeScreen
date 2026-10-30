// Race mode: the live timing screen. live.py serves this page at http://localhost:8765 with
// <body class="race-only">; opened any other way it takes over the page while live.py runs.
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const SVG = "http://www.w3.org/2000/svg";
  const { esc } = window.Desktop;
  const RACE_ONLY = document.body.classList.contains("race-only");
  const RETRY_MS = RACE_ONLY ? 5_000 : 15_000;
  const LEAVE_AFTER_MS = 10_000;  // grace period before going back to the everyday view

  let ws = null;
  let snap = null;
  let snapAt = 0;          // performance.now() when snap arrived
  let track = null;        // { points, cum, total, mini, corners }
  let cars = {};           // num -> { el, frac }
  let leaveTimer = null;

  // ---------- connection ----------

  function connect() {
    const cfg = window.Desktop.data?.race;
    if (cfg && cfg.enabled === false) return;
    const host = RACE_ONLY ? location.host : `127.0.0.1:${cfg?.port || 8765}`;
    try {
      ws = new WebSocket(`ws://${host}`);
    } catch {
      return setTimeout(connect, RETRY_MS);
    }
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "track") setTrack(msg);
      else if (msg.type === "snapshot") onSnapshot(msg);
    };
    ws.onclose = () => {
      ws = null;
      if (document.body.classList.contains("racing")) {
        clearTimeout(leaveTimer);
        leaveTimer = setTimeout(leaveRace, LEAVE_AFTER_MS);
      }
      setTimeout(connect, RETRY_MS);
    };
  }

  function enterRace() {
    clearTimeout(leaveTimer);
    if (document.body.classList.contains("racing")) return;
    document.body.classList.add("racing");
    $("#race").hidden = false;
    requestAnimationFrame(frame);
  }

  function leaveRace() {
    if (RACE_ONLY) return waiting("Race mode has finished. This page comes back to life for the next session.");
    document.body.classList.remove("racing");
    document.body.className = document.body.className.replace(/\bflag-\S+/g, "").trim();
    $("#race").hidden = true;
    snap = null;
    window.Desktop.render?.();
  }

  // ---------- track ----------

  function setTrack(t) {
    const pts = t.points || [];
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    }
    const closing = pts.length ? Math.hypot(pts[0][0] - pts.at(-1)[0], pts[0][1] - pts.at(-1)[1]) : 0;
    track = { ...t, cum, total: (cum.at(-1) || 0) + closing };

    const d = pts.length ? "M" + pts.map((p) => p.join(",")).join("L") + "Z" : "";
    const [sx, sy] = pts[0] || [0, 0];
    const [nx, ny] = pts[3] || [1, 0];
    const ang = Math.atan2(ny - sy, nx - sx) + Math.PI / 2;
    const fx = Math.cos(ang) * 22, fy = Math.sin(ang) * 22;
    $("#track-lines").innerHTML = d
      ? `<path class="track-base" d="${d}"/><path class="track-line" d="${d}"/>
         <line class="finish" x1="${sx - fx}" y1="${sy - fy}" x2="${sx + fx}" y2="${sy + fy}"/>`
      : "";
    $("#track-corners").innerHTML = (t.corners || []).map((c) =>
      `<text class="corner" x="${c.x}" y="${c.y}">${esc(c.n)}${esc(c.letter || "")}</text>`).join("");
  }

  // position along the lap (0..1) -> [x, y]
  function pointAt(frac) {
    const target = (((frac % 1) + 1) % 1) * track.total;
    const { cum, points } = track;
    let lo = 0, hi = cum.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (cum[mid] <= target) lo = mid; else hi = mid - 1;
    }
    const a = points[lo];
    const b = points[(lo + 1) % points.length];
    const segLen = (lo + 1 < cum.length ? cum[lo + 1] : track.total) - cum[lo] || 1;
    const f = (target - cum[lo]) / segLen;
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  }

  // Where the end of timing mini-sector g sits on the lap (0..1). The outline
  // knows where its own mini-sectors start; scale if F1 now uses a different count.
  function segEnd(g, count) {
    if (g < 0 || !count) return 0;
    const marks = [0, ...(track.mini || [])];
    if (marks.length < 2) return (g + 1) / count;
    const x = ((g + 1) / count) * (marks.length - 1);
    const i = Math.min(Math.floor(x), marks.length - 2);
    const f = x - i;
    return marks[i] + (marks[i + 1] - marks[i]) * f;
  }

  // Estimated lap fraction: last mini-sector crossing + time since, at the car's pace,
  // never past the next mini-sector until the feed says it got there.
  function estimate(d, feedNow, count) {
    if (d.seg < 0 || !d.segT) return 1 - 0.0035 * ((d.pos || 20) + 1);  // on the grid
    const base = segEnd(d.seg, count) % 1;
    const next = segEnd((d.seg + 1) % count, count);
    const room = (((next - base) % 1) + 1) % 1 || 1 / count;
    const moved = Math.max(0, (feedNow - d.segT) / (d.pace || 95));
    return base + Math.min(moved, room * 0.97);
  }

  // ---------- frames ----------

  function feedNow() {
    if (!snap) return 0;
    return snap.t + ((performance.now() - snapAt) / 1000) * (snap.speed || 0);
  }

  let lastFrame = 0;
  function frame(ts) {
    if (!document.body.classList.contains("racing")) return;
    requestAnimationFrame(frame);
    if (ts - lastFrame < 33) return;  // ~30 fps is plenty for a wallpaper
    lastFrame = ts;
    drawCars();
    drawClock();
  }

  function drawCars() {
    if (!snap || !track || !track.points.length) return;
    const now = feedNow();
    const favs = new Set(snap.favorites || []);
    const layer = $("#cars");
    const seen = new Set();
    const leader = snap.drivers.find((d) => d.pos === 1);
    for (const d of snap.drivers) {
      const onTrack = !d.out && !d.inPit && (d.xy || d.seg >= 0 || snap.session.type === "Race");
      if (!onTrack) continue;
      seen.add(d.num);
      let car = cars[d.num];
      if (!car) {
        const g = document.createElementNS(SVG, "g");
        g.innerHTML = `<circle class="ring" r="17"/><circle class="body" r="12"/><text x="18" y="-14"></text>`;
        layer.appendChild(g);
        car = cars[d.num] = { el: g, frac: null, xy: null };
      }
      const fav = favs.has(d.tla);
      car.el.setAttribute("class", `car${d === leader ? " leader" : ""}${fav ? " fav" : ""}`);
      car.el.querySelector(".body").setAttribute("fill", d.color);
      car.el.querySelector(".body").setAttribute("r", fav ? 15 : 12);
      car.el.querySelector("text").textContent = d.tla;

      let x, y;
      if (d.xy) {
        car.xy = car.xy ? car.xy.map((v, i) => v + (d.xy[i] - v) * 0.35) : d.xy;
        [x, y] = car.xy;
      } else {
        const target = estimate(d, now, snap.segCount);
        if (car.frac === null) car.frac = target;
        let diff = target - car.frac;
        diff -= Math.round(diff);            // shortest way round the lap
        car.frac += Math.abs(diff) > 0.25 ? diff : diff * 0.25;
        [x, y] = pointAt(car.frac);
      }
      car.el.setAttribute("transform", `translate(${x.toFixed(1)},${y.toFixed(1)})`);
    }
    // leader and favourites on top
    if (leader && cars[leader.num]) layer.appendChild(cars[leader.num].el);
    for (const d of snap.drivers) if (favs.has(d.tla) && cars[d.num]) layer.appendChild(cars[d.num].el);
    for (const num of Object.keys(cars)) {
      if (!seen.has(num)) { cars[num].el.remove(); delete cars[num]; }
    }
  }

  function drawClock() {
    const el = document.getElementById("rb-clock");
    if (!el || !snap) return;
    const s = snap.session;
    if (s.type !== "Race" && s.remaining != null) {
      const rem = s.clockRunning ? s.remaining - (feedNow() - snap.t) : s.remaining;
      el.innerHTML = `<b>${hms(Math.max(rem, 0))}</b> left`;
    }
  }

  const hms = (sec) => {
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
  };

  // ---------- snapshot rendering ----------

  function onSnapshot(msg) {
    snap = msg;
    snapAt = performance.now();
    if (!msg.drivers.length) return;  // connected before the session has any data
    enterRace();
    if (RACE_ONLY) waiting(null);
    const flag = msg.session.status === "Finished" || msg.session.status === "Finalised"
      ? "chequered" : msg.track.status;
    document.body.className = document.body.className.replace(/\bflag-\S+/g, "").trim();
    document.body.classList.add(`flag-${flag}`);
    renderBar(msg, flag);
    renderTower(msg);
    renderPits(msg);
    renderRc(msg);
  }

  const FLAG_TEXT = {
    green: "GREEN", yellow: "YELLOW", sc: "SAFETY CAR", vsc: "VSC", "vsc-ending": "VSC ENDING",
    red: "RED FLAG", chequered: "CHEQUERED",
  };

  function renderBar(m, flag) {
    const s = m.session;
    const waiting = s.status === "Inactive";
    const label = waiting ? "STARTING SOON" : FLAG_TEXT[flag] || flag.toUpperCase();
    const meeting = (s.meeting || "").replace("Grand Prix", "GP");
    const lap = s.type === "Race" && s.lap
      ? `<div class="rb-lap">LAP <b>${s.lap}</b>${s.laps ? ` / ${s.laps}` : ""}</div>`
      : `<div class="rb-lap" id="rb-clock"></div>`;
    const w = m.weather;
    const wx = w.air ? `Air ${Math.round(w.air)}°C · Track ${Math.round(w.track)}°C${w.rain ? " · Rain" : ""}` : "";
    const badges = [
      m.mode === "replay" ? `<span class="badge replay">REPLAY${m.speed && m.speed !== 1 ? ` ${m.speed}×` : ""}</span>` : "",
      m.delay ? `<span class="badge">DELAY ${m.delay}s</span>` : "",
      s.part && s.type === "Qualifying" ? `<span class="badge">Q${s.part}</span>` : "",
    ].join("");
    $("#race-bar").innerHTML = `<span class="flag ${waiting ? "waiting" : esc(flag)}">${label}</span>
      <div class="rb-title">${esc(meeting)} <span>${esc(s.name)}</span></div>${lap}
      <div class="rb-wx">${esc(wx)}</div><div class="rb-badges">${badges}</div>`;
    $("#map-note").textContent = m.estimated
      ? "Car positions estimated from mini-sector timing" : "Live car positions";
  }

  function renderTower(m) {
    const tower = $("#tower");
    const rowH = (tower.clientHeight - 40) / Math.max(m.drivers.length, 20);
    if (!tower.querySelector(".head")) {
      tower.innerHTML = `<div class="t-row head"><span></span><span></span><span>Driver</span>
        <span style="text-align:right">${m.session.type === "Race" ? "Gap" : "Best"}</span>
        <span style="text-align:right">${m.session.type === "Race" ? "Int" : "Gap"}</span>
        <span style="text-align:right">Last</span><span style="text-align:right">S1</span>
        <span style="text-align:right">S2</span><span style="text-align:right">S3</span>
        <span>Mini sectors</span><span>Tyre</span><span>Pit</span></div><div class="cut-line" hidden></div>`;
    }
    const favs = new Set(m.favorites || []);
    const race = m.session.type === "Race";
    const seen = new Set();
    m.drivers.forEach((d, i) => {
      seen.add(d.num);
      let row = tower.querySelector(`[data-num="${d.num}"]`);
      if (!row) {
        row = document.createElement("div");
        row.dataset.num = d.num;
        tower.appendChild(row);
      }
      const cls = ["t-row"];
      if (d.inPit) cls.push("in-pit");
      if (d.out) cls.push("out");
      if (d.knocked || (m.session.cut && i >= m.session.cut)) cls.push("knocked");
      if (favs.has(d.tla)) cls.push("fav");
      row.className = cls.join(" ");
      row.style.transform = `translateY(${(40 + i * rowH).toFixed(1)}px)`;
      row.style.top = "0";
      row.style.height = `${rowH - 2}px`;
      const gap = race ? d.gap : d.best;
      const interval = race ? d.int : d.gap;
      const t = d.tyre || {};
      row.innerHTML = `<span class="pos">${d.pos ?? ""}</span><span class="bar" style="--c:${esc(d.color)}"></span>
        <span class="tla">${esc(d.tla)}</span>
        <span class="gap ${d.fastest && !race ? "purple" : ""}">${esc(d.out ? "OUT" : gap || "")}</span>
        <span class="int">${esc(interval || "")}</span>
        <span class="last">${esc(d.last || "")}</span>
        ${d.sectors.map((s) => `<span class="sec ${s.c || ""} ${s.stale ? "stale" : ""}">${esc(s.v || "")}</span>`).join("")}
        <span class="mini">${d.mini.map((st) => `<i class="s${st}"></i>`).join("")}</span>
        <span>${t.c ? `<span class="tyre ${esc(t.c)}">${esc(t.c)}</span><span class="age">${t.age ?? ""}</span>` : ""}</span>
        <span class="pits">${d.inPit && !d.out ? "<b>PIT</b>" : d.pits || ""}</span>`;
    });
    tower.querySelectorAll("[data-num]").forEach((row) => { if (!seen.has(row.dataset.num)) row.remove(); });
    const cut = tower.querySelector(".cut-line");
    cut.hidden = !m.session.cut;
    if (m.session.cut) cut.style.transform = `translateY(${(40 + m.session.cut * rowH - 2).toFixed(1)}px)`;
  }

  function renderPits(m) {
    const inPit = m.drivers.filter((d) => d.inPit && !d.out);
    $("#pit-box").innerHTML = inPit.length
      ? `<span class="lbl">PIT LANE</span>${inPit.map((d) =>
          `<span class="p ${window.Desktop.inkFor(d.color)}" style="--c:${esc(d.color)}">${esc(d.tla)}</span>`).join("")}`
      : "";
  }

  function renderRc(m) {
    $("#rc").innerHTML = m.rc.slice(0, 4).map((r) => {
      const time = r.utc ? window.Desktop.timeOf(new Date(r.utc)).replace(/\s?[AP]M/, "") : "";
      return `<div><span class="tm">${esc(time)}</span><span class="fd ${esc(r.flag || "")}"></span>
        <span>${esc(r.msg)}</span></div>`;
    }).join("");
  }

  // Shown in the race-only page until timing data arrives, and after the session.
  function waiting(text) {
    let el = document.getElementById("race-waiting");
    if (!el) {
      el = document.createElement("div");
      el.id = "race-waiting";
      el.className = "race-wait";
      $("#race-map").appendChild(el);
    }
    el.hidden = !text;
    if (text) el.innerHTML = `<b>F1 live timing</b><span>${esc(text)}</span>`;
  }

  if (RACE_ONLY) {
    document.title = "F1 Live Timing";
    enterRace();
    waiting("Waiting for the session's timing data…");
  }
  if (!window.Desktop.STATIC) connect();
})();
