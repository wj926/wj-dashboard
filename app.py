#!/usr/bin/env python3
"""wj-dashboard — Flask 서버.

GET  /                          → 페이지 렌더 (Bento 레이아웃)
GET  /api/version               → yaml mtime (polling)
POST /api/task                  → 자연어 텍스트 → task 생성
POST /api/task/<id>/done        → 완료 표시
POST /api/task/<id>/undo        → 완료 취소
POST /api/task/<id>/snooze      → due_at 미루기 (?days=N)
POST /api/task/<id>/update      → 임의 필드 갱신 (body json)

데이터: settings.WJ_DATA_PATH (기본: examples/dashboard.yaml)
"""
from __future__ import annotations

import json
import os
from datetime import date

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from settings import SETTINGS

import compute
import terminal_pty
import thinking_compute

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
# WebSocket keepalive — cloudflared/모바일망 idle 끊김 방지 (claude crunch 2m+ 구간 보호)
app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 25}
sock = Sock(app)

# 모드 배지를 모든 템플릿에서 접근 가능하게
app.jinja_env.globals["WJ_MODE"] = SETTINGS.mode_badge
app.jinja_env.globals["WJ_IS_DEMO"] = SETTINGS.is_demo


@app.before_request
def _require_password():
    # demo 모드는 공개 접근 허용 (홍보용)
    if SETTINGS.is_demo or not SETTINGS.auth_password:
        return None
    auth = request.authorization
    if auth and (auth.password or "") == SETTINGS.auth_password:
        return None
    from flask import Response
    return Response(
        "Auth required",
        401,
        {"WWW-Authenticate": 'Basic realm="wj-dashboard"'},
    )


@app.before_request
def _guard_terminal():
    # terminal 기능 끈 모드에서 WS/HTTP 둘 다 차단
    if SETTINGS.enable_terminal:
        return None
    path = request.path or ""
    if path.startswith("/ws/term") or path.startswith("/api/term") or path.startswith("/terminal"):
        from flask import Response
        return Response("terminal disabled in this deployment", 404)
    return None


@app.before_request
def _guard_chat():
    # chat 기능 끈 배포에서 Claude 챗 endpoint 차단 (설정과 실제 동작 일치)
    if SETTINGS.enable_chat:
        return None
    path = request.path or ""
    if path in ("/api/chat", "/api/think-chat"):
        from flask import Response
        return Response("chat disabled in this deployment", 404)
    return None


def _same_origin_host() -> bool:
    """state-changing 요청의 Origin/Referer 호스트가 우리 호스트와 같은지.
    정상 브라우저는 same-origin POST 에 Origin 을 보내고, 외부 공격 페이지는
    자기 Origin 을 달고 오므로 호스트 불일치로 걸러진다 (CSRF 방어)."""
    from urllib.parse import urlparse
    src = request.headers.get("Origin") or request.headers.get("Referer")
    if not src:
        return False
    return urlparse(src).netloc == request.host


@app.before_request
def _csrf_guard():
    # GET/HEAD/OPTIONS 는 상태를 안 바꾸므로 통과
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if _same_origin_host():
        return None
    from flask import Response
    return Response("CSRF: origin/referer check failed", 403)


@app.before_request
def _guard_ws_origin():
    # WebSocket 업그레이드(GET)는 _csrf_guard 를 통과하므로, /ws/ 는 별도로
    # 업그레이드 전 HTTP 레이어에서 Origin 호스트 일치를 강제 (교차 출처 hijack 차단)
    if (request.path or "").startswith("/ws/"):
        if not _same_origin_host():
            from flask import Response
            return Response("ws origin check failed", 403)
    return None

try:
    import markdown as _md
    _MD = _md.Markdown(extensions=["extra", "sane_lists", "nl2br"])
    def _render_md(text):
        if not text:
            return ""
        _MD.reset()
        return _MD.convert(text)
except Exception:
    def _render_md(text):
        return text or ""

app.jinja_env.filters["md"] = _render_md


@app.route("/favicon.ico")
def favicon():
    # 브라우저 자동 /favicon.ico 요청을 모든 페이지에서 한 번에 처리 (404 콘솔 에러 제거)
    return app.send_static_file("favicon.svg")


@sock.route("/ws/term")
def ws_term(ws):
    """PTY terminal — sid 기준으로 영속 세션 attach. sid 미지정 시 'global'.
    Origin 검증은 _guard_ws_origin(before_request) 에서 업그레이드 전에 처리."""
    sid = request.args.get("sid") or "global"
    terminal_pty.handle_ws(ws, sid=sid)


@app.route("/api/term/sessions")
def api_term_sessions():
    return jsonify(terminal_pty.list_sessions())


@app.post("/api/term/sessions")
def api_term_session_create():
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    res = terminal_pty.create_session(label=label)
    code = 200 if res.get("ok") else 400
    return jsonify(res), code


@app.delete("/api/term/sessions/<sid>")
def api_term_session_delete(sid: str):
    if not terminal_pty.valid_sid(sid):
        return jsonify({"ok": False, "error": "invalid sid"}), 400
    ok = terminal_pty.kill_tmux_session(sid)
    return jsonify({"ok": ok})


try:
    import bleach as _bleach
    _MD_ALLOWED_TAGS = list(_bleach.sanitizer.ALLOWED_TAGS) + [
        "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "div", "span",
        "pre", "table", "thead", "tbody", "tr", "th", "td", "img",
    ]
    _MD_ALLOWED_ATTRS = {
        "*": ["class", "id"], "a": ["href", "title"], "img": ["src", "alt", "title"],
    }
    def _sanitize_html(html: str) -> str:
        return _bleach.clean(html, tags=_MD_ALLOWED_TAGS, attributes=_MD_ALLOWED_ATTRS, strip=True)
except Exception:
    def _sanitize_html(html: str) -> str:
        return html


@app.get("/api/think/page-html")
def api_think_page_html():
    """MD 파일 path → 렌더된 HTML (bleach 로 sanitize). 탭 시스템의 file 탭에서 호출."""
    rel_path = (request.args.get("path") or "").strip()
    if not rel_path:
        return jsonify({"ok": False, "error": "path required"}), 400
    page = thinking_compute.get_page_by_path(rel_path)
    if not page:
        return jsonify({"ok": False, "error": "page not found"}), 404
    body_md = "\n\n".join(
        f"## {k}\n{v}" for k, v in (page.get("sections") or {}).items() if v
    ) or "(빈 페이지)"
    return jsonify({
        "ok": True,
        "title": page.get("title") or rel_path,
        "path": page.get("path") or rel_path,
        "category": page.get("category", ""),
        "html": _sanitize_html(_render_md(body_md)),
    })


@app.post("/api/term/sessions/<sid>/rename")
def api_term_session_rename(sid: str):
    if not terminal_pty.valid_sid(sid):
        return jsonify({"ok": False, "error": "invalid sid"}), 400
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "label required"}), 400
    terminal_pty.set_label(sid, label)
    return jsonify({"ok": True, "sid": sid, "label": label})


@app.post("/api/term/upload")
def api_term_upload():
    """파일/이미지 첨부 → thinking/uploads/. 응답: {path, name}."""
    import time as _t
    from pathlib import Path as _P
    from werkzeug.utils import secure_filename
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file"}), 400
    upload_dir = _P(str(SETTINGS.uploads_dir))
    upload_dir.mkdir(parents=True, exist_ok=True)
    ts = int(_t.time() * 1000)
    name = secure_filename(f.filename or "paste")
    if not name:
        name = "paste"
    final = upload_dir / f"{ts}-{name}"
    f.save(str(final))
    return jsonify({"ok": True, "path": str(final), "name": name})


@app.route("/")
def index():
    data = compute.load_yaml()
    today = date.today()
    # ?ym=YYYY-MM 으로 다른 달 보기
    ym = request.args.get("ym", "")
    view_year = view_month = None
    if ym and "-" in ym:
        try:
            y, m = ym.split("-")
            view_year, view_month = int(y), int(m)
            if not (1 <= view_month <= 12):
                view_year = view_month = None
        except ValueError:
            view_year = view_month = None
    view = compute.build_view(data, today, view_year, view_month)
    # JS 주입용
    view["by_day_json_str"] = json.dumps(view["by_day_json"], ensure_ascii=False)
    view["projects_json_str"] = json.dumps(view["projects_json"], ensure_ascii=False)
    view["inbox_json_str"] = json.dumps(view["inbox_json"], ensure_ascii=False)
    view["active_tab"] = "work"
    return render_template("index.html", **view)


@app.route("/think")
def think_index():
    """Spotlight 베이스 메인 (2026-05-17~). 옛 3-패널은 /think/legacy."""
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    h = thinking_compute.build_hierarchy_data()
    return render_template("think_spotlight.html",
                           today=today, data=data, h=h, active_tab="think")


@app.route("/think/legacy")
def think_index_legacy():
    today = date.today()
    idx = thinking_compute.build_index(today)
    return render_template("think_index.html", today=today, active_tab="think", **idx)


@app.route("/think/page/<path:rel_path>")
def think_page(rel_path: str):
    today = date.today()
    page = thinking_compute.get_page_by_path(rel_path)
    if not page:
        return "Page not found", 404
    idx = thinking_compute.build_index(today)
    decisions = thinking_compute.parse_decisions(page["sections"].get("decisions", ""))
    todos = thinking_compute.parse_todos(page["sections"].get("todos", ""))
    timeline = thinking_compute.parse_timeline(page["sections"].get("timeline", ""))
    backlinks = thinking_compute.get_page_backlinks(page, idx)
    raw_mentions = thinking_compute.get_page_raw_mentions(page)
    impl_stats = thinking_compute.page_impl_stats(decisions)
    return render_template(
        "think_page.html",
        raw_mentions=raw_mentions,
        impl_stats=impl_stats,
        page=page,
        decisions=decisions,
        todos=todos,
        timeline=timeline,
        backlinks=backlinks,
        today=today,
        active_tab="think",
    )


COMBOS = [
    {"n":1,"code":"Cockpit",    "tag":"운영 정석",       "desc":"6위젯 mosaic, 한눈에 전체 상황. 오전 10분 점검용.",
     "layout":"┌────┬────┬────┐\n│#6  │#2  │#5  │\n├────┼────┼────┤\n│#3  │#4  │#1  │\n└────┴────┴────┘"},
    {"n":2,"code":"Beacon",     "tag":"시간 흐름 hero",   "desc":"히트맵 풀스크린 + 우측 사이드 위젯 3개. 초보 친화.",
     "layout":"┌────────────┬──┐\n│            │#6│\n│  #2 Heat   ├──┤\n│   Hero     │#5│\n│            ├──┤\n│            │#3│\n└────────────┴──┘"},
    {"n":3,"code":"Delta",      "tag":"결정 흐름",         "desc":"Decision River 가 hero. 주간/월말 회고에 강함.",
     "layout":"┌────────────┬──┐\n│  #3 River  │#5│\n│   Hero     ├──┤\n│            │#6│\n├──────┬─────┴──┤\n│ #2   │  #4    │\n└──────┴────────┘"},
    {"n":4,"code":"Orbit",      "tag":"관계망 우선",       "desc":"Graph constellation hero. 장기 플랜/지식 구조.",
     "layout":"┌────────────┬──┐\n│            │#6│\n│  #1 Graph  ├──┤\n│   Hero     │#2│\n├──────┬─────┴──┤\n│ #3   │  #5    │\n└──────┴────────┘"},
    {"n":5,"code":"Funnel",     "tag":"진행도 직답",       "desc":"Pipeline hero + 4 보조. 실행 병목 파악.",
     "layout":"┌────────────────┐\n│  #5 Pipeline   │\n├───────┬────────┤\n│  #3   │  #2    │\n├───────┼────────┤\n│  #6   │  #4    │\n└───────┴────────┘"},
    {"n":6,"code":"Arbor",      "tag":"사이드바 탐색",     "desc":"좌 폴더트리 네비 + 우 메인. 페이지 깊게 들어가기.",
     "layout":"┌──┬─────────────┐\n│#4│  #2 + #6    │\n│  ├──────┬──────┤\n│  │ #3   │ #1   │\n│  ├──────┴──────┤\n│  │   #5        │\n└──┴─────────────┘"},
    {"n":7,"code":"Trinity",    "tag":"옵시디언 3패널",     "desc":"좌 탐색 / 중 본문 / 우 연결. 분석형 사용자.",
     "layout":"┌──┬────────┬──┐\n│#4│        │#1│\n│  │  #3    │+ │\n│  │ + #2   │#2│\n├──┴────────┴──┤\n│   #5 strip   │\n└──────────────┘"},
    {"n":8,"code":"Panorama",   "tag":"탭 전환",          "desc":"KPI 바 + 4탭(Today/History/Structure/Execution). 복잡도 제어.",
     "layout":"┌──────────────┐\n│  KPI bar     │\n├──────────────┤\n│ Tabs ▾       │\n├──────────────┤\n│ Tab content  │\n│   (조합)     │\n└──────────────┘"},
    {"n":9,"code":"Spotlight",  "tag":"단일 집중",        "desc":"Focus Cards 풀스크린 + 클릭 시 River 사이드 + 하단 Pipeline strip.",
     "layout":"┌────────────┬──┐\n│            │#3│\n│  #6 Hero   │  │\n│  Big Cards │  │\n├────────────┴──┤\n│  #5 strip     │\n└───────────────┘"},
    {"n":10,"code":"Compass",   "tag":"iPad 최적",        "desc":"좌 30% 빠른 상태 (Focus+Pipeline) / 우 70% 캔버스 (세그먼트 전환).",
     "layout":"┌──┬─────────────┐\n│#6│  Canvas     │\n│  │  ▾ #2/#3/   │\n│#5│    #1/#4    │\n│  │             │\n└──┴─────────────┘"},
    {"n":11,"code":"Mosaic",    "tag":"위젯 다수",         "desc":"작은 위젯 9개 격자. 운영자 다중 모니터링.",
     "layout":"┌──┬──┬──┬──┐\n│#2│#6│#6│#6│\n├──┴──┼──┴──┤\n│ #2  │ #3  │\n├──┬──┴┬────┤\n│#5│#4 │ #1 │\n└──┴───┴────┘"},
    {"n":12,"code":"Archive",   "tag":"회고 모드",        "desc":"히트맵 + Focus 변화 + 결정 폐기 필터 강조. 월말 회고.",
     "layout":"┌─────────┬────┐\n│ #2 180d │#6  │\n├─────────┴────┤\n│ #3 + filter  │\n├──────┬───────┤\n│ #4   │ #1    │\n└──────┴───────┘"},
]


@app.route("/combos")
def combos_index():
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    return render_template("combos/index.html", data=data, combos=COMBOS, active_tab="think")


@app.route("/combos/<int:n>")
def combo_n(n: int):
    if n < 1 or n > len(COMBOS):
        return "out of range", 404
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    meta = COMBOS[n-1]
    return render_template(f"combos/c{n:02d}_{meta['code'].lower()}.html",
                           data=data, meta=meta, combos=COMBOS, n=n, active_tab="think")


CHAT_SPOTS = [
    {"id":"a","name":"우측 탭 토글",   "desc":"River 자리에 [River|Chat] 탭. 평소엔 River, 클릭하면 Chat. 양쪽 균형."},
    {"id":"b","name":"하단 sticky 바",  "desc":"Pipeline strip 위에 항상 보이는 textarea. 막 던지기 최적, 흐름 안 끊김."},
    {"id":"c","name":"떠다니는 모달",   "desc":"우하단 ↗ floating 버튼 → 모달. 평소 화면 침범 0, 호출 1클릭."},
    {"id":"d","name":"우측 drawer",     "desc":"우측 슬라이드 패널. iPad/PC 둘 다 자연스러움. 본문과 공존."},
    {"id":"e","name":"상단 hero 입력",  "desc":"Focus 카드 위에 큰 textarea + 최근 raw 미리보기. '오늘 뭐 던질래?' 초대형."},
]


@app.route("/chat-spots")
def chat_spots_index():
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    return render_template("chat_spots/index.html", data=data, spots=CHAT_SPOTS, active_tab="think")


@app.route("/chat-spots/<variant>")
def chat_spot_variant(variant: str):
    valid = [s["id"] for s in CHAT_SPOTS]
    if variant not in valid:
        return "not found", 404
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    spot = next(s for s in CHAT_SPOTS if s["id"] == variant)
    return render_template(f"chat_spots/cs_{variant}.html", data=data, spot=spot, spots=CHAT_SPOTS, active_tab="think")


@app.route("/mockups")
def mockups_index():
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    return render_template("mockups/index.html", data=data, active_tab="think")


@app.route("/mockups/<int:n>")
def mockup_n(n: int):
    if n < 1 or n > 6:
        return "out of range", 404
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    tpl = {
        1: "mockups/m1_graph.html",
        2: "mockups/m2_heatmap.html",
        3: "mockups/m3_river.html",
        4: "mockups/m4_tree.html",
        5: "mockups/m5_pipeline.html",
        6: "mockups/m6_focus.html",
    }[n]
    return render_template(tpl, data=data, n=n, active_tab="think")


@app.route("/build-tracker")
def build_tracker():
    """구현 추적 mockup — thinking 결정 ↔ 실제 구현 매핑."""
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    impl = thinking_compute.build_implementation_tracker()
    return render_template("mockups/build_tracker.html", data=data, impl=impl, active_tab="think")


TREE_PATTERNS = [
    {"id":"spine",  "name":"Decision Spine",   "tag":"결정 중심",   "desc":"페이지 → 결정 → 증거(코드/PR/테스트). 의사결정과 구현의 인과 시각화."},
    {"id":"ladder", "name":"Milestone Ladder", "tag":"단계 중심",   "desc":"페이지 → MVP/V2/V3 단계 → 결정. 로드맵·우선순위 회의에 강함."},
    {"id":"cap",    "name":"Capability Map",   "tag":"도메인 중심", "desc":"페이지 → 기능군(UI/검색/챗봇/추적) → 결정. 어느 영역이 병목인지."},
    {"id":"chrono", "name":"Chrono Tree",      "tag":"시간 중심",   "desc":"날짜 → 그 날의 결정·구현 이벤트. 데일리/주간 리뷰."},
    {"id":"twin",   "name":"Twin Tree",        "tag":"⭐ 토글",     "desc":"같은 데이터를 '결정 중심' ↔ '단계 중심' 토글. Codex 최종 추천."},
]


@app.route("/tree-trackers")
def tree_trackers_index():
    tree = thinking_compute.build_tree_data()
    return render_template("tree_trackers/index.html", tree=tree, patterns=TREE_PATTERNS, active_tab="think")


@app.route("/tree-trackers/<variant>")
def tree_tracker_variant(variant: str):
    valid = [p["id"] for p in TREE_PATTERNS]
    if variant not in valid:
        return "not found", 404
    tree = thinking_compute.build_tree_data()
    pat = next(p for p in TREE_PATTERNS if p["id"] == variant)
    return render_template(f"tree_trackers/tt_{variant}.html",
                           tree=tree, pat=pat, patterns=TREE_PATTERNS, active_tab="think")


TREE_VIS = [
    {"id":"org",      "name":"Org Chart",       "tag":"수직 박스 트리",  "desc":"위→아래 조직도 스타일. 박스 노드 + 연결선. CSS만으로 깔끔."},
    {"id":"htree",    "name":"Horizontal Tree", "tag":"좌→우 펼침",     "desc":"d3 cluster layout. 진짜 트리 다이어그램. 결정 가지가 옆으로."},
    {"id":"sunburst", "name":"Sunburst",        "tag":"방사형",         "desc":"중심에서 바깥으로. 면적 = 결정 수. 한눈에 비중 비교."},
    {"id":"mindmap",  "name":"Mind Map",        "tag":"자연 배치",       "desc":"d3 force layout. 페이지가 중심, 결정이 위성. 드래그 가능."},
    {"id":"treemap",  "name":"Treemap",         "tag":"면적 비율",       "desc":"네모 면적 = 그 영역 크기. 한눈에 무게 중심."},
]


@app.route("/tree-vis")
def tree_vis_index():
    h = thinking_compute.build_hierarchy_data()
    return render_template("tree_vis/index.html", h=h, vis_list=TREE_VIS, active_tab="think")


@app.route("/tree-vis/<variant>")
def tree_vis_variant(variant: str):
    valid = [v["id"] for v in TREE_VIS]
    if variant not in valid:
        return "not found", 404
    h = thinking_compute.build_hierarchy_data()
    meta = next(v for v in TREE_VIS if v["id"] == variant)
    return render_template(f"tree_vis/v_{variant}.html",
                           h=h, meta=meta, vis_list=TREE_VIS, active_tab="think")


_LABEL_CACHE = {"result": None, "ts": 0}


@app.route("/label-decisions")
def label_decisions_page():
    """30개 결정 자동 분류 UI."""
    return render_template("label_decisions.html", active_tab="think")


@app.post("/api/classify-decisions")
def api_classify_decisions():
    """Claude Opus 자동 분류 (시간 1-2분)."""
    import time
    res = thinking_compute.classify_decisions_with_claude(timeout=180)
    if res.get("ok"):
        _LABEL_CACHE["result"] = res
        _LABEL_CACHE["ts"] = time.time()
    return jsonify(res)


@app.post("/api/apply-impl-labels")
def api_apply_impl_labels():
    """확정된 라벨 일괄 md 적용. body: {labels: [{page_path, decision_raw, status}, ...]}"""
    body = request.get_json(silent=True) or {}
    labels = body.get("labels") or []
    results = []
    for lab in labels:
        r = thinking_compute.apply_impl_label_to_file(
            lab.get("page_path", ""),
            lab.get("decision_raw", ""),
            lab.get("status", "planned"),
        )
        results.append(r)
    ok_n = sum(1 for r in results if r.get("ok"))
    return jsonify({"ok": True, "applied": ok_n, "total": len(results), "results": results})


@app.route("/today")
def today_journey():
    """오늘 대화 흐름 정리 페이지 — 사용자 요청 → 산출 mockup 매핑."""
    return render_template("today_journey.html", active_tab="think")


@app.post("/api/think/page/auto-mark")
def api_think_auto_mark():
    """페이지 결정들의 구현 상태를 Claude 가 자동 판단해 .md 갱신.
    body: {rel_path, hint?}.
    """
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("rel_path") or "").strip()
    hint = (body.get("hint") or "").strip()
    if not rel_path:
        return jsonify({"ok": False, "error": "rel_path required"}), 400
    result = thinking_compute.auto_mark_page(rel_path, hint)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.post("/api/think/decision/state")
def api_think_decision_state():
    """결정 구현 상태 토글. body: {rel_path, dec_hash, status}."""
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("rel_path") or "").strip()
    dec_hash = (body.get("dec_hash") or "").strip()
    status = (body.get("status") or "").strip()
    if not (rel_path and dec_hash and status):
        return jsonify({"ok": False, "error": "rel_path, dec_hash, status required"}), 400
    result = thinking_compute.update_decision_impl(rel_path, dec_hash, status)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.route("/think/page/<path:rel_path>/edit")
def think_page_edit(rel_path: str):
    """페이지 원본 .md 편집 화면."""
    page = thinking_compute.get_page_by_path(rel_path)
    if not page:
        return "Page not found", 404
    from pathlib import Path as _P
    body = _P(page["abs_path"]).read_text(encoding="utf-8")
    return render_template("think_edit.html", page=page, body=body, rel_path=rel_path, active_tab="think")


@app.post("/api/think/page/save")
def api_think_page_save():
    """페이지 .md 덮어쓰기. yaml frontmatter 검증 + 1회 백업."""
    import yaml as _yaml
    import re as _re
    import shutil as _sh
    import os as _os
    from pathlib import Path as _P
    body = request.get_json(silent=True) or {}
    rel_path = (body.get("rel_path") or "").strip()
    content = body.get("content")
    if not rel_path or content is None:
        return jsonify({"ok": False, "error": "rel_path, content required"}), 400
    base = _P(thinking_compute.THINKING_ROOT).resolve()
    target = (base / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return jsonify({"ok": False, "error": "path traversal"}), 400
    if not target.exists() or target.suffix != ".md":
        return jsonify({"ok": False, "error": "page not found"}), 404
    m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", content, _re.DOTALL)
    if m:
        try:
            _yaml.safe_load(m.group(1))
        except _yaml.YAMLError as e:
            return jsonify({"ok": False, "error": f"frontmatter yaml 오류: {e}"}), 400
    bak = target.with_suffix(target.suffix + ".bak")
    _sh.copy2(target, bak)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    _os.replace(tmp, target)
    return jsonify({"ok": True, "backup": bak.name})


@app.route("/think/file/<path:rel_path>")
def think_file(rel_path: str):
    """thinking/wiki/... 안 정적 파일(첨부 이미지 등) 서빙. path traversal 차단."""
    from flask import send_file
    from pathlib import Path as _P
    base = _P(thinking_compute.THINKING_ROOT).resolve()
    target = (base / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return "bad path", 400
    if not target.exists() or not target.is_file():
        return "not found", 404
    return send_file(str(target))


@app.route("/think/raw/<date_str>")
def think_raw(date_str: str):
    """raw/YYYY-MM-DD.md 원본 보기."""
    import re as _re
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return "bad date", 400
    p = thinking_compute.RAW_ROOT / f"{date_str}.md"
    if not p.exists():
        return "not found", 404
    text = p.read_text(encoding="utf-8")
    return render_template("think_raw.html", date_str=date_str, body=text, active_tab="think")


def _related_tasks(page_title: str | None) -> dict:
    """우 사이드 '관련 업무 대시보드' 데이터.
    page_title 이 있으면 source_thinking 매핑 + 본문에 그 title 언급된 task 우선.
    없으면 전역 — 마감 임박/진행/최근 완료.
    """
    from datetime import timedelta
    data = compute.load_yaml()
    tasks = data.get("tasks") or []
    projects = {p["id"]: p for p in (data.get("projects") or [])}
    today = date.today()

    def proj_name(t):
        return (projects.get(t.get("project_id")) or {}).get("title") or "Inbox"

    def days_left(t):
        d = t.get("due_at")
        if not d:
            return None
        if isinstance(d, str):
            try:
                d = date.fromisoformat(d[:10])
            except Exception:
                return None
        return (d - today).days

    related = []
    if page_title:
        for t in tasks:
            if t.get("source_thinking") == page_title or (page_title in (t.get("note") or "")):
                related.append(t)

    todo_tasks = [t for t in tasks if t.get("status") == "todo"]
    urgent = sorted([t for t in todo_tasks if days_left(t) is not None and days_left(t) <= 3],
                    key=lambda t: days_left(t))
    upcoming = sorted([t for t in todo_tasks if days_left(t) is not None and 3 < days_left(t) <= 14],
                      key=lambda t: days_left(t))
    no_due = [t for t in todo_tasks if days_left(t) is None]
    recent_done = sorted([t for t in tasks if t.get("status") == "done" and t.get("done_at")],
                         key=lambda t: str(t.get("done_at")), reverse=True)[:5]

    def serialize(t):
        d = t.get("due_at")
        return {
            "id": t["id"],
            "title": t["title"],
            "project": proj_name(t),
            "due_at": d.isoformat() if hasattr(d, "isoformat") else (d or None),
            "days_left": days_left(t),
            "note": (t.get("note") or "")[:120],
            "status": t["status"],
        }
    return {
        "related": [serialize(t) for t in related],
        "urgent": [serialize(t) for t in urgent],
        "upcoming": [serialize(t) for t in upcoming],
        "no_due": [serialize(t) for t in no_due],
        "recent_done": [serialize(t) for t in recent_done],
        "total_todo": len(todo_tasks),
    }


@app.route("/think/chat")
def think_chat_global():
    """전역 풀스크린 대화."""
    today = date.today()
    idx = thinking_compute.build_index(today)
    return render_template("chat/page.html",
                           mode="global", page=None, idx=idx,
                           active_tab="think")


@app.route("/think/chat/page/<path:rel_path>")
def think_chat_context(rel_path: str):
    """컨텍스트 (특정 페이지) 풀스크린 대화."""
    today = date.today()
    page = thinking_compute.get_page_by_path(rel_path)
    if not page:
        return "Page not found", 404
    idx = thinking_compute.build_index(today)
    decisions = thinking_compute.parse_decisions(page["sections"].get("decisions", ""))
    todos = thinking_compute.parse_todos(page["sections"].get("todos", ""))
    timeline = thinking_compute.parse_timeline(page["sections"].get("timeline", ""))
    return render_template("chat/page.html",
                           mode="context", page=page, decisions=decisions,
                           todos=todos, timeline=timeline, idx=idx,
                           active_tab="think")


@app.route("/api/version")
def version():
    try:
        st = os.stat(compute.DATA_PATH)
        return jsonify({"mtime": st.st_mtime, "size": st.st_size})
    except FileNotFoundError:
        return jsonify({"error": "data not found"}), 404


@app.post("/api/task/parse")
def task_parse():
    """[legacy regex] 컨펌용 미리보기. 저장 X."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    today = date.today()
    return jsonify(compute.preview_task_from_text(text, today))


@app.post("/api/think-chat")
def think_chat_api():
    """thinking 챗봇 — 자연어 대화. 사용자 발화는 raw 에 자동 append."""
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    if not messages:
        return jsonify({"ok": False, "error": "messages required"}), 400
    # 마지막 user 메시지 raw append
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    raw_info = None
    if last_user and last_user.get("content"):
        raw_info = thinking_compute.append_raw(last_user["content"])
    result = thinking_compute.think_chat(messages)
    result["raw"] = raw_info
    return jsonify(result)


@app.post("/api/chat")
def chat():
    """진짜 챗봇 — Claude Opus 호출.
    body: {messages: [{role:'user'|'assistant', content:str}, ...]}
    response: claude 의 JSON {reply, task, needs_clarification, questions, ok}
    """
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    if not messages:
        return jsonify({"ok": False, "error": "messages required"}), 400
    data = compute.load_yaml()
    projects = data.get("projects") or []
    today = date.today()
    result = compute.claude_chat(messages, today, projects)
    return jsonify(result)


@app.post("/api/task/commit")
def task_commit():
    """미리보기 컨펌 → 실제 저장."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    due_at = body.get("due_at") or None
    project_id = body.get("project_id") or None
    note = body.get("note") or ""
    return jsonify(compute.commit_task(title, due_at, project_id, note))


@app.post("/api/task")
def create_task():
    """1발 등록 (parse + commit). 호환용."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    today = date.today()
    return jsonify(compute.create_task_from_text(text, today))


@app.post("/api/task/<task_id>/done")
def task_done(task_id: str):
    today = date.today()
    result = compute.mark_done(task_id, today)
    code = 200 if result.get("ok") else 404
    return jsonify(result), code


@app.post("/api/task/<task_id>/undo")
def task_undo(task_id: str):
    result = compute.update_task(task_id, {"status": "todo", "done_at": None})
    code = 200 if result.get("ok") else 404
    return jsonify(result), code


@app.post("/api/task/<task_id>/snooze")
def task_snooze(task_id: str):
    days = int(request.args.get("days", "1"))
    result = compute.snooze(task_id, days)
    code = 200 if result.get("ok") else 404
    return jsonify(result), code


@app.post("/api/task/<task_id>/update")
def task_update(task_id: str):
    body = request.get_json(silent=True) or {}
    result = compute.update_task(task_id, body)
    code = 200 if result.get("ok") else 404
    return jsonify(result), code


if __name__ == "__main__":
    app.run(host=SETTINGS.host, port=SETTINGS.port, debug=False)
