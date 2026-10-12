#!/usr/bin/env python3
"""OVOS Status Indicator - KDE Plasma system tray icon via StatusNotifierItem D-Bus protocol."""
import asyncio
import os
import signal as signal_module
import sys
import threading
import time
from io import BytesIO
from PIL import Image, ImageDraw
from dbus_next import BusType
from dbus_next.aio import MessageBus
from dbus_next.message import Message
from dbus_next.service import ServiceInterface, method, signal as dbus_signal, dbus_property
from dbus_next.constants import PropertyAccess
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message as OVOSMessage

IDLE = 0
LISTENING = 1
PROCESSING = 2
COLORS = {
    IDLE: (160, 160, 160),
    LISTENING: (0, 200, 0),
    PROCESSING: (0, 120, 255),
}
SIZE = 22

state = IDLE
bus_ovos = None
bus_dbus = None
sni_iface = None
proc_end_timer = None
loop = None


def pil_to_argb32(img):
    w, h = img.size
    data = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            r, g, b, a = img.getpixel((x, y))
            idx = (y * w + x) * 4
            data[idx] = a
            data[idx + 1] = r
            data[idx + 2] = g
            data[idx + 3] = b
    return bytes(data)


def make_icon_pixmap(color):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    margin = 2
    d.ellipse(
        [margin, margin, SIZE - margin - 1, SIZE - margin - 1],
        fill=(*color, 255),
    )
    return [SIZE, SIZE, pil_to_argb32(img)]


STATUS_TEXTS = {
    IDLE: "Inactivo",
    LISTENING: "Escuchando...",
    PROCESSING: "Procesando...",
}


def _entorno_gui():
    """Entorno correcto para lanzar ventanas (chat, configuración…).

    Este proceso es solo el icono de la bandeja: el dibujo lo hace
    plasmashell, asi que puede vivir sin DISPLAY ni WAYLAND_DISPLAY en su
    entorno. Pero las ventanas que lanza si necesitan pantalla, y si
    heredan este entorno vacio, Qt prueba con X11, no conecta y se cae
    (kdialog llegaba a hacer un core dump). Aqui se deduce la pantalla
    real de la sesion en vez de confiar en lo que haya heredado.
    """
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime

    # Primero Wayland: si hay socket, ya no tenemos nada que buscar.
    if not env.get("WAYLAND_DISPLAY"):
        try:
            for nombre in sorted(os.listdir(runtime)):
                if nombre.startswith("wayland-") and not nombre.endswith(".lock"):
                    env["WAYLAND_DISPLAY"] = nombre
                    break
        except OSError:
            pass

    if env.get("WAYLAND_DISPLAY"):
        env["XDG_SESSION_TYPE"] = "wayland"
        # Hay Wayland de verdad. No dejar que Qt se caiga a xcb, que aqui
        # no tiene a quien conectarse... y tampoco adivinar un DISPLAY: en
        # una sesion asi puede haber sockets X de otras cosas, y un
        # DISPLAY equivocado es peor que ninguno.
        env.setdefault("QT_QPA_PLATFORM", "wayland")
    elif not env.get("DISPLAY"):
        # Sesion X11: los sockets viven en /tmp/.X11-unix
        try:
            for nombre in sorted(os.listdir("/tmp/.X11-unix")):
                if nombre.startswith("X") and nombre[1:].isdigit():
                    env["DISPLAY"] = ":" + nombre[1:]
                    break
        except OSError:
            pass

        # La cookie de X11, si el display necesita una
        if env.get("DISPLAY") and not env.get("XAUTHORITY"):
            try:
                cookies = sorted(
                    (os.path.getmtime(os.path.join(runtime, f)), f)
                    for f in os.listdir(runtime) if f.startswith("xauth_"))
                if cookies:
                    env["XAUTHORITY"] = os.path.join(runtime, cookies[-1][1])
            except OSError:
                pass

    # El bus de sesion: sin esto, dbus_next busca un fichero por pantalla en
    # ~/.dbus/session-bus/, no lo encuentra, y la ventana se cae callada
    # creyendo que otra instancia ya esta en marcha.
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        sock = f"/run/user/{os.getuid()}/bus"
        if os.path.exists(sock):
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={sock}"
    return env


class StatusNotifierItem(ServiceInterface):
    def __init__(self, app_name):
        super().__init__("org.kde.StatusNotifierItem")
        self._pixmaps = [make_icon_pixmap(COLORS[IDLE])]
        self._title = app_name
        self._status = "Passive"
        self._tooltip = ["OVOS", [], "Inactivo", ""]
        self._category = "ApplicationStatus"
        self._id = "ovos-status"
        self._window_id = 0

    def update_icon(self, color, status_text):
        self._pixmaps = [make_icon_pixmap(color)]
        self._tooltip = ["OVOS", [], status_text, ""]
        self.NewIcon()
        self.NewToolTip()

    @dbus_signal()
    def NewIcon(self):
        pass

    @dbus_signal()
    def NewToolTip(self):
        pass

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return self._category

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return self._id

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return self._title

    @dbus_property(access=PropertyAccess.READWRITE)
    def Status(self) -> "s":
        return self._status

    @Status.setter
    def Status(self, val: "s"):
        self._status = val

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "u":
        return self._window_id

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return "ovos"

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionMovieName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> '(sa(iiay)ss)':
        return self._tooltip

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> 'a(iiay)':
        return self._pixmaps

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconPixmap(self) -> 'a(iiay)':
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconPixmap(self) -> 'a(iiay)':
        return []

    @method()
    def Activate(self, x: "i", y: "i"):
        stop_speech()

    @method()
    def SecondaryActivate(self, x: "i", y: "i"):
        pass

    @method()
    async def ContextMenu(self, x: "i", y: "i"):
        import subprocess as sp_mod
        proc = await asyncio.create_subprocess_exec(
            "kdialog", "--title", "OVOS", "--menu", "",
            "escribir", "Escribir pregunta...",
            "config",   "Configuración...",
            "restart",  "Reiniciar OVOS",
            "stop",     "Salir de OVOS",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_entorno_gui(),
        )
        stdout, _ = await proc.communicate()
        choice = stdout.decode().strip() if stdout else ""
        if choice == "escribir":
            sp_mod.Popen(
                [sys.executable,
                 os.path.expanduser("~/.local/share/ovos/chat_window.py")],
                env=_entorno_gui())
        elif choice == "config":
            sp_mod.Popen(
                [sys.executable,
                 os.path.expanduser("~/.local/share/ovos/ovos_config_gui.py")],
                env=_entorno_gui())
        elif choice == "restart":
            sp_mod.Popen(["systemctl", "--user", "restart", "ovos.service"])
        elif choice == "stop":
            sp_mod.Popen(["systemctl", "--user", "stop", "ovos.service"])

    @method()
    def Scroll(self, delta: "i", orientation: "s"):
        pass


def stop_speech():
    global proc_end_timer
    if proc_end_timer:
        proc_end_timer.cancel()
        proc_end_timer = None
    if bus_ovos:
        bus_ovos.emit(OVOSMessage("mycroft.audio.speech.stop"))
    set_state(IDLE)


def set_state(new_state):
    global state
    state = new_state
    if sni_iface:
        sni_iface.update_icon(COLORS[state], STATUS_TEXTS[state])


def on_record_begin(msg):
    set_state(LISTENING)


def on_record_end(msg):
    pass


def on_utterance(msg):
    global proc_end_timer
    if proc_end_timer:
        proc_end_timer.cancel()
    proc_end_timer = threading.Timer(8.0, on_proc_timeout)
    proc_end_timer.daemon = True
    proc_end_timer.start()
    set_state(PROCESSING)


def on_proc_timeout(_message=None):
    global proc_end_timer
    proc_end_timer = None
    if state == PROCESSING:
        set_state(IDLE)


def ovos_bus_thread():
    global bus_ovos
    bus_ovos = MessageBusClient()
    bus_ovos.run_in_thread()
    bus_ovos.on("recognizer_loop:record_begin", on_record_begin)
    bus_ovos.on("recognizer_loop:record_end", on_record_end)
    bus_ovos.on("recognizer_loop:utterance", on_utterance)
    bus_ovos.on("recognizer_loop:speech.recognition.unknown", on_proc_timeout)
    while True:
        time.sleep(1)


REGISTER_MSG = Message(
    destination="org.kde.StatusNotifierWatcher",
    path="/StatusNotifierWatcher",
    interface="org.kde.StatusNotifierWatcher",
    member="RegisterStatusNotifierItem",
    signature="s",
    body=["/StatusNotifierItem"],
)


async def register_with_watcher(bus):
    for attempt in range(120):
        try:
            reply = await bus.call(REGISTER_MSG)
            print("Registered with StatusNotifierWatcher", flush=True)
            return
        except Exception as e:
            if attempt == 0:
                print(f"Waiting for StatusNotifierWatcher... ({e})", flush=True)
        await asyncio.sleep(2)
    print("Failed to register after 120 attempts", flush=True)


_watcher_reconnect = False


def on_dbus_signal(msg):
    global _watcher_reconnect
    if (msg.member == "NameOwnerChanged"
            and len(msg.body) >= 3
            and msg.body[0] == "org.kde.StatusNotifierWatcher"
            and msg.body[2]  # new owner is non-empty → watcher started
            and not _watcher_reconnect):
        _watcher_reconnect = True
        asyncio.run_coroutine_threadsafe(
            _delayed_reregister(), loop)


async def _delayed_reregister():
    global _watcher_reconnect
    await asyncio.sleep(2)
    _watcher_reconnect = False
    if bus_dbus:
        await register_with_watcher(bus_dbus)


async def main():
    global sni_iface, loop, bus_dbus
    loop = asyncio.get_running_loop()

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    bus_dbus = bus
    sni_iface = StatusNotifierItem("OVOS")
    bus.export("/StatusNotifierItem", sni_iface)

    pid = os.getpid()
    service_name = f"org.kde.StatusNotifierItem-{pid}-1"
    await bus.request_name(service_name)

    await register_with_watcher(bus)

    # Watch for StatusNotifierWatcher restarts (e.g. kded6 module reload)
    watch_match = (
        "type='signal',"
        "sender='org.freedesktop.DBus',"
        "interface='org.freedesktop.DBus',"
        "member='NameOwnerChanged',"
        "arg0='org.kde.StatusNotifierWatcher'"
    )
    await bus.call(Message(
        destination="org.freedesktop.DBus",
        path="/org/freedesktop/DBus",
        interface="org.freedesktop.DBus",
        member="AddMatch",
        signature="s",
        body=[watch_match],
    ))
    bus.add_message_handler(on_dbus_signal)

    t = threading.Thread(target=ovos_bus_thread, daemon=True)
    t.start()

    signal_module.signal(signal_module.SIGTERM, lambda *a: os._exit(0))
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
