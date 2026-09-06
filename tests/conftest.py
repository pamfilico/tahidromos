"""Shared fixtures. Tests talk to the stack from outside, over published ports."""

from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
import smtplib
import time
import uuid
from email.message import EmailMessage
from email.utils import make_msgid

import pytest
import requests

DOMAIN = os.environ.get("MAIL_DOMAIN", "tahidromos.test")
PASSWORD = os.environ.get("MAIL_DEFAULT_PASSWORD", "password")
SMTP_HOST = os.environ.get("TEST_SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("TEST_SMTP_PORT", "1587"))
SMTP_PLAIN_PORT = int(os.environ.get("TEST_SMTP_PLAIN_PORT", "1025"))
IMAP_HOST = os.environ.get("TEST_IMAP_HOST", "localhost")
IMAP_PORT = int(os.environ.get("TEST_IMAP_PORT", "1143"))
IMAPS_PORT = int(os.environ.get("TEST_IMAPS_PORT", "1993"))
API_URL = os.environ.get("TEST_API_URL", "http://localhost:8080")

POLICY = email.policy.default


def qualify(user: str) -> str:
    return user if "@" in user else f"{user}@{DOMAIN}"


class MailClient:
    """Deliberately thin: plain smtplib + imaplib, no project code."""

    def __init__(self, user: str, password: str = PASSWORD):
        self.address = qualify(user)
        self.password = password

    # -- sending -------------------------------------------------------

    def send(self, to, subject: str, body: str, *, cc=None, headers=None,
             in_reply_to: str | None = None, references: list[str] | None = None,
             auth: bool = True, port: int | None = None) -> str:
        to = [to] if isinstance(to, str) else list(to)
        cc = [cc] if isinstance(cc, str) else list(cc or [])

        message = EmailMessage()
        message["From"] = self.address
        message["To"] = ", ".join(qualify(a) for a in to)
        if cc:
            message["Cc"] = ", ".join(qualify(a) for a in cc)
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=DOMAIN)
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = " ".join(references)
        for name, value in (headers or {}).items():
            message[name] = value
        message.set_content(body)

        with smtplib.SMTP(SMTP_HOST, port or SMTP_PORT, timeout=20) as smtp:
            smtp.ehlo()
            if auth:
                smtp.login(self.address, self.password)
            smtp.send_message(message)
        return str(message["Message-ID"])

    def reply_to(self, original: EmailMessage, body: str, to: str | None = None) -> str:
        refs = str(original.get("References", "")).split()
        parent = str(original["Message-ID"]).strip()
        if parent not in refs:
            refs.append(parent)
        subject = str(original["Subject"])
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        target = to or email.utils.parseaddr(str(original["From"]))[1]
        return self.send(target, subject, body, in_reply_to=parent, references=refs)

    # -- reading -------------------------------------------------------

    def _connect(self, use_ssl: bool = False):
        if use_ssl:
            import ssl as _ssl

            context = _ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = _ssl.CERT_NONE
            conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAPS_PORT, ssl_context=context, timeout=20)
        else:
            conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT, timeout=20)
        conn.login(self.address, self.password)
        return conn

    def inbox(self, mailbox: str = "INBOX", use_ssl: bool = False) -> list[EmailMessage]:
        conn = self._connect(use_ssl)
        try:
            conn.select(mailbox)
            _, data = conn.uid("SEARCH", None, "ALL")
            out = []
            for uid in (data[0] or b"").split():
                _, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                for part in fetched:
                    if isinstance(part, tuple):
                        out.append(email.message_from_bytes(part[1], policy=POLICY))
            return out
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def mailboxes(self) -> list[str]:
        conn = self._connect()
        try:
            _, data = conn.list()
            names = []
            for line in data:
                if not line:
                    continue
                text = line.decode() if isinstance(line, bytes) else str(line)
                # the name is the last token, quoted or not
                match = re.search(r'"([^"]*)"\s*$', text) or re.search(r"(\S+)\s*$", text)
                if match:
                    names.append(match.group(1))
            return names
        finally:
            conn.logout()

    def wait_for(self, predicate, timeout: float = 40.0, interval: float = 0.5,
                 mailbox: str = "INBOX") -> EmailMessage:
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                for message in self.inbox(mailbox):
                    if predicate(message):
                        return message
            except Exception as exc:
                last_error = exc
            time.sleep(interval)
        raise AssertionError(
            f"{self.address}: no matching message in {mailbox} within {timeout}s"
            + (f" (last error: {last_error})" if last_error else "")
        )

    def wait_for_subject(self, subject: str, **kwargs) -> EmailMessage:
        return self.wait_for(lambda m: str(m.get("Subject", "")) == subject, **kwargs)

    def wait_for_id(self, message_id: str, **kwargs) -> EmailMessage:
        wanted = message_id.strip()
        return self.wait_for(lambda m: str(m.get("Message-ID", "")).strip() == wanted, **kwargs)

    def purge(self, mailbox: str = "INBOX") -> None:
        conn = self._connect()
        try:
            conn.select(mailbox)
            _, data = conn.uid("SEARCH", None, "ALL")
            for uid in (data[0] or b"").split():
                conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            conn.expunge()
        finally:
            conn.logout()


def _wait_for_stack(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    problems = []
    while time.monotonic() < deadline:
        problems = []
        try:
            requests.get(f"{API_URL}/health", timeout=5).raise_for_status()
        except Exception as exc:
            problems.append(f"api: {exc}")
        try:
            MailClient("alice").mailboxes()
        except Exception as exc:
            problems.append(f"imap: {exc}")
        if not problems:
            return
        time.sleep(2)
    raise RuntimeError("stack never became ready:\n  " + "\n  ".join(problems))


@pytest.fixture(scope="session", autouse=True)
def stack_ready():
    _wait_for_stack()


@pytest.fixture
def unique() -> str:
    """A token to keep parallel/repeat runs from colliding."""
    return uuid.uuid4().hex[:10]


@pytest.fixture
def overview(api):
    return api.get("/overview").json()


@pytest.fixture
def alice() -> MailClient:
    return MailClient("alice")


@pytest.fixture
def bob() -> MailClient:
    return MailClient("bob")


@pytest.fixture
def carol() -> MailClient:
    return MailClient("carol")


@pytest.fixture
def api():
    class Api:
        base = API_URL

        def get(self, path, **kwargs):
            return requests.get(f"{API_URL}{path}", timeout=60, **kwargs)

        def post(self, path, json=None, **kwargs):
            return requests.post(f"{API_URL}{path}", json=json, timeout=120, **kwargs)

        def delete(self, path, **kwargs):
            return requests.delete(f"{API_URL}{path}", timeout=60, **kwargs)

        def patch(self, path, json=None, **kwargs):
            return requests.patch(f"{API_URL}{path}", json=json, timeout=60, **kwargs)

    return Api()


@pytest.fixture
def captured(api) -> MailClient:
    """The mailbox that catches anything addressed outside the local domains."""
    return MailClient(api.get("/overview").json()["capture_address"])
