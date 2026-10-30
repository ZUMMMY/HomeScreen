"""F1's own live timing feed, through FastF1's SignalR client.

FastF1 keeps up with F1's connection details. Without an F1 TV sign-in F1 may
send only some of the data, or none; with one (python -m fastf1 auth f1tv
--authenticate) it sends everything, including exact car positions.

Everything received is also recorded (FastF1's own format) under cache/recordings,
so a session can be replayed later with: python live.py --replay-file <file>
"""

import ast
import json
import logging
import queue
import threading
import time

from ..common import parse_time

log = logging.getLogger("f1live")


class LiveFeed:
    def __init__(self, record_path, f1tv_login: bool):
        self.record_path = record_path
        self.f1tv_login = f1tv_login
        self.q: queue.Queue = queue.Queue()
        self.alive = False

    def start(self) -> None:
        from fastf1.livetiming.client import SignalRClient  # heavy import, only for live sessions

        _allow_no_token()
        feed = self

        class Client(SignalRClient):
            def _on_message(self, msg):
                super()._on_message(msg)  # keeps FastF1's raw recording
                feed._push(msg)

        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = Client(str(self.record_path), filemode="a", timeout=0, no_auth=not self.f1tv_login)
        self.alive = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.client.start()
        except Exception:
            log.exception("Live timing client stopped")
        self.alive = False

    def stop(self) -> None:
        try:
            self.client._exit()
        except Exception:
            pass
        self.alive = False

    def _push(self, msg) -> None:
        now = time.time()
        if isinstance(msg, list) and len(msg) >= 2:
            stamp = msg[2] if len(msg) > 2 else None
            self.q.put((now, msg[0], msg[1], _stamp(stamp, now)))
            return
        result = getattr(msg, "result", None)  # the initial state after subscribing
        if isinstance(result, dict):
            for topic, data in result.items():
                self.q.put((now, topic, data, now))


def _allow_no_token() -> None:
    """Without an F1 TV login FastF1 passes access_token_factory=None, which signalrcore 1.0
    rejects ("access_token_factory is not function"). Leave the option out instead."""
    from signalrcore.hub_connection_builder import HubConnectionBuilder

    original = HubConnectionBuilder.with_url
    if getattr(original, "_allows_no_token", False):
        return

    def with_url(self, hub_url, options=None):
        if options and "access_token_factory" in options and options["access_token_factory"] is None:
            options = {k: v for k, v in options.items() if k != "access_token_factory"}
        return original(self, hub_url, options)

    with_url._allows_no_token = True
    HubConnectionBuilder.with_url = with_url


def read_recording(path) -> list[tuple[float, str, object]]:
    """Messages from a FastF1 recording, oldest first."""
    with open(path, encoding="utf-8", errors="replace") as f:
        parsed = [p for p in map(parse_line, f) if p]
    # initial-state lines carry no timestamp; put them just before everything else
    first = min((t for _, _, t in parsed if t is not None), default=1.0) - 1
    msgs = [(first if t is None else t, topic, data) for topic, data, t in parsed]
    msgs.sort(key=lambda m: m[0])
    return msgs


def parse_line(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        msg = ast.literal_eval(line)
    except (ValueError, SyntaxError):
        try:
            msg = json.loads(line.replace("'", '"').replace("True", "true").replace("False", "false"))
        except ValueError:
            return None
    if not isinstance(msg, list) or len(msg) < 2:
        return None
    topic, data = msg[0], msg[1]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            pass  # a bare base64 .z payload
    stamp = msg[2] if len(msg) > 2 else ""
    return topic, data, _stamp(stamp, None)


def _stamp(value, default):
    if not value:
        return default
    try:
        return parse_time(value).timestamp()
    except ValueError:
        return default
