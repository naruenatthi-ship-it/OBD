# ติดตั้งบน Raspberry Pi (ติดไว้ในรถ)

Pi จะทำงานเองทุกครั้งที่ได้รับไฟ:

- **รถตื่น** (แบต 12V ≥ 13.0 V แปลว่า DC-DC ทำงาน เช่น รถ READY หรือกำลังชาร์จ):
  อ่านค่าแบตทุก 5 วินาที บันทึกลง `drive_วันที่.csv` เช็กเกณฑ์แจ้งเตือน
  และส่งเข้ามือถือ (ถ้าตั้ง ntfy ไว้)
- **ตรวจรถรายสัปดาห์** (`checkup`) อัตโนมัติ เมื่อรถ READY และจอดนิ่ง (กระแสต่ำกว่า 5 A)
- **รถหลับ:** ไม่ส่งข้อความใดๆ เข้ารถ (จะได้ไม่ไปปลุกรถ) วัดแค่แรงดันแบต 12V
  ทุก 5 นาทีลง `aux12v_เดือน.csv`
- **หน้าจอ** `http://deepal-pi.local:8000` ดูค่าสดได้จาก iPhone หรือคอม
  มีหน้า **ไฟล์ข้อมูล** (ดาวน์โหลดประวัติทั้งหมด) และ **กราฟแนวโน้ม**

โปรแกรมส่งได้แค่คำสั่งอ่านค่าเหมือนเดิม

## ของที่ต้องมี

- Raspberry Pi Zero 2 W (หรือ Pi 3B+/4)
- microSD 32 GB (แนะนำรุ่น High Endurance)
- ที่ชาร์จในรถ 5V ≥ 2.5A พร้อมสาย micro-USB (เสียบช่อง **PWR IN** ของ Pi)
- กล่อง vLinker MC+ (หรือ ELM327 บลูทูธที่ดี)
- คอมตั้งโต๊ะ (ใช้เตรียมการ์ดครั้งแรก)

## ขั้นที่ 1: เตรียมการ์ดจากคอมตั้งโต๊ะ

1. ดาวน์โหลดและติดตั้ง **Raspberry Pi Imager** จาก https://www.raspberrypi.com/software/
2. เสียบ microSD เข้าคอม แล้วเปิด Imager
3. เลือก
   - **Device:** Raspberry Pi Zero 2 W
   - **OS:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**
   - **Storage:** การ์ดที่เสียบอยู่ (ระวังเลือกผิดเป็นไดรฟ์อื่น)
4. กด Next → **Edit settings** แล้วตั้งค่า
   - **Hostname:** `deepal-pi`
   - **Username / Password:** ตั้งเองและจดไว้
   - **Wireless LAN:** ชื่อและรหัส WiFi บ้าน (ต้องเป็น **2.4 GHz** เพราะ Zero 2 W ใช้ 5 GHz ไม่ได้)
     และ Wireless LAN country: **TH**
   - **Locale:** Time zone `Asia/Bangkok`
   - แท็บ **Services:** เปิด **Enable SSH** (ใช้ password)
5. กด Save → Yes เพื่อเขียนการ์ด รอจนเสร็จ

## ขั้นที่ 2: เปิด Pi และเข้าไปสั่งงานจากคอม

1. เสียบการ์ดเข้า Pi แล้วเสียบไฟที่ช่อง **PWR IN** (ทำที่บ้านก่อนก็ได้ ใช้ที่ชาร์จมือถือ 5V)
2. รอ 2–3 นาที (การเปิดครั้งแรกจะนานกว่าปกติ)
3. บนคอม Windows เปิด **PowerShell** แล้วพิมพ์

   ```
   ssh ชื่อผู้ใช้@deepal-pi.local
   ```

   ตอบ `yes` แล้วใส่รหัสผ่าน (ถ้าหา `deepal-pi.local` ไม่เจอ ดู IP ของ Pi ในหน้าตั้งค่าเราเตอร์
   แล้วใช้ `ssh ชื่อผู้ใช้@IP` แทน)

## ขั้นที่ 3: ติดตั้งโปรแกรม

บน Pi (ในหน้าต่าง ssh):

```bash
sudo apt-get install -y git
git clone -b claude/happy-hawking-6te6q7 https://github.com/naruenatthi-ship-it/OBD.git
cd OBD
bash pi/install.sh
```

ถ้ารีโปเป็นแบบส่วนตัว (private) แล้ว git clone ไม่ได้: ดาวน์โหลด ZIP จาก GitHub บนคอม
แล้วส่งเข้า Pi ด้วยคำสั่งบนคอม `scp OBD.zip ชื่อผู้ใช้@deepal-pi.local:`
จากนั้นบน Pi รัน `sudo apt-get install -y unzip && unzip OBD.zip` แล้วเข้าโฟลเดอร์ที่ได้ และรัน `bash pi/install.sh`

## ขั้นที่ 4: ให้ Pi ต่อ Hotspot ของ iPhone ได้ตอนอยู่ในรถ

1. บน iPhone: ตั้งค่า → ฮอตสปอตส่วนบุคคล → เปิด **"เพิ่มความเข้ากันได้สูงสุด"**
   (Maximize Compatibility) เพื่อให้ใช้ 2.4 GHz
2. เปิด Hotspot ไว้ใกล้ Pi แล้วบน Pi รัน

   ```bash
   sudo nmcli dev wifi connect "ชื่อ Hotspot" password "รหัส Hotspot"
   ```

Pi จะจำทั้ง WiFi บ้านและ Hotspot แล้วต่อเองตามที่เจอ

## ขั้นที่ 5: จับคู่กล่อง OBD

เอา Pi ไปที่รถ เสียบกล่อง vLinker ที่ช่อง OBD เปิดรถให้ READY แล้วบน Pi รัน

```bash
cd ~/OBD
bash pi/pair.sh
```

สคริปต์จะหากล่อง จับคู่ บันทึกที่อยู่ และลองอ่านค่าแบต (`check`) ให้ดู
ถ้าค่าถูกต้อง เริ่มบริการ:

```bash
sudo systemctl restart deepal-obd
journalctl -u deepal-obd -f      # ดูการทำงาน (กด Ctrl+C เพื่อออก)
```

## ขั้นที่ 6 (ไม่บังคับ): แจ้งเตือนและค่าเพิ่มเติม

```bash
sudo nano /etc/default/deepal-obd
```

- แจ้งเตือนเข้ามือถือ: `OBD_OPTIONS="--ntfy deepal-s05-ตั้งชื่อให้เดายาก"`
- ค่าที่หาเจอเพิ่ม (อุณหภูมิ OBC, แรงดันรายเซลล์): วางไฟล์ไว้ที่
  `/var/lib/deepal-obd/signals.json` แล้วเพิ่ม `--signals /var/lib/deepal-obd/signals.json`
- ติดใน **Honda CR-V e:HEV**: ดูขั้นตอนใน [docs/CRV.md](../docs/CRV.md#ใช้กับ-raspberry-pi)
- เสร็จแล้วกด Ctrl+O, Enter, Ctrl+X แล้ว `sudo systemctl restart deepal-obd`

## การใช้งานประจำวัน

| ทำอะไร | วิธี |
|---|---|
| ดูค่าสดในรถ | เปิด Hotspot ของ iPhone แล้วเข้า `http://deepal-pi.local:8000` (ถ้าไม่ขึ้น ใช้ IP ของ Pi แทน) |
| ดาวน์โหลดประวัติ | อยู่วง WiFi เดียวกับ Pi แล้วเข้า `http://deepal-pi.local:8000/files` |
| ดูกราฟแนวโน้ม | `http://deepal-pi.local:8000/trends` |
| สั่งคำสั่งอื่นเอง (เช่น `ecus`, `discover`) | ssh เข้า Pi แล้ว `sudo systemctl stop deepal-obd` ก่อน (กล่องรับได้ทีละโปรแกรม) จากนั้น `cd /opt/deepal-obd && python3 -m deepal_s05 --port rfcomm://MAC ecus` เสร็จแล้ว `sudo systemctl start deepal-obd` |

## ติดตั้งในรถ

- เสียบที่ชาร์จที่**ไฟดับตามรถ** ไม่อย่างนั้น Pi จะเปิดค้างและกินแบต 12V
  ถ้าช่องไฟไม่ดับตามรถ ให้ใส่ `--shutdown-below 12.2` ใน `AUTOPILOT_OPTIONS`
  Pi จะปิดตัวเองเมื่อแบต 12V ต่ำกว่า 12.2 V นาน 10 นาที
- วางไว้ในที่ร่ม เช่น ใต้เบาะหรือในคอนโซล ไม่ให้โดนแดดตรง
- **นาฬิกา:** Pi ไม่มีถ่านเก็บเวลา ถ้าไม่ได้ต่อเน็ต (Hotspot หรือ WiFi บ้าน) เวลาในไฟล์บันทึกอาจคลาดเคลื่อน
  จะกลับมาถูกต้องเมื่อต่อเน็ตได้
- ไฟถูกตัดทันทีตอนดับรถ: ตั้งค่าให้เขียนการ์ดน้อยที่สุดแล้ว แต่ถ้าการ์ดเสีย
  ไฟล์ประวัติอาจหาย ให้ดาวน์โหลดจากหน้า `/files` เก็บไว้ที่คอมเป็นระยะ

## อัปเดตโปรแกรม

```bash
cd ~/OBD && git pull && bash pi/install.sh && sudo systemctl restart deepal-obd
```

(install.sh ไม่ทับไฟล์ตั้งค่าและข้อมูลที่เก็บไว้)
