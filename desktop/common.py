"""Shared helpers: paths, config, HTTP, time and the common event shape."""

import json
import logging
import os
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
WALLPAPER = ROOT / "wallpaper"

log = logging.getLogger("desktop")


def setup_logging(name: str) -> None:
    CACHE.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(CACHE / f"{name}.log", maxBytes=1_000_000,
                            backupCount=1, encoding="utf-8")
    ]
    if sys.stderr is not None:  # pythonw.exe has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def load_config() -> dict:
    path = Path(os.environ.get("MADISON_DESKTOP_CONFIG", ROOT / "config.toml"))
    if not path.exists():
        log.warning("No config.toml yet, using config.example.toml")
        path = ROOT / "config.example.toml"
    with path.open("rb") as f:
        return tomllib.load(f)


_http: requests.Session | None = None


def http() -> requests.Session:
    global _http
    if _http is None:
        _http = requests.Session()
        _http.headers["User-Agent"] = "madison-desktop/1.0 (personal wallpaper)"
    return _http


def get_json(url: str, params: dict | None = None, timeout: int = 20):
    r = http().get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def local_tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg.get("timezone", "America/Chicago"))


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def watch_window(cfg: dict, now: datetime) -> tuple[datetime, datetime]:
    """Time range sources should return events for."""
    ahead = cfg.get("display", {}).get("watch_days_ahead", 21)
    return now - timedelta(days=2), now + timedelta(days=ahead)


def make_event(*, id: str, source: str, title: str, start: datetime,
               end: datetime | None = None, **extra) -> dict:
    """The one event shape every source returns and the wallpaper reads."""
    ev = {
        "id": id,
        "source": source,
        "kind": "watch",       # watch | calendar | local
        "category": source,
        "title": title,
        "subtitle": "",
        "start": to_iso(start),
        "end": to_iso(end or start + timedelta(hours=2)),
        "all_day": False,
        "tbd": False,          # date known, time not announced yet
        "tag": "",
        "color": None,
        "theme": None,
        "importance": 0,
        "status": None,        # pre | live | final; filled from the clock if a source leaves it empty
        "score": None,
        "detail": None,
        "tv": None,
        "location": None,
        "local": False,        # happening in Madison
    }
    ev.update(extra)
    return ev


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    write_text_atomic(path, json.dumps(data, indent=1, ensure_ascii=False))
