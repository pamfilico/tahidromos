"""A small client for a tahidromos server.

    from tahidromos_client import Tahidromos

    mail = Tahidromos()                       # http://localhost:8080
    inbox = mail.inbox("signup")              # a mailbox nobody else is using
    app.register(email=inbox.address)

    message = inbox.wait(subject_contains="Confirm")
    browser.goto(message.link)                # or message.code for an OTP

Every wait is a long poll on the server, so tests never need sleep().
"""

from __future__ import annotations

import secrets
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import requests

__all__ = ["Tahidromos", "Inbox", "Message", "TahidromosError"]
__version__ = "1.0.0"

DEFAULT_URL = "http://localhost:8080"


class TahidromosError(RuntimeError):
    """The server said no."""


class MessageNotFound(TahidromosError, AssertionError):
    """Nothing matched within the timeout.

    Subclasses AssertionError so an unmet expectation reads as a test failure
    rather than an error.
    """


@dataclass
class Message:
    """One delivered message, with the parts tests actually assert on."""

    raw_data: dict[str, Any] = field(repr=False)

    def __getitem__(self, key: str) -> Any:
        return self.raw_data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw_data.get(key, default)

    @property
    def uid(self) -> str:
        return self.raw_data["uid"]

    @property
    def subject(self) -> str:
        return self.raw_data.get("subject", "")

    @property
    def sender(self) -> str:
        return self.raw_data.get("from", "")

    @property
    def to(self) -> list[str]:
        return self.raw_data.get("to", [])

    @property
    def cc(self) -> list[str]:
        return self.raw_data.get("cc", [])

    @property
    def text(self) -> str:
        return self.raw_data.get("text", "")

    @property
    def html(self) -> str | None:
        return self.raw_data.get("html")

    @property
    def stripped_text(self) -> str:
        """The body without the quoted thread or signature beneath it."""
        return self.raw_data.get("stripped_text", "")

    @property
    def links(self) -> list[str]:
        return self.raw_data.get("links", [])

    @property
    def link(self) -> str | None:
        """The first link — usually the magic link or confirmation URL."""
        return self.raw_data.get("link")

    @property
    def code(self) -> str | None:
        """The one-time code, if there is one."""
        return self.raw_data.get("code")

    @property
    def message_id(self) -> str:
        return self.raw_data.get("message_id", "")

    @property
    def in_reply_to(self) -> str | None:
        return self.raw_data.get("in_reply_to")

    @property
    def references(self) -> list[str]:
        return self.raw_data.get("references", [])

    @property
    def thread_root(self) -> str:
        return self.raw_data.get("thread_root", "")

    @property
    def depth(self) -> int:
        return self.raw_data.get("depth", 0)

    @property
    def seen(self) -> bool:
        return bool(self.raw_data.get("seen"))

    @property
    def raw(self) -> str:
        return self.raw_data.get("raw", "")

    def link_containing(self, needle: str) -> str:
        for link in self.links:
            if needle.lower() in link.lower():
                return link
        raise MessageNotFound(
            f"no link containing {needle!r} in {self.subject!r}; found {self.links}")

    def __repr__(self) -> str:
        return f"<Message {self.sender} -> {', '.join(self.to)}: {self.subject!r}>"


class Inbox:
    """A mailbox on the server, usually one nobody else in the run is using."""

    def __init__(self, client: "Tahidromos", address: str, password: str):
        self.client = client
        self.address = address
        self.password = password

    # -- reading ---------------------------------------------------------

    def wait(self, timeout: float = 30.0, **filters) -> Message:
        """Block until a message matching every filter arrives.

        Filters: subject_contains, from_contains, to_contains, text_contains,
        link_contains, has_code, message_id, in_reply_to, thread_root,
        unseen_only, mark_seen.
        """
        payload = {"user": self.address, "timeout": timeout, **filters}
        response = self.client._request("POST", "/wait", json=payload, timeout=timeout + 15)
        if response.status_code == 408:
            raise MessageNotFound(
                f"{self.address}: nothing matched {filters} within {timeout}s")
        return Message(self.client._ok(response))

    def messages(self, mailbox: str = "INBOX", unseen: bool = False,
                 limit: int = 100) -> list[Message]:
        data = self.client._get(f"/messages/{self.address}",
                                params={"mailbox": mailbox, "unseen": unseen, "limit": limit})
        return [Message(m) for m in data["messages"]]

    def message(self, uid: str, mailbox: str = "INBOX") -> Message:
        return Message(self.client._get(f"/messages/{self.address}/{uid}",
                                        params={"mailbox": mailbox}))

    def threads(self) -> list[dict]:
        return self.client._get(f"/threads/{self.address}")["threads"]

    def count(self) -> int:
        return len(self.messages())

    # -- writing ---------------------------------------------------------

    def send(self, to: str | list[str], subject: str = "", text: str = "",
             **extra) -> dict:
        return self.client.send(self.address, to, subject, text, **extra)

    def reply(self, message: Message | str, text: str, reply_all: bool = False,
              **extra) -> dict:
        """Reply to a message; the server builds the threading headers."""
        uid = message.uid if isinstance(message, Message) else message
        return self.client._ok(self.client._request("POST", "/reply", json={
            "user": self.address, "uid": uid, "text": text,
            "reply_all": reply_all, **extra}))

    # -- housekeeping ----------------------------------------------------

    def mark_read(self, message: Message | str, seen: bool = True) -> dict:
        uid = message.uid if isinstance(message, Message) else message
        return self.client._ok(self.client._request(
            "PATCH", f"/messages/{self.address}/{uid}", json={"seen": seen}))

    def purge(self, mailbox: str = "INBOX") -> int:
        return self.client._ok(self.client._request(
            "DELETE", f"/messages/{self.address}", params={"mailbox": mailbox}))["deleted"]

    def delete(self) -> bool:
        return self.client._ok(self.client._request(
            "DELETE", f"/inboxes/{self.address}"))["deleted"]

    # -- talking to it directly -------------------------------------------

    def smtp(self) -> smtplib.SMTP:
        """An authenticated smtplib connection, for testing your own sending."""
        connection = smtplib.SMTP(self.client.smtp_host, self.client.smtp_port, timeout=20)
        connection.ehlo()
        connection.login(self.address, self.password)
        return connection

    def send_raw(self, message: EmailMessage) -> None:
        """Submit a message you built yourself."""
        if not message["From"]:
            message["From"] = self.address
        with self.smtp() as connection:
            connection.send_message(message)

    def __repr__(self) -> str:
        return f"<Inbox {self.address}>"

    def __str__(self) -> str:
        return self.address


class Tahidromos:
    """A tahidromos server."""

    def __init__(self, url: str = DEFAULT_URL, smtp_host: str | None = None,
                 smtp_port: int = 1587, imap_host: str | None = None,
                 imap_port: int = 1143, run_id: str | None = None,
                 session: requests.Session | None = None):
        self.url = url.rstrip("/")
        host = self.url.split("//", 1)[-1].split(":", 1)[0]
        self.smtp_host = smtp_host or host
        self.smtp_port = smtp_port
        self.imap_host = imap_host or host
        self.imap_port = imap_port
        self.run_id = run_id or f"run-{secrets.token_hex(4)}"
        self.session = session or requests.Session()

    # -- plumbing --------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 30)
        try:
            return self.session.request(method, f"{self.url}{path}", **kwargs)
        except requests.RequestException as exc:
            raise TahidromosError(f"cannot reach tahidromos at {self.url}: {exc}") from exc

    @staticmethod
    def _ok(response: requests.Response) -> Any:
        if response.status_code >= 400:
            raise TahidromosError(f"{response.status_code} {response.text[:400]}")
        return response.json()

    def _get(self, path: str, **kwargs) -> Any:
        return self._ok(self._request("GET", path, **kwargs))

    # -- server ----------------------------------------------------------

    def health(self) -> dict:
        return self._get("/health")

    def is_up(self) -> bool:
        try:
            return self.health().get("status") == "ok"
        except TahidromosError:
            return False

    def wait_until_ready(self, timeout: float = 60.0) -> None:
        """Poll /health until the server answers — for CI startup."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_up():
                return
            time.sleep(0.5)
        raise TahidromosError(f"tahidromos at {self.url} was not ready within {timeout}s")

    def config(self) -> dict:
        return self._get("/config")

    def apps(self) -> dict:
        return self._get("/apps")

    def overview(self) -> dict:
        return self._get("/overview")

    # -- inboxes ---------------------------------------------------------

    def inbox(self, prefix: str = "test", domain: str | None = None) -> Inbox:
        """A brand-new mailbox with a unique address, tagged with this run."""
        data = self._ok(self._request("POST", "/inboxes", json={
            "prefix": prefix, "domain": domain, "run_id": self.run_id}))
        return Inbox(self, data["address"], data["password"])

    def mailbox(self, address: str, password: str | None = None) -> Inbox:
        """An existing mailbox, such as a seeded one like `alice`."""
        return Inbox(self, address, password or "password")

    def cleanup(self) -> list[str]:
        """Delete every inbox this client created."""
        return self._ok(self._request("DELETE", "/inboxes",
                                      params={"run_id": self.run_id}))["deleted"]

    # -- sending ---------------------------------------------------------

    def send(self, sender: str, to: str | list[str], subject: str = "",
             text: str = "", **extra) -> dict:
        payload = {"from": str(sender), "to": to if isinstance(to, list) else [str(to)],
                   "subject": subject, "text": text, **extra}
        return self._ok(self._request("POST", "/send", json=payload))

    def conversation(self, participants: list[str], subject: str = "Test conversation",
                     turns: int = 4, **extra) -> dict:
        """Generate a real multi-turn thread in one call."""
        return self._ok(self._request("POST", "/conversation", json={
            "participants": [str(p) for p in participants],
            "subject": subject, "turns": turns, **extra}, timeout=120))

    def scenario(self, name: str, to: str, **extra) -> dict:
        """Deliver a canned awkward message — bounce, newsletter, HTML, and so on."""
        return self._ok(self._request("POST", "/scenarios", json={
            "name": name, "to": str(to), **extra}))

    def scenarios(self) -> list[dict]:
        return self._get("/scenarios")["scenarios"]

    def parse(self, text: str = "", html: str = "") -> dict:
        """Strip quotes and signatures and pull out links and codes."""
        return self._ok(self._request("POST", "/parse", json={"text": text, "html": html}))

    def __repr__(self) -> str:
        return f"<Tahidromos {self.url} run_id={self.run_id}>"
