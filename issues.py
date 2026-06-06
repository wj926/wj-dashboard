"""이슈 트래커 저장 (issues.json, repo 밖 ~/.config/wj-dashboard/).

이슈 1건: 제목/내용/작성자/상태(open|closed)/이미지/댓글/생성시각.
앱 관련 버그·할 일을 그때그때 적어두고, 해결되면 댓글로 닫는다.
절대 raise 하지 않는다(실패 시 안전 기본값).
"""
from __future__ import annotations

import json as _json
import os
import time
from pathlib import Path

_PATH = Path(
    os.environ.get(
        "WJ_ISSUES_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "issues.json"),
    )
)


def _load() -> list:
    try:
        d = _json.loads(_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(items: list) -> bool:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(_json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, _PATH)
        try:
            os.chmod(_PATH, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _norm(s, default: str = "") -> str:
    return (str(s).strip() if s is not None else "") or default


def _safe_imgs(images) -> list:
    """첨부 이미지 URL 화이트리스트: 로컬 업로드 경로(/...) 또는 https 만 허용.

    javascript:/data:/그 외 스킴은 버린다(href/src 주입 방지).
    """
    out = []
    for x in images or []:
        u = _norm(x)
        if u and ((u.startswith("/") and not u.startswith("//")) or u.lower().startswith("https://")):
            out.append(u)
    return out


def _label(ts) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(int(ts or 0)).strftime("%m/%d %H:%M")
    except Exception:
        return ""


def list_issues() -> list:
    """열린 이슈 먼저(최신순), 해결된 건 아래(최신순). 표시용 날짜 라벨 부여."""
    items = _load()
    items.sort(key=lambda x: (x.get("status") == "closed", -(x.get("created") or 0)))
    for it in items:
        it["created_label"] = _label(it.get("created"))
        for c in it.get("comments", []) or []:
            c["created_label"] = _label(c.get("created"))
    return items


def get(issue_id: str) -> dict:
    for it in _load():
        if it.get("id") == issue_id:
            return it
    return {}


def add(title: str, body: str, author: str, images=None) -> dict:
    title = _norm(title)
    if not title:
        return {}
    items = _load()
    issue = {
        "id": "iss_" + str(int(time.time() * 1000)),
        "title": title,
        "body": _norm(body),
        "author": _norm(author, "WJ"),
        "status": "open",
        "images": _safe_imgs(images),
        "comments": [],
        "created": int(time.time()),
    }
    items.append(issue)
    return issue if _save(items) else {}


def add_comment(issue_id: str, author: str, body: str, images=None, close: bool = False) -> bool:
    """댓글 추가. close=True 면 이슈를 해결됨으로 닫는다."""
    body = _norm(body)
    imgs = _safe_imgs(images)
    if not body and not imgs and not close:
        return False
    items = _load()
    for it in items:
        if it.get("id") == issue_id:
            it.setdefault("comments", []).append({
                "author": _norm(author, "WJ"),
                "body": body,
                "images": imgs,
                "close": bool(close),
                "created": int(time.time()),
            })
            if close:
                it["status"] = "closed"
            return _save(items)
    return False


def set_status(issue_id: str, status: str) -> bool:
    if status not in ("open", "closed"):
        return False
    items = _load()
    for it in items:
        if it.get("id") == issue_id:
            it["status"] = status
            return _save(items)
    return False
