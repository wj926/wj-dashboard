"""답장 초안용 말투 프로필 + 서명.

저장 위치: repo 밖 ~/.config/wj-dashboard/email_persona.json (.gitignore, email_rules 와 동일 패턴).

- persona: 자유 텍스트(내 말투/원칙). 비어 있지 않으면 모든 초안 system 프롬프트에 주입.
- signature: 초안 마지막 줄 서명. 비우면 서명을 붙이지 않는다.
- 절대 raise 하지 않는다. 파일이 없거나 깨지면 기본값으로 동작(하위호환).
"""
from __future__ import annotations

import json as _json
import os
from pathlib import Path

_PATH = Path(
    os.environ.get(
        "WJ_EMAIL_PERSONA_PATH",
        str(Path.home() / ".config" / "wj-dashboard" / "email_persona.json"),
    )
)

# persona 비어 있으면 기존 톤 기본값으로 동작. signature 는 합리적 기본 하나.
_DEFAULT = {"persona": "", "signature": "이우진 드림"}


def load() -> dict:
    try:
        d = _json.loads(_PATH.read_text(encoding="utf-8"))
        sig = d.get("signature")
        return {
            "persona": str(d.get("persona") or ""),
            "signature": str(sig if sig is not None else _DEFAULT["signature"]),
        }
    except Exception:
        return dict(_DEFAULT)


def save(persona: str, signature: str) -> bool:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "persona": (persona or "").strip(),
            "signature": (signature or "").strip(),
        }
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, _PATH)
        try:
            os.chmod(_PATH, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False
