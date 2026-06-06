"""이메일 탭 view 빌더 (email_data.build_view() 와 같은 shape).

build_email_view() 는 services(EmailServices) 를 받아 /email 템플릿이 기대하는
dict 를 만든다. services 가 없으면 get_email_services() 로 만든다.

원칙:
- 절대 raise 하지 않는다. 실패/미연동이면 is_mock/needs_auth 가 반영된 안전한 view.
- fake 모드에서는 email_data 와 사실상 동일한 view (그래야 /email 화면이 유지됨).
- 인박스 view 빌드 단계에서 LLM 을 호출하지 않는다(비용 통제). draft/candidates 는
  목업 fixture 그대로 노출하되 LLM 메서드는 부르지 않는다.

템플릿이 기대하는 key:
  queue, focus, draft, candidates, drafts_box, labels, progress, estats,
  is_mock (+ needs_auth, active_tab 은 app.py 에서 주입)
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from zoneinfo import ZoneInfo

import email_data
import email_score

KST = ZoneInfo("Asia/Seoul")


def _safe_rules() -> list:
    try:
        import email_rules
        return email_rules.list_rules()
    except Exception:
        return []


_QUOTE_MARKER = None


def _render_text_body(text: str) -> str:
    """text/plain 본문을 HTML 로. 인용문(이전 대화/원본 메일/서명) 은 접어서 최신 내용 먼저."""
    import html as _h
    import re
    global _QUOTE_MARKER
    if _QUOTE_MARKER is None:
        _QUOTE_MARKER = re.compile(
            r"(-{2,}\s*Original Message\s*-{2,}"
            r"|-{2,}\s*원본 메일\s*-{2,}"
            r"|^On .{3,80} wrote:\s*$"
            r"|^\d{4}년 .{1,40}(작성|보냄):\s*$"
            r"|^\d{4}\.\s*\d{1,2}\.\s*\d{1,2}.{0,40}(작성|보냄)"
            r"|^>{1,}\s)",
            re.M,
        )
    try:
        m = _QUOTE_MARKER.search(text or "")
        if m and m.start() > 20:
            latest = (text[:m.start()]).rstrip()
            quoted = text[m.start():]
            return (
                "<p>" + _h.escape(latest).replace("\n", "<br>") + "</p>"
                + '<div class="quoted-wrap">'
                + '<button type="button" class="quoted-toggle">이전 대화 펼치기</button>'
                + '<div class="quoted" hidden>' + _h.escape(quoted).replace("\n", "<br>") + "</div>"
                + "</div>"
            )
        return "<p>" + _h.escape(text or "").replace("\n", "<br>") + "</p>"
    except Exception:
        return "<p>" + _h.escape(text or "").replace("\n", "<br>") + "</p>"


def _fmt_until(ts) -> str:
    """복귀 예정 시각(epoch) -> KST 라벨. 멀면 '급해질 때', 가까우면 'M/D HH:MM'."""
    try:
        from datetime import datetime, timedelta
        ts = int(ts or 0)
        if ts <= 0:
            return ""
        now = datetime.now(KST)
        dt = datetime.fromtimestamp(ts, KST)
        # 30일 이상 뒤 = '급해질 때까지' 프리셋
        if dt - now > timedelta(days=29):
            return "급해질 때"
        if dt.date() == now.date():
            return "오늘 " + dt.strftime("%H:%M")
        if dt.date() == (now + timedelta(days=1)).date():
            return "내일 " + dt.strftime("%H:%M")
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return ""


def _display_name(from_header: str | None) -> str:
    """From 헤더에서 표시 이름만(없으면 이메일)."""
    try:
        name, addr = parseaddr(from_header or "")
        return name or addr or "(발신자 없음)"
    except Exception:
        return from_header or "(발신자 없음)"


def _addr(from_header: str | None) -> str:
    try:
        return parseaddr(from_header or "")[1] or ""
    except Exception:
        return ""


def _rel_time(date_header: str | None) -> str:
    """RFC 2822 Date 헤더 -> KST 상대시간. 실패하면 원문 그대로."""
    if not date_header:
        return ""
    try:
        dt = parsedate_to_datetime(date_header)
        if dt is None:
            return date_header
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = (datetime.now(timezone.utc) - dt).total_seconds()
        if diff < 0:
            diff = 0
        if diff < 60:
            return "방금"
        if diff < 3600:
            return f"{int(diff // 60)}분 전"
        if diff < 86400:
            return f"{int(diff // 3600)}시간 전"
        if diff < 172800:
            return "어제"
        return dt.astimezone(KST).strftime("%m/%d")
    except Exception:
        return date_header


def _safe_mock_view() -> dict:
    """email_data 목업을 기반으로 한 안전 view (실패 시에도 항상 dict)."""
    try:
        view = email_data.build_view()
    except Exception:
        view = {}
    base = {
        "queue": [],
        "focus": {},
        "draft": {"status": "none"},
        "candidates": [],
        "drafts_box": [],
        "labels": [],
        "progress": {"idx": 0, "total": 0},
        "estats": {"unread": 0, "pending_events": 0, "pending_drafts": 0, "followups": 0},
        "rules": _safe_rules(),
        "later": [],
        "is_mock": True,
        "needs_auth": False,
    }
    base.update(view or {})
    base.setdefault("needs_auth", False)
    return base


def _needs_auth_view() -> dict:
    """연동 필요 상태 안전 view (빈 큐 + needs_auth 표시)."""
    return {
        "queue": [],
        "focus": {},
        "draft": {"status": "none"},
        "candidates": [],
        "drafts_box": [],
        "labels": [],
        "progress": {"idx": 0, "total": 0},
        "estats": {"unread": 0, "pending_events": 0, "pending_drafts": 0, "followups": 0},
        "rules": _safe_rules(),
        "later": [],
        "is_mock": True,
        "needs_auth": True,
    }


def build_email_view(services=None, selected_id: str | None = None, query: str | None = None,
                     allow_fallback: bool = False, sort: str = "priority",
                     unread_only: bool = False) -> dict:
    """email_data.build_view() 와 같은 shape 를 반환. 절대 raise 하지 않는다."""
    try:
        if services is None:
            import email_services
            services = email_services.get_email_services()

        backend = getattr(services, "backend", "fake")

        # fake 모드: 목업 fixture 와 사실상 동일한 view 를 유지한다.
        if backend != "real":
            return _safe_mock_view()

        # real 모드: 캐시 우선(즉시 렌더). 캐시 없으면 배치 fetch 1회 후 캐시 저장.
        import email_cache
        cached = email_cache.load()
        messages = (cached or {}).get("messages") or []
        if not messages:
            try:
                import gmail as _gmail
                messages = _gmail.fetch_inbox()
            except Exception:
                messages = []
            if messages:
                email_cache.save(messages)
        if not messages:
            # 캐시 비었고 fetch 도 실패 == 인증 실패/토큰 만료/Google 실패.
            return _needs_auth_view()
        return _build_real_view(messages, selected_id, allow_fallback, query, sort, unread_only)
    except Exception:
        # 어떤 경로로도 예외가 새 나가면 안 된다.
        return _needs_auth_view()


def _build_real_view(messages: list[dict], selected_id: str | None,
                     allow_fallback: bool = False, query: str | None = None,
                     sort: str = "priority", unread_only: bool = False) -> dict:
    """캐시된 정규화 메시지(본문 포함) -> 템플릿 view dict. 추가 API 호출 없음.

    캐시 메시지에 이미 body 가 있으므로 포커스 본문도 get_message 없이 바로 쓴다.
    """
    try:
        import time as _time
        import email_store
        import email_rules
        hidden = email_store.hidden_ids()
        snoozed = email_store.snoozed()
        now_ts = int(_time.time())
        rules = email_rules.list_rules()  # 1회 로드, 큐 전체에 재사용
        overrides = email_store.priority_overrides()  # 메일별 수동 우선순위(최우선)
        later_items = []
        drafts_map = {}
        try:
            drafts_map = (email_store._load() or {}).get("drafts", {}) or {}
        except Exception:
            drafts_map = {}

        # 같은 스레드는 최신 메시지 1개로 합친다(Gmail 처럼). 오래된 메시지의 묵은 '오늘' 오판도 줄어듦.
        latest = {}
        for m in messages:
            if not isinstance(m, dict):
                continue
            tid = m.get("thread_id") or m.get("id")
            cur = latest.get(tid)
            if cur is None or (m.get("internal_ts") or 0) >= (cur.get("internal_ts") or 0):
                latest[tid] = m
        messages = sorted(latest.values(), key=lambda x: x.get("internal_ts") or 0, reverse=True)

        q_norm = (query or "").strip().lower()
        queue = []
        by_id = {}
        for m in messages:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            by_id[mid] = m  # 숨긴 것도 by_id 엔 둠(영수증/발신자 조회용)
            if mid in hidden:
                continue    # 화면(큐)에서만 제외. 실제 Gmail 은 그대로.
            headers = m.get("headers") or {}
            # 안읽음/검색 필터 — 큐 표시에서만 제외(by_id 엔 남아 영수증/조회 가능)
            is_unread = "UNREAD" in (m.get("label_ids") or [])
            if unread_only and not is_unread:
                continue
            if q_norm:
                _body = m.get("body") or {}
                hay = (
                    (headers.get("subject") or "") + " "
                    + (headers.get("from") or "") + " "
                    + (m.get("snippet") or "") + " "
                    + (_body.get("text") or "")
                ).lower()
                if q_norm not in hay:
                    continue
            s = email_score.score(m)
            if rules:
                s = email_rules.apply_to(m, s, rules)
            if overrides.get(mid) in ("p0", "p1", "p2"):
                s["priority"] = overrides[mid]  # 사용자 수동 지정이 최우선

            # 스누즈: 복귀시각 전이고 아직 p0 아니면 메인 큐에서 빼서 '나중에'로.
            sn = snoozed.get(mid)
            resurfaced = False
            if sn:
                time_up = now_ts >= int(sn.get("until") or 0)
                urgent = s["priority"] == "p0"
                if not (time_up or urgent):
                    later_items.append({
                        "id": mid,
                        "sender": _display_name(headers.get("from")),
                        "subject": headers.get("subject") or "(제목 없음)",
                        "kind": sn.get("kind") or "view",
                        "until_label": _fmt_until(sn.get("until")),
                    })
                    continue
                resurfaced = True

            qitem = {
                "id": mid,
                "sender": _display_name(headers.get("from")),
                "subject": headers.get("subject") or "(제목 없음)",
                "time": _rel_time(headers.get("date")),
                "priority": s["priority"],
                "reasons": s["reasons"],
                "has_event": s["has_event"],
                "has_draft": bool(drafts_map.get(mid)),
                "current": False,
                "unread": is_unread,
                "internal_ts": m.get("internal_ts") or 0,
                "resurfaced": resurfaced,
                "resurfaced_reason": ("마감 임박" if resurfaced and s["priority"] == "p0" else ("시간 됨" if resurfaced else "")),
            }
            queue.append(qitem)

        # 정렬: 기본 우선순위(p0>p1>p2), 또는 날짜순(newest/oldest)
        if sort == "newest":
            queue.sort(key=lambda x: x.get("internal_ts") or 0, reverse=True)
        elif sort == "oldest":
            queue.sort(key=lambda x: x.get("internal_ts") or 0)
        else:
            queue.sort(key=lambda q: email_score.order_key(q.get("priority", "p2")))

        focus = {}
        target_id = selected_id or (queue[0]["id"] if queue else None)
        # 선택한 id 가 캐시에 없으면(오래된 링크/갱신 때 빠진 메일) 큐 첫 메일로 폴백.
        # 단 allow_fallback(읽기 전용 표시) 일 때만. 상태 변경 라우트(발신자 제외/영수증
        # 저장 등)는 폴백하면 엉뚱한 메일을 건드리므로, 그쪽은 focus={} 로 남겨 무동작시킨다.
        if allow_fallback and target_id and target_id not in by_id and queue:
            target_id = queue[0]["id"]
        if target_id and target_id in by_id:
            idx = 0
            for i, q in enumerate(queue):
                if q.get("id") == target_id:
                    q["current"] = True
                    idx = i
            m = by_id[target_id]
            headers = m.get("headers") or {}
            body = m.get("body") or {}
            body_html = body.get("html_sanitized") or ""
            if not body_html and body.get("text"):
                body_html = _render_text_body(body["text"])
            sf = email_score.score(m)
            if rules:
                sf = email_rules.apply_to(m, sf, rules)
            if overrides.get(target_id) in ("p0", "p1", "p2"):
                sf["priority"] = overrides[target_id]  # 사용자 수동 지정이 최우선
            focus = {
                "id": target_id,
                "sender": _display_name(headers.get("from")),
                "sender_email": _addr(headers.get("from")),
                "subject": headers.get("subject") or "(제목 없음)",
                "time": _rel_time(headers.get("date")),
                "priority": sf["priority"],
                "reasons": sf["reasons"],
                "body_html": body_html,
                "summary": {
                    "event": "일정 후보 확인" if sf["has_event"] else "",
                    "draft": "미발송 초안 1" if drafts_map.get(target_id) else "초안 생성 필요",
                    "followup": "",
                },
            }

        focus_draft = drafts_map.get(target_id) if target_id else None

        # 저장된 일정 후보 -> 템플릿 shape(date/time/place/source/status/title/id)
        cand_view = []
        try:
            for c in email_store.get_candidates(target_id) if target_id else []:
                cand_view.append({
                    "id": c.get("id"),
                    "title": c.get("title") or "일정 후보",
                    "date": c.get("date_label") or "",
                    "time": c.get("time_label") or "",
                    "place": c.get("place") or "",
                    "source": c.get("source") or "",
                    "status": c.get("status") or "pending",
                })
        except Exception:
            cand_view = []
        if focus and cand_view:
            pend = sum(1 for c in cand_view if c["status"] == "pending")
            focus["summary"]["event"] = (
                f"일정 후보 {len(cand_view)}" + (f" · 승인 대기 {pend}" if pend else "")
            )

        return {
            "queue": queue,
            "focus": focus,
            "draft": focus_draft if focus_draft else {"status": "none"},
            "candidates": cand_view,
            "drafts_box": [],
            "labels": [],
            "progress": {"idx": (idx + 1) if focus else 0, "total": len(queue)},
            "estats": {
                "unread": len(queue),
                "pending_events": sum(1 for c in cand_view if c.get("status") == "pending"),
                "pending_drafts": sum(1 for q in queue if q.get("has_draft")),
                "followups": 0,
            },
            "rules": rules,
            "later": later_items,
            "is_mock": False,
            "needs_auth": False,
        }
    except Exception:
        return _needs_auth_view()
