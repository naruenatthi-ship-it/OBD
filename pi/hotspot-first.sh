#!/bin/bash
# Run every minute by deepal-hotspot.timer: when the phone's hotspot is in
# range, move to it even if another known WiFi is connected. NetworkManager
# only uses autoconnect priorities when nothing is connected, so without
# this a Pi on the home or office WiFi never comes back to the hotspot.
set -uo pipefail
active=$(nmcli -t -f NAME,DEVICE connection show --active | grep ':wlan0$' |
         cut -d: -f1)
[ "$active" = hotspot ] && exit 0
ssid=$(nmcli -g 802-11-wireless.ssid connection show hotspot 2>/dev/null) ||
    exit 0
[ -n "$ssid" ] || exit 0
if nmcli -t -f SSID dev wifi list --rescan yes 2>/dev/null |
        grep -Fxq -- "$ssid"; then
    logger -t deepal-hotspot "hotspot $ssid in range, switching from ${active:-nothing}"
    nmcli connection up hotspot >/dev/null 2>&1
fi
