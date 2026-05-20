#!/usr/bin/env python3
# File: mpdpop_cache.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Description: Layered text/metadata cache for mpdpop.
#
#   Write path:  value → Redis  AND  SQLite (always persisted)
#   Read path:   Redis (fastest, in-memory)
#                  → pickle file (fast, no server needed)
#                  → SQLite via SQLAlchemy (always available)
#
#   Cover art intentionally NOT cached here — raw image files are faster.
#   Only text blobs (bio, tags, artist metadata) go through this cache.
#
# ENV / mpdpop.env keys consumed:
#   CACHE_REDIS_URL      redis://[:password@]host:port/db   (blank = skip Redis)
#   CACHE_PICKLE_DIR     directory for .pkl files           (blank = alongside DB)
#   CACHE_DB_URL         SQLAlchemy URL                     (default: sqlite:///mpdpop_cache.db)
#   CACHE_TTL_DAYS       integer days before expiry         (default: 30)
#   CACHE_PICKLE         1/true/yes to enable pickle layer  (default: true)
#
# License: MIT

from __future__ import annotations

import hashlib
import os
import pickle
import re
import tempfile
import time
from pathlib import Path
from typing import Any

# ── lazy imports (all optional except stdlib) ──────────────────────────────────
# redis, sqlalchemy — imported at first use so the module loads without them

try:
    from mpdpop_env import Config
except ImportError:
    class Config:  # type: ignore
        def __getitem__(self, k): return os.environ.get(k, "")
        def get(self, k, d=""): return os.environ.get(k, d)
        def int(self, k, fb=0):
            try: return int(os.environ.get(k, fb))
            except: return fb
        def bool(self, k, fb=False):
            v = os.environ.get(k, "").lower()
            return v in ("1","true","yes","on") if v else fb
        def has(self, k): return bool(os.environ.get(k,"").strip())


_SENTINEL = object()   # distinguish "not cached" from None / ""


# ─────────────────────────────────────────────────────────────────────────────
# Key helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_key(namespace: str, *parts: str) -> str:
    """
    Deterministic cache key.
    e.g. _make_key("bio", "Radiohead", "OK Computer")
         → "bio:radiohead|ok_computer:<md5>"
    """
    raw   = "|".join(p.lower().strip() for p in parts)
    slug  = re.sub(r"[^\w]", "_", raw)[:60]
    digest = hashlib.md5(raw.encode()).hexdigest()[:10]
    return f"{namespace}:{slug}:{digest}"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — Redis
# ─────────────────────────────────────────────────────────────────────────────

class _RedisLayer:
    """
    Wraps a redis.Redis connection.  All failures are silent — if Redis is
    down the other layers take over automatically.
    """
    def __init__(self, url: str):
        self._url  = url
        self._r    = None
        self._ok   = False
        self._connect()

    def _connect(self):
        try:
            import redis  # type: ignore
            self._r  = redis.Redis.from_url(self._url, decode_responses=False,
                                             socket_connect_timeout=2,
                                             socket_timeout=2)
            self._r.ping()
            self._ok = True
        except Exception:
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok

    def get(self, key: str) -> Any:
        if not self._ok:
            return _SENTINEL
        try:
            raw = self._r.get(key)
            if raw is None:
                return _SENTINEL
            return pickle.loads(raw)
        except Exception:
            return _SENTINEL

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if not self._ok:
            return
        try:
            self._r.setex(key, ttl_seconds, pickle.dumps(value))
        except Exception:
            pass

    def delete(self, key: str) -> None:
        if not self._ok:
            return
        try:
            self._r.delete(key)
        except Exception:
            pass

    def flush_namespace(self, namespace: str) -> int:
        """Delete all keys starting with 'namespace:'. Returns count."""
        if not self._ok:
            return 0
        try:
            keys = self._r.keys(f"{namespace}:*")
            if keys:
                return self._r.delete(*keys)
        except Exception:
            pass
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Pickle files
# ─────────────────────────────────────────────────────────────────────────────

class _PickleLayer:
    """
    One .pkl file per cache key, stored in a flat directory.
    Each file contains {"value": ..., "expires": unix_timestamp}.
    """
    def __init__(self, directory: Path):
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^\w:\-]", "_", key)[:120]
        return self._dir / f"{safe}.pkl"

    def get(self, key: str) -> Any:
        p = self._path(key)
        if not p.exists():
            return _SENTINEL
        try:
            data = pickle.loads(p.read_bytes())
            if time.time() > data.get("expires", 0):
                p.unlink(missing_ok=True)
                return _SENTINEL
            return data["value"]
        except Exception:
            return _SENTINEL

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        p = self._path(key)
        try:
            payload = {"value": value, "expires": time.time() + ttl_seconds}
            p.write_bytes(pickle.dumps(payload))
        except Exception:
            pass

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def flush_namespace(self, namespace: str) -> int:
        count = 0
        prefix = re.sub(r"[^\w:\-]", "_", namespace)
        for f in self._dir.glob(f"{prefix}*.pkl"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        return count

    def evict_expired(self) -> int:
        """Remove all expired pickle files. Call occasionally."""
        count = 0
        for f in self._dir.glob("*.pkl"):
            try:
                data = pickle.loads(f.read_bytes())
                if time.time() > data.get("expires", 0):
                    f.unlink()
                    count += 1
            except Exception:
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
        return count


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — SQLAlchemy / SQLite (always available fallback)
# ─────────────────────────────────────────────────────────────────────────────

_SA_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS mpdpop_cache (
    key         TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL,
    value_blob  BLOB NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_namespace ON mpdpop_cache (namespace);
CREATE INDEX IF NOT EXISTS idx_expires   ON mpdpop_cache (expires_at);
"""

class _SQLLayer:
    """
    SQLAlchemy-based cache layer.  Falls back to raw sqlite3 if SQLAlchemy
    is not installed (so the app always works).
    """
    def __init__(self, db_url: str):
        self._url  = db_url
        self._lock = __import__("threading").Lock()
        self._mode = None   # "sqlalchemy" | "sqlite3" | None
        self._engine = None
        self._conn   = None
        self._init_db()

    # ── init ─────────────────────────────────────────────────────────────────

    def _init_db(self):
        # Try SQLAlchemy first
        try:
            from sqlalchemy import create_engine, text  # type: ignore
            engine = create_engine(
                self._url,
                connect_args={"check_same_thread": False} if "sqlite" in self._url else {},
                pool_pre_ping=True,
            )
            with engine.connect() as conn:
                for stmt in _SA_TABLE_DDL.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(text(stmt))
                conn.commit()
            self._engine = engine
            self._mode   = "sqlalchemy"
            return
        except Exception:
            pass

        # Fallback: raw sqlite3
        try:
            import sqlite3
            db_path = self._url_to_sqlite_path(self._url)
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.executescript(_SA_TABLE_DDL)
            conn.commit()
            self._conn = conn
            self._mode = "sqlite3"
        except Exception:
            self._mode = None

    @staticmethod
    def _url_to_sqlite_path(url: str) -> Path:
        """Extract file path from sqlite:///path or use default."""
        if url.startswith("sqlite:///"):
            p = url[10:]
            return Path(p) if p else Path(tempfile.gettempdir()) / "mpdpop_cache.db"
        return Path(tempfile.gettempdir()) / "mpdpop_cache.db"

    @property
    def available(self) -> bool:
        return self._mode is not None

    # ── public interface ──────────────────────────────────────────────────────

    def get(self, key: str) -> Any:
        if not self.available:
            return _SENTINEL
        with self._lock:
            row = self._select_one(
                "SELECT value_blob, expires_at FROM mpdpop_cache WHERE key = ?",
                (key,)
            )
        if not row:
            return _SENTINEL
        value_blob, expires_at = row
        if time.time() > expires_at:
            self.delete(key)
            return _SENTINEL
        try:
            return pickle.loads(value_blob)
        except Exception:
            return _SENTINEL

    def set(self, key: str, value: Any, ttl_seconds: int,
            namespace: str = "default") -> None:
        if not self.available:
            return
        now     = time.time()
        expires = now + ttl_seconds
        blob    = pickle.dumps(value)
        with self._lock:
            self._execute(
                """INSERT INTO mpdpop_cache
                       (key, namespace, value_blob, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value_blob = excluded.value_blob,
                       created_at = excluded.created_at,
                       expires_at = excluded.expires_at""",
                (key, namespace, blob, now, expires)
            )

    def delete(self, key: str) -> None:
        if not self.available:
            return
        with self._lock:
            self._execute("DELETE FROM mpdpop_cache WHERE key = ?", (key,))

    def flush_namespace(self, namespace: str) -> int:
        if not self.available:
            return 0
        with self._lock:
            return self._execute(
                "DELETE FROM mpdpop_cache WHERE namespace = ?", (namespace,)
            )

    def evict_expired(self) -> int:
        if not self.available:
            return 0
        with self._lock:
            return self._execute(
                "DELETE FROM mpdpop_cache WHERE expires_at < ?", (time.time(),)
            )

    def stats(self) -> dict:
        """Return row counts per namespace."""
        if not self.available:
            return {}
        with self._lock:
            rows = self._select_all(
                "SELECT namespace, COUNT(*) FROM mpdpop_cache GROUP BY namespace"
            )
        return {r[0]: r[1] for r in rows} if rows else {}

    # ── internal query helpers ────────────────────────────────────────────────

    def _select_one(self, sql: str, params: tuple):
        try:
            if self._mode == "sqlalchemy":
                from sqlalchemy import text
                with self._engine.connect() as c:
                    row = c.execute(text(sql.replace("?", ":p")),
                                    self._named(params)).fetchone()
                return row
            else:
                return self._conn.execute(sql, params).fetchone()
        except Exception:
            return None

    def _select_all(self, sql: str, params: tuple = ()):
        try:
            if self._mode == "sqlalchemy":
                from sqlalchemy import text
                with self._engine.connect() as c:
                    return c.execute(text(sql.replace("?", ":p")),
                                     self._named(params)).fetchall()
            else:
                return self._conn.execute(sql, params).fetchall()
        except Exception:
            return []

    def _execute(self, sql: str, params: tuple) -> int:
        """Execute a write statement. Returns rowcount."""
        try:
            if self._mode == "sqlalchemy":
                from sqlalchemy import text
                with self._engine.begin() as c:
                    r = c.execute(text(sql.replace("?", ":p")),
                                  self._named(params))
                    return r.rowcount
            else:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur.rowcount
        except Exception:
            return 0

    @staticmethod
    def _named(params: tuple) -> dict:
        """Convert positional params to SQLAlchemy named params."""
        return {f"p{i}": v for i, v in enumerate(params)}

    def _select_one(self, sql: str, params: tuple):
        """Override to use indexed named params properly."""
        try:
            if self._mode == "sqlalchemy":
                from sqlalchemy import text
                named_sql = sql
                named_params: dict = {}
                i = 0
                while "?" in named_sql:
                    named_sql = named_sql.replace("?", f":p{i}", 1)
                    named_params[f"p{i}"] = params[i]
                    i += 1
                with self._engine.connect() as c:
                    return c.execute(text(named_sql), named_params).fetchone()
            else:
                return self._conn.execute(sql, params).fetchone()
        except Exception:
            return None

    def _select_all(self, sql: str, params: tuple = ()):
        try:
            if self._mode == "sqlalchemy":
                from sqlalchemy import text
                named_sql = sql
                named_params: dict = {}
                i = 0
                while "?" in named_sql:
                    named_sql = named_sql.replace("?", f":p{i}", 1)
                    named_params[f"p{i}"] = params[i]
                    i += 1
                with self._engine.connect() as c:
                    return c.execute(text(named_sql), named_params).fetchall()
            else:
                return self._conn.execute(sql, params).fetchall()
        except Exception:
            return []

    def _execute(self, sql: str, params: tuple) -> int:
        try:
            if self._mode == "sqlalchemy":
                from sqlalchemy import text
                named_sql = sql
                named_params: dict = {}
                i = 0
                while "?" in named_sql:
                    named_sql = named_sql.replace("?", f":p{i}", 1)
                    named_params[f"p{i}"] = params[i]
                    i += 1
                with self._engine.begin() as c:
                    r = c.execute(text(named_sql), named_params)
                    return r.rowcount
            else:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur.rowcount
        except Exception:
            return 0


# ─────────────────────────────────────────────────────────────────────────────
# Public: BioCache  (the one thing mpdpop_artinfo.py uses)
# ─────────────────────────────────────────────────────────────────────────────

class BioCache:
    """
    Layered read/write cache for artist biography text (and any other
    text metadata).

    Read order:   Redis → pickle → SQLite
    Write order:  Redis + pickle + SQLite  (all written on every cache miss)

    Usage:
        cache = BioCache(cfg)
        text  = cache.get("bio", artist)
        if text is None:
            text = expensive_fetch(artist)
            cache.set("bio", text, artist)
    """

    def __init__(self, cfg: Config):
        self.cfg       = cfg
        self._ttl      = cfg.int("CACHE_TTL_DAYS", 30) * 86400

        # ── Layer 1: Redis ────────────────────────────────────────────────────
        redis_url = cfg.get("CACHE_REDIS_URL", "").strip()
        self._redis: _RedisLayer | None = (
            _RedisLayer(redis_url) if redis_url else None
        )

        # ── Layer 2: Pickle ───────────────────────────────────────────────────
        use_pickle = cfg.bool("CACHE_PICKLE", True) if hasattr(cfg, "bool") else True
        if use_pickle:
            pickle_dir_raw = cfg.get("CACHE_PICKLE_DIR", "").strip()
            if pickle_dir_raw:
                pickle_dir = Path(pickle_dir_raw).expanduser()
            else:
                pickle_dir = self._default_db_path().parent / "mpdpop_pickle"
            self._pickle: _PickleLayer | None = _PickleLayer(pickle_dir)
        else:
            self._pickle = None

        # ── Layer 3: SQLAlchemy / SQLite ──────────────────────────────────────
        db_url = cfg.get("CACHE_DB_URL", "").strip()
        if not db_url:
            db_url = f"sqlite:///{self._default_db_path()}"
        self._sql = _SQLLayer(db_url)

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, namespace: str, *key_parts: str) -> str | None:
        """
        Return cached string value or None (cache miss).
        Promotes value upward through layers on hit (Redis warm-up).
        """
        key = _make_key(namespace, *key_parts)

        # L1: Redis
        if self._redis and self._redis.available:
            val = self._redis.get(key)
            if val is not _SENTINEL:
                return val

        # L2: Pickle
        if self._pickle:
            val = self._pickle.get(key)
            if val is not _SENTINEL:
                # promote to Redis
                if self._redis and self._redis.available:
                    self._redis.set(key, val, self._ttl)
                return val

        # L3: SQLite
        val = self._sql.get(key)
        if val is not _SENTINEL:
            # promote to pickle + Redis
            if self._pickle:
                self._pickle.set(key, val, self._ttl)
            if self._redis and self._redis.available:
                self._redis.set(key, val, self._ttl)
            return val

        return None  # true miss

    def set(self, namespace: str, value: str, *key_parts: str) -> None:
        """Write value to all available layers."""
        key = _make_key(namespace, *key_parts)
        if self._redis and self._redis.available:
            self._redis.set(key, value, self._ttl)
        if self._pickle:
            self._pickle.set(key, value, self._ttl)
        self._sql.set(key, value, self._ttl, namespace=namespace)

    def invalidate(self, namespace: str, *key_parts: str) -> None:
        """Remove a single entry from all layers."""
        key = _make_key(namespace, *key_parts)
        if self._redis and self._redis.available:
            self._redis.delete(key)
        if self._pickle:
            self._pickle.delete(key)
        self._sql.delete(key)

    def flush(self, namespace: str) -> dict[str, int]:
        """Remove all entries for a namespace. Returns counts per layer."""
        counts: dict[str, int] = {}
        if self._redis and self._redis.available:
            counts["redis"] = self._redis.flush_namespace(namespace)
        if self._pickle:
            counts["pickle"] = self._pickle.flush_namespace(namespace)
        counts["sql"] = self._sql.flush_namespace(namespace)
        return counts

    def evict_expired(self) -> dict[str, int]:
        """Prune expired entries from pickle + SQL (Redis TTL is automatic)."""
        counts: dict[str, int] = {}
        if self._pickle:
            counts["pickle"] = self._pickle.evict_expired()
        counts["sql"] = self._sql.evict_expired()
        return counts

    def stats(self) -> dict:
        """Return diagnostic info about cache state."""
        return {
            "redis":   "up" if (self._redis and self._redis.available) else "unavailable",
            "pickle":  str(self._pickle._dir) if self._pickle else "disabled",
            "sql":     self._sql._url if self._sql.available else "unavailable",
            "sql_mode": self._sql._mode,
            "sql_rows": self._sql.stats(),
            "ttl_days": self._ttl // 86400,
        }

    # ── internal ──────────────────────────────────────────────────────────────

    def _default_db_path(self) -> Path:
        """Default SQLite file: ~/.local/share/mpdpop/cache.db or temp."""
        candidates = [
            Path.home() / ".local" / "share" / "mpdpop",
            Path(tempfile.gettempdir()) / "mpdpop",
        ]
        for p in candidates:
            try:
                p.mkdir(parents=True, exist_ok=True)
                return p / "cache.db"
            except OSError:
                pass
        return Path(tempfile.gettempdir()) / "mpdpop_cache.db"


# ─────────────────────────────────────────────────────────────────────────────
# CLI diagnostics
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    cfg   = Config()
    cache = BioCache(cfg)

    if "--stats" in sys.argv:
        import json
        print(json.dumps(cache.stats(), indent=2, default=str))
        sys.exit(0)

    if "--evict" in sys.argv:
        counts = cache.evict_expired()
        print(f"Evicted: {counts}")
        sys.exit(0)

    if "--flush" in sys.argv:
        ns = sys.argv[sys.argv.index("--flush") + 1] if "--flush" in sys.argv[:-1] else "bio"
        counts = cache.flush(ns)
        print(f"Flushed namespace '{ns}': {counts}")
        sys.exit(0)

    # smoke test
    print("BioCache smoke test")
    print(f"  Stats: {cache.stats()}")
    key_parts = ("Test Artist",)

    val = cache.get("bio", *key_parts)
    print(f"  Initial get: {val!r}")

    cache.set("bio", "This is a test biography.", *key_parts)
    val = cache.get("bio", *key_parts)
    print(f"  After set:   {val!r}")

    cache.invalidate("bio", *key_parts)
    val = cache.get("bio", *key_parts)
    print(f"  After invalidate: {val!r}")
    print("Done.")
