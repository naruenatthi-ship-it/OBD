"""Diagnostic trouble codes read with UDS ReadDTCInformation (19 02)."""

# Status byte bits (ISO 14229-1)
TEST_FAILED = 0x01
PENDING = 0x04
CONFIRMED = 0x08
FAILED_SINCE_CLEAR = 0x20
WARNING_LAMP = 0x80

STATUS_TEXT = [
    (TEST_FAILED, "กำลังเกิดอยู่"),
    (PENDING, "รอยืนยัน"),
    (CONFIRMED, "ยืนยันแล้ว"),
    (FAILED_SINCE_CLEAR, "เคยเกิดหลังลบโค้ดครั้งล่าสุด"),
    (WARNING_LAMP, "สั่งไฟเตือน"),
]

# A few SAE J2012 generic codes. Manufacturer-specific codes (P1xxx,
# P30xx-P33xx, B1/B2, C1/C2, U1/U2) have no public meaning.
GENERIC = {
    "P0562": "แรงดันระบบ 12V ต่ำ",
    "P0563": "แรงดันระบบ 12V สูง",
    "P0A80": "ให้เปลี่ยนแบตเตอรี่ไฮบริด/EV",
    "P0AA6": "ฉนวนระบบไฟแรงสูงรั่ว (isolation fault)",
    "U0001": "บัส CAN ความเร็วสูงมีปัญหา",
    "U0100": "ขาดการสื่อสารกับกล่อง ECM/PCM",
    "U0101": "ขาดการสื่อสารกับกล่องเกียร์ (TCM)",
    "U0111": "ขาดการสื่อสารกับกล่องควบคุมแบตเตอรี่ (BECM)",
    "U0121": "ขาดการสื่อสารกับกล่อง ABS",
    "U0140": "ขาดการสื่อสารกับกล่อง BCM",
    "U0155": "ขาดการสื่อสารกับหน้าปัด",
    "U0293": "ขาดการสื่อสารกับกล่องควบคุมระบบขับเคลื่อนไฮบริด/EV",
}

MANUFACTURER_SPECIFIC = "โค้ดเฉพาะผู้ผลิต"

ACTIVE_MASK = TEST_FAILED | PENDING | CONFIRMED | FAILED_SINCE_CLEAR


def format_dtc(b1, b2, b3=None):
    """Three DTC bytes to text, e.g. (0x05, 0x62, 0x16) -> 'P0562-16'."""
    code = "%s%d%X%X%X" % ("PCBU"[b1 >> 6], (b1 >> 4) & 3, b1 & 0x0F,
                           b2 >> 4, b2 & 0x0F)
    return code if b3 is None else "%s-%02X" % (code, b3)


def is_generic(code):
    """SAE J2012 split between generic and manufacturer-specific codes."""
    system, digit = code[0], code[1]
    if system == "P":
        return digit in "02" or (digit == "3" and code[2] in "456789")
    return digit in "03"


def describe(code):
    base = code[:5]
    if base in GENERIC:
        return GENERIC[base]
    if not is_generic(base):
        return MANUFACTURER_SPECIFIC
    return ""


def status_text(status):
    return ", ".join(t for bit, t in STATUS_TEXT if status & bit) or \
        "ไม่มีสถานะที่ใช้งาน (0x%02X)" % status


def parse_response(msg):
    """59 02 <availability mask> (DTC_hi DTC_mid DTC_lo status)* ->
    list of dicts with code, status, status_text, description."""
    if len(msg) < 3 or msg[0] != 0x59 or msg[1] != 0x02:
        raise ValueError("not a 59 02 response: %s" % msg.hex())
    records = []
    body = msg[3:]
    for i in range(0, len(body) - 3, 4):
        b1, b2, b3, status = body[i:i + 4]
        code = format_dtc(b1, b2, b3)
        records.append({"code": code, "status": status,
                        "status_text": status_text(status),
                        "description": describe(code),
                        "active": bool(status & ACTIVE_MASK)})
    return records
