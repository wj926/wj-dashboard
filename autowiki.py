from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import fcntl
import yaml

from thinking_compute import THINKING_ROOT, RAW_ROOT, WIKI_ROOT, build_index

DRAFT_ROOT = THINKING_ROOT / "_autowiki_drafts"
SEED_CATEGORIES = {"회사", "연구", "제안서", "랩문화", "개인", "시스템"}
MERGE_LEDGER_PATH = THINKING_ROOT / "_autowiki_drafts" / "merge_ledger.json"
AUTOWIKI_EXCLUDE_RAW = {"2026-06-01"}
AUTOWIKI_EXCLUDE_SLUGS = {"모닝논문", "모딕논문"}


@dataclass
class Proposal:
    ingest_id: str
    raw_source: str
    raw_excerpt: str
    target: dict[str, Any]
    proposed: dict[str, Any]
    needs_user_confirm: bool
    warnings: list[str]


def _normalize_key(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _slugify(text: str) -> str:
    s = text.strip().replace(" ", "-")
    s = re.sub(r"[^0-9A-Za-z가-힣_-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


def _is_excluded_raw_date(raw_date: str) -> bool:
    return raw_date in AUTOWIKI_EXCLUDE_RAW


def _is_excluded_slug(slug: str | None) -> bool:
    return bool(slug and slug.strip() in AUTOWIKI_EXCLUDE_SLUGS)


def is_excluded_target(target: dict[str, Any]) -> bool:
    kind = (target or {}).get("kind")
    if kind == "existing":
        rel_path = str((target or {}).get("rel_path") or "")
        rel_slug = Path(rel_path).stem if rel_path else ""
        return _is_excluded_slug(rel_slug)
    if kind == "new":
        return _is_excluded_slug((target or {}).get("slug"))
    return False


def _parse_raw_sections(raw_path: Path) -> list[dict[str, Any]]:
    if not raw_path.exists():
        return []
    text = raw_path.read_text(encoding="utf-8")
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections.append(current)
            current = {
                "heading": line[3:].strip(),
                "lines": [],
                "mentions": [],
            }
            continue
        if line.startswith("# "):
            continue
        if current is None:
            current = {"heading": "(머리말)", "lines": [], "mentions": []}
        current["lines"].append(line)
        for m in re.finditer(r"->\s*\[\[([^\]]+)\]\]", line):
            current["mentions"].append(m.group(1).strip())
    if current is not None:
        sections.append(current)

    out = []
    raw_source = f"raw/{raw_path.name}"
    for sec in sections:
        body = "\n".join(sec["lines"]).strip()
        if not body:
            continue
        bullet_lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("-")]
        if not bullet_lines:
            bullet_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        for i, raw_line in enumerate(bullet_lines):
            mentions = [m.group(1).strip() for m in re.finditer(r"->\s*\[\[([^\]]+)\]\]", raw_line)]
            clean_line = re.sub(r"\s*->\s*\[\[[^\]]+\]\]", "", raw_line).strip()
            clean_line = re.sub(r"^-\s*", "", clean_line)
            if not clean_line:
                continue
            out.append(
                {
                    "raw_source": raw_source,
                    "heading": sec["heading"],
                    "line_index": i,
                    "line": clean_line,
                    "mentions": mentions,
                }
            )
    return out


def _build_page_lookup() -> dict[str, Any]:
    idx = build_index(date.today())
    pages = idx["pages"]
    by_norm: dict[str, list[dict[str, Any]]] = {}
    seen_per_key: dict[str, set[str]] = {}
    for p in pages:
        candidates = [p.get("title") or "", p.get("slug") or ""] + (p.get("aliases") or [])
        for c in candidates:
            if not c:
                continue
            key = _normalize_key(c)
            if key not in seen_per_key:
                seen_per_key[key] = set()
            if p["path"] in seen_per_key[key]:
                continue
            seen_per_key[key].add(p["path"])
            by_norm.setdefault(key, []).append(p)
    return {"index": idx, "by_norm": by_norm, "pages": pages}


def _infer_category_from_path(rel_path: str) -> str | None:
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "wiki":
        cat = parts[1]
        return cat if cat in SEED_CATEGORIES else None
    return None


def _default_phraser(raw_line: str, raw_date: str, mention: str | None) -> dict[str, Any]:
    short = raw_line.strip()
    if len(short) > 180:
        short = short[:177] + "..."
    return {
        "timeline_add": f"- {raw_date}: {short} (출처 raw/{raw_date}.md)",
        "decisions_add": [f"- {raw_date} | 결정: {short} | 근거: raw 메모 반영 | 상태: 유효"],
        "todos_add": [],
        "open_q_add": [f"- {short}"] if "?" in short or "열린 질문" in short else [],
        "current_note": f"{raw_date} 메모 반영 초안 ({mention or '미지정 주제'})",
    }


def _make_ingest_id(raw_source: str, heading: str, line: str) -> str:
    basis = f"{raw_source}|{heading}|{line}".encode("utf-8")
    return f"{raw_source}#{hashlib.sha256(basis).hexdigest()[:16]}"


def _resolve_target(mention: str | None, lookup: dict[str, Any], raw_date: str) -> tuple[dict[str, Any], bool, list[str]]:
    warnings: list[str] = []
    if not mention:
        return ({"kind": "unsure"}, True, ["라우팅 태그(-> [[주제]]) 없음"])

    hits = lookup["by_norm"].get(_normalize_key(mention), [])
    if len(hits) == 1:
        p = hits[0]
        rel_path = p["path"]
        cat = _infer_category_from_path(rel_path)
        if not cat:
            warnings.append("기존 페이지 카테고리 추정 실패")
        return ({"kind": "existing", "rel_path": rel_path}, False, warnings)

    if len(hits) >= 2:
        rels = ", ".join(sorted({h["path"] for h in hits})[:3])
        return ({"kind": "unsure"}, True, [f"동일 키 매칭 다수: {rels}"])

    slug = _slugify(mention)
    guessed_category = None
    # 새 페이지 카테고리는 자동 단정 금지. 기존 slug prefix/동명 없으면 보류.
    needs_confirm = True
    warnings.append("기존 wiki 페이지 매칭 실패: 새 페이지 후보")
    return (
        {
            "kind": "new",
            "category": guessed_category,
            "slug": slug,
        },
        needs_confirm,
        warnings,
    )


def generate_drafts(raw_rel_or_date: str, phraser: Callable[[str, str, str | None], dict[str, Any]] | None = None) -> list[Proposal]:
    """raw 파일 항목을 읽어 draft proposal 생성 (wiki/raw 쓰기 금지)."""
    raw_key = raw_rel_or_date.strip()
    if raw_key.endswith(".md"):
        raw_name = Path(raw_key).name
    elif raw_key.startswith("raw/"):
        raw_name = Path(raw_key).name
    else:
        raw_name = f"{raw_key}.md"
    raw_path = RAW_ROOT / raw_name
    if not raw_path.exists():
        raise FileNotFoundError(f"raw 파일 없음: {raw_path}")

    raw_date = raw_path.stem
    if _is_excluded_raw_date(raw_date):
        raise ValueError(f"보호된 raw 날짜는 draft 생성 불가: {raw_date}")
    phr = phraser or _default_phraser
    lookup = _build_page_lookup()

    proposals: list[Proposal] = []
    seen_ids: set[str] = set()
    for item in _parse_raw_sections(raw_path):
        mention = item["mentions"][0] if item["mentions"] else None
        target, needs_confirm, warnings = _resolve_target(mention, lookup, raw_date)
        if is_excluded_target(target):
            continue

        phrased = phr(item["line"], raw_date, mention)
        proposed = {
            "timeline_add": phrased.get("timeline_add"),
            "decisions_add": list(phrased.get("decisions_add") or []),
            "todos_add": list(phrased.get("todos_add") or []),
            "open_q_add": list(phrased.get("open_q_add") or []),
            "current_note": phrased.get("current_note"),
        }

        if target["kind"] == "new":
            proposed["new_page_frontmatter"] = {
                "title": mention or item["line"][:30],
                "slug": target.get("slug") or _slugify(mention or item["line"][:30]),
                "category_primary": target.get("category"),
                "type": "thought",
                "status": "active",
                "created": raw_date,
                "updated": raw_date,
            }
            if not proposed["new_page_frontmatter"]["category_primary"]:
                needs_confirm = True
                warnings.append("new 페이지 category_primary 미확정")

        ingest_id = _make_ingest_id(item["raw_source"], item["heading"], item["line"])
        if ingest_id in seen_ids:
            continue
        seen_ids.add(ingest_id)

        proposals.append(
            Proposal(
                ingest_id=ingest_id,
                raw_source=item["raw_source"],
                raw_excerpt=f"[{item['heading']}] {item['line']}",
                target=target,
                proposed=proposed,
                needs_user_confirm=needs_confirm,
                warnings=warnings,
            )
        )

    proposals.sort(key=lambda p: p.ingest_id)
    return proposals


def write_drafts(proposals: list[Proposal]) -> str:
    """draft 파일을 thinking 바깥 wiki/raw 트리와 분리된 위치에 저장."""
    DRAFT_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = DRAFT_ROOT / f"{date.today().isoformat()}.json"
    payload = {
        "generated_at": date.today().isoformat(),
        "count": len(proposals),
        "proposals": [asdict(p) for p in proposals],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path)


FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n(.*)$", re.DOTALL)


def _today() -> str:
    return date.today().isoformat()


def _safe_rel_under(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    rr = root.resolve()
    try:
        p.relative_to(rr)
    except ValueError as e:
        raise ValueError(f"path traversal 금지: {rel}") from e
    return p


def _read_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    m = FRONTMATTER_RE.match(md_text)
    if not m:
        return {}, md_text
    fm = yaml.safe_load(m.group(1)) or {}
    return fm, m.group(2)


def _render_frontmatter(fm: dict[str, Any], body: str) -> str:
    y = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{y}\n---\n{body}"


def _append_lines_to_section(body: str, heading: str, lines_to_add: list[str]) -> tuple[str, bool]:
    if not lines_to_add:
        return body, False
    raw_lines = body.splitlines()
    sec_re = re.compile(rf"^##\s+{re.escape(heading)}\s*$")
    start = -1
    for i, ln in enumerate(raw_lines):
        if sec_re.match(ln):
            start = i
            break
    changed = False
    add = [x for x in lines_to_add if isinstance(x, str) and x.strip()]
    if not add:
        return body, False
    if start == -1:
        if raw_lines and raw_lines[-1].strip():
            raw_lines.append("")
        raw_lines.append(f"## {heading}")
        raw_lines.extend(add)
        changed = True
        return "\n".join(raw_lines) + "\n", changed

    end = len(raw_lines)
    for j in range(start + 1, len(raw_lines)):
        if raw_lines[j].startswith("## "):
            end = j
            break
    insert_at = end
    while insert_at > start + 1 and raw_lines[insert_at - 1].strip() == "":
        insert_at -= 1
    new_lines = raw_lines[:insert_at] + add + raw_lines[insert_at:]
    changed = True
    return "\n".join(new_lines) + ("\n" if body.endswith("\n") else ""), changed


def _load_ledger(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return {"applied_ids": []}
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"applied_ids": []}
        if not isinstance(payload.get("applied_ids"), list):
            payload["applied_ids"] = []
        return payload
    except Exception:
        return {"applied_ids": []}


def _save_ledger(ledger_path: Path, payload: dict[str, Any]) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger_path.with_suffix(ledger_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ledger_path)


def _apply_existing_proposal(wiki_root: Path, proposal: dict[str, Any], today: str) -> tuple[bool, str]:
    rel_path = proposal.get("target", {}).get("rel_path")
    if not rel_path:
        return False, "target.rel_path 없음"
    page = _safe_rel_under(wiki_root, rel_path.replace("wiki/", "", 1) if rel_path.startswith("wiki/") else rel_path)
    if not page.exists():
        return False, f"대상 페이지 없음: {page}"

    with open(page, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        old_text = f.read()

        fm, body = _read_frontmatter(old_text)
        old_updated = str(fm.get("updated") or "")
        fm["updated"] = today

        changed = False
        body2 = body

        tl = proposal.get("proposed", {}).get("timeline_add")
        body2, c1 = _append_lines_to_section(body2, "타임라인", [tl] if tl else [])
        changed |= c1
        body2, c2 = _append_lines_to_section(body2, "열린 질문", proposal.get("proposed", {}).get("open_q_add") or [])
        changed |= c2
        body2, c3 = _append_lines_to_section(body2, "결정·방향", proposal.get("proposed", {}).get("decisions_add") or [])
        changed |= c3
        body2, c4 = _append_lines_to_section(body2, "할 일", proposal.get("proposed", {}).get("todos_add") or [])
        changed |= c4

        new_text = _render_frontmatter(fm, body2)
        if old_text != new_text:
            changed = True
        elif old_updated != today:
            changed = True
        if not changed:
            return False, "변경 없음"

        bak = page.with_suffix(page.suffix + ".bak")
        bak.write_text(old_text, encoding="utf-8")
        tmp = page.with_suffix(page.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, page)
    return True, "applied(existing)"


def _append_category_index(wiki_root: Path, category: str, slug: str, title: str) -> None:
    idx = _safe_rel_under(wiki_root, f"{category}/_index.md")
    if not idx.exists():
        return
    with open(idx, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        text = f.read()
        entry = f"- [[{slug}]] - {title}"
        if entry in text or f"[[{slug}]]" in text:
            return
        new_text = text + ("" if text.endswith("\n") else "\n") + entry + "\n"
        bak = idx.with_suffix(idx.suffix + ".bak")
        bak.write_text(text, encoding="utf-8")
        tmp = idx.with_suffix(idx.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, idx)


def _apply_new_proposal(wiki_root: Path, proposal: dict[str, Any], today: str) -> tuple[bool, str]:
    target = proposal.get("target", {})
    category = target.get("category")
    slug = target.get("slug")
    fm_in = proposal.get("proposed", {}).get("new_page_frontmatter") or {}
    if not category or not slug:
        return False, "new target category/slug 미확정"
    cat_dir = _safe_rel_under(wiki_root, category)
    cat_dir.mkdir(parents=True, exist_ok=True)
    page = _safe_rel_under(wiki_root, f"{category}/{slug}.md")
    if page.exists():
        return False, "이미 존재하는 페이지"

    fm = {
        "title": fm_in.get("title") or slug,
        "slug": fm_in.get("slug") or slug,
        "category_primary": fm_in.get("category_primary") or category,
        "category_secondary": fm_in.get("category_secondary") or [],
        "type": fm_in.get("type") or "thought",
        "status": fm_in.get("status") or "active",
        "tags": fm_in.get("tags") or [],
        "created": fm_in.get("created") or today,
        "updated": today,
        "links": fm_in.get("links") or [],
        "aliases": fm_in.get("aliases") or [],
    }
    body_lines = [
        "## 지금 생각",
        "현재 결론:",
        "왜 중요:",
        f"최근 변경: {today} (한 줄)",
        "",
        "## 결정·방향",
        "",
        "## 할 일",
        "",
        "## 열린 질문",
        "",
        "## 타임라인",
    ]
    body = "\n".join(body_lines) + "\n"
    tl = proposal.get("proposed", {}).get("timeline_add")
    body, _ = _append_lines_to_section(body, "타임라인", [tl] if isinstance(tl, str) else [])
    body, _ = _append_lines_to_section(body, "열린 질문", proposal.get("proposed", {}).get("open_q_add") or [])
    body, _ = _append_lines_to_section(body, "결정·방향", proposal.get("proposed", {}).get("decisions_add") or [])
    body, _ = _append_lines_to_section(body, "할 일", proposal.get("proposed", {}).get("todos_add") or [])
    text = _render_frontmatter(fm, body)

    with open(page, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        if page.exists() and page.stat().st_size > 0:
            return False, "경합: 파일 이미 생성됨"
        tmp = page.with_suffix(page.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, page)
    _append_category_index(wiki_root, category, slug, fm["title"])
    return True, "applied(new)"


def apply_proposal(proposal: dict[str, Any] | Proposal, wiki_root: Path = WIKI_ROOT, ledger_path: Path = MERGE_LEDGER_PATH) -> dict[str, Any]:
    p = asdict(proposal) if isinstance(proposal, Proposal) else proposal
    ingest_id = p.get("ingest_id")
    if not ingest_id:
        return {"ok": False, "changed": False, "reason": "ingest_id 없음"}

    if p.get("needs_user_confirm"):
        return {"ok": False, "changed": False, "reason": "사용자 승인 필요"}
    if is_excluded_target(p.get("target") or {}):
        return {"ok": False, "changed": False, "reason": "보호 대상(모닝논문) 차단"}
    kind = p.get("target", {}).get("kind")
    if kind == "unsure":
        return {"ok": False, "changed": False, "reason": "target unsure"}

    ledger = _load_ledger(ledger_path)
    applied = set(ledger.get("applied_ids") or [])
    if ingest_id in applied:
        return {"ok": True, "changed": False, "reason": "already applied (ledger)"}

    today = _today()
    try:
        if kind == "existing":
            changed, reason = _apply_existing_proposal(wiki_root, p, today)
        elif kind == "new":
            changed, reason = _apply_new_proposal(wiki_root, p, today)
        else:
            return {"ok": False, "changed": False, "reason": f"미지원 kind: {kind}"}
    except Exception as e:
        return {"ok": False, "changed": False, "reason": f"apply 예외: {e}"}

    if changed:
        ledger.setdefault("applied_ids", [])
        ledger["applied_ids"].append(ingest_id)
        ledger["updated"] = today
        _save_ledger(ledger_path, ledger)
        return {"ok": True, "changed": True, "reason": reason}
    return {"ok": True, "changed": False, "reason": reason}


def apply_batch(draft_json_path: str | Path, only_ids: list[str] | None = None, wiki_root: Path = WIKI_ROOT, ledger_path: Path = MERGE_LEDGER_PATH) -> dict[str, Any]:
    p = Path(draft_json_path)
    payload = json.loads(p.read_text(encoding="utf-8"))
    proposals = payload.get("proposals") or []
    selected = set(only_ids) if only_ids else None
    results: list[dict[str, Any]] = []
    for prop in proposals:
        ingest_id = prop.get("ingest_id")
        if selected is not None and ingest_id not in selected:
            continue
        r = apply_proposal(prop, wiki_root=wiki_root, ledger_path=ledger_path)
        results.append({"ingest_id": ingest_id, **r})
    return {
        "ok": all(x.get("ok") for x in results) if results else True,
        "count": len(results),
        "applied": sum(1 for x in results if x.get("changed")),
        "results": results,
    }
