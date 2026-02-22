import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Optional
from .theme import UITheme
from .widgets import StatCard

class Dashboard:
    def __init__(self, title: str = "Guild Activity Bridge"):
        self.theme = UITheme()
        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("1080x680")
        self.queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._build_ui()
        self.root.after(100, self._drain)

    def _build_ui(self):
        t = self.theme
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=t.bg)
        style.configure("Card.TFrame", background=t.panel)
        style.configure("TLabel", background=t.bg, foreground=t.text, font=(t.font, 10))
        style.configure("Muted.TLabel", background=t.panel, foreground=t.muted, font=(t.font, 9))
        style.configure("Value.TLabel", background=t.panel, foreground=t.accent, font=(t.font, 14, "bold"))

        self.root.configure(bg=t.bg)
        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x", padx=12, pady=12)
        self.status = tk.StringVar(value="Inicializando...")
        ttk.Label(top, textvariable=self.status, style="Value.TLabel").pack(anchor="w")

        cards = ttk.Frame(main)
        cards.pack(fill="x", padx=12)
        self.card_latency = StatCard(cards, "Latencia", "-- ms", t)
        self.card_payload = StatCard(cards, "Payload", "-- bytes", t)
        self.card_parse = StatCard(cards, "Último parse", "--", t)
        self.card_upload = StatCard(cards, "Último upload", "--", t)
        for c in (self.card_latency, self.card_payload, self.card_parse, self.card_upload):
            c.pack(side="left", padx=8, pady=8, fill="x", expand=True)

        self.log = tk.Text(main, wrap="word", bg=t.panel_alt, fg=t.text, insertbackground=t.text, relief="flat")
        self.log.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    def _drain(self):
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                wow_running, health, wow_path, activity, progress, queue_note = payload
                self.status.set(f"{activity} | {progress} | WoW: {'ON' if wow_running else 'OFF'} | Cola: {queue_note}")
                self.card_latency.set(f"{health.get('last_latency_ms','--')} ms")
                self.card_payload.set(f"{health.get('last_payload_size','--')} bytes")
                self.card_parse.set(str(health.get('last_parse_ok','--')))
                self.card_upload.set(str(health.get('last_upload_ok','--')))
            elif kind == "log":
                level, msg = payload
                self.log.insert("end", f"[{level}] {msg}\n")
                self.log.see("end")
        self.root.after(120, self._drain)

    def run(self):
        self.root.mainloop()
