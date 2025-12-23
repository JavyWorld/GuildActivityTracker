#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bridge UI for Guild Activity Tracker Bridge.

✅ Modo A (EPIC): CustomTkinter dashboard (si Tk/Tcl está disponible)
✅ Modo B (Fallback): System Tray (pystray) cuando no hay Tkinter/CustomTkinter
- Mantiene API: update_status(), show_activity(), push_log(), set_console_visible(), set_autostart_enabled(), run()

Requisitos:
- customtkinter (opcional pero recomendado)
- Pillow + pystray (recomendado para tray y manejo de imágenes)
"""

from __future__ import annotations

import os
import sys
import time
import threading
import queue
import datetime
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional, Any, Callable, List, Tuple

# ---------------------------
# Safe imports: tkinter/ctk
# ---------------------------
TK_AVAILABLE = False
CTK_AVAILABLE = False
tk = None
ctk = None
Image = None

try:
    import tkinter as tk  # type: ignore
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False
    tk = None

if TK_AVAILABLE:
    try:
        import customtkinter as ctk  # type: ignore
        from PIL import Image  # type: ignore
        CTK_AVAILABLE = True
    except Exception:
        CTK_AVAILABLE = False
        ctk = None
        Image = None

# ---------------------------
# Tray fallback imports
# ---------------------------
TRAY_AVAILABLE = False
pystray = None

try:
    import pystray  # type: ignore
    from PIL import Image as PILImage  # type: ignore
    TRAY_AVAILABLE = True
except Exception:
    TRAY_AVAILABLE = False
    pystray = None
    PILImage = None


# ---------------------------
# Native message box fallback (Windows)
# ---------------------------
def _native_message_box(title: str, text: str) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0)
            return
        except Exception:
            pass
    # Fallback
    print(f"[{title}] {text}")


@dataclass
class UITheme:
    bg_dark: str = "#0f172a"
    bg_card: str = "#1e293b"
    text_main: str = "#f1f5f9"
    text_dim: str = "#94a3b8"
    accent_primary: str = "#3b82f6"
    accent_success: str = "#10b981"
    accent_warning: str = "#f59e0b"
    accent_danger: str = "#ef4444"
    terminal_bg: str = "#020617"
    button_secondary: str = "#334155"


class _TrayRoot:
    """
    Dummy root so guild_activity_bridge can treat tray-mode as "has UI".
    Provides protocol/after/destroy methods that do nothing or schedule timers.
    """
    def __init__(self):
        self._timers: List[threading.Timer] = []

    def protocol(self, *_args, **_kwargs):
        return

    def after(self, ms: int, fn: Callable):
        t = threading.Timer(max(0.0, ms / 1000.0), fn)
        t.daemon = True
        self._timers.append(t)
        t.start()

    def destroy(self):
        # cancel any pending timers
        for t in self._timers:
            try:
                t.cancel()
            except Exception:
                pass
        self._timers.clear()


class BridgeUI:
    def __init__(
        self,
        enabled: bool,
        icon_path: str,
        on_full_roster: Optional[Callable[[], Any]] = None,
        on_exit: Optional[Callable[[], Any]] = None,
        on_toggle_console: Optional[Callable[[], bool]] = None,
        on_toggle_autostart: Optional[Callable[[Optional[bool]], bool]] = None,
        autostart_available: bool = False,
        autostart_enabled: bool = False,
        theme: Optional[UITheme] = None,
        console_visible: bool = True,
    ):
        self.enabled = bool(enabled)
        self.icon_path = icon_path
        self.on_full_roster = on_full_roster
        self.on_exit = on_exit
        self.on_toggle_console = on_toggle_console
        self.on_toggle_autostart = on_toggle_autostart
        self.autostart_available = bool(autostart_available)
        self.autostart_enabled = bool(autostart_enabled)
        self.theme = theme or UITheme()
        self.console_visible = bool(console_visible)

        self.root = None  # Tk root or _TrayRoot sentinel (tray mode)
        self.mode = "disabled"  # "ctk" | "tk" | "tray" | "disabled"

        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        # UI refs
        self.labels: Dict[str, Any] = {}
        self.progress_container = None
        self.progress_bar = None
        self.status_label = None
        self.log_widget = None
        self.btn_console = None
        self.autostart_var = None

        # tray
        self._tray_icon = None
        self._tray_stop = threading.Event()
        self._tray_state = {
            "wow": "OFFLINE",
            "activity": "SYSTEM IDLE",
            "progress": "--",
            "latency": "--",
            "payload": "--",
            "queue": "vacía",
            "watch": "",
            "last_upload": "Pending",
        }
        self._recent_logs: List[Tuple[str, str]] = []  # (level, msg)

        if not self.enabled:
            return

        self._init_mode()

        # schedule queue drain
        if self.root is not None:
            try:
                self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
            except Exception:
                pass
            try:
                self.root.after(400, self._drain_queue)
            except Exception:
                # tray dummy root still has after
                self.root.after(400, self._drain_queue)

    # ---------------------------
    # Mode selection
    # ---------------------------
    def _init_mode(self):
        # Try CTK dashboard first
        if CTK_AVAILABLE:
            try:
                self._init_window_ctk()
                self.mode = "ctk"
                return
            except Exception as e:
                self.push_log(f"CTK init falló, fallback a Tray. Motivo: {e}", "warn")

        # If Tk exists but CTK not, do basic Tk window
        if TK_AVAILABLE:
            try:
                self._init_window_tk()
                self.mode = "tk"
                return
            except Exception as e:
                self.push_log(f"TK init falló, fallback a Tray. Motivo: {e}", "warn")

        # Tray fallback
        if TRAY_AVAILABLE:
            self._init_tray()
            self.mode = "tray"
            # Dummy root to keep bridge happy
            self.root = _TrayRoot()
            return

        # No UI possible
        self.enabled = False
        self.mode = "disabled"
        self.root = None
        _native_message_box("GAT Bridge", "No UI disponible: faltan Tkinter/CustomTkinter y también pystray.\nContinuando sin UI.")

    # ---------------------------
    # Window: CustomTkinter
    # ---------------------------
    def _init_window_ctk(self):
        assert ctk is not None
        t = self.theme

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("Guild Tracker // Command Bridge")
        self.root.geometry("880x690")
        self.root.configure(fg_color=t.bg_dark)

        # Header
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(22, 8))

        # Logo (optional)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logo_file = os.path.join(script_dir, "media", "gat_logo.png")
        if os.path.exists(logo_file) and Image is not None:
            try:
                pil = Image.open(logo_file)
                self._logo_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(48, 48))
                ctk.CTkLabel(header, text="", image=self._logo_img).pack(side="left", padx=(0, 14))
            except Exception:
                pass

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="GUILD TRACKER BRIDGE",
            font=("Segoe UI", 24, "bold"),
            text_color=t.text_main
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text="Tactical Data Uplink System",
            font=("Segoe UI", 11),
            text_color=t.text_dim
        ).pack(anchor="w")

        badge = ctk.CTkLabel(
            header,
            text="UI: CTK",
            font=("Segoe UI", 12, "bold"),
            text_color=t.accent_success,
            fg_color=t.bg_card,
            corner_radius=8,
            padx=12,
            pady=4
        )
        badge.pack(side="right")

        # Stats cards row
        grid = ctk.CTkFrame(self.root, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=12)
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        def stat_card(col: int, title: str, key: str):
            frame = ctk.CTkFrame(grid, fg_color=t.bg_card, corner_radius=12)
            frame.grid(row=0, column=col, padx=6, sticky="ew")

            ctk.CTkLabel(frame, text=title, font=("Segoe UI", 10), text_color=t.text_dim).pack(anchor="w", padx=14, pady=(10, 0))
            val = ctk.CTkLabel(frame, text="--", font=("Segoe UI", 14, "bold"), text_color=t.text_main)
            val.pack(anchor="w", padx=14, pady=(0, 10))
            self.labels[key] = val

        stat_card(0, "GAME STATUS", "wow")
        stat_card(1, "QUEUE", "queue")
        stat_card(2, "LATENCY", "latency")
        stat_card(3, "PAYLOAD", "payload")

        # Ops frame
        ops = ctk.CTkFrame(self.root, fg_color=t.bg_card, corner_radius=12)
        ops.pack(fill="x", padx=20, pady=10)

        self.status_label = ctk.CTkLabel(
            ops,
            text="SYSTEM IDLE",
            font=("Consolas", 14, "bold"),
            text_color=t.accent_primary
        )
        self.status_label.pack(anchor="w", padx=14, pady=(14, 6))

        # Progress container (stealth)
        self.progress_container = ctk.CTkFrame(ops, fg_color="transparent")
        self.progress_bar = ctk.CTkProgressBar(self.progress_container, orientation="horizontal", height=12, corner_radius=8)
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.set(0.0)
        self.progress_bar.configure(progress_color=t.accent_primary)

        self.labels["progress_text"] = ctk.CTkLabel(
            self.progress_container,
            text="--",
            font=("Segoe UI", 10),
            text_color=t.text_dim
        )
        self.labels["progress_text"].pack(anchor="e")

        # Controls
        ctrl = ctk.CTkFrame(self.root, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=6)

        left = ctk.CTkFrame(ctrl, fg_color="transparent")
        left.pack(side="left")

        watch = ctk.CTkLabel(left, text="Watching: ...", font=("Consolas", 10), text_color=t.text_dim)
        watch.pack(anchor="w")
        self.labels["watch"] = watch

        # autostart switch
        if self.on_toggle_autostart and self.autostart_available and tk is not None:
            self.autostart_var = tk.BooleanVar(value=self.autostart_enabled)
            sw = ctk.CTkSwitch(
                left,
                text="Iniciar con Windows",
                variable=self.autostart_var,
                command=self._toggle_autostart_switch,
                font=("Segoe UI", 11),
                text_color=t.text_dim,
                progress_color=t.accent_success,
            )
            sw.pack(anchor="w", pady=(6, 0))

        right = ctk.CTkFrame(ctrl, fg_color="transparent")
        right.pack(side="right")

        if self.on_toggle_console:
            self.btn_console = ctk.CTkButton(
                right,
                text=("Hide Console" if self.console_visible else "Show Console"),
                command=self._toggle_console_button,
                font=("Segoe UI", 11),
                fg_color=t.button_secondary,
                hover_color="#475569",
                corner_radius=8,
                height=34,
                width=130,
            )
            self.btn_console.pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            right,
            text="FORCE SYNC",
            command=self._request_full,
            font=("Segoe UI", 12, "bold"),
            fg_color=t.accent_primary,
            hover_color="#2563eb",
            corner_radius=8,
            height=34,
            width=140,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            right,
            text="VERIFY",
            command=self._verify_install,
            font=("Segoe UI", 12, "bold"),
            fg_color=t.accent_warning,
            hover_color="#d97706",
            corner_radius=8,
            height=34,
            width=110,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            right,
            text="EXIT",
            command=self._handle_close,
            font=("Segoe UI", 12, "bold"),
            fg_color=t.accent_danger,
            hover_color="#b91c1c",
            corner_radius=8,
            height=34,
            width=90,
        ).pack(side="left")

        # Logs
        ctk.CTkLabel(
            self.root,
            text="> SYSTEM LOGS",
            font=("Consolas", 11, "bold"),
            text_color=t.text_dim,
        ).pack(anchor="w", padx=24, pady=(10, 0))

        self.log_widget = ctk.CTkTextbox(
            self.root,
            fg_color=t.terminal_bg,
            text_color=t.accent_success,
            font=("Consolas", 11),
            corner_radius=10
        )
        self.log_widget.pack(fill="both", expand=True, padx=20, pady=(6, 18))
        self.log_widget.configure(state="disabled")

        # set icon for window (best effort)
        try:
            if tk is not None and os.path.isfile(self.icon_path):
                img = tk.PhotoImage(file=self.icon_path)
                self.root.iconphoto(False, img)
        except Exception:
            pass

    # ---------------------------
    # Window: basic Tk fallback
    # ---------------------------
    def _init_window_tk(self):
        assert tk is not None
        self.root = tk.Tk()
        self.root.title("GAT Bridge (Basic UI)")
        self.root.geometry("640x420")

        top = tk.Frame(self.root)
        top.pack(fill="x", padx=12, pady=10)

        self.labels["wow"] = tk.Label(top, text="OFFLINE", font=("Segoe UI", 12, "bold"))
        self.labels["wow"].pack(anchor="w")

        self.labels["watch"] = tk.Label(top, text="Watching: ...", font=("Consolas", 9))
        self.labels["watch"].pack(anchor="w", pady=(4, 0))

        self.status_label = tk.Label(self.root, text="SYSTEM IDLE", font=("Consolas", 12, "bold"))
        self.status_label.pack(anchor="w", padx=12, pady=(8, 4))

        self.labels["progress_text"] = tk.Label(self.root, text="--", font=("Segoe UI", 10))
        self.labels["progress_text"].pack(anchor="w", padx=12, pady=(0, 8))

        btns = tk.Frame(self.root)
        btns.pack(fill="x", padx=12, pady=6)

        tk.Button(btns, text="Force Sync", command=self._request_full).pack(side="left", padx=(0, 8))
        tk.Button(btns, text="Verify", command=self._verify_install).pack(side="left", padx=(0, 8))
        if self.on_toggle_console:
            self.btn_console = tk.Button(btns, text=("Hide Console" if self.console_visible else "Show Console"), command=self._toggle_console_button)
            self.btn_console.pack(side="left", padx=(0, 8))
        tk.Button(btns, text="Exit", command=self._handle_close).pack(side="right")

        self.log_widget = tk.Text(self.root, height=12)
        self.log_widget.pack(fill="both", expand=True, padx=12, pady=(10, 12))

    # ---------------------------
    # Tray mode
    # ---------------------------
    def _load_tray_image(self):
        # Prefer icon_path; else fallback to media/gat_logo.png; else blank
        candidates = []
        if self.icon_path:
            candidates.append(self.icon_path)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, "media", "gat_logo.png"))
        candidates.append(os.path.join(script_dir, "gat_icon.png"))

        for p in candidates:
            try:
                if p and os.path.isfile(p) and PILImage is not None:
                    return PILImage.open(p)
            except Exception:
                continue

        # blank
        if PILImage is not None:
            return PILImage.new("RGBA", (64, 64), (40, 40, 40, 255))
        return None

    def _init_tray(self):
        if not TRAY_AVAILABLE or pystray is None:
            return

        img = self._load_tray_image()

        def _safe_thread(fn: Callable, *args):
            def runner():
                try:
                    fn(*args)
                except Exception:
                    pass
            th = threading.Thread(target=runner, daemon=True)
            th.start()

        def on_force(_icon, _item):
            if self.on_full_roster:
                _safe_thread(self.on_full_roster)
            self.push_log("Tray: FORCE SYNC solicitado.", "warn")

        def on_verify(_icon, _item):
            _safe_thread(self._verify_install)
            self.push_log("Tray: VERIFY solicitado.", "info")

        def on_open_folder(_icon, _item):
            _safe_thread(self._open_install_folder)

        def on_toggle_console(_icon, _item):
            if self.on_toggle_console:
                try:
                    state = self.on_toggle_console()
                    self.set_console_visible(bool(state))
                except Exception:
                    pass

        def on_toggle_autostart(_icon, _item):
            if self.on_toggle_autostart and self.autostart_available:
                desired = not self.autostart_enabled
                try:
                    ok = self.on_toggle_autostart(desired)
                    if ok:
                        self.set_autostart_enabled(desired)
                except Exception:
                    pass

        def on_show_logs(_icon, _item):
            logs = self._format_recent_logs(18)
            _native_message_box("GAT Bridge - Recent Logs", logs)

        def on_exit(_icon, _item):
            self._tray_stop.set()
            try:
                if self.on_exit:
                    self.on_exit()
            except Exception:
                pass
            try:
                _icon.stop()
            except Exception:
                pass

        # Checkable menu items
        def _checked_autostart(_item):
            return bool(self.autostart_enabled)

        menu_items = [
            pystray.MenuItem("Force Sync (Full Roster)", on_force, default=True),
            pystray.MenuItem("Verify Install", on_verify),
            pystray.MenuItem("Open Install Folder", on_open_folder),
        ]

        if self.on_toggle_console:
            menu_items.append(pystray.MenuItem("Toggle Console", on_toggle_console))

        if self.on_toggle_autostart and self.autostart_available:
            menu_items.append(pystray.MenuItem("Start with Windows", on_toggle_autostart, checked=_checked_autostart))

        menu_items += [
            pystray.MenuItem("Show Recent Logs", on_show_logs),
            pystray.MenuItem("Exit", on_exit),
        ]

        self._tray_icon = pystray.Icon(
            "GAT Bridge",
            img,
            title="GAT Bridge",
            menu=pystray.Menu(*menu_items)
        )

        # Background updater
        threading.Thread(target=self._tray_update_loop, daemon=True).start()

    def _tray_update_loop(self):
        while not self._tray_stop.is_set():
            # update tooltip/title from state
            try:
                if self._tray_icon is not None:
                    wow = self._tray_state.get("wow", "OFFLINE")
                    qn = self._tray_state.get("queue", "vacía")
                    lat = self._tray_state.get("latency", "--")
                    act = self._tray_state.get("activity", "SYSTEM IDLE")
                    title = f"WoW: {wow} | Queue: {qn} | Lat: {lat} | {act}"
                    # pystray title updates are not always instant across backends; still helpful
                    self._tray_icon.title = title[:120]
            except Exception:
                pass
            time.sleep(1.0)

    def _format_recent_logs(self, max_lines: int = 15) -> str:
        if not self._recent_logs:
            return "No logs yet."
        lines = []
        for lvl, msg in self._recent_logs[-max_lines:]:
            lines.append(f"[{lvl.upper()}] {msg}")
        return "\n".join(lines)

    # ---------------------------
    # Public API called by bridge
    # ---------------------------
    def update_status(
        self,
        wow_running: bool,
        health: Dict[str, Any],
        watch_path: str,
        activity: str = "",
        progress: str = "",
        queue_note: str = "",
    ):
        if not self.enabled:
            return

        wow_text = "ONLINE" if wow_running else "OFFLINE"

        update: Dict[str, Any] = {
            "wow": wow_text,
            "watch": f"File: {os.path.basename(watch_path) if watch_path else ''}",
            "upload": health.get("last_upload_ok") or "Pending",
            "latency": f"{health.get('last_latency_ms') or '--'} ms",
            "payload": f"{health.get('last_payload_size') or '--'} bytes",
            "queue": queue_note or "vacía",
        }

        # Determine idle to hide progress
        is_idle = (not activity) or ("espera" in activity.lower()) or ("idle" in activity.lower())
        update["show_progress"] = not is_idle

        update["activity"] = activity or "SYSTEM IDLE"

        if progress:
            update["progress_text"] = progress

            # Parse "X/Y" (ej: "Lote 2/7")
            match = re.search(r"(\d+)\s*/\s*(\d+)", progress)
            if match:
                try:
                    curr = int(match.group(1))
                    total = int(match.group(2))
                    if total > 0:
                        update["progress_float"] = float(curr) / float(total)
                except Exception:
                    update["progress_float"] = 0.0
            else:
                # Parse percent
                if "%" in progress:
                    try:
                        p_val = re.search(r"(\d+(\.\d+)?)", progress)
                        if p_val:
                            update["progress_float"] = float(p_val.group(1)) / 100.0
                    except Exception:
                        pass
        else:
            update["progress_text"] = "--"
            update["progress_float"] = 0.0

        self.queue.put(update)

    def show_activity(self, message: str, progress: str = ""):
        # Keep this lightweight: just updates activity/progress
        self.queue.put({
            "activity": message or "SYSTEM IDLE",
            "progress_text": progress or "--",
            "show_progress": bool(progress and progress != "--"),
        })

    def push_log(self, message: str, level: str = "info"):
        self.queue.put({"log": str(message), "log_level": str(level)})

    def set_console_visible(self, visible: bool):
        self.console_visible = bool(visible)
        # Update button text if exists
        if self.btn_console is not None:
            try:
                if self.mode == "ctk" and ctk is not None:
                    self.btn_console.configure(text=("Hide Console" if visible else "Show Console"))
                elif self.mode == "tk" and tk is not None:
                    self.btn_console.configure(text=("Hide Console" if visible else "Show Console"))
            except Exception:
                pass

    def set_autostart_enabled(self, enabled: bool):
        self.autostart_enabled = bool(enabled)
        # Update switch if in CTK mode
        if self.autostart_var is not None:
            try:
                self.autostart_var.set(self.autostart_enabled)
            except Exception:
                pass

    # ---------------------------
    # Queue drain and apply updates
    # ---------------------------
    def _drain_queue(self):
        try:
            while not self.queue.empty():
                u = self.queue.get_nowait()
                self._apply(u)
        finally:
            if self.root is not None:
                try:
                    self.root.after(450, self._drain_queue)
                except Exception:
                    pass

    def _apply(self, update: Dict[str, Any]):
        # Update tray state always
        if "wow" in update:
            self._tray_state["wow"] = update["wow"]
        if "activity" in update:
            self._tray_state["activity"] = update["activity"]
        if "progress_text" in update:
            self._tray_state["progress"] = update["progress_text"]
        if "latency" in update:
            self._tray_state["latency"] = update["latency"]
        if "payload" in update:
            self._tray_state["payload"] = update["payload"]
        if "queue" in update:
            self._tray_state["queue"] = update["queue"]
        if "watch" in update:
            self._tray_state["watch"] = update["watch"]
        if "upload" in update:
            self._tray_state["last_upload"] = update["upload"]

        # Apply to window UI
        if self.mode in ("ctk", "tk") and self.root is not None:
            # Basic labels
            for k, v in update.items():
                if k in self.labels:
                    try:
                        if self.mode == "ctk":
                            self.labels[k].configure(text=str(v))
                        else:
                            self.labels[k].configure(text=str(v))
                    except Exception:
                        pass

            # Wow color cue (CTK)
            if self.mode == "ctk" and "wow" in update and "wow" in self.labels:
                try:
                    txt = str(update["wow"])
                    if "ONLINE" in txt:
                        self.labels["wow"].configure(text_color=self.theme.accent_success)
                    else:
                        self.labels["wow"].configure(text_color=self.theme.accent_danger)
                except Exception:
                    pass

            # Activity
            if "activity" in update and self.status_label is not None:
                try:
                    if self.mode == "ctk":
                        self.status_label.configure(text=str(update["activity"]).upper())
                    else:
                        self.status_label.configure(text=str(update["activity"]).upper())
                except Exception:
                    pass

            # Progress visibility (CTK only)
            if self.mode == "ctk" and self.progress_container is not None:
                show = bool(update.get("show_progress", False))
                try:
                    mapped = self.progress_container.winfo_ismapped()
                    if show and not mapped:
                        self.progress_container.pack(fill="x", padx=14, pady=(0, 10))
                    elif (not show) and mapped:
                        self.progress_container.pack_forget()
                except Exception:
                    pass

            # Progress value (CTK)
            if self.mode == "ctk" and self.progress_bar is not None and "progress_float" in update:
                try:
                    val = float(update["progress_float"])
                    if val < 0:
                        val = 0.0
                    if val > 1:
                        val = 1.0
                    self.progress_bar.set(val)
                except Exception:
                    pass

            # Logs
            if "log" in update:
                self._append_log(str(update.get("log", "")), str(update.get("log_level", "info")))

        # Log memory for tray
        if "log" in update:
            lvl = str(update.get("log_level", "info"))
            msg = str(update.get("log", ""))
            self._recent_logs.append((lvl, msg))
            if len(self._recent_logs) > 200:
                self._recent_logs = self._recent_logs[-200:]

    def _append_log(self, message: str, level: str = "info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        if self.log_widget is None:
            return
        try:
            if self.mode == "ctk":
                self.log_widget.configure(state="normal")
                self.log_widget.insert("end", line)
                self.log_widget.see("end")
                self.log_widget.configure(state="disabled")
            else:
                # basic Tk text widget
                self.log_widget.insert("end", line)
                self.log_widget.see("end")
        except Exception:
            pass

    # ---------------------------
    # Actions
    # ---------------------------
    def _request_full(self):
        if self.on_full_roster:
            self.push_log(">> MANUAL SYNC INITIATED...", "warn")
            try:
                self.on_full_roster()
            except Exception as e:
                self.push_log(f"Sync Error: {e}", "error")

    def _toggle_console_button(self):
        if not self.on_toggle_console:
            return
        try:
            state = self.on_toggle_console()
            self.set_console_visible(bool(state))
        except Exception:
            pass

    def _toggle_autostart_switch(self):
        if not self.on_toggle_autostart:
            return
        if self.autostart_var is None:
            return
        desired = bool(self.autostart_var.get())
        try:
            ok = self.on_toggle_autostart(desired)
            if ok:
                self.set_autostart_enabled(desired)
            else:
                # revert
                self.autostart_var.set(not desired)
        except Exception:
            try:
                self.autostart_var.set(not desired)
            except Exception:
                pass

    def _open_install_folder(self):
        folder = os.path.dirname(os.path.abspath(__file__))
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            pass

    def _verify_install(self):
        """
        Runs verify_install.bat if present.
        """
        folder = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(folder, "verify_install.bat")
        if not os.path.isfile(cand):
            _native_message_box("GAT Verify", "verify_install.bat no existe en la carpeta de instalación.")
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["cmd", "/c", cand], cwd=folder)
            else:
                subprocess.Popen([cand], cwd=folder)
        except Exception as e:
            _native_message_box("GAT Verify", f"No pude ejecutar verify_install.bat\n{e}")

    def _handle_close(self):
        try:
            if self.on_exit:
                self.on_exit()
        except Exception:
            pass
        try:
            if self.mode in ("ctk", "tk") and self.root is not None:
                self.root.destroy()
        except Exception:
            pass
        # tray close triggers icon stop from menu

    # ---------------------------
    # Run loop
    # ---------------------------
    def run(self):
        if not self.enabled:
            return

        if self.mode == "tray":
            if self._tray_icon is None:
                return
            try:
                self._tray_icon.run()
            except Exception as e:
                _native_message_box("GAT Tray", f"Tray run falló: {e}")
            return

        # Window modes
        if self.root is not None:
            try:
                self.root.mainloop()
            except Exception:
                pass
