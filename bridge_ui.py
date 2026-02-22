#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI profesional sin dependencias externas (solo tkinter/stdlib)."""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Callable

try:
    from ui.dashboard import Dashboard
    TK_OK = True
except Exception:
    Dashboard = None
    TK_OK = False


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
        theme: Optional[Any] = None,
        console_visible: bool = True,
    ):
        self.enabled = bool(enabled) and TK_OK
        self._on_full_roster = on_full_roster
        self._on_exit = on_exit
        self._on_toggle_console = on_toggle_console
        self._on_toggle_autostart = on_toggle_autostart
        self._autostart_enabled = autostart_enabled
        self._console_visible = console_visible
        self._dash = Dashboard() if self.enabled else None
        self._thread: Optional[threading.Thread] = None

    def update_status(self, wow_running: Optional[bool], health: Dict[str, Any], wow_path: str, **kwargs):
        if self._dash:
            self._dash.queue.put(("status", (wow_running, health, wow_path, kwargs.get("activity", "--"), kwargs.get("progress", "--"), kwargs.get("queue_note", "--"))))

    def show_activity(self, message: str, progress: str = "--"):
        self.push_log(f"{message} :: {progress}", level="INFO")

    def push_log(self, msg: str, level: str = "INFO"):
        if self._dash:
            self._dash.queue.put(("log", (level.upper(), msg)))

    def set_console_visible(self, visible: bool):
        self._console_visible = bool(visible)

    def set_autostart_enabled(self, enabled: bool):
        self._autostart_enabled = bool(enabled)

    def run(self):
        if not self._dash:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._dash.run, daemon=True)
        self._thread.start()
