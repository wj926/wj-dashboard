#!/usr/bin/env python3
"""/email 을 test client 로 렌더해 정적 HTML 파일로 떨군다(시각 캡처용).

http 서버 없이 file:// 로 캡처하기 위한 용도. _tabs/url_for 등은 test client 가
실제로 렌더하므로 충실도가 유지되고, static CSS 링크만 절대 file 경로로 치환한다.
"""
import os
import pathlib
import sys

os.environ.setdefault("WJ_MODE", "demo")
os.environ.setdefault("WJ_EMAIL_BACKEND", "fake")
os.environ.setdefault("WJ_EMAIL_LLM_BACKEND", "fake")

import app  # noqa: E402

out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "/tmp/wj-email-render/email.html"
out_path = pathlib.Path(out)
out_path.parent.mkdir(parents=True, exist_ok=True)

client = app.app.test_client()
resp = client.get("/email")
html = resp.get_data(as_text=True)

static_dir = pathlib.Path("static").resolve()
# url_for('static', filename=...) 는 "/static/..." 로 렌더된다. file:// 절대경로로.
html = html.replace('href="/static/', f'href="file://{static_dir}/')
html = html.replace('src="/static/', f'src="file://{static_dir}/')

out_path.write_text(html, encoding="utf-8")
print(f"status={resp.status_code} -> {out_path} ({len(html)} bytes)")
