# mpdpop V2

A cross-platform MPD playlist popup controller with cover art, artist biography,
layered caching, and a built-in command bar.

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
| `mpdpop.py` | 1003 | Entry point, Tkinter dialog, platform dialogs |
| `mpdpop_artinfo.py` | 1109 | Cover art fetcher, bio fetcher, UI panel widgets |
| `mpdpop_cache.py` | 655 | Layered cache: Redis → pickle → SQLAlchemy/SQLite |
| `mpdpop_env.py` | 222 | Config loader (`mpdpop.env` + `os.environ`) |
| `mpdpop.env` | 53 | Your settings and API keys (edit this) |
| `mpdpop_overlay.py` | 1027 | CD Art Display-style always-on-top desktop overlay for MPD. |

All five files must live in the **same directory**.

---

## Quick start

```bash
# minimum — no extra packages needed
python3 mpdpop.py

# recommended — enables JPEG cover art
pip install pillow

# full feature set
pip install pillow redis sqlalchemy
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
| `pillow` | JPEG / WebP cover art, proper aspect-ratio resize for big cover popup |
| `redis` | Redis cache layer (fastest, in-memory) |
| `sqlalchemy` | Richer DB support for cache (PostgreSQL, MySQL, etc.) — falls back to raw `sqlite3` |
| `AppKit` (macOS) | Accurate multi-monitor detection on macOS |
| `xrandr` (Linux) | Accurate multi-monitor detection on Linux (usually pre-installed) |

Without `pillow`, only native PNG cover art renders. JPEG files show a
placeholder with an install hint.

---

## Configuration

Copy `mpdpop.env` to the same directory as the scripts and edit it.
`os.environ` always overrides `.env` values.

The file is searched in order:
1. Same directory as `mpdpop_env.py`
2. `~/.config/mpdpop.env`
3. `~/.mpdpop.env`

**Important**: leave unused values blank — do not add `# comments` after a value
on the same line, as they are treated as part of the value.

```ini
# ── MPD Connection ───────────────────────────────────────────────────────────
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
```

All keys can also be set as environment variables:

```bash
export MPD_HOST=192.168.1.10
export LASTFM_API_KEY=abc123
python3 mpdpop.py
```

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `↑` / `↓` | Navigate track list (works from any widget) |
| `PgUp` / `PgDn` | Scroll list by `PAGE_STEP` rows |
| `Enter` | Play selected track |
| `F` | Focus filter box |
| `T` | Focus track number input |
| `S` | Open big cover art popup |
| `C` | Toggle command bar |
| `Esc` | Close command bar (if open), otherwise close dialog |
| Double-click | Play track immediately |

### Command bar (`C`)

The command bar accepts any shell command. Output appears in a scrollable
4-line terminal below the input.

examples:
```
toggle              ← pause / resume
next                ← skip to next
prev                ← go back
volume +5           ← volume up 5%
volume -10          ← volume down 10%
status              ← show full MPD status
search artist Lorde ← search playlist
clear
load myplaylist
play
ls | head -20       ← pipes work too
```

- `↑` / `↓` inside the command field cycles history (last `CMD_HISTORY` entries)
- `Esc` inside the command field closes the bar without closing the dialog
- Commands run in a background thread with a 10-second timeout — the UI never freezes

---

## Big cover popup (`S` or click thumbnail)

Opens a floating modal window showing the album art at `COVER_BIG_SIZE` pixels.
The image scales to fit while preserving aspect ratio. Close with `Esc`, `S`,
or clicking the image.

Configurable size:
```ini
COVER_BIG_SIZE = 600   # or 800 for large displays
```

---

## Cover art sources

Tried in order, first success wins. Result is cached to disk as a file.

1. **MPD embedded art** — `readpicture` command (fastest, no network)
2. **Local file** — `cover.jpg`, `folder.jpg`, `front.jpg`, `AlbumArt.jpg`, etc.
   next to the music file. Searches `MPD_MUSIC_DIR`, `~/Music`, `/var/lib/mpd/music`
3. **Last.fm** `album.getInfo` — requires `LASTFM_API_KEY`
4. **MusicBrainz Cover Art Archive** — no key needed
5. **Discogs** — requires `DISCOGS_TOKEN`

Cover files are cached at `COVER_CACHE_DIR` (default: system temp).
Set a persistent directory to avoid re-downloading between sessions:

```ini
COVER_CACHE_DIR = ~/.cache/mpdpop/covers
```

---

## Artist biography sources

Tried in order, first non-empty result wins. Text is cached in the layered cache.

1. **Last.fm** `artist.getInfo` — richest, includes listener counts; requires `LASTFM_API_KEY`
2. **Discogs** artist profile — requires `DISCOGS_TOKEN`
3. **MusicBrainz** artist annotation
4. **Wikipedia** intro paragraph — no key needed, always available

Language for Wikipedia can be set with `BIO_LANG` (e.g. `id` for Indonesian,
`de` for German). Last.fm also honours this for translated bios where available.

---

## Layered cache

Text metadata (artist bios, tags) is cached in three layers. Each layer is
independently optional.

```
Read order:   Redis → pickle files → SQLite
Write order:  all available layers on every cache miss
```

| Layer | Speed | Requires | Notes |
|---|---|---|---|
| Redis | ~0.1 ms | `pip install redis` + Redis server | In-memory, TTL automatic |
| Pickle files | ~1 ms | nothing | One `.pkl` file per cache key |
| SQLite / SQLAlchemy | ~5 ms | `sqlite3` (stdlib) or `pip install sqlalchemy` | Always-on fallback |

On a cache hit, the value is promoted upward (pickle → Redis, SQLite → pickle + Redis)
so subsequent reads are faster.

### Cache CLI

```bash
# Show cache statistics
python3 mpdpop_cache.py --stats

# Evict expired entries from pickle + SQLite
python3 mpdpop_cache.py --evict

# Flush all bio entries from all layers
python3 mpdpop_cache.py --flush bio
```

### Using a different database

```ini
# PostgreSQL
CACHE_DB_URL = postgresql://user:pass@localhost/mpdpop

# MySQL
CACHE_DB_URL = mysql+pymysql://user:pass@localhost/mpdpop
```

Requires `pip install sqlalchemy` plus the appropriate driver
(`psycopg2`, `pymysql`, etc.).

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

---

## Environment variable reference

| Variable | Default | Description |
|---|---|---|
| `MPD_HOST` | `127.0.0.1` | MPD server hostname or IP |
| `MPD_PORT` | `6600` | MPD server port |
| `MPD_PASSWORD` | _(blank)_ | MPD password if required |
| `MPD_TIMEOUT` | `5` | Socket timeout in seconds |
| `LASTFM_API_KEY` | _(blank)_ | Last.fm API key |
| `DISCOGS_TOKEN` | _(blank)_ | Discogs personal access token |
| `MUSICBRAINZ_APP` | `mpdpop/1.0` | MusicBrainz User-Agent string |
| `CACHE_REDIS_URL` | _(blank)_ | Redis URL, e.g. `redis://localhost:6379/0` |
| `CACHE_PICKLE` | `true` | Enable pickle file cache layer |
| `CACHE_PICKLE_DIR` | _(blank)_ | Directory for pickle files |
| `CACHE_DB_URL` | _(blank)_ | SQLAlchemy DB URL |
| `CACHE_TTL_DAYS` | `30` | Days before cached bio text expires |
| `COVER_SIZE` | `120` | Thumbnail size in pixels (square) |
| `COVER_BIG_SIZE` | `480` | Big cover popup size in pixels |
| `COVER_CACHE_DIR` | _(blank)_ | Directory for cover art image files |
| `INFO_PANEL_BIO_H` | `80` | Bio text area height in pixels |
| `BIO_MAX_CHARS` | `600` | Truncate bio text to this many characters |
| `BIO_LANG` | `en` | Language code for Wikipedia / Last.fm bio |
| `DIALOG_WIDTH` | `780` | Main dialog width in pixels |
| `DIALOG_HEIGHT` | `620` | Main dialog height in pixels |
| `PAGE_STEP` | `10` | Rows scrolled per PgUp / PgDn |
| `CMD_HISTORY` | `50` | Maximum command bar history entries |
| `WINDOW_ICON` | _(blank)_ | Path to icon file (`.ico`, `.png`, `.gif`) |

---

## LICENSE

Hadi Cahyadi 

[MIT © Hadi Cahyadi](LICENSE)

## 👤 Author
        
[Hadi Cahyadi](mailto:cumulus13@gmail.com)
    

[![Buy Me a Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/cumulus13)

[![Donate via Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/cumulus13)
 
[Support me on Patreon](https://www.patreon.com/cumulus13)
