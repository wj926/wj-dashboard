#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path("/home/dami/wj")
APP_ROOT = ROOT / "dashboard-app"
THINKING_ROOT = ROOT / "thinking"
WIKI_ROOT = THINKING_ROOT / "wiki"
RAW_ROOT = THINKING_ROOT / "raw"
DRAFT_JSON = THINKING_ROOT / "_autowiki_drafts" / "2026-06-01.json"
SANDBOX = Path("/tmp/wj_merge_sandbox")

FORBIDDEN_RAW = "2026-06-01.md"
FORBIDDEN_WIKI_PREFIX = "랩문화/모딕논문/"

os.environ.setdefault("WJ_MODE", "dev")
os.environ["WJ_THINKING_ROOT"] = str(THINKING_ROOT)
sys.path.insert(0, str(APP_ROOT))

from autowiki import apply_proposal  # noqa: E402


def print_result(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"  - {detail}")


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def tree_digest(base: Path, kind: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(base.rglob("*")):
        if p.is_dir():
            continue
        rel = str(p.relative_to(base))
        if kind == "raw" and rel == FORBIDDEN_RAW:
            continue
        if kind == "wiki" and rel.startswith(FORBIDDEN_WIKI_PREFIX):
            continue
        out[rel] = file_sha(p)
    return out


def split_fm_body(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            fm = yaml.safe_load(parts[0][4:]) or {}
            return fm, parts[1]
    return {}, text


def parse_section_lines(body: str, heading: str) -> list[str]:
    lines = body.splitlines()
    out: list[str] = []
    in_sec = False
    for ln in lines:
        if ln.startswith("## "):
            if ln.strip() == f"## {heading}":
                in_sec = True
                continue
            if in_sec:
                break
        if in_sec:
            out.append(ln)
    return out


def is_subsequence(old: list[str], new: list[str]) -> bool:
    j = 0
    for ln in new:
        if j < len(old) and ln == old[j]:
            j += 1
    return j == len(old)


def main() -> int:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    (SANDBOX / "wiki").mkdir(parents=True, exist_ok=True)

    real_before = {
        "wiki": tree_digest(WIKI_ROOT, "wiki"),
        "raw": tree_digest(RAW_ROOT, "raw"),
    }

    payload = json.loads(DRAFT_JSON.read_text(encoding="utf-8"))
    proposals = payload.get("proposals") or []
    existing = next(
        (p for p in proposals if p.get("target", {}).get("kind") == "existing" and "업무-대시보드/업무-대시보드.md" in p.get("target", {}).get("rel_path", "")),
        None,
    )
    unsure = next((p for p in proposals if p.get("target", {}).get("kind") == "unsure"), None)
    if not existing or not unsure:
        print("필수 proposal(existing/unsure) 탐색 실패")
        return 1

    rel_existing = existing["target"]["rel_path"].replace("wiki/", "", 1)
    src_page = WIKI_ROOT / rel_existing
    dst_page = SANDBOX / "wiki" / rel_existing
    dst_page.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_page, dst_page)

    src_cat_index = WIKI_ROOT / "시스템" / "_index.md"
    dst_cat_index = SANDBOX / "wiki" / "시스템" / "_index.md"
    dst_cat_index.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_cat_index, dst_cat_index)

    ledger = SANDBOX / "merge_ledger.json"

    before_text = dst_page.read_text(encoding="utf-8")
    before_fm, before_body = split_fm_body(before_text)
    before_tl = parse_section_lines(before_body, "타임라인")
    before_oq = parse_section_lines(before_body, "열린 질문")

    r1 = apply_proposal(existing, wiki_root=SANDBOX / "wiki", ledger_path=ledger)
    after_text = dst_page.read_text(encoding="utf-8")
    after_fm, after_body = split_fm_body(after_text)
    after_tl = parse_section_lines(after_body, "타임라인")
    after_oq = parse_section_lines(after_body, "열린 질문")

    l3_ok = r1.get("ok") and r1.get("changed") and is_subsequence(before_body.splitlines(), after_body.splitlines())
    print_result("L3 비손상(본문 append-only)", l3_ok, r1.get("reason", ""))

    l3_log_ok = is_subsequence(before_tl, after_tl) and is_subsequence(before_oq, after_oq)
    print_result("로그층 보호(타임라인/열린 질문 기존줄 보존)", l3_log_ok, f"timeline {len(before_tl)}->{len(after_tl)}, open_q {len(before_oq)}->{len(after_oq)}")

    fm_same = dict(before_fm)
    fm_same.pop("updated", None)
    afm_same = dict(after_fm)
    afm_same.pop("updated", None)
    lfm_ok = (fm_same == afm_same) and (before_fm.get("updated") != after_fm.get("updated"))
    print_result("frontmatter(updated만 변경)", lfm_ok, f"updated {before_fm.get('updated')} -> {after_fm.get('updated')}")

    sha_after_first = file_sha(dst_page)
    r2 = apply_proposal(existing, wiki_root=SANDBOX / "wiki", ledger_path=ledger)
    sha_after_second = file_sha(dst_page)
    l4_ok = r2.get("ok") and (not r2.get("changed")) and sha_after_first == sha_after_second
    print_result("L4 멱등성(2회차 no-op)", l4_ok, r2.get("reason", ""))

    rr = apply_proposal(unsure, wiki_root=SANDBOX / "wiki", ledger_path=ledger)
    reject_ok = (not rr.get("ok")) and ("승인 필요" in rr.get("reason", "") or "unsure" in rr.get("reason", ""))
    print_result("거부 처리(unsure/confirm)", reject_ok, rr.get("reason", ""))

    new_prop = {
        "ingest_id": "sandbox-new-001",
        "raw_source": "raw/2026-05-17.md",
        "raw_excerpt": "sandbox new page",
        "target": {"kind": "new", "category": "시스템", "slug": "머지-하네스-테스트"},
        "proposed": {
            "timeline_add": "- 2026-06-01: 샌드박스 new 생성 테스트 (출처 raw/2026-05-17.md)",
            "decisions_add": ["- 2026-06-01 | 결정: 샌드박스 new 생성 | 근거: 하네스 | 상태: 유효"],
            "todos_add": ["- [ ] 머지 하네스 검증"],
            "open_q_add": ["- 생성 직후 구조가 유효한가?"],
            "current_note": "무시됨",
            "new_page_frontmatter": {
                "title": "머지 하네스 테스트",
                "slug": "머지-하네스-테스트",
                "category_primary": "시스템",
                "type": "thought",
                "status": "active",
                "created": "2026-06-01",
                "updated": "2026-06-01",
            },
        },
        "needs_user_confirm": False,
        "warnings": [],
    }
    rnew = apply_proposal(new_prop, wiki_root=SANDBOX / "wiki", ledger_path=ledger)
    new_page_ok = (SANDBOX / "wiki" / "시스템" / "머지-하네스-테스트.md").exists() and rnew.get("ok")
    print_result("new 페이지 생성(append-only 신규)", new_page_ok, rnew.get("reason", ""))

    real_after = {
        "wiki": tree_digest(WIKI_ROOT, "wiki"),
        "raw": tree_digest(RAW_ROOT, "raw"),
    }
    l5_ok = real_before == real_after
    print_result("L5 실위키/실raw 무변경", l5_ok, "체크섬 동일(금지경로 제외)" if l5_ok else "변경 감지")

    diff_sample = []
    before_lines = before_body.splitlines()
    after_lines = after_body.splitlines()
    for i, ln in enumerate(after_lines):
        if i >= len(before_lines) or ln != before_lines[i]:
            if ln.strip().startswith("- "):
                diff_sample.append(f"+ {ln}")
        if len(diff_sample) >= 6:
            break
    print("\n샌드박스 append 샘플")
    for ln in diff_sample[:6]:
        print(ln)

    all_ok = all([l3_ok, l3_log_ok, lfm_ok, l4_ok, reject_ok, new_page_ok, l5_ok])
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
