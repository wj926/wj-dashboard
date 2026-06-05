"""pytest 공통 설정 — 이메일 탭 하네스.

app import 전에 env 를 고정한다(WJ_MODE 미설정이면 settings 가 죽고,
demo 모드면 Basic 인증도 우회되어 test client 가 401 안 맞는다).
실 Gmail 대신 fake 백엔드를 기본값으로 둔다(S2+ 에서 사용).
"""
import os

os.environ.setdefault("WJ_MODE", "demo")
os.environ.setdefault("WJ_EMAIL_BACKEND", "fake")
os.environ.setdefault("WJ_EMAIL_LLM_BACKEND", "fake")
os.environ.setdefault("WJ_EMAIL_STATE_PATH", "/tmp/wj-email-state-test.json")
os.environ.setdefault("WJ_EVENTS_PATH", "/tmp/wj-events-test.json")
os.environ.setdefault("WJ_RECEIPTS_DIR", "/tmp/wj-receipts-test")
os.environ.setdefault("WJ_EMAIL_RULES_PATH", "/tmp/wj-rules-test.json")

import pytest


@pytest.fixture
def client():
    import app
    app.app.config.update(TESTING=True)
    return app.app.test_client()
