#!/bin/bash
# ติดตั้งโปรแกรม Deepal S05 OBD บน Raspberry Pi (Raspberry Pi OS Lite)
# วิธีใช้: bash pi/install.sh   (รันจากโฟลเดอร์โปรเจกต์ ไม่ต้องใส่ sudo)
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/deepal-obd
DATA=/var/lib/deepal-obd
CONF=/etc/default/deepal-obd
USER_NAME="${SUDO_USER:-$USER}"

echo "==> ติดตั้งแพ็กเกจที่ต้องใช้"
sudo apt-get update
sudo apt-get install -y python3-serial bluez
# Bluetooth LE adapters (vLinker MC+ "MC-IOS") need bleak
sudo apt-get install -y python3-bleak || \
    { sudo apt-get install -y python3-pip &&
      sudo pip3 install --break-system-packages bleak; } || \
    echo "!! ติดตั้ง bleak ไม่ได้ ถ้าใช้กล่อง Bluetooth LE ให้รัน pi/pair.sh ตอนต่อเน็ตอีกครั้ง"

echo "==> คัดลอกโปรแกรมไปที่ $APP"
sudo rm -rf "$APP"
sudo mkdir -p "$APP"
sudo cp -r "$SRC/deepal_s05" "$SRC/pi" "$SRC/examples" "$SRC/README.md" "$APP/"

echo "==> ติดตั้งคำสั่งลัด obd"
sudo install -m 755 "$SRC/pi/obd" /usr/local/bin/obd
echo "$SRC" | sudo tee "$APP/.source" >/dev/null

echo "==> สร้างโฟลเดอร์เก็บข้อมูล $DATA"
sudo install -d -o "$USER_NAME" -g "$USER_NAME" "$DATA"

if [ ! -f "$CONF" ]; then
    echo "==> สร้างไฟล์ตั้งค่า $CONF"
    sudo tee "$CONF" >/dev/null <<'CONFEOF'
# พอร์ตกล่อง OBD (pi/pair.sh จะใส่ให้อัตโนมัติ)
OBD_PORT=rfcomm://00:00:00:00:00:00

# ตัวเลือกเพิ่ม เช่น
#   --ntfy deepal-s05-xxxxxxxx      ส่งแจ้งเตือนเข้ามือถือ
#   --signals /var/lib/deepal-obd/signals.json   ค่าที่หาเจอเพิ่ม
#   --capacity 56.1                 ความจุแบต
#   --car crv-hybrid --obdb /var/lib/deepal-obd/Honda-CR-V-Hybrid.json   ติดใน CR-V e:HEV (ดู docs/CRV.md)
OBD_OPTIONS=""

# ตัวเลือกของ autopilot เช่น
#   --shutdown-below 12.2   ปิด Pi เมื่อแบต 12V ต่ำ (ถ้าเสียบไฟที่ไม่ดับตามรถ)
#   --checkup-days 7        ตรวจรถอัตโนมัติทุกกี่วัน
AUTOPILOT_OPTIONS=""
CONFEOF
fi

echo "==> ลดการเขียนการ์ด: เก็บ log ระบบไว้ในหน่วยความจำ"
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=20M\n' | \
    sudo tee /etc/systemd/journald.conf.d/deepal-obd.conf >/dev/null

echo "==> อนุญาตให้โปรแกรมสั่งปิดเครื่องเมื่อแบต 12V ต่ำ และหยุด/เปิดบริการโดยไม่ถามรหัส"
{
    echo "$USER_NAME ALL=(root) NOPASSWD: /sbin/shutdown"
    for action in start stop restart; do
        echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl $action deepal-obd"
    done
} | sudo tee /etc/sudoers.d/deepal-obd >/dev/null
sudo chmod 440 /etc/sudoers.d/deepal-obd
sudo usermod -aG bluetooth "$USER_NAME"
sudo rfkill unblock bluetooth || true  # some images ship it blocked

echo "==> ติดตั้งบริการให้เริ่มเองทุกครั้งที่เปิดเครื่อง"
sed "s/@USER@/$USER_NAME/" "$APP/pi/deepal-obd.service" | \
    sudo tee /etc/systemd/system/deepal-obd.service >/dev/null
sudo cp "$APP/pi/deepal-hotspot.service" "$APP/pi/deepal-hotspot.timer" \
    /etc/systemd/system/
sudo chmod +x "$APP/pi/hotspot-first.sh"
sudo systemctl daemon-reload
sudo systemctl enable deepal-obd
sudo systemctl enable --now deepal-hotspot.timer

echo
echo "ติดตั้งเสร็จแล้ว ขั้นต่อไป:"
echo "  1. เสียบกล่อง OBD ที่รถ เปิดรถให้ READY แล้วรัน: obd pair"
echo "  2. (ไม่บังคับ) แก้ตั้งค่า: sudo nano $CONF"
echo "  3. เริ่มบริการ: sudo systemctl restart deepal-obd"
echo "  4. ดูการทำงาน: obd status"
echo
echo "ต่อไปพิมพ์ obd เพื่อเปิดเมนูคำสั่งทั้งหมด"
