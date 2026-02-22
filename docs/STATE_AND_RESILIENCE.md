# Estado persistente y resiliencia

## Archivos de estado local

### `gat_bridge_state.json`

Campos:

- `last_uploaded_stats_ts`: último timestamp de stats confirmado.
- `last_web_session_id`: id de sesión de upload más reciente.
- `roster_snapshot`: foto compacta de roster para cálculo de delta.

### `upload_queue.jsonl`

Cola append-only de payloads no entregados.

Formato por línea:

- `ts` (int)
- `purpose` (string descriptivo)
- `payload` (dict JSON)

## Política de retry

En `_post_to_web_with_retry`:

- backoff exponencial (arranca ~1s, crece hasta tope),
- manejo explícito de códigos 401/403/400/422/413,
- tras varios intentos fallidos, persistencia en cola local.

## Reintento de cola

La cola se intenta drenar cuando:

- WoW vuelve a detectarse en ejecución,
- antes de nuevos uploads importantes.

Si un item vuelve a fallar, permanece en cola.

## Beneficio operativo

Este diseño evita pérdida de eventos por inestabilidad temporal de red o backend y permite eventual consistency.

