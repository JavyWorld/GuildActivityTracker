# Operación, configuración y troubleshooting

## Variables clave (`user_paths.py` o entorno)

- `WOW_ADDON_PATH`: ruta al `GuildActivityTracker.lua` (preferido en `user_paths.py`).
- `WEB_API_URL`: base o endpoint (se normaliza).
- `WEB_API_KEY`: clave del backend.
- `ENABLE_WEB_UPLOAD`: habilita subida.
- `ENABLE_STATS_INCREMENTAL_WEB`: habilita incremental de stats.
- `POLL_INTERVAL`: intervalo de monitoreo (segundos).
- `BATCH_SIZE`: lote roster/chat.
- `STATS_BATCH_SIZE`: lote stats.
- `HTTP_TIMEOUT`: timeout HTTP.
- `MIN_ROSTER_SIZE`: mínimo de miembros para procesar.

## Señales esperadas en logs

- Arranque: versión y path vigilado.
- Cambio de WoW running state.
- Parse OK y health panel.
- Trazas de lotes (`[ROSTER]`, `[STATS]`, `[HTTP]`).
- Ajuste por 413 (`Reduciendo batch_size ...`).

## Fallas frecuentes

1. **No encuentra archivo Lua**
   - validar `WOW_ADDON_PATH` y permisos.
2. **401/403 en API**
   - revisar `WEB_API_KEY` en `user_paths.py` o entorno.
3. **400/422 en API**
   - incompatibilidad de formato backend/payload.
4. **413 persistente**
   - bajar `BATCH_SIZE`/`STATS_BATCH_SIZE`.
5. **No detecta WoW**
   - revisar nombres de proceso en `WOW_PROCESS_NAMES`.

## Nota sobre UI

El bridge puede ejecutarse en headless o con UI (`ENABLE_UI=true`) usando Tkinter estándar.

