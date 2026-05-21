#!/usr/bin/env python3
# File: mpdpop_env.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Description: Config loader — reads mpdpop.env then overlays os.environ.
#              All API keys and MPD settings come from here.
# License: MIT

import os
import re
from pathlib import Path

# ── Locations searched for the .env file, in order ──────────────────────────
_ENV_SEARCH = [
    Path(__file__).parent / "mpdpop.env",       # same dir as this script
    Path.home() / ".config" / "mpdpop.env",     # XDG config
    Path.home() / ".mpdpop.env",                # home dotfile
]

# ── Defaults ─────────────────────────────────────────────────────────────────
_DEFAULTS: dict[str, str] = {
    # MPD connection
    "MPD_HOST":     "127.0.0.1",
    "MPD_PORT":     "6600",
    "MPD_PASSWORD": "",
    "MPD_TIMEOUT":  "5",

    # API keys — leave blank to skip that service
    "LASTFM_API_KEY":    "",       # https://www.last.fm/api/account/create
    "DISCOGS_TOKEN":     "",       # https://www.discogs.com/settings/developers
    "MUSICBRAINZ_APP":   "mpdpop/1.0",   # User-Agent for MusicBrainz (no key needed)

    # Cache layers
    "CACHE_REDIS_URL":   "",           # e.g. redis://localhost:6379/0  (blank = skip)
    "CACHE_PICKLE_DIR":  "",           # blank = auto (alongside DB)
    "CACHE_DB_URL":      "",           # blank = sqlite in ~/.local/share/mpdpop/
    "CACHE_TTL_DAYS":    "30",         # days before cached bio expires
    "CACHE_PICKLE":      "true",       # set false to disable pickle layer

    # Cover art
    "COVER_SIZE":        "120",    # px, square thumbnail in main dialog
    "COVER_BIG_SIZE":    "480",    # px, square for big cover popup (s / click)
    "INFO_PANEL_BIO_H":  "80",     # px height of artist bio text area
    "COVER_CACHE_DIR":   "",       # blank = system temp; set to persist cache

    # Artist bio
    "BIO_MAX_CHARS":     "600",    # truncate bio text to this many characters
    "BIO_LANG":          "en",     # Wikipedia language code

    "CMD_HISTORY":       "50",     # max command history entries
    "DIALOG_WIDTH":      "780",
    "DIALOG_HEIGHT":     "620",
    "PAGE_STEP":         "10",
    "WINDOW_ICON":       "",       # path to icon file (.ico / .png / .gif)
}


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file. Supports # comments and quoted values."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()
        # Strip surrounding quotes (single or double)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
            raw = raw[1:-1]
        # Inline comment removal for unquoted values
        raw = re.sub(r"\s+#.*$", "", raw)
        result[key] = raw
    return result


class Config:
    """
    Merged configuration:
      mpdpop.env  <  os.environ  (os.environ always wins)

    Usage:
        from mpdpop_env import Config
        cfg = Config()
        key = cfg["LASTFM_API_KEY"]
        size = cfg.int("COVER_SIZE")
    """

    def __init__(self):
        self._data: dict[str, str] = dict(_DEFAULTS)

        # Layer 1 — first .env file found
        for p in _ENV_SEARCH:
            parsed = _parse_env_file(p)
            if parsed:
                self._data.update(parsed)
                self._env_file = p
                break
        else:
            self._env_file = None

        # Layer 2 — os.environ overrides everything
        for key in list(self._data.keys()):
            if key in os.environ:
                self._data[key] = os.environ[key]

    # ── Access helpers ────────────────────────────────────────────────────────

    def __getitem__(self, key: str) -> str:
        return self._data.get(key, "")

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def int(self, key: str, fallback: int = 0) -> int:
        try:
            return int(self._data.get(key, fallback))
        except (ValueError, TypeError):
            return fallback

    def bool(self, key: str, fallback: bool = False) -> bool:
        val = self._data.get(key, "").lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off"):
            return False
        return fallback

    def has(self, key: str) -> bool:
        """True if key is set and non-empty."""
        return bool(self._data.get(key, "").strip())

    def env_file_path(self) -> str:
        """Return path of the .env file that was loaded, or empty string."""
        return str(self._env_file) if self._env_file else ""

    def cover_cache_dir(self) -> Path:
        """Return resolved cover cache directory, creating it if needed."""
        import tempfile
        raw = self._data.get("COVER_CACHE_DIR", "").strip()
        if raw:
            p = Path(raw).expanduser()
        else:
            p = Path(tempfile.gettempdir()) / "mpdpop_covers"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def all(self) -> dict[str, str]:
        return dict(self._data)

    def __repr__(self) -> str:
        safe = {k: ("***" if "KEY" in k or "TOKEN" in k or "PASSWORD" in k else v)
                for k, v in self._data.items()}
        return f"Config(env_file={self._env_file}, data={safe})"


# ── Template generator ───────────────────────────────────────────────────────

def write_template(path: Path | None = None) -> Path:
    """Write a commented template mpdpop.env to *path* (default: next to this file)."""
    if path is None:
        path = Path(__file__).parent / "mpdpop.env"

    template = """\
# mpdpop.env — configuration for mpdpop
# Lines starting with # are comments.
# os.environ always overrides values set here.

# ── MPD Connection ───────────────────────────────────────────────────────────
MPD_HOST     = 127.0.0.1
MPD_PORT     = 6600
MPD_PASSWORD =
MPD_TIMEOUT  = 5

# ── API Keys ─────────────────────────────────────────────────────────────────
# Last.fm  — free key at https://www.last.fm/api/account/create
LASTFM_API_KEY   =

# Discogs  — personal token at https://www.discogs.com/settings/developers
DISCOGS_TOKEN    =

# MusicBrainz needs no key; set your app name/version for the User-Agent
MUSICBRAINZ_APP  = mpdpop/1.0

# ── Cover Art ────────────────────────────────────────────────────────────────
COVER_SIZE      = 120          # thumbnail size in pixels (square)
COVER_CACHE_DIR =              # blank = system temp dir

# ── Artist Bio ───────────────────────────────────────────────────────────────
BIO_MAX_CHARS = 600            # truncate bio to this many characters
BIO_LANG      = en             # Wikipedia language code (en, id, de, fr …)

# ── UI ───────────────────────────────────────────────────────────────────────
DIALOG_WIDTH  = 780
DIALOG_HEIGHT = 620
PAGE_STEP     = 10             # rows scrolled per PgUp/PgDn
"""
    path.write_text(template, encoding="utf-8")
    return path


# ── CLI helper ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--write-template" in sys.argv:
        p = write_template()
        print(f"Template written to: {p}")
    else:
        cfg = Config()
        print(repr(cfg))
        if cfg.env_file_path():
            print(f"Loaded from: {cfg.env_file_path()}")
        else:
            print("No .env file found; using defaults + os.environ")
