# -*- coding: utf-8 -*-
import os
import uuid
import time
import hmac as _hmac
import logging
import secrets
import shutil
import tempfile
import threading
import datetime
import requests
import omise
import anthropic as _anthropic_module
from flask import Flask, request, jsonify, render_template, session, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from chatbot import chat, get_initial_message, analyze_situation
from calculators import calculate_severance, calculate_leave
from pdf_generator import generate_kor7_pdf, generate_demand_letter_pdf, TEMPLATE_PATH, LIBREOFFICE_CMD
from chatbot import generate_demand_letter_body
from line_bot import handle_message, verify_signature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2MB 요청 크기 제한

# ─────────────────────────────────────────────
#  SECRET_KEY — 미설정 시 프로덕션에서 즉시 중단
# ─────────────────────────────────────────────
_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    if os.environ.get("RENDER") or os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError(
            "SECRET_KEY 환경변수가 설정되지 않았습니다. "
            "Render 대시보드에서 SECRET_KEY를 설정하세요."
        )
    logger.warning("SECRET_KEY 환경변수 미설정 — 개발 환경 전용 키 사용 (프로덕션 금지)")
    _secret_key = "dev-secret-key-change-in-production"
app.secret_key = _secret_key

OMISE_SECRET_KEY          = os.environ.get("OMISE_SECRET_KEY", "")
OMISE_PUBLIC_KEY          = os.environ.get("OMISE_PUBLIC_KEY", "")
APP_BASE_URL              = os.environ.get("APP_BASE_URL", "").rstrip("/")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
ADMIN_LINE_USER_ID        = os.environ.get("ADMIN_LINE_USER_ID", "")
PACKAGE_PRICE_SATANG      = 10000  # 100 THB

# ─────────────────────────────────────────────
#  Anthropic 클라이언트 (모듈 수준 — 요청마다 재생성 금지)
# ─────────────────────────────────────────────
_ac_client = _anthropic_module.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ─────────────────────────────────────────────
#  Rate Limiter
# ─────────────────────────────────────────────
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)

# ─────────────────────────────────────────────
#  인메모리 스토어 + 스레드 락
# ─────────────────────────────────────────────
_pending_orders: dict = {}  # inv → {case_data, analysis_result, _paid, charge_id, _ts}
_pdf_store:      dict = {}  # sid → {demand_path, petition_path, download_token, _ts}
_orders_lock = threading.Lock()

_ORDER_TTL = 24 * 3600   # 24시간
_PDF_TTL   = 1  * 3600   # 1시간


# ─────────────────────────────────────────────
#  CSRF 헬퍼
# ─────────────────────────────────────────────
def _get_csrf_token() -> str:
    """세션에 CSRF 토큰이 없으면 생성하여 반환."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def _verify_csrf():
    """X-CSRF-Token 헤더 검증. 실패 시 (response, status) 튜플 반환, 성공 시 None."""
    token    = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not token or not expected or not _hmac.compare_digest(token, expected):
        logger.warning("CSRF 검증 실패: ip=%s path=%s", request.remote_addr, request.path)
        return jsonify({"error": "Invalid or missing CSRF token"}), 403
    return None


# ─────────────────────────────────────────────
#  보안 헤더
# ─────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]          = "DENY"
    response.headers["X-XSS-Protection"]         = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    proto = request.headers.get("X-Forwarded-Proto", "")
    if request.is_secure or proto == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ─────────────────────────────────────────────
#  관리자 LINE 알림
# ─────────────────────────────────────────────
def _notify_admin(invoice: str, name: str, error: str, refunded: bool):
    if not ADMIN_LINE_USER_ID or not LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning("관리자 LINE 알림 미설정 — ADMIN_LINE_USER_ID 또는 LINE_CHANNEL_ACCESS_TOKEN 없음")
        return
    status = "완료" if refunded else "실패"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = (
        f"🚨 PDF 생성 실패\n"
        f"invoice: {invoice}\n"
        f"이름: {name}\n"
        f"에러: {error}\n"
        f"환불 상태: {status}\n"
        f"시각: {now}"
    )
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"to": ADMIN_LINE_USER_ID, "messages": [{"type": "text", "text": text}]},
            timeout=5,
        )
    except Exception as ex:
        logger.error("관리자 LINE 알림 전송 실패: %s", ex)


def _evict_expired():
    """만료된 항목 정리 (각 요청 시 호출)."""
    now = time.time()
    with _orders_lock:
        for inv in [k for k, v in _pending_orders.items() if now - v.get("_ts", 0) > _ORDER_TTL]:
            _pending_orders.pop(inv, None)
            logger.debug("_pending_orders 만료 제거: %s", inv)
    for sid in [k for k, v in _pdf_store.items() if now - v.get("_ts", 0) > _PDF_TTL]:
        entry = _pdf_store.pop(sid, {})
        for path_key in ("demand_path", "petition_path"):
            p = entry.get(path_key)
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        logger.debug("_pdf_store 만료 제거: %s", sid)


# ─────────────────────────────────────────────
#  시작 시 환경 검증
# ─────────────────────────────────────────────
def _startup_checks():
    import subprocess as _sp
    if not os.path.exists(TEMPLATE_PATH):
        raise RuntimeError(f"คร.7 템플릿 파일을 찾을 수 없습니다: {TEMPLATE_PATH}")
    try:
        result = _sp.run(
            [LIBREOFFICE_CMD, "--version"],
            capture_output=True, timeout=10
        )
        if result.returncode != 0:
            logger.warning("LibreOffice 버전 확인 실패 (returncode=%s)", result.returncode)
        else:
            logger.info("LibreOffice 확인 완료: %s", result.stdout.decode(errors="replace").strip())
    except FileNotFoundError:
        logger.warning("LibreOffice를 찾을 수 없습니다: %s — PDF 생성이 실패할 수 있습니다.", LIBREOFFICE_CMD)
    except Exception as e:
        logger.warning("LibreOffice 확인 중 오류: %s", e)


with app.app_context():
    _startup_checks()


# ─────────────────────────────────────────────
#  CSRF 토큰 엔드포인트
# ─────────────────────────────────────────────
@app.route("/csrf-token")
def csrf_token_endpoint():
    return jsonify({"token": _get_csrf_token()})


# ─────────────────────────────────────────────
#  페이지
# ─────────────────────────────────────────────

@app.route("/")
def index():
    session.clear()
    session["messages"] = []
    csrf_token = _get_csrf_token()
    return render_template("index.html", omise_public_key=OMISE_PUBLIC_KEY, csrf_token=csrf_token)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


# ─────────────────────────────────────────────
#  폼 기반 플로우
# ─────────────────────────────────────────────

_VALID_ISSUES = {
    "wrongful_termination", "no_severance", "unpaid_wages",
    "no_notice", "unpaid_leave", "forced_resignation",
    "no_overtime_pay", "unfair_dismissal",
}

def _validate_analyze_input(data: dict) -> str | None:
    """입력값 검증. 오류 메시지 반환, 정상이면 None."""
    try:
        age = int(data.get("age", 0))
        if not 15 <= age <= 80:
            return "age must be 15–80"
    except (TypeError, ValueError):
        return "age must be an integer"

    try:
        salary = float(data.get("monthly_salary", 0))
        if not 100 <= salary <= 10_000_000:
            return "monthly_salary must be 100–10,000,000"
    except (TypeError, ValueError):
        return "monthly_salary must be a number"

    try:
        years = float(data.get("work_years", 0))
        if not 0 <= years <= 60:
            return "work_years must be 0–60"
    except (TypeError, ValueError):
        return "work_years must be a number"

    issues = data.get("issues", [])
    if not isinstance(issues, list):
        return "issues must be a list"
    if any(i not in _VALID_ISSUES for i in issues):
        return "issues contains invalid value"

    name = str(data.get("name", "")).strip()
    if len(name) > 200:
        return "name too long"

    return None


@app.route("/analyze", methods=["POST"])
@limiter.limit("10 per minute")
def analyze():
    err = _verify_csrf()
    if err:
        return err

    data = request.json or {}

    err = _validate_analyze_input(data)
    if err:
        return jsonify({"error": err}), 400

    session["user_data"] = data

    result = analyze_situation(data)
    return jsonify(result)


@app.route("/generate_petition_pdf", methods=["POST"])
@limiter.limit("5 per minute")
def generate_petition_pdf():
    err = _verify_csrf()
    if err:
        return err

    data = request.json or {}
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
#  채팅 플로우
# ─────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
@limiter.limit("30 per minute")
def chat_endpoint():
    err = _verify_csrf()
    if err:
        return err

    user_input = (request.json or {}).get("message", "").strip()
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
    body_bytes = request.get_data()
    body = body_bytes.decode("utf-8")

    logger.info("LINE webhook received, body length: %d", len(body))

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
    new_token = _get_csrf_token()
    return jsonify({"reply": get_initial_message(), "csrf_token": new_token})


@app.route("/calculate/severance", methods=["POST"])
@limiter.limit("20 per minute")
def severance():
    err = _verify_csrf()
    if err:
        return err
    data = request.json or {}
    try:
        salary = float(data["salary"])
        years  = float(data["years"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "salary and years required as numbers"}), 400
    amount, detail = calculate_severance(salary, years)
    return jsonify({"amount": amount, "detail": detail})


@app.route("/calculate/leave", methods=["POST"])
@limiter.limit("20 per minute")
def leave():
    err = _verify_csrf()
    if err:
        return err
    data = request.json or {}
    try:
        salary      = float(data["salary"])
        unused_days = int(data["unused_days"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "salary and unused_days required"}), 400
    payout = calculate_leave(salary, unused_days)
    return jsonify({"payout": payout})


@app.route("/generate_pdf", methods=["POST"])
@limiter.limit("5 per minute")
def generate_pdf():
    err = _verify_csrf()
    if err:
        return err
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
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    return tmp.name


def extract_pdf_data_from_messages(messages):
    import json
    extract_prompt = """จากการสนทนาต่อไปนี้ ให้ดึงข้อมูลและตอบเป็น JSON เท่านั้น ไม่มีข้อความอื่น:
{
  "complainant_name": "", "age": "", "province": "", "phone": "",
  "employer_name": "", "business_type": "", "employer_province": "",
  "start_date": "", "end_date": "", "position": "", "wage_rate": "",
  "reason": "", "severance": "", "notice_pay": "", "wage_owed": "",
  "filed_province": "กรุงเทพมหานคร"
}"""
    try:
        response = _ac_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=extract_prompt,
            messages=messages
        )
        if not response.content:
            return {}
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        logger.error("extract_pdf_data_from_messages 오류: %s", e, exc_info=True)
        return {}


# ─────────────────────────────────────────────
#  /generate-package  — 결제 완료 후 서류 생성
# ─────────────────────────────────────────────

@app.route("/generate-package", methods=["POST"])
@limiter.limit("5 per minute")
def generate_package():
    err = _verify_csrf()
    if err:
        return err

    try:
        body      = request.get_json(force=True)
        inv       = body.get("inv", "")
        case_data = body.get("case_data", {})

        if not case_data.get("complainant_name"):
            return jsonify({"error": "complainant_name required"}), 400

        # 결제 검증: inv가 결제 완료 상태인지 확인
        with _orders_lock:
            order = _pending_orders.get(inv, {})
            if not order.get("_paid"):
                logger.warning("generate_package: 미결제 또는 유효하지 않은 inv=%s", inv)
                return jsonify({"error": "결제가 확인되지 않았습니다."}), 403
            retry_count = order.get("retry_count", 0)

        tmp_dir = tempfile.mkdtemp()

        try:
            # ── 1. 내용증명 생성 ────────────────────────────────────────
            letter_body = generate_demand_letter_body(case_data)
            demand_pdf_path = os.path.join(tmp_dir, "demand_letter.pdf")
            generate_demand_letter_pdf(case_data, letter_body, demand_pdf_path)

            # ── 2. คร.7 진정서 생성 ─────────────────────────────────────
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

            # ── 3. session_id + download_token 발급 ────────────────────
            sid            = str(uuid.uuid4())
            download_token = secrets.token_urlsafe(32)
            _evict_expired()
            _pdf_store[sid] = {
                "demand_path":   demand_pdf_path,
                "petition_path": petition_pdf_path,
                "download_token": download_token,
                "_ts":           time.time(),
            }

            return jsonify({"ok": True, "session_id": sid, "download_token": download_token})

        except Exception as e:
            logger.error("generate_package PDF 생성 오류: %s", e, exc_info=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)

            # ── retry_count 증가 (스레드 안전) ──────────────────────────
            with _orders_lock:
                if inv in _pending_orders:
                    new_retry = _pending_orders[inv].get("retry_count", 0) + 1
                    _pending_orders[inv]["retry_count"] = new_retry
                else:
                    new_retry = retry_count + 1

            # ── 1회 실패: 재시도 유도 ────────────────────────────────────
            if new_retry < 2:
                logger.warning("generate_package 1차 실패 — 재시도 유도: inv=%s", inv)
                return jsonify({
                    "error": "เกิดข้อผิดพลาดในการสร้างเอกสาร กรุณาลองใหม่อีกครั้ง",
                    "retry": True,
                }), 500

            # ── 2회 실패: 환불 + LINE 알림 ──────────────────────────────
            refunded  = False
            with _orders_lock:
                charge_id = _pending_orders.get(inv, {}).get("charge_id", "")
            if charge_id and OMISE_SECRET_KEY:
                try:
                    omise.api_secret = OMISE_SECRET_KEY
                    omise.Charge.retrieve(charge_id).refund(amount=PACKAGE_PRICE_SATANG)
                    refunded = True
                    logger.info("Omise 자동 환불 완료: charge=%s inv=%s", charge_id, inv)
                except Exception as refund_err:
                    logger.error("Omise 자동 환불 실패: charge=%s err=%s", charge_id, refund_err)

            _notify_admin(
                invoice=inv,
                name=case_data.get("complainant_name", "-"),
                error=str(e),
                refunded=refunded,
            )

            return jsonify({
                "error": "เกิดข้อผิดพลาดในการสร้างเอกสาร ระบบได้คืนเงินให้อัตโนมัติแล้ว กรุณาติดต่อ Line OA เพื่อขอความช่วยเหลือ",
                "retry": False,
                "refunded": refunded,
            }), 500

    except Exception as e:
        logger.error("generate_package 오류: %s", e, exc_info=True)
        return jsonify({"error": "패키지 생성 중 오류가 발생했습니다."}), 500


# ─────────────────────────────────────────────
#  PDF 다운로드 (download_token 인증 필수)
# ─────────────────────────────────────────────

@app.route("/download/demand-letter/<session_id>")
def download_demand_letter(session_id):
    token = request.args.get("token", "")
    entry = _pdf_store.get(session_id)
    if not entry:
        return jsonify({"error": "File not found or expired"}), 404
    stored_token = entry.get("download_token", "")
    if not stored_token or not _hmac.compare_digest(token, stored_token):
        logger.warning("PDF 다운로드 인증 실패: sid=%s ip=%s", session_id, request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 403
    if not os.path.exists(entry["demand_path"]):
        return jsonify({"error": "File not found"}), 404
    return send_file(
        entry["demand_path"],
        as_attachment=True,
        download_name="หนังสือบอกกล่าว.pdf",
        mimetype="application/pdf"
    )


@app.route("/download/petition/<session_id>")
def download_petition(session_id):
    token = request.args.get("token", "")
    entry = _pdf_store.get(session_id)
    if not entry:
        return jsonify({"error": "File not found or expired"}), 404
    stored_token = entry.get("download_token", "")
    if not stored_token or not _hmac.compare_digest(token, stored_token):
        logger.warning("PDF 다운로드 인증 실패: sid=%s ip=%s", session_id, request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 403
    if not os.path.exists(entry["petition_path"]):
        return jsonify({"error": "File not found"}), 404
    return send_file(
        entry["petition_path"],
        as_attachment=True,
        download_name="คร.7_คำร้อง.pdf",
        mimetype="application/pdf"
    )


# ─────────────────────────────────────────────
#  Omise 결제 연동
# ─────────────────────────────────────────────

@app.route("/create-payment", methods=["POST"])
@limiter.limit("5 per minute")
def create_payment():
    err = _verify_csrf()
    if err:
        return err

    body            = request.get_json(force=True) or {}
    token           = body.get("token", "")
    case_data       = body.get("case_data", {})
    analysis_result = body.get("analysis_result", {})

    if not token:
        return jsonify({"error": "token required"}), 400
    if not OMISE_SECRET_KEY:
        return jsonify({"error": "payment not configured"}), 503

    _evict_expired()
    inv = uuid.uuid4().hex[:16]
    with _orders_lock:
        _pending_orders[inv] = {
            "case_data":       case_data,
            "analysis_result": analysis_result,
            "_paid":           False,
            "_ts":             time.time(),
        }

    try:
        omise.api_secret = OMISE_SECRET_KEY

        base_url = APP_BASE_URL or request.url_root.rstrip("/")
        if base_url.startswith("http://"):
            base_url = "https://" + base_url[len("http://"):]

        return_uri = f"{base_url}/payment-return?inv={inv}"
        logger.info("[PAYMENT] return_uri=%s", return_uri)

        charge = omise.Charge.create(
            amount=PACKAGE_PRICE_SATANG,
            currency="thb",
            card=token,
            return_uri=return_uri,
            metadata={"invoice": inv},
        )

        with _orders_lock:
            if inv in _pending_orders:
                _pending_orders[inv]["charge_id"]     = charge.id
                _pending_orders[inv]["charge_status"] = charge.status
                _pending_orders[inv]["last_event"]    = "create_payment"

        logger.info("[PAYMENT] charge created: inv=%s status=%s", inv, charge.status)

        if charge.status == "successful":
            with _orders_lock:
                if inv in _pending_orders:
                    _pending_orders[inv]["_paid"] = True
            logger.info("Omise 즉시 결제 완료: %s", inv)
            return jsonify({"ok": True, "inv": inv, "paid": True})

        if getattr(charge, "authorize_uri", None):
            logger.info("Omise 3DS 리다이렉트: %s", inv)
            return jsonify({"ok": True, "inv": inv, "authorize_uri": charge.authorize_uri})

        logger.warning("Omise 결제 실패: %s status=%s", inv, charge.status)
        with _orders_lock:
            if inv in _pending_orders:
                _pending_orders[inv]["_paid"] = False

        return jsonify({
            "error": "การชำระเงินล้มเหลว",
            "status": charge.status
        }), 402

    except Exception as e:
        logger.error("create_payment 오류: %s", e, exc_info=True)
        return jsonify({
            "error": "เกิดข้อผิดพลาดระหว่างดำเนินการชำระเงิน"
        }), 500


@app.route("/webhook/omise", methods=["POST"])
def webhook_omise():
    """Omise 백엔드 웹훅 — charge 이벤트 수신.
    웹훅 body를 신뢰하지 않고, charge ID로 Omise API에 직접 조회하여 상태 확인.
    """
    try:
        event     = request.get_json(force=True) or {}
        event_key = event.get("key")

        if event_key not in ["charge.complete", "charge.create", "charge.failed", "charge.expired"]:
            return "OK", 200

        charge_id = (event.get("data") or {}).get("id", "")
        if not charge_id or not charge_id.startswith("chrg_"):
            logger.warning("Omise 웹훅: 유효하지 않은 charge id — %s", charge_id)
            return "OK", 200

        if not OMISE_SECRET_KEY:
            logger.error("Omise 웹훅: OMISE_SECRET_KEY 미설정")
            return "Error", 500

        omise.api_secret    = OMISE_SECRET_KEY
        verified_charge     = omise.Charge.retrieve(charge_id)
        metadata = getattr(verified_charge, "metadata", None)
        inv = ""
        if isinstance(metadata, dict):
            inv = metadata.get("invoice", "")
        elif metadata is not None:
            try:
                inv = metadata["invoice"]
            except (KeyError, TypeError):
                inv = ""

        if inv and inv in _pending_orders:
            with _orders_lock:
                if inv in _pending_orders:
                    _pending_orders[inv]["charge_id"]     = charge_id
                    _pending_orders[inv]["charge_status"] = verified_charge.status
                    _pending_orders[inv]["_paid"]         = (verified_charge.status == "successful")
            logger.info("Omise 웹훅 상태 저장: inv=%s status=%s", inv, verified_charge.status)
        elif inv:
            logger.warning("Omise 웹훅: inv=%s not in _pending_orders", inv)

        return "OK", 200
    except Exception as e:
        logger.error("webhook_omise 오류: %s", e, exc_info=True)
        return "Error", 400


@app.route("/payment-return")
def payment_return():
    """3DS 완료 후 브라우저 복귀 페이지"""
    csrf_token = _get_csrf_token()
    return render_template("index.html", omise_public_key=OMISE_PUBLIC_KEY, csrf_token=csrf_token)


@app.route("/get-order-data/<inv>")
@limiter.limit("30 per minute")
def get_order_data(inv):
    """결제 복귀 후 프론트에서 case_data/analysis_result 복원용 — charge_id 미노출"""
    entry = _pending_orders.get(inv)
    if not entry:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "paid":            entry.get("_paid", False),
        "status":          entry.get("charge_status"),
        "case_data":       entry.get("case_data"),
        "analysis_result": entry.get("analysis_result"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
