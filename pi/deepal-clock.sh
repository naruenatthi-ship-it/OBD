#!/bin/sh
# The Pi Zero has no clock battery. After a power cut it starts from the
# time last saved by the system, which can be an hour old, so recordings
# went back in time (17 Nov: 20:55 came 171 s after 20:58). This keeps
# the last known time every minute (save) and at boot moves the clock
# forward to it when the clock is behind (restore). It never moves the
# clock back; network time corrects it later when the hotspot is on.
F=${CLOCK_FILE:-/var/lib/deepal-obd/.clock}
case "$1" in
    restore)
        saved=$(cat "$F" 2>/dev/null) || exit 0
        case "$saved" in ''|*[!0-9]*) exit 0 ;; esac
        if [ "$saved" -gt "$(date +%s)" ]; then
            date -s "@$saved" >/dev/null
        fi ;;
    save)
        date +%s > "$F.tmp" && mv "$F.tmp" "$F" ;;
    *)
        echo "usage: $0 restore|save" >&2
        exit 2 ;;
esac
