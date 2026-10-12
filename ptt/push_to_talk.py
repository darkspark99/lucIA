#!/usr/bin/env python3
"""Push-to-talk: press Ctrl+Alt+Num- to start voice command (no wake word needed)."""
import subprocess
import time
import re
from ovos_bus_client import MessageBusClient, Message

LEFTCTRL = 29
RIGHTCTRL = 97
LEFTALT = 56
RIGHTALT = 100
KPMINUS = 74

bus = None
ctrl_down = False
alt_down = False
ptt_down = False
already_triggered = False


def listen():
    global bus
    if bus is None:
        bus = MessageBusClient()
        bus.run_in_thread()
        time.sleep(1)
    bus.emit(Message("recognizer_loop:wake_up"))
    bus.emit(Message("mycroft.mic.listen"))


def release():
    global bus
    if bus is None:
        bus = MessageBusClient()
        bus.run_in_thread()
        time.sleep(1)
    # corta la grabacion en curso inmediatamente
    bus.emit(Message("recognizer_loop:record_stop"))
    bus.emit(Message("recognizer_loop:wake_down"))


def main():
    global ctrl_down, alt_down, ptt_down, already_triggered
    proc = subprocess.Popen(
        ["sudo", "evtest", "/dev/input/event0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    pattern = re.compile(
        r"Event: time \S+, type (\d+) \(\S+\), code (\d+) \(\S+\), value (\d+)"
    )

    for line in proc.stdout:
        match = pattern.match(line.strip())
        if not match:
            continue
        ev_type = int(match.group(1))
        code = int(match.group(2))
        value = int(match.group(3))

        if ev_type != 1:  # EV_KEY
            continue
        if value == 2:  # repeat, ignore
            continue

        is_press = value == 1

        if code in (LEFTCTRL, RIGHTCTRL):
            ctrl_down = is_press
        elif code in (LEFTALT, RIGHTALT):
            alt_down = is_press
        elif code == KPMINUS:
            ptt_down = is_press

        if ctrl_down and alt_down and ptt_down:
            if not already_triggered:
                already_triggered = True
                listen()
        else:
            if already_triggered:
                already_triggered = False
                release()


if __name__ == "__main__":
    main()
