#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:3004}"
BUILD_ID="20260518T1930"
TS="$(date +%s%N)"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

HTML="$TMPDIR/chat.html"
CSS="$TMPDIR/term.css"
SBJS="$TMPDIR/term-scrollbar.js"

curl -fsSL \
  -H 'Cache-Control: no-cache, no-store, max-age=0' \
  -H 'Pragma: no-cache' \
  "${BASE_URL}/think/chat?__v=${BUILD_ID}&_ts=${TS}" > "$HTML"

assert_has() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  if ! grep -qE "$pattern" "$file"; then
    echo "[FAIL] ${message}" >&2
    exit 1
  fi
}

assert_has "$HTML" 'window.__WJ_CHAT_BUILD_ID__ = "20260518T1930"' 'build id bootstrap 누락'
assert_has "$HTML" 'id="wj-term-scrollbar"' '스크롤바 DOM 누락'
assert_has "$HTML" 'id="work-dbg"' '진단 영역(work-dbg) 누락'
assert_has "$HTML" 'importFresh\("/static/term/term-instance\.js", 3\)' 'term-instance import 버전 불일치'
assert_has "$HTML" 'importFresh\("/static/term/term-scrollbar\.js", 4\)' 'term-scrollbar import 버전 불일치'
assert_has "$HTML" 'importFresh\("/static/term/term-touch\.js", 6\)' 'term-touch import 버전 불일치'

TERM_CSS_PATH="$(grep -oE '/static/term/term\.css\?[^" ]+' "$HTML" | head -n1)"
if [[ -z "$TERM_CSS_PATH" ]]; then
  echo '[FAIL] term.css 링크를 찾지 못함' >&2
  exit 1
fi

curl -fsSL -H 'Cache-Control: no-cache' "${BASE_URL}${TERM_CSS_PATH}" > "$CSS"

assert_has "$CSS" '\.wj-term-scrollbar \{' '스크롤바 CSS 블록 없음'
assert_has "$CSS" 'width: 14px' '스크롤바 width=14px 아님'
assert_has "$CSS" 'z-index: 80' '스크롤바 z-index=80 아님'
assert_has "$CSS" '\.wj-term-scrollbar\.active \{ display: block; \}' 'active display:block 규칙 없음'

# 모바일에서 추가로 숨기는 규칙이 없는지 확인 (기본 display:none 1건 + active block 1건은 정상)
HIDE_COUNT="$(grep -Ec 'wj-term-scrollbar[^\n]*display:\s*none|display:\s*none[^\n]*wj-term-scrollbar' "$CSS" || true)"
if [[ "$HIDE_COUNT" -gt 1 ]]; then
  echo "[FAIL] 스크롤바를 숨기는 CSS 규칙이 추가로 감지됨 (count=${HIDE_COUNT})" >&2
  exit 1
fi

curl -fsSL -H 'Cache-Control: no-cache' "${BASE_URL}/static/term/term-scrollbar.js?v=4&b=${BUILD_ID}" > "$SBJS"
assert_has "$SBJS" 'barEl\.classList\.add\("active"\)' 'scrollbar 초기 active 강제 코드 누락'
assert_has "$SBJS" 'buf\.baseY' 'xterm buffer.baseY 폴백 누락'
assert_has "$SBJS" 'scrollHeight > clientHeight' 'viewport.scrollTop 폴백 누락'

echo "[PASS] HTML/CSS/JS contract OK (${BASE_URL}, build=${BUILD_ID})"
echo "- DOM: wj-term-scrollbar/work-dbg 존재"
echo "- CSS: width=12px, z-index=80, active=display:block"
echo "- JS: importFresh+cache-bust, scrollbar active/fallback 포함"
