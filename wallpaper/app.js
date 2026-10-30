// Everyday mode: renders data.js (written by fetch.py) and reloads it every minute.
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  let D = window.DESKTOP_DATA || null;
  // ?static=1: drawn once by Edge as a still picture (picture mode), so no ticking clock
  const STATIC = new URLSearchParams(location.search).has("static");
  if (STATIC) document.body.classList.add("static");

  // ---------- helpers shared with race.js ----------

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tz = () => (D && D.timezone) || "America/Chicago";
  const fmtCache = {};
  const fmt = (opts, locale = "en-US") => {
    const key = locale + tz() + JSON.stringify(opts);
    return (fmtCache[key] ||= new Intl.DateTimeFormat(locale, { timeZone: tz(), ...opts }));
  };
  const timeOf = (d) => fmt({ hour: "numeric", minute: "2-digit" }).format(d);
  // YYYY-MM-DD in the wallpaper's time zone (en-CA formats dates that way)
  const dayKey = (d) => fmt({ year: "numeric", month: "2-digit", day: "2-digit" }, "en-CA").format(d);
  const weekday = (d, style = "short") => fmt({ weekday: style }).format(d);
  const hourOf = (d) => Number(fmt({ hour: "numeric", hourCycle: "h23" }).format(d));

  // "9:00 – 9:30 AM", or "11:00 AM – 1:00 PM" when the half of the day changes
  function timeRange(s, e) {
    const a = timeOf(s), b = timeOf(e);
    return a.slice(-2) === b.slice(-2) ? `${a.slice(0, -3)} – ${b}` : `${a} – ${b}`;
  }

  function relative(ms) {
    if (ms <= 0) return "now";
    const m = Math.round(ms / 60000);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ${m % 60}m`;
    return `${Math.floor(h / 24)}d ${h % 24}h`;
  }

  function inkFor(hex) {
    const n = parseInt(String(hex || "#888").replace("#", ""), 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    return 0.299 * r + 0.587 * g + 0.114 * b > 160 ? "dark-ink" : "";
  }
  const tag = (ev) =>
    `<span class="tag ${inkFor(ev.color)}" style="--c:${esc(ev.color || "#666")}">${esc(ev.tag)}</span>`;

  window.Desktop = { esc, fmt, timeOf, relative, inkFor, STATIC, get data() { return D; } };

  // ---------- theme ----------

  function applyTheme() {
    const t = D.theme || {};
    const root = document.documentElement.style;
    for (const k of ["bg1", "bg2", "bg3", "accent"]) if (t[k]) root.setProperty(`--${k}`, t[k]);
    const body = document.body;
    body.className = body.className.replace(/\bpattern-\S+/g, "").trim();
    body.classList.add(`pattern-${t.pattern || "waves"}`);
    body.classList.toggle("icons-right", D.display?.iconsSide === "right");
    body.classList.toggle("has-image", !!t.image);
    $(".bg-image").style.backgroundImage = t.image ? `url("${t.image}")` : "";
    const wm = $(".watermark");
    wm.textContent = t.watermark || "";
    // Fit the watermark to the screen width.
    const len = Math.max((t.watermark || "").length, 4);
    wm.style.fontSize = `min(30vh, ${(100 / (len * 0.66)).toFixed(1)}vw)`;
  }

  // ---------- pieces ----------

  const events = () => (D.events || []).map((e) => ({ ...e, s: new Date(e.start), e: new Date(e.end) }));

  function renderClock() {
    const now = new Date();
    $("#clock .time").textContent = timeOf(now).replace(/\s?[AP]M$/, "");
    $("#clock .date").textContent = fmt({ weekday: "long", month: "long", day: "numeric" }).format(now);
    const w = D.weather;
    $("#clock .weather-now").textContent = w
      ? `${w.icon} ${w.temp}${w.unit} ${w.text} · feels ${w.feels}° · ${D.location}`
      : D.location || "";
  }

  function renderHero(all, now) {
    const el = $("#hero");
    const h = D.headline && all.find((e) => e.id === D.headline.id);
    if (!h) {
      const w = D.weather;
      el.className = "hero-quiet";
      el.innerHTML = `<div class="kicker">${esc(weekday(now, "long"))}</div>
        <h1 class="hero-title">${esc(D.location || "Madison")}</h1>
        <div class="hero-meta">${w ? `${w.icon} ${w.temp}${w.unit} · ${esc(w.text)}` : ""}</div>`;
      return;
    }
    el.className = "";
    const mode = D.headline.mode;
    const parts = (h.subtitle || "").split(" · ");
    let detail = "";
    let note = "";
    let title = h.title;
    if (h.source === "f1") {
      detail = h.session_label;
      title = h.title.split(" · ")[0];
      if (h.early && h.status === "pre") note = "⏰ Early start, set an alarm";
      if (h.status === "live") note = `Live timing: localhost:${D.race?.port || 8765}`;
    } else if (h.source === "mlb" && h.postseason) {
      detail = parts.slice(0, 2).join(" · ");
      note = parts[2] === "if needed" ? "If needed" : parts[2] || "";
    } else if (h.source === "espn") {
      detail = h.sport;
    } else if (h.kind === "local") {
      detail = "Around Madison";
    }

    let kicker;
    let cls = "kicker";
    if (h.status === "live") {
      kicker = `Live now${detail ? " · " + detail : ""}`;
      cls += " live";
    } else if (mode === "next") {
      kicker = `${weekday(h.s, "long")}${detail ? " · " + detail : ""}`;
    } else {
      const when = h.status === "final" ? "Today" : hourOf(h.s) >= 17 ? "Tonight" : "Today";
      kicker = `${when}${detail ? " · " + detail : ""}`;
    }

    const meta = [h.all_day ? "Time TBA" : timeOf(h.s), h.source === "f1" ? h.subtitle : h.location, h.tv]
      .filter(Boolean).map(esc).join('<span class="sep">·</span>');

    let bottom = "";
    if (h.status === "live" || h.status === "final") {
      bottom = h.score
        ? `<div class="hero-score"><b>${esc(h.score)}</b><span>${esc(h.detail || (h.status === "final" ? "Final" : ""))}</span></div>`
        : `<div class="hero-count">${h.status === "live" ? "underway" : "finished"}</div>`;
    } else if (!h.all_day) {
      bottom = `<div class="hero-count">starts in <b>${relative(h.s - now)}</b></div>`;
    }
    el.innerHTML = `<div class="${cls}">${esc(kicker)}</div>
      <h1 class="hero-title">${esc(title)}</h1>
      <div class="hero-meta">${meta}</div>
      ${bottom}
      ${note ? `<div class="hero-note">${esc(note)}</div>` : ""}`;
  }

  function renderToday(all, now) {
    const el = $("#today");
    const todayKey = dayKey(now);
    const cal = all.filter((e) => e.kind === "calendar");
    const hasCalendar = cal.length > 0 || (D.errors && D.errors.gcal);
    const onDay = (key) => cal.filter((e) => e.all_day
      ? dayKey(e.s) <= key && key < dayKey(new Date(e.e - 1)) || dayKey(e.s) === key
      : dayKey(e.s) === key);

    const agenda = (list) => `<ul class="agenda">${list.map((e) => {
      const cls = e.e <= now ? "past" : e.s <= now ? "now" : "";
      return `<li class="${cls}" style="--c:${esc(e.color)}"><span class="bar"></span>
        <span class="when">${esc(timeRange(e.s, e.e))}</span><span class="what">${esc(e.title)}</span></li>`;
    }).join("")}</ul>`;
    const allDay = (list) => list.length
      ? `<div class="allday">${list.map((e) => `<span style="--c:${esc(e.color)}">${esc(e.title)}</span>`).join("")}</div>`
      : "";

    const today = onDay(todayKey);
    let html = `<h2>Today <span>${esc(fmt({ weekday: "short", month: "short", day: "numeric" }).format(now))}</span></h2>`;
    if (!hasCalendar) {
      html += `<p class="empty">Add your Google Calendar's secret iCal link to config.toml to see your day here.</p>`;
    } else {
      const timed = today.filter((e) => !e.all_day);
      html += allDay(today.filter((e) => e.all_day));
      html += timed.length ? agenda(timed) : `<p class="empty">Nothing else on the calendar today.</p>`;
      const left = timed.filter((e) => e.e > now).length;
      if (left < 3) {
        const tmr = new Date(now.getTime() + 86400000);
        const next = onDay(dayKey(tmr)).filter((e) => !e.all_day).slice(0, 3);
        if (next.length) html += `<div class="sub-head">Tomorrow</div>${agenda(next)}`;
      }
    }
    el.innerHTML = html;
  }

  function itemRow(e, now) {
    const live = e.status === "live";
    const right = live
      ? `<span class="live-pill">LIVE</span>${e.score ? `<span class="rel score">${esc(e.score)}</span>` : ""}`
      : e.all_day
        ? `${esc(weekday(e.s))}<span class="rel">time TBA</span>`
        : `${esc(weekday(e.s))} ${timeOf(e.s)}<span class="rel">in ${relative(e.s - now)}</span>`;
    const sub = [e.subtitle, e.tv].filter(Boolean).join(" · ");
    return `<div class="item ${live ? "is-live" : ""}">${tag(e)}
      <div><div class="t">${esc(e.title)}</div><div class="s">${esc(sub)}${e.early ? ' <span class="early">· early</span>' : ""}</div></div>
      <div class="w">${right}</div></div>`;
  }

  function renderNext(all, now) {
    const headId = D.headline?.id;
    const list = all
      .filter((e) => e.kind === "watch" && (e.status === "pre" || e.status === "live") && e.id !== headId)
      .slice(0, 6);
    $("#next").innerHTML = list.length
      ? `<h2>Next up</h2><div class="list">${list.map((e) => itemRow(e, now)).join("")}</div>`
      : "";
  }

  function renderLocal(all, now) {
    const soon = now.getTime() + 7 * 86400000;
    const headId = D.headline?.id;
    const list = all
      .filter((e) => (e.kind === "local" || e.local) && e.status !== "final" && e.s.getTime() < soon && e.id !== headId)
      .slice(0, 4);
    $("#local").innerHTML = list.length
      ? `<h2>Around ${esc(D.location || "Madison")}</h2><div class="list">${list.map((e) => itemRow(e, now)).join("")}</div>`
      : "";
  }

  function renderResults() {
    const r = D.results;
    if (!r || !r.results?.length) { $("#results").innerHTML = ""; return; }
    const rows = r.results.slice(0, 10).map((d) =>
      `<div style="--c:${esc(d.color)}"><span class="p">${esc(d.pos)}</span><span class="b"></span>
        <span class="n">${esc(d.tla)}</span><span class="g">${esc(d.gap || "")}</span></div>`).join("");
    $("#results").innerHTML = `<h2>${esc(r.session)} result <span>${esc(r.meeting)}</span></h2>
      <div class="results">${rows}</div>`;
  }

  function renderWeek(all, now) {
    const days = D.display?.daysAhead || 7;
    const wx = Object.fromEntries((D.weather?.days || []).map((d) => [d.date, d]));
    let html = "";
    for (let i = 0; i < days; i++) {
      const day = new Date(now.getTime() + i * 86400000);
      const key = dayKey(day);
      const w = wx[key];
      const on = all.filter((e) => dayKey(e.s) === key);
      const shows = on.filter((e) => e.kind !== "calendar")
        .sort((a, b) => b.importance - a.importance).slice(0, 3)
        .sort((a, b) => a.s - b.s);
      const calCount = on.filter((e) => e.kind === "calendar").length;
      const chips = shows.map((e) =>
        `<div class="chip" style="--c:${esc(e.color)}"><span class="dot"></span><b>${esc(e.tag)}</b>
          <span>${e.all_day ? "TBA" : esc(timeOf(e.s).replace(":00", "").replace(" ", "").toLowerCase())}</span>
          <span>${esc(shortLabel(e))}</span></div>`).join("");
      html += `<div class="day ${i === 0 ? "today" : ""}">
        <div class="dh"><span class="dn">${i === 0 ? "TODAY" : esc(weekday(day).toUpperCase())}</span>
          <span class="dd">${esc(fmt({ day: "numeric" }).format(day))}</span></div>
        <div class="wx">${w ? `${w.icon} ${w.hi}° / ${w.lo}°${w.rain >= 30 ? ` · ${w.rain}%` : ""}` : "&nbsp;"}</div>
        <div class="chips">${chips}</div>
        ${calCount ? `<div class="cal">${calCount} on calendar</div>` : ""}</div>`;
    }
    $("#week").innerHTML = html;
  }

  function shortLabel(e) {
    if (e.source === "f1") return { race: "Race", quali: "Quali", sprint: "Sprint", "sprint-quali": "SQ", practice: e.session_label.replace("Practice ", "FP") }[e.session] || "";
    if (e.source === "mlb" || e.source === "espn") return e.title.replace(/^\S+\s/, "");
    return e.title;
  }

  function renderStatus() {
    const errs = Object.keys(D.errors || {});
    const updated = timeOf(new Date(D.generated));
    $("#status-line").textContent = `Updated ${updated}` +
      (errs.length ? ` · couldn't reach ${errs.join(", ")}, showing saved data` : "");
  }

  function render() {
    if (!D) return;
    const now = new Date();
    const all = events();
    applyTheme();
    renderClock();
    renderHero(all, now);
    renderResults();
    renderToday(all, now);
    renderNext(all, now);
    renderLocal(all, now);
    renderWeek(all, now);
    renderStatus();
    fitRail();
  }

  // Drop list rows from the bottom cards until the right-hand column fits the screen.
  function fitRail() {
    const rail = $("#rail");
    for (let guard = 0; guard < 30 && rail.scrollHeight > rail.clientHeight + 1; guard++) {
      const local = document.querySelectorAll("#local .item");
      const next = document.querySelectorAll("#next .item");
      if (local.length > 1) local[local.length - 1].remove();
      else if (next.length > 3) next[next.length - 1].remove();
      else if (local.length === 1) $("#local").innerHTML = "";
      else if (next.length > 1) next[next.length - 1].remove();
      else break;
    }
  }

  function reloadData() {
    const s = document.createElement("script");
    s.src = `data.js?t=${Date.now()}`;
    s.onload = () => { D = window.DESKTOP_DATA || D; render(); s.remove(); };
    s.onerror = () => s.remove();
    document.head.appendChild(s);
  }

  render();
  setInterval(reloadData, 60_000);
  setInterval(() => { if (D) renderClock(); }, 5_000);
  setInterval(render, 30_000);  // countdowns and past/now markers
  // Belt and braces: a full reload now and then, unless race mode is showing.
  setInterval(() => { if (!document.body.classList.contains("racing")) location.reload(); }, 30 * 60_000);
  window.Desktop.render = render;
})();
