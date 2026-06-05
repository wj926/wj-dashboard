# 이메일 탭 개발/검증 하네스

이 문서는 `docs/email_dev_guide.md`를 지키면서 `/email` 탭을 개발하기 위한 하네스 설계다. 목표는 OAuth, 실 Gmail, 실 Calendar, 실 LLM 없이도 개발과 검증을 계속 돌리고, 승인 게이트와 비밀 저장 위치 같은 불변식이 깨지면 바로 잡히게 하는 것이다.

대상 앱은 `/home/dami/wj/dashboard-app`의 Flask wj 앱이다. 산출물은 테스트, 정적 검사, 헤드리스 캡처, Codex 리뷰 루프를 하나의 개발 절차로 묶는다.

## 1. 하네스 개요

하네스는 네 레이어로 나눈다.

| 레이어 | 자동 여부 | 잡는 것 | 못 잡는 것 |
| --- | --- | --- | --- |
| Google API Fake | 자동/로컬 | OAuth 없이 Gmail/Calendar/LLM 데이터 흐름, 장애 주입, 중복 클릭 | 실제 Google scope, refresh token 만료, Gmail API 응답 차이 |
| pytest | 자동 | 라우트 계약, 예외 무전파, 승인 전 상태 불변, sanitize 계약, 상태 파일 위치 | 문구 뉘앙스, 화면 밀도, 실제 시각 깨짐 |
| grep/스크립트 | 자동 | repo 비밀, 금지 문자열, CDN, 위험한 `|safe`, glyph 정책 | 문맥을 타는 false positive, 실제 API 부작용 |
| 시각 검수 | 반자동 | 1400px 데스크톱 레이아웃, M4 포커스 화면, 승인 문구 가시성 | 모든 상태 조합, 백엔드 멱등성 |
| 수동/Codex 리뷰 | 수동 | 자동화가 놓친 UX, 보안 의도, 가이드 위반 해석 | 반복 회귀 방지 |

가이드 8장 위험 체크리스트 커버리지:

| 체크 항목 | 커버 방식 | 하네스 기준 |
| --- | --- | --- |
| `google_client_secret.json`, `google_token.json`이 repo에 없다 | grep 검사, pytest | `git ls-files`와 `find`로 repo 내부 금지 파일 검사 |
| token 파일은 `~/.config/wj-dashboard/` 아래에 있다 | pytest, 수동 리뷰 | 설정 상수와 env override가 repo 경로를 기본값으로 쓰지 않는지 검사 |
| `.gitignore`에 필요한 패턴이 있다 | grep 검사 | `google_token.json`, `google_client_secret.json`, `email_state.json`, `_shots/` 패턴 확인 |
| LLM API key도 env 또는 config dir에서만 읽는다 | grep 검사, 수동 리뷰 | `OPENAI_API_KEY`류 값이 코드에 하드코딩되지 않는지 검사 |
| Gmail 원본 HTML을 sanitize 없이 `|safe`로 출력하지 않는다 | pytest, grep 검사, 수동 리뷰 | `body_html|safe`는 sanitize 산출물에만 허용 |
| 테스트 사용자에 본인 Gmail이 등록되어 있다 | 수동 리뷰 | OAuth 실연동 전 체크리스트로만 확인 |
| 테스트 모드 refresh token 7일 만료를 알고 있다 | 수동 리뷰 | auth UI/로그 문구와 운영 메모 확인 |
| scope 변경 후 재동의가 필요하다는 UI/로그가 있다 | pytest, 수동 리뷰 | `needs_auth:true` 응답과 안내 문구 확인 |
| `gmail.modify`는 S5 전까지 늦춘다 | grep 검사, 수동 리뷰 | S2-S4 브랜치에서 `gmail.modify` scope 문자열 금지 |
| Calendar 쓰기는 `calendar.events`로 시작한다 | grep 검사 | 전체 `https://www.googleapis.com/auth/calendar` scope 금지 |
| 초안 생성과 발송은 별도 액션이다 | pytest, 수동 리뷰 | generate endpoint와 send endpoint 분리, generate에서 send 호출 금지 |
| 일정 감지와 Calendar 등록은 별도 액션이다 | pytest, 수동 리뷰 | detect endpoint와 approve endpoint 분리, detect에서 insert 호출 금지 |
| API 성공 전에는 `발송 완료`, `등록 완료`를 표시하지 않는다 | pytest, grep 검사, 시각 검수 | fake 실패/대기 상태에서 완료 문구 미표시 |
| 발송 버튼 중복 클릭을 막는다 | pytest, 시각 검수 | 같은 `draft_id` send 반복 시 한 번만 성공 처리 |
| 일정 승인 중복 클릭을 막는다 | pytest | 같은 `candidate_id` approve 반복 시 기존 `event_id` 반환 |
| 후보 상태, 발송 상태, 팔로업 해제 상태를 로컬 상태 파일에 저장한다 | pytest, 수동 리뷰 | `~/.config/wj-dashboard/email_state.json` 또는 env override |
| 상태 파일도 repo에 넣지 않는다 | grep 검사, pytest | repo 내부 상태 파일 탐지 실패 처리 |
| message body hash를 저장해 stale candidate/draft를 구분한다 | pytest, 수동 리뷰 | body hash mismatch면 stale 응답 |
| Gmail message id/thread id와 로컬 candidate id 매핑이 안정적이다 | pytest | fake fixture에서 id 재빌드 후 동일성 확인 |
| `자동`처럼 보이는 문구가 없다 | grep 검사, 시각 검수, 수동 리뷰 | 긍정형 자동 발송/등록 문구 금지, 부정형 설명은 허용 |
| 우선순위 이유칩이 짧고 구체적이다 | 시각 검수, 수동 리뷰 | 큐에서 3개 이하, 짧은 한국어 문구 |
| 팔로업과 스누즈가 UI에서 구분된다 | 시각 검수, 수동 리뷰 | 별도 버튼/상태/설명 확인 |
| 본문 하이라이트와 일정 후보가 연결된다 | pytest, 시각 검수 | `<mark data-candidate-id>`와 후보 card id 매칭 |
| 답장 작성 중 자동 reload가 발생하지 않는다 | pytest, 수동 리뷰 | draft edit 상태에서 polling reload 금지 |
| Google API 실패가 Flask 전체 장애로 번지지 않는다 | pytest | 장애 주입 fake에서 `GET /email` 200 |
| 모든 public Gmail/Calendar 함수는 실패 시 빈 결과 또는 `ok:false`를 반환한다 | pytest | public 함수 장애 주입 테스트 |
| 로그에 메일 본문 전문을 남기지 않는다 | grep 검사, 수동 리뷰 | `logger.*body`, `print(body)`류 금지 |
| LLM 호출은 버튼 클릭 시에만 일어난다 | pytest | `GET /email`, queue API에서 fake LLM call count 0 |
| 데스크톱 전용 viewport(`width=1400`) 전제를 유지한다 | 시각 검수, grep 검사 | template viewport와 1400px 캡처 확인 |

자동화는 merge 전 회귀 방지용이다. 수동 검수는 특히 문구, 승인 버튼의 시각적 위계, fake로 대체하기 어려운 OAuth 운영 항목에 쓴다.

## 2. 레이어 1: Google API 목 설계

실 Gmail, Calendar, LLM 호출을 앱 라우트에서 직접 import하지 않고, 작은 인터페이스 뒤로 숨긴다. 라우트는 `get_email_services()` 같은 factory를 통해 real 또는 fake 구현을 받는다.

권장 파일 구조:

```text
gmail.py                 # 실 Gmail 구현
calendar_write.py        # 실 Calendar 쓰기 구현
llm_email.py             # 실 LLM 구현
email_services.py        # Protocol, factory, fake 주입
email_fake.py            # 테스트/로컬 개발용 Fake 구현
email_data.py            # 기존 목업 fixture, Fake 데이터 소스로 재사용
tests/fixtures/email/    # 필요 시 fixture JSON 추가
```

인터페이스 골격:

```python
class GmailClient(Protocol):
    def list_inbox(self, query: str = "in:inbox newer_than:30d", max_results: int = 30) -> list[dict]: ...
    def get_message(self, message_id: str, fmt: str = "full") -> dict: ...
    def get_thread(self, thread_id: str) -> dict: ...
    def list_drafts(self, max_results: int = 20) -> list[dict]: ...
    def build_followups(self, days: int = 2, max_results: int = 50) -> list[dict]: ...
    def create_draft(self, thread_id: str, to: str, subject: str, body_text: str, in_reply_to: str | None = None) -> dict: ...
    def send_draft(self, draft_id: str) -> dict: ...
    def modify_labels(self, message_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> dict: ...

class CalendarWriter(Protocol):
    def insert_event(self, candidate: dict, calendar_id: str = "primary") -> dict: ...
    def delete_event(self, event_id: str, calendar_id: str = "primary") -> dict: ...

class EmailLLM(Protocol):
    def detect_events(self, message: dict, now_kst: datetime) -> dict: ...
    def generate_reply_draft(self, message: dict, thread: dict, tone: str) -> dict: ...
```

실 구현과 Fake는 같은 시그니처를 만족해야 한다. pytest에서는 `inspect.signature()`로 public 메서드 시그니처 동등성을 검사한다.

Fake 구현 원칙:

| Fake | 데이터 | 동작 |
| --- | --- | --- |
| `FakeGmailClient` | `email_data.build_view()`의 `queue`, `focus`, `drafts_box`, `labels` | inbox/message/thread/draft를 고정 fixture로 반환 |
| `FakeCalendarWriter` | `email_data.build_view()["candidates"]` | approve 전에는 insert 호출 없음, insert 후 `event_id="fake_evt_<candidate_id>"` 반환 |
| `FakeEmailLLM` | `email_data.build_view()["draft"]`, `candidates` | 버튼 호출 시에만 draft/candidates 반환, call count 기록 |
| `FaultyFake*` | 같은 fixture | 모든 메서드가 내부 예외를 만나도 빈 결과 또는 `ok:false` 반환하는지 검증 |

`email_data.py` 재사용 방식:

```python
view = email_data.build_view()
fake_messages = {m["id"]: m for m in view["queue"]}
fake_focus = view["focus"]
fake_candidates = {f"evt_{fake_focus['id']}_{i}": c for i, c in enumerate(view["candidates"])}
```

목업 fixture의 `body_html`은 테스트에서 "이미 sanitize된 fixture"로 취급한다. 실 Gmail 원본 HTML은 반드시 `sanitize_email_html(raw_html)` 또는 text/plain 변환을 거친 뒤 같은 `focus.body_html` key에 들어간다.

real/fake 토글:

```text
WJ_EMAIL_BACKEND=fake          # 기본값. OAuth 없이 개발
WJ_EMAIL_BACKEND=real          # 실 Gmail/Calendar/LLM 사용
WJ_EMAIL_FAKE_MODE=ok          # ok, auth_expired, google_down, llm_down
WJ_EMAIL_STATE_PATH=/tmp/wj-email-state.json
WJ_EMAIL_LLM_BACKEND=fake      # fake, real, off
```

factory 골격:

```python
def get_email_services() -> EmailServices:
    backend = os.environ.get("WJ_EMAIL_BACKEND", "fake")
    if backend == "real":
        return EmailServices(gmail=RealGmailClient(), calendar=RealCalendarWriter(), llm=RealEmailLLM())
    return EmailServices(gmail=FakeGmailClient.from_email_data(), calendar=FakeCalendarWriter(), llm=FakeEmailLLM())
```

장애 주입은 fake 내부에서 처리한다. 예를 들어 `WJ_EMAIL_FAKE_MODE=google_down`이면 fake가 내부적으로 `RuntimeError`를 발생시키는 branch를 타되, public 메서드 밖으로는 예외를 내보내지 않고 `[]`, `{}`, `{"ok": false, "error": "fake_google_down"}`만 반환한다.

## 3. 레이어 2: 자동 테스트(pytest)

권장 디렉토리:

```text
tests/
  conftest.py
  test_email_contracts.py
  test_email_routes.py
  test_email_approval_gates.py
  test_email_security.py
  test_email_static_policy.py
```

테스트 전제:

```bash
python -m pip install pytest
```

프로젝트에 pytest가 없으면 `requirements-dev.txt`를 별도 슬라이스에서 추가하거나, CI/로컬 문서에 위 설치 절차를 명시한다. 앱 런타임 `requirements.txt`에는 테스트 도구를 섞지 않는 것을 기본으로 한다.

`tests/conftest.py` 골격:

```python
@pytest.fixture(autouse=True)
def fake_email_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WJ_EMAIL_BACKEND", "fake")
    monkeypatch.setenv("WJ_EMAIL_LLM_BACKEND", "fake")
    monkeypatch.setenv("WJ_EMAIL_STATE_PATH", str(tmp_path / "email_state.json"))

@pytest.fixture
def client():
    import app
    app.app.config.update(TESTING=True)
    return app.app.test_client()
```

필수 테스트 목록:

| 파일 | 테스트 | 의도 |
| --- | --- | --- |
| `test_email_contracts.py` | `test_real_and_fake_gmail_signatures_match` | real/fake public Gmail 함수 시그니처가 갈라지지 않게 한다 |
| `test_email_contracts.py` | `test_real_and_fake_calendar_signatures_match` | Calendar 쓰기 fake가 실 구현과 같은 계약을 유지한다 |
| `test_email_contracts.py` | `test_public_gmail_functions_never_raise_on_fault` | Gmail 장애가 Flask 장애로 번지지 않게 한다 |
| `test_email_contracts.py` | `test_public_calendar_functions_never_raise_on_fault` | Calendar 쓰기 실패가 `ok:false`로 닫히게 한다 |
| `test_email_contracts.py` | `test_llm_not_called_by_view_build` | 인박스 로딩만으로 LLM 비용이 발생하지 않게 한다 |
| `test_email_approval_gates.py` | `test_generate_draft_does_not_send` | 초안 생성과 발송이 다른 액션임을 보장한다 |
| `test_email_approval_gates.py` | `test_send_draft_requires_send_endpoint` | `send_draft`는 승인 endpoint에서만 호출되게 한다 |
| `test_email_approval_gates.py` | `test_detect_events_does_not_insert_calendar` | 일정 감지가 Calendar 등록을 하지 않게 한다 |
| `test_email_approval_gates.py` | `test_approve_event_is_idempotent` | 같은 후보 중복 승인으로 중복 이벤트가 생기지 않게 한다 |
| `test_email_approval_gates.py` | `test_send_draft_is_idempotent` | 같은 draft 중복 클릭으로 중복 발송이 생기지 않게 한다 |
| `test_email_approval_gates.py` | `test_pending_state_never_renders_done_before_success` | API 성공 전 `발송 완료`/`등록 완료` 표시를 막는다 |
| `test_email_routes.py` | `test_get_email_200_renders_focus_shell` | `GET /email`이 200이고 좌/중/우 필수 요소를 렌더한다 |
| `test_email_routes.py` | `test_get_email_auth_failure_still_200` | 인증 실패 fake에서도 앱이 죽지 않고 연동 필요 상태를 보여준다 |
| `test_email_routes.py` | `test_email_action_json_success_contract` | 액션 API 성공 응답이 `{ok,message,data,version}` 계약을 지킨다 |
| `test_email_routes.py` | `test_email_action_json_failure_contract` | 액션 API 실패 응답이 `{ok:false,error,message}` 계약을 지킨다 |
| `test_email_routes.py` | `test_duplicate_click_json_contract` | 중복 클릭 응답이 멱등이고 클라이언트가 처리 가능한 JSON을 돌려준다 |
| `test_email_security.py` | `test_no_repo_secret_or_token_files` | repo 내부에 Google/LLM 비밀 파일이 없는지 확인한다 |
| `test_email_security.py` | `test_email_state_path_is_outside_repo_by_default` | 상태 파일 기본 위치가 repo 밖인지 보장한다 |
| `test_email_security.py` | `test_body_safe_only_after_sanitize` | Gmail 원본 HTML이 sanitize 없이 `|safe`로 들어가지 않게 한다 |
| `test_email_security.py` | `test_sanitizer_removes_script_remote_image_and_event_attrs` | script, remote image, event handler attr를 제거한다 |
| `test_email_static_policy.py` | `test_no_external_cdn_in_email_template` | 이메일 탭이 외부 CDN에 의존하지 않게 한다 |
| `test_email_static_policy.py` | `test_no_positive_auto_send_or_register_wording` | 자동 발송/등록처럼 보이는 문구를 막는다 |

라우트 endpoint 이름은 가이드 계약을 따른다.

```text
GET  /email
POST /api/email/messages/<message_id>/draft/generate
POST /api/email/drafts/<draft_id>/send
POST /api/email/messages/<message_id>/events/detect
POST /api/email/events/<candidate_id>/approve
POST /api/email/events/<candidate_id>/ignore
POST /api/email/events/<candidate_id>/undo
GET  /api/email/version
```

보안 테스트의 sanitize 계약은 다음 형태로 둔다.

```python
raw = '<p onclick="x()">hi<script>x()</script><img src="https://tracker/p.png"></p>'
clean = sanitize_email_html(raw)
assert "<script" not in clean
assert "onclick" not in clean
assert "https://tracker" not in clean
```

`templates/email_focus.html`의 `{{ focus.body_html|safe }}`는 sanitize된 `focus.body_html`에만 허용한다. 테스트는 실데이터 builder가 `sanitize_email_html()` 또는 text/plain escape를 거친 값만 넣는지 확인한다.

## 4. 레이어 3: 정적 검사(grep/스크립트)

정적 검사는 `scripts/check_email_static.sh`로 묶는 것을 권장한다. 아래는 한 줄 명령 예시다.

repo 내부 Google 비밀/토큰 파일 탐지:

```bash
git ls-files | rg -n '(^|/)(google_client_secret|client_secret|google_token|token|email_state)\.json$' && exit 1 || true
```

작업트리 비밀/토큰 파일 탐지:

```bash
find . -path ./.git -prune -o -type f \( -name 'google_client_secret.json' -o -name 'client_secret*.json' -o -name 'google_token.json' -o -name 'email_state.json' \) -print -quit | rg . && exit 1 || true
```

Gmail/Google/LLM key 하드코딩 탐지:

```bash
rg -n '(AIza[0-9A-Za-z_-]{20,}|ya29\.|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-)' . --glob '!docs/email_harness.md' --glob '!*.png' && exit 1 || true
```

repo 밖 설정 위치 사용 확인:

```bash
rg -n '~/.config/wj-dashboard|Path\.home\(\).*wj-dashboard|WJ_EMAIL_STATE_PATH|WJ_GOOGLE_TOKEN|WJ_GOOGLE_CLIENT_SECRET' gmail.py calendar_write.py email_services.py app.py
```

긍정형 자동 발송/등록 문구 금지:

```bash
rg -n '(자동\s*(발송|전송|등록)|알아서\s*(발송|전송|등록)|메일을 보내겠습니다|캘린더에 자동)' templates static app.py gmail.py calendar_write.py llm_email.py && exit 1 || true
```

완료 문구가 서버 확인 전 JS에 박히는지 검사:

```bash
rg -n '발송 완료|등록 완료|추가했습니다|전송 완료' static templates app.py | rg -v 'ok|success|event_id|sent_at|status ==|status ==' && exit 1 || true
```

외부 CDN 금지:

```bash
rg -n 'https?://(cdn|unpkg|jsdelivr|cdnjs|fonts\.googleapis|fonts\.gstatic)' templates/email_focus.html static && exit 1 || true
```

Gmail 원본 HTML 위험 출력 검사:

```bash
rg -n '\|safe' templates/email_focus.html templates static | rg -v 'body_html|json_str' && exit 1 || true
```

sanitize 함수 존재 확인:

```bash
rg -n 'sanitize_email_html|bleach\.clean|html\.escape|MarkupSafe|markupsafe' gmail.py email_compute.py email_view.py
```

em dash와 화살표 glyph 금지:

```bash
rg -nP '[\x{2014}\x{2190}-\x{21FF}]' templates static app.py email_data.py gmail.py calendar_write.py llm_email.py docs/email_dev_guide.md docs/email_harness.md && exit 1 || true
```

S2-S4에서 `gmail.modify` 조기 도입 금지:

```bash
rg -n 'https://www\.googleapis\.com/auth/gmail\.modify' gmail.py docs tests && exit 1 || true
```

전체 Calendar scope 금지:

```bash
rg -n 'https://www\.googleapis\.com/auth/calendar(["'\''\]])' calendar_write.py gmail.py docs tests && exit 1 || true
```

메일 본문 전문 로그 금지:

```bash
rg -n '(print|logger\.(debug|info|warning|error|exception)).*(body|body_html|raw_html|payload|message)' app.py gmail.py llm_email.py calendar_write.py email_services.py && exit 1 || true
```

위 명령들은 false positive가 날 수 있다. false positive는 예외 목록을 키우기보다 코드 위치를 더 명확하게 바꾸는 것을 우선한다.

## 5. 레이어 4: 시각 검수 하네스

시각 검수는 1400px 데스크톱 전용 화면을 기준으로 한다. 산출물은 repo에 넣지 않는다.

권장 산출물 위치:

```text
_shots/email/
  email-1400.png
  email-1400-cal.png
  email-1400-draft.png
  email-1400-proc.png
```

`.gitignore`에는 다음을 추가해야 한다.

```text
_shots/
```

로컬 Flask 서버를 쓰는 방식:

```bash
WJ_EMAIL_BACKEND=fake flask --app app run --host 127.0.0.1 --port 5055
```

다른 터미널에서 캡처:

```bash
mkdir -p _shots/email && google-chrome --headless=new --disable-gpu --window-size=1400,1000 --screenshot=_shots/email/email-1400.png http://127.0.0.1:5055/email
```

스크립트 골격은 `scripts/capture_email.sh`로 둔다.

```bash
#!/usr/bin/env bash
set -euo pipefail
export WJ_EMAIL_BACKEND="${WJ_EMAIL_BACKEND:-fake}"
export WJ_EMAIL_STATE_PATH="${WJ_EMAIL_STATE_PATH:-/tmp/wj-email-state.json}"
mkdir -p _shots/email
python -m flask --app app run --host 127.0.0.1 --port 5055 &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
python scripts/wait_http.py http://127.0.0.1:5055/email
google-chrome --headless=new --disable-gpu --window-size=1400,1000 --screenshot=_shots/email/email-1400.png http://127.0.0.1:5055/email
```

`wait_http.py`가 없다면 shell `curl -fsS` loop로 대체한다. 단, 스크립트 구현 시에는 네트워크가 아닌 localhost만 호출한다.

라우트만 렌더하는 대안:

```bash
WJ_EMAIL_BACKEND=fake python scripts/render_email_static.py --out /tmp/wj-email-render/email.html
google-chrome --headless=new --disable-gpu --window-size=1400,1000 --screenshot=_shots/email/email-1400.png file:///tmp/wj-email-render/email.html
```

다만 `url_for()`와 `_tabs.html` 포함, Flask context, static 경로를 실제와 맞추려면 로컬 Flask 방식이 더 낫다.

시각 검수 절차:

1. `scripts/capture_email.sh` 실행.
2. `_shots/email/email-1400.png`를 사람 또는 Codex가 확인한다.
3. 확인 항목은 좌측 큐, 중앙 본문, 우측 탭 3개, topbar `정적 목업`/연동 상태, 승인 문구, `width=1400` 기준 overflow다.
4. 우측 탭은 Playwright 또는 간단한 DOM script로 `AI 초안`, `일정 후보`, `팔로업·처리`를 클릭한 뒤 각각 캡처한다.
5. 리뷰에서 발견한 레이아웃/문구 문제를 수정하고 같은 캡처를 다시 남긴다.

Codex 리뷰 프롬프트 예:

```text
_shots/email/email-1400.png를 보고 docs/email_dev_guide.md 기준으로 이메일 탭 UI를 리뷰하라.
특히 승인 게이트 문구, 완료 상태 오해 가능성, 좌/중/우 정보 구조, 본문 sanitize 전제, 데스크톱 overflow를 확인하라.
```

## 6. 한 방 실행

단일 진입점은 `scripts/check_email.sh`를 권장한다. `Makefile`이 생기면 `make check-email`이 이 스크립트를 호출하게 한다.

`scripts/check_email.sh` 구성:

```bash
#!/usr/bin/env bash
set -euo pipefail
export WJ_EMAIL_BACKEND="${WJ_EMAIL_BACKEND:-fake}"
export WJ_EMAIL_LLM_BACKEND="${WJ_EMAIL_LLM_BACKEND:-fake}"
export WJ_EMAIL_STATE_PATH="${WJ_EMAIL_STATE_PATH:-/tmp/wj-email-state.json}"

python -m pytest tests/test_email_contracts.py tests/test_email_routes.py tests/test_email_approval_gates.py tests/test_email_security.py
bash scripts/check_email_static.sh
bash scripts/capture_email.sh
```

Makefile 예:

```make
.PHONY: check check-email
check: check-email
check-email:
	bash scripts/check_email.sh
```

슬라이스별 통과 기준:

| 슬라이스 | 필수 레이어 |
| --- | --- |
| S1 탭 + 목업 화면 | 라우트 pytest, 정적 검사, 1400px 캡처, Codex 리뷰 |
| S2 OAuth + 인박스 읽기 | S1 + Gmail fake/real signature, 장애 주입, sanitize 테스트 |
| S3 초안 생성/발송 | S2 + LLM fake, generate/send 분리, send 멱등성 |
| S4 일정 감지/캘린더 승인 | S3 + detect/approve 분리, insert 멱등성, source highlight 테스트 |
| S5 우선순위/팔로업 | S4 + score/followup 테스트, Gmail modify scope 확인, 라벨/스누즈 실패 처리 |

`make check`는 default fake 모드로만 돈다. 실 Gmail smoke test는 별도 명령으로 분리한다.

```bash
WJ_EMAIL_BACKEND=real WJ_EMAIL_LLM_BACKEND=off python -m pytest tests/test_email_real_smoke.py -q
```

실연동 smoke test는 개인 토큰과 네트워크가 필요하므로 기본 check에 넣지 않는다.

## 7. 개발 루프(정식화)

각 슬라이스는 같은 루프를 따른다.

```text
작성
테스트 작성 또는 갱신
python -m pytest tests/test_email_*.py
bash scripts/check_email_static.sh
bash scripts/capture_email.sh
Codex 리뷰
수정
scripts/check_email.sh 통과
커밋
```

슬라이스별 완료 기준:

| 슬라이스 | 구현 범위 | 자동 테스트 DoD | 정적/시각/리뷰 DoD |
| --- | --- | --- | --- |
| S1 탭 + 목업 화면 | `/email`, `email_data.build_view()`, `email_focus.html`, `_tabs.html` | `GET /email` 200, active tab, 필수 shell 렌더 | Gmail 미연동 표시, 좌/중/우 보임, CDN 없음, 1400px 캡처 정상 |
| S2 OAuth + 인박스 읽기 | `gmail.py`, readonly scope, `build_email_view()` | 토큰 없음/만료/Google 실패에도 예외 없음, 빈 큐 또는 `needs_auth` | token/client secret repo 없음, `gmail.modify` 없음, 본문 sanitize 확인 |
| S3 초안 생성/발송 | `llm_email.py`, draft generate, draft create/send | `GET /email` LLM call count 0, generate는 send 안 함, send 멱등 | `승인 후 발송` 전 완료 문구 없음, 자동 발송 오해 문구 없음 |
| S4 일정 감지/캘린더 승인 | detect, `calendar_write.py`, approve/ignore/undo, state file | detect는 insert 안 함, approve 멱등, 실패 시 `ok:false` | `calendar.events` scope만 사용, 등록 성공 후에만 `등록 완료`, 하이라이트 연결 |
| S5 우선순위/팔로업 | score, followups, labels/star/archive/snooze | p0 정렬, 내가 마지막 답장한 thread 제외, modify 실패 시 거짓 완료 없음 | 팔로업/스누즈 구분, reason chip 3개 이하, `gmail.modify` 도입 리뷰 |

커밋 전 체크:

```bash
git status --short
bash scripts/check_email.sh
git diff -- docs/email_dev_guide.md docs/email_harness.md app.py gmail.py calendar_write.py llm_email.py templates/email_focus.html tests scripts
```

diff 리뷰 포인트:

| 영역 | 확인 |
| --- | --- |
| 승인 게이트 | 발송/등록 부작용 endpoint가 분리되어 있는가 |
| 실패 처리 | public 함수가 raise를 밖으로 내보내지 않는가 |
| 상태 | 중복 클릭과 stale body hash를 처리하는가 |
| 보안 | repo 밖 설정, sanitize, 로그 본문 금지 |
| UX | 완료 문구가 성공 후에만 보이는가 |

## 8. 지금 당장 만들 하네스 최소본

S1 목업 탭 검증을 위해서는 아래 순서가 최소다.

| 우선순위 | 만들 것 | 이유 |
| --- | --- | --- |
| 1 | `tests/conftest.py` | Flask test client와 fake env 기본값 고정 |
| 2 | `tests/test_email_routes.py::test_get_email_200_renders_focus_shell` | `/email` 라우트와 M4 필수 요소 회귀 방지 |
| 3 | `tests/test_email_security.py::test_no_repo_secret_or_token_files` | 초기부터 repo 비밀 유입 차단 |
| 4 | `scripts/check_email_static.sh` 최소 grep | CDN, 비밀, 위험 문구, glyph를 빠르게 잡음 |
| 5 | `scripts/capture_email.sh` | 이미 수행한 빌드, 헤드리스 캡처, 리뷰, 수정 루프를 공식화 |
| 6 | `scripts/check_email.sh` | pytest, static, capture를 한 명령으로 묶음 |
| 7 | `_shots/` `.gitignore` 패턴 | 캡처 산출물이 commit되지 않게 함 |

S1 최소 pytest 케이스:

```text
test_get_email_200_renders_focus_shell
  의도: `/email`이 200이고 처리 큐, 중앙 본문, AI 초안, 일정 후보, 팔로업 탭을 렌더한다.

test_email_tab_active
  의도: `_tabs.html`에서 메일 탭이 active 상태가 된다.

test_mock_badge_visible
  의도: 실 Gmail 미연동 상태가 topbar에 명확히 보인다.

test_no_external_cdn_in_email_focus
  의도: 목업 탭이 외부 CDN 없이 렌더된다.

test_no_secret_files_in_repo
  의도: Google token/client secret/state 파일이 repo에 들어오지 않는다.
```

S1 최소 정적 검사:

```bash
git ls-files | rg -n '(^|/)(google_client_secret|client_secret|google_token|token|email_state)\.json$' && exit 1 || true
rg -n 'https?://(cdn|unpkg|jsdelivr|cdnjs|fonts\.googleapis|fonts\.gstatic)' templates/email_focus.html static && exit 1 || true
rg -nP '[\x{2014}\x{2190}-\x{21FF}]' templates/email_focus.html static && exit 1 || true
rg -n '(자동\s*(발송|전송|등록)|알아서\s*(발송|전송|등록)|메일을 보내겠습니다)' templates/email_focus.html static && exit 1 || true
```

S1 최소 캡처:

```bash
WJ_EMAIL_BACKEND=fake bash scripts/capture_email.sh
```

S1 통과 판단:

| 항목 | 기준 |
| --- | --- |
| route | `/email` 200 |
| view | 좌측 큐, 중앙 focus, 우측 3개 탭 렌더 |
| status | `정적 목업`, `Gmail 미연동` 같은 상태 표시 |
| policy | repo 비밀 없음, CDN 없음, 금지 glyph 없음 |
| visual | 1400px PNG에서 overflow와 겹침 없음 |
| review | Codex 리뷰에서 승인 게이트/문구/레이아웃 지적 없음 |

이 최소본을 먼저 넣은 뒤 S2부터 Fake 인터페이스와 장애 주입 테스트를 확장한다. 이렇게 하면 실 Gmail 연동이 늦어져도 `/email` 탭의 핵심 불변식은 계속 검사된다.
