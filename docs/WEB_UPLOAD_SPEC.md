# Especificación de formato de subida web (`/api/upload`)

## Endpoint y autenticación

- URL final normalizada a `.../api/upload`.
- Método: `POST`
- Headers:
  - `X-API-Key: <WEB_API_KEY>`
  - `Content-Type: application/json`

## Convención de compatibilidad de claves

El bridge envía **ambas** convenciones de naming en varios payloads:

- `snake_case` (ej. `upload_session_id`)
- `camelCase` (ej. `uploadSessionId`)

Esto garantiza compatibilidad con backends antiguos/nuevos.

---

## A) Payload de stats incremental

Campos base:

- `upload_session_id` / `uploadSessionId`
- `is_final_batch` / `isFinalBatch` (actualmente `false` en stats incremental)
- `batch_index` / `batchIndex`
- `total_batches` / `totalBatches`
- `stats`: lista de snapshots

Cada snapshot normalizado incluye:

- `iso` (ISO UTC)
- `ts` (epoch int)
- `onlineCount` (int)
- `online` (dict)

Optimización importante:

- Solo el último snapshot de la serie conserva `online` completo.
- Snapshots anteriores del mismo envío se fuerzan a `online = {}` para reducir tamaño.

---

## B) Payload de roster/chat chunked

Campos de sesión/lote:

- `upload_session_id` / `uploadSessionId`
- `is_final_batch` / `isFinalBatch`
- `batch_index` / `batchIndex`
- `total_batches` / `totalBatches`
- `session_phase` / `sessionPhase` (`start`, `chunk`, `final`)

Control de delta:

- `roster_mode` / `rosterMode`: `delta`, `full`, `no_change`
- `roster_summary` / `rosterSummary`:
  - `mode`
  - `added`
  - `updated`
  - `removed`
  - `total_members` / `totalMembers`
  - `reason`
- `removed_members` / `removedMembers` (solo lote final en delta/full)

Datos de negocio:

- `master_roster`:
  - jugador -> `{ rank, lvl, class }`
- `data` (chat_data):
  - jugador -> `{ total, rankName, lastMessage, lastSeenTS, lastSeen }`
- `has_changes` (bool)

### Heartbeat sin cambios

Si no hay cambios de roster:

- se envía un único payload (`1/1`) con `master_roster: {}`, `data: {}`,
- `roster_mode = no_change`,
- resumen en cero,
- útil para mantener sesión viva/confirmar ciclo.

## Gestión de error 413 (payload grande)

Cuando el backend responde 413 durante envío de roster/chat:

- se lanza control interno,
- el bridge reduce `batch_size` a la mitad (mínimo 10),
- recalcula lotes y reintenta.

