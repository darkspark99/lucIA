#!/usr/bin/env python3
"""Historial de conversaciones de la ventana de chat.

Guarda cada conversación en un fichero JSON propio dentro de
~/.local/share/ovos/historial/. Un fichero por conversación, así se puede
borrar una sin tocar las demás y no hay un único fichero que se corrompa
entero. Los números van en el nombre (marca de tiempo), que es justo el
orden en que se want mostrar.

No depende de Qt a propósito: la ventana lo usa, pero los tests no.
"""
import json
import os
import re
import time
import unicodedata

HISTORIAL_DIR = os.path.expanduser("~/.local/share/ovos/historial")
MAX_CONVERSACIONES = 300
MAX_MENSAJES = 500          # por conversación
MAX_TITULO = 60


# ---------- rutas ----------

def _dir():
    os.makedirs(HISTORIAL_DIR, mode=0o700, exist_ok=True)
    return HISTORIAL_DIR


def _ruta(cid):
    return os.path.join(_dir(), f"{cid}.json")


# ---------- texto ----------

def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFKD", t or "")
                   if not unicodedata.combining(c))


def limpiar(txt):
    """Quita saltos de linea y espacios repetidos de un mensaje."""
    return re.sub(r"\s+", " ", (txt or "")).strip()


def _corta(palabra, n):
    """Recorta a n caracteres y pone puntos suspensivos si sobra."""
    if len(palabra) <= n:
        return palabra.rstrip(" .,;:")
    return palabra[:n].rstrip(" .,;:") + "..."


def titulo_desde(mensajes):
    """Titulo automatico: lo primero que dijo el usuario, recortado.

    Si el usuario no ha escrito nada todavia (p. ej. solo el saludo del
    asistente), se usa el primer mensaje del asistente para que en el menu
    no aparezcan un monton de "sin titulo" iguales.
    """
    for roles in (("u",), ("u", "a")):
        for m in mensajes:
            if m.get("rol") in roles and limpiar(m.get("texto")):
                t = limpiar(m["texto"])
                return _corta(t, MAX_TITULO)
    return "Conversación sin título"


def resumen(mensajes, n=70):
    """Ultimo mensaje del usuario, para reconocerla de un vistazo."""
    for m in reversed(mensajes):
        if m.get("rol") == "u":
            t = limpiar(m.get("texto"))
            return _corta(t, n) if t else ""
    for m in reversed(mensajes):
        t = limpiar(m.get("texto"))
        if t:
            return _corta(t, n)
    return ""


# ---------- conversaciones ----------

def nuevo_id():
    """Id con marca de tiempo, legible y ordenable."""
    return time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid() % 1000:03d}"


def nueva(titulo=""):
    """Crea una conversacion vacia y ya la deja escrita en disco."""
    ahora = time.time()
    cid = nuevo_id()
    # dos en el mismo segundo no pueden compartir nombre
    while os.path.exists(_ruta(cid)):
        cid = f"{cid}x"
    conv = {
        "id": cid,
        "titulo": (titulo or "").strip() or "Conversación nueva",
        "creado": ahora,
        "modificado": ahora,
        "mensajes": [],
    }
    guardar(conv)
    return conv


def cargar(cid):
    try:
        with open(_ruta(cid), encoding="utf-8") as fh:
            conv = json.load(fh)
    except Exception:
        return None
    if not isinstance(conv, dict) or not conv.get("id"):
        return None
    conv.setdefault("mensajes", [])
    conv.setdefault("titulo", "Conversación sin título")
    conv.setdefault("creado", 0)
    conv.setdefault("modificado", 0)
    return conv


def guardar(conv):
    """Escribe la conversacion. Atómico: tmp + rename."""
    try:
        msgs = conv.get("mensajes") or []
        if len(msgs) > MAX_MENSAJES:
            conv["mensajes"] = msgs[-MAX_MENSAJES:]
        conv["modificado"] = time.time()
        # titulo siempre derivado si el usuario no le puso uno propio
        if not conv.get("titulo_manual"):
            conv["titulo"] = titulo_desde(conv["mensajes"])
        tmp = _ruta(conv["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(conv, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, _ruta(conv["id"]))
        os.chmod(_ruta(conv["id"]), 0o600)
        return True
    except Exception:
        return False


def listar(limite=MAX_CONVERSACIONES):
    """Todas las conversaciones, de la mas reciente a la mas antigua.

    Solo devuelve las que tienen mensajes: las vacias son ruido.
    """
    out = []
    try:
        nombres = os.listdir(HISTORIAL_DIR)
    except FileNotFoundError:
        return out
    for n in nombres:
        if not n.endswith(".json"):
            continue
        conv = cargar(n[:-5])
        if not conv or not conv["mensajes"]:
            continue
        out.append({
            "id": conv["id"],
            "titulo": conv["titulo"],
            "creado": conv["creado"],
            "modificado": conv["modificado"],
            "resumen": resumen(conv["mensajes"]),
            "n": len(conv["mensajes"]),
        })
    out.sort(key=lambda c: c["modificado"], reverse=True)
    return out[:limite]


def anadir(conv, rol, texto):
    """Mete un mensaje y actualiza el titulo si procede."""
    texto = (texto or "").strip()
    if not texto:
        return False
    # se guarda tal cual, con sus saltos de linea: limpiar() se usa solo
    # para el título y el resumen, que sí van en una linea
    conv.setdefault("mensajes", []).append({
        "rol": rol,
        "texto": texto,
        "hora": time.time(),
    })
    return guardar(conv)


def renombrar(cid, titulo):
    conv = cargar(cid)
    if not conv:
        return None
    conv["titulo"] = (titulo or "").strip() or "Conversación sin título"
    conv["titulo_manual"] = True
    return conv if guardar(conv) else None


def borrar(cid):
    try:
        os.remove(_ruta(cid))
        return True
    except Exception:
        return False


def purgar_vacias():
    """Las conversaciones que se crearon y no llegaron a usarse."""
    n = 0
    try:
        nombres = os.listdir(HISTORIAL_DIR)
    except FileNotFoundError:
        return 0
    for nombre in nombres:
        if not nombre.endswith(".json"):
            continue
        conv = cargar(nombre[:-5])
        if conv is not None and not conv["mensajes"]:
            if borrar(conv["id"]):
                n += 1
    return n


def limpiar_antiguas(limite=MAX_CONVERSACIONES):
    """Se queda con las 'limite' mas recientes y borra el resto."""
    todas = []
    try:
        nombres = os.listdir(HISTORIAL_DIR)
    except FileNotFoundError:
        return 0
    for n in nombres:
        if not n.endswith(".json"):
            continue
        conv = cargar(n[:-5])
        if conv and conv["mensajes"]:
            todas.append((conv["modificado"], conv["id"]))
    todas.sort(reverse=True)
    n = 0
    for _, cid in todas[limite:]:
        if borrar(cid):
            n += 1
    return n


def cuando(ts):
    """'hoy 14:32', 'ayer 09:10', '12 mar'… para poner en el menú."""
    if not ts:
        return ""
    d = time.localtime(ts)
    hoy = time.localtime()
    if time.strftime("%Y%m%d", d) == time.strftime("%Y%m%d", hoy):
        return "hoy " + time.strftime("%H:%M", d)
    ayer = time.localtime(time.time() - 86400)
    if time.strftime("%Y%m%d", d) == time.strftime("%Y%m%d", ayer):
        return "ayer " + time.strftime("%H:%M", d)
    if d.tm_year == hoy.tm_year:
        return time.strftime("%d %b", d)
    return time.strftime("%d %b %Y", d)


if __name__ == "__main__":       # comprobación manual rápida
    print("carpeta:", HISTORIAL_DIR)
    print("existe :", os.path.isdir(HISTORIAL_DIR))
    for c in listar(10):
        print(f"  {cuando(c['modificado']):>12}  {c['titulo']}  "
              f"({c['n']} mensajes)")