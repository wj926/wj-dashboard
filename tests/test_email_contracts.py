"""S2 실/Fake Gmail 계약, 장애 무전파, LLM 비호출 검증."""
import inspect

import email_fake
import email_view
import gmail


# gmail.py 모듈 레벨 public 함수 <-> FakeGmailClient 메서드 정합 대상
_SHARED_GMAIL_FUNCS = [
    "list_inbox",
    "get_message",
    "get_thread",
    "list_drafts",
    "build_followups",
]


def _params_without_self(sig: inspect.Signature) -> list[tuple]:
    """(name, default) 목록. self/cls 는 제외해 모듈함수 <-> 메서드 비교를 맞춘다."""
    out = []
    for name, p in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        out.append((name, p.default))
    return out


def test_real_and_fake_gmail_signatures_match():
    """gmail.py public 함수와 FakeGmailClient 메서드 시그니처(이름/기본값)가 일치한다."""
    fake = email_fake.FakeGmailClient.from_email_data()
    for fn_name in _SHARED_GMAIL_FUNCS:
        real_fn = getattr(gmail, fn_name)
        fake_fn = getattr(fake, fn_name)
        real_params = _params_without_self(inspect.signature(real_fn))
        fake_params = _params_without_self(inspect.signature(fake_fn))
        assert real_params == fake_params, (
            f"{fn_name} 시그니처 불일치: real={real_params} fake={fake_params}"
        )


def test_public_gmail_functions_never_raise_on_fault(monkeypatch):
    """장애 주입(google_down/auth_expired)에서도 fake public 메서드가 예외를 던지지 않고
    빈 결과/ok:false 를 반환한다."""
    for mode in ("google_down", "auth_expired"):
        monkeypatch.setenv("WJ_EMAIL_FAKE_MODE", mode)
        gm = email_fake.FakeGmailClient.from_email_data()
        assert gm.list_inbox() == []
        assert gm.get_message("m_minjun") == {}
        assert gm.get_thread("t_1") == {}
        assert gm.list_drafts() == []
        assert gm.build_followups() == []

        cal = email_fake.FakeCalendarWriter()
        r_ins = cal.insert_event({"id": "evt_1", "title": "x"})
        assert r_ins.get("ok") is False
        r_del = cal.delete_event("fake_evt_1")
        assert r_del.get("ok") is False


def test_public_calendar_functions_never_raise_on_fault(monkeypatch):
    """Calendar 쓰기 fake 가 장애에서 ok:false 로 닫힌다."""
    monkeypatch.setenv("WJ_EMAIL_FAKE_MODE", "google_down")
    cal = email_fake.FakeCalendarWriter()
    assert cal.insert_event({"id": "e", "title": "t"}).get("ok") is False
    assert cal.delete_event("fake_evt_e").get("ok") is False


def test_llm_not_called_by_view_build(monkeypatch):
    """build_email_view 호출 후 FakeEmailLLM call count == 0 (인박스 로딩은 LLM 무호출)."""
    monkeypatch.setenv("WJ_EMAIL_BACKEND", "fake")
    import email_services

    services = email_services.get_email_services()
    assert isinstance(services.llm, email_fake.FakeEmailLLM)
    assert services.llm.call_count == 0

    view = email_view.build_email_view(services=services)
    assert isinstance(view, dict)
    # view 빌드만으로는 LLM 이 절대 호출되면 안 된다.
    assert services.llm.call_count == 0
