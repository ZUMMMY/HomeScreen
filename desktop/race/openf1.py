"""Replays from OpenF1's free historical data, converted to F1 live-timing messages.

OpenF1 has every session since 2023 (lap and sector times, mini-sectors, gaps,
tyres, pit stops, race control, weather). Its real-time feed is paid, so this is
used for replays: testing race mode, or watching a session back later.
"""

import logging
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

from ..common import CACHE, get_json, now_utc, parse_time, read_json, write_json
from .state import fmt_lap

log = logging.getLogger("openf1")

BASE = "https://api.openf1.org/v1"
ENDPOINTS = ["drivers", "laps", "intervals", "position", "stints", "race_control",
             "weather", "pit", "session_result"]


def find_session(spec: str) -> dict:
    """'latest' = latest finished race, 'latest-any' = latest finished session, or a session_key."""
    if spec.isdigit():
        rows = get_json(f"{BASE}/sessions", {"session_key": spec})
        if not rows:
            raise SystemExit(f"OpenF1 has no session {spec}")
        return rows[0]
    now = now_utc()
    rows = get_json(f"{BASE}/sessions", {"year": now.year})
    done = [r for r in rows if parse_time(r["date_end"]) < now and not r.get("is_cancelled")]
    if spec == "latest":
        done = [r for r in done if r["session_name"] == "Race"] or done
    if not done:
        raise SystemExit("No finished sessions found on OpenF1")
    return max(done, key=lambda r: r["date_start"])


def load(session: dict) -> dict:
    key = session["session_key"]
    folder = CACHE / "openf1" / str(key)
    data = {}
    for ep in ENDPOINTS + ["meetings"]:
        path = folder / f"{ep}.json"
        rows = read_json(path)
        if rows is None:
            params = {"meeting_key": session["meeting_key"]} if ep == "meetings" else {"session_key": key}
            log.info("Downloading %s for session %s", ep, key)
            try:
                rows = get_json(f"{BASE}/{ep}", params, timeout=60)
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 404:
                    raise
                rows = []  # OpenF1 answers 404 when a session has none (e.g. gaps in qualifying)
            write_json(path, rows)
            time.sleep(0.4)  # be gentle with a free API
        data[ep] = rows
    return data


def ts(value) -> float:
    return parse_time(value).timestamp()


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def build_messages(session: dict, data: dict) -> list[tuple[float, str, dict]]:
    msgs: list[tuple[float, str, dict]] = []

    def emit(t, topic, payload):
        msgs.append((t, topic, payload))

    def line(t, num, payload):
        emit(t, "TimingData", {"Lines": {str(num): payload}})

    t0, t_end = ts(session["date_start"]), ts(session["date_end"])
    race = session["session_type"] == "Race"
    meeting = (data.get("meetings") or [{}])[0]
    laps = sorted((l for l in data["laps"] if l.get("date_start")),
                  key=lambda l: (l["driver_number"], l["lap_number"]))
    by_driver = defaultdict(list)
    for lap in laps:
        by_driver[lap["driver_number"]].append(lap)

    counts = next(([len(l.get(f"segments_sector_{i}") or []) for i in (1, 2, 3)]
                   for l in laps if all(l.get(f"segments_sector_{i}") for i in (1, 2, 3))), [7, 7, 7])
    full = [l for l in laps if l.get("lap_duration") and all(l.get(f"duration_sector_{i}") for i in (1, 2, 3))]
    typical = statistics.median(l["lap_duration"] for l in full) if full else 95.0
    split = ([statistics.mean(l[f"duration_sector_{i}"] / l["lap_duration"] for l in full) for i in (1, 2, 3)]
             if full else [1 / 3] * 3)
    total_laps = max((l["lap_number"] for l in laps), default=0)
    first_lap = min((ts(l["date_start"]) for l in laps if l["lap_number"] == 1), default=t0)
    start = first_lap - 1 if race else t0
    # opening state goes before everything, including pre-session position data
    t_open = min([t0 - 600] + [ts(r["date"]) for ep in ("position", "intervals", "weather")
                               for r in data[ep]]) - 1

    # ---- opening state
    emit(t_open, "SessionInfo", {
        "Meeting": {"Name": meeting.get("meeting_name", ""), "Location": session.get("location"),
                    "Circuit": {"Key": session["circuit_key"], "ShortName": session["circuit_short_name"]}},
        "Name": session["session_name"], "Type": session["session_type"],
        "StartDate": session["date_start"], "EndDate": session["date_end"],
        "GmtOffset": session.get("gmt_offset"), "Year": session.get("year"),
    })
    emit(t_open, "DriverList", {str(d["driver_number"]): {
        "RacingNumber": str(d["driver_number"]), "Tla": d.get("name_acronym"),
        "FullName": d.get("full_name"), "BroadcastName": d.get("broadcast_name"),
        "TeamName": d.get("team_name"), "TeamColour": d.get("team_colour") or "888888",
    } for d in data["drivers"]})
    emit(t_open, "TimingData", {"Lines": {str(d["driver_number"]): {
        "Position": "", "NumberOfLaps": 0, "NumberOfPitStops": 0, "InPit": not race,
        "LastLapTime": {"Value": ""}, "BestLapTime": {"Value": ""},
        "Sectors": [{"Value": "", "Segments": [{"Status": 0} for _ in range(n)]} for n in counts],
    } for d in data["drivers"]}})
    emit(t_open, "TrackStatus", {"Status": "1", "Message": "AllClear"})
    emit(t_open, "SessionStatus", {"Status": "Inactive"})
    if race:
        emit(t_open, "LapCount", {"CurrentLap": 1, "TotalLaps": total_laps})
    else:
        emit(t0, "ExtrapolatedClock", {"Utc": iso(t0), "Extrapolating": True,
                                       "Remaining": fmt_hms(t_end - t0)})
    emit(start, "SessionStatus", {"Status": "Started"})

    # ---- laps: mini-sectors, sectors, lap times
    lap_start = {}
    leader_lap = 1
    lap_starts = []
    for num, dl in by_driver.items():
        best = None
        for i, lap in enumerate(dl):
            t = ts(lap["date_start"])
            lap_start[(num, lap["lap_number"])] = t
            lap_starts.append((t, lap["lap_number"]))
            nxt = ts(dl[i + 1]["date_start"]) if i + 1 < len(dl) else None
            dur = lap.get("lap_duration") or ((nxt - t) if nxt and nxt - t < typical * 3 else typical)
            secs = [lap.get(f"duration_sector_{k}") for k in (1, 2, 3)]
            if not all(secs):
                secs = [dur * f for f in split]
            if nxt and t + sum(secs) > nxt - 0.05:  # keep this lap's timing before the next lap starts
                squeeze = (nxt - 0.05 - t) / sum(secs)
                secs = [s * squeeze for s in secs]
                dur = min(dur, nxt - 0.05 - t)
            if not race and lap.get("is_pit_out_lap"):
                line(t, num, {"InPit": False, "PitOut": True})
            if lap["lap_number"] > 1:
                line(t, num, {"Sectors": {str(k): {"Value": "", "Segments": {
                    str(j): {"Status": 0} for j in range(counts[k])}} for k in range(3)}})
            cursor = t
            for k in range(3):
                segs = lap.get(f"segments_sector_{k + 1}") or []
                for j, status in enumerate(segs):
                    if status:
                        line(cursor + (j + 1) / len(segs) * secs[k], num,
                             {"Sectors": {str(k): {"Segments": {str(j): {"Status": status}}}}})
                cursor += secs[k]
                if lap.get(f"duration_sector_{k + 1}"):
                    line(cursor, num, {"Sectors": {str(k): {"Value": f"{secs[k]:.3f}"}}})
            end = t + dur
            update = {"NumberOfLaps": lap["lap_number"]}
            if lap.get("lap_duration") and lap["lap_duration"] < typical * 1.6:  # not time sat in the garage
                update["LastLapTime"] = {"Value": fmt_lap(lap["lap_duration"])}
                if best is None or lap["lap_duration"] < best:
                    best = lap["lap_duration"]
                    update["BestLapTime"] = {"Value": fmt_lap(best)}
            line(end, num, update)

    if race:
        for t, n in sorted(lap_starts):
            if n > leader_lap:
                leader_lap = n
                emit(t, "LapCount", {"CurrentLap": n, "TotalLaps": total_laps})

    # ---- running order and gaps
    for p in data["position"]:
        line(ts(p["date"]), p["driver_number"], {"Position": str(p["position"])})
    for row in data["intervals"]:
        payload = {}
        if row.get("gap_to_leader") is not None:
            payload["GapToLeader"] = _gap(row["gap_to_leader"])
        if row.get("interval") is not None:
            payload["IntervalToPositionAhead"] = {"Value": _gap(row["interval"])}
        if payload:
            line(ts(row["date"]), row["driver_number"], payload)

    # ---- tyres
    for st in data["stints"]:
        num = st["driver_number"]
        age0 = st.get("tyre_age_at_start") or 0
        first = st.get("lap_start") or 1
        when = lap_start.get((num, first), start)
        idx = str((st.get("stint_number") or 1) - 1)
        emit(when, "TimingAppData", {"Lines": {str(num): {"Stints": {idx: {
            "Compound": st.get("compound") or "UNKNOWN", "New": "true" if age0 == 0 else "false",
            "TotalLaps": age0, "StartLaps": age0}}}}})
        for lap_no in range(first, (st.get("lap_end") or first) + 1):
            t = lap_start.get((num, lap_no + 1))
            if t:
                emit(t, "TimingAppData", {"Lines": {str(num): {"Stints": {idx: {
                    "TotalLaps": age0 + lap_no - first + 1}}}}})

    # ---- pit stops
    stops = defaultdict(int)
    for p in sorted(data["pit"], key=lambda p: p["date"]):
        num = p["driver_number"]
        stops[num] += 1
        t = ts(p["date"])
        lane = p.get("lane_duration") or p.get("pit_duration") or 22
        line(t, num, {"InPit": True, "NumberOfPitStops": stops[num]})
        line(t + lane, num, {"InPit": False, "PitOut": True})
        line(t + lane + 15, num, {"PitOut": False})

    # ---- race control, track status, qualifying parts
    status, yellow_sectors, part = "1", set(), None
    rcs = sorted(data["race_control"], key=lambda r: r["date"])
    # qualifying shows a chequered flag after Q1 and Q2 too; only the last one ends the session
    last_chequered = max((i for i, r in enumerate(rcs) if r.get("flag") == "CHEQUERED"), default=-1)
    for i, rc in enumerate(rcs):
        t = ts(rc["date"])
        emit(t, "RaceControlMessages", {"Messages": {str(i): {
            "Utc": rc["date"], "Lap": rc.get("lap_number"), "Category": rc.get("category"),
            "Flag": rc.get("flag"), "Scope": rc.get("scope"), "Message": rc.get("message")}}})
        new = _track_status(rc, status, yellow_sectors)
        if new != status:
            status = new
            emit(t, "TrackStatus", {"Status": status, "Message": STATUS_TEXT[status]})
        if rc.get("qualifying_phase") and rc["qualifying_phase"] != part:
            part = rc["qualifying_phase"]
            emit(t, "TimingData", {"SessionPart": part})
        if i == last_chequered:
            emit(t, "SessionStatus", {"Status": "Finished"})

    # ---- weather
    for w in data["weather"]:
        emit(ts(w["date"]), "WeatherData", {
            "AirTemp": str(w.get("air_temperature")), "TrackTemp": str(w.get("track_temperature")),
            "Humidity": str(w.get("humidity")), "Rainfall": str(w.get("rainfall") or 0),
            "WindSpeed": str(w.get("wind_speed"))})

    # ---- retirements
    last_end = {num: ts(dl[-1]["date_start"]) + (dl[-1].get("lap_duration") or typical)
                for num, dl in by_driver.items()}
    for r in data.get("session_result", []):
        if r.get("dnf") and r["driver_number"] in last_end:
            line(last_end[r["driver_number"]] + 20, r["driver_number"], {"Retired": True})

    final = max(m[0] for m in msgs) + 30
    emit(final, "SessionStatus", {"Status": "Finalised"})
    msgs.sort(key=lambda m: m[0])
    return msgs


def replay_start(session: dict, msgs, from_lap: int | None, from_minute: float | None) -> float:
    """Where the replay clock begins."""
    t0 = ts(session["date_start"])
    if from_minute is not None:
        return t0 + from_minute * 60
    if session["session_type"] == "Race":
        laps = [m for m in msgs if m[1] == "LapCount"]
        target = from_lap or 1
        hit = next((t for t, _, d in laps if d.get("CurrentLap", 0) >= target), None)
        if hit is not None:
            return hit - (5 if from_lap else 60)
    return t0 - 30


STATUS_TEXT = {"1": "AllClear", "2": "Yellow", "4": "SCDeployed", "5": "Red", "6": "VSCDeployed", "7": "VSCEnding"}


def _track_status(rc: dict, status: str, yellow: set) -> str:
    msg = (rc.get("message") or "").upper()
    flag, scope = rc.get("flag"), rc.get("scope")
    if rc.get("category") == "SafetyCar":
        if "VIRTUAL SAFETY CAR ENDING" in msg:
            return "7"
        if "VIRTUAL SAFETY CAR" in msg and "DEPLOYED" in msg:
            return "6"
        if "SAFETY CAR" in msg and "DEPLOYED" in msg:
            return "4"
        return status
    if flag == "RED":
        return "5"
    if scope == "Sector" and rc.get("sector") is not None:
        if flag in ("YELLOW", "DOUBLE YELLOW"):
            yellow.add(rc["sector"])
        elif flag == "CLEAR":
            yellow.discard(rc["sector"])
    if flag in ("GREEN", "CLEAR") and scope == "Track":
        yellow.clear()
        return "1"
    if status in ("4", "5", "6", "7"):
        return status
    return "2" if yellow else "1"


def _gap(v) -> str:
    if isinstance(v, (int, float)):
        return f"+{v:.3f}" if v else ""
    return str(v)


def fmt_hms(sec: float) -> str:
    sec = max(int(sec), 0)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
