"""자동 답장 초안 생성 (email_refresh 배치 끝에서 호출).

답장 필요 & 미답장 & 초안 없음인 메일 중 우선순위 상위 N개에만 미발송 초안을
미리 만들어 둔다.

불변식:
- 절대 발송하지 않는다. 미발송(status=unsent)으로 로컬 저장만 한다.
- 무과금 codex 경로(RealEmailLLM.generate_reply_draft)만 사용한다.
- 상한 N = WJ_AUTODRAFT_MAX (기본 5, 0 이면 자동 생성 끔).
- 어떤 예외도 refresh 본체를 죽이지 않는다(호출부에서 try/except).
"""
from __future__ import annotations

import json as _json
import os
from email.utils import parseaddr
from pathlib import Path

# 자동초안 제외 발신자 목록(이메일 주소, 소문자). repo 밖 ~/.config/wj-dashboard/.
_MUTE_PATH = Path(
    os.environ.get(
        "WJ_AUTODRAFT_MUTE_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "email_autodraft_mute.json"),
    )
)


def max_n() -> int:
    try:
        return int(os.environ.get("WJ_AUTODRAFT_MAX", "5") or "5")
    except Exception:
        return 5


def list_muted() -> list:
    """자동초안을 만들지 않을 발신자 이메일 목록(소문자). 실패 시 빈 리스트."""
    try:
        d = _json.loads(_MUTE_PATH.read_text(encoding="utf-8"))
        return [str(x).strip().lower() for x in (d.get("muted") or []) if str(x).strip()]
    except Exception:
        return []


def add_muted(email: str) -> bool:
    """발신자 이메일을 제외 목록에 추가(이미 있으면 그대로 성공)."""
    e = (email or "").strip().lower()
    if not e:
        return False
    try:
        cur = list_muted()
        if e not in cur:
            cur.append(e)
        _MUTE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MUTE_PATH.with_suffix(".tmp")
        tmp.write_text(_json.dumps({"muted": cur}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, _MUTE_PATH)
        try:
            os.chmod(_MUTE_PATH, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def select_targets(msgs: list, existing_drafts: dict, limit: int):
    """미리 초안을 만들 메일을 고른다(선별만 분리 — 테스트 용이).

    조건: id 있음 + 내가 아직 답장 안 함 + 기존 초안 없음 + score 우선순위 p0/p1
          + reasons 에 '답장 필요'.
    반환: (대상 메일 리스트[<=limit], 상한 초과로 제외된 수)
    """
    import email_score

    muted = set(list_muted())
    scored = []
    for m in msgs or []:
        mid = m.get("id")
        if not mid or m.get("i_replied"):
            continue
        if (existing_drafts or {}).get(mid):
            continue
        _, addr = parseaddr((m.get("headers") or {}).get("from") or "")
        if addr and addr.strip().lower() in muted:
            continue  # 사용자가 '이 발신자 자동초안 끄기' 한 사람
        sf = email_score.score(m)
        if sf.get("priority") not in ("p0", "p1"):
            continue
        if "답장 필요" not in (sf.get("reasons") or []):
            continue
        scored.append((sf["priority"], m))

    scored.sort(key=lambda t: email_score.order_key(t[0]))
    lim = max(0, limit)
    targets = [m for _, m in scored[:lim]]
    return targets, max(0, len(scored) - lim)


def run(msgs: list) -> dict:
    """대상 선별 후 미발송 초안을 생성/저장한다. 로그를 남기고 요약 dict 반환."""
    n = max_n()
    if n <= 0:
        print("[autodraft] 비활성 (WJ_AUTODRAFT_MAX=0)")
        return {"enabled": False, "made": 0, "targets": 0, "skipped": 0}

    import email_store
    import llm_email

    existing = (email_store._load() or {}).get("drafts", {}) or {}
    targets, skipped = select_targets(msgs, existing, n)

    llm = llm_email.RealEmailLLM()
    made = 0
    for m in targets:
        try:
            res = llm.generate_reply_draft(m, {}, "정중·간결")
        except Exception as e:
            print(f"[autodraft] 생성 예외(건너뜀) {m.get('id')}: {type(e).__name__}")
            continue
        if res.get("ok") and res.get("draft"):
            d = dict(res["draft"])
            d["status"] = "unsent"
            d["auto"] = True  # 자동 생성 표시(수동과 구분)
            if email_store.save_draft(m["id"], d):
                made += 1

    print(f"[autodraft] 생성 {made} / 대상 {len(targets)} / 상한초과 미생성 {skipped} (상한 {n})")
    return {"enabled": True, "made": made, "targets": len(targets), "skipped": skipped}
