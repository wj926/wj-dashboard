#!/usr/bin/env python3
"""원격/헤드리스용 수동 OAuth (브라우저 콜백 없이 코드 붙여넣기).

서버(localhost)와 동의하는 컴퓨터가 다를 때 사용. 동의 후 뜨는
"localhost 연결 거부" 페이지의 주소창 URL 에 인증 code 가 들어있고, 그걸 붙여 토큰 발급.

  geturl                  -> 동의 URL 출력 (+ /tmp 에 PKCE verifier/state 저장)
  exchange "<code|URL>"   -> 코드로 토큰 발급/저장 (~/.config/wj-dashboard/google_token.json)
"""
import os
import sys
import json
import base64
import hashlib
import secrets
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gmail  # noqa: E402

REDIRECT = "http://localhost:34771/"
STASH = "/tmp/wj-oauth-manual.json"


def _flow():
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_secrets_file(
        str(gmail.CLIENT_SECRET_PATH), scopes=gmail.SCOPES_READONLY, redirect_uri=REDIRECT
    )


def geturl():
    if not gmail.CLIENT_SECRET_PATH.exists():
        print("[oauth] client secret 없음:", gmail.CLIENT_SECRET_PATH)
        return 1
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    flow = _flow()
    url, state = flow.authorization_url(
        access_type="offline", prompt="consent",
        code_challenge=challenge, code_challenge_method="S256",
    )
    json.dump({"verifier": verifier, "state": state}, open(STASH, "w"))
    print(url)
    return 0


def exchange(arg):
    code = (arg or "").strip()
    if code.startswith("http"):
        code = (parse_qs(urlparse(code).query).get("code") or [""])[0]
    if not code:
        print("[oauth] code 를 못 찾음. '연결 거부' 페이지의 주소창 전체 URL 또는 code 값을 주세요.")
        return 1
    stash = json.load(open(STASH)) if os.path.exists(STASH) else {}
    flow = _flow()
    try:
        flow.fetch_token(code=code, code_verifier=stash.get("verifier"))
    except Exception as e:
        print("[oauth] 토큰 교환 실패:", e)
        print("  (code 가 만료됐거나 한 번 쓴 코드일 수 있습니다. geturl 부터 다시.)")
        return 1
    creds = flow.credentials
    gmail.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    gmail.TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(gmail.TOKEN_PATH, 0o600)
    except OSError:
        pass
    print("[oauth] 토큰 저장 완료:", gmail.TOKEN_PATH)
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "geturl"
    if mode == "geturl":
        raise SystemExit(geturl())
    if mode == "exchange":
        if len(sys.argv) < 3:
            print("usage: email_oauth_manual.py exchange '<code 또는 redirected URL>'")
            raise SystemExit(1)
        raise SystemExit(exchange(sys.argv[2]))
    print("usage: email_oauth_manual.py [geturl|exchange <code|url>]")
    raise SystemExit(1)
