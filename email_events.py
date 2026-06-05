"""규칙 기반 일정 근거 문장 추출 (S4a). LLM 전 1차 패스.

본문에서 날짜/시간 표현이 든 문장만 뽑아 LLM 에 넘긴다(LLM 비용/오탐 축소).
LLM 이 실패해도 이 결과만으로 최소 후보를 만들 수 있다. 절대 raise 안 함.
"""
from __future__ import annotations

import re

# 날짜/시간 신호. email_score 와 겹치지만 여기선 '문장 추출'이 목적이라 따로 둔다.
_DATE_RE = re.compile(
    r"(\d{1,2}\s*[/월]\s*\d{1,2}일?"          # 6/9, 6월 9일
    r"|\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}"  # 2026-06-09
    r"|\d{1,2}\s*:\s*\d{2}"                    # 14:00
    r"|오[전후]\s*\d{1,2}\s*시?"               # 오후 2시
    r"|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"       # 2시 30분
    r"|(?:다음|이번|차)\s*주"                   # 다음 주
    r"|(?:내일|모레|오늘|금일)"
    r"|월요일|화요일|수요일|목요일|금요일|토요일|일요일"
    r"|마감|deadline|due\b|by\s+\w+day"
    r"|\d{1,2}\s*(?:am|pm)"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2})",
    re.I,
)

_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|[\n\r]+")


def extract_date_sentences(text: str, max_sentences: int = 6) -> list[dict]:
    """본문에서 날짜/시간 표현이 든 문장 목록. [{source, offset}]. 실패 시 []."""
    try:
        if not text:
            return []
        out: list[dict] = []
        seen: set[str] = set()
        pos = 0
        # 문장 단위로 자르되, 원문 내 대략적 offset 도 같이 보관(하이라이트용).
        for raw in _SPLIT_RE.split(text):
            sent = (raw or "").strip()
            start = text.find(sent, pos) if sent else -1
            if sent:
                pos = max(pos, start + len(sent)) if start >= 0 else pos
            if not sent or len(sent) < 3:
                continue
            if not _DATE_RE.search(sent):
                continue
            key = sent[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append({"source": sent[:240], "offset": start if start >= 0 else -1})
            if len(out) >= max_sentences:
                break
        return out
    except Exception:
        return []


def has_date_signal(text: str) -> bool:
    try:
        return bool(_DATE_RE.search(text or ""))
    except Exception:
        return False
