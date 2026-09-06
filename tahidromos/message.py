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
DEPTH_HEADER = "X-Tahidromos-Depth"


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

    for name, value in (headers or {}).items():
        del reply[name]
        reply[name] = value
    return reply


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
