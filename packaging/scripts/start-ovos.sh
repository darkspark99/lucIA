#!/bin/bash
# Iniciar todos los servicios de OVOS
systemctl --user start ovos.service
sleep 1
systemctl --user start ovos-messagebus.service
sleep 1
systemctl --user start ovos-core.service
sleep 2
systemctl --user start ovos-phal.service
systemctl --user start ovos-audio.service
systemctl --user start ovos-status-indicator.service
systemctl --user start ovos-ptt.service
