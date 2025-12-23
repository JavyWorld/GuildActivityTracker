"""
Guild Activity Tracker Bridge - Versión 43.0 (THE RELAY TANK)
Robust bridge between WoW SavedVariables (GuildActivityTrackerDB) and:
  1) Website API (/api/upload) with session-based chunking

Principios:
- NO rompe funciones existentes: mantiene los mismos métodos públicos del V42.
- Datos completos (sin "atajos" de contenido): NO se descarta data de chat; se normaliza.
- Subida web resiliente: reintentos fuertes + ajuste automático si hay 413.
- Evita inflar la DB web: sube snapshots de stats incrementalmente (persistiendo estado local).
- Normaliza nombres: unifica "Nombre" vs "Nombre-Reino" (caso típico de roster sin reino).
"""

import os
import sys
import time
import logging
import json
import re
import math
import uuid
import threading
import queue
import platform
import importlib.util
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple, Optional, Iterable

import requests

from dotenv import load_dotenv
import colorama
from colorama import Fore
import slpp

psutil_spec = importlib.util.find_spec("psutil")
if psutil_spec:
    import psutil  # type: ignore
else:
    psutil = None  # type: ignore

tk_spec = importlib.util.find_spec("tkinter")
if tk_spec:
    import tkinter as tk  # type: ignore
else:
    tk = None  # type: ignore

# UI opcional mejorada (customtkinter + PIL). Si no están instalados, la app cae a modo consola.
ctk_spec = importlib.util.find_spec("customtkinter")
if ctk_spec:
    import customtkinter as ctk  # type: ignore
else:
    ctk = None  # type: ignore

pil_spec = importlib.util.find_spec("PIL")
if pil_spec:
    from PIL import Image, ImageEnhance  # type: ignore
else:
    Image = None  # type: ignore
    ImageEnhance = None  # type: ignore

try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:
    ZoneInfo = None  # type: ignore

colorama.init(autoreset=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# =========================
# Defaults (pueden override por .env)
# =========================
DEFAULT_WEB_API_URL = os.getenv("WEB_API_URL", "https://nexonir.replit.app/api/upload")
DEFAULT_WEB_API_KEY = os.getenv("WEB_API_KEY", "4df6e5051f959afa012c2498592765c7417e09b05b1f0edf451d34fb6b694ddf")
DEFAULT_HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "120"))
DEFAULT_BATCH_SIZE = int(os.getenv("BATCH_SIZE", "80"))  # 80 más seguro contra 413 que 100
DEFAULT_STATS_BATCH_SIZE = int(os.getenv("STATS_BATCH_SIZE", "80"))
DEFAULT_TZ = os.getenv("LOCAL_TIMEZONE", "America/New_York")

STATE_FILENAME = os.getenv("BRIDGE_STATE_FILE", "gat_bridge_state.json")
LOCAL_QUEUE_FILE = os.getenv("UPLOAD_QUEUE_FILE", "upload_queue.jsonl")
UPLOADER_VERSION = "43.0"


# =========================
# UI helpers / estilos
# =========================
COLORS = {
    "bg": "#0B1220",
    "panel": "#111C2E",
    "panel2": "#0F1A2B",
    "border": "#1B2A44",
    "text": "#EAF0FF",
    "muted": "#AAB7D1",
    "gold": "#F5C542",
    "ok": "#2ECC71",
    "warn": "#F39C12",
    "err": "#E74C3C",
    "info": "#3498DB",
}

FONT_TITLE = ("Segoe UI", 22, "bold")
FONT_H2 = ("Segoe UI", 14, "bold")
FONT_BODY = ("Segoe UI", 13)
FONT_MONO = ("Consolas", 11)


def resource_path(rel_path: str) -> str:
    """Permite cargar assets tanto en entorno dev como en ejecutable PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel_path)


def shorten_path(path: str, max_len: int = 48) -> str:
    if len(path) <= max_len:
        return path
    head = path[: max_len // 2 - 2]
    tail = path[-max_len // 2 + 2 :]
    return f"{head}...{tail}"


def fmt_age(ts_iso: Optional[str]) -> str:
    if not ts_iso:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_iso)
    except Exception:
        return ts_iso
    delta = datetime.now(dt.tzinfo or timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"hace {seconds}s"
    if seconds < 3600:
        return f"hace {seconds // 60}m"
    if seconds < 86400:
        return f"hace {seconds // 3600}h"
    return f"hace {seconds // 86400}d"


@dataclass
class BridgeState:
    last_uploaded_stats_ts: int = 0
    last_web_session_id: str = ""
    roster_snapshot: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "BridgeState":
        return BridgeState(
            last_uploaded_stats_ts=int(d.get("last_uploaded_stats_ts", 0) or 0),
            last_web_session_id=str(d.get("last_web_session_id", "") or ""),
            roster_snapshot=d.get("roster_snapshot", {}) if isinstance(d.get("roster_snapshot", {}), dict) else {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_uploaded_stats_ts": int(self.last_uploaded_stats_ts),
            "last_web_session_id": str(self.last_web_session_id or ""),
            "roster_snapshot": self.roster_snapshot,
        }


class LocalUploadQueue:
    def __init__(self, path: str):
        self.path = path
@@ -187,51 +257,54 @@ class Config:
        # Realm default para normalizar nombres (si el roster viene sin "-Reino")
        self.default_realm = os.getenv("GUILD_REALM", os.getenv("DEFAULT_REALM", "")).replace(" ", "")

        # Loop
        self.poll_interval = int(os.getenv("POLL_INTERVAL", "5"))
        self.wow_process_names = [
            n.strip() for n in os.getenv(
                "WOW_PROCESS_NAMES", "Wow.exe,Wow-64.exe,WowT.exe,WowClassic.exe"
            ).split(",")
            if n.strip()
        ]

        # Web upload
        self.web_api_url = os.getenv("WEB_API_URL", DEFAULT_WEB_API_URL)
        self.web_api_key = os.getenv("WEB_API_KEY", DEFAULT_WEB_API_KEY)
        self.http_timeout = int(os.getenv("HTTP_TIMEOUT", str(DEFAULT_HTTP_TIMEOUT)))
        self.batch_size = int(os.getenv("BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
        self.stats_batch_size = int(os.getenv("STATS_BATCH_SIZE", str(DEFAULT_STATS_BATCH_SIZE)))

        # Behavior toggles
        self.enable_web_upload = os.getenv("ENABLE_WEB_UPLOAD", "true").lower() == "true"
        self.enable_stats_incremental_web = os.getenv("ENABLE_STATS_INCREMENTAL_WEB", "true").lower() == "true"
        self.enable_ui = os.getenv("ENABLE_UI", "true").lower() == "true"
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.ui_icon_path = os.path.normpath(
            os.getenv("GAT_ICON_PATH", os.path.join(base_dir, "Media", "app.ico"))
        )
        self.ui_logo_path = os.path.normpath(
            os.getenv("GAT_LOGO_PATH", os.path.join(base_dir, "Media", "gat_logo.png"))
        )

        # Safety: si se detecta roster muy chico, NO saltar (guild pequeña). Ajustable:
        self.min_roster_size = int(os.getenv("MIN_ROSTER_SIZE", "1"))

        self._validate()

    def _validate(self):
        if not self.wow_addon_path or self.wow_addon_path == '.':
            detected = self._auto_detect_wow_addon_path()
            if detected:
                self.wow_addon_path = detected
                logger.info(f"{Fore.GREEN}Detectado GuildActivityTracker.lua en: {self.wow_addon_path}")
            else:
                prompted = self._prompt_wow_addon_path()
                if prompted:
                    self.wow_addon_path = prompted
                    logger.info(f"{Fore.GREEN}Ruta configurada manualmente: {self.wow_addon_path}")
                else:
                    raise ValueError("Error en WOW_ADDON_PATH: está vacío o inválido. Define la ruta en .env o como variable de entorno, o coloca GuildActivityTracker.lua en la ubicación estándar.")

        if not os.path.isfile(self.wow_addon_path):
            logger.warning(f"{Fore.YELLOW}AVISO: Archivo LUA no encontrado en {self.wow_addon_path}. "
                           f"El bridge quedará vigilando hasta que exista.")

@@ -341,271 +414,585 @@ class Config:
                return ""

            expanded = os.path.normpath(os.path.expandvars(os.path.expanduser(user_input)))

            if os.path.isfile(expanded) and expanded.lower().endswith(".lua"):
                return expanded

            if not os.path.isdir(expanded):
                print(f"No encontré el directorio: {expanded}. Intenta de nuevo.\n")
                continue

            detected = self._auto_detect_wow_addon_path(manual_base=expanded)
            if detected:
                if not os.path.isfile(detected):
                    print("No encontré GuildActivityTracker.lua todavía, pero usaré esta ruta y esperaré a que se cree:")
                    print(f"  {detected}\n")
                    confirm = input("¿Quieres usarla? [S/n]: ").strip().lower()
                    if confirm in ("", "s", "si", "sí"):
                        return detected
                else:
                    return detected

            print("No pude localizar GuildActivityTracker.lua en la ruta indicada. Verifica e intenta nuevamente.\n")


class UILogHandler(logging.Handler):
    """Envía las líneas de log al panel UI sin bloquear el loop."""

    def __init__(self, ui: "BridgeUI"):
        super().__init__()
        self.ui = ui

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname.upper()
            msg = self.format(record)
            self.ui.push_log(level, msg)
        except Exception:
            pass


class BridgeUI:
    def __init__(
        self,
        enabled: bool,
        icon_path: str,
        logo_path: str,
        on_full_roster: Optional[callable] = None,
        on_open_folder: Optional[callable] = None,
        on_copy_path: Optional[callable] = None,
        on_restart: Optional[callable] = None,
        on_exit: Optional[callable] = None,
    ):
        self.enabled = enabled and ctk is not None
        self.icon_path = icon_path
        self.logo_path = logo_path
        self.on_full_roster = on_full_roster
        self.on_open_folder = on_open_folder
        self.on_copy_path = on_copy_path
        self.on_restart = on_restart
        self.on_exit = on_exit
        self.state: Dict[str, Any] = {
            "wow_detected": False,
            "watch_path": "",
            "last_parse": None,
            "last_upload": None,
            "latency_ms": None,
            "payload_bytes": None,
            "version": UPLOADER_VERSION,
            "uploading": False,
            "last_error": None,
        }
        self.event_q: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self.root: Optional[ctk.CTk] = None if not self.enabled else ctk.CTk()

        if not self.enabled:
            if enabled and ctk is None:
                logger.info("Interfaz gráfica moderna no disponible (customtkinter no instalado). Usando modo consola.")
            return

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root.title("Guild Activity Tracker Bridge — Command Center")
        self.root.geometry("1160x700")
        self.root.minsize(1000, 640)
        self.root.configure(fg_color=COLORS["bg"])
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        try:
            if os.path.isfile(self.icon_path):
                self.root.iconbitmap(self.icon_path)
        except Exception:
            pass
        try:
            if tk is not None and os.path.isfile(self.icon_path):
                self.root.iconphoto(False, tk.PhotoImage(file=self.icon_path))
        except Exception:
            pass

        self.logo_header = None
        self.logo_watermark = None
        if Image is not None:
            try:
                base_img = Image.open(self.logo_path).convert("RGBA")
                head = base_img.resize((48, 48))
                self.logo_header = ctk.CTkImage(light_image=head, dark_image=head, size=(48, 48))

                wm = base_img.resize((780, 780))
                if ImageEnhance is not None:
                    wm = ImageEnhance.Color(wm).enhance(0.25)
                    wm = ImageEnhance.Contrast(wm).enhance(0.8)
                alpha = wm.split()[-1].point(lambda a: int(a * 0.08))
                wm.putalpha(alpha)
                self.logo_watermark = ctk.CTkImage(light_image=wm, dark_image=wm, size=(780, 780))
            except Exception:
                self.logo_header = None
                self.logo_watermark = None

        self._build_header()
        self._build_body()
        self._build_footer()
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.root.after(120, self._poll_events)

    # ---------- UI pieces ----------
    def _build_header(self):
        self.header = ctk.CTkFrame(self.root, fg_color=COLORS["panel2"], corner_radius=0)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.grid_columnconfigure(1, weight=1)

        if self.logo_header:
            ctk.CTkLabel(self.header, image=self.logo_header, text="").grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=16)
        else:
            ctk.CTkLabel(self.header, text="GAT", text_color=COLORS["gold"], font=("Segoe UI", 24, "bold"))\
                .grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=16)

        self.title_lbl = ctk.CTkLabel(self.header, text="Guild Activity Tracker Bridge", font=FONT_TITLE, text_color=COLORS["text"])
        self.title_lbl.grid(row=0, column=1, sticky="w", pady=(16, 0))

        self.sub_lbl = ctk.CTkLabel(
            self.header,
            text="Command Center • Telemetry & Upload Control",
            font=FONT_BODY,
            text_color=COLORS["muted"],
        )
        self.sub_lbl.grid(row=1, column=1, sticky="w", pady=(0, 16))

        self.pill = ctk.CTkFrame(self.header, corner_radius=999, fg_color=COLORS["info"], border_color=COLORS["border"], border_width=1)
        self.pill.grid(row=0, column=2, rowspan=2, padx=18, pady=16, sticky="e")
        self.pill_lbl = ctk.CTkLabel(self.pill, text="IDLE", font=("Segoe UI", 12, "bold"), text_color="#081018")
        self.pill_lbl.pack(padx=12, pady=6)

    def _build_body(self):
        self.body = ctk.CTkFrame(self.root, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=18, pady=18)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        if self.logo_watermark:
            wm = ctk.CTkLabel(self.body, image=self.logo_watermark, text="")
            wm.place(relx=0.55, rely=0.52, anchor="center")

        # Telemetría
        self.left = ctk.CTkFrame(self.body, fg_color=COLORS["panel2"], corner_radius=18)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.left.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(self.left, text="Telemetry", font=FONT_H2, text_color=COLORS["text"]).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 10))

        self.card_wow = self._make_card(self.left, "Estado WoW", "🛰️")
        self.card_file = self._make_card(self.left, "Archivo vigilado", "📁")
        self.card_parse = self._make_card(self.left, "Último parse", "⏱️")
        self.card_upload = self._make_card(self.left, "Último upload", "☁️")
        self.card_latency = self._make_card(self.left, "Latencia", "📡")
        self.card_payload = self._make_card(self.left, "Payload", "📦")
        self.card_version = self._make_card(self.left, "Versión", "🏷️")

        cards = [
            (self.card_wow, 1, 0), (self.card_file, 1, 1),
            (self.card_parse, 2, 0), (self.card_upload, 2, 1),
            (self.card_latency, 3, 0), (self.card_payload, 3, 1),
            (self.card_version, 4, 0),
        ]
        for card, r, c in cards:
            card.grid(row=r, column=c, sticky="nsew", padx=12, pady=10)

        # Log + acciones
        self.right = ctk.CTkFrame(self.body, fg_color=COLORS["panel2"], corner_radius=18)
        self.right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.right.grid_rowconfigure(1, weight=1)
        self.right.grid_columnconfigure(0, weight=1)

        topbar = ctk.CTkFrame(self.right, fg_color="transparent")
        topbar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
        topbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(topbar, text="Activity Log", font=FONT_H2, text_color=COLORS["text"]).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(topbar, text="Copiar log", width=100, fg_color=COLORS["panel"], hover_color=COLORS["border"], command=self._copy_log).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(topbar, text="Limpiar", width=90, fg_color=COLORS["panel"], hover_color=COLORS["border"], command=self._clear_log).grid(row=0, column=2, padx=(8, 0))

        self.log = ctk.CTkTextbox(self.right, font=FONT_MONO, wrap="word", fg_color=COLORS["panel"], corner_radius=14, text_color=COLORS["text"])
        self.log.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
        self.log.configure(state="disabled")
        for name, color in ("INFO", COLORS["info"]), ("WARNING", COLORS["warn"]), ("ERROR", COLORS["err"]):
            try:
                self.log.tag_config(name, foreground=color)
            except Exception:
                pass

        action_bar = ctk.CTkFrame(self.right, fg_color="transparent")
        action_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        action_bar.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkButton(action_bar, text="Abrir carpeta", fg_color=COLORS["panel"], hover_color=COLORS["border"], command=self._open_folder).grid(row=0, column=0, padx=6)
        ctk.CTkButton(action_bar, text="Copiar ruta", fg_color=COLORS["panel"], hover_color=COLORS["border"], command=self._copy_path).grid(row=0, column=1, padx=6)
        ctk.CTkButton(action_bar, text="Reiniciar bridge", fg_color=COLORS["panel"], hover_color=COLORS["border"], command=self._restart).grid(row=0, column=2, padx=6)
        ctk.CTkButton(action_bar, text="Minimizar", fg_color=COLORS["panel"], hover_color=COLORS["border"], command=self._minimize).grid(row=0, column=3, padx=6)

    def _build_footer(self):
        self.footer = ctk.CTkFrame(self.root, fg_color=COLORS["panel2"], corner_radius=0)
        self.footer.grid(row=2, column=0, sticky="ew")
        self.footer.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(self.footer, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="ew", padx=18, pady=12)
        inner.grid_columnconfigure(0, weight=1)

        self.btn_send = ctk.CTkButton(
            inner,
            text="Enviar roster completo ahora",
            height=46,
            fg_color=COLORS["gold"],
            hover_color="#FFD36A",
            text_color="#081018",
            font=("Segoe UI", 14, "bold"),
            command=self._request_full,
        )
        self.btn_send.grid(row=0, column=0, sticky="w")

        self.hint = ctk.CTkLabel(
            inner,
            text="Tip: Mantén el bridge abierto para que el watcher siga subiendo.",
            text_color=COLORS["muted"],
            font=FONT_BODY,
        )
        self.hint.grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _make_card(self, parent, label: str, icon: str):
        frame = ctk.CTkFrame(parent, fg_color=COLORS["panel"], corner_radius=14)
        frame.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text=f"{icon} {label}", text_color=COLORS["muted"], font=FONT_BODY).grid(row=0, column=0, sticky="w")
        value_lbl = ctk.CTkLabel(frame, text="—", text_color=COLORS["text"], font=("Segoe UI", 16, "bold"))
        value_lbl.grid(row=1, column=0, sticky="w", padx=14, pady=(6, 12))
        return value_lbl

    # ---------- Interacción ----------
    def _request_full(self):
        if self.on_full_roster:
            self.btn_send.configure(state="disabled")
            self.push_log("INFO", "Solicitud manual: Enviando roster completo…")
            threading.Thread(target=self.on_full_roster, daemon=True).start()

    def _open_folder(self):
        if self.on_open_folder:
            try:
                self.on_open_folder()
            except Exception:
                pass

    def _copy_path(self):
        if self.on_copy_path:
            try:
                self.on_copy_path()
            except Exception:
                pass

    def _restart(self):
        if self.on_restart:
            try:
                self.on_restart()
            except Exception:
                pass

    def _minimize(self):
        try:
            self.root.iconify()
        except Exception:
            pass

    def _clear_log(self):
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
        except Exception:
            pass

    def _copy_log(self):
        try:
            content = self.log.get("1.0", "end")
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.push_log("INFO", "Log copiado al portapapeles")
        except Exception:
            pass

    def _handle_close(self):
        if self.on_exit:
            try:
                self.on_exit()
            except Exception:
                pass
        if self.root is not None:
            self.root.destroy()

    # ---------- Loop ----------
    def _poll_events(self):
        try:
            while True:
                evt = self.event_q.get_nowait()
                self._apply_event(evt)
        except queue.Empty:
            pass
        finally:
            if self.root is not None:
                self.root.after(160, self._poll_events)

    def _apply_event(self, evt: Tuple[str, Any]):
        if not self.enabled:
            return
        kind, data = evt
        if kind == "STATUS":
            self._render_status(data)
        elif kind == "LOG":
            level, msg = data
            self._append_log(level, msg)
        elif kind == "UI":
            if data.get("enable_send"):
                self.btn_send.configure(state="normal")

    def _append_log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {level:<5} {msg}\n"
        try:
            self.log.configure(state="normal")
            tag = "ERROR" if level.startswith("ERR") else ("WARNING" if "WARN" in level else "INFO")
            self.log.insert("end", line, tag)
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:
            pass

    def _render_status(self, data: Dict[str, Any]):
        wow = data.get("wow")
        file_txt = data.get("file")
        last_parse = data.get("last_parse")
        last_upload = data.get("last_upload")
        latency = data.get("latency_ms")
        payload = data.get("payload_bytes")
        version = data.get("version")
        pill = data.get("pill")
        uploading = data.get("uploading")

        if wow is not None:
            txt, ok = wow
            self.card_wow.configure(text_color=COLORS["ok"] if ok else COLORS["err"])
            self.card_wow.configure(text=txt)
        if file_txt is not None:
            self.card_file.configure(text=file_txt)
        if last_parse is not None:
            self.card_parse.configure(text=last_parse)
        if last_upload is not None:
            self.card_upload.configure(text=last_upload)
        if latency is not None:
            col = COLORS["ok"] if latency < 300 else (COLORS["warn"] if latency < 900 else COLORS["err"])
            self.card_latency.configure(text=f"{latency} ms", text_color=col)
        if payload is not None:
            self.card_payload.configure(text=f"{payload} bytes")
        if version is not None:
            self.card_version.configure(text=str(version))
        if pill is not None:
            label, kind = pill
            self._set_pill(label, kind)
        if uploading is not None:
            if uploading:
                self._set_pill("SUBIENDO…", "info")

    def _set_pill(self, text: str, kind: str):
        color = COLORS.get(kind, COLORS["info"])
        self.pill.configure(fg_color=color)
        self.pill_lbl.configure(text=text, text_color="#081018")

    # ---------- API público ----------
    def run(self):
        if not self.enabled:
            return
        self.root.mainloop()

    def push_status(self, data: Dict[str, Any]):
        if self.enabled:
            self.event_q.put(("STATUS", data))

    def push_log(self, level: str, msg: str):
        if self.enabled:
            self.event_q.put(("LOG", (level, msg)))

    def update(self, wow_running: bool, health: Dict[str, Any], watch_path: str):
        if not self.enabled:
            return
        data = {
            "wow": ("WoW detectado" if wow_running else "WoW no detectado", wow_running),
            "file": shorten_path(watch_path),
            "last_parse": f"{health.get('last_parse_ok') or 'pendiente'} ({fmt_age(health.get('last_parse_ok'))})" if health.get('last_parse_ok') else "pendiente",
            "last_upload": f"{health.get('last_upload_ok') or 'pendiente'} ({fmt_age(health.get('last_upload_ok'))})" if health.get('last_upload_ok') else "pendiente",
            "latency_ms": health.get("last_latency_ms"),
            "payload_bytes": health.get("last_payload_size"),
            "version": health.get("version", UPLOADER_VERSION),
        }

        pill_kind = "ok" if wow_running else "err"
        pill_text = "ONLINE" if wow_running else "WoW no detectado"
        if health.get("uploading"):
            pill_kind, pill_text = "info", "SUBIENDO…"
        if health.get("last_error"):
            pill_kind, pill_text = "err", "ERROR"
        data["pill"] = (pill_text, pill_kind)

        self.push_status(data)

    def notify_uploading(self, uploading: bool):
        if not self.enabled:
            return
        self.push_status({"uploading": uploading})

    def enable_send_button(self):
        if self.enabled:
            self.event_q.put(("UI", {"enable_send": True}))


class GuildActivityBridge:
    def __init__(self, config: Config):
        self.config = config
        self.lua_parser = slpp.SLPP()
        self.last_mtime = 0
        self.health = {
            "last_upload_ok": None,
            "last_parse_ok": None,
            "last_latency_ms": None,
            "last_payload_size": None,
            "version": UPLOADER_VERSION,
            "uploading": False,
            "last_error": None,
        }

        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": self.config.web_api_key, "Content-Type": "application/json"})

        self.local_queue = LocalUploadQueue(os.path.join(os.path.dirname(os.path.abspath(__file__)), LOCAL_QUEUE_FILE))

        # Estado persistente (para stats incremental al Web)
        self.state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILENAME)
        self.state = self._load_state()
        self._stop_event = threading.Event()
        self._force_full_roster = threading.Event()
        self._force_reason = "manual"
        self.ui = BridgeUI(
            self.config.enable_ui,
            self.config.ui_icon_path,
            self.config.ui_logo_path,
            on_full_roster=lambda: self.request_full_roster("manual-ui"),
            on_open_folder=self._open_watch_folder,
            on_copy_path=self._copy_watch_path,
            on_restart=self._restart_bridge,
            on_exit=self.stop,
        )
        if self.ui.enabled:
            handler = UILogHandler(self.ui)
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)


    # =========================
    # Estado persistente local
    # =========================
    def _load_state(self) -> BridgeState:
        try:
            if os.path.isfile(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return BridgeState.from_dict(d if isinstance(d, dict) else {})
        except Exception as e:
            logger.warning(f"{Fore.YELLOW}No pude cargar state file ({self.state_path}): {e}")
        return BridgeState()

    def _save_state(self):
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except Exception as e:
            logger.warning(f"{Fore.YELLOW}No pude guardar state file ({self.state_path}): {e}")


    def _make_upload_session_id(self) -> str:
        # Un session ID consistente por ciclo de /reload (unifica stats + roster)
        return f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    def _is_wow_running(self) -> bool:
        if psutil is None:
            return True

        targets = {p.lower(): True for p in self.config.wow_process_names}
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in targets:
                return True
        return False

    def request_full_roster(self, reason: str = "manual"):
        self._force_reason = reason
        self._force_full_roster.set()
        logger.info(f"{Fore.CYAN}Se solicitó envío completo del roster (motivo: {reason}). Se ejecutará en el próximo ciclo.")

    def stop(self):
        self._stop_event.set()

    def _open_watch_folder(self):
        try:
            folder = os.path.dirname(self.config.wow_addon_path)
            if not folder:
                return
            if platform.system().lower().startswith("win"):
                os.startfile(folder)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            logger.warning(f"No pude abrir la carpeta: {e}")

    def _copy_watch_path(self):
        try:
            if self.ui.enabled and self.ui.root is not None:
                self.ui.root.clipboard_clear()
                self.ui.root.clipboard_append(self.config.wow_addon_path)
                self.ui.push_log("INFO", "Ruta copiada al portapapeles")
        except Exception as e:
            logger.warning(f"No pude copiar ruta: {e}")

    def _restart_bridge(self):
        try:
            logger.info("Reiniciando bridge...")
            os.execl(sys.executable, sys.executable, *sys.argv)
        except Exception as e:
            logger.error(f"No se pudo reiniciar: {e}")

    # =========================
    # Loop principal
    # =========================
    def start(self):
        logger.info(f"{Fore.GREEN}=== SISTEMA V43.0 (THE RELAY TANK) ===")
        logger.info(f"Vigilando: {self.config.wow_addon_path}")
        self._check_latest_version()
        self._start_command_listener()

        if self.ui.enabled and self.ui.root is not None:
            worker = threading.Thread(target=self._run_loop, daemon=True)
            worker.start()
            self.ui.run()
            self.stop()
        else:
            self._run_loop()

    def _start_command_listener(self):
        if not sys.stdin.isatty():
            return

        def _listen():
            while not self._stop_event.is_set():
                try:
                    line = input().strip().lower()
@@ -759,50 +1146,51 @@ class GuildActivityBridge:
                logger.warning(f"{Fore.MAGENTA}⚠ Procesamiento devolvió vacío. Saltando ciclo.")
                return

            # =================================================================
            # =================================================================
            # PASO 2: WEB UPLOAD
            # =================================================================
            if self.config.enable_web_upload:
                                self.local_queue.flush(self._post_to_web_with_retry)
                                # Un solo Session ID para TODO en este ciclo (stats + roster/chat)
                                web_session_id = self._make_upload_session_id()
                                self.state.last_web_session_id = web_session_id
                                self._save_state()

                                # 3A) Stats incremental (para NO duplicar snapshots en la DB del sitio)
                                if processed_data.get("stats") and self.config.enable_stats_incremental_web:
                                    self._upload_stats_incremental_to_web(processed_data["stats"], web_session_id)

                                # 3B) Roster + chat por sesión en lotes (evita 413)
                                self._upload_chunked_to_web(processed_data, web_session_id, *self._consume_force_full_flag())


        except Exception as e:
            logger.error(f"Error procesando archivo: {e}", exc_info=True)
        finally:
            self.ui.enable_send_button()
            self._print_health_panel()

    # =========================
    # LUA parsing helpers
    # =========================
    def _extract_lua_table(self, content: str) -> Optional[str]:
        """
        SavedVariables típicamente:
            GuildActivityTrackerDB = { ... }
        SLPP necesita solo la tabla.
        """
        idx = content.find("{")
        if idx == -1:
            return None
        table = content[idx:].strip()

        # Por si hay caracteres raros después del cierre final, intentamos recortar:
        # (método simple por robustez; SavedVariables suele terminar en '}' limpio)
        # Si falla, igual devolveremos el string completo.
        last = table.rfind("}")
        if last != -1:
            table = table[: last + 1]
        return table

    # =========================
@@ -1448,96 +1836,107 @@ class GuildActivityBridge:
                batch_size = new_batch
                total_batches = math.ceil(total_members / batch_size)
                # NO avanzamos idx; reintentamos mismo lote con batch más chico.
                time.sleep(1.0)

        self.state.roster_snapshot = self._build_roster_snapshot(processed_data.get("roster_members") or processed_data.get("members") or {})
        self._save_state()
        logger.info(f"{Fore.GREEN}✔✔ Upload Web Roster/Chat Completado Exitosamente (session {session_id}).")

    # -------------------------
    # HTTP helper
    # -------------------------
    def _post_to_web_with_retry(self, payload: Dict[str, Any], purpose: str = "", allow_queue: bool = True):
        """
        Reintentos fuertes (no abandona fácil).
        Lanza _TooLarge413 si 413 (para que el caller ajuste batch size).
        """
        url = self.config.web_api_url
        headers = {"X-API-Key": self.config.web_api_key, "Content-Type": "application/json"}

        backoff = 1.0
        max_backoff = 20.0
        attempt = 0
        max_attempts_before_queue = 5

        self.health["uploading"] = True
        self.ui.notify_uploading(True)
        try:
            while True:
                attempt += 1
                try:
                    start = time.time()
                    resp = self._session.post(url, json=payload, headers=headers, timeout=self.config.http_timeout)
                    elapsed_ms = int((time.time() - start) * 1000)
                    self.health["last_latency_ms"] = elapsed_ms
                    self.health["last_payload_size"] = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

                    if resp.status_code == 200:
                        self.health["last_upload_ok"] = datetime.now().isoformat()
                        self.health["last_error"] = None
                        return

                    if resp.status_code == 413:
                        raise _TooLarge413()

                    # auth problems -> no tiene sentido reintentar infinito
                    if resp.status_code in (401, 403):
                        logger.error(f"{Fore.RED}Web auth error ({resp.status_code}) en {purpose}. Revisa WEB_API_KEY / settings del sitio.")
                        self.health["last_error"] = f"Auth {resp.status_code}"
                        raise RuntimeError(f"Web auth error {resp.status_code}")

                    # zod validation / bad request
                    if resp.status_code in (400, 422):
                        try:
                            details = resp.json()
                        except Exception:
                            details = resp.text[:400]
                        logger.error(f"{Fore.RED}Web validation error {resp.status_code} en {purpose}: {details}")
                        self.health["last_error"] = f"400/422 {details}"
                        raise RuntimeError(f"Web validation error {resp.status_code}")

                    # 429/5xx/etc: reintentar
                    logger.warning(f"{Fore.YELLOW}Web error {resp.status_code} en {purpose}. Intento {attempt}. Backoff {backoff:.1f}s")
                    if allow_queue and attempt >= max_attempts_before_queue:
                        self.local_queue.enqueue(payload, purpose)
                        logger.warning(f"{Fore.MAGENTA}Persisten errores de server ({resp.status_code}). Payload en cola local.")
                        return
                    time.sleep(backoff)
                    backoff = min(max_backoff, backoff * 1.6)
                    continue

                except _TooLarge413:
                    raise
                except requests.RequestException as e:
                    logger.warning(f"{Fore.YELLOW}Web conexión falló en {purpose}: {e}. Intento {attempt}. Backoff {backoff:.1f}s")
                    self.health["last_error"] = str(e)
                    if allow_queue and attempt >= max_attempts_before_queue:
                        self.local_queue.enqueue(payload, purpose)
                        logger.warning(f"{Fore.MAGENTA}No hay conexión estable ({purpose}). Payload guardado en cola local para reintento.")
                        return
                    time.sleep(backoff)
                    backoff = min(max_backoff, backoff * 1.6)
                    continue
        finally:
            self.health["uploading"] = False
            self.ui.notify_uploading(False)
            self.ui.enable_send_button()


class _TooLarge413(Exception):
    pass


def main():
    try:
        bridge = GuildActivityBridge(Config())
        bridge.start()
    except Exception as e:
        logger.error(f"Fallo inicio: {e}", exc_info=True)
        try:
            input("Enter para cerrar...")
        except Exception:
            pass


if __name__ == "__main__":
    main()
