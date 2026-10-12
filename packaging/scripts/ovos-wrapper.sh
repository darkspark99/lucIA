#!/bin/bash
# OVOS service wrapper: import session environment before launching
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

_import() {
    local var="$1"
    local val
    val=$(systemctl --user show-environment 2>/dev/null | grep "^$var=" | head -1 | cut -d= -f2-)
    if [ -n "$val" ]; then
        export "$var=$val"
    fi
}

# 1. Importar el entorno de la sesión gráfica desde el gestor de systemd
#    (KDE lo exporta en el inicio de sesión: DISPLAY, WAYLAND_DISPLAY,
#    XAUTHORITY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS...).
for var in DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_RUNTIME_DIR \
           XDG_SESSION_TYPE QT_WAYLAND_RECONNECT DBUS_SESSION_BUS_ADDRESS; do
    _import "$var"
done

# 2. Fallback de detección si faltara WAYLAND_DISPLAY/DISPLAY
if [ -z "$WAYLAND_DISPLAY" ]; then
    for sock in "$XDG_RUNTIME_DIR"/wayland-*; do
        if [ -S "$sock" ]; then
            export WAYLAND_DISPLAY="$(basename "$sock")"
            break
        fi
    done
fi
if [ -z "$WAYLAND_DISPLAY" ] && [ -z "$DISPLAY" ]; then
    for d in /tmp/.X11-unix/X[0-9]; do
        [ -S "$d" ] || continue
        dpy=":${d##*/X}"
        if command -v xdotool &>/dev/null &&
           DISPLAY="$dpy" xdotool getdisplaygeometry >/dev/null 2>&1; then
            export DISPLAY="$dpy"
            break
        fi
    done
fi

# 3. En sesión Wayland, las apps Qt (kdialog, ventana de chat, GUI de
#    configuración) deben usar el plugin wayland y no xcb.
if [ -n "$WAYLAND_DISPLAY" ]; then
    export QT_QPA_PLATFORM=wayland
fi

exec "$@"