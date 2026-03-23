# -*- coding: utf-8 -*-
import os
import uuid
import logging
import tempfile
import omise
from flask import Flask, request, jsonify, render_template, session, send_file
from chatbot import chat, get_initial_message, analyze_situation
from calculators import calculate_severance, calculate_leave
from pdf_generator import generate_kor7_pdf, generate_demand_letter_pdf
from chatbot import generate_demand_letter_body
from line_bot import handle_message, verify_signature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2MB 요청 크기 제한

_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    logger.warning("SECRET_KEY 환경변수가 설정되지 않았습니다. 프로덕션에서는 반드시 설정하세요.")
    _secret_key = "dev-secret-key-change-in-production"
app.secret_key = _secret_key

OMISE_SECRET_KEY     = os.environ.get("OMISE_SECRET_KEY", "")
OMISE_PUBLIC_KEY     = os.environ.get("OMISE_PUBLIC_KEY", "")
APP_BASE_URL         = os.environ.get("APP_BASE_URL", "").rstrip("/")
PACKAGE_PRICE_SATANG = 10000  # 100 THB

_pending_orders: dict = {}  # inv → {case_data, analysis_result, _paid}


# ─────────────────────────────────────────────
#  페이지
# ─────────────────────────────────────────────

@app.route("/")
def index():
    session.clear()
    session["messages"] = []
    return render_template("index.html", omise_public_key=OMISE_PUBLIC_KEY)


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

    tmp_path = _create_temp_pdf()
    try:
        generate_kor7_pdf(merged, tmp_path)
        return send_file(
            tmp_path,
            as_attachment=True,
            download_name="คร.7.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        logger.error("generate_petition_pdf 오류: %s", e, exc_info=True)
        return jsonify({"error": "문서 생성 중 오류가 발생했습니다."}), 500


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

@app.route("/webhook/line", methods=["GET", "POST"])
def line_webhook():
    if request.method == "GET":
        return "OK GET", 200

    signature = request.headers.get("X-Line-Signature", "")
    body_bytes = request.get_data()  # raw bytes 유지
    body = body_bytes.decode("utf-8")  # 로그/파싱용

    logger.debug("LINE webhook body: %s", body)

    if not signature:
        logger.warning("LINE webhook: X-Line-Signature 헤더 없음 — 요청 거부")
        return "Unauthorized", 401

    if not verify_signature(body_bytes, signature):
        logger.warning("LINE webhook: 서명 불일치 — 요청 거부")
        return "Unauthorized", 401

    try:
        handle_message(body)
    except Exception as e:
        logger.error("handle_message 오류: %s", e, exc_info=True)

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
    data = request.json or {}
    tmp_path = _create_temp_pdf()
    try:
        generate_kor7_pdf(data, tmp_path)
        return send_file(
            tmp_path,
            as_attachment=True,
            download_name="คร.7.pdf",
            mimetype="application/pdf"
        )
    except Exception as e:
        logger.error("generate_pdf 오류: %s", e, exc_info=True)
        return jsonify({"error": "문서 생성 중 오류가 발생했습니다."}), 500


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

def _create_temp_pdf() -> str:
    """임시 PDF 파일 경로를 생성하고 반환합니다."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    return tmp.name


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
    except (json.JSONDecodeError, ValueError, Exception) as e:
        logger.error("extract_pdf_data_from_messages 오류: %s", e, exc_info=True)
        return {}


# ─────────────────────────────────────────────────────────────────
#  PDF 임시 저장소 (session_id → {demand_path, petition_path})
# ─────────────────────────────────────────────────────────────────

_pdf_store = {}


# ─────────────────────────────────────────────────────────────────
#  /generate-package  — 패키지 생성 엔드포인트
#  프론트에서 결제 완료 후 호출 → JSON 반환 (session_id)
# ─────────────────────────────────────────────────────────────────

@app.route("/generate-package", methods=["POST"])
def generate_package():
    """
    Request body (JSON):
    {
        "case_data": { ... }
    }
    Response: { "session_id": "...", "ok": true }
    """
    import uuid

    try:
        body      = request.get_json(force=True)
        case_data = body.get("case_data", {})

        if not case_data.get("complainant_name"):
            return jsonify({"error": "complainant_name required"}), 400

        tmp_dir = tempfile.mkdtemp()

        # ── 1. 내용증명 항의서 생성 ──────────────────────────────────
        letter_body = generate_demand_letter_body(case_data)
        demand_pdf_path = os.path.join(tmp_dir, "demand_letter.pdf")
        generate_demand_letter_pdf(case_data, letter_body, demand_pdf_path)

        # ── 2. คร.7 진정서 생성 ──────────────────────────────────────
        issues = case_data.get("issues", [])
        issue_map = {
            "wrongful_termination": "ถูกเลิกจ้างโดยไม่เป็นธรรมและไม่มีเหตุผลอันสมควร",
            "no_severance":         "นายจ้างไม่จ่ายค่าชดเชยการเลิกจ้าง",
            "unpaid_wages":         "นายจ้างค้างจ่ายค่าจ้าง",
            "no_notice":            "นายจ้างไม่บอกกล่าวล่วงหน้าก่อนเลิกจ้าง",
            "unpaid_leave":         "นายจ้างไม่จ่ายค่าจ้างสำหรับวันลาที่ยังไม่ได้ใช้",
            "forced_resignation":   "ถูกบังคับให้ลาออกซึ่งถือเป็นการเลิกจ้างโดยอ้อม",
        }
        reason_parts = [issue_map[i] for i in issues if i in issue_map]
        reason = " และ".join(reason_parts) if reason_parts else case_data.get("reason", "")

        kor7_data = {
            "complainant_name":    case_data.get("complainant_name"),
            "age":                 case_data.get("age"),
            "address_no":          case_data.get("address_no", case_data.get("address", "")),
            "phone":               case_data.get("phone"),
            "employer_name":       case_data.get("employer_name"),
            "employer_address_no": case_data.get("employer_address_no", case_data.get("employer_address", "")),
            "position":            case_data.get("position"),
            "start_date":          case_data.get("start_date"),
            "end_date":            case_data.get("end_date"),
            "wage_rate":           case_data.get("wage_rate"),
            "severance":           case_data.get("severance_amount"),
            "notice_pay":          case_data.get("notice_pay"),
            "wage_owed":           case_data.get("wage_owed"),
            "ot_amount":           case_data.get("ot_amount"),
            "filed_province":      case_data.get("filed_province", "กรุงเทพมหานคร"),
            "reason":              reason,
        }
        petition_pdf_path = os.path.join(tmp_dir, "kor7_petition.pdf")
        generate_kor7_pdf(kor7_data, petition_pdf_path)

        # ── 3. session_id 발급 후 경로 저장 ─────────────────────────
        sid = str(uuid.uuid4())
        _pdf_store[sid] = {
            "demand_path":   demand_pdf_path,
            "petition_path": petition_pdf_path,
        }

        return jsonify({"ok": True, "session_id": sid})

    except Exception as e:
        logger.error("generate_package 오류: %s", e, exc_info=True)
        return jsonify({"error": "패키지 생성 중 오류가 발생했습니다."}), 500


# ─────────────────────────────────────────────────────────────────
#  개별 PDF 다운로드 엔드포인트
# ─────────────────────────────────────────────────────────────────

@app.route("/download/demand-letter/<session_id>")
def download_demand_letter(session_id):
    entry = _pdf_store.get(session_id)
    if not entry or not os.path.exists(entry["demand_path"]):
        return jsonify({"error": "File not found"}), 404
    return send_file(
        entry["demand_path"],
        as_attachment=True,
        download_name="หนังสือบอกกล่าว.pdf",
        mimetype="application/pdf"
    )


@app.route("/download/petition/<session_id>")
def download_petition(session_id):
    entry = _pdf_store.get(session_id)
    if not entry or not os.path.exists(entry["petition_path"]):
        return jsonify({"error": "File not found"}), 404
    return send_file(
        entry["petition_path"],
        as_attachment=True,
        download_name="คร.7_คำร้อง.pdf",
        mimetype="application/pdf"
    )


# ─────────────────────────────────────────────────────────────────
#  Omise 결제 연동
# ─────────────────────────────────────────────────────────────────

@app.route("/create-payment", methods=["POST"])
def create_payment():
    """
    Body: { "token": "tokn_...", "case_data": {...}, "analysis_result": {...} }
    Response: { "inv": "...", "paid": true }
           or { "inv": "...", "authorize_uri": "..." }  (3DS 필요 시)
    """
    body          = request.get_json(force=True) or {}
    token         = body.get("token", "")
    case_data     = body.get("case_data", {})
    analysis_result = body.get("analysis_result", {})

    if not token:
        return jsonify({"error": "token required"}), 400
    if not OMISE_SECRET_KEY:
        return jsonify({"error": "payment not configured"}), 503

    inv = uuid.uuid4().hex[:16]
    _pending_orders[inv] = {"case_data": case_data, "analysis_result": analysis_result, "_paid": False}

    try:
        omise.api_secret = OMISE_SECRET_KEY
        charge = omise.Charge.create(
            amount=PACKAGE_PRICE_SATANG,
            currency="thb",
            card=token,
            return_uri=f"{APP_BASE_URL}/payment-return?inv={inv}",
            metadata={"invoice": inv},
        )

        if charge.status == "successful":
            _pending_orders[inv]["_paid"] = True
            logger.info("Omise 즉시 결제 완료: %s", inv)
            return jsonify({"ok": True, "inv": inv, "paid": True})

        if getattr(charge, "authorize_uri", None):
            logger.info("Omise 3DS 리다이렉트: %s", inv)
            return jsonify({"ok": True, "inv": inv, "authorize_uri": charge.authorize_uri})

        logger.warning("Omise 결제 실패: %s status=%s", inv, charge.status)
        return jsonify({"error": "결제 실패", "status": charge.status}), 402

    except Exception as e:
        logger.error("create_payment 오류: %s", e, exc_info=True)
        return jsonify({"error": "결제 처리 중 오류가 발생했습니다."}), 500


@app.route("/webhook/omise", methods=["POST"])
def webhook_omise():
    """Omise 백엔드 웹훅 — charge.complete 이벤트 수신"""
    try:
        event  = request.get_json(force=True) or {}
        if event.get("key") == "charge.complete":
            charge = event.get("data", {})
            inv    = (charge.get("metadata") or {}).get("invoice", "")
            if inv and charge.get("status") == "successful":
                if inv in _pending_orders:
                    _pending_orders[inv]["_paid"] = True
                    logger.info("Omise 웹훅 결제 확인: %s", inv)
        return "OK", 200
    except Exception as e:
        logger.error("webhook_omise 오류: %s", e, exc_info=True)
        return "Error", 400


@app.route("/payment-return")
def payment_return():
    """3DS 완료 후 브라우저 복귀 페이지"""
    return render_template("index.html", omise_public_key=OMISE_PUBLIC_KEY)


@app.route("/get-order-data/<inv>")
def get_order_data(inv):
    """결제 복귀 후 프론트에서 case_data/analysis_result 복원용"""
    entry = _pending_orders.get(inv)
    if not entry:
        return jsonify({"error": "not found"}), 404
    if not entry.get("_paid"):
        return jsonify({"paid": False}), 202
    return jsonify({
        "paid": True,
        "case_data":       entry["case_data"],
        "analysis_result": entry["analysis_result"],
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
