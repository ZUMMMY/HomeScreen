"""Calendar suggestions: notable things in the next month, offered as Windows notifications.

fetch.py runs this on its own every 15 minutes. By hand:

    python suggest.py --google-login   sign in to Google once, so "Add to calendar" is one click
    python suggest.py --list           look a month ahead now and list every suggestion
    python suggest.py --test           show the next suggestion as a notification right away
    python suggest.py --again          ask again about the ones you didn't answer

fetch.py also starts `suggest.py --ask <id>` for each notification; it waits for your click.
"""

import argparse
import sys
from datetime import timedelta

from desktop import suggest
from desktop.common import load_config, now_utc, parse_time, setup_logging
from desktop.suggest import google


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ask", metavar="ID", help=argparse.SUPPRESS)
    parser.add_argument("--google-login", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--again", action="store_true")
    args = parser.parse_args()

    setup_logging("suggest")
    cfg = load_config()
    if args.ask:
        suggest.ask(cfg, args.ask)
    elif args.google_login:
        google.login()
    elif args.again:
        _again(cfg)
    elif args.test:
        test_cfg = {**cfg, "suggestions": {**cfg.get("suggestions", {}), "quiet_hours": [0, 0], "max_per_day": 99}}
        before = {s for s, i in suggest.load()["items"].items() if i["status"] == "asked"}
        store = suggest.run(test_cfg, now_utc())
        shown = [i for s, i in store["items"].items() if i["status"] == "asked" and s not in before]
        print(f"Showing: {shown[0]['title']}" if shown else "Nothing to show.")
    else:
        _list(cfg)


def _again(cfg: dict) -> None:
    """Unanswered suggestions go back in the queue (not ones a notification is still waiting on)."""
    hours = cfg.get("suggestions", {}).get("answer_hours", 12)
    cutoff = now_utc() - timedelta(hours=hours, minutes=15)
    store = suggest.load()
    n = 0
    for item in store["items"].values():
        stale = item["status"] == "asked" and parse_time(item["asked"]) < cutoff
        if item["status"] == "unanswered" or stale:
            item["status"], n = "new", n + 1
    suggest.save(store)
    print(f"{n} unanswered suggestions will be asked again.")


def _list(cfg: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    store = suggest.run(cfg, now_utc(), refresh=True, notify=False)
    print(f"Google Calendar: {'signed in' if google.signed_in() else 'not set up (Accept opens the event page instead)'}")
    print(f"TMDB movies:     {'on' if cfg.get('suggestions', {}).get('tmdb_token') else 'off (no tmdb_token)'}")
    print()
    for item in sorted(store["items"].values(), key=lambda i: (i["date"], i.get("start") or "")):
        print(f"{item['status']:<12} {suggest.when(cfg, item):<24} {item['title']}  ({item['why']})")


if __name__ == "__main__":
    main()
