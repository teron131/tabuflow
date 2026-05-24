"""Standalone EML/MSG reference inspection tools."""

from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Any

BODY_PREVIEW_CHARS = 2_000
WHITESPACE = re.compile(r"\s+")


class HtmlTextExtractor(HTMLParser):
    """Minimal HTML-to-text collector for email body previews."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Collect visible text data."""
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        """Return collected HTML text."""
        return "\n".join(self.parts)


def _clean_text(value: Any) -> str:
    """Normalize text for compact JSON payloads."""
    return WHITESPACE.sub(" ", str(value or "").replace("\x00", " ")).strip()


def _body_preview(value: Any, *, max_chars: int) -> str:
    """Return a compact body preview."""
    text = _clean_text(value)
    return text[: max(0, max_chars)]


def _html_to_text(value: Any) -> str:
    """Convert simple email HTML into plain text."""
    if value is None:
        return ""
    html = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    parser = HtmlTextExtractor()
    parser.feed(html)
    return parser.text()


def _first_body_part(message: EmailMessage) -> tuple[str, str]:
    """Return preferred body text and source kind from an EML message."""
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        return "", "none"
    content = body.get_content()
    if body.get_content_type() == "text/html":
        return _html_to_text(content), "html"
    return str(content), "plain"


def _email_payload(
    path: Path,
    *,
    format_name: str,
    subject: str,
    sender: Any,
    recipients: Any,
    cc: Any,
    sent_at: Any,
    body: str,
    body_source: str,
    attachments: list[str],
    max_body_chars: int,
) -> dict[str, Any]:
    """Build a generic email inspection payload."""
    return {
        "path": str(path),
        "format": format_name,
        "subject": subject,
        "sender": _clean_text(sender),
        "recipients": _clean_text(recipients),
        "cc": _clean_text(cc),
        "sent_at": None if sent_at is None else _clean_text(sent_at),
        "body_source": body_source,
        "body_preview": _body_preview(body, max_chars=max_body_chars),
        "body_char_count": len(_clean_text(body)),
        "attachments": attachments,
    }


def _inspect_eml(path: Path, *, max_body_chars: int) -> dict[str, Any]:
    """Inspect an RFC822 EML file."""
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    body, body_source = _first_body_part(message)
    attachments = [filename for part in message.iter_attachments() if (filename := part.get_filename())]
    subject = _clean_text(message.get("subject"))
    return _email_payload(
        path,
        format_name="eml",
        subject=subject,
        sender=message.get("from"),
        recipients=message.get("to"),
        cc=message.get("cc"),
        sent_at=message.get("date"),
        body=body,
        body_source=body_source,
        attachments=attachments,
        max_body_chars=max_body_chars,
    )


def _inspect_msg(path: Path, *, max_body_chars: int) -> dict[str, Any]:
    """Inspect a Microsoft Outlook MSG file."""
    try:
        import extract_msg
    except ModuleNotFoundError as exc:
        raise RuntimeError("MSG inspection requires the optional extract-msg dependency.") from exc

    message = extract_msg.Message(str(path))
    try:
        subject = _clean_text(message.subject)
        body = str(message.body or "") or _html_to_text(message.htmlBody)
        attachments = [
            _clean_text(getattr(attachment, "longFilename", None) or getattr(attachment, "shortFilename", None))
            for attachment in message.attachments
            if getattr(attachment, "longFilename", None) or getattr(attachment, "shortFilename", None)
        ]
        return _email_payload(
            path,
            format_name="msg",
            subject=subject,
            sender=message.sender,
            recipients=message.to,
            cc=message.cc,
            sent_at=None if message.date is None else message.date.isoformat(),
            body=body,
            body_source="plain" if message.body else "html",
            attachments=attachments,
            max_body_chars=max_body_chars,
        )
    finally:
        message.close()


def inspect_email_file(
    path: str | Path,
    *,
    max_body_chars: int = BODY_PREVIEW_CHARS,
) -> dict[str, Any]:
    """Inspect an email file as reference context, not as a table artifact."""
    email_path = Path(path).expanduser().resolve()
    if not email_path.is_file():
        raise FileNotFoundError(f"Email file not found: {email_path}")

    suffix = email_path.suffix.lower()
    if suffix == ".eml":
        payload = _inspect_eml(email_path, max_body_chars=max_body_chars)
    elif suffix == ".msg":
        payload = _inspect_msg(email_path, max_body_chars=max_body_chars)
    else:
        raise ValueError(f"Unsupported email format: {email_path.suffix}")

    payload["status"] = "ok"
    payload["reference_only"] = True
    payload["summary"] = f"Inspected {payload['format']} reference email `{payload['subject']}`."
    return payload
