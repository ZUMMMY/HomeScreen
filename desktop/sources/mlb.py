"""MLB games (the Brewers by default) from the public MLB Stats API."""

from collections import defaultdict
from datetime import datetime, timedelta

from ..common import get_json, make_event, parse_time, watch_window

API = "https://statsapi.mlb.com/api/v1/schedule"

STATUS = {"Preview": "pre", "Live": "live", "Final": "final"}
# gameType: R regular, F wild card, D division series, L LCS, W World Series, S spring
IMPORTANCE = {"W": 99, "L": 97, "D": 96, "F": 95, "R": 80, "S": 40}
POSTSEASON = {"F", "D", "L", "W"}


def fetch(cfg: dict, now: datetime) -> list[dict]:
    lo, hi = watch_window(cfg, now)
    priority = cfg.get("priority", {})
    events = []
    for team in cfg.get("mlb", []):
        data = get_json(API, {
            "sportId": 1,
            "teamId": team["team_id"],
            # look back further so postseason series records can be counted
            "startDate": (lo - timedelta(days=12)).date().isoformat(),
            "endDate": hi.date().isoformat(),
            "hydrate": "linescore,broadcasts(all),team",
        })
        games = [g for d in data.get("dates", []) for g in d["games"]]
        series = _series_records(games, team["team_id"])
        for g in games:
            start = parse_time(g["gameDate"])
            if start < lo:
                continue
            events.append(_to_event(g, team, series, priority))
    return events


def _to_event(g: dict, team: dict, series: dict, priority: dict) -> dict:
    away, home = g["teams"]["away"], g["teams"]["home"]
    we_are_home = home["team"]["id"] == team["team_id"]
    opp = away if we_are_home else home
    start = parse_time(g["gameDate"])
    game_type = g.get("gameType", "R")
    postseason = game_type in POSTSEASON
    state = STATUS.get(g["status"]["abstractGameState"], "pre")
    detailed = g["status"]["detailedState"]

    title = f"{team['name']} {'vs' if we_are_home else '@'} {opp['team'].get('teamName', opp['team']['name'])}"
    if postseason:
        sub = g.get("seriesDescription", "Postseason")
        if g.get("seriesGameNumber"):
            sub += f" · Game {g['seriesGameNumber']}"
        if g.get("ifNecessary") == "Y":
            sub += " · if needed"
        elif record := series.get(g.get("seriesDescription")):
            sub += f" · {record}"
    else:
        sub = g.get("venue", {}).get("name", "")

    score = detail = None
    if state in ("live", "final") and "score" in away:
        score = (f"{away['team'].get('abbreviation', '')} {away.get('score', 0)} – "
                 f"{home.get('score', 0)} {home['team'].get('abbreviation', '')}")
    if state == "live":
        ls = g.get("linescore", {})
        detail = f"{ls.get('inningState', '')} {ls.get('currentInningOrdinal', '')}".strip()
    elif state == "final":
        detail = detailed
    elif detailed in ("Postponed", "Delayed Start", "Suspended"):
        detail = detailed

    tv = []
    for b in g.get("broadcasts", []):
        if b.get("type") != "TV" or b.get("language", "en") != "en":
            continue
        for name in b.get("name", "").split(" / "):
            if name and name.upper() not in (t.upper() for t in tv):
                tv.append(name)
    category = "mlb-postseason" if postseason else "mlb"
    return make_event(
        id=f"mlb-{g['gamePk']}",
        source="mlb",
        category=category,
        title=title,
        subtitle=sub,
        start=start,
        end=start + timedelta(hours=3, minutes=15),
        tag=team.get("short", "MLB"),
        color="#ffc52f",
        theme=team.get("theme", "brewers"),
        importance=priority.get(category, IMPORTANCE.get(game_type, 80)) - (5 if g.get("ifNecessary") == "Y" else 0),
        status=state,
        score=score,
        detail=detail,
        tv=" / ".join(tv[:2]) or None,
        location=g.get("venue", {}).get("name"),
        local=False,
        postseason=postseason,
    )


def _series_records(games: list[dict], team_id: int) -> dict:
    """'MIL leads 3–1' style text per postseason series, from finished games."""
    wins: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    abbr = {}
    to_win = {}
    for g in games:
        if g.get("gameType") not in POSTSEASON or g["status"]["abstractGameState"] != "Final":
            continue
        key = g.get("seriesDescription")
        to_win[key] = g.get("gamesInSeries", 7) // 2 + 1
        for side in ("away", "home"):
            t = g["teams"][side]
            abbr[t["team"]["id"]] = t["team"].get("abbreviation", "")
            if t.get("isWinner"):
                wins[key][0 if t["team"]["id"] == team_id else 1] += 1
    out = {}
    for key, (us, them) in wins.items():
        name = abbr.get(team_id, "")
        if us >= to_win[key]:
            out[key] = f"{name} wins {us}–{them}"
        elif them >= to_win[key]:
            out[key] = f"{name} loses {us}–{them}"
        elif us == them:
            out[key] = f"Series tied {us}–{them}"
        elif us > them:
            out[key] = f"{name} leads {us}–{them}"
        else:
            out[key] = f"{name} trails {us}–{them}"
    return out
