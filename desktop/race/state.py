"""Running state of an F1 session, built from F1 live-timing style messages.

Every feed (F1's live timing, a recording, or an OpenF1 replay) is turned into
(time, topic, data) messages in F1's own format, so this is the only place that
needs to understand them. snapshot() is what the wallpaper draws.
"""

import base64
import copy
import json
import math
import zlib

from ..common import parse_time

TRACK_STATUS = {"1": "green", "2": "yellow", "4": "sc", "5": "red", "6": "vsc", "7": "vsc-ending"}
COMPOUNDS = {"SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W"}
FINISHED = {"Finished", "Finalised", "Ends"}


def deep_merge(target, update):
    """Apply an F1 feed delta. Lists in the feed are patched with {"index": value} dicts."""
    if isinstance(target, list) and isinstance(update, dict):
        for k, v in update.items():
            if not str(k).isdigit():
                continue
            i = int(k)
            while len(target) <= i:
                target.append({})
            if isinstance(v, dict) and isinstance(target[i], (dict, list)):
                deep_merge(target[i], v)
            else:
                target[i] = copy.deepcopy(v)
        return target
    for k, v in update.items():
        if k == "_deleted":
            for name in v if isinstance(v, list) else []:
                target.pop(name, None)
            continue
        cur = target.get(k)
        if isinstance(v, dict) and isinstance(cur, (dict, list)):
            deep_merge(cur, v)
        else:
            target[k] = copy.deepcopy(v)
    return target


def decode_z(data: str):
    """Position.z / CarData.z payloads are base64 raw-deflate JSON."""
    return json.loads(zlib.decompress(base64.b64decode(data), -zlib.MAX_WBITS))


def lap_seconds(text) -> float | None:
    if not text:
        return None
    try:
        parts = str(text).split(":")
        return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    except ValueError:
        return None


def fmt_lap(sec: float | None) -> str:
    if sec is None:
        return ""
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


def _items(x):
    """Iterate a feed list that may arrive as a list or as an {"index": value} dict."""
    if isinstance(x, list):
        return enumerate(x)
    if isinstance(x, dict):
        return sorted((int(k), v) for k, v in x.items() if str(k).isdigit())
    return ()


class RaceState:
    def __init__(self):
        self.t = 0.0
        self.info: dict = {}
        self.drivers: dict = {}
        self.timing: dict = {"Lines": {}}
        self.app: dict = {"Lines": {}}
        self.lapcount: dict = {}
        self.weather: dict = {}
        self.track = {"Status": "1", "Message": "AllClear"}
        self.status = "Inactive"
        self.clock: dict = {}
        self.rc: list = []
        self.seg_count = 0
        self.progress: dict = {}      # num -> {"seg": global mini-sector index, "t": crossing time}
        self.pace: dict = {}          # num -> recent lap time in seconds
        self.sector_best = [math.inf] * 3
        self.sector_pb: dict = {}
        self.sector_color: dict = {}
        self.sector_prev: dict = {}
        self.xy: dict = {}            # num -> (t, x, y) when exact positions are available
        self.messages = 0

    # ------------------------------------------------------------------ input

    def apply(self, topic: str, data, t: float) -> None:
        self.messages += 1
        self.t = max(self.t, t)
        if topic.endswith(".z") and isinstance(data, str):
            data = decode_z(data)
            topic = topic[:-2]
        handler = getattr(self, "_on_" + topic, None)
        if handler and isinstance(data, (dict, list)):
            handler(data, t)

    def _on_SessionInfo(self, data, t):
        deep_merge(self.info, data)

    def _on_DriverList(self, data, t):
        for num, d in data.items():
            if isinstance(d, dict) and str(num).isdigit():
                deep_merge(self.drivers.setdefault(num, {}), d)

    def _on_TimingAppData(self, data, t):
        deep_merge(self.app, data)

    def _on_LapCount(self, data, t):
        deep_merge(self.lapcount, data)

    def _on_WeatherData(self, data, t):
        deep_merge(self.weather, data)

    def _on_TrackStatus(self, data, t):
        deep_merge(self.track, data)

    def _on_SessionStatus(self, data, t):
        self.status = data.get("Status", self.status)

    def _on_ExtrapolatedClock(self, data, t):
        deep_merge(self.clock, data)

    def _on_RaceControlMessages(self, data, t):
        msgs = data.get("Messages")
        for _, m in _items(msgs):
            if isinstance(m, dict) and m.get("Message"):
                self.rc.append(m)
                if m.get("Flag") == "CHEQUERED" and self.info.get("Type") == "Race":
                    self.status = "Finished"
        self.rc = self.rc[-30:]

    def _on_Position(self, data, t):
        for frame in data.get("Position", []):
            ts = parse_time(frame["Timestamp"]).timestamp()
            for num, p in frame.get("Entries", {}).items():
                self.xy[num] = (ts, p.get("X", 0), p.get("Y", 0))

    def _on_TimingData(self, data, t):
        lines = self.timing.setdefault("Lines", {})
        for num, upd in (data.get("Lines") or {}).items():
            if not isinstance(upd, dict):
                continue
            cur = lines.setdefault(num, {})
            seg_updates = list(self._segment_updates(upd))
            self._track_sectors(num, upd, cur)
            deep_merge(cur, upd)
            if seg_updates:
                self._track_progress(num, cur, seg_updates, t)
            last = (upd.get("LastLapTime") or {}).get("Value") if isinstance(upd.get("LastLapTime"), dict) else None
            if last:
                self._track_pace(num, lap_seconds(last))
        rest = {k: v for k, v in data.items() if k != "Lines"}
        if rest:
            deep_merge(self.timing, rest)

    # --------------------------------------------------------- derived values

    @staticmethod
    def _segment_updates(upd):
        for si, sec in _items(upd.get("Sectors")):
            if not isinstance(sec, dict):
                continue
            for gi, seg in _items(sec.get("Segments")):
                if isinstance(seg, dict) and seg.get("Status"):
                    yield si, gi, seg["Status"]

    def _track_progress(self, num, cur, seg_updates, t):
        counts = []
        for _, sec in _items(cur.get("Sectors")):
            segs = sec.get("Segments") if isinstance(sec, dict) else None
            counts.append(len(segs) if isinstance(segs, list) else 0)
        total = sum(counts)
        if not total:
            return
        self.seg_count = max(self.seg_count, total)
        offsets = [sum(counts[:i]) for i in range(len(counts))]
        newest = max(offsets[si] + gi for si, gi, _ in seg_updates if si < len(offsets))
        prev = self.progress.get(num)
        if prev is None or newest > prev["seg"] or (newest < prev["seg"] and (newest <= 2 or prev["seg"] >= total - 3)):
            self.progress[num] = {"seg": newest, "t": t}

    def _track_sectors(self, num, upd, cur):
        colors = self.sector_color.setdefault(num, [None] * 3)
        pbs = self.sector_pb.setdefault(num, [math.inf] * 3)
        prevs = self.sector_prev.setdefault(num, [""] * 3)
        for si, sec in _items(upd.get("Sectors")):
            if si > 2 or not isinstance(sec, dict) or "Value" not in sec:
                continue
            value = sec["Value"]
            if not value:
                old = _items(cur.get("Sectors"))
                for i, s in old:
                    if i == si and isinstance(s, dict) and s.get("Value"):
                        prevs[si] = s["Value"]
                continue
            v = lap_seconds(value)
            if v is None:
                continue
            if sec.get("OverallFastest") or v <= self.sector_best[si]:
                colors[si] = "purple"
            elif sec.get("PersonalFastest") or v <= pbs[si]:
                colors[si] = "green"
            else:
                colors[si] = "yellow"
            pbs[si] = min(pbs[si], v)
            self.sector_best[si] = min(self.sector_best[si], v)

    def _track_pace(self, num, sec):
        if not sec:
            return
        best = min((p for p in self.pace.values() if p), default=sec)
        if sec < best * 1.8:  # skip laps spent in the pits or behind a red flag
            self.pace[num] = sec

    # ----------------------------------------------------------------- output

    def finished(self) -> bool:
        return self.status in FINISHED

    def session_type(self) -> str:
        return self.info.get("Type", "")

    def remaining(self, now: float) -> float | None:
        if not self.clock.get("Remaining"):
            return None
        rem = lap_seconds(self.clock["Remaining"]) or 0
        if self.clock.get("Extrapolating") and self.clock.get("Utc"):
            rem -= now - parse_time(self.clock["Utc"]).timestamp()
        return max(rem, 0)

    def snapshot(self, now: float, transform=None) -> dict:
        lines = self.timing.get("Lines", {})
        race = self.session_type() == "Race"
        bests = {n: lap_seconds(_best_lap(l)) for n, l in lines.items()}
        fastest_num = min((n for n in bests if bests[n]), key=lambda n: bests[n], default=None)
        fastest = bests.get(fastest_num)
        typical = sorted(p for p in self.pace.values() if p)
        default_pace = typical[len(typical) // 2] if typical else 95.0

        drivers = []
        for num, d in self.drivers.items():
            line = lines.get(num, {})
            prog = self.progress.get(num, {})
            stints = list(dict(_items(self.app.get("Lines", {}).get(num, {}).get("Stints"))).values())
            stint = stints[-1] if stints else {}
            sectors = []
            for si in range(3):
                sec = dict(_items(line.get("Sectors"))).get(si, {})
                value = sec.get("Value", "") if isinstance(sec, dict) else ""
                prev = self.sector_prev.get(num, [""] * 3)[si]
                sectors.append({
                    "v": value or prev,
                    "c": self.sector_color.get(num, [None] * 3)[si] if value else None,
                    "stale": not value,
                })
            mini = [s.get("Status", 0) for _, sec in _items(line.get("Sectors"))
                    if isinstance(sec, dict) for _, s in _items(sec.get("Segments")) if isinstance(s, dict)]
            gap, interval = _gaps(line, race, bests.get(num), fastest, num == fastest_num)
            xy = None
            if transform and num in self.xy and now - self.xy[num][0] < 5:
                xy = transform(self.xy[num][1], self.xy[num][2])
            drivers.append({
                "num": num,
                "tla": d.get("Tla", num),
                "name": d.get("FullName") or d.get("BroadcastName", ""),
                "team": d.get("TeamName", ""),
                "color": "#" + (d.get("TeamColour") or "888888"),
                "pos": _int(line.get("Position")),
                "gap": gap,
                "int": interval,
                "last": (line.get("LastLapTime") or {}).get("Value", ""),
                "best": _best_lap(line),
                "laps": line.get("NumberOfLaps"),
                "sectors": sectors,
                "mini": mini,
                "tyre": {
                    "c": COMPOUNDS.get(str(stint.get("Compound", "")).upper(), "?") if stint else None,
                    "age": stint.get("TotalLaps"),
                    "new": str(stint.get("New", "")).lower() == "true",
                },
                "pits": line.get("NumberOfPitStops") or 0,
                "inPit": bool(line.get("InPit")),
                "out": bool(line.get("Retired") or line.get("Stopped")),
                "knocked": bool(line.get("KnockedOut")),
                "seg": prog.get("seg", -1),
                "segT": prog.get("t"),
                "pace": self.pace.get(num) or default_pace,
                "xy": xy,
                "fastest": num == fastest_num,
            })
        drivers.sort(key=lambda x: (x["pos"] or 99, x["tla"]))

        meeting = self.info.get("Meeting", {})
        part = self.timing.get("SessionPart")
        n = len(drivers)
        cut = None
        if self.session_type() == "Qualifying" and part in (1, 2) and n > 10:
            cut = 10 + (n - 10) // 2 if part == 1 else 10
        return {
            "type": "snapshot",
            "t": now,
            "session": {
                "meeting": meeting.get("Name", ""),
                "circuit": meeting.get("Circuit", {}).get("ShortName", ""),
                "name": self.info.get("Name", ""),
                "type": self.session_type(),
                "status": self.status,
                "part": part,
                "cut": cut,
                "lap": self.lapcount.get("CurrentLap"),
                "laps": self.lapcount.get("TotalLaps"),
                "remaining": self.remaining(now),
                "clockRunning": bool(self.clock.get("Extrapolating")),
                "start": self.info.get("StartDate"),
                "gmtOffset": self.info.get("GmtOffset"),
            },
            "track": {
                "status": TRACK_STATUS.get(str(self.track.get("Status")), "green"),
                "message": self.track.get("Message", ""),
            },
            "weather": {
                "air": self.weather.get("AirTemp"),
                "track": self.weather.get("TrackTemp"),
                "rain": str(self.weather.get("Rainfall", "0")) not in ("0", "", "False", "false"),
                "humidity": self.weather.get("Humidity"),
            },
            "rc": [{"utc": m.get("Utc"), "msg": m.get("Message"), "flag": m.get("Flag"),
                    "cat": m.get("Category")} for m in reversed(self.rc[-6:])],
            "segCount": self.seg_count,
            "drivers": drivers,
        }


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _best_lap(line: dict) -> str:
    best = (line.get("BestLapTime") or {}).get("Value") if isinstance(line.get("BestLapTime"), dict) else None
    if best:
        return best
    for _, b in sorted(_items(line.get("BestLapTimes")), reverse=True):
        if isinstance(b, dict) and b.get("Value"):
            return b["Value"]
    return ""


def _gaps(line: dict, race: bool, best: float | None, fastest: float | None, is_fastest: bool):
    if race:
        gap = line.get("GapToLeader", "")
        interval = (line.get("IntervalToPositionAhead") or {}).get("Value", "")
        if _int(line.get("Position")) == 1:
            gap, interval = "Leader", ""
        elif str(gap).startswith("LAP"):
            gap = ""
        return gap, interval
    gap = line.get("TimeDiffToFastest") or ""
    interval = line.get("TimeDiffToPositionAhead") or ""
    if isinstance(interval, dict):
        interval = interval.get("Value", "")
    if not gap and best and fastest:
        gap = "" if is_fastest else f"+{best - fastest:.3f}"
    return gap, interval
