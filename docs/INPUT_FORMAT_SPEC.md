# Especificación de formato de entrada (SavedVariables Lua)

## Archivo esperado

- Ruta: `WOW_ADDON_PATH`
- Formato: texto Lua con una asignación de tabla global (ejemplo típico de SavedVariables).

El parser del bridge no depende del nombre exacto de la variable; busca desde el primer `{` hasta el último `}` y decodifica ese bloque.

## Estructura lógica esperada

La tabla raíz debe contener (según disponibilidad):

- `roster` (dict)
- `data` (dict de actividad/chat)
- `stats` (list o dict)
- `mythic` (dict opcional)

### `roster` (obligatorio para upload útil)

Mapa de jugador -> info base.

Campos consumidos por jugador:

- `rank` (string)
- `level` (int)
- `class` (string)
- `is_online` (bool)

Si faltan, el bridge aplica defaults.

### `data` (actividad/chat)

Mapa de jugador -> datos de actividad.

Campos relevantes:

- `total` (int)
- `daily` (dict)
- `lastMessage` (string)
- `rankName` (string)
- `rankIndex` (int)
- `lastSeen` (string)
- `lastSeenTS` (epoch int)

### `stats` (snapshots)

Se aceptan dos variantes:

1. **Lista de snapshots** (`list[dict]`) con campos como `ts`, `iso`, `onlineCount`, `online`.
2. **Diccionario** (`dict`) indexado por timestamps o por índices serializados.

El bridge normaliza ambas a una lista ordenada por `ts`.

### `mythic`

Se conserva como dict si existe; no participa en la lógica principal de chunking de roster/chat.

## Resolución de nombres y realm

El sistema canoniza jugadores a `Nombre-Reino` cuando puede inferir reino (`defaultRealm`).

Reglas:

- si ya viene con guion (`Nombre-Reino`) se respeta,
- si viene solo `Nombre`, se completa con realm inferido,
- si hay ambigüedad en `data`, se usa lógica por `lastSeenTS` o se omite match dudoso.

