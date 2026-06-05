# wj 앱 이메일 탭 개발 가이드

## 1. 개요와 범위

이 문서는 wj Flask 개인 대시보드에 Gmail 관리 기능을 붙일 때 따를 개발 가이드다. 목표는 독립 사이트를 새로 만드는 것이 아니라 기존 wj 앱 안에 `메일` 탭과 `/email` 라우트를 추가하고, 이미 만든 M4 포커스 단일메일 화면(`templates/email_focus.html`)을 실제 Gmail 데이터로 교체해 가는 것이다.

확정된 UI 방향은 M4 포커스 단일메일 모드다. 좌측은 오늘 처리할 메일 큐, 중앙은 선택된 메일 본문과 일정 근거 문장 하이라이트, 우측은 `AI 초안`, `일정 후보`, `팔로업·처리` 탭으로 구성한다. 사용자는 개인 Gmail 1계정만 연결한다. UI 문구는 한국어이고, 화면은 데스크톱 전용으로 둔다.

기능 범위는 다음과 같다.

- Gmail 인박스 읽기, 검색, 라벨/별표/스누즈/아카이브 등 Gmail 기본 조작 일부.
- 메일에서 일정/마감 후보를 감지하고 사용자가 승인한 경우에만 Google Calendar에 등록.
- 메일별 `초안 생성` 버튼으로 LLM 답장 초안을 생성하고, 사용자가 승인한 경우에만 Gmail로 발송.
- 급한/중요 메일 부각, 내가 마지막으로 답장하지 않은 스레드 기반 팔로업 모음, 임시보관함 노출.

지금 하지 않는 범위는 다음과 같다.

- Tailscale, VPN, 추가 네트워크 보안 설계. 현재는 기존 wj 앱의 Basic 인증 뒤에 둔다.
- 다계정 Gmail, 조직/Workspace 관리, 서비스 계정 domain-wide delegation.
- Gmail push notification, Pub/Sub, 실시간 푸시.
- 메일 내용을 장기 저장하는 별도 DB 구축.
- 자동 발송, 자동 캘린더 등록. 두 액션 모두 반드시 승인 게이트를 둔다.

중요한 기존 앱 관례:

- `app.py`는 `@app.before_request`에서 Basic 인증, terminal/chat guard, CSRF/same-origin guard를 처리한다. 새 이메일 POST API도 이 방어 레이어 뒤에 자동으로 들어간다.
- 페이지 라우트는 `render_template(..., active_tab="...")`를 넘기고, 템플릿은 풀 HTML 문서로 작성하며 `_tabs.html`을 include한다.
- 스타일은 `static/dashboard.css`의 토큰을 기본으로 쓴다. 주요 토큰은 `--navy #1f3a5f`, `--bg #0f172a`, `--card #fff`, `--border`, `--muted`, `--chip`, `--p0`, `--p1`, `--p2`, `--dash`, `--think`이다.
- `gcal.py`는 읽기 전용 `.ics` 오버레이 모듈이며, 어떤 경우에도 raise하지 않고 실패 시 빈 결과를 돌려 대시보드가 계속 동작하게 한다. Gmail/Calendar 쓰기 모듈도 이 원칙을 계승한다.
- 비밀 값은 코드와 git에 넣지 않는다. 기존 `gcal.py`처럼 `~/.config/wj-dashboard/` 또는 환경변수에서만 읽는다.

## 2. 아키텍처 한 장

요청 흐름은 다음처럼 둔다.

```text
브라우저
  -> Flask app.py
     -> before_request: Basic 인증, 기능 guard, CSRF/same-origin
     -> GET /email
        -> gmail.py: 인증, Gmail 읽기, 큐/포커스/팔로업 계산
        -> email_data.py 호환 view dict 생성
        -> templates/email_focus.html 렌더
     -> POST /api/email/...
        -> gmail.py 또는 calendar_write.py 액션
        -> JSON 응답
        -> 프론트 fetch 후 부분 갱신 또는 reload
```

기존 `gcal.py`와의 관계:

- `gcal.py`는 현재 업무 탭 캘린더 오버레이용 읽기 전용 `.ics` 모듈로 유지한다.
- Gmail 메일에서 감지한 일정을 Calendar API로 등록하는 쓰기 기능은 새 모듈로 분리한다. 권장 이름은 `calendar_write.py` 또는 `google_calendar_write.py`다.
- 이유: `.ics` 읽기와 OAuth 기반 Calendar API 쓰기는 인증 방식, 실패 처리, scope, 부작용이 다르다. 한 파일에 합치면 읽기 전용 오버레이의 안전성이 흐려진다.
- 단, 두 모듈 모두 `KST = ZoneInfo("Asia/Seoul")`, `~/.config/wj-dashboard/` 비밀 저장, `절대 raise 안 함` 원칙은 공유한다.

모듈 책임:

- `app.py`: 라우트 등록, request body 검증, `jsonify`, `render_template(active_tab="email")`.
- `gmail.py`: Gmail OAuth 인증, Gmail API 호출, 메시지/스레드 파싱, 라벨/별표/스누즈/아카이브/초안/발송.
- `calendar_write.py`: OAuth 인증 공유 또는 별도 service 생성, `events.insert`, 등록/되돌리기.
- `email_compute.py` 또는 `email_view.py`: Gmail 원본을 `email_data.build_view()`와 같은 view dict로 매핑. 처음에는 `gmail.py` 안에 두어도 되지만, 계산이 커지면 분리한다.
- `llm_email.py`: 일정 감지, 답장 초안 생성, 비용 제한, 프롬프트/JSON 파싱.
- `email_data.py`: 실연동 전 목업 fixture. 실데이터가 안정화되면 테스트 fixture로 남긴다.

## 3. 백엔드 포인트

### Google Cloud OAuth 설정 절차

1. Google Cloud Console에서 새 프로젝트를 만든다. 이름 예: `wj-dashboard-personal`.
2. `APIs & Services > Library`에서 `Gmail API`와 `Google Calendar API`를 사용 설정한다.
3. `Google Auth Platform` 또는 `OAuth consent screen`에서 동의 화면을 만든다.
4. 개인 Gmail 1계정 기준이면 처음에는 `External` + `Testing`으로 둔다.
5. 테스트 사용자에 본인 Gmail 주소를 추가한다. 테스트 사용자에 없으면 OAuth 승인이 막힌다.
6. 앱 이름은 `wj dashboard`, 사용자 지원 이메일과 개발자 연락처는 본인 이메일로 둔다.
7. scope를 추가한다. 최소 scope 원칙을 지킨다.
8. `Credentials > Create credentials > OAuth client ID`에서 OAuth 클라이언트를 만든다.
9. 개발/개인 서버에서 브라우저 OAuth를 할 것이므로 우선 `Desktop app` 클라이언트를 권장한다. 로컬 callback 서버를 직접 쓰려면 `Web application`도 가능하지만 redirect URI 관리가 필요하다.
10. `client_secret.json`을 다운로드해 `~/.config/wj-dashboard/google_client_secret.json`에 둔다. repo 안에 두지 않는다.

권장 scope:

| 기능 | Scope | 등급 | 비고 |
| --- | --- | --- | --- |
| 메일 본문/스레드 읽기 | `https://www.googleapis.com/auth/gmail.readonly` | Restricted | 인박스 읽기, 본문 표시, 일정 감지에 필요 |
| Gmail 라벨/아카이브/스누즈 등 수정 | `https://www.googleapis.com/auth/gmail.modify` | Restricted | 라벨 적용, 읽음 처리, 아카이브, 스누즈 구현에 필요 |
| 임시보관함 생성/수정/발송 | `https://www.googleapis.com/auth/gmail.compose` | Restricted | draft 생성과 draft send에 필요 |
| 발송만 | `https://www.googleapis.com/auth/gmail.send` | Sensitive | `gmail.compose`를 쓰면 별도 필요 여부를 실제 호출 방식에 맞춰 재검토 |
| 라벨만 관리 | `https://www.googleapis.com/auth/gmail.labels` | Non-sensitive | 라벨 목록/생성/수정만 따로 필요할 때 |
| 캘린더 이벤트 쓰기 | `https://www.googleapis.com/auth/calendar.events` | Sensitive 계열로 취급 | `events.insert`에 충분. 전체 `calendar` scope보다 좁다 |

주의:

- Gmail API 공식 scope 표 기준으로 `gmail.readonly`, `gmail.modify`, `gmail.compose`는 Restricted다. 개인용 테스트라도 Cloud Console에서는 restricted scope 경고와 검증 요구가 보일 수 있다.
- `gmail.send`는 발송만 가능한 Sensitive scope다. 하지만 이 앱은 본문 읽기가 필수라 `gmail.readonly` 또는 `gmail.modify`가 필요하다.
- `gmail.modify`는 `gmail.readonly`보다 넓지만, 실제 Gmail 조작(라벨, 아카이브, 스누즈)을 하려면 필요하다. S2에서는 `gmail.readonly`만으로 시작하고 S5에서 `gmail.modify`를 추가하는 식으로 scope 확대 시점을 늦춘다.
- scope를 바꾸면 기존 `token.json`으로는 권한이 부족할 수 있다. 이때 token 삭제 후 재동의가 필요하다.

테스트 모드 refresh token 7일 만료 함정:

- OAuth 동의 화면이 `External`이고 publishing status가 `Testing`이면, profile 기본 scope만 쓰는 경우를 제외하고 refresh token이 7일 후 만료될 수 있다.
- 증상은 access token refresh 시 `invalid_grant`, `Token has been expired or revoked` 같은 에러가 나는 것이다.
- 개발 단계 대응은 `~/.config/wj-dashboard/google_token.json`을 지우고 재동의하는 것이다.
- 장기 운영 대응은 앱을 `In production`으로 전환하는 것이다. 단, sensitive/restricted scope를 쓰면 미검증 앱 경고 또는 Google 검증 이슈가 생길 수 있다.
- 본인만 쓰는 개인 앱에서는 우선 테스트 모드로 개발하되, 문서/로그에 `7일마다 재동의 가능`을 명시한다. 이 문제를 코드 버그로 오해하지 않게 한다.

### 토큰 저장/갱신 설계

저장 위치는 다음처럼 고정한다.

```text
~/.config/wj-dashboard/
  google_client_secret.json
  google_token.json
  gcal_ics
```

환경변수 override도 허용한다.

```text
WJ_GOOGLE_CLIENT_SECRET=/path/to/client_secret.json
WJ_GOOGLE_TOKEN=/path/to/token.json
```

동작 원칙:

- `client_secret.json`과 `token.json`은 절대 repo에 두지 않는다.
- `token.json`에는 refresh token이 포함될 수 있으므로 파일 권한은 가능하면 `0600`으로 둔다.
- `get_credentials(scopes)`는 token 파일이 있으면 로드하고, access token 만료 시 refresh token으로 갱신한다.
- refresh 실패나 scope 부족이면 `{"ok": False, "needs_auth": True, "auth_url": ...}` 형태로 반환한다.
- 페이지 GET에서는 인증 실패를 raise하지 말고 목업/빈 상태 또는 `연동 필요` 상태 view를 렌더한다.
- OAuth 최초 동의는 CLI 방식(`InstalledAppFlow.run_local_server`)으로 먼저 구현해도 된다. 웹 라우트에서 `/email/auth/start`, `/email/auth/callback`을 붙이는 것은 다음 단계로 미룰 수 있다.

예시 시그니처:

```python
CONFIG_DIR = Path.home() / ".config" / "wj-dashboard"
CLIENT_SECRET_PATH = Path(os.environ.get("WJ_GOOGLE_CLIENT_SECRET", CONFIG_DIR / "google_client_secret.json"))
TOKEN_PATH = Path(os.environ.get("WJ_GOOGLE_TOKEN", CONFIG_DIR / "google_token.json"))

SCOPES_READONLY = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

SCOPES_FULL = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]

def get_credentials(scopes: list[str]) -> tuple[bool, object | None, dict]:
    """성공 시 (True, creds, meta), 실패 시 (False, None, meta). 절대 raise하지 않는다."""
```

### gmail.py 모듈 설계

`gmail.py`는 `gcal.py`의 실패 처리 원칙을 그대로 따른다.

- 어떤 public 함수도 예외를 밖으로 던지지 않는다.
- 실패하면 빈 리스트, 빈 dict, 또는 `{"ok": False, "error": "...", "needs_auth": bool}`를 반환한다.
- 앱 전체가 Gmail 오류 때문에 죽으면 안 된다.
- API 응답 원본을 템플릿에 직접 넘기지 않고, 작고 안정된 dict로 정규화한다.
- 메일 본문 HTML은 반드시 sanitize하거나 text/plain 우선으로 렌더한다. Gmail 원본 HTML을 그대로 `|safe`로 출력하지 않는다.

권장 함수 시그니처:

```python
def get_service(scopes: list[str] | None = None):
    """Gmail API service 또는 None. 실패 시 None."""

def list_inbox(query: str = "in:inbox newer_than:30d", max_results: int = 30) -> list[dict]:
    """큐 계산용 메시지 요약 리스트. 실패 시 []."""

def get_message(message_id: str, fmt: str = "full") -> dict:
    """단일 메시지 정규화. 실패 시 {}."""

def get_thread(thread_id: str) -> dict:
    """스레드 메시지 목록과 내가 마지막 응답했는지 계산할 원본. 실패 시 {}."""

def list_drafts(max_results: int = 20) -> list[dict]:
    """임시보관함 요약. 실패 시 []."""

def build_followups(days: int = 2, max_results: int = 50) -> list[dict]:
    """내가 마지막 답장을 안 한 스레드 계산. 실패 시 []."""

def create_draft(thread_id: str, to: str, subject: str, body_text: str,
                 in_reply_to: str | None = None) -> dict:
    """Gmail draft 생성. 성공: {ok, draft_id}. 실패: {ok:false,...}."""

def send_draft(draft_id: str) -> dict:
    """기존 draft 발송. 승인 버튼에서만 호출."""

def send_message(to: str, subject: str, body_text: str,
                 thread_id: str | None = None) -> dict:
    """draft 없이 바로 MIME 생성 후 발송. 가능하면 send_draft 우선."""

def modify_labels(message_id: str, add: list[str] | None = None,
                  remove: list[str] | None = None) -> dict:
    """라벨/읽음/별표 등 변경."""

def archive(message_id: str) -> dict:
    """INBOX 라벨 제거."""

def star(message_id: str, on: bool = True) -> dict:
    """STARRED 라벨 add/remove."""

def snooze(message_id: str, until_ts: int) -> dict:
    """Gmail users.messages.modify 또는 users.messages.batchModify로 스누즈 가능 여부 확인 후 구현."""
```

파싱 함수도 명시적으로 둔다.

```python
def normalize_message(raw: dict) -> dict:
    """Gmail API message -> UI용 message dict."""

def extract_headers(payload: dict) -> dict:
    """From, To, Subject, Date, Message-ID, In-Reply-To."""

def extract_body(payload: dict) -> dict:
    """{text, html_sanitized, snippets}. text/plain 우선, 없으면 html->text."""
```

팔로업 계산:

- 내 계정 이메일 주소를 `profile = users().getProfile(userId="me")`에서 가져온다.
- 후보 쿼리 예: `in:inbox -from:me newer_than:60d`.
- 스레드별 마지막 메시지 sender가 내가 아니고, 마지막 메시지 이후 N일 이상 지났고, `CATEGORY_PROMOTIONS` 같은 낮은 우선 카테고리가 아니면 팔로업 후보로 본다.
- 내가 답장한 뒤 상대가 다시 답장하지 않은 스레드는 팔로업이 아니다.
- `SENT` 메일과 thread history를 함께 확인해야 오탐이 줄어든다.

### 캘린더 쓰기

Calendar API 쓰기는 `calendar_write.py`로 분리한다.

권장 함수:

```python
def get_calendar_service():
    """Calendar API service 또는 None."""

def insert_event(candidate: dict, calendar_id: str = "primary") -> dict:
    """events.insert. 성공: {ok, event_id, html_link}. 실패: {ok:false,...}."""

def delete_event(event_id: str, calendar_id: str = "primary") -> dict:
    """등록 되돌리기용. 실패해도 raise하지 않는다."""
```

`events.insert` 입력 예:

```python
event = {
    "summary": candidate["title"],
    "location": candidate.get("place") or "",
    "description": "wj 메일 후보에서 승인 등록\n\n근거: " + candidate.get("source", ""),
    "start": {"dateTime": candidate["start_iso"], "timeZone": "Asia/Seoul"},
    "end": {"dateTime": candidate["end_iso"], "timeZone": "Asia/Seoul"},
}
```

원칙:

- 캘린더 등록은 `POST /api/email/events/<candidate_id>/approve`에서만 한다.
- 후보 생성 단계에서는 Calendar API를 호출하지 않는다.
- `candidate_id`와 `message_id`를 묶어 중복 등록을 막는다.
- 등록 후 `event_id`를 어디에 저장할지 결정해야 한다. 초기에는 서버 메모리나 Gmail draft metadata만으로 충분하지 않으므로, 최소한 `~/.config/wj-dashboard/email_state.json` 같은 로컬 상태 파일을 둔다. 이 파일도 git에 넣지 않는다.

### 일정 감지와 답장 초안 LLM 호출

LLM 호출 위치는 서버다. 브라우저에서 메일 본문이나 API key를 직접 LLM provider로 보내지 않는다.

일정 감지 입력:

- `message_id`, `thread_id`
- 제목, 발신자, 수신자
- 정규화된 text/plain 본문
- 수신 날짜 KST
- 현재 날짜 KST

일정 감지 출력:

```json
{
  "ok": true,
  "candidates": [
    {
      "id": "evt_m_123_0",
      "message_id": "m_123",
      "title": "김민준 면담",
      "start_iso": "2026-06-09T14:00:00+09:00",
      "end_iso": "2026-06-09T14:30:00+09:00",
      "date_label": "6/9(월)",
      "time_label": "14:00",
      "place": "연구실",
      "source": "다음 주 월요일(6월 9일) 오후 2시",
      "source_offsets": [135, 157],
      "confidence": 0.87,
      "status": "pending"
    }
  ]
}
```

답장 초안 생성 입력:

- 스레드의 최근 N개 메시지 요약
- 현재 포커스 메일 본문
- 내가 원하는 tone (`정중·간결`, `따뜻하게`, `간단 수락`)
- 일정 후보가 있으면 후보 요약
- 금지 조건: 없는 약속 만들지 않기, 자동 발송 금지, 개인정보 과다 인용 금지

답장 초안 출력:

```json
{
  "ok": true,
  "draft": {
    "status": "generated",
    "to": "김민준 <minjun@damilab.kr>",
    "subject": "Re: [재문의] 면담 가능 시간 회신 부탁드립니다",
    "tone": "정중·간결",
    "text": "안녕하세요 민준님,\n\n...",
    "warnings": []
  }
}
```

비용 통제:

- 인박스 목록 로딩 때 LLM을 호출하지 않는다.
- 메일별 `초안 생성`, `일정 후보 찾기` 버튼을 눌렀을 때만 호출한다.
- 동일 `message_id` + `body_hash` + `tone`에 대한 결과는 로컬 상태 파일에 캐시한다.
- 본문 전체가 긴 경우 최근 메시지와 quoted text 제거 결과만 보낸다.
- 첨부파일은 기본적으로 LLM 입력에서 제외한다.

출처 문장 추출:

- 1차는 규칙 기반으로 날짜/시간 표현 주변 문장을 뽑는다.
- 예: `다음 주`, `월요일`, `오후 2시`, `6/9`, `마감`, `due`, `deadline`, `by Friday`.
- LLM은 후보 구조화와 애매한 표현 해석에만 쓴다.
- 프론트 하이라이트는 `source_offsets`가 있으면 offset 기반, 없으면 `source` 문자열 매칭 기반으로 한다.

### 우선순위/이유칩/팔로업 산출 로직

우선순위는 규칙 기반을 먼저 사용한다. LLM 보조는 선택이다.

`p0` 예:

- 오늘/내일 마감 또는 일정 후보가 있고 응답 필요.
- 발신자가 직접 사람이고, 2일 이상 내가 답장하지 않음.
- 제목/본문에 `urgent`, `마감`, `오늘`, `내일까지`, `reminder`, `D-1` 등이 있음.
- 일정 후보가 승인 대기이고 메일이 최근 24시간 이내.

`p1` 예:

- 7일 이내 마감/일정.
- 2일 미만 미회신이지만 직접 요청.
- 교수/학생/행정실/학회 등 중요 발신자.

`p2` 예:

- 뉴스레터, GitHub 알림, 자동 시스템 메일.
- 답장 필요성이 낮고 마감/일정 후보가 없음.

이유칩은 UI에서 짧게 보여야 한다.

- `오늘 마감`
- `일정 포함`
- `답장 필요`
- `3일째 미회신`
- `학회`
- `행정`
- `dev`
- `자동 알림`

계산 결과 예:

```python
def score_message(m: dict, thread: dict, now: datetime) -> dict:
    return {
        "priority": "p0",
        "score": 92,
        "reasons": ["답장 필요", "3일째 미회신", "일정 포함"],
    }
```

### API 라우트 설계

페이지:

- `GET /email`
  - Gmail 연동 전: `email_data.build_view()`로 목업 렌더.
  - Gmail 연동 후: `gmail.build_email_view()` 결과를 `render_template("email_focus.html", active_tab="email", **view)`로 렌더.
  - query: `?id=<message_id>`, `?q=<gmail_search>`, `?label=<label_id>`.

인증:

- `GET /email/auth/status`
- `GET /email/auth/start`
- `GET /email/auth/callback`
- `POST /api/email/auth/revoke`

읽기/부분 갱신:

- `GET /api/email/version`
- `GET /api/email/queue?q=...`
- `GET /api/email/messages/<message_id>`
- `GET /api/email/threads/<thread_id>`
- `GET /api/email/drafts`
- `GET /api/email/followups`

초안/발송:

- `POST /api/email/messages/<message_id>/draft/generate`
- `POST /api/email/drafts`
- `POST /api/email/drafts/<draft_id>/send`
- `POST /api/email/drafts/<draft_id>/discard`

일정 후보:

- `POST /api/email/messages/<message_id>/events/detect`
- `POST /api/email/events/<candidate_id>/approve`
- `POST /api/email/events/<candidate_id>/ignore`
- `POST /api/email/events/<candidate_id>/restore`
- `POST /api/email/events/<candidate_id>/undo`

Gmail 처리:

- `POST /api/email/messages/<message_id>/archive`
- `POST /api/email/messages/<message_id>/star`
- `POST /api/email/messages/<message_id>/unstar`
- `POST /api/email/messages/<message_id>/snooze`
- `POST /api/email/messages/<message_id>/labels`
- `POST /api/email/followups/<thread_id>/dismiss`

JSON 응답 형태:

```json
{
  "ok": true,
  "message": "캘린더에 추가했습니다",
  "data": {},
  "version": 1730000000
}
```

오류 응답:

```json
{
  "ok": false,
  "error": "google_auth_expired",
  "message": "Google 재동의가 필요합니다",
  "needs_auth": true
}
```

HTTP status:

- 입력 오류: 400
- 인증 필요/토큰 만료: 401 또는 403보다 `200 + ok:false + needs_auth:true`도 페이지 UX에는 실용적이다. 액션 API는 401 권장.
- 찾을 수 없음: 404
- Google API 실패: 502 또는 200 `ok:false`. 기존 앱 패턴은 대체로 `jsonify(result)`와 code를 섞는다. 이메일 쪽은 액션 실패가 명확해야 하므로 code를 함께 주는 편이 낫다.

멱등성:

- 일정 승인은 `candidate_id` 기준으로 이미 `done`이면 기존 `event_id`를 반환한다.
- 발송은 가장 위험하다. `send_draft`는 `draft_id`가 이미 발송되었는지 로컬 상태 파일에 기록하고 중복 클릭을 막는다.
- `archive`, `star`, `labels`는 같은 요청 반복 시 결과가 같도록 구현한다.
- `ignore`, `dismiss`도 상태 파일에 저장해 반복 호출 가능하게 한다.

CSRF:

- 기존 `app.py`의 `_csrf_guard()`가 POST의 same-origin을 검사한다.
- 프론트 fetch는 same-origin relative URL을 사용한다.
- 별도 CSRF token을 추가할 필요는 없지만, Gmail 발송/Calendar 등록처럼 부작용이 큰 액션에는 confirm UI를 둔다.

### email_data.build_view() 목업 dict를 실데이터로 바꾸는 매핑

현재 `email_data.build_view()` 반환 구조는 유지한다. 실데이터 builder가 같은 key를 채우면 `email_focus.html`을 크게 바꾸지 않고 전환할 수 있다.

| 목업 필드 | 실데이터 원천 | 변환 규칙 |
| --- | --- | --- |
| `queue[].id` | Gmail `message.id` | 현재 포커스 선택용 |
| `queue[].sender` | `From` header display name | 없으면 이메일 앞부분 |
| `queue[].subject` | `Subject` header | 없으면 `(제목 없음)` |
| `queue[].time` | `Date` header | KST 상대시간 `방금`, `1시간 전`, `어제` |
| `queue[].priority` | `score_message()` | `p0/p1/p2` |
| `queue[].reasons` | 규칙 기반 이유칩 | 최대 3개 |
| `queue[].has_event` | 감지된 후보 존재 | pending/done/ignored 포함 |
| `queue[].has_draft` | Gmail draft 또는 생성 캐시 | draft id가 있으면 true |
| `queue[].current` | 선택 message id | GET `/email?id=...` 기준 |
| `focus.id` | Gmail `message.id` | 중앙 본문 대상 |
| `focus.sender` | `From` display name | avatar 첫 글자에도 사용 |
| `focus.sender_email` | `From` email | header parse |
| `focus.subject` | `Subject` | `Re:` 유지 |
| `focus.time` | `Date` | KST 표시 |
| `focus.priority` | queue와 동일 | badge 색상 |
| `focus.reasons` | queue와 동일 | 중앙 이유칩 |
| `focus.body_html` | sanitize된 본문 | 일정 source 문장만 `<mark>` 허용 |
| `focus.summary.event` | candidates 상태 | `일정 후보 N · 승인 대기` |
| `focus.summary.draft` | draft 상태 | `초안 생성 필요`, `미발송 초안 1`, `발송 완료` |
| `focus.summary.followup` | followup 계산 | `팔로업 3일째` 등 |
| `draft.status` | Gmail draft/cache | `none/generated/unsent/sent/discarded`를 템플릿 표시값으로 매핑 |
| `draft.to` | Reply-To/From | 사람이 읽는 형태 |
| `draft.text` | LLM 출력 또는 Gmail draft | 발송 전 수정 가능 |
| `candidates[]` | 감지 결과 + 상태 파일 | `pending/done/ignored` |
| `drafts_box[]` | Gmail drafts.list/get | 좌측 임시보관함 |
| `labels[]` | Gmail labels.list + count 계산 | system label은 숨기거나 한글화 |
| `progress` | queue index | `idx`, `total` |
| `estats` | 큐/후보/초안/팔로업 합계 | topbar 상태 |
| `is_mock` | 실연동 여부 | Gmail 붙으면 false |

실데이터 view 함수 예:

```python
def build_email_view(selected_id: str | None = None, query: str | None = None) -> dict:
    """email_data.build_view()와 같은 shape를 반환. 실패 시 빈/연동필요 view."""
```

## 4. 프론트엔드 포인트

### wj 앱 템플릿 규약 준수

`templates/email_focus.html`은 현재 규약을 이미 따른다.

- 풀 HTML 문서.
- `<link rel="icon" href="{{ url_for('static', filename='favicon.svg') }}">`.
- `<link rel="stylesheet" href="{{ url_for('static', filename='dashboard.css') }}?v=...">`.
- `<div class="topbar">` 안에 brand와 `{% include "_tabs.html" %}`.
- `active_tab="email"`을 받아 `_tabs.html`에서 메일 탭을 켠다.
- 외부 CDN은 지양한다. 이메일 탭은 지금 JS/CSS를 inline으로 갖고 있으나, 구현이 커지면 `static/email.js`, `static/email.css`로 분리하고 version query를 붙인다.

`_tabs.html`에는 세 번째 탭을 추가할 예정이다.

```html
<a class="tt {% if active_tab == 'email' %}on{% endif %}" href="/email">메일</a>
```

아이콘은 기존 inline SVG 스타일에 맞춘다. 외부 아이콘 CDN은 쓰지 않는다.

### email_focus.html 상태 전환과 부분 갱신

현재 JS는 우측 패널 탭 전환과 요약칩 점프만 한다.

유지할 동작:

- `.pt-tab[data-pane]` 클릭 -> `showPane(name)`.
- `.sjump[data-tab]` 클릭 -> 해당 우측 탭으로 이동.
- 본문 일정 하이라이트 클릭 -> 가능하면 해당 candidate card로 스크롤하고 `.on` 또는 outline을 잠깐 준다.

액션 후 갱신 전략:

- S2/S3 초기에는 액션 성공 후 `location.reload()`가 가장 안전하다.
- UX를 개선할 때 `fetch -> 해당 영역만 교체`로 간다.
- 부분 갱신 후보:
  - 초안 생성: 우측 `AI 초안` pane만 교체.
  - 일정 승인/무시: 우측 `일정 후보` pane과 topbar stats만 교체.
  - 팔로업 해제/스누즈/아카이브: 좌측 큐와 중앙 focus 이동.

낙관적 업데이트 주의:

- `승인 후 발송`, `캘린더에 추가`는 낙관적 업데이트 금지. Google API 성공 응답을 받은 뒤에만 `발송 완료`, `등록 완료`로 바꾼다.
- `별표`, `라벨 변경`은 낙관적 업데이트를 할 수 있지만 실패 시 되돌려야 한다.
- 버튼 중복 클릭 방지를 위해 요청 중 `disabled`와 `처리 중` 상태를 둔다.

공통 fetch helper:

```javascript
async function emailAction(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {})
  });
  const j = await r.json().catch(() => ({ok:false, message:"응답 파싱 실패"}));
  if (!r.ok || !j.ok) throw new Error(j.message || j.error || "요청 실패");
  return j;
}
```

### 승인 게이트 상태 표현

상태명은 화면 전체에서 일관되게 쓴다.

초안:

- `초안 없음`: 아직 생성하지 않음. 버튼은 `초안 생성`.
- `생성 중`: LLM 호출 중.
- `미발송 초안`: 생성됐지만 발송 전. 버튼은 `승인 후 발송`, `수정`, `다시 생성`, `폐기`.
- `발송 완료`: 발송 성공. 버튼은 비활성 또는 `스레드 보기`.
- `폐기됨`: 다시 생성 가능.

일정 후보:

- `승인 대기`: 후보지만 Calendar API 미호출.
- `등록 중`: 승인 요청 중.
- `등록 완료`: Calendar `event_id` 있음.
- `무시됨`: 사용자가 무시.
- `되돌림`: Calendar 삭제 성공 또는 로컬 상태 되돌림.

문구 금지:

- `자동 발송`
- `자동 등록`
- `메일을 보내겠습니다`
- `캘린더에 추가했습니다`를 API 성공 전에 표시

권장 문구:

- `승인 후 발송`
- `캘린더에 추가`
- `초안은 자동 발송되지 않습니다`
- `메일에서 감지한 일정은 후보입니다`

### 폴링 방식

기존 업무 탭은 `static/dashboard.js`에서 `/api/version`을 30초마다 확인하고 mtime 변경 시 reload한다.

이메일 탭은 별도 version endpoint를 둔다.

```json
GET /api/email/version
{
  "ok": true,
  "version": 1730000000,
  "queue_count": 12,
  "pending_events": 1,
  "pending_drafts": 1,
  "followups": 2
}
```

version 산출:

- 초기에는 `time.time()` 기반 캐시 version 또는 로컬 상태 파일 mtime.
- Gmail history API를 붙이면 `historyId`를 활용할 수 있다.
- push notification 전까지는 30~60초 폴링으로 충분하다.

UI:

- topbar의 `.sync-state`를 `메일 OK · 14:02`, `새 메일 감지`, `Google 재동의 필요`처럼 표시한다.
- 새 메일이 감지되면 바로 reload하지 말고 `새로고침` 버튼을 보여도 된다. 답장 작성 중 reload는 위험하다.

## 5. UI/UX 포인트

승인 액션이 묻히면 안 된다.

- 우측 패널의 primary 버튼은 하나만 강하게 보이게 한다.
- `승인 후 발송`과 `캘린더에 추가`는 서로 다른 탭에 있어도 같은 색 계열(`--dash`)을 쓰되 문구를 명확히 다르게 한다.
- 발송과 등록은 위험도가 높으므로 버튼 주변에 현재 상태를 표시한다.

자동처럼 보이는 표현을 피한다.

- 일정 감지는 `후보`, 초안 생성은 `초안`이라고 부른다.
- LLM 결과를 `추천`, `초안`, `후보`로 표현한다.
- 완료 상태는 실제 Google API 성공 후에만 쓴다.

우선순위 메일 부각:

- 좌측 큐에서 `p0`는 `--p0`, `p1`은 `--p1`, `p2`는 `--p2`.
- `p0`가 많으면 색이 무뎌지므로, `p0` 기준을 엄격하게 둔다.
- `오늘 마감`, `3일째 미회신`, `일정 포함` 같은 이유칩이 우선순위의 설명 역할을 해야 한다.

팔로업과 스누즈는 다르다.

- 팔로업: 내가 조치해야 하는 스레드 상태. `내가 마지막 답장 안 함`, `N일째 미회신`.
- 스누즈: Gmail에서 나중에 다시 보이게 하는 시간 기반 숨김.
- 팔로업 해제는 로컬 상태에서 `dismissed_followups`로 관리한다.
- 스누즈는 Gmail API 상태를 바꾸는 액션이다.

본문 일정 하이라이트와 후보 연결:

- 본문 `<mark>`는 후보 card의 `source`와 1:1로 연결한다.
- 하이라이트 클릭 시 `일정 후보` 탭으로 이동하고 해당 candidate를 강조한다.
- 후보 card의 source 문장을 클릭하면 중앙 본문 위치로 스크롤한다.
- offset 기반 하이라이트가 틀릴 경우 무리하게 표시하지 말고 source 문장만 card에 보여준다.

메일 본문 표시:

- Gmail HTML은 tracking pixel, remote image, inline style이 많다. 기본은 text/plain 또는 sanitize HTML이다.
- remote image는 기본 비활성. 필요하면 `이미지 표시` 버튼을 둔다.
- quoted reply는 접거나 제거한다. 포커스 화면에서는 최신 메시지 내용이 먼저 보여야 한다.

## 6. 단계별 구현 순서

### S1. 탭 + 목업 화면

상태: 이미 만듦.

작업:

- `/email` 라우트가 `email_data.build_view()`를 호출해 `email_focus.html` 렌더.
- `_tabs.html`에 `메일` 탭 추가.
- `active_tab="email"` 연결.

DoD:

- `/`, `/think`, `/email` 세 탭 이동이 된다.
- `/email`에서 좌 큐/중앙 본문/우 패널 탭이 보인다.
- Gmail 미연동 표시가 topbar에 보인다.
- 다른 코드의 업무/생각 탭 동작이 깨지지 않는다.

### S2. OAuth + 인박스 읽기

작업:

- `gmail.py` 생성.
- Google OAuth client secret/token 경로를 `~/.config/wj-dashboard/`로 고정.
- `gmail.readonly` scope만으로 `list_inbox`, `get_message`, `get_thread` 구현.
- `build_email_view()`가 실제 Gmail 인박스 일부를 목업 dict shape로 변환.
- 인증 실패 시 `연동 필요` 상태를 렌더하고 앱은 계속 동작.

DoD:

- repo에 token/client secret이 없다.
- 토큰이 없거나 만료되어도 Flask가 죽지 않는다.
- `/email`에서 실제 Gmail 제목/발신자/본문이 표시된다.
- 본문 HTML은 sanitize 또는 text/plain으로 표시된다.
- Google API 오류가 빈 큐 또는 `needs_auth` UI로 처리된다.

### S3. 초안 생성·발송

작업:

- `llm_email.py`에 답장 초안 생성 함수 추가.
- `POST /api/email/messages/<message_id>/draft/generate`.
- Gmail draft 생성 `create_draft`.
- `POST /api/email/drafts/<draft_id>/send`는 승인 버튼에서만 호출.
- 중복 발송 방지 상태 기록.

DoD:

- 인박스 로딩만으로 LLM이 호출되지 않는다.
- 메일별 버튼 클릭 시에만 초안이 생성된다.
- 생성된 초안은 `미발송 초안`으로 표시된다.
- `승인 후 발송` 클릭 전에는 Gmail 발송이 절대 일어나지 않는다.
- 같은 draft에 대해 중복 클릭해도 한 번만 발송된다.

### S4. 일정 감지·캘린더 승인

작업:

- 규칙 기반 일정 후보 추출 + LLM 보조 구조화.
- `calendar_write.py` 생성.
- `POST /api/email/messages/<message_id>/events/detect`.
- `POST /api/email/events/<candidate_id>/approve`.
- `POST /api/email/events/<candidate_id>/ignore`.
- 후보 상태와 Calendar `event_id`를 로컬 상태 파일에 저장.

DoD:

- 일정 후보는 `승인 대기`로만 생성된다.
- `캘린더에 추가` 클릭 전에는 Calendar API 쓰기가 일어나지 않는다.
- 등록 성공 후에만 `등록 완료`가 표시된다.
- 본문 하이라이트와 후보 source가 연결된다.
- 같은 후보를 두 번 승인해도 중복 이벤트가 생기지 않는다.

### S5. 우선순위·팔로업

작업:

- `score_message()` 규칙 정리.
- `build_followups()` 구현.
- Gmail 라벨/별표/아카이브/스누즈 액션 추가. 이때 `gmail.modify` scope가 필요하다.
- 좌측 큐 정렬: `p0 > p1 > p2`, 미회신일수, 일정/마감 가까운 순.
- `dismissed_followups`, snooze 상태 반영.

DoD:

- `p0` 메일이 좌측 큐 상단에 안정적으로 뜬다.
- 이유칩만 봐도 왜 중요한지 알 수 있다.
- 내가 마지막 답장을 보낸 스레드는 팔로업에서 빠진다.
- 팔로업 해제와 스누즈가 다른 상태로 저장된다.
- 라벨/별표/아카이브 실패 시 UI가 거짓 완료 상태를 보이지 않는다.

### S6. 나중 보안 하드닝

작업:

- Tailscale 또는 사설망 접근 제한.
- OAuth callback route 보호.
- token 파일 권한 점검.
- audit log: 발송/캘린더 등록 같은 부작용 기록.
- Gmail restricted scope 검증이 필요한 경우 문서/절차 정리.

DoD:

- 외부 노출 경로가 명확하다.
- token/client secret 유출 가능성이 낮다.
- 발송/등록 액션 기록을 나중에 추적할 수 있다.

## 7. 지금 안 하는 것 / 나중에

지금 안 한다.

- Tailscale, VPN, Cloudflare Access 같은 추가 접근 제어.
- 여러 Gmail 계정 통합.
- Workspace domain-wide delegation.
- Gmail push notification / Pub/Sub.
- 첨부파일 OCR/요약.
- 대량 발송, 뉴스레터 발송, CRM식 캠페인.
- 메일 전체를 DB에 복제 저장.
- 자동 발송, 자동 캘린더 등록.
- 모바일 UI 최적화.

나중에 검토한다.

- `/email/auth/start`와 `/email/auth/callback`을 통한 웹 OAuth 플로우.
- Google app publishing status를 production으로 바꾸는 시점.
- restricted scope 검증 필요 여부.
- Gmail history API 기반 변경 감지.
- 메일 검색 고급 필터.
- 중요 발신자 allowlist/denylist.
- LLM provider별 비용/토큰 사용량 기록.
- 캘린더 이벤트 수정/되돌리기 UX 강화.

## 8. 위험/주의 체크리스트

보안/비밀:

- [ ] `google_client_secret.json`, `google_token.json`이 repo에 없다.
- [ ] token 파일은 `~/.config/wj-dashboard/` 아래에 있다.
- [ ] `.gitignore`에 필요한 패턴이 있다.
- [ ] LLM API key도 env 또는 config dir에서만 읽는다.
- [ ] Gmail 원본 HTML을 sanitize 없이 `|safe`로 출력하지 않는다.

OAuth:

- [ ] 테스트 사용자에 본인 Gmail이 등록되어 있다.
- [ ] 테스트 모드 refresh token 7일 만료를 알고 있다.
- [ ] scope 변경 후 재동의가 필요하다는 UI/로그가 있다.
- [ ] `gmail.modify`는 S5 전까지 늦춘다.
- [ ] Calendar 쓰기는 `calendar.events`로 시작하고 전체 `calendar` scope를 피한다.

승인 게이트:

- [ ] 초안 생성과 발송은 별도 액션이다.
- [ ] 일정 감지와 Calendar 등록은 별도 액션이다.
- [ ] API 성공 전에는 `발송 완료`, `등록 완료`를 표시하지 않는다.
- [ ] 발송 버튼 중복 클릭을 막는다.
- [ ] 일정 승인 중복 클릭을 막는다.

데이터/상태:

- [ ] 후보 상태, 발송 상태, 팔로업 해제 상태를 로컬 상태 파일에 저장한다.
- [ ] 상태 파일도 repo에 넣지 않는다.
- [ ] message body hash를 저장해 stale candidate/draft를 구분한다.
- [ ] Gmail message id/thread id와 로컬 candidate id 매핑이 안정적이다.

UX:

- [ ] `자동`처럼 보이는 문구가 없다.
- [ ] 우선순위 이유칩이 짧고 구체적이다.
- [ ] 팔로업과 스누즈가 UI에서 구분된다.
- [ ] 본문 하이라이트와 일정 후보가 연결된다.
- [ ] 답장 작성 중 자동 reload가 발생하지 않는다.

운영:

- [ ] Google API 실패가 Flask 전체 장애로 번지지 않는다.
- [ ] 모든 public Gmail/Calendar 함수는 실패 시 빈 결과 또는 `ok:false`를 반환한다.
- [ ] 로그에 메일 본문 전문을 남기지 않는다.
- [ ] LLM 호출은 버튼 클릭 시에만 일어난다.
- [ ] 데스크톱 전용 viewport(`width=1400`) 전제를 유지하거나, 모바일 지원 시 별도 설계를 한다.
