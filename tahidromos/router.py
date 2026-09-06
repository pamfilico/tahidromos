"""Delivery: deciding where an accepted message actually goes.

Three outcomes, in order:

  1. the recipient is a known local mailbox      -> delivered to it
  2. the recipient is on a local domain but new  -> the mailbox is created,
     then delivered (so random per-test addresses just work)
  3. anything else                               -> rewritten to the capture
     mailbox, never sent onward

(3) is the safety net. A dev app that mails a real customer address finds it
in `captured@…` and nowhere near the internet.
"""

from __future__ import annotations

import asyncio
import logging
import time
from email.utils import formatdate, make_msgid

from .config import Config
from .store import Store

log = logging.getLogger("tahidromos.router")


class Router:
    def __init__(self, store: Store, config: Config, auto_create: bool = True):
        self.store = store
        self.config = config
        self.auto_create = auto_create
        self.delivered = 0
        self.captured = 0
        self.listeners: list = []

    # -- recipient policy -------------------------------------------------

    async def check_recipient(self, address: str) -> tuple[bool, str]:
        """Called at RCPT TO time. A dev server accepts almost everything."""
        if "@" not in address:
            return False, "Address must contain a domain"
        return True, ""

    @staticmethod
    def strip_tag(address: str) -> str:
        """`bob+invoices@x.test` -> `bob@x.test` (RFC 5233 subaddressing)."""
        localpart, _, domain = address.partition("@")
        base, plus, _tag = localpart.partition("+")
        return f"{base}@{domain}" if plus and base else address

    def resolve(self, address: str) -> tuple[str, bool]:
        """Return (mailbox address, was_captured).

        A tagged address is normalised first, so `bob+invoices@` can never
        become a mailbox of its own that shadows `bob@`.
        """
        address = self.strip_tag(address.strip().lower())
        if self.store.account_exists(address):
            return address, False

        if self.config.is_local(address):
            if self.auto_create:
                password = self.config.password_for(address) or self.config.default_password
                self.store.create_account(address, password, app="auto",
                                          description="Created on first delivery")
                log.info("auto-created mailbox %s", address)
                return address, False
            return self.config.capture_address, True
        return self.config.capture_address, True

    # -- delivery ---------------------------------------------------------

    def ensure_headers(self, content: bytes, mail_from: str) -> bytes:
        """Stamp Date and Message-ID when the client omitted them.

        Real MTAs do this, and a message without a Message-ID cannot be
        replied to — there is nothing for In-Reply-To to point at.
        """
        content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        head, separator, body = content.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        present = {line.split(b":", 1)[0].strip().lower()
                   for line in lines
                   if b":" in line and not line[:1].isspace()}

        additions: list[bytes] = []
        if b"date" not in present:
            additions.append(f"Date: {formatdate(localtime=True)}".encode())
        if b"message-id" not in present:
            domain = mail_from.rsplit("@", 1)[-1] or self.config.primary_domain
            additions.append(f"Message-ID: {make_msgid(domain=domain)}".encode())
        if not additions:
            return content
        return b"\r\n".join(additions + lines) + separator + body

    def add_received_header(self, content: bytes, mail_from: str, recipient: str,
                            peer: str) -> bytes:
        received = (
            f"Received: from {peer} by {self.config.hostname} (tahidromos)\r\n"
            f"\tfor <{recipient}>; {formatdate(localtime=True)}\r\n"
        ).encode()
        return received + content

    async def deliver(self, mail_from: str, recipients: list[str], content: bytes,
                      submitted_by: str | None = None, peer: str = "unknown") -> str:
        receipt = f"{int(time.time() * 1000):x}"
        content = self.ensure_headers(content, mail_from)
        for recipient in recipients:
            target, captured = self.resolve(recipient)
            raw = self.add_received_header(content, mail_from, recipient, peer)
            uid = self.store.deliver(target, raw, mailbox="INBOX", envelope_to=recipient)
            if uid is None:
                log.warning("could not deliver to %s", target)
                continue
            if captured:
                self.captured += 1
                log.info("captured %s -> %s (uid %s)", recipient, target, uid)
            else:
                self.delivered += 1
                log.info("delivered %s -> %s (uid %s)", mail_from, target, uid)
            for listener in list(self.listeners):
                try:
                    result = listener(raw, recipient, target, uid)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    log.exception("delivery listener failed")

        # keep a copy in the sender's Sent folder when they authenticated
        if submitted_by and self.store.account_exists(submitted_by):
            self.store.deliver(submitted_by, content, mailbox="Sent", flags="\\Seen")
        return receipt
