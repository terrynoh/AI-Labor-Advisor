# -*- coding: utf-8 -*-
"""
pdf_generator.py
ระบบสร้าง PDF แบบฟอร์ม คร.7 (คำร้องต่อพนักงานตรวจแรงงาน)
โดยการเติมข้อมูลลงใน .docx template แล้ว convert เป็น PDF ด้วย LibreOffice
"""

import os
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime
from docx import Document

# ── LibreOffice command path ────────────────────────────────────────────────
if platform.system() == "Windows":
    LIBREOFFICE_CMD = r"C:\Program Files\LibreOffice\program\soffice.exe"
else:
    LIBREOFFICE_CMD = "libreoffice"

# ── ที่อยู่ template (วางไว้ในโฟลเดอร์เดียวกับไฟล์นี้) ──────────────────
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "template_kor7.docx")

# ── ตัวเลขไทย ──────────────────────────────────────────────────────────────
THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

def _to_thai_year(year: int) -> int:
    """Convert CE year to Buddhist Era (BE)"""
    return year + 543

def _today_parts():
    now = datetime.now()
    return str(now.day), THAI_MONTHS[now.month], str(_to_thai_year(now.year))

def _baht_text(amount) -> str:
    """Simple baht-to-words (returns number string for now — extend as needed)"""
    try:
        n = float(amount)
        return f"{n:,.2f} บาท"
    except:
        return str(amount)

def _fill(run, value):
    """Replace a dotted-line run with an actual value, preserving formatting."""
    if value:
        run.text = f" {str(value)} "
    else:
        run.text = "................................"


def generate_kor7_pdf(data: dict, output_path: str) -> str:
    """
    Fill คร.7 template with data and save as PDF.

    data keys (all optional — missing fields keep blank lines):
        complainant_name, id_number, id_issued_at, id_issue_date, id_expiry,
        nationality, work_permit, age,
        address_no, moo, street, subdistrict, district, province,
        postal_code, phone,
        reg_address_no, reg_moo, reg_street, reg_subdistrict, reg_district,
        reg_province, reg_postal_code, reg_phone,
        employer_name, employer_person, business_type,
        employer_address_no, employer_moo, employer_street,
        employer_subdistrict, employer_district, employer_province,
        employer_postal, employer_phone, employer_landmark,
        workplace_address_no, workplace_moo, workplace_street,
        workplace_subdistrict, workplace_district, workplace_province,
        workplace_postal, workplace_phone, workplace_landmark,
        start_date, end_date, position, department, supervisor, wage_rate,
        work_days_per_week, work_hours_per_day, work_start_time, work_end_time,
        break_start_time, break_end_time,
        pay_schedule_wage, pay_schedule_overtime,
        pay_schedule_holiday, pay_schedule_holiday_ot,
        reason,
        wage_from_date, wage_to_date, wage_owed,
        minimum_wage,
        notice_pay,
        ot_from_date, ot_to_date, ot_amount,
        holiday_from_date, holiday_to_date, holiday_amount,
        holiday_ot_from_date, holiday_ot_to_date, holiday_ot_amount,
        severance_from_date, severance_to_date, severance,
        filed_province,
    """

    # ── Load template ──────────────────────────────────────────────────────
    doc = Document(TEMPLATE_PATH)
    paras = doc.paragraphs
    d = data  # shorthand

    # Helper: fill a specific para/run pair
    def f(para_idx, run_idx, value):
        try:
            _fill(paras[para_idx].runs[run_idx], value)
        except (IndexError, AttributeError):
            pass

    # ── DATE / LOCATION ────────────────────────────────────────────────────
    day, month, year = _today_parts()
    f(8,  2, d.get("filed_province", "กรุงเทพมหานคร"))   # เขียนที่
    f(9,  1, day)                                          # วันที่
    f(9,  3, month)                                        # เดือน
    f(9,  7, year)                                         # พ.ศ.

    # ── SECTION 1: ผู้ร้อง ────────────────────────────────────────────────
    f(10, 7, d.get("complainant_name"))        # ชื่อ-นามสกุล
    f(11, 1, d.get("id_number"))               # หมายเลขบัตรประชาชน
    f(12, 1, d.get("id_issued_at"))            # ออกให้ ณ
    f(12, 3, d.get("id_issue_date"))           # วันออกบัตร
    f(12, 5, d.get("id_expiry"))               # วันหมดอายุ
    f(13, 1, d.get("nationality", "ไทย"))      # สัญชาติ
    f(13, 5, d.get("work_permit"))             # ใบอนุญาตทำงาน
    f(14, 1, d.get("age"))                     # อายุ
    f(14, 3, d.get("address_no"))              # บ้านเลขที่ (ปัจจุบัน)
    f(14, 5, d.get("moo"))                     # หมู่ที่
    f(14, 7, d.get("street"))                  # ถนน
    f(15, 3, d.get("subdistrict"))             # ตำบล/แขวง
    f(15, 7, d.get("district"))                # อำเภอ/เขต
    f(16, 1, d.get("province"))                # จังหวัด
    f(16, 3, d.get("postal_code"))             # รหัสไปรษณีย์
    f(16, 5, d.get("phone"))                   # โทรศัพท์

    # ทะเบียนบ้าน (ถ้าไม่ระบุ ใช้ที่อยู่ปัจจุบัน)
    f(17, 1, d.get("reg_address_no", d.get("address_no")))
    f(17, 3, d.get("reg_moo",        d.get("moo")))
    f(17, 5, d.get("reg_street",     d.get("street")))
    f(18, 3, d.get("reg_subdistrict",d.get("subdistrict")))
    f(18, 7, d.get("reg_district",   d.get("district")))
    f(19, 1, d.get("reg_province",   d.get("province")))
    f(19, 3, d.get("reg_postal_code",d.get("postal_code")))
    f(19, 5, d.get("reg_phone",      d.get("phone")))

    # ── SECTION 2: นายจ้าง ───────────────────────────────────────────────
    f(27, 4, d.get("employer_name"))           # ชื่อสถานประกอบกิจการ
    f(28, 7, d.get("employer_person"))         # เจ้าของ/ผู้จัดการ
    f(28, 9, d.get("business_type"))           # ประกอบกิจการ
    f(29, 1, d.get("employer_address_no"))     # เลขที่สำนักงาน
    f(29, 3, d.get("employer_moo"))            # หมู่ที่
    f(29, 5, d.get("employer_street"))         # ถนน
    f(29, 9, d.get("employer_subdistrict"))    # ตำบล/แขวง
    f(30, 3, d.get("employer_district"))       # อำเภอ/เขต
    f(30, 5, d.get("employer_province"))       # จังหวัด
    f(30, 7, d.get("employer_postal"))         # รหัสไปรษณีย์
    f(31, 1, d.get("employer_phone"))          # โทรศัพท์
    f(31, 3, d.get("employer_landmark"))       # ใกล้เคียงกับ

    # สถานที่ทำงาน (ถ้าไม่ระบุ ใช้ที่อยู่นายจ้าง)
    f(36, 1, d.get("workplace_address_no",  d.get("employer_address_no")))
    f(36, 3, d.get("workplace_moo",         d.get("employer_moo")))
    f(36, 5, d.get("workplace_street",      d.get("employer_street")))
    f(36, 9, d.get("workplace_subdistrict", d.get("employer_subdistrict")))
    f(37, 3, d.get("workplace_district",    d.get("employer_district")))
    f(37, 5, d.get("workplace_province",    d.get("employer_province")))
    f(37, 7, d.get("workplace_postal",      d.get("employer_postal")))
    f(38, 1, d.get("workplace_phone",       d.get("employer_phone")))
    f(38, 3, d.get("workplace_landmark",    d.get("employer_landmark")))

    # ── SECTION 3: ระยะเวลาทำงาน ─────────────────────────────────────────
    f(39, 4, d.get("start_date"))              # วันที่เริ่มทำงาน
    f(39, 6, d.get("end_date"))                # วันสิ้นสุด
    f(40, 1, d.get("position"))                # หน้าที่
    f(40, 5, d.get("department"))              # ฝ่าย/แผนก
    f(41, 5, d.get("supervisor"))              # หัวหน้างาน
    f(41, 7, d.get("wage_rate"))               # อัตราค่าจ้าง

    # ── SECTION 4: เวลาทำงาน ─────────────────────────────────────────────
    f(44, 4, d.get("work_days_per_week"))      # สัปดาห์ละ ... วัน
    f(44, 6, d.get("work_hours_per_day"))      # วันละ ... ชั่วโมง
    f(44, 8, d.get("work_start_time"))         # เริ่ม เวลา
    f(45, 1, d.get("work_end_time"))           # ถึงเวลา
    f(45, 5, d.get("break_start_time"))        # พักตั้งแต่
    f(45, 9, d.get("break_end_time"))          # ถึงเวลา

    # ── SECTION 5: กำหนดจ่าย ─────────────────────────────────────────────
    f(47, 6, d.get("pay_schedule_wage"))
    f(48, 6, d.get("pay_schedule_overtime"))
    f(49, 6, d.get("pay_schedule_holiday"))
    f(50, 6, d.get("pay_schedule_holiday_ot"))

    # ── SECTION 6: สาเหตุ ────────────────────────────────────────────────
    reason = d.get("reason", "")
    if reason:
        # Split across the two continuation lines
        mid = len(reason) // 2
        f(51, 4, reason[:mid])
        f(52, 0, reason[mid:])
    
    # ── SECTION 7: การเรียกร้อง ──────────────────────────────────────────
    # 7.1 ค่าจ้าง
    wage_owed = d.get("wage_owed")
    if wage_owed:
        f(54, 4, d.get("wage_from_date", d.get("start_date")))
        f(54, 6, d.get("wage_to_date",   d.get("end_date")))
        f(55, 1, f"{float(wage_owed):,.2f}" if wage_owed else "")
        f(55, 3, _baht_text(wage_owed))

    # 7.2 ค่าจ้างขั้นต่ำ
    min_wage = d.get("minimum_wage")
    if min_wage:
        f(56, 4, f"{float(min_wage):,.2f}")

    # 7.4 ค่าจ้างแทนการบอกกล่าวล่วงหน้า (notice pay)
    notice = d.get("notice_pay")
    if notice:
        f(58, 4, f"{float(notice):,.2f}")
        f(59, 0, _baht_text(notice))

    # 7.5 ค่าล่วงเวลา
    ot = d.get("ot_amount")
    if ot:
        f(60, 4, d.get("ot_from_date", d.get("start_date")))
        f(60, 6, d.get("ot_to_date",   d.get("end_date")))
        f(61, 1, f"{float(ot):,.2f}")
        f(61, 3, _baht_text(ot))

    # 7.6 ค่าทำงานในวันหยุด
    hol = d.get("holiday_amount")
    if hol:
        f(62, 5, d.get("holiday_from_date", d.get("start_date")))
        f(62, 7, d.get("holiday_to_date",   d.get("end_date")))
        f(63, 1, f"{float(hol):,.2f}")
        f(63, 3, _baht_text(hol))

    # 7.7 ค่าล่วงเวลาในวันหยุด
    hol_ot = d.get("holiday_ot_amount")
    if hol_ot:
        f(64, 3, d.get("holiday_ot_from_date", d.get("start_date")))
        f(64, 5, d.get("holiday_ot_to_date",   d.get("end_date")))
        f(65, 1, f"{float(hol_ot):,.2f}")
        f(65, 3, _baht_text(hol_ot))

    # 7.8 ค่าชดเชยการเลิกจ้าง
    sev = d.get("severance")
    if sev:
        f(67, 4, d.get("severance_from_date", d.get("start_date")))
        f(68, 1, d.get("severance_to_date",   d.get("end_date")))
        f(68, 3, f"{float(sev):,.2f}" if sev else "")
        f(68, 5, _baht_text(sev))

    # ── SECTION 8: สถานที่จ่าย ────────────────────────────────────────────
    f(85, 3, d.get("filed_province", "กรุงเทพมหานคร"))

    # ── ลายเซ็น ───────────────────────────────────────────────────────────
    f(91, 2, d.get("complainant_name"))        # ลงชื่อ
    # Para[92] contains name in parentheses — rebuild it
    try:
        paras[92].runs[0].text = f'\t\t\t\t\t\t\t         ({d.get("complainant_name", "")})'
    except (IndexError, AttributeError):
        pass

    # ── Save filled .docx to temp ──────────────────────────────────────────
    tmp_dir  = tempfile.mkdtemp()
    tmp_docx = os.path.join(tmp_dir, "filled_kor7.docx")
    doc.save(tmp_docx)

    # ── Convert to PDF via LibreOffice ─────────────────────────────────────
    try:
        result = subprocess.run(
            [LIBREOFFICE_CMD, "--headless", "--convert-to", "pdf",
             "--outdir", tmp_dir, tmp_docx],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice error: {result.stderr}")
    except FileNotFoundError:
        raise RuntimeError(
            f"LibreOffice not found at: {LIBREOFFICE_CMD}"
        )

    tmp_pdf = os.path.join(tmp_dir, "filled_kor7.pdf")
    if not os.path.exists(tmp_pdf):
        raise RuntimeError("PDF conversion failed — output file not found")

    shutil.move(tmp_pdf, output_path)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return output_path
