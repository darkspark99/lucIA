#!/bin/bash
# Import X11 environment into systemd --user so OVOS services can use them
sleep 2
systemctl --user import-environment DISPLAY XAUTHORITY
