#!/usr/bin/env bash
# 이메일 탭 시각 검수 — test client 로 정적 렌더 후 file:// 1400px 캡처.
# (headless chrome 가 이 환경에서 http localhost 로 불안정해 render->file 방식 사용.)
# 산출물 _shots/email/ 은 .gitignore 처리(커밋 안 함).
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${WJ_PY:-/home/dami/miniconda3/bin/python}"
export WJ_MODE="${WJ_MODE:-demo}"
export WJ_EMAIL_BACKEND="${WJ_EMAIL_BACKEND:-fake}"
export WJ_EMAIL_LLM_BACKEND="${WJ_EMAIL_LLM_BACKEND:-fake}"
export WJ_EMAIL_STATE_PATH="${WJ_EMAIL_STATE_PATH:-/tmp/wj-email-state.json}"

mkdir -p _shots/email
RENDER="/tmp/wj-email-render/email.html"
"$PY" scripts/render_email_static.py --out "$RENDER" || { echo "[capture] 렌더 실패"; exit 1; }

timeout 50 google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1400,1700 --screenshot="_shots/email/email-1400.png" \
  "file://$RENDER" >/dev/null 2>&1

if [ -s _shots/email/email-1400.png ]; then
  echo "[capture] OK -> _shots/email/email-1400.png"
  ls -la _shots/email/email-1400.png
else
  echo "[capture] 스크린샷 생성 실패"; exit 1
fi
