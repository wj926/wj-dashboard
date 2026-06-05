"""S4a: 일정 감지/후보 생성. 핵심 불변식 = 감지≠캘린더쓰기, 인박스 로딩은 감지 미호출."""


def _post(client, url, body=None):
    return client.post(url, headers={"Sec-Fetch-Site": "same-origin"}, json=body or {})


def _focus_id():
    import email_data
    return email_data.build_view()["focus"]["id"]


def test_extract_date_sentences_finds_dates():
    import email_events
    text = "안녕하세요. 다음 주 월요일(6월 9일) 오후 2시에 면담 가능할까요? 감사합니다."
    sents = email_events.extract_date_sentences(text)
    assert sents and any("6월 9일" in s["source"] or "오후 2시" in s["source"] for s in sents)


def test_no_date_text_yields_no_sentences():
    import email_events
    assert email_events.extract_date_sentences("그냥 안부 인사드립니다. 잘 지내시죠.") == []


def test_inbox_load_does_not_call_detect_llm(client, monkeypatch):
    """인박스(/email) 렌더만으로는 일정 감지 LLM 이 호출되면 안 된다."""
    import email_fake
    calls = {"n": 0}
    real = email_fake.FakeEmailLLM.detect_events

    def spy(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(email_fake.FakeEmailLLM, "detect_events", spy)
    client.get("/email")
    assert calls["n"] == 0, "인박스 로딩이 일정 감지 LLM 을 호출했다"

    mid = _focus_id()
    _post(client, f"/api/email/messages/{mid}/events/detect")
    assert calls["n"] == 1, "감지 버튼이 LLM 을 호출하지 않았다"


def test_detect_does_not_touch_calendar(client, monkeypatch):
    """감지는 캘린더 쓰기가 아니다. insert_event 가 절대 호출되면 안 된다."""
    import email_fake
    calls = {"n": 0}
    real = email_fake.FakeCalendarWriter.insert_event

    def spy(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(email_fake.FakeCalendarWriter, "insert_event", spy)
    mid = _focus_id()
    _post(client, f"/api/email/messages/{mid}/events/detect")
    assert calls["n"] == 0, "감지가 캘린더 insert 를 호출했다"


def test_no_calendar_approve_endpoint_yet(client):
    """S4a 에는 캘린더 등록 경로가 없어야 한다(S4b 에서 승인 게이트와 함께 추가)."""
    mid = _focus_id()
    r = client.post(
        f"/api/email/events/x/approve",
        headers={"Sec-Fetch-Site": "same-origin"}, json={},
    )
    assert r.status_code == 404


def test_detect_saves_candidates_with_ids(client):
    import email_store
    mid = _focus_id()
    r = _post(client, f"/api/email/messages/{mid}/events/detect")
    assert r.status_code == 200 and r.get_json()["ok"]
    cands = email_store.get_candidates(mid)
    assert cands, "후보가 저장되지 않았다"
    assert all(c.get("id") for c in cands), "후보에 안정 id 가 없다"
    assert all(c.get("status") in ("pending", "done", "ignored") for c in cands)


def test_ignore_and_restore_candidate(client):
    import email_store
    mid = _focus_id()
    _post(client, f"/api/email/messages/{mid}/events/detect")
    cid = email_store.get_candidates(mid)[0]["id"]
    assert _post(client, f"/api/email/messages/{mid}/events/{cid}/ignore").get_json()["ok"]
    assert any(c["id"] == cid and c["status"] == "ignored" for c in email_store.get_candidates(mid))
    assert _post(client, f"/api/email/messages/{mid}/events/{cid}/restore").get_json()["ok"]
    assert any(c["id"] == cid and c["status"] == "pending" for c in email_store.get_candidates(mid))


def test_cal_pane_renders(client):
    mid = _focus_id()
    html = client.get(f"/api/email/cal-pane?id={mid}").get_data(as_text=True)
    assert "후보" in html


def test_ignore_missing_candidate_returns_404(client):
    """없는 후보 무시 요청은 거짓 성공이 아니라 404 (P1 검수 반영)."""
    mid = _focus_id()
    r = _post(client, f"/api/email/messages/{mid}/events/no_such_cid/ignore")
    assert r.status_code == 404 and r.get_json()["ok"] is False


def test_redetect_preserves_ignored_status(client):
    """다시 감지해도 이미 '무시'한 후보가 되살아나지 않는다 (P1 검수 반영)."""
    import email_store
    mid = _focus_id()
    _post(client, f"/api/email/messages/{mid}/events/detect")
    cands = email_store.get_candidates(mid)
    # pending 인 후보 하나를 무시
    target = next((c for c in cands if c["status"] == "pending"), cands[0])
    cid, sig = target["id"], (target["title"], (target.get("source") or "")[:60])
    _post(client, f"/api/email/messages/{mid}/events/{cid}/ignore")
    # 같은 메일을 다시 감지 (fake 는 같은 fixture 후보를 돌려줌)
    _post(client, f"/api/email/messages/{mid}/events/detect")
    after = email_store.get_candidates(mid)
    same = next((c for c in after if (c["title"], (c.get("source") or "")[:60]) == sig), None)
    assert same is not None and same["status"] == "ignored", "다시 감지가 무시 상태를 덮어썼다"
