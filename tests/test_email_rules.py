"""처리 규칙(말로 가르치면 똑똑해짐). 규칙 추가/적용/토글/삭제."""
import os


def _post(client, url, body=None):
    return client.post(url, headers={"Sec-Fetch-Site": "same-origin"}, json=body or {})


def _reset():
    try:
        os.remove(os.environ["WJ_EMAIL_RULES_PATH"])
    except OSError:
        pass


def test_apply_rule_bumps_priority():
    """발신자 매칭 priority_up 규칙이 p2 메일을 p1 으로 올린다."""
    import email_rules
    _reset()
    email_rules.add_rule("두나무 메일 위로", {"label": "두나무 우선", "effect": "priority_up",
                                          "match": {"from": ["dunamu"], "subject_kw": []}})
    msg = {"headers": {"from": "두나무 <ir@dunamu.com>", "subject": "안내"}}
    score = {"priority": "p2", "reasons": [], "has_event": False, "category": ""}
    out = email_rules.apply_to(msg, score)
    assert out["priority"] == "p1"
    assert any("규칙: 두나무 우선" == c for c in out["reasons"])


def test_disabled_rule_not_applied():
    import email_rules
    _reset()
    r = email_rules.add_rule("x", {"label": "L", "effect": "priority_up", "match": {"from": ["dunamu"], "subject_kw": []}})
    email_rules.set_enabled(r["id"], False)
    msg = {"headers": {"from": "a@dunamu.com", "subject": "s"}}
    out = email_rules.apply_to(msg, {"priority": "p2", "reasons": []})
    assert out["priority"] == "p2"


def test_add_rule_route(client):
    import email_rules
    _reset()
    r = _post(client, "/api/email/rules/add", {"text": "두나무 메일은 항상 위로"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert len(email_rules.list_rules()) == 1


def test_inbox_load_does_not_call_parse_rule(client, monkeypatch):
    """인박스 로딩/규칙 적용은 LLM(parse_rule)을 호출하지 않는다. 추가 때만."""
    import email_fake
    calls = {"n": 0}
    real = email_fake.FakeEmailLLM.parse_rule

    def spy(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(email_fake.FakeEmailLLM, "parse_rule", spy)
    client.get("/email")
    assert calls["n"] == 0
    _post(client, "/api/email/rules/add", {"text": "영수증 메일은 영수증함 후보로"})
    assert calls["n"] == 1


def test_toggle_and_delete_routes(client):
    import email_rules
    _reset()
    rule = email_rules.add_rule("x", {"label": "L", "effect": "priority_up", "match": {"from": ["a"], "subject_kw": []}})
    rid = rule["id"]
    assert _post(client, f"/api/email/rules/{rid}/toggle", {"enabled": False}).get_json()["ok"]
    assert email_rules.list_rules()[0]["enabled"] is False
    assert _post(client, f"/api/email/rules/{rid}/delete").get_json()["ok"]
    assert email_rules.list_rules() == []
    assert _post(client, f"/api/email/rules/{rid}/delete").status_code == 404


def test_rules_pane_renders(client):
    html = client.get("/api/email/rules-pane").get_data(as_text=True)
    assert "규칙" in html


def test_add_rule_empty_400(client):
    r = _post(client, "/api/email/rules/add", {"text": "   "})
    assert r.status_code == 400


def test_platform_token_stripped_from_match(client):
    """중계 플랫폼명(이클래스/eclass)은 match.from 에서 걸러져 과매칭을 막는다 (P1 검수 반영)."""
    import email_rules
    _reset()
    rule = email_rules.add_rule("이호정 메일 위로", {
        "label": "이호정 위로", "effect": "priority_up",
        "match": {"from": ["이호정", "동국대학교 이클래스", "eclass"], "subject_kw": []},
    })
    assert "이호정" in rule["match"]["from"]
    assert not any(("eclass" in s.lower() or "이클래스" in s) for s in rule["match"]["from"])
    # 이호정에게는 적용, 같은 플랫폼의 다른 발신자(고신영)에게는 적용 안 됨
    up = email_rules.apply_to({"headers": {"from": "이호정 (이클래스) <eclass@dongguk.edu>", "subject": "x"}},
                              {"priority": "p2", "reasons": []})
    assert up["priority"] == "p1"
    other = email_rules.apply_to({"headers": {"from": "고신영 (이클래스) <eclass@dongguk.edu>", "subject": "x"}},
                                 {"priority": "p2", "reasons": []})
    assert other["priority"] == "p2", "다른 발신자까지 과매칭됨"
