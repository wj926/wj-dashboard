#!/usr/bin/env python3
"""인박스를 배치로 받아 로컬 캐시에 쌓는다(systemd 타이머 또는 수동).

/email 은 이 캐시에서 바로 읽어 즉시 렌더한다. 토큰 만료/네트워크 실패면
기존 캐시를 유지하고 종료(앱은 계속 동작).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gmail        # noqa: E402
import email_cache  # noqa: E402
import email_filters  # noqa: E402


def main() -> int:
    q = email_filters.build_query()
    msgs = gmail.fetch_inbox(query=q)
    if not msgs:
        print("[refresh] 0건 수신 (토큰 만료/네트워크?). 기존 캐시 유지.")
        return 1
    # 내가 이미 답장한 스레드 표시(처리함 판단용). SENT 를 읽어 thread 별 내 마지막 발송 시각과 비교.
    sent_map = gmail.fetch_sent_thread_dates()
    replied = 0
    for m in msgs:
        tid = m.get("thread_id")
        my_ts = sent_map.get(tid, 0)
        m["i_replied"] = bool(tid and my_ts and my_ts >= (m.get("internal_ts") or 0))
        if m["i_replied"]:
            replied += 1
    print(f"[refresh] 내가 답장한(처리함) {replied}건 표시")
    ok = email_cache.save(msgs)
    print(f"[refresh] {len(msgs)}건 캐시 저장 ok={ok} -> {email_cache.CACHE_PATH}")
    print(f"[refresh] query: {q}")

    # 캐시 저장 성공 시, 답장 필요한 메일에 미발송 초안을 미리 생성(무과금 codex, 상한 N).
    # 어떤 실패도 refresh 본체를 죽이지 않는다.
    if ok:
        try:
            import email_autodraft
            email_autodraft.run(msgs)
        except Exception as e:
            print(f"[refresh] autodraft 건너뜀(무시): {type(e).__name__}: {e}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
