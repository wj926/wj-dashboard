#!/usr/bin/env bash
# 이메일 탭 정적 정책 검사 (grep 기반 — rg 미설치 환경 대비).
# 스코프: 내가 만드는 email_* 파일에만. 레거시 wj앱 파일(dashboard.js 등)은 제외.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

EMAIL_FILES="templates/email_focus.html templates/_email_center.html templates/email_receipts.html templates/_email_panel_draft.html templates/_email_panel_cal.html templates/_email_panel_rules.html templates/_email_later.html templates/_email_queue_items.html email_data.py"
for f in gmail.py calendar_write.py llm_email.py email_services.py email_fake.py email_compute.py email_view.py email_score.py email_events.py email_rules.py wj_events.py email_filters.py email_cache.py email_store.py; do
  [ -f "$f" ] && EMAIL_FILES="$EMAIL_FILES $f"
done
echo "[check_email_static] 대상: $EMAIL_FILES"

# 1. repo 추적 비밀/토큰/상태 파일 금지
if git ls-files | grep -E '(^|/)(google_client_secret|client_secret|google_token|token|email_state)\.json$'; then
  echo "  [FAIL] repo 에 비밀/토큰 파일 추적됨"; fail=1
else echo "  [ok] repo 비밀/토큰 파일 없음"; fi

# 2. 외부 CDN 금지 (이메일 템플릿)
if grep -nE 'https?://(cdn|unpkg|jsdelivr|cdnjs|fonts\.googleapis|fonts\.gstatic|d3js\.org)' templates/email_focus.html; then
  echo "  [FAIL] 외부 CDN 참조"; fail=1
else echo "  [ok] 외부 CDN 없음"; fi

# 3. em-dash / 화살표 글리프 금지
if grep -nP '[\x{2014}\x{2190}-\x{21FF}]' $EMAIL_FILES; then
  echo "  [FAIL] 금지 글리프(em-dash/화살표)"; fail=1
else echo "  [ok] 금지 글리프 없음"; fi

# 4. 긍정형 자동 발송/등록 문구 금지 (승인 게이트 보호)
if grep -nP '(자동\s*(발송|전송|등록)|자동발송|알아서\s*(발송|전송|등록)|메일을 보내겠습니다|캘린더에 자동)' $EMAIL_FILES; then
  echo "  [FAIL] 자동 발송/등록 오해 문구"; fail=1
else echo "  [ok] 자동 발송/등록 문구 없음"; fi

# 5. |safe 는 body_html 에만 (Gmail 원본 sanitize 전제)
if grep -n '|safe' templates/email_focus.html | grep -v 'body_html'; then
  echo "  [FAIL] body_html 외 |safe 사용"; fail=1
else echo "  [ok] |safe 는 body_html 에만"; fi

if [ "$fail" -eq 0 ]; then echo "[check_email_static] PASS"; else echo "[check_email_static] FAIL"; exit 1; fi
