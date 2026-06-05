"""S1 — 비밀/토큰/상태 파일이 repo 에 유입되지 않는지."""
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]

_SECRET_NAMES = {
    "google_client_secret.json", "google_token.json",
    "token.json", "email_state.json",
}


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True
    )
    return out.stdout.splitlines()


def test_no_secret_files_in_repo():
    bad = []
    for f in _tracked_files():
        name = pathlib.Path(f).name
        if name in _SECRET_NAMES or name.startswith("client_secret"):
            bad.append(f)
    assert not bad, f"repo 에 비밀/토큰/상태 파일이 추적됨: {bad}"


def test_gitignore_has_secret_patterns():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pat in ("google_token.json", "google_client_secret.json",
                "email_state.json", "_shots/"):
        assert pat in gi, f".gitignore 에 {pat} 패턴 누락"


def test_sanitizer_removes_script_remote_image_and_event_attrs():
    """gmail.sanitize_email_html 가 <script>, onclick, remote img 를 제거한다."""
    import gmail
    raw = ('<p onclick="x()">hi<script>x()</script>'
           '<img src="https://tracker/p.png"></p>')
    clean = gmail.sanitize_email_html(raw)
    assert "<script" not in clean
    assert "onclick" not in clean
    assert "https://tracker" not in clean
    assert "<img" not in clean
    # 안전한 본문 텍스트는 살아있어야 한다.
    assert "hi" in clean


def test_body_safe_only_after_sanitize():
    """extract_body 의 html_sanitized 는 sanitize 를 거친 값만 담는다(원본 그대로 X)."""
    import gmail
    import base64

    danger = ('<p onclick="evil()">hello<script>steal()</script>'
              '<img src="https://tracker.example/x.png"></p>')
    data = base64.urlsafe_b64encode(danger.encode("utf-8")).decode("ascii")
    payload = {"mimeType": "text/html", "body": {"data": data}}
    body = gmail.extract_body(payload)
    html = body.get("html_sanitized", "")
    assert "<script" not in html
    assert "onclick" not in html
    assert "tracker.example" not in html
    assert "hello" in html


def test_sanitizer_drops_style_and_script_block_contents():
    """<style>/<script> 의 내부 텍스트(CSS/JS)까지 제거한다(본문에 CSS 줄줄 새는 것 방지)."""
    import gmail
    raw = ('<style>.x{display:none;min-height:480px;background:#fff}</style>'
           '<p>본문 내용</p><script>var a=1;steal()</script>')
    clean = gmail.sanitize_email_html(raw)
    assert "min-height" not in clean
    assert "display:none" not in clean
    assert "steal()" not in clean
    assert "본문 내용" in clean
