#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI helpers for Guild Activity Tracker Bridge.

This module centralizes all tkinter visuals so the main bridge logic
can focus on processing and uploads without mixing aesthetic details.
"""
import os
import queue
from dataclasses import dataclass
from typing import Dict, Optional

try:
    import tkinter as tk  # type: ignore
    TK_AVAILABLE = True
except Exception:
    tk = None  # type: ignore
    TK_AVAILABLE = False


@dataclass
class UITheme:
    background: str = "#0f172a"
    foreground: str = "#e5e7eb"
    accent: str = "#facc15"
    muted: str = "#94a3b8"
    button_bg: str = "#1e293b"
    button_bg_active: str = "#334155"
    danger: str = "#ef4444"
    success: str = "#22c55e"
    warning: str = "#fbbf24"


class BridgeUI:
    def __init__(
        self,
        enabled: bool,
        icon_path: str,
        on_full_roster: Optional[callable] = None,
        on_exit: Optional[callable] = None,
        on_toggle_console: Optional[callable] = None,
        theme: Optional[UITheme] = None,
        console_visible: bool = True,
    ):
        self.enabled = enabled and TK_AVAILABLE
        self.icon_path = icon_path
        self.on_full_roster = on_full_roster
        self.on_exit = on_exit
        self.on_toggle_console = on_toggle_console
        self.theme = theme or UITheme()
        self.root: Optional["tk.Tk"] = None if tk is None else (tk.Tk() if self.enabled else None)
        self.queue: "queue.Queue[Dict[str, str]]" = queue.Queue()
        self.labels: Dict[str, "tk.StringVar"] = {}
        self.activity_var: Optional["tk.StringVar"] = None
        self.progress_var: Optional["tk.StringVar"] = None
        self.log_widget: Optional["tk.Text"] = None
        self.console_button_text: Optional["tk.StringVar"] = None
        self.console_visible = console_visible

        if not self.enabled or self.root is None:
            return

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.root.after(400, self._drain_queue)

    # ---------- UI construction ----------
    def _build_ui(self):
        assert self.root is not None
        t = self.theme

        self.root.title("Guild Activity Tracker Bridge")
        self.root.configure(bg=t.background)
        self.root.geometry("640x420")
        try:
            if os.path.isfile(self.icon_path):
                self.root.iconphoto(False, tk.PhotoImage(file=self.icon_path))
        except Exception:
            pass

        header = tk.Label(
            self.root,
            text="Guild Activity Tracker Bridge",
            bg=t.background,
            fg=t.accent,
            font=("Segoe UI", 15, "bold"),
            anchor="w",
            padx=12,
            pady=8,
        )
        header.pack(fill="x")

        fields = [
            ("wow", "Estado WoW"),
            ("watch", "Archivo vigilado"),
            ("parse", "Último parse"),
            ("upload", "Último upload"),
            ("latency", "Latencia"),
            ("payload", "Tamaño payload"),
            ("version", "Versión"),
            ("queue", "Cola local"),
        ]

        for key, label in fields:
            var = tk.StringVar(value=f"{label}: ...")
            self.labels[key] = var
            row = tk.Label(
                self.root,
                textvariable=var,
                bg=t.background,
                fg=t.foreground,
                anchor="w",
                justify="left",
                font=("Segoe UI", 10),
                padx=12,
                pady=2,
            )
            row.pack(fill="x")

        self.activity_var = tk.StringVar(value="Actividad: En espera")
        self.progress_var = tk.StringVar(value="Progreso: --")
        tk.Label(
            self.root,
            textvariable=self.activity_var,
            bg=t.background,
            fg=t.warning,
            anchor="w",
            padx=12,
            pady=4,
            font=("Segoe UI", 11, "bold"),
        ).pack(fill="x")
        tk.Label(
            self.root,
            textvariable=self.progress_var,
            bg=t.background,
            fg=t.accent,
            anchor="w",
            padx=12,
            pady=2,
            font=("Segoe UI", 10, "italic"),
        ).pack(fill="x")

        controls = tk.Frame(self.root, bg=t.background)
        controls.pack(fill="x", pady=6)
        tk.Button(
            controls,
            text="Enviar roster completo ahora",
            command=self._request_full,
            bg=t.button_bg,
            fg=t.foreground,
            activebackground=t.button_bg_active,
            activeforeground=t.accent,
            relief="groove",
            padx=10,
            pady=4,
        ).pack(side="left", padx=12)

        if self.on_toggle_console:
            self.console_button_text = tk.StringVar(
                value="Ocultar consola" if self.console_visible else "Mostrar consola"
            )
            tk.Button(
                controls,
                textvariable=self.console_button_text,
                command=self._toggle_console,
                bg=t.button_bg,
                fg=t.foreground,
                activebackground=t.button_bg_active,
                activeforeground=t.accent,
                relief="groove",
                padx=10,
                pady=4,
            ).pack(side="left", padx=8)

        log_frame = tk.LabelFrame(
            self.root,
            text="Eventos recientes",
            bg=t.background,
            fg=t.muted,
            bd=1,
            relief="groove",
            labelanchor="nw",
            padx=8,
            pady=6,
        )
        log_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self.log_widget = tk.Text(
            log_frame,
            height=8,
            bg="#0b1220",
            fg=t.foreground,
            insertbackground=t.accent,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_widget.pack(fill="both", expand=True)
        if self.log_widget is not None:
            self.log_widget.tag_config("info", foreground=t.foreground)
            self.log_widget.tag_config("warn", foreground=t.warning)
            self.log_widget.tag_config("error", foreground=t.danger)
            self.log_widget.tag_config("success", foreground=t.success)

        footer = tk.Label(
            self.root,
            text="Cierra esta ventana para salir del bridge.",
            bg=t.background,
            fg=t.muted,
            anchor="w",
            padx=10,
            pady=8,
            font=("Segoe UI", 9, "italic"),
        )
        footer.pack(fill="x", side="bottom")

    # ---------- State updates ----------
    def _drain_queue(self):
        try:
            while not self.queue.empty():
                update = self.queue.get_nowait()
                self._apply(update)
        finally:
            if self.root is not None:
                self.root.after(500, self._drain_queue)

    def _apply(self, update: Dict[str, str]):
        if not self.enabled:
            return
        for key, var in self.labels.items():
            if key in update:
                var.set(update[key])
        if "activity" in update and self.activity_var is not None:
            self.activity_var.set(update["activity"])
        if "progress" in update and self.progress_var is not None:
            self.progress_var.set(update["progress"])
        if "log" in update:
            self._append_log(update["log"], update.get("log_level", "info"))

    # ---------- Public API ----------
    def update_status(
        self,
        wow_running: bool,
        health: Dict[str, str],
        watch_path: str,
        activity: str = "",
        progress: str = "",
        queue_note: str = "",
    ):
        if not self.enabled:
            return
        status = {
            "wow": f"Estado WoW: {'Detectado' if wow_running else 'No detectado'}",
            "watch": f"Archivo vigilado: {watch_path}",
            "parse": f"Último parse: {health.get('last_parse_ok') or 'pendiente'}",
            "upload": f"Último upload: {health.get('last_upload_ok') or 'pendiente'}",
            "latency": f"Latencia: {health.get('last_latency_ms') or 's/d'} ms",
            "payload": f"Tamaño payload: {health.get('last_payload_size') or 's/d'} bytes",
            "version": f"Versión: {health.get('version')}",
            "queue": f"Cola local: {queue_note or 'vacía'}",
        }
        if activity:
            status["activity"] = f"Actividad: {activity}"
        if progress:
            status["progress"] = f"Progreso: {progress}"
        self.queue.put(status)

    def show_activity(self, message: str, progress: str = ""):
        if not self.enabled:
            return
        self.queue.put({"activity": f"Actividad: {message}", "progress": f"Progreso: {progress or '--'}"})

    def push_log(self, message: str, level: str = "info"):
        if not self.enabled:
            return
        self.queue.put({"log": message, "log_level": level})

    def _append_log(self, message: str, level: str = "info"):
        if self.log_widget is None:
            return
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", message + "\n", level if level in {"info", "warn", "error", "success"} else "info")
        # Mantener el log acotado (últimas 200 líneas)
        lines = int(self.log_widget.index('end-1c').split('.')[0])
        if lines > 220:
            self.log_widget.delete('1.0', f"{lines-200}.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _request_full(self):
        if self.on_full_roster:
            try:
                self.on_full_roster()
            except Exception:
                pass

    def _toggle_console(self):
        if not self.on_toggle_console:
            return
        try:
            new_state = self.on_toggle_console()
            self.set_console_visible(new_state)
        except Exception:
            pass

    def set_console_visible(self, visible: bool):
        self.console_visible = visible
        if self.console_button_text is not None:
            label = "Ocultar consola" if visible else "Mostrar consola"
            if self.root is not None:
                try:
                    self.root.after(0, lambda: self.console_button_text.set(label))
                    return
                except Exception:
                    pass
            self.console_button_text.set(label)

    def _handle_close(self):
        if self.on_exit:
            try:
                self.on_exit()
            except Exception:
                pass
        if self.root is not None:
            self.root.destroy()

    def run(self):
        if not self.enabled or self.root is None:
            return
        self.root.mainloop()

