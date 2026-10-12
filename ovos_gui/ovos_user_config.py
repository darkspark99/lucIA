#!/usr/bin/env python3
"""OVOS Asistente - Configuracion de usuario compartida (gui + tts).

Archivos:
  ~/.config/ovos-asistente.json   configuracion GUI del widget
  ~/.config/mycroft/mycroft.conf  configuracion de voz (TTS piper)
"""
import json
import os
import subprocess
import urllib.request

CONFIG_PATH = os.path.expanduser("~/.config/ovos-asistente.json")
MYCROFT_CONF_PATH = os.path.expanduser("~/.config/mycroft/mycroft.conf")

# Modulo TTS: 'piper-multi' elige la voz segun el idioma del mensaje; se
# mantiene tambien la clave de 'piper' por compatibilidad.
MODULO_TTS = "ovos-tts-plugin-piper-multi"
MODULO_PIPER = "ovos-tts-plugin-piper"

# Pools de ollama: el del usuario (sin sudo) y el del sistema.
OLLAMA_POOLS = (11435, 11434)
OLLAMA_LABEL = {11435: "usuario", 11434: "sistema"}

DEFAULT_CONFIG = {
    "gui": {
        "waveform_color": "#ff2200",
        "waveform_style": "barras",
        "waveform_opacity": 1.0,
        "color_listening": "#00cc44",
        "color_thinking": "#3399ff",
        "color_speaking": "#ff2200",
        "color_idle": "#666666",
    },
    "tts": {"voice": None},
    "modelo": {"actual": None, "url": None},
}

# (etiqueta, nombre piper, idioma, genero)
VOICES = [
    ("Español (México) - Claude - femenina", "es_MX-claude-high", "es-MX", "Mujer"),
    ("Español (México) - Ald - femenina", "es_MX-ald-medium", "es-MX", "Mujer"),
    ("Español (México) - Ald - femenina (ligera)", "es_MX-ald-x_low", "es-MX", "Mujer"),
    ("Español (España) - Dave - masculina", "es_ES-davefx-medium", "es-ES", "Hombre"),
    ("Español (España) - Carl - masculina (ligera)", "es_ES-carlfm-x_low", "es-ES", "Hombre"),
    ("Español (España) - Harv - masculina", "es_ES-sharvard-medium", "es-ES", "Hombre"),
    ("Español (España) - MLS 10246 - femenina", "es_ES-mls_10246-low", "es-ES", "Mujer"),
    ("Español (España) - MLS 9972 - femenina", "es_ES-mls_9972-low", "es-ES", "Mujer"),
    ("Catalán - Ona - femenina", "ca_ES-upc_ona-medium", "ca-ES", "Mujer"),
    ("Catalán - Pau - masculina", "ca_ES-upc_pau-x_low", "ca-ES", "Hombre"),
    ("English (US) - Amy - female", "en_US-amy-medium", "en-US", "Mujer"),
    ("English (US) - Lessac - male", "en_US-lessac-medium", "en-US", "Hombre"),
    ("English (UK) - Alba - female", "en_GB-alba-medium", "en-GB", "Mujer"),
    ("English (UK) - Ryan - male", "en_GB-ryan-medium", "en-GB", "Hombre"),
]

STYLES = {
    "barras": "Barras (ecualizador)",
    "circulos": "Círculos (puntos)",
    "linea": "Línea (osciloscopio)",
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in (data.get("gui") or {}).items():
            if k in cfg["gui"]:
                cfg["gui"][k] = v
        if isinstance(data.get("tts"), dict) and data["tts"].get("voice"):
            cfg["tts"]["voice"] = data["tts"]["voice"]
        if isinstance(data.get("modelo"), dict):
            cfg["modelo"] = dict(data["modelo"])
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return cfg


def _es_embedding(nombre):
    """Los modelos de embeddings no conversan."""
    n = (nombre or "").lower()
    return any(k in n for k in ("embed", "minilm", "nomic", "clip",
                                "bge-", "e5-", "rerank"))


def listar_modelos(timeout=4):
    """[(etiqueta, modelo, puerto)] de todos los pools de ollama.

    Se omiten los modelos de embeddings: no pueden conversar.
    """
    lista = []
    for puerto in OLLAMA_POOLS:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{puerto}/api/tags",
                    timeout=timeout) as r:
                datos = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        for m in (datos.get("models") or []):
            nombre = m.get("name") or ""
            if not nombre or _es_embedding(nombre):
                continue
            lista.append((f"{nombre}  ·  {OLLAMA_LABEL.get(puerto, puerto)}",
                          nombre, puerto))
    return lista


def modelo_actual(cfg=None):
    """Nombre del modelo de respuesta configurado (o None)."""
    if cfg is None:
        cfg = load_config()
    return (cfg.get("modelo") or {}).get("actual") or None


def set_modelo(cfg, nombre, puerto):
    """Deja el modelo local y su endpoint en la config (muta cfg)."""
    cfg["modelo"] = {
        "tipo": "local",
        "actual": nombre,
        "url": f"http://127.0.0.1:{puerto}/v1/chat/completions",
        "puerto": puerto,
    }
    return cfg


# --- Proveedores remotos -------------------------------------------------
# Copia intencionada de la tabla que hay en el skill: los dos ficheros se
# despliegan por separado y el GUI no deberia importar el modulo del skill
# (arrastraria ovos_workshop entero).
PROVEEDORES = {
    "anthropic": {"nombre": "Claude (Anthropic)", "web": "https://platform.claude.com/"},
    "openai": {"nombre": "OpenAI / ChatGPT", "web": "https://platform.openai.com/"},
    "openrouter": {"nombre": "OpenRouter (cualquier modelo, paga por uso)", "web": "https://openrouter.ai/"},
    "groq": {"nombre": "Groq (muy rapido, capa gratis)", "web": "https://console.groq.com/"},
    "deepseek": {"nombre": "DeepSeek (muy barato)", "web": "https://platform.deepseek.com/"},
    "gemini": {"nombre": "Google Gemini", "web": "https://aistudio.google.com/apikey"},
    "mistral": {"nombre": "Mistral", "web": "https://console.mistral.ai/"},
    "xai": {"nombre": "xAI (Grok)", "web": "https://console.x.ai/"},
    "custom": {"nombre": "Otro servidor compatible con OpenAI", "web": ""},
}

CLAVES_PATH = os.path.expanduser("~/.config/ovos-claves.json")


def leer_clave(proveedor):
    """Clave de API guardada para ese proveedor (o "")."""
    try:
        with open(CLAVES_PATH, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get(proveedor) or ""
    except Exception:
        return ""


def guardar_clave(proveedor, clave):
    """Guarda la clave en el archivo privado de claves (0600)."""
    try:
        os.makedirs(os.path.dirname(CLAVES_PATH), exist_ok=True)
        try:
            with open(CLAVES_PATH, encoding="utf-8") as fh:
                datos = json.load(fh)
            if not isinstance(datos, dict):
                datos = {}
        except FileNotFoundError:
            datos = {}
        if clave:
            datos[proveedor] = clave
        else:
            datos.pop(proveedor, None)
        tmp = CLAVES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, CLAVES_PATH)
        os.chmod(CLAVES_PATH, 0o600)
        return True
    except Exception:
        return False


# Modelos que se pueden descargar con 'ollama pull'. Los nombres exactos
# estan en MODELOS_CLAVE: la ventana consulta al registro el tamano real
# antes de proponer la descarga.
MODELOS_CLAVE = [
    ("llama3.1:8b", "Conversación general"),
    ("ministral-3:8b", "Razonamiento y datos"),
    ("qwen2.5-coder:7b", "Código y programación"),
    ("deepseek-coder-v2:16b", "Código, muy preciso"),
    ("codestral:22b", "Código, modelo grande"),
    ("gpt-oss:20b", "Razonamiento abierto de OpenAI"),
    ("gemma3:12b", "General, rápido"),
    ("phi4:14b", "Razonamiento compacto"),
]

REGISTRY_OLLAMA = "https://registry.ollama.ai/v2"


def tamano_modelo(modelo, timeout=15):
    """Bytes que ocupara al descargarse, segun el registro de ollama.

    Devuelve None si no se puede averiguar (sin red, o nombre desconocido).
    """
    nombre = (modelo or "").strip()
    if ":" not in nombre:
        nombre = f"{nombre}:latest"
    repo, _, tag = nombre.partition(":")
    ruta = repo if "/" in repo else f"library/{repo}"
    req = urllib.request.Request(
        f"{REGISTRY_OLLAMA}/{ruta}/manifests/{tag}",
        headers={"Accept": "application/vnd.docker.distribution."
                            "manifest.v2+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            capas = json.loads(r.read().decode("utf-8")).get("layers") or []
        total = sum(c.get("size") or 0 for c in capas)
        return total or None
    except Exception:
        return None


def espacio_libre(ruta=os.path.expanduser("~")):
    """Bytes libres en el disco donde se instala ollama."""
    try:
        st = os.statvfs(ruta)
        return st.f_bavail * st.f_frsize
    except Exception:
        return None


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def current_tts_voice():
    try:
        with open(MYCROFT_CONF_PATH, "r", encoding="utf-8") as f:
            conf = json.load(f)
        tts = conf.get("tts", {}) or {}
        for clave in (MODULO_TTS, MODULO_PIPER):
            voz = (tts.get(clave) or {}).get("voice")
            if voz:
                return voz
        return None
    except Exception:
        return None


def apply_secondary_langs(langs):
    """Escribe 'secondary_langs' en mycroft.conf.

    Son los idiomas que, ademas del principal, padatious entrena y
    reconoce. Se derivan de idiomas.json. No reinicia servicios: los
    contenedores de padatious se crean al arrancar ovos-core."""
    try:
        with open(MYCROFT_CONF_PATH, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        conf = {}
    conf["secondary_langs"] = list(langs or [])
    backup = MYCROFT_CONF_PATH + "~"
    try:
        os.replace(MYCROFT_CONF_PATH, backup)
    except Exception:
        pass
    with open(MYCROFT_CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)
    return True


def apply_tts_voice(voice):
    if not voice:
        return False
    try:
        with open(MYCROFT_CONF_PATH, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        conf = {}
    conf.setdefault("tts", {})
    conf["tts"]["module"] = MODULO_TTS
    conf["tts"].setdefault(MODULO_PIPER, {})["voice"] = voice
    conf["tts"].setdefault(MODULO_TTS, {})["voice"] = voice
    backup = MYCROFT_CONF_PATH + "~"
    try:
        os.replace(MYCROFT_CONF_PATH, backup)
    except Exception:
        pass
    with open(MYCROFT_CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)
    subprocess.run(
        ["systemctl", "--user", "restart", "ovos-audio.service"],
        capture_output=True)
    return True


def ensure_tts_module():
    """Asegura que mycroft.conf use el plugin TTS multi-idioma.

    Mantiene la voz ya configurada copiandola a la clave del nuevo modulo.
    No reinicia servicios (se aplica al siguiente arranque de ovos-audio).
    """
    try:
        with open(MYCROFT_CONF_PATH, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except Exception:
        return False
    tts = conf.setdefault("tts", {})
    if tts.get("module") == MODULO_TTS:
        return False
    voz = (tts.get(MODULO_PIPER) or {}).get("voice")
    tts["module"] = MODULO_TTS
    if voz:
        tts.setdefault(MODULO_TTS, {})["voice"] = voz
    backup = MYCROFT_CONF_PATH + "~"
    try:
        os.replace(MYCROFT_CONF_PATH, backup)
    except Exception:
        pass
    with open(MYCROFT_CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2, ensure_ascii=False)
    return True


if __name__ == "__main__":
    c = load_config()
    print(json.dumps(c, indent=2, ensure_ascii=False))
    print("Voz TTS actual:", current_tts_voice())