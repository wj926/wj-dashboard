"""영수증 PDF 첨부 저장. 첨부 파일은 RECEIPTS_DIR 에 저장하고 안전하게 서빙한다."""
import os


def _post(client, url):
    return client.post(url, headers={"Sec-Fetch-Site": "same-origin"}, json={})


def _focus_id():
    import email_data
    return email_data.build_view()["focus"]["id"]


def test_save_receipt_ok_without_attachment(client):
    """fake 백엔드는 첨부가 없으니 pdf_count 0 으로 저장만 된다(에러 없이)."""
    mid = _focus_id()
    r = _post(client, f"/api/email/messages/{mid}/save-receipt")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert r.get_json()["pdf_count"] == 0


def test_save_receipt_file_and_serve(client):
    """직접 PDF 바이트를 저장하면 영수증함 파일 서빙 라우트로 받아진다."""
    import email_store
    rid = _focus_id()
    rec = email_store.save_receipt_file(rid, "영수증.pdf", b"%PDF-1.4 fake pdf bytes")
    assert rec and rec["name"].endswith(".pdf")
    email_store.add_receipt({"id": rid, "subject": "s", "files": [rec]})
    r = client.get(f"/email/receipts/{rid}/file/0")
    assert r.status_code == 200
    assert r.data.startswith(b"%PDF")


def test_serve_missing_file_404(client):
    r = client.get("/email/receipts/nope/file/0")
    assert r.status_code == 404


def test_nfd_korean_filename_preserved(client):
    """메일 첨부의 NFD(자모 분리형) 한글 파일명이 깨지지 않고 NFC 로 보존된다."""
    import unicodedata
    import email_store
    nfd = unicodedata.normalize("NFD", "김현진_석사.pdf")  # 분해형(완성형 아님)
    assert nfd != "김현진_석사.pdf"  # 정말 분해형인지 확인
    safe = email_store._safe_name(nfd)
    assert safe == "김현진_석사.pdf", f"NFD 한글이 깨졌다: {safe!r}"
    assert "_" * 3 not in safe  # 밑줄로 뭉개지지 않음


def test_path_traversal_filename_is_sanitized(client):
    import email_store
    rec = email_store.save_receipt_file("rid_x", "../../etc/passwd", b"data")
    assert rec
    # 저장 경로가 RECEIPTS_DIR 안에 있고, 상위로 못 빠져나간다
    base = os.path.realpath(os.environ["WJ_RECEIPTS_DIR"])
    assert os.path.realpath(rec["path"]).startswith(base)
    assert "passwd" in rec["name"]  # 이름은 남되 슬래시는 제거됨
    assert "/" not in rec["name"]


def test_remove_receipt_deletes_files(client):
    import email_store
    rid = "rid_del"
    rec = email_store.save_receipt_file(rid, "a.pdf", b"%PDF x")
    email_store.add_receipt({"id": rid, "subject": "s", "files": [rec]})
    assert os.path.exists(rec["path"])
    email_store.remove_receipt(rid)
    assert not os.path.exists(rec["path"])
