"""thinking 폴더 → 페이지 모델 + 인덱스.

WIKI_ROOT 의 .md 파일 (frontmatter + sections) 을 파싱해서
페이지 목록·카테고리별·결정 트래커·타임라인 합본·백링크 등 제공.

원본 진실원천은 .md. 매 request 다시 파싱 (1인 + 페이지 수 적음).
나중에 stale-mtime 캐시 도입 가능.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from settings import SETTINGS

THINKING_ROOT = SETTINGS.thinking_root
WIKI_ROOT = THINKING_ROOT / "wiki"
RAW_ROOT = THINKING_ROOT / "raw"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

# 헤딩 → 섹션 키 매핑 (유사 표현 흡수)
SECTION_HEADINGS = {
    "지금 생각": "current",
    "현재 생각": "current",
    "결정·방향": "decisions",
    "결정과 방향": "decisions",
    "결정": "decisions",
    "할 일": "todos",
    "할일": "todos",
    "열린 질문": "open_questions",
    "열린질문": "open_questions",
    "타임라인": "timeline",
    "배경": "background",
    "현황": "context",
}


# ---------- 파싱 ----------

def parse_page(path: Path) -> dict | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        meta = {}
        body = text
    else:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        body = m.group(2)

    sections = parse_sections(body)
    body_links = re.findall(r"\[\[([^\]]+)\]\]", body)

    rel = path.relative_to(THINKING_ROOT)
    return {
        "path": str(rel),
        "abs_path": str(path),
        "meta": meta,
        "title": meta.get("title") or path.stem,
        "slug": meta.get("slug") or path.stem,
        "category": meta.get("category_primary") or "기타",
        "type": meta.get("type") or "thought",
        "status": meta.get("status") or "active",
        "tags": meta.get("tags") or [],
        "links": meta.get("links") or [],
        "aliases": meta.get("aliases") or [],
        "body_links": body_links,
        "updated": str(meta.get("updated") or ""),
        "created": str(meta.get("created") or ""),
        "body": body,
        "sections": sections,
        "source_thinking": meta.get("source_thinking"),
        "synopsis": meta.get("synopsis"),
        "outputs": meta.get("outputs") or [],
    }


def parse_sections(body: str) -> dict:
    """## 헤딩 기준 섹션 추출. 키는 SECTION_HEADINGS 매핑된 영문 key."""
    sections: dict[str, str] = {}
    current_key = None
    current_lines: list[str] = []
    for line in body.split("\n"):
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            heading = m.group(1).strip()
            current_key = SECTION_HEADINGS.get(heading, heading)
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(line)
    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()
    return sections


import hashlib as _hashlib

IMPL_KR_TO_EN = {"완료": "shipped", "진행": "in_progress", "계획": "planned"}
IMPL_EN_TO_KR = {v: k for k, v in IMPL_KR_TO_EN.items()}


def _decision_norm(line: str) -> str:
    """raw 결정 줄에서 `| 구현: X` 토큰을 제거 (상태 토글에도 hash 안정)."""
    return re.sub(r"\s*\|\s*구현:\s*\S+", "", line).strip()


def decision_hash(line: str) -> str:
    return _hashlib.sha256(_decision_norm(line).encode("utf-8")).hexdigest()[:10]


def parse_decisions(section_text: str) -> list[dict]:
    """결정·방향 섹션 한 줄 파싱.
    형식: - YYYY-MM-DD | 결정: ... | 근거: ... | 상태: 유효/폐기 | 구현: 계획/진행/완료
    """
    out = []
    for line in section_text.split("\n"):
        line = line.strip()
        if not line.startswith("- "):
            continue
        line = line[2:].strip()
        date_m = re.match(r"^(\d{4}-\d{2}-\d{2})", line)
        date_str = date_m.group(1) if date_m else None
        is_dropped = "폐기" in line
        dec_m = re.search(r"결정:\s*(.+?)(?=\s*\|\s*(?:근거|상태|구현)|$)", line)
        decision = dec_m.group(1).strip() if dec_m else line
        reason_m = re.search(r"근거:\s*(.+?)(?=\s*\|\s*(?:상태|구현)|$)", line)
        reason = reason_m.group(1).strip() if reason_m else ""
        impl_m = re.search(r"구현:\s*(완료|진행|계획)", line)
        impl_status = IMPL_KR_TO_EN.get(impl_m.group(1)) if impl_m else "planned"
        out.append({
            "raw": line,
            "date": date_str,
            "decision": decision,
            "reason": reason,
            "is_dropped": is_dropped,
            "impl_status": impl_status,
            "impl_status_real": impl_status if impl_m else None,
            "hash": decision_hash(line),
        })
    return out


def update_decision_impl(rel_path: str, dec_hash: str, new_status: str) -> dict:
    """page md 파일에서 dec_hash 매칭되는 결정 줄의 `구현:` 칸을 new_status 로 교체.
    파일 단위 flock + atomic rename."""
    import fcntl
    import os
    if new_status not in {"planned", "in_progress", "shipped", "dropped"}:
        return {"ok": False, "error": "bad status"}
    p = (THINKING_ROOT / rel_path).resolve()
    try:
        p.relative_to(THINKING_ROOT.resolve())
    except ValueError:
        return {"ok": False, "error": "path traversal"}
    if not p.exists() or p.suffix != ".md":
        return {"ok": False, "error": "page not found"}
    # dropped 은 별도 상태 칸(상태: 폐기), 구현 칸은 계획으로 되돌려두지 않고 그대로 둠
    new_kr = IMPL_EN_TO_KR.get(new_status)  # dropped 면 None
    with open(p, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        text = f.read()
    lines = text.split("\n")
    changed = False
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if decision_hash(body) != dec_hash:
            continue
        # 상태 칸 (폐기 토글)
        if new_status == "dropped":
            if "폐기" not in body:
                if "상태:" in body:
                    body = re.sub(r"상태:\s*\S+", "상태: 폐기", body)
                else:
                    body = body + " | 상태: 폐기"
        else:
            body = re.sub(r"상태:\s*폐기[^|]*", "상태: 유효", body)
            if "상태:" not in body:
                body = body + " | 상태: 유효"
        # 구현 칸
        if new_kr is not None:
            if "구현:" in body:
                body = re.sub(r"구현:\s*\S+", f"구현: {new_kr}", body)
            else:
                body = body + f" | 구현: {new_kr}"
        # 들여쓰기 보존
        indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        lines[i] = f"{indent}- {body}"
        changed = True
        break
    if not changed:
        return {"ok": False, "error": "decision not found"}
    new_text = "\n".join(lines)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, p)
    return {"ok": True, "status": new_status}


CLAUDE_BIN = SETTINGS.claude_bin


def auto_mark_page(rel_path: str, hint: str = "", timeout: int = 60) -> dict:
    """페이지 본문 + 결정 리스트를 Claude 에게 주고, 각 결정의 현재 구현 상태를 JSON 으로 받아 .md 갱신.
    sources of truth: 페이지 본문 ("지금" 섹션, outputs, dream/solid/open, 타임라인) + 사용자 hint.
    """
    import json as _json
    import subprocess as _sp
    page = get_page_by_path(rel_path)
    if not page:
        return {"ok": False, "error": "page not found"}
    decisions = parse_decisions(page["sections"].get("decisions", ""))
    live = [d for d in decisions if not d["is_dropped"]]
    if not live:
        return {"ok": True, "applied": 0, "total": 0, "results": []}

    # 페이지 컨텍스트 — 본문 핵심 섹션만
    sections = page["sections"]
    ctx_parts = [f"# {page['title']}"]
    if sections.get("current"):
        ctx_parts.append("## 지금\n" + sections["current"])
    if page.get("outputs"):
        ctx_parts.append("## 운영중인 결과물\n" + "\n".join(f"- {o.get('label')}: {o.get('url')}" for o in page["outputs"]))
    syn = page.get("synopsis") or {}
    if syn.get("solid"):
        ctx_parts.append("## 안정 (solid)\n" + "\n".join(f"- {s}" for s in syn["solid"]))
    if syn.get("open"):
        ctx_parts.append("## 열림 (open, 아직 안된 것)\n" + "\n".join(f"- {o}" for o in syn["open"]))
    if sections.get("timeline"):
        ctx_parts.append("## 최근 타임라인 (참고)\n" + sections["timeline"][:1500])
    context = "\n\n".join(ctx_parts)

    dec_list = [{"hash": d["hash"], "date": d["date"], "decision": d["decision"], "current": d["impl_status"]} for d in live]

    system_prompt = (
        "당신은 한 페이지의 결정들이 *현재* 얼마나 구현되었는지 판단합니다.\n"
        "각 결정에 대해 다음 4 상태 중 하나를 골라 JSON 으로 답하세요:\n"
        "- shipped: 운영중·완료. 페이지의 outputs 나 안정(solid) 또는 타임라인에서 명백히 구현/적용됐다고 보임.\n"
        "- in_progress: 진행중. 일부 구현됐거나 mockup·계획 단계 통과.\n"
        "- planned: 아직 안 됨. 결정만 있고 구현 흔적 없음.\n"
        "- dropped: 폐기되어야 함 (이미 다른 결정으로 대체됨이 명백할 때만).\n"
        "보수적으로 판단하세요. 애매하면 planned 유지.\n"
        "응답은 반드시 JSON 한 덩어리만, 코드블록 없이:\n"
        '{"decisions": [{"hash": "abc1234567", "status": "shipped", "why": "한 줄 근거"}]}\n'
        "hash 는 입력과 동일하게 echo. why 는 16자 이내 한국어."
    )
    user_input = (
        "## 페이지 컨텍스트\n" + context +
        ("\n\n## 사용자 힌트\n" + hint if hint else "") +
        "\n\n## 판단할 결정 목록\n" + _json.dumps(dec_list, ensure_ascii=False, indent=2)
    )

    try:
        result = _sp.run(
            [CLAUDE_BIN, "--print", "--model", "claude-opus-4-7", "--system-prompt", system_prompt],
            input=user_input, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"claude exit {result.returncode}: {result.stderr[:300]}"}
        raw = result.stdout.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError:
            return {"ok": False, "error": "JSON parse fail", "raw": raw[:500]}
    except _sp.TimeoutExpired:
        return {"ok": False, "error": f"claude timeout ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    items = parsed.get("decisions") or []
    applied = 0
    results = []
    for it in items:
        h = it.get("hash")
        st = it.get("status")
        if not h or st not in {"planned", "in_progress", "shipped", "dropped"}:
            continue
        r = update_decision_impl(rel_path, h, st)
        if r.get("ok"):
            applied += 1
        results.append({"hash": h, "status": st, "why": it.get("why"), "ok": r.get("ok"), "error": r.get("error")})
    return {"ok": True, "applied": applied, "total": len(live), "results": results}


def page_impl_stats(decisions: list[dict]) -> dict:
    """페이지/그룹 구현률 — 분모에서 폐기 제외."""
    total = sum(1 for d in decisions if not d["is_dropped"])
    by = {"planned": 0, "in_progress": 0, "shipped": 0, "dropped": 0}
    for d in decisions:
        if d["is_dropped"]:
            by["dropped"] += 1
        else:
            by[d.get("impl_status", "planned")] += 1
    pct = round(100 * by["shipped"] / total) if total else 0
    return {"total": total, "by": by, "pct": pct}


def parse_timeline(section_text: str) -> list[dict]:
    """타임라인 섹션 한 줄 파싱.
    형식: - YYYY-MM-DD: 변화/생각 (출처 raw/YYYY-MM-DD.md)
    """
    out = []
    for line in section_text.split("\n"):
        line = line.strip()
        if not line.startswith("- "):
            continue
        line = line[2:].strip()
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[:·]\s*(.*)$", line)
        if m:
            out.append({"date": m.group(1), "text": m.group(2), "raw": line})
        else:
            out.append({"date": None, "text": line, "raw": line})
    return out


def parse_todos(section_text: str) -> list[dict]:
    """할 일 섹션 한 줄 파싱.
    형식: - [ ] 할 일 / - [x] 완료된 일
    """
    out = []
    for line in section_text.split("\n"):
        line = line.strip()
        m = re.match(r"^-\s*\[([ xX])\]\s*(.+)$", line)
        if m:
            out.append({
                "done": m.group(1).lower() == "x",
                "text": m.group(2).strip(),
            })
        elif line.startswith("- "):
            out.append({"done": False, "text": line[2:].strip()})
    return out


# ---------- 인덱스 ----------

def find_all_pages() -> list[dict]:
    """wiki/ 전체 페이지 스캔. _index.md 는 제외."""
    pages = []
    if not WIKI_ROOT.exists():
        return pages
    for md_path in WIKI_ROOT.rglob("*.md"):
        if md_path.name == "_index.md":
            continue
        page = parse_page(md_path)
        if page:
            pages.append(page)
    return pages


def build_index(today: date) -> dict:
    pages = find_all_pages()

    # 시드 카테고리 6개 (thinking/CLAUDE.md) — 빈 카테고리도 트리에 보이게
    SEED_CATEGORIES = ["시스템", "연구", "제안서", "랩문화", "회사", "개인"]
    by_category: dict[str, list] = {cat: [] for cat in SEED_CATEGORIES}
    for p in pages:
        by_category.setdefault(p["category"], []).append(p)
    # 카테고리별 페이지 갱신일 순
    for cat in by_category.values():
        cat.sort(key=lambda p: p.get("updated") or "", reverse=True)

    long_term_plans = [p for p in pages if p["type"] == "long-term-plan"]
    ideas = [p for p in pages if p["type"] == "idea"]
    thoughts = [p for p in pages if p["type"] == "thought"]

    recent = sorted(pages, key=lambda p: p.get("updated") or "", reverse=True)[:8]

    # 결정 합본
    all_decisions = []
    for p in pages:
        decs = parse_decisions(p["sections"].get("decisions", ""))
        for d in decs:
            d2 = dict(d)
            d2["page_title"] = p["title"]
            d2["page_slug"] = p["slug"]
            d2["page_path"] = p["path"]
            d2["page_category"] = p["category"]
            all_decisions.append(d2)
    # 날짜 내림차순
    all_decisions.sort(key=lambda d: d.get("date") or "", reverse=True)

    # 타임라인 합본
    all_timeline = []
    for p in pages:
        events = parse_timeline(p["sections"].get("timeline", ""))
        for e in events:
            e2 = dict(e)
            e2["page_title"] = p["title"]
            e2["page_slug"] = p["slug"]
            e2["page_path"] = p["path"]
            all_timeline.append(e2)
    all_timeline.sort(key=lambda e: e.get("date") or "", reverse=True)

    # 백링크 — title 기반 (또는 slug)
    backlinks: dict[str, list] = {}
    for p in pages:
        link_targets = set()
        link_targets.update(p.get("links") or [])
        link_targets.update(p.get("body_links") or [])
        for tgt in link_targets:
            backlinks.setdefault(tgt, []).append({
                "title": p["title"],
                "path": p["path"],
                "category": p["category"],
            })

    stats = {
        "total_pages": len(pages),
        "active_count": sum(1 for p in pages if p["status"] == "active"),
        "long_term_count": len(long_term_plans),
        "categories": {k: len(v) for k, v in by_category.items()},
        "total_decisions": len(all_decisions),
        "active_decisions": sum(1 for d in all_decisions if not d["is_dropped"]),
    }

    return {
        "pages": pages,
        "by_category": by_category,
        "long_term_plans": long_term_plans,
        "ideas": ideas,
        "thoughts": thoughts,
        "recent": recent,
        "all_decisions": all_decisions,
        "all_timeline": all_timeline,
        "backlinks": backlinks,
        "stats": stats,
    }


_RAW_CACHE = {"mtime_sig": None, "data": None}


def _normalize_key(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _parse_raw_file(path: Path) -> list[dict]:
    """raw/YYYY-MM-DD.md → ## 헤더 단위 묶음 리스트.
    각 묶음 끝의 `-> [[X]]` (한 줄에 여러 개 가능) 를 mentions 로 모은다.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    date_str = path.stem  # YYYY-MM-DD
    chunks = []
    current = None
    for line in text.split("\n"):
        if line.startswith("## "):
            if current is not None:
                chunks.append(current)
            current = {
                "date": date_str,
                "heading": line[3:].strip(),
                "lines": [],
                "mentions": [],
                "file": path.name,
            }
        elif line.startswith("# "):
            continue  # 파일 제목 (# 2026-05-15) 등 무시
        else:
            if current is None:
                current = {"date": date_str, "heading": "(머리말)", "lines": [], "mentions": [], "file": path.name}
            current["lines"].append(line)
            for m in re.finditer(r"->\s*\[\[([^\]]+)\]\]", line):
                current["mentions"].append(m.group(1).strip())
    if current is not None:
        chunks.append(current)
    for c in chunks:
        body = "\n".join(c["lines"]).strip("\n")
        c["body"] = body
        preview_lines = [ln for ln in body.split("\n") if ln.strip()][:2]
        c["preview"] = "\n".join(preview_lines)
    return chunks


def build_raw_index() -> dict:
    """모든 raw/*.md 파싱. mtime sig 기반 캐시."""
    files = sorted(RAW_ROOT.glob("*.md")) if RAW_ROOT.exists() else []
    sig = tuple((f.name, f.stat().st_mtime_ns) for f in files)
    if _RAW_CACHE["mtime_sig"] == sig and _RAW_CACHE["data"] is not None:
        return _RAW_CACHE["data"]
    chunks_all = []
    by_mention: dict[str, list] = {}
    for f in files:
        for c in _parse_raw_file(f):
            chunks_all.append(c)
            for tgt in c.get("mentions") or []:
                by_mention.setdefault(_normalize_key(tgt), []).append(c)
    # 페이지별 묶음은 날짜 역순
    for key in by_mention:
        by_mention[key].sort(key=lambda x: x.get("date") or "", reverse=True)
    data = {"chunks": chunks_all, "by_mention": by_mention, "files": [f.name for f in files]}
    _RAW_CACHE["mtime_sig"] = sig
    _RAW_CACHE["data"] = data
    return data


def get_page_raw_mentions(page: dict) -> list[dict]:
    """페이지를 가리키는 raw 묶음들 (title + aliases + slug 모두 매칭)."""
    raw_idx = build_raw_index()
    keys = set()
    for k in [page.get("title"), page.get("slug")] + (page.get("aliases") or []):
        if k:
            keys.add(_normalize_key(k))
    seen = set()
    out = []
    for k in keys:
        for c in raw_idx["by_mention"].get(k, []):
            cid = id(c)  # 동일 chunk 객체만 중복 제거 (같은 file 안 같은 heading 둘이면 둘 다 살림)
            if cid in seen:
                continue
            seen.add(cid)
            out.append(c)
    out.sort(key=lambda x: x.get("date") or "", reverse=True)
    return out


def get_page_by_path(rel_path: str) -> dict | None:
    """thinking/ 상대 경로로 페이지 로드. 루트 탈출 방어 + .md 만 허용."""
    p = (THINKING_ROOT / rel_path).resolve()
    try:
        p.relative_to(THINKING_ROOT.resolve())
    except ValueError:
        return None
    if p.suffix != ".md":
        return None
    return parse_page(p)


def get_page_backlinks(page: dict, index: dict) -> list[dict]:
    """페이지로 들어오는 백링크. title + aliases 둘 다 확인."""
    out = []
    seen_paths = set()
    keys = [page["title"]] + (page.get("aliases") or []) + [page.get("slug")]
    for key in keys:
        if not key:
            continue
        for bl in index["backlinks"].get(key, []):
            if bl["path"] == page["path"]:
                continue  # self
            if bl["path"] in seen_paths:
                continue
            seen_paths.add(bl["path"])
            out.append(bl)
    return out


# ============================================================
# Mockup 전용 데이터 가공 — /mockups/* 에서 inject 용
# ============================================================
from collections import defaultdict


def build_mockup_data(today=None) -> dict:
    """6개 mockup 이 공통으로 쓰는 가공 dict."""
    from datetime import date as _date, timedelta
    if today is None:
        today = _date.today()
    idx = build_index(today)

    # ── 1) 그래프 노드/엣지
    pages = idx["pages"]
    title_to_idx = {p["title"]: i for i, p in enumerate(pages)}
    # alias 도
    for i, p in enumerate(pages):
        for a in (p.get("aliases") or []):
            title_to_idx.setdefault(a, i)
    nodes = []
    for i, p in enumerate(pages):
        decs = parse_decisions(p["sections"].get("decisions", ""))
        tl = parse_timeline(p["sections"].get("timeline", ""))
        nodes.append({
            "id": i,
            "title": p["title"],
            "category": p["category"],
            "type": p["type"],
            "status": p["status"],
            "updated": p["updated"][:10],
            "decisions": len(decs),
            "timeline": len(tl),
            "weight": len(decs) + len(tl),
            "path": p["path"],
        })
    edges = []
    seen_edge = set()
    for i, p in enumerate(pages):
        for link in (p.get("links") or []):
            j = title_to_idx.get(link)
            if j is None or j == i:
                continue
            key = tuple(sorted([i, j]))
            if key in seen_edge:
                continue
            seen_edge.add(key)
            edges.append({"source": i, "target": j})

    # ── 2) 히트맵 (최근 180일 일자별 활동량)
    activity = defaultdict(int)
    page_daily = defaultdict(lambda: defaultdict(int))  # page_title -> date -> score
    for d in idx["all_decisions"]:
        dt = d.get("date")
        if dt:
            activity[dt] += 3
            page_daily[d["page_title"]][dt] += 3
    for e in idx["all_timeline"]:
        dt = e.get("date")
        if dt:
            activity[dt] += 2
            page_daily[e["page_title"]][dt] += 2
    # 최근 180일 채우기 (0 포함)
    heat_cells = []
    start = today - timedelta(days=180)
    cur = start
    while cur <= today:
        s = cur.isoformat()
        heat_cells.append({"date": s, "score": activity.get(s, 0),
                           "weekday": cur.weekday()})
        cur += timedelta(days=1)
    # 최근 7/30일 토픽
    def topic_score(days):
        cutoff = today - timedelta(days=days)
        out = defaultdict(int)
        for title, daily in page_daily.items():
            for dt, sc in daily.items():
                try:
                    if _date.fromisoformat(dt) >= cutoff:
                        out[title] += sc
                except ValueError:
                    pass
        return sorted(out.items(), key=lambda x: -x[1])
    top_7d = topic_score(7)
    top_30d = topic_score(30)

    # ── 3) Decision River (이미 sort 됨 in build_index)
    decisions_all = idx["all_decisions"]
    # 페이지별 색 인덱스
    page_color = {p["title"]: i % 8 for i, p in enumerate(pages)}

    # ── 4) Folder tree (활동 점수 포함)
    tree = []
    for cat, ps in idx["by_category"].items():
        cat_pages = []
        for p in ps:
            decs = parse_decisions(p["sections"].get("decisions", ""))
            todos = parse_todos(p["sections"].get("todos", ""))
            tl = parse_timeline(p["sections"].get("timeline", ""))
            done_n = sum(1 for t in todos if t["done"])
            try:
                days_idle = (today - _date.fromisoformat(p["updated"][:10])).days
            except ValueError:
                days_idle = 999
            cat_pages.append({
                "title": p["title"],
                "path": p["path"],
                "type": p["type"],
                "status": p["status"],
                "decisions": len(decs),
                "todos_total": len(todos),
                "todos_done": done_n,
                "timeline": len(tl),
                "updated": p["updated"][:10],
                "days_idle": days_idle,
                "activity_recent": sum(sc for dt, sc in page_daily[p["title"]].items()
                                       if _date.fromisoformat(dt) >= today - timedelta(days=30)) if page_daily[p["title"]] else 0,
            })
        tree.append({"category": cat, "pages": cat_pages})

    # ── 5) Pipeline (페이지별 4-단계 점수)
    pipeline = []
    for p in pages:
        decs = parse_decisions(p["sections"].get("decisions", ""))
        todos = parse_todos(p["sections"].get("todos", ""))
        refine_len = len(p["sections"].get("current", "")) + len(p["sections"].get("decisions", ""))
        capture_len = len(p.get("raw_body", "")) if "raw_body" in p else refine_len * 2
        done_n = sum(1 for t in todos if t["done"])
        # 승격 = source_thinking 가 자기를 가리키는 dashboard 프로젝트 (간이: 0 으로 두고 TODO)
        promoted = 0
        pipeline.append({
            "title": p["title"],
            "path": p["path"],
            "capture": min(100, capture_len // 30),
            "refine": min(100, refine_len // 30),
            "decision": min(100, len(decs) * 12),
            "todo_pct": int(100 * done_n / len(todos)) if todos else 0,
            "todo_open": len(todos) - done_n,
            "promoted": promoted,
        })

    # ── 6) Focus Cards — 집중도 점수 (최근 14일 가중)
    focus = []
    for p in pages:
        decs = parse_decisions(p["sections"].get("decisions", ""))
        todos = parse_todos(p["sections"].get("todos", ""))
        open_q_len = len(p["sections"].get("open_questions", "").strip())
        recent_score = 0
        for dt, sc in page_daily[p["title"]].items():
            try:
                age = (today - _date.fromisoformat(dt)).days
                if age <= 14:
                    recent_score += sc * (1 - age / 14)  # 가까울수록 가중
            except ValueError:
                pass
        # 마지막 결정 1개
        last_dec = None
        for d in idx["all_decisions"]:
            if d["page_title"] == p["title"]:
                last_dec = d
                break
        impl = page_impl_stats(decs)
        focus.append({
            "title": p["title"],
            "path": p["path"],
            "category": p["category"],
            "score": round(recent_score, 1),
            "decisions": len(decs),
            "open_questions_chars": open_q_len,
            "todos_open": sum(1 for t in todos if not t["done"]),
            "last_decision": last_dec,
            "updated": p["updated"][:10],
            "impl_pct": impl["pct"],
            "impl_shipped": impl["by"]["shipped"],
            "impl_total": impl["total"],
        })
    focus.sort(key=lambda x: -x["score"])

    return {
        "today": today.isoformat(),
        "stats": idx["stats"],
        "all_timeline": idx["all_timeline"],
        "nodes": nodes,
        "edges": edges,
        "heat_cells": heat_cells,
        "top_7d": top_7d,
        "top_30d": top_30d,
        "decisions_all": decisions_all,
        "page_color": page_color,
        "tree": tree,
        "pipeline": pipeline,
        "focus": focus,
    }


# ============================================================
# Chat (Spotlight 우측 챗봇) — 실 동작
# ============================================================
import subprocess as _sp_t
import json as _json_t
from datetime import datetime as _dt_t

CLAUDE_BIN_T = SETTINGS.claude_bin
RAW_DIR = THINKING_ROOT / "raw"


def append_raw(text: str) -> dict:
    """사용자 발화를 raw/YYYY-MM-DD.md 에 append. 원자적 (flock)."""
    import fcntl, os
    if not text or not text.strip():
        return {"ok": False, "error": "empty"}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    today = _dt_t.now().date().isoformat()
    target = RAW_DIR / f"{today}.md"
    ts = _dt_t.now().strftime("%H:%M")
    entry = f"\n## {ts} (chat)\n\n{text.strip()}\n"
    # 새 파일이면 헤더
    is_new = not target.exists()
    with open(target, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        if is_new:
            f.write(f"# raw — {today}\n")
        f.write(entry)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return {"ok": True, "path": str(target.relative_to(THINKING_ROOT)), "timestamp": ts}


def _think_system_prompt(today_iso: str, page_titles: list[str]) -> str:
    pages_line = ", ".join(page_titles) if page_titles else "(아직 없음)"
    return f"""당신은 사용자의 thinking 시스템 대화 동반자입니다.
오늘은 {today_iso}.
WJ의 현재 thinking 페이지: {pages_line}

당신의 역할:
- WJ가 던지는 생각을 받아주고 자연스럽게 대화합니다.
- 일방적 양식/폼 응답 절대 금지. 진짜 사람처럼 짧고 자연스러운 한국어 대화체.
- 필요하면 한 가지만 질문해서 명확화 (한꺼번에 여러 개 묻지 말 것).
- WJ의 발화는 raw 파일에 이미 자동 저장됩니다 — "저장했습니다" 류 알림 금지.
- WJ가 명시적으로 "정리하자", "결정으로 박자", "어디 페이지에 넣지" 같이 요청할 때만 정제/분류 제안.
- 정제 제안 시: 어느 페이지의 어느 섹션(지금 생각/결정·방향/할 일/열린 질문/타임라인)에 어떻게 들어갈지 제시.
- 답변은 짧게 (보통 1~3문장). 길어지면 가독성 떨어짐.
- WJ는 평어/구어로 던지므로 당신도 격식 풀고 답할 것. 다만 존댓말 유지.
- 마크다운 강조(**굵게**, *기울임*) 가능. 코드블록은 patch 제안할 때만.

WJ가 그냥 푸념/생각 정리만 하는 경우가 많으니, 충실히 듣고 공감하고 가벼운 되묻기로 충분합니다."""


def think_chat(messages: list[dict], timeout: int = 60) -> dict:
    """thinking 챗봇 — 자연어 대화. JSON 강제 X.
    messages = [{role: 'user'|'assistant', content: str}, ...]
    """
    today_iso = _dt_t.now().date().isoformat()
    # 현 페이지 목록
    idx = build_index(_dt_t.now().date())
    page_titles = [p["title"] for p in idx["pages"]]
    system_prompt = _think_system_prompt(today_iso, page_titles)

    convo = ""
    for m in messages:
        role = "User" if m["role"] == "user" else "Bot"
        convo += f"[{role}]: {m['content']}\n\n"
    convo += "[Bot]:"

    try:
        result = _sp_t.run(
            [CLAUDE_BIN_T, "--print", "--model", "claude-opus-4-7",
             "--system-prompt", system_prompt],
            input=convo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"claude exit {result.returncode}: {result.stderr[:300]}"}
        reply = result.stdout.strip()
        # 코드블록 제거 안 함 (정제 제안 시 코드블록 그대로 보여줘야)
        return {"ok": True, "reply": reply}
    except _sp_t.TimeoutExpired:
        return {"ok": False, "error": "claude timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def build_implementation_tracker() -> dict:
    """구현 추적 mockup — fake 매핑 (시간 오래된 순서 = 구현 완료).
    실제는 decision frontmatter 또는 별도 dict 으로 매핑할 것.
    """
    from datetime import date as _date
    idx = build_index(_date.today())
    # 시간 오름차순 정렬 (오래된 게 먼저)
    decs = sorted(idx["all_decisions"], key=lambda d: d.get("date") or "")
    total = len(decs)
    n_ship = total * 2 // 5      # 40% shipped
    n_prog = total * 1 // 5      # 20% in_progress
    # 실제 라벨(impl_status_real) 우선, 없으면 fake fallback
    LABELS = {"shipped": "✅ 구현 완료", "in_progress": "⏳ 진행 중", "planned": "📋 계획"}
    for i, d in enumerate(decs):
        if d.get("impl_status_real"):
            d["impl_status"] = d["impl_status_real"]
        else:
            if i < n_ship:
                d["impl_status"] = "shipped"
            elif i < n_ship + n_prog:
                d["impl_status"] = "in_progress"
            else:
                d["impl_status"] = "planned"
        d["impl_label"] = LABELS[d["impl_status"]]
    # 페이지별 구현률
    page_impl = {}
    for d in decs:
        t = d["page_title"]
        page_impl.setdefault(t, {"total": 0, "shipped": 0, "in_progress": 0, "planned": 0})
        page_impl[t]["total"] += 1
        page_impl[t][d["impl_status"]] += 1
    for t, stats in page_impl.items():
        stats["pct"] = round(100 * stats["shipped"] / stats["total"]) if stats["total"] else 0
    return {
        "decisions": list(reversed(decs)),  # 최근 순으로 다시
        "page_impl": page_impl,
        "total": total,
        "shipped": n_ship,
        "in_progress": n_prog,
        "planned": total - n_ship - n_prog,
    }


def build_tree_data():
    """트리 mockup 5종 공통 — 결정에 phase/domain/evidence fake 부여."""
    impl = build_implementation_tracker()
    PHASES = ["MVP", "V2", "V3"]
    for i, d in enumerate(impl["decisions"]):
        # phase: 오래된 = MVP, 최근 = V3
        if d["impl_status"] == "shipped":
            d["phase"] = "MVP"
        elif d["impl_status"] == "in_progress":
            d["phase"] = "V2"
        else:
            d["phase"] = "V3"
        # domain: 키워드 매칭
        text = (d["decision"] + " " + d.get("reason", "")).lower()
        if any(k in text for k in ["레이아웃", "bento", "색상", "css", "ui", "디자인", "모바일"]):
            d["domain"] = "UI/레이아웃"
        elif any(k in text for k in ["spotlight", "검색", "search", "focus"]):
            d["domain"] = "검색/Spotlight"
        elif any(k in text for k in ["챗봇", "chat", "claude", "opus", "대화", "메시지"]):
            d["domain"] = "챗봇/AI"
        elif any(k in text for k in ["추적", "tracker", "진행", "구현", "트리", "파이프"]):
            d["domain"] = "데이터/추적"
        elif any(k in text for k in ["thinking", "결정", "정제", "raw", "wiki"]):
            d["domain"] = "thinking 스키마"
        else:
            d["domain"] = "기타"
        # fake evidence
        if d["impl_status"] == "shipped":
            d["evidence"] = {"commits": 2 + (i % 3), "files": 1 + (i % 3), "pr": f"#{100+i}", "tests": "pass"}
        elif d["impl_status"] == "in_progress":
            d["evidence"] = {"commits": 1, "files": 2, "pr": f"#{100+i} (open)", "tests": "pending"}
        else:
            d["evidence"] = {"commits": 0, "files": 0, "pr": "-", "tests": "-"}
    # 그룹화 helpers
    by_page = {}
    by_phase = {"MVP": [], "V2": [], "V3": []}
    by_domain = {}
    by_date = {}
    for d in impl["decisions"]:
        by_page.setdefault(d["page_title"], []).append(d)
        by_phase[d["phase"]].append(d)
        by_domain.setdefault(d["domain"], []).append(d)
        dt = d.get("date") or "날짜 없음"
        by_date.setdefault(dt, []).append(d)
    # 페이지별 phase 진행률
    page_phase_pct = {}
    for title, decs in by_page.items():
        per_phase = {"MVP": [], "V2": [], "V3": []}
        for d in decs:
            per_phase[d["phase"]].append(d)
        page_phase_pct[title] = {}
        for phase, ds in per_phase.items():
            if not ds:
                page_phase_pct[title][phase] = None
            else:
                ship = sum(1 for d in ds if d["impl_status"] == "shipped")
                page_phase_pct[title][phase] = {
                    "pct": round(100*ship/len(ds)),
                    "shipped": ship,
                    "total": len(ds),
                }
    return {
        "impl": impl,
        "by_page": by_page,
        "by_phase": by_phase,
        "by_domain": by_domain,
        "by_date": dict(sorted(by_date.items(), reverse=True)),
        "page_phase_pct": page_phase_pct,
    }


def build_hierarchy_data():
    """d3 hierarchy 표준 트리 dict. visualization mockup 공통."""
    tree = build_tree_data()
    PAGE_COLOR = {
        "wj 앱": "#a855f7",
        "업무 대시보드": "#3b82f6",
        "thinking 시스템": "#16a34a",
        "논문 관리 시스템": "#f59e0b",
    }
    root_children = []
    for title, decs in tree["by_page"].items():
        ship = sum(1 for d in decs if d["impl_status"] == "shipped")
        prog = sum(1 for d in decs if d["impl_status"] == "in_progress")
        plan = sum(1 for d in decs if d["impl_status"] == "planned")
        page_node = {
            "name": title,
            "type": "page",
            "color": PAGE_COLOR.get(title, "#64748b"),
            "shipped": ship, "in_progress": prog, "planned": plan,
            "total": len(decs),
            "pct": round(100*ship/len(decs)) if decs else 0,
            "children": [
                {
                    "name": d["decision"][:60],
                    "full_text": d["decision"],
                    "type": "decision",
                    "impl": d["impl_status"],
                    "phase": d.get("phase"),
                    "domain": d.get("domain"),
                    "date": d.get("date"),
                    "value": 1,
                } for d in decs
            ]
        }
        root_children.append(page_node)
    return {
        "name": "thinking 시스템",
        "type": "root",
        "children": root_children,
        "stats": tree["impl"],
    }


# ============================================================
# 결정 구현 상태 자동 분류 (Claude Opus)
# ============================================================
import json as _json_c

def classify_decisions_with_claude(timeout: int = 180) -> dict:
    """30개 결정 일괄 분류. wj-dashboard 코드 트리도 같이 컨텍스트로.
    출력: {decisions: [{page_title, date, decision, suggested_status, suggested_evidence}]}
    """
    from datetime import date as _date
    idx = build_index(_date.today())
    decs = idx["all_decisions"]
    if not decs:
        return {"ok": False, "error": "no decisions"}

    # wj-dashboard 의 파일 목록 (단순)
    code_root = Path(__file__).resolve().parent
    code_files = []
    for f in code_root.rglob("*"):
        if f.is_file() and f.suffix in {".py", ".html", ".css", ".js"}:
            rel = f.relative_to(code_root)
            if "node_modules" in str(rel) or ".venv" in str(rel):
                continue
            code_files.append(str(rel))
    code_files = sorted(code_files)[:80]

    # 결정 텍스트 압축
    dec_list = []
    for i, d in enumerate(decs):
        dec_list.append(f"[{i}] {d.get('page_title', '')} · {d.get('date', '')} · {d.get('decision', '')[:140]}")

    prompt = f"""wj-dashboard 프로젝트. thinking 시스템의 결정 {len(decs)} 개가 있고, 그 결정들이 실제 wj-dashboard 코드/기능으로 얼마나 구현됐는지 분류해야 함.

# 현 wj-dashboard 코드 파일 목록 (일부, 참고용)
{chr(10).join(code_files)}

# 결정 목록
{chr(10).join(dec_list)}

# 출력 (JSON 만, 다른 텍스트 없음)
{{
  "decisions": [
    {{"idx": 0, "status": "shipped|in_progress|planned", "evidence": "어떤 파일/기능에 반영됐는지 1줄"}},
    ...
  ]
}}

분류 기준:
- shipped: 실제 wj-dashboard 코드에 반영됐음 (파일/엔드포인트/UI 로 동작 중)
- in_progress: 일부만 됐거나 mockup 단계
- planned: 결정만 했고 구현 아직

JSON 만 출력. 코드블록 wrap X."""

    try:
        result = _sp_t.run(
            [CLAUDE_BIN_T, "--print", "--model", "claude-opus-4-7"],
            input=prompt,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"claude exit {result.returncode}: {result.stderr[:300]}"}
        raw = result.stdout.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = _json_c.loads(raw)
        # merge 결정 원본
        merged = []
        for sug in parsed.get("decisions", []):
            i = sug.get("idx")
            if i is not None and 0 <= i < len(decs):
                d = dict(decs[i])
                d["suggested_status"] = sug.get("status", "planned")
                d["suggested_evidence"] = sug.get("evidence", "")
                merged.append(d)
        return {"ok": True, "decisions": merged}
    except _json_c.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON parse fail: {e}", "raw": raw[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def apply_impl_label_to_file(page_path: str, decision_raw: str, status: str) -> dict:
    """thinking md 파일의 결정 줄을 찾아서 `| 구현: <status>` 추가/갱신.
    decision_raw 는 build_index 의 d['raw'] 와 매칭."""
    LABEL_MAP = {"shipped": "완료", "in_progress": "진행", "planned": "계획"}
    label_ko = LABEL_MAP.get(status, status)
    # path traversal 방어 — update_decision_impl 과 동일 패턴 (resolve + relative_to + .md 제한)
    full = (THINKING_ROOT / page_path).resolve()
    try:
        full.relative_to(THINKING_ROOT.resolve())
    except ValueError:
        return {"ok": False, "error": "path traversal"}
    if not full.exists() or full.suffix != ".md":
        return {"ok": False, "error": "file not found"}
    text = full.read_text(encoding="utf-8")
    # decision_raw 가 line 의 일부 (- prefix 제외) 이므로 검색
    needle = "- " + decision_raw
    if needle not in text:
        return {"ok": False, "error": "decision line not found"}
    # 기존 | 구현: X 가 있으면 교체, 없으면 끝에 append
    line_idx = text.find(needle)
    line_end = text.find("\n", line_idx)
    if line_end == -1:
        line_end = len(text)
    line = text[line_idx:line_end]
    # 기존 구현 메타 제거
    new_line = re.sub(r"\s*\|\s*구현:\s*[^|]+", "", line).rstrip()
    new_line += f" | 구현: {label_ko}"
    text = text[:line_idx] + new_line + text[line_end:]
    full.write_text(text, encoding="utf-8")
    return {"ok": True, "path": page_path, "status": status}
