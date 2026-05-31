#!/usr/bin/env python3
"""구글 캘린더 통합 검증 하네스 (L1~L5).

- 비파괴 원칙: GET/파싱/정적검사만 수행
- 기본 인증 비번은 env 파일에서 읽음 (하드코딩 금지)
- 라이브(실행 서버)와 소스(파일) 상태를 함께 보여 재시작 필요 여부를 분명히 알림
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


APP_ROOT = Path("/home/dami/wj/dashboard-app")
APP_PY = APP_ROOT / "app.py"
TPL_INDEX = APP_ROOT / "templates" / "index.html"
SERVER_PY = Path("/home/dami/miniconda3/bin/python")


@dataclass
class LayerResult:
    layer: str
    ok: bool
    evidence: str


class CalendarCellParser(HTMLParser):
    """달력 셀(data-iso 가진 div.c) 안에 cell-gcal-list 가 렌더됐는지 추적.

    중요: div 중첩 depth 를 세어 셀의 '자기 닫힘' 시점에만 컨텍스트를 비운다.
    (모든 </div> 마다 pop 하면 셀 내부 첫 자식 div 가 닫힐 때 셀이 사라져 오검출.)
    """

    def __init__(self) -> None:
        super().__init__()
        self.visible_isos: set[str] = set()
        self.gcal_isos: set[str] = set()
        self._depth = 0
        self._cell: tuple[str, int] | None = None  # (iso, 셀이 열린 depth)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        self._depth += 1
        d = {k: (v or "") for k, v in attrs}
        klass = d.get("class", "")
        iso = d.get("data-iso")
        if iso and re.search(r"\bc\b", klass):
            self._cell = (iso, self._depth)
            self.visible_isos.add(iso)
            return
        if self._cell and "cell-gcal-list" in klass:
            self.gcal_isos.add(self._cell[0])

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._cell and self._depth == self._cell[1]:
            self._cell = None
        self._depth -= 1


def read_password(env_path: Path) -> str:
    text = env_path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line.startswith("WJ_PASSWORD="):
            continue
        val = line.split("=", 1)[1].strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if val:
            return val
    raise RuntimeError(f"WJ_PASSWORD not found in {env_path}")


def auth_header(user: str, pw: str) -> str:
    token = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def http_get(url: str, user: str, pw: str, timeout: float = 10.0) -> tuple[int, str]:
    req = Request(url, headers={"Authorization": auth_header(user, pw)}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except URLError as e:
        return 0, f"URLError: {e}"


def extract_js_object(html: str, var_name: str) -> dict[str, Any] | None:
    m = re.search(rf"window\.__{re.escape(var_name)}__\s*=\s*(\{{[\s\S]*?\}})\s*;", html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def source_has_gcal_injection() -> tuple[bool, bool]:
    tpl = TPL_INDEX.read_text(encoding="utf-8") if TPL_INDEX.exists() else ""
    app = APP_PY.read_text(encoding="utf-8") if APP_PY.exists() else ""
    tpl_ok = "window.__GCAL__" in tpl and "gcal_json_str" in tpl
    app_ok = "view[\"gcal_json_str\"]" in app and "gcal.events_by_day" in app and "except Exception:" in app
    return tpl_ok, app_ok


def ensure_playwright() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401
        return True, "playwright import OK"
    except Exception:
        pass

    cmds = [
        [str(SERVER_PY), "-m", "pip", "install", "playwright"],
        [str(SERVER_PY), "-m", "playwright", "install", "chromium"],
    ]
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except Exception as e:
            return False, f"install failed: {' '.join(cmd)} / {e}"

    try:
        import playwright  # noqa: F401
        return True, "playwright install+import OK"
    except Exception as e:
        return False, f"playwright import failed after install: {e}"


def layer3_and_layer4_with_playwright(base_url: str, user: str, pw: str) -> tuple[LayerResult, LayerResult]:
    ok_pw, msg = ensure_playwright()
    if not ok_pw:
        l3 = LayerResult("L3", False, f"확인 못 함: {msg}")
        l4 = LayerResult("L4", False, f"확인 못 함: {msg}")
        return l3, l4

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 960}, http_credentials={"username": user, "password": pw})
            page = ctx.new_page()
            page.goto(base_url, wait_until="domcontentloaded", timeout=20000)

            gcal = page.evaluate("() => window.__GCAL__ || {}")
            candidate_iso = None
            candidate_title = None
            for iso, events in gcal.items():
                if not events:
                    continue
                if page.locator(f'.cal .c[data-iso="{iso}"]').count() < 1:
                    continue
                title = (events[0] or {}).get("title")
                if title:
                    candidate_iso, candidate_title = iso, title
                    break

            if not candidate_iso or not candidate_title:
                browser.close()
                return (
                    LayerResult("L3", False, "실패: 클릭 가능한 구글 일정 날짜를 찾지 못함(window.__GCAL__ 비어있거나 현재월 셀 없음)"),
                    LayerResult("L4", False, "실패: L3 전제 미충족으로 시각/정확도 검증 불가"),
                )

            page.locator(f'.cal .c[data-iso="{candidate_iso}"]').first.click()
            page.wait_for_selector("#ov-day.on", timeout=5000)
            modal_text = page.locator("#ov-day-body").inner_text(timeout=3000)
            l3_ok = candidate_title in modal_text
            l3 = LayerResult(
                "L3",
                l3_ok,
                f"clicked={candidate_iso}, expected_title={candidate_title!r}, modal_contains={l3_ok}",
            )

            desktop_png = "/tmp/wj_calint_desktop.png"
            page.screenshot(path=desktop_png, full_page=True)

            # 모바일 화면 + 모달 포함 스크린샷
            mctx = browser.new_context(viewport={"width": 390, "height": 844}, http_credentials={"username": user, "password": pw})
            mpage = mctx.new_page()
            mpage.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            mpage.locator(f'.cal .c[data-iso="{candidate_iso}"]').first.click()
            mpage.wait_for_selector("#ov-day.on", timeout=5000)
            mobile_png = "/tmp/wj_calint_mobile.png"
            mpage.screenshot(path=mobile_png, full_page=True)

            # 정확도: 강홍익 결혼 14:00, 05:00 불검출
            found_good = False
            found_bad = False
            for _iso, events in gcal.items():
                for ev in (events or []):
                    title = str((ev or {}).get("title") or "")
                    t = str((ev or {}).get("time") or "")
                    if "강홍익 결혼" in title and t == "14:00":
                        found_good = True
                    if "강홍익 결혼" in title and t == "05:00":
                        found_bad = True
            l4_ok = found_good and not found_bad
            l4 = LayerResult(
                "L4",
                l4_ok,
                f"강홍익 결혼 14:00={found_good}, 05:00={found_bad}, screenshots=[{desktop_png}, {mobile_png}]",
            )

            browser.close()
            return l3, l4
    except Exception as e:
        return (
            LayerResult("L3", False, f"확인 못 함: playwright 실행 오류: {e}"),
            LayerResult("L4", False, f"확인 못 함: playwright 실행 오류: {e}"),
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify Google Calendar integration into calendar modal (L1~L5)")
    ap.add_argument("--base-url", default="http://127.0.0.1:3004")
    ap.add_argument("--env-file", default="/home/dami/.config/wj-dashboard/env")
    ap.add_argument("--auth-user", default="verifier")
    args = ap.parse_args()

    pw = read_password(Path(args.env_file))
    base = args.base_url.rstrip("/")

    results: list[LayerResult] = []

    status, html = http_get(f"{base}/", args.auth_user, pw)
    live_gcal = extract_js_object(html, "GCAL") if status == 200 else None

    tpl_ok, app_ok = source_has_gcal_injection()

    # L1 데이터
    l1_ok = status == 200 and isinstance(live_gcal, dict) and len(live_gcal) > 0 and any((live_gcal.get(k) or []) for k in live_gcal.keys())
    l1_evd = f"http={status}, live_window.__GCAL__={'present' if live_gcal is not None else 'missing'}, non_empty={bool(live_gcal and any((live_gcal.get(k) or []) for k in live_gcal.keys()))}"
    if tpl_ok and app_ok and live_gcal is None:
        l1_evd += " | source는 반영됐으나 라이브 HTML에 미반영(서버 재시작 필요 가능성)"
    results.append(LayerResult("L1", l1_ok, l1_evd))

    # L2 렌더
    if status == 200:
        parser = CalendarCellParser()
        parser.feed(html)
        expected = set()
        if isinstance(live_gcal, dict):
            for iso, evs in live_gcal.items():
                if iso in parser.visible_isos and evs:
                    expected.add(iso)
        missing = sorted(expected - parser.gcal_isos)
        l2_ok = len(missing) == 0 and len(parser.gcal_isos) >= 1
        results.append(
            LayerResult(
                "L2",
                l2_ok,
                f"visible_cells={len(parser.visible_isos)}, expected_gcal_days={len(expected)}, rendered_cell_gcal_days={len(parser.gcal_isos)}, missing={missing[:5]}",
            )
        )
    else:
        results.append(LayerResult("L2", False, f"GET / 실패로 렌더 검증 불가(status={status})"))

    # L3/L4 상호작용/정확도+스크린샷
    l3, l4 = layer3_and_layer4_with_playwright(base, args.auth_user, pw)
    results.append(l3)
    results.append(l4)

    # L5 견고성 (정적)
    app_text = APP_PY.read_text(encoding="utf-8") if APP_PY.exists() else ""
    has_try = 'view["gcal_by_day"] = gcal.events_by_day' in app_text and 'view["gcal_agenda"] = gcal.agenda' in app_text
    has_except = "except Exception:" in app_text and 'view["gcal_by_day"] = {}' in app_text and 'view["gcal_agenda"] = []' in app_text
    l5_ok = has_try and has_except
    results.append(LayerResult("L5", l5_ok, f"index() gcal try={has_try}, except_fallback={has_except}"))

    # 소스/라이브 보조 상태
    print("[INFO] source-contract:")
    print(f"  - templates/index.html window.__GCAL__ 주입: {'YES' if tpl_ok else 'NO'}")
    print(f"  - app.py gcal_json_str/try-except: {'YES' if app_ok else 'NO'}")

    print("\n[RESULT]")
    fail_count = 0
    for r in results:
        tag = "PASS" if r.ok else "FAIL"
        if not r.ok:
            fail_count += 1
        print(f"- {r.layer} {tag}: {r.evidence}")

    if fail_count:
        print(f"\nSUMMARY: FAIL ({fail_count} layer(s) failed)")
        return 1
    print("\nSUMMARY: PASS (all layers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
