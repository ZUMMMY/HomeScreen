"""F1 race mode: a live timing page at http://localhost:8765 to keep open alongside the race.

The same port serves the page and streams the timing to it over a websocket. fetch.py starts
this shortly before each session. To try it by hand:

    python live.py                              live session (F1 timing feed)
    python live.py --replay latest              replay the latest race from OpenF1
    python live.py --replay latest --speed 10 --from-lap 20
    python live.py --replay 11396               any OpenF1 session_key
    python live.py --replay-file cache/recordings/<file>.txt
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import time
import webbrowser
from collections import deque
from pathlib import Path

from websockets.asyncio.server import broadcast, serve

from websockets.datastructures import Headers
from websockets.http11 import Response

from desktop.common import CACHE, WALLPAPER, load_config, now_utc, parse_time, read_json, setup_logging, to_iso, write_json
from desktop.race import openf1, track
from desktop.race.state import RaceState

log = logging.getLogger("live")

MAX_RUNTIME = 6 * 3600
STALL_SECONDS = 120
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
                 ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}


def serve_page(connection, request):
    """Plain HTTP requests get the page's files; websocket upgrades go on to Hub.handler."""
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    name = request.path.split("?")[0].lstrip("/") or "index.html"
    path = (WALLPAPER / name).resolve()
    if WALLPAPER.resolve() not in path.parents or not path.is_file():
        return connection.respond(404, "Not found")
    body = path.read_bytes()
    if name == "index.html":  # served by race mode: the timing screen only
        body = body.replace(b"<body>", b'<body class="race-only">', 1)
    headers = Headers([("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream")),
                       ("Content-Length", str(len(body))), ("Cache-Control", "no-store")])
    return Response(200, "OK", headers, body)


class Hub:
    """Websocket clients (the wallpaper) and what to send them."""

    def __init__(self, extras: dict):
        self.clients = set()
        self.extras = extras
        self.track_msg = None
        self.track_transform = None
        self.last_snapshot = None
        self._track_loading = False

    async def handler(self, ws):
        self.clients.add(ws)
        try:
            if self.track_msg:
                await ws.send(self.track_msg)
            if self.last_snapshot:
                await ws.send(self.last_snapshot)
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)

    def send_snapshot(self, state: RaceState, now: float) -> None:
        snap = state.snapshot(now, self.track_transform)
        snap.update(self.extras)
        snap["estimated"] = not any(d["xy"] for d in snap["drivers"])
        self.last_snapshot = json.dumps(snap, separators=(",", ":"))
        broadcast(self.clients, self.last_snapshot)

    async def ensure_track(self, state: RaceState) -> None:
        circuit = state.info.get("Meeting", {}).get("Circuit", {}).get("Key")
        if self.track_msg or self._track_loading or not circuit:
            return
        self._track_loading = True
        year = state.info.get("Year") or int((state.info.get("StartDate") or "2026")[:4])
        outline = await asyncio.to_thread(track.load, circuit, year)
        if outline:
            self.track_transform = outline.pop("transform")
            self.track_msg = json.dumps(outline, separators=(",", ":"))
            broadcast(self.clients, self.track_msg)
        else:
            log.warning("No track outline for circuit %s; the map will be empty", circuit)
            self.track_msg = json.dumps({"type": "track", "points": [], "corners": [], "mini": []})


async def run_replay(msgs, start_t: float, speed: float, hub: Hub, linger: float) -> None:
    state = RaceState()
    i = 0
    while i < len(msgs) and msgs[i][0] <= start_t:
        state.apply(msgs[i][1], msgs[i][2], msgs[i][0])
        i += 1
    wall0 = time.monotonic()
    next_snap = 0.0
    finished_at = None
    log.info("Replaying %d messages at %gx", len(msgs) - i, speed)
    while True:
        now = start_t + (time.monotonic() - wall0) * speed
        while i < len(msgs) and msgs[i][0] <= now:
            state.apply(msgs[i][1], msgs[i][2], msgs[i][0])
            i += 1
        await hub.ensure_track(state)
        if time.monotonic() >= next_snap:
            hub.send_snapshot(state, now)
            next_snap = time.monotonic() + 1
        if state.finished():
            finished_at = finished_at or now
        if i >= len(msgs) or (finished_at and now - finished_at > 60):  # archives run on past the flag
            break
        await asyncio.sleep(0.05)
    log.info("Replay finished; holding the final classification for %ds", linger)
    hub.extras["speed"] = 0
    end = time.monotonic() + linger
    while time.monotonic() < end:
        hub.send_snapshot(state, now)
        await asyncio.sleep(1)


async def run_live(cfg: dict, hub: Hub, linger: float) -> None:
    from desktop.race.f1live import LiveFeed

    rm = cfg.get("race_mode", {})
    delay = float(rm.get("delay_seconds", 0))
    record = CACHE / "recordings" / f"{now_utc():%Y-%m-%d_%H%M}.txt"
    feed = LiveFeed(record, rm.get("f1tv_login", False))
    feed.start()

    state = RaceState()
    pending = deque()
    started = time.time()
    last_msg = started
    finished_at = None
    saved_status = None
    give_up = _scheduled_end(started) + 1800
    next_snap = 0.0
    while True:
        while not feed.q.empty():
            item = feed.q.get_nowait()
            pending.append(item)
            last_msg = item[0]
        now = time.time()
        while pending and pending[0][0] + delay <= now:
            _, topic, data, t = pending.popleft()
            state.apply(topic, data, t)
        await hub.ensure_track(state)
        if now >= next_snap:
            hub.send_snapshot(state, now - delay)
            next_snap = now + 1

        if state.finished():
            finished_at = finished_at or now
            if saved_status != state.status:
                save_results(state)
                saved_status = state.status
            if now - finished_at > linger:
                log.info("Session over; exiting")
                break
        elif now - last_msg > STALL_SECONDS:
            log.warning("No timing data for %ds; reconnecting", STALL_SECONDS)
            feed.stop()
            feed = LiveFeed(record, rm.get("f1tv_login", False))
            feed.start()
            last_msg = now
        if now - started > MAX_RUNTIME or (not state.info and now > give_up):
            log.info("No session found in time; exiting")
            break
        await asyncio.sleep(0.1)
    feed.stop()


def _scheduled_end(started: float) -> float:
    """End time of the F1 session fetch.py found around now, else three hours from now."""
    for ev in read_json(CACHE / "source-f1.json", []) or []:
        start, end = parse_time(ev["start"]).timestamp(), parse_time(ev["end"]).timestamp()
        if start - 3600 <= started <= end + 3600:
            return end
    return started + 3 * 3600


CHROME_PATHS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / r"Google\Chrome\Application\chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / r"Google\Chrome\Application\chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe",
]


def open_page(setting, url: str) -> None:
    """open_browser = "chrome" opens a new Chrome window; true uses your default browser."""
    if setting == "chrome":
        chrome = next((p for p in CHROME_PATHS if p.exists()), None)
        if chrome:
            log.info("Opening %s in Chrome", url)
            subprocess.Popen([str(chrome), "--new-window", url], close_fds=True)
            return
        log.warning("Chrome not found; opening the timing page in your default browser")
    if setting:
        webbrowser.open(url)


def save_results(state: RaceState) -> None:
    snap = state.snapshot(time.time())
    race = snap["session"]["type"] == "Race"
    rows = []
    for d in snap["drivers"]:
        if not d["pos"]:
            continue
        gap = d["gap"] if d["gap"] != "Leader" else ""
        if not race and not gap:
            gap = d["best"]
        rows.append({"pos": d["pos"], "tla": d["tla"], "color": d["color"], "gap": gap})
    write_json(CACHE / "last_session.json", {
        "meeting": snap["session"]["meeting"].replace("Grand Prix", "GP"),
        "session": snap["session"]["name"],
        "time": to_iso(now_utc()),
        "results": rows[:10],
    })
    log.info("Saved results for %s", snap["session"]["name"])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replay", metavar="SESSION", help="'latest', 'latest-any' or an OpenF1 session_key")
    parser.add_argument("--replay-file", metavar="PATH", help="a recording from cache/recordings")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--from-lap", type=int)
    parser.add_argument("--from-minute", type=float)
    parser.add_argument("--linger", type=float, help="seconds to keep showing the result afterwards")
    parser.add_argument("--port", type=int)
    parser.add_argument("--start-at", metavar="TIME", help="wait until this ISO time before starting")
    args = parser.parse_args()

    setup_logging("live")
    if args.start_at:
        wait = parse_time(args.start_at).timestamp() - time.time()
        if wait > 0:
            log.info("Waiting %.0f minutes for the session", wait / 60)
            await asyncio.sleep(wait)
    cfg = load_config()
    rm = cfg.get("race_mode", {})
    port = args.port or rm.get("port", 8765)
    replaying = bool(args.replay or args.replay_file)
    hub = Hub({
        "mode": "replay" if replaying else "live",
        "speed": args.speed if replaying else 1,
        "delay": 0 if replaying else rm.get("delay_seconds", 0),
        "favorites": rm.get("favorite_drivers", []),
    })

    async with serve(hub.handler, "127.0.0.1", port, process_request=serve_page):
        url = f"http://localhost:{port}"
        log.info("Live timing at %s", url)
        if not (args.replay or args.replay_file):
            open_page(rm.get("open_browser", False), url)
        await _run(args, cfg, hub)


async def _run(args, cfg: dict, hub: Hub) -> None:
    rm = cfg.get("race_mode", {})
    if args.replay:
        session = openf1.find_session(args.replay)
        log.info("Replaying %s %s (session %s)", session.get("location"), session["session_name"],
                 session["session_key"])
        msgs = openf1.build_messages(session, openf1.load(session))
        start = openf1.replay_start(session, msgs, args.from_lap, args.from_minute)
        await run_replay(msgs, start, args.speed, hub, args.linger or 120)
    elif args.replay_file:
        from desktop.race.f1live import read_recording
        msgs = read_recording(args.replay_file)
        start = msgs[0][0] + (args.from_minute or 0) * 60
        await run_replay(msgs, start, args.speed, hub, args.linger or 120)
    else:
        await run_live(cfg, hub, args.linger or rm.get("linger_minutes", 20) * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        # also covers the port being taken by a copy that's already running
        logging.getLogger("live").exception("Race mode stopped")
