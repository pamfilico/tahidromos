"""Building messages, and the three rules of threading.

RFC 5322 §3.6.4 is short and every mail client agrees on it:

  1. In-Reply-To  = the parent's Message-ID
  2. References   = the parent's References, then the parent's Message-ID
  3. Subject      = "Re: " exactly once, however deep the chain gets

Getting these right is the whole difference between a mail sink and something
you can test a conversation against.
"""

from __future__ import annotations

import email
import email.policy
import re
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from typing import Iterable

POLICY = email.policy.default
# On the wire, headers and body are separated by CRLF (RFC 5322 §2.1).
# Serialising with the default policy gives bare LF, which then confuses
# anything that splits the header block on CRLF.
TRANSPORT_POLICY = email.policy.SMTP

RE_PREFIX = re.compile(r"^\s*(re|aw|sv|antw|res)\s*(\[\d+\])?\s*:\s*", re.IGNORECASE)
# Fwd:, FW:, WG: (German), TR: (French), RV: (Spanish), Enc: (Portuguese)
FWD_PREFIX = re.compile(r"^\s*(fwd?|wg|tr|rv|enc)\s*(\[\d+\])?\s*:\s*", re.IGNORECASE)

DEPTH_HEADER = "X-Tahidromos-Depth"
# Gmail's convention, and the only reliable way to tie a forward back to the
# message it came from once the body has been rewritten.
FORWARDED_ID_HEADER = "X-Forwarded-Message-Id"
FORWARD_COUNT_HEADER = "X-Tahidromos-Forward-Count"
FORWARD_PATH_HEADER = "X-Tahidromos-Forward-Path"


def parse(raw: bytes | str) -> EmailMessage:
    if isinstance(raw, str):
        return email.message_from_string(raw, policy=POLICY)
    return email.message_from_bytes(raw, policy=POLICY)


def to_bytes(message: EmailMessage) -> bytes:
    """Serialise for transport, with CRLF line endings."""
    return message.as_bytes(policy=TRANSPORT_POLICY)


def addresses(message: EmailMessage, *headers: str) -> list[str]:
    values: list[str] = []
    for header in headers:
        values.extend(str(v) for v in message.get_all(header, []))
    return [address for _, address in getaddresses(values) if address]


def recipients(message: EmailMessage) -> list[str]:
    return addresses(message, "To", "Cc", "Bcc")


def plain_body(message: EmailMessage) -> str:
    try:
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            return part.get_content().rstrip("\n")
        part = message.get_body(preferencelist=("html",))
        if part is not None:
            return re.sub(r"<[^>]+>", "", part.get_content()).strip()
    except Exception:
        pass
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode("utf-8", "replace")
    return str(message.get_payload())


def html_body(message: EmailMessage) -> str | None:
    try:
        part = message.get_body(preferencelist=("html",))
        return part.get_content() if part is not None else None
    except Exception:
        return None


def reply_subject(subject: str) -> str:
    subject = (subject or "").strip()
    if RE_PREFIX.match(subject):
        return subject
    return f"Re: {subject}" if subject else "Re:"


def forward_subject(subject: str) -> str:
    """`Report` -> `Fwd: Report`, and `Fwd: Report` stays as it is."""
    subject = (subject or "").strip()
    if FWD_PREFIX.match(subject):
        return subject
    return f"Fwd: {subject}" if subject else "Fwd:"


FORWARD_SEPARATOR = "---------- Forwarded message ----------"


def forward_header_block(original: EmailMessage) -> str:
    """The block every mail client puts above a forwarded body."""
    lines = [FORWARD_SEPARATOR]
    for name in ("From", "Date", "Subject", "To", "Cc"):
        value = str(original.get(name, "")).strip()
        if value:
            lines.append(f"{name}: {value}")
    return "\n".join(lines)


def forward_header_block_html(original: EmailMessage) -> str:
    import html as _html

    rows = []
    for name in ("From", "Date", "Subject", "To", "Cc"):
        value = str(original.get(name, "")).strip()
        if value:
            rows.append(
                f'<tr><td style="color:#6b7280;padding-right:10px;vertical-align:top">'
                f'{name}:</td><td>{_html.escape(value)}</td></tr>')
    return (
        '<div style="margin:16px 0 8px;color:#6b7280">'
        f'{_html.escape(FORWARD_SEPARATOR)}</div>'
        f'<table style="font-size:13px;margin-bottom:12px">{"".join(rows)}</table>'
    )


def forward_count(message: EmailMessage) -> int:
    raw = str(message.get(FORWARD_COUNT_HEADER, "")).strip()
    return int(raw) if raw.isdigit() else 0


def forward_path(message: EmailMessage) -> list[str]:
    return [hop for hop in str(message.get(FORWARD_PATH_HEADER, "")).split() if hop]


def references_chain(parent: EmailMessage, limit: int = 40) -> list[str]:
    chain = str(parent.get("References", "")).split()
    parent_id = str(parent.get("Message-ID", "")).strip()
    if parent_id and parent_id not in chain:
        chain.append(parent_id)
    if len(chain) > limit:  # keep the root, drop the middle, keep the tail
        chain = chain[:1] + chain[-(limit - 1):]
    return chain


def depth(message: EmailMessage) -> int:
    explicit = str(message.get(DEPTH_HEADER, "")).strip()
    if explicit.isdigit():
        return int(explicit)
    return len(str(message.get("References", "")).split())


def quote_body(body: str, author: str, date: str | None) -> str:
    when = date or ""
    try:
        if when:
            when = parsedate_to_datetime(when).strftime("%a, %d %b %Y at %H:%M")
    except (TypeError, ValueError):
        pass
    intro = f"On {when}, {author} wrote:" if when else f"{author} wrote:"
    quoted = "\n".join(f"> {line}" for line in (body or "").splitlines())
    return f"{intro}\n{quoted}"


def build(sender: str, to: Iterable[str] | str, subject: str, body: str,
          cc: Iterable[str] | str | None = None, html: str | None = None,
          headers: dict[str, str] | None = None, domain: str = "tahidromos.test") -> EmailMessage:
    def listify(value):
        if value is None:
            return []
        return [value] if isinstance(value, str) else list(value)

    message = EmailMessage(policy=POLICY)
    message["From"] = sender
    message["To"] = ", ".join(listify(to))
    if cc:
        message["Cc"] = ", ".join(listify(cc))
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(body or "")
    if html:
        message.add_alternative(html, subtype="html")
    for name, value in (headers or {}).items():
        del message[name]
        message[name] = value
    return message


def build_reply(parent: EmailMessage, body: str, sender: str, reply_all: bool = False,
                quote: bool = True, headers: dict[str, str] | None = None,
                domain: str = "tahidromos.test") -> EmailMessage:
    reply_to = str(parent.get("Reply-To") or parent.get("From") or "")
    to = [address for _, address in getaddresses([reply_to]) if address]

    cc: list[str] = []
    if reply_all:
        others = addresses(parent, "To", "Cc")
        seen = {a.lower() for a in to} | {sender.lower()}
        for address in others:
            if address.lower() not in seen:
                cc.append(address)
                seen.add(address.lower())

    text = body
    if quote:
        text = (f"{body}\n\n"
                f"{quote_body(plain_body(parent), str(parent.get('From', '')), str(parent.get('Date', '')))}")

    reply = build(sender, to, reply_subject(str(parent.get("Subject", ""))), text,
                  cc=cc or None, domain=domain)

    parent_id = str(parent.get("Message-ID", "")).strip()
    if parent_id:
        reply["In-Reply-To"] = parent_id
    chain = references_chain(parent)
    if chain:
        reply["References"] = " ".join(chain)
    reply[DEPTH_HEADER] = str(depth(parent) + 1)

    # Carry the forward trail through replies too, so a loop that alternates
    # between a forwarding rule and an auto-responder is still detectable.
    trail = forward_path(parent)
    if trail:
        reply[FORWARD_PATH_HEADER] = " ".join(trail)
    if forward_count(parent):
        reply[FORWARD_COUNT_HEADER] = str(forward_count(parent))

    for name, value in (headers or {}).items():
        del reply[name]
        reply[name] = value
    return reply


def build_forward(original: EmailMessage, sender: str, to: Iterable[str] | str,
                  note: str = "", cc: Iterable[str] | str | None = None,
                  mode: str = "inline", keep_thread: bool = True,
                  headers: dict[str, str] | None = None,
                  domain: str = "tahidromos.test") -> EmailMessage:
    """Forward a message, the way a mail client does.

    `mode="inline"` quotes the original under a forwarded-header block and
    carries its attachments across. `mode="attachment"` attaches the whole
    original as message/rfc822 instead, which is what you want when the bytes
    have to survive untouched.

    A forward is not a reply, so no In-Reply-To is set. `References` is carried
    when `keep_thread` (Gmail's behaviour, and it means a reply to the forward
    still lands in the right conversation), and `X-Forwarded-Message-Id` always
    points back at the original.
    """
    subject = forward_subject(str(original.get("Subject", "")))
    original_text = plain_body(original)
    original_html = html_body(original)

    text = note.rstrip() + "\n\n" if note.strip() else ""
    text += forward_header_block(original)
    if mode == "inline":
        text += "\n\n" + original_text
    else:
        text += "\n\n(the original message is attached)"

    forward = build(sender, to, subject, text, cc=cc, domain=domain)

    if mode == "inline" and original_html:
        import html as _html

        note_html = (f"<div>{_html.escape(note).replace(chr(10), '<br>')}</div>"
                     if note.strip() else "")
        forward.add_alternative(
            f"<html><body>{note_html}{forward_header_block_html(original)}"
            f"<div>{original_html}</div></body></html>", subtype="html")

    if mode == "attachment":
        forward.add_attachment(original)          # message/rfc822
    else:
        _carry_attachments(original, forward)

    original_id = str(original.get("Message-ID", "")).strip()
    if original_id:
        forward[FORWARDED_ID_HEADER] = original_id
    if keep_thread:
        chain = references_chain(original)
        if chain:
            forward["References"] = " ".join(chain)

    forward[DEPTH_HEADER] = str(depth(original) + 1)
    forward[FORWARD_COUNT_HEADER] = str(forward_count(original) + 1)
    path = forward_path(original) + [sender]
    forward[FORWARD_PATH_HEADER] = " ".join(path)

    for name, value in (headers or {}).items():
        del forward[name]
        forward[name] = value
    return forward


def _carry_attachments(original: EmailMessage, target: EmailMessage) -> None:
    """Copy the original's attachments onto the forward.

    A forward that silently drops the invoice is worse than no forward.
    """
    for part in original.iter_attachments():
        content_type = part.get_content_type()
        maintype, _, subtype = content_type.partition("/")
        filename = part.get_filename()

        if maintype == "message":
            # message/* payloads are sub-messages, not bytes
            nested = part.get_payload(0) if part.is_multipart() else None
            if nested is not None:
                target.add_attachment(nested)
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        target.add_attachment(payload, maintype=maintype or "application",
                              subtype=subtype or "octet-stream",
                              filename=filename or f"attachment.{subtype or 'bin'}")


def summarize_thread(messages: list[dict]) -> list[dict]:
    threads: dict[str, dict] = {}
    for message in sorted(messages, key=lambda m: m.get("internal_date") or ""):
        root = message.get("thread_root") or message.get("message_id") or ""
        thread = threads.setdefault(root, {"thread_root": root,
                                           "subject": message.get("subject", ""),
                                           "messages": [], "participants": []})
        thread["messages"].append(message)
        people = [parseaddr(message.get("from", ""))[1]]
        people += list(message.get("to", [])) + list(message.get("cc", []))
        for address in people:
            if address and address not in thread["participants"]:
                thread["participants"].append(address)
    for thread in threads.values():
        thread["message_count"] = len(thread["messages"])
        thread["depth"] = max((m.get("depth", 0) for m in thread["messages"]), default=0)
    return sorted(threads.values(),
                  key=lambda t: t["messages"][-1].get("internal_date") or "", reverse=True)
