from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from thinking_compute import THINKING_ROOT, RAW_ROOT, WIKI_ROOT, build_index

DRAFT_ROOT = THINKING_ROOT / "_autowiki_drafts"
SEED_CATEGORIES = {"회사", "연구", "제안서", "랩문화", "개인", "시스템"}


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
    phr = phraser or _default_phraser
    lookup = _build_page_lookup()

    proposals: list[Proposal] = []
    seen_ids: set[str] = set()
    for item in _parse_raw_sections(raw_path):
        mention = item["mentions"][0] if item["mentions"] else None
        target, needs_confirm, warnings = _resolve_target(mention, lookup, raw_date)

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
