# Guild Activity Bridge - v43.0 (THE RELAY TANK) 🛡️

**Puente robusto de sincronización entre World of Warcraft, Google Sheets y Web APIs.**

Este script en Python monitorea en tiempo real el archivo `SavedVariables` de tu addon (`GuildActivityTracker.lua`), procesa los datos crudos y los sincroniza de manera inteligente hacia dos destinos: una hoja de cálculo de **Google Sheets** (para administración interna) y una **API Web** externa (para visualización pública/web).

> **Versión actual:** 43.0 "THE RELAY TANK"
> **Enfoque:** Resiliencia extrema, subidas incrementales y manejo inteligente de errores de red (413 Payload Too Large).

---


## 📚 Documentación ampliada

Para una guía completa (IA + humano) revisa el índice: [`INDEX.md`](INDEX.md).

## 🚀 Características Principales

* **Monitoreo en Tiempo Real:** Detecta cambios en el archivo `.lua` automáticamente (al hacer `/reload` o desconectarse).
* **Sincronización Dual:**
* **Google Sheets:** Actualiza Roster, Historial de Chat, Logs de Actividad, Scores de Míticas+ y Dashboard.
* **Web API:** Sube datos al sitio web utilizando un sistema de *chunking* (lotes) para evitar tiempos de espera.


* **Gestión de Datos "Relay Tank":**
* **Normalización de Nombres:** Unifica formatos "Nombre" vs "Nombre-Reino" para evitar duplicados.
* **Stats Incrementales:** Solo sube los *snapshots* de actividad nuevos a la web para no saturar la base de datos.
* **Manejo de Errores 413:** Si el servidor rechaza un paquete por tamaño, el bridge reduce automáticamente el tamaño del lote y reintenta sin detenerse.


* **Persistencia de Estado:** Utiliza `gat_bridge_state.json` para recordar qué datos ya fueron subidos, asegurando que no haya duplicados ni huecos en la información.

---

## 🛠️ Requisitos Previos

1. **Python 3.9+** instalado.
2. **Cuenta de Google Cloud** con las APIs de *Google Sheets* y *Google Drive* habilitadas.
3. **Credenciales de Servicio (JSON):** Archivo de autenticación de Google.
4. **World of Warcraft** con el addon `GuildActivityTracker` instalado y activo.

---

## 📦 Instalación

### Opción 1: Instalador todo-en-uno (Windows, sin Python)

1. Empaqueta `installer/bootstrapper.py` como `.exe` con PyInstaller (ejemplo):
   ```bash
   pyinstaller --onefile installer/bootstrapper.py
   ```
   El ejecutable resultante puede compartirse a cualquier PC con Windows.

2. En el equipo de destino, ejecuta el `.exe` y sigue los pocos prompts si no se detecta
   automáticamente la ruta de AddOns o las credenciales. El instalador:
   - Descarga un **Python portátil** y las dependencias del bridge.
   - Copia los archivos del bridge a `%LOCALAPPDATA%\GuildActivityBridge`.
   - Descarga e instala el addon **Guild-Command-Center** desde GitHub en la carpeta de AddOns.
   - Genera el `.env` con `WEB_API_URL`, `WEB_API_KEY` y `WOW_ADDON_PATH`.
   - Crea un lanzador oculto y lo registra en **Inicio de Windows** (puedes omitirlo con `--no-startup`).

3. Tras reiniciar Windows, el bridge quedará en segundo plano esperando los cambios del addon; no es
   necesario abrir la consola.

### Opción 2: Instalación manual

1. **Clonar el repositorio:**
```bash
git clone https://github.com/tu-usuario/guild-activity-bridge.git
cd guild-activity-bridge

```


2. **Crear entorno virtual (Recomendado):**
```bash
python -m venv venv
.\venv\Scripts\activate  # En Windows
# source venv/bin/activate  # En Linux/Mac

```


3. **Instalar dependencias:**
```bash
pip install -r requirements.txt

```


4. **Configurar credenciales:**
* Coloca tu archivo de credenciales de Google (ej. `credentials.json`) en la raíz del proyecto.



---

## ⚙️ Configuración (.env)

Crea un archivo llamado `.env` en la raíz del proyecto (puedes copiar el ejemplo a continuación). **Asegúrate de ajustar las rutas correctamente.**

```ini
# --- Google Sheets ---
GOOGLE_SHEETS_CREDENTIALS=credentials.json
GOOGLE_SHEET_NAME="Guild Activity Tracker"
GOOGLE_SHEET_WORKSHEET="Members"

# --- World of Warcraft ---
# Ruta EXACTA a tu archivo SavedVariables. Usa doble barra invertida (\\) en Windows.
WOW_ADDON_PATH="C:\\Program Files (x86)\\World of Warcraft\\_retail_\\WTF\\Account\\TU_CUENTA\\SavedVariables\\GuildActivityTracker.lua"

# --- Web API (Opcional) ---
ENABLE_WEB_UPLOAD=true
WEB_API_URL="https://tu-sitio-web.com/api/upload"
WEB_API_KEY="tu_api_key_secreta"

# --- Ajustes Avanzados (Tuning) ---
POLL_INTERVAL=5          # Segundos de espera entre chequeos
BATCH_SIZE=80            # Tamaño del lote para subida web (Roster)
STATS_BATCH_SIZE=80      # Tamaño del lote para subida web (Stats)
HTTP_TIMEOUT=120         # Tiempo de espera máximo para la API

```

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
* `slpp.py`: Librería para parsear tablas de Lua a Python.
* `credentials.json`: **Llave**. Tu acceso a Google Cloud (¡No subir a GitHub!).
* `.env`: **Configuración**. Variables de entorno privadas.

---

## ⚠️ Solución de Problemas

* **Error "Google Sheets API Error":** Verifica que el archivo `credentials.json` es correcto y que has compartido la hoja de cálculo con el *client_email* que aparece dentro del JSON.
* **Error "413 Payload Too Large":** El script se ajustará solo, pero si persiste, reduce el valor de `BATCH_SIZE` en tu archivo `.env`.
* **No detecta cambios:** Asegúrate de hacer `/reload` o salir del juego (WoW solo escribe en el archivo al recargar la interfaz o cerrar sesión).

---

## 📝 Créditos

Desarrollado para mantener la sincronización de la Guild al día.
**Versión actual:** v43.0 (Stable Release).
