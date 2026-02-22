# Guild Activity Bridge - v43.0 (THE RELAY TANK) 🛡️

**Puente robusto de sincronización entre World of Warcraft y una API Web.**

Este script en Python monitorea en tiempo real el archivo `SavedVariables` de tu addon (`GuildActivityTracker.lua`), procesa los datos crudos y los sincroniza de manera inteligente los datos del addon hacia una **API Web** externa (visualización pública/web).

> **Versión actual:** 43.0 "THE RELAY TANK"
> **Enfoque:** Resiliencia extrema, subidas incrementales y manejo inteligente de errores de red (413 Payload Too Large).

---


## 📚 Documentación ampliada

Para una guía completa (IA + humano) revisa el índice: [`INDEX.md`](INDEX.md).

## 🚀 Características Principales

* **Monitoreo en Tiempo Real:** Detecta cambios en el archivo `.lua` automáticamente (al hacer `/reload` o desconectarse).
* **Sincronización Web:**
* **Web API:** Sube datos al sitio web utilizando un sistema de *chunking* (lotes) para evitar tiempos de espera.


* **Gestión de Datos "Relay Tank":**
* **Normalización de Nombres:** Unifica formatos "Nombre" vs "Nombre-Reino" para evitar duplicados.
* **Stats Incrementales:** Solo sube los *snapshots* de actividad nuevos a la web para no saturar la base de datos.
* **Manejo de Errores 413:** Si el servidor rechaza un paquete por tamaño, el bridge reduce automáticamente el tamaño del lote y reintenta sin detenerse.


* **Persistencia de Estado:** Utiliza `gat_bridge_state.json` para recordar qué datos ya fueron subidos, asegurando que no haya duplicados ni huecos en la información.

---

## 🛠️ Requisitos Previos

1. **Python 3.9+** instalado.
2. **World of Warcraft** con el addon `GuildActivityTracker` instalado y activo.

---

## 📦 Instalación (Portable, sin Auto-Install)

Este proyecto ahora está orientado a uso **portable**:

1. Descarga o clona el repositorio.
2. (Opcional) instala dependencias de `requirements.txt` si no vienen en tu entorno Python.
3. Edita `user_paths.py` con tus rutas/llaves.
4. Ejecuta `python guild_activity_bridge.py`.

> Ya no se recomienda el modo Auto-Install. El flujo principal es copiar/descargar y configurar rutas.

## ⚙️ Configuración rápida (`user_paths.py`)

Edita **solo** `user_paths.py` para configurar rutas y credenciales:

```python
WOW_ADDON_PATH = r"C:\Program Files (x86)\World of Warcraft\_retail_\WTF\Account\TU_CUENTA\SavedVariables\GuildActivityTracker.lua"
WEB_API_URL = "https://tu-sitio-web.com/api/upload"
WEB_API_KEY = "tu_api_key_secreta"
ENABLE_WEB_UPLOAD = True
POLL_INTERVAL = 5
BATCH_SIZE = 80
STATS_BATCH_SIZE = 80
HTTP_TIMEOUT = 120
```

> También puedes usar variables de entorno/.env; tienen prioridad sobre `user_paths.py`.

---

## ▶️ Uso

### Método Automático (Windows)

Simplemente ejecuta el archivo `iniciar.bat`. Esto abrirá la consola, activará el script y mantendrá la ventana abierta para ver los logs.

### Método Manual

Desde tu terminal o consola:

```bash
python guild_activity_bridge.py

```

Deberías ver un mensaje como:
`=== SISTEMA V43.0 (THE RELAY TANK) ===`
`Vigilando: ...GuildActivityTracker.lua`

---

## 📂 Estructura del Proyecto

* `guild_activity_bridge.py`: **El cerebro**. Script principal que contiene toda la lógica de la versión 43.0.
* `gat_bridge_state.json`: **Memoria**. Archivo generado automáticamente para guardar el estado de la última subida (no borrar).
* `user_paths.py`: archivo simple para configurar rutas/keys sin tocar el core.
* `ui/`: módulos visuales Tkinter (tema, widgets y dashboard).

---

## ⚠️ Solución de Problemas

* **Error "413 Payload Too Large":** El script se ajustará solo, pero si persiste, reduce el valor de `BATCH_SIZE` en `user_paths.py` o entorno.
* **No detecta cambios:** Asegúrate de hacer `/reload` o salir del juego (WoW solo escribe en el archivo al recargar la interfaz o cerrar sesión).

---

## 📝 Créditos

Desarrollado para mantener la sincronización de la Guild al día.
**Versión actual:** v43.0 (Stable Release).
