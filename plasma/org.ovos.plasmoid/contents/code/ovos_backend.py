#!/usr/bin/env python3
"""OVOS Plasmoid Bridge - Conecta OVOS messagebus con el widget de Plasma."""
import json
import math
import numpy as np
import os
import sys
import struct
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from ovos_user_config import load_config
except ImportError:
    def load_config():
        return {}

STATE = {
    "status": "idle",
    "transcript": "",
    "waveform": [0.0] * 48,
    "last_speech": "",
}

lock = threading.Lock()
HOST = "127.0.0.1"
PORT = 8765
SPEAK_TIMEOUT = 30.0
THINKING_TIMEOUT = 120.0
DEBOUNCE_END = 0.8
NUM_BARS = 48
FRAME_RATE = 30

CURVE = np.exp(-0.5 * ((np.arange(NUM_BARS) - (NUM_BARS - 1) / 2) / (NUM_BARS / 5.5)) ** 2)
CURVE = CURVE / CURVE.max()


class AudioCapture:
    def __init__(self):
        self._proc = None
        self._amp = 0.0
        self._running = False
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        try:
            sink = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=3
            ).stdout.strip()
            if not sink:
                return False
            monitor = f"{sink}.monitor"
            self._proc = subprocess.Popen(
                ["parec", f"--device={monitor}", "--raw",
                 "--rate=8000", "--channels=1", "--format=s16le",
                 "--latency-msec=5"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            print(f"[audio] capture init failed: {e}")
            return False

    def _read_loop(self):
        read_size = 267 * 2
        while self._running and self._proc:
            try:
                raw = self._proc.stdout.read(read_size)
                if not raw or len(raw) < 2:
                    break
                samples = struct.unpack(f"<{len(raw)//2}h", raw[:len(raw)//2*2])
                peak = max(abs(s) for s in samples) / 32768.0
                with self._lock:
                    if peak > self._amp:
                        self._amp = self._amp * 0.3 + peak * 0.7
                    else:
                        self._amp = self._amp * 0.85 + peak * 0.15
            except Exception:
                break

    def get_amplitude(self):
        with self._lock:
            return self._amp if self._amp > 0.015 else 0.0

    def stop(self):
        self._running = False
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()
            self._proc = None


class SpeechAnimator:
    def __init__(self, audio_capture=None):
        self._audio = audio_capture
        self._running = False
        self._bars = np.zeros(NUM_BARS)
        self._fallback = audio_capture is None
        self._frame = 0.0
        self._word_timer = 0

    def start(self, text):
        self._running = True
        self._frame = 0.0
        self._word_timer = 0

    def stop(self):
        self._running = False

    def tick(self):
        if not self._running:
            return None

        if self._fallback:
            self._frame += 1.0
            f = self._frame
            self._word_timer -= 1
            if self._word_timer <= 0:
                energy = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(f * 0.08 + math.sin(f * 0.12) * 0.6))
                shape = CURVE + 0.35 * (np.random.rand(NUM_BARS) - 0.5)
                peak = energy * np.clip(shape, 0, 1)
                np.maximum(self._bars, peak, out=self._bars)
                self._word_timer = int(3 + 10 * (0.5 + 0.5 * math.sin(f * 0.09)))
            self._bars *= 0.78
            self._bars = np.clip(self._bars, 0, 1)
            return self._bars.tolist()

        amp = self._audio.get_amplitude()

        if amp > 0.02:
            shape = CURVE + 0.4 * (np.random.rand(NUM_BARS) - 0.5)
            peak = amp * 2.0 * np.clip(shape, 0, 1)
            np.maximum(self._bars, np.clip(peak, 0, 1), out=self._bars)

        self._bars *= 0.78
        self._bars = np.clip(self._bars, 0, 1)

        return self._bars.tolist()


class OVOSBridge:
    def __init__(self):
        self.bus = MessageBusClient()
        self.bus.run_in_thread()
        self._speak_end_timer = None
        self._watchdog_timer = None
        self._audio = AudioCapture()
        self._has_audio = self._audio.start()
        self._anim = SpeechAnimator(audio_capture=self._audio if self._has_audio else None)
        self._last_text = ""
        self._setup_handlers()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

    def _setup_handlers(self):
        self.bus.on("recognizer_loop:record_begin", self._on_listening)
        self.bus.on("recognizer_loop:utterance", self._on_utterance)
        self.bus.on("mycroft.skill.handler.start", self._on_thinking)
        self.bus.on("speak", self._on_speaking)
        self.bus.on("recognizer_loop:audio_output_start", self._on_audio_start)
        self.bus.on("recognizer_loop:audio_output_end", self._on_audio_track_end)
        self.bus.on("mycroft.audio.queue.end", self._on_audio_end)
        self.bus.on("mycroft.audio.queue_end", self._on_audio_end)
        self.bus.on("mycroft.audio.speech.stop", self._on_audio_end)
        self.bus.on("recognizer_loop:speech.recognition.unknown", self._on_idle)

    def _cancel_timers(self):
        if self._speak_end_timer:
            self._speak_end_timer.cancel()
            self._speak_end_timer = None
        if self._watchdog_timer:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

    def _set_idle(self):
        self._cancel_timers()
        self._anim.stop()
        with lock:
            STATE["status"] = "idle"

    def _schedule_idle(self, delay):
        self._cancel_timers()
        self._watchdog_timer = threading.Timer(delay, self._set_idle)
        self._watchdog_timer.daemon = True
        self._watchdog_timer.start()

    def _on_listening(self, message):
        with lock:
            STATE["status"] = "listening"
            STATE["transcript"] = "..."

    def _on_utterance(self, message):
        utterances = message.data.get("utterances", [])
        text = utterances[0] if utterances else ""
        with lock:
            STATE["transcript"] = text
            STATE["status"] = "thinking"
        self._schedule_idle(THINKING_TIMEOUT)

    def _on_thinking(self, message):
        with lock:
            if STATE["status"] not in ("speaking",):
                STATE["status"] = "thinking"
        self._schedule_idle(THINKING_TIMEOUT)

    def _on_speaking(self, message):
        text = message.data.get("utterance", "") or message.data.get("text", "")
        with lock:
            if text:
                STATE["last_speech"] = text
                self._last_text = text
            STATE["status"] = "speaking"
        self._cancel_timers()
        self._anim.start(text or self._last_text)
        self._watchdog_timer = threading.Timer(SPEAK_TIMEOUT, self._set_idle)
        self._watchdog_timer.daemon = True
        self._watchdog_timer.start()

    def _on_audio_start(self, message):
        with lock:
            STATE["status"] = "speaking"

    def _on_audio_track_end(self, message):
        self._cancel_timers()
        self._speak_end_timer = threading.Timer(DEBOUNCE_END, self._set_idle)
        self._speak_end_timer.daemon = True
        self._speak_end_timer.start()

    def _on_audio_end(self, message):
        self._cancel_timers()
        self._speak_end_timer = threading.Timer(DEBOUNCE_END, self._set_idle)
        self._speak_end_timer.daemon = True
        self._speak_end_timer.start()

    def _on_idle(self, message):
        self._set_idle()

    def _tick_loop(self):
        while True:
            w = self._anim.tick()
            with lock:
                if w is not None:
                    STATE["waveform"] = w
                else:
                    STATE["waveform"] = [0.0] * NUM_BARS
            time.sleep(1.0 / FRAME_RATE)


bridge = None


class StateHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/state":
            with lock:
                body = dict(STATE)
            body["config"] = load_config()
            resp = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        elif self.path == "/config":
            resp = json.dumps(load_config()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        elif self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"pong")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    global bridge
    bridge = OVOSBridge()
    server = HTTPServer((HOST, PORT), StateHandler)
    print(f"OVOS_BACKEND_PORT={PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
