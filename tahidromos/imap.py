"""An IMAP4rev1 server, written from scratch on asyncio.

Enough of RFC 3501 for real clients and for Python's imaplib: CAPABILITY,
LOGIN, LIST, SELECT/EXAMINE, STATUS, CREATE, DELETE, APPEND, SEARCH, FETCH,
STORE, COPY, EXPUNGE, CLOSE, plus the UID variants and IDLE.

Two details matter more than the rest, because getting them wrong is what
makes a mail server unusable for testing:

  * SEARCH UNSEEN really consults the flags, so an auto-responder can find
    what it has not answered yet
  * BODY.PEEK[] does not set \\Seen, so reading a mailbox in the UI cannot
    change what a test observes
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
from datetime import datetime, timezone

log = logging.getLogger("tahidromos.imap")

MAX_LINE = 10 * 1024 * 1024

CAPABILITIES = "IMAP4rev1 AUTH=PLAIN LOGINDISABLED-NOT UIDPLUS IDLE LITERAL+ CHILDREN"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

LITERAL = re.compile(rb"\{(\d+)(\+?)\}\r?\n$")


def internal_date(iso: str) -> str:
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        when = datetime.now(timezone.utc)
    return (f'{when.day:02d}-{MONTHS[when.month - 1]}-{when.year} '
            f'{when.hour:02d}:{when.minute:02d}:{when.second:02d} +0000')


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_sequence(spec: str, universe: list[int]) -> list[int]:
    """`1:3,7,9:*` -> the uids in `universe` that it covers."""
    if not universe:
        return []
    highest = max(universe)
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            low, _, high = part.partition(":")
            start = highest if low.strip() == "*" else int(low)
            end = highest if high.strip() == "*" else int(high)
            if start > end:
                start, end = end, start
            wanted.update(range(start, end + 1))
        else:
            wanted.add(highest if part == "*" else int(part))
    return sorted(uid for uid in universe if uid in wanted)


def split_atoms(text: str) -> list[str]:
    """Split an IMAP argument list, honouring quotes and parentheses."""
    atoms: list[str] = []
    current = ""
    depth = 0
    in_quotes = False
    escape = False
    for char in text:
        if escape:
            current += char
            escape = False
            continue
        if char == "\\" and in_quotes:
            escape = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            continue
        if not in_quotes:
            if char == "(":
                depth += 1
                if depth == 1:
                    continue
            elif char == ")":
                depth -= 1
                if depth == 0:
                    atoms.append(current)
                    current = ""
                    continue
            elif char == " " and depth == 0:
                if current:
                    atoms.append(current)
                    current = ""
                continue
        current += char
    if current:
        atoms.append(current)
    return atoms


class ImapSession:
    def __init__(self, server: "ImapServer", reader, writer):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.store = server.store
        self.address: str | None = None
        self.mailbox: str | None = None
        self.readonly = False
        self.idling = False

    # -- io -------------------------------------------------------------

    async def send(self, line: str) -> None:
        self.writer.write(line.encode("utf-8", "replace") + b"\r\n")
        await self.writer.drain()

    async def send_bytes(self, payload: bytes) -> None:
        self.writer.write(payload)
        await self.writer.drain()

    async def read_command(self) -> tuple[str, bytes] | None:
        """Read one command, pulling in any literals it announces."""
        try:
            raw = await self.reader.readuntil(b"\n")
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.LimitOverrunError):
            return None
        if not raw:
            return None

        literal_payload = b""
        while True:
            match = LITERAL.search(raw)
            if not match:
                break
            length = int(match.group(1))
            if match.group(2) != b"+":
                await self.send("+ Ready for literal data")
            try:
                literal_payload = await self.reader.readexactly(length)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                return None
            try:
                more = await self.reader.readuntil(b"\n")
            except (asyncio.IncompleteReadError, ConnectionResetError):
                more = b"\r\n"
            raw = raw[:match.start()] + b'"<literal>"' + more
        return raw.decode("utf-8", "replace").rstrip("\r\n"), literal_payload

    # -- main loop -------------------------------------------------------

    async def run(self) -> None:
        await self.send(f"* OK [CAPABILITY {CAPABILITIES}] tahidromos IMAP ready")
        while True:
            command = await self.read_command()
            if command is None:
                break
            line, literal = command
            if not line.strip():
                continue

            tag, _, rest = line.partition(" ")
            verb, _, argument = rest.partition(" ")
            verb = verb.upper().strip()
            argument = argument.strip()

            if verb == "UID":
                sub, _, argument = argument.partition(" ")
                handler = getattr(self, f"uid_{sub.upper().strip()}", None)
                argument = argument.strip()
            else:
                handler = getattr(self, f"do_{verb}", None)

            if handler is None:
                await self.send(f"{tag} BAD Unknown command {verb}")
                continue
            try:
                if await handler(tag, argument, literal) is False:
                    break
            except Exception:
                log.exception("imap command failed: %s", verb)
                await self.send(f"{tag} NO Internal error")
        try:
            self.writer.close()
        except Exception:
            pass

    # -- helpers ---------------------------------------------------------

    def require_auth(self) -> bool:
        return self.address is not None

    def uids(self) -> list[int]:
        return self.store.uids(self.address, self.mailbox) if self.mailbox else []

    async def unauthenticated(self, tag: str) -> None:
        await self.send(f"{tag} NO Please log in first")

    async def unselected(self, tag: str) -> None:
        await self.send(f"{tag} NO No mailbox selected")

    # -- any state -------------------------------------------------------

    async def do_CAPABILITY(self, tag, argument, literal) -> None:
        await self.send(f"* CAPABILITY {CAPABILITIES}")
        await self.send(f"{tag} OK CAPABILITY completed")

    async def do_NOOP(self, tag, argument, literal) -> None:
        await self.send(f"{tag} OK NOOP completed")

    async def do_LOGOUT(self, tag, argument, literal) -> bool:
        await self.send("* BYE tahidromos signing off")
        await self.send(f"{tag} OK LOGOUT completed")
        return False

    async def do_LOGIN(self, tag, argument, literal) -> None:
        parts = split_atoms(argument)
        if len(parts) < 2:
            await self.send(f"{tag} BAD Syntax: LOGIN username password")
            return
        username, password = parts[0], parts[1]
        if literal and username == "<literal>":
            username = literal.decode("utf-8", "replace")
        if self.store.authenticate(username, password):
            self.address = username.lower()
            log.info("imap login ok user=%s", self.address)
            await self.send(f"{tag} OK [CAPABILITY {CAPABILITIES}] LOGIN completed")
        else:
            log.info("imap login failed user=%s", username)
            await asyncio.sleep(0.3)
            await self.send(f"{tag} NO LOGIN failed: invalid credentials")

    async def do_AUTHENTICATE(self, tag, argument, literal) -> None:
        # imaplib only needs LOGIN for our purposes; keep the error honest.
        await self.send(f"{tag} NO AUTHENTICATE not supported, use LOGIN")

    # -- authenticated state ---------------------------------------------

    async def do_LIST(self, tag, argument, literal) -> None:
        if not self.require_auth():
            return await self.unauthenticated(tag)
        for name in self.store.mailboxes(self.address):
            attributes = "\\HasNoChildren" + (" \\Noinferiors" if name == "INBOX" else "")
            await self.send(f'* LIST ({attributes}) "." {quote(name)}')
        await self.send(f"{tag} OK LIST completed")

    do_LSUB = do_LIST

    async def do_CREATE(self, tag, argument, literal) -> None:
        if not self.require_auth():
            return await self.unauthenticated(tag)
        name = (split_atoms(argument) or [""])[0]
        if not name:
            await self.send(f"{tag} BAD Syntax: CREATE mailbox")
            return
        self.store.create_mailbox(self.address, name)
        await self.send(f"{tag} OK CREATE completed")

    async def do_DELETE(self, tag, argument, literal) -> None:
        if not self.require_auth():
            return await self.unauthenticated(tag)
        name = (split_atoms(argument) or [""])[0]
        if self.store.delete_mailbox(self.address, name):
            await self.send(f"{tag} OK DELETE completed")
        else:
            await self.send(f"{tag} NO Cannot delete {name}")

    async def do_STATUS(self, tag, argument, literal) -> None:
        if not self.require_auth():
            return await self.unauthenticated(tag)
        parts = split_atoms(argument)
        name = parts[0] if parts else "INBOX"
        status = self.store.mailbox_status(self.address, name)
        if status is None:
            await self.send(f"{tag} NO Mailbox does not exist")
            return
        await self.send(
            f'* STATUS {quote(name)} (MESSAGES {status["exists"]} UNSEEN {status["unseen"]} '
            f'UIDNEXT {status["uid_next"]} UIDVALIDITY {status["uid_validity"]})')
        await self.send(f"{tag} OK STATUS completed")

    async def _select(self, tag: str, argument: str, readonly: bool) -> None:
        if not self.require_auth():
            return await self.unauthenticated(tag)
        name = (split_atoms(argument) or ["INBOX"])[0]
        status = self.store.mailbox_status(self.address, name)
        if status is None:
            await self.send(f"{tag} NO Mailbox does not exist")
            return
        self.mailbox = name
        self.readonly = readonly
        await self.send(f'* {status["exists"]} EXISTS')
        await self.send("* 0 RECENT")
        await self.send("* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)")
        await self.send("* OK [PERMANENTFLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)] ok")
        await self.send(f'* OK [UIDVALIDITY {status["uid_validity"]}] UIDs valid')
        await self.send(f'* OK [UIDNEXT {status["uid_next"]}] Predicted next UID')
        await self.send(f'{tag} OK [{"READ-ONLY" if readonly else "READ-WRITE"}] '
                        f'{"EXAMINE" if readonly else "SELECT"} completed')

    async def do_SELECT(self, tag, argument, literal) -> None:
        await self._select(tag, argument, readonly=False)

    async def do_EXAMINE(self, tag, argument, literal) -> None:
        await self._select(tag, argument, readonly=True)

    async def do_APPEND(self, tag, argument, literal) -> None:
        if not self.require_auth():
            return await self.unauthenticated(tag)
        parts = split_atoms(argument)
        name = parts[0] if parts else "INBOX"
        flags = ""
        for part in parts[1:]:
            if part.startswith("\\"):
                flags = part
        if not literal:
            await self.send(f"{tag} BAD APPEND needs a literal")
            return
        uid = self.store.deliver(self.address, literal, mailbox=name, flags=flags)
        if uid is None:
            await self.send(f"{tag} NO Cannot append to {name}")
            return
        await self.send(f"{tag} OK [APPENDUID 1 {uid}] APPEND completed")

    # -- selected state ---------------------------------------------------

    def _search(self, criteria: str) -> list[int]:
        """A small but honest subset of the SEARCH grammar."""
        summaries = self.store.summaries(self.address, self.mailbox)
        tokens = split_atoms(criteria.upper()) if criteria.strip() else ["ALL"]
        original = split_atoms(criteria) if criteria.strip() else ["ALL"]

        selected = summaries
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in ("ALL", "RECENT"):
                pass
            elif token == "UNSEEN" or token == "NEW":
                selected = [m for m in selected if not m["seen"]]
            elif token == "SEEN":
                selected = [m for m in selected if m["seen"]]
            elif token == "DELETED":
                selected = [m for m in selected if "\\Deleted" in m["flags"]]
            elif token == "UNDELETED":
                selected = [m for m in selected if "\\Deleted" not in m["flags"]]
            elif token == "ANSWERED":
                selected = [m for m in selected if "\\Answered" in m["flags"]]
            elif token in ("SUBJECT", "FROM", "TO", "BODY", "TEXT", "HEADER"):
                offset = 2 if token == "HEADER" else 1
                value = original[index + offset] if index + offset < len(original) else ""
                needle = value.lower()
                if token == "SUBJECT":
                    selected = [m for m in selected if needle in m["subject"].lower()]
                elif token == "FROM":
                    selected = [m for m in selected if needle in m["from"].lower()]
                elif token == "TO":
                    selected = [m for m in selected
                                if any(needle in a.lower() for a in m["to"])]
                else:
                    selected = [
                        m for m in selected
                        if needle in (self.store.raw(self.address, int(m["uid"]), self.mailbox)
                                      or b"").decode("utf-8", "replace").lower()]
                index += offset
            index += 1
        return [int(m["uid"]) for m in selected]

    async def do_SEARCH(self, tag, argument, literal) -> None:
        if not self.mailbox:
            return await self.unselected(tag)
        uids = self._search(argument)
        order = self.uids()
        sequence = [str(order.index(uid) + 1) for uid in uids if uid in order]
        await self.send("* SEARCH" + ("" if not sequence else " " + " ".join(sequence)))
        await self.send(f"{tag} OK SEARCH completed")

    async def uid_SEARCH(self, tag, argument, literal) -> None:
        if not self.mailbox:
            return await self.unselected(tag)
        uids = self._search(argument)
        await self.send("* SEARCH" + ("" if not uids else " " + " ".join(map(str, uids))))
        await self.send(f"{tag} OK UID SEARCH completed")

    async def _fetch(self, tag: str, argument: str, by_uid: bool) -> None:
        if not self.mailbox:
            return await self.unselected(tag)
        sequence_spec, _, items = argument.partition(" ")
        items_upper = items.upper()
        order = self.uids()

        if by_uid:
            targets = parse_sequence(sequence_spec, order)
        else:
            positions = parse_sequence(sequence_spec, list(range(1, len(order) + 1)))
            targets = [order[p - 1] for p in positions if 0 < p <= len(order)]

        wants_body = "BODY[" in items_upper or "BODY.PEEK[" in items_upper or "RFC822" in items_upper
        peek = "BODY.PEEK[" in items_upper
        wants_flags = "FLAGS" in items_upper
        wants_date = "INTERNALDATE" in items_upper
        wants_size = "RFC822.SIZE" in items_upper
        wants_envelope = "ENVELOPE" in items_upper

        for uid in targets:
            summary = self.store.message(self.address, uid, self.mailbox)
            if summary is None:
                continue
            position = order.index(uid) + 1

            parts: list[str] = []
            if by_uid or "UID" in items_upper:
                parts.append(f"UID {uid}")
            if wants_flags:
                parts.append(f'FLAGS ({" ".join(summary["flags"])})')
            if wants_date:
                parts.append(f'INTERNALDATE "{internal_date(summary["internal_date"])}"')
            if wants_size:
                parts.append(f'RFC822.SIZE {summary["size"]}')
            if wants_envelope:
                parts.append(f'ENVELOPE ({quote(summary["subject"])})')

            if wants_body:
                raw = self.store.raw(self.address, uid, self.mailbox) or b""
                header = f'* {position} FETCH ({" ".join(parts + ["BODY[] {%d}" % len(raw)])}'
                await self.send_bytes(header.encode() + b"\r\n")
                await self.send_bytes(raw)
                await self.send_bytes(b")\r\n")
                if not peek and not self.readonly:
                    self.store.set_flags(self.address, uid, add=["\\Seen"], mailbox=self.mailbox)
            else:
                await self.send(f'* {position} FETCH ({" ".join(parts)})')

        await self.send(f'{tag} OK {"UID " if by_uid else ""}FETCH completed')

    async def do_FETCH(self, tag, argument, literal) -> None:
        await self._fetch(tag, argument, by_uid=False)

    async def uid_FETCH(self, tag, argument, literal) -> None:
        await self._fetch(tag, argument, by_uid=True)

    async def _store(self, tag: str, argument: str, by_uid: bool) -> None:
        if not self.mailbox:
            return await self.unselected(tag)
        if self.readonly:
            await self.send(f"{tag} NO Mailbox is read-only")
            return
        sequence_spec, _, rest = argument.partition(" ")
        operation, _, flag_list = rest.partition(" ")
        operation = operation.upper()
        flags = [f for f in split_atoms(flag_list) if f]

        order = self.uids()
        if by_uid:
            targets = parse_sequence(sequence_spec, order)
        else:
            positions = parse_sequence(sequence_spec, list(range(1, len(order) + 1)))
            targets = [order[p - 1] for p in positions if 0 < p <= len(order)]

        for uid in targets:
            if operation.startswith("+FLAGS"):
                current = self.store.set_flags(self.address, uid, add=flags, mailbox=self.mailbox)
            elif operation.startswith("-FLAGS"):
                current = self.store.set_flags(self.address, uid, remove=flags,
                                               mailbox=self.mailbox)
            else:  # FLAGS — replace outright
                existing = self.store.message(self.address, uid, self.mailbox)
                current = self.store.set_flags(
                    self.address, uid, add=flags,
                    remove=[f for f in (existing["flags"] if existing else []) if f not in flags],
                    mailbox=self.mailbox)
            if current is not None and not operation.endswith(".SILENT"):
                position = order.index(uid) + 1
                suffix = f" UID {uid}" if by_uid else ""
                await self.send(f'* {position} FETCH (FLAGS ({" ".join(current)}){suffix})')

        await self.send(f'{tag} OK {"UID " if by_uid else ""}STORE completed')

    async def do_STORE(self, tag, argument, literal) -> None:
        await self._store(tag, argument, by_uid=False)

    async def uid_STORE(self, tag, argument, literal) -> None:
        await self._store(tag, argument, by_uid=True)

    async def _copy(self, tag: str, argument: str, by_uid: bool) -> None:
        if not self.mailbox:
            return await self.unselected(tag)
        sequence_spec, _, target = argument.partition(" ")
        target = (split_atoms(target) or ["INBOX"])[0]
        order = self.uids()
        targets = (parse_sequence(sequence_spec, order) if by_uid else
                   [order[p - 1] for p in parse_sequence(sequence_spec,
                                                         list(range(1, len(order) + 1)))
                    if 0 < p <= len(order)])
        for uid in targets:
            raw = self.store.raw(self.address, uid, self.mailbox)
            if raw is not None:
                self.store.deliver(self.address, raw, mailbox=target)
        await self.send(f'{tag} OK {"UID " if by_uid else ""}COPY completed')

    async def do_COPY(self, tag, argument, literal) -> None:
        await self._copy(tag, argument, by_uid=False)

    async def uid_COPY(self, tag, argument, literal) -> None:
        await self._copy(tag, argument, by_uid=True)

    async def do_EXPUNGE(self, tag, argument, literal) -> None:
        if not self.mailbox:
            return await self.unselected(tag)
        removed = self.store.expunge(self.address, self.mailbox)
        for _ in range(removed):
            await self.send("* 1 EXPUNGE")
        await self.send(f"{tag} OK EXPUNGE completed")

    async def do_CLOSE(self, tag, argument, literal) -> None:
        if self.mailbox and not self.readonly:
            self.store.expunge(self.address, self.mailbox)
        self.mailbox = None
        await self.send(f"{tag} OK CLOSE completed")

    async def do_CHECK(self, tag, argument, literal) -> None:
        await self.send(f"{tag} OK CHECK completed")

    async def do_IDLE(self, tag, argument, literal) -> None:
        if not self.mailbox:
            return await self.unselected(tag)
        await self.send("+ idling")
        self.idling = True
        try:
            while True:
                line = await self.reader.readuntil(b"\n")
                if line.strip().upper() == b"DONE":
                    break
        except (asyncio.IncompleteReadError, ConnectionResetError):
            return
        finally:
            self.idling = False
        await self.send(f"{tag} OK IDLE terminated")


class ImapServer:
    def __init__(self, store, tls_context: ssl.SSLContext | None = None):
        self.store = store
        self.tls_context = tls_context

    async def handle(self, reader, writer) -> None:
        session = ImapSession(self, reader, writer)
        try:
            await session.run()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            log.exception("imap session crashed")
        finally:
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def listen(self, host: str, port: int, tls: bool = False) -> asyncio.Server:
        server = await asyncio.start_server(
            self.handle, host, port, limit=MAX_LINE,
            ssl=self.tls_context if tls else None)
        log.info("imap listening on %s:%s%s", host, port, " (implicit TLS)" if tls else "")
        return server
