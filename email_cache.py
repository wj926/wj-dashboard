"""이메일 인박스 로컬 캐시.

백그라운드(타이머)가 Gmail 을 주기적으로 받아 이 캐시에 쌓고, /email 은 캐시에서
바로 읽어 즉시 렌더한다. 캐시는 비밀이 아니지만 메일 내용이므로 repo 밖
~/.config/wj-dashboard/ 에 두고 .gitignore 처리한다.

절대 raise 하지 않는다(실패 시 None / 빈 결과).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CACHE_PATH = Path(
    os.environ.get(
        "WJ_EMAIL_CACHE_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "email_cache.json"),
    )
)


def save(messages: list[dict]) -> bool:
    """정규화된 메시지 리스트를 캐시에 저장. 성공 True."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": int(time.time()), "messages": messages or []}
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CACHE_PATH)
        try:
            os.chmod(CACHE_PATH, 0o600)
        except OSError:
            pass
        return True
    except Exception:
        return False


def load() -> dict | None:
    """캐시 dict({fetched_at, messages}) 또는 None."""
    try:
        if not CACHE_PATH.exists():
            return None
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        data.setdefault("messages", [])
        data.setdefault("fetched_at", 0)
        return data
    except Exception:
        return None


def age_seconds() -> int | None:
    """캐시 나이(초). 없으면 None."""
    d = load()
    if not d:
        return None
    try:
        return max(0, int(time.time()) - int(d.get("fetched_at") or 0))
    except Exception:
        return None
