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

- `Config`: variables de `user_paths.py` + entorno, defaults y normalización de URL web.
- `BridgeState`: `last_uploaded_stats_ts`, `last_web_session_id`, `roster_snapshot`.
- `LocalUploadQueue`: cola local persistente en JSONL ante fallos de red/API.
- `GuildActivityBridge`: orquestador completo.

### 2) `user_paths.py` (configuración editable)

Responsabilidades:

- centralizar paths y claves editables por usuario,
- permitir uso portable sin tocar el core,
- mantener overrides por variables de entorno cuando aplique.

### 3) `bridge_ui.py`
 (legacy / no crítico)

Ahora actúa como wrapper estable para una UI moderna basada en Tkinter puro (sin dependencias externas), apoyada por módulos en `ui/`.

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

