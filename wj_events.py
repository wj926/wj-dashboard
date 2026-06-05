"""wj 앱 자체 캘린더 이벤트(로컬). 이메일 일정 후보를 '승인'하면 여기 저장된다.

Google Calendar 가 아니라 wj 업무 탭 캘린더(gcal 오버레이 레이어)에 합쳐 보여주기 위한
로컬 저장소다. 그래서 OAuth/추가 권한이 필요 없다.

원칙(gcal.py 계승):
- 절대 raise 하지 않는다. 실패 시 [] / {} / False.
- 저장은 repo 밖 ~/.config/wj-dashboard/wj_events.json (.gitignore).
- 이벤트 shape 는 gcal 과 맞춘다: {iso, time, title} (+ id, source, ref, origin).
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
STORE_PATH = Path(
    os.environ.get(
        "WJ_EVENTS_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "wj_events.json"),
    )
)


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


def add_event(iso: str, time_str: str | None, title: str,
              source: str = "", ref: str = "", origin: str = "email") -> str:
    """이벤트 추가. ref 가 같은 이벤트가 이미 있으면 그 id 반환(중복 등록 방지). 실패 시 ""."""
    if not iso or not title:
        return ""
    items = _load()
    if ref:
        for e in items:
            if e.get("ref") == ref:
                return e.get("id") or ""
    eid = f"wjev_{int(_time.time()*1000)}_{len(items)}"
    items.append({
        "id": eid,
        "iso": iso,
        "time": (time_str or None),
        "title": title[:120],
        "source": source[:240],
        "ref": ref,
        "origin": origin,
        "created_at": int(_time.time()),
    })
    return eid if _save(items) else ""


def remove_event(event_id: str) -> bool:
    items = _load()
    new = [e for e in items if e.get("id") != event_id]
    if len(new) == len(items):
        return False
    return _save(new)


def remove_by_ref(ref: str) -> bool:
    if not ref:
        return False
    items = _load()
    new = [e for e in items if e.get("ref") != ref]
    if len(new) == len(items):
        return True
    return _save(new)


def event_id_for_ref(ref: str) -> str:
    for e in _load():
        if e.get("ref") == ref:
            return e.get("id") or ""
    return ""


def _sort_key(e: dict):
    return (e.get("iso") or "", e.get("time") is None, e.get("time") or "")


def events_by_day(today: date, view_year: int | None, view_month: int | None) -> dict:
    """표시 달(±7일) 이벤트를 {iso: [{iso,time,title}]} 로. gcal.events_by_day 와 합치기 쉽게."""
    try:
        if view_year is None or view_month is None:
            view_year, view_month = today.year, today.month
        first = date(view_year, view_month, 1)
        start = (first - timedelta(days=7)).isoformat()
        nxt = date(view_year + 1, 1, 1) if view_month == 12 else date(view_year, view_month + 1, 1)
        end = (nxt + timedelta(days=7)).isoformat()
        by: dict[str, list] = {}
        for e in _load():
            iso = e.get("iso") or ""
            if start <= iso <= end:
                by.setdefault(iso, []).append({"iso": iso, "time": e.get("time"), "title": e.get("title")})
        for iso in by:
            by[iso].sort(key=_sort_key)
        return by
    except Exception:
        return {}


def agenda(today: date, days: int = 45, limit: int = 25) -> list:
    """오늘 이후 다가오는 wj 이벤트."""
    try:
        today_iso = today.isoformat()
        end_iso = (today + timedelta(days=days)).isoformat()
        evs = [
            {"iso": e.get("iso"), "time": e.get("time"), "title": e.get("title")}
            for e in _load()
            if e.get("iso") and today_iso <= e["iso"] <= end_iso
        ]
        evs.sort(key=_sort_key)
        return evs[:limit]
    except Exception:
        return []
