"""An SMTP server, written from scratch on asyncio.

Implements the parts of RFC 5321 (and the SMTP AUTH and STARTTLS extensions)
that a development mail server needs: EHLO/HELO, AUTH PLAIN and LOGIN,
MAIL FROM, RCPT TO, DATA, RSET, NOOP, QUIT, VRFY.

Two personalities, decided by `require_auth`:

  * port 25   — open inside your network, no credentials needed, so an app
                can point SMTP_HOST at it and just work
  * port 587  — submission: AUTH required, which is the path a mail client
                or a "reply" button uses
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import ssl
import time
from dataclasses import dataclass, field

log = logging.getLogger("tahidromos.smtp")

MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_LINE = 8192
MAX_RECIPIENTS = 100

ADDRESS = re.compile(r"<\s*([^>]*)\s*>|(\S+)")


def extract_address(argument: str) -> str | None:
    """`FROM:<alice@x.test> SIZE=42` -> `alice@x.test`."""
    _, _, rest = argument.partition(":")
    rest = rest.strip()
    if not rest:
        return None
    match = ADDRESS.match(rest)
    if not match:
        return None
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip()


@dataclass
class Envelope:
    mail_from: str = ""
    recipients: list[str] = field(default_factory=list)
    content: bytes = b""

    def reset(self) -> None:
        self.mail_from = ""
        self.recipients = []
        self.content = b""


class SmtpSession:
    """One client connection."""

    def __init__(self, server: "SmtpServer", reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.peer = writer.get_extra_info("peername")
        self.envelope = Envelope()
        self.username: str | None = None
        self.greeted = False
        self.tls = writer.get_extra_info("sslcontext") is not None

    # -- io -------------------------------------------------------------

    async def send(self, line: str) -> None:
        self.writer.write(line.encode("utf-8", "replace") + b"\r\n")
        await self.writer.drain()

    async def send_multi(self, code: int, lines: list[str]) -> None:
        for index, text in enumerate(lines):
            separator = " " if index == len(lines) - 1 else "-"
            await self.send(f"{code}{separator}{text}")

    async def read_line(self) -> str | None:
        try:
            raw = await self.reader.readuntil(b"\n")
        except asyncio.IncompleteReadError:
            return None
        except asyncio.LimitOverrunError:
            await self.send("500 5.5.2 Line too long")
            return ""
        except (ConnectionResetError, BrokenPipeError):
            return None
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    # -- main loop -------------------------------------------------------

    async def run(self) -> None:
        await self.send(f"220 {self.server.hostname} tahidromos ESMTP ready")
        while True:
            line = await self.read_line()
            if line is None:
                break
            if line == "":
                continue

            verb, _, argument = line.partition(" ")
            verb = verb.upper().strip()
            argument = argument.strip()

            handler = getattr(self, f"do_{verb}", None)
            if handler is None:
                await self.send(f"500 5.5.2 Unrecognised command: {verb}")
                continue
            try:
                if await handler(argument) is False:
                    break
            except Exception:
                log.exception("smtp command failed: %s", verb)
                await self.send("451 4.3.0 Internal error")
        try:
            self.writer.close()
        except Exception:
            pass

    # -- commands --------------------------------------------------------

    async def do_QUIT(self, argument: str) -> bool:
        await self.send(f"221 2.0.0 {self.server.hostname} closing connection")
        return False

    async def do_NOOP(self, argument: str) -> None:
        await self.send("250 2.0.0 OK")

    async def do_RSET(self, argument: str) -> None:
        self.envelope.reset()
        await self.send("250 2.0.0 OK")

    async def do_HELO(self, argument: str) -> None:
        self.greeted = True
        await self.send(f"250 {self.server.hostname}")

    async def do_EHLO(self, argument: str) -> None:
        self.greeted = True
        extensions = [
            f"{self.server.hostname} greets {argument or 'you'}",
            "PIPELINING",
            "8BITMIME",
            "SMTPUTF8",
            "ENHANCEDSTATUSCODES",
            f"SIZE {MAX_MESSAGE_BYTES}",
            "AUTH PLAIN LOGIN",
        ]
        if self.server.tls_context is not None and not self.tls:
            extensions.insert(-1, "STARTTLS")
        await self.send_multi(250, extensions)

    async def do_STARTTLS(self, argument: str) -> None:
        if self.server.tls_context is None:
            await self.send("454 4.7.0 TLS not available")
            return
        await self.send("220 2.0.0 Ready to start TLS")
        try:
            await self.writer.start_tls(self.server.tls_context)
        except Exception:
            log.warning("STARTTLS negotiation failed", exc_info=True)
            return
        self.tls = True
        self.greeted = False
        self.envelope.reset()

    async def do_VRFY(self, argument: str) -> None:
        # A dev server has nothing to hide, and this makes debugging easy.
        address = extract_address("x:" + argument) or argument.strip()
        if address and self.server.store.account_exists(address):
            await self.send(f"250 2.1.5 <{address}>")
        else:
            await self.send("252 2.1.5 Cannot verify, will attempt delivery")

    async def do_AUTH(self, argument: str) -> None:
        if self.username:
            await self.send("503 5.5.1 Already authenticated")
            return
        mechanism, _, initial = argument.partition(" ")
        mechanism = mechanism.upper()

        if mechanism == "PLAIN":
            blob = initial.strip()
            if not blob:
                await self.send("334 ")
                blob = await self.read_line() or ""
            credentials = self._decode(blob)
            if credentials is None:
                await self.send("501 5.5.2 Cannot decode AUTH PLAIN")
                return
            parts = credentials.split("\x00")
            if len(parts) != 3:
                await self.send("501 5.5.2 Malformed AUTH PLAIN")
                return
            await self._finish_auth(parts[1], parts[2])

        elif mechanism == "LOGIN":
            blob = initial.strip()
            if not blob:
                await self.send("334 " + base64.b64encode(b"Username:").decode())
                blob = await self.read_line() or ""
            username = self._decode(blob)
            await self.send("334 " + base64.b64encode(b"Password:").decode())
            password = self._decode(await self.read_line() or "")
            if username is None or password is None:
                await self.send("501 5.5.2 Cannot decode AUTH LOGIN")
                return
            await self._finish_auth(username, password)

        else:
            await self.send("504 5.5.4 Unsupported authentication mechanism")

    @staticmethod
    def _decode(blob: str) -> str | None:
        try:
            return base64.b64decode(blob.strip(), validate=True).decode("utf-8", "replace")
        except Exception:
            return None

    async def _finish_auth(self, username: str, password: str) -> None:
        if self.server.store.authenticate(username, password):
            self.username = username.lower()
            log.info("auth ok user=%s", self.username)
            await self.send("235 2.7.0 Authentication successful")
        else:
            log.info("auth failed user=%s", username)
            await asyncio.sleep(0.3)  # token brute-force speed bump
            await self.send("535 5.7.8 Authentication credentials invalid")

    async def do_MAIL(self, argument: str) -> None:
        if not self.greeted:
            await self.send("503 5.5.1 Send HELO/EHLO first")
            return
        if self.server.require_auth and not self.username:
            await self.send("530 5.7.0 Authentication required")
            return
        if self.envelope.mail_from:
            await self.send("503 5.5.1 Nested MAIL command")
            return
        if not argument.upper().startswith("FROM:"):
            await self.send("501 5.5.4 Syntax: MAIL FROM:<address>")
            return
        address = extract_address(argument)
        if address is None:
            await self.send("501 5.1.7 Bad sender address")
            return
        self.envelope.mail_from = address
        await self.send("250 2.1.0 Sender OK")

    async def do_RCPT(self, argument: str) -> None:
        if not self.envelope.mail_from:
            await self.send("503 5.5.1 Send MAIL FROM first")
            return
        if not argument.upper().startswith("TO:"):
            await self.send("501 5.5.4 Syntax: RCPT TO:<address>")
            return
        address = extract_address(argument)
        if not address or "@" not in address:
            await self.send("501 5.1.3 Bad recipient address")
            return
        if len(self.envelope.recipients) >= MAX_RECIPIENTS:
            await self.send("452 4.5.3 Too many recipients")
            return

        accepted, reason = await self.server.router.check_recipient(address)
        if not accepted:
            await self.send(f"550 5.1.1 {reason}")
            return
        self.envelope.recipients.append(address)
        await self.send("250 2.1.5 Recipient OK")

    async def do_DATA(self, argument: str) -> None:
        if not self.envelope.recipients:
            await self.send("503 5.5.1 Send RCPT TO first")
            return
        await self.send("354 End data with <CR><LF>.<CR><LF>")

        chunks: list[bytes] = []
        size = 0
        too_big = False
        while True:
            try:
                raw = await self.reader.readuntil(b"\n")
            except (asyncio.IncompleteReadError, ConnectionResetError):
                return
            except asyncio.LimitOverrunError:
                too_big = True
                break
            if raw in (b".\r\n", b".\n"):
                break
            if raw.startswith(b".."):        # RFC 5321 §4.5.2 dot-stuffing
                raw = raw[1:]
            size += len(raw)
            if size > MAX_MESSAGE_BYTES:
                too_big = True
            if not too_big:
                chunks.append(raw)

        if too_big:
            self.envelope.reset()
            await self.send("552 5.3.4 Message too large")
            return

        content = b"".join(chunks)
        content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        self.envelope.content = content

        try:
            result = await self.server.router.deliver(
                self.envelope.mail_from, list(self.envelope.recipients), content,
                submitted_by=self.username,
                peer=self.peer[0] if self.peer else "unknown")
        except Exception:
            log.exception("delivery failed")
            self.envelope.reset()
            await self.send("451 4.3.0 Delivery failed")
            return

        self.envelope.reset()
        await self.send(f"250 2.0.0 OK: queued as {result}")


class SmtpServer:
    def __init__(self, store, router, hostname: str, require_auth: bool,
                 tls_context: ssl.SSLContext | None = None):
        self.store = store
        self.router = router
        self.hostname = hostname
        self.require_auth = require_auth
        self.tls_context = tls_context

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = SmtpSession(self, reader, writer)
        try:
            await session.run()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            log.exception("smtp session crashed")
        finally:
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def listen(self, host: str, port: int, tls: bool = False) -> asyncio.Server:
        server = await asyncio.start_server(
            self.handle, host, port, limit=MAX_LINE,
            ssl=self.tls_context if tls else None)
        kind = "submission" if self.require_auth else "smtp"
        log.info("%s listening on %s:%s%s", kind, host, port, " (implicit TLS)" if tls else "")
        return server
