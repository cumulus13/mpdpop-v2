#!/usr/bin/env python3

# File: mpdpop.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Date: 2026-05-18
# Description: MPD playlist popup controller - cross-platform
# License: MIT

import sys
import os
import time
import subprocess
import socket
import shutil
from abc import ABC, abstractmethod


# ============================================
# MPD CLIENT
# ============================================
class MPDClient:
    """Simple MPD client using environment variables."""

    def __init__(self):
        self.host = os.environ.get("MPD_HOST", "127.0.0.1")
        self.port = int(os.environ.get("MPD_PORT", "6600"))
        self.password = os.environ.get("MPD_PASSWORD", None)
        self.timeout = int(os.environ.get("MPD_TIMEOUT", "5"))

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
            response = self._read_response(sock)
            if not response.startswith("OK"):
                sock.close()
                raise ConnectionError(f"Auth failed: {response}")

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
        """Return list of track dicts with title, artist, file."""
        try:
            sock = self._connect()
            sock.sendall(b"playlistinfo\n")
            response = self._read_response(sock)
            sock.close()

            tracks = []
            current = {}
            for line in response.split('\n'):
                if line.startswith("file: "):
                    if current:
                        tracks.append(current)
                    current = {"file": line[6:], "title": "", "artist": "", "duration": ""}
                elif line.startswith("Title: "):
                    current["title"] = line[7:]
                elif line.startswith("Artist: "):
                    current["artist"] = line[8:]
                elif line.startswith("Name: "):
                    if not current.get("title"):
                        current["title"] = line[6:]
                elif line.startswith("duration: "):
                    try:
                        secs = float(line[10:])
                        current["duration"] = f"{int(secs // 60)}:{int(secs % 60):02d}"
                    except ValueError:
                        pass
            if current:
                tracks.append(current)

            # Fallback: use filename if no title
            for t in tracks:
                if not t["title"]:
                    t["title"] = os.path.basename(t["file"])

            return tracks

        except Exception as e:
            return [{"title": f"Error: {e}", "artist": "", "file": "", "duration": ""}]

    def get_current_song(self) -> dict:
        """Return currently playing song info."""
        try:
            sock = self._connect()
            sock.sendall(b"currentsong\n")
            response = self._read_response(sock)
            sock.close()

            info = {}
            for line in response.split('\n'):
                if line.startswith("Pos: "):
                    info["pos"] = int(line[5:])
                elif line.startswith("Title: "):
                    info["title"] = line[7:]
                elif line.startswith("Artist: "):
                    info["artist"] = line[8:]
            return info
        except Exception:
            return {}

    def play_track(self, track_number: int) -> str:
        """Play track by playlist index (1-based)."""
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
    def show(self, tracks: list[dict], current_pos: int) -> str | None:
        """Show the playlist picker. Returns track number string or None."""
        pass

    def _format_content(self, tracks: list[dict], current_pos: int) -> str:
        """Build clean, readable playlist text."""
        lines = []
        lines.append(f"MPD Playlist  [{len(tracks)} tracks]")
        lines.append("=" * 60)

        for i, t in enumerate(tracks[:99], 1):
            marker = "▶" if (current_pos is not None and i - 1 == current_pos) else " "
            artist = f" — {t['artist']}" if t["artist"] else ""
            title = t["title"][:42] + "…" if len(t["title"]) > 42 else t["title"]
            dur = t["duration"] or "  ?  "
            lines.append(f" {marker} {i:2d}. {title}{artist}  [{dur}]")

        lines.append("=" * 60)
        return "\n".join(lines)


# ============================================
# WINDOWS — Tkinter (always bundled with Python)
# ============================================
class WindowsInputDialog(InputDialog):
    def show(self, tracks: list[dict], current_pos: int) -> str | None:
        return self._tk_dialog(tracks, current_pos)

    def _mouse_pos(self):
        """Return (mouse_x, mouse_y) via ctypes."""
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

    def _tk_dialog(self, tracks: list[dict], current_pos: int) -> str | None:
        try:
            import tkinter as tk
            from tkinter import ttk

            # Number of rows to jump on Page Up/Down
            PAGE_STEP = 10

            result_holder = [None]
            mx, my, sw, sh = self._mouse_pos()

            root = tk.Tk()
            root.title("MPD Controller")
            root.resizable(True, True)

            # DPI awareness — crisp on high-DPI screens
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

            w, h = 700, 580
            x = max(0, min(mx - w // 2, sw - w))
            y = max(0, min(my - h // 2, sh - h))
            root.geometry(f"{w}x{h}+{x}+{y}")
            root.attributes("-topmost", True)

            # ---- Style ----
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("Treeview", rowheight=24, font=("Segoe UI", 10))
            style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
            style.map("Treeview",
                      background=[("selected", "#1a5fa8")],
                      foreground=[("selected", "white")])

            # ---- Header ----
            hdr = tk.Frame(root, bg="#1a1a2e")
            hdr.pack(fill="x")
            tk.Label(hdr, text="  ▶  MPD Controller", bg="#1a1a2e", fg="white",
                     font=("Segoe UI", 13, "bold"), anchor="w", pady=10).pack(fill="x", padx=10)
            hint = (f"  {len(tracks)} tracks  ·  "
                    "↑↓ navigate  ·  Enter play  ·  F filter  ·  T track#  ·  PgUp/Dn scroll")
            tk.Label(hdr, text=hint, bg="#1a1a2e", fg="#8899cc",
                     font=("Segoe UI", 8), anchor="w", pady=4).pack(fill="x", padx=10)

            # ---- Filter bar ----
            fbar = tk.Frame(root, bg="#f0f0f0", pady=5)
            fbar.pack(fill="x", padx=8, pady=(6, 0))
            tk.Label(fbar, text="[F] Filter:", bg="#f0f0f0",
                     font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
            filter_var = tk.StringVar()
            filter_entry = tk.Entry(fbar, textvariable=filter_var, font=("Segoe UI", 10))
            filter_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

            # ---- Track list ----
            list_frame = tk.Frame(root)
            list_frame.pack(fill="both", expand=True, padx=8, pady=6)

            cols = ("num", "title", "artist", "dur")
            tree = ttk.Treeview(list_frame, columns=cols, show="headings",
                                 selectmode="browse")
            tree.heading("num",    text="#",      anchor="center")
            tree.heading("title",  text="Title",  anchor="w")
            tree.heading("artist", text="Artist", anchor="w")
            tree.heading("dur",    text="Time",   anchor="center")
            tree.column("num",    width=42,  stretch=False, anchor="center")
            tree.column("title",  width=310, stretch=True,  anchor="w")
            tree.column("artist", width=210, stretch=True,  anchor="w")
            tree.column("dur",    width=58,  stretch=False, anchor="center")

            vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            tree.tag_configure("now", background="#123a6e", foreground="white")

            all_tracks = list(enumerate(tracks[:99], 1))

            def populate(filter_text=""):
                tree.delete(*tree.get_children())
                q = filter_text.lower()
                for i, t in all_tracks:
                    if q and q not in t["title"].lower() and q not in t["artist"].lower():
                        continue
                    tag = "now" if (current_pos is not None and i - 1 == current_pos) else ""
                    num_label = f"▶{i}" if tag == "now" else str(i)
                    tree.insert("", "end", iid=str(i),
                                values=(num_label, t["title"], t["artist"] or "—",
                                        t["duration"] or ""),
                                tags=(tag,))
                if not filter_text and current_pos is not None:
                    iid = str(current_pos + 1)
                    if tree.exists(iid):
                        tree.see(iid)
                        tree.selection_set(iid)

            populate()

            # ---- Helpers ----
            def _get_num(raw) -> str:
                return str(raw).replace("▶", "").strip()

            def _sync_entry_from_tree():
                """Update the track# entry from the current tree selection."""
                sel = tree.selection()
                if sel:
                    raw = tree.item(sel[0])["values"][0]
                    num_entry.delete(0, "end")
                    num_entry.insert(0, _get_num(raw))

            def _focus_tree():
                """Give focus to tree without disturbing selection."""
                tree.focus_set()
                sel = tree.selection()
                if not sel:
                    children = tree.get_children()
                    if children:
                        tree.selection_set(children[0])
                        tree.focus(children[0])
                else:
                    tree.focus(sel[0])

            def _move_selection(delta: int):
                """Move tree selection by delta rows, keep item in view."""
                children = tree.get_children()
                if not children:
                    return
                sel = tree.selection()
                if sel:
                    idx = children.index(sel[0])
                else:
                    idx = -1
                new_idx = max(0, min(len(children) - 1, idx + delta))
                iid = children[new_idx]
                tree.selection_set(iid)
                tree.focus(iid)
                tree.see(iid)
                _sync_entry_from_tree()

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

            # ---- Tree events ----
            tree.bind("<<TreeviewSelect>>", lambda e: _sync_entry_from_tree())
            tree.bind("<Double-1>", on_double_click)

            # Override default Treeview Up/Down so they always work even when
            # focus is elsewhere, and wire Return → play
            def tree_key(event):
                k = event.keysym
                if k in ("Up", "Down"):
                    _focus_tree()
                    _move_selection(-1 if k == "Up" else 1)
                    return "break"
                if k == "Return":
                    play_selected()
                    return "break"
                if k == "Prior":          # Page Up
                    _move_selection(-PAGE_STEP)
                    return "break"
                if k == "Next":           # Page Down
                    _move_selection(PAGE_STEP)
                    return "break"

            tree.bind("<Up>",    tree_key)
            tree.bind("<Down>",  tree_key)
            tree.bind("<Return>", tree_key)
            tree.bind("<Prior>", tree_key)
            tree.bind("<Next>",  tree_key)

            # ---- Global hotkeys (work from any widget) ----
            def global_key(event):
                k = event.keysym
                focused = root.focus_get()

                # Up/Down always drive tree navigation, even from entry widgets
                if k in ("Up", "Down"):
                    _focus_tree()
                    _move_selection(-1 if k == "Up" else 1)
                    return "break"

                # Page Up/Down always scroll the table
                if k == "Prior":
                    _focus_tree()
                    _move_selection(-PAGE_STEP)
                    return "break"
                if k == "Next":
                    _focus_tree()
                    _move_selection(PAGE_STEP)
                    return "break"

                # F → focus filter (unless already typing in filter)
                if k == "f" and focused is not filter_entry:
                    filter_entry.focus_set()
                    filter_entry.icursor("end")
                    return "break"

                # T → focus track# entry
                if k == "t" and focused is not num_entry:
                    num_entry.focus_set()
                    num_entry.select_range(0, "end")
                    return "break"

                # Escape → cancel
                if k == "Escape":
                    root.destroy()
                    return "break"

                # Enter → play (when not in filter_entry where Enter should still work)
                if k == "Return" and focused is not filter_entry:
                    play_selected()
                    return "break"

            root.bind("<Key>", global_key)

            # Filter: typing narrows list; Down arrow leaves filter and moves to tree
            def filter_key(event):
                if event.keysym == "Down":
                    _focus_tree()
                    _move_selection(0)   # keeps current or selects first
                    return "break"
                if event.keysym == "Return":
                    # If exactly one result, play it; else move to tree
                    children = tree.get_children()
                    if len(children) == 1:
                        tree.selection_set(children[0])
                        play_selected()
                    else:
                        _focus_tree()
                    return "break"

            filter_entry.bind("<Key>", filter_key)

            def on_filter(*_):
                populate(filter_var.get())

            filter_var.trace_add("write", on_filter)

            # ---- Footer ----
            footer = tk.Frame(root, bg="#f0f0f0", pady=8)
            footer.pack(fill="x", padx=8)

            tk.Label(footer, text="[T] Track #:", bg="#f0f0f0",
                     font=("Segoe UI", 10)).pack(side="left", padx=(0, 4))
            num_entry = tk.Entry(footer, width=6, font=("Segoe UI", 12), justify="center")
            num_entry.pack(side="left")
            # Enter in track# entry plays directly
            num_entry.bind("<Return>", play_selected)
            # Up/Down in track# entry navigates table
            num_entry.bind("<Up>",    lambda e: (global_key(e), "break")[1])
            num_entry.bind("<Down>",  lambda e: (global_key(e), "break")[1])
            num_entry.bind("<Prior>", lambda e: (global_key(e), "break")[1])
            num_entry.bind("<Next>",  lambda e: (global_key(e), "break")[1])

            tk.Frame(footer, bg="#f0f0f0").pack(side="left", expand=True, fill="x")

            tk.Button(footer, text="Cancel", width=9, font=("Segoe UI", 10),
                      command=root.destroy).pack(side="left", padx=(0, 6))
            tk.Button(footer, text="Play  ▶", width=10, font=("Segoe UI", 10, "bold"),
                      bg="#1a7f4a", fg="white", activebackground="#29a863",
                      relief="flat", cursor="hand2",
                      command=play_selected).pack(side="left")

            # Start with focus on the tree so arrow keys work immediately
            root.after(50, _focus_tree)
            root.mainloop()
            return result_holder[0]

        except Exception as e:
            print(f"Tkinter dialog error: {e}")
            print(self._format_content(tracks, current_pos))
            try:
                return input("Track number: ").strip() or None
            except (EOFError, KeyboardInterrupt):
                return None


# ============================================
# MACOS
# ============================================
class MacOSInputDialog(InputDialog):
    def show(self, tracks: list[dict], current_pos: int) -> str | None:
        try:
            mp_result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get position of mouse cursor'],
                capture_output=True, text=True
            )
            mx, my = (mp_result.stdout.strip().split(", ")
                      if mp_result.returncode == 0 and mp_result.stdout.strip()
                      else ("400", "300"))

            content = self._format_content(tracks, current_pos)
            escaped = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

            script = f'''
set theDialog to display dialog "{escaped}\\n\\nEnter track number to play:" ¬
    with title "MPD Controller" ¬
    default answer "" ¬
    buttons {{"Cancel", "Play"}} ¬
    default button "Play" ¬
    with icon note

if button returned of theDialog is "Play" then
    return text returned of theDialog
else
    return ""
end if
'''
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            output = result.stdout.strip()
            return output if output else None

        except Exception as e:
            print(f"macOS dialog error: {e}")
            return self._fallback_input(tracks, current_pos)

    def _fallback_input(self, tracks, current_pos):
        print(self._format_content(tracks, current_pos))
        try:
            return input("Track number: ").strip() or None
        except (EOFError, KeyboardInterrupt):
            return None


# ============================================
# LINUX — multi-backend with proper sizing
# ============================================
class LinuxInputDialog(InputDialog):
    def show(self, tracks: list[dict], current_pos: int) -> str | None:
        content = self._format_content(tracks, current_pos)

        # Try backends in order of preference
        if shutil.which("zenity"):
            result = self._zenity(content, tracks)
            if result is not None:
                return result

        if shutil.which("kdialog"):
            result = self._kdialog(content)
            if result is not None:
                return result

        if shutil.which("yad"):
            result = self._yad(content)
            if result is not None:
                return result

        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            result = self._tk_dialog(tracks, current_pos)
            if result is not None:
                return result

        # Terminal fallback
        return self._terminal_input(tracks, current_pos)

    # ------ Zenity (GTK) ------
    def _zenity(self, content: str, tracks: list[dict]) -> str | None:
        mx, my, sw, sh = self._mouse_pos()

        # Use --list for a proper scrollable table
        cmd = [
            "zenity", "--list",
            "--title", "MPD Controller",
            "--text", f"Select a track to play  ({len(tracks)} total)",
            "--column", "#",
            "--column", "Title",
            "--column", "Artist",
            "--column", "Duration",
            "--width", "700",
            "--height", "550",
            "--hide-header",
        ]

        for i, t in enumerate(tracks[:99], 1):
            cmd += [str(i), t["title"] or "?", t["artist"] or "—", t["duration"] or "?"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                chosen = result.stdout.strip()
                # zenity --list returns the first column value
                return chosen.split("|")[0] if chosen else None
            return None
        except Exception:
            # Fallback to entry dialog if list doesn't work
            cmd = [
                "zenity", "--entry",
                "--title", "MPD Controller",
                "--text", content,
                "--entry-text", "",
                "--width", "640",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else None

    # ------ KDialog (Qt/KDE) ------
    def _kdialog(self, content: str) -> str | None:
        try:
            # Use --menu for a proper list
            cmd = ["kdialog", "--title", "MPD Controller", "--inputbox",
                   content + "\n\nEnter track number to play:", ""]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    # ------ YAD (advanced GTK) ------
    def _yad(self, content: str) -> str | None:
        try:
            cmd = [
                "yad", "--entry",
                "--title", "MPD Controller",
                "--text", content,
                "--width", "700",
                "--height", "600",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    # ------ Tkinter (always available with Python) ------
    def _tk_dialog(self, tracks: list[dict], current_pos: int) -> str | None:
        try:
            import tkinter as tk
            from tkinter import ttk

            PAGE_STEP = 10
            result_holder = [None]

            root = tk.Tk()
            root.title("MPD Controller")
            root.resizable(True, True)

            mx, my, sw, sh = self._mouse_pos()
            w, h = 700, 580
            x = max(0, min(mx - w // 2, sw - w))
            y = max(0, min(my - h // 2, sh - h))
            root.geometry(f"{w}x{h}+{x}+{y}")
            root.attributes("-topmost", True)

            style = ttk.Style()
            style.theme_use("clam")
            style.configure("Treeview", rowheight=24, font=("DejaVu Sans", 10))
            style.configure("Treeview.Heading", font=("DejaVu Sans", 9, "bold"))
            style.map("Treeview",
                      background=[("selected", "#1a5fa8")],
                      foreground=[("selected", "white")])

            hdr = tk.Frame(root, bg="#1a1a2e")
            hdr.pack(fill="x")
            tk.Label(hdr, text="  ▶  MPD Controller", bg="#1a1a2e", fg="white",
                     font=("DejaVu Sans", 13, "bold"), anchor="w", pady=10).pack(fill="x", padx=10)
            hint = (f"  {len(tracks)} tracks  ·  "
                    "↑↓ navigate  ·  Enter play  ·  F filter  ·  T track#  ·  PgUp/Dn scroll")
            tk.Label(hdr, text=hint, bg="#1a1a2e", fg="#8899cc",
                     font=("DejaVu Sans", 8), anchor="w", pady=4).pack(fill="x", padx=10)

            fbar = tk.Frame(root, pady=5)
            fbar.pack(fill="x", padx=8, pady=(6, 0))
            tk.Label(fbar, text="[F] Filter:",
                     font=("DejaVu Sans", 9)).pack(side="left", padx=(0, 4))
            filter_var = tk.StringVar()
            filter_entry = tk.Entry(fbar, textvariable=filter_var, font=("DejaVu Sans", 10))
            filter_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

            list_frame = tk.Frame(root)
            list_frame.pack(fill="both", expand=True, padx=8, pady=6)

            cols = ("num", "title", "artist", "dur")
            tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
            tree.heading("num",    text="#",      anchor="center")
            tree.heading("title",  text="Title",  anchor="w")
            tree.heading("artist", text="Artist", anchor="w")
            tree.heading("dur",    text="Time",   anchor="center")
            tree.column("num",    width=42,  stretch=False, anchor="center")
            tree.column("title",  width=310, stretch=True,  anchor="w")
            tree.column("artist", width=210, stretch=True,  anchor="w")
            tree.column("dur",    width=58,  stretch=False, anchor="center")

            vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            tree.tag_configure("now", background="#123a6e", foreground="white")

            all_tracks = list(enumerate(tracks[:99], 1))

            def populate(filter_text=""):
                tree.delete(*tree.get_children())
                q = filter_text.lower()
                for i, t in all_tracks:
                    if q and q not in t["title"].lower() and q not in t["artist"].lower():
                        continue
                    tag = "now" if (current_pos is not None and i - 1 == current_pos) else ""
                    num_label = f"▶{i}" if tag == "now" else str(i)
                    tree.insert("", "end", iid=str(i),
                                values=(num_label, t["title"], t["artist"] or "—",
                                        t["duration"] or ""),
                                tags=(tag,))
                if not filter_text and current_pos is not None:
                    iid = str(current_pos + 1)
                    if tree.exists(iid):
                        tree.see(iid)
                        tree.selection_set(iid)

            populate()

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

            def tree_key(event):
                k = event.keysym
                if k == "Up":    _move_selection(-1);         return "break"
                if k == "Down":  _move_selection(1);          return "break"
                if k == "Prior": _move_selection(-PAGE_STEP); return "break"
                if k == "Next":  _move_selection(PAGE_STEP);  return "break"
                if k == "Return": play_selected();             return "break"

            tree.bind("<Up>",    tree_key)
            tree.bind("<Down>",  tree_key)
            tree.bind("<Prior>", tree_key)
            tree.bind("<Next>",  tree_key)
            tree.bind("<Return>", tree_key)

            def global_key(event):
                k = event.keysym
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
                    filter_entry.focus_set(); filter_entry.icursor("end"); return "break"
                if k == "t" and focused is not num_entry:
                    num_entry.focus_set(); num_entry.select_range(0, "end"); return "break"
                if k == "Escape":
                    root.destroy(); return "break"
                if k == "Return" and focused is not filter_entry:
                    play_selected(); return "break"

            root.bind("<Key>", global_key)

            def filter_key(event):
                if event.keysym == "Down":
                    _focus_tree(); _move_selection(0); return "break"
                if event.keysym == "Return":
                    children = tree.get_children()
                    if len(children) == 1:
                        tree.selection_set(children[0]); play_selected()
                    else:
                        _focus_tree()
                    return "break"

            filter_entry.bind("<Key>", filter_key)
            filter_var.trace_add("write", lambda *_: populate(filter_var.get()))

            footer = tk.Frame(root, pady=8, padx=12)
            footer.pack(fill="x")
            tk.Label(footer, text="[T] Track #:",
                     font=("DejaVu Sans", 10)).pack(side="left", padx=(0, 4))
            num_entry = tk.Entry(footer, width=6, font=("DejaVu Sans", 12), justify="center")
            num_entry.pack(side="left")
            num_entry.bind("<Return>", play_selected)
            num_entry.bind("<Up>",    lambda e: (global_key(e), "break")[1])
            num_entry.bind("<Down>",  lambda e: (global_key(e), "break")[1])
            num_entry.bind("<Prior>", lambda e: (global_key(e), "break")[1])
            num_entry.bind("<Next>",  lambda e: (global_key(e), "break")[1])

            btn_frame = tk.Frame(footer)
            btn_frame.pack(side="right")
            tk.Button(btn_frame, text="Cancel", command=root.destroy,
                      width=8).pack(side="left", padx=4)
            tk.Button(btn_frame, text="Play ▶", command=play_selected, width=8,
                      bg="#1a7f4a", fg="white", activebackground="#2aaf6a",
                      relief="flat").pack(side="left")

            root.after(50, _focus_tree)
            root.mainloop()
            return result_holder[0]

        except Exception as e:
            print(f"Tkinter dialog error: {e}")
            return None

    # ------ Terminal fallback ------
    def _terminal_input(self, tracks: list[dict], current_pos: int) -> str | None:
        print(self._format_content(tracks, current_pos))
        print()
        try:
            val = input("Track number (or Enter to cancel): ").strip()
            return val if val else None
        except (EOFError, KeyboardInterrupt):
            print()
            return None

    def _mouse_pos(self):
        """Return (mouse_x, mouse_y, screen_w, screen_h)."""
        try:
            if shutil.which("xdotool"):
                out = subprocess.run(
                    ["xdotool", "getmouselocation", "--shell"],
                    capture_output=True, text=True
                ).stdout
                lines = {l.split("=")[0]: int(l.split("=")[1])
                         for l in out.strip().split("\n") if "=" in l}
                sw = lines.get("SCREEN", 0)
                # Get screen size via xdpyinfo
                try:
                    xd = subprocess.run(["xdpyinfo"], capture_output=True, text=True).stdout
                    for line in xd.split("\n"):
                        if "dimensions:" in line:
                            dims = line.split()[1].split("x")
                            sw, sh = int(dims[0]), int(dims[1])
                            break
                    else:
                        sw, sh = 1920, 1080
                except Exception:
                    sw, sh = 1920, 1080
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
    client = MPDClient()
    tracks = client.get_playlist()

    if not tracks:
        print("No tracks in playlist.")
        return

    if tracks[0]["title"].startswith("Error:"):
        print(f"Failed to connect to MPD at {client.host}:{client.port}")
        print(tracks[0]["title"])
        return

    current = client.get_current_song()
    current_pos = current.get("pos", None)

    dialog = get_dialog()
    result = dialog.show(tracks, current_pos)

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
