---
title: RSS reader
slug: rss-reader
category_primary: 사이드
category_secondary: [Rust]
type: project
status: active
tags: [Rust, TUI, 사이드]
created: 2026-05-09
updated: 2026-05-17
links: []
aliases: []
---

## 지금 생각
Rust 공부 + 매일 쓸 도구 = RSS reader 직접 만들기. feed-rs 로 파서 toy 만들어 보고 (100줄), 지금은 SQLite 로 read/unread 저장 붙이는 중. 다음은 ratatui 로 TUI. 욕심 부리지 말고 vim 키바인딩 j/k/Enter 만 먼저.

## 결정·방향
- 2026-05-12 | 결정: 마이그레이션은 sqlx 까지 안 가고 raw rusqlite 으로 단순하게 | 근거: 도구 학습이 아닌 Rust 학습이 목적 | 상태: 유효
- 2026-05-13 | 결정: TUI 우선, 웹은 한참 뒤 | 근거: TUI 가 Rust 답고, 매일 터미널에서 씀 | 상태: 유효

## 할 일
- [ ] SQLite read/unread 마무리 (5/26)
- [ ] ratatui 리스트 뷰 (6월 중)
- [ ] feed 추가/삭제 명령어
