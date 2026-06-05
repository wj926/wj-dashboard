"""이메일 탭 서비스 레이어 (real <-> fake 토글).

라우트는 실 Gmail/Calendar/LLM 을 직접 import 하지 않고 get_email_services()
factory 로 구현을 받는다. WJ_EMAIL_BACKEND(기본 "fake") 로 real/fake 를 고른다.

real 분기에서만 gmail.py 등을 lazy import 한다. 파일 상단에서 google 라이브러리를
import 하지 않는다(미설치 환경에서 app import 가 깨지면 안 됨).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class GmailClient(Protocol):
    def list_inbox(self, query: str = "in:inbox newer_than:30d", max_results: int = 30) -> list[dict]: ...
    def get_message(self, message_id: str, fmt: str = "full") -> dict: ...
    def get_thread(self, thread_id: str) -> dict: ...
    def list_drafts(self, max_results: int = 20) -> list[dict]: ...
    def build_followups(self, days: int = 2, max_results: int = 50) -> list[dict]: ...
    def list_attachments(self, message_id: str) -> list[dict]: ...
    def download_attachment(self, message_id: str, attachment_id: str) -> bytes: ...


@runtime_checkable
class CalendarWriter(Protocol):
    def insert_event(self, candidate: dict, calendar_id: str = "primary") -> dict: ...
    def delete_event(self, event_id: str, calendar_id: str = "primary") -> dict: ...


@runtime_checkable
class EmailLLM(Protocol):
    def detect_events(self, message: dict, now_kst: datetime) -> dict: ...
    def generate_reply_draft(self, message: dict, thread: dict, tone: str) -> dict: ...
    def parse_rule(self, text: str) -> dict: ...


@dataclass
class EmailServices:
    gmail: GmailClient
    calendar: CalendarWriter
    llm: EmailLLM
    backend: str = "fake"


class _RealGmailAdapter:
    """gmail.py 의 모듈 레벨 함수를 GmailClient 메서드로 감싼다(lazy import)."""

    def list_inbox(self, query: str = "in:inbox newer_than:30d", max_results: int = 30) -> list[dict]:
        import gmail
        return gmail.list_inbox(query=query, max_results=max_results)

    def get_message(self, message_id: str, fmt: str = "full") -> dict:
        import gmail
        return gmail.get_message(message_id, fmt=fmt)

    def get_thread(self, thread_id: str) -> dict:
        import gmail
        return gmail.get_thread(thread_id)

    def list_drafts(self, max_results: int = 20) -> list[dict]:
        import gmail
        return gmail.list_drafts(max_results=max_results)

    def build_followups(self, days: int = 2, max_results: int = 50) -> list[dict]:
        import gmail
        return gmail.build_followups(days=days, max_results=max_results)

    def list_attachments(self, message_id: str) -> list[dict]:
        import gmail
        return gmail.list_attachments(message_id)

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        import gmail
        return gmail.download_attachment(message_id, attachment_id)


def get_email_services() -> EmailServices:
    """WJ_EMAIL_BACKEND 로 real/fake 구현을 고른다. 기본은 fake."""
    backend = (os.environ.get("WJ_EMAIL_BACKEND") or "fake").strip().lower()
    import email_fake
    if backend == "real":
        # real 분기에서만 무거운 의존성을 lazy 로 끌어온다.
        # calendar_write/llm_email 은 후속 슬라이스 산출물이라 아직 없을 수 있다.
        # 없으면 해당 부분만 fake 로 닫고 앱이 죽지 않게 한다(절대 raise 금지).
        try:
            from calendar_write import RealCalendarWriter
            calendar = RealCalendarWriter()
        except Exception:
            calendar = email_fake.FakeCalendarWriter()
        return EmailServices(
            gmail=_RealGmailAdapter(),
            calendar=calendar,
            llm=_real_llm(),
            backend="real",
        )
    return EmailServices(
        gmail=email_fake.FakeGmailClient.from_email_data(),
        calendar=email_fake.FakeCalendarWriter(),
        llm=email_fake.FakeEmailLLM(),
        backend="fake",
    )


def _real_llm():
    """실 LLM 구현 lazy import. 아직 없으면 fake LLM 으로 닫는다(절대 raise 금지)."""
    try:
        import llm_email
        return llm_email.RealEmailLLM()
    except Exception:
        import email_fake
        return email_fake.FakeEmailLLM()
