# -*- coding: utf-8 -*-
import os
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

ตัวอย่างการตอบที่ถูกต้อง:
กรณีถูกบังคับลาออก:
"สถานการณ์ของคุณชัดเจนมากครับ — การที่นายจ้างข่มขู่และบังคับให้เขียนใบลาออก 
ถือเป็น 'การเลิกจ้าง' ตามกฎหมาย ไม่ใช่การลาออกโดยสมัครใจ
คุณมีสิทธิ์ได้รับ:
✅ ค่าชดเชยตามอายุงาน
✅ อาจฟ้องเลิกจ้างไม่เป็นธรรม เรียกค่าเสียหายเพิ่มได้
เพื่อคำนวณค่าชดเชยที่คุณได้รับ ขอถามว่าคุณทำงานที่นี่มานานเท่าไหร่ครับ?"
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
    session_messages: [{"role": "user/assistant", "content": "..."}]
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
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=updated
        )
        reply = response.content[0].text
        updated.append({"role": "assistant", "content": reply})
        return reply, updated, None

    except Exception as e:
        return None, session_messages, str(e)