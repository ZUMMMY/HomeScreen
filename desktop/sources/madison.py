"""Around Madison: Ticketmaster events near the city plus your own madison_events.toml."""

import tomllib
from datetime import date, datetime, time, timedelta

from ..common import ROOT, get_json, local_tz, make_event, parse_time

TICKETMASTER = "https://app.ticketmaster.com/discovery/v2/events.json"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BIG_VENUES = ("Kohl Center", "Breese Stevens", "Alliant Energy Center", "Overture",
              "The Sylvee", "Orpheum", "Majestic", "Memorial Union", "Camp Randall")


def fetch(cfg: dict, now: datetime) -> list[dict]:
    opts = cfg.get("madison", {})
    events = _manual(cfg, opts, now)
    if opts.get("ticketmaster_key"):
        events += _ticketmaster(cfg, opts, now)
    return events


def _manual(cfg: dict, opts: dict, now: datetime) -> list[dict]:
    path = ROOT / opts.get("manual_file", "madison_events.toml")
    if not path.exists():
        return []
    with path.open("rb") as f:
        entries = tomllib.load(f).get("event", [])
    tz = local_tz(cfg)
    today = now.astimezone(tz).date()
    days = cfg.get("display", {}).get("days_ahead", 7) + 7
    out = []
    for i, e in enumerate(entries):
        for day in _occurrences(e, today, days):
            if e.get("start"):
                start = datetime.combine(day, time.fromisoformat(e["start"]), tz)
                end = (datetime.combine(day, time.fromisoformat(e["end"]), tz)
                       if e.get("end") else start + timedelta(hours=2))
            else:
                start = datetime.combine(day, time.min, tz)
                end = start + timedelta(days=1)
            out.append(make_event(
                id=f"msn-{i}-{day.isoformat()}",
                source="madison",
                kind="local",
                category="madison",
                title=e["title"],
                subtitle=e.get("location", ""),
                start=start,
                end=end,
                all_day=not e.get("start"),
                tag="MSN",
                color="#5ab0ff",
                theme="madison",
                importance=e.get("importance", 50),
                location=e.get("location"),
                local=True,
            ))
    return out


def _occurrences(e: dict, today: date, days: int):
    if e.get("date"):
        d = date.fromisoformat(e["date"])
        if today <= d <= today + timedelta(days=days):
            yield d
        return
    if not e.get("weekly"):
        return
    weekday = WEEKDAYS.index(e["weekly"][:3].title())
    for n in range(days + 1):
        d = today + timedelta(days=n)
        if d.weekday() != weekday:
            continue
        md = d.strftime("%m-%d")
        if e.get("from", "01-01") <= md <= e.get("until", "12-31"):
            yield d


def _ticketmaster(cfg: dict, opts: dict, now: datetime) -> list[dict]:
    loc = cfg.get("location", {})
    days = cfg.get("display", {}).get("days_ahead", 7) + 7
    data = get_json(TICKETMASTER, {
        "apikey": opts["ticketmaster_key"],
        "latlong": f"{loc.get('lat', 43.0731)},{loc.get('lon', -89.4012)}",
        "radius": opts.get("radius_miles", 8),
        "unit": "miles",
        "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": 60,
        "sort": "date,asc",
    })
    tz = local_tz(cfg)
    out = []
    seen = set()
    for ev in data.get("_embedded", {}).get("events", []):
        name = ev.get("name", "")
        # Badgers games already come from ESPN, with scores.
        if "Badgers" in name or "Wisconsin" in name:
            continue
        key = (name, ev["dates"]["start"].get("localDate"))
        if key in seen:
            continue
        seen.add(key)
        start_info = ev["dates"]["start"]
        if start_info.get("dateTime"):
            start = parse_time(start_info["dateTime"])
            all_day = False
        else:
            start = datetime.combine(date.fromisoformat(start_info["localDate"]), time.min, tz)
            all_day = True
        venue = (ev.get("_embedded", {}).get("venues") or [{}])[0].get("name", "")
        genre = (ev.get("classifications") or [{}])[0].get("segment", {}).get("name", "")
        out.append(make_event(
            id=f"tm-{ev['id']}",
            source="madison",
            kind="local",
            category="madison",
            title=name,
            subtitle=" · ".join(x for x in (venue, genre) if x),
            start=start,
            end=start + timedelta(hours=3),
            all_day=all_day,
            tag="MSN",
            color="#5ab0ff",
            theme="madison",
            importance=58 if any(v in venue for v in BIG_VENUES) else 50,
            location=venue or None,
            local=True,
        ))
    return out
