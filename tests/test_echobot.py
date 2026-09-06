"""The auto-responder: a conversation partner that always replies."""

from __future__ import annotations

import pytest

from conftest import DOMAIN


def test_bot_replies_to_a_first_message(alice, unique):
    subject = f"bot hello {unique}"
    sent_id = alice.send("echo", subject, "are you there?")

    reply = alice.wait_for(
        lambda m: str(m.get("In-Reply-To", "")).strip() == sent_id.strip(),
        timeout=60,
    )
    assert str(reply["Subject"]) == f"Re: {subject}"
    assert f"echo@{DOMAIN}" in str(reply["From"])
    assert str(reply["Auto-Submitted"]) == "auto-replied"


def test_replying_to_the_bot_gets_another_reply(alice, unique):
    """send -> bot replies -> reply to the reply -> bot replies again."""
    subject = f"bot chain {unique}"
    first_id = alice.send("echo", subject, "turn 1")

    bot_first = alice.wait_for(
        lambda m: str(m.get("In-Reply-To", "")).strip() == first_id.strip(), timeout=60)

    second_id = alice.reply_to(bot_first, "turn 3")
    bot_second = alice.wait_for(
        lambda m: str(m.get("In-Reply-To", "")).strip() == second_id.strip(), timeout=60)

    references = str(bot_second["References"]).split()
    assert references[0] == first_id.strip(), "bot lost the thread root"
    assert len(references) >= 3, f"chain too short: {references}"
    assert int(str(bot_second["X-Tahidromos-Depth"])) > int(str(bot_first["X-Tahidromos-Depth"]))


@pytest.mark.slow
def test_bot_to_bot_ping_pong_builds_a_deep_chain_then_stops(unique):
    """The full reply-to-the-reply-to-the-reply case.

    Seed one message from echo2 to echo and step back: the two bots answer
    each other, each reply threading onto the last, until ECHOBOT_MAX_DEPTH
    cuts the chain off.  Nothing drives this but the mail server itself.
    """
    import time

    from conftest import MailClient

    echo, echo2 = MailClient("echo"), MailClient("echo2")
    subject = f"ping pong {unique}"
    root_id = echo2.send("echo", subject, "volley 1")

    deadline = time.monotonic() + 90
    seen: dict[str, int] = {}
    while time.monotonic() < deadline:
        for mailbox in (echo, echo2):
            for message in mailbox.inbox():
                if subject not in str(message.get("Subject", "")):
                    continue
                message_id = str(message.get("Message-ID", "")).strip()
                seen[message_id] = len(str(message.get("References", "")).split())
        if len(seen) >= 8:
            break
        time.sleep(3)

    assert len(seen) >= 5, f"bots only exchanged {len(seen)} messages: {seen}"

    depths = sorted(seen.values())
    assert depths[-1] >= 4, f"chain never got deep: {depths}"

    # every message must hang off the same root -- one thread, not many
    roots = set()
    for mailbox in (echo, echo2):
        for message in mailbox.inbox():
            if subject in str(message.get("Subject", "")):
                references = str(message.get("References", "")).split()
                roots.add(references[0] if references else str(message["Message-ID"]).strip())
    assert roots == {root_id.strip()}, f"thread fragmented into {roots}"

    # and it must terminate rather than loop forever
    time.sleep(20)
    final = len({str(m.get("Message-ID", "")).strip()
                 for box in (echo, echo2) for m in box.inbox()
                 if subject in str(m.get("Subject", ""))})
    assert final <= 12, f"runaway loop: {final} messages and counting"
