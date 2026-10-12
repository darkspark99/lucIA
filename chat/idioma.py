"""Idioma de las ventanas del proyecto.

Solo affects a las ventanas y menus; la voz y lo que dice el asistente
siguen siendo cosa aparte (ver `VOICES` en ovos_user_config.py).

La clave de cada texto es el propio castellano. Asi, si falta una
traducion, se ve el castellano en vez de una clave rara, y el codigo se
lee igual que antes: t("Nueva conversacion").

El idioma "auto" sigue a la voz elegida, que es lo que espera el usuario:
si cambia de voz a otra lengua, las ventanas cambian con ella.
"""

import json
import os
import urllib.request

AUTOMATICO = "auto"

IDIOMAS = {
    "es": "Español",
    "en": "English",
    "ca": "Català",
}

# =====================================================================
#  AÑADIR UN IDIOMA NUEVO (sin tocar el codigo)
# =====================================================================
# 1) Copia la carpeta de plantillas y traducelas:
#        cp -r skill_asistente_linux/locale/es-es \
#              skill_asistente_linux/locale/<ovos>
#    siendo <ovos> el codigo OVOS del idioma (p. ej. fr-fr, de-de...).
#    Traduce el contenido de cada fichero .dialog.
# 2) Añade una entrada en ~/.local/share/ovos/idiomas.json, por ejemplo:
#        "fr": {
#            "nombre": "Français",     <- etiqueta del selector
#            "ovos":   "fr-fr",        <- carpeta locale y lang que se manda
#            "llm":    "français",     <- como debe llamarse el idioma al pedirselo al LLM
#            "voz":    "fr_FR-siwis-medium"
#        }
#    La voz puede ser el nombre de una voz piper (se descarga sola), o un
#    objeto con URL si es un modelo tuyo:
#        "voz": {"nombre": "mi_voz", "onnx": "https://.../mi_voz.onnx",
#                "json": "https://.../mi_voz.onnx.json"}
# 3) El selector de idioma y la voz TTS se actualizan solos al reiniciar
#    la ventana de configuracion.
# =====================================================================

_RUTA_IDIOMAS = os.path.join(os.path.expanduser("~"), ".local", "share",
                             "ovos", "idiomas.json")

# Tablas que se construyen a partir de idiomas.json (con estos valores
# por defecto si el fichero no existe o le falta algun idioma).
_CODIGOS_OVOS = {"es": "es-es", "en": "en-us", "ca": "ca-es"}
_VOCES = {"es": "es_MX-claude-high", "en": "en_US-amy-medium", "ca": ""}
_NOMBRES_LLM = {"es": "español", "en": "English", "ca": "català"}
_VOCES_URL = {}   # cod -> {"nombre", "onnx", "json"} para voces por URL


def _carga_idiomas():
    try:
        with open(_RUTA_IDIOMAS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _aplica_idiomas(datos):
    """Vuelca idiomas.json sobre las tablas de arriba (extiende/override)."""
    for cod, d in (datos or {}).items():
        if not isinstance(d, dict):
            continue
        cod = str(cod).strip().lower()
        if not cod:
            continue
        if d.get("nombre"):
            IDIOMAS[cod] = d["nombre"]
        if d.get("ovos"):
            _CODIGOS_OVOS[cod] = d["ovos"]
        if d.get("llm"):
            _NOMBRES_LLM[cod] = d["llm"]
        v = d.get("voz")
        if isinstance(v, str):
            _VOCES[cod] = v
        elif isinstance(v, dict):
            nombre = v.get("nombre") or ""
            _VOCES[cod] = nombre
            if nombre and v.get("onnx"):
                _VOCES_URL[cod] = {
                    "nombre": nombre,
                    "onnx": v["onnx"],
                    "json": v.get("json") or (v["onnx"] + ".json"),
                }


_aplica_idiomas(_carga_idiomas())

CODIGOS_OVOS = _CODIGOS_OVOS
VOCES_TTS = _VOCES
VOCES_URL = _VOCES_URL
NOMBRES_LLM = _NOMBRES_LLM

# Castellano: la clave es el texto, asi que no hace falta tabla propia.
TRAD = {
    "en": {
        # --- ventanas de configuracion ---
        "Configuración de OVOS Asistente": "OVOS Assistant settings",
        "Configuración": "Settings",
        "Asistente": "Assistant",
        "Nombre del asistente:": "Assistant name:",
        "p. ej. lucIA, SIA, marIA...": "e.g. lucIA, SIA, marIA...",
        "Con este nombre te saluda la ventana de chat y se presenta el "
        "asistente.": "The chat window greets you with this name, and the "
                      "assistant introduces itself with it.",
        "Modelo de lenguaje": "Language model",
        "Modelos locales": "Local models",
        "Modelo instalado:": "Installed model:",
        "Actualizar lista": "Refresh list",
        "Instalar otro:": "Install another:",
        "Ver pasos y espacio": "Show steps and disk space",
        "Instalar el modelo seleccionado…": "Install the selected model…",
        "Modelo remoto (API)": "Remote model (API)",
        "Proveedor:": "Provider:",
        "URL (opcional):": "URL (optional):",
        "solo para «otro»: https://mi-servidor/v1/chat/completions":
            "for “other” only: https://my-server/v1/chat/completions",
        "Clave de API:": "API key:",
        "pega aqui tu clave de API": "paste your API key here",
        "Modelo:": "Model:",
        "identificador exacto del modelo, p. ej. claude-sonnet-5":
            "exact model identifier, e.g. claude-sonnet-5",
        "Ver modelos de mi cuenta": "List models on my account",
        "Probar conexión": "Test connection",
        "Voz del asistente": "Assistant voice",
        "Voz:": "Voice:",
        "p. ej. es_EU-female-medium": "e.g. en_US-amy-medium",
        "La voz elegida se descarga automáticamente si no está instalada. "
        "El audio se reinicia al guardar.":
            "The chosen voice is downloaded automatically if not installed. "
            "Audio restarts when you save.",
        "Cambiar voz": "Change voice",
        "Apariencia del widget (waveform)": "Widget appearance (waveform)",
        "Diseño:": "Design:",
        "Color del waveform:": "Waveform colour:",
        "Opacidad:": "Opacity:",
        "Colores de estado:": "State colours:",
        "Guardar": "Save",
        "Cancelar": "Cancel",
        "Idioma de las ventanas:": "Window language:",
        "Idioma automatico (sigue a la voz)": "Automatic (follows the voice)",
        # --- mensajes ---
        "Instalar modelo local": "Install local model",
        "Instalar modelo": "Install model",
        "Escribe primero la clave de API.": "Enter the API key first.",
        "Escribe el identificador del modelo.": "Enter the model identifier.",
        "Ver modelos": "List models",
        "Haz clic en un identificador de la lista para copiarlo en el campo "
        "de modelo.": "Click an identifier in the list to copy it into the "
                       "model field.",
        "(no se encuentra ningún ollama)": "(ollama not found)",
        "(no se ha encontrado ningún modelo)": "(no models found)",
        "Has elegido un modelo remoto pero no has escrito qué modelo es."
        "\n\nO escribes el identificador, o vuelves a «Local».":
            "You picked a remote model but did not say which one."
            "\n\nEither enter the identifier, or switch back to “Local”.",
        "Falta la clave de API para ese proveedor.":
            "That provider needs an API key.",
    },
    "ca": {
        "Configuración de OVOS Asistente": "Configuració de l'OVOS Assistent",
        "Configuración": "Configuració",
        "Asistente": "Assistent",
        "Nombre del asistente:": "Nom de l'assistent:",
        "p. ej. lucIA, SIA, marIA...": "p. ex. lucIA, SIA, marIA...",
        "Con este nombre te saluda la ventana de chat y se presenta el "
        "asistente.": "Amb aquest nom et saluda la finestra de xat i es "
                      "presenta l'assistent.",
        "Modelo de lenguaje": "Model de llengua",
        "Modelos locales": "Models locals",
        "Modelo instalado:": "Model instal·lat:",
        "Actualizar lista": "Actualitza la llista",
        "Instalar otro:": "Instal·la'n un altre:",
        "Ver pasos y espacio": "Mostra els passos i l'espai",
        "Instalar el modelo seleccionado…": "Instal·la el model seleccionat…",
        "Modelo remoto (API)": "Model remot (API)",
        "Proveedor:": "Proveïdor:",
        "URL (opcional):": "URL (opcional):",
        "solo para «otro»: https://mi-servidor/v1/chat/completions":
            "només per a «altre»: https://el-meu-servidor/v1/chat/completions",
        "Clave de API:": "Clau de l'API:",
        "pega aqui tu clave de API": "enganxa aquí la teva clau de l'API",
        "Modelo:": "Model:",
        "identificador exacto del modelo, p. ej. claude-sonnet-5":
            "identificador exacte del model, p. ex. claude-sonnet-5",
        "Ver modelos de mi cuenta": "Mostra els models del meu compte",
        "Probar conexión": "Prova la connexió",
        "Voz del asistente": "Veu de l'assistent",
        "Voz:": "Veu:",
        "p. ej. es_EU-female-medium": "p. ex. ca_ES-upc_ona-medium",
        "La voz elegida se descarga automáticamente si no está instalada. "
        "El audio se reinicia al guardar.":
            "La veu triada es baixa automàticament si no està instal·lada. "
            "L'àudio es reinicia en desar.",
        "Cambiar voz": "Canvia la veu",
        "Apariencia del widget (waveform)": "Aparença del gidget (waveform)",
        "Diseño:": "Disseny:",
        "Color del waveform:": "Color del waveform:",
        "Opacidad:": "Opacitat:",
        "Colores de estado:": "Colors d'estat:",
        "Guardar": "Desa",
        "Cancelar": "Cancel·la",
        "Idioma de las ventanas:": "Idioma de les finestres:",
        "Idioma automatico (sigue a la voz)":
            "Idioma automàtic (segueix la veu)",
        "Instalar modelo local": "Instal·la el model local",
        "Instalar modelo": "Instal·la el model",
        "Escribe primero la clave de API.":
            "Escriu primer la clau de l'API.",
        "Escribe el identificador del modelo.":
            "Escriu l'identificador del model.",
        "Ver modelos": "Mostra els models",
        "Haz clic en un identificador de la lista para copiarlo en el campo "
        "de modelo.": "Fes clic en un identificador de la llista per "
                       "copiar-lo al camp de model.",
        "(no se encuentra ningún ollama)": "(no s'ha trobat cap ollama)",
        "(no se ha encontrado ningún modelo)":
            "(no s'ha trobat cap model)",
        "Has elegido un modelo remoto pero no has escrito qué modelo es."
        "\n\nO escribes el identificador, o vuelves a «Local».":
            "Has triat un model remot però no has dit quin model és."
            "\n\nO escrius l'identificador, o tornes a «Local».",
        "Falta la clave de API para ese proveedor.":
            "Aquest proveïdor necessita una clau de l'API.",
    },
}

# Textos de la ventana de chat: van aparte porque el modulo es comun.
TRAD_CHAT = {
    "en": {
        "OVOS - Chat": "OVOS - Chat",
        "Nueva": "New",
        "Empezar una conversación en blanco (Ctrl+N)":
            "Start a blank conversation (Ctrl+N)",
        "Historial ▾": "History ▾",
        "Conversaciones guardadas (Ctrl+H). Clic derecho aquí para la lista.":
            "Saved conversations (Ctrl+H). Right-click here for the list.",
        "Escribe tu pregunta y pulsa Enter...":
            "Type your question and press Enter...",
        "Enviar": "Send",
        "Responde a la pregunta del asistente...":
            "Answer the assistant's question...",
        "Conversaciones guardadas": "Saved conversations",
        "(todavia no hay ninguna)": "(there are none yet)",
        "Abrir conversación…": "Open conversation…",
        "Renombrar esta…": "Rename this one…",
        "Nueva conversación": "New conversation",
        "Borrar la conversación actual": "Delete the current conversation",
        "Borrar la seleccionada": "Delete the selected one",
        "Borrar conversación": "Delete conversation",
        "Conversación en curso": "Conversation in progress",
        "El asistente te está esperando una respuesta.\n\nSi empiezas otra "
        "conversación, esta se queda guardada pero la respuesta se enviará a "
        "la nueva.\n¿Seguir?":
            "The assistant is waiting for an answer.\n\nIf you start another "
            "conversation, this one stays saved but the answer will go to the "
            "new one.\nContinue?",
        "¿Renombrar a qué?": "Rename to what?",
        "¿Seguro que quieres borrar esta conversación?":
            "Are you sure you want to delete this conversation?",
        "¿Seguro que quieres borrar esta conversación y todas sus "
        "conversaciones guardadas?":
            "Are you sure you want to delete this conversation and all your "
            "saved conversations?",
        "Elige una conversación": "Choose a conversation",
        "Abrir": "Open",
        "Renombrar": "Rename",
        "Borrar": "Delete",
        "No hay conversaciones guardadas": "No saved conversations",
        "¿Cuántos mensajes?": "How many messages?",
    },
    "ca": {
        "Nueva": "Nova",
        "Empezar una conversación en blanco (Ctrl+N)":
            "Comença una conversa en blanc (Ctrl+N)",
        "Historial ▾": "Historial ▾",
        "Conversaciones guardadas (Ctrl+H). Clic derecho aquí para la lista.":
            "Converses desades (Ctrl+H). Clic dret aquí per a la llista.",
        "Escribe tu pregunta y pulsa Enter...":
            "Escriu la teva pregunta i premu Enter...",
        "Enviar": "Envia",
        "Responde a la pregunta del asistente...":
            "Resposta la pregunta de l'assistent...",
        "Conversaciones guardadas": "Converses desades",
        "(todavia no hay ninguna)": "(encara no n'hi ha cap)",
        "Abrir conversación…": "Obre la conversa…",
        "Renombrar esta…": "Reanomena aquesta…",
        "Nueva conversación": "Conversa nova",
        "Borrar la conversación actual": "Esborra la conversa actual",
        "Borrar la seleccionada": "Esborra la seleccionada",
        "Borrar conversación": "Esborra la conversa",
        "Conversación en curso": "Conversa en curs",
        "El asistente te está esperando una respuesta.\n\nSi empiezas otra "
        "conversación, esta se queda guardada pero la respuesta se enviará a "
        "la nueva.\n¿Seguir?":
            "L'assistent està esperant una resposta.\n\nSi comences una altra "
            "conversa, aquesta es queda desada però la resposta anirà a la "
            "nova.\nContinuar?",
        "¿Renombrar a qué?": "Amb quin nom l'hem de reanomenar?",
        "¿Seguro que quieres borrar esta conversación?":
            "Segur que vols esborrar aquesta conversa?",
        "¿Seguro que quieres borrar esta conversación y todas sus "
        "conversaciones guardadas?":
            "Segur que vols esborrar aquesta conversa i totes les teves "
            "converses desades?",
        "Elige una conversación": "Tria una conversa",
        "Abrir": "Obre",
        "Renombrar": "Reanomena",
        "Borrar": "Esborra",
        "No hay conversaciones guardadas": "No hi ha converses desades",
        "¿Cuántos mensajes?": "Quants missatges?",
    },
}


def _normaliza(cod):
    """'es-ES', 'ES', 'es' -> 'es'. Desconocidos -> None."""
    if not cod:
        return None
    cod = str(cod).strip().lower().replace("_", "-")
    base = cod.split("-")[0]
    return base if base in IDIOMAS else None


def desde_voz(lang_voz):
    """Idioma de una voz a partir de su codigo ('es-ES' -> 'es')."""
    return _normaliza(lang_voz) or "es"


def activa(idioma):
    """Fija el idioma de las ventanas. Silencioso si no lo conhece."""
    global _ACTIVO
    _ACTIVO = _normaliza(idioma) or _ACTIVO
    return _ACTIVO


def idioma_activo():
    return _ACTIVO


def codigo_ovos():
    """Codigo de idioma que entiende OVOS ('es-es','en-us','ca-es').

    Se manda en el mensaje del chat para que el skill conteste con las
    plantillas de ese idioma, independientemente del idioma de la voz."""
    return CODIGOS_OVOS.get(_ACTIVO, "es-es")


RUTA_MYCROFT = os.path.join(os.path.expanduser("~"), ".config", "mycroft",
                            "mycroft.conf")

# Modulo TTS. El plugin 'piper-multi' elige la voz segun el idioma del
# mensaje (a partir de idiomas.json); el de piper se mantiene por
# compatibilidad de configuracion.
MODULO_TTS = "ovos-tts-plugin-piper-multi"
MODULO_PIPER = "ovos-tts-plugin-piper"


def _idioma_core():
    """Codigo base del idioma principal de OVOS (de mycroft.conf)."""
    try:
        with open(RUTA_MYCROFT, encoding="utf-8") as f:
            lang = (json.load(f) or {}).get("lang") or "es"
    except Exception:
        lang = "es"
    return str(lang).strip().split("-")[0].lower()


def codigos_secundarios():
    """Codigos OVOS de los idiomas de idiomas.json distintos del principal.

    Es lo que hay que poner en 'secondary_langs' de mycroft.conf para que
    padatious entrene (y reconozca) esos idiomas. Añadir un idioma a
    idiomas.json basta para que aparezca aqui, sin tocar codigo."""
    core = _idioma_core()
    salida = []
    for d in (_carga_idiomas() or {}).values():
        if not isinstance(d, dict) or not d.get("ovos"):
            continue
        cod = str(d["ovos"])
        if cod.split("-")[0].lower() == core:
            continue
        if cod not in salida:
            salida.append(cod)
    return salida


def _dir_voces():
    return os.path.join(os.path.expanduser("~"), ".local", "share", "piper_tts")


def voz_instalada(voz):
    """True si el modelo piper de esa voz ya esta en disco."""
    if not voz:
        return False
    return os.path.exists(os.path.join(_dir_voces(), voz + ".onnx"))


def voz_tts_actual():
    """Voz configurada ahora mismo en mycroft.conf."""
    try:
        with open(RUTA_MYCROFT, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    tts = cfg.get("tts", {}) or {}
    for clave in (MODULO_TTS, MODULO_PIPER):
        voz = (tts.get(clave) or {}).get("voice")
        if voz:
            return voz
    return None


def _baja_url(url, destino):
    with urllib.request.urlopen(url, timeout=300) as r, \
            open(destino, "wb") as f:
        while True:
            trozo = r.read(1 << 16)
            if not trozo:
                break
            f.write(trozo)


def _descargar_voz(voz, codigo=None):
    """Baja el modelo de esa voz. Acepta nombre piper o voz por URL."""
    codigo = codigo or _ACTIVO
    spec = VOCES_URL.get(codigo)
    if spec and spec.get("nombre") == voz:
        # Voz propia alojada en una URL: se guarda con el nombre esperado.
        onnx = os.path.join(_dir_voces(), voz + ".onnx")
        cfgj = os.path.join(_dir_voces(), voz + ".onnx.json")
        try:
            os.makedirs(_dir_voces(), exist_ok=True)
            if not os.path.exists(onnx):
                _baja_url(spec["onnx"], onnx)
            if not os.path.exists(cfgj):
                _baja_url(spec["json"], cfgj)
            return True
        except Exception:
            return False
    # Voz del catalogo piper: la descarga el propio plugin.
    try:
        from ovos_tts_plugin_piper.voice_models import get_voice_files
        get_voice_files(voz)
        return True
    except Exception:
        return False


def voz_para(codigo=None):
    return VOCES_TTS.get(codigo or _ACTIVO, "")


def nombre_llm(codigo=None):
    """Como se llama el idioma al pedirselo al LLM (p. ej. 'français')."""
    return NOMBRES_LLM.get(codigo or _ACTIVO, "")


def idioma_de_voz(voz):
    """Codigo de idioma ('es','en','ca') de una voz, o None."""
    return _normaliza(voz)


def descargar_voz(voz, codigo=None):
    """Baja la voz si falta. True si queda disponible."""
    if voz_instalada(voz):
        return True
    return _descargar_voz(voz, codigo)


def asegurar_voz_tts(codigo=None, descargar=True):
    """Ajusta la voz del TTS al idioma indicado (o al activo).

    Si el modelo no esta, intenta descargarlo. Devuelve
    (cambiada, mensaje) para poder avisar en la interfaz.
    """
    codigo = codigo or _ACTIVO
    voz = voz_para(codigo)
    if not voz:
        return False, "No hay voz TTS definida para '%s'." % codigo
    if not voz_instalada(voz) and descargar:
        if not _descargar_voz(voz, codigo):
            return False, ("La voz '%s' no esta instalada y no se pudo "
                           "descargar." % voz)
    spec = VOCES_URL.get(codigo)
    modelo = os.path.join(_dir_voces(), voz + ".onnx") if spec else None
    modelo_cfg = os.path.join(_dir_voces(), voz + ".onnx.json") if spec else None
    try:
        with open(RUTA_MYCROFT, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    tts = cfg.setdefault("tts", {})
    tts["module"] = MODULO_TTS
    piper = tts.setdefault(MODULO_PIPER, {})
    multi = tts.setdefault(MODULO_TTS, {})
    cambiada = multi.get("voice") != voz or \
        multi.get("model") != modelo
    for destino in (piper, multi):
        destino["voice"] = voz
        if modelo:
            destino["model"] = modelo
            destino["model_config"] = modelo_cfg
        else:
            destino.pop("model", None)
            destino.pop("model_config", None)
    try:
        carpeta = os.path.dirname(RUTA_MYCROFT)
        if carpeta and not os.path.isdir(carpeta):
            os.makedirs(carpeta, exist_ok=True)
        with open(RUTA_MYCROFT, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception as e:
        return False, "No se pudo escribir la voz: %s" % e
    return cambiada, "Voz cambiada a '%s'." % voz


def t(texto, **kwargs):
    """Traduce un texto al idioma activo.

    Si no hay traduccion, devuelve el castellano original, que para eso
    es la clave. Acepta kwargs para los textos con {algo} dentro.
    """
    cod = _ACTIVO
    if cod != "es":
        destino = TRAD_CHAT.get(cod, {})
        if texto in destino:
            salida = destino[texto]
            return salida.format(**kwargs) if kwargs else salida
        destino = TRAD.get(cod, {})
        if texto in destino:
            salida = destino[texto]
            return salida.format(**kwargs) if kwargs else salida
    return texto.format(**kwargs) if kwargs else texto


def falta_alguna(idioma):
    """Textos de es/en/ca que aun no estan traducidos. Para el test."""
    cod = _normaliza(idioma)
    if cod is None or cod == "es":
        return []
    faltan = []
    for grupo in (TRAD, TRAD_CHAT):
        for es, destino in grupo.get(cod, {}).items():
            if destino.strip() == es.strip():
                faltan.append(es)
    return faltan


def _carga_desde_config(ruta):
    """Lee gui.idioma de la configuracion, si la hay."""
    try:
        with open(ruta, encoding="utf-8") as f:
            cfg = json.load(f)
        return (cfg.get("gui") or {}).get("idioma") or AUTOMATICO
    except Exception:
        return AUTOMATICO


def resuelve(ruta_config, lang_voz=None):
    """Idioma que toca: el de la configuracion, o el de la voz si es auto."""
    choix = _carga_desde_config(ruta_config)
    if choix == AUTOMATICO:
        return activa(desde_voz(lang_voz))
    return activa(choix)


_ACTIVO = "es"