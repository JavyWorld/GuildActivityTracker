#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Guild Activity Tracker Bridge - Versión 43.1 (Headless TRACE)
Robust bridge between WoW SavedVariables (GuildActivityTrackerDB) and:
  1) Website API (/api/upload) with session-based chunking

Principios:
- NO rompe funciones existentes: mantiene los mismos métodos públicos del V43.
- Datos completos: NO se descarta data de chat; se normaliza.
- Subida web resiliente: reintentos fuertes + ajuste automático si hay 413.
- Evita inflar la DB web: sube snapshots de stats incrementalmente (persistiendo estado local).
- Normaliza nombres: unifica "Nombre" vs "Nombre-Reino".
- Modo TRACE por consola: sin UI, ventana minimizada y logs verbosos.
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple, Optional, Iterable

import requests
from dotenv import load_dotenv

import colorama
from colorama import Fore
import slpp

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

colorama.init(autoreset=True)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

psutil_spec = importlib.util.find_spec("psutil")
if psutil_spec:
    import psutil  # type: ignore
else:
    psutil = None  # type: ignore

try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:
    ZoneInfo = None  # type: ignore


# =========================
# Defaults (pueden override por .env)
# =========================
DEFAULT_WEB_API_URL = "https://nexonir.replit.app/api/upload"
DEFAULT_WEB_API_KEY = "4df6e5051f959afa012c2498592765c7417e09b05b1f0edf451d34fb6b694ddf"
DEFAULT_HTTP_TIMEOUT = 120
DEFAULT_BATCH_SIZE = 80  # más seguro contra 413 que 100
DEFAULT_STATS_BATCH_SIZE = 80
DEFAULT_TZ = "America/New_York"

STATE_FILENAME = os.getenv("BRIDGE_STATE_FILE", "gat_bridge_state.json")
LOCAL_QUEUE_FILE = os.getenv("UPLOAD_QUEUE_FILE", "upload_queue.jsonl")
UPLOADER_VERSION = "43.1"


class ConsoleReporter:
    """
    Reporter ultra simple para modo headless: todo se loguea a consola con mucho detalle.
    """

    def __init__(self):
        self.enabled = False
        self.root = None

    def update_status(self, wow_running: Optional[bool], health: Dict[str, Any], wow_path: str, **kwargs):
        try:
            activity = kwargs.get("activity", "--")
            progress = kwargs.get("progress", "--")
            queue_note = kwargs.get("queue_note", "--")
            logger.debug(
                f"[STATUS] wow_running={wow_running} | activity={activity} | progress={progress} | queue={queue_note} "
                f"| health(lat_ms={health.get('last_latency_ms')}, payload={health.get('last_payload_size')}, "
                f"parse={health.get('last_parse_ok')}, upload={health.get('last_upload_ok')}) | path={wow_path}"
            )
        except Exception:
            pass

    def show_activity(self, message: str, progress: str = "--"):
        logger.info(f"[ACTIVITY] {message} :: {progress}")

    def push_log(self, msg: str, level: str = "INFO"):
        level = (level or "info").lower()
        log_fn = getattr(logger, level, logger.info)
        log_fn(f"[TRACE] {msg}")

    def set_console_visible(self, visible: bool):
        return

    def set_autostart_enabled(self, enabled: bool):
        return

    def run(self):
        return


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

    def _ensure_dir(self):
        base = os.path.dirname(self.path)
        if base and not os.path.isdir(base):
            os.makedirs(base, exist_ok=True)

    def enqueue(self, payload: Dict[str, Any], purpose: str):
        try:
            self._ensure_dir()
            record = {
                "ts": int(time.time()),
                "purpose": purpose,
                "payload": payload,
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"No pude guardar en cola local: {e}")

    def load_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        if not os.path.isfile(self.path):
            return entries
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"No pude leer cola local: {e}")
        return entries

    def rewrite(self, entries: List[Dict[str, Any]]):
        try:
            self._ensure_dir()
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except Exception as e:
            logger.warning(f"No pude reescribir cola local: {e}")

    def pending_entries(self) -> int:
        try:
            return len(self.load_entries())
        except Exception:
            return 0

    def flush(self, sender):
        entries = self.load_entries()
        if not entries:
            return
        remaining: List[Dict[str, Any]] = []
        logger.info(f"{Fore.CYAN}Procesando cola local: {len(entries)} pendientes...")
        for entry in entries:
            payload = entry.get("payload", {})
            purpose = entry.get("purpose", "queued upload")
            try:
                sender(payload, purpose=purpose, allow_queue=False)
            except Exception as e:
                logger.warning(f"No pude re-subir payload en cola ({purpose}): {e}")
                remaining.append(entry)
        if remaining:
            self.rewrite(remaining)
        else:
            try:
                os.remove(self.path)
            except Exception:
                pass


class Config:
    """
    Mantiene los mismos campos del V43, pero agrega robustez en WEB_API_URL.
    """
    def __init__(self):
        load_dotenv()

        raw_path = os.getenv('WOW_ADDON_PATH', '').strip()
        self.wow_addon_path = os.path.normpath(os.path.expandvars(raw_path)) if raw_path else ""

        self.default_realm = os.getenv("GUILD_REALM", os.getenv("DEFAULT_REALM", "")).replace(" ", "")

        self.poll_interval = int(os.getenv("POLL_INTERVAL", "5"))
        self.wow_process_names = [
            n.strip() for n in os.getenv(
                "WOW_PROCESS_NAMES", "Wow.exe,Wow-64.exe,WowT.exe,WowClassic.exe"
            ).split(",")
            if n.strip()
        ]

        raw_web_url = (os.getenv("WEB_API_URL", DEFAULT_WEB_API_URL) or "").strip()
        self.web_api_url = self._normalize_web_api_url(raw_web_url)

        self.web_api_key = os.getenv("WEB_API_KEY", DEFAULT_WEB_API_KEY)
        self.http_timeout = int(os.getenv("HTTP_TIMEOUT", str(DEFAULT_HTTP_TIMEOUT)))
        self.batch_size = int(os.getenv("BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
        self.stats_batch_size = int(os.getenv("STATS_BATCH_SIZE", str(DEFAULT_STATS_BATCH_SIZE)))

        self.enable_web_upload = os.getenv("ENABLE_WEB_UPLOAD", "true").lower() == "true"
        self.enable_stats_incremental_web = os.getenv("ENABLE_STATS_INCREMENTAL_WEB", "true").lower() == "true"

        self.min_roster_size = int(os.getenv("MIN_ROSTER_SIZE", "1"))

        self._validate()

    def _normalize_web_api_url(self, raw: str) -> str:
        """
        Acepta:
          - https://site
          - https://site/
          - https://site/api
          - https://site/api/upload
        Devuelve SIEMPRE .../api/upload
        """
        s = (raw or "").strip()
        if not s:
            return DEFAULT_WEB_API_URL

        s = s.rstrip("/")
        # si ya trae /api/upload
        if s.endswith("/api/upload"):
            return s

        # si termina en /api -> /api/upload
        if s.endswith("/api"):
            return s + "/upload"

        # si incluye /api/ pero no es upload (dejamos tal cual, por si el user sabe lo que hace)
        if "/api/" in s:
            return s

        # base -> base/api/upload
        return s + "/api/upload"

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
                    raise ValueError(
                        "Error en WOW_ADDON_PATH: está vacío o inválido. "
                        "Define la ruta en .env o como variable de entorno."
                    )

        if not os.path.isfile(self.wow_addon_path):
            logger.warning(
                f"{Fore.YELLOW}AVISO: Archivo LUA no encontrado en {self.wow_addon_path}. "
                f"El bridge quedará vigilando hasta que exista."
            )

    def _auto_detect_wow_addon_path(self, manual_base: Optional[str] = None) -> str:
        candidates = []
        home = os.path.expanduser("~")

        def _add_base(base_root: str):
            if base_root and base_root not in candidates:
                candidates.append(base_root)

        _add_base(os.path.join(home, "Documents", "World of Warcraft"))
        _add_base(os.path.join(home, "World of Warcraft"))

        if os.name == "nt":
            userprofile = os.getenv("USERPROFILE", home)
            _add_base(os.path.join(userprofile, "Documents", "World of Warcraft"))
            _add_base(os.path.join(userprofile, "AppData", "Roaming", "World of Warcraft"))
            _add_base(os.path.join(userprofile, "AppData", "Local", "World of Warcraft"))

            program_files = os.getenv("PROGRAMFILES", os.path.join("C:\\", "Program Files"))
            program_files_x86 = os.getenv("PROGRAMFILES(X86)", os.path.join("C:\\", "Program Files (x86)"))
            localized_pf = [
                os.path.join("C:\\", "Archivos de programa"),
                os.path.join("C:\\", "Archivos de programa (x86)"),
                os.path.join("C:\\", "Programas"),
            ]

            _add_base(os.path.join(program_files, "World of Warcraft"))
            _add_base(os.path.join(program_files_x86, "World of Warcraft"))
            for pf in localized_pf:
                _add_base(os.path.join(pf, "World of Warcraft"))
            _add_base(os.path.join("C:\\", "World of Warcraft"))

        if manual_base:
            candidates.insert(0, manual_base)

        flavors = ["", "_retail_", "_classic_", "_classic_era_", "_ptr_", "_beta_"]
        fallback = ""

        for base in candidates:
            for flavor in flavors:
                wow_root = os.path.join(base, flavor) if flavor else base
                if not os.path.isdir(wow_root):
                    continue

                for current, dirs, files in os.walk(wow_root):
                    depth = current.replace(wow_root, "").count(os.sep)
                    if depth > 5:
                        dirs[:] = []
                        continue

                    if "GuildActivityTracker.lua" in files:
                        return os.path.normpath(os.path.join(current, "GuildActivityTracker.lua"))

                    if current.endswith("SavedVariables") and not fallback:
                        fallback = os.path.normpath(os.path.join(current, "GuildActivityTracker.lua"))

                account_root = os.path.join(wow_root, "WTF", "Account")
                if os.path.isdir(account_root) and not fallback:
                    try:
                        accounts = [a for a in os.listdir(account_root) if os.path.isdir(os.path.join(account_root, a))]
                        if accounts:
                            saved_vars = os.path.join(account_root, accounts[0], "SavedVariables")
                            fallback = os.path.normpath(os.path.join(saved_vars, "GuildActivityTracker.lua"))
                    except Exception:
                        pass

        return fallback

    def _prompt_wow_addon_path(self) -> str:
        if not sys.stdin.isatty():
            return ""

        banner = "\n" + "=" * 68 + "\n" + \
                 " Configuración interactiva - Guild Activity Bridge\n" + \
                 " No se encontró la ruta a GuildActivityTracker.lua.\n" + \
                 " Ayúdame indicándome dónde está instalado World of Warcraft.\n" + \
                 "=" * 68 + "\n"
        print(banner)
        print("Pasos:")
        print(" 1) Copia la ruta donde está instalado el juego")
        print(r"    (ej: C:\\Program Files (x86)\\World of Warcraft).")
        print(" 2) O pega directamente la ruta completa al archivo GuildActivityTracker.lua.\n")

        while True:
            user_input = input("Ruta de WoW o al archivo GuildActivityTracker.lua (enter para cancelar): ").strip()
            if not user_input:
                print("Cancelado. Puedes definir WOW_ADDON_PATH en .env.")
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

            print("No pude localizar GuildActivityTracker.lua. Verifica e intenta nuevamente.\n")


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
        }
        self._ui_activity = "En espera"
        self._ui_progress = "--"
        self._ui_queue_note = "vacía"
        self._console_hwnd = None
        self._console_visible = True
        self._autostart_supported = os.name == "nt"
        self._autostart_enabled = self._detect_autostart_enabled()

        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": self.config.web_api_key, "Content-Type": "application/json"})

        self.local_queue = LocalUploadQueue(os.path.join(os.path.dirname(os.path.abspath(__file__)), LOCAL_QUEUE_FILE))

        self.state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILENAME)
        self.state = self._load_state()
        self._stop_event = threading.Event()
        self._force_full_roster = threading.Event()
        self._force_reason = "manual"

        self._console_toggle_available = self._init_console_window_state()
        self._minimize_console_window()

        self.ui = ConsoleReporter()
        self.ui.push_log("UI desactivada. Modo TRACE por consola/minimizado.", level="info")


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
        logger.info(f"{Fore.CYAN}Se solicitó envío completo del roster (motivo: {reason}).")

    def stop(self):
        self._stop_event.set()

    # =========================
    # Loop principal
    # =========================
    def start(self):
        logger.info(f"{Fore.GREEN}=== SISTEMA V{UPLOADER_VERSION} (THE RELAY TANK) ===")
        logger.info(f"Vigilando: {self.config.wow_addon_path}")
        self._check_latest_version()
        self._start_command_listener()

        if self._autostart_supported:
            status = "habilitado" if self._autostart_enabled else "deshabilitado"
            logger.info(f"Inicio automático en Windows: {status}")
        else:
            logger.info("Inicio automático solo disponible en Windows (omitido).")

        self._run_loop()

    def _start_command_listener(self):
        if not sys.stdin.isatty():
            return

        def _listen():
            while not self._stop_event.is_set():
                try:
                    line = input().strip().lower()
                except EOFError:
                    break
                except Exception:
                    break

                if line in ("full", "f", "full roster", "roster full"):
                    self.request_full_roster("manual-cli")

        threading.Thread(target=_listen, daemon=True).start()

    def _run_loop(self):
        last_wow_state: Optional[bool] = None

        while not self._stop_event.is_set():
            try:
                wow_running = self._is_wow_running()
                if wow_running != last_wow_state:
                    if wow_running:
                        logger.info("World of Warcraft detectado. Activando monitoreo y cola local.")
                        self.local_queue.flush(self._post_to_web_with_retry)
                        self.last_mtime = 0
                        self._set_ui_activity("WoW detectado: monitoreo activo", level="success")
                    else:
                        logger.info("World of Warcraft no está en ejecución. Esperando...")
                        self._set_ui_activity("Esperando que World of Warcraft esté en ejecución")
                last_wow_state = wow_running

                if not wow_running:
                    self._refresh_ui(False)
                    time.sleep(self.config.poll_interval)
                    continue

                if os.path.isfile(self.config.wow_addon_path):
                    current_mtime = os.path.getmtime(self.config.wow_addon_path)
                    needs_process = False
                    if self.last_mtime == 0 or current_mtime != self.last_mtime:
                        needs_process = True
                    elif self._force_full_roster.is_set():
                        needs_process = True

                    if needs_process:
                        if current_mtime != self.last_mtime and self.last_mtime != 0:
                            logger.info(f"{Fore.CYAN}¡Cambio detectado! Esperando estabilización de archivo...")
                            self._wait_for_file_stable(self.config.wow_addon_path)
                        self.last_mtime = current_mtime
                        self._set_ui_activity("Procesando SavedVariables", progress="Lectura de archivo")
                        self.process_file()

                self._refresh_ui(True)
                time.sleep(self.config.poll_interval)

            except KeyboardInterrupt:
                logger.info("Cerrando bridge por KeyboardInterrupt.")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Error ciclo: {e}", exc_info=True)
                time.sleep(5)

    def _wait_for_file_stable(self, path: str, checks: int = 4, delay: float = 0.7):
        last = (-1, -1.0)
        stable = 0
        for _ in range(checks * 3):
            try:
                st = os.stat(path)
                cur = (st.st_size, st.st_mtime)
                if cur == last:
                    stable += 1
                    if stable >= checks:
                        return
                else:
                    stable = 0
                    last = cur
            except Exception:
                pass
            time.sleep(delay)

    def _check_latest_version(self):
        try:
            base = self.config.web_api_url.rsplit("/api", 1)[0]
            url = f"{base}/api/uploader/latest"
            resp = self._session.get(url, timeout=10)
            if resp.status_code != 200:
                return
            data = resp.json()
            latest = str(data.get("version") or data.get("latest") or "")
            if latest and latest != UPLOADER_VERSION:
                logger.warning(f"{Fore.YELLOW}Nueva versión disponible: {latest}. Estás en {UPLOADER_VERSION}.")
            else:
                logger.info(f"{Fore.GREEN}Uploader actualizado ({UPLOADER_VERSION}).")
        except Exception as e:
            logger.info(f"No se pudo verificar versión más reciente: {e}")

    def _consume_force_full_flag(self) -> Tuple[bool, str]:
        if self._force_full_roster.is_set():
            self._force_full_roster.clear()
            return True, self._force_reason
        return False, ""

    def _queue_status_note(self) -> str:
        try:
            pending = self.local_queue.pending_entries()
            return f"{pending} pendiente(s)" if pending else "vacía"
        except Exception:
            return "desconocida"

    # =========================
    # Autostart (VBS aligned to installer)
    # =========================
    def _startup_dir(self) -> Optional[str]:
        if os.name != "nt":
            return None
        appdata = os.getenv("APPDATA")
        if not appdata:
            return None
        return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")

    def _startup_vbs_path(self) -> Optional[str]:
        sd = self._startup_dir()
        if not sd:
            return None
        return os.path.join(sd, "GuildActivityBridge.vbs")

    def _startup_bat_path_legacy(self) -> Optional[str]:
        sd = self._startup_dir()
        if not sd:
            return None
        return os.path.join(sd, "GuildActivityBridgeStartup.bat")

    def _detect_autostart_enabled(self) -> bool:
        vbs = self._startup_vbs_path()
        bat = self._startup_bat_path_legacy()
        return bool((vbs and os.path.isfile(vbs)) or (bat and os.path.isfile(bat)))

    def _preferred_python_command(self) -> str:
        if os.name != "nt":
            return sys.executable
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            candidate = exe[:-10] + "pythonw.exe"
            if os.path.isfile(candidate):
                return candidate
        return exe

    def _set_autostart(self, enable: bool) -> bool:
        if not self._autostart_supported:
            return False

        startup_dir = self._startup_dir()
        vbs_path = self._startup_vbs_path()
        bat_path = self._startup_bat_path_legacy()

        if not startup_dir or not vbs_path:
            logger.warning(f"{Fore.YELLOW}No se encontró la carpeta de inicio automático (APPDATA).")
            return False

        try:
            os.makedirs(startup_dir, exist_ok=True)

            if enable:
                # Prefer: chain to installer-made hidden vbs if exists
                hidden_vbs = os.path.join(SCRIPT_DIR, "start_bridge_hidden.vbs")
                start_bat = os.path.join(SCRIPT_DIR, "start_bridge.bat")
                script_path = os.path.abspath(__file__)

                if os.path.isfile(hidden_vbs):
                    cmd = f'wscript.exe "{hidden_vbs}"'
                elif os.path.isfile(start_bat):
                    cmd = f'cmd /c ""{start_bat}""'
                else:
                    py = self._preferred_python_command()
                    cmd = f'"{py}" "{script_path}"'

                vbs = [
                    'Set shell = CreateObject("WScript.Shell")',
                    f'shell.Run "{cmd}", 0, False',
                ]
                with open(vbs_path, "w", encoding="utf-8") as f:
                    f.write("\r\n".join(vbs) + "\r\n")

                # si existía legacy .bat, lo quitamos
                if bat_path and os.path.isfile(bat_path):
                    try:
                        os.remove(bat_path)
                    except Exception:
                        pass

                self._autostart_enabled = True
                self.ui.push_log("Inicio automático habilitado (VBS en Startup).", level="success")
            else:
                # remove both
                if os.path.isfile(vbs_path):
                    os.remove(vbs_path)
                if bat_path and os.path.isfile(bat_path):
                    os.remove(bat_path)

                self._autostart_enabled = False
                self.ui.push_log("Inicio automático deshabilitado.", level="info")

            self.ui.set_autostart_enabled(self._autostart_enabled)
            return True

        except Exception as e:
            logger.warning(f"{Fore.YELLOW}No pude actualizar inicio automático: {e}")
            return False

    def toggle_autostart(self, enable: Optional[bool] = None) -> bool:
        """
        ✅ Compatible con UI tray:
        - Si enable es None -> toggle
        - Si enable viene explícito -> se aplica
        """
        if not self._autostart_supported:
            return False

        if enable is None:
            enable = not self._autostart_enabled

        success = self._set_autostart(bool(enable))
        if not success:
            self.ui.push_log("No se pudo cambiar el inicio automático. Revisa permisos.", level="warn")
        return success

    # =========================
    # Console visibility
    # =========================
    def _init_console_window_state(self) -> bool:
        if os.name != "nt":
            self._console_visible = False
            return False
        try:
            import ctypes
            self._console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            self._console_visible = bool(self._console_hwnd)
            return bool(self._console_hwnd)
        except Exception:
            self._console_hwnd = None
            self._console_visible = False
            return False

    def _minimize_console_window(self):
        """
        Minimiza la ventana de la consola para dejarla en la barra de tareas (modo headless-friendly).
        """
        if os.name != "nt" or not self._console_hwnd:
            return
        try:
            import ctypes
            SW_MINIMIZE = 6
            ctypes.windll.user32.ShowWindow(self._console_hwnd, SW_MINIMIZE)
            self._console_visible = False
            logger.info("Consola minimizada (ejecución en segundo plano con trazas verbosas).")
        except Exception as e:
            logger.debug(f"No se pudo minimizar la consola: {e}")

    def _set_console_visibility(self, visible: bool):
        if os.name != "nt" or not self._console_hwnd:
            return
        try:
            import ctypes
            SW_HIDE = 0
            SW_SHOW = 5
            ctypes.windll.user32.ShowWindow(self._console_hwnd, SW_SHOW if visible else SW_HIDE)
            if visible:
                ctypes.windll.user32.SetForegroundWindow(self._console_hwnd)
            self._console_visible = visible
            self.ui.set_console_visible(self._console_visible)
        except Exception:
            pass

    def _hide_console_window(self):
        if not self._console_toggle_available:
            return
        if self._console_visible:
            self._set_console_visibility(False)

    def toggle_console_visibility(self) -> bool:
        if not self._console_toggle_available:
            return self._console_visible
        new_state = not self._console_visible
        self._set_console_visibility(new_state)
        if new_state:
            self._set_ui_activity("Consola mostrada", level="info")
        else:
            self.ui.push_log("Consola oculta", level="info")
        return self._console_visible

    def _set_ui_activity(self, message: str, progress: str = "--", level: str = "info"):
        self._ui_activity = message
        self._ui_progress = progress or "--"
        self.ui.show_activity(message, progress=self._ui_progress)
        self.ui.push_log(message, level=level)

    def _refresh_ui(self, wow_running: Optional[bool] = None):
        if wow_running is None:
            wow_running = self._is_wow_running()
        self._ui_queue_note = self._queue_status_note()
        self.ui.update_status(
            wow_running,
            self.health,
            self.config.wow_addon_path,
            activity=self._ui_activity,
            progress=self._ui_progress,
            queue_note=self._ui_queue_note,
        )

    def _print_health_panel(self):
        panel = [
            "=== HEALTH PANEL ===",
            f"Último parse OK: {self.health.get('last_parse_ok') or 'pendiente'}",
            f"Último upload OK: {self.health.get('last_upload_ok') or 'pendiente'}",
            f"Latencia al server: {self.health.get('last_latency_ms') or 's/d'} ms",
            f"Tamaño payload: {self.health.get('last_payload_size') or 's/d'} bytes",
            f"Versión: {self.health.get('version')}",
        ]
        logger.info(" | ".join(panel))
        self._refresh_ui(self._is_wow_running())

    # =========================
    # Procesamiento principal
    # =========================
    def process_file(self):
        try:
            logger.info(f"{Fore.BLUE}Leyendo datos...")
            self._set_ui_activity("Leyendo SavedVariables", progress="Esperando datos")
            with open(self.config.wow_addon_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            if not content.strip():
                return

            table_text = self._extract_lua_table(content)
            if not table_text:
                logger.error("No se pudo extraer la tabla LUA del archivo.")
                return

            try:
                data = self.lua_parser.decode(table_text)
            except Exception as e:
                logger.error(f"Error decodificando LUA con SLPP: {e}")
                return

            if not isinstance(data, dict):
                logger.error("El contenido LUA no decodificó a un diccionario.")
                return

            self.health["last_parse_ok"] = datetime.now().isoformat()
            self._set_ui_activity("Datos decodificados", progress="Unificando tablas")

            processed_data, _active_count = self._process_and_merge_data(data)
            if not processed_data:
                logger.warning(f"{Fore.MAGENTA}⚠ Procesamiento devolvió vacío. Saltando ciclo.")
                return

            if self.config.enable_web_upload:
                self._set_ui_activity("Preparando subida web", progress="Creando sesión")
                self.local_queue.flush(self._post_to_web_with_retry)

                web_session_id = self._make_upload_session_id()
                self.state.last_web_session_id = web_session_id
                self._save_state()

                if processed_data.get("stats") and self.config.enable_stats_incremental_web:
                    self._upload_stats_incremental_to_web(processed_data["stats"], web_session_id)

                self._upload_chunked_to_web(processed_data, web_session_id, *self._consume_force_full_flag())

        except Exception as e:
            logger.error(f"Error procesando archivo: {e}", exc_info=True)
        finally:
            self._print_health_panel()
            self._set_ui_activity("En espera", progress="Monitoreando cambios")

    # =========================
    # LUA parsing helpers
    # =========================
    def _extract_lua_table(self, content: str) -> Optional[str]:
        idx = content.find("{")
        if idx == -1:
            return None
        table = content[idx:].strip()
        last = table.rfind("}")
        if last != -1:
            table = table[: last + 1]
        return table

    # =========================
    # Normalización de nombres
    # =========================
    def _infer_default_realm(self, raw_roster: Dict[str, Any], raw_activity: Dict[str, Any]) -> str:
        if self.config.default_realm:
            return self.config.default_realm

        realm_counts: Dict[str, int] = {}

        def bump(full: str):
            if "-" in full:
                realm = full.split("-", 1)[1].replace(" ", "")
                if realm:
                    realm_counts[realm] = realm_counts.get(realm, 0) + 1

        for k in raw_activity.keys():
            bump(str(k))
        for k in raw_roster.keys():
            bump(str(k))

        if realm_counts:
            best = sorted(realm_counts.items(), key=lambda x: (-x[1], x[0]))[0][0]
            return best

        return "Unknown"

    def _canonicalize_player_key(self, name: str, default_realm: str) -> str:
        n = (name or "").strip()
        if not n:
            return n
        if "-" in n:
            return n
        if not default_realm:
            return n
        return f"{n}-{default_realm}"

    def _short_name(self, full: str) -> str:
        return (full or "").split("-", 1)[0]

    def _build_roster_snapshot(self, roster_members: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        snapshot: Dict[str, Dict[str, Any]] = {}
        for name, info in roster_members.items():
            if not isinstance(info, dict):
                continue
            snapshot[name] = {
                "rank": info.get("rank", "Member"),
                "lvl": int(info.get("level", 0) or 0),
                "class": info.get("class", "UNKNOWN"),
                "lastSeenTS": int(info.get("lastSeenTS", 0) or 0),
                "lastMessage": info.get("lastMessage", ""),
            }
        return snapshot

    def _compute_roster_delta(self, roster_members: Dict[str, Any]):
        prev = self.state.roster_snapshot or {}
        current_snapshot = self._build_roster_snapshot(roster_members)

        added: Dict[str, Dict[str, Any]] = {}
        updated: Dict[str, Dict[str, Any]] = {}
        removed: List[str] = []

        for name, info in current_snapshot.items():
            if name not in prev:
                added[name] = roster_members.get(name, info)
            elif info != prev.get(name, {}):
                updated[name] = roster_members.get(name, info)

        for name in prev.keys():
            if name not in current_snapshot:
                removed.append(name)

        return added, updated, removed

    def _find_chat_entry_for_roster_member(
        self,
        roster_key: str,
        canonical_key: str,
        raw_activity: Dict[str, Any],
        default_realm: str,
    ) -> Optional[Dict[str, Any]]:
        if canonical_key in raw_activity and isinstance(raw_activity[canonical_key], dict):
            return raw_activity[canonical_key]

        if roster_key in raw_activity and isinstance(raw_activity[roster_key], dict):
            return raw_activity[roster_key]

        short = self._short_name(canonical_key)

        if short in raw_activity and isinstance(raw_activity[short], dict):
            candidates = [k for k in raw_activity.keys() if str(k).startswith(short + "-")]
            if len(candidates) == 0:
                return raw_activity[short]
            return None

        candidates = []
        for k, v in raw_activity.items():
            ks = str(k)
            if ks.startswith(short + "-") and isinstance(v, dict):
                candidates.append((ks, v))

        if len(candidates) == 1:
            return candidates[0][1]

        if len(candidates) > 1:
            def ts_of(entry: Dict[str, Any]) -> int:
                try:
                    return int(entry.get("lastSeenTS", 0) or 0)
                except Exception:
                    return 0
            candidates.sort(key=lambda kv: ts_of(kv[1]), reverse=True)
            top_ts = ts_of(candidates[0][1])
            second_ts = ts_of(candidates[1][1])
            if top_ts and second_ts and abs(top_ts - second_ts) < 60:
                return None
            return candidates[0][1]

        return None

    # =========================
    # PASO 1: Unificación data (Roster + Chat + Stats)
    # =========================
    def _process_and_merge_data(self, lua_data: Dict) -> Tuple[Optional[Dict], int]:
        raw_roster = lua_data.get('roster', {}) or {}
        raw_activity = lua_data.get('data', {}) or {}
        raw_stats = lua_data.get('stats', []) or []
        raw_mythic = lua_data.get('mythic', {}) or {}

        if not isinstance(raw_roster, dict):
            raw_roster = {}
        if not isinstance(raw_activity, dict):
            raw_activity = {}
        if not isinstance(raw_stats, (list, dict)):
            raw_stats = []

        default_realm = self._infer_default_realm(raw_roster, raw_activity)

        if len(raw_roster) < max(0, self.config.min_roster_size):
            return None, 0

        logger.info(f"Procesando {len(raw_roster)} miembros (Normalizando realm='{default_realm}')...")

        roster_members: Dict[str, Dict[str, Any]] = {}

        for roster_key, roster_info in raw_roster.items():
            rk = str(roster_key)
            ck = self._canonicalize_player_key(rk, default_realm)

            if not isinstance(roster_info, dict):
                roster_info = {}

            entry = roster_members.get(ck) or {
                "rank": "Desconocido",
                "level": 80,
                "class": "UNKNOWN",
                "is_online": False,

                "rankIndex": 99,
                "rankName": "—",
                "total": 0,
                "daily": {},
                "lastSeen": "",
                "lastSeenTS": 0,
                "lastMessage": ""
            }

            entry["rank"] = roster_info.get("rank", entry.get("rank", "Desconocido")) or entry.get("rank", "Desconocido")
            entry["level"] = int(roster_info.get("level", entry.get("level", 80)) or 0) or entry.get("level", 80)
            entry["class"] = roster_info.get("class", entry.get("class", "UNKNOWN")) or entry.get("class", "UNKNOWN")
            entry["is_online"] = bool(roster_info.get("is_online", entry.get("is_online", False)))

            roster_members[ck] = entry

        for canonical_name, member_entry in roster_members.items():
            roster_key_guess = self._short_name(canonical_name)
            chat_data = self._find_chat_entry_for_roster_member(
                roster_key=roster_key_guess,
                canonical_key=canonical_name,
                raw_activity=raw_activity,
                default_realm=default_realm,
            )

            if chat_data:
                try:
                    member_entry["total"] = int(chat_data.get("total", member_entry.get("total", 0)) or 0)
                except Exception:
                    pass
                if isinstance(chat_data.get("daily"), dict):
                    member_entry["daily"] = chat_data.get("daily", {}) or {}
                member_entry["lastMessage"] = str(chat_data.get("lastMessage", member_entry.get("lastMessage", "")) or "")

                rn = chat_data.get("rankName")
                if rn and rn != "—":
                    member_entry["rankName"] = rn
                    try:
                        member_entry["rankIndex"] = int(chat_data.get("rankIndex", 99) or 99)
                    except Exception:
                        member_entry["rankIndex"] = 99

                try:
                    ts = int(chat_data.get("lastSeenTS", 0) or 0)
                except Exception:
                    ts = 0
                if ts > 0:
                    member_entry["lastSeenTS"] = ts
                    member_entry["lastSeen"] = str(chat_data.get("lastSeen", "") or "")

        chat_only_members: Dict[str, Dict[str, Any]] = {}
        for raw_name, chat_data in raw_activity.items():
            if not isinstance(chat_data, dict):
                continue
            rn = str(raw_name)
            ck = self._canonicalize_player_key(rn, default_realm)

            if ck in roster_members:
                continue

            entry = {
                "rank": str(chat_data.get("rankName", "Former") or "Former"),
                "level": 0,
                "class": "UNKNOWN",
                "is_online": False,
                "rankIndex": int(chat_data.get("rankIndex", 99) or 99),
                "rankName": str(chat_data.get("rankName", "—") or "—"),
                "total": int(chat_data.get("total", 0) or 0),
                "daily": chat_data.get("daily", {}) if isinstance(chat_data.get("daily"), dict) else {},
                "lastSeen": str(chat_data.get("lastSeen", "") or ""),
                "lastSeenTS": int(chat_data.get("lastSeenTS", 0) or 0),
                "lastMessage": str(chat_data.get("lastMessage", "") or ""),
            }
            chat_only_members[ck] = entry

        stats_list = self._normalize_stats(raw_stats, default_realm)

        union_members = dict(roster_members)
        for ck, v in chat_only_members.items():
            if ck not in union_members:
                union_members[ck] = v

        processed = {
            "members": union_members,
            "roster_members": roster_members,
            "stats": stats_list,
            "mythic": raw_mythic if isinstance(raw_mythic, dict) else {},
            "meta": {"defaultRealm": default_realm}
        }
        return processed, len(roster_members)

    def _normalize_stats(self, raw_stats: Any, default_realm: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        if isinstance(raw_stats, dict):
            values = list(raw_stats.values())
            if values and all(isinstance(v, dict) for v in values):
                items = list(raw_stats.items())

                def key_as_int(k: Any) -> Optional[int]:
                    try:
                        return int(k)
                    except Exception:
                        return None

                if all(key_as_int(k) is not None for k, _ in items):
                    items.sort(key=lambda kv: key_as_int(kv[0]) or 0)
                    snaps = [kv[1] for kv in items]
                else:
                    def ts_of(s: Dict[str, Any]) -> int:
                        try:
                            return int(s.get("ts", 0) or 0)
                        except Exception:
                            return 0
                    snaps = sorted(values, key=ts_of)

                return self._normalize_stats(snaps, default_realm)

            pairs: List[Tuple[int, Any]] = []
            for k, v in raw_stats.items():
                try:
                    ts = int(k)
                except Exception:
                    continue
                pairs.append((ts, v))
            pairs.sort(key=lambda x: x[0])

            for ts, v in pairs:
                iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                count_val = 0
                if isinstance(v, dict):
                    oc = v.get("onlineCount")
                    if oc is None:
                        online = v.get("online", {}) or {}
                        count_val = len(online) if isinstance(online, dict) else 0
                    else:
                        try:
                            count_val = int(oc or 0)
                        except Exception:
                            count_val = 0
                else:
                    try:
                        count_val = int(v or 0)
                    except Exception:
                        count_val = 0

                out.append({"iso": iso, "ts": ts, "onlineCount": int(count_val), "online": {}})
            return out

        if isinstance(raw_stats, list):
            snaps = [s for s in raw_stats if isinstance(s, dict)]

            def ts_of(s: Dict[str, Any]) -> int:
                try:
                    return int(s.get("ts", 0) or 0)
                except Exception:
                    return 0

            snaps.sort(key=ts_of)
            if not snaps:
                return []

            last_ts = ts_of(snaps[-1])

            for snap in snaps:
                ts = ts_of(snap)
                iso = snap.get("iso")
                if not iso and ts:
                    iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                online_count = snap.get("onlineCount")
                if online_count is None:
                    try:
                        online_count = len(snap.get("online", {}) or {})
                    except Exception:
                        online_count = 0

                online_payload: Dict[str, Any] = {}
                if ts == last_ts:
                    online = snap.get("online", {}) or {}
                    if isinstance(online, dict):
                        for name, info in online.items():
                            if not isinstance(info, dict):
                                continue
                            online_payload[self._canonicalize_player_key(str(name), default_realm)] = {
                                "class": info.get("class", "UNKNOWN"),
                                "level": int(info.get("level", 80) or 80),
                                "rank": info.get("rank", "Member"),
                            }

                out.append({
                    "iso": str(iso or ""),
                    "ts": ts,
                    "onlineCount": int(online_count or 0),
                    "online": online_payload
                })

            return out

        return out

    # =========================
    # WEB UPLOADER
    # =========================
    def _upload_stats_incremental_to_web(self, stats_list: List[Dict[str, Any]], upload_session_id: str):
        try:
            if not stats_list:
                return

            new_snaps = [s for s in stats_list if isinstance(s, dict) and int(s.get("ts", 0) or 0) > self.state.last_uploaded_stats_ts]
            if not new_snaps:
                return

            new_snaps.sort(key=lambda s: int(s.get("ts", 0) or 0))

            logger.info(f"{Fore.YELLOW}Subiendo {len(new_snaps)} snapshots nuevos a Web (incremental stats)...")
            self._set_ui_activity("Subiendo snapshots", progress=f"{len(new_snaps)} pendientes")

            batch_size = max(10, self.config.stats_batch_size)
            total_batches = int(math.ceil(len(new_snaps) / batch_size))

            for i in range(0, len(new_snaps), batch_size):
                chunk = new_snaps[i:i + batch_size]
                for j in range(len(chunk) - 1):
                    chunk[j]["online"] = {}

                payload = {
                    "upload_session_id": upload_session_id,
                    "is_final_batch": False,
                    "batch_index": int(i // batch_size) + 1,
                    "total_batches": total_batches,

                    "uploadSessionId": upload_session_id,
                    "isFinalBatch": False,
                    "batchIndex": int(i // batch_size) + 1,
                    "totalBatches": total_batches,

                    "stats": chunk,
                }

                self._set_ui_activity(
                    "Subiendo snapshots",
                    progress=f"Lote {int(i // batch_size) + 1}/{total_batches}",
                )
                logger.info(
                    f"[STATS] Enviando lote {int(i // batch_size) + 1}/{total_batches} "
                    f"({len(chunk)} snapshots, ts {chunk[0].get('ts')} -> {chunk[-1].get('ts')})"
                )
                self._post_to_web_with_retry(payload, purpose=f"stats {i//batch_size+1}/{total_batches}")

            self.state.last_uploaded_stats_ts = int(new_snaps[-1].get("ts", self.state.last_uploaded_stats_ts) or self.state.last_uploaded_stats_ts)
            self._save_state()

        except Exception as e:
            logger.error(f"{Fore.RED}Error stats incremental web: {e}", exc_info=True)

    def _upload_chunked_to_web(self, processed_data: Dict, upload_session_id: str, force_full: bool = False, force_reason: str = ""):
        if not self.config.enable_web_upload:
            return

        roster_members = processed_data.get("roster_members") or processed_data.get("members") or {}
        if not isinstance(roster_members, dict) or not roster_members:
            logger.warning("No roster_members para subir a Web.")
            return

        self._set_ui_activity("Preparando roster/chat", progress=f"{len(roster_members)} miembros detectados")

        added, updated, removed = self._compute_roster_delta(roster_members)
        roster_mode = "delta"
        roster_reason = "delta"

        if force_full:
            roster_mode = "full"
            roster_reason = force_reason or "full"
            logger.info(f"{Fore.CYAN}Envío completo de roster solicitado ({roster_reason}). {len(roster_members)} miembros.")
        elif added or updated or removed:
            roster_members = {**added, **updated}
            logger.info(f"{Fore.CYAN}Delta roster -> added: {len(added)}, updated: {len(updated)}, removed: {len(removed)}")
        else:
            roster_mode = "no_change"
            roster_reason = "no_change"
            summary_payload = {
                "upload_session_id": upload_session_id,
                "is_final_batch": True,
                "batch_index": 1,
                "total_batches": 1,
                "removed_members": [],

                "uploadSessionId": upload_session_id,
                "isFinalBatch": True,
                "batchIndex": 1,
                "totalBatches": 1,
                "removedMembers": [],

                "master_roster": {},
                "data": {},
                "roster_mode": roster_mode,
                "roster_summary": {
                    "mode": roster_mode,
                    "added": 0,
                    "updated": 0,
                    "removed": 0,
                    "total_members": len(processed_data.get("roster_members") or processed_data.get("members") or {}),
                    "reason": roster_reason,
                },

                "rosterMode": roster_mode,
                "rosterSummary": {
                    "mode": roster_mode,
                    "added": 0,
                    "updated": 0,
                    "removed": 0,
                    "totalMembers": len(processed_data.get("roster_members") or processed_data.get("members") or {}),
                    "reason": roster_reason,
                },
            }
            self._post_to_web_with_retry(summary_payload, purpose="roster no-change heartbeat")
            self.state.roster_snapshot = self._build_roster_snapshot(processed_data.get("roster_members") or processed_data.get("members") or {})
            self._save_state()
            logger.info(f"{Fore.CYAN}No hay cambios. Se envió heartbeat.")
            self._set_ui_activity("Heartbeat sin cambios", progress="Roster intacto")
            return

        all_keys = list(roster_members.keys())
        total_members = len(all_keys)

        batch_size = max(10, int(self.config.batch_size))
        session_id = upload_session_id

        total_batches = max(1, int(math.ceil(total_members / batch_size)))
        logger.info(f"{Fore.YELLOW}Upload Web Roster/Chat (ID: {session_id}) - miembros: {total_members}, batch: {batch_size}")
        self._set_ui_activity("Subiendo roster/chat", progress=f"0/{total_batches} lotes")

        def build_payload(batch_keys: List[str], batch_index: int, total_batches: int, is_final: bool) -> Dict[str, Any]:
            master_roster: Dict[str, Any] = {}
            chat_data: Dict[str, Any] = {}

            for name in batch_keys:
                info = roster_members.get(name, {}) if isinstance(roster_members.get(name, {}), dict) else {}
                master_roster[name] = {
                    "rank": info.get("rank", "Member"),
                    "lvl": int(info.get("level", 80) or 80),
                    "class": info.get("class", "UNKNOWN"),
                }

                total = int(info.get("total", 0) or 0)
                ts = int(info.get("lastSeenTS", 0) or 0)
                last_msg = str(info.get("lastMessage", "") or "")
                rank_name = info.get("rankName") if info.get("rankName") and info.get("rankName") != "—" else info.get("rank")

                if total > 0 or ts > 0 or last_msg:
                    last_seen_iso = ""
                    if ts > 0:
                        last_seen_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                    chat_data[name] = {
                        "total": total,
                        "rankName": rank_name or "Member",
                        "lastMessage": last_msg,
                        "lastSeenTS": ts,
                        "lastSeen": last_seen_iso,
                    }

            if total_batches == 1:
                session_phase = "final"
            elif batch_index == 1:
                session_phase = "start"
            elif is_final:
                session_phase = "final"
            else:
                session_phase = "chunk"

            payload = {
                "upload_session_id": session_id,
                "is_final_batch": bool(is_final),
                "batch_index": int(batch_index),
                "total_batches": int(total_batches),
                "removed_members": removed if is_final and roster_mode in ("delta", "full") else [],
                "session_phase": session_phase,
                "roster_mode": roster_mode,
                "roster_summary": {
                    "mode": roster_mode,
                    "added": len(added) if roster_mode in ("delta", "full") else 0,
                    "updated": len(updated) if roster_mode in ("delta", "full") else 0,
                    "removed": len(removed) if roster_mode in ("delta", "full") else 0,
                    "total_members": len(processed_data.get("roster_members") or processed_data.get("members") or {}),
                    "reason": roster_reason,
                },

                "uploadSessionId": session_id,
                "isFinalBatch": bool(is_final),
                "batchIndex": int(batch_index),
                "totalBatches": int(total_batches),
                "removedMembers": removed if is_final and roster_mode in ("delta", "full") else [],
                "sessionPhase": session_phase,
                "rosterMode": roster_mode,
                "rosterSummary": {
                    "mode": roster_mode,
                    "added": len(added) if roster_mode in ("delta", "full") else 0,
                    "updated": len(updated) if roster_mode in ("delta", "full") else 0,
                    "removed": len(removed) if roster_mode in ("delta", "full") else 0,
                    "totalMembers": len(processed_data.get("roster_members") or processed_data.get("members") or {}),
                    "reason": roster_reason,
                },

                "master_roster": master_roster,
                "data": chat_data,
                "has_changes": roster_mode in ("delta", "full"),
            }
            return payload

        idx = 0
        batch_index = 1

        while idx < total_members:
            batch_keys = all_keys[idx: idx + batch_size]
            is_final = (idx + batch_size) >= total_members
            payload = build_payload(batch_keys, batch_index, total_batches, is_final)

            try:
                self._set_ui_activity(
                    "Subiendo roster/chat",
                    progress=f"Lote {batch_index}/{total_batches} ({len(batch_keys)} miembros)",
                )
                logger.info(
                    f"[ROSTER] Lote {batch_index}/{total_batches} | miembros_en_lote={len(batch_keys)} "
                    f"| total_miembros={total_members} | modo={roster_mode} | razon={roster_reason}"
                )
                self._post_to_web_with_retry(payload, purpose=f"roster batch {batch_index}/{total_batches} ({len(batch_keys)})")
                idx += batch_size
                batch_index += 1
                time.sleep(0.35)
            except _TooLarge413:
                if batch_size <= 10:
                    logger.error(f"{Fore.RED}✘ 413 incluso con batch_size=10. Revisa límite backend o reduce data.")
                    raise
                new_batch = max(10, batch_size // 2)
                logger.warning(f"{Fore.RED}413. Reduciendo batch_size {batch_size} -> {new_batch} y reintentando.")
                batch_size = new_batch
                total_batches = max(1, int(math.ceil(total_members / batch_size)))
                time.sleep(1.0)

        self.state.roster_snapshot = self._build_roster_snapshot(processed_data.get("roster_members") or processed_data.get("members") or {})
        self._save_state()
        logger.info(f"{Fore.GREEN}✔✔ Upload Web Roster/Chat completado (session {session_id}).")
        self._set_ui_activity("Subida web completada", progress=f"Sesión {session_id}", level="success")

    # -------------------------
    # HTTP helper
    # -------------------------
    def _post_to_web_with_retry(self, payload: Dict[str, Any], purpose: str = "", allow_queue: bool = True):
        url = self.config.web_api_url
        headers = {"X-API-Key": self.config.web_api_key, "Content-Type": "application/json"}

        backoff = 1.0
        max_backoff = 20.0
        attempt = 0
        max_attempts_before_queue = 5

        while True:
            attempt += 1
            try:
                start = time.time()
                resp = self._session.post(url, json=payload, headers=headers, timeout=self.config.http_timeout)
                elapsed_ms = int((time.time() - start) * 1000)
                self.health["last_latency_ms"] = elapsed_ms
                self.health["last_payload_size"] = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                logger.debug(
                    f"[HTTP] POST attempt {attempt} -> {url} | ms={elapsed_ms} | size={self.health['last_payload_size']} "
                    f"| purpose={purpose}"
                )

                if resp.status_code == 200:
                    self.health["last_upload_ok"] = datetime.now().isoformat()
                    return

                if resp.status_code == 413:
                    raise _TooLarge413()

                if resp.status_code in (401, 403):
                    logger.error(f"{Fore.RED}Web auth error ({resp.status_code}) en {purpose}. Revisa WEB_API_KEY.")
                    self.ui.push_log(f"Auth error {resp.status_code} en {purpose}", level="error")
                    raise RuntimeError(f"Web auth error {resp.status_code}")

                if resp.status_code in (400, 422):
                    try:
                        details = resp.json()
                    except Exception:
                        details = resp.text[:400]
                    logger.error(f"{Fore.RED}Web validation error {resp.status_code} en {purpose}: {details}")
                    self.ui.push_log(f"Validación falló ({resp.status_code}) en {purpose}", level="error")
                    raise RuntimeError(f"Web validation error {resp.status_code}")

                logger.warning(f"{Fore.YELLOW}Web error {resp.status_code} en {purpose}. Intento {attempt}. Backoff {backoff:.1f}s")
                self.ui.push_log(f"Web error {resp.status_code} en {purpose}. Reintento {attempt}", level="warn")

                if allow_queue and attempt >= max_attempts_before_queue:
                    self.local_queue.enqueue(payload, purpose)
                    logger.warning(f"{Fore.MAGENTA}Persisten errores ({resp.status_code}). Payload en cola local.")
                    self._ui_queue_note = self._queue_status_note()
                    self._refresh_ui(self._is_wow_running())
                    return

                time.sleep(backoff)
                backoff = min(max_backoff, backoff * 1.6)
                continue

            except _TooLarge413:
                raise
            except requests.RequestException as e:
                logger.warning(f"{Fore.YELLOW}Web conexión falló en {purpose}: {e}. Intento {attempt}. Backoff {backoff:.1f}s")
                if allow_queue and attempt >= max_attempts_before_queue:
                    self.local_queue.enqueue(payload, purpose)
                    logger.warning(f"{Fore.MAGENTA}Sin conexión estable. Payload guardado en cola local ({purpose}).")
                    return
                time.sleep(backoff)
                backoff = min(max_backoff, backoff * 1.6)
                continue


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
