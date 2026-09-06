"""Inbound-parse webhook simulation.

Postmark, SendGrid and Mailgun all offer an "inbound parse" webhook: mail
arrives at their servers and they POST it to your application. Testing that
locally normally means a public endpoint and a tunnel, because the usual dev
mail tools cannot receive mail at all.

Here, delivery is local, so we can just POST the same shape at your app.

Off unless you set INBOUND_WEBHOOK_URL — the zero-config path stays a plain
mail server with nothing to configure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from . import extract
from . import message as msg

log = logging.getLogger("tahidromos.webhook")

FORMATS = ("postmark", "sendgrid", "mailgun", "raw")


def _addr_list(message: EmailMessage, header: str) -> list[dict]:
    values = [str(v) for v in message.get_all(header, [])]
    return [{"Email": address, "Name": name}
            for name, address in getaddresses(values) if address]


def _headers(message: EmailMessage) -> list[dict]:
    return [{"Name": name, "Value": str(value)} for name, value in message.items()]


def build_payload(raw: bytes, envelope_to: str, mailbox: str, fmt: str) -> dict:
    """Shape one delivered message the way the named provider would."""
    message = msg.parse(raw)
    text = msg.plain_body(message)
    html = msg.html_body(message) or ""
    sender_name, sender_address = parseaddr(str(message.get("From", "")))
    subject = str(message.get("Subject", ""))
    message_id = str(message.get("Message-ID", "")).strip()

    if fmt == "postmark":
        # https://postmarkapp.com/developer/webhooks/inbound-webhook
        return {
            "FromName": sender_name,
            "From": sender_address,
            "FromFull": {"Email": sender_address, "Name": sender_name},
            "To": ", ".join(a["Email"] for a in _addr_list(message, "To")),
            "ToFull": _addr_list(message, "To"),
            "Cc": ", ".join(a["Email"] for a in _addr_list(message, "Cc")),
            "CcFull": _addr_list(message, "Cc"),
            "OriginalRecipient": envelope_to,
            "Subject": subject,
            "MessageID": message_id.strip("<>"),
            "ReplyTo": str(message.get("Reply-To", "")),
            "MailboxHash": _mailbox_hash(envelope_to),
            "Date": str(message.get("Date", "")),
            "TextBody": text,
            "HtmlBody": html,
            "StrippedTextReply": extract.strip_quotes(text),
            "Headers": _headers(message),
            "Attachments": [],
        }

    if fmt == "sendgrid":
        # https://www.twilio.com/docs/sendgrid/for-developers/parsing-email
        return {
            "headers": "\r\n".join(f"{k}: {v}" for k, v in message.items()),
            "dkim": "{@tahidromos.test : pass}",
            "to": ", ".join(a["Email"] for a in _addr_list(message, "To")),
            "cc": ", ".join(a["Email"] for a in _addr_list(message, "Cc")),
            "from": str(message.get("From", "")),
            "sender_ip": "127.0.0.1",
            "subject": subject,
            "text": text,
            "html": html,
            "envelope": json.dumps({"to": [envelope_to], "from": sender_address}),
            "charsets": json.dumps({"to": "UTF-8", "subject": "UTF-8", "from": "UTF-8",
                                    "text": "UTF-8", "html": "UTF-8"}),
            "SPF": "pass",
            "attachments": "0",
        }

    if fmt == "mailgun":
        # https://documentation.mailgun.com/docs/mailgun/user-manual/receive-forward-store/
        return {
            "recipient": envelope_to,
            "sender": sender_address,
            "from": str(message.get("From", "")),
            "subject": subject,
            "body-plain": text,
            "stripped-text": extract.strip_quotes(text),
            "stripped-signature": "",
            "body-html": html,
            "stripped-html": html,
            "attachment-count": 0,
            "timestamp": int(_timestamp(message)),
            "token": message_id.strip("<>")[:50],
            "signature": "development",
            "message-headers": json.dumps([[k, str(v)] for k, v in message.items()]),
            "Message-Id": message_id,
            "In-Reply-To": str(message.get("In-Reply-To", "")),
            "References": str(message.get("References", "")),
        }

    # raw: everything we know, in tahidromos's own shape
    return {
        "mailbox": mailbox,
        "envelope_to": envelope_to,
        "from": sender_address,
        "to": [a["Email"] for a in _addr_list(message, "To")],
        "cc": [a["Email"] for a in _addr_list(message, "Cc")],
        "subject": subject,
        "message_id": message_id,
        "in_reply_to": str(message.get("In-Reply-To", "")),
        "references": str(message.get("References", "")).split(),
        "date": str(message.get("Date", "")),
        "text": text,
        "html": html,
        **extract.summarize(text, html),
        "raw": raw.decode("utf-8", "replace"),
    }


def _mailbox_hash(address: str) -> str:
    """Postmark's plus-address tag, e.g. ticket+abc123@ -> "abc123"."""
    localpart = address.split("@", 1)[0]
    _, plus, tag = localpart.partition("+")
    return tag if plus else ""


def _timestamp(message: EmailMessage) -> float:
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(str(message["Date"])).timestamp()
    except Exception:
        import time

        return time.time()


class WebhookSender:
    """Fire-and-retry POSTs, so a slow app under test does not lose deliveries."""

    def __init__(self, url: str, fmt: str = "postmark", secret: str = "",
                 only: str = "", timeout: float = 10.0, retries: int = 3):
        self.url = url
        self.fmt = fmt if fmt in FORMATS else "postmark"
        self.secret = secret
        self.only = re.compile(only) if only else None
        self.timeout = timeout
        self.retries = retries
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None

    def wants(self, address: str) -> bool:
        return self.only is None or bool(self.only.search(address))

    async def deliver(self, raw: bytes, envelope_to: str, mailbox: str) -> None:
        if not self.wants(envelope_to) and not self.wants(mailbox):
            return
        payload = build_payload(raw, envelope_to, mailbox, self.fmt)
        body = json.dumps(payload).encode()

        headers = {"content-type": "application/json",
                   "user-agent": "tahidromos-inbound/1.0",
                   "x-tahidromos-format": self.fmt}
        if self.secret:
            headers["x-tahidromos-signature"] = self.secret

        for attempt in range(1, self.retries + 1):
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._post, body, headers)
                self.sent += 1
                log.info("inbound webhook -> %s (%s) for %s", self.url, self.fmt, envelope_to)
                return
            except Exception as exc:
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                if attempt == self.retries:
                    self.failed += 1
                    log.warning("inbound webhook failed after %s attempts: %s",
                                attempt, self.last_error)
                    return
                await asyncio.sleep(0.5 * attempt)

    def _post(self, body: bytes, headers: dict) -> None:
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status >= 400:
                raise urllib.error.HTTPError(self.url, response.status, "webhook rejected",
                                             response.headers, None)

    def status(self) -> dict:
        return {"enabled": True, "url": self.url, "format": self.fmt,
                "only": self.only.pattern if self.only else None,
                "sent": self.sent, "failed": self.failed, "last_error": self.last_error}
