"""Self-contained bootstrapper to install Guild Activity Bridge and the WoW addon.

Este script está diseñado para empaquetarse como un único `.exe` de Windows (PyInstaller).
Cuando se ejecuta:

1) Despliega un runtime de Python portátil (no requiere Python preinstalado).
2) Instala dependencias del bridge.
3) Copia el bridge a un directorio de instalación por usuario.
4) Descarga e instala el addon "Guild-Command-Center" desde GitHub.
5) Crea lanzadores y autostart opcional para que el bridge espere en segundo plano.

MEJORA CLAVE:
- Configura WOW_ADDON_PATH apuntando al SavedVariables real:
  ...\\_retail_\\WTF\\Account\\*\\SavedVariables\\GuildActivityTracker.lua
  (aunque cambie el folder de cuenta tipo 364416775#1)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional

import requests


PYTHON_EMBED_VERSION = "3.11.9"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_EMBED_VERSION}/"
    f"python-{PYTHON_EMBED_VERSION}-embed-amd64.zip"
)

ADDON_REPO = "JavyWorld/Guild-Command-Center"
ADDON_BRANCH = "main"
REPO_ZIP_URL = f"https://github.com/{ADDON_REPO}/archive/refs/heads/{ADDON_BRANCH}.zip"

# Optional config file baked into the packaged .exe to avoid prompting users.
INSTALL_CONFIG = Path(__file__).with_name("install_config.json")

INSTALL_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "GuildActivityBridge"
STARTUP_DIR = (
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
)


def log(msg: str) -> None:
    print(f"[installer] {msg}")


def log_step(step: int, title: str) -> None:
    log(f"Paso {step}: {title} ...")


def download_file(url: str, dest: Path) -> Path:
    log(f"Descargando {url} ...")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with dest.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=1024 * 128):
            if chunk:
                fh.write(chunk)
    return dest


def extract_zip(zip_path: Path, dest: Path) -> None:
    log(f"Extrayendo {zip_path.name} en {dest} ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)


def ensure_portable_python(target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    python_dir = target_dir / f"python-{PYTHON_EMBED_VERSION}"
    python_exe = python_dir / "python.exe"
    if python_exe.exists():
        log("Runtime de Python portátil ya presente.")
        return python_exe

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_zip = Path(tmp_dir) / "python-embed.zip"
        download_file(PYTHON_EMBED_URL, tmp_zip)
        extract_zip(tmp_zip, python_dir)

    # Habilitar site-packages en el embed (quitando la restricción del ._pth)
    pth_file = next(python_dir.glob("*._pth"), None)
    if pth_file:
        content = pth_file.read_text(encoding="utf-8").splitlines()
        edited: list[str] = []
        for line in content:
            # Dejamos import site activo
            if line.strip().startswith("import site"):
                edited.append("import site")
            else:
                edited.append(line)
        pth_file.write_text("\n".join(edited) + "\n", encoding="utf-8")
        log(f"Actualizado {pth_file.name} para habilitar site-packages.")

    get_pip = target_dir / "get-pip.py"
    download_file("https://bootstrap.pypa.io/get-pip.py", get_pip)
    log("Instalando pip en el runtime portátil ...")
    subprocess.run([str(python_exe), str(get_pip)], check=True)
    return python_exe


def pip_install(python_exe: Path, requirements: Path) -> None:
    log("Instalando dependencias del bridge ...")
    subprocess.run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(python_exe), "-m", "pip", "install", "-r", str(requirements)], check=True)


def copy_project_files(
    source_root: Path, install_root: Path, extras: Optional[Iterable[Path]] = None
) -> None:
    install_root.mkdir(parents=True, exist_ok=True)

    # OJO: credentials.json contiene credenciales de Google. Si piensas distribuir esto
    # a otras personas, lo recomendable es NO copiarlo y usar un modo "web-only".
    files_to_copy: List[Path] = [
        source_root / "guild_activity_bridge.py",
        source_root / "bridge_ui.py",
        source_root / "requirements.txt",
        source_root / "credentials.json",
        source_root / "iniciar.bat",
    ]
    if extras:
        files_to_copy.extend(extras)

    for path in files_to_copy:
        if path.exists():
            dest = install_root / path.name
            if path.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
            log(f"Copiado {path.name} -> {dest}")
        else:
            log(f"Advertencia: {path.name} no existe en source_root ({source_root}).")

    media_src = source_root / "media"
    media_dst = install_root / "media"
    if media_src.exists():
        if media_dst.exists():
            shutil.rmtree(media_dst)
        shutil.copytree(media_src, media_dst)
        log("Copiado directorio media/.")


def detect_wow_addons_paths() -> List[Path]:
    candidates: List[Path] = []
    program_files = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES")
    user_profile = os.environ.get("USERPROFILE")
    if program_files:
        wow_root = Path(program_files) / "World of Warcraft"
        candidates.extend(
            [
                wow_root / "_retail_" / "Interface" / "AddOns",
                wow_root / "_classic_" / "Interface" / "AddOns",
                wow_root / "_classic_era_" / "Interface" / "AddOns",
            ]
        )
    if user_profile:
        documents_root = Path(user_profile) / "Documents" / "World of Warcraft"
        candidates.extend(
            [
                documents_root / "_retail_" / "Interface" / "AddOns",
                documents_root / "_classic_" / "Interface" / "AddOns",
                documents_root / "_classic_era_" / "Interface" / "AddOns",
            ]
        )
    return [p for p in candidates if p.exists()]


def detect_savedvariables_from_addons_path(addons_path: Path) -> Optional[Path]:
    """
    A partir de:
      ...\\_retail_\\Interface\\AddOns
    detecta:
      ...\\_retail_\\WTF\\Account\\*\\SavedVariables\\GuildActivityTracker.lua

    Devuelve el archivo más reciente si hay varios.
    """
    addons_path = addons_path.expanduser()

    # Encontrar el root: _retail_, _classic_, etc.
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


def install_addon(addons_path: Path, extracted_repo_root: Optional[Path] = None) -> None:
    """
    Instala el addon dentro de Interface/AddOns.
    Si se pasa extracted_repo_root, lo instala desde ahí (ya descargado).
    Si no, descarga el zip del repo.
    """
    addons_path.mkdir(parents=True, exist_ok=True)

    def _install_from_repo_root(repo_root: Path) -> Path:
        addon_folder = repo_root / "Guild-Command-Center"
        if not addon_folder.exists():
            raise SystemExit(
                f"No se encontró la carpeta del addon en el repo extraído: {addon_folder}"
            )

        target_folder = addons_path / addon_folder.name
        if target_folder.exists():
            shutil.rmtree(target_folder)
        shutil.copytree(addon_folder, target_folder)
        return target_folder

    if extracted_repo_root is not None:
        target = _install_from_repo_root(extracted_repo_root)
        log(f"Addon instalado en {target}")
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_zip = Path(tmp_dir) / "repo.zip"
        download_file(REPO_ZIP_URL, tmp_zip)
        extract_zip(tmp_zip, Path(tmp_dir))
        extracted_root = next(Path(tmp_dir).iterdir())
        target = _install_from_repo_root(extracted_root)
        log(f"Addon instalado en {target}")


def write_env_file(
    install_root: Path, wow_addon_path_value: str, web_api_url: str, web_api_key: str
) -> None:
    env_path = install_root / ".env"

    # Comillas en WOW_ADDON_PATH por seguridad (path con #, espacios, etc.)
    template = textwrap.dedent(
        f"""
        WEB_API_URL={web_api_url}
        WEB_API_KEY={web_api_key}
        WOW_ADDON_PATH="{wow_addon_path_value}"
        ENABLE_AUTOSTART_UI=true
        """
    ).strip() + "\n"
    env_path.write_text(template, encoding="utf-8")
    log(f"Archivo .env generado en {env_path}")


def create_start_scripts(install_root: Path, python_exe: Path) -> None:
    runner = install_root / "start_bridge.bat"
    runner.write_text(
        textwrap.dedent(
            f"""
            @echo off
            cd /d "{install_root}"
            "{python_exe}" -u -B guild_activity_bridge.py
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    log(f"Creado {runner}")

    # VBS con ruta ABSOLUTA al .bat para que funcione incluso si lo copias a Startup
    launcher = install_root / "start_bridge_hidden.vbs"
    # Escape backslashes in the runner path for VBS
    vbs_runner_path = str(runner).replace('\\', '\\\\')
    launcher.write_text(
        textwrap.dedent(f"""
            Set shell = CreateObject("WScript.Shell")
            shell.Run "cmd /c \"{vbs_runner_path}\"", 0, False
        """).strip() + "\n",
        encoding="utf-8",
    )
    log(f"Creado {launcher}")


def register_startup(install_root: Path) -> None:
    if not STARTUP_DIR.exists():
        STARTUP_DIR.mkdir(parents=True, exist_ok=True)

    # Copiamos el vbs "oculto" al startup. Como contiene ruta absoluta al .bat, funciona 100%.
    shortcut = STARTUP_DIR / "GuildActivityBridge.vbs"
    src = install_root / "start_bridge_hidden.vbs"
    shutil.copy2(src, shortcut)
    log(f"Autostart configurado: {shortcut}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Instalador automático de Guild Activity Bridge (one-click)"
    )
    parser.add_argument("--web-api-url", help="URL de la Web API (se usará en .env)")
    parser.add_argument("--web-api-key", help="API key para la Web API")
    parser.add_argument("--wow-path", help="Ruta al directorio AddOns de World of Warcraft")
    parser.add_argument(
        "--no-startup",
        action="store_true",
        help="No registrar ejecución automática al iniciar Windows",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Permitir preguntas en consola si faltan datos (por defecto es 100% automático)",
    )
    return parser.parse_args()


def load_config() -> dict:
    if INSTALL_CONFIG.exists():
        try:
            return json.loads(INSTALL_CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log(f"Advertencia: no se pudo leer install_config.json ({exc}). Se ignorará.")
    return {}


def resolve_value(
    label: str,
    cli_value: Optional[str],
    cfg: dict,
    cfg_key: str,
    default: Optional[str] = None,
    interactive: bool = False,
) -> str:
    if cli_value:
        return cli_value
    if cfg_key in cfg and cfg[cfg_key]:
        return str(cfg[cfg_key])
    if default:
        return default
    if interactive:
        return input(f"{label}: ").strip()
    raise SystemExit(
        f"Falta {label}. Añádelo a install_config.json o pásalo como argumento --{cfg_key.replace('_', '-')}."
    )


def resolve_source_root_for_copy() -> Path:
    """
    Intenta usar el repo local (cuando ejecutas el bootstrapper dentro del repo).
    Si no existe, lanza error claro.
    (Nota: si quieres que el .exe sea totalmente autosuficiente sin repo local,
    puedes cambiar esto para descargar el repo y usarlo como source_root).
    """
    candidate = Path(__file__).resolve().parent.parent
    must_have = candidate / "guild_activity_bridge.py"
    if must_have.exists():
        return candidate
    raise SystemExit(
        "No se encontró guild_activity_bridge.py junto al bootstrapper.\n"
        "Ejecuta este instalador desde el repo o ajusta resolve_source_root_for_copy() "
        "para descargar el repo y copiar desde ahí."
    )


def main() -> None:
    args = parse_args()
    install_root = INSTALL_ROOT

    config = load_config()
    log(f"Instalando en {install_root}")

    log_step(1, "Preparar Python portátil")
    python_exe = ensure_portable_python(install_root)

    log_step(2, "Copiar archivos del bridge")
    source_root = resolve_source_root_for_copy()
    copy_project_files(source_root, install_root)

    log_step(3, "Instalar dependencias")
    pip_install(python_exe, install_root / "requirements.txt")

    log_step(4, "Detectar carpeta AddOns de WoW")
    detected_paths = detect_wow_addons_paths()
    default_addons_path = detected_paths[0] if detected_paths else None
    wow_addons_path = Path(
        resolve_value(
            "Ruta AddOns de WoW",
            args.wow_path,
            config,
            "wow_path",
            default=str(default_addons_path) if default_addons_path else None,
            interactive=args.interactive,
        )
    ).expanduser()

    log_step(5, "Instalar addon Guild-Command-Center")
    install_addon(wow_addons_path)

    web_api_url = resolve_value(
        "WEB_API_URL", args.web_api_url, config, "web_api_url", interactive=args.interactive
    )
    web_api_key = resolve_value(
        "WEB_API_KEY", args.web_api_key, config, "web_api_key", interactive=args.interactive
    )

    log_step(6, "Detectar SavedVariables y guardar configuración .env")
    savedvars = detect_savedvariables_from_addons_path(wow_addons_path)
    if savedvars:
        log(f"Detectado SavedVariables: {savedvars}")
        wow_addon_path_value = str(savedvars)
    else:
        # Si aún no existe, dejamos "." para que el bridge auto-detecte más tarde.
        log(
            "No se encontró GuildActivityTracker.lua todavía.\n"
            "Esto puede pasar si el usuario aún no abrió WoW con el addon.\n"
            "Se guardará WOW_ADDON_PATH=\".\" y el bridge auto-detectará cuando el archivo aparezca "
            "(tras abrir WoW y hacer /reload o salir del juego)."
        )
        wow_addon_path_value = "."

    write_env_file(install_root, wow_addon_path_value, web_api_url, web_api_key)

    log_step(7, "Crear lanzadores (UI oculta)")
    create_start_scripts(install_root, python_exe)

    if not args.no_startup:
        log_step(8, "Registrar inicio automático")
        register_startup(install_root)
    else:
        log("Se omitió el inicio automático (--no-startup)")

    log("Instalación completada. El bridge se ejecutará en segundo plano en el próximo inicio.")


if __name__ == "__main__":
    main()
    