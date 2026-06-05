"""S1 — /email 라우트와 M4 목업 화면 회귀 방지."""
import re


def test_get_email_200_renders_focus_shell(client):
    """/email 이 200 이고 좌(처리 큐)/중(본문)/우(3탭) + 승인 게이트 문구를 렌더."""
    r = client.get("/email")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # 좌측 큐 / 우측 탭 3개
    assert "처리 큐" in html
    assert "AI 초안" in html
    assert "일정 후보" in html
    assert "팔로업" in html
    # 승인 게이트 문구 (발송/등록은 항상 승인)
    assert "승인 후 발송" in html
    assert "캘린더에 추가" in html
    assert "미발송 초안" in html


def test_email_tab_active(client):
    """active_tab=email 이면 메일 탭에 on 클래스가 붙는다."""
    html = client.get("/email").get_data(as_text=True)
    assert 'href="/email"' in html
    assert 'class="tt on" href="/email"' in html


def test_mock_badge_visible(client):
    """실 Gmail 미연동 상태가 화면에 분명히 표시된다."""
    html = client.get("/email").get_data(as_text=True)
    assert ("Gmail 미연동" in html) or ("정적 목업" in html)


def test_no_external_cdn_in_email_focus(client):
    """이메일 탭은 외부 CDN 없이 렌더된다(오프라인/내부망 전제)."""
    html = client.get("/email").get_data(as_text=True)
    for bad in ("cdn.", "unpkg", "jsdelivr", "cdnjs",
                "fonts.googleapis", "fonts.gstatic", "d3js.org"):
        assert bad not in html, f"외부 CDN 참조 발견: {bad}"


def test_no_positive_auto_send_wording(client):
    """자동 발송/등록처럼 보이는 긍정형 문구가 없어야 한다(승인 게이트 보호)."""
    html = client.get("/email").get_data(as_text=True)
    for bad in ("자동 발송", "자동발송", "자동 전송", "자동 등록", "알아서 발송", "캘린더에 자동"):
        assert bad not in html, f"자동 발송/등록 오해 문구 발견: {bad}"
