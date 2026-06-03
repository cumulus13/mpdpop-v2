#!/usr/bin/env python3
# File: mpdpop_overlay.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Description: CD Art Display-style always-on-top desktop overlay for MPD.
#
#   Features:
#     • Square album art dominates the widget (size configurable)
#     • Controls fade in on mouse hover: ⏮  ⏹  ⏯  ⏭  🔊
#     • Thin interactive progress bar (click to seek, wheel to skip ±5s)
#     • Title + artist text scrolls across the bottom
#     • Mirror/reflection of cover beneath the widget
#     • Volume wheel anywhere on widget
#     • Draggable (click-drag anywhere)
#     • Double-click → open main mpdpop playlist popup
#     • Right-click context menu
#     • Borderless, always on top, configurable opacity
#     • Polls MPD every second for live position + track changes
#     • Cover art cache shared with mpdpop (same COVER_CACHE_DIR)
#     • Smooth hover fade in/out
#     • System tray icon (Windows/Linux)
#     • OVERLAY_* config keys in mpdpop.env
#
# Run standalone:   python3 mpdpop_overlay.py
# Or alongside:    python3 mpdpop.py  (if OVERLAY_AUTO_START = true)
#
# License: MIT

from __future__ import annotations

import os
import sys
import math
import socket
import threading
import time
import subprocess
import shutil
from pathlib import Path

# ── optional companion modules ────────────────────────────────────────────────
try:
    from mpdpop_env import Config
    _CFG = Config()
except ImportError:
    class _FallbackConfig:
        def get(self, k, d=""): return os.environ.get(k, d)
        def int(self, k, fb=0):
            try: return int(os.environ.get(k, fb))
            except: return fb
        def bool(self, k, fb=False):
            v = os.environ.get(k, "").lower()
            return v in ("1","true","yes","on") if v else fb
        def has(self, k): return bool(os.environ.get(k,"").strip())
        def cover_cache_dir(self):
            import tempfile
            p = Path(tempfile.gettempdir()) / "mpdpop_covers"
            p.mkdir(parents=True, exist_ok=True)
            return p
    _CFG = _FallbackConfig()

try:
    from mpdpop_artinfo import CoverArtFetcher
    _HAS_ARTINFO = True
except ImportError:
    _HAS_ARTINFO = False
    CoverArtFetcher = None


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(key: str, default: str) -> str:
    return _CFG.get(key, default).strip()

def _cfgi(key: str, default: int) -> int:
    return _CFG.int(key, default)

def _cfgf(key: str, default: float) -> float:
    try: return float(_CFG.get(key, str(default)))
    except: return default


# ─────────────────────────────────────────────────────────────────────────────
# MPD mini-client  (self-contained, no dependency on mpdpop.py)
# ─────────────────────────────────────────────────────────────────────────────

class _MPD:
    def __init__(self):
        self.host     = _cfg("MPD_HOST", "127.0.0.1")
        self.port     = _cfgi("MPD_PORT", 6600)
        self.password = _cfg("MPD_PASSWORD", "")
        self.timeout  = _cfgi("MPD_TIMEOUT", 5)

    def _conn(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect((self.host, self.port))
        buf = b""
        while not buf.endswith(b"\n"):
            c = s.recv(1); buf += c
        if self.password:
            s.sendall(f'password "{self.password}"\n'.encode())
            self._read(s)
        return s

    def _read(self, s) -> str:
        r = b""
        while True:
            c = s.recv(4096)
            if not c: break
            r += c
            if b"\nOK\n" in r or b"\nACK" in r or r.endswith(b"OK\n"): break
        return r.decode(errors="replace").strip()

    def _cmd(self, cmd: str) -> str:
        try:
            s = self._conn()
            s.sendall((cmd + "\n").encode())
            r = self._read(s); s.close(); return r
        except Exception as e:
            return f"ERR {e}"

    def status(self) -> dict:
        d = {}
        for line in self._cmd("status").split("\n"):
            if ": " in line:
                k, _, v = line.partition(": ")
                d[k.strip()] = v.strip()
        return d

    def currentsong(self) -> dict:
        d = {}
        for line in self._cmd("currentsong").split("\n"):
            if ": " in line:
                k, _, v = line.partition(": ")
                d[k.strip()] = v.strip()
        return d

    def play(self):    self._cmd("play")
    def pause(self):   self._cmd("pause")
    def stop(self):    self._cmd("stop")
    def next(self):    self._cmd("next")
    def previous(self): self._cmd("previous")

    def toggle_pause(self, state: str):
        """state: 'play' | 'pause' | 'stop'"""
        if state == "play":
            self._cmd("pause 1")
        else:
            self._cmd("play")

    def seek(self, seconds: float):
        self._cmd(f"seekcur {seconds:.1f}")

    def seek_to(self, elapsed: float):
        self._cmd(f"seekcur {elapsed:.1f}")

    def volume(self, vol: int):
        self._cmd(f"setvol {max(0, min(100, vol))}")

    def get_volume(self) -> int:
        try: return int(self.status().get("volume", "50"))
        except: return 50

    def toggle_repeat(self):
        st = self.status()
        v = "0" if st.get("repeat","0") == "1" else "1"
        self._cmd(f"repeat {v}")

    def toggle_random(self):
        st = self.status()
        v = "0" if st.get("random","0") == "1" else "1"
        self._cmd(f"random {v}")


# ─────────────────────────────────────────────────────────────────────────────
# Cover art loader  (threaded, cached)
# ─────────────────────────────────────────────────────────────────────────────

class _CoverLoader:
    def __init__(self):
        self._cache_dir = _CFG.cover_cache_dir()
        self._lock      = threading.Lock()
        self._current   = ""   # artist|album key of what's loading

    def fetch(self, song: dict, on_done, root=None):
        """
        Start background fetch. on_done(bytes|None) is called on the
        Tkinter main thread if root is provided, otherwise directly.
        """
        artist = song.get("Artist", "")
        album  = song.get("Album",  "")
        title  = song.get("Title",  "")
        file_  = song.get("file",   "")
        key    = f"{artist}|{album}"

        with self._lock:
            if self._current == key:
                return   # already loading / loaded this one
            self._current = key

        def _worker():
            data = None
            try:
                if _HAS_ARTINFO:
                    fetcher = CoverArtFetcher(_CFG)
                    data = fetcher.fetch(
                        artist, album, title, file_,
                        _cfg("MPD_HOST", "127.0.0.1"),
                        _cfgi("MPD_PORT", 6600),
                    )
                else:
                    import hashlib, re as _re
                    slug = _re.sub(r"[^\w]", "_",
                                   f"{artist}{album}".lower())[:40]
                    digest = hashlib.md5(
                        f"{artist}|{album}".encode()).hexdigest()[:10]
                    for ext in ("jpg", "png", "gif"):
                        p = self._cache_dir / f"{slug}_{digest}.{ext}"
                        if p.exists():
                            data = p.read_bytes()
                            break
            except Exception:
                pass
            # Always dispatch to main thread
            if root is not None:
                root.after(0, lambda: on_done(data))
            else:
                on_done(data)

        threading.Thread(target=_worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Image helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_photo(data: bytes, size: int, tk_root=None):
    """Return (PhotoImage, PIL_Image_or_None, pil_available:bool)."""
    try:
        from PIL import Image, ImageTk
        import io
        img   = Image.open(io.BytesIO(data)).convert("RGBA")
        img   = img.resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        return photo, img, True
    except ImportError:
        pass
    except Exception:
        pass
    # Tkinter native PNG fallback (no Pillow)
    try:
        import tkinter as tk
        if data[:4] == b"\x89PNG":
            native = tk.PhotoImage(data=data)
            pw, ph = native.width(), native.height()
            factor = max(1, max(pw, ph) // size)
            if factor > 1:
                native = native.subsample(factor, factor)
            return native, None, False
    except Exception:
        pass
    return None, None, False


def _make_reflection(pil_img, size: int, height: int = 60):
    """Return a PhotoImage of a faded upside-down reflection."""
    try:
        from PIL import Image, ImageTk, ImageDraw
        import io
        ref = pil_img.crop((0, size - height, size, size)).transpose(
            Image.FLIP_TOP_BOTTOM).convert("RGBA")
        # gradient alpha mask
        mask = Image.new("L", (size, height))
        draw = ImageDraw.Draw(mask)
        for y in range(height):
            alpha = int(120 * (1 - y / height))
            draw.line([(0, y), (size, y)], fill=alpha)
        ref.putalpha(mask)
        return ImageTk.PhotoImage(ref)
    except Exception:
        return None


def _make_rounded(pil_img, size: int, radius: int = 14):
    """Return PIL image with rounded corners."""
    try:
        from PIL import Image, ImageDraw
        out  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        mask = Image.new("L",    (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
        out.paste(pil_img.convert("RGBA"), (0, 0), mask)
        return out
    except Exception:
        return pil_img


# ─────────────────────────────────────────────────────────────────────────────
# Text scroller helper
# ─────────────────────────────────────────────────────────────────────────────

class _Scroller:
    """Scrolls text left when it's wider than the canvas."""
    def __init__(self, canvas, tag: str, x: int, y: int,
                 width: int, font, fill: str, speed: int = 1):
        self._c     = canvas
        self._tag   = tag
        self._x     = x
        self._y     = y
        self._w     = width
        self._font  = font
        self._fill  = fill
        self._speed = speed
        self._text  = ""
        self._pos   = x
        self._tw    = 0
        self._job   = None
        self._pause = 0

    def set_text(self, text: str):
        if text == self._text:
            return
        self._text = text
        self._pos  = self._x
        self._pause = 40   # frames to pause at start
        self._c.delete(self._tag)
        self._c.create_text(self._x, self._y, text=text,
                            font=self._font, fill=self._fill,
                            anchor="nw", tags=self._tag)
        # measure text width
        try:
            self._tw = self._c.bbox(self._tag)[2] - self._c.bbox(self._tag)[0]
        except Exception:
            self._tw = 0

    def tick(self):
        if not self._text or self._tw <= self._w:
            return  # no scroll needed
        if self._pause > 0:
            self._pause -= 1
            return
        self._pos -= self._speed
        if self._pos < self._x - self._tw - 20:
            self._pos  = self._x + self._w
            self._pause = 30
        self._c.coords(self._tag, self._pos, self._y)


# ─────────────────────────────────────────────────────────────────────────────
# Main overlay widget
# ─────────────────────────────────────────────────────────────────────────────

class MPDOverlay:
    """
    CD Art Display-style desktop overlay.

    Config keys (all in mpdpop.env):
      OVERLAY_SIZE          square cover size px       (default 220)
      OVERLAY_X             initial X position         (default -1 = centre)
      OVERLAY_Y             initial Y position         (default -1 = centre)
      OVERLAY_OPACITY       window alpha 0.0–1.0       (default 0.92)
      OVERLAY_REFLECTION    show mirror below cover    (default true)
      OVERLAY_POLL_MS       MPD poll interval ms       (default 1000)
      OVERLAY_FONT          font name                  (default auto)
      OVERLAY_CONTROLS_SIZE control button size px     (default 32)
      OVERLAY_ALWAYS_ON_TOP pin above other windows    (default true)
      OVERLAY_AUTO_START    start with mpdpop.py       (default false)
      OVERLAY_CORNER_RADIUS rounded cover corners px   (default 12)
    """

    # ── colours ──────────────────────────────────────────────────────────────
    C_BG         = "#0a0a0a"
    C_COVER_BG   = "#1a1a2e"
    C_PBAR_BG    = "#1e293b"
    C_PBAR_FG    = "#3b82f6"
    C_PBAR_LIVE  = "#60a5fa"
    C_TEXT_TITLE = "#f1f5f9"
    C_TEXT_ART   = "#94a3b8"
    C_BTN_NORM   = "#ffffff"
    C_BTN_HOVER  = "#60a5fa"
    C_BTN_BG     = "#0f172a"
    C_OVERLAY    = "#000000"

    PBAR_H    = 4      # progress bar height px
    TEXT_PAD  = 6      # padding around text area
    CTRL_H    = 48     # controls overlay height
    FADE_STEP = 0.07   # alpha per frame for hover fade

    def __init__(self, cfg=None):
        self.cfg   = cfg or _CFG
        self._mpd  = _MPD()
        self._covl = _CoverLoader()

        # sizing
        self._sz     = _cfgi("OVERLAY_SIZE",          220)
        self._radius = _cfgi("OVERLAY_CORNER_RADIUS",  12)
        self._ctrl_sz = _cfgi("OVERLAY_CONTROLS_SIZE", 32)
        self._opacity = _cfgf("OVERLAY_OPACITY",       0.92)
        self._reflect = self.cfg.get("OVERLAY_REFLECTION","true").lower() \
                        not in ("0","false","no","off")
        self._poll_ms = _cfgi("OVERLAY_POLL_MS",      1000)
        self._aot     = self.cfg.get("OVERLAY_ALWAYS_ON_TOP","true").lower() \
                        not in ("0","false","no","off")

        # font
        fn = _cfg("OVERLAY_FONT", "")
        if not fn:
            fn = "Segoe UI" if sys.platform == "win32" else \
                 "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans"
        self._font = fn
        # All font sizes derived from one base size
        self._fs       = _cfgi("OVERLAY_FONT_SIZE", 10)   # base font size
        self._fs_title  = self._fs                         # track title
        self._fs_artist = max(7, self._fs - 2)             # artist (slightly smaller)
        self._fs_note   = max(16, self._sz // 4)           # ♪ placeholder
        self._fs_btn    = max(10, self._ctrl_sz // 2)      # control buttons
        self._fs_vol    = max(7,  self._fs - 3)            # volume badge
        self._fs_badge  = max(7,  self._fs - 3)            # status badges
        self._fs_time   = max(6,  self._fs - 4)            # pbar time tooltip

        # state
        self._state         = {}    # last MPD status dict
        self._song          = {}    # last currentsong dict
        self._cover_data    = None  # raw bytes
        self._cover_photo   = None  # PhotoImage (prevent GC)
        self._cover_pil     = None  # PIL Image
        self._reflect_photo = None
        self._hover_alpha   = 0.0   # 0=hidden 1=fully visible controls
        self._hover_active  = False
        self._drag_x        = 0
        self._drag_y        = 0
        self._vol_tooltip_job = None
        self._scroll_job    = None

        # layout constants
        self._ref_h  = 60 if self._reflect else 0
        self._txt_h  = max(36, self._fs_title + self._fs_artist + 18)
        self._total_h = self._sz + self._PBAR_H() + self._txt_h + self._ref_h
        self._total_w = self._sz

        self._root      = None
        self._canvas    = None
        self._scroller  = None

    def _PBAR_H(self): return self.PBAR_H

    # ── build ─────────────────────────────────────────────────────────────────

    def run(self):
        import tkinter as tk
        root = tk.Tk()
        self._root = root

        root.overrideredirect(True)     # borderless
        root.attributes("-topmost", self._aot)
        root.attributes("-alpha",   self._opacity)
        root.configure(bg=self.C_BG)

        # transparent background on Windows
        if sys.platform == "win32":
            try:
                root.attributes("-transparentcolor", self.C_BG)
            except Exception:
                pass

        # position
        ox = _cfgi("OVERLAY_X", -1)
        oy = _cfgi("OVERLAY_Y", -1)
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        if ox < 0: ox = sw - self._total_w - 40
        if oy < 0: oy = sh // 2 - self._total_h // 2
        root.geometry(f"{self._total_w}x{self._total_h}+{ox}+{oy}")

        # canvas covers everything
        cv = tk.Canvas(root,
                       width=self._total_w, height=self._total_h,
                       bg=self.C_BG, highlightthickness=0, bd=0)
        cv.pack()
        self._canvas = cv

        self._build_canvas()
        self._bind_events()
        self._build_scrollers()

        # Escape key quits the overlay
        root.bind("<Escape>",    lambda _: root.destroy())
        root.bind("<KeyPress-q>", lambda _: root.destroy())
        # M key toggles mirror/reflection
        root.bind("<KeyPress-m>", lambda _: self._toggle_mirror())
        root.bind("<KeyPress-M>", lambda _: self._toggle_mirror())

        # start polling
        root.after(100, self._poll)
        root.after(50,  self._animate)

        # system tray (best-effort)
        self._start_tray()

        root.mainloop()

    # ── canvas layout ─────────────────────────────────────────────────────────

    def _build_canvas(self):
        cv = self._canvas
        sz = self._sz

        # ── cover area ────────────────────────────────────────────────────────
        cv.create_rectangle(0, 0, sz, sz,
                            fill=self.C_COVER_BG, outline="",
                            tags="cover_bg")
        # placeholder note
        cv.create_text(sz // 2, sz // 2, text="♪",
                       fill="#1e293b", font=(self._font, self._fs_note),
                       tags="cover_note")
        # cover image (added dynamically)

        # ── progress bar ─────────────────────────────────────────────────────
        py = sz
        cv.create_rectangle(0, py, sz, py + self.PBAR_H,
                            fill=self.C_PBAR_BG, outline="", tags="pbar_bg")
        cv.create_rectangle(0, py, 0, py + self.PBAR_H,
                            fill=self.C_PBAR_FG, outline="", tags="pbar_fill")
        # hover glow line on pbar
        cv.create_rectangle(0, py, 0, py + self.PBAR_H,
                            fill=self.C_PBAR_LIVE, outline="",
                            tags="pbar_glow", state="hidden")
        # pbar time tooltip
        cv.create_text(4, py - 4, text="",
                       font=(self._font, self._fs_time), fill="#64748b",
                       anchor="sw", tags="pbar_time")

        # ── text area ─────────────────────────────────────────────────────────
        ty = sz + self.PBAR_H
        cv.create_rectangle(0, ty, sz, ty + self._txt_h,
                            fill=self.C_BG, outline="", tags="text_bg")

        # ── reflection area ───────────────────────────────────────────────────
        if self._reflect:
            ry = sz + self.PBAR_H + self._txt_h
            cv.create_rectangle(0, ry, sz, ry + self._ref_h,
                                fill=self.C_BG, outline="", tags="reflect_bg")
            # reflection image added dynamically

        # ── controls overlay (hidden, shown on hover) ─────────────────────────
        # semi-transparent dark overlay across the cover
        cv.create_rectangle(0, sz - self.CTRL_H, sz, sz,
                            fill=self.C_BTN_BG, outline="",
                            tags="ctrl_bg", state="hidden")

        # button positions: centred row in CTRL_H band
        btn_y    = sz - self.CTRL_H // 2
        icons    = ["⏮", "⏹", "⏯", "⏭"]
        self._btn_tags = []
        btn_count = len(icons)
        spacing  = sz // (btn_count + 1)
        for i, icon in enumerate(icons):
            bx = spacing * (i + 1)
            tag = f"btn_{i}"
            # button background circle
            r = self._ctrl_sz // 2
            cv.create_oval(bx - r, btn_y - r, bx + r, btn_y + r,
                           fill=self.C_BTN_BG, outline="#334155",
                           tags=(tag + "_bg", "ctrl_elem"), state="hidden")
            cv.create_text(bx, btn_y,
                           text=icon,
                           font=(self._font, self._fs_btn),
                           fill=self.C_BTN_NORM,
                           tags=(tag, "ctrl_elem"), state="hidden")
            self._btn_tags.append((tag, bx, btn_y, icon))

        # volume badge (top-right, shown on hover)
        cv.create_rectangle(sz - 44, 4, sz - 4, 20,
                            fill="#0f172a", outline="#1e293b",
                            tags=("vol_bg","ctrl_elem"), state="hidden")
        cv.create_text(sz - 24, 12, text="50%",
                       font=(self._font, self._fs_vol), fill="#64748b",
                       tags=("vol_text","ctrl_elem"), state="hidden")

        # ── status badges (top-left corner, tiny, hover only) ─────────────────
        cv.create_text(6, 10, text="",
                       font=(self._font, self._fs_badge, "bold"), fill="#3b82f6",
                       anchor="nw", tags=("status_badges","ctrl_elem"),
                       state="hidden")

    def _build_scrollers(self):
        sz    = self._sz
        ty    = sz + self.PBAR_H
        pad   = self.TEXT_PAD

        self._scroller_title = _Scroller(
            self._canvas, "scroll_title",
            x=pad, y=ty + 5,
            width=sz - pad * 2,
            font=(self._font, self._fs_title, "bold"),
            fill=self.C_TEXT_TITLE,
            speed=1,
        )
        self._scroller_artist = _Scroller(
            self._canvas, "scroll_artist",
            x=pad, y=ty + self._fs_title + 8,
            width=sz - pad * 2,
            font=(self._font, self._fs_artist),
            fill=self.C_TEXT_ART,
            speed=1,
        )

    # ── event binding ─────────────────────────────────────────────────────────

    def _bind_events(self):
        cv = self._canvas
        sz = self._sz

        # drag
        cv.bind("<ButtonPress-1>",   self._on_press)
        cv.bind("<B1-Motion>",       self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Double-Button-1>", self._on_double_click)

        # hover
        cv.bind("<Enter>",           self._on_enter)
        cv.bind("<Leave>",           self._on_leave)
        cv.bind("<Motion>",          self._on_motion)

        # scroll wheel — volume
        cv.bind("<MouseWheel>",      self._on_wheel)       # Windows/macOS
        cv.bind("<Button-4>",        self._on_wheel)       # Linux scroll up
        cv.bind("<Button-5>",        self._on_wheel)       # Linux scroll down

        # progress bar click/drag
        pbar_y = sz
        def _in_pbar(y): return pbar_y <= y <= pbar_y + self.PBAR_H + 4
        def _pbar_click(e):
            if _in_pbar(e.y):
                self._seek_to_x(e.x)
                return "break"
        cv.bind("<ButtonPress-1>",   lambda e: (_pbar_click(e), self._on_press(e)))
        cv.bind("<B1-Motion>",       lambda e: (_pbar_click(e) if _in_pbar(e.y)
                                                else self._on_drag(e)))

        # right-click context menu
        cv.bind("<Button-3>",        self._show_context)
        cv.bind("<Button-2>",        self._show_context)   # macOS middle

        # button clicks (wired after tags are known)
        actions = [
            self._mpd.previous,
            self._mpd.stop,
            lambda: self._mpd.toggle_pause(
                self._state.get("state","stop")),
            self._mpd.next,
        ]
        for i, (tag, bx, by, icon) in enumerate(self._btn_tags):
            action = actions[i]
            cv.tag_bind(tag,      "<Button-1>", lambda e, a=action: (a(), "break"))
            cv.tag_bind(tag+"_bg","<Button-1>", lambda e, a=action: (a(), "break"))
            cv.tag_bind(tag,      "<Enter>",
                        lambda e, t=tag: cv.itemconfig(t, fill=self.C_BTN_HOVER))
            cv.tag_bind(tag,      "<Leave>",
                        lambda e, t=tag: cv.itemconfig(t, fill=self.C_BTN_NORM))

    def _on_press(self, e):
        self._drag_x = e.x_root - self._root.winfo_x()
        self._drag_y = e.y_root - self._root.winfo_y()
        self._drag_start = (e.x, e.y)

    def _on_drag(self, e):
        x = e.x_root - self._drag_x
        y = e.y_root - self._drag_y
        self._root.geometry(f"+{x}+{y}")

    def _on_release(self, e):
        pass

    def _on_double_click(self, e):
        """Open the main mpdpop playlist popup."""
        threading.Thread(
            target=lambda: subprocess.Popen(
                [sys.executable, str(Path(__file__).parent / "mpdpop.py")]),
            daemon=True,
        ).start()

    def _on_enter(self, e):
        self._hover_active = True

    def _on_leave(self, e):
        self._hover_active = False

    def _on_motion(self, e):
        self._hover_active = True
        # pbar hover glow
        sz = self._sz
        pbar_y = sz
        if pbar_y <= e.y <= pbar_y + self.PBAR_H + 2:
            try:
                dur = float(self._state.get("duration", 0))
                if dur > 0:
                    t = e.x / self._sz * dur
                    ts = f"{int(t//60)}:{int(t%60):02d}"
                    self._canvas.itemconfig("pbar_glow", state="normal")
                    self._canvas.coords("pbar_glow",
                                        0, pbar_y, e.x, pbar_y + self.PBAR_H)
                    self._canvas.itemconfig("pbar_time", text=ts)
            except Exception:
                pass
        else:
            self._canvas.itemconfig("pbar_glow", state="hidden")
            self._canvas.itemconfig("pbar_time", text="")

    def _on_wheel(self, e):
        try:
            vol = self._mpd.get_volume()
            if e.num == 4 or (hasattr(e, "delta") and e.delta > 0):
                vol = min(100, vol + 5)
            else:
                vol = max(0,   vol - 5)
            self._mpd.volume(vol)
            self._canvas.itemconfig("vol_text", text=f"{vol}%")
            # show vol badge briefly
            self._canvas.itemconfig("vol_bg",   state="normal")
            self._canvas.itemconfig("vol_text", state="normal")
            if self._vol_tooltip_job:
                self._root.after_cancel(self._vol_tooltip_job)
            self._vol_tooltip_job = self._root.after(
                1500, lambda: (
                    self._canvas.itemconfig("vol_bg",   state="hidden"),
                    self._canvas.itemconfig("vol_text", state="hidden"),
                ) if not self._hover_active else None
            )
        except Exception:
            pass

    def _seek_to_x(self, x: int):
        try:
            dur = float(self._state.get("duration", 0))
            if dur > 0:
                frac = max(0.0, min(1.0, x / self._sz))
                self._mpd.seek_to(frac * dur)
        except Exception:
            pass

    def _show_context(self, e):
        import tkinter as tk
        menu = tk.Menu(self._root, tearoff=0,
                       bg="#0f172a", fg="#cbd5e1",
                       activebackground="#1e40af",
                       font=(self._font, 9))
        st = self._state.get("state","stop")
        menu.add_command(label="⏯  Play/Pause",
                         command=lambda: self._mpd.toggle_pause(st))
        menu.add_command(label="⏮  Previous",  command=self._mpd.previous)
        menu.add_command(label="⏭  Next",       command=self._mpd.next)
        menu.add_command(label="⏹  Stop",       command=self._mpd.stop)
        menu.add_separator()
        menu.add_command(label="⟳  Toggle Repeat",
                         command=self._mpd.toggle_repeat)
        menu.add_command(label="⤮  Toggle Random",
                         command=self._mpd.toggle_random)
        menu.add_separator()
        mirror_label = "🪞  Hide Mirror" if self._reflect else "🪞  Show Mirror"
        menu.add_command(label=mirror_label,
                         command=self._toggle_mirror)
        menu.add_separator()
        menu.add_command(label="📋  Open Playlist",
                         command=self._on_double_click)
        menu.add_separator()
        menu.add_command(label="✕  Close Overlay",
                         command=self._root.destroy)
        menu.tk_popup(e.x_root, e.y_root)

    # ── polling ───────────────────────────────────────────────────────────────

    def _poll(self):
        def _worker():
            try:
                status = self._mpd.status()
                song   = self._mpd.currentsong()
                self._root.after(0, lambda: self._update(status, song))
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()
        self._root.after(self._poll_ms, self._poll)

    def _update(self, status: dict, song: dict):
        changed_song = (song.get("file","") != self._song.get("file",""))
        self._state  = status
        self._song   = song

        # progress bar
        try:
            elapsed  = float(status.get("elapsed",  0))
            duration = float(status.get("duration", 0))
            frac = elapsed / duration if duration > 0 else 0.0
        except Exception:
            frac = 0.0
        pbar_w = int(self._sz * frac)
        py = self._sz
        self._canvas.coords("pbar_fill", 0, py, pbar_w, py + self.PBAR_H)

        # play/pause icon
        playing = status.get("state","stop") == "play"
        for i, (tag, bx, by, icon) in enumerate(self._btn_tags):
            if icon == "⏯":
                new_icon = "⏸" if playing else "▶"
                self._canvas.itemconfig(tag, text=new_icon)

        # volume badge
        try:
            vol = int(status.get("volume", 50))
            self._canvas.itemconfig("vol_text", text=f"{vol}%")
        except Exception:
            pass

        # status badges text
        badges = []
        if status.get("repeat","0")  == "1": badges.append("⟳")
        if status.get("random","0")  == "1": badges.append("⤮")
        if status.get("single","0")  == "1": badges.append("①")
        if status.get("consume","0") == "1": badges.append("⌫")
        self._canvas.itemconfig("status_badges", text=" ".join(badges))

        # text
        title  = song.get("Title",  song.get("Name", "Unknown"))
        artist = song.get("Artist", "")
        self._scroller_title.set_text(title)
        self._scroller_artist.set_text(artist)

        # cover art — reload only when track changes
        if changed_song:
            self._cover_data  = None
            self._cover_photo = None
            self._cover_pil   = None
            self._canvas.delete("cover_img")
            self._canvas.delete("reflect_img")
            self._canvas.itemconfig("cover_note", state="normal")
            # pass root so callback is dispatched on the main thread
            self._covl.fetch(song, self._on_cover_ready, root=self._root)

    def _on_cover_ready(self, data: bytes | None):
        if not data:
            return
        self._cover_data = data
        photo, pil_img, has_pil = _load_photo(data, self._sz, self._root)
        if not photo:
            return

        if has_pil and pil_img:
            pil_rounded = _make_rounded(pil_img, self._sz, self._radius)
            try:
                from PIL import ImageTk
                photo = ImageTk.PhotoImage(pil_rounded)
            except Exception:
                pass
            self._cover_pil   = pil_rounded
            self._cover_photo = photo
        else:
            self._cover_photo = photo

        self._canvas.delete("cover_img")
        self._canvas.create_image(0, 0, anchor="nw", image=self._cover_photo,
                                  tags="cover_img")
        self._canvas.itemconfig("cover_note", state="hidden")
        self._canvas.tag_raise("ctrl_bg")
        for tag, *_ in self._btn_tags:
            self._canvas.tag_raise(tag + "_bg")
            self._canvas.tag_raise(tag)
        self._canvas.tag_raise("ctrl_elem")

        # reflection
        if self._reflect and self._cover_pil:
            ref_photo = _make_reflection(self._cover_pil, self._sz, self._ref_h)
            if ref_photo:
                self._reflect_photo = ref_photo
                self._canvas.delete("reflect_img")
                ry = self._sz + self.PBAR_H + self._txt_h
                self._canvas.create_image(0, ry, anchor="nw",
                                          image=self._reflect_photo,
                                          tags="reflect_img")

        # update system tray
        self._update_tray(data)

    # ── animation loop ────────────────────────────────────────────────────────

    def _animate(self):
        # hover fade
        target = 1.0 if self._hover_active else 0.0
        if abs(self._hover_alpha - target) > 0.01:
            self._hover_alpha += self.FADE_STEP * (1 if target > self._hover_alpha else -1)
            self._hover_alpha  = max(0.0, min(1.0, self._hover_alpha))
            self._apply_ctrl_alpha(self._hover_alpha)

        # text scroll
        self._scroller_title.tick()
        self._scroller_artist.tick()

        self._root.after(30, self._animate)   # ~33 fps

    def _apply_ctrl_alpha(self, alpha: float):
        state = "normal" if alpha > 0.05 else "hidden"
        for tag in ("ctrl_bg", "ctrl_elem", "status_badges"):
            try:
                self._canvas.itemconfig(tag, state=state)
            except Exception:
                pass
        for tag, *_ in self._btn_tags:
            for t in (tag, tag + "_bg"):
                try:
                    self._canvas.itemconfig(t, state=state)
                except Exception:
                    pass
        # simulate partial transparency on ctrl_bg by blending colour
        # (Tkinter canvas can't do per-item alpha, so we approximate)
        if alpha > 0:
            # darken bg from transparent to opaque
            darkness = int(15 + 20 * alpha)
            hex_col  = f"#{darkness:02x}{darkness//3:02x}{darkness//3*2:02x}"
            try:
                self._canvas.itemconfig("ctrl_bg", fill=hex_col)
            except Exception:
                pass

    # ── mirror toggle ─────────────────────────────────────────────────────────

    def _toggle_mirror(self):
        """Toggle reflection visibility and resize the window to match."""
        self._reflect = not self._reflect

        if self._reflect:
            # Show reflection
            self._ref_h = 60
            new_h = self._sz + self._PBAR_H() + self._txt_h + self._ref_h
            self._total_h = new_h
            self._root.geometry(
                f"{self._total_w}x{new_h}+"
                f"{self._root.winfo_x()}+{self._root.winfo_y()}"
            )
            self._canvas.config(height=new_h)
            # Redraw reflection if cover is already loaded
            if self._cover_pil is not None:
                ref_photo = _make_reflection(
                    self._cover_pil, self._sz, self._ref_h)
                if ref_photo:
                    self._reflect_photo = ref_photo
                    self._canvas.delete("reflect_img")
                    ry = self._sz + self.PBAR_H + self._txt_h
                    self._canvas.create_image(
                        0, ry, anchor="nw",
                        image=self._reflect_photo,
                        tags="reflect_img")
            else:
                # Show bg rectangle in case no cover yet
                ry = self._sz + self.PBAR_H + self._txt_h
                self._canvas.itemconfig("reflect_bg", state="normal")
        else:
            # Hide reflection
            self._ref_h = 0
            new_h = self._sz + self._PBAR_H() + self._txt_h
            self._total_h = new_h
            self._canvas.delete("reflect_img")
            try:
                self._canvas.itemconfig("reflect_bg", state="hidden")
            except Exception:
                pass
            self._root.geometry(
                f"{self._total_w}x{new_h}+"
                f"{self._root.winfo_x()}+{self._root.winfo_y()}"
            )
            self._canvas.config(height=new_h)

    # ── system tray ───────────────────────────────────────────────────────────

    def _start_tray(self):
        """Best-effort system tray icon. Silently skipped if unsupported."""
        self._tray_icon = None
        try:
            if sys.platform == "win32":
                # Use pystray if available
                import pystray
                from PIL import Image as _PImage
                import io as _io
                # default icon: blue square
                img = _PImage.new("RGB", (64, 64), "#1d4ed8")
                self._tray_icon = pystray.Icon(
                    "mpdpop",
                    img,
                    "MPD Overlay",
                    menu=pystray.Menu(
                        pystray.MenuItem("Open Playlist",
                                         lambda: self._on_double_click(None)),
                        pystray.MenuItem("Quit",
                                         lambda: self._root.after(0,
                                             self._root.destroy)),
                    )
                )
                threading.Thread(target=self._tray_icon.run,
                                 daemon=True).start()
        except Exception:
            pass

    def _update_tray(self, data: bytes):
        try:
            if self._tray_icon is None: return
            from PIL import Image as _PImage
            import io as _io
            img = _PImage.open(_io.BytesIO(data)).convert("RGB")
            img = img.resize((64, 64), _PImage.LANCZOS)
            self._tray_icon.icon = img
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    overlay = MPDOverlay()
    overlay.run()


if __name__ == "__main__":
    main()
