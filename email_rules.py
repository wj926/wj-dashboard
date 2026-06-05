"""처리 규칙(말로 가르치면 똑똑해짐). 로컬 저장 + 우선순위 산정에 적용.

WJ 가 자연어로 규칙을 쓰면("두나무 메일은 항상 위로") LLM 이 매칭조건+효과로 해석해
저장하고, 인박스 점수 계산 때 적용한다.

원칙:
- 절대 raise 하지 않는다. 실패 시 안전 기본값.
- 저장은 repo 밖 ~/.config/wj-dashboard/email_rules.json (.gitignore).
- 규칙 '추가' 때만 LLM 호출(과금/지연 통제). 인박스 로딩·적용은 LLM 안 씀(순수 매칭).
- 효과는 우선순위 가감/영수증 후보 표시 정도. 실제 발송·삭제·캘린더 등 부작용 없음.
"""
from __future__ import annotations

import json
import os
import time as _time
from pathlib import Path

STORE_PATH = Path(
    os.environ.get(
        "WJ_EMAIL_RULES_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "email_rules.json"),
    )
)

EFFECTS = ("priority_up", "priority_down", "receipt", "later")

# 중계 플랫폼/시스템 발신자 토큰: match.from 에 들어오면 무관한 메일까지 잡으므로 거른다.
_FROM_STOP = (
    "eclass", "이클래스", "gmail", "지메일", "naver", "네이버", "daum", "다음",
    "kakao", "카카오", "mailer", "mailer-daemon", "noreply", "no-reply", "donotreply",
    "notification", "notifications", "알림", "no_reply", "mail.", "google",
)


def _clean_from(tokens: list) -> list:
    """발신자 매칭 토큰에서 중계 플랫폼/시스템 이름과 너무 짧은 토큰 제거."""
    out = []
    for t in tokens or []:
        s = (t or "").strip()
        low = s.lower()
        if len(s) < 2:
            continue
        if any(stop in low for stop in _FROM_STOP):
            continue
        out.append(s)
    return out


def _load() -> list:
    try:
        if STORE_PATH.exists():
            d = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
    except Exception:
        pass
    return []


def _save(items: list) -> bool:
    try:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STORE_PATH)
        try:
            os.chmod(STORE_PATH, 0o600)
        except OSError:
            pass
        return True
    except Exception:
        return False


def list_rules() -> list:
    return _load()


def add_rule(text: str, parsed: dict | None = None) -> dict:
    """규칙 추가. parsed(매칭/효과)가 없으면 호출부가 LLM 해석을 넣어준다. 실패 시 {}."""
    text = (text or "").strip()
    if not text:
        return {}
    p = parsed if isinstance(parsed, dict) else {}
    rule = {
        "id": f"rule_{int(_time.time()*1000)}",
        "text": text[:300],
        "label": (p.get("label") or text[:24]).strip()[:30],
        "match": {
            "from": _clean_from(p.get("match", {}).get("from") or [])[:8],
            "subject_kw": [s for s in (p.get("match", {}).get("subject_kw") or []) if s][:8],
        },
        "effect": p.get("effect") if p.get("effect") in EFFECTS else "priority_up",
        "enabled": True,
        "needs_review": not bool(p),  # LLM 해석 못 했으면 검토 필요 표시
        "created_at": int(_time.time()),
    }
    items = _load()
    items.insert(0, rule)
    return rule if _save(items) else {}


def set_enabled(rule_id: str, enabled: bool) -> bool:
    items = _load()
    hit = False
    for r in items:
        if r.get("id") == rule_id:
            r["enabled"] = bool(enabled)
            hit = True
    return _save(items) if hit else False


def delete_rule(rule_id: str) -> bool:
    items = _load()
    new = [r for r in items if r.get("id") != rule_id]
    if len(new) == len(items):
        return False
    return _save(new)


_PRI = {"p0": 0, "p1": 1, "p2": 2}
_PRI_INV = {0: "p0", 1: "p1", 2: "p2"}


def _matches(rule: dict, frm: str, subject: str) -> bool:
    m = rule.get("match") or {}
    frm_l = (frm or "").lower()
    sub_l = (subject or "").lower()
    for s in m.get("from") or []:
        if s and s.lower() in frm_l:
            return True
    for s in m.get("subject_kw") or []:
        if s and s.lower() in sub_l:
            return True
    return False


def apply_to(message: dict, score: dict, rules: list | None = None) -> dict:
    """활성 규칙을 점수에 적용. 우선순위 가감 + '규칙: <label>' 이유칩. 순수 매칭, LLM 안 씀.

    rules 를 넘기면 파일을 다시 읽지 않는다(큐 전체 적용 시 1회 로드 재사용).
    반환: {priority, reasons, has_event, category, rule_labels, receipt_suggested, later_suggested}
    """
    try:
        out = dict(score)
        rule_labels = []
        receipt = False
        later = False
        headers = (message or {}).get("headers") or {}
        frm = headers.get("from") or ""
        subject = headers.get("subject") or ""
        pri = _PRI.get(out.get("priority", "p2"), 2)
        for rule in (rules if rules is not None else _load()):
            if not rule.get("enabled"):
                continue
            if not _matches(rule, frm, subject):
                continue
            eff = rule.get("effect")
            label = rule.get("label") or "규칙"
            if eff == "priority_up":
                pri = max(0, pri - 1)
            elif eff == "priority_down":
                pri = min(2, pri + 1)
            elif eff == "receipt":
                receipt = True
            elif eff == "later":
                later = True
            rule_labels.append(label)
        out["priority"] = _PRI_INV.get(pri, "p2")
        reasons = list(out.get("reasons") or [])
        for lb in rule_labels:
            chip = f"규칙: {lb}"
            if chip not in reasons:
                reasons.append(chip)
        out["reasons"] = reasons[:4]
        out["rule_labels"] = rule_labels
        out["receipt_suggested"] = receipt
        out["later_suggested"] = later
        return out
    except Exception:
        return score
