# Arquitectura del sistema

## Módulos principales

### 1) `guild_activity_bridge.py` (núcleo)

Responsabilidades:

- cargar configuración (`Config`),
- observar cambios en archivo Lua,
- parsear/normalizar data (`_process_and_merge_data`, `_normalize_stats`),
- calcular delta de roster,
- subir chunks de roster y stats,
- manejar retries/backoff/cola local,
- persistir estado incremental (`BridgeState`).

Clases clave:

- `Config`: variables `.env`, defaults y normalización de URL web.
- `BridgeState`: `last_uploaded_stats_ts`, `last_web_session_id`, `roster_snapshot`.
- `LocalUploadQueue`: cola local persistente en JSONL ante fallos de red/API.
- `GuildActivityBridge`: orquestador completo.

### 2) `installer/bootstrapper.py` (instalación)

Responsabilidades:

- descarga Python portable,
- instala dependencias (`requirements.txt`),
- configura `.env`,
- integra/descarga addon,
- configura autostart Windows.

### 3) `bridge_ui.py` (legacy / no crítico)

Existe como wrapper UI/tray histórico, pero el bridge actual está declarado en modo consola/headless dentro del núcleo (`BridgeUI = None`).

## Dependencias relevantes

- `slpp` para decodificar tabla Lua.
- `requests` para HTTP.
- `python-dotenv` para configuración.
- `psutil` (opcional) para detectar proceso de WoW.
- `colorama` para logs legibles en consola.

## Ciclo operativo resumido

1. Detecta WoW en ejecución (`_is_wow_running`).
2. Vigila mtime de `GuildActivityTracker.lua`.
3. En cambio de archivo -> `process_file()`.
4. Parsea tabla Lua (`_extract_lua_table` + `slpp.decode`).
5. Genera `processed_data` unificado.
6. Sube stats incremental.
7. Sube roster/chat por lotes con modo `delta` o `full`.
8. Actualiza estado y health panel.

