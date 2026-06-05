"""여기서 지우기 / 발신자 제외 / 영수증 저장 (로컬, Gmail 무변경) 동작."""


def _post(client, url):
    # CSRF guard 통과용 same-origin 헤더
    return client.post(url, headers={"Sec-Fetch-Site": "same-origin"}, json={})


def test_hide_endpoint_adds_to_store(client):
    import email_data
    import email_store
    mid = email_data.build_view()["focus"]["id"]
    r = _post(client, f"/api/email/messages/{mid}/hide")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert mid in email_store.hidden_ids()


def test_save_receipt_stores_snapshot(client):
    import email_data
    import email_store
    mid = email_data.build_view()["focus"]["id"]
    r = _post(client, f"/api/email/messages/{mid}/save-receipt")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert mid in [x.get("id") for x in email_store.receipts()]


def test_receipts_page_renders(client):
    html = client.get("/email/receipts").get_data(as_text=True)
    assert "영수증함" in html


def test_hidden_excluded_from_real_view():
    """real view 가 hidden id 를 큐에서 빼는지(by_id 엔 남김)."""
    import email_store
    import email_view
    msgs = [
        {"id": "a1", "headers": {"from": "X <x@a.com>", "subject": "s1"}, "body": {}, "label_ids": [], "snippet": ""},
        {"id": "a2", "headers": {"from": "Y <y@a.com>", "subject": "s2"}, "body": {}, "label_ids": [], "snippet": ""},
    ]
    email_store.hide("a1")
    try:
        view = email_view._build_real_view(msgs, None)
        ids = [q["id"] for q in view["queue"]]
        assert "a1" not in ids and "a2" in ids
    finally:
        email_store.unhide("a1")


def test_unhide_restores(client):
    import email_data
    import email_store
    mid = email_data.build_view()["focus"]["id"]
    _post(client, f"/api/email/messages/{mid}/hide")
    assert mid in email_store.hidden_ids()
    r = _post(client, f"/api/email/messages/{mid}/unhide")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert mid not in email_store.hidden_ids()
