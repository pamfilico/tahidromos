"""The REST API and the mailbox browser.

The API talks to the store directly rather than looping back through IMAP,
so it stays fast, but it never does anything the protocols could not: reading
a message does not set \\Seen, and replies go out through the same router an
SMTP client would hit.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import message as msg
from .config import Config
from .router import Router
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"


class SendRequest(BaseModel):
    sender: str = Field(..., alias="from")
    to: list[str] | str
    subject: str = ""
    text: str = ""
    html: str | None = None
    cc: list[str] | str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    model_config = {"populate_by_name": True}


class ReplyRequest(BaseModel):
    user: str
    uid: str | None = None
    message_id: str | None = None
    text: str = ""
    sender: str | None = Field(None, alias="from")
    mailbox: str = "INBOX"
    reply_all: bool = False
    quote: bool = True
    mark_seen: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    model_config = {"populate_by_name": True}


class WaitRequest(BaseModel):
    user: str
    mailbox: str = "INBOX"
    timeout: float = 30.0
    interval: float = 0.25
    subject_contains: str | None = None
    from_contains: str | None = None
    text_contains: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    unseen_only: bool = False


class AccountRequest(BaseModel):
    address: str
    password: str | None = None
    app: str = "manual"
    is_bot: bool = False
    description: str = ""


class FlagRequest(BaseModel):
    seen: bool


class ConversationRequest(BaseModel):
    participants: list[str] = Field(..., min_length=2)
    subject: str = "Test conversation"
    turns: int = Field(4, ge=1, le=100)
    body_template: str = "Message {n} of the thread, from {sender}."


def bracket(message_id: str) -> str:
    return message_id if message_id.startswith("<") else f"<{message_id}>"


def create_app(store: Store, router: Router, config: Config, started_at: float) -> FastAPI:
    app = FastAPI(
        title="tahidromos",
        version="1.0.0",
        description="A development mail server that can hold a conversation. "
                    "Real SMTP, real IMAP, real reply threads.",
    )

    # ---------------------------------------------------------- helpers

    def resolve(user: str) -> str:
        address = config.qualify(user)
        if not store.account_exists(address):
            raise HTTPException(status_code=404, detail=f"no mailbox {address}")
        return address

    def ensure(addresses: list[str]) -> list[str]:
        created = []
        for raw in addresses:
            address = config.qualify(raw)
            if store.account_exists(address) or not config.is_local(address):
                continue
            password = config.password_for(address) or config.default_password
            store.create_account(address, password, app="auto",
                                 description="Created on demand by the API")
            created.append(address)
        return created

    async def send_message(message, sender: str) -> str:
        await router.deliver(sender, msg.recipients(message), msg.to_bytes(message),
                             submitted_by=sender, peer="api")
        return str(message["Message-ID"])

    # ---------------------------------------------------------- meta

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "domain": config.primary_domain,
                "uptime_seconds": round(time.time() - started_at, 1),
                "delivered": router.delivered, "captured": router.captured}

    @app.get("/config", tags=["meta"])
    def configuration() -> dict[str, Any]:
        payload = config.as_dict()
        payload["seed_accounts"] = [a["address"] for a in store.accounts()]
        payload["local_domains"] = config.domains
        return payload

    @app.get("/apps", tags=["apps"])
    def apps() -> dict[str, Any]:
        return config.as_dict()

    @app.get("/overview", tags=["apps"])
    def overview() -> dict[str, Any]:
        result = []
        grand_total = grand_unread = 0
        for entry in config.apps:
            boxes = []
            app_total = app_unread = 0
            for mailbox in entry.mailboxes:
                total, unread = store.counts(mailbox.address)
                app_total += total
                app_unread += unread
                boxes.append({
                    "localpart": mailbox.localpart, "address": mailbox.address,
                    "password": mailbox.password, "is_bot": mailbox.is_bot,
                    "is_capture": mailbox.is_capture, "description": mailbox.description,
                    "total": total, "unread": unread,
                    "online": store.account_exists(mailbox.address),
                })
            grand_total += app_total
            grand_unread += app_unread
            result.append({
                "name": entry.name, "domain": entry.domain, "description": entry.description,
                "smtp_address": entry.smtp_address, "smtp_password": entry.smtp_password,
                "mailboxes": boxes, "total": app_total, "unread": app_unread,
            })
        return {"apps": result, "total": grand_total, "unread": grand_unread,
                "domains": config.domains, "capture_address": config.capture_address,
                "default_password": config.default_password,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # ---------------------------------------------------------- accounts

    @app.get("/accounts", tags=["accounts"])
    def list_accounts() -> dict[str, Any]:
        accounts = [{**a, "reachable": True} for a in store.accounts()]
        return {"accounts": accounts, "count": len(accounts)}

    @app.post("/accounts", status_code=201, tags=["accounts"])
    def create_account(request: AccountRequest) -> dict[str, Any]:
        address = config.qualify(request.address)
        password = request.password or config.password_for(address) or config.default_password
        store.create_account(address, password, app=request.app,
                             is_bot=request.is_bot, description=request.description)
        return {"address": address, "password": password, "created": True}

    @app.delete("/accounts/{user}", tags=["accounts"])
    def delete_account(user: str) -> dict[str, Any]:
        address = config.qualify(user)
        return {"address": address, "deleted": store.delete_account(address)}

    # ---------------------------------------------------------- reading

    @app.get("/mailboxes/{user}", tags=["read"])
    def mailboxes(user: str) -> dict[str, Any]:
        address = resolve(user)
        return {"user": address, "mailboxes": store.mailboxes(address)}

    @app.get("/messages/{user}", tags=["read"])
    def messages(user: str, mailbox: str = "INBOX", limit: int = Query(100, ge=1, le=1000),
                 unseen: bool = False, include_raw: bool = False) -> dict[str, Any]:
        address = resolve(user)
        found = store.summaries(address, mailbox)
        if unseen:
            found = [m for m in found if not m["seen"]]
        found = list(reversed(found))[:limit]
        if include_raw:
            for item in found:
                raw = store.raw(address, int(item["uid"]), mailbox)
                item["raw"] = raw.decode("utf-8", "replace") if raw else ""
        return {"user": address, "mailbox": mailbox, "count": len(found), "messages": found}

    @app.get("/messages/{user}/{uid}", tags=["read"])
    def message(user: str, uid: str, mailbox: str = "INBOX") -> dict[str, Any]:
        address = resolve(user)
        try:
            found = store.message(address, int(uid), mailbox)
        except ValueError:
            raise HTTPException(status_code=422, detail="uid must be a number") from None
        if found is None:
            raise HTTPException(status_code=404, detail=f"no message with uid {uid}")
        parsed = msg.parse(found["raw"])
        found["date"] = str(parsed.get("Date", ""))
        found["text"] = msg.plain_body(parsed)
        found["html"] = msg.html_body(parsed)
        return found

    @app.get("/messages/{user}/{uid}/raw", response_class=PlainTextResponse, tags=["read"])
    def message_raw(user: str, uid: str, mailbox: str = "INBOX") -> str:
        return message(user, uid, mailbox)["raw"]

    @app.patch("/messages/{user}/{uid}", tags=["read"])
    def set_flags(user: str, uid: str, request: FlagRequest,
                  mailbox: str = "INBOX") -> dict[str, Any]:
        """Mark one message read or unread.

        Reading never changes flags on its own, so browsing the UI cannot
        perturb a test that is waiting on an unread count.
        """
        address = resolve(user)
        flags = store.set_flags(address, int(uid),
                                add=["\\Seen"] if request.seen else [],
                                remove=[] if request.seen else ["\\Seen"], mailbox=mailbox)
        if flags is None:
            raise HTTPException(status_code=404, detail=f"no message with uid {uid}")
        return {"user": address, "uid": uid, "seen": request.seen, "flags": flags}

    @app.delete("/messages/{user}", tags=["read"])
    def purge(user: str, mailbox: str = "INBOX") -> dict[str, Any]:
        address = resolve(user)
        return {"user": address, "mailbox": mailbox, "deleted": store.purge(address, mailbox)}

    @app.get("/threads/{user}", tags=["threads"])
    def threads(user: str, mailbox: str = "INBOX") -> dict[str, Any]:
        address = resolve(user)
        grouped = msg.summarize_thread(store.summaries(address, mailbox))
        return {"user": address, "mailbox": mailbox, "count": len(grouped), "threads": grouped}

    @app.get("/threads/{user}/{thread_root:path}", tags=["threads"])
    def thread(user: str, thread_root: str, mailbox: str = "INBOX") -> dict[str, Any]:
        root = bracket(thread_root)
        for candidate in threads(user, mailbox)["threads"]:
            if candidate["thread_root"] == root:
                return candidate
        raise HTTPException(status_code=404, detail=f"no thread rooted at {root}")

    # ---------------------------------------------------------- writing

    @app.post("/send", status_code=201, tags=["write"])
    async def send(request: SendRequest) -> dict[str, Any]:
        to = [request.to] if isinstance(request.to, str) else list(request.to)
        cc = [request.cc] if isinstance(request.cc, str) else list(request.cc or [])
        sender = config.qualify(request.sender)
        created = ensure([sender, *to, *cc])

        built = msg.build(sender, [config.qualify(a) for a in to], request.subject,
                          request.text, cc=[config.qualify(a) for a in cc] or None,
                          html=request.html, headers=request.headers,
                          domain=sender.split("@")[1])
        message_id = await send_message(built, sender)
        return {"message_id": message_id, "from": sender,
                "to": [config.qualify(a) for a in to],
                "cc": [config.qualify(a) for a in cc],
                "subject": request.subject, "accounts_created": created}

    @app.post("/reply", status_code=201, tags=["write"])
    async def reply(request: ReplyRequest) -> dict[str, Any]:
        address = resolve(request.user)
        found = None
        if request.uid:
            found = store.message(address, int(request.uid), request.mailbox)
        elif request.message_id:
            wanted = bracket(request.message_id)
            for candidate in store.summaries(address, request.mailbox):
                if candidate["message_id"] == wanted:
                    found = store.message(address, int(candidate["uid"]), request.mailbox)
                    break
        else:
            raise HTTPException(status_code=422, detail="provide either uid or message_id")
        if found is None:
            raise HTTPException(status_code=404, detail="original message not found")

        parent = msg.parse(found["raw"])
        sender = config.qualify(request.sender) if request.sender else address
        built = msg.build_reply(parent, request.text, sender=sender,
                                reply_all=request.reply_all, quote=request.quote,
                                headers=request.headers, domain=sender.split("@")[1])
        ensure([sender, *msg.recipients(built)])
        if request.mark_seen:
            store.set_flags(address, int(found["uid"]), add=["\\Seen"], mailbox=request.mailbox)
        store.set_flags(address, int(found["uid"]), add=["\\Answered"], mailbox=request.mailbox)

        message_id = await send_message(built, sender)
        return {"message_id": message_id, "in_reply_to": str(built["In-Reply-To"]),
                "references": str(built["References"]).split(),
                "depth": int(str(built[msg.DEPTH_HEADER])), "from": sender,
                "to": str(built["To"]), "subject": str(built["Subject"]),
                "replied_to_uid": found["uid"]}

    @app.post("/wait", tags=["write"])
    async def wait(request: WaitRequest) -> dict[str, Any]:
        address = resolve(request.user)

        def matches(item: dict) -> bool:
            if request.unseen_only and item["seen"]:
                return False
            if request.subject_contains and \
                    request.subject_contains.lower() not in item["subject"].lower():
                return False
            if request.from_contains and request.from_contains.lower() not in item["from"].lower():
                return False
            if request.message_id and item["message_id"] != bracket(request.message_id):
                return False
            if request.in_reply_to and (item["in_reply_to"] or "") != bracket(request.in_reply_to):
                return False
            if request.text_contains:
                raw = store.raw(address, int(item["uid"]), request.mailbox) or b""
                if request.text_contains.lower() not in raw.decode("utf-8", "replace").lower():
                    return False
            return True

        deadline = time.monotonic() + request.timeout
        while True:
            for item in reversed(store.summaries(address, request.mailbox)):
                if matches(item):
                    return store.message(address, int(item["uid"]), request.mailbox)
            if time.monotonic() >= deadline:
                raise HTTPException(status_code=408,
                                    detail=f"no matching message within {request.timeout}s")
            await asyncio.sleep(request.interval)

    @app.post("/conversation", status_code=201, tags=["demo"])
    async def conversation(request: ConversationRequest) -> dict[str, Any]:
        """Build a real multi-turn thread, each turn an actual reply to the last."""
        people = [config.qualify(p) for p in request.participants]
        ensure(people)

        opener = msg.build(people[0], [people[1]], request.subject,
                           request.body_template.format(n=1, sender=people[0]),
                           domain=people[0].split("@")[1])
        last_id = await send_message(opener, people[0])
        trail = [{"n": 1, "from": people[0], "to": people[1],
                  "message_id": last_id, "depth": 0}]

        for turn in range(2, request.turns + 1):
            speaker = people[(turn - 1) % len(people)]
            found = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and found is None:
                for candidate in reversed(store.summaries(speaker)):
                    if candidate["message_id"] == last_id:
                        found = store.message(speaker, int(candidate["uid"]))
                        break
                if found is None:
                    await asyncio.sleep(0.05)
            if found is None:
                raise HTTPException(status_code=504,
                                    detail=f"turn {turn}: {speaker} never received {last_id}")

            built = msg.build_reply(msg.parse(found["raw"]),
                                    request.body_template.format(n=turn, sender=speaker),
                                    sender=speaker, quote=False,
                                    domain=speaker.split("@")[1])
            last_id = await send_message(built, speaker)
            trail.append({"n": turn, "from": speaker, "to": str(built["To"]),
                          "message_id": last_id, "in_reply_to": str(built["In-Reply-To"]),
                          "references": str(built["References"]).split(),
                          "depth": int(str(built[msg.DEPTH_HEADER]))})

        return {"participants": people, "subject": request.subject, "turns": len(trail),
                "thread_root": trail[0]["message_id"], "messages": trail}

    # ---------------------------------------------------------- UI

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=500, detail="UI assets missing")
        return page.read_text(encoding="utf-8")

    return app
