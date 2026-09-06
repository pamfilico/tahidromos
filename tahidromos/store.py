"""The message store.

SQLite holds accounts, mailboxes, messages and flags. IMAP's model is simple
enough to map directly: every mailbox owns a monotonic UID counter, every
message keeps the exact bytes it was delivered with, and flags live in their
own table so a message can be marked read without rewriting it.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime

POLICY = email.policy.default

DEFAULT_FOLDERS = ("INBOX", "Sent", "Drafts", "Trash", "Junk", "Archive")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY,
    address     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    secret      TEXT NOT NULL,
    app         TEXT NOT NULL DEFAULT '',
    is_bot      INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mailboxes (
    id         INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    uid_next   INTEGER NOT NULL DEFAULT 1,
    uid_validity INTEGER NOT NULL,
    UNIQUE (account_id, name)
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY,
    mailbox_id    INTEGER NOT NULL REFERENCES mailboxes(id) ON DELETE CASCADE,
    uid           INTEGER NOT NULL,
    raw           BLOB NOT NULL,
    size          INTEGER NOT NULL,
    internal_date REAL NOT NULL,
    message_id    TEXT NOT NULL DEFAULT '',
    in_reply_to   TEXT NOT NULL DEFAULT '',
    references_   TEXT NOT NULL DEFAULT '',
    thread_root   TEXT NOT NULL DEFAULT '',
    subject       TEXT NOT NULL DEFAULT '',
    from_addr     TEXT NOT NULL DEFAULT '',
    to_addrs      TEXT NOT NULL DEFAULT '',
    cc_addrs      TEXT NOT NULL DEFAULT '',
    envelope_to   TEXT NOT NULL DEFAULT '',
    snippet       TEXT NOT NULL DEFAULT '',
    flags         TEXT NOT NULL DEFAULT '',
    UNIQUE (mailbox_id, uid)
);

CREATE INDEX IF NOT EXISTS messages_mailbox ON messages(mailbox_id, uid);
CREATE INDEX IF NOT EXISTS messages_thread  ON messages(thread_root);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------


def hash_secret(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_secret(stored: str, password: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2":
            return False
        expected = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                       bytes.fromhex(salt_hex), 120_000)
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


# --------------------------------------------------------------------------
# parsed headers
# --------------------------------------------------------------------------


@dataclass
class Parsed:
    message_id: str
    in_reply_to: str
    references: list[str]
    thread_root: str
    subject: str
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    date: float
    snippet: str


def _addresses(message: EmailMessage, header: str) -> list[str]:
    values = [str(v) for v in message.get_all(header, [])]
    return [address for _, address in getaddresses(values) if address]


def parse_headers(raw: bytes) -> Parsed:
    message = email.message_from_bytes(raw, policy=POLICY)
    message_id = str(message.get("Message-ID", "")).strip()
    in_reply_to = str(message.get("In-Reply-To", "")).strip()
    references = str(message.get("References", "")).split()

    # RFC 5322 §3.6.4: the first entry of References is the thread root.
    if references:
        thread_root = references[0]
    elif in_reply_to:
        thread_root = in_reply_to
    else:
        thread_root = message_id

    when = time.time()
    if message.get("Date"):
        try:
            when = parsedate_to_datetime(str(message["Date"])).timestamp()
        except (TypeError, ValueError):
            pass

    return Parsed(
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        thread_root=thread_root,
        subject=str(message.get("Subject", "")),
        from_addr=(_addresses(message, "From") or [""])[0],
        to_addrs=_addresses(message, "To"),
        cc_addrs=_addresses(message, "Cc"),
        date=when,
        snippet=_snippet(message),
    )


def _snippet(message: EmailMessage, length: int = 160) -> str:
    """A short preview for the message list, computed once at delivery."""
    try:
        part = message.get_body(preferencelist=("plain", "html"))
        text = part.get_content() if part is not None else ""
    except Exception:
        payload = message.get_payload(decode=True)
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text[:length]


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------


class Store:
    """Thread-safe SQLite store. All public methods take addresses, not ids."""

    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    # -- connections ----------------------------------------------------

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    # -- accounts -------------------------------------------------------

    def create_account(self, address: str, password: str, app: str = "",
                       is_bot: bool = False, description: str = "") -> int:
        address = address.strip().lower()
        with self._write_lock:
            connection = self.connection()
            row = connection.execute("SELECT id FROM accounts WHERE address = ?",
                                     (address,)).fetchone()
            if row:
                account_id = row["id"]
                connection.execute(
                    "UPDATE accounts SET secret = ?, app = ?, is_bot = ?, description = ? "
                    "WHERE id = ?",
                    (hash_secret(password), app, int(is_bot), description, account_id))
            else:
                cursor = connection.execute(
                    "INSERT INTO accounts (address, secret, app, is_bot, description, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (address, hash_secret(password), app, int(is_bot), description, time.time()))
                account_id = cursor.lastrowid
            for folder in DEFAULT_FOLDERS:
                self._ensure_mailbox(connection, account_id, folder)
            return account_id

    def account_id(self, address: str) -> int | None:
        row = self.connection().execute("SELECT id FROM accounts WHERE address = ?",
                                        (address.strip().lower(),)).fetchone()
        return row["id"] if row else None

    def account_exists(self, address: str) -> bool:
        return self.account_id(address) is not None

    def authenticate(self, address: str, password: str) -> bool:
        row = self.connection().execute("SELECT secret FROM accounts WHERE address = ?",
                                        (address.strip().lower(),)).fetchone()
        return bool(row) and verify_secret(row["secret"], password)

    def accounts(self) -> list[dict]:
        rows = self.connection().execute(
            "SELECT address, app, is_bot, description FROM accounts ORDER BY app, address")
        return [{"address": r["address"], "app": r["app"],
                 "is_bot": bool(r["is_bot"]), "description": r["description"]} for r in rows]

    def delete_account(self, address: str) -> bool:
        with self._write_lock:
            cursor = self.connection().execute("DELETE FROM accounts WHERE address = ?",
                                               (address.strip().lower(),))
            return cursor.rowcount > 0

    # -- mailboxes ------------------------------------------------------

    def _ensure_mailbox(self, connection: sqlite3.Connection, account_id: int,
                        name: str) -> int:
        row = connection.execute(
            "SELECT id FROM mailboxes WHERE account_id = ? AND name = ?",
            (account_id, name)).fetchone()
        if row:
            return row["id"]
        cursor = connection.execute(
            "INSERT INTO mailboxes (account_id, name, uid_next, uid_validity) VALUES (?, ?, 1, ?)",
            (account_id, name, int(time.time())))
        return cursor.lastrowid

    def create_mailbox(self, address: str, name: str) -> bool:
        account_id = self.account_id(address)
        if account_id is None:
            return False
        with self._write_lock:
            self._ensure_mailbox(self.connection(), account_id, name)
        return True

    def delete_mailbox(self, address: str, name: str) -> bool:
        if name.upper() == "INBOX":
            return False
        account_id = self.account_id(address)
        if account_id is None:
            return False
        with self._write_lock:
            cursor = self.connection().execute(
                "DELETE FROM mailboxes WHERE account_id = ? AND name = ?", (account_id, name))
            return cursor.rowcount > 0

    def mailbox_id(self, address: str, name: str) -> int | None:
        account_id = self.account_id(address)
        if account_id is None:
            return None
        row = self.connection().execute(
            "SELECT id FROM mailboxes WHERE account_id = ? AND name = ? COLLATE NOCASE",
            (account_id, name)).fetchone()
        return row["id"] if row else None

    def mailboxes(self, address: str) -> list[str]:
        account_id = self.account_id(address)
        if account_id is None:
            return []
        rows = self.connection().execute(
            "SELECT name FROM mailboxes WHERE account_id = ? ORDER BY "
            "CASE WHEN name = 'INBOX' THEN 0 ELSE 1 END, name", (account_id,))
        return [row["name"] for row in rows]

    def mailbox_status(self, address: str, name: str) -> dict | None:
        mailbox = self.mailbox_id(address, name)
        if mailbox is None:
            return None
        connection = self.connection()
        row = connection.execute(
            "SELECT uid_next, uid_validity FROM mailboxes WHERE id = ?", (mailbox,)).fetchone()
        total = connection.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE mailbox_id = ?", (mailbox,)).fetchone()["n"]
        unseen = connection.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE mailbox_id = ? "
            "AND instr(flags, '\\Seen') = 0", (mailbox,)).fetchone()["n"]
        return {"name": name, "exists": total, "unseen": unseen,
                "uid_next": row["uid_next"], "uid_validity": row["uid_validity"]}

    # -- messages -------------------------------------------------------

    def deliver(self, address: str, raw: bytes, mailbox: str = "INBOX",
                flags: str = "", envelope_to: str = "") -> int | None:
        """Append a message and return its new UID."""
        with self._write_lock:
            connection = self.connection()
            account_id = self.account_id(address)
            if account_id is None:
                return None
            mailbox_id = self._ensure_mailbox(connection, account_id, mailbox)
            row = connection.execute("SELECT uid_next FROM mailboxes WHERE id = ?",
                                     (mailbox_id,)).fetchone()
            uid = row["uid_next"]
            parsed = parse_headers(raw)
            connection.execute(
                "INSERT INTO messages (mailbox_id, uid, raw, size, internal_date, message_id,"
                " in_reply_to, references_, thread_root, subject, from_addr, to_addrs, cc_addrs,"
                " envelope_to, snippet, flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mailbox_id, uid, raw, len(raw), time.time(), parsed.message_id,
                 parsed.in_reply_to, " ".join(parsed.references), parsed.thread_root,
                 parsed.subject, parsed.from_addr, ",".join(parsed.to_addrs),
                 ",".join(parsed.cc_addrs), envelope_to or address, parsed.snippet, flags))
            connection.execute("UPDATE mailboxes SET uid_next = ? WHERE id = ?",
                               (uid + 1, mailbox_id))
            return uid

    def _rows(self, address: str, mailbox: str, where: str = "", params: tuple = ()) -> list:
        mailbox_id = self.mailbox_id(address, mailbox)
        if mailbox_id is None:
            return []
        query = ("SELECT uid, size, internal_date, message_id, in_reply_to, references_,"
                 " thread_root, subject, from_addr, to_addrs, cc_addrs, envelope_to,"
                 " snippet, flags FROM messages WHERE mailbox_id = ?")
        if where:
            query += f" AND {where}"
        query += " ORDER BY uid"
        return list(self.connection().execute(query, (mailbox_id, *params)))

    def uids(self, address: str, mailbox: str = "INBOX") -> list[int]:
        return [row["uid"] for row in self._rows(address, mailbox)]

    def unseen_uids(self, address: str, mailbox: str = "INBOX") -> list[int]:
        return [row["uid"] for row in
                self._rows(address, mailbox, "instr(flags, '\\Seen') = 0")]

    def summaries(self, address: str, mailbox: str = "INBOX") -> list[dict]:
        return [self._summary(row, mailbox) for row in self._rows(address, mailbox)]

    @staticmethod
    def _summary(row, mailbox: str) -> dict:
        flags = row["flags"].split()
        return {
            "uid": str(row["uid"]),
            "mailbox": mailbox,
            "size": row["size"],
            "internal_date": datetime.fromtimestamp(row["internal_date"], timezone.utc).isoformat(),
            "message_id": row["message_id"],
            "in_reply_to": row["in_reply_to"] or None,
            "references": row["references_"].split(),
            "thread_root": row["thread_root"],
            "depth": len(row["references_"].split()),
            "subject": row["subject"],
            "from": row["from_addr"],
            "to": [a for a in row["to_addrs"].split(",") if a],
            "cc": [a for a in row["cc_addrs"].split(",") if a],
            "envelope_to": row["envelope_to"],
            "snippet": row["snippet"],
            "flags": flags,
            "seen": "\\Seen" in flags,
        }

    def raw(self, address: str, uid: int, mailbox: str = "INBOX") -> bytes | None:
        mailbox_id = self.mailbox_id(address, mailbox)
        if mailbox_id is None:
            return None
        row = self.connection().execute(
            "SELECT raw FROM messages WHERE mailbox_id = ? AND uid = ?",
            (mailbox_id, uid)).fetchone()
        return bytes(row["raw"]) if row else None

    def message(self, address: str, uid: int, mailbox: str = "INBOX") -> dict | None:
        rows = self._rows(address, mailbox, "uid = ?", (uid,))
        if not rows:
            return None
        summary = self._summary(rows[0], mailbox)
        raw = self.raw(address, uid, mailbox)
        summary["raw"] = raw.decode("utf-8", "replace") if raw else ""
        return summary

    def set_flags(self, address: str, uid: int, add: list[str] = (), remove: list[str] = (),
                  mailbox: str = "INBOX") -> list[str] | None:
        with self._write_lock:
            mailbox_id = self.mailbox_id(address, mailbox)
            if mailbox_id is None:
                return None
            connection = self.connection()
            row = connection.execute(
                "SELECT flags FROM messages WHERE mailbox_id = ? AND uid = ?",
                (mailbox_id, uid)).fetchone()
            if row is None:
                return None
            flags = [f for f in row["flags"].split()]
            for flag in add:
                if flag not in flags:
                    flags.append(flag)
            for flag in remove:
                if flag in flags:
                    flags.remove(flag)
            connection.execute("UPDATE messages SET flags = ? WHERE mailbox_id = ? AND uid = ?",
                               (" ".join(flags), mailbox_id, uid))
            return flags

    def expunge(self, address: str, mailbox: str = "INBOX") -> int:
        with self._write_lock:
            mailbox_id = self.mailbox_id(address, mailbox)
            if mailbox_id is None:
                return 0
            cursor = self.connection().execute(
                "DELETE FROM messages WHERE mailbox_id = ? AND instr(flags, '\\Deleted') > 0",
                (mailbox_id,))
            return cursor.rowcount

    def purge(self, address: str, mailbox: str = "INBOX") -> int:
        with self._write_lock:
            mailbox_id = self.mailbox_id(address, mailbox)
            if mailbox_id is None:
                return 0
            cursor = self.connection().execute("DELETE FROM messages WHERE mailbox_id = ?",
                                               (mailbox_id,))
            return cursor.rowcount

    def counts(self, address: str, mailbox: str = "INBOX") -> tuple[int, int]:
        mailbox_id = self.mailbox_id(address, mailbox)
        if mailbox_id is None:
            return 0, 0
        connection = self.connection()
        total = connection.execute("SELECT COUNT(*) AS n FROM messages WHERE mailbox_id = ?",
                                   (mailbox_id,)).fetchone()["n"]
        unseen = connection.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE mailbox_id = ? AND instr(flags,'\\Seen') = 0",
            (mailbox_id,)).fetchone()["n"]
        return total, unseen
