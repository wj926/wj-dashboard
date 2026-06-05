"""규칙 기반 우선순위 + 이유칩 (실 Gmail 메시지용).

목업 M4 처럼 P0/P1/P2 색과 짧은 이유칩을 보여주기 위한 가벼운 휴리스틱.
LLM 안 쓴다(비용 0). 절대 raise 하지 않는다.
나중에 S5 에서 스레드/미회신일수 기반으로 정교화.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def _days_old(date_header: str) -> int:
    """받은 지 며칠 됐나(KST 무관, UTC 기준 일수). 모르면 큰 값."""
    try:
        dt = parsedate_to_datetime(date_header or "")
        if dt is None:
            return 999
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return 999

_AUTOMATED = (
    "noreply", "no-reply", "do-not-reply", "donotreply", "notifications",
    "notification", "mailer-daemon", "automated", "newsletter", "no_reply",
    "updates@", "alert@", "bounce", "mailer@", "postmaster",
)
_P0_KW = (
    "긴급", "urgent", "오늘", "내일", "마감", "deadline", "재문의",
    "회신 부탁", "reminder", "d-1", "asap", "즉시", "급히", "촉박",
)
_DATE_WORDS = (
    "마감", "deadline", "월요일", "화요일", "수요일", "목요일", "금요일",
    "토요일", "일요일", "오전", "오후", "회의", "미팅", "면담", "세미나",
    "일정", "심사", "발표",
)
_DATE_RE = re.compile(
    r"(\d{1,2}\s*[/월]\s*\d{1,2}|\d{1,2}:\d{2}|\d{1,2}\s*시|오[전후]\s*\d|due\b|deadline|by\s+\w+day)",
    re.I,
)


def _is_automated(frm: str) -> bool:
    low = (frm or "").lower()
    return any(a in low for a in _AUTOMATED)


def _category(frm: str, subject: str) -> str:
    low = ((frm or "") + " " + (subject or "")).lower()
    if any(k in low for k in ("neurips", "icml", "iclr", " acl", "emnlp", "aclweb",
                              "cvpr", "학회", "conference", "review", "submission", "camera-ready")):
        return "학회"
    if any(k in low for k in ("github", "gitlab", "pull request", "commit", "[pr")):
        return "dev"
    if any(k in low for k in ("행정", "교무", "학과", "office", "대학원", "장학")):
        return "행정"
    return ""


def score(message: dict) -> dict:
    """{priority, reasons(<=3), has_event, category}. 실패해도 안전 기본값."""
    try:
        headers = message.get("headers") or {}
        frm = headers.get("from") or ""
        subject = headers.get("subject") or ""
        snippet = message.get("snippet") or ""
        labels = message.get("label_ids") or []
        text = f"{subject} {snippet}"
        low = text.lower()

        bulk = any(
            l in ("CATEGORY_UPDATES", "CATEGORY_FORUMS", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL")
            for l in labels
        )
        automated = _is_automated(frm) or bulk
        unread = "UNREAD" in labels
        has_date = bool(_DATE_RE.search(text)) or any(w in text for w in _DATE_WORDS)
        cat = _category(frm, subject)

        # 내가 이미 답장한 스레드 == 처리함. 긴급/답장필요 빼고 우선순위 내림.
        if message.get("i_replied"):
            r = ["처리함"] + ([cat] if cat else [])
            return {"priority": "p2", "reasons": r[:3], "has_event": has_date, "category": cat}

        days_old = _days_old(headers.get("date"))
        fresh = days_old <= 1  # 상대 표현('오늘')은 발송 시점 기준이라 오래되면 이미 지남
        has_today = any(k in low for k in ("오늘", "today", "금일", "내일까지", "d-1"))
        has_deadline = any(k in low for k in ("마감", "deadline", "due"))
        deadline_near = has_deadline and days_old <= 5
        strong_kw = any(k in low for k in ("긴급", "urgent", "asap", "즉시", "급히", "재문의", "reminder"))
        is_reply = subject.strip().lower().startswith("re:")
        direct = any(k in text for k in ("부탁", "문의", "질문", "회신", "요청", "검토", "답장"))

        # P1 을 사람메일 기본값으로 쓰지 않는다(홍보/알림이 P1 로 뜨던 문제).
        # 실제 행동 신호(마감 임박/일정/중요 발신자/직접 요청/답장 스레드)가 있을 때만 P1.
        actionable = deadline_near or has_date or bool(cat) or direct or (unread and is_reply)
        if (not automated) and fresh and (has_today or strong_kw):
            pri = "p0"
        elif (not automated) and actionable:
            pri = "p1"
        else:
            pri = "p2"

        reasons: list[str] = []
        if has_today and fresh:
            reasons.append("오늘 마감")
        elif deadline_near:
            reasons.append("마감 임박")
        if has_date:
            reasons.append("일정 포함")
        if (not automated) and (direct or (unread and is_reply)):
            reasons.append("답장 필요")
        if cat:
            reasons.append(cat)
        if automated and not reasons:
            reasons.append("자동 알림")

        seen = set()
        out = []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return {"priority": pri, "reasons": out[:3], "has_event": has_date, "category": cat}
    except Exception:
        return {"priority": "p2", "reasons": [], "has_event": False, "category": ""}


_ORDER = {"p0": 0, "p1": 1, "p2": 2}


def order_key(priority: str) -> int:
    return _ORDER.get(priority, 3)
