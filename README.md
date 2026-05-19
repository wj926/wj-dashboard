# wj-dashboard

내 머릿속 잡생각과 할 일을 한 페이지에서 굴리는 개인 dashboard.

Flask 백엔드 + Bento 레이아웃 + 자연어 task 입력 + 마크다운 위키. 셀프호스팅 (Tailscale / Cloudflare Tunnel) 을 가정한다.

## 왜 만들었나

- task 관리 도구는 많지만 "그때그때 머릿속에 떠오른 잡생각" 을 받아주는 곳이 따로 있어야 한다고 느낌.
- 잡생각 → 마크다운 위키 (`thinking/`), 할 일 → yaml (`dashboard.yaml`). 둘 다 사람이 읽고 git 으로 버전관리 가능한 plain text.
- 한 화면에서 마감 임박 task, 진행 중 프로젝트, 최근 raw 일지, 위키 페이지가 다 보임.
- 자연어로 "내일 저녁까지 결제 리팩토링 카나리 올리기" 같이 던지면 Claude CLI 가 yaml 에 task 등록.

## 데모

`WJ_MODE=demo` 로 띄우면 동봉된 페르소나 (30대 백엔드 개발자) 의 1~2주치 데이터로 바로 돌아간다. 회사 일 (결제 리팩토링) / 사이드 (Rust RSS reader, 홈랩) / 잡생활 (러닝, 부모님 검진) 이 섞여있어서 도구 감을 잡기 좋다.

## 빠른 시작 (Demo)

```bash
git clone https://github.com/<user>/wj-dashboard.git
cd wj-dashboard
pip install -r requirements.txt

WJ_MODE=demo python app.py
# → http://127.0.0.1:3004
```

데모 모드는 인증 없이 열린다 (공개 데이터). 종료는 Ctrl+C.

## 본인 데이터로 운영 (Prod)

1. 데이터 위치 정하기 (repo 밖에 두는 걸 강력 권장)

   ```
   ~/data/wj-dashboard/
     dashboard.yaml      # task / project
     thinking/
       raw/YYYY-MM-DD.md # 일지
       wiki/*/*.md       # 정리된 페이지
       uploads/          # 첨부
   ```

   초기 구조는 `examples/` 를 복사해서 비우는 게 빠르다.

2. `.env` 작성

   ```bash
   cp .env.example .env
   # WJ_MODE=prod
   # WJ_DATA_PATH=~/data/wj-dashboard/dashboard.yaml
   # WJ_THINKING_ROOT=~/data/wj-dashboard/thinking
   # WJ_PASSWORD=<강한-비번>
   ```

3. 실행

   ```bash
   set -a; source .env; set +a
   python app.py
   ```

`prod` 모드는 필수 환경변수가 빠지면 부팅을 거부한다 (fail-fast). 비밀번호 빈 값, `examples/` 가리키기 등도 거부.

## systemd + Cloudflare Tunnel (선택)

내가 운영하는 방식. 외부 도메인 + HTTPS 가 공짜로 붙는다.

`~/.config/systemd/user/wj-dashboard.service`:

```ini
[Unit]
Description=wj-dashboard
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/wj-dashboard
EnvironmentFile=/home/<user>/.config/wj-dashboard/env
ExecStart=/usr/bin/python /path/to/wj-dashboard/app.py
Restart=on-failure
KillMode=process

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now wj-dashboard
```

Cloudflare Tunnel 은 `localhost:3004` 을 외부 서브도메인에 매핑. 자세한 건 [Cloudflare Tunnel docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

## 화면 구성

- **Today / Inbox** — 오늘·내일 마감 + project 안 잡힌 task
- **프로젝트별** — 활성 project 카드, 마감 D-day 표시
- **달력 grid** — 한 달 task 분포
- **thinking 위키** — 카테고리별 페이지 트리, 결정 트래커, 타임라인 합본, 백링크
- **자연어 입력** — 한 줄 box, Claude CLI 호출로 task 자동 등록
- **(opt) 터미널** — claude CLI 를 WebSocket PTY 로 띄움. `WJ_ENABLE_TERMINAL=true` 일 때만.

## 데이터 포맷

### `dashboard.yaml`

```yaml
schema: dashboard.v1
projects:
- id: prj_xxx
  title: ...
  status: active | done | paused
  due_at: 'YYYY-MM-DD' | null
  tags: [...]
  source_thinking: thinking/wiki/.../foo.md   # optional
tasks:
- id: tsk_xxx
  project_id: prj_xxx | null
  title: ...
  status: todo | doing | done
  due_at: 'YYYY-MM-DD' | null
  done_at: 'YYYY-MM-DD' | null
  note: ...
```

### `thinking/wiki/<카테고리>/<slug>.md`

frontmatter + 본문 섹션:

```markdown
---
title: ...
slug: ...
category_primary: 일 | 사이드 | 학습 | ...
type: project | note | idea
status: active | parked
tags: [...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

## 지금 생각
...

## 결정·방향
- YYYY-MM-DD | 결정: ... | 근거: ... | 상태: 유효
...

## 할 일
- [ ] ...

## 타임라인
- YYYY-MM-DD: ...
```

### `thinking/raw/YYYY-MM-DD.md`

날짜별 잡 일지. 자연어로 chat 으로 던진 게 timestamp 단위로 append 된다.

## 환경변수

| 키 | 필수 | 기본 | 설명 |
|---|---|---|---|
| `WJ_MODE` | yes | — | `prod` / `demo` / `dev` |
| `WJ_PASSWORD` | prod | — | Basic Auth. 빈 값이면 prod 부팅 거부 |
| `WJ_DATA_PATH` | prod | `examples/dashboard.yaml` | 메인 yaml |
| `WJ_THINKING_ROOT` | prod | `examples/thinking` | 위키 루트 |
| `WJ_UPLOADS_DIR` | no | `<thinking>/uploads` | 첨부 폴더 |
| `WJ_TERM_LABEL_FILE` | no | `examples/term-labels.json` | 터미널 라벨 영속 |
| `WJ_CLAUDE_BIN` | chat/term | `claude` | Claude CLI 경로 |
| `WJ_ENABLE_TERMINAL` | no | `false` | WebSocket PTY 켜기 (보안 주의) |
| `WJ_ENABLE_CHAT` | no | `true` (`false` on demo) | 자연어 task 입력 |
| `WJ_HOST` | no | `127.0.0.1` | bind 호스트 |
| `WJ_PORT` | no | `3004` | bind 포트 |

## 보안 메모

- `WJ_ENABLE_TERMINAL=true` 면 인증된 사용자가 서버 셸을 실질적으로 얻는다. **반드시 강한 `WJ_PASSWORD` + 신뢰된 사용자만**. 외부 공개 X.
- demo 모드는 인증을 끈다. 데모 데이터에 개인정보 절대 넣지 말 것.
- 본인 데이터는 repo 바깥에 두기. `.gitignore` 가 `data/`, `*.bak` 만 막아주므로 repo 안에 데이터를 두면 실수로 커밋될 수 있음.

## 한계

- 1인 사용 가정. 멀티유저 / RBAC / 협업 기능 없음.
- yaml/마크다운 atomic write 정도만 있고 동시 편집 잠금 없음 (혼자 쓰니까).
- thinking 위키는 매 요청 다시 파싱한다. 페이지 수백 개 넘어가면 캐시 도입 필요.
- 자연어 task 입력은 `claude` CLI 의존. 별도 LLM 백엔드 추상화 없음.

## 라이선스

MIT. `LICENSE` 참고.

## 이름

운영자의 닉네임을 그대로 붙인 도구라 `wj-dashboard`. fork 해서 본인 이름으로 부르면 됨.
