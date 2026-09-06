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
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import emailtemplates
from . import extract
from . import message as msg
from . import scenarios as scenario_library
from . import spam as spam_engine
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


class ForwardRequest(BaseModel):
    """Forward a message to someone else."""
    user: str = Field(..., description="Mailbox the original is in")
    to: list[str] | str
    uid: str | None = None
    message_id: str | None = None
    note: str = Field("", description="Text placed above the forwarded block")
    cc: list[str] | str | None = None
    sender: str | None = Field(None, alias="from")
    mailbox: str = "INBOX"
    mode: str = Field("inline", pattern="^(inline|attachment)$")
    keep_thread: bool = Field(True, description="Carry References so a reply threads")
    headers: dict[str, str] = Field(default_factory=dict)
    model_config = {"populate_by_name": True}


class WaitRequest(BaseModel):
    """Block until a message matching every supplied filter arrives.

    This exists so tests never need sleep(). Every filter is optional and
    they are combined with AND.
    """
    user: str
    mailbox: str = "INBOX"
    timeout: float = 30.0
    interval: float = 0.25
    subject_contains: str | None = None
    from_contains: str | None = None
    to_contains: str | None = None
    text_contains: str | None = None
    link_contains: str | None = None
    has_code: bool = False
    message_id: str | None = None
    in_reply_to: str | None = None
    thread_root: str | None = None
    unseen_only: bool = False
    mark_seen: bool = False


class AccountRequest(BaseModel):
    address: str
    password: str | None = None
    app: str = "manual"
    is_bot: bool = False
    description: str = ""


class FlagRequest(BaseModel):
    seen: bool


class InboxRequest(BaseModel):
    """A throwaway mailbox, so parallel tests cannot collide."""
    prefix: str = Field("test", max_length=40,
                        description="Readable prefix for the generated address")
    domain: str | None = Field(None, description="Defaults to the primary domain")
    run_id: str | None = Field(None, description="Tag to group inboxes from one CI run")
    password: str | None = None


class SpamRequest(BaseModel):
    """Score a message you supply, rather than one that was delivered."""
    raw: str | None = Field(None, description="A full RFC 5322 message")
    subject: str = ""
    text: str = ""
    html: str = ""
    sender: str = Field("someone@example.test", alias="from")
    engine: str = Field("auto", description="auto | builtin | rspamd")
    model_config = {"populate_by_name": True}


class ParseRequest(BaseModel):
    text: str = ""
    html: str = ""
    keep_signature: bool = False


class ConversationRequest(BaseModel):
    participants: list[str] = Field(..., min_length=2)
    subject: str = "Test conversation"
    turns: int = Field(4, ge=1, le=100)
    body_template: str = "Message {n} of the thread, from {sender}."
    seed: int | None = Field(None, description="Same seed, same bodies")


class TemplateRequest(BaseModel):
    """Send a rendered template to a mailbox."""
    name: str
    to: str
    sender: str | None = Field(None, alias="from")
    subject: str | None = None
    context: dict[str, Any] = Field(default_factory=dict,
                                    description="Values merged over the template defaults")
    model_config = {"populate_by_name": True}


class TemplatePreviewRequest(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)


class ScenarioRequest(BaseModel):
    """Deliver one of the canned awkward messages."""
    name: str
    to: str
    seed: int | None = Field(None, description="Same seed, same bytes")
    options: dict[str, Any] = Field(default_factory=dict,
                                    description="Scenario options, e.g. {\"size_kb\": 64}")


def bracket(message_id: str) -> str:
    return message_id if message_id.startswith("<") else f"<{message_id}>"


def create_app(store: Store, router: Router, config: Config, started_at: float,
               webhook=None, rspamd=None) -> FastAPI:
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

    def enrich(found: dict) -> dict:
        """Add the parsed body, links and one-time code to a stored message."""
        parsed = msg.parse(found["raw"])
        found["date"] = str(parsed.get("Date", ""))
        found["text"] = msg.plain_body(parsed)
        found["html"] = msg.html_body(parsed)
        found["attachments"] = [
            {"filename": part.get_filename(),
             "content_type": part.get_content_type(),
             "size": len(part.get_payload(decode=True) or b"")}
            for part in parsed.iter_attachments()
        ]
        found["forwarded_from"] = str(parsed.get(msg.FORWARDED_ID_HEADER, "")).strip() or None
        found["forward_count"] = msg.forward_count(parsed)
        found.update(extract.summarize(found["text"], found["html"] or ""))
        return found

    async def send_message(message, sender: str) -> str:
        await router.deliver(sender, msg.recipients(message), msg.to_bytes(message),
                             submitted_by=sender, peer="api")
        return str(message["Message-ID"])

    # ---------------------------------------------------------- meta

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "domain": config.primary_domain,
                "uptime_seconds": round(time.time() - started_at, 1),
                "delivered": router.delivered, "captured": router.captured,
                "mailboxes": len(store.accounts()),
                "inbound_webhook": webhook.status() if webhook else {"enabled": False}}

    @app.get("/webhook", tags=["meta"])
    def webhook_status() -> dict[str, Any]:
        """Whether the inbound-parse webhook is on, and how it is doing."""
        if webhook is None:
            return {"enabled": False,
                    "hint": "set INBOUND_WEBHOOK_URL to POST every delivery at your app"}
        return webhook.status()

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

    # ---------------------------------------------------------- inboxes

    @app.post("/inboxes", status_code=201, tags=["inboxes"])
    def create_inbox(request: InboxRequest) -> dict[str, Any]:
        """Create a throwaway mailbox with a unique address.

        One inbox per test is the cure for the two classic causes of flaky
        email tests: parallel runs sharing a mailbox, and assertions that
        grab "the latest unread" belonging to somebody else's test.
        """
        import re as _re
        import secrets

        prefix = _re.sub(r"[^a-z0-9._-]+", "-", request.prefix.lower()).strip("-") or "test"
        domain = (request.domain or config.primary_domain).lower()
        address = f"{prefix}-{secrets.token_hex(5)}@{domain}"
        password = request.password or config.default_password

        store.create_account(address, password, app=request.run_id or "disposable",
                             description=f"Disposable inbox ({request.run_id or 'no run id'})")
        return {"address": address, "password": password, "run_id": request.run_id,
                "smtp": {"host": config.hostname, "port": 587},
                "imap": {"host": config.hostname, "port": 143}}

    @app.delete("/inboxes/{address}", tags=["inboxes"])
    def delete_inbox(address: str) -> dict[str, Any]:
        """Delete one throwaway mailbox and everything in it."""
        full = config.qualify(address)
        return {"address": full, "deleted": store.delete_account(full)}

    @app.delete("/inboxes", tags=["inboxes"])
    def delete_run_inboxes(run_id: str = Query(..., min_length=1)) -> dict[str, Any]:
        """Delete every inbox created with this run_id — CI teardown in one call."""
        removed = []
        for account in store.accounts():
            if account["app"] == run_id:
                if store.delete_account(account["address"]):
                    removed.append(account["address"])
        return {"run_id": run_id, "deleted": removed, "count": len(removed)}

    # ---------------------------------------------------------- spam

    def score_raw(raw: bytes, engine: str = "auto", recipient: str = "") -> dict:
        """Score with Rspamd when it is configured and wanted, else built-in."""
        if engine in ("auto", "rspamd") and rspamd is not None:
            try:
                return rspamd.check(raw, recipient=recipient)
            except Exception as exc:
                if engine == "rspamd":
                    raise HTTPException(
                        status_code=502, detail=f"rspamd is unreachable: {exc}") from None
                # auto: fall back quietly, but say which engine answered
                result = spam_engine.score_message(raw)
                result["rspamd_error"] = str(exc)
                return result
        if engine == "rspamd":
            raise HTTPException(
                status_code=503,
                detail="rspamd is not configured; set RSPAMD_URL to use it")
        return spam_engine.score_message(raw)

    @app.get("/messages/{user}/{uid}/spam", tags=["spam"])
    def score_message(user: str, uid: str, mailbox: str = "INBOX",
                      engine: str = Query("auto", pattern="^(auto|builtin|rspamd)$")
                      ) -> dict[str, Any]:
        """Score a delivered message, with the rules that fired."""
        address = resolve(user)
        raw = store.raw(address, int(uid), mailbox)
        if raw is None:
            raise HTTPException(status_code=404, detail=f"no message with uid {uid}")
        return score_raw(raw, engine, recipient=address)

    @app.post("/spam", tags=["spam"])
    def score_arbitrary(request: SpamRequest) -> dict[str, Any]:
        """Score a message you pass in, without delivering it first."""
        if request.raw:
            raw = request.raw.encode()
        else:
            built = msg.build(request.sender, ["someone@" + config.primary_domain],
                              request.subject, request.text,
                              html=request.html or None, domain=config.primary_domain)
            raw = msg.to_bytes(built)
        return score_raw(raw, request.engine)

    @app.get("/spam", tags=["spam"])
    def spam_engines() -> dict[str, Any]:
        """Which scoring engines are available."""
        return {
            "builtin": {"available": True,
                        "spam_at": spam_engine.SPAM_AT,
                        "suspicious_at": spam_engine.SUSPICIOUS_AT},
            "rspamd": {"available": rspamd is not None,
                       "url": getattr(rspamd, "url", None),
                       "hint": None if rspamd else
                               "set RSPAMD_URL to score with rspamd instead"},
        }

    # ---------------------------------------------------------- parsing

    @app.post("/parse", tags=["parsing"])
    def parse_body(request: ParseRequest) -> dict[str, Any]:
        """Strip quotes and signatures, and pull out links and one-time codes.

        Exposed on its own so you can run it over mail that did not come
        from here — the same logic the message endpoints apply.
        """
        return {
            "stripped_text": extract.strip_quotes(request.text, request.keep_signature),
            "links": extract.find_links(request.text, request.html),
            "code": extract.find_code(request.text, request.html),
        }

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
        return enrich(found)

    @app.get("/messages/{user}/{uid}/raw", response_class=PlainTextResponse, tags=["read"])
    def message_raw(user: str, uid: str, mailbox: str = "INBOX") -> str:
        return message(user, uid, mailbox)["raw"]

    @app.get("/messages/{user}/{uid}/html", response_class=HTMLResponse, tags=["read"])
    def message_html(user: str, uid: str, mailbox: str = "INBOX") -> str:
        """The HTML part on its own, so an iframe can render it at any width."""
        found = message(user, uid, mailbox)
        if not found.get("html"):
            escaped = (found.get("text") or "").replace("&", "&amp;").replace("<", "&lt;")
            return ("<!doctype html><meta charset=utf-8>"
                    "<body style=\"margin:0;padding:20px;font:14px/1.6 ui-monospace,"
                    "SFMono-Regular,Menlo,monospace;white-space:pre-wrap;\">"
                    f"{escaped}</body>")
        return found["html"]

    @app.get("/messages/{user}/{uid}/eml", tags=["read"])
    def message_eml(user: str, uid: str, mailbox: str = "INBOX") -> Response:
        """Download the message as a .eml file."""
        address = resolve(user)
        raw = store.raw(address, int(uid), mailbox)
        if raw is None:
            raise HTTPException(status_code=404, detail=f"no message with uid {uid}")
        return Response(
            content=raw, media_type="message/rfc822",
            headers={"content-disposition":
                     f'attachment; filename="{address.split("@")[0]}-{uid}.eml"'})

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

    def _locate(address: str, mailbox: str, uid: str | None,
                message_id: str | None) -> dict:
        """Find one message by uid or Message-ID, or raise."""
        if uid:
            found = store.message(address, int(uid), mailbox)
        elif message_id:
            wanted = bracket(message_id)
            found = None
            for candidate in store.summaries(address, mailbox):
                if candidate["message_id"] == wanted:
                    found = store.message(address, int(candidate["uid"]), mailbox)
                    break
        else:
            raise HTTPException(status_code=422, detail="provide either uid or message_id")
        if found is None:
            raise HTTPException(status_code=404, detail="original message not found")
        return found

    @app.post("/reply", status_code=201, tags=["write"])
    async def reply(request: ReplyRequest) -> dict[str, Any]:
        address = resolve(request.user)
        found = _locate(address, request.mailbox, request.uid, request.message_id)

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

    @app.post("/forward", status_code=201, tags=["write"])
    async def forward(request: ForwardRequest) -> dict[str, Any]:
        """Forward a message, the way a mail client does.

        The forwarded-header block, the `Fwd:` prefix and the attachments are
        handled here. A forward is not a reply, so no `In-Reply-To` is set,
        but `References` is carried by default so that a reply to the forward
        still threads.
        """
        address = resolve(request.user)
        found = _locate(address, request.mailbox, request.uid, request.message_id)

        to = [request.to] if isinstance(request.to, str) else list(request.to)
        cc = [request.cc] if isinstance(request.cc, str) else list(request.cc or [])
        sender = config.qualify(request.sender) if request.sender else address

        parent = msg.parse(found["raw"])
        built = msg.build_forward(
            parent, sender=sender,
            to=[config.qualify(a) for a in to],
            cc=[config.qualify(a) for a in cc] or None,
            note=request.note, mode=request.mode, keep_thread=request.keep_thread,
            headers=request.headers, domain=sender.split("@")[1],
        )
        created = ensure([sender, *msg.recipients(built)])
        message_id = await send_message(built, sender)

        return {
            "message_id": message_id,
            "forwarded_message_id": str(built[msg.FORWARDED_ID_HEADER]),
            "from": sender,
            "to": [config.qualify(a) for a in to],
            "cc": [config.qualify(a) for a in cc],
            "subject": str(built["Subject"]),
            "mode": request.mode,
            "references": str(built["References"] or "").split(),
            "forward_count": int(str(built[msg.FORWARD_COUNT_HEADER])),
            "attachments": [part.get_filename() for part in built.iter_attachments()],
            "accounts_created": created,
        }

    @app.get("/forwards", tags=["write"])
    def forward_rules() -> dict[str, Any]:
        """Mailboxes that forward everything on, from the app config."""
        rules = []
        for app_config in config.apps:
            for box in app_config.mailboxes:
                if box.forward_to:
                    rules.append({"mailbox": box.address, "app": app_config.name,
                                  "forward_to": [config.qualify(t) for t in box.forward_to],
                                  "keep_copy": box.keep_copy})
        return {"rules": rules, "count": len(rules),
                "max_hops": router.max_forwards,
                "forwarded": router.forwarded}

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
            if request.thread_root and item["thread_root"] != bracket(request.thread_root):
                return False
            if request.to_contains and request.to_contains.lower() not in \
                    (",".join(item["to"]) + "," + item["envelope_to"]).lower():
                return False
            if request.text_contains or request.link_contains or request.has_code:
                raw = store.raw(address, int(item["uid"]), request.mailbox) or b""
                body = raw.decode("utf-8", "replace")
                if request.text_contains and request.text_contains.lower() not in body.lower():
                    return False
                if request.link_contains or request.has_code:
                    parsed = msg.parse(raw)
                    text, html = msg.plain_body(parsed), msg.html_body(parsed) or ""
                    if request.link_contains and not extract.find_link(
                            text, html, request.link_contains):
                        return False
                    if request.has_code and not extract.find_code(text, html):
                        return False
            return True

        deadline = time.monotonic() + request.timeout
        while True:
            for item in reversed(store.summaries(address, request.mailbox)):
                if matches(item):
                    if request.mark_seen:
                        store.set_flags(address, int(item["uid"]), add=["\\Seen"],
                                        mailbox=request.mailbox)
                    return enrich(store.message(address, int(item["uid"]), request.mailbox))
            if time.monotonic() >= deadline:
                raise HTTPException(status_code=408,
                                    detail=f"no matching message within {request.timeout}s")
            await asyncio.sleep(request.interval)

    # ---------------------------------------------------------- templates

    @app.get("/templates", tags=["templates"])
    def list_templates() -> dict[str, Any]:
        """The email templates this server can render and send."""
        return {"templates": emailtemplates.catalogue()}

    @app.get("/templates/{name}/preview", response_class=HTMLResponse, tags=["templates"])
    def preview_template(name: str) -> str:
        """The rendered HTML on its own, for a browser or a screenshot tool."""
        try:
            return emailtemplates.render(name, {}, domain=config.primary_domain)["html"]
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no template {name!r}") from None

    @app.post("/templates/{name}/preview", tags=["templates"])
    def preview_template_with_context(name: str, request: TemplatePreviewRequest) -> dict[str, Any]:
        """Render with your own values, without sending anything."""
        try:
            return emailtemplates.render(name, request.context, domain=config.primary_domain)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no template {name!r}") from None

    @app.post("/templates", status_code=201, tags=["templates"])
    async def send_template(request: TemplateRequest) -> dict[str, Any]:
        """Render a template and deliver it, HTML and plain text together."""
        try:
            rendered = emailtemplates.render(request.name, request.context,
                                             domain=config.primary_domain)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"no template {request.name!r}; try one of "
                       f"{[t['name'] for t in emailtemplates.catalogue()]}") from None

        recipient = config.qualify(request.to)
        sender = config.qualify(request.sender) if request.sender else rendered["from"]
        ensure([sender, recipient])

        built = msg.build(sender, [recipient], request.subject or rendered["subject"],
                          rendered["text"], html=rendered["html"],
                          domain=sender.split("@")[1])
        message_id = await send_message(built, sender)
        return {"template": request.name, "message_id": message_id, "from": sender,
                "to": recipient, "subject": str(built["Subject"]),
                "size": len(msg.to_bytes(built))}

    @app.get("/scenarios", tags=["demo"])
    def list_scenarios() -> dict[str, Any]:
        """The canned messages you can have delivered."""
        return {"scenarios": scenario_library.catalogue()}

    @app.post("/scenarios", status_code=201, tags=["demo"])
    async def deliver_scenario(request: ScenarioRequest) -> dict[str, Any]:
        """Deliver a bounce, a newsletter, an HTML-only message, and so on.

        These are the shapes that are tedious to build by hand and that break
        naive parsers. With a seed the bytes are identical every run, so they
        work as snapshot fixtures.
        """
        recipient = config.qualify(request.to)
        ensure([recipient])
        try:
            built = scenario_library.build(
                request.name, to=recipient, domain=config.primary_domain,
                seed=request.seed, **request.options)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"no scenario {request.name!r}; "
                       f"try one of {[s['name'] for s in scenario_library.catalogue()]}"
            ) from None
        except TypeError as exc:
            raise HTTPException(status_code=422, detail=f"bad options: {exc}") from None

        sender = msg.addresses(built, "From")[0]
        await router.deliver(sender, [recipient], msg.to_bytes(built), peer="scenario")
        return {"scenario": request.name, "to": recipient, "from": sender,
                "subject": str(built["Subject"]),
                "message_id": str(built["Message-ID"]), "seed": request.seed,
                "size": len(msg.to_bytes(built))}

    @app.post("/conversation", status_code=201, tags=["demo"])
    async def conversation(request: ConversationRequest) -> dict[str, Any]:
        """Build a real multi-turn thread, each turn an actual reply to the last."""
        people = [config.qualify(p) for p in request.participants]
        ensure(people)
        rng = __import__("random").Random(request.seed) if request.seed is not None else None

        def body(turn: int, speaker: str) -> str:
            text = request.body_template.format(n=turn, sender=speaker)
            if rng is not None:
                text += f"\n\nnonce {rng.getrandbits(32):08x}"
            return text

        opener = msg.build(people[0], [people[1]], request.subject,
                           body(1, people[0]), domain=people[0].split("@")[1])
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

            built = msg.build_reply(msg.parse(found["raw"]), body(turn, speaker),
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
