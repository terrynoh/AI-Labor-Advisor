# -*- coding: utf-8 -*-
import os
import json
import logging
import anthropic
from calculators import calculate_severance, calculate_leave, calculate_unpaid_wages

logger = logging.getLogger(__name__)
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

    except anthropic.APIStatusError as e:
        logger.error("Claude API 오류 (status %s): %s", e.status_code, e.message)
        return None, session_messages, "AI 서비스 일시 오류입니다. 잠시 후 다시 시도해주세요."
    except anthropic.APIConnectionError as e:
        logger.error("Claude API 연결 오류: %s", e)
        return None, session_messages, "네트워크 연결 오류입니다. 인터넷 연결을 확인해주세요."
    except Exception as e:
        logger.error("chat 예상치 못한 오류: %s", e, exc_info=True)
        return None, session_messages, "시스템 오류가 발생했습니다. 잠시 후 다시 시도해주세요."


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
}}

กฎสำคัญ:
- ห้ามคำนวณค่าชดเชยเอง ให้ใช้ตัวเลขจาก [ค่าชดเชยที่คำนวณแล้ว] เท่านั้น
- ใน can_claim ให้คัดลอกตัวเลขจาก [ค่าชดเชยที่คำนวณแล้ว] โดยตรง"""

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
        f"วันลาคงเหลือ: {unused_days} วัน\n"
        f"ปัญหาที่พบ: {issue_text}\n"
        f"\n[ค่าชดเชยที่คำนวณแล้ว — ใช้ตัวเลขนี้เท่านั้น ห้ามคำนวณเอง]\n"
        f"ค่าชดเชยการเลิกจ้าง: {severance_amount:,.0f} บาท ({severance_detail})\n"
        f"ค่าวันลาที่ยังไม่ได้ใช้: {leave_payout:,.0f} บาท\n"
    )

    prompt = ANALYSIS_PROMPT.format(user_info=user_info)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        analysis = json.loads(text)
    except Exception as e:
        logger.error("analyze_situation 오류: %s", e, exc_info=True)
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


DEMAND_LETTER_PROMPT = """คุณเป็นทนายความผู้เชี่ยวชาญกฎหมายแรงงานไทย
เขียนเนื้อหาหนังสือบอกกล่าวทวงถามอย่างเป็นทางการ เป็นภาษาไทย

ข้อมูลคดี:
{case_info}

กฎการเขียน:
- ใช้ภาษาทางการ กระชับ ชัดเจน
- อ้างกฎหมายที่เกี่ยวข้องพร้อมมาตราให้ชัดเจน
- ระบุจำนวนเงินที่เรียกร้องแต่ละรายการ
- ไม่ต้องมี opening/closing paragraph (มีแล้วในระบบ)
- ตอบเฉพาะเนื้อหาตรงกลาง 3-5 ย่อหน้า ไม่มีหัวข้อ
- ห้ามใช้ bullet points หรือ numbering
- เขียนต่อเนื่องเป็น paragraph"""


def generate_demand_letter_body(case_data: dict) -> str:
    """
    Use Claude to generate the body text of the demand letter.

    case_data keys:
        complainant_name, employer_name,
        start_date, end_date, position, wage_rate,
        issues (list),
        severance_amount (float),
        leave_payout (float),
        wage_owed (float),
        ot_amount (float),
        notice_pay (float),
        total_amount (float)

    returns: Thai body text (str)
    """
    issues_th = ", ".join(ISSUE_LABELS.get(i, i) for i in case_data.get("issues", []))

    # Build itemized claim list
    claims = []
    if case_data.get("severance_amount"):
        claims.append(f"ค่าชดเชยการเลิกจ้าง {case_data['severance_amount']:,.2f} บาท")
    if case_data.get("notice_pay"):
        claims.append(f"ค่าจ้างแทนการบอกกล่าวล่วงหน้า {case_data['notice_pay']:,.2f} บาท")
    if case_data.get("wage_owed"):
        claims.append(f"ค่าจ้างค้างชำระ {case_data['wage_owed']:,.2f} บาท")
    if case_data.get("leave_payout"):
        claims.append(f"ค่าวันลาที่ยังไม่ได้ใช้ {case_data['leave_payout']:,.2f} บาท")
    if case_data.get("ot_amount"):
        claims.append(f"ค่าล่วงเวลา {case_data['ot_amount']:,.2f} บาท")

    claims_text = "\n".join(f"- {c}" for c in claims) if claims else "- ตามที่ระบุในคดี"
    total = case_data.get("total_amount", 0)

    case_info = (
        f"ชื่อลูกจ้าง: {case_data.get('complainant_name', '...')}\n"
        f"ชื่อนายจ้าง: {case_data.get('employer_name', '...')}\n"
        f"ตำแหน่ง: {case_data.get('position', '...')}\n"
        f"อัตราค่าจ้าง: {case_data.get('wage_rate', '...')} บาท/เดือน\n"
        f"ระยะเวลาทำงาน: {case_data.get('start_date', '...')} - {case_data.get('end_date', '...')}\n"
        f"ปัญหาที่พบ: {issues_th}\n"
        f"รายการเรียกร้อง:\n{claims_text}\n"
        f"รวมทั้งสิ้น: {total:,.2f} บาท"
    )

    prompt = DEMAND_LETTER_PROMPT.format(case_info=case_info)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error("generate_demand_letter_body 오류: %s", e, exc_info=True)
        # Fallback template
        return (
            f"ข้าพเจ้าขอแจ้งให้ทราบว่า {case_data.get('employer_name', 'นายจ้าง')} "
            f"ได้กระทำการอันเป็นการละเมิดสิทธิ์ของข้าพเจ้า ได้แก่ {issues_th} "
            f"ซึ่งเป็นการฝ่าฝืนพระราชบัญญัติคุ้มครองแรงงาน พ.ศ. 2541\n\n"
            f"ข้าพเจ้าขอเรียกร้องให้ชำระเงินรวมทั้งสิ้น {total:,.2f} บาท "
            f"ตามรายการที่ระบุด้านล่าง"
        )
