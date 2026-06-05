#!/usr/bin/env bash
# 이메일 탭 하네스 한 방 실행 — pytest + 정적검사 + 시각캡처.
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${WJ_PY:-/home/dami/miniconda3/bin/python}"
export WJ_MODE="${WJ_MODE:-demo}"
export WJ_EMAIL_BACKEND="${WJ_EMAIL_BACKEND:-fake}"
export WJ_EMAIL_LLM_BACKEND="${WJ_EMAIL_LLM_BACKEND:-fake}"
export WJ_EMAIL_STATE_PATH="${WJ_EMAIL_STATE_PATH:-/tmp/wj-email-state.json}"

echo "== [1/3] pytest =="
"$PY" -m pytest tests/ -q || exit 1
echo "== [2/3] static =="
bash scripts/check_email_static.sh || exit 1
echo "== [3/3] capture =="
bash scripts/capture_email.sh || exit 1
echo "ALL PASS"
