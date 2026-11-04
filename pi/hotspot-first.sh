#!/bin/bash
# Run every minute by deepal-hotspot.timer: when a known WiFi ranked above
# the current one is in range (by default the phone hotspot), move to it.
# NetworkManager only uses autoconnect priorities when nothing is
# connected, so without this a Pi on the home or office WiFi never comes
# back to the hotspot. The order is set with `obd wifi first/order`.
set -uo pipefail
out=$(PYTHONPATH=/opt/deepal-obd python3 -m deepal_s05.wifi auto 2>&1)
[ -n "$out" ] && logger -t deepal-hotspot "$out"
exit 0
