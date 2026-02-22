# Overview funcional

## ¿Qué hace este programa?

`GuildActivityBridge` monitorea el archivo `SavedVariables` del addon de WoW (`GuildActivityTracker.lua`) y, cuando detecta cambios:

1. Lee el contenido Lua.
2. Extrae y decodifica la tabla.
3. Unifica/normaliza datos de roster, actividad de chat y snapshots de stats.
4. Publica la información a una API web (`/api/upload`) en lotes (chunking).
5. Mantiene estado local para evitar duplicados y soportar reconexión/reintentos.

## Flujo end-to-end

```text
WoW Addon -> SavedVariables (.lua)
        -> guild_activity_bridge.py (parse + normalización + delta)
        -> payloads HTTP JSON por lotes
        -> API Web /api/upload
```

## Objetivo principal

Garantizar que la web reciba datos completos y consistentes, incluso con:

- cambios de formato de nombre (`Nombre` vs `Nombre-Reino`),
- lotes muy grandes (error 413),
- fallas temporales de red/API,
- reinicios del bridge o de WoW.

## Entradas y salidas

- **Entrada canónica:** archivo Lua de SavedVariables (`WOW_ADDON_PATH`).
- **Salida canónica:** JSON HTTP `POST` hacia `WEB_API_URL` con `X-API-Key`.
- **Estado local:** `gat_bridge_state.json` + `upload_queue.jsonl`.

