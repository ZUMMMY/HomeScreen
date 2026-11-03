# Madison Desktop

A Windows wallpaper built from your Google Calendar, the Badgers, the Brewers, F1 and what's on
around Madison. The theme changes with whatever matters most that day. During F1 sessions a live
timing page runs at **http://localhost:8765** to keep open alongside the race.

The wallpaper is a **still picture**: Edge (part of Windows) draws the page off-screen when you sign
in and every 15 minutes, giving each monitor its own picture at its own size, and Windows shows it
like any other wallpaper. Nothing runs in between and the graphics card isn't used.

```
Task Scheduler ── sign-in, then every 15 min ──► fetch.py ──► data.js ──► Edge draws index.html ──► desktop pictures
                                       │
                                       └─ books 10 min before each F1 session ──► live.py ──► http://localhost:8765
                                                                                  (only while the session is on)
```

## Setup

1. Install **Python 3.12+** (python.org, tick "Add to PATH").
2. From this folder, run `powershell -ExecutionPolicy Bypass -File setup\install.ps1`.
3. Open `config.toml` and paste your calendar's secret iCal link. In Google Calendar on the web, open
   Settings, pick the calendar, go to *Integrate calendar* and copy *Secret address in iCal format*.
   Treat that link like a password.
4. Run `powershell -ExecutionPolicy Bypass -File setup\register-tasks.ps1` to refresh automatically.
   The desktop picture changes on the first run.

To go back to your old wallpaper: `.venv\Scripts\python -m desktop.render --restore`, then
`setup\register-tasks.ps1 -Remove` so it isn't redrawn again.

Optional extras:
- A free Ticketmaster key (developer.ticketmaster.com) in `config.toml` adds concerts and shows near Madison.
- Add your own one-off or weekly happenings to `madison_events.toml`.
- Drop your own photos into `wallpaper\themes\<theme>\` (`f1`, `brewers`, `badgers`, `madison`,
  `autumn`, `winter`, `spring`, `summer`). One is picked each day for that theme.
- Packers, Bucks or any other team: add an `[[espn]]` block to `config.toml`.

## What decides the day's theme

Each event has an importance score. The highest-scoring event today sets the theme and fills the
big headline:

| Event | Score |
|---|---|
| World Series / LCS / Division Series | 99 / 97 / 96 |
| F1 race | 100 |
| Badgers football | 95 |
| F1 qualifying / sprint | 90 / 88 |
| Brewers regular season | 80 |
| Badgers basketball / volleyball / hockey | 65–75 |
| F1 practice | 60 |
| Madison events | 45–58 |

With nothing on, the theme follows the season. Change scores under `[priority]` in `config.toml`.

## F1 race mode

Every 15-minute check looks at the F1 schedule. If a session goes live (10 minutes before it
starts) before the next check, race mode is booked in Task Scheduler for that exact minute; if one
is already on, say you just switched the PC on, it starts straight away.

While it runs, open **http://localhost:8765** in any browser, beside your stream: the track map with
a moving dot for every car, a timing tower with gaps, sector times (purple / green / yellow),
mini-sectors, tyres and pit stops, plus race control messages and a screen-edge glow under yellow,
safety car and red flags. Narrow the window and it stacks the map above the tower. The desktop
headline shows the address while the session is live. With `open_browser = "chrome"` (or `true` for
your default browser) a new window opens on the page by itself when race mode starts.

The port only exists while race mode runs: from 10 minutes before the session until 20 minutes
after it ends (the final result stays up that long). It listens on 127.0.0.1, so only this PC can
open it. Afterwards the result stays in a card on the wallpaper for the rest of the day.

**Data.** Live timing comes from F1's own feed through the FastF1 library. As of the 2026 season F1
locks parts of it behind an F1 TV login. Without one, F1 may send only some of the data, or none at
all; we'll find out on the next race weekend. To sign in (your password goes to F1 only, through
FastF1):

```
.venv\Scripts\python -m fastf1 auth f1tv --authenticate
```

then set `f1tv_login = true` in `config.toml`. Signed in, the cars are drawn at their real positions.
Otherwise their positions are estimated from mini-sector timing.

**Try it any time** with a replay from OpenF1's free archive, then open http://localhost:8765:

```
.venv\Scripts\python live.py --replay latest --speed 5 --from-lap 15
.venv\Scripts\python live.py --replay latest-any        # latest session of any kind
.venv\Scripts\python live.py --replay 11392             # any OpenF1 session_key
```

Live sessions are recorded to `cache\recordings\` and can be replayed with `--replay-file`.

Watching on a delayed stream? Set `delay_seconds` in `config.toml` so the timing doesn't spoil it.

## Calendar suggestions

Every few hours it looks a month ahead for things worth putting on your calendar that aren't
there yet:
- **Movies:** the month's most popular wide releases, plus anything matching your `follow` list
  (titles, franchises, studios, directors or actors).
- **Big games and races:** F1 races, Badgers football, Brewers playoff games. Games with a TBD
  kickoff wait until the time is announced.
- **Big shows in Madison:** these need a Ticketmaster key.

Each 15-minute check shows at most one as a Windows notification with **Add to calendar** and
**No thanks**, at most `max_per_day` a day, never during quiet hours or while race mode is on.
It never asks about the same thing twice. Settings are under `[suggestions]` in `config.toml`.

**Setup**
1. Run `powershell -ExecutionPolicy Bypass -File setup\register-notifications.ps1`. It adds a
   *Madison Desktop* Start menu shortcut, which Windows needs to pass a notification's button clicks
   back to the app (opening it refreshes the wallpaper). It's for your user only, and `-Remove`
   undoes it. Each notification has a small background process waiting for your answer, for up to
   `answer_hours`. After that, or if you swipe it away, it drops out; `suggest.py --again` brings it back.
2. Movies: get a free TMDB account, then go to themoviedb.org > Settings > API. Put the
   *API Read Access Token* in `tmdb_token`, and add what you follow to `follow`.
3. One-click adding needs Google's Calendar API, because the iCal link is read-only. Without it,
   **Add to calendar** opens Google Calendar's new-event page already filled in, and you click Save.
   1. At console.cloud.google.com, create a project and enable the **Google Calendar API**.
   2. Under *Google Auth Platform*, set it up as **External**, then under *Audience* click
      **Publish app**. In Testing mode Google signs you out every 7 days.
   3. Under *Clients*, create a **Desktop app** client, download its JSON and save it in this folder
      as `google-oauth-client.json`.
   4. Run `.venv\Scripts\python suggest.py --google-login` and sign in. Google will say it hasn't
      verified the app; that's expected for your own project (*Advanced > Go to ...*).

   The client file and your sign-in (`cache\google-token.json`) stay out of git. The app can only
   add and edit calendar events.

To try it now:

```
.venv\Scripts\python suggest.py --list    # what it would suggest, and what's already on your calendar
.venv\Scripts\python suggest.py --test    # show the next one as a notification now
.venv\Scripts\python suggest.py --again   # re-ask the ones you didn't answer
```

Movie data comes from TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.

## Keeping it light

Measured on this PC:
- **Wallpaper:** each redraw is Edge running for about 1.5 seconds per monitor with the GPU
  switched off, about 4 CPU-seconds in all. Every 15 minutes that averages under 0.05% of the PC,
  and nothing runs in between.
- **Race mode server (live.py):** under 0.05% CPU and about 130 MB of memory, only during sessions.
- **The timing page:** a normal browser tab drawing at up to 30 frames a second, only while you
  have it open.

`setup\perf-check.ps1` samples CPU, GPU and memory of the Python scripts if you want to check:

```
powershell -ExecutionPolicy Bypass -File setup\perf-check.ps1 -Minutes 3
```

## Files

- `fetch.py` refreshes `wallpaper/data.js`. Use `--summary` to see what it found.
- `live.py` is race mode: it serves the timing page and streams the data to it.
- `suggest.py` handles calendar suggestions: the notification buttons, the Google sign-in and `--list` / `--test`.
- `desktop/suggest/` finds the suggestions (TMDB movies, big games and shows), shows the notifications and adds the events to Google Calendar.
- `desktop/render.py` draws the wallpaper pictures with Edge and sets them per monitor (`--restore` puts your old wallpaper back).
- `desktop/sources/` holds one module per feed: Google Calendar, Jolpica (F1 schedule), MLB Stats API,
  ESPN, Ticketmaster plus your own list, and Open-Meteo weather.
- `desktop/race/` has the timing state, the OpenF1 replay converter, the live feed and the track outlines (MultiViewer).
- `cache/` holds logs, the last good copy of every feed, track outlines and recordings.

ESPN, MultiViewer and F1's live timing are unofficial and can change without notice. If a feed
fails, the wallpaper keeps showing its last good data and says so at the bottom of the right column.
