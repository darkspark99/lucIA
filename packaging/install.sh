#!/usr/bin/env bash
# ============================================================
#  lucIA - instalador para cualquier distro
#  Instala y configura todo el asistente de voz para Linux:
#   - skill OVOS (plugin pip) con el fallback de conversación
#   - widget plasmoid (onda de voz + icono de estado en KDE)
#   - bridge del widget (servicio systemd de usuario)
#   - push-to-talk (micro al pulsar un atajo)
# Uso:  ./packaging/install.sh
# ============================================================
set -e
cd "$(dirname "$0")/.."
REPO="$PWD"

if [ "$(id -u)" = "0" ]; then
    echo "Error: ejecuta SIN sudo (instala en tu HOME)."
    exit 1
fi

confirm() {
    printf '%s [s/N] ' "$1"
    read -r r
    case "$r" in
        s|S|si|Si|Sí|sí|y|Y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

echo "== Paquetes base (python-pip) =="
if ! command -v pip3 >/dev/null 2>&1; then
    echo "Instalando pip..."
    case "$(command -v apt-get dnf zypper pacman 2>/dev/null | head -1)" in
        *apt-get) sudo apt-get install -y python3-pip ;;
        *dnf)     sudo dnf install -y python3-pip ;;
        *zypper)  sudo zypper --non-interactive install python3-pip ;;
        *pacman)  sudo pacman -S --noconfirm python-pip ;;
    esac
fi

echo "== Skill OVOS (plugin) =="
pip3 install "$REPO" --break-system-packages 2>/dev/null \
    || pip3 install --user "$REPO" 2>/dev/null \
    || pip3 install "$REPO"

# Interprete con las dependencias de OVOS: el venv si existe, si no python3.
PYTHON="$(command -v python3)"
if [ -x "$HOME/.venvs/ovos/bin/python3" ]; then
    PYTHON="$HOME/.venvs/ovos/bin/python3"
fi

# Instala una unit systemd sustituyendo @PYTHON@ por el interprete detectado.
instala_unit() {
    local src="$1"
    mkdir -p "$HOME/.config/systemd/user"
    sed "s|@PYTHON@|$PYTHON|g" "$src" \
        > "$HOME/.config/systemd/user/$(basename "$src")"
}

echo "== Widget plasmoid (requiere KDE Plasma) =="
if [ -d "$HOME/.local/share/plasma/plasmoids" ]; then
    mkdir -p "$HOME/.local/share/plasma/plasmoids"
    cp -r "$REPO/plasma/org.ovos.plasmoid" \
        "$HOME/.local/share/plasma/plasmoids/"
    instala_unit "$REPO/plasma/org.ovos.plasmoid/ovos-plasmoid-bridge.service"
    systemctl --user daemon-reload 2>/dev/null || true
    # Backend que alimenta al widget (la service lo ejecuta desde aqui)
    mkdir -p "$HOME/.local/share/ovos"
    cp "$REPO/plasma/org.ovos.plasmoid/contents/code/ovos_backend.py" \
        "$HOME/.local/share/ovos/ovos_plasmoid_bridge.py"
    echo "Widget copiado. Añádelo en el escritorio pulsando"
    echo "  widget añadir -> 'OVOS'."
else
    echo "No se detectó Plasma; se omite el widget."
fi

echo "== Ventana de chat (canal de texto separado del microfono) =="
mkdir -p "$HOME/.local/share/ovos"
cp "$REPO/chat/chat_window.py" "$HOME/.local/share/ovos/chat_window.py"
cp "$REPO/chat/historial_chat.py" "$HOME/.local/share/ovos/historial_chat.py"
cp "$REPO/chat/idioma.py" "$HOME/.local/share/ovos/idioma.py"
# Iconos de las ventanas (sin esto Qt dibuja su icono por defecto)
for _d in "$REPO/chat/iconos" "$REPO/ovos_gui/iconos"; do
    [ -d "$_d" ] || continue
    mkdir -p "$HOME/.local/share/ovos/iconos"
    cp "$_d"/lucIA.ico "$_d"/lucIA.png "$HOME/.local/share/ovos/iconos/" 2>/dev/null || true
done
echo "  Se abre desde el icono del systray -> 'Escribir pregunta...'."

echo "== Systray (icono de estado + menu) =="
cp "$REPO/systray/status_indicator.py" "$HOME/.local/share/ovos/"

echo "== Configuracion (GUI + voz) =="
cp "$REPO/ovos_gui/ovos_user_config.py" "$HOME/.local/share/ovos/"
cp "$REPO/ovos_gui/ovos_config_gui.py" "$HOME/.local/share/ovos/"
cp "$REPO/ovos_gui/idioma.py" "$HOME/.local/share/ovos/idioma.py"
# Tabla de idiomas: NO sobreescribir si ya existe (el usuario puede
# haber añadido idiomas). Solo se instala la version de fabrica.
if [ ! -f "$HOME/.local/share/ovos/idiomas.json" ]; then
    cp "$REPO/ovos_gui/idiomas.json" "$HOME/.local/share/ovos/idiomas.json"
fi
# Idiomas extra (los de idiomas.json) en 'secondary_langs' de mycroft.conf,
# para que padatious entrene y reconozca mas de un idioma.
python3 - "$HOME/.local/share/ovos" <<'PY' 2>/dev/null || true
import sys, os
sys.path.insert(0, os.path.abspath(sys.argv[1]))
try:
    import idioma, ovos_user_config as u
    u.apply_secondary_langs(idioma.codigos_secundarios())
    u.ensure_tts_module()
except Exception:
    pass
PY

echo "== Push-to-talk (micro con atajo de teclado) =="
mkdir -p "$HOME/.local/share/ovos/ptt"
cp "$REPO/ptt/push_to_talk.py" "$HOME/.local/share/ovos/ptt/"
if [ -n "$DISPLAY" ]; then
    echo "Enlaza el PTT a un atajo (Preferencias del sistema ->"
    echo "  Atajos -> Globales -> Personalizado):"
    echo "  comando:  python3 ~/.local/share/ovos/ptt/push_to_talk.py"
    echo "  efecto:   mantén pulsado para abrir el micro; al soltar,"
    echo "            deja de escuchar de inmediato."
fi

echo "== Scripts de arranque =="
if [ -d "$REPO/packaging/scripts" ]; then
    cp "$REPO/packaging/scripts/"*.sh "$HOME/.local/share/ovos/"
    chmod +x "$HOME/.local/share/ovos/"*.sh 2>/dev/null || true
fi

echo "== Servicios systemd de usuario (systray, PTT, entorno) =="
if [ -d "$REPO/packaging/systemd" ]; then
    for _u in "$REPO/packaging/systemd/"*.service; do
        [ -f "$_u" ] || continue
        instala_unit "$_u"
    done
    systemctl --user daemon-reload 2>/dev/null || true
    # Habilitar/arrancar (sin abortar si algo no está disponible todavía)
    for _s in ovos-env-import ovos-status-indicator ovos-ptt ovos-plasmoid-bridge; do
        systemctl --user enable --now "$_s.service" 2>/dev/null || true
    done
    echo "Servicios instalados. Comprueba con:  systemctl --user status ovos-status-indicator"
fi

echo
if [ -n "$DISPLAY" ]; then
    echo "Se abrirá la configuración opcional (voz hombre/mujer/idioma y"
    echo "  apariencia del waveform). Si no quieres, cancela el diálogo."
    sleep 2
    python3 "$HOME/.local/share/ovos/ovos_config_gui.py" 2>/dev/null || \
        echo "  (configuración omitida)"
fi

echo
echo "Completado. Reinicia los servicios:"
echo "  systemctl --user restart ovos.service"
echo "  (si tienes Plasma) systemctl --user restart plasma-plasmashell.service"