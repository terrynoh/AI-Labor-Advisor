# -*- coding: utf-8 -*-
"""
line_bot.py
LINE Messaging API Webhook Handler
ได้สิทธิ์ — AI Labor Advisor LINE OA

Flow:
  START → ask_name → ask_age → ask_salary → ask_years
  → 즉석 계산 후 "부당해고 당하셨나요?" [네/아니요]
  → 웹 리다이렉트 (파라미터 pre-fill)
"""

import os
import hashlib
import hmac
import base64
import json
import urllib.parse
import requests

LINE_CHANNEL_SECRET       = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_API_URL              = "https://api.line.me/v2/bot/message/reply"
WEB_APP_URL               = "https://ai-labor-advisor.onrender.com/"

# ── Session store ──────────────────────────────────────────────────────────
sessions = {}

# ── Steps ──────────────────────────────────────────────────────────────────
STEP_START           = "start"
STEP_ASK_NAME        = "ask_name"
STEP_ASK_AGE         = "ask_age"
STEP_ASK_SALARY      = "ask_salary"
STEP_ASK_TENURE      = "ask_tenure"
STEP_ASK_TERMINATION = "ask_termination"

# ── 근속기간 구간 → 대표값 매핑 ───────────────────────────────────────────
TENURE_OPTIONS = [
    {"label": "น้อยกว่า 1 ปี",   "text": "tenure_1", "years": 0.5,  "display": "น้อยกว่า 1 ปี"},
    {"label": "1 - 3 ปี",        "text": "tenure_2", "years": 2.0,  "display": "1–3 ปี"},
    {"label": "3 - 6 ปี",        "text": "tenure_3", "years": 4.0,  "display": "3–6 ปี"},
    {"label": "6 - 10 ปี",       "text": "tenure_4", "years": 8.0,  "display": "6–10 ปี"},
    {"label": "10 - 20 ปี",      "text": "tenure_5", "years": 15.0, "display": "10–20 ปี"},
    {"label": "มากกว่า 20 ปี",   "text": "tenure_6", "years": 25.0, "display": "มากกว่า 20 ปี"},
]
TENURE_MAP = {opt["text"]: opt for opt in TENURE_OPTIONS}


def verify_signature(body: bytes, signature: str) -> bool:
    hash_val = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    return base64.b64encode(hash_val).decode("utf-8") == signature


def reply(reply_token: str, messages: list):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"replyToken": reply_token, "messages": messages}
    try:
        res = requests.post(LINE_API_URL, headers=headers, json=payload, timeout=10)
        print("LINE reply:", res.status_code, res.text)
    except Exception as e:
        print(f"[LINE reply ERROR] {e}")


def text_msg(text: str) -> dict:
    return {"type": "text", "text": text}


def quick_reply_msg(text: str, items: list) -> dict:
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


def flex_tenure_msg(text: str) -> dict:
    """근속기간 선택용 Flex Message 세로 버튼"""
    buttons = []
    for opt in TENURE_OPTIONS:
        buttons.append({
            "type": "button",
            "style": "secondary",
            "height": "sm",
            "margin": "xs",
            "action": {
                "type": "message",
                "label": opt["label"],
                "text": opt["text"]
            }
        })

    return {
        "type": "flex",
        "altText": "เลือกระยะเวลาทำงาน",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": text,
                        "wrap": True,
                        "size": "sm",
                        "color": "#374151",
                        "margin": "none"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "contents": buttons
            }
        }
    }


def button_url_msg(text: str, label: str, url: str) -> dict:
    """웹 리다이렉트 버튼이 달린 Flex 메시지."""
    return {
        "type": "flex",
        "altText": label,
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": text,
                        "wrap": True,
                        "size": "sm",
                        "color": "#374151"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#0A5C36",
                        "action": {
                            "type": "uri",
                            "label": label,
                            "uri": url
                        }
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "margin": "sm",
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


def build_redirect_url(data: dict, issue: str) -> str:
    params = urllib.parse.urlencode({
        "name":           data.get("name", ""),
        "age":            data.get("age", ""),
        "salary":         data.get("monthly_salary", ""),
        "work_years":     data.get("work_years", ""),   # 웹 폼 pre-fill용 (웹에서 재입력 가능)
        "tenure_display": data.get("tenure_display", ""),
        "issue":          issue,
        "from":           "line",
    })
    return f"{WEB_APP_URL}?{params}"


def handle_message(body: str):
    data = json.loads(body)
    for event in data.get("events", []):
        if event.get("type") != "message":
            continue
        if event["message"].get("type") != "text":
            continue
        user_id     = event["source"]["userId"]
        reply_token = event["replyToken"]
        user_text   = event["message"]["text"]
        print("user:", user_text)
        process_message(user_id, reply_token, user_text)


def process_message(user_id: str, reply_token: str, user_text: str):
    from calculators import calculate_severance

    text    = user_text.strip()
    session = sessions.get(user_id, {"step": STEP_START, "data": {}})
    step    = session["step"]
    data    = session["data"]

    # 리셋 키워드
    if text.lower() in ["เริ่มใหม่", "reset", "start", "สวัสดี", "หวัดดี", "ใหม่"]:
        sessions[user_id] = {"step": STEP_START, "data": {}}
        step = STEP_START

    # ── START ────────────────────────────────────────────────────────────
    if step == STEP_START:
        sessions[user_id] = {"step": STEP_ASK_NAME, "data": {}}
        reply(reply_token, [text_msg(
            "สวัสดีครับ! 🐘 ผมน้องช้าง\n"
            "ผู้ช่วยด้านสิทธิ์แรงงานไทย\n\n"
            "ผมจะช่วยคำนวณค่าชดเชยและสิทธิ์ที่คุณควรได้รับตามกฎหมายครับ\n\n"
            "เริ่มเลย — ชื่อของคุณคืออะไรครับ? 😊"
        )])

    # ── 이름 ─────────────────────────────────────────────────────────────
    elif step == STEP_ASK_NAME:
        if not text:
            reply(reply_token, [text_msg("กรุณาพิมพ์ชื่อของคุณครับ 😊")])
            return
        data["name"] = text
        sessions[user_id] = {"step": STEP_ASK_AGE, "data": data}
        reply(reply_token, [text_msg(
            f"ยินดีที่รู้จักครับ คุณ{text}! 😊\n\n"
            "อายุเท่าไหร่ครับ? (ตัวเลขเท่านั้น เช่น 32)"
        )])

    # ── 나이 ─────────────────────────────────────────────────────────────
    elif step == STEP_ASK_AGE:
        try:
            age = int(text.replace(",", ""))
            if not 15 <= age <= 80:
                raise ValueError
            data["age"] = age
            sessions[user_id] = {"step": STEP_ASK_SALARY, "data": data}
            reply(reply_token, [text_msg(
                "เข้าใจแล้วครับ 👍\n\n"
                "เงินเดือนล่าสุดของคุณเท่าไหร่ครับ?\n"
                "(ตัวเลขเท่านั้น เช่น 15000)"
            )])
        except ValueError:
            reply(reply_token, [text_msg("กรุณากรอกอายุเป็นตัวเลขครับ เช่น 32")])

    # ── 급여 ─────────────────────────────────────────────────────────────
    elif step == STEP_ASK_SALARY:
        try:
            salary = float(text.replace(",", "").replace("บาท", "").strip())
            if salary < 100:
                raise ValueError
            data["monthly_salary"] = salary
            sessions[user_id] = {"step": STEP_ASK_TENURE, "data": data}
            reply(reply_token, [flex_tenure_msg(
                "ขอบคุณครับ 😊\n\nทำงานที่บริษัทนี้นานแค่ไหนครับ?"
            )])
        except ValueError:
            reply(reply_token, [text_msg("กรุณากรอกเงินเดือนเป็นตัวเลขครับ เช่น 15000")])

    # ── 근속기간 구간 선택 → 즉석 계산 후 부당해고 질문 ────────────────────
    elif step == STEP_ASK_TENURE:
        tenure = TENURE_MAP.get(text)
        if not tenure:
            reply(reply_token, [flex_tenure_msg(
                "กรุณาเลือกระยะเวลาทำงานครับ 😊"
            )])
            return

        years = tenure["years"]
        data["work_years"]       = years
        data["tenure_display"]   = tenure["display"]
        sessions[user_id] = {"step": STEP_ASK_TERMINATION, "data": data}

        salary = data.get("monthly_salary", 0)
        name   = data.get("name", "คุณ")
        sev, detail = calculate_severance(salary, years)

        reply(reply_token, [quick_reply_msg(
            f"ได้เลยครับ คุณ{name}! 🐘\n\n"
            f"จากข้อมูลที่แจ้งมา หากถูกเลิกจ้างโดยไม่มีความผิด\n"
            f"คุณมีสิทธิ์ได้รับค่าชดเชยขั้นต่ำ\n\n"
            f"💰 {sev:,.0f} บาท\n"
            f"({detail})\n\n"
            f"คุณถูกเลิกจ้างโดยไม่มีความผิดใช่ไหมครับ?",
            [
                {"label": "✅ ใช่ครับ/ค่ะ", "text": "YES"},
                {"label": "❌ ไม่ใช่",       "text": "NO"},
            ]
        )])

    # ── 부당해고 여부 → 웹 리다이렉트 ─────────────────────────────────
    elif step == STEP_ASK_TERMINATION:
        is_yes = text.upper() in ("YES", "ใช่", "ใช่ครับ", "ใช่ค่ะ", "✅ ใช่ครับ/ค่ะ")
        is_no  = text.upper() in ("NO",  "ไม่", "ไม่ใช่",  "❌ ไม่ใช่")

        if not is_yes and not is_no:
            reply(reply_token, [quick_reply_msg(
                "กรุณาเลือกครับ 😊",
                [
                    {"label": "✅ ใช่ครับ/ค่ะ", "text": "YES"},
                    {"label": "❌ ไม่ใช่",       "text": "NO"},
                ]
            )])
            return

        salary     = data.get("monthly_salary", 0)
        work_years = data.get("work_years", 0)

        if is_yes:
            sev, detail = calculate_severance(salary, work_years)
            url = build_redirect_url(data, issue="wrongful_termination")
            msg = (
                f"✅ คุณมีสิทธิ์ได้รับ\n\n"
                f"💰 ค่าชดเชย: {sev:,.0f} บาท\n"
                f"({detail})\n\n"
                f"นอกจากนี้อาจมีค่าวันลา ค่าแจ้งล่วงหน้า\n"
                f"และค่าเสียหายเพิ่มเติมครับ\n\n"
                f"กดปุ่มด้านล่างเพื่อตรวจสอบสิทธิ์ครบทุกรายการ\n"
                f"และดาวน์โหลดเอกสารได้เลยครับ 👇\n\n"
                f"📝 หมายเหตุ: เอกสารจะถูกกรอกข้อมูลพื้นฐานให้อัตโนมัติ\n"
                f"ส่วนข้อมูลส่วนตัว เช่น เลขบัตรประชาชน วันเริ่ม-สิ้นสุดงาน\n"
                f"กรุณากรอกเพิ่มเติมด้วยตัวเองก่อนยื่นครับ"
            )
            label = "📋 ตรวจสอบสิทธิ์ครบทุกรายการ"
        else:
            url = build_redirect_url(data, issue="")
            msg = (
                f"เข้าใจครับ 😊\n\n"
                f"ยังมีสิทธิ์อื่นๆ ที่คุณอาจได้รับ เช่น\n"
                f"💰 ค่าจ้างค้างจ่าย\n"
                f"📅 ค่าวันลาที่ยังไม่ได้ใช้\n"
                f"⏰ ค่าล่วงเวลา\n\n"
                f"กดปุ่มด้านล่างเพื่อตรวจสอบสิทธิ์ทั้งหมดครับ 👇\n\n"
                f"📝 หมายเหตุ: เอกสารจะถูกกรอกข้อมูลพื้นฐานให้อัตโนมัติ\n"
                f"ส่วนข้อมูลส่วนตัว เช่น เลขบัตรประชาชน วันเริ่ม-สิ้นสุดงาน\n"
                f"กรุณากรอกเพิ่มเติมด้วยตัวเองก่อนยื่นครับ"
            )
            label = "📋 ตรวจสอบสิทธิ์ทั้งหมด"

        # 세션 초기화 (다음 대화 대비)
        sessions[user_id] = {"step": STEP_START, "data": {}}
        reply(reply_token, [button_url_msg(msg, label, url)])

    # ── fallback ─────────────────────────────────────────────────────────
    else:
        sessions[user_id] = {"step": STEP_START, "data": {}}
        reply(reply_token, [quick_reply_msg(
            "สวัสดีครับ! 🐘 กดปุ่มด้านล่างเพื่อเริ่มต้นใช้งานครับ",
            [{"label": "🔍 เช็คสิทธิ์แรงงาน", "text": "สวัสดี"}]
        )])
