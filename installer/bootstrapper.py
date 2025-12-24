from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
import textwrap
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, List


# ============================================================
# ✅ CONFIG HARD-CODED (ONE-CLICK)
# ============================================================

WEB_API_URL = "https://nexonir.replit.app"
WEB_API_KEY = "4df6e5051f959afa012c2498592765c7417e09b05b1f0edf451d34fb6b694ddf"

# Bridge/Uploader repo
UPLOADER_REPO = "JavyWorld/GuildActivityTracker"
UPLOADER_BRANCH = "main"
UPLOADER_ZIP_URL = f"https://github.com/{UPLOADER_REPO}/archive/refs/heads/{UPLOADER_BRANCH}.zip"

# Addon repo (folder final SIEMPRE será GuildActivityTracker)
ADDON_REPO = "JavyWorld/Guild-Command-Center"
ADDON_BRANCH = "main"
ADDON_ZIP_URL = f"https://github.com/{ADDON_REPO}/archive/refs/heads/{ADDON_BRANCH}.zip"

# Python portable embed
PYTHON_EMBED_VERSION = "3.11.9"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/"
    f"python-{PYTHON_EMBED_VERSION}-embed-amd64.zip"
)

# Tcl/Tk MSI (para habilitar tkinter en el Python embebido)
TCLTK_MSI_URL = f"https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/amd64/tcltk.msi"

# Install paths
INSTALL_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GuildActivityBridge"
LOG_FILE = INSTALL_ROOT / "installer_log.txt"

STARTUP_DIR = (
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
)

STARTUP_VBS_NAME = "GuildActivityBridge.vbs"


# ============================================================
# UI helpers
# ============================================================

def msgbox(title: str, text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0)
    except Exception:
        pass


def log(msg: str) -> None:
    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)
    line = f"[installer] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def step(n: int, title: str) -> None:
    log(f"Paso {n}: {title} ...")


def pause_console() -> None:
    try:
        os.system("pause")
    except Exception:
        pass


# ============================================================
# Download + Zip helpers
# ============================================================

def download_file(url: str, dest: Path) -> Path:
    log(f"Descargando: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "GAT-Installer/1.0"})
    with urllib.request.urlopen(req, timeout=240) as r:
        data = r.read()
    dest.write_bytes(data)
    log(f"Descarga OK: {dest} ({dest.stat().st_size} bytes)")
    return dest


def extract_zip(zip_path: Path, dest: Path) -> None:
    log(f"Extrayendo {zip_path.name} -> {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)


def download_and_extract_repo(zip_url: str, tmp_dir: Path) -> Path:
    tmp_zip = tmp_dir / "repo.zip"
    download_file(zip_url, tmp_zip)
    extract_zip(tmp_zip, tmp_dir)

    roots = [p for p in tmp_dir.iterdir() if p.is_dir()]
    if not roots:
        raise RuntimeError("Zip extraído sin carpetas (estructura inesperada).")

    roots.sort(key=lambda p: sum(1 for _ in p.rglob("*")), reverse=True)
    return roots[0]


def find_file_ci(repo_root: Path, filename: str) -> Optional[Path]:
    """Busca un archivo por nombre, case-insensitive."""
    target = filename.lower()
    for p in repo_root.rglob("*"):
        if p.is_file() and p.name.lower() == target:
            return p
    return None


# ============================================================
# Portable Python + pip
# ============================================================

def ensure_portable_python(target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    python_dir = target_dir / f"python-{PYTHON_EMBED_VERSION}"
    python_exe = python_dir / "python.exe"

    if python_exe.exists():
        log("Python portable ya existe.")
        return python_exe

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        tmp_zip = td_path / "python-embed.zip"
        download_file(PYTHON_EMBED_URL, tmp_zip)
        extract_zip(tmp_zip, python_dir)

    # habilitar import site + permitir imports locales (.\" y .\DLLs)
    pth_file = next(python_dir.glob("*._pth"), None)
    if pth_file:
        pth_lines = pth_file.read_text(encoding="utf-8").splitlines()
        out: List[str] = []
        for line in pth_lines:
            s = line.strip()
            if s.startswith("#import site") or s == "import site":
                out.append("import site")
            else:
                out.append(line)

        # Asegura que el Python embebido pueda importar módulos del propio folder y DLLs (tkinter/_tkinter)
        def _has_entry(value: str) -> bool:
            v = value.strip().lower()
            for l in out:
                if l.strip().lower() == v:
                    return True
            return False

        # Inserta antes de "import site" (si existe) para mantenerlo como última línea
        insert_at = len(out)
        for i, l in enumerate(out):
            if l.strip() == "import site":
                insert_at = i
                break

        if not _has_entry("."):
            out.insert(insert_at, ".")
            insert_at += 1
        if not (_has_entry(".\\DLLs") or _has_entry(".\\dlls") or _has_entry("DLLs") or _has_entry("dlls") or _has_entry(".\\DLLS")):
            out.insert(insert_at, ".\\DLLs")

        pth_file.write_text("\n".join(out) + "\n", encoding="utf-8")
        log(f"Actualizado {pth_file.name} (import site + rutas locales habilitadas).")

    # instalar pip
    get_pip = target_dir / "get-pip.py"
    download_file("https://bootstrap.pypa.io/get-pip.py", get_pip)
    log("Instalando pip (Python portable)...")
    subprocess.run([str(python_exe), str(get_pip)], check=True)

    return python_exe



def ensure_tkinter_support(python_dir: Path, python_exe: Optional[Path] = None) -> None:
    """
    Habilita tkinter en el Python "embeddable" copiando Tcl/Tk + _tkinter.pyd desde el MSI oficial (tcltk.msi).
    
    Nota: tkinter NO se puede instalar via pip; necesita DLLs/runtime de Tcl/Tk. (Por eso usamos tcltk.msi).
    """
    try:
        dlls_dir = python_dir / "DLLs"
        tcl_dir = python_dir / "tcl"
        dlls_dir.mkdir(parents=True, exist_ok=True)

        have_pyd = (python_dir / "_tkinter.pyd").exists() or (dlls_dir / "_tkinter.pyd").exists()
        have_tcl = (tcl_dir / "tcl8.6").exists() and (tcl_dir / "tk8.6").exists()
        if have_pyd and have_tcl:
            log("Tkinter ya está presente en el Python portable (Tcl/Tk + _tkinter).")
            return

        log("Instalando soporte Tkinter (Tcl/Tk) en el Python portable...")
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            msi_path = td_path / "tcltk.msi"
            extract_root = td_path / "tcltk_extract"
            extract_root.mkdir(parents=True, exist_ok=True)

            download_file(TCLTK_MSI_URL, msi_path)

            # Extrae el MSI sin instalar "Python completo" (admin install /a)
            # msiexec viene en Windows.
            cmd = ["msiexec", "/a", str(msi_path), "/qn", f"TARGETDIR={str(extract_root)}"]
            log("Ejecutando: " + " ".join(cmd))
            subprocess.run(cmd, check=True)

            # Encuentra DLLs (ubicación varía según versión). Buscamos _tkinter.pyd como ancla.
            tk_pyds = list(extract_root.rglob("_tkinter.pyd"))
            if not tk_pyds:
                raise RuntimeError("No encontré _tkinter.pyd dentro de tcltk.msi (extracción falló).")

            src_tk_pyd = tk_pyds[0]
            src_dlls = src_tk_pyd.parent

            # tcl/ (busca carpeta tcl8.6)
            tcl8 = list(extract_root.rglob("tcl8.6"))
            if not tcl8:
                raise RuntimeError("No encontré la carpeta tcl8.6 dentro de tcltk.msi.")
            src_tcl_root = tcl8[0].parent  # .../tcl

            # Copiar DLLs necesarias
            for fname in ["_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"]:
                src = src_dlls / fname
                if src.exists():
                    dst = dlls_dir / fname
                    shutil.copy2(src, dst)
                    log(f"Copiado: {fname} -> {dst}")

            # Copiar carpeta tcl completa
            if tcl_dir.exists():
                shutil.rmtree(tcl_dir, ignore_errors=True)
            shutil.copytree(src_tcl_root, tcl_dir)
            log(f"Copiado: tcl -> {tcl_dir}")

        # Quick self-test
        if python_exe and python_exe.exists():
            try:
                out = subprocess.check_output([str(python_exe), "-c", "import tkinter; print(tkinter.TkVersion)"], text=True).strip()
                log(f"Tkinter OK. TkVersion={out}")
            except Exception as exc:
                log(f"WARNING: Tkinter self-test falló: {exc}")

    except Exception as exc:
        log(f"WARNING: No pude habilitar tkinter automáticamente: {exc}")

def pip_install(python_exe: Path, requirements: Path) -> None:
    log("Instalando dependencias con pip...")
    subprocess.run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(python_exe), "-m", "pip", "install", "-r", str(requirements)], check=True)


# ============================================================
# WoW detection
# ============================================================

def detect_wow_addons_paths() -> List[Path]:
    candidates: List[Path] = []
    program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES")
    user_profile = os.environ.get("USERPROFILE")

    if program_files:
        wow_root = Path(program_files) / "World of Warcraft"
        candidates.extend([
            wow_root / "_retail_" / "Interface" / "AddOns",
            wow_root / "_classic_" / "Interface" / "AddOns",
            wow_root / "_classic_era_" / "Interface" / "AddOns",
        ])

    if user_profile:
        docs_root = Path(user_profile) / "Documents" / "World of Warcraft"
        candidates.extend([
            docs_root / "_retail_" / "Interface" / "AddOns",
            docs_root / "_classic_" / "Interface" / "AddOns",
            docs_root / "_classic_era_" / "Interface" / "AddOns",
        ])

    return candidates


def choose_wow_addons_path() -> Path:
    for p in detect_wow_addons_paths():
        if p.exists():
            log(f"Detectado AddOns existente: {p}")
            return p

    program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES")
    user_profile = os.environ.get("USERPROFILE")

    pf_candidate = Path(program_files) / "World of Warcraft" / "_retail_" / "Interface" / "AddOns" if program_files else None
    docs_candidate = Path(user_profile) / "Documents" / "World of Warcraft" / "_retail_" / "Interface" / "AddOns" if user_profile else None

    if pf_candidate:
        try:
            pf_candidate.mkdir(parents=True, exist_ok=True)
            log(f"Creada ruta AddOns (Program Files): {pf_candidate}")
            return pf_candidate
        except Exception as exc:
            log(f"No pude crear AddOns en Program Files (probable permisos). Motivo: {exc}")

    if docs_candidate:
        docs_candidate.mkdir(parents=True, exist_ok=True)
        log(f"Creada ruta AddOns (Documents): {docs_candidate}")
        return docs_candidate

    fallback = Path.home() / "World of Warcraft" / "_retail_" / "Interface" / "AddOns"
    fallback.mkdir(parents=True, exist_ok=True)
    log(f"Fallback AddOns: {fallback}")
    return fallback


def detect_savedvariables_from_addons_path(addons_path: Path) -> Optional[Path]:
    wow_root = None
    for parent in [addons_path] + list(addons_path.parents):
        name = parent.name.lower()
        if name in ("_retail_", "_classic_", "_classic_era_", "_ptr_", "_beta_"):
            wow_root = parent
            break

    if wow_root is None:
        return None

    account_root = wow_root / "WTF" / "Account"
    if not account_root.exists():
        return None

    candidates = list(account_root.glob("*/SavedVariables/GuildActivityTracker.lua"))
    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# ============================================================
# Bridge + Addon installation
# ============================================================

def copy_bridge_from_repo(repo_root: Path, install_root: Path) -> None:
    install_root.mkdir(parents=True, exist_ok=True)

    # 🔥 Case-insensitive + validación dura
    required = ["guild_activity_bridge.py", "requirements.txt"]
    copied = []

    for name in required:
        p = find_file_ci(repo_root, name)
        if not p or not p.is_file():
            raise RuntimeError(f"No encontré '{name}' dentro del repo {UPLOADER_REPO} (case-insensitive).")
        shutil.copy2(p, install_root / name)
        copied.append(name)
        log(f"Copiado {name} <- {p}")

    # (UI eliminada) No copiamos bridge_ui.py para mantener la instalación simple.


    # opcional media/
    media = None
    for p in repo_root.rglob("*"):
        if p.is_dir() and p.name.lower() == "media":
            media = p
            break
    if media:
        dst = install_root / "media"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(media, dst)
        log("Copiado media/ (opcional)")


def install_addon_as_guildactivitytracker(addons_path: Path) -> None:
    addons_path.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        repo_root = download_and_extract_repo(ADDON_ZIP_URL, td_path)

        # Detectar carpeta raíz del addon por .toc (case-insensitive)
        addon_source = None
        for d in repo_root.rglob("*"):
            if d.is_dir():
                for f in d.iterdir():
                    if f.is_file() and f.suffix.lower() == ".toc":
                        addon_source = d
                        break
            if addon_source:
                break

        if addon_source is None:
            raise RuntimeError("No encontré ningún .toc en el repo del addon. Revisa estructura del repo.")

        target = addons_path / "GuildActivityTracker"
        if target.exists():
            shutil.rmtree(target)

        shutil.copytree(addon_source, target)
        log(f"Addon instalado como: {target}")


# ============================================================
# env + scripts + startup
# ============================================================

def write_env_file(install_root: Path, wow_addon_path_value: str) -> None:
    env_path = install_root / ".env"
    content = textwrap.dedent(
        f"""
        WEB_API_URL={WEB_API_URL}
        WEB_API_KEY={WEB_API_KEY}
        WOW_ADDON_PATH="{wow_addon_path_value}"
        ENABLE_UI=false
        GAT_CONSOLE_LOG_LEVEL=INFO
        GAT_FILE_LOG_LEVEL=DEBUG
        GAT_LOG_DIR="logs"
        """
    ).strip() + "\n"
    env_path.write_text(content, encoding="utf-8")
    log(".env creado.")


def create_start_scripts(install_root: Path, python_exe: Path) -> None:
    """Crea scripts para arrancar el bridge en modo consola (sin UI).

    - start_bridge.bat: arranca el bridge en el mismo CMD.
    - start_bridge_minimized.vbs: arranca el .bat en un CMD minimizado (taskbar).
    """
    runner = install_root / "start_bridge.bat"
    runner.write_text(
        textwrap.dedent(
            f"""
            @echo off
            setlocal
            cd /d "{install_root}"
            "{python_exe}" -u "{install_root / 'guild_activity_bridge.py'}"
            endlocal
            """
        ).strip() + "\n",
        encoding="utf-8",
    )

    vbs_min = install_root / "start_bridge_minimized.vbs"
    vbs_min.write_text(
        textwrap.dedent(
            f"""
            Set WshShell = CreateObject("WScript.Shell")
            ' 7 = Minimized, stays in taskbar
            WshShell.Run chr(34) & "{runner}" & chr(34), 7, False
            """
        ).strip() + "\n",
        encoding="utf-8",
    )

    log("start_bridge.bat + start_bridge_minimized.vbs creados.")


def register_startup(install_root: Path) -> None:
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    src = install_root / "start_bridge_minimized.vbs"
    dst = STARTUP_DIR / STARTUP_VBS_NAME
    # Sobrescribir limpio si ya existía
    if dst.exists():
        try:
            dst.unlink()
        except Exception:
            pass
    shutil.copy2(src, dst)
    log(f"Autostart registrado: {dst}")


# ============================================================
# Desktop helpers (solo .cmd para cero duplicados)
# ============================================================

def get_desktop_dir() -> Path:
    # Known Folder Desktop (fiable)
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def guid_from_string(s: str) -> GUID:
        import uuid
        u = uuid.UUID(s)
        data4 = (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:])
        return GUID(u.time_low, u.time_mid, u.time_hi_version, data4)

    FOLDERID_Desktop = guid_from_string("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
    SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
    SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]

    ppszPath = ctypes.c_wchar_p()
    hr = SHGetKnownFolderPath(ctypes.byref(FOLDERID_Desktop), 0, None, ctypes.byref(ppszPath))
    if hr == 0 and ppszPath.value:
        return Path(ppszPath.value)

    up = os.environ.get("USERPROFILE")
    return Path(up) / "Desktop" if up else Path.home() / "Desktop"


def cleanup_old_desktop_items(desktop: Path) -> None:
    # Borra cualquier GAT Bridge - * (.cmd/.lnk) de instalaciones anteriores
    for p in desktop.glob("GAT Bridge - *.*"):
        try:
            if p.is_file():
                p.unlink()
        except Exception:
            pass


def write_verify_script(install_root: Path, addons_path_str: str) -> None:
    verify = install_root / "verify_install.bat"

    root = str(install_root)
    python_exe = str((install_root / "python-3.11.9" / "python.exe"))
    vbs_startup = str(STARTUP_DIR / STARTUP_VBS_NAME)

    verify.write_text(
        textwrap.dedent(
            rf"""
            @echo off
            setlocal
            title GAT Bridge - Verificar Instalacion

            echo.
            echo ==========================================
            echo   GAT Bridge - Verificar Instalacion
            echo ==========================================
            echo.
            echo Install root: {root}
            echo.

            call :chk "{root}\guild_activity_bridge.py" "guild_activity_bridge.py"
            call :chk "{root}\requirements.txt" "requirements.txt"
            call :chk "{root}\.env" ".env"
            call :chk "{root}\start_bridge.bat" "start_bridge.bat"
            call :chk "{root}\start_bridge_minimized.vbs" "start_bridge_minimized.vbs"
            call :chk "{root}\installer_log.txt" "installer_log.txt"
            call :chk "{python_exe}" "Python portable: python.exe"

            echo.
            if exist "{vbs_startup}" (
              echo OK: Autostart presente: {vbs_startup}
            ) else (
              echo WARN: Autostart NO presente: {vbs_startup}
            )

            echo.
            echo WoW AddOns path detectado:
            echo   {addons_path_str}
            echo.

            echo Test import basico (requests, watchdog, dotenv)...
            "{python_exe}" -c "import requests, watchdog, dotenv; print('OK imports')"
            if errorlevel 1 (
              echo WARN: El test de imports fallo. Reinstala dependencias.
            )

            echo.
            echo Listo. Presiona una tecla para cerrar.
            pause >nul
            exit /b 0

            :chk
            if exist %1 (
              echo OK: %2
            ) else (
              echo WARN: %2 NO encontrado
            )
            exit /b 0
            """
        ).strip() + "\n",
        encoding="utf-8",
    )
    log("verify_install.bat creado.")


def create_desktop_cmds_only(install_root: Path) -> None:
    desktop = get_desktop_dir()
    if not desktop.exists():
        log(f"WARNING: Desktop no existe: {desktop}")
        return

    vbs_min = install_root / "start_bridge_minimized.vbs"
    verify = install_root / "verify_install.bat"
    open_folder = install_root
    open_log = LOG_FILE

    cmds = [
        ("GAT Bridge - Start (Minimized).cmd", f'@echo off\nwscript.exe "{vbs_min}"\n'),
        ("GAT Bridge - Verify Install.cmd", f'@echo off\ncmd /k "{verify}"\n'),
        ("GAT Bridge - Open Folder.cmd", f'@echo off\nexplorer.exe "{open_folder}"\n'),
        ("GAT Bridge - Open Installer Log.cmd", f'@echo off\nnotepad.exe "{open_log}"\n'),
    ]

    created = []
    for name, body in cmds:
        path = desktop / name
        path.write_text(body, encoding="utf-8")
        created.append(path)

    # Limpieza: eliminar duplicados por nombre (case-insensitive) más allá de estos 4
    keep_names = set(n.lower() for n, _ in cmds)
    for p in desktop.glob("GAT Bridge - *.cmd"):
        if p.name.lower() not in keep_names:
            try:
                p.unlink()
            except Exception:
                pass

    log(f"Desktop (.cmd) creados y duplicados limpiados: {desktop}")


def write_install_summary(install_root: Path, wow_addons_path: Path, wow_addon_path_value: str) -> None:
    summary = install_root / "INSTALL_SUMMARY.txt"
    summary.write_text(
        textwrap.dedent(f"""
        ✅ Guild Activity Bridge - Instalación completada

        Bridge instalado en:
        {install_root}

        WoW AddOns path:
        {wow_addons_path}

        Addon instalado como:
        {wow_addons_path}\\GuildActivityTracker

        WOW_ADDON_PATH (SavedVariables detectado):
        {wow_addon_path_value}

        Autostart:
        {STARTUP_DIR}\\{STARTUP_VBS_NAME}

        Tips:
        - Abre WoW, asegúrate que el addon esté activado.
        - Haz /reload (o sal del juego) para que WoW escriba SavedVariables.
        - En el escritorio tienes "GAT Bridge - Verify Install".
        """).strip() + "\n",
        encoding="utf-8",
    )
    log("INSTALL_SUMMARY.txt creado.")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    try:
        INSTALL_ROOT.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists():
            LOG_FILE.unlink()
    except Exception:
        pass

    log("==========================================")
    log("GAT Installer iniciado (One-Click)")
    log(f"Install root: {INSTALL_ROOT}")
    log("==========================================")

    try:
        step(1, "Preparar Python portable + pip")
        python_exe = ensure_portable_python(INSTALL_ROOT)
        # UI eliminada: omitimos Tkinter/Tcl/Tk para mantener el instalador simple y robusto.

        step(2, "Descargar repo del Bridge/Uploader")
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            uploader_root = download_and_extract_repo(UPLOADER_ZIP_URL, td_path)
            log(f"Uploader repo extraído: {uploader_root}")

            step(3, "Copiar archivos del Bridge (sin iniciar.bat)")
            copy_bridge_from_repo(uploader_root, INSTALL_ROOT)

        step(4, "Instalar dependencias del Bridge (pip)")
        pip_install(python_exe, INSTALL_ROOT / "requirements.txt")

        step(5, "Detectar / escoger ruta AddOns de WoW")
        wow_addons_path = choose_wow_addons_path()
        log(f"WoW AddOns path elegido: {wow_addons_path}")

        step(6, "Instalar Addon como GuildActivityTracker")
        install_addon_as_guildactivitytracker(wow_addons_path)

        step(7, "Detectar SavedVariables y escribir .env")
        savedvars = detect_savedvariables_from_addons_path(wow_addons_path)
        if savedvars:
            wow_addon_path_value = str(savedvars)
            log(f"SavedVariables detectado: {wow_addon_path_value}")
        else:
            wow_addon_path_value = "."
            log("No encontré GuildActivityTracker.lua todavía. WOW_ADDON_PATH='.' (auto-detección).")

        write_env_file(INSTALL_ROOT, wow_addon_path_value)

        step(8, "Crear scripts de arranque + verify + summary")
        create_start_scripts(INSTALL_ROOT, python_exe)
        write_verify_script(INSTALL_ROOT, wow_addons_path)
        write_install_summary(INSTALL_ROOT, wow_addons_path, wow_addon_path_value)

        step(9, "Registrar AutoStart + crear 4 accesos (.cmd) sin duplicados")
        register_startup(INSTALL_ROOT)
        create_desktop_cmds_only(INSTALL_ROOT)

        step(10, "Arrancar Bridge automáticamente (minimized)")
        vbs = INSTALL_ROOT / "start_bridge_minimized.vbs"
        subprocess.Popen(["wscript.exe", str(vbs)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        log("✅ Instalación COMPLETADA con éxito.")
        msgbox(
            "GAT Installer - OK",
            "Instalación completada ✅\n\n"
            f"Bridge: {INSTALL_ROOT}\n"
            f"Addon:  {wow_addons_path}\\GuildActivityTracker\n\n"
            "Autostart creado.\n"
            "Bridge arrancado en background.\n\n"
            "Tip: abre WoW y haz /reload si aún no existe SavedVariables.",
        )

    except Exception as exc:
        log("❌ ERROR durante la instalación:")
        log(str(exc))
        msgbox(
            "GAT Installer - ERROR",
            "Falló la instalación ❌\n\n"
            f"Error: {exc}\n\n"
            f"Revisa el log:\n{LOG_FILE}",
        )

    print("\nListo. Puedes cerrar esta ventana.")
    print(f"Log: {LOG_FILE}")
    pause_console()


if __name__ == "__main__":
    main()