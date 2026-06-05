"""S4b(로컬): 승인한 일정 후보를 wj 캘린더(업무 탭)에 등록. Google 아님.

불변식: 등록은 오직 'approve' 클릭에서만. 같은 후보 중복 등록 안 됨. 되돌리기 가능.
"""
import os


def _post(client, url, body=None):
    return client.post(url, headers={"Sec-Fetch-Site": "same-origin"}, json=body or {})


def _focus_id():
    import email_data
    return email_data.build_view()["focus"]["id"]


def _reset_events():
    # 테스트 격리: wj 이벤트 + 이메일 상태(후보 포함) 파일을 비운다.
    for key, default in (("WJ_EVENTS_PATH", ""), ("WJ_EMAIL_STATE_PATH", "")):
        p = os.environ.get(key, default)
        if p:
            try:
                os.remove(p)
            except OSError:
                pass


def _detect_and_first_pending(client, mid):
    import email_store
    _post(client, f"/api/email/messages/{mid}/events/detect")
    return next((c for c in email_store.get_candidates(mid) if c["status"] == "pending"), None)


def test_approve_adds_to_wj_calendar(client):
    import wj_events
    _reset_events()
    mid = _focus_id()
    cand = _detect_and_first_pending(client, mid)
    assert cand, "pending 후보가 없다"
    r = _post(client, f"/api/email/messages/{mid}/events/{cand['id']}/approve")
    assert r.status_code == 200 and r.get_json()["ok"]
    # wj_events 에 ref=mid:cid 로 1건 들어갔는지
    assert wj_events.event_id_for_ref(f"{mid}:{cand['id']}")
    # 후보 상태 done
    import email_store
    assert any(c["id"] == cand["id"] and c["status"] == "done" for c in email_store.get_candidates(mid))


def test_approve_is_idempotent(client):
    import wj_events
    _reset_events()
    mid = _focus_id()
    cand = _detect_and_first_pending(client, mid)
    _post(client, f"/api/email/messages/{mid}/events/{cand['id']}/approve")
    _post(client, f"/api/email/messages/{mid}/events/{cand['id']}/approve")
    same_ref = [e for e in wj_events._load() if e.get("ref") == f"{mid}:{cand['id']}"]
    assert len(same_ref) == 1, "중복 등록되었다"


def test_undo_removes_from_calendar(client):
    import wj_events
    import email_store
    _reset_events()
    mid = _focus_id()
    cand = _detect_and_first_pending(client, mid)
    _post(client, f"/api/email/messages/{mid}/events/{cand['id']}/approve")
    assert wj_events.event_id_for_ref(f"{mid}:{cand['id']}")
    r = _post(client, f"/api/email/messages/{mid}/events/{cand['id']}/undo")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert not wj_events.event_id_for_ref(f"{mid}:{cand['id']}")
    assert any(c["id"] == cand["id"] and c["status"] == "pending" for c in email_store.get_candidates(mid))


def test_approve_missing_candidate_404(client):
    _reset_events()
    mid = _focus_id()
    r = _post(client, f"/api/email/messages/{mid}/events/no_cid/approve")
    assert r.status_code == 404


def test_approved_event_shows_in_work_calendar(client):
    """승인한 이메일 일정이 업무 탭(/) 캘린더 데이터에 합쳐져 나타난다."""
    import wj_events
    _reset_events()
    # 가까운 날짜 이벤트를 직접 추가(라우트 의존 없이 머지만 검증)
    from datetime import date
    iso = date.today().isoformat()
    wj_events.add_event(iso=iso, time_str="11:00", title="석사 본심사", ref="t:1")
    html = client.get("/").get_data(as_text=True)
    assert "석사 본심사" in html, "업무 탭 캘린더에 wj 이벤트가 안 보인다"
