"""구글 캘린더 .ics 읽기 (읽기 전용 오버레이용).

원칙:
- 어떤 경우에도 raise 하지 않는다. 실패하면 빈 결과를 돌려 대시보드가 그대로 동작하게 한다.
- 비밀 .ics URL 은 env(WJ_GCAL_ICS_URL) 또는 파일(~/.config/wj-dashboard/gcal_ics)에서만 읽는다. 코드/깃에 박지 않는다.
- raw 피드는 10분 캐시. 시각은 Asia/Seoul(KST)로 변환해 날짜별로 버킷.
"""
from __future__ import annotations

import os
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
_ICS_FILE = Path.home() / ".config" / "wj-dashboard" / "gcal_ics"
_CACHE_TTL = 600  # 초
_cache: dict = {"ts": 0.0, "raw": None, "url": None, "cal": None}


def _read_url() -> str | None:
    u = (os.environ.get("WJ_GCAL_ICS_URL") or "").strip()
    if u:
        return u
    try:
        return (_ICS_FILE.read_text(encoding="utf-8").strip() or None)
    except OSError:
        return None


def _fetch_raw() -> bytes | None:
    url = _read_url()
    if not url:
        return None
    now = time.time()
    if (_cache["raw"] is not None and _cache["url"] == url
            and (now - _cache["ts"]) < _CACHE_TTL):
        return _cache["raw"]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wj-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        _cache.update(ts=now, raw=raw, url=url, cal=None)  # 새 raw → 파싱 캐시 무효화
        return raw
    except Exception:
        return _cache["raw"]  # 실패하면 마지막 캐시라도 (없으면 None)


def _get_calendar():
    """파싱된 icalendar.Calendar 를 캐시 (437KB 재파싱 방지)."""
    raw = _fetch_raw()
    if not raw:
        return None
    if _cache.get("cal") is not None:
        return _cache["cal"]
    try:
        import icalendar
        _cache["cal"] = icalendar.Calendar.from_ical(raw)
        return _cache["cal"]
    except Exception:
        return None


def _to_kst_parts(dt):
    """dt(date|datetime) → (iso_date, 'HH:MM' | None)."""
    if isinstance(dt, datetime):
        local = dt.astimezone(KST) if dt.tzinfo is not None else dt.replace(tzinfo=KST)
        return local.date().isoformat(), local.strftime("%H:%M")
    return dt.isoformat(), None  # all-day


def _expanded(start: date, end: date) -> list[dict]:
    cal = _get_calendar()
    if cal is None:
        return []
    try:
        import recurring_ical_events
        comps = recurring_ical_events.of(cal).between(start, end)
    except Exception:
        return []
    out = []
    seen = set()
    for c in comps:
        try:
            d = c.get("DTSTART")
            if d is None:
                continue
            iso, hhmm = _to_kst_parts(d.dt)
            title = str(c.get("SUMMARY") or "(제목 없음)").strip()
            key = (iso, hhmm, title)
            if key in seen:  # 동일 일정 중복 제거 (캘린더 병합/수정본)
                continue
            seen.add(key)
            out.append({"iso": iso, "time": hhmm, "title": title})
        except Exception:
            continue
    return out


def _sort_key(e: dict):
    return (e["iso"], e["time"] is None, e["time"] or "")


def events_by_day(today: date, view_year: int | None, view_month: int | None) -> dict:
    """표시 중인 달(±7일) 일정을 {iso_date: [events]} 로."""
    if view_year is None or view_month is None:
        view_year, view_month = today.year, today.month
    first = date(view_year, view_month, 1)
    start = first - timedelta(days=7)
    nxt = date(view_year + 1, 1, 1) if view_month == 12 else date(view_year, view_month + 1, 1)
    end = nxt + timedelta(days=7)
    by: dict[str, list] = {}
    for ev in _expanded(start, end):
        by.setdefault(ev["iso"], []).append(ev)
    for iso in by:
        by[iso].sort(key=_sort_key)
    return by


def agenda(today: date, days: int = 45, limit: int = 25) -> list[dict]:
    """오늘 이후 다가오는 일정 (일정 모드 사이드 리스트용)."""
    today_iso = today.isoformat()
    evs = [e for e in _expanded(today, today + timedelta(days=days)) if e["iso"] >= today_iso]
    evs.sort(key=_sort_key)
    return evs[:limit]
