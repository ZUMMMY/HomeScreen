"""Upcoming movies from TMDB: the month's biggest wide releases plus anything you follow.

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

import logging
from datetime import date, datetime, timedelta

from ..common import CACHE, http, local_tz, parse_time, read_json, to_iso, write_json

log = logging.getLogger("movies")

API = "https://api.themoviedb.org/3"
IMAGES = "https://image.tmdb.org/t/p/w780"
DETAILS_MAX_AGE = timedelta(days=3)
PAGES = 3  # 20 movies a page, most popular first


def upcoming(cfg: dict, now: datetime, days: int) -> list[dict]:
    """Suggestion candidates for movies opening in US theaters in the next `days` days."""
    opts = cfg.get("suggestions", {})
    token = (opts.get("tmdb_token") or "").strip()
    if not token:
        return []
    today = now.astimezone(local_tz(cfg)).date()
    last = today + timedelta(days=days)
    found = []
    for page in range(1, PAGES + 1):
        data = _get(token, "/discover/movie", {
            "region": "US", "with_release_type": "2|3", "include_adult": "false",
            "release_date.gte": today.isoformat(), "release_date.lte": last.isoformat(),
            "sort_by": "popularity.desc", "page": page,
        })
        found += data.get("results", [])
        if page >= data.get("total_pages", 1):
            break

    cache = read_json(CACHE / "tmdb-details.json", {}) or {}
    follow = [t.lower() for t in opts.get("follow", []) if t.strip()]
    big_left = opts.get("big_releases", 4)
    out = []
    for m in found:
        info = _details(token, m["id"], cache, now)
        opens, wide = _us_release(info, m)
        if not opens or not today <= opens <= last:
            continue
        why = _followed(m.get("title", ""), info, follow)
        if not why and wide and big_left > 0:
            big_left -= 1
            why = "Big release"
        if why:
            out.append(_candidate(m, info, opens, why))
    write_json(CACHE / "tmdb-details.json", cache)
    return out


def _get(token: str, path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    headers = {}
    if token.startswith("eyJ"):  # the "API Read Access Token"
        headers["Authorization"] = f"Bearer {token}"
    else:  # the older v3 "API Key"
        params["api_key"] = token
    r = http().get(API + path, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def _details(token: str, movie_id: int, cache: dict, now: datetime) -> dict:
    hit = cache.get(str(movie_id))
    if hit and now - parse_time(hit["fetched"]) < DETAILS_MAX_AGE:
        return hit["data"]
    data = _get(token, f"/movie/{movie_id}", {"append_to_response": "release_dates,credits"})
    keep = {
        "belongs_to_collection": data.get("belongs_to_collection"),
        "production_companies": [{"name": c["name"]} for c in data.get("production_companies", [])],
        "genres": [g["name"] for g in data.get("genres", [])],
        "runtime": data.get("runtime"),
        "release_dates": data.get("release_dates", {}),
        "directors": [c["name"] for c in data.get("credits", {}).get("crew", []) if c.get("job") == "Director"],
        "cast": [c["name"] for c in data.get("credits", {}).get("cast", [])[:6]],
    }
    cache[str(movie_id)] = {"fetched": to_iso(now), "data": keep}
    return keep


def _us_release(info: dict, movie: dict) -> tuple[date | None, bool]:
    """US theatrical date, and whether it's a wide release rather than limited."""
    for country in info.get("release_dates", {}).get("results", []):
        if country.get("iso_3166_1") != "US":
            continue
        for kind, wide in ((3, True), (2, False)):  # TMDB: 3 theatrical, 2 limited
            dates = sorted(r["release_date"][:10] for r in country.get("release_dates", []) if r.get("type") == kind)
            if dates:
                return date.fromisoformat(dates[0]), wide
    if movie.get("release_date"):
        return date.fromisoformat(movie["release_date"]), True
    return None, False


def _followed(title: str, info: dict, follow: list[str]) -> str | None:
    """Which of your `follow` terms this movie matches, if any."""
    names = [
        title,
        *(c["name"] for c in info.get("production_companies", [])),
        (info.get("belongs_to_collection") or {}).get("name", ""),
        *info.get("directors", []),
        *info.get("cast", []),
    ]
    for term in follow:
        for name in names:
            if term in name.lower():
                return f"You follow {term.title()}"
    return None


def _candidate(m: dict, info: dict, opens: date, why: str) -> dict:
    title = m.get("title") or m.get("original_title") or "Untitled"
    bits = [", ".join(info.get("genres", [])[:2])]
    if info.get("runtime"):
        bits.append(f"{info['runtime'] // 60}h {info['runtime'] % 60}m")
    if info.get("directors"):
        bits.append("Dir. " + info["directors"][0])
    link = f"https://www.themoviedb.org/movie/{m['id']}"
    return {
        "kind": "movie",
        "key": f"tmdb-{m['id']}",
        "title": f"{title} in theaters",
        "name": title,
        "date": opens.isoformat(),
        "all_day": True,
        "detail": " · ".join(b for b in bits if b),
        "why": why,
        "location": None,
        "description": f"{m.get('overview', '').strip()}\n\n{link}".strip(),
        "link": link,
        "image_url": IMAGES + m["backdrop_path"] if m.get("backdrop_path") else None,
        "busy": False,
    }
