# mpdpop V2

A cross-platform MPD playlist popup controller with album cover art, artist
biography, desktop overlay widget, layered caching, and a built-in command bar.

Runs on **Windows**, **macOS**, and **Linux**. Multi-monitor aware. No mandatory
third-party dependencies — everything degrades gracefully when optional packages
are absent.

---

[![Demo](https://github.com/cumulus13/mpdpop-v2/blob/b4348ca04cdf2c30cb142c2a7b27ef7203880181/mpdpop.webp)](https://github.com/cumulus13/mpdpop-v2/blob/b4348ca04cdf2c30cb142c2a7b27ef7203880181/mpdpop.webp)

[![Overlay 1](https://github.com/cumulus13/mpdpop-v2/blob/99da1d993de2bba38db6524b6630b05053da4816/overlay1.png)](https://github.com/cumulus13/mpdpop-v2/blob/99da1d993de2bba38db6524b6630b05053da4816/overlay1.png)      [![Overlay 2](https://github.com/cumulus13/mpdpop-v2/blob/99da1d993de2bba38db6524b6630b05053da4816/overlay2.png)](https://github.com/cumulus13/mpdpop-v2/blob/99da1d993de2bba38db6524b6630b05053da4816/overlay2.png)      [![Overlay 3](https://github.com/cumulus13/mpdpop-v2/blob/99da1d993de2bba38db6524b6630b05053da4816/overlay3.png)](https://github.com/cumulus13/mpdpop-v2/blob/99da1d993de2bba38db6524b6630b05053da4816/overlay3.png)

---

## Files

| File | Lines | Purpose |
|---|---|---|
| `mpdpop.py` | 1366 | Entry point — Tkinter playlist dialog, all platform backends |
| `mpdpop_artinfo.py` | 1210 | Cover art fetcher, bio fetcher, info panel widgets |
| `mpdpop_cache.py` | 655 | Layered cache: Redis → pickle → SQLAlchemy/SQLite |
| `mpdpop_env.py` | 238 | Config loader (`mpdpop.env` + `os.environ`) |
| `mpdpop_overlay.py` | 1026 | CD Art Display-style always-on-top desktop overlay |
| `mpdpop.env` | — | Your settings and API keys (edit this) |

All files must live in the **same directory**.

---

## Quick start

```bash
# minimum — no extra packages needed
python3 mpdpop.py

# desktop overlay (runs standalone)
python3 mpdpop_overlay.py

# recommended — enables JPEG cover art + rounded corners + reflection
pip install pillow

# full feature set
pip install pillow redis sqlalchemy pystray
```

---

## Requirements

### Mandatory
- Python 3.10+
- MPD running and reachable (default `127.0.0.1:6600`)
- Tkinter (bundled with standard Python on all platforms)

### Optional (install for extra features)

| Package | Feature unlocked |
|---|---|
| `pillow` | JPEG/WebP cover art, rounded corners, mirror reflection, aspect-ratio resize |
| `redis` | Redis cache layer (fastest, in-memory) |
| `sqlalchemy` | Richer DB support for bio cache (PostgreSQL, MySQL, etc.) |
| `pystray` | System tray icon in overlay (Windows) |
| `AppKit` (macOS) | Accurate multi-monitor detection |
| `xrandr` (Linux) | Accurate multi-monitor detection (usually pre-installed) |

Without `pillow`, only native PNG cover art renders. JPEG files show a
placeholder with an install hint.
---

## Configuration

Edit `mpdpop.env` in the same directory as the scripts.
`os.environ` always overrides `.env` values.

The file is searched in order:
1. Same directory as `mpdpop_env.py`
2. `~/.config/mpdpop.env`
3. `~/.mpdpop.env`

**Important**: leave unused values blank — do not add `# comments` after a value
on the same line, as they are treated as part of the value.

```ini
# ── MPD Connection ────────────────────────────────────────────────────────────
MPD_HOST     = 127.0.0.1
MPD_PORT     = 6600
MPD_PASSWORD =
MPD_TIMEOUT  = 5

# ── API Keys ─────────────────────────────────────────────────────────────────
# Last.fm — free key at https://www.last.fm/api/account/create
LASTFM_API_KEY   =

# Discogs — personal token at https://www.discogs.com/settings/developers
DISCOGS_TOKEN    =

# MusicBrainz — no key needed; set your app name for the User-Agent header
MUSICBRAINZ_APP  = mpdpop/1.0

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_REDIS_URL  =              # redis://[:pass@]host:6379/0
CACHE_PICKLE     = true
CACHE_PICKLE_DIR =
CACHE_DB_URL     =              # blank = ~/.local/share/mpdpop/cache.db
CACHE_TTL_DAYS   = 30
CACHE_REVALIDATE_DAYS = 7

# ── Cover Art ─────────────────────────────────────────────────────────────────
COVER_SIZE       = 120          # thumbnail px in main dialog
COVER_BIG_SIZE   = 480          # big cover popup px (S key / click)
COVER_CACHE_DIR  =              # blank = system temp dir

# ── Artist Bio ────────────────────────────────────────────────────────────────
BIO_MAX_CHARS    = 600
BIO_LANG         = en           # Wikipedia language code

# ── UI ────────────────────────────────────────────────────────────────────────
DIALOG_WIDTH     = 780
DIALOG_HEIGHT    = 620
INFO_PANEL_BIO_H = 80           # bio text area height px
PAGE_STEP        = 10           # rows per PgUp/PgDn
CMD_HISTORY      = 50           # command bar history size
WINDOW_ICON      =

# ── Overlay ───────────────────────────────────────────────────────────────────
OVERLAY_SIZE          = 220
OVERLAY_X             = -1
OVERLAY_Y             = -1
OVERLAY_OPACITY       = 0.92
OVERLAY_REFLECTION    = true
OVERLAY_POLL_MS       = 1000
OVERLAY_FONT          =
OVERLAY_FONT_SIZE     = 10
OVERLAY_CONTROLS_SIZE = 32
OVERLAY_ALWAYS_ON_TOP = true
OVERLAY_AUTO_START    = false
OVERLAY_CORNER_RADIUS = 12
```

All keys can also be set as environment variables:

```bash
export MPD_HOST=192.168.1.10
export LASTFM_API_KEY=abc123
python3 mpdpop.py
```

---

## Playlist dialog (`mpdpop.py`)

### Keyboard shortcuts

| Key | Action |
|---|---|
| `↑` / `↓` | Navigate track list (works from any widget) |
| `PgUp` / `PgDn` | Scroll list by `PAGE_STEP` rows |
| `Enter` | Play selected track |
| `F` | Focus filter box |
| `T` | Focus track number field |
| `S` | Open big cover art popup |
| `C` | Toggle command bar |
| `R` | Toggle MPD **repeat** on/off |
| `N` | Toggle MPD si**n**gle on/off |
| `Z` | Toggle MPD random (shuffle) on/off |
| `O` | Toggle MPD cons**o**me on/off |
| `Esc` | Close command bar if open, otherwise close dialog |
| Double-click | Play track immediately |

### Status badges

A row of clickable badges in the top bar shows the current state of repeat,
single, random, and consume. Each badge turns blue when the option is on.
Clicking a badge toggles it. The same options are toggled by `R`, `N`, `Z`, `O`.

### Command bar (`C`)

The command bar accepts MPC shorthand or any shell command. Output appears in a
scrollable 4-line terminal below the input. The playlist auto-refreshes after
every command using a diff-based update (safe for any playlist size).

**MPC shorthand** — the `mpc` prefix is optional for known sub-commands:

```
next           →  mpc next
play 3         →  mpc play 3
volume +5      →  mpc volume +5
toggle         →  mpc toggle
status         →  mpc status
del 3-7        →  mpc del 3-7
mpc next       →  mpc next        (prefix already there, unchanged)
ls /           →  ls /            (not an mpc command, runs as-is)
```

When expansion happens the prompt shows both: `$ next  →  mpc next`
- `↑` / `↓` inside the command field cycles history (last `CMD_HISTORY` entries)
- `Esc` inside the command field closes the bar without closing the dialog
- Commands run in a background thread with a 10-second timeout — the UI never freezes

**Navigation inside the command bar:**

| Key | Action |
|---|---|
| `Enter` | Run command |
| `↑` / `↓` | Cycle command history |
| `Tab` | Move focus to filter box |
| `Alt+F` | Move focus to filter box |
| `Alt+T` | Move focus to track number field |
| `Esc` | Close command bar |

Commands run in a background thread with a 10-second timeout — the UI never freezes.

### Big cover popup (`S` or click thumbnail)

Opens a floating modal window showing the album art at `COVER_BIG_SIZE` pixels.
The image scales to fit while preserving aspect ratio.
Close with `Esc`, `S`, or clicking the image.

### Window icon

The icon is resolved in order:
1. `WINDOW_ICON` from config (absolute path, or relative to script directory)
2. `mpdpop.ico`, `mpdpop.png`, `mpdpop.gif` in the script directory
3. Any `.ico` → `.png` → `.gif` found in the script directory (alphabetical)

A missing icon is silently ignored — it never prevents the dialog from opening.

### Multi-monitor

The dialog opens on whichever monitor contains the mouse cursor, centred within
that monitor's work area.

| Platform | Method |
|---|---|
| Windows | `MonitorFromPoint` + `GetMonitorInfoW` |
| Linux | `xrandr --query` parsing |
| macOS | `AppKit.NSScreen.screens()` |
| Fallback | Virtual screen size from cursor position |

### Platform dialog backends

| Platform | Primary | Fallback |
|---|---|---|
| Windows | Tkinter | terminal input |
| macOS | Tkinter | terminal input |
| Linux | Tkinter (if `$DISPLAY` / `$WAYLAND_DISPLAY`) | zenity → kdialog → terminal |

The zenity/kdialog fallbacks do not include cover art or bio — those require Tkinter.

---

## Info panel

The panel at the top of the dialog shows cover art, track metadata, and artist bio.

- **Startup** — spinner and progress bar start immediately. Cache hit stops them in
  under 10 ms. Cache miss runs a background network fetch.
- **Arrow navigation** — labels update instantly on every keypress. Spinner and
  progress bar restart immediately. A network fetch fires only after 280 ms of idle
  time (debounce) so rapid scrolling never floods the network.
- **Token guard** — each fetch call mints a generation token. Results from
  superseded fetches (user moved to another track before previous fetch finished)
  are silently discarded.
- **Stale-while-revalidate** — cached bios older than `CACHE_REVALIDATE_DAYS`
  trigger a silent background re-fetch. The UI still shows the cached version
  instantly; the cache is updated if the bio changed.

---

## Cover art sources

Tried in order, first success wins. Result is cached to a file.

1. **MPD embedded art** — `readpicture` command (fastest, no network)
2. **Local file** — `cover.jpg`, `folder.jpg`, `front.jpg`, `AlbumArt.jpg`, etc.
   Searches `MPD_MUSIC_DIR`, `~/Music`, `/var/lib/mpd/music`
3. **Last.fm** `album.getInfo` — requires `LASTFM_API_KEY`
4. **MusicBrainz Cover Art Archive** — no key needed
5. **Discogs** — requires `DISCOGS_TOKEN`

Cover files are cached at `COVER_CACHE_DIR` (default: system temp).
Set a persistent directory to avoid re-downloading between sessions:

```ini
COVER_CACHE_DIR = ~/.cache/mpdpop/covers   # persistent across reboots
```

---

## Artist biography sources

Tried in order, first non-empty result wins. The source label shows which
service provided the data and whether it came from cache:

| Label | Meaning |
|---|---|
| `via Last.fm` | Fetched live from Last.fm |
| `via Last.fm (cached)` | Read from cache; originally from Last.fm |
| `cached` | Text cached by old code with no source record; backfilling in background |
| `not found` | All services tried, nothing returned — retried every time |

1. **Last.fm** `artist.getInfo` — requires `LASTFM_API_KEY`
2. **Discogs** artist profile — requires `DISCOGS_TOKEN`
3. **MusicBrainz** artist annotation
4. **Wikipedia** intro paragraph — always available, no key needed

Set `BIO_LANG` for non-English bios (e.g. `id`, `de`, `fr`).

---

## Layered cache

Bio text is cached across three layers. Cover art uses plain files (faster than
any DB for binary blobs).

```
Read order:   Redis → pickle files → SQLite
Write order:  all available layers on every cache miss
```

| Layer | Speed | Requires |
|---|---|---|
| Redis | ~0.1 ms | `pip install redis` + running Redis server |
| Pickle files | ~1 ms | nothing (stdlib only) |
| SQLite / SQLAlchemy | ~5 ms | `sqlite3` stdlib, or `pip install sqlalchemy` for other DBs |

On a cache hit the value is promoted upward so subsequent reads are faster.

**Stale-while-revalidate** — entries older than `CACHE_REVALIDATE_DAYS` (default 7)
trigger a silent background refresh. `CACHE_TTL_DAYS` (default 30) is the hard
expiry after which the entry is deleted entirely.

Not-found results are **never cached** — all services are retried every time
until one returns data.

### Cache CLI

```bash
python3 mpdpop_cache.py --stats          # row counts per namespace
python3 mpdpop_cache.py --evict          # remove expired pickle + SQL entries
python3 mpdpop_cache.py --flush bio      # wipe all bio entries from all layers
```

### Alternative databases

```ini
# PostgreSQL
CACHE_DB_URL = postgresql://user:pass@localhost/mpdpop

# MySQL
CACHE_DB_URL = mysql+pymysql://user:pass@localhost/mpdpop
```

Requires `pip install sqlalchemy` plus the driver (`psycopg2`, `pymysql`, etc.).

---

## Multi-monitor support

The dialog opens on whichever monitor contains the mouse cursor, centred
within that monitor's work area (taskbar excluded on Windows).

| Platform | Detection method |
|---|---|
| Windows | `MonitorFromPoint` + `GetMonitorInfoW` (win32 API) |
| Linux | `xrandr --query` output parsing |
| macOS | `AppKit.NSScreen.screens()` (requires `pyobjc`) |
| Fallback | Virtual screen bounds from cursor position |

---

## Platform dialog backends

| Platform | Primary | Fallback chain |
|---|---|---|
| Windows | Tkinter (bundled) | terminal input |
| macOS | Tkinter | terminal input |
| Linux | Tkinter (if `$DISPLAY` set) | zenity → kdialog → terminal |

The Linux fallback chain (`zenity`, `kdialog`) does not include the cover art
or bio panel — those require Tkinter.

---

## Info panel behaviour

The panel at the top of the dialog shows cover art, track metadata, and artist bio.

- **Startup**: spinner and progress bar start immediately. If cached data exists,
  they stop within milliseconds. If not, a network fetch runs in the background.
- **Arrow navigation**: labels update instantly on each keypress. The spinner and
  progress bar restart immediately. A fetch fires after 280 ms of idle time
  (debounce) so rapid scrolling does not flood the network.
- **Token guard**: each fetch call mints a new generation token. Results from
  superseded fetches (user moved to a different track before the previous fetch
  completed) are silently discarded.

---

## Config module CLI

```bash
# Print current resolved config (API keys redacted)
python3 mpdpop_env.py

# Write a fresh commented template to ./mpdpop.env
python3 mpdpop_env.py --write-template
```

---

## Art info module CLI

```bash
# Test fetching for a specific artist / album
python3 mpdpop_artinfo.py "Radiohead" "OK Computer"
```

Prints cover art size (bytes) and the first 300 characters of the bio, using
whichever services are configured.

## Desktop overlay (`mpdpop_overlay.py`)

A CD Art Display-style always-on-top widget that sits on the desktop showing
album cover art and playback controls.

```bash
python3 mpdpop_overlay.py
```

Or set `OVERLAY_AUTO_START = true` to launch it alongside the playlist dialog.

### Layout

```
┌──────────────────────────────┐  ← OVERLAY_SIZE px (default 220)
│                              │
│       Album cover art        │  rounded corners, shared cover cache
│    (fills entire square)     │
│                              │
│  ⏮  ⏹  ⏯  ⏭       50%  │  fades in on hover, hidden at rest
├──────────────────────────────┤  4px progress bar (click to seek)
│ Song Title                   │  scrolling if too wide
│ Artist                       │
├──────────────────────────────┤
│  ░░▓▓▓░░░░░░░░░░░░░░░░░░░    │  mirror reflection (toggle with M)
└──────────────────────────────┘
```

### Overlay interactions

| Action | Result |
|---|---|
| Hover | Controls and status badges fade in smoothly |
| Mouse wheel | Volume ±5%, shows badge for 1.5 s |
| Click progress bar | Seek to position |
| Drag | Reposition anywhere on screen |
| Double-click | Open `mpdpop.py` playlist popup |
| Right-click | Context menu |

### Overlay keyboard shortcuts

| Key | Action |
|---|---|
| `M` | Toggle mirror/reflection on/off |
| `Esc` | Quit overlay |
| `Q` | Quit overlay |

### Status badges (top-left, hover only)

Small icons show active MPD options: `⟳` repeat, `⤮` random, `①` single, `⌫` consume.

### Context menu (right-click)

Play/Pause · Previous · Next · Stop · Toggle Repeat · Toggle Random ·
🪞 Hide/Show Mirror · Open Playlist · Close Overlay

### Overlay config keys

| Key | Default | Description |
|---|---|---|
| `OVERLAY_SIZE` | `220` | Cover square size in pixels |
| `OVERLAY_X` | `-1` | Initial X position (`-1` = right edge) |
| `OVERLAY_Y` | `-1` | Initial Y position (`-1` = vertically centred) |
| `OVERLAY_OPACITY` | `0.92` | Window alpha (0.0–1.0) |
| `OVERLAY_REFLECTION` | `true` | Show mirror below cover at startup |
| `OVERLAY_POLL_MS` | `1000` | MPD poll interval in milliseconds |
| `OVERLAY_FONT` | _(auto)_ | Font name (blank = Segoe UI / Helvetica Neue / DejaVu Sans) |
| `OVERLAY_FONT_SIZE` | `10` | Base font size; all other sizes derived from this |
| `OVERLAY_CONTROLS_SIZE` | `32` | Control button size in pixels |
| `OVERLAY_ALWAYS_ON_TOP` | `true` | Pin above other windows |
| `OVERLAY_AUTO_START` | `false` | Launch with `mpdpop.py` |
| `OVERLAY_CORNER_RADIUS` | `12` | Cover corner radius in pixels (requires Pillow) |

Font size derivation from `OVERLAY_FONT_SIZE` (base = `N`):

| Element | Size |
|---|---|
| Track title | `N` bold |
| Artist name | `max(7, N-2)` |
| ♪ placeholder | `max(16, cover÷4)` |
| Control buttons | `max(10, ctrl_size÷2)` |
| Volume / badges | `max(7, N-3)` |
| Progress time tooltip | `max(6, N-4)` |

---

## Environment variable reference

### MPD

| Variable | Default | Description |
|---|---|---|
| `MPD_HOST` | `127.0.0.1` | MPD server hostname or IP |
| `MPD_PORT` | `6600` | MPD server port |
| `MPD_PASSWORD` | _(blank)_ | MPD password if required |
| `MPD_TIMEOUT` | `5` | Socket timeout in seconds |

### API keys

| Variable | Default | Description |
|---|---|---|
| `LASTFM_API_KEY` | _(blank)_ | Last.fm API key |
| `DISCOGS_TOKEN` | _(blank)_ | Discogs personal access token |
| `MUSICBRAINZ_APP` | `mpdpop/1.0` | MusicBrainz User-Agent string |

### Cache

| Variable | Default | Description |
|---|---|---|
| `CACHE_REDIS_URL` | _(blank)_ | Redis URL, e.g. `redis://localhost:6379/0` |
| `CACHE_PICKLE` | `true` | Enable pickle file cache layer |
| `CACHE_PICKLE_DIR` | _(blank)_ | Directory for pickle files |
| `CACHE_DB_URL` | _(blank)_ | SQLAlchemy DB URL (blank = SQLite in `~/.local/share/mpdpop/`) |
| `CACHE_TTL_DAYS` | `30` | Hard expiry — entry deleted after this many days |
| `CACHE_REVALIDATE_DAYS` | `7` | Soft threshold — silent background re-fetch after this many days |

### Cover art

| Variable | Default | Description |
|---|---|---|
| `COVER_SIZE` | `120` | Thumbnail size in pixels (main dialog) |
| `COVER_BIG_SIZE` | `480` | Big cover popup size in pixels |
| `COVER_CACHE_DIR` | _(blank)_ | Directory for cover image files (blank = system temp) |

### Artist bio

| Variable | Default | Description |
|---|---|---|
| `BIO_MAX_CHARS` | `600` | Truncate bio text to this many characters |
| `BIO_LANG` | `en` | Language code for Wikipedia / Last.fm |

### UI (playlist dialog)

| Variable | Default | Description |
|---|---|---|
| `DIALOG_WIDTH` | `780` | Main dialog width in pixels |
| `DIALOG_HEIGHT` | `620` | Main dialog height in pixels |
| `INFO_PANEL_BIO_H` | `80` | Artist bio area height in pixels |
| `PAGE_STEP` | `10` | Rows scrolled per PgUp / PgDn |
| `CMD_HISTORY` | `50` | Maximum command bar history entries |
| `WINDOW_ICON` | _(blank)_ | Path to icon file (`.ico`, `.png`, `.gif`) — auto-detected if blank |

### Overlay

| Variable | Default | Description |
|---|---|---|
| `OVERLAY_SIZE` | `220` | Cover square size in pixels |
| `OVERLAY_X` | `-1` | Initial X (`-1` = right screen edge) |
| `OVERLAY_Y` | `-1` | Initial Y (`-1` = vertically centred) |
| `OVERLAY_OPACITY` | `0.92` | Window alpha 0.0–1.0 |
| `OVERLAY_REFLECTION` | `true` | Show mirror at startup |
| `OVERLAY_POLL_MS` | `1000` | MPD poll interval in milliseconds |
| `OVERLAY_FONT` | _(auto)_ | Font name |
| `OVERLAY_FONT_SIZE` | `10` | Base font size |
| `OVERLAY_CONTROLS_SIZE` | `32` | Control button size in pixels |
| `OVERLAY_ALWAYS_ON_TOP` | `true` | Always on top |
| `OVERLAY_AUTO_START` | `false` | Launch overlay when `mpdpop.py` opens |
| `OVERLAY_CORNER_RADIUS` | `12` | Cover corner radius in pixels |

---

## CLI tools

```bash
# Config — print resolved config, write template
python3 mpdpop_env.py
python3 mpdpop_env.py --write-template

# Art info — test cover + bio fetch for an artist
python3 mpdpop_artinfo.py "Radiohead" "OK Computer"

# Cache — stats, evict, flush
python3 mpdpop_cache.py --stats
python3 mpdpop_cache.py --evict
python3 mpdpop_cache.py --flush bio
```

---

## LICENSE

Hadi Cahyadi 

[MIT © Hadi Cahyadi](LICENSE)

## 👤 Author
        
[Hadi Cahyadi](mailto:cumulus13@gmail.com)
    

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/cumulus13)

[![Donate via Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/cumulus13)
 
[Support me on Patreon](https://www.patreon.com/cumulus13)
