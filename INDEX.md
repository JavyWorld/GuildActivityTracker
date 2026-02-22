# Índice de Documentación — Guild Activity Tracker

Este índice está pensado para que **IAs y humanos** puedan entender el sistema completo sin leer todo el código fuente.

## Lectura recomendada (orden)

1. [`docs/OVERVIEW.md`](docs/OVERVIEW.md) — visión general del programa y flujo end-to-end.
2. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — componentes, responsabilidades y dependencias.
3. [`docs/INPUT_FORMAT_SPEC.md`](docs/INPUT_FORMAT_SPEC.md) — formato exacto esperado del archivo `GuildActivityTracker.lua`.
4. [`docs/WEB_UPLOAD_SPEC.md`](docs/WEB_UPLOAD_SPEC.md) — formato exacto de payloads HTTP para la web.
5. [`docs/INVARIANTS.md`](docs/INVARIANTS.md) — reglas que **no deben romperse**.
6. [`docs/STATE_AND_RESILIENCE.md`](docs/STATE_AND_RESILIENCE.md) — estado persistente, cola local y recuperación de errores.
7. [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — operación diaria, configuración y troubleshooting.
8. [`docs/GLOSSARY.md`](docs/GLOSSARY.md) — glosario de términos del sistema.
9. [`docs/PORTABLE_SETUP.md`](docs/PORTABLE_SETUP.md) — guía portable sin instalador.

## Mapa rápido de archivos clave en código

- `guild_activity_bridge.py` — proceso principal (headless), parseo Lua, normalización, chunking y subida web.
- `user_paths.py` — archivo portable para editar rutas/keys sin tocar el core.
- `bridge_ui.py` + `ui/` — interfaz visual moderna basada en Tkinter (sin librerías externas).
- `gat_bridge_state.json` — estado persistente incremental (se genera/actualiza en runtime).
- `logs/guild_activity_bridge.log` — log principal.

