"""Circuit outlines from the MultiViewer API (unofficial), cached on disk.

The outline comes from one recorded lap in F1's own coordinates (tenths of a
metre, y pointing up), along with where each timing mini-sector starts.
"""

import logging
import math

from ..common import CACHE, get_json, read_json, write_json

log = logging.getLogger("track")

URL = "https://api.multiviewer.app/api/v1/circuits/{key}/{year}"
SIZE = 1000
PAD = 70


def load(circuit_key, year: int) -> dict | None:
    cache = CACHE / "tracks" / f"{circuit_key}.json"
    raw = read_json(cache)
    if raw is None:
        for y in (year, year - 1, year - 2, year - 3):
            try:
                raw = get_json(URL.format(key=circuit_key, year=y))
                break
            except Exception as exc:
                log.info("No outline for circuit %s in %s: %s", circuit_key, y, exc)
        if raw is None:
            return None
        write_json(cache, raw)
    return build(raw)


def build(raw: dict) -> dict:
    to_screen = _transform(raw)
    xs, ys = raw["x"], raw["y"]
    points = [to_screen(x, y) for x, y in zip(xs, ys)]

    cum = [0.0]
    for i in range(1, len(xs)):
        cum.append(cum[-1] + math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]))
    total = cum[-1] + math.hypot(xs[0] - xs[-1], ys[0] - ys[-1])
    mini = [cum[min(i, len(cum) - 1)] / total for i in raw.get("miniSectorsIndexes", [])]

    corners = []
    for c in raw.get("corners", []):
        p = c["trackPosition"]
        a = math.radians(c.get("angle", 0))
        corners.append({"n": c.get("number"), "letter": c.get("letter") or "",
                        "x": to_screen(p["x"] + 600 * math.cos(a), p["y"] + 600 * math.sin(a))})
    return {
        "type": "track",
        "circuitKey": raw.get("circuitKey"),
        "name": raw.get("circuitName", ""),
        "points": [[round(x, 1), round(y, 1)] for x, y in points],
        "corners": [{"n": c["n"], "letter": c["letter"], "x": round(c["x"][0], 1), "y": round(c["x"][1], 1)}
                    for c in corners],
        "mini": [round(f, 5) for f in mini],
        "transform": to_screen,
    }


def _transform(raw: dict):
    """Map F1 coordinates onto a SIZE x SIZE canvas: rotate, flip y for the screen, fit."""
    rot = math.radians(raw.get("rotation", 0))
    c, s = math.cos(rot), math.sin(rot)

    def turn(x, y):
        return x * c - y * s, -(x * s + y * c)

    pts = [turn(x, y) for x, y in zip(raw["x"], raw["y"])]
    min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
    min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)
    scale = (SIZE - 2 * PAD) / max(max_x - min_x, max_y - min_y)
    off_x = (SIZE - (max_x - min_x) * scale) / 2
    off_y = (SIZE - (max_y - min_y) * scale) / 2

    def to_screen(x, y):
        tx, ty = turn(x, y)
        return (tx - min_x) * scale + off_x, (ty - min_y) * scale + off_y

    return to_screen
