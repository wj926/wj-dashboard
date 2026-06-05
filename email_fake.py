"""이메일 탭 Fake 백엔드 (OAuth/실 Gmail/실 LLM 없이 개발·테스트).

원칙:
- 데이터는 email_data.build_view() 를 재사용한다. fake 모드 /email 화면은
  목업과 사실상 동일해야 한다.
- WJ_EMAIL_FAKE_MODE(ok/auth_expired/google_down/llm_down) 로 장애를 주입한다.
  내부에서 예외를 만나도 public 메서드는 절대 밖으로 던지지 않고
  []/{}/{"ok":false,"error":...}/needs_auth 로 닫는다.
- FakeEmailLLM 은 call count 를 기록한다(인박스 로딩만으로 호출되면 안 됨 검증용).

gmail.py 의 public 함수 시그니처와 FakeGmailClient 메서드 시그니처는
tests/test_email_contracts.py 가 정합성을 검사한다.
"""
from __future__ import annotations

import os

import email_data


def _fake_mode() -> str:
    return os.environ.get("WJ_EMAIL_FAKE_MODE", "ok")


class _FakeFault(RuntimeError):
    """fake 내부 장애 주입용. public 메서드 밖으로 새 나가면 안 된다."""


def _maybe_google_fault():
    """google_down/auth_expired 모드면 내부 예외를 던진다(메서드가 잡아 닫음)."""
    mode = _fake_mode()
    if mode == "google_down":
        raise _FakeFault("fake_google_down")
    if mode == "auth_expired":
        raise _FakeFault("fake_auth_expired")


def _auth_error() -> dict:
    return {"ok": False, "error": "fake_auth_expired", "needs_auth": True}


def _google_error() -> dict:
    return {"ok": False, "error": "fake_google_down", "needs_auth": False}


def _fault_dict() -> dict:
    """현재 fake 모드에 맞는 ok:false dict (메서드가 dict 를 돌려야 할 때)."""
    mode = _fake_mode()
    if mode == "auth_expired":
        return _auth_error()
    return _google_error()


class FakeGmailClient:
    """email_data 고정 fixture 기반 Gmail 읽기 fake.

    메서드 시그니처는 gmail.py 의 동명 public 함수와 정합해야 한다.
    """

    def __init__(self, view: dict | None = None):
        self._view = view or {}

    @classmethod
    def from_email_data(cls) -> "FakeGmailClient":
        try:
            return cls(email_data.build_view())
        except Exception:
            return cls({})

    # --- 읽기 -------------------------------------------------------------
    def list_inbox(self, query: str = "in:inbox newer_than:30d", max_results: int = 30) -> list[dict]:
        try:
            _maybe_google_fault()
            return list(self._view.get("queue") or [])[:max_results]
        except Exception:
            return []

    def get_message(self, message_id: str, fmt: str = "full") -> dict:
        try:
            _maybe_google_fault()
            focus = self._view.get("focus") or {}
            if focus.get("id") == message_id:
                return dict(focus)
            for m in self._view.get("queue") or []:
                if m.get("id") == message_id:
                    return dict(m)
            return {}
        except Exception:
            return {}

    def get_thread(self, thread_id: str) -> dict:
        try:
            _maybe_google_fault()
            focus = self._view.get("focus") or {}
            return {"id": thread_id, "messages": [dict(focus)] if focus else []}
        except Exception:
            return {}

    def list_drafts(self, max_results: int = 20) -> list[dict]:
        try:
            _maybe_google_fault()
            return list(self._view.get("drafts_box") or [])[:max_results]
        except Exception:
            return []

    def build_followups(self, days: int = 2, max_results: int = 50) -> list[dict]:
        try:
            _maybe_google_fault()
            # 미회신 이유칩이 있는 큐 항목을 팔로업 후보로 본다.
            out = []
            for m in self._view.get("queue") or []:
                reasons = m.get("reasons") or []
                if any("미회신" in r for r in reasons):
                    out.append({"thread_id": m.get("id")})
            return out[:max_results]
        except Exception:
            return []

    def list_attachments(self, message_id: str) -> list[dict]:
        # fake 에는 실제 첨부가 없다.
        return []

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        return b""


class FakeCalendarWriter:
    """approve 전에는 insert 없음. insert 후 fake event_id 반환."""

    def insert_event(self, candidate: dict, calendar_id: str = "primary") -> dict:
        try:
            _maybe_google_fault()
            cid = (candidate or {}).get("id") or (candidate or {}).get("title") or "evt"
            return {"ok": True, "event_id": f"fake_evt_{cid}", "html_link": ""}
        except Exception:
            return _fault_dict()

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> dict:
        try:
            _maybe_google_fault()
            return {"ok": True, "event_id": event_id}
        except Exception:
            return _fault_dict()


class FakeEmailLLM:
    """버튼 호출 시에만 draft/candidates 반환. call count 기록."""

    def __init__(self, view: dict | None = None):
        self._view = view if view is not None else _safe_view()
        self.call_count = 0

    def detect_events(self, message: dict, now_kst) -> dict:
        self.call_count += 1
        try:
            if _fake_mode() == "llm_down":
                return {"ok": False, "error": "fake_llm_down", "candidates": []}
            return {"ok": True, "candidates": list(self._view.get("candidates") or [])}
        except Exception:
            return {"ok": False, "error": "fake_llm_error", "candidates": []}

    def generate_reply_draft(self, message: dict, thread: dict, tone: str) -> dict:
        self.call_count += 1
        try:
            if _fake_mode() == "llm_down":
                return {"ok": False, "error": "fake_llm_down"}
            draft = dict(self._view.get("draft") or {})
            draft["status"] = "generated"
            draft["tone"] = tone
            return {"ok": True, "draft": draft}
        except Exception:
            return {"ok": False, "error": "fake_llm_error"}

    def parse_rule(self, text: str) -> dict:
        """LLM 없이 가벼운 휴리스틱으로 규칙 해석(테스트/오프라인용)."""
        self.call_count += 1
        try:
            t = (text or "")
            low = t.lower()
            if "영수증" in t or "결제" in t or "전표" in t:
                eff = "receipt"
            elif "나중" in t:
                eff = "later"
            elif any(k in t for k in ("낮", "덜", "숨", "내려")):
                eff = "priority_down"
            else:
                eff = "priority_up"
            # 따옴표 안 토큰이나 영문/숫자 토큰을 발신자 후보로 (대충)
            import re as _re
            froms = _re.findall(r"['\"]([^'\"]{2,20})['\"]", t)
            if not froms:
                froms = [w for w in _re.findall(r"[A-Za-z][A-Za-z0-9._-]{2,}", t)][:1]
            return {
                "label": t.strip()[:20],
                "effect": eff,
                "match": {"from": froms[:2], "subject_kw": []},
            }
        except Exception:
            return {}


def _safe_view() -> dict:
    try:
        return email_data.build_view()
    except Exception:
        return {}
