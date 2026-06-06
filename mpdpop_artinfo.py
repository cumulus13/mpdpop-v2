#!/usr/bin/env python3
# File: mpdpop_artinfo.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Description: Cover art + artist biography fetcher.
#              Service priority (each falls through to next on failure):
#
#   Cover art:   MPD embedded → Last.fm → MusicBrainz/CAA → Discogs
#   Artist bio:  Last.fm → Discogs → MusicBrainz → Wikipedia
#
# All API keys read via mpdpop_env.Config.
# License: MIT

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable
import sys

if any(i in ("--mpdpop-debug", "--debug") for i in sys.argv[1:]):
    try:
        from richcolorlog import setup_logging, print_traceback as tprint  # type: ignore
        LOG_FILE_NAME = str(Path(__file__).parent / Path(__file__).stem ) + ".log"
        print(f"LOG_FILE_NAME: {LOG_FILE_NAME}")
        logger = setup_logging("MPDPOP", level="DEBUG", log_file=True, log_file_name=LOG_FILE_NAME)
    except:
        import logging
        logger = logging.getLogger("MPDPOP")  # type: ignore
        logger.setLevel(logging.DEBUG)  # type: ignore
else:
    class logger:
        def info(self, *args, **kargs):
            return

        error = info
        warning = info
        notice = info
        emergency = info
        alert = info
        debug = info
        critical = info
        ctraceback = info

# ── lazy import guard ─────────────────────────────────────────────────────────
try:
    from mpdpop_env import Config
except ImportError:
    # allow running standalone without the env module
    class Config:  # type: ignore
        def __getitem__(self, k): return os.environ.get(k, "")
        def get(self, k, d=""): return os.environ.get(k, d)
        def int(self, k, fb=0):
            try: return int(os.environ.get(k, fb))
            except: return fb
        def has(self, k): return bool(os.environ.get(k, "").strip())
        def cover_cache_dir(self):
            import tempfile; p = Path(tempfile.gettempdir()) / "mpdpop_covers"
            p.mkdir(parents=True, exist_ok=True); return p

_UA = "mpdpop/1.0 (https://github.com/cumulus13/mpdpop)"
_TIMEOUT = 8  # seconds per HTTP request

# ── cache layer (optional — degrades to no-cache if module missing) ───────────
try:
    from mpdpop_cache import BioCache
    _HAS_CACHE = True
except ImportError:
    _HAS_CACHE = False
    class BioCache:  # type: ignore
        """Stub when mpdpop_cache.py is absent."""
        def __init__(self, cfg): pass
        def get(self, *a, **kw): return None
        def set(self, *a, **kw): pass
        def stats(self): return {}


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, headers: dict | None = None,
         timeout: int = _TIMEOUT) -> bytes | None:
    """Simple GET, returns raw bytes or None on any error."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _getj(url: str, params: dict | None = None, headers: dict | None = None) -> dict | list | None:
    """GET + JSON parse."""
    raw = _get(url, params, headers)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _slug(text: str) -> str:
    """URL-safe slug for cache keys."""
    return re.sub(r"[^\w\-]", "_", text.lower())[:80]


def _album_from_path(mpd_file: str) -> str:
    """
    Extract album name from file path as a last resort when the Album tag
    is missing. Uses the immediate parent directory name (usually the album).
    e.g. "Artist/Some Album/01 - Track.flac" → "Some Album"
    """
    if not mpd_file:
        return ""
    parts = Path(mpd_file).parts
    # parts[-1] = filename, parts[-2] = album dir (if it exists)
    if len(parts) >= 2:
        return parts[-2]
    return ""


def _cache_path(cache_dir: Path, artist: str, album: str) -> Path:
    """
    Stable cache key based ONLY on artist + album.
    Never uses title — tracks on the same album always hit the same file.
    If album is empty, the key degrades to artist-only (still stable across
    tracks from the same artist when album tags are completely missing).
    """
    # normalise: strip, lowercase for the hash, keep original slug readable
    norm_artist = artist.strip().lower()
    norm_album  = album.strip().lower()
    key = hashlib.md5(f"{norm_artist}|{norm_album}".encode()).hexdigest()[:16]
    slug = _slug(artist)
    # try jpg first (most common), but accept any extension when reading
    return cache_dir / f"{slug}_{key}.jpg"


def _find_cache_file(cache_dir: Path, artist: str, album: str) -> Path | None:
    """
    Return existing cache file for artist+album regardless of extension,
    or None if not cached yet.
    """
    base = _cache_path(cache_dir, artist, album)
    # exact path first
    if base.exists():
        return base
    # try other extensions (png, gif, webp)
    stem = base.stem
    for ext in ("png", "gif", "webp", "jpeg"):
        p = base.with_suffix(f".{ext}")
        if p.exists():
            return p
    return None


def _truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def _detect_image_ext(data: bytes) -> str:
    """Detect image format from magic bytes. Returns 'jpg', 'png', 'gif', 'webp'."""
    if data[:4] == b"\x89PNG":         return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"): return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "webp"
    return "jpg"   # default — JPEG magic is FF D8, but also covers unknowns


# ─────────────────────────────────────────────────────────────────────────────
# Cover Art
# ─────────────────────────────────────────────────────────────────────────────

class CoverArtFetcher:
    """
    Fetch album cover art as raw JPEG/PNG bytes.

    Priority:
      1. MPD embedded art (readpicture command)
      2. Local folder.jpg / cover.jpg / front.jpg next to the music file
      3. Last.fm album.getInfo
      4. MusicBrainz Cover Art Archive
      5. Discogs
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache_dir = cfg.cover_cache_dir()

    # ── public API ───────────────────────────────────────────────────────────

    def fetch(self, artist: str, album: str, title: str = "",
              mpd_file: str = "", mpd_host: str = "127.0.0.1",
              mpd_port: int = 6600) -> bytes | None:
        """Return raw image bytes or None. Caches to disk."""

        # Resolve album: tag value → directory name from file path → empty
        # This ensures tracks on the same album always share the same cache key
        # even when some tracks have the Album tag and others don't.
        if not album and mpd_file:
            album = _album_from_path(mpd_file)

        # With no artist AND no album we can't make a meaningful key
        if not artist and not album:
            return None

        # Check cache — try all extensions, not just .jpg
        cached = _find_cache_file(self.cache_dir, artist, album)
        logger.debug(f"cached: {cached}")  # type: ignore
        if cached is not None:
            try:
                return cached.read_bytes()
            except OSError:
                pass   # corrupted file — fall through to re-fetch

        # Fetch from sources
        data = (
            self._from_mpd(mpd_host, mpd_port, mpd_file)
            or self._from_local(mpd_file)
            or self._from_lastfm(artist, album)
            or self._from_musicbrainz(artist, album)
            or self._from_discogs(artist, album)
        )

        logger.debug(f"data: {data}")  # type: ignore
        if data:
            # Detect actual format to store with correct extension
            ext = _detect_image_ext(data)
            out = _cache_path(self.cache_dir, artist, album).with_suffix(f".{ext}")
            try:
                out.write_bytes(data)
            except OSError:
                pass

        logger.debug(f"data: {data}")  # type: ignore
        return data

    # ── source 1: MPD embedded picture ───────────────────────────────────────

    def _from_mpd(self, host: str, port: int, mpd_file: str) -> bytes | None:
        """
        MPD readpicture protocol (binary-safe):
          → client sends:  readpicture "path/to/file" <offset>\n
          ← server sends:  size: <total>\n
                           type: image/jpeg\n
                           binary: <chunk_size>\n
                           <chunk_size raw bytes>\n
                           OK\n
          Repeat with increasing offset until all bytes received.
        """
        if not mpd_file:
            return None
        try:
            # helper: connect and consume banner
            def _connect():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect((host, port))
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(1)
                    if not chunk:
                        break
                    buf += chunk
                return s

            # helper: read lines until we hit the binary data marker
            def _read_headers(s) -> dict:
                headers = {}
                buf = b""
                while True:
                    byte = s.recv(1)
                    if not byte:
                        break
                    buf += byte
                    if buf.endswith(b"\n"):
                        line = buf.decode(errors="replace").strip()
                        buf = b""
                        if not line:
                            continue
                        if line == "OK":
                            headers["_done"] = True
                            break
                        if line.startswith("ACK"):
                            headers["_error"] = line
                            break
                        if ": " in line:
                            k, _, v = line.partition(": ")
                            headers[k.lower()] = v
                        # stop at binary: N — binary data follows immediately
                        if line.lower().startswith("binary:"):
                            break
                return headers

            # helper: read exactly N bytes
            def _read_exact(s, n: int) -> bytes:
                data = b""
                while len(data) < n:
                    chunk = s.recv(min(4096, n - len(data)))
                    if not chunk:
                        break
                    data += chunk
                return data

            # First request to get total size
            s = _connect()
            escaped = mpd_file.replace('"', '\\"')
            s.sendall(f'readpicture "{escaped}" 0\n'.encode())
            headers = _read_headers(s)

            if "_error" in headers or "_done" in headers:
                s.close()
                return None

            total_size = int(headers.get("size", 0))
            chunk_size = int(headers.get("binary", 0))
            if total_size == 0 or chunk_size == 0:
                s.close()
                return None

            # Read first chunk
            image_data = _read_exact(s, chunk_size)
            s.recv(1)   # trailing \n after binary data
            # consume OK\n
            s.recv(3)
            s.close()

            # Read remaining chunks if image spans multiple requests
            offset = chunk_size
            while offset < total_size:
                s = _connect()
                s.sendall(f'readpicture "{escaped}" {offset}\n'.encode())
                h2 = _read_headers(s)
                sz2 = int(h2.get("binary", 0))
                if sz2 == 0:
                    s.close()
                    break
                chunk = _read_exact(s, sz2)
                s.recv(1)   # trailing \n
                s.recv(3)   # OK\n
                s.close()
                image_data += chunk
                offset += sz2

            return image_data if len(image_data) == total_size else None

        except Exception:
            return None

    # ── source 2: local file next to music ───────────────────────────────────

    def _from_local(self, mpd_file: str) -> bytes | None:
        if not mpd_file:
            return None

        names = [
            "folder.jpg", "cover.jpg", "front.jpg", "AlbumArt.jpg",
            "folder.png", "cover.png", "front.png", "AlbumArt.png",
            "album.jpg",  "album.png", "artwork.jpg", "artwork.png",
            "Folder.jpg", "Cover.jpg", "Front.jpg",   # capitalised variants
        ]

        # Build candidate directories in priority order:
        # 1. mpd_file as absolute path (if the file system is mounted)
        # 2. Each known music root + mpd_file (relative path)
        candidate_dirs: list[Path] = []

        file_path = Path(mpd_file)
        if file_path.is_absolute() and file_path.exists():
            candidate_dirs.append(file_path.parent)
        
        music_dirs = [
            os.environ.get("MPD_MUSIC_DIR", ""),
            self.cfg.get("MPD_MUSIC_DIR", ""),
            os.path.expanduser("~/Music"),
            "/var/lib/mpd/music",
            "/home/mpd/music",
        ]
        for base in music_dirs:
            if not base:
                continue
            track_path = Path(base) / mpd_file
            album_dir  = track_path.parent
            if album_dir not in candidate_dirs:
                candidate_dirs.append(album_dir)

        for album_dir in candidate_dirs:
            for name in names:
                p = album_dir / name
                if p.exists():
                    try:
                        return p.read_bytes()
                    except OSError:
                        pass

            # Also try any *.jpg / *.png in the directory (glob fallback)
            try:
                for ext in ("*.jpg", "*.png", "*.gif"):
                    candidates = sorted(album_dir.glob(ext))
                    # skip files that look like track art (contain digits at start)
                    for c in candidates:
                        if not c.stem[:2].isdigit():
                            return c.read_bytes()
            except OSError:
                pass

        return None

    # ── source 3: Last.fm ─────────────────────────────────────────────────────

    def _from_lastfm(self, artist: str, album: str) -> bytes | None:
        key = self.cfg["LASTFM_API_KEY"]
        if not key or not artist:
            return None
        params = {
            "method": "album.getinfo",
            "api_key": key,
            "artist": artist,
            "album": album or artist,
            "format": "json",
            "autocorrect": "1",
        }
        data = _getj("https://ws.audioscrobbler.com/2.0/", params)
        if not data or "album" not in data:
            return None
        images = data["album"].get("image", [])
        # prefer extralarge or large
        url = ""
        for size in ("extralarge", "large", "medium"):
            for img in images:
                if img.get("size") == size and img.get("#text"):
                    url = img["#text"]
                    break
            if url:
                break
        return _get(url) if url else None

    # ── source 4: MusicBrainz / Cover Art Archive ─────────────────────────────

    def _from_musicbrainz(self, artist: str, album: str) -> bytes | None:
        if not artist or not album:
            return None
        ua = self.cfg.get("MUSICBRAINZ_APP", "mpdpop/1.0")
        # step 1: search for release
        params = {
            "query": f'artist:"{artist}" release:"{album}"',
            "fmt": "json",
            "limit": "3",
        }
        hdr = {"User-Agent": ua}
        data = _getj("https://musicbrainz.org/ws/2/release/", params, hdr)
        if not data or not data.get("releases"):
            return None
        for release in data["releases"]:
            mbid = release.get("id")
            if not mbid:
                continue
            # step 2: fetch from CAA
            img_data = _get(f"https://coverartarchive.org/release/{mbid}/front-250")
            if img_data:
                return img_data
        return None

    # ── source 5: Discogs ─────────────────────────────────────────────────────

    def _from_discogs(self, artist: str, album: str) -> bytes | None:
        token = self.cfg["DISCOGS_TOKEN"]
        if not token or not artist:
            return None
        params = {
            "q": f"{artist} {album}".strip(),
            "type": "release",
            "per_page": "3",
            "token": token,
        }
        data = _getj("https://api.discogs.com/database/search", params)
        if not data or not data.get("results"):
            return None
        for result in data["results"]:
            thumb = result.get("cover_image") or result.get("thumb")
            if thumb:
                img = _get(thumb)
                if img:
                    return img
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Artist Biography
# ─────────────────────────────────────────────────────────────────────────────

class ArtistBioFetcher:
    """
    Fetch a short artist biography text.

    Cache read order:  Redis → pickle → SQLite  (via BioCache)
    Fetch order:       Last.fm → Discogs → MusicBrainz → Wikipedia
    Cache write:       all layers on first fetch
    """

    def __init__(self, cfg: Config, cache: BioCache | None = None):
        self.cfg   = cfg
        self.cache = cache or BioCache(cfg)

    def fetch(self, artist: str, title: str = "") -> tuple[str, str]:
        """
        Return (bio_text, source_label).

        Cache strategy — stale-while-revalidate:
          • Always serve from cache immediately if data exists (fast UI).
          • If cached entry is older than CACHE_REVALIDATE_DAYS, silently
            re-fetch in background. If new data differs, update the cache.
            The current call still returns the cached version instantly.
          • Not-found results are never cached — all services retried next call.
        """
        if not artist:
            return "", ""

        import time as _time
        max_chars       = self.cfg.int("BIO_MAX_CHARS",        600)
        revalidate_secs = self.cfg.int("CACHE_REVALIDATE_DAYS", 7) * 86400

        # ── cache read ────────────────────────────────────────────────────────
        cached     = self.cache.get("bio",        artist)
        cached_src = self.cache.get("bio_source", artist)
        cached_at  = self.cache.get("bio_cached_at", artist)

        if cached is not None and not cached.startswith("No biography found"):
            # Determine age of cached entry
            age_secs = None
            if cached_at:
                try:
                    age_secs = _time.time() - float(cached_at)
                except (ValueError, TypeError):
                    age_secs = None

            is_stale = (age_secs is None) or (age_secs > revalidate_secs)

            if is_stale:
                # Serve cached version immediately, revalidate silently
                threading.Thread(
                    target=self._revalidate,
                    args=(artist, cached, cached_src, max_chars),
                    daemon=True,
                ).start()
                label = f"{cached_src} (cached·updating…)" if cached_src \
                        else "cached·updating…"
            else:
                label = f"{cached_src} (cached)" if cached_src else "cached"

            return cached, label

        # ── cache miss — full live fetch ──────────────────────────────────────
        return self._live_fetch(artist, max_chars)

    # ─────────────────────────────────────────────────────────────────────────

    def _live_fetch(self, artist: str, max_chars: int) -> tuple[str, str]:
        """Try each service in order, clean result, write all cache keys."""
        import time as _time

        bio    = ""
        source = ""

        if not bio:
            bio = self._from_lastfm(artist)
            if bio: source = "via Last.fm"
        if not bio:
            bio = self._from_discogs(artist)
            if bio: source = "via Discogs"
        if not bio:
            bio = self._from_musicbrainz(artist)
            if bio: source = "via MusicBrainz"
        if not bio:
            bio = self._from_wikipedia(artist)
            if bio: source = "via Wikipedia"

        if not bio:
            # Never cache not-found — retry all services next call
            return f'No biography found for "{artist}".', "not found"

        # clean
        bio = re.sub(r"<[^>]+>", "", bio)
        bio = re.sub(r"\s+", " ", bio).strip()
        bio = re.sub(r"\s*Read more on Last\.fm\s*$", "", bio,
                     flags=re.IGNORECASE).strip()
        result = _truncate(bio, max_chars)

        # write all three keys atomically
        now = str(_time.time())
        self.cache.set("bio",           result, artist)
        self.cache.set("bio_source",    source, artist)
        self.cache.set("bio_cached_at", now,    artist)

        return result, source

    def _revalidate(self, artist: str, old_text: str,
                    old_src: str, max_chars: int) -> None:
        """
        Background revalidation — called when cached entry is stale.
        Fetches fresh data; if it differs from cached text, updates all keys.
        Never called from the main thread — safe to block on network here.
        """
        try:
            new_text, new_source = self._live_fetch(artist, max_chars)
            # _live_fetch already wrote cache if data was found.
            # If no change, the timestamp was still refreshed — that's fine.
        except Exception:
            pass   # network error — keep old cached data, try again next time

    def _backfill_source(self, artist: str) -> None:
        """
        Background: for old cache entries that have bio text but no source.
        Probes services to identify origin, writes ONLY bio_source + bio_cached_at.
        Never overwrites bio text.
        """
        import time as _time
        source = ""
        if self._from_lastfm(artist):      source = "via Last.fm"
        elif self._from_discogs(artist):   source = "via Discogs"
        elif self._from_musicbrainz(artist): source = "via MusicBrainz"
        elif self._from_wikipedia(artist): source = "via Wikipedia"
        if source:
            self.cache.set("bio_source",    source,            artist)
            self.cache.set("bio_cached_at", str(_time.time()), artist)

        return result, source

    def _backfill_source(self, artist: str) -> None:
        """
        Called in a background thread only when bio text is cached but
        bio_source is not. Probes each service in order — just enough to
        identify which one has data — then writes ONLY the source key.
        Never touches the cached bio text.
        """
        source = ""
        if self._from_lastfm(artist):
            source = "via Last.fm"
        elif self._from_discogs(artist):
            source = "via Discogs"
        elif self._from_musicbrainz(artist):
            source = "via MusicBrainz"
        elif self._from_wikipedia(artist):
            source = "via Wikipedia"
        if source:
            self.cache.set("bio_source", source, artist)

    # ── source 1: Last.fm ─────────────────────────────────────────────────────

    def _from_lastfm(self, artist: str) -> str:
        key = self.cfg["LASTFM_API_KEY"]
        if not key:
            return ""
        params = {
            "method": "artist.getinfo",
            "api_key": key,
            "artist": artist,
            "format": "json",
            "autocorrect": "1",
            "lang": self.cfg.get("BIO_LANG", "en"),
        }
        data = _getj("https://ws.audioscrobbler.com/2.0/", params)
        if not data:
            return ""
        try:
            return data["artist"]["bio"]["summary"] or ""
        except (KeyError, TypeError):
            return ""

    # ── source 2: Discogs ─────────────────────────────────────────────────────

    def _from_discogs(self, artist: str) -> str:
        token = self.cfg["DISCOGS_TOKEN"]
        if not token:
            return ""
        params = {"q": artist, "type": "artist", "per_page": "3", "token": token}
        data = _getj("https://api.discogs.com/database/search", params)
        if not data or not data.get("results"):
            return ""
        for result in data["results"]:
            resource_url = result.get("resource_url")
            if not resource_url:
                continue
            detail = _getj(resource_url,
                           headers={"Authorization": f"Discogs token={token}"})
            if detail and detail.get("profile"):
                return detail["profile"]
        return ""

    # ── source 3: MusicBrainz annotation ─────────────────────────────────────

    def _from_musicbrainz(self, artist: str) -> str:
        ua = self.cfg.get("MUSICBRAINZ_APP", "mpdpop/1.0")
        params = {"query": f'artist:"{artist}"', "fmt": "json", "limit": "3"}
        data = _getj("https://musicbrainz.org/ws/2/artist/", params,
                     {"User-Agent": ua})
        if not data or not data.get("artists"):
            return ""
        for a in data["artists"]:
            mbid = a.get("id")
            if not mbid:
                continue
            ann = _getj(f"https://musicbrainz.org/ws/2/artist/{mbid}",
                        {"inc": "annotation", "fmt": "json"},
                        {"User-Agent": ua})
            if ann and ann.get("annotation"):
                text = ann["annotation"].get("text", "")
                if text:
                    return text
        return ""

    # ── source 4: Wikipedia ───────────────────────────────────────────────────

    def _from_wikipedia(self, artist: str) -> str:
        lang = self.cfg.get("BIO_LANG", "en")
        params = {
            "action": "query",
            "titles": artist,
            "prop": "extracts",
            "exintro": "true",
            "explaintext": "true",
            "redirects": "1",
            "format": "json",
        }
        data = _getj(f"https://{lang}.wikipedia.org/w/api.php", params)
        if not data:
            return ""
        try:
            pages = data["query"]["pages"]
            for page in pages.values():
                text = page.get("extract", "")
                if text and not page.get("missing"):
                    return text
        except (KeyError, TypeError):
            pass
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Async loader — fires both fetches in background threads, calls back on result
# ─────────────────────────────────────────────────────────────────────────────

class ArtInfoLoader:
    """
    Kick off cover art + bio fetches in background threads.
    Calls on_cover(bytes) and on_bio(str) from those threads when ready.
    The caller is responsible for thread-safe UI updates (use root.after() etc.)
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._cache         = BioCache(cfg)
        self._cover_fetcher = CoverArtFetcher(cfg)
        self._bio_fetcher   = ArtistBioFetcher(cfg, cache=self._cache)

    def load(
        self,
        artist: str,
        album: str,
        title: str = "",
        mpd_file: str = "",
        mpd_host: str = "127.0.0.1",
        mpd_port: int = 6600,
        on_cover: Callable[[bytes | None], None] | None = None,
        on_bio: Callable[[str, str], None] | None = None,
    ) -> None:
        """Start background threads for cover + bio. Returns immediately.
        on_bio is called as on_bio(text, source) where source is the service name.
        """
        if on_cover:
            threading.Thread(
                target=self._cover_worker,
                args=(artist, album, title, mpd_file, mpd_host, mpd_port, on_cover),
                daemon=True,
            ).start()
        if on_bio:
            threading.Thread(
                target=self._bio_worker,
                args=(artist, title, on_bio),
                daemon=True,
            ).start()

    def _cover_worker(self, artist, album, title, mpd_file,
                      mpd_host, mpd_port, callback):
        try:
            data = self._cover_fetcher.fetch(
                artist, album, title, mpd_file, mpd_host, mpd_port)
            callback(data)
        except Exception:
            callback(None)

    def _bio_worker(self, artist, title, callback):
        try:
            text, source = self._bio_fetcher.fetch(artist, title)
            callback(text, source)
        except Exception:
            callback("", "")


# ─────────────────────────────────────────────────────────────────────────────
# Tkinter helpers — build the info panel widget
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Constants — only colours here; sizes are computed per-call from cfg
# ─────────────────────────────────────────────────────────────────────────────
_PANEL_BG  = "#111827"
_INFO_BG   = "#1a1a2e"
_BIO_BG    = "#0f172a"
_COVER_BG  = "#1e293b"
_PBAR_H    = 3    # progress bar strip height (px) — fixed, tiny


# ─────────────────────────────────────────────────────────────────────────────
# Spinner  (pure Canvas arc animation, no GIF required)
# ─────────────────────────────────────────────────────────────────────────────

class _Spinner:
    """
    Animated arc spinner drawn on a tk.Canvas.
    Call .start() to begin, .stop() to freeze, .show_image(photo) to replace.
    """
    _ARC_COLOR   = "#3b82f6"
    _TRACK_COLOR = "#1e3a5f"
    _STEP        = 12   # degrees per frame
    _INTERVAL    = 30   # ms per frame

    def __init__(self, canvas, size: int):
        self._c    = canvas
        self._size = size
        self._angle = 0
        self._job   = None
        self._running = False

        pad = size // 6
        self._bbox = (pad, pad, size - pad, size - pad)

        # track arc (static)
        self._c.create_arc(*self._bbox, start=0, extent=359,
                           outline=self._TRACK_COLOR, width=3,
                           style="arc", tags="track")
        # moving arc
        self._arc = self._c.create_arc(*self._bbox, start=90, extent=90,
                                       outline=self._ARC_COLOR, width=3,
                                       style="arc", tags="spinner")
        # centre note (visible while spinning)
        cx = size // 2
        self._note = self._c.create_text(cx, cx, text="♪",
                                         fill="#334155",
                                         font=("sans-serif", size // 5),
                                         tags="note")

    def start(self):
        self._running = True
        self._c.itemconfigure("track",   state="normal")
        self._c.itemconfigure("spinner", state="normal")
        self._c.itemconfigure("note",    state="normal")
        self._c.delete("cover")
        self._tick()

    def stop(self):
        self._running = False
        if self._job:
            try:
                self._c.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def show_image(self, photo):
        """Replace spinner with a PhotoImage."""
        self.stop()
        self._c.itemconfigure("track",   state="hidden")
        self._c.itemconfigure("spinner", state="hidden")
        self._c.itemconfigure("note",    state="hidden")
        self._c.delete("cover")
        self._c.create_image(0, 0, anchor="nw", image=photo, tags="cover")
        self._c._photo = photo  # prevent GC

    def show_error(self):
        """Show a static ✕ when art is unavailable."""
        self.stop()
        self._c.itemconfigure("track",   state="hidden")
        self._c.itemconfigure("spinner", state="hidden")
        cx = self._size // 2
        self._c.itemconfigure("note", state="normal")
        self._c.itemconfig("note", text="✕", fill="#475569")

    def _tick(self):
        if not self._running:
            return
        self._angle = (self._angle - self._STEP) % 360
        self._c.itemconfigure("spinner", start=self._angle)
        self._job = self._c.after(self._INTERVAL, self._tick)


# ─────────────────────────────────────────────────────────────────────────────
# Shimmer progress bar
# ─────────────────────────────────────────────────────────────────────────────

class _ProgressBar:
    """
    Indeterminate shimmer bar drawn on a tk.Canvas.
    Fixed height _PBAR_H px, fills parent width.
    """
    _BG      = "#0f172a"
    _FG      = "#3b82f6"
    _SHIMMER = "#60a5fa"
    _STEP    = 6    # px per frame
    _INTERVAL= 20   # ms per frame
    _W_FRAC  = 0.35 # shimmer block is 35 % of total width

    def __init__(self, canvas):
        self._c    = canvas
        self._pos  = 0.0
        self._job  = None
        self._running = False
        self._width = 1

        self._bar  = self._c.create_rectangle(0, 0, 0, _PBAR_H,
                                              fill=self._FG, outline="",
                                              tags="bar")
        self._shim = self._c.create_rectangle(0, 0, 0, _PBAR_H,
                                              fill=self._SHIMMER, outline="",
                                              tags="shim")
        self._c.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        self._width = max(event.width, 1)

    def start(self):
        self._running = True
        self._c.itemconfigure("bar",  state="normal")
        self._c.itemconfigure("shim", state="normal")
        self._pos = 0.0
        self._tick()

    def stop(self):
        self._running = False
        if self._job:
            try:
                self._c.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._c.itemconfigure("bar",  state="hidden")
        self._c.itemconfigure("shim", state="hidden")

    def _tick(self):
        if not self._running:
            return
        w   = self._width
        sw  = int(w * self._W_FRAC)
        x0  = int(self._pos) - sw
        x1  = int(self._pos)
        # main bar fills from 0 to leading edge
        self._c.coords("bar",  0, 0, max(0, x1), _PBAR_H)
        # shimmer is a brighter trailing block
        self._c.coords("shim", max(0, x0), 0, max(0, x1), _PBAR_H)
        self._pos += self._STEP
        if self._pos > w + sw:
            self._pos = 0.0
        self._job = self._c.after(self._INTERVAL, self._tick)


# ─────────────────────────────────────────────────────────────────────────────
# Public: make_info_panel
# ─────────────────────────────────────────────────────────────────────────────

def make_info_panel(parent, font_name: str = "Segoe UI", cfg=None) -> dict:
    """
    Build a fixed-height info panel whose dimensions come from cfg.

    Sizes (all configurable via mpdpop.env / os.environ):
      COVER_SIZE      — cover canvas square px  (default 120)
      INFO_PANEL_BIO_H — bio area height px     (default 80)

    Spinner and progress bar are built but NOT started — they start only
    when update_info_panel() is called (i.e. when a real fetch begins).

    Layout:
    ┌────────────────────────────────────────────────────────────┐  ← cover_px + 16 pad
    │ [Canvas cover_px²]  │ ♪ now playing                        │
    │  spinner/cover      │ Title (bold)                         │
    │                     │ Artist                               │
    │                     │ Album · Duration                     │
    ├── progress bar (3px) ─────────────────────────────────────┤
    │ source label                                               │
    │ Bio text  (bio_h px, scrollable)                           │
    └────────────────────────────────────────────────────────────┘
    ── 1px separator ──────────────────────────────────────────────
    """
    try:
        import tkinter as tk
    except ImportError:
        return {}

    # ── derive sizes from cfg (or sensible defaults) ──────────────────────────
    cover_px = 120
    bio_h    = 80
    if cfg is not None:
        try: cover_px = int(cfg.get("COVER_SIZE",       "120"))
        except: pass
        try: bio_h    = int(cfg.get("INFO_PANEL_BIO_H", "80"))
        except: pass

    top_h   = cover_px + 16        # padding above + below cover
    panel_h = top_h + _PBAR_H + bio_h + 16 + 1   # +16 source label, +1 sep

    # ── outer wrapper — pack_propagate(False) locks total height ──────────────
    outer = tk.Frame(parent, bg=_PANEL_BG, height=panel_h, width=1)
    outer.pack(fill="x")
    outer.pack_propagate(False)

    # ── top row (cover + meta), height locked ─────────────────────────────────
    top = tk.Frame(outer, bg=_PANEL_BG, height=top_h)
    top.pack(fill="x")
    top.pack_propagate(False)

    # Cover canvas — fixed square, size from cfg
    cover_canvas = tk.Canvas(top,
                             width=cover_px, height=cover_px,
                             bg=_COVER_BG, highlightthickness=0,
                             relief="flat")
    cover_canvas.pack(side="left", padx=(10, 8), pady=8)

    # Spinner starts immediately — if cache hits, it stops in <10ms
    spinner = _Spinner(cover_canvas, cover_px)
    spinner.start()

    # Meta column
    info_col = tk.Frame(top, bg=_INFO_BG, height=top_h)
    info_col.pack(side="left", fill="x", expand=True, pady=0, padx=(0, 10))
    info_col.pack_propagate(False)

    tk.Label(info_col, text="♪  now playing",
             bg=_INFO_BG, fg="#4ade80",
             font=(font_name, 8), anchor="w"
             ).pack(fill="x", padx=6, pady=(10, 0))

    title_label = tk.Label(info_col, text="—",
                           bg=_INFO_BG, fg="#f1f5f9",
                           font=(font_name, 11, "bold"), anchor="w")
    title_label.pack(fill="x", padx=6, pady=(4, 0))

    artist_label = tk.Label(info_col, text="",
                            bg=_INFO_BG, fg="#94a3b8",
                            font=(font_name, 10), anchor="w")
    artist_label.pack(fill="x", padx=6, pady=(2, 0))

    album_label = tk.Label(info_col, text="",
                           bg=_INFO_BG, fg="#64748b",
                           font=(font_name, 9, "italic"), anchor="w")
    album_label.pack(fill="x", padx=6, pady=(1, 0))

    # ── progress bar — starts immediately, stops when bio arrives ────────────
    pbar_canvas = tk.Canvas(outer, height=_PBAR_H, bg=_BIO_BG,
                            highlightthickness=0)
    pbar_canvas.pack(fill="x")
    pbar = _ProgressBar(pbar_canvas)
    pbar.start()   # stops automatically when bio arrives (cache = instant)

    # ── bio section, height from cfg ──────────────────────────────────────────
    bio_outer = tk.Frame(outer, bg=_BIO_BG, height=bio_h + 16)
    bio_outer.pack(fill="x")
    bio_outer.pack_propagate(False)

    source_label = tk.Label(bio_outer, text="fetching…",
                            bg=_BIO_BG, fg="#475569",
                            font=(font_name, 7, "italic"), anchor="e")
    source_label.pack(fill="x", padx=8, pady=(2, 0))

    bio_text = tk.Text(bio_outer,
                       height=1,
                       wrap="word",
                       bg=_BIO_BG, fg="#94a3b8",
                       font=(font_name, 9),
                       relief="flat", bd=0,
                       padx=8, pady=2,
                       cursor="arrow",
                       state="disabled",
                       exportselection=False)
    bio_text.pack(fill="both", expand=True)
    bio_text.bind("<Key>", lambda e: "break")

    # ── separator ─────────────────────────────────────────────────────────────
    tk.Frame(outer, height=1, bg="#1e293b").pack(fill="x", side="bottom")

    return {
        "cover_canvas":  cover_canvas,
        "cover_px":      cover_px,      # stored so update_info_panel can use it
        "spinner":       spinner,
        "pbar":          pbar,
        "title_label":   title_label,
        "artist_label":  artist_label,
        "album_label":   album_label,
        "bio_text":      bio_text,
        "source_label":  source_label,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public: _fill_labels_only  (cheap, sync, no fetch)
# ─────────────────────────────────────────────────────────────────────────────

def _fill_labels_only(widgets: dict, track: dict) -> None:
    """
    Update the meta labels (title / artist / album) instantly with no
    network activity.  Used on startup and during fast arrow navigation
    so the text stays in sync while the debounce timer is counting down.
    """
    if not widgets:
        return
    title  = track.get("title",    "")
    artist = track.get("artist",   "")
    album  = track.get("album",    "")
    dur    = track.get("duration", "")
    _set_label_truncated(widgets["title_label"],  title  or "—", 48)
    _set_label_truncated(widgets["artist_label"], artist or "",  52)
    _set_label_truncated(widgets["album_label"],
                         " · ".join(filter(None, [album, dur])) or "", 52)


# ─────────────────────────────────────────────────────────────────────────────
# Public: update_info_panel  (labels + spinner + background fetch)
# ─────────────────────────────────────────────────────────────────────────────

def update_info_panel(widgets: dict, track: dict, root,
                      loader: "ArtInfoLoader",
                      cfg: "Config",
                      mpd_host: str = "127.0.0.1",
                      mpd_port: int = 6600) -> None:
    """
    Called only after the debounce timer fires (user has stopped moving).
    Updates labels, restarts spinner + progress bar, then fires background
    fetches.  A generation token discards results from superseded fetches.
    """
    if not widgets:
        return

    cover_size = widgets.get("cover_px", cfg.int("COVER_SIZE", 120))
    artist = track.get("artist", "")
    title  = track.get("title",  "")
    album  = track.get("album",  "")
    dur    = track.get("duration", "")
    file_  = track.get("file",   "")

    # ── update labels (may already be set by _fill_labels_only, harmless) ────
    _fill_labels_only(widgets, track)

    # ── restart loading indicators ────────────────────────────────────────────
    widgets["spinner"].start()
    widgets["pbar"].start()
    widgets["source_label"].config(text="fetching…")
    _set_bio_text(widgets["bio_text"], "")

    # ── generation token — stale callbacks self-discard ───────────────────────
    token = object()
    widgets["_token"] = token

    def _on_cover(data: bytes | None):
        if widgets.get("_token") is not token:
            return
        root.after(0, lambda: _apply_cover(widgets, data, cover_size))

    def _on_bio(text: str, source: str):
        if widgets.get("_token") is not token:
            return
        root.after(0, lambda: _apply_bio(widgets, text, source))

    loader.load(
        artist=artist, album=album, title=title,
        mpd_file=file_, mpd_host=mpd_host, mpd_port=mpd_port,
        on_cover=_on_cover, on_bio=_on_bio,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _set_label_truncated(label, text: str, max_chars: int) -> None:
    if len(text) > max_chars:
        text = text[:max_chars - 1] + "…"
    label.config(text=text)


def _set_bio_text(bio_widget, text: str) -> None:
    bio_widget.config(state="normal")
    bio_widget.delete("1.0", "end")
    if text:
        bio_widget.insert("1.0", text)
    bio_widget.config(state="disabled")
    bio_widget.yview_moveto(0.0)   # scroll back to top


def _apply_bio(widgets: dict, text: str, source: str) -> None:
    """Called on main thread when bio arrives. Stops progress bar."""
    widgets["pbar"].stop()
    widgets["source_label"].config(text=source)
    _set_bio_text(widgets["bio_text"],
                  text if text else "No biography available.")


def _apply_cover(widgets: dict, data: bytes | None, size: int) -> None:
    """Called on main thread when cover art arrives. Stops spinner."""
    # Always store raw bytes so big-cover dialog can use them later
    widgets["_cover_data"] = data
    spinner: _Spinner = widgets["spinner"]
    if not data:
        spinner.show_error()
        return
    try:
        from PIL import Image, ImageTk
        import io
        img   = Image.open(io.BytesIO(data)).convert("RGB")
        img   = img.resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        spinner.show_image(photo)
    except ImportError:
        _apply_cover_fallback(widgets["cover_canvas"], spinner, data, size)
    except Exception:
        spinner.show_error()


def _apply_cover_fallback(canvas, spinner: _Spinner,
                           data: bytes, size: int) -> None:
    """No Pillow — try tkinter native PNG."""
    try:
        import tkinter as tk
        if data[:4] != b"\x89PNG":
            spinner.show_error()
            return
        photo  = tk.PhotoImage(data=data)
        pw, ph = photo.width(), photo.height()
        if pw > 0 and ph > 0:
            factor = max(1, max(pw, ph) // size)
            photo  = photo.subsample(factor, factor)
        spinner.show_image(photo)
    except Exception:
        spinner.show_error()


# ─────────────────────────────────────────────────────────────────────────────
# Public: show_big_cover  — opens a Toplevel with full-size cover art
# ─────────────────────────────────────────────────────────────────────────────

def show_big_cover(widgets: dict, root, cfg, track: dict) -> None:
    """
    Open a floating Toplevel window showing the cover art at a larger size.
    Size is controlled by COVER_BIG_SIZE (default 480px square).
    Triggered by pressing 's' or clicking the cover canvas.
    If no cover data is available yet, shows a "no cover" message.
    """
    try:
        import tkinter as tk
    except ImportError:
        return

    data: bytes | None = widgets.get("_cover_data")   # may be None if not loaded yet
    big_size = 480
    try:
        big_size = int(cfg.get("COVER_BIG_SIZE", "480"))
    except Exception:
        pass

    # ── build toplevel ────────────────────────────────────────────────────────
    win = tk.Toplevel(root)
    win.title(track.get("title", "Cover Art"))
    win.configure(bg="#0f172a")
    win.resizable(False, False)
    win.attributes("-topmost", True)

    # Centre over parent
    root.update_idletasks()
    rx, ry = root.winfo_rootx(), root.winfo_rooty()
    rw, rh = root.winfo_width(), root.winfo_height()
    win.geometry(f"+{rx + rw//2 - big_size//2}+{ry + rh//2 - big_size//2}")

    # ── title bar inside window ───────────────────────────────────────────────
    title_text = track.get("title", "")
    artist_text = track.get("artist", "")
    if title_text or artist_text:
        header = tk.Frame(win, bg="#0f172a")
        header.pack(fill="x", padx=10, pady=(10, 4))
        if title_text:
            tk.Label(header, text=title_text,
                     bg="#0f172a", fg="#f1f5f9",
                     font=("Segoe UI", 11, "bold"),
                     anchor="center").pack(fill="x")
        if artist_text:
            tk.Label(header, text=artist_text,
                     bg="#0f172a", fg="#64748b",
                     font=("Segoe UI", 9),
                     anchor="center").pack(fill="x")

    # ── image area ────────────────────────────────────────────────────────────
    canvas = tk.Canvas(win, width=big_size, height=big_size,
                       bg="#1e293b", highlightthickness=0)
    canvas.pack(padx=10, pady=(4, 6))

    if not data:
        # No cover yet — show placeholder
        canvas.create_text(big_size // 2, big_size // 2,
                           text="♪\n\nNo cover available",
                           fill="#475569",
                           font=("Segoe UI", 20),
                           justify="center")
    else:
        _draw_big_cover(canvas, data, big_size)

    # ── close hint ────────────────────────────────────────────────────────────
    tk.Label(win, text="press Esc or S to close",
             bg="#0f172a", fg="#334155",
             font=("Segoe UI", 8)).pack(pady=(0, 8))

    win.bind("<Escape>", lambda _: win.destroy())
    win.bind("<s>",      lambda _: win.destroy())
    win.bind("<S>",      lambda _: win.destroy())
    canvas.bind("<Button-1>", lambda _: win.destroy())

    win.focus_set()
    win.grab_set()   # modal — disable interaction with parent while open


def _draw_big_cover(canvas, data: bytes, size: int) -> None:
    """Render cover bytes onto canvas at given size."""
    try:
        from PIL import Image, ImageTk
        import io
        img   = Image.open(io.BytesIO(data)).convert("RGB")
        # Scale to fit inside size×size keeping aspect ratio
        img.thumbnail((size, size), Image.LANCZOS)
        # Centre on canvas if not square
        photo = ImageTk.PhotoImage(img)
        ox = (size - img.width)  // 2
        oy = (size - img.height) // 2
        canvas.create_image(ox, oy, anchor="nw", image=photo)
        canvas._photo = photo   # prevent GC
    except ImportError:
        # No Pillow — try native PNG
        try:
            import tkinter as tk
            if data[:4] != b"\x89PNG":
                raise ValueError("not PNG")
            photo = tk.PhotoImage(data=data)
            pw, ph = photo.width(), photo.height()
            # subsample to fit
            factor = max(1, max(pw, ph) // size)
            if factor > 1:
                photo = photo.subsample(factor, factor)
            ox = (size - photo.width())  // 2
            oy = (size - photo.height()) // 2
            canvas.create_image(ox, oy, anchor="nw", image=photo)
            canvas._photo = photo
        except Exception:
            canvas.create_text(size // 2, size // 2,
                               text="♪\n\n(install Pillow\nfor JPEG support)",
                               fill="#475569",
                               font=("Segoe UI", 14),
                               justify="center")
    except Exception:
        canvas.create_text(size // 2, size // 2,
                           text="♪\n\nCould not render cover",
                           fill="#475569",
                           font=("Segoe UI", 14),
                           justify="center")


# ─────────────────────────────────────────────────────────────────────────────
# CLI test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cfg = Config()
    artist = sys.argv[1] if len(sys.argv) > 1 else "Radiohead"
    album  = sys.argv[2] if len(sys.argv) > 2 else "OK Computer"

    print(f"Testing ArtInfoLoader for: {artist} / {album}")
    print(f"  Last.fm key : {'set' if cfg.has('LASTFM_API_KEY') else 'NOT SET'}")
    print(f"  Discogs tok : {'set' if cfg.has('DISCOGS_TOKEN') else 'NOT SET'}")
    print()

    done = threading.Event()

    def show_cover(data):
        print(f"  Cover art  : {len(data)} bytes" if data else "  Cover art  : not found")

    def show_bio(text, source):
        print(f"  Bio source : {source}")
        print(f"  Bio ({len(text)} chars):\n  {text[:300]}…")
        done.set()

    loader = ArtInfoLoader(cfg)
    loader.load(artist=artist, album=album, on_cover=show_cover, on_bio=show_bio)
    done.wait(timeout=30)
