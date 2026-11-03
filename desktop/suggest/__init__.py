"""Suggestions: notable things in the next month that aren't on your calendar yet.

Every few hours this looks a month ahead for big movie releases and films you follow (TMDB),
the biggest games and races (ESPN, MLB, F1) and big shows in Madison, and drops whatever is
already on your calendar. Each fetch.py run then shows at most one as a Windows notification
with "Add to calendar" and "No thanks" buttons, and never more than max_per_day in a day.
A small process (suggest.py --ask) waits for the answer, for up to answer_hours.
Your answers are kept in cache/suggestions.json, so nothing is asked twice.
"""

import hashlib
import logging
import re
import socket
import subprocess
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from ..common import CACHE, ROOT, http, local_tz, parse_time, read_json, to_iso, write_json
from . import google, movies, toast

log = logging.getLogger("suggest")

STORE = CACHE / "suggestions.json"
REFRESH_EVERY = timedelta(hours=6)
ID = re.compile(r"^[0-9a-f]{10}$")
# words too common to tell one event from another when matching against your calendar
GENERIC = {"the", "and", "for", "with", "game", "badgers", "wisconsin", "race", "grand", "prix",
           "theaters", "movie", "watch", "tickets", "show", "concert", "live", "part", "tour"}


def run(cfg: dict, now: datetime, refresh: bool = False, notify: bool = True) -> dict:
    """Refresh the candidates when due, then maybe show one notification."""
    store = load()
    if refresh or _stale(store, now):
        _refresh(cfg, now, store)
        save(store)
    if notify and _notify(cfg, now, store):
        save(store)
    return store


def load() -> dict:
    store = read_json(STORE, {}) or {}
    store.setdefault("items", {})
    return store


def save(store: dict) -> None:
    write_json(STORE, store)


def ask(cfg: dict, sid: str) -> None:
    """Show one suggestion and wait for the answer (this runs as its own small process)."""
    item = load()["items"].get(sid)
    if not item or item["status"] != "asked":
        return
    xml = toast.suggestion_xml(item, when(cfg, item), _image(item))
    wait = timedelta(hours=cfg.get("suggestions", {}).get("answer_hours", 12))
    quiet = False
    while True:
        clicked = toast.ask(xml, sid, wait, quiet)
        if not clicked:  # swiped away or left too long: drop it, `suggest.py --again` brings it back
            toast.remove(sid)
            _decide(sid, "unanswered", keep_image=True)
            log.info("No answer about %s", item["title"])
            return
        handle(cfg, clicked)
        if not clicked.startswith("open/"):
            return
        quiet = True  # they looked at it; put the question back in the list without popping up


def handle(cfg: dict, clicked: str) -> None:
    """A notification click: accept/<id>, decline/<id> or open/<id> (the notification itself)."""
    action, _, sid = clicked.strip("/").partition("/")
    sid = sid.strip("/")
    if action not in ("accept", "decline", "open") or not ID.match(sid):
        log.warning("Ignoring unknown click %r", clicked[:80])
        return
    store = load()
    item = store["items"].get(sid)
    if not item:
        log.warning("No suggestion %s", sid)
        return
    log.info("%s: %s", action.title(), item["title"])
    if action == "open":  # look, don't add: Google Calendar's filled-in page still needs a Save
        webbrowser.open(item.get("link") or google.template_url(cfg, item))
        return
    toast.remove(sid)
    if item["status"] in ("accepted", "declined"):
        log.info("%s was already %s", item["title"], item["status"])
        return
    if action == "decline":
        _decide(sid, "declined")
        toast.show(toast.message_xml("Got it", f"Won't ask about {item['title']} again."), sid)
        return

    try:
        made = google.add_event(cfg, item)
    except google.NotSignedIn as exc:
        log.info("Opening Google Calendar's own page instead (%s)", exc)
        webbrowser.open(google.template_url(cfg, item))
        _decide(sid, "accepted", via="browser")
        toast.show(toast.message_xml("Almost there", "Click Save in Google Calendar to add it."), sid)
        return
    except Exception:
        log.exception("Couldn't add %s", item["title"])
        toast.show(toast.message_xml("Couldn't add it to your calendar",
                                     f"{item['title']}: see cache/suggest.log"), sid)
        return
    _decide(sid, "accepted", uid=made.get("iCalUID"), event_link=made.get("htmlLink"))
    log.info("Added %s to Google Calendar", item["title"])
    toast.show(toast.message_xml("Added to your calendar", f"{item['title']} · {when(cfg, item)}"), sid)


def when(cfg: dict, item: dict) -> str:
    if item["all_day"]:
        return f"{date.fromisoformat(item['date']):%a, %b} {date.fromisoformat(item['date']).day}"
    t = parse_time(item["start"]).astimezone(local_tz(cfg))
    return f"{t:%a, %b} {t.day} · {t:%I:%M %p}".replace(" 0", " ")


def _decide(sid: str, status: str, keep_image: bool = False, **extra) -> None:
    store = load()  # re-read: fetch.py may have written it since this was shown
    store["items"][sid].update(status=status, decided=to_iso(datetime.now().astimezone()), **extra)
    save(store)
    if not keep_image:
        (CACHE / "posters" / f"{sid}.jpg").unlink(missing_ok=True)


def _stale(store: dict, now: datetime) -> bool:
    last = store.get("refreshed")
    return not last or now - parse_time(last) > REFRESH_EVERY


def _refresh(cfg: dict, now: datetime, store: dict) -> None:
    from ..sources import espn, f1, gcal, madison, mlb

    opts = cfg.get("suggestions", {})
    days = opts.get("days_ahead", 30)
    tz = local_tz(cfg)
    wide = {**cfg, "display": {**cfg.get("display", {}), "watch_days_ahead": days, "days_ahead": days}}

    found = []
    try:
        found += movies.upcoming(cfg, now, days)
    except Exception:
        log.exception("Movies failed")
    min_watch = opts.get("min_importance", 92)
    min_show = opts.get("show_min_importance", 55)
    for mod, kind, why in ((espn, "game", "Big game"), (mlb, "game", "Brewers postseason"),
                           (f1, "race", "F1 race weekend"), (madison, "show", "Big night in Madison")):
        try:
            got = mod.fetch(wide, now)
        except Exception:
            log.exception("%s failed", mod.__name__)
            continue
        for ev in got:
            least = min_show if kind == "show" else min_watch
            # TBD kickoffs come back once the time is announced, so the calendar gets it right
            if ev["importance"] >= least and not ev["tbd"] and parse_time(ev["start"]) > now:
                found.append(_from_event(ev, kind, why, tz))
    try:
        calendar = gcal.fetch(wide, now)
    except Exception:
        log.exception("Couldn't read your calendar to skip what's on it")
        return  # better to wait than to suggest things you already have

    items = store["items"]
    for cand in found:
        sid = hashlib.sha1(cand["key"].encode()).hexdigest()[:10]
        old = items.get(sid)
        if old and old["status"] not in ("new", "on_calendar"):
            continue
        cand["id"] = sid
        cand["status"] = "on_calendar" if _on_calendar(cand, calendar, tz) else "new"
        cand["found"] = (old or {}).get("found") or to_iso(now)
        items[sid] = cand
    cutoff = (now - timedelta(days=7)).astimezone(tz).date()
    for sid in [s for s, it in items.items() if date.fromisoformat(it["date"]) < cutoff]:
        del items[sid]
    store["refreshed"] = to_iso(now)
    log.info("%d suggestions waiting, %d already on your calendar", sum(i["status"] == "new" for i in items.values()),
             sum(i["status"] == "on_calendar" for i in items.values()))


def _from_event(ev: dict, kind: str, why: str, tz) -> dict:
    start = parse_time(ev["start"])
    detail = " · ".join(x for x in (ev.get("location") or ev["subtitle"], ev.get("tv")) if x)
    lines = [ev["subtitle"], f"TV: {ev['tv']}" if ev.get("tv") else ""]
    return {
        "kind": kind,
        "key": ev["id"],
        "title": ev["title"],
        "name": ev["title"],
        "date": start.astimezone(tz).date().isoformat(),
        "start": ev["start"],
        "end": ev["end"],
        "all_day": ev["all_day"],
        "detail": detail,
        "why": why,
        "location": ev.get("location"),
        "description": "\n".join(l for l in lines if l),
        "link": None,
        "image_url": None,
        "busy": kind == "show",  # you'd be out; games and races you watch from home
    }


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in GENERIC}


def _on_calendar(item: dict, calendar: list[dict], tz) -> bool:
    """Something on your calendar around that day shares a telling word with it."""
    words = _words(item["name"])
    day = date.fromisoformat(item["date"])
    lo, hi = (0, 21) if item["kind"] == "movie" else (-1, 1)  # a film you might see weeks after it opens
    for ev in calendar:
        diff = (parse_time(ev["start"]).astimezone(tz).date() - day).days
        if lo <= diff <= hi and words & _words(ev["title"]):
            return True
    return False


def _notify(cfg: dict, now: datetime, store: dict) -> bool:
    opts = cfg.get("suggestions", {})
    tz = local_tz(cfg)
    local = now.astimezone(tz)
    quiet_from, quiet_to = opts.get("quiet_hours", [22, 8])
    if (local.hour >= quiet_from or local.hour < quiet_to) if quiet_from > quiet_to else quiet_from <= local.hour < quiet_to:
        return False
    if _race_on(cfg):
        return False  # don't pop up over the race
    today = local.date().isoformat()
    asked_today = sum(1 for i in store["items"].values() if (i.get("asked") or "")[:10] == today)
    if asked_today >= opts.get("max_per_day", 4):
        return False
    waiting = sorted((i for i in store["items"].values()
                      if i["status"] == "new" and _starts(i, tz) > now), key=lambda i: _starts(i, tz))
    if not waiting:
        return False
    if not toast.registered():
        log.info("%d suggestions waiting; run setup\\register-notifications.ps1 to get them as notifications",
                 len(waiting))
        return False
    item = waiting[0]
    item["status"] = "asked"
    item["asked"] = local.isoformat(timespec="seconds")
    save(store)  # the waiting process reads it
    _spawn("--ask", item["id"])
    log.info("Asked about %s", item["title"])
    return True


def _spawn(*args: str) -> None:
    """Start suggest.py on its own, without a window, outliving this run."""
    exe = Path(sys.executable)
    if exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    cmd = [str(exe), str(ROOT / "suggest.py"), *args]
    try:
        subprocess.Popen(cmd, cwd=ROOT, close_fds=True, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:  # not allowed to leave Task Scheduler's job; it still outlives fetch.py
        subprocess.Popen(cmd, cwd=ROOT, close_fds=True, creationflags=flags)


def _starts(item: dict, tz) -> datetime:
    if item["all_day"]:
        return datetime.combine(date.fromisoformat(item["date"]), datetime.max.time(), tz)
    return parse_time(item["start"])


def _race_on(cfg: dict) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", cfg.get("race_mode", {}).get("port", 8765))) == 0


def _image(item: dict) -> str | None:
    """Notifications can only show pictures saved on this PC."""
    if not item.get("image_url"):
        return None
    path = CACHE / "posters" / f"{item['id']}.jpg"
    if not path.exists():
        try:
            r = http().get(item["image_url"], timeout=15)
            r.raise_for_status()
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(r.content)
        except Exception:
            log.warning("Couldn't download the picture for %s", item["title"])
            return None
    return path.as_uri()
