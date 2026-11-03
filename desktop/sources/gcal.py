"""Calendars through their iCal links: Google's secret address, iCloud's public link, etc."""

import hashlib
import logging
from datetime import date, datetime, time, timedelta

import icalendar
import recurring_ical_events

from ..common import CACHE, http, local_tz, make_event, read_json, write_json

log = logging.getLogger("gcal")

failed: list[str] = []  # calendars that couldn't be read on the last fetch (shown on the wallpaper)


def fetch(cfg: dict, now: datetime) -> list[dict]:
    tz = local_tz(cfg)
    days = cfg.get("display", {}).get("days_ahead", 7)
    start = datetime.combine(now.astimezone(tz).date(), time.min, tz) - timedelta(days=1)
    end = start + timedelta(days=days + 2)

    # games and races you added from a suggestion are already on the wallpaper from their own feed
    shown_elsewhere = {i["uid"] for i in (read_json(CACHE / "suggestions.json", {}) or {}).get("items", {}).values()
                       if i.get("uid") and i["kind"] != "movie"}
    events = []
    failed.clear()
    for cal in cfg.get("calendars", []):
        url = (cal.get("url") or "").strip()
        if not url:
            continue
        name = cal.get("name", "calendar")
        cache = CACHE / f"calendar-{hashlib.sha1(url.encode()).hexdigest()[:10]}.json"
        try:
            got = [_to_event(c, cal, tz) for c in _components(url, start, end)
                   if str(c.get("UID", "")) not in shown_elsewhere]
            write_json(cache, got)
        except Exception as exc:  # one bad link shouldn't hide the other calendars
            log.error("Calendar %r: %s", name, exc)
            failed.append(name)
            got = read_json(cache, []) or []
            for ev in got:
                ev["status"] = None
        events += got
    if failed and not events:
        raise RuntimeError(f"couldn't read {', '.join(failed)}")
    return events


def _components(url: str, start: datetime, end: datetime):
    if url.startswith("webcal://"):  # iCloud and others hand out webcal:// links; it's plain https
        url = "https://" + url[len("webcal://"):]
    r = http().get(url, timeout=30)
    r.raise_for_status()
    if not r.content.lstrip().startswith(b"BEGIN:VCALENDAR"):
        raise ValueError("the link returned a web page, not calendar data. For Google use "
                         "Settings > the calendar > Integrate calendar > Secret address in iCal format")
    ical = icalendar.Calendar.from_ical(r.content)
    for comp in recurring_ical_events.of(ical).between(start, end):
        if str(comp.get("STATUS", "")).upper() != "CANCELLED":
            yield comp


def _to_event(comp, cal: dict, tz) -> dict:
    dtstart = comp.decoded("DTSTART")
    dtend = comp.decoded("DTEND") if "DTEND" in comp else None
    all_day = not isinstance(dtstart, datetime)
    if all_day:
        start = datetime.combine(dtstart, time.min, tz)
        last = dtend if isinstance(dtend, date) else dtstart + timedelta(days=1)
        end = datetime.combine(last, time.min, tz)
    else:
        start = dtstart if dtstart.tzinfo else dtstart.replace(tzinfo=tz)
        end = dtend if isinstance(dtend, datetime) else start + timedelta(hours=1)
        end = end if end.tzinfo else end.replace(tzinfo=tz)

    uid = str(comp.get("UID", "")) + start.isoformat()
    return make_event(
        id="gcal-" + hashlib.sha1(uid.encode()).hexdigest()[:12],
        source="gcal",
        kind="calendar",
        category="calendar",
        title=str(comp.get("SUMMARY", "Busy")),
        start=start,
        end=end,
        all_day=all_day,
        tag=cal.get("name", ""),
        color=cal.get("color", "#8ab4f8"),
        location=str(comp.get("LOCATION", "")) or None,
    )
