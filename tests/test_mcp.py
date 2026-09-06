"""The MCP tool surface.

Driven through `call_tool`, the same entry point a model uses, so the tests
exercise the schemas and the result shapes rather than the helper functions
underneath them.
"""

from __future__ import annotations

import json

import pytest

mcpserver = pytest.importorskip("mcp.server.mcpserver",
                                reason="tahidromos-mcp is not installed")


@pytest.fixture(scope="module")
def server():
    from tahidromos_mcp import server as module
    return module.server


def unwrap(result):
    """Pull the data out of a CallToolResult, the way a client would."""
    assert not result.is_error, f"tool failed: {result.content}"
    if result.structured_content:
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


async def run(server, name, **arguments):
    return unwrap(await server.call_tool(name, arguments))


@pytest.mark.anyio
async def test_every_tool_is_described(server):
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert {"create_inbox", "wait_for_email", "send_email", "reply_to_email",
            "forward_email", "echo_bot_address", "cleanup"} <= names

    for tool in tools:
        assert tool.description, f"{tool.name} has no description for the model to read"
        assert tool.input_schema is not None


@pytest.mark.anyio
async def test_server_status_reports_a_live_server(server):
    status = await run(server, "server_status")
    assert status["status"] == "ok"
    assert status["mailboxes"] > 0
    assert "tahidromos.test" in status["domains"]


@pytest.mark.anyio
async def test_create_read_and_clean_up_an_inbox(server):
    box = await run(server, "create_inbox", purpose="agent-task")
    assert box["address"].startswith("agent-task-")

    await run(server, "send_email", from_address=box["address"], to=box["address"],
              subject="A note to self", body="Remember the milk.")
    got = await run(server, "wait_for_email", address=box["address"],
                    subject_contains="note to self", timeout_seconds=20)

    assert got["found"] is True
    assert got["body"].strip() == "Remember the milk."
    assert got["subject"] == "A note to self"

    listed = await run(server, "list_emails", address=box["address"])
    assert listed["count"] >= 1

    read = await run(server, "read_email", address=box["address"], uid=got["uid"])
    assert read["message_id"] == got["message_id"]

    assert (await run(server, "delete_inbox", address=box["address"]))["deleted"] is True


@pytest.mark.anyio
async def test_waiting_returns_an_answer_not_an_error_on_timeout(server):
    box = await run(server, "create_inbox", purpose="patience")
    result = await run(server, "wait_for_email", address=box["address"],
                       subject_contains="never arrives", timeout_seconds=2)
    assert result["found"] is False
    assert "timeout" in result["reason"]


@pytest.mark.anyio
async def test_an_agent_can_hold_a_threaded_conversation(server):
    """The point of the whole thing: send, get a reply, reply to the reply."""
    box = await run(server, "create_inbox", purpose="conversation")
    bot = await run(server, "echo_bot_address")
    assert bot["address"], "no auto-responder available"

    sent = await run(server, "send_email", from_address=box["address"],
                     to=bot["address"], subject="Ticket #42", body="Is this fixed?")
    reply = await run(server, "wait_for_email", address=box["address"],
                      in_reply_to=sent["message_id"], timeout_seconds=45)

    assert reply["found"] is True
    assert reply["subject"] == "Re: Ticket #42"
    assert reply["from"] == bot["address"]

    second = await run(server, "reply_to_email", address=box["address"],
                       uid=reply["uid"], body="Thanks, closing it.")
    assert second["subject"] == "Re: Ticket #42"
    assert second["thread_depth"] > reply["thread_depth"]

    threads = await run(server, "list_threads", address=box["address"])
    assert threads["count"] >= 1


@pytest.mark.anyio
async def test_an_agent_can_forward(server):
    sender = await run(server, "create_inbox", purpose="fwd-from")
    recipient = await run(server, "create_inbox", purpose="fwd-to")

    await run(server, "send_test_scenario", to=sender["address"],
              scenario="attachment", seed=5)
    original = await run(server, "wait_for_email", address=sender["address"],
                         subject_contains="Invoice", timeout_seconds=20)

    forwarded = await run(server, "forward_email", address=sender["address"],
                          uid=original["uid"], to=recipient["address"],
                          note="Passing this on.")
    assert forwarded["subject"].startswith("Fwd:")
    assert "invoice-10432.pdf" in forwarded["attachments"]

    arrived = await run(server, "wait_for_email", address=recipient["address"],
                        subject_contains="Fwd:", timeout_seconds=20)
    assert arrived["found"] is True
    assert arrived["forwarded_from"] == original["message_id"]


@pytest.mark.anyio
async def test_magic_link_and_code_come_back_extracted(server):
    box = await run(server, "create_inbox", purpose="signin")
    await run(server, "send_test_scenario", to=box["address"], scenario="otp", seed=13)

    got = await run(server, "wait_for_email", address=box["address"],
                    has_code=True, timeout_seconds=20)
    assert got["code"].isdigit()
    assert "magic?token=" in got["link"]


@pytest.mark.anyio
async def test_scenarios_are_listed_for_the_model(server):
    listed = await run(server, "list_test_scenarios")
    names = {s["name"] for s in listed["scenarios"]}
    assert {"bounce", "otp", "deep_reply", "attachment"} <= names


@pytest.mark.anyio
async def test_spam_scoring_explains_itself(server):
    box = await run(server, "create_inbox", purpose="spam")
    await run(server, "send_email", from_address=box["address"], to=box["address"],
              subject="WIN FREE MONEY NOW!!!",
              body="ACT NOW! YOU HAVE WON THE LOTTERY.")
    got = await run(server, "wait_for_email", address=box["address"],
                    subject_contains="WIN FREE", timeout_seconds=20)

    score = await run(server, "check_spam_score", address=box["address"], uid=got["uid"])
    assert score["verdict"] in ("suspicious", "spam")
    assert any("SUBJECT_ALL_CAPS" in reason for reason in score["reasons"])


@pytest.mark.anyio
async def test_cleanup_removes_the_session_inboxes(server):
    box = await run(server, "create_inbox", purpose="temporary")
    result = await run(server, "cleanup")
    assert box["address"] in result["deleted"]
