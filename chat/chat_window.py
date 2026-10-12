#!/usr/bin/env python3
"""Ventana de chat persistente de OVOS (systray -> 'Escribir pregunta...').

- Permanece abierta como chat hasta que el usuario la cierra.
- Muestra las respuestas del asistente en texto (util si no puedes oir el TTS).
- CANAL SEPARADO del microfono: todo lo enviado aqui va por sesion 'chat';
  nunca arma el microfono (no emite mycroft.mic.listen) y las preguntas del
  asistente se contestan escribiendo aqui, no con la voz."""
import asyncio
import json
import os
import re
import sys
import threading
import time

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QFontDatabase, QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QMenu, QInputDialog,
    QMessageBox, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import historial_chat
import idioma
from idioma import t

CONFIG_PATH = os.path.expanduser("~/.config/ovos-asistente.json")

from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method
from dbus_next.message import Message as DBusMessage

from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message as OVOSMessage

SES_ID = "chat"
SESSION = {"session_id": SES_ID, "lang": "es-es",
           "skill_id": "chat-window", "context": {}}
CTX = {"session": SESSION, "source": "chat-window"}
DBUS_NAME = "org.darkspark.OvosChat"
DBUS_PATH = "/org/darkspark/OvosChat"


def _nombre_asistente():
    ruta = os.path.expanduser("~/.config/ovos-asistente.json")
    try:
        with open(ruta, encoding="utf-8") as fh:
            nombre = (json.load(fh).get("nombre_asistente") or "").strip()
        if nombre:
            return nombre
    except Exception:
        pass
    return "lucIA"


NOMBRE_ASISTENTE = _nombre_asistente()

ovos_bus = None
gui = None


class GuiBridge(QObject):
    append_user = pyqtSignal(str)
    append_assistant = pyqtSignal(str)
    set_pending = pyqtSignal(bool)
    quit_requested = pyqtSignal()


bridge = GuiBridge()


class OvosChat(ServiceInterface):
    def __init__(self):
        super().__init__(DBUS_NAME)

    @method()
    def ShowWindow(self):
        bridge.set_pending.emit(False)
        gui.raise_()
        gui.showNormal()
        gui.activateWindow()
        gui.input.setFocus()


class SelectorConversaciones(QDialog):
    """Ventana con la lista completa: mejor que un menu si hay muchas."""

    def __init__(self, padre, items):
        super().__init__(padre)
        self.setWindowTitle(t("Conversaciones guardadas"))
        self.resize(560, 420)
        self.elegido = None
        self._items = items

        lay = QVBoxLayout(self)
        self.lista = QListWidget(self)
        self.lista.setAlternatingRowColors(True)
        for c in items:
            marca = "• " if c["id"] == (getattr(padre, "conv", None) or {}).get("id") else ""
            etiq = (f"{marca}{c['titulo']}\n"
                    f"      {historial_chat.cuando(c['modificado'])} · "
                    f"{c['n']} mensajes\n"
                    f"      {c['resumen']}")
            item = QListWidgetItem(etiq)
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setToolTip(c["titulo"])
            self.lista.addItem(item)
        if items:
            self.lista.setCurrentRow(0)
        self.lista.itemDoubleClicked.connect(self._aceptar)
        self.lista.currentItemChanged.connect(
            lambda *_: self._previsualizar())
        lay.addWidget(self.lista, 1)

        self.detalle = QTextEdit(self)
        self.detalle.setReadOnly(True)
        self.detalle.setMaximumHeight(130)
        lay.addWidget(self.detalle)

        fila = QHBoxLayout()
        btn_borrar = QPushButton(t("Borrar la seleccionada"), self)
        btn_borrar.clicked.connect(self._borrar)
        fila.addWidget(btn_borrar)
        fila.addStretch(1)
        botones = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel, self)
        botones.accepted.connect(self._aceptar)
        botones.rejected.connect(self.reject)
        fila.addWidget(botones)
        lay.addLayout(fila)
        self._previsualizar()

    def _seleccionada(self):
        it = self.lista.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _previsualizar(self):
        cid = self._seleccionada()
        conv = historial_chat.cargar(cid) if cid else None
        if not conv:
            self.detalle.clear()
            return
        lineas = []
        for m in conv.get("mensajes", [])[-30:]:
            quien = "Tu" if m.get("rol") == "u" else NOMBRE_ASISTENTE
            lineas.append(f"{quien}: {historial_chat.limpiar(m.get('texto'))}")
        self.detalle.setPlainText("\n\n".join(lineas))
        barra = self.detalle.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _borrar(self):
        cid = self._seleccionada()
        if not cid:
            return
        conv = historial_chat.cargar(cid)
        r = QMessageBox.question(
            self, t("Borrar conversación"),
            f"¿Borrar «{(conv or {}).get('titulo', '')}»?\n\n"
            "No se puede deshacer.")
        if r != QMessageBox.StandardButton.Yes:
            return
        historial_chat.borrar(cid)
        self._items = [c for c in self._items if c["id"] != cid]
        self.lista.clear()
        for c in self._items:
            item = QListWidgetItem(
                f"{c['titulo']}\n      "
                f"{historial_chat.cuando(c['modificado'])} · {c['n']} mensajes")
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.lista.addItem(item)
        if self._items:
            self.lista.setCurrentRow(0)

    def _aceptar(self):
        cid = self._seleccionada()
        if cid:
            self.elegido = cid
            self.accept()


class ChatWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(t("OVOS - Chat"))
        self.setMinimumSize(480, 420)
        self.resize(560, 520)

        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.lbl_head = QLabel(f"Chatea con {NOMBRE_ASISTENTE}")
        self.lbl_head.setStyleSheet("font-weight: bold;")
        head.addWidget(self.lbl_head)
        head.addStretch(1)
        self.btn_nueva = QPushButton(t("Nueva"), self)
        self.btn_nueva.setToolTip(t("Empezar una conversación en blanco "
                                        "(Ctrl+N)"))
        self.btn_nueva.clicked.connect(self._nueva_conversacion)
        head.addWidget(self.btn_nueva)
        self.btn_hist = QPushButton(t("Historial ▾"), self)
        self.btn_hist.setToolTip(t("Conversaciones guardadas (Ctrl+H). "
                                       "Clic derecho aquí para la lista."))
        self.btn_hist.clicked.connect(self._menu_historial)
        head.addWidget(self.btn_hist)
        lay.addLayout(head)

        # Clic derecho en cualquier parte de la cabecera: la misma lista.
        self.lbl_head.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lbl_head.customContextMenuRequested.connect(
            self._menu_historial)
        self.btn_hist.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_hist.customContextMenuRequested.connect(self._menu_historial)

        self.history = QTextEdit(self)
        self.history.setReadOnly(True)
        self.history.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(self.history, 1)

        row = QHBoxLayout()
        self.input = QLineEdit(self)
        self.input.setPlaceholderText(t("Escribe tu pregunta y pulsa Enter..."))
        self.input.returnPressed.connect(self._send)
        btn = QPushButton(t("Enviar"), self)
        btn.clicked.connect(self._send)
        row.addWidget(self.input, 1)
        row.addWidget(btn)
        lay.addLayout(row)

        self.pending = False
        self.conv = None
        self._ids = {}          # id -> acción del menú, para reutilizarlos
        bridge.append_user.connect(self._on_user_text)
        bridge.append_assistant.connect(self._on_assistant_text)
        bridge.set_pending.connect(self._on_set_pending)
        bridge.quit_requested.connect(self._on_quit)

        self._saludo = (f"<i>¡Hola! Soy {NOMBRE_ASISTENTE}. "
                        f"¿En qué te puedo ayudar?</i>")
        historial_chat.purgar_vacias()
        self._nueva_conversacion()
        self._crear_atajos()

    # ---------------- historial de conversaciones ----------------

    def _crear_atajos(self):
        for texto, slot in (("Ctrl+H", self._menu_historial),
                            ("Ctrl+N", self._nueva_conversacion)):
            ac = QAction(self)
            ac.setShortcut(texto)
            ac.triggered.connect(slot)
            self.addAction(ac)

    def _titulo_ventana(self):
        t = (self.conv or {}).get("titulo") or "Conversación nueva"
        return f"{t} — {NOMBRE_ASISTENTE}"

    def _abrir_ultima_o_nueva(self):
        """Al arrancar, sigue donde lo dejaste."""
        ultimas = historial_chat.listar(1)
        if ultimas:
            conv = historial_chat.cargar(ultimas[0]["id"])
            if conv:
                self.conv = conv
                self._pintar_conversacion(conv)
                self.setWindowTitle(self._titulo_ventana())
                return
        self._nueva_conversacion()

    def _nueva_conversacion(self):
        if self.pending and not self._confirmar_descartar():
            return
        self.conv = historial_chat.nueva()
        self.pending = False
        bridge.set_pending.emit(False)
        self.history.clear()
        self._append(self._saludo, "a")
        self.setWindowTitle(self._titulo_ventana())

    def _confirmar_descartar(self):
        r = QMessageBox.question(
            self, t("Conversación en curso"),
            "El asistente te está esperando una respuesta.\n\n"
            "Si empiezas otra conversación, esta se queda guardada pero "
            "la respuesta se enviará a la nueva.\n¿Seguir?")
        return r == QMessageBox.StandardButton.Yes

    def _pintar_conversacion(self, conv):
        """Vuelca los mensajes guardados en el cuadre de texto."""
        self.history.clear()
        for m in conv.get("mensajes", []):
            self._pintar_mensaje(m.get("rol", "a"), m.get("texto", ""),
                                 persistir=False)
        if not conv.get("mensajes"):
            self._append(self._saludo, "a")
        self._al_final()

    def _al_final(self):
        barra = self.history.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _menu_historial(self, _pos=None):
        """Despliega la lista de conversaciones guardadas."""
        menu = self._construir_menu_historial()
        menu.exec(self.btn_hist.mapToGlobal(
            self.btn_hist.rect().bottomLeft()))

    def _construir_menu_historial(self):
        """Menú con los titulos de lo guardado. Va aparte del que lo
        despliega para poder montarlo sin abrirlo (tests, inspección)."""
        menu = QMenu(self)
        acc = QAction(t("Abrir conversación…"), self)
        acc.triggered.connect(self._abrir_conversacion)
        menu.addAction(acc)

        renombrar = QAction(t("Renombrar esta…"), self)
        renombrar.triggered.connect(self._guardar_con_nombre)
        menu.addAction(renombrar)

        nueva = QAction(t("Nueva conversación"), self)
        nueva.triggered.connect(self._nueva_conversacion)
        menu.addAction(nueva)
        menu.addSeparator()

        menu.addSection(t("Conversaciones guardadas"))
        items = historial_chat.listar()
        if not items:
            vacio = QAction(t("(todavia no hay ninguna)"), self)
            vacio.setEnabled(False)
            menu.addAction(vacio)
        for c in items:
            marca = ("• " if c["id"] == (self.conv or {}).get("id") else "")
            etiq = (f"{marca}{c['titulo']}\n"
                    f"      {historial_chat.cuando(c['modificado'])} · "
                    f"{c['n']} mensajes")
            act = QAction(etiq, self)
            act.triggered.connect(lambda _=False, cid=c["id"]: self._cambiar_a(cid))
            self._ids[act] = c["id"]
            menu.addAction(act)

        menu.addSeparator()
        borrar = QAction(t("Borrar la conversación actual"), self)
        borrar.triggered.connect(self._borrar_actual)
        menu.addAction(borrar)
        return menu

    def _cambiar_a(self, cid):
        conv = historial_chat.cargar(cid)
        if not conv:
            return
        self.conv = conv
        self._pintar_conversacion(conv)
        self.setWindowTitle(self._titulo_ventana())
        self.input.setFocus()

    def _abrir_conversacion(self):
        """Selector en una ventana aparte, para listas largas."""
        dlg = SelectorConversaciones(self, historial_chat.listar(200))
        if dlg.exec() and dlg.elegido:
            self._cambiar_a(dlg.elegido)

    def _guardar_con_nombre(self):
        if not self.conv:
            return
        texto, ok = QInputDialog.getText(self, "Renombrar conversación",
                                         "Título:", text=self.conv["titulo"])
        if ok and texto.strip():
            actualizado = historial_chat.renombrar(self.conv["id"], texto.strip())
            if actualizado:
                self.conv = actualizado
                self.setWindowTitle(self._titulo_ventana())

    def _borrar_actual(self):
        if not self.conv:
            return
        cid, titulo = self.conv["id"], self.conv["titulo"]
        r = QMessageBox.question(
            self, t("Borrar conversación"),
            f"¿Seguro que quieres borrar «{titulo}»?\n\n"
            "No se puede deshacer.")
        if r != QMessageBox.StandardButton.Yes:
            return
        historial_chat.borrar(cid)
        self.conv = None
        self._nueva_conversacion()

    def _on_quit(self):
        print("cerrando ventana de chat (orden del asistente)", flush=True)
        self.close()

    # ------- apariencia -------
    @staticmethod
    def _escapa(txt):
        return (txt.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;"))

    def _append(self, html, kind):
        color = {"u": "#1b7f3b", "a": "#2a5db0"}.get(kind, "#222")
        self.history.append(f'<span style="color:{color}">{html}</span>')
        self._al_final()

    def _pintar_mensaje(self, kind, txt, persistir=True):
        """Pinta un mensaje y, si toca, lo deja guardado."""
        if not (txt or "").strip():
            return
        who = "<b>[Tu]</b>" if kind == "u" else f"<b>[{NOMBRE_ASISTENTE}]</b>"
        self._append(f"{who} {self._escapa(txt)}", kind)
        if persistir and self.conv is not None:
            # si al guardar cambia el titulo, se refleja en la ventana
            if historial_chat.anadir(self.conv, kind, txt):
                self.setWindowTitle(self._titulo_ventana())

    def _on_user_text(self, txt):
        self._pintar_mensaje("u", txt)

    def _on_assistant_text(self, txt):
        self._pintar_mensaje("a", txt)

    def _on_set_pending(self, p):
        self.pending = p
        if p:
            self.input.setPlaceholderText(t("Responde a la pregunta del asistente..."))
        else:
            self.input.setPlaceholderText(t("Escribe tu pregunta y pulsa Enter..."))
        self.input.setFocus()

    # ------- envio -------
    @staticmethod
    def _quiere_cerrar(txt):
        t = txt.strip().lower()
        if re.match(r"^no\b", t):
            return False
        return bool(re.search(
            r"\b(cierra|cerrar|cerra|cerrame|cierra me|cierrame)\b"
            r".*\b(ventana|chat|pregunta|preguntas|cuadro)\b", t))

    def _send(self):
        txt = self.input.text().strip()
        if not txt:
            return
        self.input.clear()
        try:
            _fija_idioma()
        except Exception:
            pass
        if self._quiere_cerrar(txt):
            print("cierre local por texto del usuario", flush=True)
            self.close()
            return
        bridge.append_user.emit(txt)
        if self.pending:
            self.pending = False
            bridge.set_pending.emit(False)
            if ovos_bus:
                ovos_bus.emit(OVOSMessage(
                    "ovos.asistente.respuesta",
                    {"ses_id": SES_ID, "texto": txt}))
        else:
            if ovos_bus:
                ovos_bus.emit(OVOSMessage(
                    "recognizer_loop:utterance",
                    {"utterances": [txt], "lang": idioma.codigo_ovos()},
                    CTX))

    # ------- control -------
    def closeEvent(self, event):
        super().closeEvent(event)


# ---------------- bus OVOS ----------------

def _ses_id(msg) -> str:
    try:
        return (msg.context or {}).get("session", {}).get("session_id") or ""
    except Exception:
        return ""


def on_speak(msg):
    if _ses_id(msg) != SES_ID:
        return
    u = (msg.data or {}).get("utterance") or ""
    if not u or u == "...":
        return
    bridge.append_assistant.emit(u)
    # El TTS (ovos-audio) ignora 'speak' de sesiones 'chat' por diseno:
    # solo reproduce la sesion default. El skill asisistente espeja sus
    # propias respuestas para que suenen; las de otros skills (p. ej.
    # salutaciones) no: aqui se espejan tambien para que siempre se oigan.
    skill = (msg.data or {}).get("meta", {}) or {}
    if str(skill.get("skill", "")).startswith("lucia"):
        return
    ovos_bus.emit(OVOSMessage("speak", {
        "utterance": u,
        "meta": {"skill": "chat-window"},
    }, {}))


def on_pregunta(msg):
    # el texto de la pregunta ya llega como 'speak' (sesion chat);
    # aqui solo se marca el estado de espera para que la respuesta se
    # envie por el canal de texto.
    if (msg.data or {}).get("ses_id") != SES_ID:
        return
    bridge.set_pending.emit(True)


def on_cierra(msg):
    print("on_cierra recibido", flush=True)
    bridge.quit_requested.emit()


def ovos_bus_thread():
    global ovos_bus
    ovos_bus = MessageBusClient()
    ovos_bus.run_in_thread()
    ovos_bus.on("speak", on_speak)
    ovos_bus.on("ovos.asistente.pregunta", on_pregunta)
    ovos_bus.on("ovos.asistente.cierra_chat", on_cierra)
    _ = ovos_bus  # mantener la referencia global


# ---------------- D-Bus (instancia unica + ShowWindow) ----------------

async def try_show():
    """Si ya hay una instancia, le pide mostrar la ventana y devuelve True."""
    try:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        await bus.call(DBusMessage(
            destination=DBUS_NAME, path=DBUS_PATH,
            interface=DBUS_NAME, member="ShowWindow",
            signature="", body=[]))
        return True
    except Exception:
        return False


async def dbus_register():
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    named = await bus.request_name(DBUS_NAME)
    if not named:
        return  # otra instancia gano el nombre
    bus.export(DBUS_PATH, OvosChat())
    await asyncio.Event().wait()


def dbus_thread():
    asyncio.run(dbus_register())


# ---------------- main ----------------

# Icono de la aplicacion. Sin esto Qt dibuja su icono por defecto (el
# circulo con la w), que es lo que se veía en las ventanas.
ICONO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "iconos", "lucIA.ico")


def _poner_icono(app, widget=None):
    """Aplica el icono a la app entera y al widget dado."""
    icono = QIcon(ICONO)
    if icono.isNull():
        return
    app.setWindowIcon(icono)
    if widget is not None:
        widget.setWindowIcon(icono)


def _fija_idioma():
    """Las ventanas hablan el idioma elegido; en automatico, el de la voz."""
    from ovos_user_config import VOICES, current_tts_voice
    voz = current_tts_voice()
    lang = ""
    for _etiqueta, nombre, cod, _g in VOICES:
        if nombre == voz:
            lang = cod
            break
    idioma.resuelve(CONFIG_PATH, lang)
    SESSION["lang"] = idioma.codigo_ovos()
    return idioma.idioma_activo()


def main():
    global gui
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    _fija_idioma()
    _poner_icono(app)

    threading.Thread(target=ovos_bus_thread, daemon=True).start()

    # Servidor D-Bus; si el nombre ya lo sirve otra instancia, la hebra termina.
    t = threading.Thread(target=dbus_thread, daemon=True)
    t.start()
    deadline = time.monotonic() + 4.0
    while t.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not t.is_alive():
        # Otra instancia ya corre: pedirle que se muestre y salir.
        try:
            ok = asyncio.run(try_show())
        except Exception:
            ok = False
        return 0 if ok else 1

    gui = ChatWindow()
    _poner_icono(app, gui)
    gui.show()
    return app.exec()


if __name__ == "__main__":
    main()