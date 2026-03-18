# -*- coding: utf-8 -*-
"""
line_bot.py
LINE Messaging API Webhook Handler
ได้สิทธิ์ — AI Labor Advisor LINE OA
"""
def handle_message(body):
    data = json.loads(body)

    for event in data.get("events", []):
        if event.get("type") != "message":
            continue

        if event["message"].get("type") != "text":
            continue

        user_id = event["source"]["userId"]
        reply_token = event["replyToken"]
        user_text = event["message"]["text"]

        print("📩 user:", user_text)

        # 👉 기존 로직 실행
        process_message(user_id, reply_token, user_text)

import os
import hashlib
import hmac
import base64
import json
import requests

LINE_CHANNEL_SECRET      = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_API_URL             = "https://api.line.me/v2/bot/message/reply"

# ── Session store (in-memory, resets on redeploy) ──────────────────────────
# { user_id: { "step": str, "data": dict, "messages": list } }
sessions = {}

# ── Steps ──────────────────────────────────────────────────────────────────
STEP_START      = "start"
STEP_ASK_NAME   = "ask_name"
STEP_ASK_AGE    = "ask_age"
STEP_ASK_SALARY = "ask_salary"
STEP_ASK_YEARS  = "ask_years"
STEP_ASK_LEAVE  = "ask_leave"
STEP_ASK_ISSUES = "ask_issues"
STEP_ANALYZING  = "analyzing"
STEP_RESULT     = "result"
STEP_DONE       = "done"


def verify_signature(body: bytes, signature: str) -> bool:
    """Verify LINE webhook signature."""
    hash_val = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    return base64.b64encode(hash_val).decode("utf-8") == signature


def reply(reply_token: str, messages: list):
    """Send reply messages to LINE."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "replyToken": reply_token,
        "messages": messages
    }
    try:
        res = requests.post(LINE_API_URL, headers=headers, json=payload, timeout=10)
        print("📡 LINE 응답:", res.status_code, res.text)
    except Exception as e:
        print(f"[LINE reply ERROR] {e}")


def text_msg(text: str) -> dict:
    return {"type": "text", "text": text}


def quick_reply_msg(text: str, items: list) -> dict:
    """Text message with quick reply buttons."""
    return {
        "type": "text",
        "text": text,
        "quickReply": {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "message",
                        "label": item["label"],
                        "text": item["text"]
                    }
                }
                for item in items
            ]
        }
    }


def flex_result_msg(analysis: dict, severance: float, leave: float) -> dict:
    """Flex message for analysis result."""
    total = severance + leave
    issue_labels = {
        "wrongful_termination": "ถูกเลิกจ้างโดยไม่มีความผิด",
        "forced_resignation":   "ถูกบังคับให้ลาออก",
        "wage_arrears":         "ค่าจ้างค้างจ่าย",
        "no_overtime_pay":      "ไม่ได้รับค่าล่วงเวลา",
        "unused_leave":         "วันลาที่ยังไม่ได้ใช้",
        "unfair_dismissal":     "เลิกจ้างไม่เป็นธรรม",
    }

    urgency_map = {
        "high":   "🔴 เร่งด่วนสูง",
        "medium": "🟡 ปานกลาง",
        "low":    "🟢 ไม่เร่งด่วน",
    }
    urgency_text = urgency_map.get(analysis.get("urgency", "medium"), "🟡 ปานกลาง")

    return {
        "type": "flex",
        "altText": f"ผลการวิเคราะห์สิทธิ์ — ฿{total:,.0f}",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#0A5C36",
                "contents": [
                    {"type": "text", "text": "ได้สิทธิ์ — ผลการวิเคราะห์", "color": "#ffffff", "size": "md", "weight": "bold"},
                    {"type": "text", "text": urgency_text, "color": "#9FE1CB", "size": "sm", "margin": "sm"}
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#f0f5f2",
                        "cornerRadius": "10px",
                        "paddingAll": "16px",
                        "contents": [
                            {"type": "text", "text": "💰 ขั้นต่ำที่มีสิทธิ์เรียกร้อง", "size": "sm", "color": "#0A5C36"},
                            {"type": "text", "text": f"฿{total:,.0f}", "size": "xxl", "weight": "bold", "color": "#0A5C36"},
                            {"type": "text", "text": "ตาม พ.ร.บ.คุ้มครองแรงงาน 2541", "size": "xs", "color": "#6b7280", "margin": "sm"}
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "contents": [
                            *(
                                [{"type": "box", "layout": "horizontal", "contents": [
                                    {"type": "text", "text": "✅ ค่าชดเชย", "size": "sm", "flex": 3},
                                    {"type": "text", "text": f"฿{severance:,.0f}", "size": "sm", "weight": "bold", "color": "#0A5C36", "flex": 2, "align": "end"}
                                ]}] if severance > 0 else []
                            ),
                            *(
                                [{"type": "box", "layout": "horizontal", "contents": [
                                    {"type": "text", "text": "✅ วันลาคงเหลือ", "size": "sm", "flex": 3},
                                    {"type": "text", "text": f"฿{leave:,.0f}", "size": "sm", "weight": "bold", "color": "#0A5C36", "flex": 2, "align": "end"}
                                ]}] if leave > 0 else []
                            ),
                        ]
                    },
                    {
                        "type": "text",
                        "text": analysis.get("diagnosis", "")[:100] + "...",
                        "size": "sm",
                        "color": "#374151",
                        "wrap": True
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#0f2d4a",
                        "action": {
                            "type": "uri",
                            "label": "📦 รับแพ็กเกจเอกสาร",
                            "uri": "https://ai-labor-advisor.onrender.com"
                        }
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "action": {
                            "type": "message",
                            "label": "🔄 เริ่มใหม่",
                            "text": "เริ่มใหม่"
                        }
                    }
                ]
            }
        }
    }


# ── Main handler ───────────────────────────────────────────────────────────

def process_message(user_id: str, reply_token: str, user_text: str):
    """Process incoming message and reply."""
    from chatbot import analyze_situation
    from calculators import calculate_severance, calculate_leave

    text = user_text.strip()
    session = sessions.get(user_id, {"step": STEP_START, "data": {}})
    step = session["step"]
    data = session["data"]

    # ── 리셋 키워드 ──────────────────────────────────────────────────────
    if text.lower() in ["เริ่มใหม่", "reset", "start", "สวัสดี", "หวัดดี", "ใหม่"]:
        sessions[user_id] = {"step": STEP_START, "data": {}}
        step = STEP_START

    # ── Step machine ─────────────────────────────────────────────────────
    if step == STEP_START:
        sessions[user_id] = {"step": STEP_ASK_NAME, "data": {}}
        reply(reply_token, [text_msg(
            "สวัสดีครับ! 🐘 ผมน้องช้าง ผู้ช่วยด้านสิทธิ์แรงงานไทย\n\n"
            "ผมจะช่วยคำนวณค่าชดเชยและสิทธิ์ที่คุณควรได้รับตามกฎหมายครับ\n\n"
            "เริ่มเลย — ชื่อของคุณคืออะไรครับ? 😊"
        )])

    elif step == STEP_ASK_NAME:
        data["name"] = text
        sessions[user_id] = {"step": STEP_ASK_AGE, "data": data}
        reply(reply_token, [text_msg(f"ยินดีที่รู้จักครับคุณ{text}! 😊\n\nอายุเท่าไหร่ครับ? (กรอกแค่ตัวเลข)")])

    elif step == STEP_ASK_AGE:
        try:
            age = int(text)
            if not 15 <= age <= 80:
                raise ValueError
            data["age"] = age
            sessions[user_id] = {"step": STEP_ASK_SALARY, "data": data}
            reply(reply_token, [text_msg("เงินเดือนต่อเดือนเท่าไหร่ครับ? (กรอกแค่ตัวเลข เช่น 20000)")])
        except ValueError:
            reply(reply_token, [text_msg("กรุณากรอกอายุเป็นตัวเลขครับ เช่น 32")])

    elif step == STEP_ASK_SALARY:
        try:
            salary = float(text.replace(",", ""))
            data["monthly_salary"] = salary
            sessions[user_id] = {"step": STEP_ASK_YEARS, "data": data}
            reply(reply_token, [text_msg("ทำงานที่บริษัทนี้มานานแค่ไหนแล้วครับ? (กรอกเป็นปี เช่น 2.5)")])
        except ValueError:
            reply(reply_token, [text_msg("กรุณากรอกเงินเดือนเป็นตัวเลขครับ เช่น 20000")])

    elif step == STEP_ASK_YEARS:
        try:
            years = float(text.replace(",", ""))
            data["work_years"] = years
            sessions[user_id] = {"step": STEP_ASK_LEAVE, "data": data}
            reply(reply_token, [text_msg("มีวันลาพักร้อนคงเหลืออีกกี่วันครับ?\n(ถ้าไม่มีหรือไม่ทราบ พิมพ์ 0)")])
        except ValueError:
            reply(reply_token, [text_msg("กรุณากรอกอายุงานเป็นตัวเลขครับ เช่น 2.5")])

    elif step == STEP_ASK_LEAVE:
        try:
            leave_days = int(text.replace(",", ""))
            data["unused_leave_days"] = leave_days
            data["total_leave_days"] = leave_days
            sessions[user_id] = {"step": STEP_ASK_ISSUES, "data": data}
            reply(reply_token, [quick_reply_msg(
                "ปัญหาที่พบคืออะไรครับ? (เลือกที่ตรงกับสถานการณ์ของคุณ)\n\nถ้ามีหลายปัญหา ทยอยเลือกทีละข้อได้เลยครับ แล้วพิมพ์ 'วิเคราะห์' เมื่อเลือกครบ",
                [
                    {"label": "🔴 ถูกเลิกจ้าง",        "text": "wrongful_termination"},
                    {"label": "😰 ถูกบังคับลาออก",      "text": "forced_resignation"},
                    {"label": "💰 ค่าจ้างค้างจ่าย",     "text": "wage_arrears"},
                    {"label": "⏰ ไม่ได้รับ OT",         "text": "no_overtime_pay"},
                    {"label": "📅 วันลาไม่ได้รับชดเชย", "text": "unused_leave"},
                    {"label": "⚖️ เลิกจ้างไม่เป็นธรรม", "text": "unfair_dismissal"},
                    {"label": "✅ วิเคราะห์เลย",         "text": "วิเคราะห์"},
                ]
            )])
        except ValueError:
            reply(reply_token, [text_msg("กรุณากรอกจำนวนวันเป็นตัวเลขครับ เช่น 5")])

    elif step == STEP_ASK_ISSUES:
        issue_map = {
            "wrongful_termination": "wrongful_termination",
            "forced_resignation":   "forced_resignation",
            "wage_arrears":         "wage_arrears",
            "no_overtime_pay":      "no_overtime_pay",
            "unused_leave":         "unused_leave",
            "unfair_dismissal":     "unfair_dismissal",
        }
        issue_labels = {
            "wrongful_termination": "ถูกเลิกจ้างโดยไม่มีความผิด",
            "forced_resignation":   "ถูกบังคับให้ลาออก",
            "wage_arrears":         "ค่าจ้างค้างจ่าย",
            "no_overtime_pay":      "ไม่ได้รับค่าล่วงเวลา",
            "unused_leave":         "วันลาที่ยังไม่ได้ใช้",
            "unfair_dismissal":     "เลิกจ้างไม่เป็นธรรม",
        }

        if "issues" not in data:
            data["issues"] = []

        if text in issue_map:
            issue_key = issue_map[text]
            if issue_key not in data["issues"]:
                data["issues"].append(issue_key)
            selected = ", ".join(issue_labels[i] for i in data["issues"])
            sessions[user_id] = {"step": STEP_ASK_ISSUES, "data": data}
            reply(reply_token, [quick_reply_msg(
                f"เพิ่มแล้วครับ ✅\nที่เลือกไว้: {selected}\n\nเพิ่มอีกหรือวิเคราะห์เลยครับ?",
                [
                    {"label": "✅ วิเคราะห์เลย", "text": "วิเคราะห์"},
                    {"label": "🔴 ถูกเลิกจ้าง",  "text": "wrongful_termination"},
                    {"label": "💰 ค่าจ้างค้าง",  "text": "wage_arrears"},
                    {"label": "⏰ ไม่ได้รับ OT",  "text": "no_overtime_pay"},
                ]
            )])

        elif text in ["วิเคราะห์", "วิเคราะห์เลย", "ok", "ตกลง"]:
            if not data.get("issues"):
                reply(reply_token, [text_msg("กรุณาเลือกปัญหาอย่างน้อย 1 ข้อก่อนครับ")])
                return

            sessions[user_id] = {"step": STEP_ANALYZING, "data": data}
            reply(reply_token, [text_msg("⏳ น้องช้างกำลังวิเคราะห์สิทธิ์ของคุณ...\nรอสักครู่นะครับ 🐘")])

            try:
                # Analyze
                data["employment_type"] = "พนักงานประจำ"
                data["company_size"]    = "ไม่ระบุ"
                analysis = analyze_situation(data)

                severance = analysis.get("severance_amount", 0)
                leave_pay = analysis.get("leave_payout", 0)

                sessions[user_id] = {"step": STEP_RESULT, "data": data}

                reply_msgs = [flex_result_msg(analysis, severance, leave_pay)]
                reply(reply_token, reply_msgs)

            except Exception as e:
                print(f"[analyze ERROR] {e}")
                reply(reply_token, [text_msg(
                    "ขออภัยครับ เกิดข้อผิดพลาดในการวิเคราะห์\n"
                    "กรุณาลองใหม่อีกครั้ง หรือเข้าใช้งานผ่านเว็บไซต์ครับ\n\n"
                    "🌐 https://ai-labor-advisor.onrender.com"
                )])
        else:
            reply(reply_token, [text_msg(
                "กรุณาเลือกปัญหาจากตัวเลือกด้านบน หรือพิมพ์ 'วิเคราะห์' เพื่อดำเนินการต่อครับ"
            )])

    elif step == STEP_RESULT:
        reply(reply_token, [quick_reply_msg(
            "ต้องการทำอะไรต่อครับ?",
            [
                {"label": "📦 รับเอกสารครบชุด", "text": "รับเอกสาร"},
                {"label": "🔄 เริ่มใหม่",         "text": "เริ่มใหม่"},
            ]
        )])

    elif text == "รับเอกสาร":
        reply(reply_token, [text_msg(
            "📦 รับแพ็กเกจเอกสารได้ที่นี่ครับ!\n\n"
            "🌐 https://ai-labor-advisor.onrender.com\n\n"
            "เข้าเว็บแล้วกรอกข้อมูลเพิ่มเติมเล็กน้อย น้องช้างจะสร้างเอกสารให้ครับ 🐘"
        )])

    else:
        reply(reply_token, [quick_reply_msg(
            "สวัสดีครับ! 🐘 พิมพ์ 'สวัสดี' เพื่อเริ่มต้นใช้งาน หรือเลือกเมนูด้านล่างครับ",
            [
                {"label": "🔍 เช็คสิทธิ์", "text": "สวัสดี"},
                {"label": "🌐 เข้าเว็บไซต์", "text": "รับเอกสาร"},
            ]
        )])
