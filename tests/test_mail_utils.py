import email
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from icloud_mcp import mail_utils
from icloud_mcp.config import config


def _multipart_with_attachment():
    msg = MIMEMultipart("mixed")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("plain body", "plain", "utf-8"))
    alt.attach(MIMEText("<html><body><p>html body</p></body></html>", "html", "utf-8"))
    msg.attach(alt)
    part = MIMEBase("application", "pdf")
    part.set_payload(b"%PDF-1.4 fake")
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename="Ré sumé.pdf")
    msg.attach(part)
    msg["Subject"] = "=?utf-8?b?0J/RgNC40LLQtdGC?="
    msg["From"] = "Sender <s@example.com>"
    return email.message_from_bytes(msg.as_bytes())


def test_extract_body_prefers_plain_and_lists_attachments():
    msg = _multipart_with_attachment()
    text, html = mail_utils.extract_message_body(msg)
    assert text == "plain body" and html == ""
    text, html = mail_utils.extract_message_body(msg, prefer_html=True)
    assert "<p>html body</p>" in html
    attachments = mail_utils.list_attachments(msg)
    assert attachments == [
        {"index": 1, "name": "Ré sumé.pdf", "mime_type": "application/pdf", "size": 13, "inline": False}
    ]


def test_html_only_message_is_rendered_to_text():
    raw = (
        b"Subject: hi\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><h1>Title</h1><p>Hello <b>world</b></p><ul><li>one</li><li>two</li></ul></body></html>"
    )
    msg = email.message_from_bytes(raw)
    text, _ = mail_utils.extract_message_body(msg)
    assert "Title" in text and "Hello world" in text and "<" not in text
    assert "one" in text and "two" in text


def test_body_is_truncated(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_BODY_MAX_CHARS", 50)
    raw = b"Subject: hi\r\nContent-Type: text/plain\r\n\r\n" + b"x" * 500
    text, _ = mail_utils.extract_message_body(email.message_from_bytes(raw))
    assert text.startswith("x" * 50) and "truncated, original was 500 chars" in text


def test_decode_mime_header_and_flags():
    assert mail_utils.decode_mime_header("=?utf-8?b?0J/RgNC40LLQtdGC?=") == "Привет"
    assert mail_utils.flags_from_data({b"FLAGS": (b"\\Seen", b"\\Flagged")}) == ["\\Seen", "\\Flagged"]


def test_find_attachment_by_index_and_name():
    msg = _multipart_with_attachment()
    meta, data = mail_utils.find_attachment(msg, "1")
    assert meta.name == "Ré sumé.pdf" and data.startswith(b"%PDF")
    meta, _ = mail_utils.find_attachment(msg, "sumé")
    assert meta.index == 1
    with pytest.raises(ValueError, match="Available"):
        mail_utils.find_attachment(msg, "nope.txt")


def test_message_summary_fields():
    msg = _multipart_with_attachment()
    item = mail_utils.message_summary(42, msg, {b"FLAGS": (b"\\Seen",)}, "INBOX", include_body=True)
    assert item["id"] == "42" and item["subject"] == "Привет" and item["unread"] is False
    assert item["has_attachments"] is True and item["body_text"] == "plain body"


def test_build_email_message_html_gets_plain_alternative():
    msg = mail_utils.build_email_message("<p>Hi <b>there</b></p>", html=True, attachment_parts=[])
    kinds = [p.get_content_type() for p in msg.walk()]
    assert kinds == ["multipart/alternative", "text/plain", "text/html"]
    plain = [p for p in msg.walk() if p.get_content_type() == "text/plain"][0]
    assert "Hi there" in plain.get_payload(decode=True).decode()


def test_local_files_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_FILES", False)
    with pytest.raises(PermissionError):
        mail_utils.resolve_local_path(str(tmp_path), must_exist=False)
    monkeypatch.setattr(config, "LOCAL_FILES", True)
    monkeypatch.setattr(config, "LOCAL_FILES_ROOT", str(tmp_path))
    inside = tmp_path / "a.txt"
    inside.write_text("x")
    assert mail_utils.resolve_local_path(str(inside), must_exist=True) == str(inside.resolve())
    with pytest.raises(PermissionError):
        mail_utils.resolve_local_path("/etc/hosts", must_exist=True)


def test_build_attachment_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_FILES", True)
    monkeypatch.setattr(config, "LOCAL_FILES_ROOT", None)
    f = tmp_path / "note.txt"
    f.write_text("hello")
    parts = mail_utils.build_attachment_parts([str(f)])
    assert parts[0].get_filename() == "note.txt" and parts[0].get_content_type() == "text/plain"
    monkeypatch.setattr(config, "EMAIL_MAX_ATTACHMENT_BYTES", 2)
    with pytest.raises(ValueError, match="limit"):
        mail_utils.build_attachment_parts([str(f)])


def test_split_addresses():
    assert mail_utils.split_addresses("a@x.com, b@x.com;c@x.com") == ["a@x.com", "b@x.com", "c@x.com"]
    assert mail_utils.split_addresses(None) == []
