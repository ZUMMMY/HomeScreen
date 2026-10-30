"""Picture mode: screenshot the wallpaper page with Edge and make it the desktop background.

No wallpaper app runs in this mode. Edge (part of Windows) opens the page off-screen for a
couple of seconds, saves a PNG at your screen size, and Windows shows it like any other
wallpaper. Nothing runs between refreshes.

    python -m desktop.render            render and set the wallpaper now
    python -m desktop.render --restore  put back the wallpaper you had before
"""

import argparse
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from .common import CACHE, WALLPAPER, read_json, write_json

log = logging.getLogger("render")

EDGE_PATHS = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / r"Microsoft\Edge\Application\msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / r"Microsoft\Edge\Application\msedge.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\Edge\Application\msedge.exe",
]
SPI_SETDESKWALLPAPER = 20
SPIF_UPDATE_AND_BROADCAST = 0x01 | 0x02
ORIGINAL = CACHE / "original-wallpaper.json"


def find_edge() -> Path | None:
    return next((p for p in EDGE_PATHS if p.exists()), None)


def _dpi_aware() -> None:
    """Work in real pixels, ignoring display scaling."""
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor aware v2
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def screen_size() -> tuple[int, int]:
    """Primary screen size."""
    _dpi_aware()
    return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)


def render(out: Path, width: int, height: int) -> bool:
    edge = find_edge()
    if not edge:
        log.error("Microsoft Edge not found; picture mode needs it")
        return False
    url = (WALLPAPER / "index.html").as_uri() + "?static=1"
    out.unlink(missing_ok=True)
    cmd = [
        str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        # its own profile, so it never touches (or hands off to) your normal Edge
        f"--user-data-dir={CACHE / 'edge-profile'}",
        f"--window-size={width},{height}", "--force-device-scale-factor=1",
        "--allow-file-access-from-files", "--virtual-time-budget=4000",
        f"--screenshot={out}", url,
    ]
    started = time.monotonic()
    try:
        subprocess.run(cmd, capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        log.error("Edge took too long to render the wallpaper")
        return False
    ok = _wait_for(out, timeout=30)
    log.info("Rendered %dx%d in %.1fs%s", width, height, time.monotonic() - started, "" if ok else " (FAILED)")
    return ok


def _wait_for(path: Path, timeout: float) -> bool:
    """msedge.exe hands off to a child process and returns at once; the PNG lands a moment later."""
    end = time.monotonic() + timeout
    last = -1
    while time.monotonic() < end:
        size = path.stat().st_size if path.exists() else -1
        if size > 10_000 and size == last:
            return True
        last = size
        time.sleep(0.25)
    return False


class DesktopWallpaper:
    """Windows' IDesktopWallpaper COM interface: a separate picture for each monitor."""

    CLSID = "{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}"
    IID = "{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}"
    FILL = 4

    def __init__(self):
        ole32 = ctypes.oledll.ole32
        try:
            ole32.CoInitialize(None)
        except OSError:
            pass  # already initialised on this thread
        clsid, iid = _Guid(), _Guid()
        ole32.CLSIDFromString(self.CLSID, ctypes.byref(clsid))
        ole32.IIDFromString(self.IID, ctypes.byref(iid))
        self.ptr = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x17, ctypes.byref(iid), ctypes.byref(self.ptr))
        self.vtbl = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

    def _call(self, index, argtypes, *args):
        fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(self.vtbl[index])
        return fn(self.ptr, *args)

    def monitors(self) -> list[tuple[str, int, int]]:
        """(monitor id, width, height) for each connected monitor."""
        count = wintypes.UINT()
        self._call(6, [ctypes.POINTER(wintypes.UINT)], ctypes.byref(count))
        out = []
        for i in range(count.value):
            raw = ctypes.c_void_p()
            self._call(5, [wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)], i, ctypes.byref(raw))
            mid = ctypes.wstring_at(raw.value)
            ctypes.windll.ole32.CoTaskMemFree(raw)
            rect = wintypes.RECT()
            try:
                self._call(7, [wintypes.LPCWSTR, ctypes.POINTER(wintypes.RECT)], mid, ctypes.byref(rect))
            except OSError:
                continue  # listed but not connected
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w > 0 and h > 0:
                out.append((mid, w, h))
        return out

    def set(self, monitor_id: str, path: Path) -> None:
        self._call(3, [wintypes.LPCWSTR, wintypes.LPCWSTR], monitor_id, str(path))

    def fill(self) -> None:
        self._call(10, [ctypes.c_int], self.FILL)

    def close(self) -> None:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(self.vtbl[2])(self.ptr)


class _Guid(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def update() -> bool:
    """Render a picture for each monitor at its own size and show it there.

    Files alternate between two names so Windows always notices the change.
    """
    _dpi_aware()
    CACHE.mkdir(exist_ok=True)
    current = (read_json(CACHE / "render-state.json", {}) or {}).get("current", "b")
    nxt = "a" if current == "b" else "b"
    try:
        desktop = DesktopWallpaper()
        monitors = desktop.monitors()
    except OSError as exc:
        log.warning("Per-monitor wallpapers unavailable (%s); using one picture", exc)
        desktop, monitors = None, []

    if not monitors:  # fall back to one picture for every screen
        w, h = screen_size()
        out = CACHE / f"wallpaper-{w}x{h}-{nxt}.png"
        if not render(out, w, h):
            return False
        _remember_original()
        if not ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(out),
                                                           SPIF_UPDATE_AND_BROADCAST):
            log.error("Windows refused the new wallpaper: %s", ctypes.WinError())
    else:
        _remember_original()
        pictures = {}
        for _, w, h in monitors:  # one render per distinct screen size
            if (w, h) not in pictures:
                out = CACHE / f"wallpaper-{w}x{h}-{nxt}.png"
                if render(out, w, h):
                    pictures[(w, h)] = out
        if not pictures:
            return False
        desktop.fill()
        for mid, w, h in monitors:
            if (w, h) in pictures:
                desktop.set(mid, pictures[(w, h)])
        desktop.close()
    write_json(CACHE / "render-state.json", {"current": nxt})
    _tidy(nxt)
    return True


def _tidy(keep: str) -> None:
    """Remove pictures from the previous round (older sizes and the other a/b name)."""
    for f in CACHE.glob("wallpaper-*.png"):
        if not f.stem.endswith(f"-{keep}"):
            f.unlink(missing_ok=True)


def _remember_original() -> None:
    """Save the wallpaper you had before, once, so --restore can bring it back."""
    if ORIGINAL.exists():
        return
    candidates = []
    try:  # the real source file; "WallPaper" often points at Windows' own transcoded copy
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers") as key:
            candidates.append(winreg.QueryValueEx(key, "BackgroundHistoryPath0")[0])
    except OSError:
        pass
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
        candidates.append(winreg.QueryValueEx(key, "WallPaper")[0])
    for path in candidates:
        if not path or Path(path).parent == CACHE or not Path(path).exists():
            continue
        if Path(path).name == "TranscodedWallpaper":  # gets overwritten; keep a copy
            copy = CACHE / "original-wallpaper.jpg"
            shutil.copyfile(path, copy)
            path = str(copy)
        write_json(ORIGINAL, {"path": path})
        return


def restore() -> None:
    saved = read_json(ORIGINAL)
    if not saved:
        print("No saved wallpaper to restore.")
        return
    ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, saved["path"], SPIF_UPDATE_AND_BROADCAST)
    print(f"Restored {saved['path']}")


if __name__ == "__main__":
    from .common import setup_logging

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--restore", action="store_true", help="put back your previous wallpaper")
    args = parser.parse_args()
    setup_logging("render")
    if args.restore:
        restore()
    else:
        sys.exit(0 if update() else 1)
