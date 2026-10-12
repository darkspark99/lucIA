"""TTS piper multilingue: elige la voz segun el idioma del mensaje.

El servicio de audio de este sistema configura una unica voz global y la
impone en cada sintesis, de modo que un texto en ingles acababa leido con
la voz espanola (y al reves). Este plugin hereda del de piper y, si en
idiomas.json hay una voz declarada para el idioma pedido, la usa; si no,
delega en el comportamiento normal de piper.

Añadir o cambiar una voz por idioma se hace solo en idiomas.json, sin tocar
este codigo."""

import json
import os

from ovos_tts_plugin_piper import PiperTTSPlugin
from ovos_utils.log import LOG
from ovos_bus_client.message import dig_for_message

_IDIOMAS = os.path.expanduser("~/.local/share/ovos/idiomas.json")


def _voces_por_idioma():
    """{base_lang: voice} a partir de idiomas.json (clave 'voz' por idioma)."""
    try:
        with open(_IDIOMAS, encoding="utf-8") as fh:
            datos = json.load(fh) or {}
    except Exception:
        return {}
    mapa = {}
    for cod, d in datos.items():
        if str(cod).startswith("_") or not isinstance(d, dict):
            continue
        voz = d.get("voz")
        if isinstance(voz, dict):
            voz = voz.get("nombre")
        if voz:
            mapa[str(cod).strip().lower()] = voz
    return mapa


class PiperMultiTTSPlugin(PiperTTSPlugin):
    """Piper que elige la voz segun el idioma pedido (lang del mensaje)."""

    def _get_ctxt(self, kwargs=None):
        # Si el mensaje trae un idioma explicito (el idioma en que
        # respondemos), lo usamos como 'lang' desde el principio; asi la
        # voz Y la clave de cache usan el idioma real, y no el de la sesion
        # 'default' que reproduce el altavoz.
        kwargs = dict(kwargs or {})
        message = kwargs.get("message") or dig_for_message()
        if message is not None and "lang" not in kwargs:
            datos = getattr(message, "data", None) or {}
            ctx = getattr(message, "context", None) or {}
            lang = datos.get("lang") or ctx.get("lang")
            if lang:
                kwargs["lang"] = lang
        kwargs.setdefault("message", message)
        return super()._get_ctxt(kwargs)

    def get_tts(self, sentence, wav_file, lang=None, voice=None, speaker=None,
                message=None):
        base = str(lang or self.lang or "").split("-")[0].lower()
        mapa = self.config.get("voices") or _voces_por_idioma()
        elegida = mapa.get(base)
        if elegida:
            if elegida != voice:
                LOG.debug(f"piper-multi: voz '{elegida}' para el idioma "
                          f"'{base}' (pedida '{voice}')")
            voice = elegida
        return super().get_tts(sentence, wav_file, lang=lang, voice=voice,
                               speaker=speaker)
