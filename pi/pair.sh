#!/bin/bash
# จับคู่บลูทูธกับกล่อง OBD (vLinker หรือ ELM327 อื่น) แล้วบันทึกที่อยู่ลงไฟล์ตั้งค่า
# วิธีใช้: เสียบกล่องที่รถ เปิดรถ แล้วรัน  bash pi/pair.sh
set -uo pipefail
CONF=/etc/default/deepal-obd

sudo systemctl start bluetooth
bluetoothctl power on >/dev/null

echo "กำลังค้นหาอุปกรณ์บลูทูธ 20 วินาที (เปิดรถไว้ และอยู่ใกล้กล่อง)..."
bluetoothctl --timeout 20 scan on >/dev/null 2>&1

mapfile -t DEVS < <(bluetoothctl devices | grep -iE "vlink|v-link|obd|elm" || true)
if [ ${#DEVS[@]} -eq 0 ]; then
    echo "ไม่เจอชื่อที่คล้ายกล่อง OBD อุปกรณ์ที่เจอทั้งหมด:"
    mapfile -t DEVS < <(bluetoothctl devices)
fi
if [ ${#DEVS[@]} -eq 0 ]; then
    echo "ไม่เจออุปกรณ์บลูทูธเลย ตรวจว่ากล่องเสียบอยู่และรถเปิดอยู่ แล้วลองใหม่"
    exit 1
fi

for i in "${!DEVS[@]}"; do echo "  [$((i+1))] ${DEVS[$i]}"; done
CHOICE=1
if [ ${#DEVS[@]} -gt 1 ]; then
    read -rp "เลือกหมายเลขกล่อง OBD: " CHOICE
fi
MAC=$(echo "${DEVS[$((CHOICE-1))]}" | awk '{print $2}')
echo "เลือก $MAC"

bluetoothctl --agent NoInputNoOutput pair "$MAC" >/dev/null 2>&1
bluetoothctl trust "$MAC" >/dev/null 2>&1
if ! bluetoothctl info "$MAC" | grep -q "Paired: yes"; then
    echo
    echo "จับคู่อัตโนมัติไม่สำเร็จ กล่องอาจต้องใช้รหัส PIN ทำเองตามนี้:"
    echo "  bluetoothctl"
    echo "  agent KeyboardOnly"
    echo "  default-agent"
    echo "  pair $MAC        (ถ้าถามรหัส ใส่ 1234 หรือ 0000)"
    echo "  trust $MAC"
    echo "  quit"
    echo "จากนั้นรัน pair.sh อีกครั้ง"
    exit 1
fi

sudo sed -i "s|^OBD_PORT=.*|OBD_PORT=rfcomm://$MAC|" "$CONF"
echo "บันทึก OBD_PORT=rfcomm://$MAC ลง $CONF แล้ว"

echo
echo "ทดสอบอ่านค่าจากรถ..."
sudo systemctl stop deepal-obd 2>/dev/null
cd /opt/deepal-obd && python3 -m deepal_s05 --port "rfcomm://$MAC" check
echo
echo "ถ้าค่าขึ้นถูกต้อง เริ่มบริการ: sudo systemctl restart deepal-obd"
