"""앱 전역 설정 — 모든 경로/옵션은 여기 한 곳에서.

규칙:
- WJ_MODE 필수 (prod | demo | dev).
- prod 면 데이터 경로/비밀번호 모두 명시 필요, 누락/잘못된 값이면 부팅 실패.
- demo 면 repo 안의 examples/ 가 기본값.
- dev 는 demo 와 동일하나 reload 등 개발 편의 ON.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
EXAMPLES_ROOT = REPO_ROOT / "examples"

_VALID_MODES = {"prod", "demo", "dev"}


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _env_path(name: str, default: Path) -> Path:
    v = _env(name)
    return Path(v).expanduser() if v else default


@dataclass(frozen=True)
class AppSettings:
    mode: str
    data_path: Path
    thinking_root: Path
    uploads_dir: Path
    claude_bin: str
    term_label_file: Path
    enable_terminal: bool
    enable_chat: bool
    auth_password: str
    host: str
    port: int

    @property
    def is_prod(self) -> bool:
        return self.mode == "prod"

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def mode_badge(self) -> str:
        return {"prod": "PROD", "demo": "DEMO", "dev": "DEV"}.get(self.mode, "?")


def _die(msg: str) -> None:
    print(f"[wj-dashboard settings] {msg}", file=sys.stderr)
    raise SystemExit(2)


def load() -> AppSettings:
    mode = (_env("WJ_MODE") or "").strip().lower()
    if mode not in _VALID_MODES:
        _die(
            f"WJ_MODE must be one of {sorted(_VALID_MODES)} (got {mode!r}). "
            "Set it explicitly in env/EnvironmentFile."
        )

    demo_defaults = {
        "data_path": EXAMPLES_ROOT / "dashboard.yaml",
        "thinking_root": EXAMPLES_ROOT / "thinking",
        "uploads_dir": EXAMPLES_ROOT / "thinking" / "uploads",
        "term_label_file": EXAMPLES_ROOT / "term-labels.json",
    }

    if mode == "prod":
        data_path = _env_path("WJ_DATA_PATH", Path("/nonexistent/dashboard.yaml"))
        thinking_root = _env_path("WJ_THINKING_ROOT", Path("/nonexistent/thinking"))
        uploads_dir = _env_path("WJ_UPLOADS_DIR", thinking_root / "uploads")
        term_label_file = _env_path(
            "WJ_TERM_LABEL_FILE",
            Path("~/.config/wj-dashboard/term-labels.json").expanduser(),
        )
    else:
        data_path = _env_path("WJ_DATA_PATH", demo_defaults["data_path"])
        thinking_root = _env_path("WJ_THINKING_ROOT", demo_defaults["thinking_root"])
        uploads_dir = _env_path("WJ_UPLOADS_DIR", demo_defaults["uploads_dir"])
        term_label_file = _env_path("WJ_TERM_LABEL_FILE", demo_defaults["term_label_file"])

    claude_bin = _env("WJ_CLAUDE_BIN", "claude") or "claude"
    enable_terminal = _env_bool("WJ_ENABLE_TERMINAL", default=False)
    enable_chat = _env_bool("WJ_ENABLE_CHAT", default=(mode != "demo"))
    auth_password = _env("WJ_PASSWORD", "") or ""
    host = _env("WJ_HOST", "127.0.0.1") or "127.0.0.1"
    try:
        port = int(_env("WJ_PORT", "3004") or "3004")
    except ValueError:
        _die("WJ_PORT must be an integer.")

    # fail-fast 검증
    if mode == "prod":
        if not auth_password:
            _die("prod mode requires WJ_PASSWORD (non-empty).")
        for label, p in [
            ("WJ_DATA_PATH", data_path),
            ("WJ_THINKING_ROOT", thinking_root),
        ]:
            if EXAMPLES_ROOT in p.resolve().parents or p.resolve() == EXAMPLES_ROOT:
                _die(f"prod mode must not point {label} into repo's examples/ ({p}).")
            if not p.exists():
                _die(f"{label} does not exist: {p}")
        if enable_terminal and not Path(claude_bin).exists() and "/" in claude_bin:
            _die(f"WJ_CLAUDE_BIN does not exist: {claude_bin}")

    if mode == "demo":
        # demo 는 예제 데이터 존재 보장
        for label, p in [
            ("data_path", data_path),
            ("thinking_root", thinking_root),
        ]:
            if not p.exists():
                _die(f"demo mode: {label} missing — {p}. Run examples/ generator first.")

    return AppSettings(
        mode=mode,
        data_path=data_path,
        thinking_root=thinking_root,
        uploads_dir=uploads_dir,
        claude_bin=claude_bin,
        term_label_file=term_label_file,
        enable_terminal=enable_terminal,
        enable_chat=enable_chat,
        auth_password=auth_password,
        host=host,
        port=port,
    )


SETTINGS: AppSettings = load()
