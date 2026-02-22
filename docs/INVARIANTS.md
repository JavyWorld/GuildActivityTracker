# Invariantes del sistema (reglas críticas)

Estas reglas deben mantenerse para no romper compatibilidad ni integridad de datos.

1. **La URL web debe terminar en `/api/upload`** tras normalización.
2. **La identidad canónica de jugador es `Nombre-Reino`** cuando hay realm disponible.
3. **No duplicar snapshots de stats ya enviados**:
   - solo `ts > last_uploaded_stats_ts` se sube.
4. **`last_uploaded_stats_ts` debe avanzar monótonamente**.
5. **El delta de roster se calcula contra `roster_snapshot` persistido**.
6. **`removed_members` se reporta solo al final de sesión delta/full**.
7. **Mínimo de lote efectivo = 10** para chunking dinámico.
8. **En fallas repetidas de red/API, no se pierde data**:
   - payload se guarda en `upload_queue.jsonl`.
9. **Si WoW no está corriendo, no se procesa archivo** (espera activa por polling).
10. **Solo se procesa cuando cambia el mtime del archivo** (o arranque inicial).
11. **El parser opera sobre el primer `{` y último `}` del archivo**.
12. **Compatibilidad de claves dual (`snake_case` + `camelCase`) en payload web**.

