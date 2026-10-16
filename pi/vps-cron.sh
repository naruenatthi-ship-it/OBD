#!/bin/sh
# Runs on the VPS (copied there by `obd vps sync`): add one @reboot line
# that starts the web pages, keeping every other crontab line as it is.
# If the current crontab cannot be read, nothing is changed: rewriting it
# blind would wipe the owner's other jobs. A copy is kept before changing.
# Usage: sh vps-cron.sh [port]
BASE="$HOME/deepal-obd"
PORT="${1:-8080}"
LINE="@reboot sh $BASE/app/pi/vps-serve.sh $PORT"
if CURRENT=$(crontab -l 2>&1); then
    :
elif printf '%s' "$CURRENT" | grep -qi "no crontab"; then
    CURRENT=""
else
    echo "อ่าน crontab ไม่ได้ ($CURRENT) ไม่แก้อะไร: เว็บจะไม่เริ่มเองหลังรีบูต VPS"
    exit 1
fi
mkdir -p "$BASE"
printf '%s\n' "$CURRENT" > "$BASE/crontab.backup"
if printf '%s\n' "$CURRENT" | grep -qxF "$LINE"; then
    exit 0  # already there
fi
{ printf '%s\n' "$CURRENT" | grep -v "deepal-obd/app/pi/vps-serve.sh" |
    sed '/^$/d'; echo "$LINE"; } | crontab - &&
    echo "เพิ่ม @reboot ใน crontab แล้ว (สำรองของเดิมไว้ที่ $BASE/crontab.backup)"
