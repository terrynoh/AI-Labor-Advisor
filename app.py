# -*- coding: utf-8 -*-
import os
import tempfile
from flask import Flask, request, jsonify, render_template, session, send_file
from chatbot import chat, get_initial_message, analyze_situation
from calculators import calculate_severance, calculate_leave
from pdf_generator import generate_kor7_pdf, generate_demand_letter_pdf
from chatbot import generate_demand_letter_body
from line_bot import handle_message, verify_signature
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

@app.route("/webhook/line", methods=["POST"])
def line_webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    print("🔥 LINE BODY:", body)

    # Verify 요청 대응
    if not signature:
        return "OK", 200

    if not verify_signature(body, signature):
        return "Invalid signature", 400

    try:
        handle_message(body)
    except Exception as e:
        print("❌ handle_message error:", e)

    return "OK", 200

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


# ─────────────────────────────────────────────────────────────────
#  /generate-package  — 패키지 생성 엔드포인트
#  프론트에서 결제 완료 후 호출
# ─────────────────────────────────────────────────────────────────

@app.route("/generate-package", methods=["POST"])
def generate_package():
    """
    Request body (JSON):
    {
        "case_data": {
            "complainant_name": "...",
            "age": "...",
            "address": "...",
            "phone": "...",
            "employer_name": "...",
            "employer_address": "...",
            "position": "...",
            "start_date": "...",
            "end_date": "...",
            "wage_rate": 45000,
            "issues": ["wrongful_termination", ...],
            "severance_amount": 270000,
            "leave_payout": 6000,
            "notice_pay": 45000,
            "wage_owed": 0,
            "ot_amount": 0,
            "total_amount": 321000,
            "deadline": 15
        }
    }

    Response: JSON with download URLs for generated PDFs
    """
    import os, tempfile, zipfile
    from flask import send_file

    try:
        body      = request.get_json(force=True)
        case_data = body.get("case_data", {})

        if not case_data.get("complainant_name"):
            return jsonify({"error": "complainant_name required"}), 400

        tmp_dir = tempfile.mkdtemp()

        # ── 1. 내용증명 항의서 생성 ──────────────────────────────────
        letter_body = generate_demand_letter_body(case_data)
        demand_pdf_path = os.path.join(tmp_dir, "01_demand_letter.pdf")
        generate_demand_letter_pdf(case_data, letter_body, demand_pdf_path)

        # ── 2. คร.7 진정서 생성 ──────────────────────────────────────
        kor7_data = {
            "complainant_name":  case_data.get("complainant_name"),
            "age":               case_data.get("age"),
            "address_no":        case_data.get("address"),
            "phone":             case_data.get("phone"),
            "employer_name":     case_data.get("employer_name"),
            "employer_address_no": case_data.get("employer_address"),
            "position":          case_data.get("position"),
            "start_date":        case_data.get("start_date"),
            "end_date":          case_data.get("end_date"),
            "wage_rate":         case_data.get("wage_rate"),
            "severance":         case_data.get("severance_amount"),
            "notice_pay":        case_data.get("notice_pay"),
            "wage_owed":         case_data.get("wage_owed"),
            "ot_amount":         case_data.get("ot_amount"),
            "filed_province":    case_data.get("filed_province", "กรุงเทพมหานคร"),
        }
        kor7_pdf_path = os.path.join(tmp_dir, "02_kor7_petition.pdf")
        generate_kor7_pdf(kor7_data, kor7_pdf_path)

        # ── 3. ZIP으로 묶기 ──────────────────────────────────────────
        zip_path = os.path.join(tmp_dir, "labor_package.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(demand_pdf_path, "01_หนังสือบอกกล่าว.pdf")
            zf.write(kor7_pdf_path,   "02_คร7_คำร้อง.pdf")

        return send_file(
            zip_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name="labor_package.zip"
        )

    except Exception as e:
        print(f"[generate_package ERROR] {e}")
        return jsonify({"error": str(e)}), 500
    
    'app_line-addition.py'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
