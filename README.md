# lucIA — Asistente de Linux por voz (skill OVOS)

Un asistente por voz **especializado en GNU/Linux**, pensado para personas
que empiezan o para quien prefiere hablar en vez de teclear. Interactúa con
el sistema por lenguaje natural: busca y organiza archivos, instala y
desinstala programas, recomienda software, levanta servicios, enseña los
comandos, redacta textos dictados, prepara emails y consulta internet,
guíandote por voz en cada paso.

Funciona en **cualquier distro** (openSUSE, Debian/Ubuntu, Fedora, Arch:
detecta zypper/apt/dnf/pacman automáticamente) y trae un **widget de
escritorio KDE Plasma** con la onda de voz, la transcripción y el estado,
**personalizable** (colores, diseño del waveform y voz).

Se apoya en OVOS + un LLM local (Ollama con `llama3.1:8b`) mediante
*function calling*: el modelo propone la acción y el skill la ejecuta con
seguridad (confirmación hablada, `pkexec` para privilegios).

Es **multilingüe**: de fábrica responde y habla en **Español e Inglés**
(el idioma que elijas) tanto por voz como por texto, y la **voz del TTS
cambia sola** para que suene en ese idioma. Añadir cualquier otro idioma es
solo cuestión de datos, sin tocar el código (ver
[Idiomas y voz](#idiomas-y-voz-multilingüe)).

---

## Habilidades

### Archivos
- Buscar por nombre o tipo/extensión: `busca los pdf en Documentos`,
  `dónde está tareas.txt`.
- Listar carpetas, ver procesos, mover/copiar/renombrar.

### Software
- **Instalación inteligente en cascada**, sin menús:
  1. Repositorios configurados → instala directo (con confirmación).
  2. Repositorio de la comunidad (Packman en openSUSE) si no existe ahí.
  3. Flatpak → Docker → código fuente, con confirmación en cada paso.
  4. Programas de Windows/comerciales (*photoshop*, *maya*, *office*...):
     ofrece el **equivalente libre** (GIMP, Blender, LibreOffice...) o
     **emular con Wine/Proton**.
- Desinstalación segura: `desinstala vlc`.
- Recomendación por término: `¿qué programas me recomiendas para editar
  fotos?`.

### Servicios
- Levantar y guiar la configuración de servidor web (apache/nginx),
  MariaDB/SQL, PHP, FTP y SSH, con verificación final:
  `levanta un servidor http`, `activa mariadb`, `¿está funcionando ssh?`.

### Redacción de textos dictados
- `escríbeme una carta de renuncia por mudanza`,
  `redacta un email de reclamación`, `una nota para mi vecino`.
- Se pregunta a quién va dirigida y detalles opcionales, se escribe el
  borrador completo y se abre en tu editor (`kate` por defecto,
  configurable), con copia en el portapapeles.

### Email
- `envía un email a alguien@correo.com con asunto hola y texto que cuento`.
- Sin configuración: abre tu cliente de correo con todo pre-rellenado
  (`mailto:`). Con `email.smtp_*` en `settings.json`: envía directo por SMTP.

### Internet
- `abre youtube` (y google, gmail, wikipedia, netflix, facebook, instagram,
  spotify… o una URL directa).
- `busca en internet recetas de cocina`.
- `qué dice la wikipedia sobre linux` (consulta la página y resume su
  contenido).

### Aprende con usted
- `¿qué hace grep?`, `¿cómo borro un archivo?`, `explícame el comando find`.
- Base de conocimiento local + `man`/`--help` resumido por el LLM.

### Ventanas, mouse y teclado (KDE)
- Minimizar/maximizar/cerrar y cambiar entre ventanas.
- **Ver la pantalla** (captura + modelo de visión): `describe lo que se ve en
  la pantalla`, `¿qué opinas de esta imagen?`, `¿qué obra es?`. Si reconoce un
  lugar u obra, encadena una búsqueda en Wikipedia.
- **Puntero o clic por visión**: `pon el cursor en la barra de búsqueda`,
  `haz clic en Iniciar sesión`, `haz doble clic en …`, `clic derecho en …`. Se
  hace una captura, el modelo de visión localiza el elemento y `xdotool` mueve
  el cursor (o hace clic) en sus coordenadas reales.
- Dictar texto, presionar Enter, borrar lo último.
- Leer el texto seleccionado en pantalla.

### Extras
- Cálculos, traducciones, hora, fecha, clima y preguntas de cultura general
  respondidas por el LLM.
- **Demo de habilidades**: pregunta `¿qué puedes hacer?` (resumen) o
  `haz una demo` (demostración en vivo paso a paso).

---

## Métodos de entrada

| Método | Cómo se activa | Qué hace el asistente |
|---|---|---|
| **Voz (PTT)** | Mantén pulsado **Ctrl+Alt+Num-**, habla, suelta | Escucha mientras lo pulsas y **corta al soltar** (`recognizer_loop:record_stop`); responde hablando en la sesión `default` |
| **Chat de texto** | Systray → *"Escribir pregunta..."* | Ventana persistente que responde **en texto y hablando a la vez**; usa la sesión `chat` (nunca pide el micrófono) |
| **Systray** | Icono OVOS en la bandeja | Menú: Escribir pregunta…, Configuración…, Reiniciar OVOS, Salir; clic = parar la voz |
| **Widget de Plasma** | Añade el widget "OVOS" al escritorio | Muestra la onda de voz, la transcripción y el estado; clic no bloquea el micro |
| **Demo por voz** | `¿qué puedes hacer?` / `haz una demo` | Resumen hablado, o demo en vivo con ejemplos |

Los dos canales están **separados de verdad**: la ventana de chat usa su
propia sesión `chat` — sus preguntas se responden escribiendo en ella, y
aunque alguien hable en la habitación el micrófono no se activa ni
interfiere. El habla del chat se espeja a la sesión `default` (única que
`ovos-audio` reproduce), de ahí que responda por voz y texto a la vez.

La ventana de chat permanece abierta hasta que la cierres (X) o escribas
**"cierra la ventana de chat"** / **"cierra el chat"** (también por voz).
No hay wake-word: el micrófono solo se arma bajo demanda.

---

## Instalación

### Requisitos
- OVOS (ovos-core) en la misma máquina, p. ej. OpenVoiceOS Desktop.
- `ollama` con `llama3.1:8b` sirviendo en `127.0.0.1:11435`
  (configurable en `settings.json` del skill).
- KDE Plasma (opcional, para el widget y la ventana de chat).
- `pkexec`/polkit y un terminal (`konsole` por defecto, ajustable).
- Para **ver la pantalla y controlar el puntero por visión**: `xdotool` y una
  utilidad de captura (`spectacle`, `gnome-screenshot` o `import`) + un modelo
  de visión en Ollama (`qwen2.5vl:7b`).
- Python con las dependencias de OVOS. Si usas el venv estándar
  (`~/.venvs/ovos/`), `install.sh` lo detecta y lo usa en los servicios;
  si no, cae en el `python3` del sistema.

### Instalador completo
```bash
git clone https://github.com/darkspark99/lucIA.git
cd lucIA
./packaging/install.sh
systemctl --user restart ovos.service
```

`install.sh` hace todo esto:
1. Instala el skill como plugin de OVOS (`pip install .`).
2. Instala el **widget de Plasma** (onda de voz) + su puente systemd.
3. Copia la **ventana de chat** y el **systray**.
4. Copia el **módulo y diálogo de configuración** (voz y apariencia).
5. Copia el **push-to-talk** e imprime cómo enlazarlo a un atajo.
6. Copia los **scripts de arranque** (`ovos-wrapper.sh`, etc.) e instala y
   activa los **servicios systemd de usuario** (systray, PTT, entorno).
7. Al final te ofrece abrir la **Configuración…** (voz y apariencia).
8. (Opcional) instala el skill complementario `skill-demostracion` si
   quieres la demo de habilidades en vivo.

> Solo el skill (plugin OVOS): `pip install . --break-system-packages`.

> Los servicios se instalan con rutas portables (`%h`), así que valen para
> cualquier usuario. El intérprete se sustituye por el que detecte
> `install.sh` (venv de OVOS o `python3`).

### Primeros pasos tras instalar
```bash
# Arranca el asistente y comprueba que todo responde
systemctl --user restart ovos.service
curl http://127.0.0.1:8765/state && curl http://127.0.0.1:8765/config
```
- Pulsa **Ctrl+Alt+Num-** (mantén pulsado) y di *"qué programas me
  recomiendas para editar fotos"*.
- O abre el chat desde el systray y escribe *"instala vlc"*.
- Añade el widget "OVOS" al escritorio para ver la onda de voz.
- Desde el systray → **Configuración…** cambia la voz (hombre/mujer/idioma)
  y el diseño/colores del waveform.

---

## Configuración y personalización

Hay dos niveles: el **diálogo de configuración** (la vía recomendada) y los
**archivos de configuración** (para ajuste fino).

### Diálogo de configuración (ovos_gui)
Se abre desde el **systray → Configuración…** o al final de `install.sh`.
Te permite cambiar, sin tocar archivos:

- **Voz del asistente (TTS piper)**: lista de voces en español de España,
  español de México, catalán e inglés (EE. UU. y R. U.), etiquetadas por
  **idioma y género** (*Claude femenina*, *Dave masculina*, *Ona femenina*,
  *Amy female*…). Si eliges una voz no instalada, **se descarga
  automáticamente**. También admite una voz piper personalizada escribiendo
  su nombre (`es_EU-female-medium`).
- **Idioma de las ventanas**: *Automático (sigue a la voz)* o un idioma
  concreto de los dados de alta (*Español*, *English*, …). Este ajuste decide
  **en qué idioma responde y habla el asistente** (diálogos, textos y voz),
  aunque le hables o le escribas en otro idioma. Al elegir un idioma
  concreto, si la voz actual no es de ese idioma, se cambia **sola** a la voz
  por defecto de ese idioma (y se descarga si hace falta). Ver
  [Idiomas y voz](#idiomas-y-voz-multilingüe).
- **Modelo de lenguaje**: elige entre **local** u **ollama, en este
  equipo**, o **remoto, una API de pago por internet**.
  - *Local*: desplegable con los modelos ya instalados (los dos pools se
    leen solos) y un instalador que **consulta el tamaño real en el
    registro de ollama**, te dice **cuánto ocupa y cuánto espacio libre
    tienes**, explica los pasos exactos y **pide confirmación** antes de
    descargar nada.
  - *Remoto*: proveedor (Claude, OpenAI, OpenRouter, Groq, DeepSeek,
    Gemini, Mistral, xAI o cualquier otro compatible con OpenAI), clave
    de API, identificador de modelo, botón **«Ver modelos de mi cuenta»**
    para listar los tuyos y botón **«Probar conexión»** que manda una
    llamada mínima y traduce el fallo si lo hay.
- **Apariencia del widget**:
  - **Diseño del waveform**: Barras (ecualizador), Círculos (puntos) o
    Línea (osciloscopio).
  - **Color del waveform** (selector de color).
  - **Opacidad** del waveform (20–100 %).
  - **Colores de estado**: Escuchando, Pensando, Hablando e Inactivo.

Al guardar con cambios de voz, el audio se reinicia solo y el widget
aplica el nuevo aspecto en segundos (si no, quita y añade el widget).

### Archivos de configuración
| Archivo | Contenido |
|---|---|
| `~/.config/ovos-asistente.json` | Apariencia del widget (`gui.waveform_color`, `gui.waveform_style`, `gui.waveform_opacity`, `gui.color_listening/thinking/speaking/idle`), `gui.idioma` (idioma de las ventanas), `tts.voice` guardada, `nombre_asistente` y `modelo.actual` |
| `~/.config/mycroft/mycroft.conf` | Config OVOS: `tts.module` (`ovos-tts-plugin-piper-multi`), `tts.ovos-tts-plugin-piper-multi.voice`, `secondary_langs` (idiomas extra que se reconocen), STT, unidades, etc. |
| `~/.local/share/ovos/idiomas.json` | Tabla de idiomas soportados (nombre, código OVOS, nombre para el LLM y voz). Es la **fuente única** para añadir idiomas. Ver [Idiomas y voz](#idiomas-y-voz-multilingüe) |
| `~/.config/mycroft/skills/lucia/settings.json` | Ajustes del skill (lo gestiona OVOS; normalmente solo el marcador de primera ejecución) |
| Nombre del asistente | `nombre_asistente` en `~/.config/ovos-asistente.json` (por defecto `lucIA`) |

### Idiomas y voz (multilingüe)

El asistente es **bilingüe de fábrica** (Español e Inglés) y ampliable a
cualquier idioma **sin tocar el código**. Todo lo que depende del idioma vive
en datos:

| Pieza | Dónde | Qué controla |
|---|---|---|
| `idiomas.json` | `~/.local/share/ovos/idiomas.json` | Tabla de idiomas: etiqueta, código OVOS, nombre para el LLM y voz TTS |
| Plantillas de diálogo | `skill_asistente_linux/locale/<ovos>/*.dialog` | Textos que pronuncia el asistente (120 por idioma) |
| Intents | `skill_asistente_linux/locale/<ovos>/intent/*.intent` | Frases que reconoce por voz/texto (puntero/visión, modelos, identidad) |
| `hints.json` | `skill_asistente_linux/hints.json` | Palabras clave por idioma (operaciones de sistema, seguimiento, cerrar chat); si falta un idioma, usa las del español |
| Plugin TTS | `skill_asistente_linux/tts_multi.py` | Elige la voz piper según el idioma de la respuesta |

Cómo funciona:

- El **idioma de las ventanas** (Configuración → *Idioma de las ventanas*)
  decide el idioma en que **responde y habla** el asistente. En *Automático*
  sigue a la voz elegida.
- El asistente responde **siempre en el idioma seleccionado**, aunque le
  hables o le escribas en otro. Si el idioma es *Automático*, responde en el
  idioma de entrada.
- La **voz cambia sola** por idioma gracias al plugin
  **`ovos-tts-plugin-piper-multi`**: lee el idioma de la respuesta y usa la
  voz declarada para ese idioma en `idiomas.json`. Así el texto y la voz
  coinciden (nada de leer inglés con fonética española).
- Los idiomas extra se añaden a **`secondary_langs`** de `mycroft.conf`
  automáticamente, para que **padatious** (el reconocedor de frases) entrene
  y entienda también esos idiomas.
- Los ajustes de idioma y voz van con **claves separadas**: las ventanas,
  los diálogos, el prompt del LLM y el TTS se actualizan a la vez al guardar.

> **Nota sobre el micrófono (STT):** el reconocimiento de voz usa un solo
> idioma a la vez (`stt.ovos-stt-plugin-fasterwhisper.language` en
> `mycroft.conf`, por defecto `es`). El **chat de texto** entiende cualquier
> idioma al instante; para dictar por voz en otro idioma, cambia ese valor
> (p. ej. a `en`) y reinicia `ovos-listener`.

#### Añadir un idioma nuevo

1. Copia y traduce las plantillas, usando el **código OVOS** del idioma
   (`fr-fr`, `de-de`, `pt-br`…):
   ```bash
   cd skill_asistente_linux/locale
   cp -r es-es fr-fr          # traduce cada *.dialog y cada intent/*.intent
   ```
2. Añade su entrada en `~/.local/share/ovos/idiomas.json`:
   ```json
   "fr": {
       "nombre": "Français",
       "ovos":   "fr-fr",
       "llm":    "français",
       "voz":    "fr_FR-siwis-medium"
   }
   ```
   - `voz` puede ser el **nombre de una voz piper** (se descarga sola) o un
     objeto con **URL** si es un modelo propio:
     ```json
     "voz": {"nombre": "mi_voz", "onnx": "https://…/mi_voz.onnx",
             "json": "https://…/mi_voz.onnx.json"}
     ```
3. Reinicia `ovos-core` (para que padatious entrene el idioma) y vuelve a
   abrir la ventana de configuración: el idioma y su voz aparecen en los
   desplegables.

No hace falta editar ningún `.py`: el selector, la voz, los diálogos,
`secondary_langs` y el prompt del LLM se generan a partir de `idiomas.json`.

### Historial de conversaciones

La ventana de chat **guarda cada conversación** en un fichero JSON propio
dentro de `~/.local/share/ovos/historial/` (uno por conversación, permisos
`600`). Al cerrar y volver a abrir la ventana, sigue donde lo dejaste.

Para verla hay tres caminos:

- El botón **«Historial ▾»** de la cabecera, que abre un menú con los
  **títulos** de las conversaciones guardadas.
- **Clic derecho** en el título de la ventana o en el propio botón: sale el
  mismo menú.
- **Ctrl+H** abre el atajo, y **Ctrl+N** empieza una conversación en blanco.

El menú trae, además de la lista: *Abrir conversación…* (una ventana con
todas, con previsualización del contenido), *Renombrar esta…*,
*Nueva conversación* y *Borrar la conversación actual*.

Detalles:

- El **título es automático**: se coge lo primero que escribiste, recortado.
  Si lo cambias a mano, ya no se sobrescribe.
- Cada entrada muestra también **cuándo** y **cuántos mensajes** tiene, y la
  actual va marcada con «•».
- Las conversaciones vacías se limpian solas al arrancar.
- Si el asistente te está esperando una respuesta y cambias de conversación,
  te avisa antes: la respuesta se enviará a la nueva.
- Solo se guarda lo que pasa por la ventana de chat (sesión `chat`), no lo
  que hablas por micrófono.
- Tope de 500 mensajes por conversación y 300 conversaciones; lo más viejo
  se va solo. Para borrar de verdad, desde el menú.

### Multimodelo

El modelo con el que responde el asistente se cambia **al instante**, sin
reiniciar nada. Se busca en los dos pools de Ollama del sistema (el del
usuario en `11435` y el del sistema en `11434`) y también se puede elegir en
**Configuración… → Modelo de lenguaje**.

Por voz:

| Dices | Qué hace |
|---|---|
| «qué modelos tienes» / «lista los modelos» | Enumera los instalados y marca el activo |
| «usa el modelo ministral» / «cambia el modelo a qwen coder» | Cambia al modelo indicado (entiende nombres coloquiales: «llama», «qwen coder», «ministral») |
| «quiero un modelo para programar» / «necesito un modelo para generar imágenes» | Recomienda: si el modelo local adecuado ya está instalado, ofrece activarlo; si no, ofrece descargarlo; y en ambos casos ofrece abrir la web del servicio |
| «instala qwen coder» | Si el modelo ya está, lo activa; si no, lo descarga con `ollama pull` (pregunta antes) |
| «instala claude code» / «instala ollama» | Instala el CLI correspondiente, o te dice que ya está instalado |

Detalles de comportamiento:

- **Nunca se queda a medias.** Al cambiar de modelo se prueba con una
  petición real; si el nuevo no responde, se vuelve al anterior y te avisa.
- Las descargas de modelos piden confirmación y van al pool del usuario
  (`11435`), así que no hacen falta privilegios.
- Los modelos de embeddings (`all-minilm`, `nomic-embed-text`, …) se ocultan
  de la lista porque no pueden conversar.
- La preferencia queda en `~/.config/ovos-asistente.json`, en `modelo.actual`
  (más `modelo.url` y `modelo.puerto`). Si borras esa clave, el skill vuelve
  al valor de `settings.json`.

#### Modelos remotos (API de pago)

Si eliges **Remoto** en **Configuración… → Modelo de lenguaje**, el asistente
deja de usar Ollama y pasa a hablar con una API por internet. Eso significa
que **cada respuesta se paga** y que **lo que oyes y lo que escribes sale de
ese servidor**.

| Proveedor | Endpoint | Notas |
|---|---|---|
| Claude (Anthropic) | `api.anthropic.com/v1/messages` | Formato propio: `x-api-key` + `anthropic-version` |
| OpenAI | `api.openai.com/v1` | **La suscripción de ChatGPT Plus no incluye la API**: hay que pagar aparte |
| OpenRouter | `openrouter.ai/api/v1` | Un mismo sitio, muchos modelos |
| Groq | `api.groq.com/openai/v1` | Muy rápido, capa gratuita |
| DeepSeek | `api.deepseek.com` | Muy barato |
| Gemini | `generativelanguage.googleapis.com/v1beta/openai` | Compatible con el formato de OpenAI |
| Mistral | `api.mistral.ai/v1` | Modelo europeo |
| xAI (Grok) | `api.x.ai/v1` | |
| Otro compatible con OpenAI | la que escribas | Para proxies, LM Studio en red, vLLM… |

Cómo se comporta:

- **La clave va aparte**, en `~/.config/ovos-claves.json` con permisos `600`,
  nunca dentro de la configuración normal y nunca en el log.
- **Se traduce el formato**: el modelo interno habla con todos igual; por
  dentro se quitan los campos que solo existen para Ollama (`stream`,
  `keep_alive`) y en Anthropic el *system* se mueve a su campo y los
  `tools` se convierten a `input_schema`.
- **A Claude no se le manda `temperature`**: los modelos recientes lo
  rechazan con error 400.
- **Los identificadores no están escritos en el código**: se consultan con
  «Ver modelos de mi cuenta», que lista los que tu clave puede usar.
- **Probar conexión** manda la llamada mínima y enseña el error tal cual
  (clave caducada, cuota agotada, modelo mal escrito…).
- **Por voz no se cambia a un remoto**, porque lleva clave y hay que confirmar
  el gasto. Lo que sí funciona es volver a uno local: si estás en remoto y
  dices «usa el modelo llama», sales del remoto.
- La preferencia queda en `modelo.tipo = "remoto"`, `modelo.proveedor`,
  `modelo.remoto` y `modelo.url` (solo para «Otro»).

> **Aviso**: mientras haya un remoto configurado, revisa la facturación del
> proveedor aunque no lo uses, y quita la clave en
> `~/.config/ovos-claves.json` si dejas de pagar por ello.
- Los modelos especializados que se descargan con `ollama pull` y las webs
  de los servicios (Claude, ChatGPT, Cursor, Leonardo AI…) son tablas
  internas del módulo: `SERVICIOS_IA`, `CLI_IA`, `MODELOS_INSTALABLES` y
  `ESPECIALIDADES`.

#### Ajustes del skill (`settings.json`)
| Clave | Por defecto | Descripción |
|---|---|---|
| `model` | `llama3.1:8b` | Modelo LLM |
| `ollama_url` | `http://127.0.0.1:11435` | Endpoint de Ollama |
| `require_confirmation` | `true` | Confirmación hablada antes de modificar el sistema |
| `terminal` | `konsole` | Terminal para ejecutar comandos |
| `editor` | `kate` | Editor donde se abre un borrador |
| `email.smtp_host` / `smtp_port` / `smtp_user` / `smtp_password` / `remitente` | — | Para envío directo de emails (sin ellos se usa `mailto:`) |

### Voces disponibles (piper)
| Voz | Idioma | Género |
|---|---|---|
| `es_MX-claude-high` | Español (México) | Mujer (por defecto) |
| `es_MX-ald-medium` / `es_MX-ald-x_low` | Español (México) | Mujer |
| `es_ES-davefx-medium` | Español (España) | Hombre |
| `es_ES-carlfm-x_low` | Español (España) | Hombre (ligera) |
| `es_ES-sharvard-medium` | Español (España) | Hombre |
| `es_ES-mls_10246-low` / `es_ES-mls_9972-low` | Español (España) | Mujer |
| `ca_ES-upc_ona-medium` | Catalán | Mujer |
| `ca_ES-upc_pau-x_low` | Catalán | Hombre |
| `en_US-amy-medium` | Inglés (EE. UU.) | Mujer |
| `en_US-lessac-medium` | Inglés (EE. UU.) | Hombre |
| `en_GB-alba-medium` | Inglés (R. U.) | Mujer |
| `en_GB-ryan-medium` | Inglés (R. U.) | Hombre |

---

## Uso por voz y por texto

```
"¿qué programas me recomiendas para editar fotos?"
"instala gimp" / "desinstala vlc" / "instala photoshop" (equiv. o Wine)
"busca los pdf en Documentos" / "dónde está tareas.txt"
"levanta un servidor http y guíame" / "activa mariadb" / "¿está funcionando ssh?"
"qué hace grep" / "cómo muevo un archivo con mv"
"escríbeme una carta de renuncia por mudanza"
"envía un email a prueba@gmail.com con asunto hola y texto que tal estas"
"abre youtube" / "busca en internet recetas de cocina" / "qué dice la wikipedia sobre linux"
"minimiza la ventana" / "mueve el mouse a la derecha" / "lee lo que tengo seleccionado"
"qué puedes hacer" / "haz una demo"
```

Todas funcionan igual por **voz** (PTT) y por **texto** (ventana de chat);
en el chat también se **pronuncian** las respuestas.

---

## Estructura

```
skill_asistente_linux/        # código del skill (plugin `ovos.plugin.skill`)
  ├── __init__.py             # lógica del asistente (intents, LLM, sistema)
  ├── hints.json              # palabras clave por idioma (detección de tareas)
  ├── tts_multi.py            # plugin TTS multilingüe (voz según idioma)
  └── locale/<ovos>/          # diálogos (*.dialog) e intents (intent/*.intent)
plasma/org.ovos.plasmoid      # widget KDE (onda + estado + bridge systemd)
chat/                         # ventana de chat persistente (systray) + idioma.py
systray/                      # icono de estado + menú (D-Bus StatusNotifier)
ovos_gui/                     # diálogo de configuración (voz + idioma + apariencia)
ptt/                          # push-to-talk (atajo para abrir el micro)
packaging/install.sh          # instalador multiplataforma
packaging/scripts/            # scripts de arranque (wrapper, env-import, start)
packaging/systemd/            # units del proyecto (systray, ptt, env-import)
pyproject.toml / MANIFEST.in  # empaquetado (entry points, datos de locale)
```

Datos de usuario (no empaquetados):
```
~/.local/share/ovos/idiomas.json   # tabla de idiomas (fuente única)
~/.config/ovos-asistente.json      # apariencia, idioma de ventanas, modelo activo
~/.config/mycroft/mycroft.conf     # TTS, STT, secondary_langs, unidades
```

Servicios systemd de usuario (los marcados con ▪ los instala `install.sh`):
```
▪ ovos-plasmoid-bridge.service # puente HTTP del widget (127.0.0.1:8765)
▪ ovos-status-indicator.service# icono de systray
▪ ovos-ptt.service            # push-to-talk
▪ ovos-env-import.service     # importa el entorno gráfico (DISPLAY/…) a systemd
  ovos.service                 # meta
  ovos-messagebus.service      # bus de mensajes
  ovos-core.service            # skills
  ovos-listener.service        # micrófono
  ovos-audio.service           # TTS/audio
  ovos-chat.service            # ventana de chat (se crea al abrirla)
```

---

## Seguridad (modelo de confianza)

- Lista negra de patrones destructivos (jamás se ejecutan, ni por el LLM).
- Confirmación hablada obligatoria antes de toda operación que modifique el
  sistema.
- Las operaciones privilegiadas usan `pkexec`/polkit: la contraseña se pide
  en el diálogo del sistema, nunca se oculta.
- Los `find`/argumentos propuestos por el LLM se normalizan
  (escapado/comillas/paréntesis) antes de ejecutar.
- El micrófono solo se activa bajo demanda (PTT): sin wake-word.

---

## Solución de problemas

| Síntoma | Qué hacer |
|---|---|
| No contesta | `systemctl --user restart ovos.service`; mira `systemctl --user status ovos-core.service` |
| El widget no se ve / sin audio | `systemctl --user restart ovos-plasmoid-bridge.service`, y `curl http://127.0.0.1:8765/state`. Si no aplica el aspecto, quita y añade el widget |
| Cambio de voz sin efecto | El audio se reinició solo al guardar; comprueba `~/.config/mycroft/mycroft.conf` → `tts.ovos-tts-plugin-piper-multi.voice` y que `tts.module` sea `ovos-tts-plugin-piper-multi` |
| Habla en el idioma equivocado | Revisa el *Idioma de las ventanas* en Configuración y que la voz de ese idioma exista en `~/.local/share/ovos/idiomas.json`; `systemctl --user restart ovos-audio` |
| No entiende por voz otro idioma | El STT usa un solo idioma: ajusta `stt.ovos-stt-plugin-fasterwhisper.language` en `mycroft.conf` y reinicia `ovos-listener` |
| No reconoce frases en un idioma nuevo | Comprueba que está en `idiomas.json`, que existe `locale/<ovos>/intent/` y reinicia `ovos-core` (padatious entrena al arrancar) |
| El micro no para al soltar | Revisa `ovos-ptt.service` y que el atajo ejecute `push_to_talk.py` |
| Todo lento / respuestas largas | Reduce el modelo en `settings.json` o sube el de Ollama |
| Quiero silencio en el chat | El chat habla por diseño; la voz se comparte con el canal de voz |

---

## Hoja de ruta

- Guiado por voz para **capacidades diferentes**: más redacciones y envíos,
  publicación en redes sociales.
- Ampliar la base de conocimiento de comandos y la guía de servicios.
- Instalación vía OVOS Marketplace / skill repo.

---

## Créditos y agradecimientos

Este proyecto es sobre todo **pegamento** entre grandes herramientas libres.
Todo el mérito de las piezas pesadas es de sus autores:

- **[OpenVoiceOS (OVOS)](https://openvoiceos.org/)** — la plataforma de
  asistente por voz sobre la que corre todo: `ovos-core`, `ovos-workshop`,
  `ovos-bus-client`, `ovos-plugin-manager`, `ovos-audio`, `ovos-listener`,
  `ovos-messagebus`, etc. (licencia Apache-2.0).
- **[Ollama](https://ollama.com/)** — servidor local de modelos de lenguaje.
- **[Piper](https://github.com/rhasspy/piper)** (rhasspy/piper) y sus
  **[voces](https://huggingface.co/rhasspy/piper-voices)** — síntesis de voz,
  a través de `ovos-tts-plugin-piper`; el fonemizador es
  **[espeak-ng](https://github.com/espeak-ng/espeak-ng)**.
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** (SYSTRAN)
  sobre **[Whisper](https://github.com/openai/whisper)** (OpenAI) — reconocimiento
  de voz, vía `ovos-stt-plugin-fasterwhisper`.
- **[Vosk](https://alphacephei.com/vosk/)** (Alpha Cephei) — detección de
  palabra clave, vía `ovos-ww-plugin-vosk`.
- **[Silero VAD](https://github.com/snakers4/silero-vad)** — detección de
  actividad de voz, vía `ovos-vad-plugin-silero`.
- **[KDE Plasma](https://kde.org/plasma-desktop/) y [Qt](https://www.qt.io/)**
  — el widget de escritorio y las ventanas (PyQt6 + QML).
- **[Python](https://www.python.org/)** y la librería `requests`.

### Modelos de lenguaje

El asistente puede usar modelos locales servidos por Ollama, p. ej.
**Llama 3.1** (Meta Platforms, *Llama 3.1 Community License*), **Qwen**
(Alibaba), **Ministral** (Mistral AI), etc., o remotos. **Cada modelo se
rige por su propia licencia**; consúltala antes de usarlo o redistribuirlo.

### Servicios y marcas de terceros

Claude (Anthropic), ChatGPT/OpenAI, OpenRouter, Groq, DeepSeek, Google
Gemini, Mistral AI, xAI/Grok, Cursor, Leonardo AI, etc. son **marcas de sus
respectivos titulares** y se mencionan solo como **destinos opcionales** de
configuración (APIs). Este proyecto no está afiliado ni respaldado por
ninguno de ellos.

> Las licencias de todas las dependencias de terceros se conservan en sus
> propios paquetes/repositorios. Si redistribuyes este proyecto, respeta
> esas licencias y añade la tuya para el código propio.

---

## Licencia

Este proyecto se distribuye bajo la **GNU General Public License v3.0**
(GPL-3.0). Puedes usarlo, estudiarlo, modificarlo y redistribuirlo siempre
que las obras derivadas se mantengan también bajo GPL-3.0.

El texto completo está en el archivo [`LICENSE`](LICENSE), en la raíz del
repositorio. Resumen oficial: <https://www.gnu.org/licenses/gpl-3.0.html>.

```
lucIA — Asistente de Linux por voz (skill OVOS)
Copyright (C) 2026 DarkSpark2099

Este programa es software libre: puedes redistribuirlo y/o modificarlo
bajo los términos de la GNU General Public License, versión 3, publicada
por la Free Software Foundation.

Este programa se distribuye con la esperanza de que sea útil, pero SIN
NINGUNA GARANTÍA; ni siquiera la garantía implícita de COMERCIABILIDAD o
IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulta la GNU General Public
License para más detalles.
```