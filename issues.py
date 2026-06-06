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
_IMG_DIR = Path(
    os.environ.get(
        "WJ_ISSUE_IMG_DIR",
        str(Path.home() / ".config" / "wj-dashboard" / "issue_images"),
    )
)
# 매직넘버 -> 확장자 (확장자 위조 방지: 실제 바이트로 이미지 형식 확인)
_IMG_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]
_IMG_MAX_BYTES = 10 * 1024 * 1024


def _sniff_ext(data: bytes) -> str:
    """파일 첫 바이트로 이미지 형식 판별. webp 는 RIFF....WEBP. 아니면 ''."""
    for magic, ext in _IMG_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def save_image(data: bytes) -> dict:
    """이미지 바이트를 저장하고 서빙 URL 을 돌려준다. 실제 바이트로 형식 검증."""
    if not data or len(data) > _IMG_MAX_BYTES:
        return {"ok": False, "error": "파일이 없거나 너무 큽니다(최대 10MB)"}
    ext = _sniff_ext(data)
    if not ext:
        return {"ok": False, "error": "이미지 파일만 가능합니다(png/jpg/gif/webp)"}
    try:
        import secrets
        _IMG_DIR.mkdir(parents=True, exist_ok=True)
        name = "img_%d_%s.%s" % (int(time.time() * 1000), secrets.token_hex(4), ext)
        (_IMG_DIR / name).write_bytes(data)
        try:
            os.chmod(_IMG_DIR / name, 0o600)
        except Exception:
            pass
        return {"ok": True, "url": "/api/issues/image/" + name}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def image_path(name: str):
    """서빙용 안전 경로. basename 만 허용(디렉토리 탈출 방지). 없으면 None."""
    safe = os.path.basename(name or "")
    if not safe or safe != (name or "") or safe.startswith("."):
        return None
    p = _IMG_DIR / safe
    return p if p.is_file() else None


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
