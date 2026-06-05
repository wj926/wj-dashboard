"""실 Gmail 읽기 모듈 (S2: readonly scope 만).

원칙(gcal.py 계승):
- 어떤 public 함수도 예외를 밖으로 던지지 않는다. 실패하면 [], {}, 또는
  {"ok": False, "error": ..., "needs_auth": bool} 를 돌려 앱이 계속 동작하게 한다.
- 비밀(client_secret/token)은 코드/깃에 박지 않고 ~/.config/wj-dashboard/ 또는
  env override 에서만 읽는다.
- google 라이브러리는 함수 안에서 lazy import 한다. 미설치 환경에서도 이 모듈
  자체는 import 가능해야 한다(app import 가 깨지면 안 됨).
- 메일 본문 HTML 은 sanitize_email_html() 로 정제한 값만 템플릿에 넘긴다.

scope 는 readonly 만 둔다. gmail.modify/compose/send/calendar 는 S2 범위 밖이다.
"""
from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "wj-dashboard"
CLIENT_SECRET_PATH = Path(
    os.environ.get("WJ_GOOGLE_CLIENT_SECRET", str(CONFIG_DIR / "google_client_secret.json"))
)
TOKEN_PATH = Path(
    os.environ.get("WJ_GOOGLE_TOKEN", str(CONFIG_DIR / "google_token.json"))
)

SCOPES_READONLY = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

# sanitize 허용 태그/속성 (app.py 의 markdown sanitize 와 같은 bleach 사용)
_ALLOWED_TAGS = [
    "p", "br", "hr", "div", "span", "blockquote", "pre", "code",
    "b", "strong", "i", "em", "u", "s", "sub", "sup", "mark",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tr", "th", "td", "caption",
    "h1", "h2", "h3", "h4", "h5", "h6", "a",
]
# remote img 차단을 위해 img 는 허용 태그에서 제외한다(원격 트래킹 픽셀 제거).
_ALLOWED_ATTRS = {
    "*": ["class"],
    "a": ["href", "title"],
    "mark": ["data-candidate-id"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


# ---------------------------------------------------------------------------
# 인증
# ---------------------------------------------------------------------------
def get_credentials(scopes: list[str]) -> tuple[bool, object | None, dict]:
    """성공 시 (True, creds, meta), 실패 시 (False, None, meta). 절대 raise 하지 않는다.

    meta 는 needs_auth/error 등 호출부가 안전 view 를 만들 때 쓰는 힌트.
    """
    try:
        if not TOKEN_PATH.exists():
            return False, None, {"ok": False, "needs_auth": True, "error": "no_token"}
        # lazy import: 미설치 환경에서도 이 함수 호출 전까지는 app import 가 살아있음
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), scopes)
        if creds and creds.valid:
            return True, creds, {"ok": True, "needs_auth": False}
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                return False, None, {"ok": False, "needs_auth": True, "error": "refresh_failed"}
            try:
                TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
                try:
                    os.chmod(TOKEN_PATH, 0o600)
                except OSError:
                    pass
            except OSError:
                pass
            return True, creds, {"ok": True, "needs_auth": False}
        return False, None, {"ok": False, "needs_auth": True, "error": "invalid_creds"}
    except Exception as e:
        return False, None, {"ok": False, "needs_auth": True, "error": type(e).__name__}


def get_service(scopes: list[str] | None = None):
    """Gmail API service 또는 None. 실패 시 None."""
    try:
        scopes = scopes or SCOPES_READONLY
        ok, creds, _meta = get_credentials(scopes)
        if not ok or creds is None:
            return None
        from googleapiclient.discovery import build

        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------
def list_inbox(query: str = "in:inbox newer_than:30d", max_results: int = 30) -> list[dict]:
    """큐 계산용 메시지 요약 리스트. 실패 시 []."""
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return []
        resp = (
            svc.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        out = []
        for ref in resp.get("messages", []) or []:
            mid = ref.get("id")
            if not mid:
                continue
            full = get_message(mid, fmt="metadata")
            if full:
                out.append(full)
        return out
    except Exception:
        return []


def fetch_inbox(query: str | None = None, max_results: int = 120) -> list[dict]:
    """배치 요청으로 인박스 메시지(본문 포함)를 한 번에 받아 정규화. 캐시 채우기용.

    list_inbox 처럼 메시지마다 개별 호출하지 않고 batch 한 번으로 묶어 훨씬 빠르다.
    query 가 None 이면 email_filters(광고/소셜/사용자 제외목록)로 쿼리 생성. 실패 시 [].
    """
    try:
        if query is None:
            import email_filters
            query = email_filters.build_query()
    except Exception:
        query = "in:inbox newer_than:30d"
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return []
        resp = (
            svc.users().messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        refs = resp.get("messages", []) or []
        results: dict[str, dict] = {}

        def _cb(request_id, response, exception):
            if exception is None and response is not None:
                try:
                    results[request_id] = normalize_message(response)
                except Exception:
                    pass

        # Gmail batch 한도는 1요청당 최대 100개. 그 이상이면 배치 전체가 깨진다.
        # 안전하게 50개씩 끊어 여러 배치로 실행한다(max_results 가 100 넘어도 OK).
        CHUNK = 50
        indexed = [(i, ref.get("id")) for i, ref in enumerate(refs) if ref.get("id")]
        for start in range(0, len(indexed), CHUNK):
            batch = svc.new_batch_http_request(callback=_cb)
            for i, mid in indexed[start:start + CHUNK]:
                batch.add(
                    svc.users().messages().get(userId="me", id=mid, format="full"),
                    request_id=str(i),
                )
            batch.execute()
        return [results[str(i)] for i in range(len(refs)) if str(i) in results]
    except Exception:
        return []


def get_message(message_id: str, fmt: str = "full") -> dict:
    """단일 메시지 정규화. 실패 시 {}."""
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return {}
        raw = (
            svc.users()
            .messages()
            .get(userId="me", id=message_id, format=fmt)
            .execute()
        )
        return normalize_message(raw)
    except Exception:
        return {}


def list_attachments(message_id: str) -> list[dict]:
    """메시지의 첨부 목록 [{filename, mime, attachment_id, size}]. 실패 시 []. readonly 로 가능."""
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return []
        raw = (
            svc.users().messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        out: list[dict] = []

        def _walk(part: dict):
            if not part:
                return
            fn = part.get("filename") or ""
            body = part.get("body") or {}
            if fn and body.get("attachmentId"):
                out.append({
                    "filename": fn,
                    "mime": part.get("mimeType") or "",
                    "attachment_id": body.get("attachmentId"),
                    "size": body.get("size") or 0,
                })
            for sub in part.get("parts", []) or []:
                _walk(sub)

        _walk(raw.get("payload") or {})
        return out
    except Exception:
        return []


def download_attachment(message_id: str, attachment_id: str) -> bytes:
    """첨부 바이트 다운로드. 실패 시 b''. readonly 로 가능."""
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None or not attachment_id:
            return b""
        import base64
        raw = (
            svc.users().messages().attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        data = raw.get("data")
        if not data:
            return b""
        return base64.urlsafe_b64decode(data.encode("utf-8"))
    except Exception:
        return b""


def get_thread(thread_id: str) -> dict:
    """스레드 메시지 목록(내가 마지막 응답했는지 계산용 원본). 실패 시 {}."""
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return {}
        raw = (
            svc.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        msgs = [normalize_message(m) for m in raw.get("messages", []) or []]
        return {"id": raw.get("id"), "messages": msgs}
    except Exception:
        return {}


def list_drafts(max_results: int = 20) -> list[dict]:
    """임시보관함 요약. 실패 시 []."""
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return []
        resp = (
            svc.users()
            .drafts()
            .list(userId="me", maxResults=max_results)
            .execute()
        )
        out = []
        for d in resp.get("drafts", []) or []:
            msg = d.get("message") or {}
            norm = normalize_message(msg) if msg else {}
            out.append({"draft_id": d.get("id"), "message": norm})
        return out
    except Exception:
        return []


def build_followups(days: int = 2, max_results: int = 50) -> list[dict]:
    """내가 마지막 답장을 안 한 스레드 계산. 실패 시 []."""
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return []
        # 후보 쿼리: 내가 보낸 게 아닌 최근 인박스. 정밀 계산은 S5 에서.
        resp = (
            svc.users()
            .messages()
            .list(userId="me", q="in:inbox -from:me newer_than:60d", maxResults=max_results)
            .execute()
        )
        out = []
        for ref in resp.get("messages", []) or []:
            tid = ref.get("threadId")
            if tid:
                out.append({"thread_id": tid})
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------
def normalize_message(raw: dict) -> dict:
    """Gmail API message -> UI 용 message dict. 실패 시 {}."""
    try:
        if not raw:
            return {}
        payload = raw.get("payload") or {}
        headers = extract_headers(payload)
        body = extract_body(payload)
        try:
            internal_ts = int(raw.get("internalDate") or 0) // 1000
        except Exception:
            internal_ts = 0
        return {
            "id": raw.get("id"),
            "thread_id": raw.get("threadId"),
            "label_ids": raw.get("labelIds") or [],
            "snippet": raw.get("snippet") or "",
            "internal_ts": internal_ts,
            "headers": headers,
            "body": body,
        }
    except Exception:
        return {}


def fetch_sent_thread_dates(max_results: int = 150) -> dict:
    """내가 보낸 메일(SENT)의 thread_id -> 내 마지막 발송 epoch(sec). 실패 시 {}.

    인박스 메시지의 thread_id 가 여기 있고 내 발송이 그 메시지보다 나중이면
    '내가 이미 답장함(처리함)' 으로 본다. 읽기 권한만 사용.
    """
    try:
        svc = get_service(SCOPES_READONLY)
        if svc is None:
            return {}
        resp = (
            svc.users().messages()
            .list(userId="me", q="in:sent newer_than:120d", maxResults=max_results)
            .execute()
        )
        refs = resp.get("messages", []) or []
        out: dict[str, int] = {}

        def _cb(request_id, response, exception):
            if exception is None and response is not None:
                tid = response.get("threadId")
                try:
                    ts = int(response.get("internalDate") or 0) // 1000
                except Exception:
                    ts = 0
                if tid and ts > out.get(tid, 0):
                    out[tid] = ts

        # Gmail batch 는 호출당 최대 100건. refs 가 100 을 넘으면 청크로 나눠 실행한다
        # (안 그러면 batch.execute() 가 실패해 답장 판정이 통째로 비어버린다).
        BATCH_LIMIT = 100
        batch = svc.new_batch_http_request(callback=_cb)
        in_batch = 0
        for ref in refs:
            mid = ref.get("id")
            if not mid:
                continue
            batch.add(
                svc.users().messages().get(userId="me", id=mid, format="metadata", metadataHeaders=["Date"]),
                request_id=mid,
            )
            in_batch += 1
            if in_batch >= BATCH_LIMIT:
                batch.execute()
                batch = svc.new_batch_http_request(callback=_cb)
                in_batch = 0
        if in_batch:
            batch.execute()
        return out
    except Exception:
        return {}


def _decode_hdr(value: str) -> str:
    """RFC 2047 인코딩 헤더(=?UTF-8?B?..?=)를 사람이 읽는 문자열로. 실패 시 원문."""
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def extract_headers(payload: dict) -> dict:
    """From, To, Subject, Date, Message-ID, In-Reply-To 추출. 실패 시 {}."""
    try:
        wanted = {"from", "to", "subject", "date", "message-id", "in-reply-to"}
        decode = {"from", "to", "subject"}
        out: dict[str, str] = {}
        for h in (payload or {}).get("headers", []) or []:
            name = (h.get("name") or "").lower()
            if name in wanted:
                raw = h.get("value") or ""
                out[name] = _decode_hdr(raw) if name in decode else raw
        return out
    except Exception:
        return {}


def extract_body(payload: dict) -> dict:
    """{text, html_sanitized, snippets}. text/plain 우선, 없으면 html. 실패 시 빈 값."""
    import base64

    def _decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", "replace")
        except Exception:
            return ""

    def _walk(part: dict, acc: dict):
        if not part:
            return
        mime = part.get("mimeType") or ""
        body = part.get("body") or {}
        data = body.get("data")
        if mime == "text/plain" and data and not acc["text"]:
            acc["text"] = _decode(data)
        elif mime == "text/html" and data and not acc["html"]:
            acc["html"] = _decode(data)
        for sub in part.get("parts", []) or []:
            _walk(sub, acc)

    try:
        acc = {"text": "", "html": ""}
        _walk(payload or {}, acc)
        html_sanitized = sanitize_email_html(acc["html"]) if acc["html"] else ""
        return {
            "text": acc["text"],
            "html_sanitized": html_sanitized,
            "snippets": [],
        }
    except Exception:
        return {"text": "", "html_sanitized": "", "snippets": []}


def sanitize_email_html(raw_html: str) -> str:
    """Gmail 원본 HTML 정제. script/이벤트핸들러/remote img 제거, 안전 태그만.

    실패하거나 bleach 미설치면 빈 문자열(가장 안전)을 돌려 raw HTML 노출을 막는다.
    """
    try:
        if not raw_html:
            return ""
        import re
        import bleach

        # bleach 는 태그만 strip 하고 <style>/<script> 내부 텍스트(CSS/JS)는 본문으로
        # 남긴다. 마케팅 메일의 CSS 가 화면에 줄줄 새므로, 이 블록은 내용까지 통째로 제거.
        cleaned = re.sub(
            r"(?is)<(script|style|head|title)\b[^>]*>.*?</\1\s*>", " ", raw_html
        )
        cleaned = re.sub(r"(?is)</?(script|style|head|title)\b[^>]*>", " ", cleaned)

        return bleach.clean(
            cleaned,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRS,
            protocols=_ALLOWED_PROTOCOLS,
            strip=True,
            strip_comments=True,
        )
    except Exception:
        return ""
