#!/usr/bin/env python3
# File: mpdpop.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Date: 2026-05-18
# Description: MPD playlist popup controller — cross-platform
#              Cover art + artist bio via mpdpop_artinfo.py
#              Config via mpdpop_env.py / mpdpop.env
# License: MIT

import sys
import os
import subprocess
import socket
import shutil
from abc import ABC, abstractmethod

# ── optional companion modules (graceful degradation if absent) ───────────────
try:
    from mpdpop_env import Config
    _CFG = Config()
except ImportError:
    class _FallbackConfig:
        def __getitem__(self, k): return os.environ.get(k, "")
        def get(self, k, d=""): return os.environ.get(k, d)
        def int(self, k, fb=0):
            try: return int(os.environ.get(k, fb))
            except: return fb
        def has(self, k): return bool(os.environ.get(k, "").strip())
        def cover_cache_dir(self):
            import tempfile
            from pathlib import Path
            p = Path(tempfile.gettempdir()) / "mpdpop_covers"
            p.mkdir(parents=True, exist_ok=True)
            return p
    _CFG = _FallbackConfig()

try:
    from mpdpop_artinfo import (ArtInfoLoader, make_info_panel,
                                update_info_panel, _fill_labels_only,
                                _set_bio_text)
    _HAS_ARTINFO = True
except ImportError:
    _HAS_ARTINFO = False


# ============================================
# MPD CLIENT
# ============================================
class MPDClient:
    """Simple MPD client driven by Config / environment variables."""

    def __init__(self, cfg=None):
        self.cfg      = cfg or _CFG
        self.host     = self.cfg.get("MPD_HOST",     os.environ.get("MPD_HOST",     "127.0.0.1"))
        self.port     = int(self.cfg.get("MPD_PORT", os.environ.get("MPD_PORT",     "6600")))
        self.password = self.cfg.get("MPD_PASSWORD",  os.environ.get("MPD_PASSWORD", ""))
        self.timeout  = self.cfg.int("MPD_TIMEOUT", 5)

    def _connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        banner = b""
        while not banner.endswith(b"\n"):
            chunk = sock.recv(1)
            if not chunk:
                break
            banner += chunk
        if not banner.startswith(b"OK MPD"):
            sock.close()
            raise ConnectionError(f"Invalid MPD response: {banner.decode().strip()}")
        if self.password:
            sock.sendall(f'password "{self.password}"\n'.encode())
            resp = self._read_response(sock)
            if not resp.startswith("OK"):
                sock.close()
                raise ConnectionError(f"Auth failed: {resp}")
        return sock

    def _read_response(self, sock) -> str:
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\nOK\n" in response or b"\nACK" in response or response.endswith(b"OK\n"):
                break
        return response.decode().strip()

    def get_playlist(self) -> list[dict]:
        try:
            sock = self._connect()
            sock.sendall(b"playlistinfo\n")
            response = self._read_response(sock)
            sock.close()
            tracks: list[dict] = []
            current: dict = {}
            for line in response.split("\n"):
                if line.startswith("file: "):
                    if current:
                        tracks.append(current)
                    current = {"file": line[6:], "title": "", "artist": "",
                               "album": "", "duration": ""}
                elif line.startswith("Title: "):
                    current["title"] = line[7:]
                elif line.startswith("Artist: "):
                    current["artist"] = line[8:]
                elif line.startswith("Album: "):
                    current["album"] = line[7:]
                elif line.startswith("Name: ") and not current.get("title"):
                    current["title"] = line[6:]
                elif line.startswith("duration: "):
                    try:
                        secs = float(line[10:])
                        current["duration"] = f"{int(secs // 60)}:{int(secs % 60):02d}"
                    except ValueError:
                        pass
            if current:
                tracks.append(current)
            for t in tracks:
                if not t["title"]:
                    t["title"] = os.path.basename(t["file"])
            return tracks
        except Exception as e:
            return [{"title": f"Error: {e}", "artist": "", "album": "",
                     "file": "", "duration": ""}]

    def get_current_song(self) -> dict:
        try:
            sock = self._connect()
            sock.sendall(b"currentsong\n")
            response = self._read_response(sock)
            sock.close()
            info: dict = {}
            for line in response.split("\n"):
                if line.startswith("Pos: "):
                    info["pos"] = int(line[5:])
                elif line.startswith("Title: "):
                    info["title"] = line[7:]
                elif line.startswith("Artist: "):
                    info["artist"] = line[8:]
                elif line.startswith("Album: "):
                    info["album"] = line[7:]
                elif line.startswith("file: "):
                    info["file"] = line[6:]
                elif line.startswith("duration: "):
                    try:
                        secs = float(line[10:])
                        info["duration"] = f"{int(secs // 60)}:{int(secs % 60):02d}"
                    except ValueError:
                        pass
            return info
        except Exception:
            return {}

    def play_track(self, track_number: int) -> str:
        try:
            sock = self._connect()
            sock.sendall(f"play {track_number - 1}\n".encode())
            response = self._read_response(sock)
            sock.close()
            return "OK" if "OK" in response else response
        except Exception as e:
            return f"Error: {e}"


# ============================================
# DIALOG BASE
# ============================================
class InputDialog(ABC):
    @abstractmethod
    def show(self, tracks: list[dict], current: dict) -> str | None:
        pass

    def _format_content(self, tracks: list[dict], current_pos: int | None) -> str:
        lines = [f"MPD Playlist  [{len(tracks)} tracks]", "=" * 60]
        for i, t in enumerate(tracks[:99], 1):
            marker = "▶" if (current_pos is not None and i - 1 == current_pos) else " "
            artist = f" — {t['artist']}" if t["artist"] else ""
            title  = t["title"][:42] + "…" if len(t["title"]) > 42 else t["title"]
            dur    = t["duration"] or "  ?  "
            lines.append(f" {marker} {i:2d}. {title}{artist}  [{dur}]")
        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================
# SHARED TKINTER DIALOG  (Windows + Linux + macOS)
# ============================================
def _build_tk_dialog(tracks: list[dict], current: dict, cfg,
                     font_name: str, mouse_pos_fn) -> str | None:
    """
    Full-featured Tkinter dialog shared by all platforms.

    Layout (top → bottom):
      ┌─ dark topbar: title + keyboard hints ────────────────┐
      ├─ info panel (only if mpdpop_artinfo available): ──────┤
      │   [cover 120px]  |  Now Playing / Title / Artist/Album│
      │   ── artist bio text (scrollable) ─────────────────── │
      ├─ filter bar ──────────────────────────────────────────┤
      ├─ track list (Treeview + scrollbar) ───────────────────┤
      └─ footer: [T] track# entry  |  Cancel  |  Play ▶ ─────┘
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return None

    PAGE_STEP   = cfg.int("PAGE_STEP",     10)
    DLG_W       = cfg.int("DIALOG_WIDTH",  780)
    DLG_H       = cfg.int("DIALOG_HEIGHT", 620)
    current_pos = current.get("pos", None)

    result_holder: list[str | None] = [None]
    mx, my, sw, sh = mouse_pos_fn()

    root = tk.Tk()
    root.title("MPD Controller")
    root.resizable(True, True)
    root.configure(bg="#111827")

    x = max(0, min(mx - DLG_W // 2, sw - DLG_W))
    y = max(0, min(my - DLG_H // 2, sh - DLG_H))
    root.geometry(f"{DLG_W}x{DLG_H}+{x}+{y}")
    root.attributes("-topmost", True)

    # ── Treeview style ────────────────────────────────────────────────────────
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview",
                    rowheight=24,
                    font=(font_name, 10),
                    background="#1e293b",
                    foreground="#cbd5e1",
                    fieldbackground="#1e293b")
    style.configure("Treeview.Heading",
                    font=(font_name, 9, "bold"),
                    background="#0f172a",
                    foreground="#64748b",
                    relief="flat")
    style.map("Treeview",
              background=[("selected", "#1d4ed8")],
              foreground=[("selected", "#f8fafc")])

    # ── Top bar ───────────────────────────────────────────────────────────────
    topbar = tk.Frame(root, bg="#0f172a")
    topbar.pack(fill="x")
    tk.Label(topbar, text="  ▶  MPD Controller",
             bg="#0f172a", fg="#f1f5f9",
             font=(font_name, 13, "bold"),
             anchor="w", pady=8).pack(fill="x", padx=10)
    hint = ("  ↑↓ navigate  ·  Enter play  ·  "
            "F filter  ·  T track#  ·  PgUp/Dn scroll  ·  Esc cancel")
    tk.Label(topbar, text=hint,
             bg="#0f172a", fg="#475569",
             font=(font_name, 8),
             anchor="w", pady=2).pack(fill="x", padx=10)

    # ── Now-playing info panel ────────────────────────────────────────────────
    info_widgets: dict = {}
    loader = None

    if _HAS_ARTINFO:
        panel_frame = tk.Frame(root, bg="#111827")
        panel_frame.pack(fill="x")
        info_widgets = make_info_panel(panel_frame, font_name, cfg)
        loader = ArtInfoLoader(cfg)

        _mpd_host = cfg.get("MPD_HOST", "127.0.0.1")
        _mpd_port = cfg.int("MPD_PORT", 6600)

        def _startup_fetch():
            # Determine which track to show on startup:
            # - MPD playing → that track
            # - MPD stopped → first track in list (so panel is never idle/stuck)
            if current_pos is not None and current_pos < len(tracks):
                track = tracks[current_pos]
            elif tracks:
                track = tracks[0]
            else:
                # truly empty playlist — stop indicators, show idle
                if info_widgets.get("spinner"):
                    info_widgets["spinner"].show_error()
                if info_widgets.get("pbar"):
                    info_widgets["pbar"].stop()
                if info_widgets.get("title_label"):
                    info_widgets["title_label"].config(text="Empty playlist")
                if info_widgets.get("source_label"):
                    info_widgets["source_label"].config(text="")
                return
            update_info_panel(
                info_widgets, track, root, loader, cfg,
                mpd_host=_mpd_host, mpd_port=_mpd_port,
            )

        root.after(80, _startup_fetch)

    # ── Filter bar ────────────────────────────────────────────────────────────
    fbar = tk.Frame(root, bg="#1e293b", pady=5)
    fbar.pack(fill="x", padx=8, pady=(6, 0))
    tk.Label(fbar, text="[F] Filter:",
             bg="#1e293b", fg="#64748b",
             font=(font_name, 9)).pack(side="left", padx=(0, 4))
    filter_var = tk.StringVar()
    filter_entry = tk.Entry(fbar, textvariable=filter_var,
                            font=(font_name, 10),
                            bg="#0f172a", fg="#e2e8f0",
                            insertbackground="#e2e8f0",
                            relief="flat", bd=4)
    filter_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

    # ── Track list ────────────────────────────────────────────────────────────
    list_frame = tk.Frame(root, bg="#111827")
    list_frame.pack(fill="both", expand=True, padx=8, pady=6)

    cols = ("num", "title", "artist", "dur")
    tree = ttk.Treeview(list_frame, columns=cols,
                        show="headings", selectmode="browse")
    tree.heading("num",    text="#",      anchor="center")
    tree.heading("title",  text="Title",  anchor="w")
    tree.heading("artist", text="Artist", anchor="w")
    tree.heading("dur",    text="Time",   anchor="center")
    tree.column("num",    width=42,  stretch=False, anchor="center")
    tree.column("title",  width=320, stretch=True,  anchor="w")
    tree.column("artist", width=210, stretch=True,  anchor="w")
    tree.column("dur",    width=58,  stretch=False, anchor="center")

    vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    tree.tag_configure("now", background="#1e3a5f", foreground="#93c5fd")

    all_tracks = list(enumerate(tracks[:99], 1))

    def populate(filter_text: str = "") -> None:
        tree.delete(*tree.get_children())
        q = filter_text.lower()
        for i, t in all_tracks:
            if q and q not in t["title"].lower() and q not in t["artist"].lower():
                continue
            tag = "now" if (current_pos is not None and i - 1 == current_pos) else ""
            num_label = f"▶{i}" if tag == "now" else str(i)
            tree.insert("", "end", iid=str(i),
                        values=(num_label, t["title"],
                                t["artist"] or "—", t["duration"] or ""),
                        tags=(tag,))
        if not filter_text and current_pos is not None:
            iid = str(current_pos + 1)
            if tree.exists(iid):
                tree.see(iid)
                tree.selection_set(iid)

    populate()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_num(raw) -> str:
        return str(raw).replace("▶", "").strip()

    def _sync_entry_from_tree():
        sel = tree.selection()
        if sel:
            raw = tree.item(sel[0])["values"][0]
            num_entry.delete(0, "end")
            num_entry.insert(0, _get_num(raw))

    def _focus_tree():
        tree.focus_set()
        sel = tree.selection()
        if not sel:
            children = tree.get_children()
            if children:
                tree.selection_set(children[0])
                tree.focus(children[0])
        else:
            tree.focus(sel[0])

    # ── Info-panel fetch state ────────────────────────────────────────────────
    _fetch_state = {"job": None}
    _FETCH_DELAY = 280   # ms idle after last arrow key before fetch fires

    def _schedule_fetch(track: dict) -> None:
        """
        Debounce: cancel any pending fetch, schedule a new one.
        Fires only after user stops moving for _FETCH_DELAY ms.
        The _token mechanism in update_info_panel discards stale results.
        Cache hits return in <10ms so spinner/pbar stop almost instantly.
        """
        if not (_HAS_ARTINFO and loader and info_widgets):
            return
        if _fetch_state["job"] is not None:
            try:
                root.after_cancel(_fetch_state["job"])
            except Exception:
                pass
            _fetch_state["job"] = None

        def _do_fetch():
            _fetch_state["job"] = None
            update_info_panel(
                info_widgets, track, root, loader, cfg,
                mpd_host=cfg.get("MPD_HOST", "127.0.0.1"),
                mpd_port=cfg.int("MPD_PORT", 6600),
            )

        _fetch_state["job"] = root.after(_FETCH_DELAY, _do_fetch)

    def _move_selection(delta: int):
        children = tree.get_children()
        if not children:
            return
        sel = tree.selection()
        idx = children.index(sel[0]) if sel else -1
        new_idx = max(0, min(len(children) - 1, idx + delta))
        iid = children[new_idx]
        tree.selection_set(iid)
        tree.focus(iid)
        tree.see(iid)
        _sync_entry_from_tree()

        # Update labels immediately (cheap) and restart indicators.
        # Fetch fires after debounce — but spinner/pbar are live right away.
        if _HAS_ARTINFO and info_widgets:
            try:
                real_idx = int(_get_num(tree.item(iid)["values"][0])) - 1
                if 0 <= real_idx < len(tracks):
                    t = tracks[real_idx]
                    _fill_labels_only(info_widgets, t)
                    # restart animation immediately so panel feels responsive
                    info_widgets["spinner"].start()
                    info_widgets["pbar"].start()
                    info_widgets["source_label"].config(text="fetching…")
                    _set_bio_text(info_widgets["bio_text"], "")
                    _schedule_fetch(t)
            except Exception:
                pass

    def play_selected(*_):
        sel = tree.selection()
        if sel:
            raw = tree.item(sel[0])["values"][0]
            result_holder[0] = _get_num(raw)
        elif num_entry.get().strip():
            result_holder[0] = num_entry.get().strip()
        root.destroy()

    def on_double_click(event):
        if tree.identify_region(event.x, event.y) == "cell":
            play_selected()

    tree.bind("<<TreeviewSelect>>", lambda e: _sync_entry_from_tree())
    tree.bind("<Double-1>", on_double_click)

    # ── Tree key bindings ─────────────────────────────────────────────────────

    def tree_key(event):
        k = event.keysym
        if k == "Up":     _move_selection(-1);          return "break"
        if k == "Down":   _move_selection(1);           return "break"
        if k == "Prior":  _move_selection(-PAGE_STEP);  return "break"
        if k == "Next":   _move_selection(PAGE_STEP);   return "break"
        if k == "Return": play_selected();               return "break"

    for seq in ("<Up>", "<Down>", "<Prior>", "<Next>", "<Return>"):
        tree.bind(seq, tree_key)

    # ── Global key handler (any focused widget) ───────────────────────────────

    def global_key(event):
        k       = event.keysym
        focused = root.focus_get()
        if k in ("Up", "Down"):
            _focus_tree()
            _move_selection(-1 if k == "Up" else 1)
            return "break"
        if k == "Prior":
            _focus_tree(); _move_selection(-PAGE_STEP); return "break"
        if k == "Next":
            _focus_tree(); _move_selection(PAGE_STEP);  return "break"
        if k == "f" and focused is not filter_entry:
            filter_entry.focus_set()
            filter_entry.icursor("end")
            return "break"
        if k == "t" and focused is not num_entry:
            num_entry.focus_set()
            num_entry.select_range(0, "end")
            return "break"
        if k == "Escape":
            root.destroy()
            return "break"
        if k == "Return" and focused is not filter_entry:
            play_selected()
            return "break"

    root.bind("<Key>", global_key)

    # ── Filter key handler ────────────────────────────────────────────────────

    def filter_key(event):
        k = event.keysym
        if k == "Down":
            _focus_tree()
            _move_selection(0)
            return "break"
        if k == "Return":
            children = tree.get_children()
            if len(children) == 1:
                tree.selection_set(children[0])
                play_selected()
            else:
                _focus_tree()
            return "break"

    filter_entry.bind("<Key>", filter_key)
    filter_var.trace_add("write", lambda *_: populate(filter_var.get()))

    # ── Footer ────────────────────────────────────────────────────────────────
    footer = tk.Frame(root, bg="#0f172a", pady=8)
    footer.pack(fill="x", padx=8)

    tk.Label(footer, text="[T] Track #:",
             bg="#0f172a", fg="#64748b",
             font=(font_name, 10)).pack(side="left", padx=(0, 4))

    num_entry = tk.Entry(footer, width=6,
                         font=(font_name, 12), justify="center",
                         bg="#1e293b", fg="#e2e8f0",
                         insertbackground="#e2e8f0",
                         relief="flat", bd=4)
    num_entry.pack(side="left")
    num_entry.bind("<Return>", play_selected)
    for seq in ("<Up>", "<Down>", "<Prior>", "<Next>"):
        num_entry.bind(seq, lambda e: (global_key(e), "break")[1])

    tk.Frame(footer, bg="#0f172a").pack(side="left", expand=True, fill="x")

    def _btn(text: str, cmd, accent: bool = False):
        bg  = "#166534" if accent else "#1e293b"
        fg  = "#f0fdf4" if accent else "#94a3b8"
        abg = "#15803d" if accent else "#334155"
        tk.Button(footer, text=text, command=cmd,
                  font=(font_name, 10, "bold" if accent else "normal"),
                  bg=bg, fg=fg, activebackground=abg, activeforeground=fg,
                  relief="flat", padx=14, pady=4, cursor="hand2"
                  ).pack(side="left", padx=(0, 6))

    _btn("Cancel",   root.destroy)
    _btn("Play  ▶",  play_selected, accent=True)

    root.after(50, _focus_tree)
    root.mainloop()
    return result_holder[0]


# ============================================
# WINDOWS
# ============================================
class WindowsInputDialog(InputDialog):
    def show(self, tracks: list[dict], current: dict) -> str | None:
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        except Exception:
            pass
        try:
            return _build_tk_dialog(tracks, current, _CFG,
                                    "Segoe UI", self._mouse_pos)
        except Exception as e:
            print(f"Windows dialog error: {e}")
            return self._fallback(tracks, current.get("pos"))

    def _mouse_pos(self):
        try:
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            return pt.x, pt.y, sw, sh
        except Exception:
            return 960, 540, 1920, 1080

    def _fallback(self, tracks, current_pos):
        print(self._format_content(tracks, current_pos))
        try:
            return input("Track number: ").strip() or None
        except (EOFError, KeyboardInterrupt):
            return None


# ============================================
# MACOS
# ============================================
class MacOSInputDialog(InputDialog):
    def show(self, tracks: list[dict], current: dict) -> str | None:
        try:
            return _build_tk_dialog(tracks, current, _CFG,
                                    "Helvetica Neue", self._mouse_pos)
        except Exception as e:
            print(f"macOS dialog error: {e}")
            return self._fallback(tracks, current.get("pos"))

    def _mouse_pos(self):
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 "tell application \"System Events\" to get position of mouse cursor"],
                capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                mx, my = r.stdout.strip().split(", ")
                return int(mx), int(my), 1920, 1080
        except Exception:
            pass
        return 960, 540, 1920, 1080

    def _fallback(self, tracks, current_pos):
        print(self._format_content(tracks, current_pos))
        try:
            return input("Track number: ").strip() or None
        except (EOFError, KeyboardInterrupt):
            return None


# ============================================
# LINUX
# ============================================
class LinuxInputDialog(InputDialog):
    def show(self, tracks: list[dict], current: dict) -> str | None:
        # Tkinter first (richest UI, always available with Python)
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            result = _build_tk_dialog(tracks, current, _CFG,
                                      "DejaVu Sans", self._mouse_pos)
            if result is not None:
                return result

        if shutil.which("zenity"):
            result = self._zenity(tracks, current.get("pos"))
            if result is not None:
                return result

        if shutil.which("kdialog"):
            result = self._kdialog(tracks, current.get("pos"))
            if result is not None:
                return result

        return self._terminal_input(tracks, current.get("pos"))

    def _zenity(self, tracks: list[dict], current_pos) -> str | None:
        cmd = [
            "zenity", "--list",
            "--title", "MPD Controller",
            "--text", f"Select a track  ({len(tracks)} total)",
            "--column", "#", "--column", "Title",
            "--column", "Artist", "--column", "Duration",
            "--width", "700", "--height", "550", "--hide-header",
        ]
        for i, t in enumerate(tracks[:99], 1):
            cmd += [str(i), t["title"] or "?",
                    t["artist"] or "—", t["duration"] or "?"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                chosen = r.stdout.strip()
                return chosen.split("|")[0] if chosen else None
        except Exception:
            pass
        return None

    def _kdialog(self, tracks: list[dict], current_pos) -> str | None:
        try:
            content = self._format_content(tracks, current_pos)
            r = subprocess.run(
                ["kdialog", "--title", "MPD Controller",
                 "--inputbox", content + "\n\nEnter track number:", ""],
                capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    def _terminal_input(self, tracks: list[dict], current_pos) -> str | None:
        print(self._format_content(tracks, current_pos))
        try:
            val = input("Track number (or Enter to cancel): ").strip()
            return val if val else None
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    def _mouse_pos(self):
        try:
            if shutil.which("xdotool"):
                out = subprocess.run(
                    ["xdotool", "getmouselocation", "--shell"],
                    capture_output=True, text=True).stdout
                lines = {l.split("=")[0]: int(l.split("=")[1])
                         for l in out.strip().split("\n") if "=" in l}
                sw, sh = 1920, 1080
                try:
                    xd = subprocess.run(
                        ["xdpyinfo"], capture_output=True, text=True).stdout
                    for line in xd.split("\n"):
                        if "dimensions:" in line:
                            dims = line.split()[1].split("x")
                            sw, sh = int(dims[0]), int(dims[1])
                            break
                except Exception:
                    pass
                return lines.get("X", 960), lines.get("Y", 540), sw, sh
        except Exception:
            pass
        return 960, 540, 1920, 1080


# ============================================
# PLATFORM SELECTOR
# ============================================
def get_dialog() -> InputDialog:
    if sys.platform == "win32":
        return WindowsInputDialog()
    elif sys.platform == "darwin":
        return MacOSInputDialog()
    else:
        return LinuxInputDialog()


# ============================================
# MAIN CONTROLLER
# ============================================
def mpd_controller():
    client = MPDClient(_CFG)
    tracks = client.get_playlist()

    if not tracks:
        print("No tracks in playlist.")
        return

    if tracks[0]["title"].startswith("Error:"):
        print(f"Failed to connect to MPD at {client.host}:{client.port}")
        print(tracks[0]["title"])
        return

    current = client.get_current_song()

    dialog  = get_dialog()
    result  = dialog.show(tracks, current)

    if not result or not result.strip():
        print("Cancelled.")
        return

    try:
        track_num = int(result.strip())
    except ValueError:
        print(f"Invalid input: {result!r}")
        return

    if not (1 <= track_num <= len(tracks)):
        print(f"Track number out of range: {track_num} (1–{len(tracks)})")
        return

    response = client.play_track(track_num)
    t = tracks[track_num - 1]
    if response == "OK":
        artist = f" — {t['artist']}" if t["artist"] else ""
        print(f"▶  Now playing #{track_num}: {t['title']}{artist}")
    else:
        print(f"MPD error: {response}")


if __name__ == "__main__":
    mpd_controller()
