#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path("/home/dami/wj")
APP_ROOT = ROOT / "dashboard-app"
THINKING_ROOT = ROOT / "thinking"
WIKI_ROOT = THINKING_ROOT / "wiki"
RAW_ROOT = THINKING_ROOT / "raw"

# thinking_compute/settings 가 examples 경로를 보지 않도록 import 전 고정
os.environ.setdefault("WJ_MODE", "dev")
os.environ["WJ_THINKING_ROOT"] = str(THINKING_ROOT)

sys.path.insert(0, str(APP_ROOT))
from autowiki import Proposal, generate_drafts, write_drafts  # noqa: E402


def tree_digest(base: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(base.rglob("*")):
        if p.is_dir():
            continue
        rel = str(p.relative_to(base))
        h = hashlib.sha256()
        with p.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        out[rel] = h.hexdigest()
    return out


def check_allowed_schema(prop: dict) -> tuple[bool, str]:
    allowed_top = {
        "ingest_id",
        "raw_source",
        "raw_excerpt",
        "target",
        "proposed",
        "needs_user_confirm",
        "warnings",
    }
    if set(prop.keys()) != allowed_top:
        return False, f"top-level 필드 불일치: {set(prop.keys())}"

    target = prop["target"]
    if target.get("kind") not in {"existing", "new", "unsure"}:
        return False, f"target.kind invalid: {target.get('kind')}"

    proposed = prop["proposed"]
    allowed_proposed = {
        "timeline_add",
        "decisions_add",
        "todos_add",
        "open_q_add",
        "current_note",
        "new_page_frontmatter",
    }
    if not set(proposed.keys()).issubset(allowed_proposed):
        return False, f"proposed 필드 불일치: {set(proposed.keys())}"

    if target.get("kind") == "new":
        fm = proposed.get("new_page_frontmatter")
        if not isinstance(fm, dict):
            return False, "new target인데 new_page_frontmatter 없음"
        required = {"title", "slug", "category_primary", "type", "status", "created", "updated"}
        if not required.issubset(set(fm.keys())):
            return False, f"frontmatter 필수키 누락: {required - set(fm.keys())}"
    return True, ""


def has_damage_keys(prop: dict) -> bool:
    forbidden_keys = {"delete", "remove", "replace", "patch", "diff", "edits", "modifications"}
    stack = [prop]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if str(k).lower() in forbidden_keys:
                    return True
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return False


def print_result(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"  - {detail}")


def main() -> int:
    before = {
        "wiki": tree_digest(WIKI_ROOT),
        "raw": tree_digest(RAW_ROOT),
    }

    proposals_obj = generate_drafts("2026-05-17")
    draft_path = write_drafts(proposals_obj)
    proposals = [p.__dict__ if isinstance(p, Proposal) else p for p in proposals_obj]

    after = {
        "wiki": tree_digest(WIKI_ROOT),
        "raw": tree_digest(RAW_ROOT),
    }

    all_ok = True

    l5_ok = before == after
    all_ok &= l5_ok
    print_result("L5 Gate (wiki/raw 무변경)", l5_ok, "파일/체크섬 동일" if l5_ok else "변경 감지")

    l2_ok = True
    l2_msg = ""
    for p in proposals:
        ok, msg = check_allowed_schema(p)
        if not ok:
            l2_ok = False
            l2_msg = msg
            break
    all_ok &= l2_ok
    print_result("L2 구조 적법성", l2_ok, l2_msg or f"{len(proposals)}개 proposal 검사")

    l3_ok = True
    for p in proposals:
        if has_damage_keys(p):
            l3_ok = False
            break
    all_ok &= l3_ok
    print_result("L3 비손상(삭제/교체 지시 불가)", l3_ok, "삭제·교체 키워드 없음" if l3_ok else "금지 키워드 감지")

    p1 = [x.__dict__ if isinstance(x, Proposal) else x for x in generate_drafts("2026-05-17")]
    p2 = [x.__dict__ if isinstance(x, Proposal) else x for x in generate_drafts("2026-05-17")]
    s1 = json.dumps(p1, ensure_ascii=False, sort_keys=True)
    s2 = json.dumps(p2, ensure_ascii=False, sort_keys=True)
    ids1 = {x["ingest_id"] for x in p1}
    ids2 = {x["ingest_id"] for x in p2}
    l4_ok = s1 == s2 and ids1 == ids2 and len(ids1) == len(p1)
    all_ok &= l4_ok
    print_result("L4 멱등성", l4_ok, f"개수={len(p1)}, ingest_id 유니크={len(ids1)==len(p1)}")

    smoke_count_ok = len(proposals) >= 1
    smoke_existing_ok = any(
        p["target"].get("kind") == "existing" and "html관리시스템" in (p["target"].get("rel_path") or "")
        for p in proposals
    )
    smoke_ok = smoke_count_ok and smoke_existing_ok
    all_ok &= smoke_ok
    print_result(
        "Smoke(실 raw 라우팅)",
        smoke_ok,
        f"proposal={len(proposals)}, html관리시스템 existing={smoke_existing_ok}",
    )

    print("\n요약")
    print(f"- draft_path: {draft_path}")
    print(f"- proposal_count: {len(proposals)}")

    if not all_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
