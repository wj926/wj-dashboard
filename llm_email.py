"""답장 초안 생성 LLM (S3a). 실 발송/캘린더는 여기서 하지 않는다.

원칙:
- 인박스 로딩만으로는 호출되지 않는다. 오직 '초안 생성' 버튼(라우트)에서만 호출.
- 과금 없는 경로: claude CLI subprocess(--print). compute.claude_chat 패턴 계승.
- 절대 raise 하지 않는다. 실패 시 {"ok": False, "error": ...}.
- 생성은 발송이 아니다. 이 모듈은 텍스트만 만든다. Gmail 에 아무 흔적도 남기지 않는다.

EmailLLM Protocol(email_services) 호환: detect_events / generate_reply_draft.
detect_events 는 S4 산출물이라 여기서는 안전 stub(빈 후보) 으로 둔다.
"""
from __future__ import annotations

import json as _json
import os
import subprocess as _sp
from email.utils import parseaddr

TONES = ["정중·간결", "따뜻하게", "간단 수락"]


def _claude_bin() -> str:
    try:
        from settings import SETTINGS
        return SETTINGS.claude_bin or "claude"
    except Exception:
        return os.environ.get("WJ_CLAUDE_BIN", "claude") or "claude"


def _codex_bin() -> str:
    return os.environ.get("WJ_CODEX_BIN") or os.environ.get("CODEX_BIN") or "codex"


def _run_codex(system: str, user: str, timeout: int = 90) -> str:
    """codex exec 비대화형 호출(무과금, ChatGPT 구독). 마지막 메시지를 파일로 받아 반환.

    paper-revision-app 의 _run_codex_cli 패턴을 따른다. 실패/빈 응답이면 ''.
    """
    import tempfile as _tf
    from pathlib import Path as _Path

    prompt = "[SYSTEM]\n" + system + "\n\n[USER]\n" + user
    env = {
        "HOME": os.environ.get("HOME", "/home/dami"),
        "PATH": (
            os.environ.get("PATH", "/usr/bin:/bin")
            + ":/home/dami/.local/bin:/home/dami/.nvm/versions/node/v24.14.1/bin"
        ),
    }
    with _tf.NamedTemporaryFile(prefix="wj-email-codex-", suffix=".txt", delete=False) as f:
        out_path = _Path(f.name)
    try:
        cmd = [
            _codex_bin(), "exec",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "-C", "/tmp",
            "--output-last-message", str(out_path),
            "-",
        ]
        model = os.environ.get("WJ_CODEX_MODEL") or os.environ.get("CODEX_MODEL")
        if model:
            cmd[2:2] = ["--model", model]
        _sp.run(cmd, input=prompt, capture_output=True, text=True,
                timeout=timeout, cwd="/tmp", env=env)
        return out_path.read_text(encoding="utf-8", errors="replace").strip()
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass


def _signature() -> str:
    return os.environ.get("WJ_EMAIL_SIGNATURE", "이우진")


def _reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    low = s.lower()
    if low.startswith("re:"):
        return s
    return "Re: " + s if s else "Re:"


def _incoming_text(message: dict) -> str:
    """정규화 메시지에서 LLM 입력용 본문 텍스트. html 만 있으면 태그 대충 제거."""
    body = (message or {}).get("body") or {}
    text = body.get("text") or ""
    if text.strip():
        return text
    html = body.get("html_sanitized") or ""
    if html:
        import re
        return re.sub(r"<[^>]+>", " ", html)
    return (message or {}).get("snippet") or ""


def _parse_candidates(raw: str) -> list:
    """LLM 출력에서 JSON 배열을 안전 파싱. 후보 dict 정규화. 실패 시 []."""
    try:
        s = (raw or "").strip()
        if s.startswith("```"):
            s = s.strip("`")
            s = s.split("\n", 1)[-1] if "\n" in s else s
        # 배열 부분만 추출(앞뒤 잡설 방어)
        i, j = s.find("["), s.rfind("]")
        if i == -1 or j == -1 or j < i:
            return []
        arr = _json.loads(s[i:j + 1])
        if not isinstance(arr, list):
            return []
        out = []
        for c in arr:
            if not isinstance(c, dict):
                continue
            title = (c.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "title": title[:60],
                "date_label": (c.get("date_label") or "").strip()[:24],
                "time_label": (c.get("time_label") or "").strip()[:24],
                "place": (c.get("place") or "").strip()[:60],
                "start_iso": (c.get("start_iso") or "").strip()[:40],
                "end_iso": (c.get("end_iso") or "").strip()[:40],
                "source": (c.get("source") or "").strip()[:240],
                "confidence": c.get("confidence") if isinstance(c.get("confidence"), (int, float)) else 0.5,
            })
        return out[:6]
    except Exception:
        return []


_TONE_HINT = {
    "정중·간결": "정중하고 간결하게. 군더더기 없이 핵심만.",
    "따뜻하게": "따뜻하고 친근하게, 그래도 예의 있게.",
    "간단 수락": "짧게 수락/확인 위주로. 1~3문장.",
}


def _parse_rule_json(raw: str) -> dict:
    """LLM 출력에서 규칙 JSON 파싱. 실패 시 {}."""
    try:
        s = (raw or "").strip()
        if s.startswith("```"):
            s = s.strip("`")
            s = s.split("\n", 1)[-1] if "\n" in s else s
        i, j = s.find("{"), s.rfind("}")
        if i == -1 or j == -1 or j < i:
            return {}
        d = _json.loads(s[i:j + 1])
        if not isinstance(d, dict):
            return {}
        eff = d.get("effect")
        m = d.get("match") or {}
        return {
            "label": (d.get("label") or "").strip()[:30],
            "effect": eff if eff in ("priority_up", "priority_down", "receipt", "later") else "priority_up",
            "match": {
                "from": [str(x).strip() for x in (m.get("from") or []) if str(x).strip()][:8],
                "subject_kw": [str(x).strip() for x in (m.get("subject_kw") or []) if str(x).strip()][:8],
            },
        }
    except Exception:
        return {}


class RealEmailLLM:
    """claude CLI 로 답장 본문만 생성. 발송 안 함."""

    def parse_rule(self, text: str) -> dict:
        """자연어 처리규칙 -> {label, effect, match{from, subject_kw}}. 실패 시 {}."""
        try:
            text = (text or "").strip()
            if not text:
                return {}
            system = (
                "당신은 한국어 이메일 처리 규칙을 구조화하는 도우미다. JSON 객체만 출력한다"
                "(설명/코드블록 금지). 사용자가 말한 규칙을 아래 형식으로 해석한다.\n"
                '{"label":"짧은 이름","effect":"priority_up|priority_down|receipt|later",'
                '"match":{"from":["발신자 이름/도메인 일부"],"subject_kw":["제목 키워드"]}}\n'
                "effect 의미: priority_up=큐 위로/중요, priority_down=낮춤/덜 중요, "
                "receipt=영수증함 후보로, later=나중에. "
                "발신자 조건이면 match.from 에 그 사람/조직 고유 이름이나 도메인 일부만 넣는다"
                "(예: 두나무, dunamu). 주의: 사용자가 특정 사람/조직을 지칭하면 그 고유 이름만 넣고, "
                "메일을 중계하는 플랫폼/시스템/서비스 이름(예: 이클래스, eclass, 네이버, gmail, mailer, "
                "알림, notification)은 절대 match.from 에 넣지 않는다(무관한 메일까지 매칭됨). "
                "제목/내용 조건이면 match.subject_kw 에 키워드를 넣는다. 둘 다 없으면 빈 배열."
            )
            result = _sp.run(
                [_claude_bin(), "--print", "--model", "claude-opus-4-7", "--system-prompt", system],
                input=text, capture_output=True, text=True, timeout=40,
            )
            if result.returncode != 0:
                return {}
            return _parse_rule_json(result.stdout)
        except Exception:
            return {}

    def detect_events(self, message: dict, now_kst) -> dict:
        """본문에서 일정/마감 후보를 뽑는다. 캘린더엔 쓰지 않는다(S4b 가 등록 담당).

        1차 규칙으로 날짜 문장만 추리고, 그게 있을 때만 LLM 으로 구조화한다.
        LLM 실패/파싱오류여도 규칙 문장만으로 최소 후보를 만들어 돌려준다.
        """
        try:
            import email_events
            headers = (message or {}).get("headers") or {}
            subject = headers.get("subject") or ""
            text = _incoming_text(message)
            sents = email_events.extract_date_sentences(text)
            if not sents and not email_events.has_date_signal(subject):
                return {"ok": True, "candidates": []}

            today = ""
            dow = ""
            try:
                today = now_kst.strftime("%Y-%m-%d")
                dow = ["월", "화", "수", "목", "금", "토", "일"][now_kst.weekday()]
            except Exception:
                pass

            src_block = "\n".join(f"- {s['source']}" for s in sents) or "(본문 날짜 문장 없음, 제목 참고)"
            system = (
                "당신은 한국어 이메일에서 캘린더에 넣을 만한 일정/마감 후보를 뽑는 도우미다. "
                "반드시 JSON 배열만 출력한다(설명/코드블록/머리말 금지). "
                "날짜·시간이 실제로 특정되는 것만 후보로 만든다. 없으면 빈 배열 [] 만 출력. "
                "상대 표현(다음 주 월요일, 내일 등)은 주어진 오늘 날짜 기준으로 환산한다. "
                "지어내지 말고, 근거가 된 본문 문장을 source 에 원문 그대로 넣는다."
            )
            schema = (
                '[{"title":"짧은 제목","date_label":"6/9(월)","time_label":"14:00 또는 빈문자열",'
                '"place":"장소 또는 빈문자열","start_iso":"2026-06-09T14:00:00+09:00",'
                '"end_iso":"2026-06-09T14:30:00+09:00","source":"근거 본문 문장","confidence":0.0}]'
            )
            user = (
                f"오늘(KST): {today} ({dow})\n"
                f"[제목] {subject}\n"
                f"[감지된 날짜 문장]\n{src_block}\n\n"
                f"위에서 일정/마감 후보를 아래 JSON 배열 형식으로만 출력하라. 시간 불명확하면 "
                f"time_label=\"\" 이고 start_iso 는 그 날 09:00:00+09:00 로.\n"
                f"형식: {schema}"
            )
            result = _sp.run(
                [_claude_bin(), "--print", "--model", "claude-opus-4-7",
                 "--system-prompt", system],
                input=user, capture_output=True, text=True, timeout=60,
            )
            cands = []
            if result.returncode == 0:
                cands = _parse_candidates(result.stdout)
            if not cands:
                # LLM 실패/빈 결과 -> 규칙 문장만으로 최소 후보(등록 불가, 표시용).
                cands = [
                    {"title": (subject or "일정 후보")[:40], "date_label": "", "time_label": "",
                     "place": "", "start_iso": "", "end_iso": "", "source": s["source"],
                     "confidence": 0.3}
                    for s in sents[:3]
                ]
            return {"ok": True, "candidates": cands}
        except Exception as e:
            return {"ok": False, "error": type(e).__name__, "candidates": []}

    def generate_reply_draft(self, message: dict, thread: dict, tone: str) -> dict:
        try:
            headers = (message or {}).get("headers") or {}
            from_hdr = headers.get("from") or ""
            from_name, from_email = parseaddr(from_hdr)
            to_display = (f"{from_name} <{from_email}>" if from_name else from_email) or from_hdr
            subject = headers.get("subject") or ""
            incoming = _incoming_text(message)
            tone = tone if tone in TONES else TONES[0]

            import email_persona
            prof = email_persona.load()
            persona_txt = (prof.get("persona") or "").strip()
            sig = (prof.get("signature") or "").strip()

            parts = [
                "당신은 한국어 이메일 답장 초안을 작성하는 비서다.",
                "받은 메일에 대한 '답장 본문'만 출력한다.",
                "머리말, 설명, 코드블록, 따옴표 묶음 없이 본문 텍스트만 낸다.",
                "없는 약속이나 사실을 지어내지 않는다. 모르면 정중히 확인을 요청한다.",
                "기본적으로 간결하게 쓴다. 군더더기 없이 핵심만, 짧게.",
            ]
            if persona_txt:
                parts.append("작성자의 말투/원칙(반드시 반영): " + persona_txt)
            else:
                parts.append("톤: " + _TONE_HINT.get(tone, ""))
            if sig:
                parts.append(f"마지막 줄 서명은 '{sig}' 로 끝낸다.")
            system = " ".join(parts)
            user = (
                f"[받는 사람] {to_display}\n"
                f"[제목] {subject}\n\n"
                f"[받은 메일 본문]\n{incoming[:4000]}\n\n"
                f"위 메일에 대한 답장 본문을 한국어로 작성하라."
            )

            text = (_run_codex(system, user, timeout=90) or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                text = text.split("\n", 1)[-1] if "\n" in text else text
            text = text.strip()
            if not text:
                return {"ok": False, "error": "빈 응답"}

            return {
                "ok": True,
                "draft": {
                    "status": "unsent",
                    "to": to_display,
                    "subject": _reply_subject(subject),
                    "tone": tone,
                    "tones": list(TONES),
                    "text": text,
                },
            }
        except _sp.TimeoutExpired:
            return {"ok": False, "error": "codex timeout (90s)"}
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}
