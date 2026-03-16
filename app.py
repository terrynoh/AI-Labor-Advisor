# -*- coding: utf-8 -*-
import os
import tempfile
from flask import Flask, request, jsonify, render_template, session, send_file
from chatbot import chat, get_initial_message, analyze_situation
from calculators import calculate_severance, calculate_leave
from pdf_generator import generate_kor7_pdf

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")


# ─────────────────────────────────────────────
#  페이지
# ─────────────────────────────────────────────

@app.route("/")
def index():
    session.clear()
    session["messages"] = []
    return render_template("index.html")


# ─────────────────────────────────────────────
#  NEW: 폼 기반 플로우
# ─────────────────────────────────────────────

@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Step 1 폼 제출 → 분석 결과 반환
    Body: { name, age, work_years, monthly_salary,
            unused_leave_days, total_leave_days,
            employment_type, company_size, issues[] }
    """
    data = request.json or {}
    session["user_data"] = data          # 나중에 진정서용으로 재사용

    result = analyze_situation(data)
    return jsonify(result)


@app.route("/generate_petition_pdf", methods=["POST"])
def generate_petition_pdf():
    """
    Step 3 폼 제출 → 진정서 PDF 생성
    Body: Step 1 + Step 3 데이터 통합
    """
    data = request.json or {}
    # Step 1 데이터 병합 (세션에 저장된 것)
    user_data = session.get("user_data", {})
    merged = {**user_data, **data}

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    try:
        generate_kor7_pdf(merged, tmp.name)
        return send_file(
            tmp.name,
            as_attachment=True,
            download_name="คร.7.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
#  기존 채팅 플로우 (유지)
# ─────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
def chat_endpoint():
    user_input = request.json.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "빈 메시지"}), 400

    messages = session.get("messages", [])
    reply, updated, error = chat(messages, user_input)

    if error:
        return jsonify({"error": error}), 500

    session["messages"] = updated
    turn_count = len([m for m in updated if m["role"] == "user"])

    if "[READY_FOR_PDF]" in reply:
        pdf_data = extract_pdf_data_from_messages(updated)
        session["pdf_data"] = pdf_data

    return jsonify({
        "reply": reply,
        "turns_left": 20 - turn_count
    })


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    session["messages"] = []
    return jsonify({"reply": get_initial_message()})


@app.route("/calculate/severance", methods=["POST"])
def severance():
    data = request.json
    amount, detail = calculate_severance(data["salary"], data["years"])
    return jsonify({"amount": amount, "detail": detail})


@app.route("/calculate/leave", methods=["POST"])
def leave():
    data = request.json
    payout = calculate_leave(data["salary"], data["unused_days"])
    return jsonify({"payout": payout})


@app.route("/generate_pdf", methods=["POST"])
def generate_pdf():
    data = request.json
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    try:
        generate_kor7_pdf(data, tmp.name)
        return send_file(
            tmp.name,
            as_attachment=True,
            download_name="คร.7.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get_pdf_data", methods=["GET"])
def get_pdf_data():
    pdf_data = session.get("pdf_data", {})
    defaults = {
        "complainant_name": "", "age": "", "province": "", "phone": "",
        "employer_name": "", "business_type": "", "employer_province": "",
        "start_date": "", "end_date": "", "position": "", "wage_rate": "",
        "reason": "", "severance": "", "notice_pay": "", "wage_owed": "",
        "filed_date": "", "filed_province": "กรุงเทพมหานคร"
    }
    defaults.update(pdf_data)
    return jsonify(defaults)


# ─────────────────────────────────────────────
#  내부 헬퍼
# ─────────────────────────────────────────────

def extract_pdf_data_from_messages(messages):
    import anthropic as ac
    import json
    client_local = ac.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    extract_prompt = """จากการสนทนาต่อไปนี้ ให้ดึงข้อมูลและตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น:
{
  "complainant_name": "", "age": "", "province": "", "phone": "",
  "employer_name": "", "business_type": "", "employer_province": "",
  "start_date": "", "end_date": "", "position": "", "wage_rate": "",
  "reason": "", "severance": "", "notice_pay": "", "wage_owed": "",
  "filed_province": "กรุงเทพมหานคร"
}"""
    try:
        response = client_local.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=extract_prompt,
            messages=messages
        )
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except:
        return {}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
