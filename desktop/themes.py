"""Pick the day's theme and the headline event shown big on the wallpaper."""

import hashlib
from datetime import datetime, timedelta

from .common import WALLPAPER, local_tz, parse_time

THEMES = {
    "f1": {"bg1": "#050507", "bg2": "#12070a", "bg3": "#3a0609", "accent": "#e10600",
           "pattern": "speed", "watermark": "LIGHTS OUT"},
    "brewers": {"bg1": "#06102a", "bg2": "#0d1f45", "bg3": "#163a73", "accent": "#ffc52f",
                "pattern": "diamond", "watermark": "BREWERS"},
    "badgers": {"bg1": "#220002", "bg2": "#6e0207", "bg3": "#c5050c", "accent": "#ffffff",
                "pattern": "field", "watermark": "ON WISCONSIN"},
    "madison": {"bg1": "#06182b", "bg2": "#0e3352", "bg3": "#1f5a85", "accent": "#7cc4ff",
                "pattern": "waves", "watermark": "MADISON"},
    "autumn": {"bg1": "#140904", "bg2": "#3d1709", "bg3": "#9a4317", "accent": "#ffb26b",
               "pattern": "waves", "watermark": "MADISON"},
    "winter": {"bg1": "#060b15", "bg2": "#12223a", "bg3": "#3b5d88", "accent": "#cfe6ff",
               "pattern": "waves", "watermark": "MADISON"},
    "spring": {"bg1": "#07120c", "bg2": "#163623", "bg3": "#4a8556", "accent": "#c9f2a6",
               "pattern": "waves", "watermark": "MADISON"},
    "summer": {"bg1": "#03141a", "bg2": "#0a3843", "bg3": "#15707d", "accent": "#ffd27a",
               "pattern": "waves", "watermark": "MADISON"},
}
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
THEME_MIN_IMPORTANCE = 55


def pick(cfg: dict, events: list[dict], now: datetime) -> tuple[dict, dict | None]:
    """Return (theme, headline) for right now."""
    tz = local_tz(cfg)
    today = now.astimezone(tz).date()
    shows = [e for e in events if e["kind"] != "calendar" and not e.get("tbd")]
    todays = [e for e in shows
              if parse_time(e["start"]).astimezone(tz).date() == today or e["status"] == "live"]
    top = max((e for e in todays if e["importance"] >= THEME_MIN_IMPORTANCE),
              key=lambda e: e["importance"], default=None)

    key = (top["theme"] if top and top["theme"] in _themes(cfg) else None) or SEASONS[today.month]
    theme = dict(_themes(cfg)[key], key=key)
    theme["watermark"] = _watermark(key, top) or theme["watermark"]
    theme["image"] = _image_for(key, today)
    return theme, _headline(shows, todays, top, now)


def _themes(cfg: dict) -> dict:
    themes = {k: dict(v) for k, v in THEMES.items()}
    for key, custom in cfg.get("themes", {}).items():
        themes.setdefault(key, dict(THEMES["madison"])).update(custom)
    return themes


def _watermark(key: str, top: dict | None) -> str | None:
    if not top:
        return None
    if key == "f1":
        return top.get("meeting", "").replace("Grand Prix", "").strip().upper() or None
    if top.get("postseason"):
        return top["subtitle"].split(" · ")[0].upper()
    return None


def _headline(shows: list[dict], todays: list[dict], top: dict | None, now: datetime) -> dict | None:
    by_importance = lambda e: (e["importance"], -parse_time(e["start"]).timestamp())
    live = [e for e in shows if e["status"] == "live"]
    if live:
        return {"id": max(live, key=by_importance)["id"], "mode": "live"}
    later_today = [e for e in todays if e["status"] == "pre" and e["importance"] >= 50]
    if later_today:
        return {"id": max(later_today, key=by_importance)["id"], "mode": "today"}
    if top:
        return {"id": top["id"], "mode": "final"}
    soon = sorted((e for e in shows if e["status"] == "pre" and e["importance"] >= 60
                   and parse_time(e["start"]) <= now + timedelta(days=5)),
                  key=lambda e: e["start"])
    if soon:
        return {"id": soon[0]["id"], "mode": "next"}
    return None


def _image_for(key: str, day) -> str | None:
    """Your own photos: drop them in wallpaper/themes/<theme>/ and one is picked per day."""
    folder = WALLPAPER / "themes" / key
    if not folder.is_dir():
        return None
    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_TYPES)
    if not images:
        return None
    n = int(hashlib.md5(day.isoformat().encode()).hexdigest(), 16) % len(images)
    return f"themes/{key}/{images[n].name}"
