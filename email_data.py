"""이메일 탭 임시 fixtures (실 Gmail 연동 전 목업 데이터).

원칙:
- 실제 Gmail/Calendar API 연동(gmail.py)이 붙으면 이 모듈을 그게 대체/보강한다.
- 지금은 M4(포커스 단일메일) 화면을 wj앱 탭으로 즉시 띄우기 위한 정적 데이터.
- 날짜/시간은 표시용 문자열(KST 가정). 오늘=2026-06-05 기준 더미.

build_view() 하나만 app.py 가 호출한다.
"""
from __future__ import annotations

# 좌측 큐 - 오늘 처리할 메일 줄세움
QUEUE = [
    {"id": "m_emnlp", "sender": "EMNLP 2026 PCs", "subject": "Camera-ready submission due (D-1 reminder)",
     "time": "방금", "priority": "p0", "reasons": ["오늘 마감", "일정 포함"], "has_event": True, "has_draft": False, "current": False},
    {"id": "m_minjun", "sender": "김민준 (석사과정)", "subject": "[재문의] 면담 가능 시간 회신 부탁드립니다",
     "time": "1시간 전", "priority": "p0", "reasons": ["답장 필요", "3일째 미회신"], "has_event": True, "has_draft": True, "current": True},
    {"id": "m_office", "sender": "산업공학과 행정실", "subject": "[공지] 6월 정기 교수회의 안내",
     "time": "2시간 전", "priority": "p1", "reasons": ["일정 포함"], "has_event": True, "has_draft": False, "current": False},
    {"id": "m_neurips", "sender": "NeurIPS 2026", "subject": "Invitation to review for NeurIPS 2026",
     "time": "4시간 전", "priority": "p1", "reasons": ["답장 필요", "마감 6/18"], "has_event": True, "has_draft": False, "current": False},
    {"id": "m_park", "sender": "박서준 교수", "subject": "공동연구 미팅 후속 자료 요청",
     "time": "어제", "priority": "p1", "reasons": ["답장 필요", "5일째 미회신"], "has_event": False, "has_draft": True, "current": False},
    {"id": "m_seoyeon", "sender": "이서연 (학부 인턴)", "subject": "코드 관련 질문 드립니다 (데이터 로더)",
     "time": "어제", "priority": "p2", "reasons": ["답장 필요"], "has_event": False, "has_draft": True, "current": False},
    {"id": "m_github", "sender": "GitHub", "subject": "[damilab/erp] PR #142 review requested",
     "time": "2일 전", "priority": "p2", "reasons": ["dev"], "has_event": False, "has_draft": False, "current": False},
]

# 중앙 - 현재 포커스 메일 (김민준)
FOCUS = {
    "id": "m_minjun",
    "sender": "김민준 (석사과정)",
    "sender_email": "minjun@damilab.kr",
    "subject": "[재문의] 면담 가능 시간 회신 부탁드립니다",
    "time": "오늘 09:12",
    "priority": "p0",
    "reasons": ["답장 필요", "3일째 미회신"],
    # 본문: 일정 감지 근거 문장을 <mark> 로 하이라이트
    "body_html": (
        "<p>교수님 안녕하세요, 석사과정 김민준입니다.</p>"
        "<p>지난주에 말씀드린 학위논문 주제 관련해 면담을 한 번 더 청하고 싶어 메일 드립니다. "
        "먼저 보내드린 메일에 회신을 못 받아 다시 정리해 여쭙습니다.</p>"
        "<p>교수님 일정 괜찮으시면 <mark>다음 주 월요일(6월 9일) 오후 2시</mark>에 잠시 뵐 수 있을까요? "
        "어려우시면 가능하신 시간을 알려주시면 맞추겠습니다.</p>"
        "<p>바쁘신 와중에 번거롭게 해 죄송합니다. 답신 기다리겠습니다.</p>"
        "<p>감사합니다.<br>김민준 드림</p>"
    ),
    "summary": {"event": "일정 후보 1 · 승인 대기", "draft": "미발송 초안 1", "followup": "팔로업 3일째"},
}

# 우측 - AI 답장 초안 (미발송)
DRAFT = {
    "status": "unsent",      # unsent | sent | none
    "to": "김민준 (minjun@damilab.kr)",
    "tone": "정중·간결",
    "tones": ["정중·간결", "따뜻하게", "간단 수락"],
    "text": (
        "안녕하세요 민준님,\n\n"
        "회신이 늦어 미안합니다. 6월 9일 월요일 오후 2시 면담 가능합니다. "
        "그 시간에 연구실로 와 주세요. 논의할 주제를 미리 두세 줄로 정리해 오면 더 효율적일 것 같습니다.\n\n"
        "그럼 월요일에 봅시다.\n이우진 드림"
    ),
}

# 우측 - 일정 후보 (상태 3종 예시 포함)
CANDIDATES = [
    {"title": "김민준 면담", "date": "6/9(월)", "time": "14:00", "place": "연구실",
     "source": "다음 주 월요일(6월 9일) 오후 2시", "status": "pending"},
    {"title": "DAMI 세미나", "date": "6/6(금)", "time": "15:00", "place": "",
     "source": "이번 주 금요일 오후 3시", "status": "done"},
    {"title": "ACL 뉴스레터 행사", "date": "6/20(토)", "time": "종일", "place": "",
     "source": "newsletter 자동 추출", "status": "ignored"},
]

# 임시보관함(작성중 초안)
DRAFTS_BOX = [
    {"to": "박서준 교수", "subject": "후속 자료 회신 (작성중)"},
    {"to": "이서연", "subject": "데이터 로더 관련 (작성중)"},
]

# 라벨
LABELS = [
    {"name": "학회", "color": "var(--p0)", "count": 4},
    {"name": "행정", "color": "var(--p1)", "count": 2},
    {"name": "연구실", "color": "var(--dash)", "count": 5},
    {"name": "dev", "color": "var(--navy)", "count": 1},
    {"name": "개인", "color": "var(--think)", "count": 3},
]

PROGRESS = {"idx": 2, "total": 12}      # 오늘 12통 중 2번째
STATS = {"unread": 9, "pending_events": 1, "pending_drafts": 1, "followups": 2}


def build_view() -> dict:
    """app.py 가 그대로 템플릿에 풀어 넣을 dict."""
    return {
        "queue": QUEUE,
        "focus": FOCUS,
        "draft": DRAFT,
        "candidates": CANDIDATES,
        "drafts_box": DRAFTS_BOX,
        "labels": LABELS,
        "progress": PROGRESS,
        "estats": STATS,
        "is_mock": True,        # 실 Gmail 연동 전이라는 표시
    }
