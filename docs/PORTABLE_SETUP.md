# Portable Setup (sin instalador)

## Objetivo
Usar el proyecto por **copia/descarga directa** sin flujo de Auto-Install.

## Pasos
1. Descargar carpeta completa del proyecto.
2. Editar `user_paths.py`.
3. Ejecutar `python guild_activity_bridge.py`.

## Archivo de configuración separado
`user_paths.py` centraliza los campos que normalmente cambia el usuario final:

- `WOW_ADDON_PATH`
- `WEB_API_URL`
- `WEB_API_KEY`
- tuning básico (`POLL_INTERVAL`, `BATCH_SIZE`, etc.)

## Modularización aplicada
- UI separada en `ui/` (`dashboard.py`, `widgets.py`, `theme.py`).
- Wrapper de compatibilidad en `bridge_ui.py`.
- Configuración de paths desacoplada en `user_paths.py`.

