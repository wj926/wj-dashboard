#!/usr/bin/env python3
"""Reusable feature-verification harness for wj dashboard.

Checks F1~F4 markers against a live server without calling data-mutating APIs.
- Allowed network calls: GET /, GET static assets, POST /api/task/parse
- Forbidden mutating APIs are never called.

Exit code:
- 0: all checks passed
- 1: one or more checks failed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class Check:
    fid: str
    name: str
    ok: bool
    evidence: str


def read_password(env_path: Path) -> str:
    if not env_path.exists():
        raise RuntimeError(f"env file not found: {env_path}")
    pw = ""
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line.startswith("WJ_PASSWORD="):
            continue
        pw = line.split("=", 1)[1].strip()
        if (pw.startswith('"') and pw.endswith('"')) or (pw.startswith("'") and pw.endswith("'")):
            pw = pw[1:-1]
        break
    if not pw:
        raise RuntimeError(f"WJ_PASSWORD not found in {env_path}")
    return pw


def _auth_header(user: str, pw: str) -> str:
    import base64

    token = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def http_request(
    method: str,
    url: str,
    *,
    auth_user: str,
    auth_pw: str,
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 10.0,
) -> Tuple[int, str, float]:
    hdr = {"Authorization": _auth_header(auth_user, auth_pw)}
    if headers:
        hdr.update(headers)
    req = Request(url, data=body, headers=hdr, method=method)
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            dt = time.perf_counter() - t0
            text = raw.decode("utf-8", errors="replace")
            return resp.status, text, dt
    except HTTPError as e:
        dt = time.perf_counter() - t0
        text = e.read().decode("utf-8", errors="replace")
        return e.code, text, dt
    except URLError as e:
        dt = time.perf_counter() - t0
        return 0, f"URLError: {e}", dt


def must_find(text: str, needle: str) -> bool:
    return needle in text


def main() -> int:
    p = argparse.ArgumentParser(description="Verify F1~F4 markers on wj dashboard")
    p.add_argument("--base-url", default="http://127.0.0.1:3004", help="dashboard base URL")
    p.add_argument("--env-file", default="/home/dami/.config/wj-dashboard/env", help="env file with WJ_PASSWORD")
    p.add_argument("--auth-user", default="verifier", help="basic-auth username")
    p.add_argument("--parse-threshold", type=float, default=0.3, help="max seconds for /api/task/parse")
    p.add_argument("--event-title", default="강홍익 결혼", help="known gcal title to verify")
    p.add_argument("--event-time", default="14:00", help="expected KST time for known event")
    p.add_argument("--event-wrong-time", default="05:00", help="wrong timezone time to reject")
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    env_path = Path(args.env_file)
    app_py = Path("/home/dami/wj/dashboard-app/app.py")

    pw = read_password(env_path)

    checks: List[Check] = []

    # Core fetches
    root_status, html, root_dt = http_request("GET", f"{base}/", auth_user=args.auth_user, auth_pw=pw)
    checks.append(
        Check(
            "SYS",
            "GET / reachable with Basic Auth",
            root_status == 200,
            f"status={root_status}, elapsed={root_dt*1000:.1f}ms, html_len={len(html)}",
        )
    )

    # Fetch the version-pinned static assets from live HTML contract
    css_status, css_text, css_dt = http_request(
        "GET", f"{base}/static/dashboard.css?v=20", auth_user=args.auth_user, auth_pw=pw
    )
    js_status, js_text, js_dt = http_request(
        "GET", f"{base}/static/dashboard.js?v=17", auth_user=args.auth_user, auth_pw=pw
    )
    checks.append(Check("SYS", "GET /static/dashboard.css?v=20", css_status == 200, f"status={css_status}, elapsed={css_dt*1000:.1f}ms"))
    checks.append(Check("SYS", "GET /static/dashboard.js?v=17", js_status == 200, f"status={js_status}, elapsed={js_dt*1000:.1f}ms"))

    # F1
    done_zone_collapsed = bool(re.search(r'id="done-zone"[^>]*class="[^"]*done-zone collapsed[^"]*"', html))
    checks.append(Check("F1", "done-toggle and done-zone(collapsed) in GET /", must_find(html, 'id="done-toggle"') and done_zone_collapsed, "done-toggle present + done-zone has class 'done-zone collapsed'" if (must_find(html, 'id="done-toggle"') and done_zone_collapsed) else "missing toggle or collapsed done-zone"))

    idx_done_zone = html.find('id="done-zone"')
    idx_is_done = html.find("is-done")
    cond_order = (idx_done_zone >= 0 and idx_is_done >= 0 and idx_is_done > idx_done_zone)
    checks.append(
        Check(
            "F1",
            "first is-done appears after done-zone",
            cond_order,
            f"done-zone@{idx_done_zone}, first is-done@{idx_is_done}",
        )
    )

    pre_done = html[:idx_done_zone] if idx_done_zone >= 0 else ""
    checks.append(
        Check(
            "F1",
            "no is-done in active section before done-zone",
            (idx_done_zone >= 0 and "is-done" not in pre_done),
            "'is-done' absent before done-zone" if (idx_done_zone >= 0 and "is-done" not in pre_done) else "found is-done before done-zone",
        )
    )

    css_done_hide = re.search(r"\.done-zone\.collapsed\s*\{[^}]*display\s*:\s*none\s*;", css_text)
    checks.append(
        Check(
            "F1",
            "CSS has .done-zone.collapsed{display:none}",
            bool(css_done_hide),
            "rule found" if css_done_hide else "rule missing",
        )
    )

    # F2
    checks.append(Check("F2", "quick-chat button exists", must_find(html, 'id="quick-chat"'), "id=quick-chat present" if must_find(html, 'id="quick-chat"') else "id=quick-chat missing"))

    submit_quick_match = re.search(r"async function submitQuick\(\)\s*\{([\s\S]*?)\n\s*\}\n\s*qIn\?\.addEventListener", js_text)
    submit_quick_body = submit_quick_match.group(1) if submit_quick_match else ""
    has_api_task = 'api("/api/task"' in submit_quick_body
    checks.append(
        Check(
            "F2",
            "submitQuick posts directly to /api/task",
            bool(submit_quick_match and has_api_task),
            "api('/api/task') found in submitQuick" if (submit_quick_match and has_api_task) else "submitQuick body missing api('/api/task')",
        )
    )

    # parse preview (safe, non-mutating)
    parse_payload = json.dumps({"text": "내일 오후 3시 회의 준비"}, ensure_ascii=False).encode("utf-8")
    parse_status, parse_text, parse_dt = http_request(
        "POST",
        f"{base}/api/task/parse",
        auth_user=args.auth_user,
        auth_pw=pw,
        body=parse_payload,
        headers={"Content-Type": "application/json", "Origin": base},
    )
    parse_ok = False
    parse_json = None
    try:
        parse_json = json.loads(parse_text)
        parse_ok = bool(parse_json.get("ok") is True)
    except Exception:
        parse_ok = False
    checks.append(
        Check(
            "F2",
            "/api/task/parse returns 200 + ok:true + under threshold",
            (parse_status == 200 and parse_ok and parse_dt <= args.parse_threshold),
            f"status={parse_status}, ok={parse_ok}, elapsed={parse_dt*1000:.1f}ms, threshold={args.parse_threshold*1000:.0f}ms",
        )
    )

    has_err_6000 = 'type === "err" ? 6000' in js_text
    send_chat_catch = re.search(r"async function sendChat\(text\)\s*\{[\s\S]*?catch\s*\(e\)\s*\{([\s\S]*?)\}\s*finally", js_text)
    catch_has_append = bool(send_chat_catch and "appendBubble" in send_chat_catch.group(1))
    checks.append(
        Check(
            "F2",
            "error toast 6s + chat catch appendBubble",
            bool(has_err_6000 and catch_has_append),
            f"err6000={has_err_6000}, catchAppendBubble={catch_has_append}",
        )
    )

    # F3
    media_900_topbar = re.search(r"@media\s*\(max-width:\s*900px\)\s*\{[\s\S]*?\.topbar\s*\{[^}]*flex-wrap\s*:\s*wrap", css_text)
    checks.append(Check("F3", "@media (max-width:900px) includes .topbar flex-wrap", bool(media_900_topbar), "rule found" if media_900_topbar else "rule missing"))

    cell_title_zero = re.search(
        r"\.cal\s*\.c\s*\.cell-title\s*\{[^}]*font-size\s*:\s*0(?:\s*;|\s*(?:px|rem|em|%)\b)",
        css_text,
    )
    checks.append(Check("F3", ".cal .c .cell-title does not set font-size:0", not bool(cell_title_zero), "font-size:0 not found" if not cell_title_zero else "font-size:0 found"))

    media_900_touch_ok = all(
        re.search(pattern, css_text)
        for pattern in [
            r"\.view-toggle\s+\.vt\s*\{[^}]*min-height\s*:\s*40px",
            r"\.filters\s+\.f\s*\{[^}]*min-height\s*:\s*40px",
            r"\.top-tabs\s+\.tt\s*\{[^}]*min-height\s*:\s*40px",
        ]
    )
    checks.append(Check("F3", "touch targets min-height 40px (.vt/.f/.tt)", media_900_touch_ok, "all min-height rules found" if media_900_touch_ok else "one or more min-height rules missing"))

    # F4
    cell_gcal_count = html.count('class="cell-gcal"')
    checks.append(Check("F4", "calendar renders at least one .cell-gcal", cell_gcal_count >= 1, f"cell-gcal count={cell_gcal_count}"))

    has_known_title = args.event_title in html
    checks.append(Check("F4", f"known gcal title present ({args.event_title})", has_known_title, "title found" if has_known_title else "title missing"))

    agenda_exists = ('id="agenda"' in html)
    agenda_row_count = html.count('class="agenda-row"')
    checks.append(Check("F4", "agenda tile present with rows", bool(agenda_exists and agenda_row_count >= 1), f"agenda id={agenda_exists}, agenda-row count={agenda_row_count}"))

    vt_icon_count = html.count('class="vt-icn"')
    checks.append(Check("F4", "view toggle has two icons (vt-icn)", vt_icon_count >= 2, f"vt-icn count={vt_icon_count}"))

    # KST conversion marker around known event
    near_good = re.search(
        rf"({re.escape(args.event_time)}[^<]{{0,50}}{re.escape(args.event_title)}|{re.escape(args.event_title)}[^<]{{0,50}}{re.escape(args.event_time)})",
        html,
    )
    near_bad = re.search(
        rf"({re.escape(args.event_wrong_time)}[^<]{{0,50}}{re.escape(args.event_title)}|{re.escape(args.event_title)}[^<]{{0,50}}{re.escape(args.event_wrong_time)})",
        html,
    )
    checks.append(Check("F4", f"KST time mapping for '{args.event_title}' is {args.event_time} (not {args.event_wrong_time})", bool(near_good and not near_bad), f"goodMatch={bool(near_good)}, badMatch={bool(near_bad)}"))

    app_text = app_py.read_text(encoding="utf-8") if app_py.exists() else ""
    gcal_try = "view[\"gcal_by_day\"] = gcal.events_by_day" in app_text and "view[\"gcal_agenda\"] = gcal.agenda" in app_text
    gcal_except = "except Exception:" in app_text and "view[\"gcal_by_day\"] = {}" in app_text and "view[\"gcal_agenda\"] = []" in app_text
    checks.append(
        Check(
            "F4",
            "server fallback: index() try/except sets empty gcal values",
            bool(gcal_try and gcal_except),
            f"tryBlock={gcal_try}, exceptFallback={gcal_except}",
        )
    )

    # Print table
    print("\nFeature Verification Report")
    print("=" * 86)
    print(f"Base URL: {base}")
    print(f"Env file: {env_path}")
    print("-" * 86)
    print(f"{'ID':<4} {'Result':<6} {'Check':<58} Evidence")
    print("-" * 86)
    for c in checks:
        res = "PASS" if c.ok else "FAIL"
        print(f"{c.fid:<4} {res:<6} {c.name:<58} {c.evidence}")

    n_fail = sum(1 for c in checks if not c.ok)
    n_pass = len(checks) - n_fail
    print("-" * 86)
    print(f"Summary: PASS={n_pass}, FAIL={n_fail}, TOTAL={len(checks)}")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
