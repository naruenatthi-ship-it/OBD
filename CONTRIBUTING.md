# ร่วมพัฒนา / Contributing

ยินดีต้อนรับทุกคนที่ใช้ Deepal S05 (และรุ่นพี่น้องอย่าง SL03, S07, L07) ครับ

## ส่งผลการลองกับรถจริง

แค่รัน `check` แล้วบอกว่าค่าไหนตรง ค่าไหนเพี้ยน (เทียบกับหน้าจอรถ) ก็ช่วยได้มาก
เปิด Issue แบบ **"ผลลองกับรถจริง"** พร้อมบอกรุ่น ความจุแบต ปีรถ และกล่อง OBD ที่ใช้

## ส่งค่าที่แกะได้ใหม่

1. เก็บข้อมูลตาม [REVERSE.md](REVERSE.md)
2. เปิด Issue แบบ **"เจอ DID ใหม่"** แนบไฟล์ `scan_*.csv` / `rec_*.csv` และบอกสถานการณ์ตอนเก็บ
   (รถเย็น/ร้อน, ขับ/ชาร์จ, เปิดแอร์ ฯลฯ) พร้อมรูปหน้าจอรถถ้ามี
3. ถ้ามั่นใจในสูตรแล้ว ส่ง Pull Request เพิ่มลงไฟล์ตัวอย่างใน `examples/` หรือใน `deepal_s05/pids.py`

### ⚠️ ความเป็นส่วนตัว

- `discover` และ `record` ตัดเลข VIN (`F190`) และ serial (`F18C`) ออกจากไฟล์ให้อัตโนมัติ
  ถ้าเคยใช้ `--keep-ids` ให้ลบแถวพวกนั้นเองก่อนแชร์
- **เบลอเลข VIN และทะเบียนรถ**ในรูปหน้าจอก่อนโพสต์
- ห้ามแชร์รหัส ntfy topic หรือรหัส WiFi ในไฟล์ตั้งค่า

## แก้โค้ด

```bash
pip install pyserial pytest ELM327-emulator
python -m pytest
```

- ชุดทดสอบรันกับตัวจำลอง `emulator/` ไม่ต้องมีรถ
- **คำสั่งที่ส่งเข้ารถต้องเป็นแบบอ่านอย่างเดียวเท่านั้น** (ดู `ALLOWED_REQUESTS` ใน `deepal_s05/elm327.py`)
  PR ที่เพิ่มคำสั่งเขียนค่า ลบโค้ด หรือสั่งงาน จะไม่ถูกรับ
- ข้อความที่ผู้ใช้เห็นเป็นภาษาไทย คอมเมนต์ในโค้ดเป็นภาษาอังกฤษ

## English

Bug reports, test results from real cars and newly decoded DIDs are welcome, in English or Thai.
Please strip VINs and serial numbers before sharing scans, and keep every request sent to the car
read-only.
