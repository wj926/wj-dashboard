"""S3a: 답장 초안 생성/폐기. 핵심 불변식 = 생성≠발송, 인박스 로딩은 LLM 미호출."""


def _post(client, url, body=None):
    return client.post(url, headers={"Sec-Fetch-Site": "same-origin"}, json=body or {})


def _focus_id():
    import email_data
    return email_data.build_view()["focus"]["id"]


def test_inbox_load_does_not_call_llm(client, monkeypatch):
    """인박스(/email) 렌더만으로는 LLM 이 호출되면 안 된다. 버튼 클릭에서만 호출."""
    import email_fake
    calls = {"n": 0}
    real = email_fake.FakeEmailLLM.generate_reply_draft

    def spy(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(email_fake.FakeEmailLLM, "generate_reply_draft", spy)
    client.get("/email")
    assert calls["n"] == 0, "인박스 로딩이 LLM 을 호출했다"

    mid = _focus_id()
    _post(client, f"/api/email/messages/{mid}/draft/generate", {"tone": "정중·간결"})
    assert calls["n"] == 1, "초안 생성 버튼이 LLM 을 호출하지 않았다"


def test_generate_saves_unsent_draft(client):
    import email_store
    mid = _focus_id()
    r = _post(client, f"/api/email/messages/{mid}/draft/generate", {"tone": "정중·간결"})
    assert r.status_code == 200 and r.get_json()["ok"]
    saved = email_store.get_draft(mid)
    assert saved.get("text"), "초안 텍스트가 저장되지 않았다"
    assert saved.get("status") == "unsent"


def test_generate_is_not_send(client):
    """생성은 발송이 아니다. 발송 표시(sent)가 절대 켜지면 안 된다."""
    import email_store
    mid = _focus_id()
    _post(client, f"/api/email/messages/{mid}/draft/generate", {"tone": "정중·간결"})
    assert email_store.is_sent(mid) is False


def test_no_send_endpoint_yet(client):
    """S3a 에는 발송 경로가 없어야 한다(있으면 안 됨). S3b 에서 승인 게이트와 함께 추가."""
    mid = _focus_id()
    r = client.post(
        f"/api/email/drafts/{mid}/send",
        headers={"Sec-Fetch-Site": "same-origin"}, json={},
    )
    assert r.status_code == 404


def test_discard_clears_draft(client):
    import email_store
    mid = _focus_id()
    _post(client, f"/api/email/messages/{mid}/draft/generate", {"tone": "정중·간결"})
    assert email_store.get_draft(mid).get("text")
    r = _post(client, f"/api/email/messages/{mid}/draft/discard")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert email_store.get_draft(mid) == {}


def test_draft_pane_renders(client):
    mid = _focus_id()
    html = client.get(f"/api/email/draft-pane?id={mid}").get_data(as_text=True)
    assert ("초안" in html)
