#!/usr/bin/env python3
"""개인 Gmail OAuth 최초 동의 (S2, readonly 만). 한 번 실행해 토큰을 저장한다.

실행:
  /home/dami/miniconda3/bin/python scripts/email_oauth_login.py

전제:
  1) Google Cloud 에서 OAuth client(Desktop app) 를 만들고 client_secret.json 을
     ~/.config/wj-dashboard/google_client_secret.json 에 둔다.
     (또는 WJ_GOOGLE_CLIENT_SECRET 환경변수로 경로 지정)
  2) google-auth-oauthlib 설치: pip install -r requirements.txt

동작:
  브라우저로 동의를 받고, refresh token 을 ~/.config/wj-dashboard/google_token.json 에 저장.
  이후 WJ_EMAIL_BACKEND=real 이면 /email 이 실제 Gmail 인박스를 읽는다.
  (테스트 모드 OAuth 는 refresh token 이 약 7일마다 만료될 수 있다. 그때 이 스크립트를 다시 실행.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gmail  # noqa: E402  (모듈 import 는 안전, google 라이브러리는 함수 내부 lazy import)


def main() -> int:
    cs = gmail.CLIENT_SECRET_PATH
    if not cs.exists():
        print(f"[oauth] client secret 파일이 없습니다: {cs}")
        print("  Google Cloud Console > Credentials 에서 OAuth client(Desktop app) 를 만들고")
        print("  내려받은 client_secret.json 을 위 경로에 두세요.")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except Exception as e:
        print(f"[oauth] google-auth-oauthlib 미설치: {e}")
        print("  설치: /home/dami/miniconda3/bin/python -m pip install -r requirements.txt")
        return 1

    print(f"[oauth] client secret: {cs}")
    print(f"[oauth] 요청 scope: {gmail.SCOPES_READONLY}")
    flow = InstalledAppFlow.from_client_secrets_file(str(cs), gmail.SCOPES_READONLY)
    # 로컬 콜백 서버를 띄워 브라우저 동의를 받는다(Desktop app 클라이언트).
    # 헤드리스 셸이라 브라우저 자동실행은 끄고, URL 을 출력해 사용자가 직접 연다.
    creds = flow.run_local_server(
        port=0,
        open_browser=False,
        authorization_prompt_message="\n[oauth] 아래 URL 을 (이 머신의) 브라우저에서 열어 동의하세요:\n\n{url}\n\n동의 후 자동으로 완료됩니다. 대기 중...\n",
    )

    gmail.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    gmail.TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(gmail.TOKEN_PATH, 0o600)
    except OSError:
        pass

    print(f"[oauth] 토큰 저장 완료: {gmail.TOKEN_PATH}")
    print("  이제 WJ_EMAIL_BACKEND=real 로 두면 /email 이 실제 Gmail 을 읽습니다.")
    print("  빠른 확인: WJ_EMAIL_BACKEND=real /home/dami/miniconda3/bin/python -c \\")
    print("    \"import gmail; print(len(gmail.list_inbox(max_results=3)))\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
