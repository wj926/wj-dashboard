"""인박스 필터 (업무 중심). 광고/소셜 카테고리 + 사용자 제외목록을 Gmail 쿼리로.

WJ 가 "이 발신자/이런 메일 빼줘" 하면 제외목록에 쌓고, 다음 fetch 부터 안 가져온다.
설정 파일: ~/.config/wj-dashboard/email_filters.json (repo 밖, .gitignore).
절대 raise 하지 않는다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FILTERS_PATH = Path(
    os.environ.get(
        "WJ_EMAIL_FILTERS_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "email_filters.json"),
    )
)

# 기본값: 업무 중심이라 광고/소셜 카테고리는 기본 제외.
_DEFAULTS = {
    "exclude_categories": ["promotions", "social"],
    "exclude_senders": [],   # 예: "noreply@foo.com" 또는 도메인 "foo.com"
    "exclude_terms": [],     # 제목/본문 키워드
    "newer_than": "30d",
}


def load() -> dict:
    try:
        if FILTERS_PATH.exists():
            d = json.loads(FILTERS_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                out = dict(_DEFAULTS)
                out.update(d)
                return out
    except Exception:
        pass
    return dict(_DEFAULTS)


def save(filters: dict) -> bool:
    try:
        FILTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        FILTERS_PATH.write_text(json.dumps(filters, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(FILTERS_PATH, 0o600)
        except OSError:
            pass
        return True
    except Exception:
        return False


def add_exclude_sender(sender: str) -> dict:
    """발신자/도메인을 제외목록에 추가하고 저장. 갱신된 filters 반환."""
    f = load()
    s = (sender or "").strip().lower()
    if s and s not in [x.lower() for x in f.get("exclude_senders", [])]:
        f.setdefault("exclude_senders", []).append(s)
        save(f)
    return f


def build_query() -> str:
    """현재 필터로 Gmail 검색 쿼리 문자열 생성."""
    f = load()
    parts = ["in:inbox"]
    nt = (f.get("newer_than") or "").strip()
    if nt:
        parts.append(f"newer_than:{nt}")
    for cat in f.get("exclude_categories", []) or []:
        c = (cat or "").strip()
        if c:
            parts.append(f"-category:{c}")
    for snd in f.get("exclude_senders", []) or []:
        s = (snd or "").strip()
        if s:
            parts.append(f"-from:{s}")
    for term in f.get("exclude_terms", []) or []:
        t = (term or "").strip()
        if t:
            parts.append(f'-"{t}"')
    return " ".join(parts)
