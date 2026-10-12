#!/usr/bin/env python3
"""OVOS Asistente - Configuracion de voz y apariencia del widget."""
import json
import os
import signal
import subprocess
import sys
import copy
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette, QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFormLayout, QGroupBox, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame,
    QComboBox, QCheckBox, QLineEdit, QPushButton, QSlider, QLabel,
    QColorDialog, QMessageBox, QDialog, QDialogButtonBox, QRadioButton,
    QTextEdit,
)

import idioma
from idioma import t
from ovos_user_config import (
    CONFIG_PATH, VOICES, STYLES, load_config, save_config,
    current_tts_voice, apply_tts_voice, apply_secondary_langs,
    listar_modelos, modelo_actual,
    set_modelo, PROVEEDORES, MODELOS_CLAVE, leer_clave, guardar_clave,
    tamano_modelo, espacio_libre,
)

STYLE_TO_KEY = {v: k for k, v in STYLES.items()}


class ColorButton(QPushButton):
    def __init__(self, color="#ff2200", title="Color", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._title = title
        self.setText(self._color.name())
        self.setMinimumWidth(150)   # fijo se comia el texto con letra grande
        self._paint()
        self.clicked.connect(self._pick)

    def _paint(self):
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Button, self._color)
        self.setPalette(pal)
        self.setText(self._color.name())

    def _pick(self):
        c = QColorDialog.getColor(self._color, self, f"Color - {self._title}")
        if c.isValid():
            self._color = c
            self._paint()

    def color(self):
        return self._color


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        cfg = load_config()
        self._cfg = cfg
        self._gui = cfg.get("gui", {})
        self._current_voice = current_tts_voice()
        idioma.resuelve(CONFIG_PATH, self._lang_de_voz())
        self.setWindowTitle(t("Configuración de OVOS Asistente"))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        # Con letra grande (p. ej. 15pt a 120 DPI) el formulario pide mas alto
        # que la pantalla y Qt lo recortaba: se veian lineas de texto sin
        # poder leerlas. Va dentro de un area desplazable, asi siempre se
        # llega a todo vaya el tamaño de letra que se use. Los botones se
        # quedan fuera, abajo, siempre a la vista.
        self.area = QScrollArea(self)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.contenido = QWidget()
        self._dentro = QVBoxLayout(self.contenido)
        self._dentro.setContentsMargins(0, 0, 0, 0)
        self.area.setWidget(self.contenido)
        layout.addWidget(self.area)

        # textos de los addRow("rotulo", widget), para poder retraducirlos
        self._rotulos = []

        box_asist = QGroupBox(t("Asistente"))
        form_asist = QFormLayout(box_asist)
        self.edit_nombre = QLineEdit(_current_nombre())
        self.edit_nombre.setPlaceholderText(t("p. ej. lucIA, SIA, marIA..."))
        form_asist.addRow(t("Nombre del asistente:"), self.edit_nombre)
        form_asist.addRow(
            "",
            QLabel(t("Con este nombre te saluda la ventana de chat y "
                      "se presenta el asistente.")))
        self._dentro.addWidget(box_asist)

        box_modelo = QGroupBox(t("Modelo de lenguaje"))
        vbox_modelo = QVBoxLayout(box_modelo)
        cfg_modelo = cfg.get("modelo") or {}
        self._cfg_modelo = cfg_modelo
        self._es_remoto = cfg_modelo.get("tipo") == "remoto"

        # --- radio: local o remoto ---
        fila_tipo = QHBoxLayout()
        self.rb_local = QRadioButton("Local (ollama, en este equipo)")
        self.rb_remoto = QRadioButton("Remoto (API de pago por internet)")
        self.rb_local.setChecked(not self._es_remoto)
        self.rb_remoto.setChecked(self._es_remoto)
        self.rb_local.toggled.connect(self._alternar_tipo)
        fila_tipo.addWidget(self.rb_local)
        fila_tipo.addWidget(self.rb_remoto)
        fila_tipo.addStretch(1)
        vbox_modelo.addLayout(fila_tipo)

        # --- local ---
        self.box_local = QGroupBox(t("Modelos locales"))
        form_local = QFormLayout(self.box_local)
        self.combo_modelo = QComboBox()
        self.combo_modelo.setMinimumWidth(360)
        self._modelos = []
        self._poblar_modelos()
        btn_recargar = QPushButton(t("Actualizar lista"))
        btn_recargar.clicked.connect(self._poblar_modelos)
        form_local.addRow(t("Modelo instalado:"), self.combo_modelo)
        form_local.addRow("", btn_recargar)

        linea = QHBoxLayout()
        self.combo_descargar = QComboBox()
        self.combo_descargar.setMinimumWidth(280)
        for nombre, uso in MODELOS_CLAVE:
            self.combo_descargar.addItem(f"{nombre}  —  {uso}", nombre)
        self.combo_descargar.currentIndexChanged.connect(self._ver_tamano)
        btn_ver = QPushButton(t("Ver pasos y espacio"))
        btn_ver.clicked.connect(self._ver_tamano)
        linea.addWidget(self.combo_descargar, 1)
        linea.addWidget(btn_ver)
        caja_desc = QWidget()
        caja_desc.setLayout(linea)
        form_local.addRow(t("Instalar otro:"), caja_desc)

        self.txt_pasos = QTextEdit()
        self.txt_pasos.setReadOnly(True)
        self.txt_pasos.setMinimumHeight(140)
        self.txt_pasos.setMaximumHeight(240)
        self.txt_pasos.setVisible(False)
        form_local.addRow("", self.txt_pasos)

        btn_instalar = QPushButton(t("Instalar el modelo seleccionado…"))
        btn_instalar.clicked.connect(self._instalar)
        form_local.addRow("", btn_instalar)
        self.lbl_modelo = QLabel()
        form_local.addRow("", self.lbl_modelo)
        self.lbl_modelo.setWordWrap(True)
        vbox_modelo.addWidget(self.box_local)

        # --- remoto ---
        self.box_remoto = QGroupBox(t("Modelo remoto (API)"))
        form_remoto = QFormLayout(self.box_remoto)
        self.combo_proveedor = QComboBox()
        self.combo_proveedor.setMinimumWidth(360)
        for clave, info in PROVEEDORES.items():
            self.combo_proveedor.addItem(info["nombre"], clave)
        sel = self.combo_proveedor.findData(cfg_modelo.get("proveedor")
                                            or "anthropic")
        if sel >= 0:
            self.combo_proveedor.setCurrentIndex(sel)
        self.combo_proveedor.currentIndexChanged.connect(self._cambio_proveedor)
        form_remoto.addRow(t("Proveedor:"), self.combo_proveedor)

        self.edit_url = QLineEdit()
        self.edit_url.setPlaceholderText(
            t("solo para «otro»: https://mi-servidor/v1/chat/completions"))
        form_remoto.addRow(t("URL (opcional):"), self.edit_url)

        self.edit_clave = QLineEdit()
        self.edit_clave.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_clave.setPlaceholderText(t("pega aqui tu clave de API"))
        form_remoto.addRow(t("Clave de API:"), self.edit_clave)

        self.edit_modelo_remoto = QLineEdit()
        self.edit_modelo_remoto.setPlaceholderText(
            t("identificador exacto del modelo, p. ej. claude-sonnet-5"))
        form_remoto.addRow(t("Modelo:"), self.edit_modelo_remoto)

        fila_botones = QHBoxLayout()
        btn_listar = QPushButton(t("Ver modelos de mi cuenta"))
        btn_listar.clicked.connect(self._listar_modelos_remotos)
        btn_probar = QPushButton(t("Probar conexión"))
        btn_probar.clicked.connect(self._probar_remoto)
        fila_botones.addWidget(btn_listar)
        fila_botones.addWidget(btn_probar)
        caja_bot = QWidget()
        caja_bot.setLayout(fila_botones)
        form_remoto.addRow("", caja_bot)

        self.lista_modelos = QTextEdit()
        self.lista_modelos.setReadOnly(True)
        self.lista_modelos.setMinimumHeight(160)
        self.lista_modelos.setMaximumHeight(260)
        self.lista_modelos.setVisible(False)
        form_remoto.addRow("", self.lista_modelos)

        self.lbl_clave = QLabel()
        self.lbl_clave.setWordWrap(True)
        form_remoto.addRow("", self.lbl_clave)
        vbox_modelo.addWidget(self.box_remoto)

        self.edit_url.setText(cfg_modelo.get("url") or "")
        self.edit_modelo_remoto.setText(cfg_modelo.get("remoto") or "")
        self._cambio_proveedor()
        self._alternar_tipo()
        self._pintar_estado()
        self._dentro.addWidget(box_modelo)

        box_tts = QGroupBox(t("Voz del asistente"))
        form = QFormLayout(box_tts)
        self.combo_voice = QComboBox()
        self.combo_voice.setMinimumWidth(360)
        for label, name, lang, genero in VOICES:
            self.combo_voice.addItem(f"{label}  ·  piper:{name}", name)
        self.cb_custom = QCheckBox("Voz personalizada (nombre piper)")
        self.edit_custom = QLineEdit()
        self.edit_custom.setPlaceholderText(t("p. ej. es_EU-female-medium"))
        self.edit_custom.setEnabled(False)
        self.cb_custom.toggled.connect(
            lambda on: self.edit_custom.setEnabled(on))
        voice_label = "sin configurar" if not self._current_voice else self._current_voice
        sel = self.combo_voice.findData(self._current_voice)
        if sel >= 0:
            self.combo_voice.setCurrentIndex(sel)
        else:
            self.cb_custom.setChecked(True)
            self.edit_custom.setText(self._current_voice or "")
        form.addRow(t("Voz:"), self.combo_voice)
        form.addRow("", self.cb_custom)
        form.addRow("", self.edit_custom)
        lbl_voz_actual = QLabel(f"Voz en uso ahora: <b>{voice_label}</b>")
        form.addRow("", lbl_voz_actual)
        form.addRow("",
                    QLabel(t("La voz elegida se descarga automáticamente si no está "
                              "instalada. El audio se reinicia al guardar.")))
        # --- idioma de las ventanas ---
        self.combo_idioma = QComboBox()
        self.combo_idioma.addItem(
            t("Idioma automatico (sigue a la voz)"), idioma.AUTOMATICO)
        for cod, etiqueta in idioma.IDIOMAS.items():
            self.combo_idioma.addItem(etiqueta, cod)
        self.combo_idioma.setMinimumWidth(360)
        _prev = (self._gui.get("idioma") or idioma.AUTOMATICO)
        _i = self.combo_idioma.findData(_prev)
        self.combo_idioma.setCurrentIndex(_i if _i >= 0 else 0)
        self.combo_idioma.currentIndexChanged.connect(self._cambio_idioma)
        form.addRow(t("Idioma de las ventanas:"), self.combo_idioma)

        self._dentro.addWidget(box_tts)

        box_gui = QGroupBox(t("Apariencia del widget (waveform)"))
        form = QFormLayout(box_gui)
        self.combo_style = QComboBox()
        for key, label in STYLES.items():
            self.combo_style.addItem(label, key)
        cur = self._gui.get("waveform_style", "barras")
        idx = self.combo_style.findData(cur)
        if idx >= 0:
            self.combo_style.setCurrentIndex(idx)
        form.addRow(t("Diseño:"), self.combo_style)

        self.btn_wave_color = ColorButton(
            self._gui.get("waveform_color", "#ff2200"),
            "Color del waveform")
        form.addRow(t("Color del waveform:"), self.btn_wave_color)

        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(20, 100)
        opacity = int(float(self._gui.get("waveform_opacity", 1.0)) * 100)
        self.slider_opacity.setValue(opacity)
        self.lbl_opacity = QLabel(f"{opacity} %")
        self.slider_opacity.valueChanged.connect(
            lambda v: self.lbl_opacity.setText(f"{v} %"))
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.slider_opacity, 1)
        h.addWidget(self.lbl_opacity)
        form.addRow(t("Opacidad:"), row)

        colores_estado = {
            "color_listening": ("Escuchando", self._gui.get("color_listening", "#00cc44")),
            "color_thinking": ("Pensando", self._gui.get("color_thinking", "#3399ff")),
            "color_speaking": ("Hablando", self._gui.get("color_speaking", "#ff2200")),
            "color_idle": ("Inactivo", self._gui.get("color_idle", "#666666")),
        }
        self._state_buttons = {}
        row2 = QWidget()
        h2 = QHBoxLayout(row2)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(8)
        for key, (label, color) in colores_estado.items():
            btn = ColorButton(color, f"Color estado {label}")
            box = QWidget()
            b = QVBoxLayout(box)
            b.setContentsMargins(0, 0, 0, 0)
            b.setSpacing(2)
            b.addWidget(btn)
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b.addWidget(lbl)
            h2.addWidget(box)
            self._state_buttons[key] = btn
        form.addRow(t("Colores de estado:"), row2)
        self._dentro.addWidget(box_gui)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Save).setText(t("Guardar"))
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(t("Cancelar"))
        btns.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # con la ventana ya montada, se guardan los textos para poder
        # cambiarlos de idioma sin cerrarla
        self._fin_construccion()

    def _lang_de_voz(self):
        """Idioma de la voz elegida en este momento (p. ej. 'en-US')."""
        for _etiqueta, nombre, cod, _g in VOICES:
            if nombre == self._current_voice:
                return cod
        return ""

    def _cambio_idioma(self):
        """Cambia el idioma de las ventanas sin cerrarlas.

        Se aplica al momento para que se vea el efecto; en disco solo se
        guarda al pulsar Guardar, como todo lo demas."""
        idioma.activa(self.combo_idioma.currentData() or "es")
        self._traducir_todo()

    def _recoge_textos(self):
        """Guarda el texto original de los widgets para poder retraducirlos.

        Se llama al final de montar el dialogo: los textos ya estan en
        castellano, y de aqui en adelante se pueden volver a pasar por t().
        Los que dependen de datos (el modelo activo, el estado) no estan
        en la tabla y se quedan como estaban, que es lo sensato."""
        self._textos_originales = []
        for _w in self.findChildren((QLabel, QPushButton, QCheckBox)):
            _txt = _w.text()
            if _txt:
                self._textos_originales.append((_w, _txt))
        for _w in self.findChildren(QGroupBox):
            _tit = _w.title()
            if _tit:
                self._textos_originales.append((_w, _tit))

    def _traducir_todo(self):
        for _w, _txt in getattr(self, "_textos_originales", []):
            _actual = _w.title() if isinstance(_w, QGroupBox) else _w.text()
            if _actual != _txt:
                continue          # es un texto dinamico: no se toca
            _nuevo = t(_txt)
            if _nuevo != _txt:
                if isinstance(_w, QGroupBox):
                    _w.setTitle(_nuevo)
                else:
                    _w.setText(_nuevo)
        _caja = self.findChild(QDialogButtonBox)
        if _caja is not None:
            _b = _caja.button(QDialogButtonBox.StandardButton.Save)
            if _b is not None:
                _b.setText(t("Guardar"))
            _b = _caja.button(QDialogButtonBox.StandardButton.Cancel)
            if _b is not None:
                _b.setText(t("Cancelar"))
        self.setWindowTitle(t("Configuración de OVOS Asistente"))
        self.update()

    def _ajustar_a_pantalla(self):
        """Que el dialogo entre en la pantalla; si no, se desplaza."""
        from PyQt6.QtWidgets import QApplication as _QA
        geo = self.screen().availableGeometry() if self.screen() \
            else _QA.primaryScreen().availableGeometry()
        w = max(self.sizeHint().width(), 520)
        h = min(self.sizeHint().height(), max(geo.height() - 80, 400))
        self.resize(min(w, geo.width() - 40), h)

    def _alternar_tipo(self):
        """Muestra solo el panel del tipo elegido."""
        remoto = self.rb_remoto.isChecked()
        self.box_local.setVisible(not remoto)
        self.box_remoto.setVisible(remoto)

    def _fin_construccion(self):
        """Se llama al final de __init__, con la ventana ya montada."""
        self._recoge_textos()

    def _pintar_estado(self):
        """Subraya de donde viene la respuesta ahora mismo."""
        cfg_m = self._cfg_modelo or {}
        if cfg_m.get("tipo") == "remoto":
            prov = PROVEEDORES.get(cfg_m.get("proveedor") or "", {})
            self.lbl_modelo.setText(
                f'<span style="color:#0a0">Ahora mismo responde un modelo '
                f'remoto: <b>{cfg_m.get("remoto") or "sin modelo"}</b> '
                f'({prov.get("nombre", "?")}).</span>')
        else:
            act = cfg_m.get("actual") or modelo_actual(self._cfg) or "llama3.1:8b"
            self.lbl_modelo.setText(
                f'<span style="color:#060">Ahora mismo responde un modelo '
                f'local: <b>{act}</b>.</span>')

    def _cambio_proveedor(self):
        clave = self.combo_proveedor.currentData()
        info = PROVEEDORES.get(clave, {})
        self.edit_clave.setText(leer_clave(clave))
        self.lbl_clave.setText(info.get("web") or "")
        self.lbl_clave.setOpenExternalLinks(bool(info.get("web")))
        self.lbl_clave.setText(
            (f'<a href="{info["web"]}">{info["web"]}</a> &nbsp; ' if info.get("web") else "")
            + "La clave se guarda aparte, solo para tu usuario. Si ya habia una, "
              "el campo sale rellenado.")

    def _ver_tamano(self):
        """Consulta el registro de ollama y explica los pasos y el espacio."""
        modelo = self.combo_descargar.currentData()
        if not modelo:
            return
        self.txt_pasos.setVisible(True)
        self.txt_pasos.setPlainText("Consultando el registro de ollama…")
        QApplication.processEvents()
        bytes_ = tamano_modelo(modelo)
        libres = espacio_libre()
        lineas = [f"Modelo:  {modelo}"]
        if bytes_:
            lineas.append(f"Descarga:  {bytes_ / 1e9:.1f} GB")
        else:
            lineas.append("Descarga:  no he podido consultar el tamaño")
        if libres:
            lineas.append(f"Espacio libre ahora:  {libres / 1e9:.1f} GB")
            if bytes_ and libres < bytes_ * 1.1:
                lineas.append("⚠ No hay sitio de sobra: la descarga "
                              "probablemente falle.")
        lineas += ["", "Pasos que haria por ti:",
                   f"  1. ollama pull {modelo}",
                   "  2. comprobar que responde",
                   "  3. dejarlo como modelo de respuesta"]
        self.txt_pasos.setPlainText("\n".join(lineas))

    def _instalar(self):
        """Descarga el modelo elegido previa confirmacion."""
        modelo = self.combo_descargar.currentData()
        if not modelo:
            return
        bytes_ = tamano_modelo(modelo)
        gb = f"unos {bytes_ / 1e9:.1f} GB" if bytes_ else "varios GB"
        uso = dict(MODELOS_CLAVE).get(modelo, "uso general")
        ret = QMessageBox.question(
            self, t("Instalar modelo local"),
            f"Se va a descargar <b>{modelo}</b> ({gb} en disco) para {uso}.\n\n"
            f"Se ejecuta:\n    ollama pull {modelo}\n\n"
            f"Tardará unos minutos según tu conexión.\n¿Continuar?")
        if ret != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.repaint()
        # OLLAMA_HOST = pool del usuario, para no necesitar privilegios
        env = dict(os.environ, OLLAMA_HOST="http://127.0.0.1:11435")
        try:
            proc = subprocess.run(["ollama", "pull", modelo], env=env,
                                  capture_output=True, text=True, timeout=7200)
            error = ""
        except Exception as exc:
            proc, error = None, str(exc)
        finally:
            QApplication.restoreOverrideCursor()
        if proc is None:
            QMessageBox.critical(self, t("Instalar modelo"),
                                 f"No se pudo descargar {modelo}:\n{error}")
            return
        if proc.returncode != 0:
            QMessageBox.critical(
                self, t("Instalar modelo"),
                f"La descarga de {modelo} falló.\n\n"
                f"{(proc.stderr or proc.stdout or '')[-600:]}")
            return
        self._poblar_modelos()
        self._seleccionar_en_combo(modelo)
        QMessageBox.information(
            self, t("Instalar modelo"),
            f"{modelo} ya está instalado y seleccionado.\n\n"
            f"Pulsa Guardar para dejarlo como modelo de respuesta.")

    def _seleccionar_en_combo(self, modelo):
        """Pone en el desplegable local el modelo indicado, si esta."""
        for i in range(self.combo_modelo.count()):
            datos = self.combo_modelo.itemData(i)
            if datos and datos[0] == modelo:
                self.combo_modelo.setCurrentIndex(i)
                return True
        return False

    def _probar_remoto(self):
        """Llama al proveedor con la clave escrita y avisa del resultado."""
        proveedor = self.combo_proveedor.currentData()
        modelo = self.edit_modelo_remoto.text().strip()
        clave = self.edit_clave.text().strip()
        if not clave:
            QMessageBox.warning(self, t("Probar conexión"),
                                t("Escribe primero la clave de API."))
            return
        if not modelo:
            QMessageBox.warning(self, t("Probar conexión"),
                                t("Escribe el identificador del modelo."))
            return
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor)
        try:
            ok, msg = _probar_endpoint(proveedor, modelo, clave,
                                       self.edit_url.text().strip())
        finally:
            QApplication.restoreOverrideCursor()
        if ok:
            QMessageBox.information(self, t("Probar conexión"), msg)
        else:
            QMessageBox.warning(self, t("Probar conexión"), msg)

    def _listar_modelos_remotos(self):
        """Pide al proveedor la lista de modelos de la cuenta."""
        proveedor = self.combo_proveedor.currentData()
        clave = self.edit_clave.text().strip()
        if not clave:
            QMessageBox.warning(self, t("Ver modelos"),
                                t("Escribe primero la clave de API."))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, texto = _listar_endpoint(proveedor, clave,
                                       self.edit_url.text().strip())
        finally:
            QApplication.restoreOverrideCursor()
        self.lista_modelos.setVisible(True)
        self.lista_modelos.setPlainText(texto)
        if ok:
            self.lbl_clave.setText(
                t("Haz clic en un identificador de la lista para copiarlo "
                  "en el campo de modelo."))

    def _poblar_modelos(self):
        """Llena el desplegable con los modelos que hay en los pools."""
        self._modelos = listar_modelos()
        self.combo_modelo.clear()
        if not self._modelos:
            self.combo_modelo.addItem(t("(no se encuentra ningún ollama)"), None)
            self.combo_modelo.setEnabled(False)
            return
        self.combo_modelo.setEnabled(True)
        actual = modelo_actual()
        for etiqueta, nombre, puerto in self._modelos:
            self.combo_modelo.addItem(etiqueta, (nombre, puerto))
            if nombre == actual:
                self.combo_modelo.setCurrentIndex(
                    self.combo_modelo.count() - 1)

    def _save(self):
        old_nombre = _current_nombre()
        nuevo_nombre = (self.edit_nombre.text().strip() or old_nombre)

        # Conservar cualquier otra clave del archivo (nombre_asistente)
        # en lugar de reconstruirlo desde cero.
        raw = {}
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            raw = {}
        cfg = {"gui": copy.deepcopy(self._gui), "tts": {}}
        cfg["gui"]["idioma"] = self.combo_idioma.currentData() or idioma.AUTOMATICO
        alpha = {k: v for k, v in raw.items() if k not in ("gui", "tts")}
        cfg.update(alpha)
        cfg["nombre_asistente"] = nuevo_nombre
        # modelo elegido: local o remoto segun el radio pulsado
        sel = self.combo_modelo.currentData()
        cfg_previo = raw.get("modelo") or {}
        modelo_previo = (cfg_previo.get("remoto")
                         if cfg_previo.get("tipo") == "remoto"
                         else cfg_previo.get("actual"))
        nuevo_remoto = None
        if self.rb_remoto.isChecked():
            proveedor = self.combo_proveedor.currentData()
            remoto = self.edit_modelo_remoto.text().strip()
            clave = self.edit_clave.text().strip()
            url_personal = self.edit_url.text().strip()
            if not remoto:
                QMessageBox.warning(
                    self, t("Configuración"),
                    "Has elegido un modelo remoto pero no has escrito "
                    "qué modelo es.\n\n"
                    "O escribes el identificador, o vuelves a «Local».")
                return
            if not clave and not leer_clave(proveedor):
                QMessageBox.warning(
                    self, t("Configuración"),
                    t("Falta la clave de API para ese proveedor."))
                return
            if clave and guardar_clave(proveedor, clave):
                self.edit_clave.setText(leer_clave(proveedor))
            cfg["modelo"] = {
                "tipo": "remoto",
                "proveedor": proveedor,
                "remoto": remoto,
                "url": url_personal,
            }
            nuevo_remoto = remoto
        elif sel:
            set_modelo(cfg, sel[0], sel[1])
        style = self.combo_style.currentData()
        cfg["gui"]["waveform_style"] = style if style in STYLES else "barras"
        cfg["gui"]["waveform_color"] = self.btn_wave_color.color().name()
        cfg["gui"]["waveform_opacity"] = self.slider_opacity.value() / 100.0
        for key, btn in self._state_buttons.items():
            cfg["gui"][key] = btn.color().name()

        voice = self.edit_custom.text().strip() if self.cb_custom.isChecked() else \
            self.combo_voice.currentData()

        # Voz acorde al idioma elegido: si el idioma es concreto y la voz
        # actual es de otro idioma, se cambia e instala la voz por defecto
        # de ese idioma (p. ej. elegir "English" pone una voz inglesa).
        auto_voz = False
        voz_idioma = ""
        cod_sel = cfg["gui"]["idioma"]
        if cod_sel in idioma.IDIOMAS:
            voz_idioma = idioma.voz_para(cod_sel)
            if voz_idioma and idioma.idioma_de_voz(voice) != cod_sel:
                auto_voz = True
                voice = voz_idioma
        changed_voice = bool(voice) and voice != self._current_voice

        if changed_voice and not auto_voz:
            ret = QMessageBox.question(
                self, t("Cambiar voz"),
                f"Se cambiará la voz a '<b>{voice}</b>' y el audio se reiniciará.\n"
                "¿Continuar?")
            if ret != QMessageBox.StandardButton.Yes:
                return
        if changed_voice:
            cfg["tts"]["voice"] = voice

        save_config(cfg)
        # Idiomas que padatious debe entrenar ademas del principal
        # (se derivan de idiomas.json; añadir un idioma no toca codigo).
        try:
            apply_secondary_langs(idioma.codigos_secundarios())
        except Exception:
            pass
        if changed_voice:
            if auto_voz:
                if not idioma.descargar_voz(voice, cod_sel):
                    QMessageBox.warning(
                        self, t("Cambiar voz"),
                        f"No pude descargar la voz '<b>{voice}</b>'. "
                        "Comprueba tu conexión; sin ella, el audio podría "
                        "sonar con la voz anterior.")
                # deja en mycroft.conf la voz (y el modelo si es por URL)
                idioma.asegurar_voz_tts(cod_sel)
            apply_tts_voice(voice)
        if nuevo_nombre != old_nombre:
            _relaunch_chat_window()
        extra = ""
        if nuevo_remoto and nuevo_remoto != modelo_previo:
            extra = ("\nEl modelo de respuesta ahora es <b>"
                     f"{nuevo_remoto}</b>, por internet y de pago.")
        elif sel and sel[0] != modelo_previo:
            extra = f"\nEl modelo de respuesta ahora es <b>{sel[0]}</b>."
        if auto_voz:
            extra += (f"\nLa voz se ha cambiado a <b>{voice}</b> "
                      "para ese idioma.")
        QMessageBox.information(
            self, t("Configuración"),
            "Configuración guardada." + extra +
            "\nEl widget de Plasma aplicará los cambios en unos segundos.\n"
            "Si no los ves, quita y vuelve a añadir el widget OVOS." +
            ("\n\nAviso: no he probado ese modelo; si algo falla, "
             "puede ser la clave o el identificador."
             if nuevo_remoto else ""))
        self.accept()


# --- Llamadas a las APIs remotas (stdlib, sin dependencias extra) -------

_CHAT_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/"
              "chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
    "xai": "https://api.x.ai/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
}

_MODELS_URLS = {
    "openai": "https://api.openai.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "xai": "https://api.x.ai/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
}


def _http_json(url, headers, body=None, timeout=45):
    """Petición JSON. Devuelve (ok, texto legible)."""
    datos = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=datos, headers=headers,
                                 method="POST" if datos else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            crudo = r.read().decode("utf-8", "replace")
        return True, crudo
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")
        try:
            cuerpo = json.loads(cuerpo).get("error", {})
            cuerpo = cuerpo.get("message") or cuerpo
        except Exception:
            pass
        return False, f"HTTP {e.code}: {str(cuerpo)[:300]}"
    except Exception as e:
        return False, f"no se pudo contactar con el servidor ({e})"


def _url_de_chat(proveedor, url_personal):
    if proveedor == "custom":
        return (url_personal or "").strip()
    return _CHAT_URLS.get(proveedor, "")


def _url_de_modelos(proveedor, url_personal):
    if proveedor == "custom":
        base = (url_personal or "").strip()
        for sufijo in ("/chat/completions", "/completions"):
            if base.endswith(sufijo):
                return base[: -len(sufijo)] + "/models"
        return base
    return _MODELS_URLS.get(proveedor, "")


def _probar_endpoint(proveedor, modelo, clave, url_personal=""):
    """Manda la llamada minima de prueba y traduce el fallo si lo hay."""
    url = _url_de_chat(proveedor, url_personal)
    if not url:
        return False, "Ese proveedor necesita una URL en el campo de arriba."
    if proveedor == "anthropic":
        ok, texto = _http_json(
            url, {"x-api-key": clave, "anthropic-version": "2023-06-01",
                  "content-type": "application/json"},
            {"model": modelo, "max_tokens": 16,
             "messages": [{"role": "user", "content": "di hola"}]})
        if not ok:
            return False, texto
        return True, f"Conectado. {modelo} ha respondido correctamente."
    ok, texto = _http_json(
        url, {"Authorization": f"Bearer {clave}",
              "content-type": "application/json"},
        {"model": modelo, "max_tokens": 16,
         "messages": [{"role": "user", "content": "di hola"}]})
    if not ok:
        return False, texto
    return True, f"Conectado. {modelo} ha respondido correctamente."


def _listar_endpoint(proveedor, clave, url_personal=""):
    """Modelos disponibles para esa clave, o el motivo del fallo."""
    url = _url_de_modelos(proveedor, url_personal)
    if not url:
        return False, ("Este proveedor no ofrece un listado automático. "
                       "Mira su documentación y escribe el identificador "
                       "a mano.")
    cabeceras = {"Authorization": f"Bearer {clave}"}
    if proveedor == "anthropic":
        cabeceras = {"x-api-key": clave,
                     "anthropic-version": "2023-06-01"}
    ok, texto = _http_json(url, cabeceras)
    if not ok:
        return False, texto
    try:
        datos = json.loads(texto)
    except Exception:
        return False, "La respuesta del servidor no se pudo entender."
    ids = [d.get("id") for d in (datos.get("data") or [])
           if isinstance(d, dict) and d.get("id")]
    if not ids:
        return False, "El servidor respondió, pero sin lista de modelos."
    return True, "\n".join(sorted(ids))


def _current_nombre():
    """Nombre del asistente usado en la config (default lucIA)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            nombre = (json.load(fh).get("nombre_asistente") or "").strip()
        return nombre or "lucIA"
    except Exception:
        return "lucIA"


def _relaunch_chat_window():
    """Aplica el nuevo nombre relanzando la ventana de chat persistente."""
    try:
        for pid in subprocess.run(
                ["pgrep", "-x", "python3"],
                capture_output=True, text=True).stdout.split():
            try:
                if not pid.strip():
                    continue
                with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
                    cmdline = (fh.read().replace(b"\0", b" ")
                               .decode(errors="ignore"))
                if "chat_window.py" in cmdline:
                    os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
    except Exception:
        pass
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "chat_window.py")
    if os.path.exists(ruta):
        try:
            subprocess.Popen(
                [sys.executable, ruta],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    icono = QIcon(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "iconos", "lucIA.ico"))
    if not icono.isNull():
        app.setWindowIcon(icono)
    dlg = ConfigDialog()
    if not icono.isNull():
        dlg.setWindowIcon(icono)
    # Ajustar a la pantalla: con letra grande el alto natural se pasa.
    dlg._ajustar_a_pantalla()
    dlg.show()
    sys.exit(dlg.exec())


if __name__ == "__main__":
    main()