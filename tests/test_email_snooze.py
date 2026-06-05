"""스누즈(나중에 다시 보기 / 나중에 답변) + 재부상.

불변식: 스누즈하면 메인 큐에서 빠진다. 단 복귀시각 도달 또는 p0(긴급)이면 다시 올라온다.
"""
import time
from email.utils import formatdate


def _post(client, url, body=None):
    return client.post(url, headers={"Sec-Fetch-Site": "same-origin"}, json=body or {})


def _msg(mid, subject, frm="A <a@x.com>", date=""):
    return {"id": mid, "thread_id": mid, "headers": {"from": frm, "subject": subject, "date": date},
            "body": {}, "label_ids": [], "snippet": ""}


def test_snooze_removes_from_queue():
    import email_store, email_view
    for x in ("s1", "s2"):
        email_store.unsnooze(x)
    msgs = [_msg("s1", "보통 메일"), _msg("s2", "다른 메일")]
    email_store.snooze("s1", int(time.time()) + 99999, "view")  # 먼 미래
    v = email_view._build_real_view(msgs, None)
    ids = [q["id"] for q in v["queue"]]
    assert "s1" not in ids and "s2" in ids, "스누즈한 메일이 큐에서 안 빠졌다"
    assert any(l["id"] == "s1" for l in v["later"]), "나중에 섹션에 안 들어갔다"


def test_resurface_when_urgent_p0():
    """스누즈 중이어도 p0(긴급)이 되면 큐로 다시 올라온다(재부상)."""
    import email_store, email_view
    email_store.unsnooze("u1")
    msgs = [_msg("u1", "긴급 회신 부탁", frm="교수 <prof@uni.ac.kr>", date=formatdate(time.time()))]
    email_store.snooze("u1", int(time.time()) + 99999, "view")  # 시간상으론 아직
    v = email_view._build_real_view(msgs, None)
    q = next((x for x in v["queue"] if x["id"] == "u1"), None)
    assert q is not None, "긴급인데 재부상 안 됨"
    assert q["priority"] == "p0" and q["resurfaced"]


def test_resurface_when_time_reached():
    import email_store, email_view
    email_store.unsnooze("t1")
    msgs = [_msg("t1", "보통 메일")]
    email_store.snooze("t1", int(time.time()) - 10, "view")  # 복귀시각 지남
    v = email_view._build_real_view(msgs, None)
    q = next((x for x in v["queue"] if x["id"] == "t1"), None)
    assert q is not None and q["resurfaced"]


def test_snooze_and_unsnooze_routes(client):
    import email_store
    mid = "m_minjun"
    r = _post(client, f"/api/email/messages/{mid}/snooze", {"preset": "3h", "kind": "reply"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert r.get_json()["until"] > int(time.time())
    sn = email_store.snoozed()
    assert mid in sn and sn[mid]["kind"] == "reply"
    assert _post(client, f"/api/email/messages/{mid}/unsnooze").get_json()["ok"]
    assert mid not in email_store.snoozed()


def test_unsnooze_missing_returns_404(client):
    """스누즈 상태 아닌 메일 올리기는 거짓 성공이 아니라 404 (P1 검수 반영)."""
    import email_store
    email_store.unsnooze("never_snoozed")
    r = _post(client, "/api/email/messages/never_snoozed/unsnooze")
    assert r.status_code == 404 and r.get_json()["ok"] is False


def test_later_pane_renders(client):
    assert client.get("/api/email/later-pane").status_code == 200
