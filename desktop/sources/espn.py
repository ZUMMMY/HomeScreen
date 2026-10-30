"""Team schedules and scores from ESPN's public site API (used for the Badgers).

This API is unofficial: it is what espn.com itself calls, and it can change.
"""

from datetime import datetime, time, timedelta

from ..common import get_json, local_tz, make_event, parse_time, watch_window

API = "https://site.api.espn.com/apis/site/v2/sports/{path}/teams/{team}/schedule"

STATUS = {"pre": "pre", "in": "live", "post": "final"}
MADISON_VENUES = ("Camp Randall", "Kohl Center", "UW Field House", "Field House", "LaBahn")


def fetch(cfg: dict, now: datetime) -> list[dict]:
    lo, hi = watch_window(cfg, now)
    tz = local_tz(cfg)
    events = []
    for team in cfg.get("espn", []):
        seen = set()
        for season_type in (2, 3):  # regular season, then postseason / bowls
            data = get_json(API.format(path=team["path"], team=team["team"]),
                            {"seasontype": season_type})
            for ev in data.get("events", []):
                if ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                start = parse_time(ev["date"])
                if lo <= start <= hi:
                    events.append(_to_event(ev, team, tz))
    return events


def _to_event(ev: dict, team: dict, tz) -> dict:
    comp = ev["competitions"][0]
    start = parse_time(ev["date"])
    tbd = not ev.get("timeValid", True)
    if tbd:
        # ESPN marks "date only" as midnight US Eastern (05:00Z); keep that date, drop the time.
        day = (start - timedelta(hours=5)).date()
        start = datetime.combine(day, time.min, tz)
    us = next((c for c in comp["competitors"] if c["team"]["id"] == team["team"]), comp["competitors"][0])
    opp = next((c for c in comp["competitors"] if c is not us), us)
    home = us.get("homeAway") == "home"
    rank = (opp.get("curatedRank") or {}).get("current")
    opp_name = opp["team"].get("shortDisplayName") or opp["team"]["displayName"]
    if rank and rank <= 25:
        opp_name = f"#{rank} {opp_name}"

    status = comp.get("status") or ev.get("status") or {}
    state = STATUS.get(status.get("type", {}).get("state"), "pre")
    score = None
    if state in ("live", "final"):
        score = f"{_abbr(us)} {_score(us)} – {_score(opp)} {_abbr(opp)}"

    venue = (comp.get("venue") or {}).get("fullName") or ""
    tv = [b.get("media", {}).get("shortName") for b in comp.get("broadcasts", [])]
    sport = team.get("sport", "")
    return make_event(
        id=f"espn-{ev['id']}",
        source="espn",
        category=f"espn-{sport.lower().replace(' ', '-').replace(chr(39), '')}",
        title=f"{team['name']} {'vs' if home else '@'} {opp_name}",
        subtitle=" · ".join(x for x in (sport, venue) if x),
        start=start,
        end=start + (timedelta(days=1) if tbd else
                     timedelta(hours=3 if "Football" in sport else 2, minutes=30)),
        tbd=tbd,
        all_day=tbd,
        tag=team.get("short", ""),
        color="#c5050c" if team.get("theme") == "badgers" else "#9aa4b2",
        theme=team.get("theme"),
        importance=team.get("importance", 70),
        status=state,
        score=score,
        detail=status.get("type", {}).get("shortDetail") if state != "pre" else None,
        tv=" / ".join(t for t in tv if t) or None,
        location=venue or None,
        local=home and any(v in venue for v in MADISON_VENUES),
        sport=sport,
    )


def _score(c: dict) -> str:
    s = c.get("score")
    if isinstance(s, dict):
        return s.get("displayValue", "")
    return str(s or 0)


def _abbr(c: dict) -> str:
    return c["team"].get("abbreviation", "")
