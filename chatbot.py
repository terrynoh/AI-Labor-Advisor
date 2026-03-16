# -*- coding: utf-8 -*-
import os
import json
import anthropic
from calculators import calculate_severance, calculate_leave, calculate_unpaid_wages

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MAX_TURNS = 20
MAX_INPUT_LENGTH = 1000

SYSTEM_PROMPT = """คุณเป็นผู้เชี่ยวชาญด้านกฎหมายแรงงานไทย ช่วยเหลือแรงงานที่มีปัญหา

หน้าที่ของคุณ:
1. รับฟังสถานการณ์ของผู้ใช้
2. วินิจฉัยทางกฎหมายทันที อย่างชัดเจน ก่อนถามข้อมูลเพิ่มเติม
3. อธิบายสิทธิ์ที่ได้รับตามกฎหมายแรงงานไทย พ.ร.บ.คุ้มครองแรงงาน 2541
4. ถามข้อมูลที่จำเป็นเพื่อคำนวณ (เงินเดือน, อายุงาน) ทีละขั้น
5. แนะนำขั้นตอนการร้องเรียนที่กรมสวัสดิการและคุ้มครองแรงงาน

การวินิจฉัยเบื้องต้นที่ต้องรู้:
- ถูกบังคับลาออก / ถูกข่มขู่ให้ลาออก = ถือเป็นการเลิกจ้าง มีสิทธิ์ค่าชดเชยเต็มจำนวน
- ถูกเลิกจ้างโดยไม่มีความผิด = มีสิทธิ์ค่าชดเชย + อาจฟ้องเลิกจ้างไม่เป็นธรรม
- ค่าจ้างค้างจ่าย = นายจ้างผิดกฎหมาย ต้องจ่ายพร้อมดอกเบี้ย 15% ต่อปี
- ลาออกเอง = ไม่มีค่าชดเชย แต่มีสิทธิ์ค่าจ้างค้างและวันลาที่ยังไม่ได้ใช้

กฎสำคัญ:
- ตอบเป็นภาษาไทยเสมอ
- หากสถานการณ์ชัดเจน ให้วินิจฉัยทันที อย่ารอถามก่อน
- ถามทีละคำถาม ไม่ถามหลายคำถามพร้อมกัน
- ใช้ภาษาเข้าใจง่าย ไม่ใช้ศัพท์กฎหมายซับซ้อน
- แสดงความเห็นอกเห็นใจ ผู้ใช้กำลังเครียดและกังวล
- ท้ายทุกคำตอบให้ใส่: "⚠️ ข้อมูลนี้เป็นคำแนะนำเบื้องต้น ไม่ใช่คำปรึกษาทางกฎหมาย"
"""


def get_initial_message():
    return (
        "สวัสดีครับ 👋 ยินดีต้อนรับสู่ AI Labor Advisor\n\n"
        "ผมช่วยเรื่องสิทธิ์แรงงานไทยได้ครับ เช่น\n"
        "🔴 ถูกเลิกจ้าง\n"
        "💰 ค่าจ้างค้างจ่าย\n"
        "📅 วันลาและค่าชดเชย\n\n"
        "วันนี้มีปัญหาเรื่องอะไรครับ?"
    )


def chat(session_messages, user_input):
    """
    session_messages: list of {"role": "user/assistant", "content": "..."}
    returns: (reply_text, updated_messages, error)
    """
    turn_count = len([m for m in session_messages if m["role"] == "user"])
    if turn_count >= MAX_TURNS:
        return "ขออภัยครับ เซสชันนี้ครบ 20 รอบแล้ว กรุณาเริ่มการสนทนาใหม่", session_messages, None

    if len(user_input) > MAX_INPUT_LENGTH:
        return f"กรุณาพิมพ์ข้อความไม่เกิน {MAX_INPUT_LENGTH} ตัวอักษรครับ", session_messages, None

    updated = session_messages + [{"role": "user", "content": user_input}]

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=updated
        )
        reply = response.content[0].text
        updated.append({"role": "assistant", "content": reply})
        return reply, updated, None

    except Exception as e:
        return None, session_messages, str(e)


# ─────────────────────────────────────────────
#  NEW: Form-based analysis (Step 1 → Step 2)
# ─────────────────────────────────────────────

ISSUE_LABELS = {
    "wrongful_termination": "ถูกเลิกจ้างโดยไม่มีความผิด",
    "forced_resignation":   "ถูกบังคับให้ลาออก",
    "wage_arrears":         "ค่าจ้างค้างจ่าย",
    "no_overtime_pay":      "ไม่ได้รับค่าล่วงเวลา",
    "unused_leave":         "วันลาที่ยังไม่ได้ใช้",
    "unfair_dismissal":     "เลิกจ้างไม่เป็นธรรม",
}

ANALYSIS_PROMPT = """คุณเป็นผู้เชี่ยวชาญกฎหมายแรงงานไทย วิเคราะห์สถานการณ์และตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น

ข้อมูลผู้ใช้:
{user_info}

ตอบรูปแบบนี้เท่านั้น (JSON ล้วนๆ):
{{
  "diagnosis": "สรุปสถานการณ์ 2-3 ประโยค ภาษาไทย",
  "rights": ["สิทธิ์ที่ 1 (ภาษาไทย)", "สิทธิ์ที่ 2", "..."],
  "can_claim": ["สิ่งที่เรียกร้องได้พร้อมอธิบายสั้นๆ", "..."],
  "legal_basis": "กฎหมายที่อ้างอิง เช่น พ.ร.บ.คุ้มครองแรงงาน 2541 มาตรา ...",
  "urgency": "high หรือ medium หรือ low",
  "recommend_petition": true หรือ false,
  "warning": "ข้อควรระวังสำคัญ ถ้ามี หรือ null"
}}"""


def analyze_situation(form_data: dict) -> dict:
    """
    Analyze form-submitted data and return structured result.

    form_data keys:
        name, age, work_years (float), monthly_salary (float),
        unused_leave_days (int), total_leave_days (int),
        employment_type, company_size,
        issues (list of issue keys from ISSUE_LABELS)

    returns: dict with analysis + pre-calculated compensation amounts
    """
    salary      = float(form_data.get("monthly_salary", 0) or 0)
    years       = float(form_data.get("work_years", 0) or 0)
    unused_days = int(form_data.get("unused_leave_days", 0) or 0)
    issues      = form_data.get("issues", [])

    # ── Pre-calculate compensation ──────────────────────────────
    severance_amount, severance_detail = (0, "")
    if issues and any(i in issues for i in ("wrongful_termination", "forced_resignation", "unfair_dismissal")):
        severance_amount, severance_detail = calculate_severance(salary, years)

    leave_payout = calculate_leave(salary, unused_days) if unused_days > 0 else 0

    # ── Build context for Claude ────────────────────────────────
    issue_text = ", ".join(ISSUE_LABELS.get(i, i) for i in issues) if issues else "ไม่ระบุ"
    user_info = (
        f"รูปแบบการจ้างงาน: {form_data.get('employment_type', 'ไม่ระบุ')}\n"
        f"ขนาดบริษัท: {form_data.get('company_size', 'ไม่ระบุ')}\n"
        f"อายุงาน: {years} ปี\n"
        f"เงินเดือน: {salary:,.0f} บาท/เดือน\n"
        f"วันลาคงเหลือ: {unused_days} วัน (จากทั้งหมด {form_data.get('total_leave_days', '?')} วัน)\n"
        f"ปัญหาที่พบ: {issue_text}"
    )

    prompt = ANALYSIS_PROMPT.format(user_info=user_info)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        analysis = json.loads(text)
    except Exception as e:
        print(f"[analyze_situation ERROR] {e}")
        analysis = {
            "diagnosis": "ไม่สามารถวิเคราะห์ได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง",
            "rights": [],
            "can_claim": [],
            "legal_basis": "",
            "urgency": "medium",
            "recommend_petition": True,
            "warning": None,
        }

    # Attach calculated amounts
    analysis["severance_amount"] = severance_amount
    analysis["severance_detail"] = severance_detail
    analysis["leave_payout"]     = leave_payout

    return analysis
