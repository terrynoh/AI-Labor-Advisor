# -*- coding: utf-8 -*-
import os
import anthropic
from calculators import calculate_severance, calculate_leave, calculate_unpaid_wages

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MAX_TURNS = 20
MAX_INPUT_LENGTH = 500

SYSTEM_PROMPT = """คุณเป็นผู้เชี่ยวชาญด้านกฎหมายแรงงานไทย ช่วยเหลือแรงงานที่มีปัญหา

หน้าที่ของคุณ:
1. ถามสถานการณ์ของผู้ใช้ก่อน (ถูกเลิกจ้าง / ลาออกเอง / ค่าจ้างค้างจ่าย / อื่นๆ)
2. ถามข้อมูลที่จำเป็น (เงินเดือน, อายุงาน ฯลฯ) ทีละขั้น
3. อธิบายสิทธิ์ตามกฎหมายแรงงานไทย พ.ร.บ.คุ้มครองแรงงาน 2541
4. แนะนำขั้นตอนการร้องเรียนที่กรมสวัสดิการและคุ้มครองแรงงาน

กฎ:
- ตอบเป็นภาษาไทยเสมอ
- ถามทีละคำถาม ไม่ถามหลายคำถามพร้อมกัน
- ใช้ภาษาเข้าใจง่าย ไม่ใช้ศัพท์กฎหมายซับซ้อน
- ท้ายทุกคำตอบให้ใส่: "⚠️ ข้อมูลนี้เป็นคำแนะนำเบื้องต้น ไม่ใช่คำปรึกษาทางกฎหมาย"

เริ่มต้นด้วยการทักทายและถามสถานการณ์ของผู้ใช้"""


def get_initial_message():
    """첫 인사 메시지"""
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
    # 턴 제한
    turn_count = len([m for m in session_messages if m["role"] == "user"])
    if turn_count >= MAX_TURNS:
        return "ขออภัยครับ เซสชันนี้ครบ 20 รอบแล้ว กรุณาเริ่มการสนทนาใหม่", session_messages, None

    # 입력 길이 제한
    if len(user_input) > MAX_INPUT_LENGTH:
        return f"กรุณาพิมพ์ข้อความไม่เกิน {MAX_INPUT_LENGTH} ตัวอักษรครับ", session_messages, None

    # 메시지 추가
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