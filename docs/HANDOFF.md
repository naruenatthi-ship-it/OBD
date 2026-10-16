# ส่งต่องาน (handoff) — อัปเดต 2026-10-16

กติกาอยู่ใน [CLAUDE.md](../CLAUDE.md) · แผนงาน [TODO.md](TODO.md) · ผลจากรถจริง [RESULTS.md](RESULTS.md) (ยาว ใช้ grep)
**ไฟล์นี้ต้องสั้น (ไม่เกิน ~60 บรรทัด)**: เก็บแค่สถานะตอนนี้ ประวัติย้ายไป RESULTS.md

## สถานะตอนนี้

- โค้ดอยู่บน `claude/happy-hawking-6te6q7` (Pi ดึงจากที่นี่) · เทสต์ผ่าน 235 ตัว · ไม่มี PR
- อาจมี session อื่น push ไป happy-hawking พร้อมกัน → ก่อน push: `git fetch` แล้ว merge ก่อนเสมอ
- ฮาร์ดแวร์ใช้จริงแล้ว: Pi Zero 2 W ในรถ + vLinker MC+ (BLE) อ่าน BMS/เซลล์/มอเตอร์/เลขไมล์ได้
- `obd survey`: ✅ ครบแล้ว (23:28) ไม่ต้องรันอีก · ผลเด่น: 7A1 F25F (ฉนวน?), F233 (kW ชาร์จสูงสุด?) · เจ้าของตกลงตัดการไล่ 0000–EFFF
  ทั้งหมด (~60 ชม. ไม่คุ้ม) เหลือสุ่ม 3 ช่วง×256 ต่อกล่อง · survey.json เก่าแปลงเองตอนโหลด (plan 2) ต้อง `obd update`
  · ผลล่าสุดสรุปใน RESULTS.md (753/754 F1F0 น่าจะแรงดัน 12V) · แก้ให้บันทึกตำแหน่งทุกครั้งที่เจอค่า (ต้อง `obd update`)
- ✅ VPS ตั้งแล้ว: Ubuntu 22.04, ผู้ใช้ `deepal` (กุญแจของ Pi เท่านั้น), เว็บพอร์ต 8080 มีรหัสผ่าน (http)
  · บอทของเจ้าของใช้ root + พอร์ต 8787 อย่าแตะ · ค้าง: HTTPS (Caddy + sslip.io), ปิด root login ด้วยรหัสผ่าน
- ใหม่: หน้า `/digest` (หรือ `obd survey digest`) สรุปผล survey/scan เป็นข้อความสั้น (เล็กกว่าไฟล์ดิบ ~6 เท่า เฉพาะของใหม่) → `survey_digest.txt` · หน้า `/digest` สร้างเองตอนเปิด ปุ่มคัดลอก/ดาวน์โหลด = ทำเครื่องหมายว่าส่งแล้ว · `?since=เวลา` ย้อนดูจากเวลานั้น (ยังไม่ได้ลองบน iPhone)
  ใช้ `--file scan_xxx.csv` กับไฟล์ scan, `--all` ดูทั้งหมด · ยังไม่ได้ลองบน Pi (ต้อง `obd update`)

## รอเจ้าของตัดสินใจ / ส่งข้อมูล

- ⏰ **เตือนเจ้าของ (ขอไว้ 2026-10-16)**: ครั้งหน้าที่ชาร์จ AC ให้ `obd check` ตอนเสียบ และอีกครั้งหลังถอด 5 นาที
  → เทียบ 7A1 F25F (ตัวเต็งค่าฉนวน: 8000 ตอนไม่เสียบ, 222 หลังชาร์จ AC) ดู RESULTS.md
- `/files` ระหว่าง `obd bg`/`obd rec`: รัน `serve` ที่พอร์ต 8000 แทน dashboard ชั่วคราว แต่ `serve` บังคับรหัสผ่าน
  → เจ้าของเลือก: ใช้รหัสผ่าน (แนะนำ) / ไม่ใช้ / ไม่ทำ (ระบบความปลอดภัยไม่ยอมให้ Claude ปิดรหัสผ่านเอง)
- `obd rec ขับ 20 watch` ตอนขับ (หาความเร็วล้อ 780 FD0C–FD10) · ชาร์จ SOC ต่ำ→เต็มแล้ว `obd soh` · บิลค่าไฟ (tariff.json)
- อุณหภูมิมอเตอร์เปลี่ยนเป็น A-50 (17 ต.ค.) ยืนยันอีกครั้ง: รถ**ดับ**ทั้งคืน แล้ว `obd check` ตอนเช้าเทียบอากาศ · ยืนยัน 80.9 rpm ต่อ km/h ด้วย `obd trip`
- ชาร์จ AC ครั้งหน้า ถ่ายจอรถ (V, A) → ยืนยัน 7A4 FD23 / ประสิทธิภาพ OBC · ตั้ง Telegram (`obd telegram TOKEN`)

## เขียนแล้วแต่ยังไม่เคยลองบน Pi จริง (มีปัญหาให้สงสัยก่อน)

- `deepal-hotspot.timer`, `obd wifi add/remove`, `obd off/reboot/bg/log`, `obd freeze` (19 04), `obd show/clean`
- หน้า `/topology`, `/dyno`, `/charge`, `/freeze`, `/health` (predict), `/cost` · `obd soh`, `obd canscan/cancompare`
- `obd telegram` (ทดสอบกับ API ปลอมเท่านั้น) · แจ้งเตือนชาร์จ DC (SOC ≥ 85%, แบต > 40 °C)
- predict: เกณฑ์ 12V 12.2 V ตามแบตตะกั่ว (ถ้า Deepal ใช้ 12V ลิเธียมต้องปรับ) · 12V กลางคืนน่าจะ ⚪ ตลอด (Pi ดับตอนรถหลับ)
- WiFi: จำไว้ 3 วง บ้าน (IP 192.168.1.37), `hotspot` (priority 10), `work` (วงที่ทำงานกันอุปกรณ์มองเห็นกัน)

## แผนที่โค้ดย่อ

| ไฟล์ | หน้าที่ |
|---|---|
| `deepal_s05/elm327.py` | ELM327 + `ALLOWED_REQUESTS` (อ่านอย่างเดียว) + `open_port` (serial/tcp/ble) · `ble.py` = bleak |
| `deepal_s05/pids.py`, `vehicles.py` | สูตรค่า Deepal + `derived()` · โปรไฟล์รถ เกณฑ์เตือน รหัสแพ็ก |
| `deepal_s05/cli.py` (2,300 บรรทัด ใช้ grep) | คำสั่งทั้งหมด · `PRIVATE_DIDS` |
| `deepal_s05/autopilot.py`, `survey.py` | service บน Pi · สำรวจ DID ทีละนิด + `digest` |
| `deepal_s05/dashboard.py`, `visual.py`, `remote.py` | หน้าเว็บ :8000 + `/files` · หน้าภาพจากไฟล์ · เว็บบน VPS |
| `deepal_s05/alerts.py`, `predict.py` | แจ้งเตือน (ntfy/Telegram) · เตือนล่วงหน้าจากแนวโน้ม |
| `deepal_s05/soh.py`, `cost.py`, `canscan.py`, `analysis.py` | SOH, ค่าไฟ, ดักฟัง CAN, ทริป |
| `pi/obd`, `pi/install.sh`, `pi/pair.sh`, `pi/vps-*` | คำสั่งลัดบน Pi, ติดตั้ง, จับคู่กล่อง, ฝั่ง VPS |
| `emulator/deepal_s05_scenario.py` | รถจำลองสำหรับเทสต์ |

## วิธีเริ่มงานต่อ

- อ่านแค่ CLAUDE.md + ไฟล์นี้ก่อน อย่าเปิด RESULTS.md/USAGE.md ทั้งไฟล์ ใช้ grep หาเฉพาะที่ต้องใช้
- ขอให้เจ้าของส่งผลจากหน้า `/digest` (หรือ `obd log`) แทนไฟล์ CSV ดิบ
- ถ้าแก้ `pi/obd` ต้องบอกให้ `obd update` ก่อนใช้ · อัปเดตไฟล์นี้ก่อนจบ session (ให้สั้นเหมือนเดิม)
