# Glosario

- **Bridge**: proceso Python que conecta SavedVariables con la API web.
- **SavedVariables**: archivo Lua persistido por addons de WoW.
- **Roster**: listado de miembros del guild y metadatos básicos.
- **Chat activity (`data`)**: métricas de actividad por jugador (mensajes, timestamps, etc.).
- **Stats snapshots**: series temporales (ej. onlineCount por timestamp).
- **Canonical name**: nombre normalizado `Nombre-Reino`.
- **Default realm**: reino inferido o configurado para completar nombres cortos.
- **Upload session**: identificador de corrida de carga web por lotes.
- **Chunking**: división en lotes para evitar payloads gigantes.
- **Delta roster**: envío de solo cambios (altas, cambios, bajas).
- **Full roster**: envío completo del roster.
- **Heartbeat no_change**: payload mínimo cuando no hubo cambios.
- **413 Payload Too Large**: respuesta HTTP que indica paquete demasiado grande.
- **Local queue**: cola local JSONL para reintento diferido.
- **Backoff**: espera creciente entre reintentos HTTP.
- **State file**: archivo JSON con estado incremental persistido.

