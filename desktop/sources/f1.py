"""F1 weekend sessions from the Jolpica API (the successor to Ergast)."""

from datetime import datetime, timedelta

from ..common import get_json, local_tz, make_event, parse_time, watch_window

API = "https://api.jolpi.ca/ergast/f1/{year}.json"

# (Jolpica key, label, kind, minutes)
SESSIONS = [
    ("FirstPractice", "Practice 1", "practice", 60),
    ("SecondPractice", "Practice 2", "practice", 60),
    ("ThirdPractice", "Practice 3", "practice", 60),
    ("SprintQualifying", "Sprint Qualifying", "sprint-quali", 45),
    ("SprintShootout", "Sprint Shootout", "sprint-quali", 45),
    ("Sprint", "Sprint", "sprint", 60),
    ("Qualifying", "Qualifying", "quali", 60),
]
IMPORTANCE = {"race": 100, "quali": 90, "sprint": 88, "sprint-quali": 75, "practice": 60}


def fetch(cfg: dict, now: datetime) -> list[dict]:
    opts = cfg.get("f1", {})
    if not opts.get("enabled", True):
        return []
    lo, hi = watch_window(cfg, now)
    tz = local_tz(cfg)
    priority = cfg.get("priority", {})

    years = {now.year, hi.year}
    events = []
    for year in sorted(years):
        races = get_json(API.format(year=year), {"limit": 100})["MRData"]["RaceTable"]["Races"]
        for race in races:
            sessions = [(race[k], label, kind, mins) for k, label, kind, mins in SESSIONS if k in race]
            sessions.append(({"date": race["date"], "time": race.get("time")}, "Race", "race", 120))
            for when, label, kind, mins in sessions:
                if kind == "practice" and not opts.get("show_practice", True):
                    continue
                if not when.get("time"):
                    continue
                start = parse_time(f"{when['date']}T{when['time']}")
                if not lo <= start <= hi:
                    continue
                local_hour = start.astimezone(tz).hour
                events.append(make_event(
                    id=f"f1-{year}-{race['round']}-{kind}-{label}".replace(" ", ""),
                    source="f1",
                    category=f"f1-{kind}",
                    title=f"{_short_name(race['raceName'])} · {label}",
                    subtitle=race["Circuit"]["circuitName"],
                    start=start,
                    end=start + timedelta(minutes=mins),
                    tag="F1",
                    color="#e10600",
                    theme="f1",
                    importance=priority.get(f"f1-{kind}", IMPORTANCE[kind]),
                    session=kind,
                    session_label=label,
                    meeting=race["raceName"],
                    round=int(race["round"]),
                    early=local_hour < 6 or local_hour >= 23,
                ))
    return events


def _short_name(race_name: str) -> str:
    return race_name.replace("Grand Prix", "GP")
