import base64
import glob
import io
import json
import os
import re
import shutil
import smtplib
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from collections import deque
from typing import List, Optional

import requests

from ovos_bus_client.message import Message, dig_for_message
from ovos_bus_client.session import SessionManager
from ovos_utils.process_utils import RuntimeRequirements
from ovos_workshop.skills.fallback import FallbackSkill
from ovos_workshop.decorators import classproperty

OLLAMA_URL = "http://127.0.0.1:11435/v1/chat/completions"
MODEL = "llama3.1:8b"

# Ollama nativo (vision): para localizar elementos visibles en pantalla
# mediante captura e interpretacion del modelo multimodal.
VISION_URL = "http://127.0.0.1:11434/api/chat"
VISION_MODEL = "qwen2.5vl:7b"

# --- Multimodelo -----------------------------------------------------
# Pools de ollama que se consultan: el del usuario (11435, sin sudo) y el
# del sistema (11434, que ademas sirve el modelo de vision). Ambos
# exponen /api/tags y /v1/chat/completions.
OLLAMA_POOLS = (11435, 11434)
# Pool donde se pueden descargar modelos sin privilegios.
POOL_INSTALACION = 11435
CONFIG_PATH = os.path.expanduser("~/.config/ovos-asistente.json")

# --- Proveedores remotos (API de pago) -----------------------------
# estilo "openai"    -> Authorization: Bearer  y payload estilo OpenAI
# estilo "anthropic" -> x-api-key + anthropic-version y payload propio
#
# 'modelo' NO se fija aqui a proposito: los identificadores cambian muy a
# menudo. La ventana de configuracion los consulta con el endpoint
# /models de cada proveedor, asi que no hay nada que quedarse viejo.
PROVEEDORES = {
    "anthropic": {
        "nombre": "Claude (Anthropic)", "estilo": "anthropic",
        "chat": "https://api.anthropic.com/v1/messages",
        "modelos": "https://api.anthropic.com/v1/models",
        "web": "https://platform.claude.com/",
        "ayuda": "La clave empieza por sk-ant-. Se saca en platform.claude.com.",
    },
    "openai": {
        "nombre": "OpenAI / ChatGPT", "estilo": "openai",
        "chat": "https://api.openai.com/v1/chat/completions",
        "modelos": "https://api.openai.com/v1/models",
        "web": "https://platform.openai.com/",
        "ayuda": "Clave sk-... de platform.openai.com. Ojo: la suscripción "
                 "de ChatGPT Plus NO incluye API; hay que pagar el uso.",
    },
    "openrouter": {
        "nombre": "OpenRouter (manyos de pago)", "estilo": "openai",
        "chat": "https://openrouter.ai/api/v1/chat/completions",
        "modelos": "https://openrouter.ai/api/v1/models",
        "web": "https://openrouter.ai/",
        "ayuda": "Una sola clave da acceso a Claude, GPT, Gemini, Llama y "
                 "demas. Paga por uso.",
    },
    "groq": {
        "nombre": "Groq (muy rapido, capa gratis)", "estilo": "openai",
        "chat": "https://api.groq.com/openai/v1/chat/completions",
        "modelos": "https://api.groq.com/openai/v1/models",
        "web": "https://console.groq.com/",
        "ayuda": "Clave gsk_... de console.groq.com. Tiene capa gratuita.",
    },
    "deepseek": {
        "nombre": "DeepSeek", "estilo": "openai",
        "chat": "https://api.deepseek.com/chat/completions",
        "modelos": "https://api.deepseek.com/models",
        "web": "https://platform.deepseek.com/",
        "ayuda": "Clave sk-... muy barata por token.",
    },
    "gemini": {
        "nombre": "Google Gemini", "estilo": "openai",
        "chat": "https://generativelanguage.googleapis.com/v1beta/openai/"
                "chat/completions",
        "modelos": "https://generativelanguage.googleapis.com/v1beta/openai/"
                   "models",
        "web": "https://aistudio.google.com/apikey",
        "ayuda": "Clave AIza... de aistudio.google.com/apikey.",
    },
    "mistral": {
        "nombre": "Mistral", "estilo": "openai",
        "chat": "https://api.mistral.ai/v1/chat/completions",
        "modelos": "https://api.mistral.ai/v1/models",
        "web": "https://console.mistral.ai/",
        "ayuda": "Clave de console.mistral.ai.",
    },
    "xai": {
        "nombre": "xAI (Grok)", "estilo": "openai",
        "chat": "https://api.x.ai/v1/chat/completions",
        "modelos": "https://api.x.ai/v1/models",
        "web": "https://console.x.ai/",
        "ayuda": "Clave xai-... de console.x.ai.",
    },
    "custom": {
        "nombre": "Otro servidor (compatible con OpenAI)", "estilo": "openai",
        "chat": "", "modelos": "",
        "web": "",
        "ayuda": "Endpoint propio: escribe la URL completa hasta "
                 "/v1/chat/completions (LM Studio, vLLM, llama.cpp server, "
                 "OpenAI en un servidor...).",
    },
}

# Las claves van aparte del resto de config y solo para el usuario: asi
# nunca acaban en un log ni en una captura de pantalla de la config.
CLAVES_PATH = os.path.expanduser("~/.config/ovos-claves.json")

# clave -> (nombre visible, web). Sirve para "abre claude" y para
# recomendar cuando el usuario pide un modelo especializado.
SERVICIOS_IA = {
    "claude": ("Claude", "https://claude.ai/new"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "gemini": ("Gemini", "https://gemini.google.com/app"),
    "perplexity": ("Perplexity", "https://www.perplexity.ai/"),
    "poe": ("Poe", "https://poe.com/"),
    "copilot": ("GitHub Copilot", "https://github.com/features/copilot"),
    "cursor": ("Cursor", "https://cursor.sh/"),
    "code": ("VS Code", "https://code.visualstudio.com/"),
    "huggingface": ("Hugging Face", "https://huggingface.co/chat"),
    "ollama": ("Biblioteca de Ollama", "https://ollama.com/library"),
    "leonardo": ("Leonardo AI", "https://leonardo.ai/"),
    "openart": ("OpenArt", "https://openart.ai/"),
    "comfyui": ("ComfyUI", "https://github.com/comfyanonymous/ComfyUI"),
    "runway": ("Runway", "https://runwayml.com/"),
    "suno": ("Suno", "https://suno.com/"),
    "elevenlabs": ("ElevenLabs", "https://elevenlabs.io/"),
}

# Instaladores reales (CLI/GUI de IA) que el sistema puede ejecutar.
# clave -> (etiqueta, binario a comprobar, comando de instalacion)
CLI_IA = {
    "claude code": ("Claude Code", "claude",
                    "curl -fsSL https://claude.ai/install.sh | bash"),
    "gemini cli": ("Gemini CLI", "gemini",
                   "npm install -g @google/gemini-cli"),
    "code": ("VS Code", "code", "zypper install code"),
    "ollama": ("Ollama", "ollama", "zypper install ollama"),
}

# Modelos que se pueden descargar con "ollama pull" -> para que sirve.
MODELOS_INSTALABLES = [
    ("llama3.1:8b", "conversación general"),
    ("ministral-3:8b", "razonamiento y datos"),
    ("qwen2.5-coder:7b", "código y programación"),
    ("deepseek-coder-v2:16b", "código, muy preciso"),
    ("codestral:22b", "código, modelo grande"),
    ("qwen2.5vl:7b", "ver e interpretar imágenes"),
    ("gemma3:12b", "general, rápido"),
    ("phi4:14b", "razonamiento compacto"),
    ("gpt-oss:20b", "razonamiento abierto de OpenAI"),
]

# Necesidad -> como la cubrimos. Primero lo local (ya instalado o
# instalable) y si no la web del servicio, que el usuario puede abrir.
def _parece_tarea(frase: str) -> bool:
    """¿El usuario ha dicho para que lo quiere, en vez de un modelo?

    "para programar", "para editar fotos"… no son nombres de modelo, asi
    que lo correcto es recomendar en vez de buscar coincidencias.
    """
    f = (frase or "").strip().lower()
    if not f:
        return False
    if f.startswith("para ") or f.startswith("para"):
        return True
    # si ninguna palabra suelta a un nombre de modelo instalado, casi
    # seguro es una tarea
    palabras = [p for p in re.split(r"[^a-záéíóúñü0-9]+", f) if len(p) > 3]
    if not palabras:
        return False
    claves_tarea = set()
    for esp in ESPECIALIDADES:
        claves_tarea.update(esp["claves"])
    return any(p in claves_tarea for p in palabras)


ESPECIALIDADES = [
    {"nombre": "código",
     "claves": ["codigo", "código", "programar", "programacion",
                "programación", "programador", "desarrollar", "desarrollo",
                "developer", "depurar", "debug", "refactorizar", "scripts",
                "codificar", "automatizar", "programar", "compilar",
                "python", "javascript", "typescript", "rust", "html", "sql"],
     "ollama": ["qwen2.5-coder:7b", "deepseek-coder-v2:16b", "llama3.1:8b"],
     "servicios": ["cursor", "claude", "chatgpt", "copilot"],
     "nota": "programar"},
    {"nombre": "imágenes",
     "claves": ["imagen", "imagenes", "imágenes", "dibujo", "dibujar",
                "ilustracion", "ilustración", "foto", "fotos", "retrato",
                "logo", "pixel art", "generar imagen", "crear imagen"],
     "ollama": [],
     "servicios": ["leonardo", "chatgpt", "openart"],
     "nota": "generar imágenes"},
    {"nombre": "vídeo",
     "claves": ["video", "videos", "vídeo", "vídeos", "animacion",
                "animación", "clip", "pelicula", "película", "generar video"],
     "ollama": [],
     "servicios": ["runway", "suno", "chatgpt"],
     "nota": "generar vídeo"},
    {"nombre": "audio y voz",
     "claves": ["audio", "voz", "voces", "musica", "música",
                "cantar", "locucion", "locución", "doblaje", "sonido",
                "podcast", "tts", "clonar voz", "sintetizar"],
     "ollama": [],
     "servicios": ["elevenlabs", "suno", "chatgpt"],
     "nota": "sintetizar o clonar voz"},
    {"nombre": "investigación",
     "claves": ["investigacion", "investigación", "investigar",
                "investigaciones", "estudio", "estudiar", "documentar",
                "informe", "papers", "tesis", "documentos",
                "resumir", "resumen", "explicar"],
     "ollama": ["ministral-3:8b", "llama3.1:8b"],
     "servicios": ["perplexity", "claude", "chatgpt"],
     "nota": "investigar y resumir"},
    {"nombre": "redacción y traducción",
     "claves": ["redaccion", "redacción", "texto", "escribir", "correo",
                "traducir", "traduccion", "traducción"],
     "ollama": ["llama3.1:8b"],
     "servicios": ["chatgpt", "claude", "gemini"],
     "nota": "redactar y traducir"},
]

# Fast keyword gate: if none of these appear, skip the LLM entirely
# and let the utterance fall through to the persona.
_RUTA_HINTS = os.path.join(os.path.dirname(__file__), "hints.json")

# Palabras clave por idioma (base). Se cargan de hints.json; esto es solo
# el respaldo minimo por si el fichero no estuviera.
_HINTS = {
    "es": {
        "system": ["archivo", "buscar", "instala", "comando", "modelo"],
        "seguimiento": ["esos archivos", "los nombres"],
        "cerrar": r"(cierra|cerrar|cerra).{0,4}(la.\s*)?(ventana|chat)",
    },
}


def _carga_hints():
    try:
        with open(_RUTA_HINTS, encoding="utf-8") as fh:
            datos = json.load(fh)
    except Exception:
        return
    for cod, bloques in (datos or {}).items():
        if str(cod).startswith("_") or not isinstance(bloques, dict):
            continue
        _HINTS[str(cod).strip().lower()] = bloques


_carga_hints()


def _base_lang(lang):
    """'en-US' -> 'en'; cadena vacia si no hay."""
    return str(lang or "").split("-")[0].strip().lower()


def _hints_para(lang):
    """Bloque de palabras clave del idioma, con respaldo a español."""
    base = _base_lang(lang)
    return _HINTS.get(base) or _HINTS.get("es") or {}


SYSTEM_HINTS = _HINTS["es"]["system"]

# Defense in depth: never execute these (even if the LLM proposes them)
DANGEROUS_PATTERNS = [
    r"\bsudo\b", r"\bpkexec\b", r"\bsu\s", r"\brm\s+-rf\b", r"\bmkfs\b",
    r"\bdd\s+", r"\bchmod\s+-R\s+777\s+/", r"\bpasswd\b", r"\bshutdown\b",
    r"\breboot\b", r"\bpoweroff\b", r">\s*/dev/sd", r":\(\)\s*\{",
    r"\bkillall\b", r"\bpkill\b", r"\bkill\s+-9", r"\brm\s+/\s*-",
]

# Unica excepcion a la lista negra: los comandos privilegiados construidos
# por el propio skill (instalar/desinstalar con polkit, agregar repos de la
# comunidad, activar servicios del sistema): todo pasa por pkexec y pide
# contrasena en el dialogo polkit.
ALLOWED_PKEXEC_INSTALL_RE = re.compile(
    r"^pkexec\s+(?:"
    r"(?:zypper\s+(?:--non-interactive\s+)?(?:install|remove)\s+"
    r"[A-Za-z0-9][A-Za-z0-9._+:-]*(\s+[A-Za-z0-9][A-Za-z0-9._+:-]*)*)|"
    r"(?:apt-get\s+-y\s+(?:install|remove)\s+"
    r"[A-Za-z0-9][A-Za-z0-9._+:-]*(\s+[A-Za-z0-9][A-Za-z0-9._+:-]*)*)|"
    r"(?:dnf\s+-y\s+(?:install|remove)\s+"
    r"[A-Za-z0-9][A-Za-z0-9._+:-]*(\s+[A-Za-z0-9][A-Za-z0-9._+:-]*)*)|"
    r"(?:pacman\s+-(?:S|R)\s+--noconfirm\s+"
    r"[A-Za-z0-9][A-Za-z0-9._+:-]*(\s+[A-Za-z0-9][A-Za-z0-9._+:-]*)*)|"
    r"(?:flatpak\s+(?:install|uninstall)\s+"
    r"[A-Za-z0-9][A-Za-z0-9._+:-]*(\s+[A-Za-z0-9][A-Za-z0-9._+:-]*)*)|"
    r"(?:zypper\s+(?:--non-interactive\s+)?(?:--gpg-auto-import-keys\s+)?"
    r"addrepo\s+(?:--refresh\s+)?\S+\s+\S+)|"
    r"(?:systemctl\s+(?:enable|disable|start|stop|restart)\s+"
    r"[A-Za-z0-9][A-Za-z0-9._-]*\.service)"
    r")\s*$")

# Programas de Windows / comerciales y su equivalente libre en Linux.
# intent -> (paquete_equivalente, descripcion)
EQUIVALENCIAS = {
    "photoshop": ("gimp", "edición de imagen"),
    "paint": ("gimp", "edición de imagen"),
    "mspaint": ("gimp", "edición de imagen"),
    "maya": ("blender", "modelado y animación 3D"),
    "3ds max": ("blender", "modelado y animación 3D"),
    "autocad": ("librecad", "dibujo técnico 2D"),
    "office": ("libreoffice", "suite de oficina"),
    "word": ("libreoffice", "procesador de textos"),
    "excel": ("libreoffice", "hoja de cálculo"),
    "powerpoint": ("libreoffice", "presentaciones"),
    "outlook": ("thunderbird", "correo electrónico"),
    "access": ("libreoffice", "base de datos"),
    "illustrator": ("inkscape", "gráficos vectoriales"),
    "coreldraw": ("inkscape", "gráficos vectoriales"),
    "premiere": ("kdenlive", "edición de vídeo"),
    "after effects": ("kdenlive", "composición y vídeo"),
    "sony vegas": ("kdenlive", "edición de vídeo"),
    "dreamweaver": ("bluefish", "editor web"),
    "visio": ("dia", "diagramas"),
    "project": ("projectlibre", "gestión de proyectos"),
    "fl studio": ("lmms", "estudio de música"),
    "cubase": ("ardour", "grabación de audio"),
    "pro tools": ("ardour", "grabación de audio"),
}

# Sitios web conocidos para abrir_web: nombre que dice el usuario -> URL
SITIOS_CONOCIDOS = {
    "youtube": "https://youtube.com",
    "google": "https://google.com",
    "gmail": "https://mail.google.com",
    "wikipedia": "https://es.wikipedia.org",
    "netflix": "https://netflix.com",
    "facebook": "https://facebook.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "instagram": "https://instagram.com",
    "github": "https://github.com",
    "amazon": "https://www.amazon.es",
    "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com",
    "marca": "https://www.marca.com",
    "el país": "https://elpais.com",
    "el pais": "https://elpais.com",
}

# Servicios que el asistente puede instalar, poner en marcha y guiar.
# clave -> (paquetes_por_pm, unidad_por_pm, puerto, guia)
SERVICIOS = {
    "web": (
        {"zypper": ["apache2", "apache2-prefork"], "apt": ["apache2"],
         "dnf": ["httpd"], "pacman": ["apache"]},
        {"zypper": "apache2", "apt": "apache2", "dnf": "httpd",
         "pacman": "httpd"},
        80,
        "Tus páginas van en /srv/www/htdocs o /var/www/html. Los vhosts "
        "se definen en /etc/apache2/vhosts.d/ y la config general en "
        "/etc/apache2/httpd.conf."),
    "nginx": (
        {"zypper": ["nginx"], "apt": ["nginx"], "dnf": ["nginx"],
         "pacman": ["nginx"]},
        {"zypper": "nginx", "apt": "nginx", "dnf": "nginx",
         "pacman": "nginx"},
        80,
        "Tus páginas van en /usr/share/nginx/html y la config en "
        "/etc/nginx/nginx.conf con los 'server blocks' en /etc/nginx/conf.d/."),
    "mariadb": (
        {"zypper": ["mariadb"], "apt": ["mariadb-server"],
         "dnf": ["mariadb-server"], "pacman": ["mariadb"]},
        {"zypper": "mariadb", "apt": "mariadb", "dnf": "mariadb",
         "pacman": "mariadb"},
        3306,
        "La config está en /etc/my.cnf. Para asegurarla, ejecuta "
        "'sudo mariadb-secure-installation' y sigue el asistente: fija "
        "contraseña de root, quita usuarios anónimos y bases de prueba."),
    "php": (
        {"zypper": ["php-fpm"], "apt": ["php-fpm"],
         "dnf": ["php-fpm"], "pacman": ["php", "php-fpm"]},
        {"zypper": "php-fpm", "apt": "php-fpm", "dnf": "php-fpm",
         "pacman": "php-fpm"},
        9000,
        "El servicio FastCGI escucha en localhost:9000. Los sitios PHP "
        "se sirven con nginx o apache; prueba con un archivo info.php."),
    "ftp": (
        {"zypper": ["vsftpd"], "apt": ["vsftpd"], "dnf": ["vsftpd"],
         "pacman": ["vsftpd"]},
        {"zypper": "vsftpd", "apt": "vsftpd", "dnf": "vsftpd",
         "pacman": "vsftpd"},
        21,
        "Config en /etc/vsftpd.conf. Para permitir subir archivos activa "
        "write_enable=YES y los usuarios locales con local_enable=YES."),
    "ssh": (
        {"zypper": ["openssh-server"], "apt": ["openssh-server"],
         "dnf": ["openssh-server"], "pacman": ["openssh"]},
        {"zypper": "sshd", "apt": "ssh", "dnf": "sshd",
         "pacman": "sshd"},
        22,
        "El puerto y el login se configuran en /etc/ssh/sshd_config. "
        "Conecta desde otro equipo con: ssh usuario@ip."),
}

SERVICIO_ALIASES = {
    "apache": "web", "apache2": "web", "http": "web", "httpd": "web",
    "servidor web": "web", "web server": "web", "server http": "web",
    "nginx": "nginx", "mariadb": "mariadb", "mysql": "mariadb",
    "sql": "mariadb", "base de datos": "mariadb", "bd": "mariadb",
    "php": "php", "ftpserver": "ftp", "ftp": "ftp", "servidor ftp": "ftp",
    "ssh": "ssh", "openssh": "ssh", "servidor ssh": "ssh",
}

# Base de conocimiento de comandos esenciales para nuevos usuarios.
KB_COMANDOS = {
    "ls": "ls lista el contenido de una carpeta. 'ls -a' muestra también "
          "archivos ocultos (los que empiezan por punto) y 'ls -l' añade "
          "permisos, dueño y tamaño. Ejemplo: ls -la /home/usuario.",
    "cd": "cd cambia de carpeta. 'cd Documentos' entra en Documentos; "
          "'cd ..' sube una carpeta; 'cd ~' o solo 'cd' te lleva a tu "
          "carpeta personal.",
    "pwd": "pwd imprime la ruta completa de la carpeta en la que estás "
           "ahora mismo. Útil para orientarte en la terminal.",
    "mkdir": "mkdir crea una carpeta nueva. Ejemplo: mkdir Proyectos crea "
             "la carpeta 'Proyectos'. Úsalo con corcho y papel: los nombres "
             "con espacios van entre comillas.",
    "rm": "rm borra archivos o carpetas. Ojo: no tiene papelera. Para "
          "carpetas se usa 'rm -r' y nunca borres carpetas del sistema. "
          "Antes de borrar, verifica con ls lo que hay dentro.",
    "cp": "cp copia archivos o carpetas. Ejemplo: cp notas.txt copia.txt. "
          "Con -r copia carpetas enteras.",
    "mv": "mv mueve o renombra. Ejemplo: mv notas.txt Documentos/ mueve el "
          "archivo a Documentos; mv viejas.txt nuevas.txt lo renombra.",
    "cat": "cat muestra el contenido de un archivo en pantalla. Ideal para "
           "leer archivos pequeños.",
    "less": "less muestra un archivo largo página a página. Pulsa espacio "
            "para avanzar, b para retroceder y q para salir.",
    "grep": "grep busca texto dentro de archivos o salidas. Ejemplo: "
            "grep 'error' syslog busca la palabra error en el archivo. "
            "Suele combinarse con otros comandos mediante el símbolo |",
    "find": "find busca archivos por nombre, tipo o tamaño. Por ejemplo: "
            "find /home -iname '*.pdf' busca todos los PDF de tu carpeta. "
            "Es tu mejor amigo para encontrar archivos perdidos.",
    "ps": "ps muestra los procesos en marcha. 'ps aux' los muestra todos "
          "con detalle. Verás el identificador (PID) de cada proceso.",
    "top": "top muestra en vivo qué procesos consumen CPU o memoria. "
           "Pulsa q para salir.",
    "chmod": "chmod cambia los permisos de un archivo. 'chmod +x programa' "
             "lo hace ejecutable. Los números 755/644 son los más comunes: "
             "el dueño manda, el grupo lee, los demás acceden.",
    "df": "df muestra el espacio libre de los discos. 'df -h' lo muestra "
          "en tamaño humano (G, M).",
    "du": "du mide el espacio que ocupa una carpeta o archivo. "
          "'du -sh carpeta' da el total en un solo número legible.",
    "tar": "tar empaqueta y comprime: 'tar -czf archivo.tar.gz carpeta' "
           "comprime; 'tar -xzf archivo.tar.gz' descomprime.",
    "curl": "curl descarga o consulta páginas desde la terminal. "
            "Ejemplo: 'curl -O http://sitio/archivo' descarga un archivo.",
    "wget": "wget descarga archivos desde internet sin abrir navegador. "
            "Ejemplo: wget https://sitio/archivo.zip.",
    "chown": "chown cambia el dueño de un archivo: 'sudo chown usuario:usuario "
             "archivo' devuelve la propiedad al usuario.",
    "zip": "zip comprime: 'zip -r cosas.zip carpeta' comprime, "
           "'unzip cosas.zip' descomprime.",
    "unzip": "unzip descomprime archivos .zip.",
    "whoami": "whoami dice con qué usuario estás trabajando (tu nombre de "
              "cuenta).",
    "sudo": "sudo ejecuta un comando con permisos de administrador. "
            "Te pedirá tu contraseña. Úsalo solo cuando un comando lo "
            "necesite, por ejemplo para instalar programas.",
    "apt": "apt es el gestor de paquetes de las distros tipo Debian/Ubuntu: "
           "'sudo apt install programa' instala, 'sudo apt remove' "
           "desinstala.",
    "dnf": "dnf es el gestor de paquetes de Fedora: 'sudo dnf install "
           "programa', 'sudo dnf remove programa'.",
    "zypper": "zypper es el gestor de paquetes de openSUSE: "
              "'sudo zypper install programa', 'sudo zypper remove'.",
    "pacman": "pacman es el gestor de paquetes de Arch: "
              "'sudo pacman -S programa' instala, 'sudo pacman -R' quita.",
    "systemctl": "systemctl controla los servicios del sistema: "
                 "'systemctl status servicio' para ver su estado, "
                 "'sudo systemctl enable --now servicio' para que arranque "
                 "con el sistema y ahora mismo.",
}

# Si el LLM devuelve run_shell_command con un 'zypper install ...' directo,
# lo reconducimos al flujo de instalacion seguro.
INSTALL_CMD_RE = re.compile(
    r"^\s*(?:zypper\s+(?:--non-interactive\s+)?install|"
    r"apt(?:-get)?\s+install|dnf\s+install|pacman\s+-S"
    r")\s+([A-Za-z0-9._+:-]+)")

# "Abre una terminal/consola": se resuelve sin pasar por el LLM, que a
# veces confundia abrir_terminal con abrir_web y lanzaba una busqueda web.
_TERMINAL_WORDS = ("terminal", "consola", "shell", "bash",
                   "linea de comandos", "línea de comandos", "konsole",
                   "kterm")
_TERMINAL_ABRE_RE = re.compile(
    r"\b(abre|abrir|abreme|ábreme|abrime|ábrime|lanza|lanzar|"
    r"inicia|iniciar)\b")
_TERMINAL_CMD_RE = re.compile(
    r"\b(?:y\s+|que\s+)?(?:ejecuta|ejecutar|corre|correr|corriendo|"
    r"lanza|lanzar|que\s+ejecute|para\s+ejecutar)\s+(.+)$", re.I)

_NOMBRES_IDIOMA = {"es": "español", "en": "English", "ca": "català"}


def _carga_nombres_idioma():
    """Amplia _NOMBRES_IDIOMA con ~/.local/share/ovos/idiomas.json.

    Asi un idioma nuevo declarado alli (campo "llm") tambien hace que el
    asistente responda en ese idioma sin tocar este fichero."""
    try:
        ruta = os.path.expanduser("~/.local/share/ovos/idiomas.json")
        with open(ruta, encoding="utf-8") as fh:
            datos = json.load(fh)
    except Exception:
        return
    for cod, d in (datos or {}).items():
        if isinstance(d, dict) and d.get("llm"):
            _NOMBRES_IDIOMA[str(cod).strip().lower()] = d["llm"]


_carga_nombres_idioma()


def _codigo_idioma():
    """Codigo del idioma en el que debe responder el asistente.

    Sale de gui.idioma en la configuracion; si esta en 'auto' o no hay
    nada, se toma el idioma del mensaje en curso y, en ultimo caso, el
    de OVOS. Asi el asistente responde en el mismo idioma en el que
    estan las ventanas y en el que se renderizan las plantillas."""
    cod = ""
    try:
        with open(os.path.expanduser("~/.config/ovos-asistente.json"),
                  encoding="utf-8") as fh:
            cod = ((json.load(fh).get("gui") or {}).get("idioma") or "")
    except Exception:
        pass
    if cod in _NOMBRES_IDIOMA:
        return cod
    try:
        msg = dig_for_message()
        if msg is not None:
            lang = msg.data.get("lang") or (msg.context or {}).get("lang") or ""
            base = str(lang).split("-")[0].lower()
            if base in _NOMBRES_IDIOMA:
                return base
    except Exception:
        pass
    try:
        from ovos_config.config import Configuration
        lang = Configuration().get("lang") or "es"
    except Exception:
        lang = "es"
    base = str(lang).split("-")[0].lower()
    return base if base in _NOMBRES_IDIOMA else "es"


def _idioma_respuesta():
    """Nombre del idioma, para metérselo al LLM ('español', 'English')."""
    return _NOMBRES_IDIOMA[_codigo_idioma()]


def _ovos_para_base(base):
    """Codigo OVOS (p. ej. 'en-US') del idioma base ('en').

    Sale del campo 'ovos' de idiomas.json; si no se encuentra, devuelve el
    propio codigo base. Es el idioma que se pone en la sesion y en el que se
    elige la voz del TTS."""
    base = _base_lang(base)
    try:
        with open(os.path.expanduser("~/.local/share/ovos/idiomas.json"),
                  encoding="utf-8") as fh:
            datos = json.load(fh) or {}
        for cod, d in datos.items():
            if str(cod).startswith("_") or not isinstance(d, dict):
                continue
            if _base_lang(cod) == base and d.get("ovos"):
                return d["ovos"]
    except Exception:
        pass
    return base


def _lang_mensaje():
    """Idioma base del mensaje en curso ('es','en'...). Es el idioma en el
    que habla el usuario, que es el que usamos para reconocer las palabras
    clave, aunque la respuesta salga en otro idioma."""
    try:
        msg = dig_for_message()
        if msg is not None:
            lang = msg.data.get("lang") or (msg.context or {}).get("lang") or ""
            base = _base_lang(lang)
            if base:
                return base
    except Exception:
        pass
    return _codigo_idioma()


def _nombre_asistente():
    """Nombre del asistente desde ~/.config/ovos-asistente.json
    (clave 'nombre_asistente'), con 'lucIA' como valor por defecto."""
    try:
        with open(os.path.expanduser("~/.config/ovos-asistente.json"),
                  encoding="utf-8") as fh:
            nombre = (json.load(fh).get("nombre_asistente") or "").strip()
        if nombre:
            return nombre
    except Exception:
        pass
    return "lucIA"


SYSTEM_PROMPT = (
    f"Eres {_nombre_asistente()}, un asistente por voz integrado en GNU/Linux "
    "y especializado en ayudar a personas que empiezan. Funciona en cualquier "
    "distro: detecta el gestor de paquetes (zypper/apt/dnf/pacman) por ti. "
    "Traduce la petición del usuario a acciones reales con las herramientas "
    "disponibles.\n"
    "- run_shell_command: ejecuta el comando sin abrir ventanas y devuelve "
    "su salida para resumirla. Úsala SIEMPRE que el usuario mencione "
    "archivos, documentos, carpetas, comandos de terminal (para ejecutarlos), "
    "procesos, red o cualquier operación sobre el sistema.\n"
    "- abrir_terminal: abre una ventana de terminal visible. Úsala SIEMPRE "
    "que el usuario pida abrir una terminal, consola, shell o konsole.\n"
    "- buscar_paquetes: recomienda programas del sistema. Usa UN término en "
    "inglés técnico (una sola palabra: image, photo, video, music, office, "
    "game) porque los repositorios usan inglés. NUNCA frases.\n"
    "- instalar_paquete: instala un programa o paquete.\n"
    "- desinstalar_paquete: desinstala un programa o paquete.\n"
    "- explicar_comando: el usuario quiere saber qué hace un comando o cómo "
    "usarlo ('¿qué hace grep?', '¿cómo borro un archivo?').\n"
    "- configurar_servicio: el usuario quiere levantar o configurar un "
    "servicio (servidor web, base de datos, PHP, FTP, SSH, MariaDB).\n"
    "- redactar_texto: redacta un texto a partir de lo que el usuario dicta o "
    "describe ('redacta una carta de renuncia por mudanza', 'ayúdame a "
    "escribir un email de reclamación'). El sistema pide los datos que "
    "falten y prepara el borrador en el editor.\n"
    "- enviar_email: prepara o envía un correo electrónico. Úsala con "
    "destinatario, asunto y texto cuando el usuario los diga; si falta "
    "algún dato, el sistema lo pregunta.\n"
    "- abrir_web: abre un sitio o página web en el navegador (youtube, "
    "wikipedia, una dirección web concreta...). NO la uses para abrir "
    "programas ni para abrir una terminal o consola.\n"
    "- buscar_internet: el usuario quiere buscar algo en la web ('busca "
    "recetas de cocina', 'investiga cómo se cultiva el café'); abre los "
    "resultados de búsqueda en el navegador.\n"
    "- consultar_web: el usuario pregunta qué dice o contiene una web "
    "concreta; se consulta la página y se resume la respuesta.\n"
    "- noop: usa SOLO para preguntas generales o conversación sin operación "
    "del sistema (por ejemplo: '¿qué es linux?', 'cuéntame un chiste').\n"
    "REGLAS:\n"
    "1. Buscar un archivo por nombre es SIEMPRE run_shell_command con find.\n"
    "   Ejemplos:\n"
    "   'estoy buscando un archivo que se llama mis apuntes y no lo "
    "encuentro' -> {'command': 'find /home/usuario -iname \"*apuntes*\"'}\n"
    "   'busca el archivo tareas.txt' "
    "-> {'command': 'find / -name \"tareas.txt\"'}\n"
    "1b. Por tipo de archivo o extension: combina varios -iname con -o "
    "dentro de parentesis, agrupando con comillas simples. No uses "
    "backslashes: el sistema normaliza comillas y parentesis "
    "automaticamente.\n"
    "   Ejemplos:\n"
    "   'busca fotos en Descargas' "
    "-> {'command': 'find /home/usuario/Descargas \\( -iname \"*.jpg\" -o "
    "-iname \"*.png\" -o -iname \"*.jpeg\" \\)'}\n"
    "   'los pdf en Documentos' "
    "-> {'command': 'find /home/usuario/Documentos -iname \"*.pdf\"'}\n"
    "2. No importa si el nombre no tiene extensión ni es exacto.\n"
    "3. Para RECOMENDAR un programa usa buscar_paquetes (nunca zypper ni "
    "apt directamente). Para saber qué comando existe o qué hace, usa "
    "explicar_comando.\n"
    "4. Para INSTALAR un programa usa instalar_paquete con el nombre exacto "
    "del paquete o aplicación. Si el usuario pide un programa de Windows o "
    "comercial (photoshop, maya, office), instalar_paquete con ese nombre; "
    "el sistema gestiona equivalentes y emulación.\n"
    "5. Para DESINSTALAR usa desinstalar_paquete.\n"
    "6. NUNCA pongas sudo, pkexec ni su dentro de run_shell_command. La "
    "instalación y los servicios ya gestionan los privilegios por sí solos.\n"
    "7. Los comandos deben ser seguros y sin efectos destructivos.\n"
    "8. Usa rutas relativas a la carpeta del usuario (/home/usuario) o "
    "absolutas.\n"
    "9. Dentro de los comandos agrupa con comillas simples "
    "(p. ej. -iname '*apuntes*'), NUNCA dobles, para que el JSON sea válido.\n"
    "10. Abrir una terminal o consola es SIEMPRE abrir_terminal. abrir_web "
    "es SOLO para sitios o páginas web; nunca para programas ni terminales.\n"
    "Responde únicamente con la llamada a la herramienta apropiada."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Ejecuta un comando de terminal en GNU/Linux y "
                           "devuelve su salida para resumirla. Usarla "
                           "SIEMPRE que el usuario pida buscar o encontrar un "
                           "archivo/documento por su nombre (incluso informal, "
                           "sin extensión: 'mis apuntes', 'tareas'), listar "
                           "carpetas, mover/copiar/borrar/renombrar, ver "
                           "procesos, instalar paquetes, consultar la red, "
                           "etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "El comando shell completo a ejecutar "
                                       "como usuario normal, sin sudo. "
                                       "Ejemplo: 'find / -name single*.txt'"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abrir_terminal",
            "description": "Abre una ventana de terminal visible al usuario, "
                           "opcionalmente ejecutando un comando dentro de "
                           "ella. Usar cuando el usuario pide abrir una "
                           "consola, terminal, shell o konsole.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Comando a ejecutar en la terminal, "
                                       "o vacío para abrir una terminal "
                                       "normal"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "instalar_paquete",
            "description": "Instala un programa o paquete en el sistema "
                           "usando el gestor de paquetes zypper. Usar cuando "
                           "el usuario pide instalar, descargar e instalar, "
                           "añadir o instalar un programa/aplicación.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paquete": {
                        "type": "string",
                        "description": "Nombre exacto del paquete a instalar "
                                       "(p. ej. 'gimp', 'vlc')"
                    }
                },
                "required": ["paquete"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_paquetes",
            "description": "Recomienda o busca programas disponibles en los "
                           "repositorios del sistema usando UN término corto "
                           "en inglés (image, photo, video, music, game). "
                           "Respeta ese término tal cual; el sistema lo "
                           "busca.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termino": {
                        "type": "string",
                        "description": "Término de búsqueda, una sola "
                                       "palabra en inglés"
                    }
                },
                "required": ["termino"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "desinstalar_paquete",
            "description": "Desinstala un programa o paquete del sistema con "
                           "confirmación previa del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paquete": {
                        "type": "string",
                        "description": "Nombre exacto del paquete a "
                                       "desinstalar (p. ej. 'gimp')"
                    }
                },
                "required": ["paquete"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "explicar_comando",
            "description": "Explica para qué sirve un comando de terminal, "
                           "cómo funciona y un ejemplo, en lenguaje sencillo. "
                           "Usar cuando el usuario pregunta 'qué hace X', "
                           "'cómo uso X', 'para qué sirve X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comando": {
                        "type": "string",
                        "description": "El comando a explicar (p. ej. "
                                       "'grep', 'chmod')"
                    }
                },
                "required": ["comando"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "configurar_servicio",
            "description": "Instala, pone en marcha y guía la configuración "
                           "de un servicio: web/apache/nginx, mariadb/mysql, "
                           "php, ftp, ssh. También consulta su estado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "servicio": {
                        "type": "string",
                        "description": "Servicio: 'web', 'nginx', 'mariadb', "
                                       "'php', 'ftp', 'ssh'"
                    },
                    "accion": {
                        "type": "string",
                        "description": "Pon 'estado' si el usuario solo "
                                       "pregunta si está activo ('revisa', "
                                       "'está funcionando', 'estado del "
                                       "servidor'). Pon 'levantar' o vacío "
                                       "para instalarlo y ponerlo en marcha."
                    }
                },
                "required": ["servicio"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "redactar_texto",
            "description": "Redacta un texto cuando el usuario lo dicta o lo "
                           "describe: carta, email, nota, reclamación, "
                           "currículum, felicitación, ayúdame a escribir... "
                           "El sistema pide lo que falte y abre el borrador en "
                           "el editor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "descripcion": {
                        "type": "string",
                        "description": "Qué quiere que redacte, tal como lo "
                                       "dice el usuario ('una carta de renuncia "
                                       "por mudanza a otra ciudad')"
                    }
                },
                "required": ["descripcion"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "enviar_email",
            "description": "Prepara o envía un correo electrónico a un "
                           "destinatario. Si el usuario dio destinatario, "
                           "asunto y texto, va aquí; si falta algo, el "
                           "sistema lo pregunta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destinatario": {
                        "type": "string",
                        "description": "Dirección de correo o nombre del "
                                       "destinatario"
                    },
                    "asunto": {
                        "type": "string",
                        "description": "Asunto del correo"
                    },
                    "texto": {
                        "type": "string",
                        "description": "Cuerpo del mensaje"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "abrir_web",
            "description": "Abre un sitio o página web en el navegador: "
                           "youtube, google, wikipedia, netflix o una "
                           "dirección web. NO usar para abrir programas ni "
                           "terminales.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sitio": {
                        "type": "string",
                        "description": "Sitio o dirección a abrir, tal como "
                                       "lo dice el usuario ('youtube', "
                                       "'el tiempo en madrid')"
                    }
                },
                "required": ["sitio"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_internet",
            "description": "El usuario quiere BUSCAR algo en la web."
                           "Abre los resultados de búsqueda en el navegador.",
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "La consulta, tal como la dice el "
                                       "usuario ('recetas de cocina fáciles')"
                    }
                },
                "required": ["consulta"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_web",
            "description": "Consulta qué dice una página web concreta: el "
                           "usuario pregunta 'qué dice la web X' o 'mira la "
                           "página X'. Se lee el contenido y se resume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pagina": {
                        "type": "string",
                        "description": "La página o dirección (p. ej. "
                                       "'wikipedia sobre linux')"
                    }
                },
                "required": ["pagina"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analizar_imagen",
            "description": "Ve la pantalla y opina, describe o identifica la "
                           "imagen principal que el usuario está viendo "
                           "(foto, obra de arte, captura, mockup...): 'qué te "
                           "parece esta imagen', 'analiza la imagen', 'da tu "
                           "opinión para mejorarla', 'dime información de "
                           "esta imagen', 'qué obra es'. Se captura la "
                           "pantalla, se le envía a un modelo de visión y se "
                           "responde hablado. Usarla cuando el usuario pida "
                           "analizar, describir, valorar o identificar una "
                           "imagen visible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "Qué quiere saber o qué opinión pide "
                                       "el usuario sobre la imagen, con sus "
                                       "propias palabras."
                    }
                },
                "required": ["consulta"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "raton_sobre_elemento",
            "description": "Mueve el puntero del ratón (y si se pide, hace "
                           "clic) sobre un elemento que se ve en la pantalla: "
                           "'pon el puntero en la barra de búsqueda', 'haz "
                           "clic en el tercer enlace de la página', 'pulsa el "
                           "botón de reproducir', 'reproduce el video con el "
                           "título X'. Se hace una captura de pantalla, se "
                           "localiza el elemento visualmente y se mueve/hace "
                           "clic en sus coordenadas reales. Usarla cuando el "
                           "usuario quiera interactuar con algo visible en "
                           "una ventana o página.",
            "parameters": {
                "type": "object",
                "properties": {
                    "descripcion": {
                        "type": "string",
                        "description": "Descripción del elemento visible a "
                                       "localizar, tal como lo dice el "
                                       "usuario: 'la barra de búsqueda', "
                                       "'el tercer enlace', 'el video titulado "
                                       "X'"
                    },
                    "accion": {
                        "type": "string",
                        "enum": ["mover", "clic", "doble_clic", "clic_derecho"],
                        "description": "Qué hacer: 'mover' solo sitúa el "
                                       "puntero; 'clic' hace clic normal; "
                                       "'doble_clic' doble clic; "
                                       "'clic_derecho' botón derecho"
                    }
                },
                "required": ["descripcion", "accion"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cambiar_modelo",
            "description": "Cambia el modelo de lenguaje con el que responde "
                           "el asistente. Usarla cuando el usuario pida "
                           "cambiar de modelo, usar otro modelo, o nombre uno "
                           "concreto. Si no dice ninguno, lista los "
                           "disponibles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "modelo": {
                        "type": "string",
                        "description": "Nombre del modelo deseado tal como lo "
                                       "dijo el usuario (por ejemplo 'llama', "
                                       "'ministral' o 'qwen coder'). Vacio "
                                       "para listar los disponibles."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "instalar_modelo",
            "description": "Descarga con 'ollama pull' un modelo de lenguaje "
                           "que todavia no esta instalado. Usarla si el "
                           "usuario quiere un modelo nuevo para una tarea "
                           "concreta (codigo, imagenes, voz...).",
            "parameters": {
                "type": "object",
                "properties": {
                    "modelo": {
                        "type": "string",
                        "description": "Modelo a descargar tal como lo nombro "
                                       "el usuario (por ejemplo 'qwen coder')."
                    }
                },
                "required": ["modelo"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recomendar_modelo",
            "description": "Recomienda como resolver una tarea con un modelo "
                           "especializado: activa uno local ya instalado, "
                           "propone descargarlo, o abre la web de un servicio. "
                           "Usarla cuando el usuario pida ayuda con codigo, "
                           "imagenes, video, voz, traduccion o "
                           "investigacion, o pregunte que modelo le conviene.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tarea": {
                        "type": "string",
                        "description": "La tarea en palabras del usuario, por "
                                       "ejemplo 'programar en python' o "
                                       "'generar imagenes'."
                    }
                },
                "required": ["tarea"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "noop",
            "description": "El usuario NO pide una operación del sistema; "
                           "es una pregunta general o conversación.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
]

AFFIRMATIVE = ("sí", "si", "yes", "yeah", "yep", "claro", "correcto",
               "afirmativo", "adelante", "hazlo", "de acuerdo", "ok", "vale",
               "dale", "por favor", "ejecutalo", "ejecútalo", "ejecuta",
               "hazlo por favor", "confirmo", "confirmado")


class AsistenteLinuxSkill(FallbackSkill):
    @classproperty
    def runtime_requirements(self) -> RuntimeRequirements:
        return RuntimeRequirements(
            internet_before_load=False,
            network_before_load=False,
            requires_internet=False,
            requires_network=False,
            no_internet_fallback=True,
            no_network_fallback=True,
        )

    @property
    def secondary_langs(self):
        """Idiomas (ademas del principal) para los que registrar los intents.

        Se sacan de idiomas.json: cada idioma declarado alli con su codigo
        OVOS (p. ej. 'en-us') y con una carpeta locale/<codigo>/intent/ hace
        que se registren sus intents. Asi añadir un idioma no toca codigo.
        """
        core = _base_lang(self.core_lang)
        raiz = os.path.join(os.path.dirname(__file__), "locale")
        resultado = []
        try:
            with open(os.path.expanduser("~/.local/share/ovos/idiomas.json"),
                      encoding="utf-8") as fh:
                datos = json.load(fh)
        except Exception:
            datos = {}
        for d in (datos or {}).values():
            if not isinstance(d, dict) or not d.get("ovos"):
                continue
            cod = d["ovos"]
            if _base_lang(cod) == core:
                continue
            if os.path.isdir(os.path.join(raiz, cod, "intent")):
                resultado.append(cod)
        return resultado

    def initialize(self):
        self.log.info(
            "AsistenteLinux: registrando fallback a prioridad 20")
        self.register_fallback(self.handle_system_task, priority=20)
        self._contexto = {}
        self._hablados = deque(maxlen=8)
        self._respuestas_pendientes = {}
        try:
            self.bus.on("ovos.asistente.respuesta", self._on_respuesta_texto)
        except Exception as e:
            self.log.warning(f"AsistenteLinux: no se registro canal de"
                             f" respuesta de texto: {e}")
        self._detectar_pm()
        try:
            self.register_intent_file("mouse.intent", self._on_raton_intent)
        except Exception as e:
            self.log.warning(f"AsistenteLinux: no se registró el intent"
                             f" de ratón: {e}")
        try:
            self.register_intent_file("modelo.intent",
                                      self._on_modelo_intent)
        except Exception as e:
            self.log.warning(f"AsistenteLinux: no se registró el intent"
                             f" de modelo: {e}")
        try:
            self.register_intent_file("modelo_ia.intent",
                                      self._on_modelo_ia_intent)
        except Exception as e:
            self.log.warning(f"AsistenteLinux: no se registró el intent"
                             f" de recomendación: {e}")
        try:
            self.register_intent_file("identity.intent",
                                      self._on_identidad_intent)
        except Exception as e:
            self.log.warning(f"AsistenteLinux: no se registró el intent"
                             f" de identidad: {e}")

    def _fija_lang_salida(self):
        """Deja el idioma de salida = idioma en que respondemos.

        Los dialogos se renderizan y la voz del TTS se eligen segun el
        idioma del mensaje/sesion; aqui lo fijamos al idioma de la
        respuesta para que texto y voz coincidan aunque el usuario hable
        (o el servicio tenga la sesion) en otro idioma. Devuelve el codigo
        OVOS usado."""
        cod = _ovos_para_base(_codigo_idioma())
        if not cod:
            return cod
        msg = dig_for_message()
        if msg is not None:
            try:
                if not isinstance(msg.context, dict):
                    msg.context = {}
                msg.context["lang"] = cod
                ses = msg.context.get("session")
                if isinstance(ses, dict):
                    ses["lang"] = cod
                if isinstance(msg.data, dict):
                    msg.data["lang"] = cod
            except Exception:
                pass
        return cod

    def speak_dialog(self, key, *a, **k):
        self._fija_lang_salida()
        return super().speak_dialog(key, *a, **k)

    def speak(self, utterance, *a, **k):
        cod = self._fija_lang_salida()
        if utterance:
            self._hablados.append((time.time(), self._eco_norm(utterance)))
            try:
                ses = SessionManager.get(dig_for_message())
                if ses.session_id.startswith("chat"):
                    # el TTS solo reproduce la sesion default: (ovos-audio
                    # ignora 'speak' de sesiones 'chat'). Espejamos el texto
                    # sin contexto de sesion para que se oiga por el altavoz
                    # ademas de mostrarse en la ventana de chat. Se incluye
                    # 'lang' para que la voz coincida con el idioma.
                    self.bus.emit(Message("speak", {
                        "utterance": utterance,
                        "lang": cod,
                        "meta": {"skill": self.skill_id},
                    }, {}))
            except Exception as e:
                self.log.debug(f"AsistenteLinux: mirror audio: {e}")
        return super().speak(utterance, *a, **k)

    @staticmethod
    def _eco_norm(txt: str) -> str:
        t = unicodedata.normalize("NFKD", txt.lower())
        t = re.sub(r"\W+", " ", t)
        return re.sub(r"\s+", "", t)

    def _es_eco(self, utt: str) -> bool:
        u = self._eco_norm(utt)
        if not u:
            return False
        ahora = time.time()
        for t, prev in self._hablados:
            if ahora - t > 12:
                continue
            if u == prev or u in prev or prev in u:
                return True
        return False

    @staticmethod
    def _es_cerrar_chat(utt: str) -> bool:
        u = re.sub(r"\W+", " ", utt.lower()).strip()
        patron = _hints_para(_lang_mensaje()).get("cerrar") or \
            r"(cierra|cerrar|cerra).{0,4}(la.\s*)?(ventana|chat|pregunta|preguntas)"
        try:
            return bool(re.search(patron, u))
        except re.error:
            return False

    def _detectar_pm(self) -> None:
        """Detecta el gestor de paquetes de la distro y prepara las
        plantillas de instalar/desinstalar/buscar. MultiopenSUSE -> zypper,
        Debian -> apt, Fedora -> dnf, Arch -> pacman."""
        candidatos = [
            ("zypper",
             "zypper --non-interactive install",
             "zypper --non-interactive remove",
             "zypper --no-refresh search"),
            ("apt",
             "apt-get -y install",
             "apt-get -y remove",
             "apt-cache search"),
            ("dnf",
             "dnf -y install",
             "dnf -y remove",
             "dnf search"),
            ("pacman",
             "pacman -S --noconfirm",
             "pacman -R --noconfirm",
             "pacman -Ss"),
        ]
        self.pm = None
        self.pm_install = "zypper --non-interactive install"
        self.pm_remove = "zypper --non-interactive remove"
        self.pm_search = "zypper --no-refresh search"
        for nombre, inst, rem, bus in candidatos:
            try:
                r = subprocess.run(
                    ["bash", "-c", f"command -v {nombre}"],
                    capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    self.pm = nombre
                    self.pm_install = inst
                    self.pm_remove = rem
                    self.pm_search = bus
                    break
            except Exception:
                continue
        self.log.info(f"AsistenteLinux: gestor de paquetes detectado "
                      f"-> {self.pm}")

    def can_answer(self, message: Message) -> bool:
        return True

    # ------------------------------------------------------------------
    # Handler principal
    # ------------------------------------------------------------------
    def handle_system_task(self, message: Message) -> bool:
        utterances = message.data.get("utterances") or []
        if not utterances:
            return False
        utt = utterances[0]
        self.log.info(f"AsistenteLinux candidata: '{utt}'")

        if self._es_eco(utt):
            self.log.info(f"AsistenteLinux eco ignorado: '{utt}'")
            return True

        if self._es_cerrar_chat(utt):
            self.log.info("AsistenteLinux cerrando ventana de chat")
            try:
                self.bus.emit(Message("ovos.asistente.cierra_chat"))
            except Exception:
                pass
            self.speak_dialog("cerrando_chat")
            return True

        ses = SessionManager.get(message)
        ses_id = ses.session_id

        # Seguimiento: 'esos archivos', 'los nombres'... usan el resultado anterior
        if ses_id in self._contexto and self._es_seguimiento(utt):
            self.log.info(f"AsistenteLinux seguimiento con contexto "
                          f"(ses={ses_id[:8]} comando={self._contexto[ses_id]['comando']})")
            ctx = self._contexto[ses_id]
            comando_c = ctx["comando"].lstrip()
            if re.match(r"^zypper(\s+--no-refresh)?\s+search\b",
                        comando_c) or comando_c.startswith("buscar_paquetes:"):
                r = self._construir_lista_paquetes(ctx["salida"])
                if not r:
                    return False
                resp = f"Encontré {r[1]} paquetes: {r[0]}"
            else:
                resp = self._construir_lista(ctx["salida"], ctx["comando"])
            if resp:
                self.speak(resp)
                return True
            return False

        # Gate rapido: si no parece operacion de sistema, respondemos con el
        # LLM (no hay skill "persona" instalado; si aqui devolvemos False la
        # conversacion se queda sin respuesta).
        if not self._looks_like_system_task(utt):
            self.log.debug("AsistenteLinux: fuera del dominio, respuesta LLM")
            return self._responder_conversacion(utt)

        # Acciones de ratón con frase clara: resolvemos sin LLM.
        raton = self._raton_por_frase(utt)
        if raton:
            self.log.info(f"AsistenteLinux: raton por frase {raton}")
            return self._raton_sobre_elemento(raton[0], raton[1], message,
                                              ses_id)

        # Análisis de imagen con frase clara: resolvemos sin LLM.
        imagen = self._imagen_por_frase(utt)
        if imagen:
            self.log.info(f"AsistenteLinux: imagen por frase -> {imagen}")
            return self._analizar_imagen(imagen, message, ses_id)

        # Abrir terminal/consola con frase clara: resolvemos sin LLM (el
        # modelo a veces lo confundia con abrir_web y abria el navegador).
        terminal = self._terminal_por_frase(utt)
        if terminal is not None:
            self.log.info(f"AsistenteLinux: terminal por frase cmd={terminal!r}")
            return self._abrir_terminal(terminal, message)

        # 1. Decision del LLM (que comando ejecutar / si es del sistema)
        try:
            decision = self._decide(utt)
        except Exception as e:
            self.log.exception(f"AsistenteLinux: fallo en decision: {e}")
            return False

        if not decision:
            self.log.info("AsistenteLinux: LLM dijo noop (no es del sistema)")
            return False

        accion = decision["name"]
        args = decision.get("arguments") or {}

        if accion == "run_shell_command":
            return self._ejecutar_comando(args.get("command", ""), message,
                                          ses_id)
        elif accion == "instalar_paquete":
            return self._instalar_paquete(args.get("paquete", ""), message,
                                          ses_id)
        elif accion == "desinstalar_paquete":
            return self._desinstalar_paquete(args.get("paquete", ""), message,
                                             ses_id)
        elif accion == "buscar_paquetes":
            return self._buscar_por_termino(args.get("termino", ""), message,
                                            ses_id)
        elif accion == "explicar_comando":
            return self._explicar_comando(args.get("comando", ""), message,
                                          ses_id)
        elif accion == "configurar_servicio":
            return self._configurar_servicio(
                args.get("servicio", ""), args.get("accion", ""), message,
                ses_id)
        elif accion == "abrir_terminal":
            return self._abrir_terminal(args.get("command") or "", message)
        elif accion == "redactar_texto":
            return self._redactar_texto(
                args.get("descripcion") or args.get("texto") or "",
                message, ses_id)
        elif accion == "enviar_email":
            return self._enviar_email(
                args.get("destinatario") or "",
                args.get("asunto") or "",
                args.get("texto") or args.get("cuerpo") or "",
                message, ses_id)
        elif accion == "abrir_web":
            return self._abrir_web(
                args.get("sitio") or args.get("url") or "", message, ses_id)
        elif accion == "buscar_internet":
            return self._buscar_internet(
                args.get("consulta") or args.get("query") or "",
                message, ses_id)
        elif accion == "consultar_web":
            return self._consultar_web(
                args.get("pagina") or args.get("url") or "", message, ses_id)
        elif accion == "analizar_imagen":
            return self._analizar_imagen(
                args.get("consulta") or "", message, ses_id)
        elif accion == "cambiar_modelo":
            return self._cambiar_modelo(args.get("modelo") or "", message,
                                        ses_id)
        elif accion == "instalar_modelo":
            return self._instalar_modelo(args.get("modelo") or "", message,
                                         ses_id)
        elif accion == "recomendar_modelo":
            return self._recomendar_modelo(args.get("tarea") or "", message,
                                           ses_id)
        elif accion == "raton_sobre_elemento":
            return self._raton_sobre_elemento(
                args.get("descripcion") or "",
                args.get("accion") or "clic",
                message, ses_id)
        return False

    # ------------------------------------------------------------------
    # Gate rapido
    # ------------------------------------------------------------------
    def _looks_like_system_task(self, utt: str) -> bool:
        utt_l = utt.lower()
        for h in _hints_para(_lang_mensaje()).get("system", []):
            if h in utt_l:
                self.log.debug(f"AsistenteLinux: hint '{h}' en '{utt_l}'")
                return True
        return False

    def _terminal_por_frase(self, utt: str):
        """Si la frase pide abrir un terminal/consola, devuelve el comando
        opcional que debe ejecutarse dentro ('ls -la'), o '' para abrir una
        terminal vacía; None si la frase no pide un terminal."""
        u = (utt or "").lower()
        if not any(w in u for w in _TERMINAL_WORDS):
            return None
        if not _TERMINAL_ABRE_RE.search(u):
            return None
        m = _TERMINAL_CMD_RE.search(utt or "")
        if m:
            return m.group(1).strip(" .?¡!¿")
        return ""

    # ------------------------------------------------------------------
    # Seguimiento de conversacion (contexto de la ultima busqueda)
    # ------------------------------------------------------------------

    def _es_seguimiento(self, utt: str) -> bool:
        utt_l = utt.lower()
        return any(h in utt_l
                   for h in _hints_para(_lang_mensaje()).get("seguimiento", []))

    def _extraer_nombres(self, salida: str, comando: str) -> List[str]:
        lineas = [l.strip() for l in salida.splitlines() if l.strip()]
        vistos = set()
        resultado = []
        es_find = comando.split()[0].split("/")[-1] == "find"
        for l in lineas:
            if re.match(r"^(find:|grep:)\s", l):
                continue
            if re.search(r"denied|denegado|no such|no existe", l,
                         re.I):
                continue
            if re.match(r"^total\s+\d+", l):
                continue
            if es_find and not re.match(r"^[/.]", l):
                continue
            if l not in vistos:
                vistos.add(l)
                resultado.append(l)
        return resultado

    def _construir_lista(self, salida: str, comando: str) -> Optional[str]:
        nombres = self._extraer_nombres(salida, comando)
        if not nombres:
            return None
        total = len(nombres)
        limite = 8
        visibles = [n.split("/")[-1] if n.startswith("/") else n
                    for n in nombres[:limite]]
        texto = ", ".join(visibles)
        if total > limite:
            texto += f", y {total - limite} más"
        prefijo = (f"Encontré {total} archivos: "
                   if comando.split()[0].split("/")[-1] == "find"
                   else f"Son {total}: ")
        return prefijo + texto

    def _listar_paquetes(self, salida: str) -> List[str]:
        """Extrae los nombres de paquete de la tabla de 'zypper search'."""
        vistos = set()
        resultado = []
        for l in salida.splitlines():
            if "|" not in l:
                continue
            # cabecera o separador de la tabla
            if re.match(r"^\s*[S ]\s*\|", l) and "Name" in l:
                continue
            if re.match(r"^\s*[-+]+\s*\|", l):
                continue
            if re.search(r"^[A-Za-z]+\.\w+(\.\w+)?$", l.strip()):
                continue
            partes = [p.strip() for p in l.split("|")]
            if len(partes) < 2:
                continue
            nombre = partes[1]
            if not nombre or "Name" == nombre or not re.match(
                    r"^[A-Za-z0-9][A-Za-z0-9._+:-]*$", nombre):
                continue
            if nombre in vistos:
                continue
            if self._es_paquete_util(nombre):
                vistos.add(nombre)
                resultado.append(nombre)
        return resultado

    def _es_paquete_util(self, nombre: str) -> bool:
        """Filtra dependencias/desarrollo para que la recomendación sea útil."""
        if nombre.startswith("lib"):
            return False
        if re.search(r"(-devel.*|-dev$|-lang$|-doc$|-debug.*|-theme.*)$",
                     nombre):
            return False
        return True

    def _construir_lista_paquetes(
            self, salida: str) -> Optional[tuple]:
        paquetes = self._listar_paquetes(salida)
        if not paquetes:
            return None
        total = len(paquetes)
        limite = 8
        texto = ", ".join(paquetes[:limite])
        if total > limite:
            texto += f", y {total - limite} más"
        return texto, total

    # ------------------------------------------------------------------
    # LLM: decision por function calling
    # ------------------------------------------------------------------
    def _decide(self, utt: str) -> Optional[dict]:
        payload = {
            "model": self._modelo_actual(),
            "stream": False,
            "max_tokens": 256,
            "temperature": 0.2,
            "messages": [
                {"role": "system",
                 "content": SYSTEM_PROMPT + f"\nResponde siempre en "
                                            f"{_idioma_respuesta()}."},
                {"role": "user", "content": utt},
            ],
            "tools": TOOLS,
            "tool_choice": "auto",
        }
        data = self._chat(payload, timeout=120)
        self.log.debug(
            f"AsistenteLinux la llm decidio: {json.dumps(data, ensure_ascii=False)}")
        calls = data.get("tool_calls") or []
        if not calls:
            # algunos modelos devuelven la llamada como JSON en content
            content = data.get("texto") or ""
            llamada = self._parsear_json_de_content(content)
            if llamada:
                name = llamada.get("name")
                if name == "noop":
                    return None
                params = llamada.get("parameters") or llamada.get("arguments") or {}
                return {"name": name, "arguments": params}
            return None
        fn = calls[0].get("function") or {}
        name = fn.get("name")
        raw_args = fn.get("arguments") or "{}"
        try:
            arguments = (json.loads(raw_args)
                         if isinstance(raw_args, str) else raw_args)
        except json.JSONDecodeError:
            arguments = {}
        return {"name": name, "arguments": arguments}

    def _parsear_json_de_content(self, content: str) -> Optional[dict]:
        """Extrae {name, parameters} de content, tolerando comillas internas sin escapar."""
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            return None
        raw = m.group(0)
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("name"):
                return obj
            return None
        except json.JSONDecodeError:
            pass  # JSON malformado: intentar extraer con regex
            name_m = re.search(r'"name"\s*:\s*"([^"]*)"', raw)
            if not name_m:
                return None
        name = name_m.group(1)
        cmd_m = re.search(r'"command"\s*:\s*"(.*)"\s*[,}]', raw, re.S)
        if cmd_m:
            return {"name": name,
                    "parameters": {"command": cmd_m.group(1)}}
        pkg_m = re.search(r'"paquete"\s*:\s*"([^"]*)"', raw)
        if pkg_m:
            return {"name": name,
                    "parameters": {"paquete": pkg_m.group(1)}}
        return {"name": name, "parameters": {}}

    # ------------------------------------------------------------------
    # Ejecucion
    # ------------------------------------------------------------------
    def _ejecutar_comando(self, cmd: str, message: Message,
                          ses_id: str) -> bool:
        cmd = cmd.strip()
        cmd = self._normalizar_find(cmd)
        if not cmd:
            self.speak_dialog("comando_vacio")
            return True
        # El LLM a veces propone 'zypper install X': reconducimos al flujo
        # de instalacion seguro (confirmacion + pkexec)
        m_viejo = INSTALL_CMD_RE.match(cmd)
        if m_viejo:
            return self._instalar_paquete(m_viejo.group(1), message, ses_id)
        if not self._seguro(cmd):
            self.speak_dialog("comando_denegado")
            return True

        # Confirmacion hablada SOLO si el comando modifica el sistema
        # (las busquedas/consultas de solo lectura se ejecutan directo)
        if (self.settings.get("require_confirmation", True)
                and not self._es_solo_lectura(cmd)):
            respuesta = self._preguntar("confirmar_comando",
                                        {"comando": cmd})
            self.log.info(f"AsistenteLinux confirmacion: '{respuesta}'")
            if not self._afirmativo(respuesta):
                self.speak_dialog("accion_cancelada")
                return True

        self.log.info(f"AsistenteLinux ejecutando: {cmd}")
        try:
            shell = os.environ.get("SHELL", "/bin/bash")
            result = subprocess.run(
                [shell, "-c", cmd],
                capture_output=True, text=True,
                timeout=int(self.settings.get("command_timeout", 60)),
                cwd=os.path.expanduser("~"),
                env={**os.environ, "LANG": "es_MX.UTF-8"})
        except subprocess.TimeoutExpired:
            self.speak_dialog("error_comando", {"detalle": "expiró el tiempo"})
            return True
        except Exception as e:
            self.log.exception(f"AsistenteLinux error: {e}")
            self.speak_dialog("error_comando", {"detalle": str(e)})
            return True

        salida = (result.stdout + result.stderr).strip()
        self._contexto[ses_id] = {"comando": cmd, "salida": salida}
        self.log.info(f"AsistenteLinux contexto guardado "
                      f"(ses={ses_id[:8]} comando={cmd})")
        if result.returncode != 0:
            self.log.warning(
                f"AsistenteLinux rc={result.returncode} salida={salida[:500]}")

        # Busquedas de solo lectura: listar los nombres, sin LLM
        if self._es_solo_lectura(cmd):
            prim = cmd.split()[0].split("/")[-1]
            if prim in ("find", "ls"):
                lista = self._construir_lista(salida, cmd)
                if lista:
                    self.speak(lista)
                    return True
            elif prim == "zypper":
                resultado = self._construir_lista_paquetes(salida)
                if resultado:
                    texto, total = resultado
                    self.speak_dialog("paquetes_encontrados", {"total": total})
                    self.speak(texto)
                    return True
                # Sin resultados en zypper: probamos Flatpak con el mismo termino
                m_term = re.search(r"search\s+(\S+)", cmd)
                if m_term:
                    apps = self._flatpak_search(m_term.group(1))
                    if apps:
                        self.speak_dialog("opensuse_flatpak", {"lista": ', '.join(apps[:6])})
                        return True
                self.speak_dialog("busqueda_sin_resultados")
                return True

        resumen = self._resumir(cmd, salida)
        if resumen:
            self.speak(resumen)
        else:
            self.speak_dialog("sin_salida", {"comando": cmd})
        return True

    def _instalar_paquete(self, paquete: str, message: Message,
                          ses_id: str) -> bool:
        paquete = paquete.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:-]*", paquete):
            self.log.warning(
                f"AsistenteLinux: paquete inválido '{paquete}'")
            return False

        # ¿Ya está instalado? => ofrecer abrirlo en vez de reinstalar
        if self._ya_instalado(paquete):
            respuesta = self._preguntar("paquete_instalado",
                                        {"paquete": paquete})
            self.log.info(
                f"AsistenteLinux ya instalado, abrir: '{respuesta}'")
            if self._afirmativo(respuesta):
                return self._abrir_paquete(paquete)
            self.speak_dialog("accion_cancelada")
            return True

        # Confirmacion por voz (la instalacion modifica el sistema)
        if self.settings.get("require_confirmation", True):
            respuesta = self._preguntar("confirmar_instalacion",
                                        {"paquete": paquete})
            self.log.info(f"AsistenteLinux confirmacion instalacion "
                          f"('{respuesta}')")
            if not self._afirmativo(respuesta):
                self.speak_dialog("accion_cancelada")
                return True

        # Avisamos antes de que salga el dialogo polkit de la contrasena
        self.speak_dialog("instalando", {"paquete": paquete})

        # pkexec lanza el prompt de polkit (dialogo grafico de contrasena)
        cmd = f"pkexec {self.pm_install} {paquete}"
        self.log.info(f"AsistenteLinux instalando: {cmd}")
        try:
            shell = os.environ.get("SHELL", "/bin/bash")
            result = subprocess.run(
                [shell, "-c", cmd],
                capture_output=True, text=True,
                timeout=int(self.settings.get("install_timeout", 600)),
                cwd=os.path.expanduser("~"),
                env={**os.environ, "LANG": "es_MX.UTF-8"})
        except subprocess.TimeoutExpired:
            self.speak_dialog("instalacion_fallida",
                              {"paquete": paquete,
                               "detalle": "expiró el tiempo de espera"})
            return True
        except Exception as e:
            self.log.exception("AsistenteLinux: error al instalar")
            self.speak_dialog("instalacion_fallida",
                              {"paquete": paquete, "detalle": str(e)})
            return True

        salida = (result.stdout + result.stderr).strip()
        self._contexto[ses_id] = {"comando": cmd, "salida": salida}
        self.log.info(f"AsistenteLinux contexto guardado "
                      f"(ses={ses_id[:8]} comando={cmd})")
        if result.returncode == 0:
            self.log.info(f"AsistenteLinux instalación OK ({paquete})")
            self.speak_dialog("instalacion_ok", {"paquete": paquete})
        else:
            if self._es_cap_not_found(result.returncode, salida):
                return self._buscar_en_cascada(paquete, message, ses_id)
            detalle = self._razon_instalacion_fallida(
                salida, result.returncode)
            self.log.warning(
                f"AsistenteLinux instalacion rc={result.returncode}: "
                f"{salida[:500]}")
            self.speak_dialog("instalacion_fallida",
                              {"paquete": paquete, "detalle": detalle})
        return True

    def _es_cap_not_found(self, rc: int, salida: str) -> bool:
        if rc == 104:
            return True
        return bool(re.search(r"not found|no se encontr|"
                              r"no packages found|no provider", salida,
                              re.I))

    def _ya_instalado(self, paquete: str) -> bool:
        try:
            r = subprocess.run(
                ["bash", "-c", f"rpm -q {paquete} >/dev/null 2>&1"],
                timeout=20)
            return r.returncode == 0
        except Exception:
            return False

    def _binario_de_paquete(self, paquete: str) -> Optional[str]:
        # 1) binarios que lista el paquete construido (rpm -ql)
        r = None
        try:
            r = subprocess.run(
                ["bash", "-c",
                 f"rpm -ql {paquete} 2>/dev/null | "
                 f"grep -E '/(usr/(s?bin)|local/bin)/'"],
                capture_output=True, text=True, timeout=20)
        except Exception:
            pass
        if r:
            for l in r.stdout.splitlines():
                binario = l.strip().split("/")[-1]
                if not binario:
                    continue
                existe = subprocess.run(
                    ["bash", "-c", f"command -v {binario}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                if existe.returncode == 0:
                    return binario
        # 2) el nombre del paquete suele ser el del binario (p. ej. 'vlc',
        #    cuyas sub-bibliotecas no listan binarios en rpm -ql)
        try:
            r2 = subprocess.run(
                ["bash", "-c", f"command -v {paquete}"],
                capture_output=True, text=True, timeout=5)
        except Exception:
            r2 = None
        if r2 and r2.returncode == 0 and r2.stdout.strip():
            return os.path.basename(r2.stdout.strip().split()[0])
        # 3) entradas .desktop que apunten al paquete
        return self._desktop_binario(paquete)

    def _desktop_binario(self, paquete: str) -> Optional[str]:
        import glob
        paq = re.escape(paquete.lower())
        dirs = ["/usr/share/applications",
                "/usr/local/share/applications",
                os.path.expanduser("~/.local/share/applications")]
        for d in dirs:
            for f in sorted(glob.glob(os.path.join(d, "*.desktop"))):
                try:
                    nombre = ""
                    binario = None
                    with open(f, "r", errors="ignore") as fh:
                        for ln in fh:
                            ln = ln.strip()
                            if ln.lower().startswith("name="):
                                if not nombre:
                                    nombre = ln.split("=", 1)[1].strip().lower()
                            elif ln.lower().startswith("exec="):
                                binario = ln.split("=", 1)[1].strip().split()[0].strip('"\'')
                    b = binario or ""
                    obt = os.path.basename(b)
                    if ((nombre and re.fullmatch(rf"{paq}", nombre)) or
                            re.search(rf"(^|[/ .-]){paq}([ %]|$)", b.lower()) or
                            (obt and obt.lower().startswith(paquete.lower()))):
                        if b and os.path.exists(b):
                            return b
                except Exception:
                    continue
        return None

    def _abrir_paquete(self, paquete: str) -> bool:
        binario = self._binario_de_paquete(paquete)
        if not binario:
            self.speak_dialog("sin_forma_de_abrir", {"paquete": paquete})
            return True
        try:
            subprocess.Popen(
                ["bash", "-c", f"nohup {binario} >/dev/null 2>&1 &"])
            self.speak_dialog("abriendo_paquete", {"paquete": paquete})
        except Exception as e:
            self.log.exception("AsistenteLinux: no se pudo abrir paquete")
            self.speak_dialog("error_comando", {"detalle": str(e)})
        return True

    # ------------------------------------------------------------------
    # Redaccion de textos dictados
    # ------------------------------------------------------------------
    TIPOS_REDACCION = (
        ("currículum", "currículum"), ("curriculum", "currículum"),
        ("felicitación", "felicitación"), ("felicitacion", "felicitación"),
        ("reclamación", "reclamación"), ("reclamacion", "reclamación"),
        ("reclamo", "reclamación"), ("carta", "carta"),
        ("email", "correo electrónico"), ("correo", "correo electrónico"),
        ("mail", "correo electrónico"), ("nota", "nota"),
        ("informe", "informe"), ("solicitud", "solicitud"),
        ("presentación", "presentación"), ("presentacion", "presentación"),
    )

    def _tipo_redaccion(self, desc: str) -> str:
        d = desc.lower()
        for palabra, tipo in self.TIPOS_REDACCION:
            if palabra in d:
                return tipo
        return "texto"

    def _llm_texto(self, prompt: str, max_tokens: int = 512) -> str:
        payload = {
            "model": self._modelo_actual(),
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": 0.6,
            "messages": [
                {"role": "system", "content":
                    f"Eres un asistente que redacta textos claros, bien "
                    f"formados y sencillos, siempre en {_idioma_respuesta()}, "
                    "para personas que empiezan. "
                    "Devuelve solo el texto pedido, sin comentarios ni "
                    "títulos ni presentación."},
                {"role": "user", "content": prompt},
            ],
        }
        data = self._chat(payload, timeout=120)
        return (data.get("texto") or "").strip()

    def _responder_conversacion(self, utt: str) -> bool:
        """Respuesta amable con el LLM para frases fuera del dominio.

        Se usa como ultimo recurso porque el sistema no recoge ninguna otra
        skill de conversacion: si aqui no contestamos, la frase se pierde.
        """
        try:
            data = self._chat(
                {
                    "model": self._modelo_actual(),
                    "stream": False,
                    "max_tokens": 160,
                    "temperature": 0.7,
                    "messages": [
                        {"role": "system", "content":
                            f"Debes contestar SIEMPRE en "
                            f"{_idioma_respuesta()}, aunque el usuario te "
                            "escriba en otro idioma. Eso es obligatorio. "
                            "Eres el asistente de voz 'OVOS' de esta "
                            "computadora Linux. Respondes de forma breve y "
                            "cercana, siempre en "
                            f"{_idioma_respuesta()}. Cubres charla informal "
                            "y preguntas de informacion general. No inventes "
                            "comandos ni acciones. Maximo 3 frases, sin "
                            "títulos."},
                        {"role": "user", "content": utt},
                    ],
                },
                timeout=60)
            texto = (data.get("texto") or "").strip()
        except Exception as e:
            self.log.warning(f"AsistenteLinux: fallo LLM conversacion: {e}")
            texto = ""
        if not texto:
            self.speak_dialog("no_entendi")
            return True
        self.speak(texto)
        return True

    @staticmethod
    def _slug(desc: str, maxlen: int = 42) -> str:
        s = unicodedata.normalize("NFKD", desc.lower())
        s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s[:maxlen] or "borrador"

    def _guardar_borrador(self, desc: str, texto: str) -> str:
        home = os.path.expanduser("~")
        base = os.path.join(home, "Documentos")
        if not os.path.isdir(base):
            base = home
        ruta = os.path.join(base, f"borrador-{self._slug(desc)}.txt")
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write(texto)
        return ruta

    def _abrir_editor(self, ruta: str) -> str:
        editor = os.path.expanduser(self.settings.get("editor", "kate"))
        try:
            r = subprocess.run(["bash", "-c", f"command -v {editor}"]
                               if re.match(r"^\S+$", editor) else ["true"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                subprocess.Popen([r.stdout.strip(), ruta])
                return editor
        except Exception:
            pass
        try:
            subprocess.Popen(["xdg-open", ruta])
            return "tu editor de texto"
        except Exception:
            return ""

    def _clipboard_put(self, texto: str) -> None:
        for copiador in ("wl-copy", "xclip"):
            try:
                r = subprocess.run(["bash", "-c", f"command -v {copiador}"],
                                   capture_output=True, text=True, timeout=3)
                if r.returncode != 0 or not r.stdout.strip():
                    continue
                if copiador == "xclip":
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=texto, text=True, timeout=5)
                else:
                    subprocess.run(["wl-copy"], input=texto, text=True,
                                   timeout=5)
                return
            except Exception:
                continue

    def _redactar_texto(self, descripcion: str, message: Message,
                        ses_id: str) -> bool:
        desc = (descripcion or "").strip()
        if not desc:
            self.speak_dialog("redactar_que")
            return True
        tipo = self._tipo_redaccion(desc)

        para = None
        # no insistimos si ya lo ha dicho ("para mi hermana", "al jefe"...)
        if not re.search(r"\b(para|a|dirigid[ao]|adresada)\b", desc.lower()):
            para = self._preguntar("redaccion_para_quien",
                                   {"tipo": tipo})
            if para is None:
                self.speak_dialog("accion_cancelada")
                return True
            if re.match(r"^(no\b|ninguno|nada|no se|no sé)", para.strip()):
                para = None

        detalles = self._preguntar("redaccion_detalles", {"tipo": tipo})
        if detalles is None:
            self.speak_dialog("accion_cancelada")
            return True
        if re.match(r"^(no\b|ninguno|nada|nada más|nada mas|sin más)",
                    detalles.strip().lower()):
            detalles = None

        prompt = (f"Redacta {desc}.\n")
        if para:
            prompt += f"Está dirigido a: {para}.\n"
        if detalles:
            prompt += f"Detalles que debes incluir: {detalles}.\n"
        prompt += (f"Escribe el {tipo} completo en {_idioma_respuesta()}, "
                   "en primera persona, bien redactado y listo para usar, "
                   "con tono natural.")
        try:
            self.speak_dialog("redaccion_preparando", {"tipo": tipo})
            texto = self._llm_texto(prompt)
        except Exception as e:
            self.log.exception("AsistenteLinux: fallo al redactar")
            self.speak_dialog("redaccion_fallo", {"detalle": str(e)[:80]})
            return True
        if not texto:
            self.speak_dialog("redaccion_fallo",
                              {"detalle": "el asistente no devolvió texto"})
            return True
        try:
            ruta = self._guardar_borrador(desc, texto)
        except Exception as e:
            ruta = ""
            self.log.warning(f"AsistenteLinux: no pude guardar borrador: {e}")
        aplicacion = self._abrir_editor(ruta) if ruta else ""
        self._clipboard_put(texto)
        self.speak_dialog("redaccion_lista",
                          {"tipo": tipo,
                           "aplicacion": aplicacion or "el editor"})
        return True

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    def _enviar_email(self, pa_para: str, asunto: str, cuerpo: str,
                      message: Message, ses_id: str) -> bool:
        para = (pa_para or "").strip()
        if not para:
            para = self._preguntar("email_para_quien")
            if not para:
                self.speak_dialog("accion_cancelada")
                return True
        if not re.match(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", para):
            para = para.replace(" ", "").replace("arroba", "@").strip()
        asunto = (asunto or "").strip()
        if not asunto:
            asunto = self._preguntar("email_asunto")
            if not asunto:
                self.speak_dialog("accion_cancelada")
                return True
        cuerpo = (cuerpo or "").strip()
        if not cuerpo:
            cuerpo = self._preguntar("email_cuerpo")
            if not cuerpo:
                self.speak_dialog("accion_cancelada")
                return True

        smtp = self.settings.get("email", {}) or {}
        if smtp.get("smtp_host"):
            ok = self._preguntar("confirmar_email",
                                 {"destinatario": para, "asunto": asunto})
            if not self._afirmativo(ok):
                self.speak_dialog("accion_cancelada")
                return True
            self.speak_dialog("email_enviando")
            error = self._enviar_smtp(smtp, para, asunto, cuerpo)
            if error is None:
                self.speak_dialog("email_enviado",
                                  {"destinatario": para, "asunto": asunto})
            else:
                self.speak_dialog("email_fallo", {"detalle": error})
            return True

        # sin SMTP configurado: abrimos el cliente de correo con todo
        # preparado (mailto:) para que lo revise y lo envie
        try:
            qs = urllib.parse.urlencode(
                {"subject": asunto, "body": cuerpo})
            subprocess.Popen(["xdg-open", f"mailto:{para}?{qs}"])
            self.speak_dialog("email_abierto",
                              {"destinatario": para, "asunto": asunto})
        except Exception as e:
            self.speak_dialog("email_fallo", {"detalle": str(e)})
        return True

    def _enviar_smtp(self, smtp: dict, para: str, asunto: str,
                     cuerpo: str) -> Optional[str]:
        host = str(smtp.get("smtp_host") or "")
        user = str(smtp.get("smtp_user") or "")
        clave = str(smtp.get("smtp_password") or "")
        puerto = int(smtp.get("smtp_port") or 587)
        remite = str(smtp.get("remitente") or user)
        if not (host and user and clave):
            return "falta configuración SMTP"
        msg = (f"From: {remite}\nTo: {para}\n"
               f"Subject: {asunto}\n\n{cuerpo}")
        try:
            if puerto == 465:
                serv = smtplib.SMTP_SSL(host, puerto, timeout=30)
            else:
                serv = smtplib.SMTP(host, puerto, timeout=30)
                serv.starttls()
            serv.login(user, clave)
            serv.sendmail(remite, [para], msg.encode("utf-8"))
            serv.quit()
            return True
        except Exception as e:
            self.log.exception("AsistenteLinux: fallo SMTP")
            return str(e)[:120]

    # ------------------------------------------------------------------
    # Internet
    # ------------------------------------------------------------------
    @staticmethod
    def _normalizar_url(pagina: str) -> str:
        p = (pagina or "").strip()
        if not p:
            return ""
        pl = p.lower()
        if re.match(r"^https?://", pl):
            return p
        if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+([/?#].*)?$", pl):
            return "https://" + p
        return ""

    def _abrir_web(self, sitio: str, message: Message, ses_id: str) -> bool:
        sitio = (sitio or "").strip()
        if not sitio:
            self.speak_dialog("web_que_abrir")
            return True
        key = re.sub(r"\b(la|el|web)\b", "", sitio.lower()).strip()
        key = re.sub(r"\s+", " ", key).strip()
        if key in SITIOS_CONOCIDOS:
            url = SITIOS_CONOCIDOS[key]
        elif key in SERVICIOS_IA:
            url = SERVICIOS_IA[key][1]
        else:
            url = self._normalizar_url(sitio)
            if not url:
                url = ("https://duckduckgo.com/?q=" +
                       urllib.parse.quote(sitio))
        try:
            subprocess.Popen(["xdg-open", url])
            self.speak_dialog("web_abriendo", {"sitio": sitio})
        except Exception as e:
            self.speak_dialog("error_comando", {"detalle": str(e)})
        return True

    def _buscar_internet(self, consulta: str, message: Message,
                         ses_id: str) -> bool:
        q = (consulta or "").strip()
        if not q:
            self.speak_dialog("web_que_buscar")
            return True
        url = ("https://duckduckgo.com/?q=" + urllib.parse.quote(q))
        try:
            subprocess.Popen(["xdg-open", url])
            self.speak_dialog("web_buscando", {"consulta": q})
        except Exception as e:
            self.speak_dialog("error_comando", {"detalle": str(e)})
        return True

    def _on_modelo_intent(self, message: Message) -> bool:
        """Intent 'modelo.intent': cambiar o listar el modelo de respuesta."""
        utt = (message.data.get("utterance") or "").strip()
        pedido = str(message.data.get("modelo") or "").strip(" .?¡!¿")
        if not pedido:
            # "cambia de modelo" y "que modelos tienes" llegan sin slot:
            # cojo lo que va detras de la palabra 'modelo'.
            m = re.search(r"modelo(?:s)?\s*(?:a|por|:)?\s*(.+)$", utt, re.I)
            resto = (m.group(1) if m else "").strip(" .?¡!¿")
            if resto and not re.match(
                    r"(que|cual|listame|lista|muestra|muestrame|dime|"
                    r"disponibles|tienes|tenemos|hay|alguno|algun|cambia|"
                    r"cambiar|usa|usar|pon|poner|activa|activar|el|la)\b",
                    resto, re.I):
                pedido = resto
        return self._cambiar_modelo(pedido, message,
                                    SessionManager.get(message).session_id)

    def _on_modelo_ia_intent(self, message: Message) -> bool:
        """Intent 'modelo_ia.intent': que modelo me conviene para X."""
        utt = (message.data.get("utterance") or "").strip()
        tarea = str(message.data.get("tarea") or "").strip(" .?¡!¿")
        if not tarea:
            tarea = re.sub(r"^.*?\bmodelo\s+(?:para\s+)?", "", utt,
                           flags=re.I).strip(" .?¡!¿")
        return self._recomendar_modelo(tarea, message,
                                       SessionManager.get(message).session_id)

    # ------------------------------------------------------------------
    # Chat: local (ollama) o remoto (API de pago)
    # ------------------------------------------------------------------
    def _clave_api(self, proveedor: str) -> str:
        """Clave del proveedor, leida del archivo privado de claves."""
        try:
            with open(CLAVES_PATH, encoding="utf-8") as fh:
                return (json.load(fh) or {}).get(proveedor) or ""
        except Exception:
            return ""

    def _guardar_clave_api(self, proveedor: str, clave: str) -> bool:
        """Guarda la clave en el archivo privado (0600)."""
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
            fd = os.open(CLAVES_PATH,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(datos, fh, indent=2, ensure_ascii=False)
            os.chmod(CLAVES_PATH, 0o600)
            return True
        except Exception as e:
            self.log.error(f"AsistenteLinux: no pude guardar la clave: {e}")
            return False

    def _es_remoto(self) -> bool:
        return (self._leer_cfg().get("modelo") or {}).get("tipo") == "remoto"

    def _chat(self, payload: dict, timeout: int = 120) -> dict:
        """Pide una completacion y la devuelve SIEMPRE con la misma forma.

        Acepta un payload estilo OpenAI (es lo que ya construye el resto del
        skill) y devuelve {'texto': str, 'tool_calls': [...]}, translateslo
        si el proveedor activo es remoto y habla otro dialecto.
        """
        if not self._es_remoto():
            resp = requests.post(self._url_chat(), json=payload,
                                 timeout=timeout)
            resp.raise_for_status()
            msg = ((resp.json().get("choices") or [{}])[0].get("message")
                   or {})
            return {"texto": msg.get("content") or "",
                    "tool_calls": msg.get("tool_calls") or []}
        return self._chat_remoto(payload, timeout)

    def _chat_remoto(self, payload: dict, timeout: int) -> dict:
        cfg = self._leer_cfg().get("modelo") or {}
        nombre_prov = cfg.get("proveedor") or ""
        prov = PROVEEDORES.get(nombre_prov)
        if prov is None:
            raise RuntimeError(f"proveedor remoto desconocido: "
                               f"{nombre_prov!r}")
        modelo = cfg.get("remoto") or ""
        if not modelo:
            raise RuntimeError(f"no hay ningun modelo remoto configurado "
                               f"para {prov['nombre']}")
        clave = self._clave_api(nombre_prov)
        if not clave:
            raise RuntimeError(f"falta la clave de {prov['nombre']}: "
                               f"escribela en Configuración…")
        mensajes = payload.get("messages") or []
        if prov["estilo"] == "anthropic":
            return self._chat_anthropic(prov, modelo, clave, mensajes,
                                        payload.get("tools"),
                                        cfg.get("url") or "")
        return self._chat_openai_remoto(prov, modelo, clave, payload,
                                        timeout, cfg)

    def _chat_anthropic(self, prov: dict, modelo: str, clave: str,
                        mensajes: list, tools: Optional[list],
                        url: str = "") -> dict:
        sistema = "\n\n".join(str(m.get("content") or "")
                              for m in mensajes if m.get("role") == "system")
        chat = [{"role": m.get("role"), "content": m.get("content")}
                for m in mensajes if m.get("role") != "system"]
        cuerpo = {
            "model": modelo,
            "max_tokens": 1024,
            "messages": chat or [{"role": "user", "content": "hola"}],
        }
        if sistema:
            cuerpo["system"] = sistema
        if tools:
            cuerpo["tools"] = [
                {"name": (t.get("function") or {}).get("name"),
                 "description": (t.get("function") or {}).get("description", ""),
                 "input_schema": (t.get("function") or {}).get("parameters")
                 or {"type": "object", "properties": {}}}
                for t in tools]
            cuerpo["tool_choice"] = {"type": "auto"}
        # OJO: los Claude de 4.6 en adelante devuelven HTTP 400 si les
        # mandas temperature/top_p/top_k distintos del valor por defecto,
        # asi que aqui no se envia ninguno de los tres.
        resp = requests.post(
            url or prov["chat"],
            headers={"x-api-key": clave,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=cuerpo, timeout=120)
        resp.raise_for_status()
        datos = resp.json()
        bloques = datos.get("content") or []
        texto = "".join(b.get("text") or "" for b in bloques
                        if b.get("type") == "text")
        llamadas = []
        for b in bloques:
            if b.get("type") != "tool_use":
                continue
            llamadas.append({
                "id": b.get("id"), "type": "function",
                "function": {"name": b.get("name"),
                             "arguments": json.dumps(b.get("input") or {})},
            })
        return {"texto": texto, "tool_calls": llamadas}

    def _chat_openai_remoto(self, prov: dict, modelo: str, clave: str,
                            payload: dict, timeout: int,
                            cfg: dict) -> dict:
        cuerpo = dict(payload)
        cuerpo["model"] = modelo
        # keep_alive y stream son cosas de ollama, no de una API
        cuerpo.pop("keep_alive", None)
        cuerpo.pop("stream", None)
        url = cfg.get("url") or prov["chat"]
        if not url:
            raise RuntimeError("falta la URL del servidor remoto")
        cabeceras = {"Authorization": f"Bearer {clave}",
                     "Content-Type": "application/json"}
        cabeceras.update(prov.get("cabeceras") or {})
        resp = requests.post(url, headers=cabeceras, json=cuerpo,
                             timeout=timeout)
        resp.raise_for_status()
        msg = ((resp.json().get("choices") or [{}])[0].get("message") or {})
        return {"texto": msg.get("content") or "",
                "tool_calls": msg.get("tool_calls") or []}

    def _probar_remoto(self, proveedor: str, modelo: str,
                       clave: str, url: str = "") -> tuple:
        """Comprueba clave+modelo con una peticion minima.

        Devuelve (ok, mensaje). El mensaje de error lleva el texto que
        devuelve el proveedor, que suele decir si es la clave o el modelo.
        """
        prov = PROVEEDORES.get(proveedor)
        if prov is None:
            return False, f"proveedor desconocido: {proveedor!r}"
        if not clave:
            return False, "falta la clave de API"
        destino = url or prov["chat"]
        if not destino:
            return False, "falta la URL del endpoint"
        if prov["estilo"] == "anthropic":
            cuerpo = {"model": modelo, "max_tokens": 4,
                      "messages": [{"role": "user", "content": "di OK"}]}
            cabeceras = {"x-api-key": clave,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"}
        else:
            cuerpo = {"model": modelo, "max_tokens": 4,
                      "messages": [{"role": "user", "content": "di OK"}]}
            cabeceras = {"Authorization": f"Bearer {clave}",
                         "Content-Type": "application/json"}
        try:
            r = requests.post(destino, headers=cabeceras, json=cuerpo,
                              timeout=30)
        except Exception as e:
            return False, f"no se pudo contactar: {e}"
        if r.status_code < 400:
            return True, f"{prov['nombre']} responde correctamente."
        detalle = ""
        try:
            datos = r.json()
            err = datos.get("error")
            if isinstance(err, dict):
                detalle = err.get("message") or ""
            elif isinstance(err, str):
                detalle = err
            detalle = detalle or datos.get("message") or ""
        except Exception:
            detalle = (r.text or "")[:200]
        if r.status_code in (401, 403):
            return False, "clave rechazada. " + (detalle or "")
        if r.status_code == 404:
            return False, (f"modelo o URL no encontrado. {detalle}").strip()
        if r.status_code == 400:
            return False, (f"petición rechazada (revisa el identificador "
                           f"del modelo). {detalle}").strip()
        return False, f"HTTP {r.status_code}. {detalle}"[:250]

    # ------------------------------------------------------------------
    # Multimodelo
    # ------------------------------------------------------------------
    @staticmethod
    def _es_embedding(nombre: str) -> bool:
        """Los modelos de embeddings no conversan: no sirven para responder."""
        return bool(re.search(r"embed|minilm|nomic|clip|bge-|e5-|rerank",
                              nombre or "", re.I))

    def _leer_cfg(self) -> dict:
        """Config del usuario compartida con el GUI y la ventana de chat."""
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                datos = json.load(fh)
            return datos if isinstance(datos, dict) else {}
        except Exception:
            return {}

    def _guardar_cfg(self, cfg: dict) -> bool:
        """Escribe la config del usuario de forma atomica."""
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_PATH)
            return True
        except Exception as e:
            self.log.error(f"AsistenteLinux: no pude guardar la config: {e}")
            return False

    def _modelo_actual(self) -> str:
        """Modelo con el que respondo ahora (config > settings > defecto)."""
        cfg = self._leer_cfg().get("modelo") or {}
        if cfg.get("tipo") == "remoto":
            prov = PROVEEDORES.get(cfg.get("proveedor") or "")
            nombre = (prov or {}).get("nombre") or cfg.get("proveedor") or "?"
            return f"{cfg.get('remoto') or 'sin modelo'} ({nombre}, remoto)"
        return cfg.get("actual") or self.settings.get("model") or MODEL

    def _url_chat(self) -> str:
        """Endpoint de chat que corresponde al modelo activo."""
        return ((self._leer_cfg().get("modelo") or {}).get("url")
                or self.settings.get("ollama_url") or OLLAMA_URL)

    def _modelos_instalados(self) -> list:
        """[(puerto, modelo)] de todos los pools, sin los de embeddings."""
        lista = []
        for puerto in OLLAMA_POOLS:
            try:
                r = requests.get(f"http://127.0.0.1:{puerto}/api/tags",
                                 timeout=4)
                r.raise_for_status()
            except Exception as e:
                self.log.debug(f"AsistenteLinux: ollama {puerto} no "
                               f"responde: {e}")
                continue
            for m in (r.json().get("models") or []):
                nombre = m.get("name") or ""
                if nombre and not self._es_embedding(nombre):
                    lista.append((puerto, nombre))
        return lista

    @staticmethod
    def _afinidad(consulta: str, modelo: str) -> float:
        """Cuanto encaja lo que dijo el usuario con un nombre de modelo."""
        piezas = [p for p in re.split(r"[^a-z0-9]+",
                                      (consulta or "").lower()) if len(p) >= 3]
        if not piezas:
            return 0.0
        plano = re.sub(r"[^a-z0-9]+", "", (modelo or "").lower())
        return sum(1 for p in piezas if p in plano) / len(piezas)

    def _resolver_modelo(self, nombre: str):
        """Mejor modelo instalado que encaja con lo que dijo el usuario.

        Devuelve (modelo, puerto) o (None, None) si no hay ninguno.
        """
        if not (nombre or "").strip():
            return None, None
        mejor, mejor_punt = None, 0.0
        for puerto, modelo in self._modelos_instalados():
            punt = self._afinidad(nombre, modelo)
            # el modelo de vision no es la mejor apuesta para responder
            if modelo == VISION_MODEL:
                punt -= 0.01
            if punt > mejor_punt:
                mejor, mejor_punt = (modelo, puerto), punt
        if mejor and mejor_punt >= 0.9:
            return mejor
        return None, None

    def _listar_modelos(self) -> bool:
        lista = self._modelos_instalados()
        if not lista:
            self.speak_dialog("ollama_sin_modelos")
            return True
        actual = self._modelo_actual()
        etiqueta = _hints_para(_lang_mensaje()).get("activo", "activo")
        partes = [f"{m}{' (' + etiqueta + ')' if m == actual else ''}"
                  for _, m in lista]
        self.speak_dialog("modelos_lista", {"lista": ", ".join(partes)})
        return True

    def _cambiar_modelo(self, nombre: str, message: Message,
                        ses_id: str) -> bool:
        # cambiar de modelo a pelo esta pensado para los modelos locales de
        # ollama. Los remotos se cambian en Configuración… porque llevan
        # clave, proveedor y URL: no es cosa de decirselo de viva voz.
        # Si venias de uno remoto, esto es justo tu salida de ahi.
        nombre = (nombre or "").strip()
        # "que modelos puedo usar para programar" puede llegar aqui con la
        # tarea en vez del modelo. Si huele a tarea, que recomiende.
        if _parece_tarea(nombre):
            return self._recomendar_modelo(nombre, message, ses_id)
        if self._es_remoto() and not nombre:
            self.speak_dialog("modelo_remoto", {"modelo": self._modelo_actual()})
            return True
        if not nombre:
            return self._listar_modelos()
        modelo, puerto = self._resolver_modelo(nombre)
        if not modelo:
            self.speak_dialog("modelo_no_parecido", {"nombre": nombre})
            return self._listar_modelos()

        cfg = self._leer_cfg()
        previo = cfg.get("modelo") or {}
        url = f"http://127.0.0.1:{puerto}/v1/chat/completions"
        cfg["modelo"] = {"tipo": "local", "actual": modelo, "url": url,
                         "puerto": puerto}
        if not self._guardar_cfg(cfg):
            self.speak_dialog("modelo_no_guardado")
            return True

        # Lo pruebo antes de darlo por bueno: si el modelo nuevo no
        # responde, vuelvo a dejar el anterior en su sitio.
        try:
            r = requests.post(url, json={
                "model": modelo, "stream": False, "max_tokens": 8,
                "temperature": 0, "keep_alive": "30m",
                "messages": [{"role": "user", "content": "di OK"}],
            }, timeout=300)
            responde = r.status_code < 400
        except Exception as e:
            responde = False
            self.log.warning(f"AsistenteLinux: {modelo} no responde: {e}")

        if not responde:
            cfg = self._leer_cfg()
            if previo:
                cfg["modelo"] = previo
            else:
                cfg.pop("modelo", None)
            self._guardar_cfg(cfg)
            self.speak_dialog("modelo_no_responde", {"modelo": modelo, "actual": self._modelo_actual()})
            return True

        self.log.info(f"AsistenteLinux: modelo de respuesta ahora {modelo} "
                      f"(puerto {puerto})")
        self.speak_dialog("modelo_cambiado", {"modelo": modelo})
        return True

    @staticmethod
    def _tamano_descarga(modelo: str) -> Optional[int]:
        """Bytes que ocupara 'modelo' al descargarse, segun el registro de
        ollama. None si el registro no lo conoce (p. ej. si no hay red)."""
        nombre = (modelo or "").strip()
        if ":" not in nombre:
            nombre = f"{nombre}:latest"
        repo, _, tag = nombre.partition(":")
        # 'biblioteca/modelo' es el nombre oficial; 'modelo' se asume official
        ruta = repo if "/" in repo else f"library/{repo}"
        try:
            r = requests.get(
                f"https://registry.ollama.ai/v2/{ruta}/manifests/{tag}",
                headers={"Accept": "application/vnd.docker.distribution."
                                    "manifest.v2+json"},
                timeout=15)
            r.raise_for_status()
            capas = r.json().get("layers") or []
            total = sum(c.get("size") or 0 for c in capas)
            return total or None
        except Exception:
            return None

    def _instalar_modelo(self, nombre: str, message: Message,
                         ses_id: str) -> bool:
        nombre = (nombre or "").strip()
        if not nombre:
            self.speak_dialog("modelo_cual_instalar")
            return True
        baja = nombre.lower()
        # un CLI/GUI conocido tiene su propio instalador. Ojo: busco con
        # limites de palabra, que si no 'code' aparece dentro de 'coder'
        # y 'instala qwen coder' acababa instalando VS Code.
        for clave, (etiqueta, binario, cmd) in CLI_IA.items():
            if not re.search(rf"\b{re.escape(clave)}\b", baja):
                continue
            if shutil.which(binario):
                self.speak_dialog("modelo_ya_instalado", {"etiqueta": etiqueta, "binario": binario})
                return True
            self.speak_dialog("modelo_no_privileged", {"etiqueta": etiqueta})
            return self._ejecutar_comando(cmd, message, ses_id)
        candidatos = [m for m, _ in MODELOS_INSTALABLES]
        mejor, punt = None, 0.0
        for modelo in candidatos:
            a = self._afinidad(nombre, modelo)
            if a > punt:
                mejor, punt = modelo, a
        if not mejor or punt < 0.9:
            # no es un modelo: puede ser una necesidad ("para imagenes"),
            # y eso lo sabe mejor _recomendar_modelo
            return self._recomendar_modelo(nombre, message, ses_id)
        ya_esta = mejor in [m for _, m in self._modelos_instalados()]
        if ya_esta:
            return self._cambiar_modelo(mejor, message, ses_id)
        # se dice cuanto va a ocupar ANTES de descargar nada
        bytes_descarga = self._tamano_descarga(mejor)
        if bytes_descarga:
            gb = bytes_descarga / 1e9
            self.speak_dialog("modelo_espacio", {"mejor": mejor, "gb": f'{gb:.1f}'})
        # descargar varios GB modifica el sistema: se pide confirmacion
        if self.settings.get("require_confirmation", True):
            respuesta = self._preguntar("confirmar_instalacion",
                                        {"paquete": f"el modelo {mejor}"})
            self.log.info(f"AsistenteLinux confirmacion instalacion de modelo"
                          f" ('{respuesta}')")
            if not self._afirmativo(respuesta):
                self.speak_dialog("accion_cancelada")
                return True
        self.speak_dialog("modelo_descargando", {"mejor": mejor})
        try:
            subprocess.run(
                ["ollama", "pull", mejor],
                env={**os.environ,
                     "OLLAMA_HOST": f"http://127.0.0.1:{POOL_INSTALACION}"},
                capture_output=True, timeout=3600, text=True)
        except Exception as e:
            self.speak_dialog("modelo_descarga_fallo", {"mejor": mejor, "detalle": e})
            return True
        return self._cambiar_modelo(mejor, message, ses_id)

    def _recomendar_modelo(self, tarea: str, message: Message,
                           ses_id: str) -> bool:
        tarea = (tarea or "").strip()
        if not tarea:
            self.speak_dialog("modelo_tipo_tarea")
            return True
        baja = tarea.lower()
        esp = next((e for e in ESPECIALIDADES
                    if any(c in baja for c in e["claves"])), None)
        if not esp:
            self.speak_dialog("modelo_tarea_sin_reco", {"tarea": tarea})
            return True
        instalados = [m for _, m in self._modelos_instalados()]
        locales = [m for m in esp["ollama"] if m in instalados]
        faltan = [m for m in esp["ollama"] if m not in instalados]
        # (clave, nombre visible): la clave es la que entiende "abre X"
        web = [(k, SERVICIOS_IA[k][0]) for k in esp["servicios"][:3]
               if k in SERVICIOS_IA]
        partes = []
        if locales:
            partes.append(
                f"en local ya tienes {locales[0]}, dime 'usa el modelo "
                f"{locales[0].split(':')[0]}' y lo activo")
        elif faltan:
            partes.append(
                f"puedo descargarte {faltan[0]} con ollama si me lo dices")
        if web:
            partes.append(
                f"o en el navegador abro "
                f"{', '.join(n for _, n in web)} con 'abre {web[0][0]}'")
        self.speak(f"Para {esp['nota']}: " + "; ".join(partes) + ".")
        return True

    def _wikipedia_article(self, consulta: str) -> Optional[str]:
        q = (consulta or "").strip()
        if not q:
            return None
        try:
            r = requests.get(
                "https://es.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": q,
                        "format": "json", "limit": 1},
                headers={"User-Agent":
                         "AsistenteLinuxSkill/1.0 (asistente@local)"},
                timeout=15)
            r.raise_for_status()
            data = r.json()
            # opensearch devuelve [consulta, [titulos], [descr], [urls]]
            titulos = data[1] if (isinstance(data, list)
                                  and len(data) > 1) else []
        except Exception as e:
            self.log.warning(f"AsistenteLinux wiki opensearch fallo: {e}")
            return None
        if titulos:
            return ("https://es.wikipedia.org/wiki/"
                    + urllib.parse.quote(str(titulos[0]).replace(" ", "_")))
        # opensearch no devuelve nada: no inventamos una URL a partir del
        # texto (daria 404). Mejor no consultar que decir un 404 en voz alta.
        return None

    def _consultar_web(self, pagina: str, message: Message,
                       ses_id: str) -> bool:
        pagina = (pagina or "").strip()
        self.log.info(f"AsistenteLinux consultar_web pagina={pagina!r}")
        if not pagina:
            self.speak_dialog("web_que_consultar")
            return True
        key = re.sub(r"\b(la |el |web de |página de |pagina de )",
                     "", pagina.lower()).strip()
        if "wikipedia" in pagina.lower():
            q = re.sub(r"\b(wikipedia|wiki|sobre|de)\b|¿|\?|,", " ",
                       key).strip()
            q = re.sub(r"^((qué|que)\s+)?(dice|dicen|diga|mira|"
                       r"consulta|consulto|resume)\s+", "", q, flags=re.I)
            q = re.sub(r"\s+", " ", q).strip(" .,¿?;:")
            url = self._wikipedia_article(q or key)
        else:
            url = self._normalizar_url(key) or SITIOS_CONOCIDOS.get(key)
        if not url:
            self.speak_dialog("web_consulta_fallo",
                              {"pagina": pagina,
                               "detalle": "no encontré esa página"})
            return True
        try:
            resp = requests.get(url, timeout=20, headers={
                "User-Agent":
                    "AsistenteLinuxSkill/1.0 (asistente@local)"})
            resp.raise_for_status()
            html = resp.text
            html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html,
                          flags=re.I)
            texto = re.sub(r"<[^>]+>", " ", html)
            texto = re.sub(r"\s+", " ", texto).strip()
            texto = texto[:4000]
        except Exception as e:
            self.log.warning(f"AsistenteLinux: web no consultable: {e}")
            self.speak_dialog("web_consulta_fallo",
                              {"pagina": pagina, "detalle": str(e)[:80]})
            return True
        try:
            resumen = self._llm_texto(
                f"Resume en 3-4 frases, en {_idioma_respuesta()}, "
                f"qué contiene esta página "
                f"web ({pagina}):\n{texto}", max_tokens=250)
        except Exception:
            resumen = texto[:300]
        if not resumen.strip():
            resumen = texto[:300]
        self.speak(resumen[:400])
        return True

    # ------------------------------------------------------------------
    # Interaccion con el ratón (ver la pantalla y situarlo/clic en un
    # elemento visible). Vision local via Ollama + xdotool sobre XWayland.
    # ------------------------------------------------------------------
    _x_env = None

    @classmethod
    def _x_candidato(cls) -> dict:
        """Devuelve el entorno X (DISPLAY+XAUTHORITY) con el que xdotool
        puede conectar de verdad, probando combinaciones hasta dar con una
        que responda. Cachea el resultado."""
        if cls._x_env is not None:
            return cls._x_env
        home = os.path.expanduser("~")
        xauths = []
        for p in (os.environ.get("XAUTHORITY"), os.path.join(home, ".Xauthority")):
            if p and os.path.exists(p) and os.access(p, os.R_OK):
                xauths.append(p)
        try:
            for p in sorted(glob.glob("/run/user/*/xauth_*")):
                if os.path.exists(p) and os.access(p, os.R_OK):
                    xauths.append(p)
        except Exception:
            pass
        xauths = list(dict.fromkeys(xauths))
        displays = list(dict.fromkeys(
            [d for d in (os.environ.get("DISPLAY"), ":1", ":0") if d]))
        cls._x_env = {}
        for disp in displays:
            for xa in xauths:
                env = dict(os.environ, DISPLAY=disp, XAUTHORITY=xa)
                try:
                    t = subprocess.run(["xdotool", "getmouselocation"],
                                       capture_output=True, text=True, env=env,
                                       timeout=12)
                except Exception:
                    continue
                if t.returncode == 0 and t.stdout.strip().startswith("x:"):
                    cls._x_env = env
                    return env
        return cls._x_env

    def _capturar_pantalla(self, ruta: str) -> bool:
        for cmd in (["spectacle", "-b", "-n", "-o", ruta],
                    ["gnome-screenshot", "-f", ruta],
                    ["import", "-window", "root", ruta]):
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=30)
                if r.returncode == 0 and os.path.getsize(ruta) > 0:
                    return True
            except Exception as e:
                self.log.debug(f"AsistenteLinux: captura fallida "
                               f"{cmd[0]}: {e}")
        return False

    def _vision_elemento(self, descripcion: str) -> Optional[dict]:
        """Captura la pantalla y pide al modelo de vision las coordenadas
        reales del elemento descrito. Devuelve {x, y, w, h} o None."""
        from PIL import Image as PILImage
        ruta = os.path.join(tempfile.gettempdir(), "ovos-raton.png")
        img = None
        for intento in range(3):
            if not self._capturar_pantalla(ruta):
                self.log.warning(
                    "AsistenteLinux: no hay herramienta de captura")
                continue
            try:
                with open(ruta, "rb") as fh:
                    img = base64.b64encode(fh.read()).decode()
            except Exception:
                img = None
            if img:
                break
        if not img:
            return None
        # el localizador va con una copia reduced: el modelo de vision tarda
        # ~13s con 1920x1080 frente a ~2s con 960px, y para un bbox le basta
        esc = 1.0
        try:
            with PILImage.open(ruta) as im0:
                ancho_orig, alto_orig = im0.size
            if ancho_orig > 960:
                esc = 960 / ancho_orig
                with PILImage.open(ruta) as im0:
                    im_chica = im0.resize((960, max(1, int(alto_orig * esc))))
                    buf = io.BytesIO()
                    im_chica.save(buf, format="PNG")
                    img = base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            self.log.debug(f"AsistenteLinux: no pude reducir la captura: {e}")
        prompt = (
            f"Localiza en esta captura el elemento: {descripcion}.\n"
            "Responde EXCLUSIVAMENTE con este JSON y nada mas:\n"
            '{"x": <px_izq>, "y": <px_sup>, "w": <ancho_px>, "h": <alto_px>}\n'
            "Si no existe ese elemento, responde exactamente {\"x\": -1}.")
        contenido = self._vision_pregunta(
            img, prompt, temperature=0, timeout=25, num_predict=60)
        self.log.info(f"AsistenteLinux: vision dijo: {contenido[:200]}")
        box = self._parse_bbox(contenido)
        if not box:
            return None
        # el bbox viene en coordenadas de la imagen reducida
        return {k: int(round(v * esc)) for k, v in box.items()}

    def _vision_pregunta(self, imagen_b64: str, prompt: str,
                         temperature: float = 0.2,
                         timeout: float = 180,
                         num_predict: Optional[int] = None) -> str:
        """Envía una imagen (base64) y un prompt al modelo de visión y
        devuelve su respuesta en texto."""
        payload = {
            "model": VISION_MODEL,
            "stream": False,
            "temperature": temperature,
            # mantiene el modelo de vision en memoria: sin esto, la primera
            # peticion tras un ratoidle tarda ~70s en cargar el modelo
            "keep_alive": "30m",
            "messages": [
                {"role": "user",
                 "content": prompt,
                 "images": [imagen_b64]},
            ],
        }
        if num_predict:
            payload["options"] = {"num_predict": num_predict}
        try:
            resp = requests.post(VISION_URL, json=payload, timeout=timeout)
            resp.raise_for_status()
            return (resp.json()["message"].get("content") or "").strip()
        except Exception as e:
            self.log.warning(f"AsistenteLinux: vision pregunta fallo: {e}")
            return ""

    def _analizar_imagen(self, consulta: str, message: Message,
                         ses_id: str) -> bool:
        consulta = (consulta or "").strip()
        self.log.info(f"AsistenteLinux analizar_imagen consulta={consulta!r}")
        if not consulta:
            self.speak_dialog("mirar_que")
            return True
        ruta = os.path.join(tempfile.gettempdir(), "ovos-imagen.png")
        if not self._capturar_pantalla(ruta):
            self.speak_dialog("captura_fallo")
            return True

        # 1) Preguntar al modelo de vision.
        # Antes se intentaba recortar la imagen principal con un bbox pedido
        # al modelo, pero devolvia coordenadas inventadas (valores fuera del
        # lienzo) y el recorte se comia el sujeto. Con la captura completa el
        # modelo responde bien y sin coste extra, asi que no recortamos.
        try:
            with open(ruta, "rb") as fh:
                img_final = base64.b64encode(fh.read()).decode()
        except Exception as e:
            self.speak_dialog("captura_lectura_fallo")
            return True
        ins = ("Responde en español, breve (2-4 frases), en tono hablado "
               "y amigable, sin listas ni viñetas. Si identificas el sujeto "
               "principal con nombre propio (obra, lugar, persona), termina "
               "tu respuesta con la línea 'SUJETO: <nombre>'.")
        respuesta = self._vision_pregunta(
            img_final, f"{consulta}\n\n{ins}", temperature=0.4,
            timeout=90, num_predict=300)
        if not respuesta:
            self.speak_dialog("imagen_analisis_fallo")
            return True
        sujeto = ""
        ms = re.search(r"\bSUJETO\s*:\s*(.+)", respuesta, re.I)
        if ms:
            cand = ms.group(1).strip().strip(".,;:")
            respuesta = re.sub(r"\s*SUJETO\s*:.*$", "", respuesta,
                               flags=re.I)
            # Solo vale un nombre propio real: si el modelo no reconoce el
            # sujeto escribe "no se puede identificar" y buscar eso en
            # Wikipedia devuelve articulos sin relacion (p.ej. una serie).
            if (cand and len(cand) <= 60 and len(cand.split()) <= 5
                    and not re.search(
                        r"no se|desconocid|no identific|ningun|ningún|"
                        r"nada|desconocido|^\W+$", cand, re.I)
                    and re.search(r"[A-ZÁÉÍÓÚÑ]", cand)):
                sujeto = cand
            else:
                self.log.debug(f"AsistenteLinux: sujeto descartado {cand!r}")
        self.speak(respuesta.strip()[:500])

        # 3) Si pidió información, encadenar búsqueda en Wikipedia.
        quiso_info = re.search(
            r"informaci[oó]n|qu[eé] obra|qu[eé] es|qu[ií]en es|"
            r"d[oó]nde se tom[oó]|famos", consulta.lower())
        if quiso_info and sujeto:
            self._consultar_web(f"wikipedia {sujeto}", message, ses_id)
        return True

    def _raton_sobre_elemento(self, descripcion: str, accion: str,
                              message: Message, ses_id: str) -> bool:
        descripcion = (descripcion or "").strip()
        accion = (accion or "clic").strip()
        self.log.info(f"AsistenteLinux raton_sobre_elemento "
                      f"desc={descripcion!r} accion={accion!r}")
        if not descripcion:
            self.speak_dialog("raton_que_elemento")
            return True
        # Si no encuentra el elemento, _vision_elemento devuelve None.
        try:
            box = self._vision_elemento(descripcion)
        except Exception as e:
            self.log.exception(f"AsistenteLinux: vision elemento fallo: {e}")
            box = None
        if not box:
            self.speak_dialog("raton_no_veo", {"descripcion": descripcion})
            return True
        cx = box["x"] + box["w"] // 2
        cy = box["y"] + box["h"] // 2
        xenv = self._x_candidato()
        if not xenv:
            self.log.warning("AsistenteLinux: sin entorno X para xdotool")
            self.speak_dialog("raton_sin_servidor")
            return True
        # Acota al area visible para que xdotool no se salga de la pantalla.
        try:
            geo = subprocess.run(["bash", "-c",
                                  "command -v xdotool >/dev/null && "
                                  "xdotool getdisplaygeometry"],
                                 capture_output=True, text=True, timeout=10,
                                 env=xenv)
            partes = (geo.stdout.split() or [])
            if len(partes) >= 2:
                max_x, max_y = int(partes[0]), int(partes[1])
                cx = min(max(cx, 0), max_x - 1)
                cy = min(max(cy, 0), max_y - 1)
        except Exception:
            pass
        xdo = ["xdotool"]
        try:
            r = subprocess.run(["bash", "-c", "command -v xdotool"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                raise FileNotFoundError("xdotool no disponible")
            rmv = subprocess.run(xdo + ["mousemove", str(cx), str(cy)],
                                 capture_output=True, text=True, timeout=15,
                                 env=xenv)
            if rmv.returncode != 0:
                self.log.warning(f"AsistenteLinux: xdotool mousemove rc="
                                 f"{rmv.returncode} err={rmv.stderr.strip()}")
            if accion in ("clic", "doble_clic", "clic_derecho"):
                boton = {"clic": "1", "doble_clic": "1",
                         "clic_derecho": "3"}[accion]
                if accion == "doble_clic":
                    rcl = subprocess.run(xdo + ["click", "--repeat", "2",
                                               "--delay", "80", "1"],
                                         capture_output=True, text=True,
                                         timeout=15, env=xenv)
                else:
                    rcl = subprocess.run(xdo + ["click", boton],
                                         capture_output=True, text=True,
                                         timeout=15, env=xenv)
                if rcl.returncode != 0:
                    self.log.warning(f"AsistenteLinux: xdotool click rc="
                                     f"{rcl.returncode} err="
                                     f"{rcl.stderr.strip()}")
            loc = subprocess.run(xdo + ["getmouselocation"],
                                 capture_output=True, text=True, timeout=10,
                                 env=xenv)
            loc_txt = loc.stdout.strip() or ""
            if loc.returncode != 0:
                self.log.warning(f"AsistenteLinux: xdotool location rc="
                                 f"{loc.returncode} err={loc.stderr.strip()}")
        except Exception as e:
            self.log.warning(f"AsistenteLinux: raton fallo: {e}")
            self.speak_dialog("raton_herramienta_fallo")
            return True
        if accion == "mover":
            self.speak_dialog("raton_movido", {"descripcion": descripcion})
        elif accion == "doble_clic":
            self.speak_dialog("raton_doble_clic", {"descripcion": descripcion})
        elif accion == "clic_derecho":
            self.speak_dialog("raton_clic_derecho", {"descripcion": descripcion})
        else:
            self.speak_dialog("raton_clic", {"descripcion": descripcion})
        self.log.info(f"AsistenteLinux: puntero ahora en {loc_txt}")
        return True

    @staticmethod
    def _raton_por_frase(utt: str) -> Optional[tuple]:
        """Detecta frases claras de interactuar con la pantalla y devuelve
        (descripcion, accion). None si no aplica."""
        u = unicodedata.normalize("NFKD", utt.lower())
        u = re.sub(r"[¿?¡!.,;:]", " ", u)
        u = re.sub(r"\s+", " ", u).strip()
        m = re.search(
            r"(?:pon|coloca|situa|mueve|lleva|deja|ponme|mueveme)\s+"
            r"(?:el\s+)?(puntero|cursor|rat[oó]n)\s+"
            r"(?:en|sobre|al|a|hacia|hasta)\s+(.+)", u)
        if m:
            return (m.group(2).strip(), "mover")
        m = re.search(r"clic(?:k)?\s+derecho\s+(?:en|sobre|a|con)\s+(.+)", u)
        if m:
            return (m.group(1).strip(), "clic_derecho")
        m = re.search(r"doble\s+clic\s+(?:en|a|sobre)\s+(.+)", u)
        if m:
            return (m.group(1).strip(), "doble_clic")
        for pat in (r"(?:haz\s+un\s+)?clic(?:k)?\s+(?:en|sobre|a)\s+(.+)",
                    r"puls[aá]\s+(?:en\s+)?(.+)",
                    r"pinch[aá]\s+(?:en\s+)?(.+)",
                    r"h[aá]zle\s+clic\s+(?:en|a)\s+(.+)"):
            m = re.search(pat, u)
            if m:
                return (m.group(1).strip(), "clic")
        m = re.search(r"reproduce\s+(?:el\s+)?(?:video|v[íi]deo)\s+"
                      r"(?:de|con|que|sobre|titulado|t[ií]tulo)?\s+(.+)", u)
        if m:
            return (f"el video {m.group(1).strip()}", "clic")
        m = re.search(
            r"(?:move|put|place|bring|set)\s+(?:the\s+)?"
            r"(?:pointer|cursor|mouse)\s+(?:to|on|onto|over|at)\s+(.+)", u)
        if m:
            return (m.group(1).strip(), "mover")
        m = re.search(r"right\s+click\s+(?:on|over|the)?\s*(.+)", u)
        if m:
            return (m.group(1).strip(), "clic_derecho")
        m = re.search(r"double\s+click\s+(?:on|over|the)?\s*(.+)", u)
        if m:
            return (m.group(1).strip(), "doble_clic")
        m = re.search(r"(?:click|press|tap)\s+(?:on\s+|over\s+|the\s+)?(.+)", u)
        if m:
            return (m.group(1).strip(), "clic")
        m = re.search(r"play\s+(?:the\s+)?(?:video|clip)\s+"
                      r"(?:of|titled|named|about)?\s*(.+)", u)
        if m:
            return (f"the video {m.group(1).strip()}", "clic")
        return None


    @staticmethod
    def _parse_bbox(texto: str) -> Optional[dict]:
        """Parsea la respuesta JSON del modelo de visión (bbox) tolerando
        claves sin comillar y ruido alrededor. Devuelve {x,y,w,h} o None."""
        m = re.search(r"\{[\s\S]*\}", texto)
        if not m:
            return None
        try:
            datos = json.loads(m.group(0))
        except json.JSONDecodeError:
            datos = None
        vals = {}
        if isinstance(datos, dict):
            for k in ("x", "y", "w", "h"):
                try:
                    vals[k] = int(datos.get(k, -1))
                except (TypeError, ValueError):
                    vals[k] = -1
        else:
            for k in ("x", "y", "w", "h"):
                km = re.search(r"[\"']?" + k + r"[\"']?\s*:\s*(-?\d+)",
                               texto)
                vals[k] = int(km.group(1)) if km else -1
        x, y, w, h = (vals.get(k, -1) for k in ("x", "y", "w", "h"))
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            return None
        return {"x": x, "y": y, "w": w, "h": h}

    @staticmethod
    def _imagen_por_frase(utt: str) -> Optional[str]:
        """Detecta frases claras de analizar la imagen visible en pantalla.
        Devuelve la consulta que debe hacerse al modelo de visión, o None."""
        u = unicodedata.normalize("NFKD", utt.lower())
        u = re.sub(r"[¿?¡!.,;:]", " ", u)
        u = re.sub(r"\s+", " ", u).strip()
        if not re.search(r"imagen|foto|pantalla", u):
            return None
        p_opinion = re.search(
        r"(?:analiza|analiza la|analiza esta|opina|opina sobre|opina de|"
        r"opini[oó]n de|que te parece|qu[eé] te parece|que opinas|"
        r"qu[eé] opinas|valora|dame tu opin[ióo]n|como te parece|"
        r"c[oó]mo te parece|mejora la|podrias mejorar|podr[ií]as mejorar)",
        u)
        if p_opinion:
            return ("Da tu opinión honesta y útil para mejorar esta imagen. "
                    "Analiza composición, colores, contraste, tipografía si la "
                    "hay, y sugiere 2-3 mejoras concretas y amigables.")
        p_info = re.search(
        r"(?:informaci[oó]n de|informaci[oó]n sobre|qu[eé] es la imagen|"
        r"qu[eé] representa|qu[eé] obra|qu[eé] lugar|d[oó]nde se tom[oó]|"
        r"donde se tom[oó]|quien es|qui[eé]n es|que pintor|qu[eé] pintor|"
        r"es famosa|famoso)",
        u)
        if p_info:
            return ("Identifica qué es esta imagen: si corresponde a un lugar, "
                    "obra de arte famosa, persona o fotografía conocida, di su "
                    "nombre y un dato breve. Si no la reconoces, descríbela "
                    "honestamente.")
        p_desc = re.search(
        r"(?:describe|describe la|describe esta|dime qu[eé] hay|"
        r"dime que hay|qu[eé] hay en|que hay en|qu[eé] se ve|que se ve|"
        r"qu[eé] muestra|que muestra|qu[eé] tiene la|que tiene la)",
        u)
        if p_desc:
            return ("Describe en detalle qué muestra esta imagen: elementos, "
                    "personas, escenario, colores y cualquier texto visible.")
        return ("Analiza esta imagen y cuéntame qué es y qué se ve, en 2-3 "
                "frases sencillas.")

    def _on_raton_intent(self, message: Message) -> bool:
        """Intent padatious 'mouse.intent': se dispara antes que skills
        genéricos para las frases de puntero/clic reproducir."""
        utt = (message.data.get("utterance") or "").strip()
        parsed = self._raton_por_frase(utt)
        if not parsed:
            parsed = ((message.data.get("elemento") or "").strip(), "clic")
        return self._raton_sobre_elemento(
            parsed[0], parsed[1], message,
            SessionManager.get(message).session_id)

    def _on_identidad_intent(self, message: Message) -> bool:
        nombre = _nombre_asistente()
        self.speak_dialog("me_llamo", {"nombre": nombre})
        return True

    def _buscar_en_cascada(self, paquete: str, message: Message,
                           ses_id: str) -> bool:
        """Busca el paquete en orden jerárquico: repos de la comunidad,
        Flatpak, Docker y, como último recurso, el código fuente."""
        self.speak_dialog("paquete_no_en_repos", {"paquete": paquete})
        equiv = self._equivalente(paquete)
        if equiv:
            if self._sugerir_equivalente(paquete, equiv, message, ses_id):
                return True
        if self._buscar_repo_extra(paquete, message, ses_id):
            return True
        if self._instalar_flatpak(paquete, message, ses_id):
            return True
        if self._instalar_docker(paquete, message, ses_id):
            return True
        return self._instalar_desde_fuente(paquete, message, ses_id)

    def _equivalente(self, paquete: str) -> Optional[str]:
        """Devuelve el nombre del paquete equivalente si el programa pedido
        es de Windows o comercial cobrado. None si no hay equivalente."""
        p = paquete.strip().lower()
        for clave, (equiv, _desc) in EQUIVALENCIAS.items():
            if clave in p or p in clave:
                return equiv
        return None
    def _sugerir_equivalente(self, programa: str, equiv: str,
                             message: Message, ses_id: str) -> bool:
        """Ofrece el equivalente o la emulacion con Wine. True si el flujo
        queda resuelto (instala el equivalente, emula o el usuario cancela);
        False si el usuario quiere seguir con la cascada normal."""
        self.speak_dialog("equivalente_sugerido",
                          {"programa": programa, "equiv": equiv})
        respuesta = self._preguntar("elegir_equivalente",
                                    {"programa": programa, "equiv": equiv})
        if not respuesta:
            self.speak_dialog("accion_cancelada")
            return True
        rl = respuesta.lower()
        if re.search(r"(gimp|blender|libreoffice|thunderbird|inkscape|"
                     r"kdenlive|equiv\w*|ese|s[ií]|si|est[aá] bien|dale)",
                     rl):
            self.log.info("AsistenteLinux: instalando equivalente "
                          f"{equiv} para {programa}")
            return self._instalar_paquete(equiv, message, ses_id)
        if re.search(r"wine|proton|emul[ao]|lutris|bottles|windows", rl):
            return self._emular_con_wine(programa, message, ses_id)
        # "no", u otra cosa: seguir buscando en Flatpak/Docker/fuente
        self.speak_dialog("siguiendo_cascada", {"programa": programa})
        return False

    def _wine_listo(self) -> bool:
        try:
            r = subprocess.run(["bash", "-c", "command -v wine"],
                               capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    def _emular_con_wine(self, programa: str, message: Message,
                     ses_id: str) -> bool:
        if not self._wine_listo():
            respuesta = self._preguntar("wine_a_instalar",
                                        {"programa": programa})
            if not self._afirmativo(respuesta):
                self.speak_dialog("accion_cancelada")
                return True
            cmd = f"pkexec {self.pm_install} wine"
            try:
                r = subprocess.run(
                    ["bash", "-c", cmd],
                    capture_output=True, text=True,
                    timeout=int(self.settings.get("install_timeout", 600)))
            except Exception as e:
                self.speak_dialog("desinstalacion_fallida",
                                  {"paquete": "wine",
                                   "detalle": str(e)})
                return True
            if r.returncode != 0:
                self.speak_dialog("desinstalacion_fallida",
                                  {"paquete": "wine",
                                   "detalle": self._razon_instalacion_fallida(
                                       r.stdout + r.stderr, r.returncode)})
                return True
        self.speak_dialog("guia_wine", {"programa": programa})
        self._abrir_terminal("winecfg", message)
        return True

    def _release_slug(self) -> str:
        try:
            with open("/etc/os-release") as f:
                t = f.read()
        except Exception:
            return ""
        vid = re.search(r'VERSION_ID="?([^"\n]+)"?', t)
        pn = re.search(r'PRETTY_NAME="?([^"\n]+)"?', t)
        nombre = ((pn.group(1) if pn else "") + " "
                  + (vid.group(1) if vid else ""))
        if "tumbleweed" in nombre.lower():
            return "openSUSE_Tumbleweed"
        if "leap" in nombre.lower():
            v = vid.group(1) if vid else ""
            if v:
                return f"openSUSE_Leap_{v}"
        return ""

    def _packman_base_url(self) -> Optional[str]:
        slug = self._release_slug()
        if not slug:
            return None
        lista = "https://ftp.gwdg.de/pub/linux/misc/packman/suse/"
        try:
            r = subprocess.run(["curl", "-s", "--max-time", "20", lista],
                               capture_output=True, text=True, timeout=30)
            for m in re.finditer(r'href="([^"]+)/"', r.stdout):
                if m.group(1) == slug:
                    return lista + m.group(1) + "/"
        except Exception:
            pass
        return lista + slug + "/"

    def _packman_tiene(self, paquete: str) -> bool:
        base = self._packman_base_url()
        if not base:
            return False
        try:
            rm = subprocess.run(["curl", "-s", "--max-time", "20",
                                 base + "repodata/repomd.xml"],
                                capture_output=True, text=True, timeout=30)
            m = re.search(r'href="([^"]*filelists[^"]*\.g?z[^"]*)"',
                          rm.stdout)
            if m:
                pat = f'name="{paquete}"'
            else:
                m = re.search(r'href="([^"]*primary[^"]*\.gz)"', rm.stdout)
                pat = f"<name>{paquete}</name>"
            if not m:
                return False
            r = subprocess.run(
                ["bash", "-c",
                 f"curl -s --max-time 90 '{base}{m.group(1)}' | "
                 f"zcat 2>/dev/null | grep -m1 {pat!r}"],
                capture_output=True, text=True, timeout=120)
            return bool(r.stdout.strip())
        except Exception:
            return False

    def _packman_enabled(self) -> bool:
        try:
            r = subprocess.run(["bash", "-c", "zypper repos"],
                               capture_output=True, text=True, timeout=30)
            return bool(re.search(r"packman", r.stdout, re.I))
        except Exception:
            return False

    def _buscar_repo_extra(self, paquete: str, message: Message,
                       ses_id: str) -> bool:
        """Cascada 1: repositorios de la comunidad (Packman, solo openSUSE).
        True si ya se resolvió la instalación (o el usuario canceló); False si
        hay que seguir con Flatpak/Docker."""
        if self.pm != "zypper":
            self.log.info("AsistenteLinux: repo de la comunidad solo "
                          "disponible en openSUSE; la cascada sigue")
            return False
        if self._packman_enabled():
            self.log.info("AsistenteLinux: Packman ya está en los repos "
                          "activados; la cascada sigue con Flatpak/Docker")
            return False
        self.speak_dialog("buscando_repo_extra", {"paquete": paquete})
        if not self._packman_tiene(paquete):
            self.speak_dialog("repo_extra_fallido", {"paquete": paquete})
            return False
        respuesta = self._preguntar("repo_extra_encontrado",
                                    {"paquete": paquete})
        if not self._afirmativo(respuesta):
            self.speak_dialog("accion_cancelada")
            return True
        base = self._packman_base_url()
        self.speak_dialog("repo_extra_instalando", {"paquete": paquete})
        cmd = (f"pkexec zypper --non-interactive --gpg-auto-import-keys "
               f"addrepo --refresh '{base}' packman && "
               f"pkexec zypper --non-interactive --gpg-auto-import-keys "
               f"install {paquete}")
        try:
            res = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True,
                timeout=int(self.settings.get("install_timeout", 600)))
        except Exception as e:
            self.log.exception("AsistenteLinux: error al añadir Packman")
            self.speak_dialog("instalacion_fallida",
                              {"paquete": paquete, "detalle": str(e)})
            return True
        if res.returncode != 0:
            self.speak_dialog("instalacion_fallida",
                              {"paquete": paquete,
                               "detalle": self._razon_instalacion_fallida(
                                   res.stdout + res.stderr, res.returncode)})
            return True
        self.speak_dialog("instalacion_ok", {"paquete": paquete})
        return True

    def _flatpak_search(self, term: str) -> List[str]:
        try:
            r = subprocess.run(
                ["bash", "-c", f"flatpak search {term} 2>/dev/null"],
                capture_output=True, text=True, timeout=60)
        except Exception:
            return []
        return self._listar_flatpaks(r.stdout)

    def _listar_flatpaks(self, salida: str) -> List[str]:
        visto = set()
        apps = []
        for m in re.finditer(
                r"\b([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)+)\b", salida):
            app = m.group(1)
            if app not in visto:
                visto.add(app)
                apps.append(app)
        return apps[:8]

    def _instalar_flatpak(self, paquete: str, message: Message,
                      ses_id: str) -> bool:
        apps = self._flatpak_search(paquete)
        if not apps:
            self.speak_dialog("flatpak_no_encontrado",
                              {"paquete": paquete})
            return False
        self.speak_dialog("flatpak_opciones", {"total": len(apps), "lista": ', '.join(apps[:5])})
        self.speak_dialog("flatpak_opciones", {"total": len(apps), "lista": ', '.join(apps[:5])})
        candidato = apps[0]
        respuesta = self._preguntar("confirmar_flatpak", {"app": candidato})
        if not self._afirmativo(respuesta):
            self.speak_dialog("accion_cancelada")
            return True
        self.speak_dialog("instalando_flatpak", {"app": candidato})
        try:
            res = subprocess.run(
                ["bash", "-c",
                 f"pkexec flatpak install --noninteractive flathub "
                 f"{candidato} 2>&1"],
                capture_output=True, text=True,
                timeout=int(self.settings.get("install_timeout", 600)))
        except Exception as e:
            self.speak_dialog("instalacion_fallida",
                              {"paquete": candidato, "detalle": str(e)})
            return True
        if res.returncode == 0:
            self.speak_dialog("instalacion_ok", {"paquete": candidato})
            return True
        detalle = (res.stdout or res.stderr or "")[:200].replace("\n", " ")
        if re.search(r"remote.*not found|no remote|flathub.*no", detalle,
                     re.I):
            detalle = "el repositorio Flathub no está configurado"
            self.speak_dialog("instalacion_fallida",
                              {"paquete": candidato, "detalle": detalle})
            ok = self._preguntar("configurar_flathub", {"app": candidato})
            if self._afirmativo(ok):
                self._abrir_terminal(
                    "flatpak remote-add --user --if-not-exists flathub "
                    "https://flathub.org/repo/flathub.flatpakrepo && "
                    f"flatpak install --user flathub {candidato}", message)
                self.speak_dialog("configurando_flathub", {"app": candidato})
            else:
                self.speak_dialog("accion_cancelada")
            return True
        self.speak_dialog("instalacion_fallida",
                          {"paquete": candidato,
                           "detalle": detalle or "falló la instalación"})
        return True

    def _listar_docker_imgs(self, salida: str) -> List[str]:
        imgs = []
        for l in salida.splitlines():
            m = re.match(r"^(\S+)\s+\S", l)
            if not m or m.group(1).lower() == "name":
                continue
            n = m.group(1)
            if n not in imgs and re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._/-]*", n):
                imgs.append(n)
        return imgs[:8]

    def _docker_estado(self) -> str:
        """Devuelve '' si docker esta disponible y el daemon activo, o el
        motivo por el que no esta listo."""
        try:
            r = subprocess.run(
                ["bash", "-c", "command -v docker"],
                capture_output=True, text=True)
            if r.returncode != 0:
                return "no está instalado"
            r = subprocess.run(
                ["bash", "-c", "docker info >/dev/null 2>&1"],
                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return ("el servicio no está activo o tu usuario no está "
                        "en el grupo docker")
        except Exception:
            return "Docker no responde"
        return ""

    def _escribir_script_docker(self) -> str:
        ruta = os.path.join(tempfile.gettempdir(), "asistente-docker.sh")
        contenido = """#!/bin/bash
        echo "=== 1. Instalando Docker en openSUSE ==="
        sudo zypper --non-interactive install docker docker-compose
        echo "=== 2. Habilitando y arrancando el daemon con systemd ==="
        sudo systemctl enable --now docker
        echo "=== 3. Añadiendo tu usuario al grupo docker ==="
        sudo usermod -aG docker "$USER"
        echo "=== Comprobacion ==="
        docker info --format 'ok, daemon activo' 2>/dev/null || echo "Sin permisos en esta sesion"
        echo ""
        echo "Nota: cierra y vuelve a abrir la sesión gráfica para usar docker sin"
        echo "contraseña. Para probar ya sin reiniciar, ejecuta en consola:  newgrp docker"
        read -p "Pulsa Enter para cerrar"
        """
        try:
            with open(ruta, "w") as f:
                f.write(contenido)
            os.chmod(ruta, 0o755)
        except Exception:
            self.log.exception("AsistenteLinux: no se pudo escribir script "
                               "docker")
        return ruta

    def _instalar_docker(self, paquete: str, message: Message,
                     ses_id: str) -> bool:
        motivo = self._docker_estado()
        if motivo:
            self.speak_dialog("docker_no_config", {"motivo": motivo})
            ok = self._preguntar("confirmar_guia_docker", {"motivo": motivo})
            if self._afirmativo(ok):
                script = self._escribir_script_docker()
                ejecutable = os.path.expanduser(
                    self.settings.get("terminal", "konsole"))
                try:
                    subprocess.Popen(
                        [ejecutable, "-e", "bash", script])
                    self.speak_dialog("guiando_docker")
                except Exception as e:
                    self.log.exception(
                        "AsistenteLinux: no se pudo abrir guia docker")
                    self.speak_dialog("error_comando", {"detalle": str(e)})
            else:
                self.speak_dialog("accion_cancelada")
            return True
        try:
            r = subprocess.run(
                ["bash", "-c", f"docker search {paquete} 2>&1"],
                capture_output=True, text=True, timeout=60)
        except Exception:
            self.speak_dialog("docker_consulta_fallo")
            return True
        imgs = self._listar_docker_imgs(r.stdout)
        if not imgs:
            self.speak_dialog("docker_no_imagen", {"paquete": paquete})
            return False
        self.speak_dialog("docker_imagenes", {"lista": ', '.join(imgs[:4])})
        self.speak_dialog("docker_imagenes", {"lista": ', '.join(imgs[:4])})
        candidato = imgs[0]
        respuesta = self._preguntar("confirmar_docker",
                                    {"imagen": candidato})
        if not self._afirmativo(respuesta):
            self.speak_dialog("accion_cancelada")
            return True
        self.speak_dialog("docker_descargando", {"candidato": candidato})
        try:
            res = subprocess.run(
                ["bash", "-c", f"docker pull {candidato} 2>&1"],
                capture_output=True, text=True,
                timeout=int(self.settings.get("install_timeout", 600)))
        except Exception as e:
            self.speak_dialog("instalacion_fallida",
                              {"paquete": candidato, "detalle": str(e)})
            return True
        if res.returncode != 0:
            detalle = (res.stdout or res.stderr or "")[:200].replace(
                "\n", " ")
            self.speak_dialog("instalacion_fallida",
                              {"paquete": candidato,
                               "detalle": detalle or "no hay permisos para "
                                                     "usar docker"})
            return True
        abrir = self._preguntar("abrir_docker", {"imagen": candidato})
        if self._afirmativo(abrir):
            self._abrir_terminal(f"docker run -it --rm {candidato}", message)
        else:
            self.speak_dialog("imagen_lista", {"imagen": candidato})
        return True

    def _instalar_desde_fuente(self, paquete: str, message: Message,
                           ses_id: str) -> bool:
        url = self._preguntar("pedir_url_fuente", {"paquete": paquete})
        if not url:
            self.speak_dialog("accion_cancelada")
            return True
        url = url.strip()
        if not re.match(r"^https?://", url):
            self.speak_dialog("url_invalida")
            return True
        respuesta = self._preguntar("confirmar_fuente",
                                    {"paquete": paquete, "url": url})
        if not self._afirmativo(respuesta):
            self.speak_dialog("accion_cancelada")
            return True
        self.speak_dialog("analizando_fuente", {"paquete": paquete})
        script = self._escribir_script_fuente(paquete, url)
        if not script:
            self.speak_dialog("fuente_no_compilable", {"paquete": paquete})
            return True
        ejecutable = os.path.expanduser(
            self.settings.get("terminal", "konsole"))
        try:
            subprocess.Popen([ejecutable, "-e", "bash", script])
            self.speak_dialog("desde_fuente", {"paquete": paquete})
        except Exception as e:
            self.log.exception(
                "AsistenteLinux: no se pudo abrir la terminal de build")
            self.speak_dialog("error_comando", {"detalle": str(e)})
        return True

    def _leer_archivos(self, ruta: str, nombres: List[str],
                   limite: int = 6000) -> str:
        texto = ""
        for n in nombres:
            try:
                with open(os.path.join(ruta, n), errors="ignore") as f:
                    texto += "\n" + f.read()[:limite]
            except Exception:
                continue
        return texto

    def _extraer_deps_readme(self, ruta: str) -> List[str]:
        texto = self._leer_archivos(
            ruta, ["README.md", "README", "readme.md", "readme",
                   "INSTALL.md", "INSTALL", "BUILD.md", "BUILD",
                   "README.txt"])
        deps = []
        for m in re.finditer(
                r"(?:zypper|apt|apt-get|dnf)\s+install\s+(?:-y\s+)?"
                r"([^\n]+)", texto, re.I):
            for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*",
                                  m.group(1)):
                tok = tok.lower()
                if len(tok) >= 2 and tok not in deps and tok not in (
                        "y", "install", "sudo", "apt", "dnf", "zypper",
                        "and", "run", "the", "para", "necesitas"):
                    deps.append(tok)
        for m in re.finditer(
                r"\blib\S+-(?:dev|devel)\b|\bpkg-config\b", texto, re.I):
            d = m.group(0).lower()
            if d not in deps:
                deps.append(d)
        deps = [d[:-3] + "devel" if d.endswith("-dev") else d
                for d in deps]
        faltan = []
        for d in deps:
            try:
                r = subprocess.run(
                    ["bash", "-c", f"rpm -q {d} >/dev/null 2>&1"],
                    stdout=subprocess.DEVNULL, timeout=15)
                if r.returncode != 0:
                    faltan.append(d)
            except Exception:
                continue
        return faltan[:10]

    def _detectar_metodo_build(self, ruta: str, paquete: str) -> Optional[dict]:
        try:
            archivos = set(os.listdir(ruta))
        except Exception:
            return None
        if "Cargo.toml" in archivos:
            return {
                "build": "cargo build --release",
                "install": ("find target/release -maxdepth 1 -type f "
                            r"! -name '*.d' ! -name '*.so' "
                            r'-exec install -Dm755 {} "$HOME/.local/bin/" \; '
                            "&& echo '(binario instalado en "
                            "~/.local/bin)'")}
        if "meson.build" in archivos:
            return {
                "build": "meson setup _build && meson compile -C _build",
                "install": "sudo meson install -C _build"}
        if "CMakeLists.txt" in archivos:
            return {
                "build": "cmake -S . -B build && cmake --build build",
                "install": "sudo cmake --install build"}
        if any(f in archivos for f in ("configure.ac", "autogen.sh",
                                       "configure", "bootstrap",
                                       "Makefile.PL")):
            autogen = ("if [ -f autogen.sh ]; then ./autogen.sh; "
                       "elif [ -f bootstrap ]; then ./bootstrap; "
                       "elif [ -f configure.ac ]; then autoreconf -fi; fi")
            return {
                "build": f"{autogen} && ./configure && make -j$(nproc)",
                "install": "sudo make install"}
        if "Makefile" in archivos:
            return {
                "build": "make -j$(nproc)",
                "install": "sudo make install"}
        if "pyproject.toml" in archivos or "setup.py" in archivos:
            return {
                "build": "python3 -m pip install --user .",
                "install": ("echo '(ya quedó instalado en tu usuario "
                            "con pip)'")}
        if "package.json" in archivos:
            return {
                "build": "npm install",
                "install": (
                    "echo 'Dependencias de la app instaladas con npm. "
                    "Mira el README para saber como ejecutarla'")}
        return None

    def _escribir_script_fuente(self, paquete: str, url: str) -> str:
        ruta = os.path.join(tempfile.gettempdir(),
                            f"asistente-src-{paquete}.sh")
        dir_base = os.path.join(tempfile.gettempdir(),
                                f"asistente-src-{paquete}")
        log = os.path.join(tempfile.gettempdir(),
                           f"asistente-src-{paquete}.log")
        try:
            subprocess.run(["bash", "-c",
                            f"rm -rf '{dir_base}' && mkdir -p '{dir_base}'"],
                           capture_output=True, timeout=60)
            r = subprocess.run(
                ["bash", "-c",
                 f"git clone --depth 1 '{url}' '{dir_base}/src' 2>&1"],
                capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                self.log.warning("AsistenteLinux: no se pudo clonar: "
                                 + (r.stdout or ""))
                self.speak_dialog("fuente_descarga_fallo")
                return ""
        except Exception as e:
            self.log.exception("AsistenteLinux: error al clonar la fuente")
            self.speak_dialog("fuente_descarga_fallo")
            return ""
        src = os.path.join(dir_base, "src")
        if not os.path.isdir(src):
            self.speak_dialog("fuente_descarga_fallo")
            return ""
        metodo = self._detectar_metodo_build(src, paquete)
        if not metodo:
            self.log.warning("AsistenteLinux: build system no reconocido en "
                             + src)
            return ""
        build = metodo["build"]
        inst = metodo["install"]
        deps = self._extraer_deps_readme(src)
        deps_txt = " ".join(deps) if deps else "ninguna"
        if deps:
            deps_loop = ("for d in %s; do\n"
                         "  rpm -q \"$d\" >/dev/null 2>&1 && continue\n"
                         "  echo \">>> Instalando dependencia: $d\"\n"
                         "  sudo zypper --non-interactive install \"$d\" "
                         ">/dev/null 2>&1 || true\n"
                         "done") % " ".join("'%s'" % d for d in deps)
        else:
            deps_loop = ": # sin dependencias detectadas en el README"
        template = """#!/bin/bash
        set -o pipefail
        cd '@SRC@'
        APP="@APP@"
        LOG=@LOG@
        exec > >(tee "$LOG") 2>&1
        echo "=== Dependencias detectadas en el README: @DEPS@ ==="
        @DEPS_LOOP@
        echo
        echo "=== Compilando @APP@ ==="
        intentar() {
  if ! eval @BUILD@; then
        echo ">>> Error de compilacion. Intento corregir dependencias..."
        grep -oE 'fatal error: [^:]+\.h' "$LOG" 2>/dev/null | sed 's/.*: //' | sort -u |
        while read -r cab; do
          [ -z "$cab" ] && continue
          cand="${cab%.h}-devel"
          echo ">>> Instalando posible dependencia: $cand"
          sudo zypper --non-interactive install "$cand" >/dev/null 2>&1 || true
        done
        grep -oE 'cannot find -l[^ ]+' "$LOG" 2>/dev/null | sed 's/.*-l//' | sort -u |
        while read -r lib; do
          cand="lib${lib//-*-dev/-devel}"
          [ "$cand" = "lib${lib}" ] && cand="lib$lib-devel"
          echo ">>> Instalando libreria de desarrollo: $cand"
          sudo zypper --non-interactive install "$cand" >/dev/null 2>&1 || true
        done
        if ! eval @BUILD@; then
          echo
          echo "=== ERROR: no pude compilar @APP@ automaticamente ==="
          echo "Lo ultimo que salio en el log:"
          tail -n 25 "$LOG"
          echo
          read -p "Pulsa Enter para cerrar"
          exit 1
        fi
  fi
        }
        intentar
        echo
        echo "=== @APP@ compilado. Instalando... ==="
        @INSTALL@
        echo
        echo "=== @APP@ compilado e instalado ==="
        read -p "Pulsa Enter para cerrar"
        """
        contenido = (template
                     .replace("@SRC@", src)
                     .replace("@APP@", paquete)
                     .replace("@LOG@", log)
                     .replace("@DEPS@", deps_txt)
                     .replace("@DEPS_LOOP@", deps_loop)
                     .replace("@BUILD@", build)
                     .replace("@INSTALL@", inst))
        try:
            with open(ruta, "w") as f:
                f.write(contenido)
            os.chmod(ruta, 0o755)
        except Exception:
            self.log.exception("AsistenteLinux: no se pudo escribir script")
        return ruta

    def _paquete_instalado(self, paquete: str) -> bool:
        """Está instalado el paquete? Adaptado al gestor de la distro."""
        checks = {
            "zypper": f"rpm -q {paquete} >/dev/null 2>&1",
            "apt": f"dpkg-query -W -f='${{Status}}' {paquete} "
                   f"2>/dev/null | grep -q 'install ok installed'",
            "dnf": f"rpm -q {paquete} >/dev/null 2>&1",
            "pacman": f"pacman -Q {paquete} >/dev/null 2>&1",
        }
        try:
            r = subprocess.run(
                ["bash", "-c",
                 checks.get(self.pm, checks["zypper"])],
                stdout=subprocess.DEVNULL, timeout=20)
            return r.returncode == 0
        except Exception:
            return False

    def _desinstalar_paquete(self, paquete: str, message: Message,
                         ses_id: str) -> bool:
        paquete = paquete.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:-]*", paquete):
            self.log.warning(
                f"AsistenteLinux: paquete inválido '{paquete}'")
            return False
        if not self._paquete_instalado(paquete):
            self.speak_dialog("paquete_no_instalado", {"paquete": paquete})
            return True
        respuesta = self._preguntar("confirmar_desinstalar",
                                    {"paquete": paquete})
        self.log.info(f"AsistenteLinux confirmacion desinstalar: "
                      f"'{respuesta}'")
        if not self._afirmativo(respuesta):
            self.speak_dialog("accion_cancelada")
            return True
        self.speak_dialog("desinstalando", {"paquete": paquete})
        cmd = f"pkexec {self.pm_remove} {paquete}"
        self.log.info(f"AsistenteLinux desinstalando: {cmd}")
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True,
                timeout=int(self.settings.get("install_timeout", 600)),
                cwd=os.path.expanduser("~"),
                env={**os.environ, "LANG": "es_MX.UTF-8"})
        except subprocess.TimeoutExpired:
            self.speak_dialog("desinstalacion_fallida",
                              {"paquete": paquete,
                               "detalle": "expiró el tiempo de espera"})
            return True
        except Exception as e:
            self.log.exception("AsistenteLinux: error al desinstalar")
            self.speak_dialog("desinstalacion_fallida",
                              {"paquete": paquete, "detalle": str(e)})
            return True
        salida = (result.stdout + result.stderr).strip()
        self._contexto[ses_id] = {"comando": cmd, "salida": salida}
        if result.returncode == 0:
            self.log.info(f"AsistenteLinux desinstalación OK ({paquete})")
            self.speak_dialog("desinstalacion_ok", {"paquete": paquete})
        else:
            detalle = self._razon_instalacion_fallida(
                salida, result.returncode)
            self.log.warning(
                f"AsistenteLinux desinstalar rc={result.returncode}: "
                f"{salida[:500]}")
            self.speak_dialog("desinstalacion_fallida",
                              {"paquete": paquete, "detalle": detalle})
        return True

    def _buscar_por_termino(self, termino: str, message: Message,
                        ses_id: str) -> bool:
        """Recomienda paquetes del sistema por término (multidistro)."""
        termino = termino.strip()
        if not termino:
            self.speak_dialog("busqueda_sin_resultados")
            return True
        try:
            r = subprocess.run(
                ["bash", "-c", f"{self.pm_search} {termino} 2>&1"],
                capture_output=True, text=True,
                timeout=90,
                env={**os.environ, "LANG": "C.UTF-8"})
        except Exception as e:
            self.log.exception("AsistenteLinux: fallo al buscar paquetes")
            self.speak_dialog("error_comando", {"detalle": str(e)})
            return True
        salida = (r.stdout + r.stderr).strip()
        self._contexto[ses_id] = {
            "comando": f"buscar_paquetes:{termino}", "salida": salida}
        paquetes = self._listar_paquetes(salida)
        if paquetes:
            visibles = ", ".join(paquetes[:8])
            self.speak_dialog("paquetes_encontrados",
                              {"total": len(paquetes)})
            self.speak(visibles +
                       (f" y {len(paquetes) - 8} más"
                        if len(paquetes) > 8 else ""))
            return True
        apps = self._flatpak_search(termino)
        if apps:
            self.speak_dialog("repos_flatpak", {"lista": ', '.join(apps[:6])})
            return True
        self.speak_dialog("busqueda_sin_resultados")
        return True

    def _manual_de(self, comando: str) -> str:
        try:
            r = subprocess.run(
                ["bash", "-c",
                 f"MANWIDTH=80 man {comando} 2>/dev/null | "
                 f"tr -s ' ' | head -c 2500"],
                capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["bash", "-c", f"{comando} --help 2>&1 | head -c 2000"],
                capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        return ""

    def _explicar_comando(self, comando: str, message: Message,
                          ses_id: str) -> bool:
        comando = (comando.strip().split()[0].lower()
                   if comando.strip() else "")
        if not comando:
            self.speak_dialog("comando_vacio")
            return True
        if comando in KB_COMANDOS and _lang_mensaje() == "es":
            self.log.info(f"AsistenteLinux: explicando {comando} (base)")
            self.speak(KB_COMANDOS[comando])
            return True
        manual = self._manual_de(comando)
        if not manual:
            self.speak_dialog("comando_sin_info")
            return True
        payload = {
            "model": self._modelo_actual(),
            "stream": False,
            "max_tokens": 220,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content":
                 f"Debes contestar SIEMPRE en {_idioma_respuesta()}, aunque "
                 "el material que te doy para consultar (el manual) esté en "
                 "otro idioma. Eso es obligatorio. Eres un asistente por voz "
                 "para nuevos usuarios de Linux. Explica en 2 o 3 frases "
                 "breves y hablables, para qué sirve este comando, cómo "
                 "funciona y un ejemplo muy sencillo. Sin viñetas ni listas. "
                 "Solo texto plano."},
                {"role": "user", "content":
                 f"Comando: {comando}\n"
                 f"Material de referencia (puede estar en otro idioma):\n"
                 f"{manual[:1500]}\n\n"
                 f"Recuerda: responde en {_idioma_respuesta()}."},
            ],
        }
        try:
            data = self._chat(payload, timeout=120)
            texto = (data.get("texto") or "").strip()
            if texto:
                self.speak(self._limpiar_resumen(texto))
                return True
        except Exception as e:
            self.log.exception(f"AsistenteLinux: fallo al explicar: {e}")
        self.speak_dialog("man_comando", {"comando": comando})
        return True

    def _servicio_resolver(self, s: str) -> Optional[str]:
        s = s.strip().lower()
        for clave, alias in SERVICIO_ALIASES.items():
            if clave in s or s in clave:
                return alias
        for k in SERVICIOS:
            if k in s or s in k:
                return k
        return None

    def _configurar_servicio(self, servicio: str, accion: str,
                             message: Message, ses_id: str) -> bool:
        clave = self._servicio_resolver(servicio)
        if not clave:
            self.speak_dialog("servicio_desconocido", {"servicio": servicio})
            return True
        paquetes, unidades, puerto, guia = SERVICIOS[clave]
        paq = paquetes.get(self.pm, paquetes.get("zypper"))
        unidad = unidades.get(self.pm, unidades.get("zypper"))

        if accion.strip().lower() in ("estado", "ver", "revisa", "revisar"):
            try:
                r = subprocess.run(
                    ["bash", "-c",
                     f"systemctl is-active {unidad} 2>&1"],
                    capture_output=True, text=True, timeout=20)
                estado = r.stdout.strip() or "desconocido"
            except Exception:
                estado = "desconocido"
            self.speak_dialog("servicio_estado",
                              {"servicio": clave, "estado": estado})
            return True

        self.speak_dialog("servicio_plan",
                          {"servicio": clave, "puerto": puerto})
        respuesta = self._preguntar("confirmar_servicio",
                                    {"servicio": clave})
        if not self._afirmativo(respuesta):
            self.speak_dialog("accion_cancelada")
            return True

        # 1) instalar los paquetes del servicio
        faltan = [p for p in paq if not self._paquete_instalado(p)]
        if faltan:
            self.speak_dialog("servicio_instalando",
                              {"servicio": clave})
            cmd = f"pkexec {self.pm_install} {' '.join(faltan)}"
            try:
                res = subprocess.run(
                    ["bash", "-c", cmd],
                    capture_output=True, text=True,
                    timeout=int(self.settings.get("install_timeout", 600)),
                    env={**os.environ, "LANG": "es_MX.UTF-8"})
            except Exception as e:
                self.log.exception("AsistenteLinux: error instalando servicio")
                self.speak_dialog("servicio_fallo",
                                  {"servicio": clave, "detalle": str(e)})
                return True
            salida = (res.stdout + res.stderr).strip()
            if res.returncode != 0:
                self.speak_dialog("servicio_fallo",
                                  {"servicio": clave,
                                   "detalle": self._razon_instalacion_fallida(
                                       salida, res.returncode)})
                return True

        # 2) arrancar como servicio del sistema
        arrancado = False
        try:
            r = subprocess.run(
                ["bash", "-c", f"systemctl is-active {unidad} 2>/dev/null"],
                capture_output=True, text=True, timeout=20)
            arrancado = (r.returncode == 0 and r.stdout.strip() == "active")
        except Exception:
            pass
        if not arrancado:
            self.speak_dialog("servicio_activando", {"servicio": clave})
            try:
                cmd = f"pkexec systemctl enable --now {unidad}.service"
                res = subprocess.run(
                    ["bash", "-c", cmd],
                    capture_output=True, text=True,
                    timeout=int(self.settings.get("command_timeout", 90)),
                    env={**os.environ, "LANG": "es_MX.UTF-8"})
                salida = (res.stdout + res.stderr).strip()
                if res.returncode != 0:
                    self.speak_dialog("servicio_systemd_fallo",
                                      {"servicio": clave,
                                       "detalle":
                                       self._razon_instalacion_fallida(
                                           salida, res.returncode)})
                    return True
            except Exception as e:
                self.log.exception(
                    "AsistenteLinux: error arrancando servicio")
                self.speak_dialog("servicio_fallo",
                                  {"servicio": clave, "detalle": str(e)})
                return True

        # 3) verificación local
        verif = ""
        if clave in ("web", "nginx"):
            try:
                r = subprocess.run(
                    ["bash", "-c", "curl -sI http://localhost | head -1"],
                    capture_output=True, text=True, timeout=20)
                verif = r.stdout.strip()
            except Exception:
                verif = ""
        self.log.info(f"AsistenteLinux: {clave} verificado en '{verif}'")
        self.speak_dialog("servicio_listo",
                          {"servicio": clave, "puerto": puerto,
                           "verif": verif or "servicio activo",
                           "guia": guia})
        return True

    def _razon_instalacion_fallida(self, salida: str,
                                   rc: int = 1) -> str:
        if not salida:
            return "el asistente de contraseña no respondió"
        # 104 = ZYPPER_EXIT_INF_CAP_NOT_FOUND
        if rc == 104 or re.search(r"not found|no se encontr|"
                                  r"no packages found|no provider", salida,
                                  re.I):
            return ("ese paquete no existe en los repositorios. "
                    "Puedes pedirme que te recomiende programas similares")
        s = salida.lower()
        if re.search(r"cancel|authorization|polkit|dismissed", s):
            return "cancelaste la contrasena"
        if re.search(r"permiso|denied|permission", s):
            return "no hay permisos suficientes"
        m = re.search(r"(?:Error|error|Failed|failed)\s*:?\s*([^\n]{0,80})",
                      salida)
        if m:
            return m.group(1).strip()
        return salida[:120].replace("\n", " ")

    def _abrir_terminal(self, cmd: str, message: Message) -> bool:
        cmd = cmd.strip()
        ejecutable = os.path.expanduser(
            self.settings.get("terminal", "konsole"))
        try:
            if cmd:
                if not self._seguro(cmd):
                    self.speak_dialog("comando_denegado")
                    return True
                # Ejecuta el comando y deja una shell interactiva abierta:
                # asi, aunque el comando falle o no exista, el usuario puede
                # seguir escribiendo (con --hold la ventana quedaba muerta).
                subprocess.Popen([ejecutable, "-e", "/bin/bash", "-c",
                                  f"{cmd}; exec /bin/bash"])
            else:
                subprocess.Popen([ejecutable])
            self.speak_dialog("terminal_abierta")
        except Exception as e:
            self.log.exception("AsistenteLinux: no se pudo abrir terminal")
            self.speak_dialog("error_comando", {"detalle": str(e)})
        return True

    def _normalizar_find(self, cmd: str) -> str:
        """Repara el escapado inconsistente de llama3.1 al generar find:
        elimina backslashes sobran a, convierte comillas dobles en simples y
        garantiza que la expresion quede entre parentesis escapados, para que
        las condiciones con -o no se apliquen al directorio actual."""
        if not re.match(r"^\S*find\b", cmd):
            return cmd
        cmd = cmd.replace("\\", "").replace('"', "'")
        # 'busca fotos' suele llegar como -iname '.jpg' (sin comodin);
        # si el patron entre comillas es solo una extension, anadimos '*'.
        cmd = re.sub(r"(-(?:iname|name)\s+')(\.\w+)(')", r"\1*\2\3", cmd)
        m = re.search(r"\bfind\s+(\S+)\s+(.*)$", cmd)
        if not m:
            return cmd
        raiz, pred = m.group(1), m.group(2).strip()
        pred = re.sub(r"^\(\s*|\s*\)$", "", pred).strip()
        if not pred:
            return f"find {raiz}"
        return f"find {raiz} \\( {pred} \\)"

    def _seguro(self, cmd: str) -> bool:
        cmd = cmd.strip()
        # Excepcion: el skill solo permite pkexec para la instalacion exacta
        # que el mismo construye (con polkit pidiendo la contrasena)
        if ALLOWED_PKEXEC_INSTALL_RE.match(cmd):
            return True
        cmd_l = cmd.lower()
        for pat in DANGEROUS_PATTERNS:
            if re.search(pat, cmd_l):
                self.log.warning(
                    f"AsistenteLinux: comando bloqueado por patrón '{pat}': "
                    f"{cmd}")
                return False
        return True

    READ_ONLY_COMMANDS = {
        "find", "ls", "exa", "eza", "grep", "rg", "cat", "head", "tail",
        "less", "more", "wc", "ps", "top", "htop", "df", "free", "uptime",
        "uname", "whoami", "pwd", "id", "date", "which", "whereis", "type",
        "file", "stat", "du", "sort", "uniq", "diff", "pgrep", "echo",
        "ip", "ss", "netstat", "ping", "hostname", "env", "printenv",
        "history", "getent", "lsof", "lsblk", "basename", "dirname",
    }

    def _es_solo_lectura(self, cmd: str) -> bool:
        cmd = cmd.strip()
        if not cmd:
            return True
        primario = cmd.split()[0].split("/")[-1]
        if primario in self.READ_ONLY_COMMANDS:
            return True
        # 'zypper search' es solo lectura (no pide confirmacion)
        if primario == "zypper" and re.match(
                r"^zypper(\s+--no-refresh)?\s+search\b", cmd):
            return True
        return False

    def _afirmativo(self, respuesta: Optional[str]) -> bool:
        if not respuesta:
            return False
        r = respuesta.strip().lower()
        return bool(re.match(r"^(s[ií]|yes|si|yep|yeah|claro|correcto|"
                             r"afirmativo|adelante|hazlo|vale|dale|ok|okay|"
                             r"de acuerdo|por favor|ejec[uú]talo|ejecuta|"
                             r"confirmo|confirmado)(\W.*)?$", r))

    def _preguntar(self, dialog: str,
                   data: Optional[dict] = None) -> Optional[str]:
        """Pide confirmacion/respuesta al usuario.

        Dos canales totalmente separados:
         - Sesion 'chat*'  -> canal de TEXTO (ventana del systray). NO arma
                              el microfono y la respuesta solo llega escrita;
                              si alguien habla en la habitacion no interviene.
         - Cualquier otra  -> canal de VOZ (get_response con activacion),
                              para cuando el usuario habla por PTT/microfono.
        """
        ses = SessionManager.get(dig_for_message())
        ses_id = ses.session_id
        if ses_id.startswith("chat"):
            return self._preguntar_texto(dialog, data)
        self._activar()
        time.sleep(2.5)
        self.log.info(f"AsistenteLinux pregunta '{dialog}' (activado)")
        try:
            return self.get_response(dialog=dialog, data=data, num_retries=0)
        finally:
            self._desactivar()
            self.log.info("AsistenteLinux respuesta recibida, desactivado")
    def _preguntar_texto(self, dialog: str,
                         data: Optional[dict] = None) -> Optional[str]:
        # pregunta por texto
        ses = SessionManager.get(dig_for_message())
        ses_id = ses.session_id
        texto = dialog
        try:
            if self.dialog_renderer:
                texto = self.dialog_renderer.render(dialog, data or {})
        except Exception:
            pass
        self.log.info(f"AsistenteLinux pregunta texto '{dialog}' (ses {ses_id})")
        # la pregunta tambien se dice en voz, ademas de mostrarse en la
        # ventana (speak() la espeja a audio si la sesion es chat)
        try:
            self.speak(texto)
        except Exception as e:
            self.log.debug(f"AsistenteLinux: voz pregunta chat: {e}")
        evento = threading.Event()
        self._respuestas_pendientes[ses_id] = {"evento": evento, "texto": None}
        try:
            self.bus.emit(Message(
                "ovos.asistente.pregunta",
                {"ses_id": ses_id, "dialog": texto},
                {"session": ses.serialize()}))
            esperado = evento.wait(timeout=60)
            if not esperado:
                return None
            return self._respuestas_pendientes[ses_id]["texto"] or ""
        finally:
            self._respuestas_pendientes.pop(ses_id, None)

    def _on_respuesta_texto(self, message: Message) -> None:
        ses_id = (message.data or {}).get("ses_id") or "default"
        texto = (message.data or {}).get("texto") or ""
        pend = self._respuestas_pendientes.get(ses_id)
        if pend is not None:
            pend["texto"] = texto.strip()
            pend["evento"].set()
            self.log.info(f"AsistenteLinux respuesta texto recibida"
                          f" (ses {ses_id})")

    def _activar(self):
        msg = dig_for_message() or Message("")
        if "skill_id" not in msg.context:
            msg.context["skill_id"] = self.skill_id
        self.bus.emit(
            msg.forward("intent.service.skills.activate",
                        data={"skill_id": self.skill_id, "timeout": 1}))

    def _desactivar(self):
        msg = dig_for_message() or Message("")
        if "skill_id" not in msg.context:
            msg.context["skill_id"] = self.skill_id
        self.bus.emit(
            msg.forward("intent.service.skills.deactivate",
                        data={"skill_id": self.skill_id}))

    # ------------------------------------------------------------------
    # Resumen hablado del resultado
    # ------------------------------------------------------------------
    def _resumir(self, cmd: str, salida: str) -> Optional[str]:
        if not salida:
            return None
        prompt = (
            f"Eres un asistente por voz que contesta en "
            f"{_idioma_respuesta()}, aunque el usuario escriba en otro "
            "idioma. Resume en 1 o 2 frases "
            "breves, naturales y hablables la salida de este comando. "
            "No uses viñetas ni listas. Si la salida es un error, di qué "
            "falló. Solo texto plano.\n"
            "REGLAS:\n"
            "- Si la salida contiene archivos, carpetas o nombres, NÓMBRALOS "
            "explícitamente (los primeros 5-6) y añade 'y N más' si hay más; "
            "nunca digas solo la cantidad.\n"
            "- Di primero cuántos hay y luego los nombres, en una sola frase "
            "hablada.\n"
            f"Comando: {cmd}\n"
            f"Salida:\n{salida[:2000]}\n"
            "Resumen hablado:"
        )
        payload = {
            "model": self._modelo_actual(),
            "stream": False,
            "max_tokens": 256,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": prompt},
            ],
        }
        try:
            data = self._chat(payload, timeout=120)
            texto = (data.get("texto") or "").strip()
            if not texto:
                return None
        except Exception as e:
            self.log.exception(f"AsistenteLinux: fallo al resumir: {e}")
        return None

    def _limpiar_resumen(self, texto: str) -> str:
        texto = re.sub(r"^\s*(assistant|asistente|Asistente)\s*:?\s*",
                       "", texto) if texto else texto
        texto = texto.replace("*", "").replace("#", "")
        texto = re.sub(r"\n+", " ", texto).strip()
        return texto