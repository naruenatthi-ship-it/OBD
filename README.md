<h1 align="center">Deepal S05 OBD</h1>

<p align="center">
  เฝ้าดูสุขภาพแบตเตอรี่และระบบของ <b>Deepal S05</b> ด้วยกล่อง ELM327 ราคาพันกว่าบาท<br>
  รู้สัญญาณก่อนพัง โดยไม่ต้องซื้อเครื่องสแกนหลักหมื่น · <b>อ่านอย่างเดียว ไม่เขียนค่าใดๆ ลงรถ</b>
</p>

<p align="center">
  <a href="https://github.com/naruenatthi-ship-it/OBD/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/naruenatthi-ship-it/OBD/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.8%2B-3776AB">
  <img alt="platform" src="https://img.shields.io/badge/runs%20on-Windows%20%7C%20Linux%20%7C%20Raspberry%20Pi-0d7a70">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-lightgrey"></a>
</p>

<table align="center">
  <tr>
    <td><img src="docs/images/dashboard-phone.png" width="260" alt="หน้าจอบน iPhone"></td>
    <td><img src="docs/images/dashboard-desktop.png" width="520" alt="หน้าจอบนคอม"></td>
  </tr>
</table>
<p align="center"><sub>หน้า dashboard บน iPhone และคอม (ข้อมูลตัวอย่าง ไม่ใช่ค่าจากรถจริง เซลล์ #47 จงใจทำให้ผิดปกติ)</sub></p>

> [!IMPORTANT]
> **สถานะ: เบต้า** ค่าแบตหลักมาจากข้อมูลที่ชุมชนแกะไว้จาก S05 ต่างประเทศ
> ลองกับ **S05 Max 56.1 kWh ไทย** แล้ว 1 คัน (ผ่านกล่อง vLinker MC+ แบบ Bluetooth LE): SOC, แรงดัน, กระแส,
> เซลล์สูงสุด/ต่ำสุด และอุณหภูมิแบต ใช้ได้ ส่วนแรงดันรายเซลล์และ SOH ของรุ่นนี้ยังต้องแกะเพิ่ม
> ถ้าลองแล้วได้ผลอย่างไร ช่วยแจ้งใน [Issues](../../issues) ได้เลย

## ทำอะไรได้บ้าง

| | |
|---|---|
| 🔋 **ดูแบตแบบสด** | SOC, แรงดัน/กระแส/กำลัง, เซลล์สูงสุด/ต่ำสุด (บอกเลขเซลล์), อุณหภูมิแบต, SOH, แบต 12V |
| 📱 **หน้าจอบนมือถือ** | dashboard ในเบราว์เซอร์ ดูจาก iPhone ผ่าน Hotspot ได้ แท็บแรงดันรายเซลล์ (ทำเครื่องหมายเซลล์ผิดปกติ), อุณหภูมิ, IR รายเซลล์ และข้อมูลแพ็ก/BMS |
| 🪪 **รู้จักรุ่นแบต** | อ่านรหัสชิ้นส่วนและเวอร์ชันของ BMS ถ้ารู้จักรหัสจะตั้งความจุแบตให้เอง ไม่ต้องเลือกรุ่นจากรายการแบบเครื่องสแกน |
| ⚡ **ฉนวนไฟแรงสูง** | เตือนเมื่อค่าฉนวนต่ำกว่าเกณฑ์มาตรฐาน (Ω/V) และเก็บลงกราฟแนวโน้ม เมื่อแกะ DID ได้แล้ว ตอนนี้จับจากโค้ดปัญหาไปก่อน |
| 🩺 **ตรวจรถรายสัปดาห์** | `checkup` รวมค่าแบต แบต 12V และโค้ดปัญหาทุกกล่อง เทียบกับครั้งก่อน แล้วสรุปเป็น ✅ / ⚠️ / 🔴 |
| 📈 **ดูแนวโน้มระยะยาว** | กราฟ SOH, ส่วนต่างเซลล์, แบต 12V, อุณหภูมิ, IR ของแพ็ก ช่วยเห็นสิ่งที่ค่อยๆ แย่ลง |
| 🔔 **แจ้งเตือนเข้ามือถือ** | แบตร้อน, แบต 12V ต่ำ, เซลล์ไม่สมดุล, โค้ดปัญหาใหม่ ผ่านแอป [ntfy](https://ntfy.sh) (ฟรี) หรือ Telegram |
| ⚡ **ชาร์จและบาลานซ์** | บันทึกระหว่างชาร์จ จับสัญญาณ OBC ลดกำลังเพราะร้อน เทียบชาร์จ 32A กับ 16A และเฝ้าดูการบาลานซ์ตอนเต็ม |
| 🚗 **ทดสอบขับซ้ำ** | `drivetest` เร่งหรือขับเส้นทางเดิม แล้วเทียบกำลังสูงสุด แรงดันตก และ IR กับเดือนก่อน |
| 🔧 **อ่านโค้ดปัญหา** | อ่านจากทุกกล่อง ECU บอกสถานะ และเทียบกับการสแกนครั้งก่อน (ไม่ลบโค้ด) |
| 📸 **ภาพนิ่งตอนเกิดโค้ด** | `freeze` อ่าน freeze frame ว่ารถทำอะไรอยู่ (SOC กระแส อุณหภูมิ) ตอนที่ตั้งโค้ด |
| 🗺️ **แผนผัง ECU ทั้งคัน** | หน้า `/topology` แสดงทุกกล่องในรถ เขียว/เหลือง/แดงตามโค้ดปัญหา กดดูรหัสชิ้นส่วนและโค้ด |
| 🏁 **ไดโนจำลอง** | หน้า `/dyno` กราฟกำลัง/แรงบิดเทียบความเร็ว และแผนที่ประสิทธิภาพมอเตอร์ (รอบ × แรงบิด) จากการขับจริง |
| 🔮 **เตือนล่วงหน้า** | หน้า `/health` เทียบทุกชิ้นส่วนกับปกติของรถคันนี้ และบอกว่าจะถึงเกณฑ์ในอีกกี่เดือน (แบต 12V, เซลล์อ่อน, หล่อเย็น, มอเตอร์, โค้ดซ้ำ) |
| 🔋 **SOH วัดเอง** | `soh` คำนวณความจุแบตจริงจากการชาร์จ (รุ่นที่ไม่บอก SOH) |
| 💸 **ค่าไฟการชาร์จ** | `/cost` สรุปรายเดือน ชาร์จบ้าน (อัตราเดียว/TOU) กับตู้ชาร์จเร็ว |
| 🌐 **ดูรถจากที่ไหนก็ได้** | Pi ส่งข้อมูลขึ้น VPS ของคุณเอง เปิดหน้าเว็บชุดเดียวกันพร้อมรหัสผ่าน |
| 🔌 **กราฟการชาร์จ** | หน้า `/charge` กำลังชาร์จ อุณหภูมิ แรงดันเซลล์เทียบ SOC และเซลล์ที่ขึ้นนำทุกครั้ง |
| 🧪 **แกะค่าใหม่เอง** | `ecus`, `discover`, `scandiff`, `record`, `correlate` ช่วยหาค่าที่ยังไม่มีใครเผยแพร่ ทำเองที่บ้านได้ |
| 🍓 **Raspberry Pi ในรถ** | ติดไว้ถาวร บันทึกเองทุกครั้งที่รถติด ไม่ส่งอะไรเข้ารถตอนรถหลับ |

<p align="center">
  <img src="docs/images/trends.png" width="45%" alt="กราฟแนวโน้ม">
  <img src="docs/images/report.png" width="45%" alt="รายงานการชาร์จ">
</p>

## รถที่รองรับ

| รถ | ตัวเลือก | ข้อมูลจาก | สถานะ |
|---|---|---|---|
| **Deepal S05** (Max 56.1 kWh, 68.82 kWh) | ค่าเริ่มต้น | [jpires71/Deepal_S05_PIDs](https://github.com/jpires71/Deepal_S05_PIDs) | 🟡 เบต้า ยังไม่ได้ลองกับรถไทย |
| **Honda CR-V e:HEV** (เจน 6, 2023+) | `--car crv-hybrid` | [OBDb/Honda-CR-V-Hybrid](https://github.com/OBDb/Honda-CR-V-Hybrid) | 🟡 เบต้า ยังไม่ได้ลองกับรถไทย · [วิธีใช้](docs/CRV.md) |

CR-V อ่านแรงดันรายเซลล์ได้ครบ 72 เซลล์ พร้อมอุณหภูมิโมดูลแบต, DC-DC, พัดลมแบต, ลมยาง และค่าเครื่องยนต์

## อุปกรณ์ที่ต้องใช้

| อุปกรณ์ | หมายเหตุ |
|---|---|
| **กล่อง ELM327 ที่ดี** | แนะนำ **Vgate vLinker MC+** (บลูทูธ 3.0+4.0 ใช้ได้ทั้ง iPhone, Android, Windows) กล่องโคลนราคาถูกมากมักใช้กับรถรุ่นนี้ไม่ได้ |
| **คอมพิวเตอร์** อย่างใดอย่างหนึ่ง | โน้ตบุ๊ก/คอม Windows, Linux หรือ macOS ที่มี Python 3.8+ **หรือ** Raspberry Pi Zero 2 W ติดในรถ ([วิธีติดตั้ง](pi/README.md)) |
| iPhone/Android (ไม่บังคับ) | ดู dashboard และรับแจ้งเตือน |

## เริ่มใช้งาน

```bash
pip install -r requirements.txt

# 1) ตรวจว่ารถตอบไหม (รถต้อง READY)
python -m deepal_s05 --port COM5 check

# 2) หน้าจอสดในเบราว์เซอร์ (เปิด http://localhost:8000)
python -m deepal_s05 --port COM5 dashboard

# 3) ตรวจรถประจำสัปดาห์
python -m deepal_s05 --port COM5 checkup
```

`--port` คือพอร์ตของกล่อง: `COM5` (บลูทูธ/USB บน Windows), `/dev/rfcomm0`, `rfcomm://AA:BB:CC:DD:EE:FF`, `ble://AA:BB:CC:DD:EE:FF` (กล่อง Bluetooth LE เช่น vLinker MC+ รุ่น MC-IOS ต้องมี bleak)
(บลูทูธบน Linux/Pi) หรือ `socket://192.168.0.10:35000` (กล่อง WiFi)

**ยังไม่มีรถหรือกล่อง?** ลองกับตัวจำลองได้:

```bash
pip install "ELM327-emulator @ git+https://github.com/Ircama/ELM327-emulator@v4.0.0"
python emulator/run_emulator.py
python -m deepal_s05 --port socket://localhost:35000 --signals examples/signals_demo.json dashboard
```

## คำสั่งทั้งหมด

| กลุ่ม | คำสั่ง |
|---|---|
| ดูค่า | `check` · `live` · `dashboard` |
| สุขภาพรถ | `checkup` · `trends` · `health` · `drivetest` · `dtc` · `aux12v` |
| ชาร์จ | `charge` · `balance` · `report` |
| Raspberry Pi | `autopilot` |
| แกะค่าใหม่ | `ecus` · `discover` · `scandiff` · `record` · `correlate` · `sniff` · `analyze` |

รายละเอียดทุกคำสั่ง ตัวเลือก และเกณฑ์แจ้งเตือน: **[คู่มือการใช้งานฉบับเต็ม](docs/USAGE.md)**

## ค่าที่อ่านได้ตอนนี้

ECU แบตเตอรี่ (BMS) header `7A1` → ตอบที่ `7A9`, UDS service `0x22`

| ค่า | DID | สูตร | รถต่างประเทศ (68.8 kWh) | S05 Max 56.1 ไทย |
|---|---|---|---|---|
| SOC | `F22F` | `A` | ✅ | ✅ ตรงกับหน้าจอรถ |
| แรงดันแพ็ก | `F228` | `(A*256+B)/10` V | ✅ | ✅ ตอบ (~405 V ที่ 98%) |
| กระแสแพ็ก | `F229` | `((A*256+B)-6015)/10` A | ✅ | ✅ V×I ตรงกับกำลังชาร์จบนจอ · **ลบ = ชาร์จเข้า** |
| แรงดันเซลล์สูงสุด / ต่ำสุด | `F250` / `F251` | `(A*256+B)/1000` V | ✅ | ✅ ตอบ |
| อุณหภูมิแบตสูงสุด / ต่ำสุด | `F252` / `F253` | `A-40` °C | ✅ | ✅ ตอบ (30–32 °C) |
| อุณหภูมิหัวชาร์จ | `F255` | `(A-32)/1.8` °C | 🟡 | ❌ ค่าไม่สมเหตุสมผล (10 °C ตอนชาร์จ) |
| SOH | `F27D` | `(A*65536+B*256+C)/10000` % | 🟡 | ❌ ไม่ตอบ (NRC 0x31) |
| **แรงดันรายเซลล์ 120 เซลล์** | `F29F`–`F2EF`, `F3A9`–`F3B0`, `F3E1`–`F3FF` (DID ละเซลล์) | `(A*256+B)/1000` V | - | 🟡 เจอในรถไทย ผลรวมตรงกับแรงดันแพ็ก · ลำดับเซลล์ยังไม่ยืนยัน |
| **อุณหภูมิในแพ็ก 16 จุด** | `F2F0`–`F2FF` | `A-40` °C | - | 🟡 เจอในรถไทย สูงสุด/ต่ำสุดตรงกับ F252/F253 |
| **รอบมอเตอร์** (กล่อง `7A7`) | `FD07` | `(A*256+B)-20000` rpm | - | ✅ 0 ตอนจอด · แรงบิด × รอบ ตรงกับกำลังแบต |
| **แรงบิดมอเตอร์** (`7A7`) | `FD08` | `((A*256+B)-4000)/10` Nm | - | ✅ ลบ = เบรกชาร์จกลับ |
| **แรงดันเข้าอินเวอร์เตอร์** (`7A7`) | `FD1A` | `A*256+B` V | - | ✅ ตรงกับแรงดันแพ็ก |
| **อุณหภูมิมอเตอร์** (`7A7`) | `FD0A` | `A-40` °C | - | 🟡 ขึ้นช้าๆ 49 → 69 °C ระหว่างขับ · offset รอยืนยัน |
| **อุณหภูมิอินเวอร์เตอร์** (`7A7`) | `FD1E` | `A-40` °C | - | 🟡 ขึ้นตามกระแสทันที · offset รอยืนยัน |

รหัส BMS ของ S05 Max 56.1 ไทย: `2101210-EM01` (โปรแกรมรู้จักและตั้งความจุให้เอง)

**ยังไม่มีใครเผยแพร่:** SOH ของรุ่น 56.1, อุณหภูมิ OBC, ความเร็วรถ/เลขไมล์, น้ำหล่อเย็น, ค่าฉนวน
ช่วยกันแกะได้ด้วย [คู่มือวันแกะข้อมูล](REVERSE.md) แล้วส่งผลผ่าน [Issues](../../issues/new/choose)
ค่าที่แกะได้ใส่ในไฟล์ `--signals` ได้ทันทีโดยไม่ต้องแก้โค้ด

## ความปลอดภัย

- โค้ดยอมส่งเฉพาะคำสั่งอ่าน: `22` (อ่านค่า), `19 02` (อ่านโค้ดปัญหา), `19 04` (อ่านภาพนิ่งตอนเกิดโค้ด), `10 01/03` (สลับโหมดวินิจฉัย), `3E 00`
  คำสั่งเขียนค่า ลบโค้ด รีเซ็ต ปลดล็อก หรือสั่งงาน **ถูกปฏิเสธในโค้ด** และมีชุดทดสอบตรวจเรื่องนี้
- โหมด Raspberry Pi ไม่ส่งข้อความเข้ารถตอนรถหลับ จะได้ไม่ไปปลุกรถจนแบต 12V หมด
- ไฟล์ที่บันทึกจาก `discover`/`record` ตัดเลข VIN และ serial ออกให้อัตโนมัติ ก่อนแชร์ไฟล์

## แผนงาน

- [x] อ่านค่าแบตหลัก, dashboard, แจ้งเตือน, checkup, trends, ชาร์จ/บาลานซ์, drivetest
- [x] อ่านโค้ดปัญหาและเทียบกับครั้งก่อน
- [x] เครื่องมือแกะค่าใหม่ และโหมด Raspberry Pi
- [x] ยืนยันค่าแบตหลักกับ S05 Max 56.1 ไทย (SOC, แรงดัน, กระแส, เซลล์สูง/ต่ำสุด, อุณหภูมิ)
- [ ] ยืนยันกับรุ่น 68.82 kWh ไทย และหา SOH ของรุ่น 56.1
- [x] แกะแรงดันรายเซลล์ (120) และอุณหภูมิในแพ็ก (16 จุด) ของรุ่น 56.1 ไทย
- [x] อุณหภูมิมอเตอร์และอินเวอร์เตอร์ รอบ และแรงบิด (กล่อง 7A7)
- [ ] อุณหภูมิ OBC และน้ำหล่อเย็น
- [ ] ค่าความต้านทานฉนวนระบบไฟแรงสูง
- [ ] ตั้งเกณฑ์แจ้งเตือนจากข้อมูลรถจริงหลายคัน
- [x] รองรับ Honda CR-V e:HEV (CAN 29 บิต, ข้อมูลจาก OBDb)
- [ ] ยืนยันค่า CR-V กับรถสเปกไทย

## ขอบคุณ

- [jpires71/Deepal_S05_PIDs](https://github.com/jpires71/Deepal_S05_PIDs) ผู้แกะและยืนยันค่าแบตชุดแรกของ S05
- [OBDb](https://github.com/OBDb) ฐานข้อมูลค่าของรถหลายยี่ห้อ ใช้สำหรับ Honda CR-V e:HEV (CC BY-SA 4.0)
- [Ircama/ELM327-emulator](https://github.com/Ircama/ELM327-emulator) ใช้เป็นตัวจำลองสำหรับทดสอบ

## ข้อจำกัดความรับผิดชอบ

โปรเจกต์นี้ทำโดยผู้ใช้รถ **ไม่เกี่ยวข้องกับ Deepal, Changan หรือตัวแทนจำหน่าย**
ไม่เกี่ยวข้องกับ Honda เช่นกัน ชื่อและเครื่องหมายการค้าเป็นของเจ้าของ ค่าที่อ่านได้เป็นค่าที่ชุมชนแกะเอง อาจไม่ตรงกับค่าจริง
ใช้เพื่อเฝ้าดูและประกอบการตัดสินใจเท่านั้น ไม่ใช่การวินิจฉัยของช่างผู้เชี่ยวชาญ การใช้งานเป็นความเสี่ยงของผู้ใช้เอง

---

## English summary

Read-only monitoring of the **Deepal S05** (Changan) through a cheap ELM327 adapter: live battery
data (SOC, pack voltage/current, min/max cell voltage, temperatures, SOH, 12 V), a phone-friendly
dashboard, weekly health checkups with history and trend charts, push alerts via ntfy or Telegram, charge and
balancing logs, repeatable drive tests, trouble code reading with diffs, tools for reverse
engineering unknown DIDs at home, and an unattended Raspberry Pi mode that never wakes a sleeping
car. Only read-type UDS requests are ever sent. Battery DIDs come from
[jpires71/Deepal_S05_PIDs](https://github.com/jpires71/Deepal_S05_PIDs); not yet verified on
Thai-market cars. The **Honda CR-V e:HEV** (6th gen) is supported too with `--car crv-hybrid`,
using signal definitions from [OBDb](https://github.com/OBDb/Honda-CR-V-Hybrid) (CC BY-SA 4.0)
over 29-bit CAN, including all 72 cell voltages. Docs are in Thai; issues in English are welcome.

## License

[MIT](LICENSE)
