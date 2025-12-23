#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI helpers for Guild Activity Tracker Bridge.

Reimagined by Gemini to be Epic, Modern, and Aesthetic.
Uses 'customtkinter' for a high-end dashboard look.
Includes dynamic progress bar visibility, Regex parsing, and Console Toggle.
"""
import os
import queue
import datetime
import re
from dataclasses import dataclass
from typing import Dict, Optional

# Try importing CustomTkinter for the epic look, fall back to standard if missing
try:
    import customtkinter as ctk  # type: ignore
    import tkinter as tk
    CTK_AVAILABLE = True
except ImportError:
    import tkinter as tk  # type: ignore
    ctk = None
    CTK_AVAILABLE = False


@dataclass
class UITheme:
    # Cyber-Security / Dark Ops Palette
    bg_dark: str = "#0f172a"      # Deep Slate
    bg_card: str = "#1e293b"      # Lighter Slate
    text_main: str = "#f1f5f9"    # White-ish
    text_dim: str = "#94a3b8"     # Gray
    accent_primary: str = "#3b82f6" # Intense Blue
    accent_success: str = "#10b981" # Emerald Green
    accent_warning: str = "#f59e0b" # Amber
    accent_danger: str = "#ef4444"  # Red
    terminal_bg: str = "#020617"  # Almost Black
    button_secondary: str = "#334155" # Slate for secondary buttons


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
        self.enabled = enabled
        self.icon_path = icon_path
        self.on_full_roster = on_full_roster
        self.on_exit = on_exit
        self.on_toggle_console = on_toggle_console
        self.theme = theme or UITheme()
        self.console_visible = console_visible
        
        # State containers
        self.root = None
        self.queue: "queue.Queue[Dict[str, str]]" = queue.Queue()
        
        # UI Element References
        self.labels: Dict[str, any] = {}
        self.progress_container = None 
        self.progress_bar = None
        self.status_label = None
        self.log_widget = None
        self.btn_console = None # Reference to the console toggle button
        
        if not self.enabled:
            return

        self._init_window()
        
        if self.root:
            self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
            self.root.after(400, self._drain_queue)

    def _init_window(self):
        """Initializes the main window with the chosen library."""
        if CTK_AVAILABLE:
            ctk.set_appearance_mode("Dark")
            ctk.set_default_color_theme("dark-blue")
            self.root = ctk.CTk()
            self.root.geometry("850x620")
            self._build_ui_modern()
        else:
            self.root = tk.Tk()
            self.root.geometry("640x480")
            self._build_ui_legacy()

        self.root.title("Guild Tracker // Command Bridge")
        try:
            if os.path.isfile(self.icon_path):
                img = tk.PhotoImage(file=self.icon_path)
                self.root.iconphoto(False, img)
        except Exception:
            pass

    # ---------- MODERN UI BUILDER (CustomTkinter) ----------
    def _build_ui_modern(self):
        t = self.theme
        self.root.configure(fg_color=t.bg_dark)

        # 1. Header Section
        header_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))

        title = ctk.CTkLabel(
            header_frame, 
            text="GUILD TRACKER BRIDGE", 
            font=("Roboto Medium", 24), 
            text_color=t.text_main
        )
        title.pack(side="left")

        version_badge = ctk.CTkLabel(
            header_frame,
            text="v2.0 Connected",
            font=("Roboto", 12),
            text_color=t.accent_success,
            fg_color=t.bg_card,
            corner_radius=6,
            padx=10,
            pady=2
        )
        version_badge.pack(side="right")

        # 2. Main Dashboard Grid (2x2 Stats)
        grid_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        grid_frame.pack(fill="x", padx=20, pady=10)
        grid_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        def create_stat_card(parent, title, key, col):
            frame = ctk.CTkFrame(parent, fg_color=t.bg_card, corner_radius=10)
            frame.grid(row=0, column=col, padx=5, sticky="ew")
            
            lbl_title = ctk.CTkLabel(frame, text=title, font=("Roboto", 11), text_color=t.text_dim)
            lbl_title.pack(anchor="w", padx=15, pady=(10, 0))
            
            lbl_value = ctk.CTkLabel(frame, text="--", font=("Roboto Medium", 14), text_color=t.text_main)
            lbl_value.pack(anchor="w", padx=15, pady=(0, 10))
            
            self.labels[key] = lbl_value
            return frame

        create_stat_card(grid_frame, "GAME STATUS", "wow", 0)
        create_stat_card(grid_frame, "LAST UPLOAD", "upload", 1)
        create_stat_card(grid_frame, "LATENCY", "latency", 2)
        create_stat_card(grid_frame, "PAYLOAD", "payload", 3)

        # 3. Operations & Progress Center
        ops_frame = ctk.CTkFrame(self.root, fg_color=t.bg_card, corner_radius=10)
        ops_frame.pack(fill="x", padx=20, pady=10)

        # Activity Status Line
        self.status_label = ctk.CTkLabel(
            ops_frame, 
            text="SYSTEM IDLE", 
            font=("Consolas", 14, "bold"), 
            text_color=t.accent_primary
        )
        self.status_label.pack(anchor="w", padx=15, pady=(15, 5))

        # --- Progress Container (Ocultable) ---
        self.progress_container = ctk.CTkFrame(ops_frame, fg_color="transparent")
        
        self.progress_bar = ctk.CTkProgressBar(self.progress_container, orientation="horizontal", height=12, corner_radius=6)
        self.progress_bar.pack(fill="x", padx=0, pady=(0, 5))
        self.progress_bar.set(0)
        self.progress_bar.configure(progress_color=t.accent_primary)

        self.labels['progress_text'] = ctk.CTkLabel(
            self.progress_container, 
            text="Initializing...", 
            font=("Roboto", 11), 
            text_color=t.text_dim
        )
        self.labels['progress_text'].pack(anchor="e", padx=0, pady=(0, 5))
        # --------------------------------------

        self.bottom_spacer = ctk.CTkLabel(ops_frame, text="", height=5)
        self.bottom_spacer.pack(side="bottom")


        # 4. Control Deck
        ctrl_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        ctrl_frame.pack(fill="x", padx=20, pady=5)

        watch_label = ctk.CTkLabel(ctrl_frame, text="Watching: ...", font=("Consolas", 10), text_color=t.text_dim)
        watch_label.pack(side="left")
        self.labels['watch'] = watch_label

        # Buttons Container (Right side)
        btn_frame = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        btn_frame.pack(side="right")

        # Toggle Console Button
        if self.on_toggle_console:
            btn_text = "Hide Console" if self.console_visible else "Show Console"
            self.btn_console = ctk.CTkButton(
                btn_frame,
                text=btn_text,
                command=self._toggle_console,
                font=("Roboto", 11),
                fg_color=t.button_secondary,
                hover_color="#475569",
                corner_radius=6,
                height=32,
                width=100
            )
            self.btn_console.pack(side="left", padx=(0, 10))

        # Force Sync Button
        btn_force = ctk.CTkButton(
            btn_frame,
            text="FORCE SYNC",
            command=self._request_full,
            font=("Roboto", 12, "bold"),
            fg_color=t.accent_primary,
            hover_color="#2563eb",
            corner_radius=6,
            height=32
        )
        btn_force.pack(side="left")
        

        # 5. Terminal / Log Area
        log_label = ctk.CTkLabel(self.root, text="> SYSTEM LOGS", font=("Consolas", 11, "bold"), text_color=t.text_dim)
        log_label.pack(anchor="w", padx=25, pady=(10, 0))

        self.log_widget = ctk.CTkTextbox(
            self.root, 
            fg_color=t.terminal_bg, 
            text_color=t.accent_success, 
            font=("Consolas", 11),
            corner_radius=8
        )
        self.log_widget.pack(fill="both", expand=True, padx=20, pady=(5, 20))
        self.log_widget.configure(state="disabled")

    # ---------- LEGACY UI (Fallback) ----------
    def _build_ui_legacy(self):
        lbl = tk.Label(self.root, text="Please install 'customtkinter' for the Modern UI", bg="red", fg="white")
        lbl.pack(fill="x")
        # Legacy fallback logic omitted for brevity as user wants modern

    # ---------- LOGIC & UPDATES ----------
    def _drain_queue(self):
        try:
            while not self.queue.empty():
                update = self.queue.get_nowait()
                self._apply(update)
        finally:
            if self.root is not None:
                self.root.after(500, self._drain_queue)

    def _apply(self, update: Dict[str, str]):
        if not self.root:
            return
            
        # Update text labels
        for key, value in update.items():
            if key in self.labels and CTK_AVAILABLE:
                self.labels[key].configure(text=value)

        # Visual Cues for specific keys
        if "wow" in update and CTK_AVAILABLE:
            if "ONLINE" in update["wow"]:
                self.labels["wow"].configure(text_color=self.theme.accent_success)
            else:
                self.labels["wow"].configure(text_color=self.theme.accent_danger)

        if "activity" in update and self.status_label and CTK_AVAILABLE:
            self.status_label.configure(text=update["activity"].upper())

        # --- LOGICA DE VISIBILIDAD DE LA BARRA ---
        if "show_progress" in update and self.progress_container and CTK_AVAILABLE:
            if update["show_progress"]:
                if not self.progress_container.winfo_ismapped():
                    self.progress_container.pack(fill="x", padx=15, pady=(0, 5))
            else:
                if self.progress_container.winfo_ismapped():
                    self.progress_container.pack_forget()

        if "progress_float" in update and self.progress_bar and CTK_AVAILABLE:
            try:
                val = float(update["progress_float"])
                self.progress_bar.set(val)
            except ValueError:
                pass

        if "log" in update:
            self._append_log(update["log"], update.get("log_level", "info"))

    def update_status(
        self,
        wow_running: bool,
        health: Dict[str, str],
        watch_path: str,
        activity: str = "",
        progress: str = "",
        queue_note: str = "",
    ):
        """Public API: Maps the raw data to the UI format."""
        if not self.root:
            return
            
        wow_text = "ONLINE" if wow_running else "OFFLINE"
        
        status = {
            "wow": wow_text,
            "watch": f"File: {os.path.basename(watch_path)}",
            "upload": health.get('last_upload_ok') or 'Pending',
            "latency": f"{health.get('last_latency_ms') or '--'} ms",
            "payload": f"{health.get('last_payload_size') or '--'} bytes",
            "queue": queue_note or 'Empty',
        }
        
        # Hide bar if idle
        is_idle = not activity or "espera" in activity.lower() or "idle" in activity.lower()
        status["show_progress"] = not is_idle

        if activity:
            status["activity"] = activity
        else:
            status["activity"] = "SYSTEM IDLE"
        
        if progress:
            status["progress_text"] = progress
            
            # --- REGEX FIX ---
            match = re.search(r"(\d+)\s*/\s*(\d+)", progress)
            if match:
                try:
                    curr = int(match.group(1))
                    total = int(match.group(2))
                    if total > 0:
                        status["progress_float"] = curr / total
                except Exception:
                    status["progress_float"] = 0.0
            elif "%" in progress:
                 try:
                    p_val = re.search(r"(\d+(\.\d+)?)", progress)
                    if p_val:
                        status["progress_float"] = float(p_val.group(1)) / 100
                 except:
                     pass
        else:
            status["progress_float"] = 0.0

        self.queue.put(status)

    def show_activity(self, message: str, progress: str = ""):
        self.update_status(False, {}, "", activity=message, progress=progress)

    def push_log(self, message: str, level: str = "info"):
        self.queue.put({"log": message, "log_level": level})

    def _append_log(self, message: str, level: str = "info"):
        if not self.log_widget:
            return
            
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_msg = f"[{timestamp}] {message}\n"
        
        if CTK_AVAILABLE:
            self.log_widget.configure(state="normal")
            self.log_widget.insert("end", formatted_msg)
            self.log_widget.see("end")
            self.log_widget.configure(state="disabled")

    def _request_full(self):
        if self.on_full_roster:
            self.push_log(">> MANUAL SYNC INITIATED...", "warn")
            try:
                self.on_full_roster()
            except Exception as e:
                self.push_log(f"Sync Error: {e}", "error")

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
        if self.btn_console and CTK_AVAILABLE:
            label = "Hide Console" if visible else "Show Console"
            try:
                self.btn_console.configure(text=label)
            except Exception:
                pass

    def _handle_close(self):
        if self.on_exit:
            self.on_exit()
        if self.root:
            self.root.destroy()

    def run(self):
        if self.root:
            self.root.mainloop()
