# 이메일 답장 자동 초안 + 말투 프로필 설계

작성일: 2026-06-05
대상: wj.damilab.cc dashboard-app, /email 탭

## 배경

이미 답장 초안 기능이 있다. 메일을 고르고 톤 버튼(`정중·간결` / `따뜻하게` / `간단 수락`)을
누르면 claude CLI 가 스레드를 읽고 답장 초안을 써서 미발송으로 저장한다. 발송은 WJ 승인
버튼을 눌러야만 일어난다.

WJ 가 원하는 변화 두 가지:

1. 고정된 톤 버튼 대신, **내 말투/원칙을 한 번 적어두면 모든 초안에 자동 반영**되게.
2. 매번 버튼을 누르지 않고, **답장이 필요한 메일은 미리 초안을 만들어두기**.

## 비목표 (YAGNI)

- 자동 발송은 하지 않는다. 발송은 기존대로 WJ 승인 버튼만. (불변식 유지)
- 메일별 한 줄 즉석 지시는 이번엔 만들지 않는다. (고정 프로필만)
- 말투 프로필 여러 개 전환(상황별 프로필)은 하지 않는다. 단일 프로필 하나.
- 초안 품질 학습/피드백 루프는 범위 밖.

## 구성 (3 부품 + 에러 폴백)

### 1. 말투 프로필 (한 번 저장)

- 저장 위치: `~/.config/wj-dashboard/email_persona.json` (repo 밖, .gitignore.
  `email_rules.json` 과 동일 패턴)
- 내용: 자유 텍스트 한 덩어리. 예:
  > "1인칭은 '제가'. 학생에게는 따뜻하고 격려하는 톤, 외부에는 정중·간결.
  > 거절은 완곡하게 이유를 붙여서. 서명은 '이우진 드림'. 이모지 쓰지 않음."
- 신규 모듈 `email_persona.py`: `load() -> str`, `save(text: str) -> bool`.
- `generate_reply_draft(message, thread, tone)` 가 이 프로필 텍스트를 프롬프트에
  끼워 넣는다. 프로필이 비어 있으면 기존 톤 기본값으로 폴백(하위호환).
- **생성 엔진: codex exec (무과금, ChatGPT 구독).** 기존 claude CLI 호출을
  `codex exec` subprocess 로 교체한다. 모닝논문/trend/paper-revision 에서 검증된 패턴
  (`codex exec [프롬프트]`, stdin 가능)을 따른다. 자동/수동 초안 모두 같은 함수라
  둘 다 codex 로 생성된다. (llm_email.py 의 다른 기능은 이번 범위 밖, 그대로 둠)
- UI: /email 탭에 작은 편집창(textarea) 하나 + 저장 버튼. 라우트
  `GET/POST /api/email/persona`.

### 2. 자동 초안 (refresh 때 미리 생성)

- 트리거: `scripts/email_refresh.py` 가 Gmail → 캐시 저장을 끝낸 직후, 한 단계 추가.
  (15분 타이머. oneshot 이라 초안 생성까지 끝나고 종료해도 무방)
- 대상 선별: `email_score.score(m)` 가 이미 붙이는 **"답장 필요"** 신호를 그대로 사용.
  - 조건: 자동발신 아님 + 미답장(`i_replied == False`) + score 가 "답장 필요"로 판정.
  - 이미 초안이 있는 메일(`email_store` 에 draft 존재)은 건너뜀(중복 생성 방지).
  - **상한 N개**(기본 5). 우선순위(p0>p1>p2) 높은 순으로 N개만. 부하/시간 보호용.
- 생성: 대상마다 `generate_reply_draft(..., 프로필 적용)` 호출 → `save_draft(mid, draft)`
  로 미발송 저장. 기존 수동 버튼과 같은 저장 경로라 UI 가 그대로 인식.
- 무과금: codex exec subprocess 라 API 과금 없음. 비용 부담은 시간뿐.
- 로그: 생성 건수 / 건너뛴 건수 / 상한 초과로 미생성한 건수를 refresh 로그에 남긴다
  (silent truncation 금지).

### 3. UI 표시

- 인박스 큐(`_email_queue_items.html`)에서 이미 `has_draft` 플래그를 들고 있다.
  초안이 미리 준비된 메일에 작은 배지(예: "● 초안 대기")를 단다.
- 메일을 열면 기존대로 우측 "AI 초안" 패널에 미발송 초안이 떠 있다. WJ 는 읽고,
  고치고(textarea), 승인 버튼만 누르면 됨.

### 4. 에러 폴백 (앞서 고친 500 의 근본 처리)

- 오늘 템플릿 방어(A)로 500 재발은 막았다(`_email_center.html`).
- 이번 작업에서 파이썬을 어차피 고치고 재시작하므로, `email_view.py:292` 에 폴백 추가:
  - URL `id` 가 캐시(`by_id`)에 없으면 → 큐 첫 메일로 폴백(있으면). 없으면 빈 안내.
- 결과: 오래된 링크를 열어도 500 없이, 인박스 첫 메일이 매끄럽게 뜬다.

## 데이터 흐름

```
[15분 타이머] email_refresh.py
  → gmail.fetch_inbox → email_cache.save (기존)
  → NEW: 캐시에서 "답장 필요" & 미답장 & 초안없음 상위 N개 선별
  → NEW: 각 메일 generate_reply_draft(persona 적용) → save_draft (미발송)

[브라우저] /email
  → build_email_view → 큐에 has_draft 배지 / 우측 패널에 미발송 초안
  → WJ 가 수정 + "승인 후 발송" (기존 발송 경로, 불변식)

[설정] /api/email/persona (GET/POST) → email_persona.json
```

## 불변식 (절대 안 깨는 것)

- WJ 승인 없이는 어떤 메일도 발송되지 않는다. 자동 초안은 "미발송" 상태로만 저장.
- Gmail 원본은 건드리지 않는다(숨김/제외는 로컬 화면 처리만, 기존과 동일).
- 자동 생성은 무과금(codex exec) 경로만 사용.

## 설정값 (기본)

- 자동 초안 상한 N = 5 (환경변수 `WJ_AUTODRAFT_MAX` 로 조정 가능, 0 이면 자동 끔)
- 말투 프로필 비어 있으면 기존 톤 기본값으로 동작(하위호환)

## 테스트

- `email_persona.load/save` 단위 테스트(빈 값/긴 텍스트/파일 없음).
- `generate_reply_draft` 가 프로필 텍스트를 프롬프트에 포함하는지(프로필 있음/없음 분기).
- `generate_reply_draft` 가 codex exec 를 호출하는지(엔진 교체 확인). codex 호출은 mock.
- 자동 선별 로직 단위 테스트: 자동발신 제외, 답장한 것 제외, 초안 있는 것 제외, 상한 N.
- `email_view` 폴백: 없는 id → 큐 첫 메일 / 큐 비면 빈 안내 (500 안 남).
- codex exec 호출은 테스트에서 mock(실제 호출 금지).

## 미해결 / 리뷰에서 정할 것

- 상한 N 기본값 5가 적절한지(하루 답장필요 메일 양에 따라).
- 자동 초안 대상에 P2(낮은 우선순위)도 포함할지, P0/P1만 할지.
- 말투 프로필 편집창을 /email 탭 어디에 둘지(상단 설정 영역 vs 별도 탭).
