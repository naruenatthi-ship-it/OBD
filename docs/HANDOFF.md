# ส่งต่องาน (handoff) — อัปเดต 2026-10-16

อ่าน [CLAUDE.md](../CLAUDE.md) (กติกา) ก่อนไฟล์นี้ · ผลจากรถจริงละเอียดอยู่ใน [TODO.md](TODO.md)

## สถานะตอนนี้

- โค้ดล่าสุดอยู่บน `claude/handoff-docs-continuation-3g6l2n` (ต่อจาก `claude/happy-hawking-6te6q7`, ไม่มี PR) · เทสต์ผ่าน 169 ตัว
- session 2026-10-16 (ต่อ): เพิ่มแจ้งเตือน Telegram (`obd telegram TOKEN`, `--telegram CHAT_ID`,
  คำสั่ง `notify-test`) ยังไม่มีผล scan จากเจ้าของ
- ฮาร์ดแวร์ใช้งานจริงแล้ว: Pi Zero 2 W ในรถ + vLinker MC+ (BLE) → อ่าน BMS/เซลล์/มอเตอร์ของ Deepal ได้
- เจ้าของเพิ่งต่อ Pi เข้า WiFi ที่ทำงานได้ และได้รับคำแนะนำให้:
  1. `obd update`
  2. `obd bg scan 7A4,742 0100-EFFF` (หา OBC) แล้วดูด้วย `obd log`
  3. ผลอยู่ที่ `http://<IP>:8000/files` (IP จาก `obd web`)
  → **ยังไม่รู้ผล** ถ้าเจ้าของส่งไฟล์ scan มา ให้หา DID ที่ตอบใน 7A4/742 แล้วบันทึกลง TODO.md

## สิ่งที่เขียนแล้วแต่ยังไม่เคยลองบน Pi จริง

ถ้าเจ้าของเจอปัญหา ให้สงสัยของในรายการนี้ก่อน

- `deepal-hotspot.timer` (`pi/hotspot-first.sh`): ทุก 60 วิ ถ้าเห็น Hotspot และยังไม่ได้ต่อ → `nmcli connection up hotspot`
- `obd wifi add/remove`, `obd off`, `obd reboot`, `obd bg`, `obd log`, คำใบ้ตอนพิมพ์ `obd 17` นอกเมนู
- `discover` เขียน CSV ทีละแถว (ดับกลางคันผลไม่หาย)
- `obd telegram TOKEN` / `obd telegram`: บันทึก `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` ลง
  `/etc/default/deepal-obd` (chmod 640) หาเลขแชตจาก getUpdates ส่งข้อความทดสอบ แล้ว restart บริการ
  · ยังไม่เคยลองกับบอทจริง (ในเทสต์ใช้ API ปลอม) · ต้อง `obd update` ก่อน

## เหตุการณ์ที่ควรรู้

- ระหว่าง `obd bg`/`obd rec` บริการ deepal-obd ถูกหยุด หน้า dashboard และ `/files` จึงเปิดไม่ได้
  → เพิ่ม `obd show [คำค้น]` พิมพ์ไฟล์ผลในจอแทน (ต้อง `obd update` หลังงานเบื้องหลังเสร็จ)
  · ไอเดียต่อ: ให้ `/files` เปิดได้ตลอด (เว็บไฟล์แยกจากตัวที่ใช้กล่อง)
- 13:51 เริ่ม `obd bg scan 7A4,742 0100-EFFF` รอบใหม่ (รอบ 13:03 ได้ 7A4 ถึง 3000 แล้ว)

- **13:03** สแกน `7A4,742 0100-EFFF` (ไม่ได้ชาร์จ) ไปถึง `7A4:3000` แล้ว Pi ดับ เพราะรถตัดไฟเอง
  ผลช่วง 7A4 0100–2FFF อยู่ใน `bg_20261016-1303.log` บน Pi แต่ยังไม่ได้ดู
- **WiFi ที่ทำงานครั้งแรก**: ต่อแล้วมือถือหา Pi ไม่เจอ ทั้ง WiFi นั้นและ Hotspot
  สาเหตุน่าจะเป็นวงที่กันอุปกรณ์มองเห็นกัน ร่วมกับรถตัดไฟ
  กู้ได้ที่บ้าน (WiFi บ้าน IP 192.168.1.37) ตอนนี้จำไว้ 3 วง:
  - บ้าน (priority 0)
  - `hotspot` (10)
  - `work` (0)
- NetworkManager ไม่สลับจากวงที่ต่ออยู่ไปวงที่ priority สูงกว่าเอง จึงต้องเพิ่ม timer ข้างบน
- BMS มี DTC `P1B27-05` (บิตสั่งไฟเตือนติด) แต่หน้าปัดไม่มีไฟเตือน
  เจ้าของเจอคำอธิบายในเน็ตว่า "กระแสชาร์จเกิน" ยังไม่ยืนยัน · อีกตัวคือ `P1B53-05` · 7A4 มี `P1A89-00`

## งานค้าง (เรียงตามความสำคัญ)

1. **หา OBC**:
   - รอผล scan รอบที่ทำงาน
   - ถ้ายังไม่เจอ ให้สแกนซ้ำตอนเสียบชาร์จ AC ที่บ้าน (`obd bg scan 7A4,742 0100-EFFF`) และลอง `--extended`
2. **ยืนยัน offset อุณหภูมิมอเตอร์/อินเวอร์เตอร์ (A-40)**:
   - จอดค้างคืนให้รถเย็น แล้ว `obd check` ตอนเช้า
   - เทียบ `motor_temp` / `inverter_temp` กับอุณหภูมิอากาศ
3. **เทียบระยะทาง `obd trip` กับเลขไมล์จริง** เพื่อยืนยัน 80.9 rpm ต่อ km/h
4. **โรดแมป B–I ใน TODO.md**:
   - B: SOH จากการชาร์จ
   - C: ประสิทธิภาพ OBC
   - D: ลายนิ้วมือเซลล์
   - E: ดักฟัง CAN
   - G: VPS
   - H: เตือนจากแนวโน้ม
   - I: CR-V
5. **งานเสริม**: ไฟล์ `.bat` สำหรับ Windows, ค่าไฟการชาร์จ, เปลี่ยนชื่อ repo (เจ้าของตัดสินใจ)
   · Telegram ✅ เขียนแล้ว รอเจ้าของสร้างบอทแล้วลอง

## แผนที่โค้ดย่อ

| ไฟล์ | หน้าที่ |
|---|---|
| `deepal_s05/elm327.py` | คุยกับกล่อง ELM327 + `ALLOWED_REQUESTS` (อ่านอย่างเดียว) + `open_port` (serial/tcp/ble) |
| `deepal_s05/ble.py` | BLE ผ่าน bleak, หลุดค้างแล้วตัดทิ้งลองใหม่ |
| `deepal_s05/pids.py` | สูตรค่า Deepal (BMS, เซลล์, มอเตอร์ 7A7) + `derived()` |
| `deepal_s05/vehicles.py` | โปรไฟล์รถ (deepal / crv-hybrid), เกณฑ์เตือน, รหัสแพ็กที่รู้จัก |
| `deepal_s05/identity.py` | อ่านรหัส ECU (F187…) และเดารุ่นแพ็กจากรหัส BMS |
| `deepal_s05/cli.py` | คำสั่งทั้งหมด (check, health, dtc, ecus, discover, record, trip, dashboard, autopilot…) |
| `deepal_s05/dashboard.py` | หน้าเว็บ :8000 (แท็บเซลล์/อุณหภูมิ/IR/ข้อมูลแพ็ก, `/files`) |
| `deepal_s05/alerts.py` | เกณฑ์เตือน + ส่งแจ้งเตือน (ntfy, Telegram, `multi_sender`, `Notifier` กันส่งซ้ำ) |
| `deepal_s05/autopilot.py` | service อัตโนมัติบน Pi (อ่านเมื่อรถ READY, checkup, แจ้งเตือน ntfy) |
| `deepal_s05/analysis.py` | correlate, trip_summaries |
| `pi/obd` | คำสั่งลัดบน Pi (ติดตั้งเป็น `/usr/local/bin/obd`) |
| `pi/install.sh`, `pi/pair.sh` | ติดตั้ง / จับคู่กล่อง (ปลด rfkill, ติด bleak, sudoers, timer) |
| `emulator/deepal_s05_scenario.py` | รถจำลองสำหรับเทสต์ (layout เซลล์/มอเตอร์ตามรถจริง) |

## วิธีเริ่มงานต่อ

- ถามเจ้าของว่าผล scan ที่ทำงานเป็นยังไง (หรือให้ส่งไฟล์ `scan_*.csv` / ผล `obd log`)
- ถามว่าอยากลอง Telegram ไหม (ขั้นตอนอยู่ใน docs/USAGE.md หัวข้อแจ้งเตือน)
- ทุกคำสั่งบน Pi ให้เป็น `obd ...` บรรทัดเดียว
- ถ้าแก้ `pi/obd` ต้องบอกให้ `obd update` ก่อนใช้
- อัปเดตไฟล์นี้ทุกครั้งก่อนจบ session
