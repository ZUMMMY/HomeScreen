"""Adding events to Google Calendar through its API (your iCal link is read-only).

One-time setup is in the README: a Google Cloud OAuth client saved as google-oauth-client.json,
then `python suggest.py --google-login`, where you sign in with Google yourself in the browser.
The sign-in is kept in cache/google-token.json. Neither file is ever committed.
"""

import base64
import hashlib
import json
import logging
import secrets
import time
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

from ..common import CACHE, ROOT, http, local_tz, parse_time, read_json, write_json

log = logging.getLogger("google")

CLIENT_FILE = ROOT / "google-oauth-client.json"
TOKEN_FILE = CACHE / "google-token.json"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
EVENTS = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"


class NotSignedIn(Exception):
    pass


def signed_in() -> bool:
    return CLIENT_FILE.exists() and bool((read_json(TOKEN_FILE, {}) or {}).get("refresh_token"))


def login(timeout: int = 300) -> None:
    """Open Google's sign-in page and keep the token it hands back to this PC."""
    client = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if query.get("state", [""])[0] == state:
                got.update({k: v[0] for k, v in query.items()})
            ok = "code" in got
            body = ("Signed in. You can close this tab." if ok else
                    "Sign-in didn't finish. Run suggest.py --google-login again.").encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 5
    redirect = f"http://127.0.0.1:{server.server_port}/"
    url = client["auth_uri"] + "?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("Opening Google sign-in in your browser. If it doesn't open, go to:\n" + url)
    webbrowser.open(url)
    deadline = time.time() + timeout
    while not got and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if "code" not in got:
        raise RuntimeError(got.get("error") or "no answer from Google sign-in in time")

    r = http().post(client["token_uri"], data={
        "code": got["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
    }, timeout=20)
    r.raise_for_status()
    _save(r.json())
    print("Signed in. Accepted suggestions now go straight onto your calendar.")


def add_event(cfg: dict, item: dict) -> dict:
    """Create the event for a suggestion; returns Google's event (htmlLink, iCalUID, ...)."""
    cal = cfg.get("suggestions", {}).get("calendar_id", "primary")
    r = http().post(EVENTS.format(cal=quote(cal, safe="")), json=event_body(cfg, item),
                    headers={"Authorization": f"Bearer {_access_token()}"}, timeout=20)
    if r.status_code == 401:
        raise NotSignedIn("Google turned down the sign-in")
    r.raise_for_status()
    return r.json()


def event_body(cfg: dict, item: dict) -> dict:
    tz = cfg.get("timezone", "America/Chicago")
    body = {
        "summary": item["title"],
        "description": item.get("description") or "",
        "transparency": "opaque" if item.get("busy") else "transparent",
        "extendedProperties": {"private": {"madisonDesktop": item["id"]}},
    }
    if item.get("location"):
        body["location"] = item["location"]
    if item["all_day"]:
        day = date.fromisoformat(item["date"])
        body["start"] = {"date": day.isoformat()}
        body["end"] = {"date": (day + timedelta(days=1)).isoformat()}
    else:
        body["start"] = {"dateTime": item["start"], "timeZone": tz}
        body["end"] = {"dateTime": item["end"], "timeZone": tz}
    return body


def template_url(cfg: dict, item: dict) -> str:
    """Google Calendar's own "new event" page, filled in (for when the API isn't set up)."""
    if item["all_day"]:
        day = date.fromisoformat(item["date"])
        dates = f"{day:%Y%m%d}/{day + timedelta(days=1):%Y%m%d}"
    else:
        fmt = lambda iso: parse_time(iso).astimezone(local_tz(cfg)).strftime("%Y%m%dT%H%M%S")
        dates = f"{fmt(item['start'])}/{fmt(item['end'])}"
    return "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": item["title"], "dates": dates,
        "ctz": cfg.get("timezone", "America/Chicago"),
        "details": item.get("description") or "", "location": item.get("location") or "",
    })


def _client() -> dict:
    if not CLIENT_FILE.exists():
        raise NotSignedIn(f"no {CLIENT_FILE.name} (see the README's Google Calendar setup)")
    data = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    return data.get("installed") or data.get("web") or data


def _access_token() -> str:
    token = read_json(TOKEN_FILE, {}) or {}
    if not token.get("refresh_token"):
        raise NotSignedIn("not signed in to Google yet (python suggest.py --google-login)")
    if token.get("access_token") and token.get("expires_at", 0) > time.time() + 60:
        return token["access_token"]
    client = _client()
    r = http().post(client["token_uri"], data={
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
    }, timeout=20)
    if r.status_code in (400, 401):  # revoked, or expired after 7 days in "Testing" mode
        raise NotSignedIn("Google sign-in expired (python suggest.py --google-login)")
    r.raise_for_status()
    return _save(r.json(), token)["access_token"]


def _save(reply: dict, old: dict | None = None) -> dict:
    token = {
        "refresh_token": reply.get("refresh_token") or (old or {}).get("refresh_token"),
        "access_token": reply["access_token"],
        "expires_at": time.time() + reply.get("expires_in", 3600),
    }
    write_json(TOKEN_FILE, token)
    return token
