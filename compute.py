"""dashboard.yaml → 뷰 데이터 변환 + 자연어 파싱 + atomic write.

app.py 는 라우팅·렌더만, 비즈니스 로직은 모두 여기.
"""
from __future__ import annotations

import calendar as _cal
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from settings import SETTINGS

DATA_PATH = SETTINGS.data_path
BACKUP_PATH = DATA_PATH.with_suffix(".yaml.bak")
CLAUDE_BIN = SETTINGS.claude_bin


# ---------- helpers ----------

def parse_date(s) -> date | None:
    if s is None:
        return None
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        return None


def urgency(due: date | None, today: date) -> str:
    if due is None:
        return "none"
    diff = (due - today).days
    if diff < 0:
        return "overdue"
    if diff <= 1:
        return "urgent"
    if diff <= 7:
        return "soon"
    return "ok"


def d_minus(due: date | None, today: date) -> int | None:
    if due is None:
        return None
    return (due - today).days


# ---------- load ----------

def load_yaml() -> dict[str, Any]:
    with open(DATA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(data: dict) -> None:
    """Atomic write — backup + temp + rename. 부분 쓰기 방지."""
    # backup
    if DATA_PATH.exists():
        BACKUP_PATH.write_bytes(DATA_PATH.read_bytes())
    # write temp + atomic rename
    fd, tmp = tempfile.mkstemp(prefix=".dashboard.", suffix=".yaml.tmp", dir=str(DATA_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        os.replace(tmp, DATA_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------- 자연어 파싱 ----------

DOW_KR = "월화수목금토일"


def parse_natural_text(text: str, today: date) -> dict:
    """텍스트에서 마감일 추출 + 정제된 제목 반환.

    예:
      "박진곤 이메일 다음주 월요일" → {title:"박진곤 이메일", due_at: 다음주 월}
      "5월 25일까지 보고서" → {title:"보고서", due_at: 5/25}
      "5/22 회의 준비" → {title:"회의 준비", due_at: 5/22}
      "내일 도서관" → {title:"도서관", due_at: 내일}
      "그냥 무언가" → {title:"그냥 무언가", due_at: None}
    """
    text = text.strip()
    due_at: date | None = None
    matched_pattern: str | None = None

    # 1) 오늘/내일/모레/어제
    if re.search(r"\b오늘\b", text):
        due_at = today
        matched_pattern = r"\b오늘\b"
    elif re.search(r"\b내일\b", text):
        due_at = today + timedelta(days=1)
        matched_pattern = r"\b내일\b"
    elif re.search(r"\b모레\b", text):
        due_at = today + timedelta(days=2)
        matched_pattern = r"\b모레\b"

    # 2) 다음주/이번주 X요일
    if due_at is None:
        m = re.search(r"(다음|이번|다다음)\s*주\s*([월화수목금토일])\s*요일?", text)
        if m:
            scope = m.group(1)
            dow_idx = DOW_KR.index(m.group(2))
            # today.weekday(): 월=0
            offset = (dow_idx - today.weekday()) % 7
            if scope == "이번":
                if offset == 0:
                    offset = 7  # 이번주 동일 요일이면 다음 주 같은 요일로 간주 (지나간 거 아니라면)
            elif scope == "다음":
                offset += 7 if offset <= today.weekday() else 7
                # 단순화: 다음주 = 이번 주 토요일 다음 월~일
                # 정확히: 다음주는 today.weekday() 기준 다음 주 월요일~일요일 사이
                days_to_next_mon = 7 - today.weekday()
                offset = days_to_next_mon + dow_idx
            elif scope == "다다음":
                days_to_next_mon = 7 - today.weekday()
                offset = days_to_next_mon + 7 + dow_idx
            due_at = today + timedelta(days=offset)
            matched_pattern = m.group(0)

    # 3) X월 Y일
    if due_at is None:
        m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            year = today.year
            if month < today.month:
                year += 1
            try:
                due_at = date(year, month, day)
                matched_pattern = m.group(0)
            except ValueError:
                pass

    # 4) M/D 형식
    if due_at is None:
        m = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", text)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            year = today.year
            if month < today.month:
                year += 1
            try:
                due_at = date(year, month, day)
                matched_pattern = m.group(0)
            except ValueError:
                pass

    # 5) X일 후
    if due_at is None:
        m = re.search(r"(\d{1,2})\s*일\s*후", text)
        if m:
            due_at = today + timedelta(days=int(m.group(1)))
            matched_pattern = m.group(0)

    # 제목 정제: 매칭된 패턴 + 조사 제거
    title = text
    if matched_pattern:
        title = re.sub(matched_pattern, "", title)
    title = re.sub(r"\s*까지\s*", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    # 끝의 조사 cleanup
    title = re.sub(r"^[,\s]+|[,\s]+$", "", title)

    if not title:
        title = "(제목 없음)"

    return {"title": title, "due_at": due_at}


def match_project(text: str, projects: list[dict]) -> str | None:
    """텍스트에서 프로젝트 매칭. title/aliases 의 핵심 키워드 sub-string."""
    text_lower = text.lower()
    best = None
    best_score = 0
    for p in projects:
        if p.get("status") != "active":
            continue
        candidates = []
        title = p.get("title", "")
        candidates.append(title)
        candidates.extend(p.get("aliases", []) or [])
        for cand in candidates:
            # 한국어는 단어 분리가 어렵고, 키워드(2~6글자)들로 분리
            tokens = re.findall(r"[가-힣A-Za-z0-9-]+", cand)
            for tok in tokens:
                if len(tok) < 2:
                    continue
                if tok.lower() in text_lower:
                    score = len(tok)
                    if score > best_score:
                        best_score = score
                        best = p["id"]
    return best


def next_task_id(today: date, existing_tasks: list[dict]) -> str:
    base = f"tsk_{today.strftime('%Y%m%d')}_"
    used = []
    for t in existing_tasks:
        tid = str(t.get("id") or "")
        if tid.startswith(base):
            suffix = tid[len(base):]
            if suffix.isdigit():
                used.append(int(suffix))
    n = (max(used) + 1) if used else 1
    return f"{base}{n:03d}"


# ---------- 액션 (yaml 수정) ----------

import json as _json
import subprocess as _sp


# ---------- Claude Opus 챗봇 ----------

def _build_system_prompt(today: date, projects: list[dict]) -> str:
    active = [p for p in projects if p.get("status") == "active"]
    proj_lines = "\n".join(f'  - id="{p["id"]}", title="{p["title"]}"' for p in active)
    dow = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    return f"""당신은 사용자의 dashboard 일정 관리 봇이다.
사용자가 자연어로 task 등록을 요청하면 의도를 파악해 JSON 으로만 답한다.

오늘: {today.isoformat()} ({dow}요일)

활성 프로젝트:
{proj_lines}

응답은 반드시 다음 JSON 형식 (그 외 텍스트 절대 X, 코드블록 X):
{{
  "reply": "사용자에게 보일 짧은 친근한 응답 (1-2문장)",
  "task": {{
    "title": "정리된 task 제목 (군더더기 제거)",
    "due_at": "YYYY-MM-DD 또는 null",
    "project_id": "prj_xxx 또는 null",
    "note": "원본 입력의 부가 정보 (예: '아침 8시' 같은 시간 메모)"
  }} 또는 null,
  "needs_clarification": true/false,
  "questions": ["사용자에게 추가로 물어볼 질문"]
}}

규칙:
- 의도가 명확하면 task 채움, needs_clarification=false. reply 예: "5/22 (목) 마감으로 '병원 예약 (오전 10시)' 를 Inbox 에 등록할게요."
- 의도가 모호하면 task=null, needs_clarification=true, questions 1~2개. reply 에 무엇이 모호한지 짚음.
- 프로젝트는 사용자 입력에 키워드 있을 때만 매칭. 없으면 null (Inbox).
- 날짜: "오늘", "내일", "다음주 X요일", "X월 Y일", "M/D" 등 자유. 없으면 null.
- title 은 동사·조사·"적어줘"같은 명령어 제거하고 깔끔하게.
- 사용자가 이미 등록된 task 를 수정 요청하면 (예: "그 마감 5/25 로") needs_clarification=true 로 어떤 task 인지 명확화.
"""


def claude_chat(messages: list[dict], today: date, projects: list[dict], timeout: int = 30) -> dict:
    """messages = [{role: 'user'|'assistant', content: str}, ...]
    Claude Opus 호출 후 JSON 파싱해 dict 반환.
    실패 시 fallback 으로 regex 파서 사용.
    """
    system_prompt = _build_system_prompt(today, projects)
    # 대화를 텍스트로 직렬화
    convo = ""
    for m in messages:
        role = "사용자" if m["role"] == "user" else "Bot"
        convo += f"[{role}]: {m['content']}\n\n"
    convo += "[Bot]:"

    try:
        result = _sp.run(
            [CLAUDE_BIN, "--print", "--model", "claude-opus-4-7",
             "--system-prompt", system_prompt],
            input=convo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"claude exit {result.returncode}: {result.stderr[:300]}"}
        raw = result.stdout.strip()
        # JSON 추출 (코드블록 wrap 가능성 처리)
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError as e:
            return {"ok": False, "error": f"JSON parse fail: {e}", "raw": raw[:500]}
        parsed["ok"] = True
        return parsed
    except _sp.TimeoutExpired:
        return {"ok": False, "error": "claude timeout (30s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def preview_task_from_text(text: str, today: date) -> dict:
    """자연어 → 파싱 미리보기. 저장 X. 챗봇 컨펌용."""
    data = load_yaml()
    projects = data.get("projects") or []
    parsed = parse_natural_text(text, today)
    proj_id = match_project(parsed["title"], projects)
    proj_name = next((p["title"] for p in projects if p["id"] == proj_id), "Inbox") if proj_id else "Inbox"
    return {
        "ok": True,
        "original": text,
        "title": parsed["title"],
        "due_at": parsed["due_at"].isoformat() if parsed["due_at"] else None,
        "project_id": proj_id,
        "project_name": proj_name,
        "projects_available": [{"id": p["id"], "title": p["title"]} for p in projects if p.get("status") == "active"],
    }


def commit_task(title: str, due_at: str | None, project_id: str | None, note: str | None = None) -> dict:
    """미리보기 컨펌 후 실제 저장."""
    data = load_yaml()
    tasks = data.get("tasks") or []
    today = date.today()
    new_id = next_task_id(today, tasks)
    now_iso = datetime.now().astimezone().replace(microsecond=0).isoformat()
    new_task = {
        "id": new_id,
        "project_id": project_id,
        "parent_task_id": None,
        "title": title.strip() or "(제목 없음)",
        "status": "todo",
        "due_at": due_at,
        "done_at": None,
        "note": note or "",
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    tasks.append(new_task)
    data["tasks"] = tasks
    save_yaml(data)
    projects = data.get("projects") or []
    proj_name = next((p["title"] for p in projects if p["id"] == project_id), "Inbox") if project_id else "Inbox"
    return {"ok": True, "id": new_id, "title": new_task["title"], "due_at": due_at, "project_name": proj_name}


def create_task_from_text(text: str, today: date) -> dict:
    """자연어 → task 생성 + dashboard.yaml 갱신. 결과 리포트."""
    data = load_yaml()
    projects = data.get("projects") or []
    tasks = data.get("tasks") or []

    parsed = parse_natural_text(text, today)
    proj_id = match_project(parsed["title"], projects)

    new_id = next_task_id(today, tasks)
    now_iso = datetime.now().astimezone().replace(microsecond=0).isoformat()

    new_task = {
        "id": new_id,
        "project_id": proj_id,
        "parent_task_id": None,
        "title": parsed["title"],
        "status": "todo",
        "due_at": parsed["due_at"].isoformat() if parsed["due_at"] else None,
        "done_at": None,
        "note": f"자연어 입력: \"{text}\"",
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    tasks.append(new_task)
    data["tasks"] = tasks
    save_yaml(data)

    proj_name = next((p["title"] for p in projects if p["id"] == proj_id), "Inbox") if proj_id else "Inbox"
    return {
        "ok": True,
        "id": new_id,
        "title": parsed["title"],
        "due_at": parsed["due_at"].isoformat() if parsed["due_at"] else None,
        "project_id": proj_id,
        "project_name": proj_name,
    }


def update_task(task_id: str, updates: dict) -> dict:
    """task 의 필드 갱신. 허용 필드만 통과."""
    allowed = {"status", "due_at", "done_at", "title", "note", "project_id"}
    data = load_yaml()
    tasks = data.get("tasks") or []
    found = None
    for t in tasks:
        if t.get("id") == task_id:
            found = t
            break
    if not found:
        return {"ok": False, "error": "task not found"}

    for k, v in updates.items():
        if k in allowed:
            found[k] = v
    found["updated_at"] = datetime.now().astimezone().replace(microsecond=0).isoformat()
    data["tasks"] = tasks
    save_yaml(data)
    return {"ok": True, "task": found}


def mark_done(task_id: str, today: date) -> dict:
    """task 완료 표시."""
    return update_task(task_id, {
        "status": "done",
        "done_at": today.isoformat(),
    })


def snooze(task_id: str, days: int) -> dict:
    """due_at 을 N일 미룸."""
    data = load_yaml()
    tasks = data.get("tasks") or []
    found = next((t for t in tasks if t.get("id") == task_id), None)
    if not found:
        return {"ok": False, "error": "task not found"}
    base = parse_date(found.get("due_at")) or date.today()
    new_due = base + timedelta(days=days)
    return update_task(task_id, {"due_at": new_due.isoformat()})


# ---------- core view ----------

def build_view(data: dict, today: date, view_year: int | None = None, view_month: int | None = None) -> dict:
    if view_year is None or view_month is None:
        view_year, view_month = today.year, today.month
    projects_raw = data.get("projects") or []
    tasks_raw = data.get("tasks") or []

    # enrich tasks
    tasks = []
    for t in tasks_raw:
        t = dict(t)
        t["due_date"] = parse_date(t.get("due_at"))
        t["done_date"] = parse_date(t.get("done_at"))
        t["d_minus"] = d_minus(t["due_date"], today)
        t["urgency"] = urgency(t["due_date"], today)
        tasks.append(t)

    # projects with rolling
    projects = []
    by_pid = {}
    for p in projects_raw:
        p = dict(p)
        p["due_date"] = parse_date(p.get("due_at"))
        p["d_minus"] = d_minus(p["due_date"], today)
        p["urgency"] = urgency(p["due_date"], today)
        p["tasks"] = []
        p["todo_count"] = 0
        p["done_count"] = 0
        by_pid[p["id"]] = p
        projects.append(p)

    inbox_tasks = []
    for t in tasks:
        pid = t.get("project_id")
        if pid and pid in by_pid:
            by_pid[pid]["tasks"].append(t)
            if t.get("status") == "done":
                by_pid[pid]["done_count"] += 1
            else:
                by_pid[pid]["todo_count"] += 1
        else:
            inbox_tasks.append(t)

    # Inbox 정렬: overdue (D+N) 먼저 → 오늘 → 가까운 미래 → 마감 없음 (맨 뒤)
    def _inbox_key(t):
        d = t.get("d_minus")
        if d is None:
            return (1, 0)
        return (0, d)
    inbox_tasks.sort(key=_inbox_key)

    # project rollups
    for p in projects:
        total = len(p["tasks"])
        p["progress_pct"] = int(p["done_count"] / total * 100) if total else 0
        # 가장 가까운 마감 (미완 + due 있는 task 중)
        upcoming = [t for t in p["tasks"] if t["due_date"] and t.get("status") != "done"]
        if upcoming:
            n = min(upcoming, key=lambda t: t["due_date"])
            p["nearest_due"] = n["due_date"]
            p["nearest_d_minus"] = n["d_minus"]
            p["nearest_urgency"] = n["urgency"]
        else:
            p["nearest_due"] = p["due_date"]
            p["nearest_d_minus"] = p["d_minus"]
            p["nearest_urgency"] = p["urgency"]

    # day → tasks 맵
    by_day: dict[date, list] = {}
    for t in tasks:
        if t["due_date"]:
            by_day.setdefault(t["due_date"], []).append(t)

    # 오늘 task
    today_tasks = by_day.get(today, [])
    urgent_today = next((t for t in today_tasks if t.get("status") != "done"), None)

    # 프로젝트 색 매핑
    project_colors = assign_project_colors(projects)
    # 프로젝트에 색 박기
    for p in projects:
        p["color"] = project_colors.get(p["id"], INBOX_COLOR)
    # task 에도 색 박기
    for t in tasks:
        t["color"] = _task_color(t, project_colors)

    # 선택된 달 캘린더 grid
    cal_grid = build_month_grid(view_year, view_month, by_day, today)

    # 이전/다음 달
    if view_month == 1:
        prev_y, prev_m = view_year - 1, 12
    else:
        prev_y, prev_m = view_year, view_month - 1
    if view_month == 12:
        next_y, next_m = view_year + 1, 1
    else:
        next_y, next_m = view_year, view_month + 1

    # 언젠가 그룹
    someday_groups = build_someday_groups(tasks, by_pid)
    someday_count = sum(g["count"] for g in someday_groups)

    # 통계
    stats = {
        "today_count": sum(1 for t in today_tasks if t.get("status") != "done"),
        "overdue_count": sum(
            1 for t in tasks
            if t["due_date"] and t["due_date"] < today and t.get("status") != "done"
        ),
        "month_count": sum(
            1 for t in tasks
            if t["due_date"]
            and t["due_date"].year == today.year
            and t["due_date"].month == today.month
            and t.get("status") != "done"
        ),
        "project_count": sum(1 for p in projects if p.get("status") == "active"),
        "inbox_count": sum(1 for t in inbox_tasks if t.get("status") != "done"),
        "someday_count": someday_count,
    }

    # active projects only
    active_projects = [p for p in projects if p.get("status") == "active"]

    # 모든 task by_day (JS 모달용) — JSON 직렬화 가능 형태
    by_day_json = {}
    for d, ts in by_day.items():
        by_day_json[d.isoformat()] = [
            {
                "id": t.get("id"),
                "title": t.get("title", ""),
                "note": t.get("note", ""),
                "project": _project_name(t.get("project_id"), by_pid),
                "urgency": t["urgency"],
                "d_minus": t["d_minus"],
                "due_at": t["due_date"].isoformat() if t["due_date"] else None,
                "status": t.get("status", "todo"),
            }
            for t in ts
        ]

    # 프로젝트 별 task 들 (JSON 직렬화)
    projects_json = {}
    for p in active_projects:
        projects_json[p["id"]] = {
            "id": p["id"],
            "title": p["title"],
            "due_at": p["due_date"].isoformat() if p["due_date"] else None,
            "tags": p.get("tags") or [],
            "progress_pct": p["progress_pct"],
            "todo_count": p["todo_count"],
            "done_count": p["done_count"],
            "source_thinking": p.get("source_thinking"),
            "tasks": [
                {
                    "id": t.get("id"),
                    "title": t.get("title", ""),
                    "note": t.get("note", ""),
                    "status": t.get("status", "todo"),
                    "due_at": t["due_date"].isoformat() if t["due_date"] else None,
                    "done_at": t["done_date"].isoformat() if t["done_date"] else None,
                    "d_minus": t["d_minus"],
                    "urgency": t["urgency"],
                }
                for t in p["tasks"]
            ],
        }

    # Inbox tasks (JSON 직렬화)
    inbox_json = [
        {
            "id": t.get("id"),
            "title": t.get("title", ""),
            "note": t.get("note", ""),
            "status": t.get("status", "todo"),
            "due_at": t["due_date"].isoformat() if t["due_date"] else None,
            "done_at": t["done_date"].isoformat() if t["done_date"] else None,
            "d_minus": t["d_minus"],
            "urgency": t["urgency"],
        }
        for t in inbox_tasks
    ]

    return {
        "today": today,
        "today_iso": today.isoformat(),
        "today_label": f"{today.year}년 {today.month}월 {today.day}일",
        "today_dow": ["월", "화", "수", "목", "금", "토", "일"][today.weekday()],
        "projects": active_projects,
        "inbox_tasks": inbox_tasks,
        "urgent_today": urgent_today,
        "cal_grid": cal_grid,
        "cal_year": view_year,
        "cal_month": view_month,
        "cal_prev_ym": f"{prev_y}-{prev_m:02d}",
        "cal_next_ym": f"{next_y}-{next_m:02d}",
        "is_current_month": (view_year, view_month) == (today.year, today.month),
        "someday_groups": someday_groups,
        "stats": stats,
        "by_day_json": by_day_json,
        "projects_json": projects_json,
        "inbox_json": inbox_json,
    }


def _project_name(pid: str | None, by_pid: dict) -> str:
    if pid and pid in by_pid:
        return by_pid[pid]["title"]
    return "Inbox"


def _task_category(t: dict, by_pid: dict) -> str:
    pid = t.get("project_id")
    if pid and pid in by_pid:
        tags = by_pid[pid].get("tags") or []
        if tags:
            return tags[0]
    return "inbox"


# 프로젝트별 색 — 8색 팔레트 (부드러운 톤)
COLOR_PALETTE = [
    {"bg": "#dbeafe", "fg": "#1e3a8a", "border": "#93c5fd"},  # 파랑
    {"bg": "#fed7aa", "fg": "#9a3412", "border": "#fdba74"},  # 주황
    {"bg": "#e9d5ff", "fg": "#581c87", "border": "#c4b5fd"},  # 보라
    {"bg": "#bbf7d0", "fg": "#14532d", "border": "#86efac"},  # 초록
    {"bg": "#fce7f3", "fg": "#831843", "border": "#f9a8d4"},  # 핑크
    {"bg": "#fef3c7", "fg": "#92400e", "border": "#fcd34d"},  # 노랑
    {"bg": "#ccfbf1", "fg": "#115e59", "border": "#5eead4"},  # 청록
    {"bg": "#c7d2fe", "fg": "#312e81", "border": "#a5b4fc"},  # 인디고
]
INBOX_COLOR = {"bg": "#f1f5f9", "fg": "#475569", "border": "#cbd5e1"}


def assign_project_colors(projects: list[dict]) -> dict[str, dict]:
    """프로젝트 id → 색 매핑. yaml 순서 기반 (안정적)."""
    out = {}
    active = [p for p in projects if p.get("status") == "active"]
    for i, p in enumerate(active):
        out[p["id"]] = COLOR_PALETTE[i % len(COLOR_PALETTE)]
    return out


def _task_color(t: dict, project_colors: dict) -> dict:
    pid = t.get("project_id")
    if pid and pid in project_colors:
        return project_colors[pid]
    return INBOX_COLOR


def build_month_grid(year: int, month: int, by_day: dict, today: date) -> list[list[dict]]:
    """일요일 시작 6주 grid. 각 cell = {day, in_month, is_today, is_past, is_sun, is_sat, count, urgency}"""
    c = _cal.Calendar(firstweekday=6)  # Sunday first
    weeks = c.monthdatescalendar(year, month)
    grid = []
    for week in weeks:
        row = []
        for d in week:
            cell: dict[str, Any] = {"date_iso": d.isoformat()}
            if d.month == month:
                cell["in_month"] = True
                cell["day"] = d.day
                cell["is_today"] = d == today
                cell["is_past"] = d < today
                wd = d.weekday()
                cell["is_sun"] = wd == 6
                cell["is_sat"] = wd == 5
                tasks = by_day.get(d, [])
                cell["count"] = len(tasks)
                if tasks:
                    # 가장 임박한 urgency (셀 테두리·dot 용)
                    order = {"overdue": 0, "urgent": 1, "soon": 2, "ok": 3, "none": 4}
                    cell["urgency"] = min((t["urgency"] for t in tasks), key=lambda u: order.get(u, 4))
                    # 각 task: 색·제목 같이 (프로젝트별 색)
                    cell["tasks_view"] = [
                        {
                            "id": t.get("id", ""),
                            "title": t.get("title", ""),
                            "color": t.get("color", INBOX_COLOR),
                            "urgency": t.get("urgency", "none"),
                        }
                        for t in tasks
                    ]
                else:
                    cell["urgency"] = None
                    cell["tasks_view"] = []
            else:
                cell["in_month"] = False
            row.append(cell)
        grid.append(row)
    return grid


def build_someday_groups(tasks: list[dict], by_pid: dict) -> list[dict]:
    """마감 없는 task 들을 프로젝트별 그룹으로."""
    groups: dict[str, list] = {}
    for t in tasks:
        if t["due_date"] is None and t.get("status") != "done":
            pid = t.get("project_id")
            key = pid if pid else "_inbox"
            groups.setdefault(key, []).append(t)

    out = []
    for key, ts in groups.items():
        if key == "_inbox":
            name = "📥 Inbox"
            src = None
        else:
            p = by_pid.get(key)
            name = p["title"] if p else key
            src = p.get("source_thinking") if p else None
        out.append({
            "key": key,
            "name": name,
            "count": len(ts),
            "tasks": [{"title": t.get("title", ""), "note": t.get("note", "")} for t in ts],
            "src_thinking": src,
            "is_inbox": key == "_inbox",
        })
    # Inbox 그룹을 맨 뒤로 (프로젝트 먼저 보이게)
    out.sort(key=lambda g: (1 if g.get("is_inbox") else 0, -g["count"]))
    # 개수 많은 순
    out.sort(key=lambda g: -g["count"])
    return out
