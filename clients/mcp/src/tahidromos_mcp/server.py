"""The MCP tool surface.

Every tool is a thin call onto the REST API, so behaviour cannot drift between
what an agent does and what a test does. Docstrings are the tool descriptions
the model reads, so they say what the tool is *for*, not just what it does.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import MCPServer
from pydantic import Field

BASE_URL = os.environ.get("TAHIDROMOS_URL", "http://localhost:8080").rstrip("/")
RUN_ID = os.environ.get("TAHIDROMOS_RUN_ID", "mcp")

server = MCPServer(
    name="tahidromos",
    title="tahidromos — a disposable mail server",
    instructions=(
        "A development mail server running locally. Use it to give yourself a "
        "throwaway email address, send and receive real mail, and hold a "
        "threaded conversation — all offline, with nothing able to reach the "
        "real internet.\n\n"
        "Start with create_inbox. Addresses are disposable: make one per task "
        "rather than reusing one. echo_bot_address gives you a counterpart "
        "that always replies, so you can rehearse a back-and-forth without "
        "another person.\n\n"
        "wait_for_email blocks until a matching message arrives, so never poll "
        "in a loop and never sleep."
    ),
)


class ServerUnavailable(RuntimeError):
    pass


async def call(method: str, path: str, payload: dict | None = None,
               timeout: float = 60.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(method, f"{BASE_URL}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise ServerUnavailable(
                f"Cannot reach tahidromos at {BASE_URL} ({exc}). Start it with:\n"
                f"  docker run -p 1025:25 -p 1587:587 -p 1143:143 -p 8080:8080 "
                f"ghcr.io/pamfilico/tahidromos"
            ) from None

    if response.status_code == 408:
        return {"found": False, "reason": "no matching message arrived before the timeout"}
    if response.status_code >= 400:
        raise RuntimeError(f"{response.status_code}: {response.text[:400]}")
    return response.json() if response.content else {}


def brief(message: dict) -> dict:
    """Trim a message to what an agent needs, so context is not wasted on MIME."""
    return {
        "uid": message.get("uid"),
        "from": message.get("from"),
        "to": message.get("to"),
        "cc": message.get("cc") or None,
        "subject": message.get("subject"),
        "date": message.get("date"),
        "body": message.get("stripped_text") or message.get("text"),
        "link": message.get("link"),
        "code": message.get("code"),
        "attachments": [a.get("filename") for a in message.get("attachments", [])] or None,
        "message_id": message.get("message_id"),
        "in_reply_to": message.get("in_reply_to"),
        "thread_depth": message.get("depth"),
        "forwarded_from": message.get("forwarded_from"),
    }


# ---------------------------------------------------------------- inboxes


@server.tool()
async def create_inbox(
    purpose: Annotated[str, Field(description="What this inbox is for, e.g. 'signup' "
                                              "or 'support-ticket'. Becomes part of "
                                              "the address.")] = "agent",
) -> dict:
    """Create a disposable email address you own for this task.

    Use one inbox per task. Nothing sent here can reach the real internet, and
    the address stops existing when you delete it or the run is cleaned up.
    """
    box = await call("POST", "/inboxes", {"prefix": purpose, "run_id": RUN_ID})
    return {"address": box["address"], "password": box["password"],
            "smtp": box["smtp"], "imap": box["imap"],
            "next": "Give this address to whatever you are testing, then call wait_for_email."}


@server.tool()
async def list_inboxes() -> dict:
    """List every mailbox on the server, with how much mail each is holding."""
    overview = await call("GET", "/overview")
    return {
        "total_messages": overview["total"],
        "unread": overview["unread"],
        "apps": [
            {"app": app["name"], "domain": app["domain"],
             "smtp_user": app["smtp_address"] or None,
             "mailboxes": [
                 {"address": box["address"], "total": box["total"], "unread": box["unread"],
                  "is_bot": box["is_bot"], "is_capture": box["is_capture"]}
                 for box in app["mailboxes"]
             ]}
            for app in overview["apps"]
        ],
    }


@server.tool()
async def delete_inbox(address: str) -> dict:
    """Delete one disposable inbox and everything in it."""
    return await call("DELETE", f"/inboxes/{address}")


@server.tool()
async def cleanup() -> dict:
    """Delete every inbox created in this session. Call this when you are done."""
    result = await call("DELETE", f"/inboxes?run_id={RUN_ID}")
    return {"deleted": result["deleted"], "count": result["count"]}


# ---------------------------------------------------------------- reading


@server.tool()
async def wait_for_email(
    address: Annotated[str, Field(description="The mailbox to watch")],
    subject_contains: Annotated[str | None, Field(description="Match on the subject")] = None,
    from_contains: Annotated[str | None, Field(description="Match on the sender")] = None,
    text_contains: Annotated[str | None, Field(description="Match on the body")] = None,
    in_reply_to: Annotated[str | None, Field(description="Wait for a reply to this "
                                                         "Message-ID")] = None,
    has_code: Annotated[bool, Field(description="Only match mail containing a "
                                                "one-time code")] = False,
    timeout_seconds: Annotated[int, Field(description="How long to wait", ge=1, le=300)] = 30,
) -> dict:
    """Wait until a matching message arrives, then return it.

    This blocks on the server rather than polling, so call it once and wait —
    do not loop, and do not sleep. Filters combine with AND; with none given it
    returns the next message to arrive.

    Returns `found: false` if nothing matched in time, which is an answer, not
    an error.
    """
    payload = {"user": address, "timeout": timeout_seconds}
    for key, value in (("subject_contains", subject_contains),
                       ("from_contains", from_contains),
                       ("text_contains", text_contains),
                       ("in_reply_to", in_reply_to)):
        if value:
            payload[key] = value
    if has_code:
        payload["has_code"] = True

    result = await call("POST", "/wait", payload, timeout=timeout_seconds + 20)
    if result.get("found") is False:
        return result
    return {"found": True, **brief(result)}


@server.tool()
async def list_emails(
    address: str,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
    unread_only: bool = False,
) -> dict:
    """List what is in a mailbox, newest first, without the full bodies."""
    query = f"/messages/{address}?limit={limit}&unseen={str(unread_only).lower()}"
    listing = await call("GET", query)
    return {
        "address": listing["user"],
        "count": listing["count"],
        "messages": [
            {"uid": m["uid"], "from": m["from"], "subject": m["subject"],
             "snippet": m.get("snippet"), "unread": not m["seen"],
             "message_id": m["message_id"], "thread_depth": m["depth"]}
            for m in listing["messages"]
        ],
    }


@server.tool()
async def read_email(address: str, uid: str) -> dict:
    """Read one message in full, including any link or one-time code in it."""
    return brief(await call("GET", f"/messages/{address}/{uid}"))


@server.tool()
async def list_threads(address: str) -> dict:
    """Group a mailbox into conversations, so you can see what belongs together."""
    result = await call("GET", f"/threads/{address}")
    return {
        "count": result["count"],
        "threads": [
            {"subject": t["subject"], "messages": t["message_count"],
             "participants": t["participants"], "depth": t["depth"],
             "thread_root": t["thread_root"]}
            for t in result["threads"]
        ],
    }


# ---------------------------------------------------------------- writing


@server.tool()
async def send_email(
    from_address: Annotated[str, Field(description="One of your inboxes")],
    to: Annotated[str, Field(description="Recipient. Anything outside the local "
                                         "domains is captured, never sent.")],
    subject: str,
    body: str,
) -> dict:
    """Send a message. Nothing can leave the machine."""
    result = await call("POST", "/send", {"from": from_address, "to": [to],
                                          "subject": subject, "text": body})
    return {"message_id": result["message_id"], "from": result["from"],
            "to": result["to"], "subject": result["subject"]}


@server.tool()
async def reply_to_email(
    address: Annotated[str, Field(description="The mailbox holding the message")],
    uid: Annotated[str, Field(description="Which message to reply to")],
    body: str,
    reply_all: bool = False,
) -> dict:
    """Reply to a message.

    In-Reply-To, References and the "Re:" prefix are built by the server, so
    the reply threads correctly without you constructing headers.
    """
    result = await call("POST", "/reply", {"user": address, "uid": uid,
                                           "text": body, "reply_all": reply_all})
    return {"message_id": result["message_id"], "subject": result["subject"],
            "to": result["to"], "thread_depth": result["depth"]}


@server.tool()
async def forward_email(
    address: Annotated[str, Field(description="The mailbox holding the message")],
    uid: str,
    to: str,
    note: Annotated[str, Field(description="Text to put above the forwarded block")] = "",
) -> dict:
    """Forward a message to someone else.

    The "Fwd:" prefix, the forwarded-header block and the attachments are
    handled for you. A reply to the forward still threads with the original.
    """
    result = await call("POST", "/forward", {"user": address, "uid": uid,
                                             "to": [to], "note": note})
    return {"message_id": result["message_id"], "subject": result["subject"],
            "to": result["to"], "attachments": result["attachments"]}


# ---------------------------------------------------------------- practice


@server.tool()
async def echo_bot_address() -> dict:
    """Get an address that always replies, so you can rehearse a conversation.

    Mail it and a properly threaded reply comes back within seconds. Reply to
    that and it answers again, up to a depth limit — useful for exercising a
    multi-turn exchange with no second person involved.
    """
    overview = await call("GET", "/overview")
    for app in overview["apps"]:
        for box in app["mailboxes"]:
            if box["is_bot"]:
                return {"address": box["address"],
                        "how": "Send it a message, then wait_for_email with "
                               "in_reply_to set to the Message-ID you sent."}
    return {"address": None, "reason": "no auto-responder is configured on this server"}


@server.tool()
async def send_test_scenario(
    to: Annotated[str, Field(description="Which mailbox to deliver it to")],
    scenario: Annotated[str, Field(description="bounce, newsletter, html_only, otp, "
                                               "deep_reply, attachment, unicode, "
                                               "auto_reply, signed or large")],
    seed: Annotated[int | None, Field(description="Same seed, identical message")] = None,
) -> dict:
    """Deliver a deliberately awkward message, to see how you handle it.

    A bounce, a newsletter, HTML with no text part, a reply buried under three
    levels of quoting, attachments, non-Latin scripts, an out-of-office.
    """
    payload: dict = {"name": scenario, "to": to}
    if seed is not None:
        payload["seed"] = seed
    result = await call("POST", "/scenarios", payload)
    return {"scenario": result["scenario"], "message_id": result["message_id"],
            "subject": result["subject"], "to": result["to"]}


@server.tool()
async def list_test_scenarios() -> dict:
    """The awkward messages available to send_test_scenario."""
    return await call("GET", "/scenarios")


@server.tool()
async def check_spam_score(address: str, uid: str) -> dict:
    """Score a message for spam, with the reason for every point.

    Useful for checking whether something you are about to send would be
    flagged, and for deciding whether something you received looks legitimate.
    """
    result = await call("GET", f"/messages/{address}/{uid}/spam")
    return {"score": result["score"], "verdict": result["verdict"],
            "engine": result["engine"],
            "reasons": [f"{h['weight']:+} {h['rule']}: {h['description']}"
                        for h in result["hits"]] or ["nothing flagged"]}


@server.tool()
async def server_status() -> dict:
    """Check the mail server is running and see what it is configured with."""
    health = await call("GET", "/health")
    config = await call("GET", "/config")
    return {
        "status": health["status"],
        "url": BASE_URL,
        "domains": config["local_domains"],
        "mailboxes": health["mailboxes"],
        "delivered": health["delivered"],
        "captured_from_outside": health["captured"],
        "run_id": RUN_ID,
    }


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
