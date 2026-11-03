"""Refresh wallpaper/data.js from every source.

Task Scheduler runs this when you sign in and every 15 minutes (see setup/register-tasks.ps1).
Each run also looks at the F1 schedule: if a session goes live before the next run, race mode
(live.py) is booked in Task Scheduler for that exact minute; if one is already on, it starts now.
It also looks a month ahead for calendar suggestions (see suggest.py).

    python fetch.py            refresh once
    python fetch.py --summary  refresh and print what the wallpaper will show
"""

import argparse
import json
import logging
import socket
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from desktop import render, suggest, themes
from desktop.common import (CACHE, ROOT, WALLPAPER, load_config, local_tz, now_utc,
                            parse_time, read_json, setup_logging, to_iso,
                            write_json, write_text_atomic)
from desktop.sources import espn, f1, gcal, madison, mlb, weather

log = logging.getLogger("fetch")

SOURCES = {"gcal": gcal, "f1": f1, "mlb": mlb, "espn": espn, "madison": madison}
LIVE_TASK = "Madison Desktop Race Mode"
RESULTS_MAX_AGE = timedelta(hours=18)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", action="store_true", help="print what the wallpaper will show")
    parser.add_argument("--no-race", action="store_true", help="don't start race mode")
    parser.add_argument("--no-render", action="store_true", help="don't redraw the desktop picture")
    parser.add_argument("--no-suggest", action="store_true", help="don't look for calendar suggestions")
    args = parser.parse_args()

    setup_logging("fetch")
    cfg = load_config()
    now = now_utc()

    events, errors = [], {}
    for name, mod in SOURCES.items():
        got, err = _run_source(name, mod, cfg, now)
        events += got
        if err:
            errors[name] = err
    for name in gcal.failed:
        errors[f"{name} calendar"] = "couldn't read this calendar's link"
    for ev in events:
        ev["status"] = ev["status"] or _clock_status(ev, now)
    events.sort(key=lambda e: (e["start"], -e["importance"]))

    wx, err = _run_source("weather", weather, cfg, now)
    if err:
        errors["weather"] = err

    theme, headline = themes.pick(cfg, events, now)
    payload = {
        "generated": to_iso(now),
        "timezone": cfg.get("timezone", "America/Chicago"),
        "location": cfg.get("location", {}).get("name", "Madison"),
        "display": {
            "iconsSide": cfg.get("display", {}).get("icons_side", "left"),
            "daysAhead": cfg.get("display", {}).get("days_ahead", 7),
        },
        "theme": theme,
        "headline": headline,
        "events": events,
        "weather": wx or None,
        "results": _recent_results(now),
        "race": {
            "enabled": cfg.get("race_mode", {}).get("enabled", True),
            "port": cfg.get("race_mode", {}).get("port", 8765),
            "favorites": cfg.get("race_mode", {}).get("favorite_drivers", []),
        },
        "errors": errors,
    }
    write_text_atomic(WALLPAPER / "data.js",
                      "window.DESKTOP_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    log.info("Wrote %d events, theme %s, %d source errors", len(events), theme["key"], len(errors))

    if not args.no_race and not _port_in_use(cfg.get("race_mode", {}).get("port", 8765)):
        _maybe_start_race_mode(cfg, events, now)
    if not args.no_render:
        render.update()
    if not args.no_suggest and cfg.get("suggestions", {}).get("enabled", False):
        try:
            suggest.run(cfg, now)
        except Exception:  # suggestions are extra; never let them break the refresh
            log.exception("Suggestions failed")
    if args.summary:
        _print_summary(cfg, payload)


def _run_source(name, mod, cfg, now):
    """Fetch one source; on failure fall back to its last good result."""
    cache = CACHE / f"source-{name}.json"
    try:
        result = mod.fetch(cfg, now)
        write_json(cache, result)
        return result, None
    except Exception as exc:  # one broken feed shouldn't blank the wallpaper
        log.exception("%s failed", name)
        cached = read_json(cache, [] if name != "weather" else {})
        if isinstance(cached, list):
            for ev in cached:
                ev["status"] = None  # recompute from the clock
        return cached, f"{type(exc).__name__}: {exc}"[:200]


def _clock_status(ev: dict, now) -> str:
    if now < parse_time(ev["start"]):
        return "pre"
    if now < parse_time(ev["end"]):
        return "live"
    return "final"


def _recent_results(now) -> dict | None:
    results = read_json(CACHE / "last_session.json")
    if results and now - parse_time(results["time"]) < RESULTS_MAX_AGE:
        return results
    return None


def _maybe_start_race_mode(cfg: dict, events: list[dict], now) -> bool:
    """Start race mode if a session is on; book it if one goes live before the next check."""
    rm = cfg.get("race_mode", {})
    if not (rm.get("enabled", True) and rm.get("auto_start", True)):
        return False
    lead = timedelta(minutes=rm.get("start_minutes_before", 10))
    # a little slack so a session going live right at the next check isn't left to chance
    next_check = now + timedelta(minutes=cfg.get("display", {}).get("refresh_minutes", 15) + 2)
    for ev in events:
        if ev["source"] != "f1":
            continue
        if ev.get("session") == "practice" and not rm.get("practice", True):
            continue
        go_live = parse_time(ev["start"]) - lead
        if go_live <= now <= parse_time(ev["end"]) + timedelta(minutes=30) and not _finished_since(ev["start"]):
            log.info("Starting race mode for %s", ev["title"])
            _start_live()
            return True
        if now < go_live <= next_check:
            _book_live(go_live, ev["title"])
            return False
    return False


def _finished_since(start: str) -> bool:
    """Race mode already saw this session to the flag (and lingered), so don't start it again."""
    saved = read_json(CACHE / "last_session.json", {}) or {}
    return bool(saved.get("time")) and parse_time(saved["time"]) >= parse_time(start)


def _book_live(at, title: str) -> None:
    """Give the race mode task a one-time trigger at `at` (only rewritten when it changes)."""
    booked = read_json(CACHE / "race-booking.json", {}) or {}
    if booked.get("at") == to_iso(at):
        return
    local = at.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    script = (f"$t = New-ScheduledTaskTrigger -Once -At ([datetime]::Parse('{local}')); "
              f"Set-ScheduledTask -TaskName '{LIVE_TASK}' -Trigger $t | Out-Null")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode == 0:
        write_json(CACHE / "race-booking.json", {"at": to_iso(at), "session": title})
        log.info("Race mode booked for %s (%s)", local, title)
    else:
        # no scheduled task (register-tasks.ps1 not run): wait in a background copy instead
        log.warning("Couldn't book race mode in Task Scheduler (%s); waiting in the background",
                    r.stderr.strip()[:200])
        _start_live(f"--start-at={to_iso(at)}", use_task=False)
        write_json(CACHE / "race-booking.json", {"at": to_iso(at), "session": title})


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _start_live(*args: str, use_task: bool = True) -> None:
    # The race mode task from register-tasks.ps1 has no run-time limit and
    # never starts a second copy, so prefer it.
    if use_task and sys.platform == "win32":
        r = subprocess.run(["schtasks", "/Run", "/TN", LIVE_TASK], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            return
    exe = Path(sys.executable)
    if exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    cmd = [str(exe), str(ROOT / "live.py"), *args]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(cmd, cwd=ROOT, close_fds=True,
                         creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    except OSError:
        subprocess.Popen(cmd, cwd=ROOT, close_fds=True, creationflags=flags)


def _print_summary(cfg: dict, payload: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tz = local_tz(cfg)
    fmt = lambda iso: parse_time(iso).astimezone(tz).strftime("%a %b %d %I:%M %p")
    t = payload["theme"]
    print(f"Theme: {t['key']}  watermark: {t['watermark']!r}  image: {t['image']}")
    head = payload["headline"]
    if head:
        ev = next(e for e in payload["events"] if e["id"] == head["id"])
        print(f"Headline ({head['mode']}): {ev['title']} — {ev['subtitle']} — {fmt(ev['start'])}")
    if payload["weather"]:
        w = payload["weather"]
        print(f"Weather: {w['temp']}{w['unit']} {w['text']}")
    print(f"Errors: {payload['errors'] or 'none'}")
    print()
    for ev in payload["events"]:
        flag = {"live": "LIVE ", "final": "done ", "pre": "     "}[ev["status"]]
        extra = f"  [{ev['score']}]" if ev["score"] else ""
        print(f"{flag}{fmt(ev['start'])}  {ev['tag']:<4} {ev['importance']:>3}  {ev['title']}"
              f"  · {ev['subtitle']}{extra}")


if __name__ == "__main__":
    main()
