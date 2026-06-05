"""본문 인용문 접기: text/plain 본문에서 이전 대화를 분리해 접는다."""


def test_render_text_body_folds_quote():
    import email_view
    text = (
        "팀장님, 안녕하세요. 오늘 회의 자료 보냅니다. 확인 부탁드립니다.\n\n"
        "-----Original Message-----\n"
        "From: 팀장 <lead@x.com>\nSent: 2026-06-01\nSubject: 회의\n\n지난 자료입니다."
    )
    html = email_view._render_text_body(text)
    assert "quoted-toggle" in html and 'class="quoted"' in html
    # 최신 내용은 접힘 밖, 인용은 quoted 안
    assert "오늘 회의 자료 보냅니다" in html.split("quoted-toggle")[0]
    assert "지난 자료입니다" in html.split('class="quoted"')[1]


def test_render_text_body_no_quote_plain():
    import email_view
    html = email_view._render_text_body("짧은 안부 인사입니다. 잘 지내시죠?")
    assert "quoted" not in html
    assert "짧은 안부" in html
