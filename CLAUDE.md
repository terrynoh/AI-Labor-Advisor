# AI Labor Advisor

## 개요
Thai labor law AI chatbot (Python/Flask)
배포: Render
API: Claude Haiku (Anthropic)

## 구조
- app.py: Flask 라우팅
- chatbot.py: 대화 로직 + 세션 관리
- calculators.py: 퇴직금/연차 계산
- templates/index.html: 챗봇 UI

## 챗봇 흐름
1. 상황 파악 (해고/자발적퇴사/임금체불)
2. 필요 정보 순차 질문
3. 계산 결과 + 법적 설명
4. คร.7 진정서 안내

## 기술스택
- Python 3.11.9 / Flask / Gunicorn
- GitHub + Render 자동배포

## 주의사항
- Thai 인코딩 UTF-8 필수
- API 키 절대 하드코딩 금지
- max 20 turns/session
- 500자 입력 제한