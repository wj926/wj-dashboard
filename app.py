#!/usr/bin/env python3
"""wj-dashboard — Flask 서버.

GET  /                          → 페이지 렌더 (Bento 레이아웃)
GET  /api/version               → yaml mtime (polling)
POST /api/task                  → 자연어 텍스트 → task 생성
POST /api/task/<id>/done        → 완료 표시
POST /api/task/<id>/undo        → 완료 취소
POST /api/task/<id>/snooze      → due_at 미루기 (?days=N)
POST /api/task/<id>/update      → 임의 필드 갱신 (body json)
POST /api/project               → 프로젝트 생성
POST /api/project/<id>/update   → 프로젝트 수정
POST /api/project/<id>/archive  → 프로젝트 보관
DELETE /api/project/<id>        → 연결 task 없는 프로젝트 삭제

데이터: settings.WJ_DATA_PATH (기본: examples/dashboard.yaml)
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from settings import SETTINGS

import compute
import gcal
import terminal_pty
import thinking_compute
import autowiki
import email_view

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
    if path == "/api/chat":
        from flask import Response
        return Response("chat disabled in this deployment", 404)
    return None


def _same_origin_host() -> bool:
    """state-changing 요청이 same-origin 인지 (CSRF 방어).
    1차: Sec-Fetch-Site (모든 모던 브라우저가 보냄, iPad Safari 16.4+ 포함) —
         same-origin/same-site/none 허용, cross-site/cross-origin 차단.
    폴백: 헤더 없는 구형 클라이언트는 Origin/Referer netloc == request.host 비교."""
    from urllib.parse import urlparse
    sfs = request.headers.get("Sec-Fetch-Site")
    if sfs:
        return sfs in ("same-origin", "same-site", "none")
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
    # 구글 캘린더 읽기 전용 오버레이 + wj 자체 이벤트(승인한 이메일 일정) 합치기 (실패해도 빈 값)
    try:
        by_day = gcal.events_by_day(today, view_year, view_month)
        agenda = gcal.agenda(today)
        try:
            import wj_events
            for iso, evs in wj_events.events_by_day(today, view_year, view_month).items():
                by_day.setdefault(iso, []).extend(evs)
                by_day[iso].sort(key=lambda e: ((e.get("time") is None), e.get("time") or ""))
            agenda = sorted(
                agenda + wj_events.agenda(today),
                key=lambda e: (e.get("iso") or "", e.get("time") is None, e.get("time") or ""),
            )[:25]
        except Exception:
            pass
        view["gcal_by_day"] = by_day
        view["gcal_agenda"] = agenda
    except Exception:
        view["gcal_by_day"] = {}
        view["gcal_agenda"] = []
    # JS 주입용: 날짜별 구글 일정(시간/제목) 맵
    gcal_js = {}
    for iso, evs in (view.get("gcal_by_day") or {}).items():
        gcal_js[iso] = [{"time": e.get("time"), "title": e.get("title")} for e in (evs or [])]
    view["gcal_json_str"] = json.dumps(gcal_js, ensure_ascii=False)
    return render_template("index.html", **view)


@app.route("/think")
def think_index():
    """Spotlight 베이스 메인 (2026-05-17~). 옛 3-패널은 /think/legacy."""
    today = date.today()
    data = thinking_compute.build_mockup_data(today)
    h = thinking_compute.build_hierarchy_data()
    return render_template("think_spotlight.html",
                           today=today, data=data, h=h, active_tab="think")


@app.route("/email")
def email_page():
    """이메일 탭 (M4 포커스 단일메일).

    email_view.build_email_view 가 WJ_EMAIL_BACKEND(기본 fake) 에 따라 목업/실 Gmail
    view 를 만든다. 실패/미연동이어도 안전 view(is_mock/needs_auth)로 200 을 렌더한다.
    발송/등록은 항상 승인 게이트(이 라우트는 읽기 전용).
    """
    view = email_view.build_email_view(
        selected_id=request.args.get("id"),
        query=request.args.get("q"),
        sort=request.args.get("sort") or "priority",
        unread_only=(request.args.get("unread") == "1"),
        allow_fallback=True,  # 읽기 전용 페이지: 오래된 링크면 큐 첫 메일로 매끄럽게
    )
    view["active_tab"] = "email"
    # 큐 컨트롤 초기 상태(URL 파라미터와 일치시켜 다음 갱신에서 필터가 떨어지지 않게)
    view["q"] = request.args.get("q") or ""
    view["sort"] = request.args.get("sort") or "priority"
    view["unread_only"] = (request.args.get("unread") == "1")
    import email_persona
    view["persona"] = email_persona.load()
    # 모바일이면 device-width viewport + 세로 스택 레이아웃(데스크톱은 그대로 width=1400).
    ua = request.headers.get("User-Agent", "")
    view["is_mobile"] = ("Mobi" in ua) or ("Android" in ua) or ("iPhone" in ua)
    return render_template("email_focus.html", **view)


@app.route("/api/email/center")
def email_center():
    """큐 메일 클릭 시 중앙 본문만 부분 렌더(AJAX, 새로고침 없이 교체)."""
    view = email_view.build_email_view(selected_id=request.args.get("id"), allow_fallback=True)
    return render_template("_email_center.html", focus=view.get("focus") or {})


@app.route("/api/email/message-view")
def email_message_view():
    """메일 전환용: center + draft + cal 을 build_email_view 1회로 묶어 반환.

    기존엔 클릭 시 center/draft-pane/cal-pane 3개 요청(=터널 왕복 3회 + 큐 3회 재빌드)이
    나갔다. 이걸 1회로 합쳐 체감 지연을 줄인다.
    """
    mid = request.args.get("id")
    view = email_view.build_email_view(selected_id=mid, allow_fallback=True)
    focus = view.get("focus") or {}
    fid = focus.get("id") or mid
    return jsonify({
        "center": render_template("_email_center.html", focus=focus),
        "draft": render_template("_email_panel_draft.html",
                                 draft=view.get("draft") or {"status": "none"}, focus_id=fid),
        "cal": render_template("_email_panel_cal.html",
                               candidates=view.get("candidates") or [], focus_id=fid),
    })


@app.post("/api/email/messages/<mid>/hide")
def email_hide(mid):
    """이 메일을 wj 화면에서만 숨긴다. 실제 Gmail 은 건드리지 않는다."""
    import email_store
    email_store.hide(mid)
    return jsonify({"ok": True})


@app.post("/api/email/messages/<mid>/unhide")
def email_unhide(mid):
    """숨김 되돌리기(다시 큐에 표시). Gmail 무관."""
    import email_store
    email_store.unhide(mid)
    return jsonify({"ok": True})


@app.post("/api/email/messages/<mid>/exclude-sender")
def email_exclude_sender(mid):
    """이 발신자를 앞으로 안 보이게(제외목록 추가) + 지금 화면에서도 숨김. Gmail 무변경."""
    import email_store
    import email_filters
    focus = email_view.build_email_view(selected_id=mid).get("focus") or {}
    sender_email = focus.get("sender_email") or ""
    if sender_email:
        email_filters.add_exclude_sender(sender_email)
    email_store.hide(mid)
    return jsonify({"ok": True, "sender": sender_email})


@app.post("/api/email/messages/<mid>/save-receipt")
def email_save_receipt(mid):
    """이 메일을 영수증으로 보관(로컬 스냅샷 + PDF 첨부 파일 저장). Gmail 무변경."""
    import time as _time
    import email_store
    import email_services
    focus = email_view.build_email_view(selected_id=mid).get("focus") or {}
    if not focus.get("id"):
        return jsonify({"ok": False, "error": "not found"}), 404

    # PDF 첨부가 있으면 그 파일을 따로 저장한다(readonly 로 다운로드, Gmail 무변경).
    files = []
    try:
        services = email_services.get_email_services()
        for a in services.gmail.list_attachments(mid):
            fn = (a.get("filename") or "")
            mime = (a.get("mime") or "")
            if mime == "application/pdf" or fn.lower().endswith(".pdf"):
                data = services.gmail.download_attachment(mid, a.get("attachment_id"))
                if data:
                    rec = email_store.save_receipt_file(mid, fn, data)
                    if rec:
                        files.append(rec)
    except Exception:
        pass

    email_store.add_receipt({
        "id": focus.get("id"),
        "sender": focus.get("sender"),
        "subject": focus.get("subject"),
        "time": focus.get("time"),
        "saved_at": int(_time.time()),
        "body_html": focus.get("body_html") or "",
        "files": files,
    })
    return jsonify({"ok": True, "pdf_count": len(files)})


@app.route("/email/receipts/<rid>/file/<int:idx>")
def email_receipt_file(rid, idx):
    """보관한 영수증 첨부(PDF) 다운로드. 저장 레코드의 경로로만 서빙(traversal 방지)."""
    from flask import send_file, abort
    import email_store
    path = email_store.receipt_file_path(rid, idx)
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=False, download_name=os.path.basename(path))


@app.route("/email/receipts")
def email_receipts_page():
    """보관한 영수증함."""
    import email_store
    return render_template(
        "email_receipts.html", receipts=email_store.receipts(), active_tab="email"
    )


@app.post("/api/email/receipts/<rid>/remove")
def email_receipt_remove(rid):
    import email_store
    email_store.remove_receipt(rid)
    return jsonify({"ok": True})


@app.route("/api/email/draft-pane")
def email_draft_pane():
    """우측 'AI 초안' 패널 부분 렌더(메일 클릭/초안 생성 후 재렌더용)."""
    mid = request.args.get("id")
    view = email_view.build_email_view(selected_id=mid)
    return render_template(
        "_email_panel_draft.html",
        draft=view.get("draft") or {"status": "none"},
        focus_id=(view.get("focus") or {}).get("id") or mid,
    )


@app.post("/api/email/messages/<mid>/draft/generate")
def email_draft_generate(mid):
    """답장 초안 생성(LLM). 발송 아님. Gmail 에 아무것도 안 만든다.

    오직 이 라우트(=버튼 클릭)에서만 LLM 을 호출한다(인박스 로딩은 호출 안 함).
    결과는 로컬(email_store)에만 'unsent' 로 저장. 발송은 S3b 의 별도 승인 라우트.
    """
    import email_store
    import email_services
    data = request.get_json(silent=True) or {}
    tone = (data.get("tone") or "정중·간결").strip()
    services = email_services.get_email_services()
    message = services.gmail.get_message(mid)
    if not message:
        return jsonify({"ok": False, "error": "메일을 찾을 수 없습니다"}), 404
    thread_id = message.get("thread_id") or message.get("threadId")
    thread = services.gmail.get_thread(thread_id) if thread_id else {}
    res = services.llm.generate_reply_draft(message, thread, tone)
    if not res.get("ok"):
        return jsonify({"ok": False, "error": res.get("error") or "초안 생성 실패"}), 502
    draft = dict(res.get("draft") or {})
    draft["status"] = "unsent"
    email_store.save_draft(mid, draft)
    return jsonify({"ok": True, "draft": draft})


@app.post("/api/email/messages/<mid>/draft/discard")
def email_draft_discard(mid):
    """생성한 초안 폐기(로컬에서만 삭제). Gmail 무관."""
    import email_store
    email_store.clear_draft(mid)
    return jsonify({"ok": True})


@app.post("/api/email/messages/<mid>/draft/save")
def email_draft_save(mid):
    """초안 편집 내용 저장(미발송 유지). 발송 아님. 기존 초안의 text 만 갱신한다."""
    import email_store
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if text is None:
        return jsonify({"ok": False, "error": "text 없음"}), 400
    d = email_store.get_draft(mid) or {}
    if not d:
        return jsonify({"ok": False, "error": "초안 없음"}), 404
    d["text"] = text
    d["status"] = "unsent"
    d["edited"] = True
    ok = email_store.save_draft(mid, d)
    return jsonify({"ok": ok})


@app.post("/api/email/messages/<mid>/priority")
def email_set_priority(mid):
    """이 메일의 우선순위를 수동 변경(p0/p1/p2). 이 메일만, 일회성. Gmail 무관.

    자동 점수/규칙보다 우선하며, 자동초안 대상 선별도 이 값을 따른다.
    """
    import email_store
    data = request.get_json(silent=True) or {}
    pri = (data.get("priority") or "").strip().lower()
    ok = email_store.set_priority(mid, pri)
    return jsonify({"ok": ok, "priority": pri if ok else None})


@app.post("/api/email/messages/<mid>/autodraft/mute")
def email_autodraft_mute(mid):
    """이 메일 발신자를 자동초안 대상에서 영구 제외 + 현재 초안 폐기.

    '이 발신자 안 보기'(큐에서 숨김)와 다르다. 메일은 그대로 보이되, 앞으로
    자동 초안만 생성하지 않는다. 발송과 무관(아무것도 보내지 않음).
    """
    import email_store
    import email_autodraft
    import email_services
    from email.utils import parseaddr
    services = email_services.get_email_services()
    message = services.gmail.get_message(mid)
    addr = ""
    if message:
        _, addr = parseaddr((message.get("headers") or {}).get("from") or "")
    ok = email_autodraft.add_muted(addr) if addr else False
    email_store.clear_draft(mid)  # 이미 만든 초안도 폐기
    return jsonify({"ok": ok, "muted": addr})


@app.route("/api/email/persona", methods=["GET", "POST"])
def email_persona_api():
    """답장 초안용 말투 프로필 + 서명 (전역 설정). 발송과 무관, 저장만 한다."""
    import email_persona
    if request.method == "GET":
        return jsonify({"ok": True, **email_persona.load()})
    data = request.get_json(silent=True) or {}
    ok = email_persona.save(data.get("persona") or "", data.get("signature") or "")
    return jsonify({"ok": ok, **email_persona.load()})


@app.route("/api/email/cal-pane")
def email_cal_pane():
    """우측 '일정 후보' 패널 부분 렌더(메일 클릭/감지 후 재렌더용)."""
    mid = request.args.get("id")
    view = email_view.build_email_view(selected_id=mid)
    return render_template(
        "_email_panel_cal.html",
        candidates=view.get("candidates") or [],
        focus_id=(view.get("focus") or {}).get("id") or mid,
    )


@app.post("/api/email/messages/<mid>/events/detect")
def email_events_detect(mid):
    """메일에서 일정 후보 감지(규칙+LLM). 캘린더엔 쓰지 않는다(S4b 가 등록).

    오직 이 라우트(=버튼 클릭)에서만 LLM 을 호출한다(인박스 로딩은 호출 안 함).
    후보는 로컬(email_store)에만 'pending' 으로 저장. 등록은 S4b 승인 라우트.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo
    import email_store
    import email_services
    services = email_services.get_email_services()
    message = services.gmail.get_message(mid)
    if not message:
        return jsonify({"ok": False, "error": "메일을 찾을 수 없습니다"}), 404
    now_kst = _dt.datetime.now(ZoneInfo("Asia/Seoul"))
    res = services.llm.detect_events(message, now_kst)
    if not res.get("ok"):
        return jsonify({"ok": False, "error": res.get("error") or "일정 감지 실패"}), 502
    cands = res.get("candidates") or []
    email_store.save_candidates(mid, cands)
    return jsonify({"ok": True, "count": len(cands), "candidates": email_store.get_candidates(mid)})


@app.post("/api/email/messages/<mid>/events/<cid>/ignore")
def email_event_ignore(mid, cid):
    """일정 후보 무시(로컬 상태만). 캘린더 무관. 후보 없으면 404."""
    import email_store
    if email_store.set_candidate_status(mid, cid, "ignored"):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "후보를 찾을 수 없습니다"}), 404


@app.post("/api/email/messages/<mid>/events/<cid>/restore")
def email_event_restore(mid, cid):
    """무시한 후보 되살리기(pending). 캘린더 무관. 후보 없으면 404."""
    import email_store
    if email_store.set_candidate_status(mid, cid, "pending"):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "후보를 찾을 수 없습니다"}), 404


def _candidate_event_fields(c, now):
    """후보 dict -> (iso, 'HH:MM'|None). 날짜를 못 구하면 ('', None)."""
    import re as _re
    from datetime import datetime as _dtm, date as _d
    start = (c.get("start_iso") or "").strip()
    if start:
        try:
            dt = _dtm.fromisoformat(start)
            tlabel = (c.get("time_label") or "").strip()
            return dt.date().isoformat(), (tlabel or dt.strftime("%H:%M") or None)
        except Exception:
            pass
    label = (c.get("date_label") or c.get("date") or "")
    m = _re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", label)
    if m:
        try:
            time_str = (c.get("time_label") or c.get("time") or "").strip()
            return _d(now.year, int(m.group(1)), int(m.group(2))).isoformat(), (time_str or None)
        except Exception:
            pass
    return "", None


@app.post("/api/email/messages/<mid>/events/<cid>/approve")
def email_event_approve(mid, cid):
    """일정 후보를 wj 캘린더(로컬, 업무 탭)에 등록. 오직 이 클릭에서만 등록. 중복 차단.

    Google Calendar 아님 -> 추가 권한/재동의 불필요. 같은 후보 두 번 눌러도 한 번만.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo
    import email_store
    import wj_events
    cand = next((c for c in email_store.get_candidates(mid) if c.get("id") == cid), None)
    if not cand:
        return jsonify({"ok": False, "error": "후보를 찾을 수 없습니다"}), 404
    now = _dt.datetime.now(ZoneInfo("Asia/Seoul"))
    iso, time_str = _candidate_event_fields(cand, now)
    if not iso:
        return jsonify({"ok": False, "error": "날짜를 알 수 없어 캘린더에 넣지 못했습니다"}), 422
    ref = f"{mid}:{cid}"
    eid = wj_events.add_event(
        iso=iso, time_str=time_str, title=cand.get("title") or "일정",
        source=cand.get("source") or "", ref=ref, origin="email",
    )
    if not eid:
        return jsonify({"ok": False, "error": "캘린더 저장 실패"}), 500
    email_store.set_candidate_status(mid, cid, "done")
    return jsonify({"ok": True, "event_id": eid, "iso": iso, "time": time_str})


@app.post("/api/email/messages/<mid>/events/<cid>/undo")
def email_event_undo(mid, cid):
    """wj 캘린더 등록 되돌리기(로컬에서 이벤트 제거 + 후보 pending). """
    import email_store
    import wj_events
    wj_events.remove_by_ref(f"{mid}:{cid}")
    email_store.set_candidate_status(mid, cid, "pending")
    return jsonify({"ok": True})


# ---- 처리 규칙(말로 가르치면 똑똑해짐) ----
@app.route("/api/email/rules-pane")
def email_rules_pane():
    """우측 '처리 규칙' 패널 부분 렌더."""
    import email_rules
    return render_template("_email_panel_rules.html", rules=email_rules.list_rules())


@app.route("/api/email/queue-pane")
def email_queue_pane():
    """좌측 큐 항목 부분 렌더(규칙 추가/토글/정렬·검색·필터 변경 후 즉시 반영용)."""
    view = email_view.build_email_view(
        selected_id=request.args.get("id"),
        query=request.args.get("q"),
        sort=request.args.get("sort") or "priority",
        unread_only=(request.args.get("unread") == "1"),
    )
    return render_template("_email_queue_items.html", queue=view.get("queue") or [])


@app.post("/api/email/rules/add")
def email_rules_add():
    """자연어 규칙 추가. LLM 으로 매칭/효과 해석 후 저장(규칙 추가 때만 LLM 호출)."""
    import email_rules
    import email_services
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "규칙 문장을 입력하세요"}), 400
    parsed = {}
    try:
        services = email_services.get_email_services()
        parsed = services.llm.parse_rule(text) or {}
    except Exception:
        parsed = {}
    rule = email_rules.add_rule(text, parsed or None)
    if not rule:
        return jsonify({"ok": False, "error": "규칙 저장 실패"}), 500
    return jsonify({"ok": True, "rule": rule})


@app.post("/api/email/rules/<rid>/toggle")
def email_rules_toggle(rid):
    import email_rules
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    ok = email_rules.set_enabled(rid, enabled)
    return (jsonify({"ok": True, "enabled": enabled}) if ok
            else (jsonify({"ok": False, "error": "규칙을 찾을 수 없습니다"}), 404))


@app.post("/api/email/rules/<rid>/delete")
def email_rules_delete(rid):
    import email_rules
    ok = email_rules.delete_rule(rid)
    return (jsonify({"ok": True}) if ok
            else (jsonify({"ok": False, "error": "규칙을 찾을 수 없습니다"}), 404))


# ---- 스누즈(나중에 다시 보기 / 나중에 답변) ----
def _snooze_until(preset: str, now_kst) -> int:
    """프리셋 -> 복귀 epoch(sec). KST 기준."""
    import datetime as _dt
    if preset == "3h":
        return int((now_kst + _dt.timedelta(hours=3)).timestamp())
    if preset == "tomorrow":
        d = (now_kst + _dt.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return int(d.timestamp())
    if preset == "nextweek":
        days = (7 - now_kst.weekday()) or 7  # 다음 주 월요일
        d = (now_kst + _dt.timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
        return int(d.timestamp())
    if preset == "urgent":
        # 멀리(30일) 잡아 시간 도달로는 안 올라오고, p0 될 때만 재부상.
        return int((now_kst + _dt.timedelta(days=30)).timestamp())
    # 기본: 3시간
    return int((now_kst + _dt.timedelta(hours=3)).timestamp())


@app.route("/api/email/later-pane")
def email_later_pane():
    """좌측 '나중에' 섹션 부분 렌더(스누즈/해제 후 갱신용)."""
    view = email_view.build_email_view()
    return render_template("_email_later.html", later=view.get("later") or [])


@app.post("/api/email/messages/<mid>/snooze")
def email_snooze(mid):
    """이 메일을 '나중에'로 보냄(메인 큐에서 빠지고, 복귀시각/긴급 시 재부상). Gmail 무관."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    import email_store
    data = request.get_json(silent=True) or {}
    preset = (data.get("preset") or "3h").strip()
    kind = (data.get("kind") or "view").strip()
    now_kst = _dt.datetime.now(ZoneInfo("Asia/Seoul"))
    until = _snooze_until(preset, now_kst)
    ok = email_store.snooze(mid, until, kind)
    return jsonify({"ok": bool(ok), "until": until})


@app.post("/api/email/messages/<mid>/unsnooze")
def email_unsnooze(mid):
    """스누즈 해제(지금 큐로 복귀). Gmail 무관. 스누즈 상태 아니면 404."""
    import email_store
    if email_store.unsnooze(mid):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "스누즈 상태가 아닙니다"}), 404


def _list_raw_dates_desc() -> list[str]:
    out = []
    for p in sorted(thinking_compute.RAW_ROOT.glob("*.md"), reverse=True):
        out.append(p.stem)
    return out


def _pick_default_raw_date() -> str | None:
    # 모닝논문 보호 날짜는 기본 선택에서 제외
    for d in _list_raw_dates_desc():
        if d not in autowiki.AUTOWIKI_EXCLUDE_RAW:
            return d
    return None


def _latest_draft_path() -> str | None:
    root = autowiki.DRAFT_ROOT
    if not root.exists():
        return None
    files = sorted(root.glob("*.json"), reverse=True)
    return str(files[0]) if files else None


def _draft_path_by_date(date_str: str) -> str:
    return str(autowiki.DRAFT_ROOT / f"{date_str}.json")


@app.route("/think/drafts")
def think_drafts():
    # 리뷰 화면(GET)은 항상 read-only. 위키 반영은 POST에서만 수행.
    latest = _latest_draft_path()
    payload = None
    if latest and os.path.exists(latest):
        try:
            payload = json.loads(Path(latest).read_text(encoding="utf-8"))
        except Exception:
            payload = None
    proposals = (payload or {}).get("proposals") or []
    return render_template(
        "think_drafts.html",
        active_tab="think",
        draft_payload=payload,
        proposals=proposals,
        raw_dates=_list_raw_dates_desc(),
        default_raw_date=_pick_default_raw_date(),
        protected_raw=sorted(autowiki.AUTOWIKI_EXCLUDE_RAW),
        protected_slugs=sorted(autowiki.AUTOWIKI_EXCLUDE_SLUGS),
    )


@app.post("/api/autowiki/generate")
def api_autowiki_generate():
    body = request.get_json(silent=True) or {}
    req_date = (body.get("date") or "").strip()
    pick_date = req_date or _pick_default_raw_date()
    if not pick_date:
        return jsonify({"ok": False, "error": "생성 가능한 raw 날짜가 없습니다"}), 400
    if pick_date in autowiki.AUTOWIKI_EXCLUDE_RAW:
        return jsonify({"ok": False, "error": f"보호된 raw 날짜는 생성 불가: {pick_date}"}), 400
    raw_path = thinking_compute.RAW_ROOT / f"{pick_date}.md"
    if not raw_path.exists():
        return jsonify({"ok": False, "error": f"raw 파일 없음: {pick_date}"}), 404
    try:
        proposals = autowiki.generate_drafts(pick_date)
        out = autowiki.write_drafts(proposals)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(
        {
            "ok": True,
            "date": pick_date,
            "draft_path": out,
            "count": len(proposals),
            "protected_raw": sorted(autowiki.AUTOWIKI_EXCLUDE_RAW),
        }
    )


@app.post("/api/autowiki/apply")
def api_autowiki_apply():
    body = request.get_json(silent=True) or {}
    draft_date = (body.get("date") or "").strip()
    ids = body.get("ids") or []
    if not draft_date:
        return jsonify({"ok": False, "error": "date required"}), 400
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        return jsonify({"ok": False, "error": "ids(list[str]) required"}), 400
    draft_path = _draft_path_by_date(draft_date)
    if not os.path.exists(draft_path):
        return jsonify({"ok": False, "error": f"draft 없음: {draft_date}"}), 404

    payload = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    proposals = payload.get("proposals") or []
    by_id = {p.get("ingest_id"): p for p in proposals if p.get("ingest_id")}

    allowed_ids: list[str] = []
    pre_results: list[dict] = []
    for ingest_id in ids:
        prop = by_id.get(ingest_id)
        if not prop:
            pre_results.append({"ingest_id": ingest_id, "ok": False, "changed": False, "reason": "draft에 없는 id"})
            continue
        if autowiki.is_excluded_target(prop.get("target") or {}):
            pre_results.append({"ingest_id": ingest_id, "ok": False, "changed": False, "reason": "보호 대상(모닝논문) 차단"})
            continue
        allowed_ids.append(ingest_id)

    batch = autowiki.apply_batch(draft_path, only_ids=allowed_ids) if allowed_ids else {"ok": True, "count": 0, "applied": 0, "results": []}
    batch_by_id = {r.get("ingest_id"): r for r in (batch.get("results") or [])}
    merged_results: list[dict] = []
    for ingest_id in ids:
        in_batch = batch_by_id.get(ingest_id)
        if in_batch:
            merged_results.append(in_batch)
            continue
        pre = next((x for x in pre_results if x.get("ingest_id") == ingest_id), None)
        if pre:
            merged_results.append(pre)

    return jsonify(
        {
            "ok": all(r.get("ok") for r in merged_results) if merged_results else True,
            "date": draft_date,
            "requested": len(ids),
            "allowed": len(allowed_ids),
            "applied": sum(1 for r in merged_results if r.get("changed")),
            "results": merged_results,
        }
    )


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


@app.post("/api/project")
def project_create():
    body = request.get_json(silent=True) or {}
    result = compute.create_project(body, date.today())
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.post("/api/project/<project_id>/update")
def project_update(project_id: str):
    body = request.get_json(silent=True) or {}
    result = compute.update_project(project_id, body)
    code = 200 if result.get("ok") else (404 if result.get("error") == "project not found" else 400)
    return jsonify(result), code


@app.post("/api/project/<project_id>/archive")
def project_archive(project_id: str):
    result = compute.archive_project(project_id)
    code = 200 if result.get("ok") else 404
    return jsonify(result), code


@app.delete("/api/project/<project_id>")
def project_delete(project_id: str):
    result = compute.delete_project(project_id)
    code = 200 if result.get("ok") else (409 if result.get("linked_count") else 404)
    return jsonify(result), code


if __name__ == "__main__":
    app.run(host=SETTINGS.host, port=SETTINGS.port, debug=False)
