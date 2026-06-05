"""로컬 이메일 상태: 화면에서 숨긴 메일(hidden) + 보관한 영수증(receipts).

원칙:
- 실제 Gmail 은 절대 건드리지 않는다(이 앱은 읽기 전용). 여기 동작은 전부 로컬 표시/보관일 뿐.
- 저장 위치는 repo 밖 ~/.config/wj-dashboard/email_state.json (.gitignore). 메일 내용 포함.
- 절대 raise 하지 않는다(실패 시 빈 결과/False).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATE_PATH = Path(
    os.environ.get(
        "WJ_EMAIL_STATE_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "email_state.json"),
    )
)
RECEIPTS_DIR = Path(
    os.environ.get(
        "WJ_RECEIPTS_DIR",
        str(Path.home() / ".config" / "wj-dashboard" / "receipts"),
    )
)


def _load() -> dict:
    try:
        if STATE_PATH.exists():
            d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("hidden", [])
                d.setdefault("receipts", [])
                d.setdefault("drafts", {})
                d.setdefault("sent", [])
                d.setdefault("candidates", {})
                d.setdefault("snoozed", {})
                return d
    except Exception:
        pass
    return {"hidden": [], "receipts": [], "drafts": {}, "sent": [], "candidates": {}, "snoozed": {}}


def _save(d: dict) -> bool:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
        try:
            os.chmod(STATE_PATH, 0o600)
        except OSError:
            pass
        return True
    except Exception:
        return False


# ---- 숨김(여기서 지우기, Gmail 은 안 지움) ----
def hidden_ids() -> set:
    try:
        return set(_load().get("hidden", []) or [])
    except Exception:
        return set()


def hide(message_id: str) -> bool:
    if not message_id:
        return False
    d = _load()
    if message_id not in d["hidden"]:
        d["hidden"].append(message_id)
        return _save(d)
    return True


def unhide(message_id: str) -> bool:
    d = _load()
    if message_id in d.get("hidden", []):
        d["hidden"].remove(message_id)
        return _save(d)
    return True


# ---- 스누즈(나중에 다시 보기 / 나중에 답변). Gmail 무관, 화면 상태만 ----
def snoozed() -> dict:
    try:
        d = _load().get("snoozed", {}) or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def snoozed_ids() -> set:
    try:
        return set(snoozed().keys())
    except Exception:
        return set()


def snooze(message_id: str, until_ts: int, kind: str = "view") -> bool:
    if not message_id:
        return False
    d = _load()
    d.setdefault("snoozed", {})[message_id] = {
        "until": int(until_ts or 0),
        "kind": "reply" if kind == "reply" else "view",
        "snoozed_at": int(time.time()),
    }
    return _save(d)


def unsnooze(message_id: str) -> bool:
    """스누즈 해제. 스누즈 상태였으면 True, 아니었으면 False(거짓 성공 방지)."""
    d = _load()
    if message_id in d.get("snoozed", {}):
        d["snoozed"].pop(message_id, None)
        return _save(d)
    return False


# ---- 영수증 보관 ----
def add_receipt(rec: dict) -> bool:
    if not rec or not rec.get("id"):
        return False
    d = _load()
    rid = rec.get("id")
    d["receipts"] = [r for r in d.get("receipts", []) if r.get("id") != rid]
    rec.setdefault("saved_at", int(time.time()))
    d["receipts"].insert(0, rec)
    return _save(d)


def receipts() -> list:
    try:
        return _load().get("receipts", []) or []
    except Exception:
        return []


def remove_receipt(rid: str) -> bool:
    d = _load()
    d["receipts"] = [r for r in d.get("receipts", []) if r.get("id") != rid]
    # 저장된 첨부 파일 디렉토리도 함께 삭제
    try:
        import shutil
        sub = RECEIPTS_DIR / _safe_name(rid)
        if sub.exists():
            shutil.rmtree(sub, ignore_errors=True)
    except Exception:
        pass
    return _save(d)


def _safe_name(name: str) -> str:
    """안전 파일/폴더명. 한글 등 유니코드는 보존하고 위험문자만 제거.

    화이트리스트(가-힣)로 막으면 메일 첨부의 NFD(자모 분리형) 한글이 전부 깨진다.
    그래서 NFC 정규화 후 경로/제어/예약 문자만 치환하는 블랙리스트 방식으로 둔다.
    """
    import re
    import unicodedata
    s = unicodedata.normalize("NFC", name or "")
    s = re.sub(r'[\x00-\x1f/\\<>:"|?*]', "_", s)  # 경로구분/제어/예약문자
    s = s.replace("..", "_").strip().lstrip(".")[:120]
    return s or "file"


def save_receipt_file(rid: str, filename: str, data: bytes) -> dict:
    """영수증 첨부(PDF 등) 바이트를 RECEIPTS_DIR/<rid>/ 아래 저장. {name, path} 또는 {}."""
    try:
        if not rid or not data:
            return {}
        sub = RECEIPTS_DIR / _safe_name(rid)
        sub.mkdir(parents=True, exist_ok=True)
        name = _safe_name(filename) or "file.pdf"
        p = sub / name
        p.write_bytes(data)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return {"name": name, "path": str(p)}
    except Exception:
        return {}


def receipt_file_path(rid: str, idx: int) -> str:
    """영수증 rid 의 idx 번째 첨부 파일 경로(없으면 ''). 안전 조회용.

    레코드의 path 를 그대로 믿지 않고, RECEIPTS_DIR 하위인지 realpath 로 재검증한다
    (state 파일이 외부에서 오염돼도 임의 파일이 새어나가지 않게).
    """
    try:
        for r in receipts():
            if r.get("id") == rid:
                files = r.get("files") or []
                if 0 <= idx < len(files):
                    p = files[idx].get("path") or ""
                    if not p:
                        return ""
                    base = os.path.realpath(str(RECEIPTS_DIR))
                    rp = os.path.realpath(p)
                    if rp == base or rp.startswith(base + os.sep):
                        return p
                    return ""
        return ""
    except Exception:
        return ""


# ---- 답장 초안(로컬 저장. 생성=발송 아님. Gmail 에 안 만듦) ----
def save_draft(message_id: str, draft: dict) -> bool:
    if not message_id or not isinstance(draft, dict):
        return False
    d = _load()
    d.setdefault("drafts", {})[message_id] = draft
    return _save(d)


def get_draft(message_id: str) -> dict:
    if not message_id:
        return {}
    try:
        return dict(_load().get("drafts", {}).get(message_id) or {})
    except Exception:
        return {}


def clear_draft(message_id: str) -> bool:
    d = _load()
    if message_id in d.get("drafts", {}):
        d["drafts"].pop(message_id, None)
        return _save(d)
    return True


# ---- 발송 추적(S3b 중복발송 차단용). 발송 코드는 S3b 에서 추가 ----
def is_sent(message_id: str) -> bool:
    try:
        return message_id in (_load().get("sent", []) or [])
    except Exception:
        return False


def mark_sent(message_id: str) -> bool:
    if not message_id:
        return False
    d = _load()
    if message_id not in d.get("sent", []):
        d.setdefault("sent", []).append(message_id)
        return _save(d)
    return True


# ---- 일정 후보(로컬. 감지=캘린더쓰기 아님. 등록은 S4b 승인 게이트) ----
def _cand_sig(c: dict) -> tuple:
    """같은 후보인지 식별하는 서명(제목+근거+시작시각/날짜라벨)."""
    return (
        (c.get("title") or "").strip(),
        (c.get("source") or "").strip()[:60],
        (c.get("start_iso") or c.get("date_label") or "").strip(),
    )


def save_candidates(message_id: str, candidates: list) -> bool:
    """후보 저장. '다시 감지' 시 기존 후보의 상태(ignored/done)와 id 를 보존한다.

    같은 서명의 후보면 사용자가 바꿔둔 상태를 유지(무시한 게 되살아나지 않게).
    새 후보만 pending + 새 id. id 충돌 없게 기존 최대 인덱스 다음부터 부여.
    """
    if not message_id or not isinstance(candidates, list):
        return False
    d = _load()
    existing = d.get("candidates", {}).get(message_id) or []
    by_sig = {}
    for c in existing:
        if isinstance(c, dict):
            by_sig[_cand_sig(c)] = c
    # 새 id 는 기존 evt_<mid>_<n> 의 최대 n 다음부터(보존 id 와 충돌 방지)
    next_idx = 0
    for c in existing:
        cid = (c or {}).get("id") or ""
        if cid.startswith(f"evt_{message_id}_"):
            try:
                next_idx = max(next_idx, int(cid.rsplit("_", 1)[1]) + 1)
            except Exception:
                pass
    norm = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        cc = dict(c)
        prev = by_sig.get(_cand_sig(cc))
        if prev:
            cc["status"] = prev.get("status", "pending")  # 기존 상태 보존
            cc["id"] = prev.get("id") or f"evt_{message_id}_{next_idx}"
            if not prev.get("id"):
                next_idx += 1
        else:
            cc.setdefault("status", "pending")
            cc["id"] = f"evt_{message_id}_{next_idx}"
            next_idx += 1
        norm.append(cc)
    d.setdefault("candidates", {})[message_id] = norm
    return _save(d)


def get_candidates(message_id: str) -> list:
    if not message_id:
        return []
    try:
        return list(_load().get("candidates", {}).get(message_id) or [])
    except Exception:
        return []


def set_candidate_status(message_id: str, cand_id: str, status: str) -> bool:
    """후보 상태 변경(pending/ignored/done). done 의 실제 캘린더 등록은 S4b."""
    d = _load()
    lst = d.get("candidates", {}).get(message_id) or []
    changed = False
    for c in lst:
        if c.get("id") == cand_id:
            c["status"] = status
            changed = True
    if changed:
        d["candidates"][message_id] = lst
        return _save(d)
    return False
