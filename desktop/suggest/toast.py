"""Windows notifications with buttons.

Windows only passes a button click straight to a desktop app while that app is running, so a
suggestion is shown by a small waiting process (suggest.py --ask) that gets the click itself.
The notifications are sent as "Madison Desktop", the Start menu shortcut that
setup/register-notifications.ps1 adds for your user.
"""

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from winrt.windows.data.xml.dom import XmlDocument
from winrt.windows.ui.notifications import (ToastActivatedEventArgs, ToastDismissalReason,
                                            ToastNotification, ToastNotificationManager)

log = logging.getLogger("toast")

APP_ID = "MadisonDesktop"
GROUP = "suggest"
SHORTCUT = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Madison Desktop.lnk"


def registered() -> bool:
    return SHORTCUT.exists()


def suggestion_xml(item: dict, when: str, image: str | None) -> str:
    """Add / no thanks notification for one suggestion. Clicking the text itself only opens a page."""
    sid = item["id"]
    lines = [item["title"], " · ".join(x for x in (when, item.get("detail") or item.get("location")) if x)]
    hero = f'<image placement="hero" src={quoteattr(image)}/>' if image else ""
    return (
        f'<toast launch="open/{sid}">'
        '<visual><binding template="ToastGeneric">'
        + "".join(f"<text>{escape(t)}</text>" for t in lines if t)
        + f'<text placement="attribution">{escape(item["why"])} · Add to your calendar?</text>'
        + hero +
        '</binding></visual>'
        '<actions>'
        f'<action content="Add to calendar" arguments="accept/{sid}"/>'
        f'<action content="No thanks" arguments="decline/{sid}"/>'
        '</actions>'
        '</toast>'
    )


def message_xml(title: str, body: str) -> str:
    return (f'<toast><visual><binding template="ToastGeneric">'
            f"<text>{escape(title)}</text><text>{escape(body)}</text>"
            "</binding></visual></toast>")


def show(xml: str, tag: str) -> ToastNotification | None:
    doc = XmlDocument()
    doc.load_xml(xml)
    toast = ToastNotification(doc)
    toast.tag = tag[:64]
    toast.group = GROUP
    toast.expires_on_reboot = True  # its buttons only work while the waiting process runs
    try:
        ToastNotificationManager.create_toast_notifier_with_id(APP_ID).show(toast)
    except OSError as exc:
        log.warning("Notification failed: %s", exc)
        return None
    return toast


def ask(xml: str, tag: str, wait: timedelta, quiet: bool = False) -> str | None:
    """Show a notification with buttons and wait for a click; returns that click's arguments.
    quiet puts it straight into the notification list without popping up."""
    clicked: dict = {}
    done = threading.Event()
    doc = XmlDocument()
    doc.load_xml(xml)
    toast = ToastNotification(doc)
    toast.tag = tag[:64]
    toast.group = GROUP
    toast.suppress_popup = quiet
    toast.expires_on_reboot = True  # its buttons only work while this process runs
    toast.expiration_time = datetime.now(timezone.utc) + wait

    def activated(_sender, args):
        clicked["arguments"] = ToastActivatedEventArgs._from(args).arguments
        done.set()

    def dismissed(_sender, args):
        if args.reason == ToastDismissalReason.USER_CANCELED:  # swiped away, not just tucked into the list
            done.set()

    tokens = (toast.add_activated(activated), toast.add_dismissed(dismissed))
    try:
        ToastNotificationManager.create_toast_notifier_with_id(APP_ID).show(toast)
    except OSError as exc:
        log.warning("Notification failed: %s", exc)
        return None
    done.wait(wait.total_seconds())
    toast.remove_activated(tokens[0])
    toast.remove_dismissed(tokens[1])
    return clicked.get("arguments")


def remove(tag: str) -> None:
    try:
        ToastNotificationManager.history.remove_grouped_tag_with_id(tag[:64], GROUP, APP_ID)
    except OSError:
        pass
