"""PTY + WebSocket terminal — claude CLI 를 tmux 세션 안에서 실행.

claude-notebook 의 tmux-wrap 패턴을 차용. PTY 죽음/WS 끊김/서버 재시작에서
claude 프로세스와 scrollback 이 살아남는다.

흐름:
  - 각 sid → tmux session "wj-<sid>" (detached, claude CLI 가 첫 프로세스)
  - WS 연결 → `tmux attach -t wj-<sid>` 를 PtyProcess 로 띄움
  - WS 끊김 → PtyProcess 종료, tmux session 은 보존 (claude 계속 살아있음)
  - 재연결 → 새 PtyProcess 가 attach, tmux 가 마지막 화면 replay
  - 명시적 kill 만 세션 종료

라벨 (사용자가 본 이름) 은 sid 와 분리해 JSON 으로 보관 — 한글/특수문자 OK.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shlex
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import ptyprocess

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
if not log.handlers:
    # systemd journal 로 INFO 흘려보내기 위해 stderr 핸들러 명시
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    log.addHandler(_h)
    log.propagate = False

# ===== 설정 =====
from settings import SETTINGS

TMUX_PREFIX = "wj-"
LABEL_FILE = SETTINGS.term_label_file
DEFAULT_CMD = SETTINGS.claude_bin
DEFAULT_CWD = str(SETTINGS.thinking_root)
SID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# ===== 라벨 영속 =====
_labels_lock = threading.Lock()


def _load_labels() -> dict:
    if not LABEL_FILE.exists():
        return {}
    try:
        return json.loads(LABEL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_labels(labels: dict) -> None:
    LABEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LABEL_FILE.with_suffix(".json.tmp")
    data = json.dumps(labels, ensure_ascii=False, indent=2)
    # 원자적 + 내구성: tmp write+fsync → rename → 디렉터리 fsync
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    tmp.replace(LABEL_FILE)
    try:
        dfd = os.open(str(LABEL_FILE.parent), os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def get_label(sid: str) -> str:
    with _labels_lock:
        return _load_labels().get(sid) or _default_label(sid)


def set_label(sid: str, label: str) -> None:
    label = (label or "").strip() or sid
    with _labels_lock:
        labels = _load_labels()
        labels[sid] = label
        _save_labels(labels)


def remove_label(sid: str) -> None:
    with _labels_lock:
        labels = _load_labels()
        if labels.pop(sid, None) is not None:
            _save_labels(labels)


def _default_label(sid: str) -> str:
    if sid == "global":
        return "전역"
    if sid.startswith("ctx_"):
        return "📄 " + sid[4:].replace("_", " ")
    return sid


# ===== sid / tmux 유틸 =====
def valid_sid(sid: str) -> bool:
    return bool(sid) and bool(SID_RE.match(sid))


def _tmux_name(sid: str) -> str:
    return TMUX_PREFIX + sid


def _clean_env() -> dict:
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    return env


def _tmux(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux"] + args,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=_clean_env(),
    )


def _has_session(sess: str) -> bool:
    try:
        return _tmux(["has-session", "-t", sess]).returncode == 0
    except Exception:
        return False


def _ensure_tmux_session(sid: str, cwd: str = DEFAULT_CWD) -> bool:
    """tmux session 이 없으면 claude CLI 와 함께 detached 생성."""
    sess = _tmux_name(sid)
    if _has_session(sess):
        return True
    try:
        cp = _tmux([
            "new-session", "-d", "-s", sess, "-c", cwd,
            "-x", "200", "-y", "50", DEFAULT_CMD,
        ])
        if cp.returncode != 0:
            log.warning("tmux new-session %s failed: %s", sess,
                        cp.stderr.decode(errors="replace")[:200])
            return False
    except Exception as e:
        log.warning("tmux new-session %s raised: %s", sess, e)
        return False
    # 옵션 — claude-notebook 과 동일. mouse on 은 제거 (xterm 의 wheel/touch 캡처와 충돌)
    for opt in (["history-limit", "50000"], ["status", "off"]):
        try:
            _tmux(["set-option", "-t", sess, *opt])
        except Exception:
            pass
    log.info("tmux session created: %s", sess)
    return True


def list_tmux_sessions() -> list[dict]:
    """현재 살아있는 wj-* tmux 세션 목록."""
    try:
        cp = _tmux([
            "list-sessions", "-F",
            "#{session_name}|#{session_created}|#{session_activity}|#{session_attached}",
        ])
    except Exception:
        return []
    if cp.returncode != 0:
        return []
    out = []
    for line in cp.stdout.decode(errors="replace").splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name, created, activity, attached = parts[:4]
        if not name.startswith(TMUX_PREFIX):
            continue
        sid = name[len(TMUX_PREFIX):]
        out.append({
            "sid": sid,
            "label": get_label(sid),
            "created": int(created or 0),
            "last_activity": int(activity or 0),
            "attached_count": int(attached or 0),
            "alive": True,
        })
    out.sort(key=lambda s: s["last_activity"], reverse=True)
    return out


def kill_tmux_session(sid: str) -> bool:
    if not valid_sid(sid):
        return False
    cp = _tmux(["kill-session", "-t", _tmux_name(sid)])
    remove_label(sid)
    log.info("tmux session killed: %s (rc=%s)", sid, cp.returncode)
    return cp.returncode == 0


def create_session(label: str = "", sid: str | None = None) -> dict:
    """새 세션 생성. sid 미지정 시 자동 생성. 결과 dict 반환.
    tmux 성공 후에만 label 저장 — 실패 시 라벨 찌꺼기 남지 않게.
    """
    if sid:
        if not valid_sid(sid):
            return {"ok": False, "error": "invalid sid format"}
    else:
        sid = "t-" + str(int(time.time() * 1000))
    ok = _ensure_tmux_session(sid)
    if not ok:
        return {"ok": False, "error": "tmux session 생성 실패"}
    if label:
        set_label(sid, label)
    return {"ok": True, "sid": sid, "label": get_label(sid)}


# ===== WS 핸들러 (tmux attach 방식 — claude-notebook 패턴) =====
# 각 sid 는 tmux session "wj-<sid>" 안에서 claude CLI 를 보유. WS 가 끊겨도 tmux 가
# 세션을 유지 → 재연결 시 tmux 가 마지막 화면을 자동 replay (환영 배너 중복 X).
# WS 마다 PtyProcess 는 `tmux attach-session` 짧은 수명 — 여러 클라이언트 동시 접속도
# tmux 가 멀티-클라이언트로 자연스럽게 처리.
def handle_ws(ws, sid: str = "global") -> None:
    """flask-sock 핸들러. tmux attach 로 영속 claude 세션 연결."""
    if not valid_sid(sid):
        try:
            ws.send(f"\r\n[invalid sid: {sid!r}]\r\n")
        except Exception:
            pass
        return

    if not _ensure_tmux_session(sid):
        try:
            ws.send(f"\r\n[tmux 세션 생성 실패: {sid}]\r\n")
        except Exception:
            pass
        return

    sess = _tmux_name(sid)
    env = _clean_env()
    env["TERM"] = "xterm-256color"
    env["LANG"] = env.get("LANG", "en_US.UTF-8")
    try:
        p = ptyprocess.PtyProcess.spawn(
            ["tmux", "attach-session", "-t", sess],
            env=env, cwd=DEFAULT_CWD, dimensions=(24, 80),
        )
    except Exception as e:
        log.warning("tmux attach spawn failed for %s: %s", sid, e)
        try:
            ws.send(f"\r\n[tmux attach 실패: {e}]\r\n")
        except Exception:
            pass
        return

    log.info("ws attached via tmux: sid=%s sess=%s", sid, sess)
    alive = threading.Event()
    alive.set()

    def reader():
        try:
            while alive.is_set():
                try:
                    chunk = p.read(4096)
                except (EOFError, OSError):
                    break
                if not chunk:
                    break
                try:
                    ws.send(chunk.decode("utf-8", "replace"))
                except Exception:
                    break
        finally:
            alive.clear()

    threading.Thread(target=reader, daemon=True).start()

    try:
        while alive.is_set():
            msg = ws.receive(timeout=2)
            if msg is None:
                continue
            try:
                obj = json.loads(msg)
            except json.JSONDecodeError:
                try:
                    p.write(msg.encode("utf-8"))
                except Exception:
                    break
                continue
            mtype = obj.get("type")
            if mtype == "input":
                data = obj.get("data") or ""
                try:
                    p.write(data.encode("utf-8"))
                except Exception:
                    break
            elif mtype == "resize":
                rows = int(obj.get("rows", 24))
                cols = int(obj.get("cols", 80))
                try:
                    fcntl.ioctl(p.fd, termios.TIOCSWINSZ,
                                struct.pack("HHHH", rows, cols, 0, 0))
                except OSError:
                    pass
                try:
                    _tmux(["refresh-client", "-t", sess])
                except Exception:
                    pass
            elif mtype == "scroll":
                # tmux copy-mode 통한 스크롤 (모바일/iPad 용 — alt-screen 안에서도 동작).
                # copy-mode 진입은 idempotent — 이미 들어가 있으면 no-op.
                action = obj.get("action") or "up"
                amount = int(obj.get("amount") or 3)
                try:
                    if action in ("up", "page-up"):
                        _tmux(["copy-mode", "-t", sess])
                        if action == "up":
                            for _ in range(amount):
                                _tmux(["send-keys", "-t", sess, "-X", "scroll-up"])
                        else:
                            _tmux(["send-keys", "-t", sess, "-X", "page-up"])
                    elif action in ("down", "page-down"):
                        if action == "down":
                            for _ in range(amount):
                                _tmux(["send-keys", "-t", sess, "-X", "scroll-down"])
                        else:
                            _tmux(["send-keys", "-t", sess, "-X", "page-down"])
                    elif action == "bottom":
                        # copy-mode 종료 → 자동으로 맨 아래로
                        _tmux(["send-keys", "-t", sess, "-X", "cancel"])
                except Exception as e:
                    log.debug("tmux scroll failed sid=%s: %s", sid, e)
            elif mtype == "enter-copy":
                try:
                    _tmux(["send-keys", "-t", sess, "-X", "copy-mode"])
                except Exception:
                    pass
            elif mtype == "ping":
                pass
    except Exception as e:
        log.debug("ws loop exception sid=%s: %s", sid, e)
    finally:
        alive.clear()
        # attach PtyProcess 만 죽임 — tmux 세션과 claude CLI 는 살아남음
        try:
            p.terminate(force=True)
        except Exception:
            pass
        log.info("ws detached: sid=%s (tmux 세션 보존)", sid)


# ===== 호환 alias =====
def list_sessions() -> list[dict]:
    """기존 코드 호환."""
    return list_tmux_sessions()


# ===== startup 처리 =====
def ensure_default_session() -> None:
    """vanilla 모드에서는 미리 spawn 안 함 — WS 연결 시점에 새 PtyProcess 생성."""
    pass


if __name__ == "__main__":
    # 디버그용
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(list_tmux_sessions(), ensure_ascii=False, indent=2))
