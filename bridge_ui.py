#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bridge_ui.py - UI/Tray wrapper for Guild Activity Bridge

Goals:
- Never crash the bridge if UI deps are missing.
- Prefer Window UI (Tk / CustomTkinter) when available.
- Fallback to System Tray icon (pystray + Pillow) when Tk is missing (common in embedded Python).
- Always write a runtime log file for easy debugging.

Public API used by guild_activity_bridge.py:
- BridgeUI(...callbacks...)
- BridgeUI.update_status(...)
- BridgeUI.show_activity(...)
- BridgeUI.push_log(...)
- BridgeUI.set_console_visible(...)
- BridgeUI.set_autostart_enabled(...)
- BridgeUI.run()
"""

from __future__ import annotations

import os
import sys
import time
import queue
import threading
import subprocess
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable, Tuple

# -----------------------------
# Availability checks
# -----------------------------

TK_AVAILABLE = False
CTK_AVAILABLE = False
PYSTRAY_AVAILABLE = False
PIL_AVAILABLE = False

try:
    import tkinter as tk  # noqa: F401
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

if TK_AVAILABLE:
    try:
        import customtkinter as ctk  # type: ignore
        CTK_AVAILABLE = True
    except Exception:
        CTK_AVAILABLE = False

try:
    from PIL import Image  # type: ignore
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

try:
    import pystray  # type: ignore
    PYSTRAY_AVAILABLE = True
except Exception:
    PYSTRAY_AVAILABLE = False


# -----------------------------
# Helpers
# -----------------------------

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _safe_mkdir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass

def _open_path(path: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass

def _truncate(s: str, n: int = 64) -> str:
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


@dataclass
class UITheme:
    # Keep it simple; the "epic" styling can be layered later
    title: str = "Guild Activity Bridge"
    subtitle: str = "Uploader / Sync Monitor"


class BridgeUI:
    """
    UI controller with safe fallbacks.

    Modes:
    - "ctk": CustomTkinter window
    - "tk":  Tk window
    - "tray": System tray icon
    - "none": no UI
    """

    def __init__(
        self,
        enabled: bool,
        icon_path: str,
        on_full_roster: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        on_toggle_console: Optional[Callable[[], None]] = None,
        on_toggle_autostart: Optional[Callable[[], None]] = None,
        autostart_available: bool = False,
        autostart_enabled: bool = False,
        theme: Optional[UITheme] = None,
        console_visible: bool = True,
    ):
        self.enabled = bool(enabled)
        self.icon_path = icon_path or ""
        self.on_full_roster = on_full_roster
        self.on_exit = on_exit
        self.on_toggle_console = on_toggle_console
        self.on_toggle_autostart = on_toggle_autostart

        self.autostart_available = bool(autostart_available)
        self.autostart_enabled = bool(autostart_enabled)
        self.console_visible = bool(console_visible)

        self.theme = theme or UITheme()

        # State exposed (guild_activity_bridge checks ui.root is not None)
        self.root: Any = None

        # Runtime status
        self._wow_running: Optional[bool] = None
        self._watch_path: str = ""
        self._queue_note: str = ""
        self._activity: str = ""
        self._progress: str = ""
        self._health: Dict[str, Any] = {}

        # Thread-safe UI queue
        self._q: "queue.Queue[Tuple[str, Tuple[Any, ...]]]" = queue.Queue()

        # Log file
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._log_path = os.path.join(self._base_dir, "bridge_ui_runtime.log")

        # Window widgets (if any)
        self._txt_log = None
        self._lbl_status = None
        self._lbl_activity = None
        self._lbl_watch = None
        self._lbl_health = None
        self._lbl_queue = None

        # Tray icon (if any)
        self._tray_icon = None

        self.mode = "none"

        if not self.enabled:
            self.mode = "none"
            self.root = None
            return

        # Decide best UI mode
        if TK_AVAILABLE:
            self.mode = "ctk" if CTK_AVAILABLE else "tk"
            try:
                self._init_window()
            except Exception as e:
                # If window init fails, fallback to tray
                self._write_log(f"UI window init failed -> fallback. Reason: {e}", level="ERROR")
                self._init_tray_or_none()
        else:
            self._init_tray_or_none()

        # First line to log
        self._write_log(f"BridgeUI initialized in mode='{self.mode}'", level="INFO")

    # -----------------------------
    # Init modes
    # -----------------------------

    def _init_tray_or_none(self) -> None:
        if PYSTRAY_AVAILABLE and PIL_AVAILABLE:
            try:
                self.mode = "tray"
                self._init_tray()
                # IMPORTANT: satisfy bridge check "ui.root is not None"
                self.root = object()  # sentinel
                return
            except Exception as e:
                self._write_log(f"Tray init failed. Reason: {e}", level="ERROR")

        self.mode = "none"
        self.root = None

    def _load_icon_image(self):
        """
        Returns a PIL image suitable for pystray.
        If icon_path is missing/invalid, returns a simple generated image.
        """
        try:
            if self.icon_path and os.path.isfile(self.icon_path):
                img = Image.open(self.icon_path)
                return img.convert("RGBA")
        except Exception:
            pass

        # fallback: tiny generated icon
        try:
            img = Image.new("RGBA", (64, 64), (15, 23, 42, 255))
            return img
        except Exception:
            return None

    def _init_tray(self) -> None:
        img = self._load_icon_image()
        title = _truncate(self.theme.title, 63)

        def _cb_full(_icon, _item):
            self._safe_call(self.on_full_roster)

        def _cb_toggle_console(_icon, _item):
            self._safe_call(self.on_toggle_console)

        def _cb_toggle_autostart(_icon, _item):
            self._safe_call(self.on_toggle_autostart)

        def _cb_open_folder(_icon, _item):
            _open_path(self._base_dir)

        def _cb_open_log(_icon, _item):
            _open_path(self._log_path)

        def _cb_exit(_icon, _item):
            try:
                if self._tray_icon is not None:
                    self._tray_icon.stop()
            except Exception:
                pass
            self._safe_call(self.on_exit)

        # Dynamic "checked" state
        def _checked_console(_item):
            return bool(self.console_visible)

        def _checked_autostart(_item):
            return bool(self.autostart_enabled)

        menu_items = []

        menu_items.append(pystray.MenuItem("Send Full Roster", _cb_full, default=True))

        if self.on_toggle_console is not None:
            menu_items.append(pystray.MenuItem("Console Visible", _cb_toggle_console, checked=_checked_console))

        if self.autostart_available and self.on_toggle_autostart is not None:
            menu_items.append(pystray.MenuItem("Autostart Enabled", _cb_toggle_autostart, checked=_checked_autostart))

        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(pystray.MenuItem("Open Bridge Folder", _cb_open_folder))
        menu_items.append(pystray.MenuItem("Open UI Log", _cb_open_log))
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(pystray.MenuItem("Exit", _cb_exit))

        menu = pystray.Menu(*menu_items)

        self._tray_icon = pystray.Icon("GuildActivityBridge", img, title, menu)

    def _init_window(self) -> None:
        # Window mode
        if self.mode == "ctk":
            ctk.set_appearance_mode("Dark")
            self.root = ctk.CTk()
            self.root.title(self.theme.title)
            self.root.geometry("900x650")
            self.root.minsize(780, 520)
            self._build_ctk()
            self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
            self.root.after(250, self._drain_queue)
        else:
            import tkinter as tk  # local import
            self.root = tk.Tk()
            self.root.title(self.theme.title)
            self.root.geometry("900x650")
            self.root.minsize(780, 520)
            self._build_tk()
            self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
            self.root.after(250, self._drain_queue)

    # -----------------------------
    # Build UI layouts
    # -----------------------------

    def _build_ctk(self) -> None:
        # CustomTkinter layout
        root = self.root

        # Header
        hdr = ctk.CTkFrame(root)
        hdr.pack(fill="x", padx=14, pady=(14, 8))

        title = ctk.CTkLabel(hdr, text=self.theme.title, font=("Segoe UI", 20, "bold"))
        title.pack(anchor="w", padx=12, pady=(10, 0))

        subtitle = ctk.CTkLabel(hdr, text=self.theme.subtitle, font=("Segoe UI", 12))
        subtitle.pack(anchor="w", padx=12, pady=(0, 10))

        # Status area
        mid = ctk.CTkFrame(root)
        mid.pack(fill="x", padx=14, pady=8)

        self._lbl_status = ctk.CTkLabel(mid, text="Status: --", font=("Segoe UI", 13, "bold"))
        self._lbl_status.pack(anchor="w", padx=12, pady=(10, 4))

        self._lbl_watch = ctk.CTkLabel(mid, text="Watch: --", font=("Segoe UI", 11))
        self._lbl_watch.pack(anchor="w", padx=12, pady=2)

        self._lbl_health = ctk.CTkLabel(mid, text="Health: --", font=("Segoe UI", 11))
        self._lbl_health.pack(anchor="w", padx=12, pady=2)

        self._lbl_queue = ctk.CTkLabel(mid, text="Queue: --", font=("Segoe UI", 11))
        self._lbl_queue.pack(anchor="w", padx=12, pady=(2, 10))

        # Activity + buttons
        row = ctk.CTkFrame(root)
        row.pack(fill="x", padx=14, pady=8)

        self._lbl_activity = ctk.CTkLabel(row, text="Activity: --", font=("Segoe UI", 12))
        self._lbl_activity.pack(side="left", padx=12, pady=10)

        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.pack(side="right", padx=10, pady=8)

        ctk.CTkButton(btns, text="Send Full Roster", command=lambda: self._safe_call(self.on_full_roster)).pack(
            side="left", padx=6
        )

        if self.on_toggle_console is not None:
            ctk.CTkButton(btns, text="Toggle Console", command=lambda: self._safe_call(self.on_toggle_console)).pack(
                side="left", padx=6
            )

        if self.autostart_available and self.on_toggle_autostart is not None:
            ctk.CTkButton(btns, text="Toggle Autostart", command=lambda: self._safe_call(self.on_toggle_autostart)).pack(
                side="left", padx=6
            )

        ctk.CTkButton(btns, text="Open Folder", command=lambda: _open_path(self._base_dir)).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Open Log", command=lambda: _open_path(self._log_path)).pack(side="left", padx=6)

        # Log
        box = ctk.CTkFrame(root)
        box.pack(fill="both", expand=True, padx=14, pady=(8, 14))

        self._txt_log = ctk.CTkTextbox(box, wrap="word")
        self._txt_log.pack(fill="both", expand=True, padx=10, pady=10)
        self._txt_log.insert("end", f"[{_now()}] UI ready.\n")
        self._txt_log.configure(state="disabled")

    def _build_tk(self) -> None:
        # Tk layout
        import tkinter as tk  # local import

        root = self.root

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)

        lbl_title = ttk.Label(frm, text=self.theme.title, font=("Segoe UI", 18, "bold"))
        lbl_title.pack(anchor="w")

        lbl_sub = ttk.Label(frm, text=self.theme.subtitle, font=("Segoe UI", 11))
        lbl_sub.pack(anchor="w", pady=(0, 10))

        stat = ttk.Frame(frm)
        stat.pack(fill="x", pady=6)

        self._lbl_status = ttk.Label(stat, text="Status: --", font=("Segoe UI", 12, "bold"))
        self._lbl_status.pack(anchor="w")

        self._lbl_watch = ttk.Label(stat, text="Watch: --")
        self._lbl_watch.pack(anchor="w", pady=2)

        self._lbl_health = ttk.Label(stat, text="Health: --")
        self._lbl_health.pack(anchor="w", pady=2)

        self._lbl_queue = ttk.Label(stat, text="Queue: --")
        self._lbl_queue.pack(anchor="w", pady=(2, 8))

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=6)

        self._lbl_activity = ttk.Label(row, text="Activity: --")
        self._lbl_activity.pack(side="left")

        btns = ttk.Frame(row)
        btns.pack(side="right")

        ttk.Button(btns, text="Send Full Roster", command=lambda: self._safe_call(self.on_full_roster)).pack(
            side="left", padx=4
        )

        if self.on_toggle_console is not None:
            ttk.Button(btns, text="Toggle Console", command=lambda: self._safe_call(self.on_toggle_console)).pack(
                side="left", padx=4
            )

        if self.autostart_available and self.on_toggle_autostart is not None:
            ttk.Button(btns, text="Toggle Autostart", command=lambda: self._safe_call(self.on_toggle_autostart)).pack(
                side="left", padx=4
            )

        ttk.Button(btns, text="Open Folder", command=lambda: _open_path(self._base_dir)).pack(side="left", padx=4)
        ttk.Button(btns, text="Open Log", command=lambda: _open_path(self._log_path)).pack(side="left", padx=4)

        ttk.Separator(frm).pack(fill="x", pady=8)

        self._txt_log = ScrolledText(frm, wrap="word", height=18)
        self._txt_log.pack(fill="both", expand=True)
        self._txt_log.insert("end", f"[{_now()}] UI ready.\n")
        self._txt_log.configure(state="disabled")

    # -----------------------------
    # Public API called by bridge
    # -----------------------------

    def update_status(
        self,
        wow_running: bool,
        health: Dict[str, Any],
        watch_path: str,
        activity: str = "",
        progress: str = "",
        queue_note: str = "",
    ) -> None:
        self._wow_running = wow_running
        self._health = dict(health or {})
        self._watch_path = str(watch_path or "")
        self._queue_note = str(queue_note or "")
        if activity:
            self._activity = str(activity)
        if progress:
            self._progress = str(progress)

        self._q.put(("status", ()))

    def show_activity(self, message: str, progress: str = "") -> None:
        self._activity = str(message or "")
        if progress:
            self._progress = str(progress)
        self._q.put(("activity", ()))

    def push_log(self, message: str, level: str = "INFO") -> None:
        msg = str(message or "")
        lvl = str(level or "INFO").upper()
        self._write_log(msg, level=lvl)

        # Update UI/tray
        self._q.put(("log", (f"[{_now()}] [{lvl}] {msg}\n",)))

    def set_console_visible(self, visible: bool) -> None:
        self.console_visible = bool(visible)
        self._q.put(("tray_refresh", ()))

    def set_autostart_enabled(self, enabled: bool) -> None:
        self.autostart_enabled = bool(enabled)
        self._q.put(("tray_refresh", ()))

    # -----------------------------
    # Run loop
    # -----------------------------

    def run(self) -> None:
        """
        Called by guild_activity_bridge on the main thread.
        - window mode: mainloop()
        - tray mode: icon.run()
        - none: return immediately
        """
        if not self.enabled:
            return

        if self.mode in ("ctk", "tk") and self.root is not None:
            try:
                self.root.mainloop()
            except Exception as e:
                self._write_log(f"UI mainloop crashed: {e}", level="ERROR")
            return

        if self.mode == "tray" and self._tray_icon is not None:
            try:
                # If we want to process queue updates periodically, we can start a small thread:
                t = threading.Thread(target=self._tray_pump_loop, daemon=True)
                t.start()
                self._tray_icon.run()
            except Exception as e:
                self._write_log(f"Tray loop crashed: {e}", level="ERROR")
            return

        # none
        return

    # -----------------------------
    # Internal processing
    # -----------------------------

    def _tray_pump_loop(self) -> None:
        # Periodically drain queue while tray is running
        while True:
            try:
                self._drain_queue(tray_only=True)
            except Exception:
                pass
            time.sleep(0.25)

    def _drain_queue(self, tray_only: bool = False) -> None:
        # Drain queued events
        while True:
            try:
                kind, args = self._q.get_nowait()
            except queue.Empty:
                break

            try:
                if kind == "log":
                    self._apply_log(args[0])
                elif kind == "status":
                    self._apply_status()
                elif kind == "activity":
                    self._apply_activity()
                elif kind == "tray_refresh":
                    self._apply_tray_title()
            except Exception as e:
                self._write_log(f"UI apply failed for '{kind}': {e}", level="ERROR")

        # Schedule next tick (window mode only)
        if not tray_only and self.mode in ("ctk", "tk") and self.root is not None:
            try:
                self.root.after(250, self._drain_queue)
            except Exception:
                pass

        # Keep tray tooltip updated
        if self.mode == "tray":
            self._apply_tray_title()

    def _apply_log(self, line: str) -> None:
        if self.mode in ("ctk", "tk") and self._txt_log is not None:
            try:
                self._txt_log.configure(state="normal")
                self._txt_log.insert("end", line)
                self._txt_log.see("end")
                self._txt_log.configure(state="disabled")
            except Exception:
                pass
        # tray/none doesn't display; log is still written to file

    def _apply_status(self) -> None:
        # Update window labels (if window mode)
        wow_txt = "ONLINE" if self._wow_running else "OFFLINE"
        v = self._health.get("version") or "--"
        p_ok = self._health.get("last_parse_ok") or "pending"
        u_ok = self._health.get("last_upload_ok") or "pending"
        lat = self._health.get("last_latency_ms") or "--"
        size = self._health.get("last_payload_size") or "--"

        status_line = f"Status: WoW {wow_txt} | Bridge v{v}"
        watch_line = f"Watch: {self._watch_path or '--'}"
        health_line = f"Health: parse={p_ok} | upload={u_ok} | latency={lat}ms | payload={size}"
        queue_line = f"Queue: {self._queue_note or '--'}"

        if self.mode in ("ctk", "tk") and self._lbl_status is not None:
            try:
                self._lbl_status.configure(text=status_line)  # type: ignore
                self._lbl_watch.configure(text=watch_line)    # type: ignore
                self._lbl_health.configure(text=health_line)  # type: ignore
                self._lbl_queue.configure(text=queue_line)    # type: ignore
            except Exception:
                pass

        # Always update tray title too
        self._apply_tray_title()

    def _apply_activity(self) -> None:
        txt = self._activity or "--"
        if self._progress and self._progress not in ("", "--"):
            txt = f"{txt} ({self._progress})"

        if self.mode in ("ctk", "tk") and self._lbl_activity is not None:
            try:
                self._lbl_activity.configure(text=f"Activity: {txt}")  # type: ignore
            except Exception:
                pass

        self._apply_tray_title()

    def _apply_tray_title(self) -> None:
        if self.mode != "tray" or self._tray_icon is None:
            return

        wow_txt = "WoW ONLINE" if self._wow_running else "WoW OFFLINE"
        act = self._activity or "Idle"
        if self._progress and self._progress not in ("", "--"):
            act = f"{act} ({self._progress})"

        title = _truncate(f"{self.theme.title} • {wow_txt} • {act}", 63)
        try:
            self._tray_icon.title = title
            self._tray_icon.update_menu()
        except Exception:
            pass

    def _handle_close(self) -> None:
        # Close window but keep bridge running; user can still use tray/log.
        # If you prefer "closing window exits bridge", call on_exit.
        try:
            if self.root is not None:
                self.root.withdraw()
        except Exception:
            try:
                if self.root is not None:
                    self.root.destroy()
            except Exception:
                pass

    def _safe_call(self, cb: Optional[Callable[[], None]]) -> None:
        try:
            if cb is not None:
                cb()
        except Exception as e:
            self._write_log(f"Callback error: {e}", level="ERROR")

    def _write_log(self, message: str, level: str = "INFO") -> None:
        try:
            line = f"[{_now()}] [{level}] {message}\n"
            with open(self._log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(line)
        except Exception:
            pass


# Optional: quick local test
if __name__ == "__main__":
    ui = BridgeUI(
        enabled=True,
        icon_path="",
        on_full_roster=lambda: print("FULL ROSTER"),
        on_exit=lambda: print("EXIT"),
        on_toggle_console=lambda: print("TOGGLE CONSOLE"),
        on_toggle_autostart=lambda: print("TOGGLE AUTOSTART"),
        autostart_available=True,
        autostart_enabled=False,
        console_visible=True,
    )

    ui.update_status(
        wow_running=False,
        health={"version": "test", "last_parse_ok": "pending", "last_upload_ok": "pending", "last_latency_ms": "0", "last_payload_size": "0"},
        watch_path="C:\\Path\\To\\GuildActivityTracker.lua",
        activity="Ready",
        progress="--",
        queue_note="vacía",
    )
    ui.push_log("BridgeUI test started.", level="INFO")
    ui.run()
